from pathlib import Path
import shutil
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
datas, binaries, hiddenimports = collect_all('librosa')
datas += [(str(root / 'web'), 'web')]
datas += collect_data_files('mlx', includes=['lib/*.metallib'])
binaries += collect_dynamic_libs('mlx')
hiddenimports += collect_submodules('mlx')
hiddenimports += collect_submodules('mlx_audio.stt.models.qwen3_asr')
hiddenimports += ['mlx_audio.stt.models.qwen3_forced_aligner', 'transformers.models.qwen2.tokenization_qwen2',
                  'transformers.models.whisper.feature_extraction_whisper']
for package in ['mlx', 'mlx-metal', 'mlx-audio', 'transformers', 'huggingface-hub', 'tokenizers', 'safetensors']:
    datas += copy_metadata(package)
for package in ['librosa', 'numba', 'scikit-learn', 'scipy', 'soundfile']:
    datas += copy_metadata(package)
for name in ['ffmpeg', 'ffprobe']:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f'Install {name} before building')
    binaries.append((path, 'bin'))
a = Analysis([str(root / 'desktop/backend.py')], pathex=[str(root)], binaries=binaries,
             datas=datas, hiddenimports=hiddenimports + ['uvicorn.logging', 'uvicorn.protocols.http.h11_impl',
             'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on', 'uvicorn.loops.asyncio'],
             excludes=['pytest', 'tkinter', 'matplotlib', 'IPython'],
             module_collection_mode={'librosa': 'py', 'mlx_audio': 'py'})
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='beatcut-engine', console=True,
          target_arch='arm64')
coll = COLLECT(exe, a.binaries, a.datas, name='beatcut-engine')
