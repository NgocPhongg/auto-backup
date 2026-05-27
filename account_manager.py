import csv
import json
import os
import requests
import sqlite3
import time
import concurrent.futures
from datetime import datetime
from urllib.parse import urlencode

class AccountManager:
    def __init__(self):
        self.accounts = []
        self.violation_keywords = [
            "violation", "violated", "guidelines", "community guidelines",
            "banned", "ban", "suspend", "suspended", "mute", "muted",
            "copyright", "strike", "warning", "restricted", "removed",
            "vi phạm", "nguyên tắc", "cộng đồng", "cấm", "đình chỉ",
            "tắt tiếng", "bản quyền", "cảnh báo", "hạn chế", "gỡ",
            "đóng băng", "khóa tài khoản",
        ]
        self.ignore_inbox_keywords = [
            "welcome to tiktok", "chào mừng bạn đến với tiktok",
            "discover effects", "khám phá hiệu ứng", "new effects",
            "suggested accounts", "tài khoản được đề xuất",
            "new follower", "started following you", "đã bắt đầu follow",
            "liked your video", "đã thích video", "mentioned you",
        ]

    def import_from_csv(self, file_path):
        """
        Đọc danh sách tài khoản từ file CSV (Username, Password, Cookie, Proxy).
        """
        try:
            with open(file_path, mode='r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                self.accounts = [row for row in reader]
            print(f"Đã nhập {len(self.accounts)} tài khoản.")
        except Exception as e:
            print(f"Lỗi khi nhập CSV: {e}")

    def check_live_cookie(self, account):
        """
        Kiểm tra trạng thái Cookie TikTok (Live/Die).
        """
        cookie_string = account.get('Cookie', '')
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Cookie": cookie_string,
            "Referer": "https://www.tiktok.com/"
        }
        
        try:
            # API endpoint kiểm tra thông tin user
            url = "https://www.tiktok.com/api/user/detail/?uniqueId=current_user"
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                # Kiểm tra xem có dữ liệu user trả về không
                if data.get('userInfo'):
                    account['Status'] = 'Live'
                    return 'Live'
            
            account['Status'] = 'Die'
            return 'Die'
        except Exception:
            account['Status'] = 'Die'
            return 'Die'

    def _parse_cookie_string(self, cookie_text):
        cookie_dict = {}
        for pair in (cookie_text or "").split(";"):
            pair = pair.strip()
            if "=" in pair:
                key, value = pair.split("=", 1)
                cookie_dict[key.strip()] = value.strip()
        return cookie_dict

    def _cookie_from_json(self, data):
        if isinstance(data, str):
            return data

        if isinstance(data, dict):
            for key in ("cookie", "Cookie", "cookie_string", "cookies"):
                if key in data:
                    value = data.get(key)
                    if isinstance(value, str):
                        return value
                    nested = self._cookie_from_json(value)
                    if nested:
                        return nested

            simple_pairs = []
            for key, value in data.items():
                if isinstance(value, (str, int, float)) and key not in ("domain", "path", "expires"):
                    simple_pairs.append(f"{key}={value}")
            if simple_pairs:
                return "; ".join(simple_pairs)

        if isinstance(data, list):
            pairs = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                name = item.get("name")
                value = item.get("value")
                domain = item.get("domain") or item.get("host_key") or ""
                if name and value is not None and ("tiktok" in domain or not domain):
                    pairs.append(f"{name}={value}")
            if pairs:
                return "; ".join(pairs)

        return ""

    def _cookie_from_sqlite(self, db_path):
        pairs = []
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                cur = conn.cursor()
                cur.execute("SELECT host_key, name, value FROM cookies")
                for host_key, name, value in cur.fetchall():
                    if "tiktok" in str(host_key).lower() and name and value:
                        pairs.append(f"{name}={value}")
            finally:
                conn.close()
        except Exception:
            return ""
        return "; ".join(pairs)

    def _extract_cookie_string(self, cookie_path_or_value):
        raw = (cookie_path_or_value or "").strip()
        if not raw:
            return ""

        if "sessionid=" in raw or ";" in raw:
            return raw

        if not os.path.exists(raw):
            return raw

        if os.path.isdir(raw):
            candidates = [
                os.path.join(raw, "Default", "Network", "Cookies"),
                os.path.join(raw, "Default", "Cookies"),
                os.path.join(raw, "Network", "Cookies"),
                os.path.join(raw, "Cookies"),
                os.path.join(raw, "cookies.json"),
                os.path.join(raw, "cookie.txt"),
            ]
        else:
            candidates = [raw]

        for path in candidates:
            if not os.path.exists(path) or os.path.isdir(path):
                continue

            cookie = ""
            lower_path = path.lower()
            if lower_path.endswith((".sqlite", ".db")) or os.path.basename(path).lower() == "cookies":
                cookie = self._cookie_from_sqlite(path)
                if cookie:
                    return cookie

            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read().strip()
            except Exception:
                continue

            if not text:
                continue
            if "sessionid=" in text or ";" in text:
                return text

            try:
                cookie = self._cookie_from_json(json.loads(text))
            except Exception:
                cookie = ""
            if cookie:
                return cookie

        return ""

    def _looks_login_required(self, response_text, data):
        text = (response_text or "").lower()
        if any(token in text for token in (
            "login_required", "login required", "isloginexpired", "is_login\":false",
            "please log in", "not logged in", "session expired",
        )):
            return True

        if isinstance(data, dict):
            status_code = str(data.get("status_code", ""))
            status_msg = str(data.get("status_msg", "") or data.get("message", "")).lower()
            if status_code in ("8", "10000", "10002", "401"):
                return True
            if any(token in status_msg for token in ("login", "session", "expired")):
                return True

        return False

    def _message_timestamp(self, value):
        if value is None:
            return None

        if isinstance(value, (int, float)):
            ts = float(value)
            if ts > 10_000_000_000:
                ts = ts / 1000.0
            return ts

        text = str(value).strip()
        if not text:
            return None

        try:
            ts = float(text)
            if ts > 10_000_000_000:
                ts = ts / 1000.0
            return ts
        except Exception:
            pass

        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed.timestamp()
        except Exception:
            return None

    def _collect_message_text(self, item):
        text_keys = (
            "title", "content", "body", "text", "msg", "message", "description",
            "notice", "sub_title", "subtitle", "preview", "abstract",
        )
        parts = []
        for key in text_keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
            elif isinstance(value, dict):
                nested = self._collect_message_text(value)
                if nested:
                    parts.append(nested)
        return " - ".join(dict.fromkeys(parts))

    def _flatten_inbox_messages(self, data):
        time_keys = (
            "create_time", "createTime", "created_at", "createdAt",
            "time", "timestamp", "notice_time", "publish_time",
        )
        messages = []

        def walk(node):
            if isinstance(node, list):
                for child in node:
                    walk(child)
                return

            if not isinstance(node, dict):
                return

            ts = None
            for key in time_keys:
                if key in node:
                    ts = self._message_timestamp(node.get(key))
                    if ts:
                        break

            text = self._collect_message_text(node)
            if ts and text:
                messages.append((ts, text))

            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value)

        walk(data)
        return messages

    def _format_inbox_messages(self, raw_messages):
        cutoff = time.time() - 86400
        formatted = []
        seen = set()

        for ts, text in sorted(raw_messages, key=lambda item: item[0], reverse=True):
            if ts < cutoff:
                continue

            normalized = " ".join(str(text).split())
            if not normalized:
                continue

            stamp = time.strftime("%H:%M", time.localtime(ts))
            message = f"{stamp} - {normalized}"
            if message not in seen:
                seen.add(message)
                formatted.append(message)

        return formatted

    def _has_violation_message(self, messages):
        combined = "\n".join(messages).lower()
        return any(keyword in combined for keyword in self.violation_keywords)

    def check_violation_api(self, profile_id, cookie_path):
        """
        Check System Inbox TikTok bằng API nền.
        Trả về tuple: (trạng thái ngắn gọn, danh sách tin nhắn 24h).
        """
        cookie_string = self._extract_cookie_string(cookie_path)
        cookie_dict = self._parse_cookie_string(cookie_string)

        if not cookie_string or not cookie_dict.get("sessionid"):
            return "Cookie chết, cần login lại", []

        try:
            from curl_cffi import requests as curl_requests
        except Exception as exc:
            return f"Thiếu curl_cffi: {exc}", []

        browser_version = (
            "5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"
        )
        base_params = {
            "aid": "1988",
            "app_language": "vi-VN",
            "app_name": "tiktok_web",
            "browser_language": "vi-VN",
            "browser_name": "Mozilla",
            "browser_online": "true",
            "browser_platform": "Win32",
            "browser_version": browser_version,
            "channel": "tiktok_web",
            "cookie_enabled": "true",
            "count": "50",
            "device_platform": "web_pc",
            "focus_state": "true",
            "from_page": "notification",
            "history_len": "3",
            "is_fullscreen": "false",
            "is_page_visible": "true",
            "language": "vi-VN",
            "os": "windows",
            "priority_region": "VN",
            "referer": "",
            "region": "VN",
            "screen_height": "1080",
            "screen_width": "1920",
            "tz_name": "Asia/Saigon",
            "webcast_language": "vi-VN",
        }
        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
            "cache-control": "no-cache",
            "cookie": cookie_string,
            "pragma": "no-cache",
            "referer": "https://www.tiktok.com/messages?lang=vi-VN",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"
            ),
        }

        endpoints = [
            "https://www.tiktok.com/api/notice/multi/",
            "https://www.tiktok.com/api/notification/get/",
            "https://www.tiktok.com/api/inbox/notice/list/",
        ]

        last_error = ""
        collected = []
        for endpoint in endpoints:
            url = f"{endpoint}?{urlencode(base_params)}"
            try:
                response = curl_requests.get(
                    url,
                    headers=headers,
                    timeout=20,
                    impersonate="chrome110",
                )
            except Exception as exc:
                last_error = str(exc)[:200]
                continue

            if response.status_code in (401, 403):
                return "Cookie chết, cần login lại", []
            if response.status_code >= 500:
                last_error = f"HTTP {response.status_code}"
                continue
            if response.status_code >= 400:
                last_error = f"HTTP {response.status_code}"
                continue

            text = response.text or ""
            try:
                data = response.json()
            except Exception:
                last_error = "API không trả JSON hợp lệ"
                continue

            if self._looks_login_required(text, data):
                return "Cookie chết, cần login lại", []

            collected.extend(self._flatten_inbox_messages(data))

        messages = self._format_inbox_messages(collected)
        if messages and self._has_violation_message(messages):
            return "Có vi phạm", messages
        if messages:
            return "Có thông báo", messages
        if last_error:
            return f"Lỗi đọc Inbox: {last_error}", []
        return "Không có thư 24h", []

    def run_check_batch(self, max_workers=10):
        """
        Kiểm tra hàng loạt sử dụng ThreadPoolExecutor.
        """
        print(f"Đang kiểm tra {len(self.accounts)} tài khoản với {max_workers} luồng...")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(self.check_live_cookie, acc) for acc in self.accounts]
            
            completed = 0
            for future in concurrent.futures.as_completed(futures):
                completed += 1
                if completed % 5 == 0 or completed == len(self.accounts):
                    print(f"Tiến độ: {completed}/{len(self.accounts)}")

    def export_results(self, output_path):
        """
        Xuất kết quả ra file CSV mới.
        """
        if not self.accounts:
            print("Không có dữ liệu để xuất.")
            return

        try:
            keys = self.accounts[0].keys()
            with open(output_path, mode='w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=keys)
                writer.writeheader()
                writer.writerows(self.accounts)
            print(f"Đã xuất kết quả ra: {output_path}")
        except Exception as e:
            print(f"Lỗi khi xuất CSV: {e}")

if __name__ == "__main__":
    # Ví dụ sử dụng
    manager = AccountManager()
    # manager.import_from_csv("accounts.csv")
    # manager.run_check_batch(10)
    # manager.export_results("checked_accounts.csv")
    print("AccountManager đã sẵn sàng.")
