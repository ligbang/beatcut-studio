"""Run the built engine with no Homebrew or virtualenv on its PATH."""
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

import pytest

ENGINE = Path(__file__).resolve().parent.parent / 'dist/BeatCut Studio.app/Contents/Resources/beatcut-engine'


@pytest.mark.skipif(not (ENGINE / 'beatcut-engine').exists(), reason='Build the macOS app to run the bundle smoke test')
def test_self_contained_desktop_engine(tmp_path):
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

            def request(path, data=None, method='POST'):
                body = json.dumps(data).encode() if data is not None else None
                req = urllib.request.Request(origin + path, data=body, method=method,
                                             headers={'X-BeatCut': '1', 'Content-Type': 'application/json'})
                with urllib.request.urlopen(req, timeout=120) as response:
                    return json.load(response)

            status = request('/api/status', method='GET')
            assert status['ffmpeg'] and status['ffprobe']
            demo = request('/api/demo')
            analysis = request('/api/analyze/' + demo['music']['id'] + '?bpm=120')
            assert len(analysis['beats']) >= 44
            result = request('/api/export', {'music_id': demo['music']['id'], 'mode': 'clips',
                             'clips': [{'id': clip['id']} for clip in demo['clips']],
                             'markers': [{'time': t} for t in analysis['beats']]})
            assert result['plan']['duration'] == 24
            assert len(result['plan']['slots']) == 13
            export = Path(result['path'])
            assert (export / 'music.wav').stat().st_size > 1000
            assert export.is_relative_to(tmp_path)
        finally:
            process.terminate()
            process.wait(timeout=15)
