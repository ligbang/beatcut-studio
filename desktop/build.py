"""Build a self-contained macOS app on the target Mac; no Python needed to run it."""
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / 'dist/BeatCut Studio.app'


def run(*args):
    subprocess.run(args, cwd=ROOT, check=True)


if __name__ == '__main__':
    run(sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', 'desktop/backend.spec')
    if APP.exists():
        shutil.rmtree(APP)
    contents = APP / 'Contents'
    resources = contents / 'Resources'
    (contents / 'MacOS').mkdir(parents=True)
    resources.mkdir()
    shutil.copytree(ROOT / 'dist/beatcut-engine', resources / 'beatcut-engine', symlinks=True)
    for name in ['LICENSE', 'README.md']:
        shutil.copy(ROOT / name, resources)
    run('xcrun', 'swiftc', '-O', '-target', 'arm64-apple-macosx14.0', 'desktop/App.swift',
        '-framework', 'AppKit', '-framework', 'WebKit', '-o', str(contents / 'MacOS/BeatCut'))
    run('xcrun', 'swift', 'desktop/Icon.swift', str(ROOT / 'build/BeatCut.iconset'))
    run('iconutil', '-c', 'icns', str(ROOT / 'build/BeatCut.iconset'), '-o', str(resources / 'BeatCut.icns'))
    # Homebrew libraries can require a newer OS than the Swift shell itself.
    minimum = (14, 0)
    for path in (resources / 'beatcut-engine').rglob('*'):
        if path.is_file() and not path.is_symlink() and (path.suffix in ('.dylib', '.so') or path.name in ('ffmpeg', 'ffprobe', 'beatcut-engine', 'Python')):
            result = subprocess.run(['xcrun', 'vtool', '-show-build', str(path)], capture_output=True, text=True)
            for value in re.findall(r'minos ([\d.]+)', result.stdout):
                minimum = max(minimum, tuple(map(int, value.split('.'))))
    info = dict(CFBundleName='BeatCut Studio', CFBundleDisplayName='拍点工坊',
                CFBundleIdentifier='io.github.ligbang.beatcut-studio', CFBundleExecutable='BeatCut',
                CFBundlePackageType='APPL', CFBundleShortVersionString='0.2.1', CFBundleVersion='1',
                CFBundleIconFile='BeatCut', LSMinimumSystemVersion='.'.join(map(str, minimum)), NSHighResolutionCapable=True,
                NSAppTransportSecurity={'NSAllowsLocalNetworking': True},
                NSHumanReadableCopyright='MIT · BeatCut Studio contributors')
    (contents / 'Info.plist').write_bytes(plistlib.dumps(info))
    run('codesign', '--force', '--deep', '--sign', '-', str(APP))
    run('codesign', '--verify', '--deep', '--strict', str(APP))
    print(f'Built: {APP}')
