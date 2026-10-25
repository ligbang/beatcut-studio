#!/bin/bash
set -e
cd "$(dirname "$0")"
if ! command -v ffmpeg >/dev/null || ! command -v ffprobe >/dev/null; then
  echo '请先安装 FFmpeg：brew install ffmpeg'
  read -r -p '按回车退出…'
  exit 1
fi
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi
.venv/bin/python run.py
