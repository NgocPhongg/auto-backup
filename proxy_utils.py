import ipaddress
import json
import threading
import time


_IP_CHECK_ENDPOINTS = (
    ("https://api.ipify.org?format=json", "json"),
    ("https://ipv4.icanhazip.com", "text"),
    ("https://ifconfig.me/ip", "text"),
)
_DIRECT_IP_CACHE = {
    "value": "",
    "fetched_at": 0.0,
}
_DIRECT_IP_CACHE_LOCK = threading.Lock()


def normalize_proxy_type(proxy_type):
    proxy_type = (proxy_type or "http").strip().lower()
    if proxy_type == "socks5h":
        proxy_type = "socks5"
    if proxy_type == "https":
        proxy_type = "http"
    if proxy_type not in ("http", "socks4", "socks5"):
        proxy_type = "http"
    return proxy_type


def parse_proxy_string(proxy_string, proxy_type="http"):
    proxy_string = (proxy_string or "").strip()
    if not proxy_string:
        return None

    proxy_type = normalize_proxy_type(proxy_type)
    if "://" in proxy_string:
        scheme, proxy_string = proxy_string.split("://", 1)
        proxy_type = normalize_proxy_type(scheme)

    username = ""
    password = ""
    if "@" in proxy_string:
        auth_part, proxy_string = proxy_string.rsplit("@", 1)
        if ":" in auth_part:
            username, password = auth_part.split(":", 1)
        else:
            username = auth_part

    parts = proxy_string.split(":", 3)
    if len(parts) < 2:
        return None

    host = parts[0].strip()
    port_text = parts[1].strip()
    if not host or not port_text.isdigit():
        return None

    if not username and len(parts) >= 3:
        username = parts[2].strip()
    if not password and len(parts) >= 4:
        password = parts[3].strip()

    return {
        "mode": proxy_type,
        "host": host,
        "port": int(port_text),
        "username": username.strip(),
        "password": password.strip(),
    }


def build_proxy_url(proxy_string, proxy_type="http"):
    parsed = parse_proxy_string(proxy_string, proxy_type)
    if not parsed:
        return ""
    auth = ""
    if parsed["username"]:
        auth = parsed["username"]
        if parsed["password"]:
            auth += f":{parsed['password']}"
        auth += "@"
    return f"{parsed['mode']}://{auth}{parsed['host']}:{parsed['port']}"


def proxy_display_text(proxy_string, proxy_type="http"):
    parsed = parse_proxy_string(proxy_string, proxy_type)
    if not parsed:
        return ""
    return f"{parsed['mode']}://{parsed['host']}:{parsed['port']}"


def proxy_custom_name(proxy_string, proxy_type="http"):
    parsed = parse_proxy_string(proxy_string, proxy_type)
    if not parsed:
        return ""
    return f"{parsed['host']}:{parsed['port']}"[:80]


def _extract_ip_from_text(text):
    text = str(text or "").strip()
    if not text:
        return ""

    candidates = []
    candidates.append(text)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            candidates.extend([
                data.get("ip", ""),
                data.get("origin", ""),
                data.get("query", ""),
            ])
    except Exception:
        pass

    for candidate in candidates:
        value = str(candidate or "").strip().split(",", 1)[0].strip()
        if not value:
            continue
        try:
            return str(ipaddress.ip_address(value))
        except Exception:
            continue
    return ""


def fetch_public_ip(proxies=None, timeout=8, use_cache=False):
    import requests

    use_cache = bool(use_cache and not proxies)
    if use_cache:
        with _DIRECT_IP_CACHE_LOCK:
            cached_ip = _DIRECT_IP_CACHE.get("value", "")
            fetched_at = float(_DIRECT_IP_CACHE.get("fetched_at", 0.0) or 0.0)
            if cached_ip and (time.time() - fetched_at) < 90.0:
                return cached_ip

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json,text/plain;q=0.9,*/*;q=0.8",
    }
    session = requests.Session()
    session.trust_env = False
    last_error = ""
    for url, _response_kind in _IP_CHECK_ENDPOINTS:
        try:
            response = session.get(
                url,
                proxies=proxies,
                timeout=timeout,
                headers=headers,
            )
            response.raise_for_status()
            ip_text = _extract_ip_from_text(response.text)
            if ip_text:
                if use_cache:
                    with _DIRECT_IP_CACHE_LOCK:
                        _DIRECT_IP_CACHE["value"] = ip_text
                        _DIRECT_IP_CACHE["fetched_at"] = time.time()
                return ip_text
            last_error = f"Khong tach duoc IP tu {url}"
        except Exception as exc:
            last_error = str(exc)
            continue

    raise RuntimeError(last_error or "Khong doc duoc IP public")


def validate_proxy_connection(proxy_string, proxy_type="http", require_ip_change=True, timeout=8):
    proxy_string = (proxy_string or "").strip()
    normalized_type = normalize_proxy_type(proxy_type)
    result = {
        "ok": False,
        "scheme": normalized_type,
        "proxy_ip": "",
        "direct_ip": "",
        "message": "",
        "proxy_url": "",
    }

    if not proxy_string:
        result.update({"ok": True, "message": "Khong dung Proxy"})
        return result

    parsed = parse_proxy_string(proxy_string, normalized_type)
    if not parsed:
        result["message"] = "Sai dinh dang Proxy. Can it nhat IP:Port"
        return result

    requested_scheme = parsed["mode"]
    direct_ip = ""
    if require_ip_change:
        try:
            direct_ip = fetch_public_ip(timeout=timeout, use_cache=True)
        except Exception:
            direct_ip = ""
    result["direct_ip"] = direct_ip

    candidate_schemes = [requested_scheme]
    if requested_scheme == "http":
        candidate_schemes.extend(["socks5", "socks4"])
    elif requested_scheme == "socks5":
        candidate_schemes.extend(["http", "socks4"])
    elif requested_scheme == "socks4":
        candidate_schemes.extend(["http", "socks5"])

    seen = set()
    ordered_candidates = []
    for scheme in candidate_schemes:
        scheme = normalize_proxy_type(scheme)
        if scheme in seen:
            continue
        seen.add(scheme)
        ordered_candidates.append(scheme)

    errors = []
    same_ip_hits = []
    for candidate in ordered_candidates:
        proxy_url = build_proxy_url(proxy_string, candidate)
        if not proxy_url:
            errors.append(f"{candidate.upper()}: sai dinh dang")
            continue

        try:
            proxy_ip = fetch_public_ip(
                proxies={"http": proxy_url, "https": proxy_url},
                timeout=timeout,
            )
        except Exception as exc:
            errors.append(f"{candidate.upper()}: {exc}")
            continue

        if require_ip_change and direct_ip and proxy_ip == direct_ip:
            same_ip_hits.append((candidate, proxy_ip))
            continue

        message = proxy_ip
        if candidate != requested_scheme:
            message = f"{message} (Auto-detected as {candidate.upper()})"
        elif not direct_ip and require_ip_change:
            message = f"{message} (Khong doc duoc IP may that de doi chieu)"

        result.update(
            {
                "ok": True,
                "scheme": candidate,
                "proxy_ip": proxy_ip,
                "message": message,
                "proxy_url": proxy_url,
            }
        )
        return result

    if same_ip_hits:
        same_scheme, same_ip = same_ip_hits[0]
        result.update(
            {
                "scheme": same_scheme,
                "proxy_ip": same_ip,
                "proxy_url": build_proxy_url(proxy_string, same_scheme),
                "message": (
                    f"Proxy co phan hoi nhung IP ra ngoai van la {same_ip}"
                    + (f", trung IP may that {direct_ip}" if direct_ip else "")
                    + ". Proxy nay chua di qua route rieng."
                ),
            }
        )
        return result

    result["message"] = "Proxy khong phan hoi hoac sai auth"
    if errors:
        result["message"] += f": {' | '.join(errors[:3])}"
    return result
