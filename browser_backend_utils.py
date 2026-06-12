from __future__ import annotations

import re
import time
import uuid

LOCAL_CHROME_BACKEND = "local_chrome"
GOLOGIN_BACKEND = "gologin"
STEALTH_FIREFOX_BACKEND = "stealth_firefox"

LOCAL_CHROME_PREFIX = "local_chrome:"
STEALTH_FIREFOX_PREFIX = "stealth_firefox:"


def normalize_browser_backend(value) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"local", "chrome", "localchrome", "local_chrome", "local_chrome_backend"}:
        return LOCAL_CHROME_BACKEND
    if text in {"stealth_firefox", "stealthfirefox", "firefox", "invisible_firefox", "invisible_playwright"}:
        return STEALTH_FIREFOX_BACKEND
    return GOLOGIN_BACKEND


def is_local_chrome_backend(value) -> bool:
    return normalize_browser_backend(value) == LOCAL_CHROME_BACKEND


def is_gologin_backend(value) -> bool:
    return normalize_browser_backend(value) == GOLOGIN_BACKEND


def is_stealth_firefox_backend(value) -> bool:
    return normalize_browser_backend(value) == STEALTH_FIREFOX_BACKEND


def browser_backend_label(value) -> str:
    backend = normalize_browser_backend(value)
    if backend == LOCAL_CHROME_BACKEND:
        return "Local Chrome"
    if backend == STEALTH_FIREFOX_BACKEND:
        return "Stealth Firefox"
    return "GoLogin"


def infer_browser_backend(profile_data: dict | None) -> str:
    data = dict(profile_data or {})
    browser_id = str(data.get("browser_id") or "").strip()
    gologin_profile_id = str(data.get("gologin_profile_id") or "").strip()
    backend = normalize_browser_backend(data.get("browser_backend"))

    if browser_id.startswith(LOCAL_CHROME_PREFIX):
        return LOCAL_CHROME_BACKEND
    if browser_id.startswith(STEALTH_FIREFOX_PREFIX):
        return STEALTH_FIREFOX_BACKEND
    if backend == LOCAL_CHROME_BACKEND:
        return LOCAL_CHROME_BACKEND
    if backend == STEALTH_FIREFOX_BACKEND:
        return STEALTH_FIREFOX_BACKEND
    if gologin_profile_id:
        return GOLOGIN_BACKEND
    if re.fullmatch(r"[0-9a-fA-F]{24}", browser_id):
        return GOLOGIN_BACKEND
    return backend


def sanitize_browser_id_part(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "profile"


def make_local_chrome_browser_id(profile_name: str = "") -> str:
    base = sanitize_browser_id_part(profile_name)[:40]
    stamp = time.strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:6]
    return f"{LOCAL_CHROME_PREFIX}{base}_{stamp}_{suffix}"


def make_stealth_firefox_browser_id(profile_name: str = "") -> str:
    base = sanitize_browser_id_part(profile_name)[:40]
    stamp = time.strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:6]
    return f"{STEALTH_FIREFOX_PREFIX}{base}_{stamp}_{suffix}"


def local_chrome_storage_key(browser_id: str) -> str:
    text = str(browser_id or "").strip()
    if text.startswith(LOCAL_CHROME_PREFIX):
        text = text[len(LOCAL_CHROME_PREFIX):]
    return sanitize_browser_id_part(text)


def stealth_firefox_storage_key(browser_id: str) -> str:
    text = str(browser_id or "").strip()
    if text.startswith(STEALTH_FIREFOX_PREFIX):
        text = text[len(STEALTH_FIREFOX_PREFIX):]
    return sanitize_browser_id_part(text)


def ensure_profile_backend_defaults(profile_data: dict | None) -> dict:
    data = dict(profile_data or {})
    backend = infer_browser_backend(data)
    data["browser_backend"] = backend
    if backend == LOCAL_CHROME_BACKEND:
        browser_id = str(data.get("browser_id") or "").strip()
        if not browser_id or not browser_id.startswith(LOCAL_CHROME_PREFIX):
            data["browser_id"] = make_local_chrome_browser_id(data.get("ten_ho_so", ""))
        data["gologin_profile_id"] = ""
    elif backend == STEALTH_FIREFOX_BACKEND:
        browser_id = str(data.get("browser_id") or "").strip()
        if not browser_id or not browser_id.startswith(STEALTH_FIREFOX_PREFIX):
            data["browser_id"] = make_stealth_firefox_browser_id(data.get("ten_ho_so", ""))
        data["gologin_profile_id"] = ""
    else:
        browser_id = str(data.get("browser_id") or "").strip()
        gologin_profile_id = str(data.get("gologin_profile_id") or "").strip()
        if not gologin_profile_id and re.fullmatch(r"[0-9a-fA-F]{24}", browser_id):
            data["gologin_profile_id"] = browser_id
    return data
