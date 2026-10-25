import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys
import uuid
from fractions import Fraction
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from . import media as storage
from .media import analyze, ffmpeg, inspect_media, load_media, media_dir, suggest_shortening
from .models import Project, ShortenRequest
from .timeline import build_plan, make_fcpxml
from . import narration

app = FastAPI(title="BeatCut Studio", version=__version__)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])


@app.middleware("http")
async def local_requests(request: Request, call_next):
    # Browser POSTs from other origins cannot add this header without a CORS preflight.
    if request.method == "POST" and request.headers.get("x-beatcut") != "1":
        return JSONResponse({"detail": "请从本机 BeatCut 页面操作"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.exception_handler(ValueError)
async def invalid_value(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(subprocess.TimeoutExpired)
async def timeout(request, exc):
    return JSONResponse({"detail": "媒体处理超时，请尝试更短的素材"}, status_code=408)


@app.get("/api/status")
def status():
    return dict(version=__version__, ffmpeg=bool(shutil.which("ffmpeg")), ffprobe=bool(shutil.which("ffprobe")))


def public_media(meta):
    return {k: v for k, v in meta.items() if k != "path"}


@app.post("/api/media")
def upload(file: UploadFile = File(...), kind: str = Form(...)):
    if kind not in ("music", "clip"):
        raise ValueError("请选择音乐或镜头素材")
    name = Path(file.filename or "untitled").name
    if Path(name).suffix.lower() not in {".mp3", ".wav", ".aif", ".aiff", ".m4a", ".aac", ".flac", ".ogg", ".mp4", ".mov", ".m4v", ".mkv", ".webm", ".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        raise ValueError("暂不支持此文件格式")
    media_id = uuid.uuid4().hex
    folder = media_dir(media_id)
    (folder / "original").mkdir(parents=True)
    path = folder / "original" / name
    try:
        with path.open("wb") as output:
            shutil.copyfileobj(file.file, output, length=1024 * 1024)
        meta = inspect_media(path, media_id, kind)
        return public_media(meta)
    except Exception:
        shutil.rmtree(folder)
        raise
    finally:
        file.file.close()


@app.get("/api/media/{media_id}")
def metadata(media_id: str):
    return public_media(load_media(media_id))


@app.get("/api/media/{media_id}/file")
def media_file(media_id: str):
    return FileResponse(load_media(media_id)["path"])


@app.get("/api/media/{media_id}/thumb")
def thumbnail(media_id: str):
    path = media_dir(media_id) / "thumb.jpg"
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


@app.post("/api/analyze/{media_id}")
def analyze_audio(media_id: str, bpm: float | None = Query(default=None, ge=20, le=400)):
    meta = load_media(media_id)
    if meta["kind"] != "music":
        raise ValueError("请选择音乐素材")
    return analyze(meta, bpm)


def prepare(project: Project):
    music = load_media(project.music_id)
    if music["kind"] != "music":
        raise ValueError("项目音乐无效")
    assets = {c.id: load_media(c.id) for c in project.clips}
    if any(m["kind"] not in ("clip", "image") for m in assets.values()):
        raise ValueError("镜头列表只能包含视频和图片")
    return music, assets, build_plan(project, music, assets)


@app.post("/api/plan")
def plan_project(project: Project):
    return prepare(project)[2]


@app.post("/api/project/validate")
def validate_project(project: Project):
    load_media(project.music_id)
    for clip in project.clips:
        load_media(clip.id)
    return project.model_dump()


@app.post("/api/shorten")
def shorten(body: ShortenRequest):
    music = load_media(body.project.music_id)
    return suggest_shortening(music, body.project.markers, body.target, body.tolerance)


def write_audio(music, plan, path):
    samples, sr = sf.read(music["path"], dtype="float32", always_2d=True)
    fps = Fraction(plan["fps"])
    chunks = []
    output_count = 0
    for section in plan["sections"]:
        start = round(Fraction(section["source_frame"]) / fps * sr)
        # Cumulative rounding keeps output audio within one sample of sequence duration.
        end_count = round(Fraction(section["offset"] + section["frames"]) / fps * sr)
        count = end_count - output_count
        chunk = samples[start:start + count]
        if len(chunk) < count:
            chunk = np.pad(chunk, ((0, count - len(chunk)), (0, 0)))
        chunks.append(chunk)
        output_count = end_count
    sf.write(path, np.concatenate(chunks), sr, subtype="PCM_16")


@app.post("/api/preview")
def preview_audio(project: Project):
    music = load_media(project.music_id)
    audio_project = project.model_copy(update={"mode": "markers", "clips": [], "first_id": None, "last_id": None})
    plan = build_plan(audio_project, music, {})
    key = hashlib.sha256(json.dumps([music["id"], project.fps, plan["sections"]]).encode()).hexdigest()
    folder = storage.DATA / "previews"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (key + ".wav")
    if not path.exists():
        write_audio(music, plan, path)
    return dict(url=f"/api/previews/{key}.wav", duration=plan["duration"])


@app.get("/api/previews/{name}")
def get_preview(name: str):
    if not re.fullmatch(r"[a-f0-9]{64}\.wav", name):
        raise HTTPException(404)
    return FileResponse(storage.DATA / "previews" / name)


@app.post("/api/export")
def export(project: Project):
    music, assets, plan = prepare(project)
    export_id = uuid.uuid4().hex
    folder = storage.DATA / "exports" / export_id
    folder.mkdir(parents=True)
    # Still images become regular movie assets, so every cut has a finite editable range.
    for media_id in {s["id"] for s in plan["slots"] if s["id"]}:
        item = assets[media_id]
        if item["kind"] == "image":
            duration = max(s["seconds"] + s["source_in"] for s in plan["slots"] if s["id"] == media_id)
            path = folder / (media_id + ".mov")
            ffmpeg("-loop", "1", "-i", item["path"], "-t", str(duration + .1), "-r", project.fps,
                   "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-preset", "fast", "-crf", "16", "-pix_fmt", "yuv420p", str(path))
            assets[media_id] = dict(item, path=str(path.resolve()), duration=duration + .1,
                                   fps=project.fps, source_start="0", width=item["width"] // 2 * 2,
                                   height=item["height"] // 2 * 2, has_audio=False)
    (folder / "timeline.fcpxml").write_bytes(make_fcpxml(project, plan, music, assets))
    (folder / "project.beatcut.json").write_text(project.model_dump_json(indent=2))
    write_audio(music, plan, folder / "music.wav")
    with (folder / "markers.csv").open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["marker", "timeline_seconds", "source_seconds", "forced_cut"])
        fps = Fraction(project.fps)
        for i, marker in enumerate(plan["markers"]):
            writer.writerow([i + 1, float(Fraction(marker["frame"]) / fps), float(Fraction(marker["source_frame"]) / fps), marker["forced"]])
    return dict(id=export_id, path=str(folder.resolve()), plan=plan,
                files={name: f"/api/exports/{export_id}/{name}" for name in ["timeline.fcpxml", "music.wav", "markers.csv", "project.beatcut.json"]})


@app.get("/api/exports/{export_id}/{name}")
def download(export_id: str, name: str):
    if not re.fullmatch(r"[a-f0-9]{32}", export_id) or name not in {"timeline.fcpxml", "subtitles.srt", "music.wav", "markers.csv", "project.beatcut.json"}:
        raise HTTPException(404)
    return FileResponse(storage.DATA / "exports" / export_id / name, filename=name)


@app.post("/api/reveal/{export_id}")
def reveal(export_id: str):
    if not re.fullmatch(r"[a-f0-9]{32}", export_id):
        raise ValueError("无效的导出编号")
    folder = storage.DATA / "exports" / export_id
    if not folder.exists():
        raise ValueError("导出不存在")
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    return dict(path=str(folder.resolve()))


@app.post("/api/open-fcp/{export_id}")
def open_fcp(export_id: str):
    if not re.fullmatch(r"[a-f0-9]{32}", export_id):
        raise ValueError("无效的导出编号")
    path = storage.DATA / "exports" / export_id / "timeline.fcpxml"
    if not path.exists():
        raise ValueError("请先导出时间线")
    if sys.platform != "darwin":
        raise ValueError("请在 Mac 上打开 FCPXML")
    result = subprocess.run(["open", str(path)], capture_output=True)
    if result.returncode:
        raise ValueError("无法打开 Final Cut Pro，请在 Finder 中手动导入 XML")
    return dict(opened=True)


@app.post("/api/demo")
def demo():
    manifest = storage.DATA / "demo.json"
    if manifest.exists():
        record = json.loads(manifest.read_text())
        try:
            return dict(music=public_media(load_media(record["music"])),
                        clips=[public_media(load_media(i)) for i in record["clips"]])
        except ValueError:
            pass
    media_id = uuid.uuid4().hex
    folder = media_dir(media_id)
    folder.mkdir(parents=True)
    sr, duration = 48000, 24
    y = np.zeros(sr * duration, dtype=np.float32)
    rng = np.random.default_rng(42)
    for i, beat in enumerate(np.arange(.25, duration - .1, .5)):
        t = np.arange(int(sr * .18)) / sr
        kick = np.sin(2 * np.pi * (70 * t - 80 * t * t)) * np.exp(-t * 28) * .7
        if i % 2:
            kick += rng.normal(0, .15, len(t)) * np.exp(-t * 35)
        start = round(beat * sr)
        count = min(len(kick), len(y) - start)
        y[start:start + count] += kick[:count]
    t = np.arange(len(y)) / sr
    y += .045 * np.sin(2 * np.pi * 220 * t) * (1 - np.cos(2 * np.pi * 2 * t))
    path = folder / "120 BPM · 演示节奏.wav"
    sf.write(path, np.column_stack((y, y)), sr, subtype="PCM_16")
    music = inspect_media(path, media_id, "music")
    clips = []
    for i, color in enumerate(["0x5e6c53", "0x8b7051", "0x4d6871"]):
        clip_id = uuid.uuid4().hex
        clip_folder = media_dir(clip_id)
        clip_folder.mkdir(parents=True)
        path = clip_folder / f"练习镜头 {i + 1:02}.mp4"
        vf = f"drawgrid=w=80:h=80:t=1:c=white@0.12,drawbox=x={80+i*100}:y=80:w=200:h=200:color=white@0.18:t=fill"
        ffmpeg("-f", "lavfi", "-i", f"color=c={color}:s=640x360:r=25:d=12", "-vf", vf,
               "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path))
        clips.append(inspect_media(path, clip_id, "clip"))
    manifest.write_text(json.dumps(dict(music=media_id, clips=[c["id"] for c in clips])))
    return dict(music=public_media(music), clips=[public_media(c) for c in clips])


@app.post('/api/narration/align')
def align_narration(body: narration.NarrationRequest):
    return narration.start_alignment(body)


@app.get('/api/narration/jobs/{job_id}')
def narration_job(job_id: str):
    if job_id not in narration.JOBS:
        raise HTTPException(404)
    result = narration.JOBS[job_id].copy()
    result.pop('cancel', None)
    return result


@app.post('/api/narration/jobs/{job_id}/cancel')
def cancel_narration(job_id: str):
    if job_id not in narration.JOBS:
        raise HTTPException(404)
    narration.JOBS[job_id]['cancel'] = True
    return dict(message='将在当前音频片段处理完后取消')


@app.post('/api/narration/validate')
def validate_narration(body: narration.NarrationProject):
    narration.validate_project(body)
    return body.model_dump()


@app.post('/api/narration/export')
def export_narration(body: narration.NarrationProject):
    return narration.export_project(body)


app.mount("/", StaticFiles(directory=storage.ROOT / "web", html=True), name="web")
