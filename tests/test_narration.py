from fractions import Fraction
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from beatcut import media, narration
from beatcut.narration import NarrationProject, match_paragraphs
from beatcut.server import app


def words_for(parts):
    words = []
    for i, text in enumerate(parts):
        for j, char in enumerate(narration.normalize(text)):
            words.append(dict(text=char, start=i * 10 + j * .2, end=i * 10 + j * .2 + .15))
    return words


def test_paragraph_alignment_places_cuts_between_speech():
    parts = ['第一段，介绍工具。', '第二段，保留停顿。', '最后一段，导出剪辑。']
    result = match_paragraphs('\n\n'.join(parts), words_for(parts), 30)
    assert result['coverage'] == 1
    assert len(result['cuts']) == 2
    for left, right, cut in zip(result['paragraphs'], result['paragraphs'][1:], result['cuts']):
        assert left['speech_end'] < cut < right['speech_start']
    assert result['paragraphs'][0]['text'] == parts[0]


def test_mismatched_manuscript_is_rejected_instead_of_estimated():
    with pytest.raises(ValueError, match='匹配率'):
        match_paragraphs('甲乙丙丁\n天地玄黄', words_for(['测试工具', '自动裁切']), 30)


def test_missing_paragraph_is_not_silently_dropped():
    with pytest.raises(ValueError, match='第 2 段'):
        match_paragraphs('今天我们测试音乐剪辑\n漏读\n最后我们保存完整项目', words_for(['今天我们测试音乐剪辑', '最后我们保存完整项目']), 30)


@pytest.fixture
def voice(tmp_path, monkeypatch):
    monkeypatch.setattr(media, 'DATA', tmp_path)
    folder = media.media_dir('a' * 32)
    folder.mkdir(parents=True)
    path = folder / 'speech.wav'
    sf.write(path, np.zeros((48101, 2)), 48000)
    meta = dict(id='a' * 32, path=str(path), kind='music', name='旁白 & 原音频.wav', duration=48101 / 48000)
    (folder / 'meta.json').write_text(json.dumps(meta))
    return meta


def test_narration_xml_preserves_every_sample_and_text(voice):
    project = NarrationProject(music_id=voice['id'], script='第一段。\n\n第二段。\n第三段。', cuts=[.4, .8])
    root = ET.fromstring(narration.make_narration_xml(project, voice))
    clips = root.findall('.//spine/clip')
    assert len(clips) == 3
    end = Fraction(0)
    for clip in clips:
        assert Fraction(clip.attrib['offset'][:-1]) == end
        assert Fraction(clip.attrib['start'][:-1]) == end
        audio = clip.find('audio')
        assert Fraction(audio.attrib['start'][:-1]) == end
        assert Fraction(audio.attrib['offset'][:-1]) == end
        end += Fraction(audio.attrib['duration'][:-1])
    assert end == Fraction(48101, 48000)
    # FCP's frame-based primary storyline must extend beyond the fractional tail.
    assert sum(Fraction(c.attrib['duration'][:-1]) for c in clips) == Fraction(26, 25)
    assert Fraction(root.find('.//sequence').attrib['duration'][:-1]) == Fraction(26, 25)
    assert clips[1].find('note').text == '第二段。'
    assert clips[1].find('audio').attrib['role'] == 'dialogue'


@pytest.mark.parametrize('cuts', [[.5, .5], [.8, .4], [-.1, .8], [.4, 2], [.4, float('nan')]])
def test_invalid_narration_cuts_rejected(voice, cuts):
    project = NarrationProject(music_id=voice['id'], script='甲\n乙\n丙', cuts=cuts)
    with pytest.raises(ValueError):
        narration.validate_project(project)


def test_narration_export_and_restore(voice):
    project = NarrationProject(music_id=voice['id'], script='第一段\n第二段', cuts=[.5])
    with TestClient(app, headers={'X-BeatCut': '1'}) as client:
        result = client.post('/api/narration/export', json=project.model_dump())
        assert result.status_code == 200, result.text
        result = result.json()
        assert result['paragraphs'] == 2
        saved = client.get(result['files']['project.beatcut.json']).json()
        assert saved == project.model_dump()
        assert client.post('/api/narration/validate', json=saved).status_code == 200


@pytest.mark.parametrize('fps, boundary', [('25', '00:00:00,520'), ('24000/1001', '00:00:00,500')])
def test_export_srt_uses_manuscript_and_exported_cut_times(voice, fps, boundary):
    project = NarrationProject(music_id=voice['id'], script='第一段，保留标点 & English。\n\n第二段。', cuts=[.51], fps=fps)
    with TestClient(app, headers={'X-BeatCut': '1'}) as client:
        response = client.post('/api/narration/export', json=project.model_dump())
        assert response.status_code == 200, response.text
        result = response.json()
        subtitle = client.get(result['files']['subtitles.srt'])
        assert subtitle.status_code == 200
        assert 'subtitles.srt' in subtitle.headers['content-disposition']
        assert subtitle.content.decode('utf-8') == (
            f'1\n00:00:00,000 --> {boundary}\n第一段，保留标点 & English。\n\n'
            f'2\n{boundary} --> 00:00:01,003\n第二段。\n\n'
        )


def test_srt_short_final_tail_still_has_positive_duration(voice):
    # The final audio component may be shorter than a millisecond.
    sf.write(voice['path'], np.zeros((48001, 2)), 48000)
    voice['duration'] = 48001 / 48000
    (media.media_dir(voice['id']) / 'meta.json').write_text(json.dumps(voice))
    project = NarrationProject(music_id=voice['id'], script='第一段\n尾音', cuts=[1])
    result = narration.export_project(project)
    text = (Path(result['path']) / 'subtitles.srt').read_text(encoding='utf-8')
    assert '00:00:01,000 --> 00:00:01,001\n尾音' in text


def test_srt_timestamp_carries_minutes_and_hours():
    assert narration.srt_timestamp(59999) == '00:00:59,999'
    assert narration.srt_timestamp(60000) == '00:01:00,000'
    assert narration.srt_timestamp(3600000) == '01:00:00,000'


def test_narration_official_apple_dtd(voice):
    from lxml import etree
    paths = list(Path('/Applications').glob('Final Cut*.app/Contents/Frameworks/Interchange.framework/Versions/A/Resources/FCPXMLv1_10.dtd'))
    if not paths:
        pytest.skip('Apple DTD not installed')
    project = NarrationProject(music_id=voice['id'], script='第一段\n第二段', cuts=[.5])
    xml = narration.make_narration_xml(project, voice)
    dtd = etree.DTD(str(paths[0]))
    assert dtd.validate(etree.fromstring(xml)), str(dtd.error_log)
