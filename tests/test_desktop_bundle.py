"""Exercise the actual app engine without Homebrew or a virtualenv on its PATH."""
import json
from fractions import Fraction
import os
from pathlib import Path
import subprocess
import time
import xml.etree.ElementTree as ET

import httpx
import pytest

ENGINE = Path(__file__).resolve().parent.parent / 'dist/BeatCut Studio.app/Contents/Resources/beatcut-engine'
MODELS = Path.home() / 'Library/Application Support/BeatCut Studio/models'


@pytest.fixture
def bundled(tmp_path):
    if not (ENGINE / 'beatcut-engine').exists():
        pytest.skip('Build the macOS app to run bundle tests')
    ready = tmp_path / 'ready.json'
    env = dict(os.environ, BEATCUT_DATA_DIR=str(tmp_path / 'data'), BEATCUT_READY_FILE=str(ready),
               NUMBA_CACHE_DIR=str(tmp_path / 'numba'), PATH=str(ENGINE / '_internal/bin') + ':/usr/bin:/bin')
    with (tmp_path / 'engine.log').open('w') as log:
        process = subprocess.Popen([str(ENGINE / 'beatcut-engine')], env=env, stdout=log, stderr=log)
        try:
            for _ in range(200):
                if ready.exists():
                    break
                assert process.poll() is None, (tmp_path / 'engine.log').read_text()
                time.sleep(.1)
            assert ready.exists()
            origin = 'http://127.0.0.1:' + str(json.loads(ready.read_text())['port'])
            with httpx.Client(base_url=origin, headers={'X-BeatCut': '1'}, timeout=120) as client:
                yield client
        finally:
            process.terminate()
            process.wait(timeout=15)


def test_self_contained_desktop_engine(bundled, tmp_path):
    status = bundled.get('/api/status').json()
    assert status['ffmpeg'] and status['ffprobe']
    demo = bundled.post('/api/demo').json()
    analysis = bundled.post('/api/analyze/' + demo['music']['id'] + '?bpm=120').json()
    assert len(analysis['beats']) >= 44
    response = bundled.post('/api/export', json={'music_id': demo['music']['id'], 'mode': 'clips',
                           'clips': [{'id': clip['id']} for clip in demo['clips']],
                           'markers': [{'time': t} for t in analysis['beats']]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['plan']['duration'] == 24
    assert len(result['plan']['slots']) == 13
    export = Path(result['path'])
    assert (export / 'music.wav').stat().st_size > 1000
    assert export.is_relative_to(tmp_path)


@pytest.mark.skipif(not all((MODELS / name / '.beatcut-ready').exists() for name in ['Qwen3-ASR-0.6B-8bit', 'Qwen3-ForcedAligner-0.6B-8bit']), reason='Local voice models not downloaded; no test-triggered downloads')
def test_self_contained_voice_engine(bundled, tmp_path):
    script = '今天测试旁白剪辑工具。\n现在开始第二段，保留自然的停顿。'
    text = tmp_path / 'script.txt'
    text.write_text(script)
    audio = tmp_path / 'speech.aiff'
    subprocess.run(['/usr/bin/say', '-v', 'Tingting', '-f', str(text), '-o', str(audio)], check=True)
    with audio.open('rb') as source:
        response = bundled.post('/api/media', files={'file': ('speech.aiff', source, 'audio/aiff')}, data={'kind': 'music'})
    assert response.status_code == 200, response.text
    music = response.json()
    response = bundled.post('/api/narration/align', json={'music_id': music['id'], 'script': script})
    assert response.status_code == 200, response.text
    job = response.json()['id']
    for _ in range(240):
        result = bundled.get('/api/narration/jobs/' + job).json()
        if result['state'] != 'running':
            break
        time.sleep(.5)
    assert result['state'] == 'done', result
    assert result['result']['coverage'] > .9
    assert len(result['result']['cuts']) == 1
    response = bundled.post('/api/narration/export', json={'music_id': music['id'], 'script': script, 'cuts': result['result']['cuts']})
    assert response.status_code == 200, response.text
    exported = response.json()
    assert exported['paragraphs'] == 2
    root = ET.fromstring(bundled.get(exported['files']['timeline.fcpxml']).content)
    components = root.findall('.//spine/clip/audio')
    assert len(components) == 2
    covered = sum(Fraction(c.get('duration')[:-1]) for c in components)
    assert float(covered) == pytest.approx(music['duration'], abs=1 / 48000)
