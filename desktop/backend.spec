from pathlib import Path
import shutil
from PyInstaller.utils.hooks import collect_all, copy_metadata

root = Path(SPECPATH).parent
datas, binaries, hiddenimports = collect_all('librosa')
datas += [(str(root / 'web'), 'web')]
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
             module_collection_mode={'librosa': 'py'})
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='beatcut-engine', console=True,
          target_arch='arm64')
coll = COLLECT(exe, a.binaries, a.datas, name='beatcut-engine')
