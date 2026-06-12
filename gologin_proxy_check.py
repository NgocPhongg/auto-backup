from urllib.parse import urlparse

from proxy_utils import (
    normalize_proxy_type,
    parse_proxy_string,
    proxy_custom_name,
    proxy_display_text,
    validate_proxy_connection,
)

API_URL = "https://api.gologin.com"


def _clean_text(value):
    return str(value or "").strip()


def _parse_auto_proxy_server(server):
    server = _clean_text(server)
    if not server:
        return "", "", ""
    if "://" not in server:
        server = f"http://{server}"
    parsed = urlparse(server)
    host = _clean_text(parsed.hostname)
    port = str(parsed.port or "").strip()
    mode = normalize_proxy_type(parsed.scheme or "http")
    return mode, host, port


def profile_proxy_to_connection(profile):
    """Extract the runnable proxy from a GoLogin profile info-for-run payload."""
    if not isinstance(profile, dict):
        return {
            "has_proxy": False,
            "proxy_string": "",
            "proxy_type": "http",
            "display": "",
            "message": "Khong doc duoc thong tin GoLogin profile",
        }

    proxy = profile.get("proxy") or {}
    if not isinstance(proxy, dict):
        proxy = {}

    raw_mode = _clean_text(proxy.get("mode")).lower()
    if not proxy or raw_mode in ("", "none", "direct"):
        return {
            "has_proxy": False,
            "proxy_string": "",
            "proxy_type": "http",
            "display": "",
            "message": "",
        }

    username = _clean_text(proxy.get("username"))
    password = _clean_text(proxy.get("password"))
    host = _clean_text(proxy.get("host"))
    port = _clean_text(proxy.get("port"))
    mode = raw_mode

    if raw_mode in ("gologin", "tor"):
        mode, host, port = _parse_auto_proxy_server(profile.get("autoProxyServer"))
        username = _clean_text(profile.get("autoProxyUsername")) or username
        password = _clean_text(profile.get("autoProxyPassword")) or password

    if mode == "geolocation":
        mode = "http"
    mode = normalize_proxy_type(mode)

    display = f"{mode}://{host}:{port}" if host and port else raw_mode or "proxy"
    if not host or not str(port).isdigit():
        return {
            "has_proxy": True,
            "proxy_string": "",
            "proxy_type": mode,
            "display": display,
            "message": f"GoLogin proxy sai dinh dang: {display}",
        }

    proxy_string = f"{host}:{port}"
    if username or password:
        proxy_string = f"{proxy_string}:{username}:{password}"

    return {
        "has_proxy": True,
        "proxy_string": proxy_string,
        "proxy_type": mode,
        "display": proxy_display_text(proxy_string, mode) or display,
        "message": "",
    }

def proxy_payload_from_string(proxy_string, proxy_type="http"):
    parsed = parse_proxy_string(proxy_string, proxy_type)
    if not parsed:
        return None
    proxy_string_for_name = f"{parsed.get('host')}:{parsed.get('port')}"
    return {
        "mode": normalize_proxy_type(parsed.get("mode")),
        "host": parsed.get("host", ""),
        "port": int(parsed.get("port") or 80),
        "username": parsed.get("username", ""),
        "password": parsed.get("password", ""),
        "changeIpUrl": "",
        "autoProxyRegion": "",
        "torProxyRegion": "",
        "customName": proxy_custom_name(proxy_string_for_name, parsed.get("mode")),
    }

def none_proxy_payload():
    return {
        "mode": "none",
        "host": "",
        "port": 80,
        "username": "",
        "password": "",
        "changeIpUrl": "",
        "autoProxyRegion": "",
        "torProxyRegion": "",
        "customName": "",
    }

def _patch_profile_proxy(token, profile_id, payload, timeout=25):
    import requests

    token = _clean_text(token)
    profile_id = _clean_text(profile_id)
    if not token:
        return False, "Thieu GoLogin API key"
    if not profile_id:
        return False, "Thieu GoLogin Profile ID"
    if not isinstance(payload, dict):
        return False, "Proxy payload khong hop le"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.patch(
            f"{API_URL}/browser/{profile_id}/proxy",
            headers=headers,
            json=payload,
            timeout=timeout,
        )
    except Exception as exc:
        return False, f"Loi ket noi API GoLogin khi cap nhat proxy: {exc}"

    if response.status_code in (200, 201, 204):
        return True, "OK"

    detail = (response.text or "").strip().replace("\n", " ")[:300]
    return False, f"GoLogin proxy API loi HTTP {response.status_code}: {detail}"

def set_profile_proxy(token, profile_id, proxy_string, proxy_type="http", timeout=25):
    payload = proxy_payload_from_string(proxy_string, proxy_type)
    if not payload:
        return False, "Sai dinh dang Proxy. Can it nhat IP:Port"
    ok, message = _patch_profile_proxy(token, profile_id, payload, timeout=timeout)
    if not ok:
        return False, message
    return True, proxy_display_text(proxy_string, payload.get("mode")) or f"{payload.get('host')}:{payload.get('port')}"

def clear_profile_proxy(token, profile_id, timeout=25):
    ok, message = _patch_profile_proxy(token, profile_id, none_proxy_payload(), timeout=timeout)
    if not ok:
        return False, message
    return True, "GoLogin proxy da duoc xoa"


def validate_profile_proxy(profile, timeout=8):
    proxy_info = profile_proxy_to_connection(profile)
    if not proxy_info.get("has_proxy"):
        return {
            "ok": True,
            "skipped": True,
            "proxy_info": proxy_info,
            "message": proxy_info.get("message", ""),
        }

    proxy_string = proxy_info.get("proxy_string", "")
    if not proxy_string:
        return {
            "ok": False,
            "skipped": False,
            "proxy_info": proxy_info,
            "message": proxy_info.get("message") or "GoLogin proxy khong hop le",
        }

    result = validate_proxy_connection(
        proxy_string,
        proxy_type=proxy_info.get("proxy_type", "http"),
        require_ip_change=True,
        timeout=timeout,
    )
    result["skipped"] = False
    result["proxy_info"] = proxy_info

    display = proxy_info.get("display", "")
    if result.get("ok"):
        proxy_ip = _clean_text(result.get("proxy_ip"))
        detail = f"GoLogin proxy OK: {display}"
        if proxy_ip:
            detail += f" -> {proxy_ip}"
        result["message"] = detail
        return result

    result["message"] = f"GoLogin proxy loi: {display} - {result.get('message') or 'khong ket noi duoc'}"
    return result
