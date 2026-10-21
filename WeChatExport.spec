# -*- mode: python ; coding: utf-8 -*-
datas = []


a = Analysis(
    ['app.py'],
    pathex=['vendor/wechat-decrypt', 'vendor/wechat-decrypt/scripts/common', 'vendor/wechat-decrypt/scripts/windows'],
    binaries=[],
    datas=datas,
    hiddenimports=['win32timezone', 'imageio_ffmpeg.binaries'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['reportlab', 'pytest', 'numpy', 'scipy', 'matplotlib', 'pandas',
              'IPython', 'lxml', 'frida', 'cryptography'],
    noarchive=False,
    optimize=0,
)
# Keep FFmpeg and Microsoft runtimes external; users install them separately.
def external_runtime(entry):
    name = entry[0].replace('\\', '/').rsplit('/', 1)[-1].lower()
    return (name.startswith(('api-ms-win-', 'vcruntime140'))
            or name in ('msvcp140.dll', 'ucrtbase.dll'))


for collection in (a.datas, a.binaries):
    collection[:] = [entry for entry in collection
                     if not external_runtime(entry)
                     and not (entry[0].replace('\\', '/').startswith('imageio_ffmpeg/binaries/')
                              and entry[0].lower().endswith('.exe'))]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='WeChatExport',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

