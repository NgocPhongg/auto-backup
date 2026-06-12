# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import zipfile

from PyInstaller.utils.hooks import collect_all


ROOT = Path.cwd()


def ensure_zero_profile_zip() -> str:
    existing_zip = ROOT / "gologin_zeroprofile.zip"
    if existing_zip.is_file():
        return str(existing_zip)

    source_dir = ROOT / "gologin_zeroprofile"
    if not source_dir.is_dir():
        raise FileNotFoundError(
            "Missing GoLogin zero profile asset. Expected gologin_zeroprofile.zip "
            "or gologin_zeroprofile/ in the workspace root."
        )

    cache_dir = ROOT / ".tmp_builds"
    cache_dir.mkdir(parents=True, exist_ok=True)
    generated_zip = cache_dir / "gologin_zeroprofile.zip"

    with zipfile.ZipFile(generated_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for item in source_dir.rglob("*"):
            if item.is_dir():
                continue
            arcname = Path("gologin_zeroprofile") / item.relative_to(source_dir)
            zf.write(item, arcname.as_posix())

    return str(generated_zip)


datas = [(ensure_zero_profile_zip(), '.'), ('proxy_auth_ext', 'proxy_auth_ext'), ('proxy_ext_0', 'proxy_ext_0'), ('assets', 'assets')]
binaries = []
hiddenimports = ['curl_cffi', 'msal', 'pandas', 'yt_dlp']
tmp_ret = collect_all('playwright')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('gologin')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main_gui.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AutoBackup',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\app_icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AutoBackup',
)
