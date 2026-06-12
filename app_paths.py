from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

APP_NAME = "AutoBackup"

JSON_DEFAULTS = {
    "accounts_data.json": [],
    "email_accounts.json": [],
    "feed_settings.json": {},
    "projects.json": [],
    "gologin_settings.json": {
        "api_key": "",
        "use_gologin_cloud": False,
        "gologin_folder_name": "",
    },
}

DATA_DIRS = (
    "browser_profiles",
    "profiles",
    "logs",
    "backups",
    "temp",
    "gologin_profiles",
    "local_chrome_profiles",
    "stealth_firefox_profiles",
)

LOCAL_CHROME_DISPOSABLE_TEST_PREFIXES = (
    "fresh_",
    "smoke",
    "test_launch_",
    "test_portable_check",
    "codexcheck_",
)


def app_root_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def app_data_dir() -> Path:
    base = os.getenv("APPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / f".{APP_NAME}"


def resource_path(name: str) -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidate = Path(sys._MEIPASS) / name
        if candidate.exists():
            return candidate
    return app_root_dir() / name

def tool_dir_path(name: str) -> Path:
    """Find external tool directories from source, release root, or bundled runtime data."""
    root_candidate = app_root_dir() / name
    candidates = [root_candidate]

    try:
        bundled_candidate = resource_path(name)
    except Exception:
        bundled_candidate = root_candidate

    if bundled_candidate not in candidates:
        candidates.append(bundled_candidate)

    internal_candidate = app_root_dir() / "_internal" / name
    if internal_candidate not in candidates:
        candidates.append(internal_candidate)

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return root_candidate


def _is_gologin_orbita_chrome(path: Path) -> bool:
    text = str(path).lower().replace("/", "\\")
    return "\\.gologin\\browser\\orbita-browser-" in text and path.name.lower() == "chrome.exe"


def find_orbita_browser_exe(preferred_path: str | None = None) -> str | None:
    candidates: list[Path] = []
    if preferred_path:
        candidates.append(Path(preferred_path))
    candidates.extend([
        Path(r"C:\Program Files\SSMATool\browser\orbita-browser.exe"),
        app_root_dir() / "browser" / "orbita-browser.exe",
    ])

    gologin_browser_root = Path.home() / ".gologin" / "browser"
    if gologin_browser_root.exists():
        candidates.extend(sorted(gologin_browser_root.glob("orbita-browser-*/chrome.exe"), reverse=True))

    for candidate in candidates:
        if not candidate.exists() or not candidate.is_file():
            continue
        if candidate.name.lower() == "orbita-browser.exe" or _is_gologin_orbita_chrome(candidate):
            return str(candidate)
    return None


def require_orbita_browser_exe(preferred_path: str | None = None) -> str:
    exe_path = find_orbita_browser_exe(preferred_path)
    if exe_path:
        return exe_path
    raise FileNotFoundError(
        "Khong tim thay Orbita/GoLogin browser. Hay cai/mo GoLogin de tai Orbita, "
        "hoac dat orbita-browser.exe trong thu muc browser cua app."
    )

def find_chrome_exe(preferred_path: str | None = None) -> str | None:
    """Find the shared Chrome binary for Local Chrome backend."""
    candidates: list[Path] = []
    if preferred_path:
        candidates.append(Path(preferred_path))
    candidates.extend([
        # Uu tien chrome-win64 portable dat canh app
        app_root_dir() / "chrome-win64" / "chrome.exe",
        app_root_dir() / "browser" / "chrome.exe",
        tool_dir_path("chrome-win64") / "chrome.exe",
        # Fallback Chrome he thong
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ])

    seen = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists() and candidate.is_file() and candidate.name.lower() == "chrome.exe":
            return str(candidate)
    return None


def require_chrome_exe(preferred_path: str | None = None) -> str:
    exe_path = find_chrome_exe(preferred_path)
    if exe_path:
        return exe_path
    raise FileNotFoundError(
        "Khong tim thay Chrome cho Local Chrome backend. Hay dat chrome.exe tai "
        "chrome-win64\\chrome.exe trong thu muc app, hoac cai Google Chrome tren may."
    )

def find_stealth_firefox_exe(preferred_path: str | None = None) -> str | None:
    candidates: list[Path] = []
    if preferred_path:
        candidates.append(Path(preferred_path))
    candidates.extend([
        app_root_dir() / "stealth_firefox" / "firefox.exe",
        app_root_dir() / "stealth_firefox" / "firefox" / "firefox.exe",
        tool_dir_path("stealth_firefox") / "firefox.exe",
        tool_dir_path("stealth_firefox") / "firefox" / "firefox.exe",
    ])
    try:
        from invisible_playwright import ensure_binary

        cached_path = ensure_binary()
        if cached_path:
            candidates.append(Path(cached_path))
    except Exception:
        pass

    seen = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.exists() and candidate.is_file() and candidate.name.lower() == "firefox.exe":
            return str(candidate)
    return None

def require_stealth_firefox_exe(preferred_path: str | None = None) -> str:
    exe_path = find_stealth_firefox_exe(preferred_path)
    if exe_path:
        return exe_path
    raise FileNotFoundError(
        "Khong tim thay Firefox patched cho Stealth Firefox backend. "
        "Hay dat firefox.exe tai stealth_firefox\\firefox.exe trong thu muc app, "
        "hoac cai invisible_playwright va chay: python -m invisible_playwright fetch"
    )


def ensure_dir(name: str | None = None) -> Path:
    path = app_data_dir() if name is None else app_data_dir() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_file(name: str, default=None) -> Path:
    ensure_dir()
    path = app_data_dir() / name
    _copy_legacy_json_once(name, path, default)
    if not path.exists() and default is not None:
        write_json_atomic(path, default)
    return path


def data_dir(name: str) -> Path:
    return ensure_dir(name)


def browser_profiles_dir() -> Path:
    return data_dir("browser_profiles")


def named_browser_profile_dir(profile_name: str) -> Path:
    safe_name = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in str(profile_name or ""))
    return browser_profiles_dir() / safe_name

def local_chrome_profiles_root() -> Path:
    return app_root_dir() / "local_chrome_profiles"

def local_chrome_check_profiles_root() -> Path:
    path = app_root_dir() / ".codex_tmp" / "local_chrome_checks"
    path.mkdir(parents=True, exist_ok=True)
    return path

def local_chrome_check_profile_dir(check_name: str) -> Path:
    safe_name = "".join(
        c if c.isalnum() or c in ("_", "-") else "_"
        for c in str(check_name or "")
    ).strip("_") or "check"
    path = local_chrome_check_profiles_root() / safe_name
    path.mkdir(parents=True, exist_ok=True)
    return path

def is_local_chrome_disposable_test_dir_name(name: str) -> bool:
    text = str(name or "").strip().lower()
    if not text:
        return False
    if text in {"smoke", "smoke2", "fresh_profile_never_used", "test_portable_check"}:
        return True
    return any(text.startswith(prefix) for prefix in LOCAL_CHROME_DISPOSABLE_TEST_PREFIXES)



def local_chrome_profile_dir(browser_id: str) -> Path:
    from browser_backend_utils import local_chrome_storage_key

    key = local_chrome_storage_key(browser_id)
    path = local_chrome_profiles_root() / key
    path.mkdir(parents=True, exist_ok=True)
    return path

def stealth_firefox_profiles_root() -> Path:
    return app_root_dir() / "stealth_firefox_profiles"

def stealth_firefox_profile_dir(browser_id: str) -> Path:
    from browser_backend_utils import stealth_firefox_storage_key

    key = stealth_firefox_storage_key(browser_id)
    path = stealth_firefox_profiles_root() / key
    path.mkdir(parents=True, exist_ok=True)
    return path


def gologin_profiles_root() -> Path:
    return data_dir("gologin_profiles")


def gologin_base_dir() -> Path:
    path = gologin_profiles_root() / "base"
    path.mkdir(parents=True, exist_ok=True)
    return path


def gologin_profile_dir(browser_id: str = "", profile_index: int | None = None) -> Path:
    root = gologin_profiles_root()
    browser_id = str(browser_id or "").strip()
    if browser_id:
        path = root / f"profile_{browser_id}"
    elif profile_index in (None, 0):
        path = root / "base"
    else:
        path = root / f"base_{profile_index}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json_atomic(path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _copy_legacy_json_once(name: str, dst: Path, default=None) -> None:
    src = app_root_dir() / name
    if not src.exists() or not src.is_file():
        return
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return
    if default is None:
        return
    current = _read_json_safely(dst)
    legacy = _read_json_safely(src)
    if current == default and legacy not in (None, default):
        shutil.copy2(src, dst)


def _copy_dir_once(src: Path, dst: Path) -> None:
    if not src.exists() or not src.is_dir():
        return
    if not dst.exists():
        shutil.copytree(src, dst)
        return
    if any(dst.iterdir()):
        return
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)

def _read_json_safely(path: Path, fallback=None):
    try:
        if path.exists() and path.is_file():
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        return fallback
    return fallback

def _collect_browser_ids(value) -> set[str]:
    ids: set[str] = set()
    if isinstance(value, dict):
        for key in ("browser_id", "gologin_profile_id"):
            browser_id = str(value.get(key) or "").strip()
            if browser_id:
                ids.add(browser_id)
        columns = value.get("columns")
        if isinstance(columns, dict):
            browser_id = str(columns.get("4") or "").strip()
            if browser_id:
                ids.add(browser_id)
        for child in value.values():
            ids.update(_collect_browser_ids(child))
    elif isinstance(value, list):
        for child in value:
            ids.update(_collect_browser_ids(child))
    return ids

def _migrate_legacy_gologin_data() -> None:
    root = app_root_dir()
    gologin_root = gologin_profiles_root()
    legacy_drive = Path("D:/")

    account_data = _read_json_safely(root / "accounts_data.json", [])
    for browser_id in _collect_browser_ids(account_data):
        dst = gologin_root / f"profile_{browser_id}"
        for src in (legacy_drive / f"profile_{browser_id}", root / f"profile_{browser_id}"):
            if src.exists() and src.is_dir():
                _copy_dir_once(src, dst)
                break

    _copy_dir_once(legacy_drive / "Testgologin", gologin_root / "base")
    try:
        for src in legacy_drive.glob("Testgologin_*"):
            suffix = src.name.removeprefix("Testgologin_")
            if suffix:
                _copy_dir_once(src, gologin_root / f"base_{suffix}")
    except Exception:
        pass


def migrate_legacy_data() -> None:
    root = app_root_dir()
    ensure_dir()

    for dirname in DATA_DIRS:
        ensure_dir(dirname)

    for filename, default in JSON_DEFAULTS.items():
        dst = app_data_dir() / filename
        _copy_legacy_json_once(filename, dst, default)
        if not dst.exists():
            write_json_atomic(dst, default)

    for dirname in ("browser_profiles", "profiles"):
        _copy_dir_once(root / dirname, app_data_dir() / dirname)

    _migrate_legacy_gologin_data()


def init_app_data() -> Path:
    migrate_legacy_data()
    return app_data_dir()
