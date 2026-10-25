import io
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from beatcut import media
from beatcut.models import ClipChoice, Marker, Project
from beatcut.server import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "DATA", tmp_path)
    return TestClient(app, headers={"X-BeatCut": "1"})


def wav_bytes(seconds=24, silence=False):
    sr = 22050
    y = np.zeros(round(seconds * sr), dtype=np.float32)
    if not silence:
        rng = np.random.default_rng(0)
        t = np.arange(2205) / sr
        click = (np.sin(2*np.pi*120*t) + rng.normal(0, .3, len(t))) * np.exp(-t*65)*.7
        for beat in np.arange(.25, seconds-.2, .5):
            start = round(beat * sr)
            y[start:start+len(click)] += click[:len(y)-start]
    file = io.BytesIO()
    sf.write(file, y, sr, format="WAV")
    return file.getvalue()


def upload(client, silence=False):
    response = client.post("/api/media", files={"file": ('节拍 & test.wav', wav_bytes(silence=silence), "audio/wav")}, data={"kind":"music"})
    assert response.status_code == 200, response.text
    return response.json()


def test_click_track_detection_and_silent_audio(client):
    meta = upload(client)
    result = client.post('/api/analyze/' + meta['id'])
    assert result.status_code == 200, result.text
    data = result.json()
    assert abs(data['bpm'] - 120) < 4
    assert len(data['beats']) >= 40
    error = [min(abs(t-b) for b in np.arange(.25, 24, .5)) for t in data['beats']]
    assert np.median(error) < .05
    silent = upload(client, silence=True)
    result = client.post('/api/analyze/' + silent['id']).json()
    assert result['bpm'] == 0 and result['beats'] == []


def test_export_audio_matches_frame_duration_and_roundtrip(client):
    meta = upload(client)
    p = Project(music_id=meta['id'], fps="30000/1001", ranges=[(1.13, 5.77), (13.21, 22.99)],
                markers=[Marker(time=i/2+.25) for i in range(48)], mode="placeholders")
    result = client.post('/api/export', json=p.model_dump())
    assert result.status_code == 200, result.text
    result = result.json()
    response = client.get(result['files']['music.wav'])
    info = sf.info(io.BytesIO(response.content))
    assert abs(info.duration-result['plan']['duration']) <= 1/48000
    saved = client.get(result['files']['project.beatcut.json']).json()
    assert Project.model_validate(saved) == p
    assert client.post('/api/project/validate', json=saved).status_code == 200
    assert client.get(result['files']['timeline.fcpxml']).status_code == 200
    assert client.get('/api/media/'+meta['id']+'/file',headers={'Range':'bytes=0-31'}).status_code == 206


def test_shortening_preserves_head_tail_and_four_beat_phase(client):
    meta = upload(client)
    p = Project(music_id=meta['id'], markers=[Marker(time=.25+i*.5) for i in range(48)])
    response = client.post('/api/shorten', json={'project':p.model_dump(),'target':12,'tolerance':1})
    assert response.status_code == 200, response.text
    for option in response.json():
        first, last = option['ranges']
        assert first[0] == 0 and last[1] == 24
        assert abs(option['duration'] - 12) <= 1
        assert abs((last[0]-first[1]) % 2) < 1e-8


def test_local_server_boundary(client):
    assert client.post('/api/demo', headers={'X-BeatCut':''}).status_code == 403
    assert client.get('/api/status', headers={'Host':'evil.example'}).status_code == 400
    assert client.get('/api/media/not-a-valid-id').status_code == 400
    assert client.get('/api/exports/not-valid/timeline.fcpxml').status_code == 404
    assert client.post('/api/media', files={'file':('bad.exe',b'not media')},data={'kind':'music'}).status_code == 400


def test_half_time_ambiguity_can_be_corrected_without_replacing_waveform(client):
    response = client.post('/api/demo')
    assert response.status_code == 200, response.text
    demo = response.json()
    auto = client.post('/api/analyze/' + demo['music']['id']).json()
    corrected = client.post('/api/analyze/' + demo['music']['id'] + '?bpm=120').json()
    assert corrected['bpm'] == 120
    assert len(corrected['beats']) >= 44
    assert corrected['peaks'] == auto['peaks']
    assert client.post('/api/analyze/' + demo['music']['id'] + '?bpm=0').status_code == 422


def test_audio_named_like_conversion_output(client):
    response = client.post('/api/media', files={'file':('audio.wav',wav_bytes(2),'audio/wav')},data={'kind':'music'})
    assert response.status_code == 200, response.text


def test_still_image_upload_and_movie_export(client, tmp_path):
    path = tmp_path / 'picture.png'
    media.ffmpeg('-f', 'lavfi', '-i', 'color=c=blue:s=64x48', '-frames:v', '1', str(path))
    result = client.post('/api/media', files={'file': ('图片 & 测试.png', path.read_bytes(), 'image/png')}, data={'kind': 'clip'})
    assert result.status_code == 200, result.text
    image = result.json()
    assert image['kind'] == 'image'
    music = upload(client)
    project = Project(music_id=music['id'], ranges=[(0, 2)], clips=[ClipChoice(id=image['id'])], mode='clips')
    result = client.post('/api/export', json=project.model_dump())
    assert result.status_code == 200, result.text
    result = result.json()
    movie = Path(result['path']) / (image['id'] + '.mov')
    assert movie.stat().st_size > 100
    assert result['plan']['duration'] == 2
    assert '.mov' in client.get(result['files']['timeline.fcpxml']).text
