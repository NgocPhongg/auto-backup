import html
import json
import re
from urllib.parse import urlencode
from concurrent.futures import ThreadPoolExecutor, as_completed


DEFAULT_ERROR_RESULT = {
    "username": "",
    "ok": False,
    "error": "",
    "follower_count": -1,
    "following_count": -1,
    "heart_count": -1,
    "video_count": -1,
}


def _normalize_username(username):
    username = (username or "").strip()
    if username.startswith("@"):
        username = username[1:]
    if "/" in username:
        username = username.rstrip("/").split("/")[-1]
        if username.startswith("@"):
            username = username[1:]
    return username.strip()


def _to_int(value):
    if value is None:
        return -1
    if isinstance(value, bool):
        return -1
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)

    text = str(value).strip().replace(",", "")
    if not text:
        return -1

    multiplier = 1
    suffix = text[-1:].upper()
    if suffix == "K":
        multiplier = 1_000
        text = text[:-1]
    elif suffix == "M":
        multiplier = 1_000_000
        text = text[:-1]
    elif suffix == "B":
        multiplier = 1_000_000_000
        text = text[:-1]

    try:
        return int(float(text) * multiplier)
    except Exception:
        return -1


def _extract_script_json(response_text):
    patterns = [
        r'<script[^>]+id=["\']__UNIVERSAL_DATA_FOR_REHYDRATION__["\'][^>]*>(.*?)</script>',
        r'<script[^>]+id=["\']SIGI_STATE["\'][^>]*>(.*?)</script>',
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    ]
    for pattern in patterns:
        match = re.search(pattern, response_text, flags=re.DOTALL | re.IGNORECASE)
        if not match:
            continue
        raw_json = html.unescape(match.group(1).strip())
        if not raw_json:
            continue
        try:
            return json.loads(raw_json)
        except json.JSONDecodeError:
            # TikTok sometimes leaves escaped solidus/entity variants. Keep trying other scripts.
            continue

    # Fallback: parse any JSON script. TikTok changes ids more often than the data shape.
    for match in re.finditer(
        r'<script[^>]+type=["\']application/json["\'][^>]*>(.*?)</script>',
        response_text,
        flags=re.DOTALL | re.IGNORECASE,
    ):
        raw_json = html.unescape(match.group(1).strip())
        if not raw_json or raw_json[0] not in "[{":
            continue
        try:
            return json.loads(raw_json)
        except json.JSONDecodeError:
            continue
    return None


def _walk_dicts(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk_dicts(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_dicts(item)


def _extract_stats_from_json(data, username):
    username_lower = username.lower()

    # SIGI_STATE classic shape.
    user_module = data.get("UserModule") if isinstance(data, dict) else None
    if isinstance(user_module, dict):
        users = user_module.get("users") or {}
        stats_map = user_module.get("stats") or {}
        if isinstance(users, dict) and isinstance(stats_map, dict):
            for uid, user in users.items():
                if not isinstance(user, dict):
                    continue
                unique_id = str(user.get("uniqueId") or user.get("unique_id") or "").lower()
                if unique_id == username_lower or not username_lower:
                    stats = stats_map.get(uid) or stats_map.get(str(uid)) or {}
                    if isinstance(stats, dict):
                        return _normalize_stats(stats)

    # UNIVERSAL_DATA common shape: __DEFAULT_SCOPE__ -> webapp.user-detail -> userInfo.
    default_scope = data.get("__DEFAULT_SCOPE__") if isinstance(data, dict) else None
    if isinstance(default_scope, dict):
        for value in default_scope.values():
            if not isinstance(value, dict):
                continue
            user_info = value.get("userInfo")
            if not isinstance(user_info, dict):
                continue
            user = user_info.get("user") or {}
            unique_id = str(user.get("uniqueId") or user.get("unique_id") or "").lower()
            if unique_id and unique_id != username_lower:
                continue
            stats = user_info.get("stats") or user_info.get("statsV2") or {}
            if isinstance(stats, dict):
                return _normalize_stats(stats)

    # Last resort: find any dict that looks like a TikTok stats object.
    for item in _walk_dicts(data):
        keys = set(item.keys())
        if {"followerCount", "followingCount"} & keys and {"heartCount", "videoCount"} & keys:
            return _normalize_stats(item)
        if {"follower_count", "following_count"} & keys and {"heart_count", "video_count"} & keys:
            return _normalize_stats(item)

    return None


def _extract_stats_from_user_detail(data, username):
    if not isinstance(data, dict):
        return None

    user_info = data.get("userInfo") or data.get("user_info") or {}
    if not isinstance(user_info, dict):
        return None

    user = user_info.get("user") or {}
    stats = user_info.get("stats") or user_info.get("statsV2") or {}
    if not isinstance(user, dict) or not isinstance(stats, dict):
        return None

    username_lower = (username or "").lower()
    unique_id = str(user.get("uniqueId") or user.get("unique_id") or "").lower()
    if username_lower and unique_id and unique_id != username_lower:
        return None

    return _normalize_stats(stats)


def _normalize_stats(stats):
    normalized = {
        "follower_count": _to_int(stats.get("followerCount", stats.get("follower_count"))),
        "following_count": _to_int(stats.get("followingCount", stats.get("following_count"))),
        "heart_count": _to_int(stats.get("heartCount", stats.get("heart", stats.get("heart_count")))),
        "video_count": _to_int(stats.get("videoCount", stats.get("video_count"))),
    }
    if any(value >= 0 for value in normalized.values()):
        return normalized
    return None


def _regex_value(response_text, names):
    search_texts = [
        response_text,
        html.unescape(response_text),
        response_text.replace(r"\"", '"').replace(r"\/", "/").replace(r"\u0022", '"'),
        response_text.replace(r'\\"', '"'),
    ]
    for name in names:
        patterns = [
            rf'"{re.escape(name)}"\s*:\s*"?([0-9][0-9.,]*[KMBkmb]?)"?',
            rf'\\"{re.escape(name)}\\"\s*:\s*"?([0-9][0-9.,]*[KMBkmb]?)"?',
            rf'&quot;{re.escape(name)}&quot;\s*:\s*"?([0-9][0-9.,]*[KMBkmb]?)"?',
        ]
        for text in search_texts:
            for pattern in patterns:
                match = re.search(pattern, text, flags=re.IGNORECASE)
                if match:
                    return _to_int(match.group(1))
    return -1


def _extract_stats_from_html(response_text):
    """Last-resort extraction when TikTok embeds escaped JSON outside known script ids."""
    stats = {
        "follower_count": _regex_value(response_text, ["followerCount", "follower_count"]),
        "following_count": _regex_value(response_text, ["followingCount", "following_count"]),
        "heart_count": _regex_value(response_text, ["heartCount", "heart", "heart_count"]),
        "video_count": _regex_value(response_text, ["videoCount", "video_count"]),
    }
    if any(value >= 0 for value in stats.values()):
        return stats

    # Meta description fallback. It is less stable, but better than failing the row.
    meta_match = re.search(
        r'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+content=["\']([^"\']+)["\']',
        response_text,
        flags=re.IGNORECASE,
    )
    if not meta_match:
        meta_match = re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)=["\'](?:description|og:description)["\']',
            response_text,
            flags=re.IGNORECASE,
        )
    if meta_match:
        desc = html.unescape(meta_match.group(1))
        follower = re.search(r'([0-9][0-9.,]*[KMBkmb]?)\s+Followers?', desc, flags=re.IGNORECASE)
        following = re.search(r'([0-9][0-9.,]*[KMBkmb]?)\s+Following', desc, flags=re.IGNORECASE)
        likes = re.search(r'([0-9][0-9.,]*[KMBkmb]?)\s+Likes?', desc, flags=re.IGNORECASE)
        stats = {
            "follower_count": _to_int(follower.group(1)) if follower else -1,
            "following_count": _to_int(following.group(1)) if following else -1,
            "heart_count": _to_int(likes.group(1)) if likes else -1,
            "video_count": -1,
        }
        if any(value >= 0 for value in stats.values()):
            return stats

    return None


def _looks_like_existing_profile(response_text, username):
    text = response_text or ""
    lower = text.lower()
    username_lower = (username or "").lower()
    if username_lower and (
        f"@{username_lower}" in lower
        or f'/{username_lower}"' in lower
        or f'/{username_lower}?' in lower
        or f'"uniqueid":"{username_lower}"' in lower
        or f'\\"uniqueid\\":\\"{username_lower}\\"' in lower
    ):
        return True
    if "couldn't find this account" in lower or "không tìm thấy tài khoản" in lower:
        return False
    if "this account doesn't exist" in lower or "account not found" in lower:
        return False
    return False


def _zero_stats():
    return {
        "follower_count": 0,
        "following_count": 0,
        "heart_count": 0,
        "video_count": 0,
    }


def _base_headers(cookie=""):
    headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9,vi;q=0.8",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "referer": "https://www.google.com/",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
        ),
    }
    if cookie and len(str(cookie)) > 20:
        headers["cookie"] = str(cookie)
    return headers


def _fetch_user_detail_api(requests, username, cookie, timeout):
    params = {
        "uniqueId": username,
        "aid": "1988",
        "app_language": "en",
        "app_name": "tiktok_web",
        "browser_language": "en-US",
        "browser_name": "Mozilla",
        "browser_online": "true",
        "browser_platform": "Win32",
        "browser_version": (
            "5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
        ),
        "channel": "tiktok_web",
        "cookie_enabled": "true",
        "device_platform": "web_pc",
        "focus_state": "true",
        "from_page": "user",
        "history_len": "2",
        "is_fullscreen": "false",
        "is_page_visible": "true",
        "language": "en",
        "os": "windows",
        "priority_region": "VN",
        "referer": "",
        "region": "VN",
        "screen_height": "1080",
        "screen_width": "1920",
        "tz_name": "Asia/Saigon",
        "webcast_language": "en",
    }
    url = "https://www.tiktok.com/api/user/detail/?" + urlencode(params)
    headers = _base_headers(cookie)
    headers.update({
        "accept": "application/json, text/plain, */*",
        "referer": f"https://www.tiktok.com/@{username}",
    })

    last_error = ""
    saw_empty_user = False
    saw_403 = False
    for impersonate in ("chrome110", "chrome136", "chrome124", "chrome"):
        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=timeout,
                impersonate=impersonate,
            )
        except Exception as exc:
            last_error = str(exc)
            continue

        if response.status_code == 403:
            saw_403 = True
            continue
        if response.status_code >= 500:
            last_error = f"http_{response.status_code}"
            continue
        if response.status_code >= 400:
            return None, f"http_{response.status_code}", impersonate

        text = (response.text or "").strip()
        if not text:
            saw_empty_user = True
            continue
        try:
            data = response.json()
        except Exception:
            try:
                data = json.loads(text)
            except Exception as exc:
                last_error = f"api_json_error: {str(exc)[:80]}"
                continue

        stats = _extract_stats_from_user_detail(data, username)
        if stats:
            return stats, "", impersonate

        user_info = data.get("userInfo") if isinstance(data, dict) else None
        user = user_info.get("user") if isinstance(user_info, dict) else None
        if isinstance(user, dict) and not user:
            saw_empty_user = True

    if saw_empty_user:
        return None, "api_empty_user", ""
    if saw_403:
        return None, "api_http_403", ""
    return None, last_error or "api_no_stats", ""


def _fetch_tikwm_public_api(requests, username, timeout):
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "en-US,en;q=0.9,vi;q=0.8",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
        ),
    }
    params = urlencode({"unique_id": username})
    urls = [
        f"https://www.tikwm.com/api/user/info?{params}",
        f"https://tikwm.com/api/user/info?{params}",
    ]

    last_error = ""
    for url in urls:
        for impersonate in ("chrome110", "chrome136", "chrome124", "chrome"):
            try:
                response = requests.get(
                    url,
                    headers=headers,
                    timeout=timeout,
                    impersonate=impersonate,
                )
            except Exception as exc:
                last_error = str(exc)
                continue

            if response.status_code >= 500:
                last_error = f"tikwm_http_{response.status_code}"
                continue
            if response.status_code >= 400:
                last_error = f"tikwm_http_{response.status_code}"
                continue

            try:
                data = response.json()
            except Exception:
                try:
                    data = json.loads(response.text or "{}")
                except Exception as exc:
                    last_error = f"tikwm_json_error: {str(exc)[:80]}"
                    continue

            if data.get("code") not in (0, "0", None):
                last_error = data.get("msg") or "tikwm_not_found"
                continue

            payload = data.get("data") or {}
            stats = payload.get("stats") or (payload.get("user") or {}).get("stats") or {}
            if isinstance(stats, dict):
                normalized = _normalize_stats(stats)
                if normalized:
                    return normalized, "", impersonate

            last_error = "tikwm_no_stats"

    return None, last_error or "tikwm_failed", ""


def fast_update_tiktok_profile(username, cookie="", timeout=20):
    """Fetch public TikTok profile stats without Playwright/CDP."""
    normalized = _normalize_username(username)
    result = dict(DEFAULT_ERROR_RESULT)
    result["username"] = normalized

    if not normalized:
        result["error"] = "missing_username"
        return result

    try:
        from curl_cffi import requests
    except Exception as exc:
        result["error"] = f"missing_curl_cffi: {exc}"
        return result

    try:
        api_stats, api_error, api_impersonate = _fetch_user_detail_api(requests, normalized, cookie, timeout)
        if api_stats:
            counter_keys = ("follower_count", "following_count", "heart_count", "video_count")
            api_stats = {key: _to_int(api_stats.get(key)) for key in counter_keys}
            api_stats = {key: (0 if value < 0 else value) for key, value in api_stats.items()}
            result.update(api_stats)
            result["ok"] = True
            result["source"] = "user_detail_api"
            if api_impersonate:
                result["impersonate"] = api_impersonate
            return result

        tikwm_stats, tikwm_error, tikwm_impersonate = _fetch_tikwm_public_api(requests, normalized, timeout)
        if tikwm_stats:
            counter_keys = ("follower_count", "following_count", "heart_count", "video_count")
            tikwm_stats = {key: _to_int(tikwm_stats.get(key)) for key in counter_keys}
            tikwm_stats = {key: (0 if value < 0 else value) for key, value in tikwm_stats.items()}
            result.update(tikwm_stats)
            result["ok"] = True
            result["source"] = "tikwm_public_api"
            if tikwm_impersonate:
                result["impersonate"] = tikwm_impersonate
            return result

        url = f"https://www.tiktok.com/@{normalized}"
        headers = _base_headers(cookie)
        response = None
        last_error = ""
        saw_403 = False
        saw_waf = False
        for impersonate in ("chrome110", "chrome136", "chrome124", "chrome"):
            try:
                response = requests.get(
                    url,
                    headers=headers,
                    timeout=timeout,
                    impersonate=impersonate,
                )
                result["impersonate"] = impersonate
                if response.status_code == 403:
                    saw_403 = True
                    continue
                if response.status_code < 500:
                    break
            except Exception as exc:
                last_error = str(exc)
                response = None
        if response is None:
            result["error"] = last_error[:200] if last_error else "request_failed"
            return result

        result["http_status"] = response.status_code
        if response.status_code >= 400:
            result["error"] = api_error or tikwm_error or f"http_{response.status_code}"
            return result

        lower_text = (response.text or "").lower()
        if "slardarwaf" in lower_text or "_wafchallengeid" in lower_text or "please wait" in lower_text:
            saw_waf = True

        data = _extract_script_json(response.text)
        stats = _extract_stats_from_json(data, normalized) if data is not None else None
        if not stats:
            stats = _extract_stats_from_html(response.text)

        if not stats:
            title_match = re.search(r"<title[^>]*>(.*?)</title>", response.text, flags=re.DOTALL | re.IGNORECASE)
            page_title = html.unescape(title_match.group(1).strip()) if title_match else ""
            if saw_waf:
                result["error"] = api_error or tikwm_error or "waf_challenge"
            elif saw_403:
                result["error"] = api_error or tikwm_error or "http_403"
            else:
                result["error"] = f"stats_not_found{': ' + page_title[:80] if page_title else ''}"
            return result

        counter_keys = ("follower_count", "following_count", "heart_count", "video_count")
        stats = {key: _to_int(stats.get(key)) for key in counter_keys}
        stats = {key: (0 if value < 0 else value) for key, value in stats.items()}
        result.update(stats)
        result["ok"] = True
        result["source"] = "profile_html"
        return result
    except Exception as exc:
        result["error"] = str(exc)[:200]
        return result


def update_all_profiles_async(username_list, max_workers=15):
    """Fetch many public TikTok profiles concurrently. Returns {original_username: result}."""
    entries = list(username_list or [])
    if not entries:
        return {}

    jobs = []
    for entry in entries:
        if isinstance(entry, dict):
            username = entry.get("username") or entry.get("tiktok_id") or entry.get("id_tiktok") or ""
            key = str(entry.get("key") or username)
            cookie = entry.get("cookie") or ""
        else:
            username = entry
            key = str(entry)
            cookie = ""
        jobs.append((key, username, cookie))

    workers = max(1, min(int(max_workers or 15), 20, len(jobs)))
    results = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(fast_update_tiktok_profile, username, cookie=cookie): (key, username)
            for key, username, cookie in jobs
        }
        for future in as_completed(future_map):
            original, username = future_map[future]
            try:
                results[original] = future.result()
            except Exception as exc:
                failed = dict(DEFAULT_ERROR_RESULT)
                failed["username"] = _normalize_username(username)
                failed["error"] = str(exc)[:200]
                results[original] = failed
    return results
