from __future__ import annotations

import json
import re
import urllib.request

from app_version import APP_VERSION, LATEST_RELEASE_API_URL, RELEASES_PAGE_URL


def _parse_version(value: str) -> tuple[int, ...]:
    text = str(value or "").strip().lower()
    text = text[1:] if text.startswith("v") else text
    parts = re.findall(r"\d+", text)
    return tuple(int(part) for part in parts[:4]) if parts else (0,)


def _is_newer_version(latest_tag: str, current_version: str = APP_VERSION) -> bool:
    latest = _parse_version(latest_tag)
    current = _parse_version(current_version)
    max_len = max(len(latest), len(current))
    latest += (0,) * (max_len - len(latest))
    current += (0,) * (max_len - len(current))
    return latest > current


def check_for_update(timeout: float = 8.0) -> dict:
    request = urllib.request.Request(
        LATEST_RELEASE_API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "AutoBackup-Updater",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    latest_tag = str(payload.get("tag_name") or "").strip()
    release_url = str(payload.get("html_url") or RELEASES_PAGE_URL)
    asset_url = ""

    for asset in payload.get("assets") or []:
        name = str(asset.get("name") or "")
        if name.lower().endswith(".zip") and name.lower().startswith("autobackup"):
            asset_url = str(asset.get("browser_download_url") or "")
            break

    download_url = asset_url or release_url
    return {
        "current_version": APP_VERSION,
        "latest_version": latest_tag.lstrip("v") if latest_tag else "",
        "latest_tag": latest_tag,
        "has_update": bool(latest_tag and _is_newer_version(latest_tag)),
        "release_url": release_url,
        "download_url": download_url,
        "release_name": str(payload.get("name") or latest_tag),
    }
