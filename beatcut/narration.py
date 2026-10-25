"""Align a manuscript to speech, keeping every sample in the editable timeline."""
import csv
from difflib import SequenceMatcher
from fractions import Fraction
import json
import logging
import math
from pathlib import Path
import platform
import threading
import unicodedata
import uuid
import xml.etree.ElementTree as ET

import numpy as np
from pydantic import BaseModel, Field
import soundfile as sf

from . import media as storage
from .models import Project
from .timeline import time_value

MODEL_NAMES = ('Qwen3-ASR-0.6B-8bit', 'Qwen3-ForcedAligner-0.6B-8bit')
JOBS = {}
WORK_LOCK = threading.Lock()


class NarrationRequest(BaseModel):
    music_id: str
    script: str = Field(min_length=1, max_length=50000)


class NarrationProject(NarrationRequest):
    kind: str = 'narration'
    version: int = 1
    name: str = Field(default='旁白分段', min_length=1, max_length=120)
    fps: str = '25'
    cuts: list[float] = Field(default_factory=list, max_length=1000)


def paragraphs(script):
    result = [line.strip() for line in script.splitlines() if line.strip()]
    if not 2 <= len(result) <= 1000:
        raise ValueError('请提供 2–1000 段文案，每段单独一行；空行会忽略。')
    if any(not normalize(p) for p in result):
        raise ValueError('每段文案都需要包含文字，不能只有标点。')
    return result


def normalize(text):
    return ''.join(c.lower() for c in unicodedata.normalize('NFKC', text) if c.isalnum())


def match_paragraphs(script, words, duration):
    """Use ordered character matches, never word-count-proportional time guesses."""
    parts = paragraphs(script)
    reference = ''.join(normalize(p) for p in parts)
    transcript, times = '', []
    for word in words:
        text = normalize(word['text'])
        if not text or word['end'] < word['start'] or word['start'] < 0 or word['end'] > duration + .1:
            continue
        transcript += text
        times.extend([(word['start'], word['end'])] * len(text))
    mapping = {}
    for block in SequenceMatcher(None, reference, transcript, autojunk=False).get_matching_blocks():
        for i in range(block.size):
            mapping[block.a + i] = block.b + i
    coverage = len(mapping) / max(1, len(reference))
    if coverage < .60:
        raise ValueError(f'原文与音频文字匹配率只有 {coverage:.0%}，请检查是否选错音频、文案版本或语言。未生成猜测切点。')
    aligned, cursor = [], 0
    for i, text in enumerate(parts):
        count = len(normalize(text))
        matches = [(j, mapping[j]) for j in range(cursor, cursor + count) if j in mapping]
        if not matches or len(matches) / count < .35:
            raise ValueError(f'第 {i + 1} 段无法可靠定位。请检查该段是否实际念出，或拆分长段落后重试。')
        first, last = matches[0], matches[-1]
        warning = ''
        if len(matches) / count < .85 or first[0] - cursor > 2 or cursor + count - 1 - last[0] > 2:
            warning = '原文与识别内容有差异，请试听段首、段尾'
        aligned.append(dict(text=text, speech_start=times[first[1]][0], speech_end=times[last[1]][1],
                            match=round(len(matches) / count, 3), warning=warning))
        cursor += count
    cuts = []
    for i, (left, right) in enumerate(zip(aligned, aligned[1:])):
        if right['speech_start'] <= left['speech_start']:
            raise ValueError(f'第 {i + 2} 段的时间顺序异常，请检查重复文案或漏读。')
        cut = (left['speech_end'] + right['speech_start']) / 2
        if left['speech_end'] > right['speech_start']:
            cut = right['speech_start']
            right['warning'] = '相邻文字时间重叠，请试听并微调切点'
        if cut <= (cuts[-1] if cuts else 0) or cut >= duration:
            raise ValueError(f'第 {i + 2} 段没有可用切点，请检查文案。')
        cuts.append(round(cut, 4))
    return dict(paragraphs=aligned, cuts=cuts, coverage=round(coverage, 3), duration=duration)


def model_root():
    return Path.home() / 'Library/Application Support/BeatCut Studio/models'


def recognize_words(meta, progress, cancelled):
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise ValueError('本地旁白对齐目前需要 Apple Silicon Mac。')
    cache = storage.media_dir(meta['id']) / 'narration-words-v1.json'
    if cache.exists():
        progress('读取已分析的旁白', 90)
        return json.loads(cache.read_text())
    from huggingface_hub import snapshot_download
    from mlx_audio.stt import load
    import mlx.core as mx
    from scipy.signal import resample_poly

    progress('准备本地语音模型；首次使用会下载模型，音频不会上传', 1)
    paths = []
    for name in MODEL_NAMES:
        if cancelled():
            raise InterruptedError()
        path = model_root() / name
        # A completed snapshot is reused without network access.
        if not (path / '.beatcut-ready').exists():
            snapshot_download('mlx-community/' + name, local_dir=path,
                              allow_patterns=['*.json', '*.safetensors', '*.txt', '*.model', '*.tiktoken', 'README.md', 'LICENSE*'])
            (path / '.beatcut-ready').touch()
        paths.append(path)
    progress('正在加载本地语音模型', 5)
    asr, aligner = (load(str(path)) for path in paths)
    samples, sr = sf.read(meta['path'], dtype='float32', always_2d=True)
    samples = resample_poly(samples.mean(axis=1), 16000, sr).astype(np.float32)
    duration = len(samples) / 16000
    words = []
    count = math.ceil(duration / 30)
    # One-second context on both sides protects speech crossing a chunk boundary.
    for i in range(count):
        if cancelled():
            raise InterruptedError()
        progress(f'正在对齐旁白 · {i + 1} / {count} 段音频', 8 + int(i / count * 80))
        core_start, core_end = i * 30, min(duration, (i + 1) * 30)
        start, end = max(0, core_start - 1), min(duration, core_end + 1)
        chunk = samples[round(start * 16000):round(end * 16000)]
        if np.max(np.abs(chunk), initial=0) < 1e-4:
            continue
        result = asr.generate(chunk, language='Chinese', max_tokens=1024, verbose=False)
        if not normalize(result.text):
            continue
        aligned = aligner.generate(audio=chunk, text=result.text, language='Chinese')
        for item in aligned:
            a, b = item.start_time + start, item.end_time + start
            midpoint = (a + b) / 2
            if core_start <= midpoint < core_end and 0 <= a <= b <= duration + .1:
                words.append(dict(text=item.text, start=round(a, 4), end=round(min(b, duration), 4)))
        mx.clear_cache()
    words.sort(key=lambda word: (word['start'], word['end']))
    if not words:
        raise ValueError('没有识别到可对齐的旁白，请确认音轨有清晰语音。')
    cache.write_text(json.dumps(words, ensure_ascii=False))
    return words


def start_alignment(request):
    paragraphs(request.script)
    meta = storage.load_media(request.music_id)
    if meta['kind'] != 'music' or meta['duration'] > 1800:
        raise ValueError('请选择最长 30 分钟的旁白音频。')
    if not WORK_LOCK.acquire(blocking=False):
        raise ValueError('已有旁白分析正在进行，请完成或取消后再试。')
    job_id = uuid.uuid4().hex
    job = dict(id=job_id, state='running', message='等待本地分析', progress=0, cancel=False)
    JOBS[job_id] = job

    def work():
        try:
            def progress(message, value):
                job.update(message=message, progress=value)
            words = recognize_words(meta, progress, lambda: job['cancel'])
            if job['cancel']:
                raise InterruptedError()
            progress('正在匹配原文段落与停顿', 95)
            result = match_paragraphs(request.script, words, meta['duration'])
            job.update(state='done', message='段落对齐完成', progress=100, result=result)
        except InterruptedError:
            job.update(state='cancelled', message='已取消分析')
        except Exception as error:
            logging.exception('Narration alignment failed')
            job.update(state='error', message=str(error))
        finally:
            WORK_LOCK.release()
    threading.Thread(target=work, daemon=True).start()
    return dict(id=job_id)


def validate_project(project):
    if project.kind != 'narration' or project.version != 1:
        raise ValueError('不支持的旁白项目版本')
    Project(music_id=project.music_id, fps=project.fps)
    meta = storage.load_media(project.music_id)
    parts = paragraphs(project.script)
    if len(project.cuts) != len(parts) - 1:
        raise ValueError('段落数量与切点不一致，请重新对齐文案')
    cuts = [0, *project.cuts, meta['duration']]
    if any(not math.isfinite(t) for t in cuts) or any(b <= a for a, b in zip(cuts, cuts[1:])):
        raise ValueError('切点必须按时间递增，且位于音频范围内')
    return meta, parts


def export_timing(project, meta):
    info = sf.info(meta['path'])
    total = Fraction(info.frames, info.samplerate)
    frame = 1 / Fraction(project.fps)
    cuts = [Fraction(0), *(round(Fraction(str(t)) / frame) * frame for t in project.cuts), total]
    if any(b <= a for a, b in zip(cuts, cuts[1:])):
        raise ValueError('两个切点量化到同一帧，请拉开距离后再导出')
    return info, frame, cuts


def make_narration_xml(project, meta):
    _, parts = validate_project(project)
    info, frame, cuts = export_timing(project, meta)
    total = cuts[-1]
    root = ET.Element('fcpxml', version='1.10')
    resources = ET.SubElement(root, 'resources')
    ET.SubElement(resources, 'format', id='r1', frameDuration=time_value(frame), width='1920', height='1080', colorSpace='1-1-1 (Rec. 709)')
    asset = ET.SubElement(resources, 'asset', id='r2', name=meta['name'], start='0s', duration=time_value(total),
                          hasAudio='1', audioSources='1', audioChannels=str(info.channels), audioRate=str(info.samplerate))
    ET.SubElement(asset, 'media-rep', kind='original-media', src=Path(meta['path']).as_uri())
    event = ET.SubElement(ET.SubElement(root, 'library'), 'event', name='BeatCut · ' + project.name)
    sequence = ET.SubElement(ET.SubElement(event, 'project', name=project.name), 'sequence', format='r1',
                             duration=time_value(math.ceil(total / frame) * frame), tcStart='0s', tcFormat='NDF', audioLayout='stereo', audioRate='48k')
    spine = ET.SubElement(sequence, 'spine')
    for i, (text, start, end) in enumerate(zip(parts, cuts, cuts[1:])):
        # FCP rounds a primary asset-clip to frames, which can trim the final samples.
        # A frame-sized container with a sample-exact audio child preserves the tail.
        clip = ET.SubElement(spine, 'clip', name=f'{i + 1:02} · {text}', offset=time_value(start),
                             start=time_value(start), duration=time_value(math.ceil((end - start) / frame) * frame), format='r1')
        ET.SubElement(clip, 'note').text = text
        ET.SubElement(clip, 'audio', ref='r2', offset=time_value(start), start=time_value(start),
                      duration=time_value(end - start), role='dialogue')
    ET.indent(root)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + ET.tostring(root, encoding='utf-8')


def srt_timestamp(milliseconds):
    seconds, milliseconds = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}'


def export_project(project):
    meta, parts = validate_project(project)
    xml = make_narration_xml(project, meta)
    _, _, cuts = export_timing(project, meta)
    # SRT has millisecond precision; shared cuts round down, the audio end rounds up.
    milliseconds = [math.floor(t * 1000) for t in cuts[:-1]] + [math.ceil(cuts[-1] * 1000)]
    export_id = uuid.uuid4().hex
    folder = storage.DATA / 'exports' / export_id
    folder.mkdir(parents=True)
    (folder / 'timeline.fcpxml').write_bytes(xml)
    (folder / 'subtitles.srt').write_text(''.join(
        f'{i + 1}\n{srt_timestamp(a)} --> {srt_timestamp(b)}\n{text}\n\n'
        for i, (text, a, b) in enumerate(zip(parts, milliseconds, milliseconds[1:]))
    ), encoding='utf-8')
    (folder / 'project.beatcut.json').write_text(project.model_dump_json(indent=2))
    with (folder / 'markers.csv').open('w', encoding='utf-8-sig', newline='') as output:
        writer = csv.writer(output)
        writer.writerow(['paragraph', 'start_seconds', 'end_seconds', 'text'])
        for i, (text, a, b) in enumerate(zip(parts, cuts, cuts[1:])):
            writer.writerow([i + 1, float(a), float(b), text])
    return dict(id=export_id, path=str(folder), paragraphs=len(parts), duration=meta['duration'],
                files={name: f'/api/exports/{export_id}/{name}' for name in ['timeline.fcpxml', 'subtitles.srt', 'markers.csv', 'project.beatcut.json']})
