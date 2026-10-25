import json
import os
import re
import subprocess
from fractions import Fraction
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("BEATCUT_DATA_DIR", ROOT / ".data"))


def run(args: list[str], timeout: int = 600) -> bytes:
    result = subprocess.run(args, capture_output=True, timeout=timeout)
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace")[-1200:])
    return result.stdout


def ffmpeg(*args: str):
    return run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args])


def media_dir(media_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", media_id):
        raise ValueError("无效的素材编号")
    return DATA / "media" / media_id


def load_media(media_id: str) -> dict:
    path = media_dir(media_id) / "meta.json"
    if not path.exists():
        raise ValueError("找不到项目中的素材，请重新导入素材或恢复 .data 文件夹")
    return json.loads(path.read_text())


def save_media(meta: dict):
    (media_dir(meta["id"]) / "meta.json").write_text(json.dumps(meta, ensure_ascii=False))


def source_timecode(tc: str, fps: Fraction) -> Fraction:
    """Source timecode origin, including NTSC drop-frame numbering."""
    if not re.fullmatch(r"\d\d:\d\d:\d\d[:;]\d\d", tc):
        return Fraction(0)
    h, m, s, f = map(int, re.split(r"[:;]", tc))
    nominal = round(fps)
    frames = ((h * 60 + m) * 60 + s) * nominal + f
    if ";" in tc and fps in (Fraction(30000, 1001), Fraction(60000, 1001)):
        minutes = h * 60 + m
        frames -= (nominal // 15) * (minutes - minutes // 10)
    return Fraction(frames) / fps


def inspect_media(path: Path, media_id: str, kind: str) -> dict:
    info = json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]))
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    image = path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    if kind == "music" and not audio:
        raise ValueError("这个文件没有音轨")
    if kind == "clip" and not video:
        raise ValueError("请选择视频或图片素材")
    duration = float(info.get("format", {}).get("duration", 0) or 0)
    if not image and (duration <= 0 or not np.isfinite(duration)):
        raise ValueError("无法读取素材时长")
    if kind == "music" and duration > 7200:
        raise ValueError("单首音乐最长支持 2 小时，请先截取需要的部分")
    rate_text = (video or {}).get("avg_frame_rate", "25")
    rate = Fraction(rate_text) if rate_text not in ("0/0", "N/A") else Fraction(25)
    if rate <= 0:
        rate = Fraction(25)
    tc = (video or {}).get("tags", {}).get("timecode", info.get("format", {}).get("tags", {}).get("timecode", ""))
    meta = dict(id=media_id, name=path.name, path=str(path.resolve()), kind="image" if image else kind,
                duration=duration, fps=str(rate), width=(video or {}).get("width", 0),
                height=(video or {}).get("height", 0), has_audio=bool(audio),
                audio_channels=(audio or {}).get("channels", 2), audio_rate=(audio or {}).get("sample_rate", "48000"),
                source_start=str(source_timecode(tc, rate)))
    folder = media_dir(media_id)
    if kind == "music":
        # The same PCM source is used for listening, analysis and FCP export.
        ffmpeg("-i", str(path), "-vn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(folder / "audio.wav"))
        meta["path"] = str((folder / "audio.wav").resolve())
        meta["duration"] = sf.info(meta["path"]).duration
        meta.update(source_start="0", audio_rate="48000", audio_channels=2)
    elif video:
        ffmpeg("-i", str(path), "-frames:v", "1", "-vf", "scale=320:-2", str(folder / "thumb.jpg"))
    save_media(meta)
    return meta


def analyze(meta: dict, bpm: float | None = None) -> dict:
    import librosa

    folder = media_dir(meta["id"])
    cached = folder / ("analysis.json" if bpm is None else f"analysis-{bpm:g}.json")
    if cached.exists():
        return json.loads(cached.read_text())
    y, sr = librosa.load(meta["path"], sr=22050, mono=True)
    if len(y) < sr // 2 or np.max(np.abs(y)) < 1e-5:
        tempo, beats = 0.0, np.array([])
    else:
        tempo, beats = librosa.beat.beat_track(y=y, sr=sr, hop_length=256, units="time", trim=True, bpm=bpm)
        tempo = float(np.asarray(tempo).item())
    # Min/max pairs retain transients even when the entire song is zoomed out.
    step = max(1, int(np.ceil(len(y) / 3200)))
    padded = np.pad(y, (0, (-len(y)) % step)).reshape(-1, step)
    peaks = np.stack([padded.min(axis=1), padded.max(axis=1)], axis=1).round(5).tolist()
    data = dict(bpm=round(tempo, 2), beats=[round(float(t), 6) for t in beats if 0 <= t < meta["duration"]],
                peaks=peaks, duration=meta["duration"])
    cached.write_text(json.dumps(data))
    return data


def suggest_shortening(meta: dict, markers: list, target: float, tolerance: float) -> list:
    """Rank same-phase joins by spectral similarity; this is not song-form recognition."""
    duration = meta["duration"]
    if target >= duration:
        raise ValueError("目标时长需要短于原音乐")
    beats = sorted({m.time for m in markers if m.enabled and m.time < duration})
    if len(beats) < 12:
        raise ValueError("至少需要 12 个有效节拍，才能建议保留开头和结尾的剪切")
    samples, sr = sf.read(meta["path"], dtype="float32", always_2d=True)
    mono = samples.mean(axis=1)
    features = []
    for t in beats:
        i = int(t * sr)
        window = mono[max(0, i - 8192):i]
        window = np.pad(window, (8192 - len(window), 0))
        spectrum = np.abs(np.fft.rfft(window * np.hanning(8192)))
        # Log-spaced bands compare timbre immediately before both candidate beats.
        edges = np.geomspace(1, len(spectrum), 33).astype(int)
        bands = np.array([np.mean(spectrum[a:max(a + 1, b)]) for a, b in zip(edges[:-1], edges[1:])])
        features.append(bands / (np.linalg.norm(bands) + 1e-9))
    options = []
    for i in range(4, len(beats) - 8):
        desired = beats[i] + duration - target
        j0 = int(np.searchsorted(beats, desired))
        for j in range(max(i + 4, j0 - 8), min(len(beats) - 4, j0 + 9)):
            if (j - i) % 4:
                continue
            actual = duration - (beats[j] - beats[i])
            if abs(actual - target) > tolerance:
                continue
            similarity = float(np.dot(features[i], features[j]))
            options.append(dict(ranges=[[0, beats[i]], [beats[j], duration]], duration=round(actual, 3),
                                similarity=round(similarity, 3), join=round(beats[i], 3),
                                score=similarity - abs(actual - target) / max(1, tolerance) * .15))
    options.sort(key=lambda x: x["score"], reverse=True)
    unique = []
    for candidate in options:
        if all(abs(candidate["join"] - x["join"]) > .5 for x in unique):
            unique.append(candidate)
        if len(unique) == 5:
            break
    if not unique:
        raise ValueError("这个时长附近没有按 4 拍对齐的剪切方案。请增大允许误差或手动选区。")
    return unique
