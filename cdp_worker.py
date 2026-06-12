"""
Worker CDP â€” Thay tháº¿ GoLoginWorker.
DÃ¹ng CDP trá»±c tiáº¿p (websockets) thay Playwright.
Há»— trá»£ screencast live preview.
"""
import os
import time
import shutil
import random
import asyncio
import socket
import json as _json
import re
import threading
import unicodedata
import uuid
from urllib.parse import quote
from PyQt5.QtCore import QThread, pyqtSignal
import win32gui
import win32con
import win32process
from gologin_config import load_gologin_settings
from gologin_profile_utils import first_real_gologin_profile_id
from gologin_proxy_check import validate_profile_proxy
from app_paths import resource_path, local_chrome_profile_dir
from browser_backend_utils import LOCAL_CHROME_BACKEND, normalize_browser_backend
from proxy_utils import (
    normalize_proxy_type,
    parse_proxy_string,
    proxy_custom_name,
    proxy_display_text,
    validate_proxy_connection,
)

ZERO_PROFILE_ZIP = str(resource_path("gologin_zeroprofile.zip"))

BROWSER_WIDTH = 960
BROWSER_HEIGHT = 680
APP_TITLEBAR_HEIGHT = 0  # 0 = hiá»‡n thanh trÃ¬nh duyá»‡t

# Viewport áº£o â€” lá»«a TikTok render layout desktop 3 cá»™t chuáº©n
VIRTUAL_VIEWPORT_W = 1280
VIRTUAL_VIEWPORT_H = 720
_GOLOGIN_START_LOCK = threading.RLock()


def _is_port_open(port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False

def _safe_randint(a: int, b: int) -> int:
    a = int(a)
    b = int(b)
    if a > b:
        a, b = b, a
    return random.randint(a, b)


class CDPWorker(QThread):
    """Worker dÃ¹ng CDP + Native Window Embedding (SetParent) â€” 60 FPS mÆ°á»£t mÃ ."""
    status_update = pyqtSignal(str, str)       # (message, color)
    finished_signal = pyqtSignal(str)           # "success" / "error"
    profile_update_signal = pyqtSignal(dict)    # {"tiktok_id": ..., "cookie": ...}
    browser_ready_signal = pyqtSignal(dict)      # browser launched; UI thread should embed HWND
    browser_closed_signal = pyqtSignal(str)      # emitted only after browser cleanup is really done

    def __init__(self, profile_index, profile_data, selected_features, feed_settings,
                 container_width=0, container_height=0, widget_id=0, parent=None,
                 manual_only=False, planned_profile_count=1):
        super().__init__(parent)
        self.profile_index = profile_index
        self.profile_data = profile_data
        self.selected_features = list(selected_features or [])
        self.feed_settings = feed_settings
        self.container_width = container_width or BROWSER_WIDTH
        self.container_height = container_height or BROWSER_HEIGHT
        self.widget_id = widget_id  # HWND cá»§a QWidget container
        self._stop_flag = False
        self._process = None
        self._debug_port = 0
        self._cdp = None
        self._embedded_hwnd = 0
        self._last_nav_url = ""
        self._last_nav_ts = 0.0
        self._gologin = None
        self._using_gologin_api = False
        self.manual_only = bool(manual_only)
        self._planned_profile_count = max(1, int(planned_profile_count or 1))
        self._browser_pids = set()
        self._process_pid = 0
        self._launch_started_at = 0.0
        self._browser_manager_acquired = False
        self._async_close_started = False
        self._browser_closed_emitted = False
        self._close_signal_lock = threading.Lock()
        self._embed_token = uuid.uuid4().hex
        self._embed_done_event = threading.Event()
        self._embed_result = False
        self._last_error = ""
        self._last_login_error = ""
        self._feed_scroll_delta_sign = 1
        supported_feature_tokens = {
            self._feature_token("Dang nhap"),
            self._feature_token("Doi avatar"),
            self._feature_token("Tuong tac o Feed"),
            self._feature_token("Tuong tac theo tu khoa"),
        }
        self._unsupported_selected_features = []
        filtered_features = []
        for feature in self.selected_features:
            token = self._feature_token(feature)
            if token in supported_feature_tokens:
                filtered_features.append(feature)
            elif str(feature or "").strip():
                self._unsupported_selected_features.append(str(feature))
        self.selected_features = filtered_features
        self._selected_feature_tokens = {
            self._feature_token(feature) for feature in self.selected_features
        }


        # Comment bank: lÆ°u comment tá»« cÃ¡c video trÆ°á»›c Ä‘á»ƒ clone chÃ©o
        self._comment_bank = []       # list comment Ä‘Ã£ thu tháº­p tá»« video cÅ©
        self._comment_history = set() # chá»‘ng trÃ¹ng láº·p trong phiÃªn
        self._comment_cooldown = False  # rate limit flag

        # Parse proxy
        self._proxy_host = ""
        self._proxy_port = ""
        self._proxy_user = ""
        self._proxy_pass = ""
        self._proxy_type = (self.profile_data.get('proxy_type', 'http') or 'http').strip().lower()
        self._gologin_profile_has_proxy = False
        parsed_proxy = self._parse_proxy_string(self.profile_data.get('proxy', ''), self._proxy_type)
        if parsed_proxy:
            self._proxy_type = parsed_proxy["mode"]
            self._proxy_host = parsed_proxy["host"]
            self._proxy_port = str(parsed_proxy["port"])
            self._proxy_user = parsed_proxy.get("username", "")
            self._proxy_pass = parsed_proxy.get("password", "")
            self._gologin_profile_has_proxy = True

        # ThÆ° má»¥c profile â€” luÃ´n dá»±a trÃªn browser_id duy nháº¥t
        browser_id = str(self.profile_data.get('browser_id', '') or '').strip()
        if not browser_id:
            # Tá»± táº¡o browser_id náº¿u chÆ°a cÃ³ (dá»±a trÃªn row index)
            browser_id = ""
        self._browser_id = ""
        self._profile_dir = ""
        self._gologin_profile_id = (self.profile_data.get("gologin_profile_id") or "").strip()
        self._browser_backend = normalize_browser_backend(self.profile_data.get("browser_backend"))
        if self._browser_backend == "local_chrome":
            if not browser_id or not browser_id.startswith("local_chrome:"):
                browser_id = str(self.profile_data.get("browser_id") or "").strip()
            if not browser_id or not browser_id.startswith("local_chrome:"):
                from browser_backend_utils import make_local_chrome_browser_id
                browser_id = make_local_chrome_browser_id(self.profile_data.get("ten_ho_so") or f"profile_{self.profile_index}")
            self._browser_id = browser_id
            self.profile_data["browser_id"] = browser_id
            self.profile_data["gologin_profile_id"] = ""
            self._gologin_profile_id = ""
            self._profile_dir = str(local_chrome_profile_dir(browser_id))
        else:
            resolved_gologin_profile_id = first_real_gologin_profile_id(
                self._gologin_profile_id,
                browser_id,
            )
            if resolved_gologin_profile_id:
                self.profile_data["browser_id"] = resolved_gologin_profile_id
                self.profile_data["gologin_profile_id"] = resolved_gologin_profile_id
                self._browser_id = resolved_gologin_profile_id
                self._gologin_profile_id = resolved_gologin_profile_id
            else:
                if str(self.profile_data.get("browser_id") or "").startswith(("auto_", "gologin_")):
                    self.profile_data["browser_id"] = ""
                if not first_real_gologin_profile_id(self.profile_data.get("gologin_profile_id", "")):
                    self.profile_data["gologin_profile_id"] = ""
                self._browser_id = ""
                self._profile_dir = ""
                self._gologin_profile_id = ""
        strict_override = self.profile_data.get("gologin_passthrough_strict")
        if strict_override is None and isinstance(self.feed_settings, dict):
            strict_override = self.feed_settings.get("gologin_passthrough_strict")
        self._strict_gologin_passthrough = self._coerce_bool(strict_override, default=True)

    @staticmethod
    def _coerce_bool(value, default=False):
        if value is None:
            return bool(default)
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
        return bool(default)

    @staticmethod
    def _feature_token(value) -> str:
        text = (
            str(value or "")
            .replace("Đ", "D")
            .replace("đ", "d")
            .replace("Ä", "D")
            .replace("Ä‘", "d")
        )
        text = unicodedata.normalize("NFKD", text)
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        return " ".join(text.lower().split())

    def _is_batch_run(self) -> bool:
        return (not self.manual_only) and self._planned_profile_count > 1

    def _has_selected_feature(self, label: str) -> bool:
        return self._feature_token(label) in getattr(self, "_selected_feature_tokens", set())

    def _has_selected_feature_fragment(self, fragment: str) -> bool:
        token = self._feature_token(fragment)
        return any(token in value for value in getattr(self, "_selected_feature_tokens", set()))

    def _has_saved_login_inputs(self) -> bool:
        username = self.profile_data.get("username", "").strip()
        password = self.profile_data.get("password", "").strip()
        cookie = self.profile_data.get("cookie", "").strip()
        cookie_backup = self.profile_data.get("cookie_backup", "").strip()
        return bool(cookie or cookie_backup or (username and password))

    def _profile_has_persistent_browser_state_hint(self) -> bool:
        if self._browser_backend == LOCAL_CHROME_BACKEND:
            profile_dir = str(getattr(self, "_profile_dir", "") or "").strip()
            if not profile_dir or not os.path.isdir(profile_dir):
                return False
            state_markers = (
                "Local State",
                "First Run",
                "Default",
                "Sessions",
                "Network",
                "Preferences",
            )
            try:
                for marker in state_markers:
                    if os.path.exists(os.path.join(profile_dir, marker)):
                        return True
                with os.scandir(profile_dir) as entries:
                    return any(entry.name not in {".", ".."} for entry in entries)
            except Exception:
                return False

        # GoLogin session that nam trong cloud/profile that.
        return bool(self._gologin_profile_id)

    def _should_defer_batch_login_validation(self) -> bool:
        if "dang nhap" not in getattr(self, "_selected_feature_tokens", set()) or not self._is_batch_run():
            return False
        return self._profile_has_persistent_browser_state_hint()

    def _batch_login_fail_fast_reason(self) -> str:
        if "dang nhap" not in getattr(self, "_selected_feature_tokens", set()) or not self._is_batch_run():
            return ""
        if self._should_defer_batch_login_validation():
            return ""
        username = self.profile_data.get("username", "").strip()
        password = self.profile_data.get("password", "").strip()
        if self._has_saved_login_inputs():
            return ""
        missing = ["cookie"]
        if not username:
            missing.append("username/email")
        if not password:
            missing.append("password")
        profile_name = self.profile_data.get("ten_ho_so", "")
        return f"[{profile_name}] Thieu {', '.join(missing)} cho batch login"

    @staticmethod
    def _has_valid_tiktok_auth_cookie(cookies) -> bool:
        auth_names = {"sessionid", "sessionid_ss", "sid_tt", "sid_guard"}
        for cookie in cookies or []:
            try:
                domain = str(cookie.get("domain") or "").lower()
                name = str(cookie.get("name") or "").lower()
                value = str(cookie.get("value") or "")
            except Exception:
                continue
            if "tiktok" in domain and name in auth_names and value and value != '""':
                return True
        return False

    def _emit_browser_closed_once(self, reason="closed"):
        with self._close_signal_lock:
            if self._browser_closed_emitted:
                return
            self._browser_closed_emitted = True
        try:
            self.browser_closed_signal.emit(str(reason or "closed"))
        except Exception:
            pass

    def _should_preserve_gologin_fingerprint(self) -> bool:
        """GoLogin/Orbita already owns fingerprint spoofing; avoid overriding it."""
        return bool(self._using_gologin_api or self._gologin_profile_id)

    async def _emit_runtime_fingerprint_snapshot(self, cdp):
        """Read browser-visible fingerprint values for comparison with GoLogin UI."""
        try:
            fp = await cdp.evaluate(r"""
            (() => {
                const safe = (fn, fallback = null) => {
                    try { return fn(); } catch (e) { return fallback; }
                };
                return {
                    userAgent: safe(() => navigator.userAgent, ''),
                    platform: safe(() => navigator.platform, ''),
                    language: safe(() => navigator.language, ''),
                    languages: safe(() => Array.from(navigator.languages || []), []),
                    timezone: safe(() => Intl.DateTimeFormat().resolvedOptions().timeZone, ''),
                    timezoneOffset: safe(() => new Date().getTimezoneOffset(), null),
                    screen: safe(() => `${screen.width}x${screen.height}`, ''),
                    availScreen: safe(() => `${screen.availWidth}x${screen.availHeight}`, ''),
                    inner: safe(() => `${window.innerWidth}x${window.innerHeight}`, ''),
                    outer: safe(() => `${window.outerWidth}x${window.outerHeight}`, ''),
                    devicePixelRatio: safe(() => window.devicePixelRatio, null),
                    webdriver: safe(() => navigator.webdriver, null),
                    hardwareConcurrency: safe(() => navigator.hardwareConcurrency, null),
                    deviceMemory: safe(() => navigator.deviceMemory, null),
                    plugins: safe(() => navigator.plugins ? navigator.plugins.length : null, null),
                };
            })()
            """) or {}
        except Exception as exc:
            self.status_update.emit(f"Khong doc duoc fingerprint runtime: {str(exc)[:60]}", "orange")
            return

        if not isinstance(fp, dict):
            return

        ua = str(fp.get("userAgent") or "")
        chrome_version = ""
        match = re.search(r"Chrome/([0-9.]+)", ua)
        if match:
            chrome_version = match.group(1)
        languages = fp.get("languages") or []
        if isinstance(languages, list):
            languages_text = ",".join(str(v) for v in languages)
        else:
            languages_text = str(languages or "")

        self.status_update.emit(
            "FP runtime: "
            f"Chrome {chrome_version or 'unknown'}, "
            f"lang {languages_text or fp.get('language') or 'unknown'}, "
            f"tz {fp.get('timezone') or 'unknown'}, "
            f"screen {fp.get('screen') or 'unknown'}, "
            f"webdriver={fp.get('webdriver')}",
            "blue",
        )
        self.status_update.emit(
            "FP viewport: "
            f"inner {fp.get('inner') or 'unknown'}, "
            f"outer {fp.get('outer') or 'unknown'}, "
            f"dpr={fp.get('devicePixelRatio')}, "
            f"plugins={fp.get('plugins')}",
            "blue",
        )
        try:
            self.profile_update_signal.emit({"gologin_runtime_fingerprint": fp})
        except Exception:
            pass

    def notify_embed_result(self, success=False, hwnd=0, pid=0, message=""):
        """Called from the Qt UI thread after native browser embedding finishes."""
        try:
            success = bool(success)
            self._embed_result = success
            self._embedded_hwnd = int(hwnd or 0) if success else 0
            if pid:
                self._browser_pids.add(int(pid))
            if message:
                self.status_update.emit(str(message), "green" if success else "orange")
        except Exception:
            self._embed_result = False
        finally:
            try:
                self._embed_done_event.set()
            except Exception:
                pass

    def _native_focus_embedded_browser(self, click_content=False):
        """Give the embedded Chrome window real Win32 focus so the omnibox is not active."""
        hwnd = int(getattr(self, "_embedded_hwnd", 0) or 0)
        if not hwnd:
            return False
        try:
            import ctypes
            import win32api

            if not win32gui.IsWindow(hwnd):
                return False

            user32 = ctypes.windll.user32
            root_hwnd = win32gui.GetAncestor(hwnd, getattr(win32con, "GA_ROOT", 2)) or hwnd
            focus_hwnd = hwnd
            chrome_tid = win32process.GetWindowThreadProcessId(focus_hwnd)[0]
            my_tid = user32.GetCurrentThreadId()
            foreground_hwnd = user32.GetForegroundWindow()
            foreground_tid = 0
            if foreground_hwnd:
                foreground_tid = win32process.GetWindowThreadProcessId(foreground_hwnd)[0]

            attached_chrome = False
            attached_foreground = False
            try:
                if chrome_tid != my_tid:
                    attached_chrome = bool(user32.AttachThreadInput(my_tid, chrome_tid, True))
                if foreground_tid and foreground_tid not in (my_tid, chrome_tid):
                    attached_foreground = bool(user32.AttachThreadInput(my_tid, foreground_tid, True))

                try:
                    user32.SetForegroundWindow(root_hwnd)
                except Exception:
                    pass
                try:
                    user32.BringWindowToTop(hwnd)
                    user32.SetActiveWindow(hwnd)
                    user32.SetFocus(focus_hwnd)
                except Exception:
                    pass
            finally:
                if attached_foreground:
                    user32.AttachThreadInput(my_tid, foreground_tid, False)
                if attached_chrome:
                    user32.AttachThreadInput(my_tid, chrome_tid, False)

            try:
                win32api.keybd_event(win32con.VK_ESCAPE, 0, 0, 0)
                time.sleep(0.03)
                win32api.keybd_event(win32con.VK_ESCAPE, 0, win32con.KEYEVENTF_KEYUP, 0)
            except Exception:
                pass

            if click_content:
                try:
                    left, top, right, bottom = win32gui.GetClientRect(hwnd)
                    width = max(1, right - left)
                    height = max(1, bottom - top)
                    sx, sy = win32gui.ClientToScreen(
                        hwnd,
                        (int(width * 0.42), int(height * 0.58)),
                    )
                    win32api.SetCursorPos((sx, sy))
                    time.sleep(0.03)
                    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, sx, sy, 0, 0)
                    time.sleep(0.03)
                    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, sx, sy, 0, 0)
                except Exception:
                    pass
            return True
        except Exception:
            return False

    def _normalize_proxy_mode(self, proxy_type):
        return normalize_proxy_type(proxy_type)

    def _parse_proxy_string(self, proxy_str, proxy_type="http"):
        """Parse proxy formats: host:port, host:port:user:pass, scheme://host:port:user:pass, or user:pass@host:port."""
        return parse_proxy_string(proxy_str, proxy_type)

    def _get_proxy_payload(self, for_gologin_api=False):
        payload = self._parse_proxy_string(
            self.profile_data.get("proxy", ""),
            self.profile_data.get("proxy_type", self._proxy_type),
        )
        if not payload:
            return None
        payload["changeIpUrl"] = ""
        if for_gologin_api:
            custom_name = proxy_custom_name(
                f"{payload.get('host')}:{payload.get('port')}",
                payload.get("mode"),
            )
            payload["customName"] = custom_name
        payload["autoProxyRegion"] = ""
        payload["torProxyRegion"] = ""
        return payload

    def _proxy_display_text(self, payload):
        if not payload:
            return ""
        return proxy_display_text(
            f"{payload.get('host')}:{payload.get('port')}",
            payload.get("mode"),
        )

    def _validate_runtime_proxy(self):
        proxy_string = (self.profile_data.get("proxy") or "").strip()
        if not proxy_string:
            return True
        requested_mode = self._normalize_proxy_mode(
            self.profile_data.get("proxy_type", self._proxy_type)
        )

        result = validate_proxy_connection(
            proxy_string,
            proxy_type=requested_mode,
            require_ip_change=True,
            timeout=8,
        )
        if not result.get("ok"):
            message = str(result.get("message") or "Proxy khong hop le")
            self._last_proxy_error = message
            self.status_update.emit(message, "red")
            return False

        detected_mode = self._normalize_proxy_mode(result.get("scheme"))
        self.profile_data["proxy_type"] = detected_mode
        self._proxy_type = detected_mode

        parsed_proxy = self._parse_proxy_string(proxy_string, detected_mode)
        if parsed_proxy:
            self._proxy_host = parsed_proxy["host"]
            self._proxy_port = str(parsed_proxy["port"])
            self._proxy_user = parsed_proxy.get("username", "")
            self._proxy_pass = parsed_proxy.get("password", "")

        update_payload = {"proxy_type": detected_mode}
        try:
            self.profile_update_signal.emit(update_payload)
        except Exception:
            pass

        proxy_ip = str(result.get("proxy_ip") or "").strip()
        direct_ip = str(result.get("direct_ip") or "").strip()
        status_text = f"Proxy OK: {proxy_ip}" if proxy_ip else "Proxy OK"
        if direct_ip and proxy_ip and direct_ip != proxy_ip:
            status_text += f" (IP may: {direct_ip})"
        if detected_mode != requested_mode:
            status_text += f" - da chuyen sang {detected_mode.upper()}"
        self.status_update.emit(status_text, "green")
        return True

    def _sync_gologin_profile_proxy(self):
        """Persist the current proxy to the GoLogin cloud profile before starting it."""
        proxy_payload = self._get_proxy_payload(for_gologin_api=True)
        if not proxy_payload:
            return True, "KhÃ´ng cÃ³ proxy Ä‘á»ƒ Ä‘á»“ng bá»™"

        token, _, _ = self._get_gologin_api_settings()
        if not token:
            return False, "Thiáº¿u GoLogin API key nÃªn khÃ´ng thá»ƒ Ä‘á»“ng bá»™ proxy."
        if not self._gologin_profile_id:
            return False, "Thiáº¿u GoLogin Profile ID nÃªn khÃ´ng thá»ƒ Ä‘á»“ng bá»™ proxy."

        try:
            import requests
            response = requests.patch(
                f"https://api.gologin.com/browser/{self._gologin_profile_id}/proxy",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=proxy_payload,
                timeout=25,
            )
        except Exception as e:
            return False, f"Lá»—i káº¿t ná»‘i API GoLogin khi Ä‘á»“ng bá»™ proxy: {e}"

        if response.status_code in (200, 201, 204):
            display = self._proxy_display_text(proxy_payload)
            try:
                self.profile_update_signal.emit({"gologin_proxy_synced": display})
            except Exception:
                pass
            return True, display

        detail = (response.text or "").strip().replace("\n", " ")[:300]
        return False, f"GoLogin proxy API lá»—i HTTP {response.status_code}: {detail}"

    def _get_gologin_api_settings(self):
        settings = load_gologin_settings()
        token = (settings.get("api_key") or "").strip()
        use_cloud = bool(settings.get("use_gologin_cloud"))
        folder_name = (settings.get("gologin_folder_name") or "").strip()
        return token, use_cloud, folder_name

    def _should_use_gologin_api(self):
        token, use_cloud, _ = self._get_gologin_api_settings()
        return bool(token and use_cloud and self._gologin_profile_id)

    def _find_pids_by_profile_hints(self):
        pids = set()
        hints = {
            str(value or "").strip().lower()
            for value in (self._gologin_profile_id, self._browser_id)
            if value and len(str(value).strip()) >= 8
        }
        profile_dir_norm = self._norm_proc_path(getattr(self, "_profile_dir", ""))
        try:
            import psutil
            browser_names = {"chrome.exe", "orbita-browser.exe", "chromium.exe"}
            for proc in psutil.process_iter(["name", "pid", "cmdline"]):
                try:
                    name = (proc.info.get("name") or "").lower()
                    if name not in browser_names:
                        continue
                    args = [str(arg) for arg in (proc.info.get("cmdline") or [])]
                    cmdline = " ".join(args).lower().replace("\\", "/")
                    matched = bool(hints and any(hint in cmdline for hint in hints))
                    if not matched and profile_dir_norm:
                        matched = profile_dir_norm in cmdline
                    if matched:
                        pids.add(int(proc.info["pid"]))
                        for child in proc.children(recursive=True):
                            pids.add(int(child.pid))
                except Exception:
                    continue
        except Exception:
            pass
        return pids

    def _cleanup_stale_profile_processes_before_start(self):
        stale_pids = self._find_pids_by_profile_hints()
        if not stale_pids:
            return
        self.status_update.emit(
            f"Trinh duyet cu van con, dang don dep {len(stale_pids)} process truoc khi mo lai...",
            "orange",
        )
        self._force_close_browser_processes(
            debug_port=0,
            profile_dir=self._profile_dir,
            known_pids=stale_pids,
        )
        time.sleep(0.8)

    def _start_local_proxy_bridge(self):
        """Start a local no-auth proxy bridge for auth proxies and return its port."""
        if not (self._proxy_host and self._proxy_port):
            return None

        from local_proxy import create_local_proxy

        proxy_str = self.profile_data.get('proxy', '')
        proxy_type = self._proxy_type
        base_port = 18080 + (self.profile_index * 10)
        for offset in range(10):
            local_port = base_port + offset
            bridge = create_local_proxy(local_port, proxy_str, proxy_type)
            if bridge:
                self._local_proxy = bridge
                self.status_update.emit(
                    f"Proxy bridge: {proxy_type}://{self._proxy_host}:{self._proxy_port} -> 127.0.0.1:{local_port}",
                    "blue"
                )
                return local_port
        return None

    def _parse_debug_port(self, debugger_address):
        text = str(debugger_address or "").strip()
        if not text:
            return None
        try:
            if text.startswith("ws://") or text.startswith("wss://"):
                from urllib.parse import urlparse
                parsed = urlparse(text)
                return int(parsed.port) if parsed.port else None
            if "://" in text:
                from urllib.parse import urlparse
                parsed = urlparse(text)
                return int(parsed.port) if parsed.port else None
            if ":" in text:
                return int(text.rsplit(":", 1)[1].split("/")[0])
            return int(text)
        except Exception:
            return None

    def _launch_browser_via_manager(self):
        try:
            from browser_manager import BrowserManager

            width = self.container_width if self.container_width else BROWSER_WIDTH
            height = self.container_height if self.container_height else BROWSER_HEIGHT
            proxy_server = ""
            if self._proxy_host and self._proxy_port:
                local_port = self._start_local_proxy_bridge()
                proxy_server = f"127.0.0.1:{local_port}" if local_port else self.profile_data.get("proxy", "")

            manager = BrowserManager()
            self._process, dynamic_port = manager.launch_browser(
                profile_id=self._browser_id,
                profile_dir=self._profile_dir,
                width=width,
                height=height,
                proxy_server=proxy_server,
            )
            self._browser_manager_acquired = True
            self._debug_port = dynamic_port
            self._process_pid = self._process.pid
            self._using_gologin_api = False
            self.status_update.emit(f"TrÃ¬nh duyá»‡t Ä‘ang má»Ÿ qua BrowserManager (port {dynamic_port})...", "blue")
            time.sleep(0.25)
            return True
        except Exception as e:
            msg = str(e)
            if "Ä‘ang Ä‘Æ°á»£c sá»­ dá»¥ng" in msg:
                msg = "Lá»—i: KhÃ´ng thá»ƒ cháº¡y, Profile Ä‘ang báº­n up video/nuÃ´i nick"
            self.status_update.emit(f"âŒ {msg}", "red")
            self.finished_signal.emit("error")
            return False

    def _release_managed_browser(self):
        if not self._browser_manager_acquired:
            return
        try:
            from browser_manager import BrowserManager
            BrowserManager().close_browser(self._browser_id, self._profile_dir)
        except Exception:
            pass
        self._browser_manager_acquired = False
        if hasattr(self, '_local_proxy') and self._local_proxy:
            try:
                self._local_proxy.stop()
            except Exception:
                pass
            self._local_proxy = None

    def _gologin_install_help(self):
        return (
            "Thieu GoLogin SDK. Cai truoc bang lenh: "
            "python -m pip install gologin"
        )

    def _validate_gologin_profile_proxy(self, gl, timeout=8):
        try:
            profile = gl.getProfile()
        except Exception as exc:
            return False, f"Khong doc duoc proxy GoLogin profile: {exc}"

        result = validate_profile_proxy(profile, timeout=timeout)
        message = str(result.get("message") or "").strip()
        proxy_info = result.get("proxy_info") or {}
        has_proxy = bool(proxy_info.get("has_proxy"))
        self._gologin_profile_has_proxy = has_proxy
        proxy_string = str(proxy_info.get("proxy_string") or "").strip() if has_proxy else ""
        proxy_type = str(proxy_info.get("proxy_type") or "").strip() if has_proxy else ""
        proxy_display = str(proxy_info.get("display") or "").strip() if has_proxy else ""
        cached_proxy = (self.profile_data.get("proxy") or "").strip()
        self.profile_data["proxy"] = proxy_string
        self.profile_data["proxy_type"] = proxy_type
        try:
            self.profile_update_signal.emit({
                "proxy": proxy_string,
                "proxy_type": proxy_type,
                "gologin_proxy_synced": proxy_display,
            })
        except Exception:
            pass

        if result.get("skipped"):
            if cached_proxy:
                self.status_update.emit(
                    "GoLogin profile hien khong dung proxy; da xoa proxy cu trong tool.",
                    "blue",
                )
            if message:
                self.status_update.emit(message, "blue")
            return True, message
        if result.get("ok"):
            if message:
                self.status_update.emit(message, "green")
            return True, message
        return False, message or "GoLogin proxy loi"

    def _force_direct_browser_when_no_gologin_proxy(self, gl, extra_params):
        if self._gologin_profile_has_proxy:
            return
        params = getattr(gl, "extra_params", extra_params)
        if params is None:
            params = []
            try:
                gl.extra_params = params
            except Exception:
                pass
        if not any(str(param).startswith("--proxy-server") or str(param) == "--no-proxy-server" for param in params):
            params.append("--no-proxy-server")
        if extra_params is not params and not any(str(param) == "--no-proxy-server" for param in extra_params):
            extra_params.append("--no-proxy-server")
        self.status_update.emit(
            "GoLogin profile khong co proxy; ep browser chay direct de tranh proxy cu trong Preferences.",
            "blue",
        )

    def _launch_browser_via_gologin_sdk(self):
        fail_fast_reason = self._batch_login_fail_fast_reason()
        if fail_fast_reason:
            self._emit_login_error(fail_fast_reason)
            self.status_update.emit(
                f"FAIL FAST: {fail_fast_reason}. Khong mo browser.",
                "red",
            )
            self.finished_signal.emit(f"error: {fail_fast_reason}")
            return False

        strict_mode = bool(self._strict_gologin_passthrough)
        token, _, _ = self._get_gologin_api_settings()
        if not token:
            self.status_update.emit(
                "Thieu GoLogin API Key. Vao menu API | Cookie de nhap token.",
                "red"
            )
            self.finished_signal.emit("error")
            return False
        if not self._gologin_profile_id:
            self.status_update.emit(
                "Profile nay chua co GoLogin Profile ID that nen khong the mo bang GoLogin.",
                "red"
            )
            self.finished_signal.emit("error")
            return False

        try:
            from gologin import GoLogin
        except Exception:
            self.status_update.emit(self._gologin_install_help(), "red")
            self.finished_signal.emit("error")
            return False

        extra_params = []
        if strict_mode:
            if self.widget_id:
                extra_params.append(f"--ssmatool-embed-token={self._embed_token}")
        else:
            extra_params = [
                f"--window-size={int(self.container_width)},{int(self.container_height)}",
                "--window-position=0,0",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-session-crashed-bubble",
                "--hide-crash-restore-bubble",
                "--disable-infobars",
                f"--ssmatool-embed-token={self._embed_token}",
            ]

        gl = None
        try:
            self._cleanup_stale_profile_processes_before_start()
            self.status_update.emit(
                (
                    f"Mo GoLogin profile {self._gologin_profile_id} bang Local SDK "
                    f"({'pass-through strict' if strict_mode else 'automation mode'})..."
                ),
                "blue"
            )
            gl_config = {
                "token": token,
                "profile_id": self._gologin_profile_id,
                "spawn_browser": True,
            }
            if strict_mode:
                gl_config["restore_last_session"] = True
                if extra_params:
                    gl_config["extra_params"] = list(extra_params)
            else:
                gl_config.update({
                    "uploadCookiesToServer": True,
                    "writeCookiesFromServer": True,
                    "restore_last_session": True,
                    "extra_params": extra_params,
                })
            gl = GoLogin(gl_config)
            ok, proxy_message = self._validate_gologin_profile_proxy(gl, timeout=8)
            if not ok:
                self.status_update.emit(proxy_message, "red")
                self.finished_signal.emit(f"error: {proxy_message}")
                return False
            self._force_direct_browser_when_no_gologin_proxy(gl, extra_params)
            if strict_mode:
                self.status_update.emit(
                    "GoLogin pass-through strict: chi mo profile, khong inject/cookie sync/header patch.",
                    "blue"
                )
            self.status_update.emit("Cho slot GoLogin SDK de mo profile...", "blue")
            with _GOLOGIN_START_LOCK:
                if self._stop_flag:
                    return False
                debugger_address = gl.start()
            if self._stop_flag:
                self._gologin = gl
                self._using_gologin_api = True
                self._debug_port = self._parse_debug_port(debugger_address) or 0
                self._process_pid = int(getattr(gl, "pid", 0) or 0)
                profile_path = getattr(gl, "profile_path", "")
                if profile_path:
                    self._profile_dir = profile_path
                if self._process_pid:
                    self._browser_pids.add(self._process_pid)
                self._browser_pids.update(self._find_pids_by_debug_port(self._debug_port))
                self._stop_gologin_profile()
                return False
            debug_port = self._parse_debug_port(debugger_address)
            if not debug_port:
                raise RuntimeError(f"GoLogin khong tra ve CDP port hop le: {debugger_address}")

            self._gologin = gl
            self._using_gologin_api = True
            self._debug_port = debug_port
            self._process_pid = int(getattr(gl, "pid", 0) or 0)
            self._process = None
            profile_path = getattr(gl, "profile_path", "")
            if profile_path:
                self._profile_dir = profile_path
            if self._process_pid:
                self._browser_pids.add(self._process_pid)
            self._browser_pids.update(self._find_pids_by_debug_port(debug_port))
            self.status_update.emit(
                f"GoLogin SDK da mo profile (CDP port {debug_port}).",
                "green"
            )
            return True
        except Exception as e:
            if gl:
                try:
                    gl.stop()
                except Exception:
                    pass
            msg = str(e)
            lowered = msg.lower()
            if "no module named" in lowered and "gologin" in lowered:
                msg = self._gologin_install_help()
            elif "orbita" in lowered or "browser" in lowered or "download" in lowered:
                msg = (
                    "GoLogin/Orbita local chua san sang. Hay cai GoLogin/Orbita "
                    "hoac cho phep SDK tai Orbita lan dau. Chi tiet: "
                    f"{msg[:180]}"
                )
            elif "profile" in lowered and ("using" in lowered or "already" in lowered):
                msg = f"GoLogin profile dang duoc su dung: {msg[:180]}"
            self.status_update.emit(f"GoLogin SDK loi: {msg[:220]}", "red")
            self.finished_signal.emit("error")
            return False

    def _launch_browser_via_local_chrome(self):
        fail_fast_reason = self._batch_login_fail_fast_reason()
        if fail_fast_reason:
            self._emit_login_error(fail_fast_reason)
            self.status_update.emit(
                f"FAIL FAST: {fail_fast_reason}. Khong mo browser.",
                "red",
            )
            self.finished_signal.emit(f"error: {fail_fast_reason}")
            return False

        try:
            from browser_manager import BrowserManager
        except Exception as exc:
            self.status_update.emit(f"Khong import duoc BrowserManager: {exc}", "red")
            self.finished_signal.emit("error")
            return False

        proxy_server = ""
        if self._proxy_host and self._proxy_port:
            local_port = self._start_local_proxy_bridge()
            proxy_server = f"127.0.0.1:{local_port}" if local_port else self.profile_data.get("proxy", "")
        elif self.profile_data.get("proxy"):
            proxy_server = self.profile_data.get("proxy", "")

        extra_params = [
            f"--window-size={int(self.container_width)},{int(self.container_height)}",
            "--window-position=0,0",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-session-crashed-bubble",
            "--hide-crash-restore-bubble",
            "--disable-infobars",
            f"--ssmatool-embed-token={self._embed_token}",
        ]

        try:
            self._cleanup_stale_profile_processes_before_start()
            self.status_update.emit(
                f"Mo Local Chrome profile {self._browser_id}...",
                "blue"
            )
            manager = BrowserManager()
            self._process, dynamic_port = manager.launch_browser(
                profile_id=self._browser_id,
                profile_dir=self._profile_dir,
                width=self.container_width,
                height=self.container_height,
                proxy_server=proxy_server,
                extra_args=extra_params,
                chrome_path=None,
                browser_backend=LOCAL_CHROME_BACKEND,
            )
            self._browser_manager_acquired = True
            self._debug_port = dynamic_port
            self._process_pid = self._process.pid
            self._using_gologin_api = False
            if self._process_pid:
                self._browser_pids.add(self._process_pid)
            self._browser_pids.update(self._find_pids_by_debug_port(dynamic_port))
            self.status_update.emit(f"Local Chrome da mo profile (CDP port {dynamic_port}).", "green")
            return True
        except Exception as e:
            msg = str(e)
            self.status_update.emit(f"Local Chrome loi: {msg[:220]}", "red")
            self.finished_signal.emit("error")
            return False

    def _release_browser_session(self):
        self._release_managed_browser()
        self._stop_gologin_profile()

    def _request_browser_embed_from_ui(self, timeout=30.0):
        """Ask the Qt UI thread to attach the browser HWND and wait without blocking the UI."""
        if not self.widget_id:
            return False

        self._embed_result = False
        self._embed_done_event.clear()
        payload = {
            "worker_id": id(self),
            "widget_id": int(self.widget_id or 0),
            "debug_port": int(self._debug_port or 0),
            "process_pid": int(self._process_pid or 0),
            "profile_dir": self._profile_dir,
            "profile_id": self._gologin_profile_id or self._browser_id,
            "embed_token": self._embed_token,
            "launch_started_at": float(self._launch_started_at or 0.0),
            "container_width": int(self.container_width or BROWSER_WIDTH),
            "container_height": int(self.container_height or BROWSER_HEIGHT),
            "timeout": float(timeout),
        }
        self.browser_ready_signal.emit(payload)

        deadline = time.time() + float(timeout) + 3.0
        while not self._stop_flag and time.time() < deadline:
            if self._embed_done_event.wait(0.1):
                return bool(self._embed_result)

        if not self._stop_flag:
            self.status_update.emit(
                f"Het thoi gian cho dashboard nhung browser (port {self._debug_port}).",
                "orange",
            )
        return False

    def run(self):
        fail_fast_reason = self._batch_login_fail_fast_reason()
        if fail_fast_reason:
            self._emit_login_error(fail_fast_reason)
            self.status_update.emit(f"FAIL FAST: {fail_fast_reason}. Khong mo browser.", "red")
            self.finished_signal.emit(f"error: {fail_fast_reason}")
            try:
                self._release_browser_session()
            finally:
                self._emit_browser_closed_once("closed")
            return
        try:
            # Kiá»ƒm tra thÃ´ng tin Ä‘Äƒng nháº­p
            if "dang nhap" in self._selected_feature_tokens:
                fail_fast_reason = self._batch_login_fail_fast_reason()
                if fail_fast_reason:
                    self._emit_login_error(fail_fast_reason)
                    self.status_update.emit(f"FAIL FAST: {fail_fast_reason}. Khong mo browser.", "red")
                    self.finished_signal.emit(f"error: {fail_fast_reason}")
                    return

            browser_id = self._browser_id
            backend_label = "Local Chrome" if self._browser_backend == LOCAL_CHROME_BACKEND else "GoLogin Local SDK"
            self.status_update.emit(
                f"[{browser_id}] Mo profile bang {backend_label}",
                "blue"
            )

            # BÆ¯á»šC 2: Má»Ÿ browser
            embedded = False
            with _GOLOGIN_START_LOCK:
                if self._stop_flag:
                    return
                self._launch_started_at = time.time()
                if self._browser_backend == "local_chrome":
                    if not self._launch_browser_via_local_chrome():
                        return
                else:
                    if not self._launch_browser_via_gologin_sdk():
                        return
                if self._stop_flag:
                    return

                # Keep the next GoLogin launch waiting until this window is embedded.
                if self.widget_id:
                    self.status_update.emit("Dang bat cua so browser vao dashboard...", "blue")
                    embedded = self._request_browser_embed_from_ui(timeout=30.0)
                else:
                    self.status_update.emit("Da tat nhung browser, browser se mo ngoai dashboard.", "orange")

            if self.manual_only:
                if embedded:
                    self.status_update.emit("Browser da nhung - ban tu thao tac", "green")
                else:
                    self.status_update.emit(
                        "Khong nhung duoc browser. Browser co the dang mo ngoai dashboard.",
                        "orange"
                    )
                while not self._stop_flag:
                    if not self._browser_alive():
                        break
                    time.sleep(0.5)
                if not self._stop_flag:
                    self.finished_signal.emit("success")
                return

            # BÆ¯á»šC 4: Cháº¡y automation báº±ng CDP
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._run_cdp_automation())
            loop.close()

        except Exception as e:
            self.status_update.emit(f"Loi: {e}", "red")
            self.finished_signal.emit(f"error: {e}")
        finally:
            if not self._async_close_started:
                self._release_browser_session()
                self._emit_browser_closed_once("closed")

    def _prepare_profile_dir(self):
        """Táº¡o profile tá»« zero template."""
        self._process = None
        if not os.path.exists(self._profile_dir):
            self.status_update.emit(f"Táº¡o profile: {os.path.basename(self._profile_dir)}", "blue")
            if os.path.exists(ZERO_PROFILE_ZIP):
                import zipfile
                parent_dir = os.path.dirname(self._profile_dir)
                os.makedirs(parent_dir, exist_ok=True)
                with zipfile.ZipFile(ZERO_PROFILE_ZIP, 'r') as zf:
                    zf.extractall(parent_dir)
                extracted = os.path.join(parent_dir, "gologin_zeroprofile")
                if os.path.exists(extracted):
                    shutil.move(extracted, self._profile_dir)
                else:
                    os.makedirs(self._profile_dir, exist_ok=True)
                self.status_update.emit("Profile tá»« template OK", "green")
            else:
                os.makedirs(self._profile_dir, exist_ok=True)

        # â˜… FIX CRITICAL: Chrome xÃ³a session cookies khi tháº¥y exit_type="Crashed"
        # Patch Preferences Ä‘á»ƒ Chrome nghÄ© láº§n trÆ°á»›c Ä‘Ã³ng bÃ¬nh thÆ°á»ng
        self._fix_chrome_exit_type()

    def _fix_chrome_exit_type(self):
        """Fix Preferences Ä‘á»ƒ Chrome giá»¯ session cookies.
        
        TikTok dÃ¹ng SESSION cookies (khÃ´ng cÃ³ expiry) â†’ Chrome xÃ³a khi Ä‘Ã³ng.
        Fix: báº­t 'Continue where you left off' (restore_on_startup=1)
        â†’ Chrome GIá»® session cookies qua cÃ¡c láº§n khá»Ÿi Ä‘á»™ng.
        """
        prefs_path = os.path.join(self._profile_dir, "Default", "Preferences")
        if not os.path.exists(prefs_path):
            return
        try:
            import json
            with open(prefs_path, "r", encoding="utf-8") as f:
                prefs = json.load(f)

            changed = False

            # Fix 1: exit_type = Normal (trÃ¡nh crash recovery)
            profile = prefs.get("profile", {})
            if profile.get("exit_type", "") != "Normal":
                profile["exit_type"] = "Normal"
                profile["exited_cleanly"] = True
                prefs["profile"] = profile
                changed = True

            # â˜… Fix 2: restore_on_startup = 1 ("Continue where you left off")
            # ÄÃ¢y lÃ  fix THáº¬T Sá»°: Chrome sáº½ GIá»® session cookies khi cÃ³ setting nÃ y
            session = prefs.get("session", {})
            if session.get("restore_on_startup") != 5:
                session["restore_on_startup"] = 5  # 5 = Open New Tab page
                prefs["session"] = session
                changed = True

            if changed:
                with open(prefs_path, "w", encoding="utf-8") as f:
                    json.dump(prefs, f, ensure_ascii=False)
                self.status_update.emit("ðŸ”§ ÄÃ£ báº­t giá»¯ session cookies", "blue")
        except Exception:
            pass

    def _kill_stale_port(self):
        try:
            import psutil
            port_str = str(self._debug_port)
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    name = (proc.info.get('name') or '').lower()
                    if name not in {"chrome.exe", "orbita-browser.exe", "chromium.exe"}:
                        continue
                    cmdline = proc.info.get('cmdline') or []
                    # Kill if same port OR same profile dir
                    has_port = any(f"--remote-debugging-port={port_str}" in c for c in cmdline)
                    profile_dir_str = str(self._profile_dir).replace('\\', '/')
                    has_profile = False
                    for c in cmdline:
                        c_norm = str(c).replace('\\', '/')
                        if "--user-data-dir=" in c_norm and profile_dir_str.lower() in c_norm.lower():
                            has_profile = True
                            break
                    if has_port or has_profile:
                        proc.kill()
                except Exception:
                    pass
        except Exception:
            pass

    def _find_pids_by_debug_port(self, port):
        pids = set()
        if not port:
            return pids
        port_text = str(port)
        try:
            import psutil
            try:
                port_int = int(port)
                for conn in psutil.net_connections(kind="tcp"):
                    try:
                        if not conn.laddr or conn.laddr.port != port_int:
                            continue
                        if conn.status != psutil.CONN_LISTEN:
                            continue
                        if conn.pid:
                            pids.add(int(conn.pid))
                    except Exception:
                        continue
            except Exception:
                pass

            browser_names = {"chrome.exe", "orbita-browser.exe", "chromium.exe"}
            for proc in psutil.process_iter(["name", "pid", "cmdline"]):
                try:
                    name = (proc.info.get("name") or "").lower()
                    if name not in browser_names:
                        continue
                    args = [str(arg) for arg in (proc.info.get("cmdline") or [])]
                    cmdline = " ".join(args).lower()
                    has_port = False
                    for i, arg in enumerate(args):
                        lower_arg = arg.lower()
                        if "remote-debugging-port" not in lower_arg:
                            continue
                        if "=" in lower_arg and lower_arg.rsplit("=", 1)[1].strip().strip('"') == port_text:
                            has_port = True
                            break
                        if i + 1 < len(args) and str(args[i + 1]).strip().strip('"') == port_text:
                            has_port = True
                            break
                    if not has_port and re.search(
                        rf"remote-debugging-port(?:=|\s+){re.escape(port_text)}(?:\D|$)",
                        cmdline,
                    ):
                        has_port = True
                    if has_port:
                        pids.add(proc.info["pid"])
                        for child in proc.children(recursive=True):
                            pids.add(child.pid)
                except Exception:
                    continue
        except Exception:
            pass
        return pids

    def _norm_proc_path(self, path):
        text = str(path or "").strip().strip('"').strip("'")
        if not text:
            return ""
        try:
            text = os.path.abspath(text)
        except Exception:
            pass
        return text.replace("\\", "/").rstrip("/").lower()

    def _find_pids_by_profile_path(self, profile_dir):
        pids = set()
        target_dir = self._norm_proc_path(profile_dir)
        if not target_dir:
            return pids
        try:
            import psutil
            browser_names = {"chrome.exe", "orbita-browser.exe", "chromium.exe"}
            for proc in psutil.process_iter(["name", "pid", "cmdline"]):
                try:
                    name = (proc.info.get("name") or "").lower()
                    if name not in browser_names:
                        continue
                    args = [str(arg) for arg in (proc.info.get("cmdline") or [])]
                    matched = False
                    for i, arg in enumerate(args):
                        lower_arg = arg.lower()
                        if "--user-data-dir" not in lower_arg:
                            continue
                        if "=" in arg:
                            user_dir = arg.split("=", 1)[1]
                        elif i + 1 < len(args):
                            user_dir = args[i + 1]
                        else:
                            user_dir = ""
                        user_dir = self._norm_proc_path(user_dir)
                        if user_dir and (user_dir == target_dir or user_dir.startswith(target_dir + "/")):
                            matched = True
                            break
                    if matched:
                        pids.add(proc.info["pid"])
                        for child in proc.children(recursive=True):
                            pids.add(child.pid)
                except Exception:
                    continue
        except Exception:
            pass
        return pids

    def _collect_browser_pids(self, debug_port=None, profile_dir=None, known_pids=None):
        pids = set(int(pid) for pid in (known_pids or []) if pid)
        if getattr(self, "_process_pid", 0):
            pids.add(int(self._process_pid))
        pids.update(int(pid) for pid in self._browser_pids if pid)
        pids.update(self._find_pids_by_debug_port(debug_port if debug_port is not None else self._debug_port))
        pids.update(self._find_pids_by_profile_path(profile_dir if profile_dir is not None else self._profile_dir))
        try:
            import psutil
            expanded = set(pids)
            for pid in list(pids):
                try:
                    proc = psutil.Process(pid)
                    for child in proc.children(recursive=True):
                        expanded.add(child.pid)
                except Exception:
                    continue
            pids = expanded
        except Exception:
            pass
        return pids

    def _force_close_browser_processes(self, debug_port=None, profile_dir=None, known_pids=None):
        pids = self._collect_browser_pids(debug_port, profile_dir, known_pids)
        if not pids:
            return
        try:
            import psutil
            browser_names = {"chrome.exe", "orbita-browser.exe", "chromium.exe"}
            procs = []
            for pid in sorted(pids, reverse=True):
                try:
                    proc = psutil.Process(pid)
                    name = (proc.name() or "").lower()
                    if name in browser_names:
                        procs.append(proc)
                except Exception:
                    continue

            for proc in procs:
                try:
                    for child in proc.children(recursive=True):
                        try:
                            child.terminate()
                        except Exception:
                            pass
                    proc.terminate()
                except Exception:
                    pass
            _, alive = psutil.wait_procs(procs, timeout=4)
            for proc in alive:
                try:
                    for child in proc.children(recursive=True):
                        try:
                            child.kill()
                        except Exception:
                            pass
                    proc.kill()
                except Exception:
                    pass
        except Exception:
            pass

    def _browser_alive(self):
        if self._embedded_hwnd:
            try:
                if win32gui.IsWindow(self._embedded_hwnd):
                    return True
            except Exception:
                pass

        if self._process:
            try:
                return self._process.poll() is None
            except Exception:
                pass

        if self._debug_port and _is_port_open(self._debug_port, timeout=0.2):
            return True

        for pid in list(self._browser_pids):
            try:
                import psutil
                if psutil.pid_exists(pid):
                    return True
            except Exception:
                pass

        if self._gologin:
            try:
                import psutil
                pid = int(getattr(self._gologin, "pid", 0) or 0)
                if pid and psutil.pid_exists(pid):
                    return True
            except Exception:
                pass

        return False

    def _stop_gologin_profile(self):
        gologin = self._gologin
        debug_port = self._debug_port
        profile_dir = self._profile_dir
        known_pids = set(self._browser_pids)
        if self._process_pid:
            known_pids.add(self._process_pid)

        if gologin:
            try:
                gologin.stop()
            except Exception:
                pass
        self._force_close_browser_processes(debug_port, profile_dir, known_pids)
        self._gologin = None
        self._using_gologin_api = False
        self._process_pid = 0
        self._browser_pids.clear()

    def _embed_browser_window_strict(self):
        """Embed the GoLogin/Orbita browser window into the Qt preview container."""
        import ctypes

        if not self.widget_id:
            return False

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        widget_id = int(self.widget_id)
        browser_names = {"chrome.exe", "orbita-browser.exe", "chromium.exe"}
        profile_dir_norm = self._norm_proc_path(self._profile_dir)
        profile_id_hint = (self._gologin_profile_id or self._browser_id or "").strip().lower()

        for _ in range(40):
            if self._stop_flag:
                return False
            try:
                if win32gui.IsWindow(widget_id):
                    break
            except Exception:
                pass
            time.sleep(0.05)
        else:
            self.status_update.emit("Container preview khong hop le, khong the nhung browser.", "orange")
            return False

        def container_size():
            try:
                _left, _top, right, bottom = win32gui.GetClientRect(widget_id)
                width = max(1, int(right))
                height = max(1, int(bottom))
                if width >= 100 and height >= 100:
                    return width, height
            except Exception:
                pass
            return int(self.container_width or BROWSER_WIDTH), int(self.container_height or BROWSER_HEIGHT)

        def hwnd_area(hwnd):
            try:
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                return max(0, right - left) * max(0, bottom - top)
            except Exception:
                return 0

        def proc_info(pid):
            info = {"name": "", "exe": "", "cmd": "", "created_at": 0.0}
            try:
                import psutil
                proc = psutil.Process(int(pid))
                info["name"] = (proc.name() or "").lower()
                try:
                    info["exe"] = self._norm_proc_path(proc.exe())
                except Exception:
                    pass
                try:
                    info["cmd"] = " ".join(str(arg) for arg in proc.cmdline()).replace("\\", "/").lower()
                except Exception:
                    pass
                try:
                    info["created_at"] = float(proc.create_time())
                except Exception:
                    pass
            except Exception:
                pass
            return info

        def has_profile_hint(info):
            text = f"{info.get('exe', '')} {info.get('cmd', '')}".lower()
            if profile_dir_norm and profile_dir_norm in text:
                return True
            if profile_id_hint and profile_id_hint in text:
                return True
            port_hint = f"remote-debugging-port={self._debug_port}"
            if self._debug_port and port_hint in text:
                return True
            return False

        def enum_cb(hwnd, results):
            try:
                if not win32gui.IsWindow(hwnd):
                    return True
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                if win32gui.GetClassName(hwnd) != "Chrome_WidgetWin_1":
                    return True
                area = hwnd_area(hwnd)
                if area < 20000:
                    return True
                process_id = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
                results.append((hwnd, int(process_id.value), area, win32gui.GetParent(hwnd)))
            except Exception:
                pass
            return True

        def enum_browser_windows(results):
            try:
                win32gui.EnumWindows(enum_cb, results)
                return True, ""
            except Exception as exc:
                first_error = str(exc)[:80]

            try:
                from ctypes import wintypes
                callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

                @callback_type
                def ctypes_enum_cb(hwnd, _lparam):
                    enum_cb(hwnd, results)
                    return True

                enum_windows = user32.EnumWindows
                enum_windows.argtypes = [callback_type, wintypes.LPARAM]
                enum_windows.restype = wintypes.BOOL
                kernel32.SetLastError(0)
                ok = enum_windows(ctypes_enum_cb, 0)
                if ok or results:
                    return True, ""
                return False, f"{first_error}; winerr={kernel32.GetLastError()}"
            except Exception as exc:
                return False, f"{first_error}; ctypes={str(exc)[:80]}"

        known_pid = getattr(self, "_process_pid", 0) or (self._process.pid if self._process else None)
        last_hwnd_count = 0
        last_pid_count = 0
        last_match_count = 0
        last_error = ""
        self.status_update.emit(
            f"Embed scan: port={self._debug_port}, profile={profile_id_hint[:12] or 'unknown'}",
            "blue",
        )

        for attempt in range(300):
            if self._stop_flag:
                return False

            hwnds = []
            ok, enum_error = enum_browser_windows(hwnds)
            if not ok:
                last_error = enum_error
                time.sleep(0.05)
                continue
            last_hwnd_count = len(hwnds)

            all_pids = self._collect_browser_pids(
                self._debug_port,
                self._profile_dir,
                [known_pid] if known_pid else [],
            )
            if all_pids:
                self._browser_pids.update(all_pids)
            last_pid_count = len(all_pids)

            exact = []
            hinted = []
            fresh = []
            for hwnd, wpid, area, parent_hwnd in hwnds:
                if wpid in all_pids:
                    exact.append((area, hwnd, wpid))
                    continue
                if attempt < 30:
                    continue
                info = proc_info(wpid)
                if info["name"] not in browser_names:
                    continue
                if self._launch_started_at and info["created_at"] < self._launch_started_at - 10:
                    continue
                if has_profile_hint(info):
                    hinted.append((info["created_at"], area, hwnd, wpid))
                elif attempt >= 60 and not parent_hwnd:
                    fresh.append((info["created_at"], area, hwnd, wpid))
            last_match_count = len(exact) + len(hinted) + len(fresh)

            if attempt in (60, 140, 220) and not last_match_count:
                self.status_update.emit(
                    f"Embed wait: hwnd={last_hwnd_count}, pid={last_pid_count}, port={self._debug_port}",
                    "blue",
                )

            target_hwnd = 0
            target_pid = 0
            if exact:
                _area, target_hwnd, target_pid = max(exact, key=lambda item: item[0])
            elif hinted:
                _created, _area, target_hwnd, target_pid = max(hinted, key=lambda item: (item[0], item[1]))
            elif fresh:
                _created, _area, target_hwnd, target_pid = max(fresh, key=lambda item: (item[0], item[1]))
                self.status_update.emit(
                    f"Embed fallback: fresh hwnd pid={target_pid}, port={self._debug_port}",
                    "orange",
                )

            if not target_hwnd:
                time.sleep(0.02 if attempt < 160 else 0.05)
                continue

            try:
                width, height = container_size()
                if win32gui.IsIconic(target_hwnd):
                    win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)

                parent_style = win32gui.GetWindowLong(widget_id, win32con.GWL_STYLE)
                parent_style |= (
                    getattr(win32con, "WS_CLIPCHILDREN", 0x02000000) |
                    getattr(win32con, "WS_CLIPSIBLINGS", 0x04000000)
                )
                win32gui.SetWindowLong(widget_id, win32con.GWL_STYLE, parent_style)

                style = win32gui.GetWindowLong(target_hwnd, win32con.GWL_STYLE)
                style &= ~(
                    win32con.WS_CAPTION |
                    win32con.WS_THICKFRAME |
                    win32con.WS_BORDER |
                    win32con.WS_MINIMIZEBOX |
                    win32con.WS_MAXIMIZEBOX |
                    win32con.WS_SYSMENU |
                    getattr(win32con, "WS_POPUP", 0x80000000)
                )
                style |= (
                    win32con.WS_CHILD |
                    win32con.WS_VISIBLE |
                    getattr(win32con, "WS_CLIPSIBLINGS", 0x04000000) |
                    getattr(win32con, "WS_CLIPCHILDREN", 0x02000000)
                )
                win32gui.SetWindowLong(target_hwnd, win32con.GWL_STYLE, style)

                ex_style = win32gui.GetWindowLong(target_hwnd, win32con.GWL_EXSTYLE)
                ex_style &= ~(
                    win32con.WS_EX_DLGMODALFRAME |
                    win32con.WS_EX_WINDOWEDGE |
                    win32con.WS_EX_CLIENTEDGE |
                    win32con.WS_EX_STATICEDGE |
                    getattr(win32con, "WS_EX_NOACTIVATE", 0x08000000) |
                    getattr(win32con, "WS_EX_APPWINDOW", 0x00040000) |
                    getattr(win32con, "WS_EX_TOPMOST", 0x00000008)
                )
                win32gui.SetWindowLong(target_hwnd, win32con.GWL_EXSTYLE, ex_style)

                kernel32.SetLastError(0)
                win32gui.SetParent(target_hwnd, widget_id)
                if win32gui.GetParent(target_hwnd) != widget_id:
                    raise RuntimeError(f"SetParent failed, winerr={kernel32.GetLastError()}")

                tb = APP_TITLEBAR_HEIGHT
                swp_framechanged = 0x0020
                win32gui.ShowWindow(target_hwnd, win32con.SW_SHOW)
                win32gui.SetWindowPos(
                    target_hwnd,
                    win32con.HWND_TOP,
                    0,
                    -tb,
                    width,
                    height + tb,
                    win32con.SWP_SHOWWINDOW |
                    swp_framechanged |
                    win32con.SWP_NOACTIVATE |
                    getattr(win32con, "SWP_NOOWNERZORDER", 0x0200),
                )

                self._embedded_hwnd = target_hwnd
                self._browser_pids.add(int(target_pid))
                self.status_update.emit(
                    f"Browser nhÃºng OK! pid={target_pid}, hwnd={target_hwnd}, port={self._debug_port}",
                    "green",
                )

                import threading
                self._lock_thread = threading.Thread(
                    target=self._lock_browser_position,
                    args=(target_hwnd, width, height),
                    daemon=True,
                )
                self._lock_thread.start()
                return True
            except Exception as exc:
                last_error = str(exc)[:80]
                if attempt in (0, 60, 140, 220):
                    self.status_update.emit(f"Nhung browser retry: {last_error}", "orange")
                time.sleep(0.05)
                continue

        detail = f"hwnd={last_hwnd_count}, pid={last_pid_count}, match={last_match_count}, port={self._debug_port}, profile={profile_id_hint[:12] or 'unknown'}"
        if last_error:
            detail += f", enum={last_error}"
        self.status_update.emit(
            f"Khong tim thay cua so Orbita/Chrome dung GoLogin profile ({detail})",
            "orange",
        )
        return False

    def _embed_browser_window(self):
        return self._embed_browser_window_strict()


    def _lock_browser_position(self, hwnd, width, height):
        """LiÃªn tá»¥c Ã©p browser vá» vá»‹ trÃ­ (0,-tb) â€” áº©n title bar + chá»‘ng kÃ©o."""
        tb = APP_TITLEBAR_HEIGHT
        last_width = 0
        last_height = 0
        last_sync = 0.0
        while not self._stop_flag:
            try:
                # Dá»«ng náº¿u browser hoáº·c container Ä‘Ã£ bá»‹ Ä‘Ã³ng
                if not win32gui.IsWindow(hwnd):
                    break
                if self.widget_id and not win32gui.IsWindow(self.widget_id):
                    break

                if self.widget_id:
                    _left, _top, right, bottom = win32gui.GetClientRect(self.widget_id)
                    width = max(1, right)
                    height = max(1, bottom)

                now = time.time()
                size_changed = width != last_width or height != last_height
                if size_changed or now - last_sync >= 2.0:
                    win32gui.SetWindowPos(
                        hwnd, None,
                        0, -tb, width, height + tb,
                        win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
                    )
                    last_width = width
                    last_height = height
                    last_sync = now
            except Exception:
                break
            time.sleep(1.0)

    async def _run_cdp_automation(self):
        """Main automation loop dÃ¹ng CDP trá»±c tiáº¿p."""
        from cdp_client import CDPClient

        self.status_update.emit(f"Káº¿t ná»‘i CDP (port {self._debug_port})...", "blue")

        try:
            self._cdp = CDPClient(port=self._debug_port)
            await self._cdp.connect(timeout=15)
            self.status_update.emit("âœ… CDP káº¿t ná»‘i thÃ nh cÃ´ng!", "green")
        except Exception as e:
            self.status_update.emit(f"âŒ CDP lá»—i: {str(e)[:60]}", "red")
            self._release_browser_session()
            self.finished_signal.emit("error")
            return

        cdp = self._cdp

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        #  STEALTH INJECTION â€” Cháº¡y TRÆ¯á»šC Má»ŒI navigation
        #  Patch navigator.webdriver, window.chrome, v.v.
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        try:
            if self._should_preserve_gologin_fingerprint():
                webdriver_value = "unknown"
                try:
                    webdriver_value = await cdp.evaluate("navigator.webdriver")
                except Exception:
                    pass
                self.status_update.emit(
                    f"GoLogin fingerprint mode: bo qua stealth JS cua tool (webdriver={webdriver_value})",
                    "blue",
                )
            else:
                await cdp.send("Page.addScriptToEvaluateOnNewDocument", {
                "source": r"""
                // â˜… Patch navigator.webdriver = false (quan trá»ng nháº¥t)
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => false,
                    configurable: true
                });

                // â˜… Fake window.chrome (Chrome tá»± cÃ³ nhÆ°ng automation mode thiáº¿u)
                if (!window.chrome) {
                    window.chrome = {
                        runtime: {
                            onConnect: { addListener: function() {} },
                            onMessage: { addListener: function() {} }
                        },
                        loadTimes: function() { return {}; },
                        csi: function() { return {}; },
                        app: { isInstalled: false, InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }, RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' } }
                    };
                }

                // â˜… XÃ³a cdc_ markers (chromedriver fingerprint)
                for (const prop of Object.keys(window)) {
                    if (prop.match(/^cdc_/) || prop.match(/^\$cdc_/)) {
                        delete window[prop];
                    }
                }

                // â˜… Fake permissions query (notification, push)
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications'
                        ? Promise.resolve({ state: Notification.permission })
                        : originalQuery(parameters)
                );

                // â˜… Fake plugins (trÃ¬nh duyá»‡t tháº­t cÃ³ plugins)
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [
                        { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                        { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                        { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' }
                    ],
                    configurable: true
                });

                // â˜… Fake languages
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en'],
                    configurable: true
                });

                // â˜… Patch iframe contentWindow.navigator.webdriver
                const originalAttachShadow = Element.prototype.attachShadow;
                Element.prototype.attachShadow = function() {
                    return originalAttachShadow.apply(this, arguments);
                };

                // â˜… Hide sourceURL traces of injected scripts
                // Cloudflare/TikTok checks Error stack traces for puppeteer/CDP markers
                const _Error = Error;
                const nativeErrorToString = Error.prototype.toString;
                """
            })
                self.status_update.emit("ðŸ›¡ï¸ Stealth injection OK (navigator.webdriver patched)", "green")
        except Exception as e:
            self.status_update.emit(f"âš ï¸ Stealth injection lá»—i: {str(e)[:50]}", "orange")

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        #  PROXY AUTH HANDLER â€” Tá»± Ä‘iá»n credentials khi cÃ³ proxy
        #  (KhÃ´ng cáº§n xá»­ lÃ½ khi khÃ´ng cÃ³ proxy â€” --no-proxy-server Ä‘Ã£ fix)
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        try:
            proxy_payload = self._get_proxy_payload()
            if proxy_payload:
                proxy_user = proxy_payload.get("username", "")
                proxy_pass = proxy_payload.get("password", "")
                if proxy_user and proxy_pass:
                    # â˜… CÃ³ proxy cÃ³ auth â†’ báº­t Fetch Ä‘á»ƒ tá»± Ä‘iá»n credentials
                    await cdp.send("Fetch.enable", {
                        "handleAuthRequests": True,
                        "patterns": [{"urlPattern": "*"}]
                    })

                    async def _provide_auth(params):
                        """Auto-fill proxy credentials."""
                        request_id = params.get("requestId", "")
                        if request_id:
                            try:
                                await cdp.send("Fetch.continueWithAuth", {
                                    "requestId": request_id,
                                    "authChallengeResponse": {
                                        "response": "ProvideCredentials",
                                        "username": proxy_user,
                                        "password": proxy_pass
                                    }
                                })
                            except Exception:
                                pass

                    async def _continue_request(params):
                        """Continue intercepted requests (báº¯t buá»™c khi Fetch.enable)."""
                        request_id = params.get("requestId", "")
                        if request_id:
                            try:
                                await cdp.send("Fetch.continueRequest", {"requestId": request_id})
                            except Exception:
                                pass

                    cdp.on("Fetch.authRequired", _provide_auth)
                    cdp.on("Fetch.requestPaused", _continue_request)
                    self.status_update.emit("ðŸ”‘ Proxy auth handler: tá»± Ä‘iá»n credentials", "blue")
        except Exception as e:
            self.status_update.emit(f"âš ï¸ Proxy auth setup: {str(e)[:50]}", "orange")

        try:
            cookie_str = self.profile_data.get("cookie", "")
            has_saved_cookie = bool(cookie_str and len(cookie_str) > 20)

            # GoLogin profile owns Accept-Language/locale. Do not override it during login.
            if self._should_preserve_gologin_fingerprint():
                self.status_update.emit("GoLogin fingerprint mode: giu header/locale cua profile", "blue")
            else:
                try:
                    await cdp.send("Network.setExtraHTTPHeaders", {
                        "headers": {
                            "Accept-Language": "en-US,en;q=0.9"
                        }
                    })
                except Exception:
                    pass
                try:
                    await cdp.send("Emulation.setLocaleOverride", {"locale": "en-US"})
                except Exception:
                    pass

            await self._emit_runtime_fingerprint_snapshot(cdp)

            # â˜… BÆ¯á»šC 1: á»ž láº¡i New Tab â€” chá» 3s nhÆ° ngÆ°á»i tháº­t
            self.status_update.emit("Browser da mo - dang o New Tab...", "blue")
            if not await self._verify_proxy_in_browser(cdp):
                self.finished_signal.emit("error")
                return

            await asyncio.sleep(3)

            # â˜… BÆ¯á»šC 2: Má»Ÿ TikTok báº±ng Ä‘Ãºng session Ä‘ang náº±m trong profile GoLogin.
            # Cookie trong tool chá»‰ lÃ  báº£n dá»± phÃ²ng, khÃ´ng bÆ¡m sá»›m Ä‘á»ƒ trÃ¡nh ghi Ä‘Ã¨ phiÃªn tháº­t.
            if has_saved_cookie:
                self.status_update.emit(
                    "Co cookie du phong trong tool, chua nap som de giu nguyen phien GoLogin.",
                    "blue",
                )

            # â˜… BÆ¯á»šC 3: Má»Ÿ Google ngáº¯n, sau Ä‘Ã³ vÃ o tháº³ng TikTok.
            # KhÃ´ng tÃ¬m/click TikTok trÃªn Google Ä‘á»ƒ trÃ¡nh Google unusual-traffic/CAPTCHA.
            if not await self._warmup_google_then_tiktok_direct(cdp):
                self.status_update.emit("Warm-up loi - vao TikTok truc tiep", "orange")
                await self._type_url_in_addressbar("tiktok.com", wait=6, cdp=cdp)
            if not await self._wait_for_tiktok_ready(cdp):
                reason = "TikTok káº¹t á»Ÿ Please wait"
                self.status_update.emit(f"Loi: {reason}", "red")
                await self._hold_browser_for_action_issue(cdp, reason)
                if not self._stop_flag:
                    self.finished_signal.emit(f"error: {reason}")
                return

            # â˜… BÆ¯á»šC 4: Kiá»ƒm tra session tháº­t cá»§a profile GoLogin trÆ°á»›c.
            profile_logged_in = await self._check_logged_in(cdp)
            if profile_logged_in:
                self.status_update.emit("Profile GoLogin dang co phien TikTok hop le.", "green")
                await self._persist_tiktok_cookies(cdp)
            else:
                if has_saved_cookie and self._has_selected_feature("Dang nhap"):
                    self.status_update.emit(
                        "Profile GoLogin chua dang nhap; cookie du phong chi dung trong buoc Dang nhap.",
                        "orange",
                    )
                else:
                    self.status_update.emit(
                        "Profile GoLogin chua dang nhap; khong nap cookie du phong o buoc khoi dong.",
                        "orange",
                    )

            # áº¨n viá»n focus/outline khi bot click
            if not self._should_preserve_gologin_fingerprint():
                await cdp.evaluate("""
            (() => {
                const style = document.createElement('style');
                style.textContent = `
                    *:focus, *:focus-visible, *:focus-within {
                        outline: none !important;
                        box-shadow: none !important;
                    }
                    [data-e2e] { outline: none !important; }
                `;
                document.head.appendChild(style);
            })()
            """)

            # Bá» qua popup chá»n chá»§ Ä‘á»
            await self._skip_tiktok_popup(cdp)
            await asyncio.sleep(1)

            # Cháº¡y cÃ¡c chá»©c nÄƒng
            self.status_update.emit(f"Chuc nang da chon: {self.selected_features}", "blue")
            if self._unsupported_selected_features:
                skipped = ", ".join(self._unsupported_selected_features)
                self.status_update.emit(
                    f"Bo qua chuc nang khong con ho tro: {skipped}",
                    "orange",
                )
            login_ok = bool(profile_logged_in)
            any_feature_ran = False
            feature_failures = []

            if self._has_selected_feature("Dang nhap"):
                any_feature_ran = True
                self._last_login_error = ""
                login_ok = await self._do_login(cdp)
                if login_ok is False:
                    detail = self._last_login_error or "Dang nhap that bai"
                    if not self._stop_flag:
                        recovered = await self._hold_browser_for_login_recovery(cdp, detail)
                        if recovered:
                            self.finished_signal.emit("success")
                            return
                        self.finished_signal.emit(f"error: {detail}")
                        return
                    self.finished_signal.emit(f"error: {detail}")
                    return
            if self._has_selected_feature("Cap nhat thong ke"):
                any_feature_ran = True
                login_ok = await self._ensure_logged_in_for_feature(cdp, "Cáº­p nháº­t thá»‘ng kÃª", login_ok)
                if login_ok:
                    self.status_update.emit("Bat dau cap nhat thong ke tai khoan...", "blue")
                    stats_ok = await self._update_tiktok_stats(cdp)
                    if not stats_ok and not self._stop_flag:
                        feature_failures.append("Cáº­p nháº­t thá»‘ng kÃª chÆ°a hoÃ n táº¥t")
                elif not self._stop_flag:
                    feature_failures.append("Cáº­p nháº­t thá»‘ng kÃª chÆ°a cháº¡y vÃ¬ profile chÆ°a Ä‘Äƒng nháº­p")

            if self._has_selected_feature("Cap nhat thong tin"):
                any_feature_ran = True
                login_ok = await self._ensure_logged_in_for_feature(cdp, "Cáº­p nháº­t thÃ´ng tin", login_ok)
                if login_ok:
                    self.status_update.emit("Bat dau cap nhat thong tin tai khoan...", "blue")
                    info_ok = await self._update_account_financial_info(cdp)
                    if not info_ok and not self._stop_flag:
                        feature_failures.append("Cáº­p nháº­t thÃ´ng tin chÆ°a hoÃ n táº¥t")
                elif not self._stop_flag:
                    feature_failures.append("Cáº­p nháº­t thÃ´ng tin chÆ°a cháº¡y vÃ¬ profile chÆ°a Ä‘Äƒng nháº­p")

            if self._has_selected_feature("Doi avatar"):
                any_feature_ran = True
                login_ok = await self._ensure_logged_in_for_feature(cdp, "Doi avatar", login_ok)
                if login_ok:
                    if await self._wait_captcha_clear_for_action(cdp, "Doi avatar"):
                        ok = await self._change_tiktok_avatar(cdp)
                        if not ok:
                            self.status_update.emit("Doi avatar that bai - xem log chi tiet.", "red")
                            if not self._stop_flag:
                                feature_failures.append("Äá»•i avatar tháº¥t báº¡i")
                    elif not self._stop_flag:
                        feature_failures.append("Äá»•i avatar bá»‹ dá»«ng vÃ¬ CAPTCHA/challenge")
                elif not self._stop_flag:
                    feature_failures.append("Äá»•i avatar chÆ°a cháº¡y vÃ¬ profile chÆ°a Ä‘Äƒng nháº­p")

            if self._has_selected_feature("Tuong tac o Feed"):
                any_feature_ran = True
                login_ok = await self._ensure_logged_in_for_feature(cdp, "Feed", login_ok)
                if login_ok:
                    if await self._wait_captcha_clear_for_action(cdp, "Feed"):
                        feed_ok = await self._do_feed_interaction(cdp)
                        if not feed_ok and not self._stop_flag:
                            feature_failures.append("TÆ°Æ¡ng tÃ¡c Feed chÆ°a hoÃ n táº¥t")
                    elif not self._stop_flag:
                        feature_failures.append("TÆ°Æ¡ng tÃ¡c Feed bá»‹ dá»«ng vÃ¬ CAPTCHA/challenge")
                elif not self._stop_flag:
                    feature_failures.append("TÆ°Æ¡ng tÃ¡c Feed chÆ°a cháº¡y vÃ¬ profile chÆ°a Ä‘Äƒng nháº­p")

            # â˜… Flexible matching cho keyword feature â€” khá»›p cáº£ (key), (kw), hay báº¥t ká»³ biáº¿n thá»ƒ nÃ o
            has_keyword_feature = self._has_selected_feature_fragment("tu khoa")
            if has_keyword_feature:
                any_feature_ran = True
                login_ok = await self._ensure_logged_in_for_feature(cdp, "Keyword", login_ok)
                if login_ok:
                    if await self._wait_captcha_clear_for_action(cdp, "Keyword"):
                        self.status_update.emit("Bat dau tuong tac theo tu khoa...", "blue")
                        keyword_ok = await self._do_keyword_interaction(cdp)
                        if not keyword_ok and not self._stop_flag:
                            feature_failures.append("TÆ°Æ¡ng tÃ¡c tá»« khÃ³a chÆ°a hoÃ n táº¥t")
                    elif not self._stop_flag:
                        feature_failures.append("TÆ°Æ¡ng tÃ¡c tá»« khÃ³a bá»‹ dá»«ng vÃ¬ CAPTCHA/challenge")
                elif not self._stop_flag:
                    feature_failures.append("TÆ°Æ¡ng tÃ¡c tá»« khÃ³a chÆ°a cháº¡y vÃ¬ profile chÆ°a Ä‘Äƒng nháº­p")

            # Náº¿u KHÃ”NG cÃ³ chá»©c nÄƒng nÃ o cháº¡y â†’ giá»¯ browser má»Ÿ
            if not self.selected_features or not any_feature_ran:
                if self.selected_features and not any_feature_ran:
                    self.status_update.emit(f"Khong co chuc nang nao khop! Features: {self.selected_features}", "orange")
                self.status_update.emit("Browser dang mo - ban tu do su dung", "green")
                while not self._stop_flag:
                    await asyncio.sleep(2)

            if feature_failures and not self._stop_flag:
                reason = "; ".join(feature_failures)
                await self._hold_browser_for_action_issue(cdp, reason)
                if not self._stop_flag:
                    self.finished_signal.emit(f"error: {reason}")
                return
            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            #  Káº¾T THÃšC: LÆ°u cookie â†’ Browser.close (graceful) â†’ Kill
            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

            # â˜… FIX 2 + FIX 4: LÆ¯U COOKIE + STORAGE TRÆ¯á»šC KHI ÄÃ“NG
            self.status_update.emit("Luu du lieu phien truoc khi dong...", "blue")
            try:
                # Chá»‘t TikTok ID trÆ°á»›c khi Ä‘Ã³ng browser. BÆ°á»›c nÃ y click vÃ o há»“ sÆ¡,
                # Ä‘á»c @username tá»« URL/localStorage vÃ  emit vá» UI Ä‘á»ƒ lÆ°u DB.
                final_logged_in = bool(login_ok)
                if not final_logged_in:
                    final_logged_in = await self._check_logged_in(cdp)
                if final_logged_in:
                    self.status_update.emit("ðŸ‘¤ Láº¥y User ID trÆ°á»›c khi Ä‘Ã³ng browser...", "blue")
                    await self._extract_profile_info(cdp, need_reload=False)

                    # Persist session cookies â†’ 30 ngÃ y TRÆ¯á»šC (quan trá»ng nháº¥t)
                    await self._persist_tiktok_cookies(cdp)
                    # Re-save cookie text vÃ o DB
                    await self._resave_cookies_to_db(cdp)
                    # Backup localStorage/sessionStorage
                    await self._save_tiktok_storage(cdp)
                    self.status_update.emit("âœ… ÄÃ£ lÆ°u táº¥t cáº£ dá»¯ liá»‡u phiÃªn", "green")
                else:
                    self.status_update.emit(
                        "âš ï¸ Browser Ä‘ang chÆ°a Ä‘Äƒng nháº­p, bá» qua lÆ°u cookie/storage Ä‘á»ƒ khÃ´ng ghi Ä‘Ã¨ phiÃªn cÅ©.",
                        "orange",
                    )
            except Exception as e:
                self.status_update.emit(f"âš ï¸ LÆ°u phiÃªn lá»—i: {str(e)[:40]}", "orange")

            # BÆ¯á»šC 1: Browser.close (graceful â€” cho Chrome ghi cookie ra Ä‘Ä©a)
            self.status_update.emit("ðŸ”’ ÄÃ³ng browser (graceful â€” chá» Chrome flush cookies)...", "blue")
            try:
                await cdp.send("Browser.close")
                await asyncio.sleep(5)  # â˜… Chá» 5s cho Chrome ghi cookie + SQLite ra Ä‘Ä©a
            except Exception:
                pass

            # BÆ¯á»šC 2: Kill process náº¿u váº«n cÃ²n sá»‘ng
            self._stop_flag = True
            self._release_browser_session()
            self._process = None

            # BÆ¯á»šC 3: Emit finished
            self.status_update.emit("âœ… HoÃ n thÃ nh!", "green")
            self.finished_signal.emit("success")

        except Exception as e:
            err_msg = str(e)
            _closed = ("connectionreset", "connectionclosed", "websocket",
                       "connection refused", "brokenpipe", "oserror",
                       "errno", "closed", "disconnect",
                       "no close frame received or sent")
            if any(s in err_msg.lower() for s in _closed):
                self.status_update.emit("ðŸ”’ Browser Ä‘Ã£ Ä‘Ã³ng.", "blue")
                self.finished_signal.emit("success")
            else:
                self.status_update.emit(f"Lá»—i: {err_msg[:80]}", "red")
                self.finished_signal.emit(f"error: {e}")
        finally:
            try: await cdp.disconnect()
            except Exception: pass

    # â”€â”€â”€ TikTok Automation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


    async def _update_account_financial_info(self, cdp):
        """Cáº­p nháº­t follow, quá»‘c gia vÃ  thÃ´ng tin tiá»n/balance cá»§a tÃ i khoáº£n TikTok."""
        try:
            if not await self._check_logged_in(cdp):
                self.status_update.emit("âš ï¸ ChÆ°a Ä‘Äƒng nháº­p, bá» qua cáº­p nháº­t thÃ´ng tin Ä‘á»ƒ khÃ´ng ghi dá»¯ liá»‡u rá»—ng.", "orange")
                return False

            info = {
                "t_follows": "",
                "country": "",
                "currency": "",
                "earned": "",
                "balance": "",
            }

            self.status_update.emit("ðŸ‘¤ Má»Ÿ trang profile Ä‘á»ƒ láº¥y follow...", "blue")
            await self._navigate_like_human(cdp, "tiktok.com/profile", wait=4)
            await asyncio.sleep(1)
            profile_info = await cdp.evaluate(r"""
            (() => {
                const textOf = (sel) => {
                    const el = document.querySelector(sel);
                    return el ? (el.textContent || '').trim() : '';
                };
                const followers =
                    textOf('[data-e2e="followers-stat"]') ||
                    textOf('strong[data-e2e="follower-count"]') ||
                    textOf('[data-e2e*="follower" i]');
                return {followers};
            })()
            """) or {}
            info["t_follows"] = profile_info.get("followers", "")

            self.status_update.emit("ðŸŒ Má»Ÿ cÃ i Ä‘áº·t tÃ i khoáº£n Ä‘á»ƒ láº¥y quá»‘c gia...", "blue")
            await self._navigate_like_human(cdp, "tiktok.com/setting/account", wait=4)
            await asyncio.sleep(1)
            country = await cdp.evaluate(r"""
            (() => {
                const labels = ['country/region', 'country', 'region', 'quá»‘c gia', 'khu vá»±c'];
                const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
                const norm = (s) => clean(s).toLowerCase();
                const rows = Array.from(document.querySelectorAll('div, section, li, p, span'));
                for (const el of rows) {
                    const text = clean(el.innerText || el.textContent || '');
                    const lower = norm(text);
                    if (!text || text.length > 180) continue;
                    const label = labels.find(k => lower.includes(k));
                    if (!label) continue;
                    let value = text
                        .replace(/country\/region/i, '')
                        .replace(/country/i, '')
                        .replace(/region/i, '')
                        .replace(/quá»‘c gia/i, '')
                        .replace(/khu vá»±c/i, '')
                        .replace(/[:ï¼š]/g, '')
                        .trim();
                    if (value && value.length <= 60) return value;
                    const parent = el.parentElement;
                    if (parent) {
                        const pText = clean(parent.innerText || parent.textContent || '');
                        value = pText
                            .replace(/country\/region/i, '')
                            .replace(/country/i, '')
                            .replace(/region/i, '')
                            .replace(/quá»‘c gia/i, '')
                            .replace(/khu vá»±c/i, '')
                            .replace(/[:ï¼š]/g, '')
                            .trim();
                        if (value && value.length <= 80) return value;
                    }
                }
                return '';
            })()
            """) or ""
            info["country"] = country

            self.status_update.emit("ðŸ’µ Má»Ÿ Balance Ä‘á»ƒ láº¥y tiá»n tá»‡/sá»‘ dÆ°...", "blue")
            await self._navigate_like_human(cdp, "tiktok.com/setting/balance", wait=5)
            await asyncio.sleep(1)
            money_info = await cdp.evaluate(r"""
            (() => {
                const body = (document.body && document.body.innerText) || '';
                const lines = body.split(/\n+/).map(s => s.trim()).filter(Boolean);
                const moneyRe = /(?:[$â‚¬Â£Â¥â‚«]\s*[0-9][0-9.,]*|[0-9][0-9.,]*\s*(?:USD|EUR|GBP|VND|JPY|US\$))/i;
                const moneyLines = lines.filter(line => moneyRe.test(line));
                const firstMoney = moneyLines[0] || '';
                let currency = '';
                const curMatch = firstMoney.match(/USD|EUR|GBP|VND|JPY|US\$|[$â‚¬Â£Â¥â‚«]/i);
                if (curMatch) currency = curMatch[0];
                return {
                    balance: firstMoney,
                    currency,
                    earned: moneyLines[1] || ''
                };
            })()
            """) or {}
            info["balance"] = money_info.get("balance", "")
            info["currency"] = money_info.get("currency", "")
            info["earned"] = money_info.get("earned", "")

            update_data = {k: v for k, v in info.items() if str(v or "").strip()}
            if not update_data:
                self.status_update.emit("âš ï¸ KhÃ´ng láº¥y Ä‘Æ°á»£c thÃ´ng tin há»£p lá»‡, giá»¯ nguyÃªn dá»¯ liá»‡u cÅ©.", "orange")
                return False

            self.profile_data.update(update_data)
            self.profile_update_signal.emit(self.profile_data)
            self.status_update.emit(
                f"âœ… Info: Follow={info['t_follows'] or 'N/A'} | QG={info['country'] or 'N/A'} | Balance={info['balance'] or 'N/A'}",
                "green"
            )
            return True
        except Exception as e:
            self.status_update.emit(f"âš ï¸ Lá»—i cáº­p nháº­t thÃ´ng tin: {str(e)[:60]}", "red")
            return False


    async def _update_tiktok_stats(self, cdp):
        """QuÃ©t trang Profile vÃ  Studio Ä‘á»ƒ cáº­p nháº­t Follow, Views, Video."""
        try:
            if not await self._check_logged_in(cdp):
                self.status_update.emit("âš ï¸ ChÆ°a Ä‘Äƒng nháº­p, bá» qua cáº­p nháº­t thá»‘ng kÃª Ä‘á»ƒ khÃ´ng ghi dá»¯ liá»‡u rá»—ng.", "orange")
                return False

            self.status_update.emit("ðŸ”„ Äang vÃ o trang cÃ¡ nhÃ¢n...", "blue")
            await cdp.navigate("https://www.tiktok.com/profile")
            await asyncio.sleep(4)
            
            # Äá»£i load xong hoáº·c redirect xong
            await cdp.evaluate("""
                new Promise(resolve => {
                    if (document.readyState === 'complete') resolve();
                    else window.addEventListener('load', resolve);
                });
            """)
            await asyncio.sleep(2)

            stats = await cdp.evaluate("""
                (() => {
                    let followers = "";
                    let likes = "";
                    let videos = "";
                    let hasProfileSignal = false;
                    
                    try {
                        const f_el = document.querySelector('[data-e2e="followers-stat"]');
                        if (f_el) {
                            followers = f_el.textContent.trim();
                            hasProfileSignal = true;
                        }
                        
                        const l_el = document.querySelector('[data-e2e="likes-stat"]');
                        if (l_el) {
                            likes = l_el.textContent.trim();
                            hasProfileSignal = true;
                        }
                        
                        const v_els = document.querySelectorAll('[data-e2e="user-post-item"]');
                        videos = v_els.length.toString();
                        if (v_els.length > 0) {
                            hasProfileSignal = true;
                        } else {
                            // Cá»‘ Ä‘áº¿m tháº» video náº¿u giao diá»‡n Ä‘á»•i
                            const fallbackVideos = document.querySelectorAll('div[class*="DivItemContainerForProfile"]');
                            videos = fallbackVideos.length.toString();
                            if (fallbackVideos.length > 0) hasProfileSignal = true;
                        }
                        if (document.querySelector('h1[data-e2e="user-title"], h2[data-e2e="user-subtitle"]')) {
                            hasProfileSignal = true;
                        }
                    } catch(e) {}
                    
                    return {followers, likes, videos, hasProfileSignal};
                })()
            """)
            
            if not stats:
                self.status_update.emit("âš ï¸ KhÃ´ng láº¥y Ä‘Æ°á»£c thÃ´ng sá»‘ tá»« trang cÃ¡ nhÃ¢n", "orange")
                return False

            if not stats.get("hasProfileSignal"):
                self.status_update.emit("âš ï¸ Trang profile chÆ°a load Ä‘Ãºng, giá»¯ nguyÃªn thá»‘ng kÃª cÅ©.", "orange")
                return False

            followers = str(stats.get('followers', '') or '').strip()
            likes = str(stats.get('likes', '') or '').strip()
            videos = str(stats.get('videos', '') or '').strip()
            if not any([followers, likes, videos]):
                self.status_update.emit("âš ï¸ Thá»‘ng kÃª tráº£ vá» rá»—ng, giá»¯ nguyÃªn dá»¯ liá»‡u cÅ©.", "orange")
                return False
            
            self.status_update.emit(f"âœ… Follow: {followers} | Likes: {likes} | Videos: {videos}", "green")
            
            # Gá»­i signal vá» UI Ä‘á»ƒ update table
            # Ta dÃ¹ng profile_update_signal hoáº·c tá»± update vÃ o profile_data
            update_data = {}
            if followers:
                update_data["t_follows"] = followers
            if likes:
                update_data["t_views"] = likes # Táº¡m dÃ¹ng Likes cho cá»™t T.Views vÃ¬ profile chá»‰ hiá»‡n Likes
            if videos:
                update_data["t_video"] = videos
            self.profile_data.update(update_data)
            self.profile_update_signal.emit(self.profile_data)
            return True
            
        except Exception as e:
            self.status_update.emit(f"âš ï¸ Lá»—i cáº­p nháº­t thá»‘ng kÃª: {str(e)[:50]}", "red")
            return False

    async def _skip_tiktok_popup(self, cdp):
        """Bá» qua popup chá»n chá»§ Ä‘á»."""
        try:
            has_skip = await cdp.evaluate("""
                (() => {
                    const btns = document.querySelectorAll('button');
                    for (const b of btns) {
                        if (b.textContent.includes('Skip')) { b.click(); return 'skipped'; }
                    }
                    return null;
                })()
            """)
            if has_skip:
                self.status_update.emit("âœ… ÄÃ£ bá» qua popup chá»§ Ä‘á»", "green")
                return
        except Exception:
            pass

        try:
            # Chá»n 1 Ã´ rá»“i báº¥m Continue
            await cdp.evaluate("""
                (() => {
                    const item = document.querySelector('[data-e2e="interest-item"]');
                    if (item) { item.click(); }
                    setTimeout(() => {
                        const btns = document.querySelectorAll('button');
                        for (const b of btns) {
                            if (b.textContent.includes('Continue')) { b.click(); break; }
                        }
                    }, 500);
                })()
            """)
            self.status_update.emit("âœ… ÄÃ£ bá» qua popup (Continue)", "green")
        except Exception:
            pass

    async def _reinject_cookies(self, cdp, cookie_str=None):
        """Náº¡p cookie dá»± phÃ²ng vÃ o browser khi luá»“ng Ä‘Äƒng nháº­p/khÃ´i phá»¥c cáº§n dÃ¹ng."""
        if cookie_str is None:
            cookie_str = self.profile_data.get("cookie", "")
        cookie_str = str(cookie_str or "")
        if not cookie_str or len(cookie_str) <= 20:
            return 0

        try:
            import time as _time
            expires_epoch = _time.time() + 30 * 24 * 3600  # 30 ngÃ y
            cookies_to_set = []
            for pair in cookie_str.split(";"):
                pair = pair.strip()
                if "=" in pair:
                    name, value = pair.split("=", 1)
                    cookies_to_set.append({
                        "name": name.strip(),
                        "value": value.strip(),
                        "domain": ".tiktok.com",
                        "path": "/",
                        "url": "https://www.tiktok.com/",
                        "secure": True,
                        "expires": expires_epoch,
                    })
            if cookies_to_set:
                await cdp.set_cookies(cookies_to_set)
                return len(cookies_to_set)
            return 0
        except Exception:
            return 0

    async def _try_cookie_login_from_string(self, cdp, cookie_str: str, label: str) -> bool:
        """DÃ¹ng cookie dá»± phÃ²ng chá»‰ trong luá»“ng Ä‘Äƒng nháº­p/khÃ´i phá»¥c."""
        cookie_str = str(cookie_str or "").strip()
        if len(cookie_str) <= 20:
            return False

        self.status_update.emit(f"ðŸª Thá»­ khÃ´i phá»¥c phiÃªn báº±ng {label}...", "blue")
        await self._restore_tiktok_storage(cdp)
        injected_count = await self._reinject_cookies(cdp, cookie_str=cookie_str)
        if injected_count <= 0:
            self.status_update.emit(f"âš ï¸ {label} khÃ´ng cÃ³ cookie há»£p lá»‡ Ä‘á»ƒ náº¡p.", "orange")
            return False

        self.status_update.emit(f"ðŸª ÄÃ£ náº¡p {injected_count} cookie tá»« {label}", "blue")
        try:
            await cdp.send("Page.reload")
        except Exception:
            await self._navigate_like_human(cdp, "tiktok.com", wait=5)
        await asyncio.sleep(5)

        if await self._check_logged_in(cdp):
            self.status_update.emit("ðŸ” Cookie há»£p lá»‡ â€” Ä‘ang xÃ¡c minh ID...", "blue")
            verified_id = await self._extract_profile_info(cdp, need_reload=False)
            if verified_id:
                self.status_update.emit(f"âœ… ÄÄƒng nháº­p cookie thÃ nh cÃ´ng! ({verified_id})", "green")
            else:
                self.status_update.emit("âœ… Cookie login OK (chÆ°a láº¥y Ä‘Æ°á»£c @username)", "green")
            await self._persist_tiktok_cookies(cdp)
            return True

        self.status_update.emit(f"âš ï¸ {label} khÃ´ng khÃ´i phá»¥c Ä‘Æ°á»£c phiÃªn.", "orange")
        return False

    async def _type_url_in_addressbar(self, url: str, wait: float = 6.0, cdp=None):
        """â˜… Má»Ÿ URL tá»« New Tab â€” dÃ¹ng JS location.href (an toÃ n Ä‘a luá»“ng).

        Má»—i CDP session lÃ  Ä‘á»™c láº­p â†’ khÃ´ng conflict khi cháº¡y nhiá»u profile.
        Tá»« chrome://newtab â†’ chuyá»ƒn vá» about:blank trÆ°á»›c â†’ rá»“i location.href.
        """
        text_to_type = url.replace("https://", "").replace("http://", "").replace("www.", "")
        full_url = f"https://www.{text_to_type}"
        now_ts = time.time()

        if not cdp:
            return

        # Cháº·n spam Ä‘iá»u hÆ°á»›ng liÃªn tiáº¿p gÃ¢y loop reload.
        if self._last_nav_url == full_url and (now_ts - self._last_nav_ts) < 8:
            return

        try:
            # Chrome://newtab lÃ  trang Ä‘áº·c biá»‡t â€” JS evaluate cÃ³ thá»ƒ fail
            # â†’ chuyá»ƒn vá» about:blank trÆ°á»›c (trang thÆ°á»ng)
            try:
                cur = await cdp.evaluate("window.location.href") or ""
                if isinstance(cur, str) and ("tiktok.com" in cur and "login" not in cur):
                    body_text = ""
                    try:
                        body_text = await cdp.evaluate("document.body ? document.body.innerText : ''") or ""
                    except Exception:
                        pass
                    if "please wait" not in body_text.lower():
                        self._last_nav_url = full_url
                        self._last_nav_ts = now_ts
                        return
                if cur.startswith("chrome://") or cur.startswith("chrome-search://"):
                    await cdp.navigate("about:blank")
                    await asyncio.sleep(0.5)
            except Exception:
                # Náº¿u evaluate fail â†’ cháº¯c cháº¯n Ä‘ang á»Ÿ trang Ä‘áº·c biá»‡t
                await cdp.navigate("about:blank")
                await asyncio.sleep(0.5)

            # â˜… DÃ¹ng JS location.href â€” giá»‘ng ngÆ°á»i gÃµ URL rá»“i Enter
            self.status_update.emit(f"â³ Äang táº£i {text_to_type}...", "blue")
            await cdp.evaluate(f'window.location.href = "{full_url}"')
            self._last_nav_url = full_url
            self._last_nav_ts = time.time()
            await asyncio.sleep(wait)
            return
        except Exception:
            pass

        # Last resort: cdp.navigate
        await cdp.navigate(full_url)
        self._last_nav_url = full_url
        self._last_nav_ts = time.time()
        await asyncio.sleep(wait)

    async def _verify_proxy_in_browser(self, cdp) -> bool:
        """Verify the running browser is really using the configured proxy."""
        proxy_payload = self._get_proxy_payload()
        if not proxy_payload:
            return True

        expected_host = proxy_payload.get("host", "")
        expected_type = proxy_payload.get("mode", "")
        self.status_update.emit(
            f"Kiá»ƒm tra proxy trong browser: {expected_type}://{expected_host}:{proxy_payload.get('port')}",
            "blue"
        )

        try:
            await cdp.navigate(f"https://api.ipify.org?format=json&_r={int(time.time())}")
            await asyncio.sleep(3)
            raw = await cdp.evaluate("document.body ? document.body.innerText : ''") or ""
        except Exception as e:
            self.status_update.emit(f"âŒ Browser khÃ´ng kiá»ƒm tra Ä‘Æ°á»£c proxy: {str(e)[:80]}", "red")
            return False

        try:
            data = _json.loads(raw.strip())
            browser_ip = str(data.get("ip", "")).strip()
        except Exception:
            match = re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", raw)
            browser_ip = match.group(0) if match else ""

        if not browser_ip:
            self.status_update.emit(f"âŒ Proxy check khÃ´ng tráº£ vá» IP: {raw[:80]}", "red")
            return False

        direct_ip = ""
        try:
            import urllib.request
            url = f"https://api.ipify.org?format=json&_direct={int(time.time())}"
            with urllib.request.urlopen(url, timeout=5) as resp:
                direct_data = _json.loads(resp.read().decode("utf-8", errors="ignore"))
                direct_ip = str(direct_data.get("ip", "")).strip()
        except Exception:
            direct_ip = ""

        if direct_ip and browser_ip == direct_ip:
            self.status_update.emit(
                f"âŒ Browser váº«n dÃ¹ng IP mÃ¡y tháº­t ({browser_ip}), proxy chÆ°a Ä‘Æ°á»£c Ã¡p dá»¥ng.",
                "red"
            )
            return False

        expected_is_ip = bool(re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", expected_host))
        if expected_is_ip and browser_ip != expected_host:
            self.status_update.emit(
                f"Proxy exit IP khÃ¡c gateway ({browser_ip} != {expected_host}); cÃ³ thá»ƒ bÃ¬nh thÆ°á»ng vá»›i proxy xoay/mobile.",
                "orange"
            )
            return True

        self.status_update.emit(f"âœ… Browser Ä‘ang Ä‘i qua proxy IP: {browser_ip}", "green")
        return True

    async def _is_google_traffic_challenge(self, cdp) -> bool:
        """Detect Google's unusual-traffic/CAPTCHA page so automation can stop cleanly."""
        try:
            return bool(await cdp.evaluate(r"""
            (() => {
                const text = ((document.body && document.body.innerText) || '').toLowerCase();
                const title = (document.title || '').toLowerCase();
                const url = location.href.toLowerCase();
                const hasCaptchaFrame = !!document.querySelector(
                    'iframe[src*="recaptcha"], iframe[src*="captcha"], iframe[title*="captcha" i]'
                );
                return hasCaptchaFrame ||
                    title.includes('sorry') ||
                    url.includes('/sorry/') ||
                    text.includes("i'm not a robot") ||
                    text.includes('unusual traffic') ||
                    text.includes('detected unusual traffic') ||
                    text.includes('our systems have detected');
            })()
            """))
        except Exception:
            return False

    async def _warmup_google_then_tiktok_direct(self, cdp) -> bool:
        """Open Google briefly, then navigate directly to TikTok without Google search/click."""
        try:
            self._google_warmup_blocked = False
            self.status_update.emit("ðŸŒ Má»Ÿ Google trÆ°á»›c khi vÃ o TikTok...", "blue")
            await self._type_url_in_addressbar("google.com", wait=random.uniform(2.0, 3.0), cdp=cdp)

            if await self._is_google_traffic_challenge(cdp):
                self._google_warmup_blocked = True
                self.status_update.emit("âš ï¸ Google hiá»‡n CAPTCHA/unusual traffic â€” bá» qua vÃ  vÃ o TikTok trá»±c tiáº¿p", "orange")
            else:
                await asyncio.sleep(random.uniform(0.8, 1.4))

            self.status_update.emit("âž¡ï¸ VÃ o TikTok trá»±c tiáº¿p...", "blue")
            await self._type_url_in_addressbar("tiktok.com", wait=random.uniform(6.0, 7.0), cdp=cdp)
            return True
        except Exception as e:
            self.status_update.emit(f"âš ï¸ Warm-up Googleâ†’TikTok lá»—i: {str(e)[:60]}", "orange")
            try:
                await self._type_url_in_addressbar("tiktok.com", wait=6, cdp=cdp)
                return True
            except Exception:
                return False

    async def _warmup_tiktok_via_google(self, cdp) -> bool:
        """Open Google, search TikTok, then click a TikTok result."""
        try:
            self._google_warmup_blocked = False
            self.status_update.emit("ðŸŒ Warm-up: má»Ÿ Google trÆ°á»›c khi vÃ o TikTok...", "blue")
            await self._type_url_in_addressbar("google.com", wait=random.uniform(2.5, 3.5), cdp=cdp)
            if await self._is_google_traffic_challenge(cdp):
                self._google_warmup_blocked = True
                self.status_update.emit("âŒ Google cháº·n unusual traffic/CAPTCHA ngay khi má»Ÿ", "red")
                return False

            # Handle Google consent if it appears.
            try:
                consent_pos = await cdp.evaluate(r"""
                (() => {
                    const norm = (s) => (s || '').toLowerCase().replace(/\s+/g, ' ').trim();
                    const labels = ['accept all', 'i agree', 'agree', 'cháº¥p nháº­n táº¥t cáº£', 'tÃ´i Ä‘á»“ng Ã½'];
                    for (const el of Array.from(document.querySelectorAll('button, div[role="button"], input[type="submit"]'))) {
                        const text = norm(el.innerText || el.textContent || el.value || '');
                        if (!labels.some(k => text.includes(k))) continue;
                        const r = el.getBoundingClientRect();
                        if (r.width > 40 && r.height > 20)
                            return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
                    }
                    return null;
                })()
                """)
                if consent_pos:
                    await self._human_move_and_click(
                        cdp, int(consent_pos["x"]), int(consent_pos["y"]), "Google consent"
                    )
                    await asyncio.sleep(random.uniform(1.0, 1.8))
            except Exception:
                pass

            search_pos = None
            for _ in range(8):
                search_pos = await self._get_center(cdp, 'textarea[name="q"], input[name="q"]')
                if search_pos:
                    break
                await asyncio.sleep(0.5)

            if not search_pos:
                self.status_update.emit("âš ï¸ Warm-up: khÃ´ng tháº¥y Ã´ tÃ¬m kiáº¿m Google", "orange")
                return False

            await self._human_move_and_click(cdp, *search_pos, "Click Ã´ Google Search")
            await asyncio.sleep(random.uniform(0.4, 0.8))
            await cdp.type_text("TikTok", delay=random.randint(90, 180))
            await asyncio.sleep(random.uniform(0.8, 1.2))
            if await self._is_google_traffic_challenge(cdp):
                self._google_warmup_blocked = True
                self.status_update.emit("âŒ Google cháº·n unusual traffic/CAPTCHA sau khi nháº­p tÃ¬m kiáº¿m", "red")
                return False

            suggestion_pos = None
            for _ in range(8):
                if await self._is_google_traffic_challenge(cdp):
                    self._google_warmup_blocked = True
                    self.status_update.emit("âŒ Google cháº·n unusual traffic/CAPTCHA á»Ÿ trang gá»£i Ã½", "red")
                    return False
                suggestion_pos = await cdp.evaluate(r"""
                (() => {
                    const norm = (s) => (s || '')
                        .toLowerCase()
                        .normalize('NFD')
                        .replace(/[\u0300-\u036f]/g, '')
                        .replace(/\s+/g, ' ')
                        .trim();
                    const query = 'tiktok';
                    const rows = Array.from(document.querySelectorAll(
                        'li[role="presentation"], li[role="option"], [role="option"], div[role="option"]'
                    ));
                    for (const row of rows) {
                        const text = norm(row.innerText || row.textContent || '');
                        if (!text || !text.includes(query)) continue;
                        const r = row.getBoundingClientRect();
                        if (r.width > 120 && r.height > 24 && r.top > 120 && r.bottom < window.innerHeight)
                            return {x: Math.round(r.x + Math.min(r.width * 0.22, 140)), y: Math.round(r.y + r.height / 2)};
                    }
                    return null;
                })()
                """)
                if suggestion_pos:
                    break
                await asyncio.sleep(0.4)

            if suggestion_pos:
                self.status_update.emit("ðŸ‘† Warm-up: click gá»£i Ã½ TikTok Ä‘áº§u tiÃªn...", "blue")
                await self._human_move_and_click(
                    cdp, int(suggestion_pos["x"]), int(suggestion_pos["y"]), "Click gá»£i Ã½ Google TikTok"
                )
            else:
                self.status_update.emit("âš ï¸ Warm-up: khÃ´ng tháº¥y gá»£i Ã½ Google â€” dÃ¹ng Enter", "orange")
                await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter"})
                await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter"})

            await asyncio.sleep(random.uniform(3.0, 4.5))
            if await self._is_google_traffic_challenge(cdp):
                self._google_warmup_blocked = True
                self.status_update.emit("âŒ Google cháº·n unusual traffic/CAPTCHA á»Ÿ trang káº¿t quáº£", "red")
                return False

            tiktok_pos = None
            for _ in range(8):
                if await self._is_google_traffic_challenge(cdp):
                    self._google_warmup_blocked = True
                    self.status_update.emit("âŒ Google cháº·n unusual traffic/CAPTCHA khi tÃ¬m link TikTok", "red")
                    return False
                tiktok_pos = await cdp.evaluate(r"""
                (() => {
                    const bad = ['support.tiktok.com', 'ads.tiktok.com', 'newsroom.tiktok.com',
                                 'developers.tiktok.com', 'business.tiktok.com'];
                    const links = Array.from(document.querySelectorAll('a[href]'));
                    for (const a of links) {
                        const href = a.href || '';
                        const text = (a.innerText || a.textContent || '').toLowerCase();
                        const isTikTok = href.includes('tiktok.com') || text.includes('tiktok');
                        if (!isTikTok) continue;
                        if (bad.some(b => href.includes(b))) continue;
                        const r = a.getBoundingClientRect();
                        if (r.width > 60 && r.height > 12 && r.top > 110 && r.top < window.innerHeight - 20)
                            return {
                                x: Math.round(r.x + Math.min(r.width * 0.35, 220)),
                                y: Math.round(r.y + r.height / 2),
                                top: r.top
                            };
                    }
                    return null;
                })()
                """)
                if tiktok_pos:
                    break
                await cdp.scroll(self.container_width // 2, self.container_height // 2, 0, random.randint(250, 500))
                await asyncio.sleep(0.8)

            if not tiktok_pos:
                self.status_update.emit("âš ï¸ Warm-up: khÃ´ng tÃ¬m tháº¥y link TikTok trÃªn Google", "orange")
                return False

            self.status_update.emit("ðŸ‘† Warm-up: click káº¿t quáº£ TikTok tá»« Google...", "blue")
            await self._human_move_and_click(
                cdp, int(tiktok_pos["x"]), int(tiktok_pos["y"]), "Click Google result TikTok"
            )
            await asyncio.sleep(random.uniform(6.0, 8.0))
            return True
        except Exception as e:
            self.status_update.emit(f"âš ï¸ Warm-up lá»—i: {str(e)[:60]}", "orange")
            return False

    async def _wait_for_tiktok_ready(self, cdp, timeout: float = 45) -> bool:
        """Wait until TikTok renders real UI instead of the lightweight Please wait page."""
        start = time.time()
        reload_count = 0
        chrome403_retries = 0
        while time.time() - start < timeout:
            try:
                state = await cdp.evaluate("""
                (() => {
                    const body = (document.body && document.body.innerText || '').trim();
                    const lower = body.toLowerCase();
                    const hasPleaseWait = lower === 'please wait...' || lower.includes('please wait');
                    const hasChrome403 = location.href.startsWith('chrome-error://') &&
                        (lower.includes('http error 403') || lower.includes('access to www.tiktok.com was denied'));
                    const hasLoginUi = !!document.querySelector(
                        'button[data-e2e="top-login-button"], #header-login-button, [data-e2e="login-button"]'
                    );
                    const hasAppUi = !!document.querySelector(
                        '[data-e2e], a[href*="/@"], div[class*="DivSideNav"], div[class*="tiktok"]'
                    );
                    return {
                        url: location.href,
                        body: body.slice(0, 80),
                        hasPleaseWait,
                        hasChrome403,
                        hasLoginUi,
                        hasAppUi,
                    };
                })()
                """) or {}

                if state.get("hasChrome403"):
                    chrome403_retries += 1
                    elapsed = int(time.time() - start)
                    self.status_update.emit(
                        f"âš ï¸ Chrome káº¹t lá»—i 403 â€” lÃ m má»›i cache TikTok, giá»¯ nguyÃªn cookie... ({elapsed}s, láº§n {chrome403_retries})",
                        "orange"
                    )
                    try:
                        await cdp.send("Network.clearBrowserCache")
                    except Exception:
                        pass
                    if chrome403_retries >= 2:
                        # Do not clear cookies/localStorage here. A temporary 403/Please-wait loop
                        # must not destroy a still-valid saved TikTok session.
                        for origin in ("https://www.tiktok.com", "https://tiktok.com"):
                            try:
                                await cdp.send("Storage.clearDataForOrigin", {
                                    "origin": origin,
                                    "storageTypes": "cache_storage,service_workers"
                                })
                            except Exception:
                                pass
                    try:
                        await cdp.navigate("about:blank")
                        await asyncio.sleep(1)
                        await cdp.navigate(f"https://www.tiktok.com/?_r={int(time.time())}")
                    except Exception:
                        try:
                            await cdp.send("Page.reload")
                        except Exception:
                            pass
                    await asyncio.sleep(6)
                    continue

                if state.get("hasLoginUi") or (state.get("hasAppUi") and not state.get("hasPleaseWait")):
                    return True

                if state.get("hasPleaseWait"):
                    elapsed = int(time.time() - start)
                    self.status_update.emit(f"â³ TikTok Ä‘ang Please wait... ({elapsed}s)", "orange")

                    # â”€â”€ Láº§n reload 1: á»Ÿ giÃ¢y 12 â”€â”€
                    if reload_count == 0 and elapsed >= 12:
                        reload_count = 1
                        self.status_update.emit("ðŸ”„ Reload TikTok láº§n 1 do káº¹t Please wait...", "orange")
                        try:
                            await cdp.send("Page.reload")
                        except Exception:
                            await cdp.navigate("https://www.tiktok.com/")
                        await asyncio.sleep(5)
                        continue

                    # â”€â”€ Láº§n reload 2: á»Ÿ giÃ¢y 25 â€” re-inject stealth trÆ°á»›c khi reload â”€â”€
                    if reload_count == 1 and elapsed >= 25:
                        reload_count = 2
                        if self._should_preserve_gologin_fingerprint():
                            self.status_update.emit("ðŸ”„ Reload TikTok láº§n 2 (giu nguyen fingerprint GoLogin)...", "orange")
                        else:
                            self.status_update.emit("ðŸ”„ Reload TikTok láº§n 2 + re-inject stealth...", "orange")
                            try:
                                # Re-inject stealth trÆ°á»›c khi reload
                                await cdp.evaluate("""
                                Object.defineProperty(navigator, 'webdriver', {
                                    get: () => false, configurable: true
                                });
                                """)
                            except Exception:
                                pass
                        try:
                            await cdp.navigate("about:blank")
                            await asyncio.sleep(1)
                            await cdp.navigate(f"https://www.tiktok.com/?_r={int(time.time())}")
                        except Exception:
                            try:
                                await cdp.send("Page.reload")
                            except Exception:
                                pass
                        await asyncio.sleep(6)
                        continue
            except Exception:
                pass

            await asyncio.sleep(2)

        return False


    async def _navigate_like_human(self, cdp, url: str, wait: float = 5.0):
        """â˜… Navigate tá»›i URL giá»‘ng ngÆ°á»i tháº­t â€” KHÃ”NG dÃ¹ng Page.navigate (CDP).

        TikTok phÃ¡t hiá»‡n `Page.navigate` (CDP programmatic) vÃ  Ä‘Ã¡ session cookie.
        Giáº£i phÃ¡p: DÃ¹ng window.location.href (JS) Ä‘á»ƒ chuyá»ƒn trang â€”
        giá»‘ng há»‡t ngÆ°á»i dÃ¹ng gÃµ URL vÃ o address bar rá»“i nháº¥n Enter.
        Browser váº«n á»Ÿ tab hiá»‡n táº¡i (New Tab) â†’ chuyá»ƒn sang TikTok tá»± nhiÃªn.
        """
        full_url = f"https://www.{url}" if not url.startswith("http") else url
        now_ts = time.time()
        if self._last_nav_url == full_url and (now_ts - self._last_nav_ts) < 8:
            return

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        #  CÃCH 1: window.location.href (JS navigation)
        #  Chuyá»ƒn trang NGAY trÃªn tab hiá»‡n táº¡i â€” giá»‘ng gÃµ URL â†’ Enter
        #  Browser á»Ÿ New Tab â†’ chuyá»ƒn sang TikTok (cÃ¹ng tab)
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        try:
            # Kiá»ƒm tra trang hiá»‡n táº¡i â€” chrome://newtab khÃ´ng cho phÃ©p JS navigate
            current_url = await cdp.evaluate("window.location.href") or ""
            if current_url.startswith("chrome://") or current_url.startswith("chrome-search://"):
                # Trang New Tab Ä‘áº·c biá»‡t â†’ dÃ¹ng cdp.navigate tá»›i about:blank trÆ°á»›c
                await cdp.navigate("about:blank")
                await asyncio.sleep(0.5)

            await cdp.evaluate(f'window.location.href = "{full_url}"')
            self._last_nav_url = full_url
            self._last_nav_ts = time.time()
            await asyncio.sleep(wait)
            return
        except Exception:
            pass

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        #  CÃCH 2 (Fallback): Target.createTarget (tab má»›i)
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        try:
            current_targets = []
            try:
                result = await cdp.send("Target.getTargets")
                current_targets = [
                    t["targetId"] for t in result.get("targetInfos", [])
                    if t.get("type") == "page"
                ]
            except Exception:
                pass

            result = await cdp.send("Target.createTarget", {
                "url": full_url,
                "newWindow": False,
            })
            new_target_id = result.get("targetId", "")

            if new_target_id:
                await asyncio.sleep(2)
                for old_id in current_targets:
                    if old_id != new_target_id:
                        try:
                            await cdp.send("Target.closeTarget", {"targetId": old_id})
                        except Exception:
                            pass
                try:
                    await cdp.send("Target.activateTarget", {"targetId": new_target_id})
                except Exception:
                    pass
                self._last_nav_url = full_url
                self._last_nav_ts = time.time()
                await asyncio.sleep(wait)
                return
        except Exception:
            pass

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        #  CÃCH 3 (Last resort): cdp.navigate
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        await cdp.navigate(full_url)
        self._last_nav_url = full_url
        self._last_nav_ts = time.time()
        await asyncio.sleep(wait)


    # â”€â”€â”€ Helper: Human-like Click vá»›i cháº¥m Ä‘á» â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


    async def _persist_tiktok_cookies(self, cdp):
        """â˜… FIX 1: Biáº¿n session cookies â†’ persistent cookies (30 ngÃ y).

        TikTok set sessionid, sid_tt, sid_guard dÆ°á»›i dáº¡ng SESSION cookies
        (khÃ´ng cÃ³ expires) â†’ Chrome XÃ“A khi Ä‘Ã³ng browser.
        Fix: Ä‘á»c táº¥t cáº£ TikTok cookies â†’ ghi láº¡i vá»›i expires = 30 ngÃ y
        â†’ Chrome lÆ°u persistent vÃ o Ä‘Ä©a, khÃ´ng xÃ³a khi Ä‘Ã³ng.
        """
        try:
            import time as _time
            cookies_result = await cdp.send("Network.getAllCookies")
            cookies = cookies_result.get("cookies", [])

            tiktok_cookies = [c for c in cookies if 'tiktok' in c.get('domain', '')]
            if not tiktok_cookies:
                return
            if not self._has_valid_tiktok_auth_cookie(tiktok_cookies):
                self.status_update.emit(
                    "âš ï¸ KhÃ´ng tháº¥y cookie Ä‘Äƒng nháº­p TikTok, bá» qua persist cookie.",
                    "orange",
                )
                return

            expires_epoch = _time.time() + 30 * 24 * 3600  # 30 ngÃ y
            persisted = 0

            for cookie in tiktok_cookies:
                # Chá»‰ convert cookies chÆ°a cÃ³ expires (session cookies)
                if cookie.get('expires', 0) <= 0 or cookie.get('session', False):
                    try:
                        # Update expires in place. Do not delete first; if setCookie fails,
                        # deleting first would destroy a valid session cookie.
                        payload = {
                            "name": cookie["name"],
                            "value": cookie["value"],
                            "domain": cookie["domain"],
                            "path": cookie.get("path", "/"),
                            "secure": cookie.get("secure", True),
                            "httpOnly": cookie.get("httpOnly", False),
                            "expires": expires_epoch,
                        }
                        same_site = cookie.get("sameSite")
                        if same_site in ("Strict", "Lax", "None"):
                            payload["sameSite"] = same_site
                        await cdp.send("Network.setCookie", payload)
                        persisted += 1
                    except Exception:
                        pass

            if persisted > 0:
                self.status_update.emit(
                    f"ðŸ”’ ÄÃ£ chuyá»ƒn {persisted} session cookie â†’ persistent (30 ngÃ y)", "green"
                )
        except Exception as e:
            self.status_update.emit(f"âš ï¸ Persist cookie lá»—i: {str(e)[:40]}", "orange")

    async def _resave_cookies_to_db(self, cdp):
        """â˜… FIX 2: Äá»c cookie má»›i nháº¥t tá»« Chrome â†’ lÆ°u vÃ o DB trÆ°á»›c khi Ä‘Ã³ng browser.

        Äáº£m báº£o DB luÃ´n cÃ³ báº£n copy cookie má»›i nháº¥t Ä‘á»ƒ inject láº¡i láº§n sau.
        """
        try:
            cookies_result = await cdp.send("Network.getAllCookies")
            cookies = cookies_result.get("cookies", [])

            tiktok_cookies = [c for c in cookies if 'tiktok' in c.get('domain', '')]
            if not tiktok_cookies:
                return
            if not self._has_valid_tiktok_auth_cookie(tiktok_cookies):
                self.status_update.emit(
                    "âš ï¸ KhÃ´ng lÆ°u cookie má»›i vÃ¬ browser khÃ´ng cÃ²n cookie Ä‘Äƒng nháº­p TikTok.",
                    "orange",
                )
                return

            cookie_str = "; ".join(
                f"{c['name']}={c['value']}" for c in tiktok_cookies
            )

            if cookie_str and len(cookie_str) > 20:
                self.profile_update_signal.emit({
                    "cookie": cookie_str,
                    "refresh_token": self.profile_data.get("refresh_token", ""),
                })
                self.status_update.emit(
                    f"ðŸª ÄÃ£ lÆ°u {len(tiktok_cookies)} cookie vÃ o DB", "green"
                )
        except Exception as e:
            self.status_update.emit(f"âš ï¸ LÆ°u cookie lá»—i: {str(e)[:40]}", "orange")

    async def _save_tiktok_storage(self, cdp):
        """â˜… FIX 4 (TrÆ°á»ng há»£p A): Backup localStorage + sessionStorage â†’ file JSON.

        TikTok lÆ°u thÃ´ng tin user (webapp_user_info, cookie_consent, v.v.)
        trong localStorage. Dá»¯ liá»‡u nÃ y giÃºp TikTok nháº­n diá»‡n phiÃªn Ä‘Äƒng nháº­p
        mÃ  khÃ´ng cáº§n dá»±a 100% vÃ o cookies.
        Backup dá»¯ liá»‡u nÃ y vÃ o file JSON trong profile dir â†’ restore láº§n sau.
        """
        try:
            storage_data = await cdp.evaluate("""
            (() => {
                const result = {localStorage: {}, sessionStorage: {}};
                try {
                    for (let i = 0; i < localStorage.length; i++) {
                        const key = localStorage.key(i);
                        result.localStorage[key] = localStorage.getItem(key);
                    }
                } catch(e) {}
                try {
                    for (let i = 0; i < sessionStorage.length; i++) {
                        const key = sessionStorage.key(i);
                        result.sessionStorage[key] = sessionStorage.getItem(key);
                    }
                } catch(e) {}
                return result;
            })()
            """)

            if storage_data and (storage_data.get('localStorage') or storage_data.get('sessionStorage')):
                storage_file = os.path.join(self._profile_dir, "tiktok_storage.json")
                import json
                with open(storage_file, "w", encoding="utf-8") as f:
                    json.dump(storage_data, f, ensure_ascii=False)

                ls_count = len(storage_data.get('localStorage', {}))
                ss_count = len(storage_data.get('sessionStorage', {}))
                self.status_update.emit(
                    f"ðŸ’¾ ÄÃ£ backup Storage (LS:{ls_count} + SS:{ss_count})", "green"
                )
        except Exception as e:
            self.status_update.emit(f"âš ï¸ Backup storage lá»—i: {str(e)[:40]}", "orange")

    async def _restore_tiktok_storage(self, cdp):
        """â˜… FIX 4 (TrÆ°á»ng há»£p A): Restore localStorage + sessionStorage tá»« file JSON.

        Pháº£i gá»i SAU KHI navigate tá»›i tiktok.com (vÃ¬ localStorage phá»¥ thuá»™c origin).
        """
        storage_file = os.path.join(self._profile_dir, "tiktok_storage.json")
        if not os.path.exists(storage_file):
            return

        try:
            import json
            with open(storage_file, "r", encoding="utf-8") as f:
                storage_data = json.load(f)

            ls_data = storage_data.get('localStorage', {})
            ss_data = storage_data.get('sessionStorage', {})

            if not ls_data and not ss_data:
                return

            # Inject localStorage
            if ls_data:
                # Escape JSON string cho JS
                ls_json = json.dumps(ls_data, ensure_ascii=False)
                await cdp.evaluate(f"""
                (() => {{
                    try {{
                        const data = {ls_json};
                        for (const [key, value] of Object.entries(data)) {{
                            localStorage.setItem(key, value);
                        }}
                    }} catch(e) {{}}
                }})()
                """)

            # Inject sessionStorage
            if ss_data:
                ss_json = json.dumps(ss_data, ensure_ascii=False)
                await cdp.evaluate(f"""
                (() => {{
                    try {{
                        const data = {ss_json};
                        for (const [key, value] of Object.entries(data)) {{
                            sessionStorage.setItem(key, value);
                        }}
                    }} catch(e) {{}}
                }})()
                """)

            self.status_update.emit(
                f"ðŸ’¾ ÄÃ£ restore Storage (LS:{len(ls_data)} + SS:{len(ss_data)})", "green"
            )
        except Exception as e:
            self.status_update.emit(f"âš ï¸ Restore storage lá»—i: {str(e)[:40]}", "orange")

    async def _ensure_cursor_dot(self, cdp):
        """Táº¡o cursor dot SVG 1 láº§n duy nháº¥t (náº¿u chÆ°a cÃ³). DÃ¹ng transform Ä‘á»ƒ update vá»‹ trÃ­."""
        await cdp.evaluate("""
        (() => {
            if (document.getElementById('__cursor_dot__')) return;
            const d = document.createElement('div');
            d.id = '__cursor_dot__';
            d.innerHTML = `<svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M5 3L19 12L12 13L9 20L5 3Z" fill="white" stroke="black" stroke-width="1.5" stroke-linejoin="round"/>
            </svg>`;
            d.style.cssText = `
                position: fixed; z-index: 999999; pointer-events: none;
                left: 0; top: 0; width: 24px; height: 24px;
                filter: drop-shadow(1px 2px 2px rgba(0,0,0,0.3));
                will-change: transform;
                transform: translate(-100px, -100px);
            `;
            document.body.appendChild(d);
        })()
        """)

    async def _move_cursor_dot(self, cdp, x, y):
        """Update vá»‹ trÃ­ cursor báº±ng transform (GPU accelerated, khÃ´ng layout reflow)."""
        await cdp.evaluate(f"""
        (() => {{
            const d = document.getElementById('__cursor_dot__');
            if (d) d.style.transform = 'translate({x}px, {y}px)';
        }})()
        """)

    async def _show_cursor_dot(self, cdp, x, y):
        """Compat wrapper â€” Ä‘áº£m báº£o cursor tá»“n táº¡i rá»“i move."""
        await self._ensure_cursor_dot(cdp)
        await self._move_cursor_dot(cdp, x, y)

    async def _smooth_mouse_drift(self, cdp, tx, ty, steps=None):
        """Di chuyá»ƒn chuá»™t mÆ°á»£t tá»« vá»‹ trÃ­ hiá»‡n táº¡i â†’ (tx, ty) báº±ng micro-bezier."""
        await self._ensure_cursor_dot(cdp)
        cx = getattr(self, '_mouse_x', self.container_width // 2)
        cy = getattr(self, '_mouse_y', self.container_height // 2)
        if steps is None:
            steps = random.randint(8, 14)
        for i in range(steps + 1):
            t = i / steps
            t = t * t * (3.0 - 2.0 * t)  # smoothstep
            mx = int(cx + (tx - cx) * t)
            my = int(cy + (ty - cy) * t)
            await cdp.send("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": mx, "y": my,
            })
            await self._move_cursor_dot(cdp, mx, my)
            await asyncio.sleep(random.uniform(0.025, 0.05))
        self._mouse_x = tx
        self._mouse_y = ty

    async def _human_move_and_click(self, cdp, x, y, label=""):
        """Di chuyá»ƒn chuá»™t theo cubic bezier + smoothstep rá»“i click â€” giá»‘ng ngÆ°á»i tháº­t."""
        if label:
            self.status_update.emit(f"ðŸ–±ï¸ {label}", "blue")

        await self._ensure_cursor_dot(cdp)

        # Láº¥y vá»‹ trÃ­ chuá»™t hiá»‡n táº¡i (gáº§n giá»¯a mÃ n hÃ¬nh náº¿u chÆ°a cÃ³)
        cur_x = getattr(self, '_mouse_x', self.container_width // 2)
        cur_y = getattr(self, '_mouse_y', self.container_height // 2)

        # Khoáº£ng cÃ¡ch di chuyá»ƒn â†’ jitter tá»· lá»‡ theo khoáº£ng cÃ¡ch
        dist = max(1, ((x - cur_x)**2 + (y - cur_y)**2) ** 0.5)
        jitter_scale = min(1.0, dist / 300)  # Khoáº£ng cÃ¡ch ngáº¯n â†’ jitter Ã­t

        # 2 Ä‘iá»ƒm Ä‘iá»u khiá»ƒn cubic bezier (jitter nhá» hÆ¡n, tá»± nhiÃªn hÆ¡n)
        jx = int(30 * jitter_scale)
        jy = int(15 * jitter_scale)
        ctrl1_x = cur_x + (x - cur_x) * random.uniform(0.2, 0.4) + random.randint(-jx, jx)
        ctrl1_y = cur_y + (y - cur_y) * random.uniform(0.2, 0.4) + random.randint(-jy, jy)
        ctrl2_x = cur_x + (x - cur_x) * random.uniform(0.6, 0.8) + random.randint(-jx//2, jx//2)
        ctrl2_y = cur_y + (y - cur_y) * random.uniform(0.6, 0.8) + random.randint(-jy//2, jy//2)

        # Nhiá»u bÆ°á»›c hÆ¡n â†’ mÆ°á»£t hÆ¡n
        steps = random.randint(18, 30)
        for i in range(steps + 1):
            t = i / steps
            # â˜… Smoothstep easing: cháº­m Ä‘áº§u â†’ nhanh giá»¯a â†’ cháº­m cuá»‘i
            t_ease = t * t * (3.0 - 2.0 * t)

            # Cubic bezier (4 Ä‘iá»ƒm)
            bx = int((1-t_ease)**3 * cur_x + 3*(1-t_ease)**2*t_ease * ctrl1_x +
                     3*(1-t_ease)*t_ease**2 * ctrl2_x + t_ease**3 * x)
            by = int((1-t_ease)**3 * cur_y + 3*(1-t_ease)**2*t_ease * ctrl1_y +
                     3*(1-t_ease)*t_ease**2 * ctrl2_y + t_ease**3 * y)
            await cdp.send("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": bx, "y": by,
            })
            await self._move_cursor_dot(cdp, bx, by)

            # â˜… Delay phi tuyáº¿n: cháº­m á»Ÿ Ä‘áº§u/cuá»‘i, nhanh á»Ÿ giá»¯a
            if t < 0.15 or t > 0.85:
                await asyncio.sleep(random.uniform(0.018, 0.035))
            else:
                await asyncio.sleep(random.uniform(0.006, 0.015))

        # LÆ°u vá»‹ trÃ­ cuá»‘i â€” dÃ¹ng tá»a Ä‘á»™ gá»‘c (khÃ´ng jitter)
        self._mouse_x = x
        self._mouse_y = y

        # Dá»«ng nhá» trÆ°á»›c khi click (giá»‘ng ngÆ°á»i suy nghÄ©)
        await asyncio.sleep(random.uniform(0.08, 0.18))

        # Click â€” chÃ­nh xÃ¡c vÃ o tÃ¢m element
        for event_type in ["mousePressed", "mouseReleased"]:
            await cdp.send("Input.dispatchMouseEvent", {
                "type": event_type, "x": x, "y": y,
                "button": "left", "clickCount": 1,
            })
            await asyncio.sleep(random.uniform(0.04, 0.08))

    async def _clear_active_text_input(self, cdp) -> bool:
        """Clear the currently focused input without submitting the form."""
        try:
            await cdp.send("Input.dispatchKeyEvent", {
                "type": "keyDown",
                "key": "Control",
                "code": "ControlLeft",
                "windowsVirtualKeyCode": 17,
                "modifiers": 2,
            })
            await cdp.send("Input.dispatchKeyEvent", {
                "type": "keyDown",
                "key": "a",
                "code": "KeyA",
                "windowsVirtualKeyCode": 65,
                "modifiers": 2,
            })
            await cdp.send("Input.dispatchKeyEvent", {
                "type": "keyUp",
                "key": "a",
                "code": "KeyA",
                "windowsVirtualKeyCode": 65,
                "modifiers": 2,
            })
            await cdp.send("Input.dispatchKeyEvent", {
                "type": "keyUp",
                "key": "Control",
                "code": "ControlLeft",
                "windowsVirtualKeyCode": 17,
            })
            await asyncio.sleep(0.08)
            for event_type in ("keyDown", "keyUp"):
                await cdp.send("Input.dispatchKeyEvent", {
                    "type": event_type,
                    "key": "Backspace",
                    "code": "Backspace",
                    "windowsVirtualKeyCode": 8,
                })
            await asyncio.sleep(0.1)
            return True
        except Exception:
            return False

    async def _active_input_value(self, cdp):
        try:
            value = await cdp.evaluate("""
            (() => {
                const el = document.activeElement;
                if (!el || !('value' in el)) return null;
                return String(el.value || '');
            })()
            """)
            return value if isinstance(value, str) else None
        except Exception:
            return None

    async def _set_active_input_value_js(self, cdp, value: str) -> bool:
        value_js = _json.dumps(value or "")
        try:
            return bool(await cdp.evaluate(f"""
            (() => {{
                const el = document.activeElement;
                if (!el || !('value' in el)) return false;
                const proto = Object.getPrototypeOf(el);
                const desc = Object.getOwnPropertyDescriptor(proto, 'value');
                if (desc && desc.set) desc.set.call(el, {value_js});
                else el.value = {value_js};
                const inputEvent = (typeof InputEvent === 'function')
                    ? new InputEvent('input', {{bubbles: true, inputType: 'insertText', data: ''}})
                    : new Event('input', {{bubbles: true}});
                el.dispatchEvent(inputEvent);
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
                return true;
            }})()
            """))
        except Exception:
            return False

    async def _type_active_input_exact(self, cdp, value: str, label: str, secret: bool = False) -> bool:
        """Enter text into the focused input, then verify before the login submit."""
        expected = value or ""
        await self._clear_active_text_input(cdp)
        await asyncio.sleep(random.uniform(0.12, 0.24))

        try:
            # Input.insertText is reliable for shifted/special characters in saved passwords.
            for ch in expected:
                await cdp.send("Input.insertText", {"text": ch})
                await asyncio.sleep(random.uniform(0.035, 0.085))
        except Exception:
            await cdp.type_text(expected, delay=random.randint(70, 130))

        await asyncio.sleep(random.uniform(0.18, 0.32))
        actual = await self._active_input_value(cdp)
        if actual != expected and not self._should_preserve_gologin_fingerprint():
            # Fallback for IME/keyboard-layout edge cases. Dispatch events so React sees the change.
            await self._set_active_input_value_js(cdp, expected)
            await asyncio.sleep(0.2)
            actual = await self._active_input_value(cdp)

        if actual == expected:
            if secret:
                self.status_update.emit(f"Da nhap {label} (len={len(expected)})", "green")
            else:
                preview = expected[:3] + "***" if len(expected) > 3 else "***"
                self.status_update.emit(f"Da nhap {label}: {preview}", "green")
            return True

        actual_len = len(actual) if isinstance(actual, str) else -1
        self.status_update.emit(
            f"Khong nhap dung {label} (expected len={len(expected)}, actual len={actual_len})",
            "red"
        )
        return False

    async def _verify_login_form_values(self, cdp, username: str, password: str) -> dict:
        username_js = _json.dumps(username or "")
        password_js = _json.dumps(password or "")
        default = {
            "username_ok": False,
            "password_ok": False,
            "username_len": -1,
            "password_len": -1,
        }
        try:
            result = await cdp.evaluate(f"""
            (() => {{
                const visible = (el) => {{
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0;
                }};
                const reject = (input) => {{
                    const text = [
                        input.name, input.id, input.type, input.autocomplete,
                        input.placeholder, input.getAttribute('aria-label'),
                        input.parentElement ? input.parentElement.innerText : ''
                    ].join(' ').toLowerCase();
                    return text.includes('code') || text.includes('otp') ||
                           text.includes('phone') || text.includes('so dien thoai');
                }};
                const expectedUser = {username_js};
                const expectedPassword = {password_js};
                const inputs = Array.from(document.querySelectorAll('input')).filter(visible);
                const passwordInput = inputs.find(input => (input.type || '').toLowerCase() === 'password');
                const userInput = inputs.find(input =>
                    (input.type || '').toLowerCase() !== 'password' && !reject(input)
                );
                const userValue = userInput ? String(userInput.value || '') : '';
                const passwordValue = passwordInput ? String(passwordInput.value || '') : '';
                return {{
                    username_ok: userValue === expectedUser,
                    password_ok: passwordValue === expectedPassword,
                    username_len: userValue.length,
                    password_len: passwordValue.length,
                }};
            }})()
            """)
            if isinstance(result, dict):
                return {**default, **result}
        except Exception:
            pass
        return default

    async def _get_center(self, cdp, selector):
        """Láº¥y 1 Ä‘iá»ƒm click an toÃ n bÃªn trong element theo selector."""
        selector_js = _json.dumps(selector or "")
        pos = await cdp.evaluate(f"""
        (() => {{
            const selector = {selector_js};
            const el = document.querySelector(selector);
            if (!el) return null;
            try {{ el.scrollIntoView({{block: 'center', inline: 'center'}}); }} catch (e) {{}}
            const r = el.getBoundingClientRect();
            const left = Math.max(0, r.left);
            const top = Math.max(0, r.top);
            const right = Math.min(window.innerWidth, r.right);
            const bottom = Math.min(window.innerHeight, r.bottom);
            const width = Math.max(0, right - left);
            const height = Math.max(0, bottom - top);
            if (width < 2 || height < 2) return null;

            const padX = Math.max(2, Math.min(Math.round(width * 0.18), Math.floor(width / 3)));
            const padY = Math.max(2, Math.min(Math.round(height * 0.18), Math.floor(height / 3)));
            const safeLeft = left + padX < right - padX ? left + padX : left;
            const safeRight = left + padX < right - padX ? right - padX : right;
            const safeTop = top + padY < bottom - padY ? top + padY : top;
            const safeBottom = top + padY < bottom - padY ? bottom - padY : bottom;
            const xs = [0.5, 0.42, 0.58, 0.35, 0.65];
            const ys = [0.5, 0.42, 0.58, 0.32, 0.68];
            const matchesTarget = (node) => !!node && (node === el || el.contains(node) || node.contains(el));

            for (const py of ys) {{
                for (const px of xs) {{
                    const x = Math.round(safeLeft + (safeRight - safeLeft) * px);
                    const y = Math.round(safeTop + (safeBottom - safeTop) * py);
                    const topEl = document.elementFromPoint(x, y);
                    if (matchesTarget(topEl)) return {{x, y}};
                }}
            }}

            return {{
                x: Math.round(left + width / 2),
                y: Math.round(top + height / 2)
            }};
        }})()
        """)
        if pos:
            return int(pos["x"]), int(pos["y"])
        return None

    async def _get_center_by_text(self, cdp, text):
        """TÃ¬m element chá»©a text cá»¥ thá»ƒ â†’ tráº£ vá» Ä‘iá»ƒm click an toÃ n."""
        text_js = _json.dumps(text or "")
        pos = await cdp.evaluate(f"""
        (() => {{
            const wanted = {text_js};
            const all = Array.from(document.querySelectorAll('*'));
            const pickPoint = (el) => {{
                const r = el.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0 || r.width >= 500 || r.height >= 150) return null;
                const left = Math.max(0, r.left);
                const top = Math.max(0, r.top);
                const right = Math.min(window.innerWidth, r.right);
                const bottom = Math.min(window.innerHeight, r.bottom);
                const width = Math.max(0, right - left);
                const height = Math.max(0, bottom - top);
                if (width < 2 || height < 2) return null;
                const xs = [0.5, 0.42, 0.58];
                const ys = [0.5, 0.35, 0.65];
                for (const py of ys) {{
                    for (const px of xs) {{
                        const x = Math.round(left + width * px);
                        const y = Math.round(top + height * py);
                        const topEl = document.elementFromPoint(x, y);
                        if (topEl && (topEl === el || el.contains(topEl) || topEl.contains(el))) {{
                            return {{x, y}};
                        }}
                    }}
                }}
                return {{x: Math.round(left + width / 2), y: Math.round(top + height / 2)}};
            }};

            for (const el of all) {{
                const tag = el.tagName.toLowerCase();
                if ((tag === 'div' || tag === 'main' || tag === 'body') && el.childElementCount > 2) continue;
                const txt = (el.innerText || '').trim();
                if (txt && txt === wanted) {{
                    const point = pickPoint(el);
                    if (point) return point;
                }}
            }}

            for (const el of all) {{
                const tag = el.tagName.toLowerCase();
                if ((tag === 'div' || tag === 'main' || tag === 'body') && el.childElementCount > 2) continue;
                const txt = el.innerText || '';
                if (txt && txt.includes(wanted)) {{
                    const point = pickPoint(el);
                    if (point) return point;
                }}
            }}
            return null;
        }})()
        """)
        if pos:
            return int(pos["x"]), int(pos["y"])
        return None

    async def _get_center_by_texts(self, cdp, texts):
        """TÃ¬m theo nhiá»u text (Æ°u tiÃªn theo thá»© tá»±)."""
        for text in texts:
            pos = await self._get_center_by_text(cdp, text)
            if pos:
                return pos
        return None

    async def _get_login_method_option_center(self, cdp):
        """TÃ¬m Ã´ 'Use phone/email/username' theo cáº£ EN/VI, chá»‹u Ä‘Æ°á»£c xuá»‘ng dÃ²ng."""
        pos = await cdp.evaluate(r"""
        (() => {
            const norm = (s) => (s || '')
                .toLowerCase()
                .normalize('NFD')
                .replace(/[\u0300-\u036f]/g, '')
                .replace(/\s+/g, ' ')
                .trim();

            const isVisible = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                return r.width > 40 && r.height > 16;
            };

            const keys = [
                'use phone / email / username',
                'use phone/email/username',
                'use phone or email',
                'use phone/email',
                'continue with phone or email',
                'su dung so dien thoai/email/ten nguoi dung',
                'su dung so dien thoai email ten nguoi dung',
                'su dung so dien thoai hoac email',
                'su dung dien thoai / email / ten nguoi dung',
                'su dung dien thoai email ten nguoi dung',
                'su dung dien thoai hoac email',
                'tiep tuc bang dien thoai hoac email',
                'tiep tuc voi so dien thoai hoac email',
                'dien thoai/email/ten nguoi dung',
                'dien thoai email ten nguoi dung',
                'so dien thoai hoac email',
            ];

            const isTargetText = (text) => {
                const t = norm(text);
                if (!t) return false;
                const compact = t.replace(/\s*\/\s*/g, '/');
                const noSlash = t.replace(/\//g, ' ');
                return keys.some(k => t.includes(k) || compact.includes(k) || noSlash.includes(k));
            };

            const bestClickableAncestor = (start) => {
                let node = start;
                for (let depth = 0; node && depth < 7; depth++, node = node.parentElement) {
                    if (!isVisible(node)) continue;
                    const r = node.getBoundingClientRect();
                    const tag = node.tagName.toLowerCase();
                    const role = (node.getAttribute('role') || '').toLowerCase();
                    const cls = node.className ? String(node.className) : '';
                    const buttonLike =
                        tag === 'button' || tag === 'a' || role === 'button' ||
                        cls.includes('channel') || cls.includes('login') ||
                        (r.width >= 180 && r.height >= 36 && r.height <= 90);
                    if (buttonLike) return node;
                }
                return start;
            };

            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            let textNode;
            while ((textNode = walker.nextNode())) {
                if (!isTargetText(textNode.nodeValue)) continue;
                const source = textNode.parentElement;
                const target = bestClickableAncestor(source);
                if (!isVisible(target)) continue;
                const r = target.getBoundingClientRect();
                return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
            }

            const candidates = Array.from(document.querySelectorAll('*'));
            for (const el of candidates) {
                if (!isVisible(el)) continue;
                const txt = el.innerText || el.textContent || '';
                if (!isTargetText(txt)) continue;
                const target = bestClickableAncestor(el);
                const r = target.getBoundingClientRect();
                return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
            }

            // Structural fallback: TikTok sometimes wraps/splits localized text.
            // If the login modal is visible, choose the second login channel row
            // (the row below QR), while excluding social-login rows.
            const reject = (text) => {
                const t = norm(text);
                return t.includes('qr') || t.includes('facebook') || t.includes('google') ||
                       t.includes('line') || t.includes('kakao') || t.includes('apple');
            };
            const rows = Array.from(document.querySelectorAll('div, button, a'))
                .filter(el => {
                    if (!isVisible(el)) return false;
                    const r = el.getBoundingClientRect();
                    if (r.width < 260 || r.width > 520 || r.height < 34 || r.height > 72) return false;
                    if (r.top < 120 || r.left < 220) return false;
                    const text = el.innerText || el.textContent || '';
                    if (norm(text).length < 3) return false;
                    return !reject(text);
                })
                .map(el => ({el, r: el.getBoundingClientRect(), text: el.innerText || el.textContent || ''}))
                .sort((a, b) => a.r.top - b.r.top);

            for (const row of rows) {
                if (isTargetText(row.text)) {
                    return {x: Math.round(row.r.x + row.r.width / 2), y: Math.round(row.r.y + row.r.height / 2)};
                }
            }
            if (rows.length >= 2) {
                const row = rows[1];
                return {x: Math.round(row.r.x + row.r.width / 2), y: Math.round(row.r.y + row.r.height / 2)};
            }
            return null;
        })()
        """)
        if pos:
            return int(pos["x"]), int(pos["y"])
        return None

    async def _click_login_method_option_js(self, cdp):
        """Click trá»±c tiáº¿p Ä‘Ãºng Ã´ phone/email/username, trÃ¡nh báº¯t nháº§m QR option."""
        try:
            return bool(await cdp.evaluate(r"""
            (() => {
                const norm = (s) => (s || '')
                    .toLowerCase()
                    .normalize('NFD')
                    .replace(/[\u0300-\u036f]/g, '')
                    .replace(/\s+/g, ' ')
                    .trim();

                const keys = [
                    'use phone / email / username',
                    'use phone/email/username',
                    'use phone or email',
                    'use phone/email',
                    'continue with phone or email',
                    'su dung so dien thoai/email/ten nguoi dung',
                    'su dung so dien thoai email ten nguoi dung',
                    'su dung so dien thoai hoac email',
                    'su dung dien thoai / email / ten nguoi dung',
                    'su dung dien thoai email ten nguoi dung',
                    'su dung dien thoai hoac email',
                    'tiep tuc bang dien thoai hoac email',
                    'tiep tuc voi so dien thoai hoac email',
                    'dien thoai/email/ten nguoi dung',
                    'dien thoai email ten nguoi dung',
                    'so dien thoai hoac email',
                ];

                const isTargetText = (text) => {
                    const t = norm(text);
                    if (!t) return false;
                    const compact = t.replace(/\s*\/\s*/g, '/');
                    const noSlash = t.replace(/\//g, ' ');
                    return keys.some(k => t.includes(k) || compact.includes(k) || noSlash.includes(k));
                };

                const isVisible = (el) => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    return r.width > 40 && r.height > 16;
                };

                const bestClickableAncestor = (start) => {
                    let node = start;
                    for (let depth = 0; node && depth < 7; depth++, node = node.parentElement) {
                        if (!isVisible(node)) continue;
                        const r = node.getBoundingClientRect();
                        const tag = node.tagName.toLowerCase();
                        const role = (node.getAttribute('role') || '').toLowerCase();
                        const cls = node.className ? String(node.className) : '';
                        const buttonLike =
                            tag === 'button' || tag === 'a' || role === 'button' ||
                            cls.includes('channel') || cls.includes('login') ||
                            (r.width >= 180 && r.height >= 36 && r.height <= 90);
                        if (buttonLike) return node;
                    }
                    return start;
                };

                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                let textNode;
                while ((textNode = walker.nextNode())) {
                    if (!isTargetText(textNode.nodeValue)) continue;
                    const target = bestClickableAncestor(textNode.parentElement);
                    if (!isVisible(target)) continue;
                    target.scrollIntoView({block: 'center', inline: 'center'});
                    target.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
                    target.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                    target.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                    target.click();
                    return true;
                }

                const reject = (text) => {
                    const t = norm(text);
                    return t.includes('qr') || t.includes('facebook') || t.includes('google') ||
                           t.includes('line') || t.includes('kakao') || t.includes('apple');
                };
                const rows = Array.from(document.querySelectorAll('div, button, a'))
                    .filter(el => {
                        if (!isVisible(el)) return false;
                        const r = el.getBoundingClientRect();
                        if (r.width < 260 || r.width > 520 || r.height < 34 || r.height > 72) return false;
                        if (r.top < 120 || r.left < 220) return false;
                        const text = el.innerText || el.textContent || '';
                        if (norm(text).length < 3) return false;
                        return !reject(text);
                    })
                    .map(el => ({el, r: el.getBoundingClientRect(), text: el.innerText || el.textContent || ''}))
                    .sort((a, b) => a.r.top - b.r.top);

                const matched = rows.find(row => isTargetText(row.text));
                const target = matched ? matched.el : (rows.length >= 2 ? rows[1].el : null);
                if (target) {
                    target.scrollIntoView({block: 'center', inline: 'center'});
                    target.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
                    target.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                    target.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                    target.click();
                    return true;
                }
                return false;
            })()
            """))
        except Exception:
            return False

    async def _get_verify_email_option_center(self, cdp):
        """Find the Email row in TikTok identity verification modal."""
        pos = await cdp.evaluate(r"""
        (() => {
            const norm = (s) => (s || '')
                .toLowerCase()
                .normalize('NFD')
                .replace(/[\u0300-\u036f]/g, '')
                .replace(/\s+/g, ' ')
                .trim();

            const isVisible = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                return r.width > 40 && r.height > 20;
            };

            const bestClickableAncestor = (start) => {
                let node = start;
                for (let depth = 0; node && depth < 7; depth++, node = node.parentElement) {
                    if (!isVisible(node)) continue;
                    const r = node.getBoundingClientRect();
                    const tag = node.tagName.toLowerCase();
                    const role = (node.getAttribute('role') || '').toLowerCase();
                    const text = norm(node.innerText || node.textContent || '');
                    if (
                        tag === 'button' || tag === 'a' || role === 'button' ||
                        (r.width >= 260 && r.width <= 620 && r.height >= 44 && r.height <= 110 && text.includes('email'))
                    ) {
                        return node;
                    }
                }
                return null;
            };

            const nodes = Array.from(document.querySelectorAll('div, button, a, span, p'))
                .map(el => ({el, r: el.getBoundingClientRect()}))
                .sort((a, b) => (a.r.width * a.r.height) - (b.r.width * b.r.height))
                .map(item => item.el);
            for (const el of nodes) {
                if (!isVisible(el)) continue;
                const text = norm(el.innerText || el.textContent || '');
                if (!text.includes('email')) continue;
                if (text.includes('feedback') || text.includes('help')) continue;
                const target = bestClickableAncestor(el);
                if (!isVisible(target)) continue;
                const r = target.getBoundingClientRect();
                if (r.width < 260 || r.width > 620 || r.height < 44 || r.height > 110) continue;
                return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
            }

            // Last fallback for the current TikTok verify layout: choose the visible
            // method row below the verification copy, then click near its right side.
            const rows = Array.from(document.querySelectorAll('div, button, a'))
                .filter(el => {
                    if (!isVisible(el)) return false;
                    const r = el.getBoundingClientRect();
                    const text = norm(el.innerText || el.textContent || '');
                    return r.width >= 360 && r.width <= 560 &&
                           r.height >= 58 && r.height <= 96 &&
                           text.includes('email') &&
                           !text.includes('feedback') &&
                           !text.includes('help');
                })
                .map(el => ({el, r: el.getBoundingClientRect()}))
                .sort((a, b) => a.r.top - b.r.top);
            if (rows.length) {
                const r = rows[0].r;
                return {x: Math.round(r.right - 28), y: Math.round(r.y + r.height / 2)};
            }
            return null;
        })()
        """)
        if pos:
            return int(pos["x"]), int(pos["y"])
        return None

    async def _click_verify_continue_if_present(self, cdp):
        """Click a Continue/Send code button after choosing verify email, if TikTok shows one."""
        try:
            return bool(await cdp.evaluate(r"""
            (() => {
                const norm = (s) => (s || '')
                    .toLowerCase()
                    .normalize('NFD')
                    .replace(/[\u0300-\u036f]/g, '')
                    .replace(/\s+/g, ' ')
                    .trim();
                const keys = ['continue', 'tiep tuc', 'next', 'tiep'];
                const isVisible = (el) => {
                    const r = el.getBoundingClientRect();
                    return r.width > 40 && r.height > 20;
                };
                for (const el of Array.from(document.querySelectorAll('button, div[role="button"], a'))) {
                    if (!isVisible(el)) continue;
                    const text = norm(el.innerText || el.textContent || '');
                    if (text.includes('resend') || text.includes('gui lai ma')) continue;
                    if (!keys.some(k => text === k || text.includes(k))) continue;
                    el.scrollIntoView({block: 'center', inline: 'center'});
                    el.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
                    el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                    el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                    el.click();
                    return true;
                }
                return false;
            })()
            """))
        except Exception:
            return False

    async def _click_mail_code_send_button_if_present(self, cdp):
        """Click nÃºt gá»­i mÃ£ email náº¿u TikTok yÃªu cáº§u báº¥m trÆ°á»›c khi mail OTP Ä‘Æ°á»£c gá»­i."""
        try:
            return bool(await cdp.evaluate(r"""
            (() => {
                const norm = (s) => (s || '')
                    .toLowerCase()
                    .normalize('NFD')
                    .replace(/[\u0300-\u036f]/g, '')
                    .replace(/\s+/g, ' ')
                    .trim();

                const isVisible = (el) => {
                    const r = el.getBoundingClientRect();
                    return r.width > 60 && r.height > 24 && r.bottom > 0 && r.top < window.innerHeight;
                };
                const isDisabled = (el) => {
                    const cls = norm(el.getAttribute('class') || '');
                    return el.disabled ||
                           el.getAttribute('aria-disabled') === 'true' ||
                           cls.includes('disabled') ||
                           cls.includes('disable');
                };

                const keys = [
                    'send code', 'send a code', 'get code', 'get a code',
                    'send email', 'email code', 'verification code',
                    'gui ma', 'gui ma qua email', 'nhan ma', 'lay ma',
                    'ma xac minh', 'xac minh email'
                ];

                const candidates = [];
                for (const el of Array.from(document.querySelectorAll('button, div[role="button"], a'))) {
                    if (!isVisible(el) || isDisabled(el)) continue;
                    const text = norm(el.innerText || el.textContent || el.getAttribute('aria-label') || '');
                    if (!text) continue;
                    if (!keys.some(k => text === k || text.includes(k))) continue;
                    const r = el.getBoundingClientRect();
                    candidates.push({el, r, text});
                }

                if (!candidates.length) return false;
                candidates.sort((a, b) => {
                    const aw = a.r.width * a.r.height;
                    const bw = b.r.width * b.r.height;
                    if (Math.abs(bw - aw) > 500) return bw - aw;
                    return a.r.top - b.r.top;
                });

                const target = candidates[0].el;
                target.scrollIntoView({block: 'center', inline: 'center'});
                target.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
                target.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                target.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                target.click();
                return true;
            })()
            """))
        except Exception:
            return False

    async def _get_otp_submit_button_center(self, cdp):
        """Find the active submit button on TikTok OTP verification screen."""
        try:
            pos = await cdp.evaluate(r"""
            (() => {
                const norm = (s) => (s || '')
                    .toLowerCase()
                    .normalize('NFD')
                    .replace(/[\u0300-\u036f]/g, '')
                    .replace(/\s+/g, ' ')
                    .trim();
                const isVisible = (el) => {
                    const r = el.getBoundingClientRect();
                    return r.width > 80 && r.height > 28 && r.bottom > 0 && r.top < window.innerHeight;
                };
                const isDisabled = (el) =>
                    el.disabled ||
                    el.getAttribute('aria-disabled') === 'true' ||
                    norm(el.getAttribute('class') || '').includes('disabled');
                const labels = ['continue', 'next', 'tiep', 'tiep tuc', 'log in', 'dang nhap'];
                const candidates = [];
                for (const el of Array.from(document.querySelectorAll('button, div[role="button"], a'))) {
                    if (!isVisible(el) || isDisabled(el)) continue;
                    const text = norm(el.innerText || el.textContent || '');
                    if (!text || text.includes('resend') || text.includes('gui lai ma')) continue;
                    if (labels.some(k => text === k || text.includes(k))) {
                        const r = el.getBoundingClientRect();
                        candidates.push({
                            x: Math.round(r.x + r.width / 2),
                            y: Math.round(r.y + r.height / 2),
                            top: r.top,
                            width: r.width,
                            area: r.width * r.height,
                        });
                    }
                }
                if (!candidates.length) return null;
                candidates.sort((a, b) => {
                    if (Math.abs(b.width - a.width) > 80) return b.width - a.width;
                    return a.top - b.top;
                });
                return {x: candidates[0].x, y: candidates[0].y};
            })()
            """)
            if pos:
                return int(pos["x"]), int(pos["y"])
        except Exception:
            pass
        return None

    async def _click_otp_submit_button(self, cdp) -> bool:
        """Click OTP submit with mouse first, then JS fallback."""
        for _ in range(8):
            pos = await self._get_otp_submit_button_center(cdp)
            if pos:
                await self._human_move_and_click(cdp, *pos, "Submit OTP")
                await asyncio.sleep(1)
                return True
            await asyncio.sleep(0.5)

        try:
            return bool(await cdp.evaluate(r"""
            (() => {
                const norm = (s) => (s || '')
                    .toLowerCase()
                    .normalize('NFD')
                    .replace(/[\u0300-\u036f]/g, '')
                    .replace(/\s+/g, ' ')
                    .trim();
                const labels = ['continue', 'next', 'tiep', 'tiep tuc', 'log in', 'dang nhap'];
                for (const el of Array.from(document.querySelectorAll('button, div[role="button"], a'))) {
                    const r = el.getBoundingClientRect();
                    if (r.width < 80 || r.height < 28) continue;
                    if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
                    const text = norm(el.innerText || el.textContent || '');
                    if (text.includes('resend') || text.includes('gui lai ma')) continue;
                    if (!labels.some(k => text === k || text.includes(k))) continue;
                    el.scrollIntoView({block: 'center', inline: 'center'});
                    el.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
                    el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                    el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                    el.click();
                    return true;
                }
                return false;
            })()
            """))
        except Exception:
            return False

    async def _get_login_button_center(self, cdp):
        """Láº¥y tÃ¢m nÃºt Log in/ÄÄƒng nháº­p (Æ°u tiÃªn selector á»•n Ä‘á»‹nh)."""
        for sel in ['button[data-e2e="top-login-button"]', 'button#header-login-button']:
            pos = await self._get_center(cdp, sel)
            if pos:
                return pos

        pos = await cdp.evaluate("""
        (() => {
            const labels = ['log in', 'Ä‘Äƒng nháº­p'];
            const all = Array.from(document.querySelectorAll('button, a'));
            for (const el of all) {
                const text = (el.textContent || '').trim().toLowerCase();
                if (!labels.includes(text)) continue;
                const r = el.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) {
                    return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
                }
            }
            return null;
        })()
        """)
        if pos:
            return int(pos["x"]), int(pos["y"])
        return None

    async def _get_login_username_input_center(self, cdp):
        """Láº¥y Ã´ email/username, trÃ¡nh báº¯t nháº§m Ã´ sá»‘ Ä‘iá»‡n thoáº¡i hoáº·c mÃ£ OTP."""
        pos = await cdp.evaluate(r"""
        (() => {
            const norm = (s) => (s || '')
                .toLowerCase()
                .normalize('NFD')
                .replace(/[\u0300-\u036f]/g, '')
                .replace(/\s+/g, ' ')
                .trim();
            const visible = (el) => {
                const r = el.getBoundingClientRect();
                return r.width > 80 && r.height > 20 && r.bottom > 0 && r.right > 0;
            };
            const reject = (text) => {
                const t = norm(text);
                return t.includes('phone') || t.includes('so dien thoai') ||
                       t.includes('ma gom') || t.includes('code') || t.includes('otp') ||
                       t.includes('password') || t.includes('mat khau');
            };
            const accept = (text) => {
                const t = norm(text);
                return t.includes('email') || t.includes('username') ||
                       t.includes('ten nguoi dung') || t.includes('tai khoan');
            };

            const inputs = Array.from(document.querySelectorAll('input'))
                .filter(input => visible(input) && (input.type || '').toLowerCase() !== 'password')
                .map(input => {
                    const text = [
                        input.name, input.id, input.type, input.autocomplete,
                        input.placeholder, input.getAttribute('aria-label'),
                        input.parentElement ? input.parentElement.innerText : ''
                    ].join(' ');
                    return {input, text, r: input.getBoundingClientRect()};
                })
                .filter(item => !reject(item.text));

            let target = inputs.find(item => accept(item.text));
            if (!target) {
                target = inputs.find(item => item.r.width >= 180 && item.r.height >= 28);
            }
            if (!target) return null;
            target.input.scrollIntoView({block: 'center', inline: 'center'});
            const r = target.input.getBoundingClientRect();
            return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
        })()
        """)
        if pos:
            return int(pos["x"]), int(pos["y"])
        return None

    async def _wait_login_username_input(self, cdp, timeout=12):
        deadline = time.time() + timeout
        while time.time() < deadline:
            pos = await self._get_login_username_input_center(cdp)
            if pos:
                return pos
            await asyncio.sleep(0.5)
        return None

    async def _detect_captcha(self, cdp) -> dict:
        """Detect visible CAPTCHA/challenge UI and return structured details."""
        default = {
            "present": False,
            "type": "none",
            "message": "",
            "confidence": 0.0,
            "selector": "",
        }
        try:
            result = await cdp.evaluate(r"""
            (() => {
                const visible = (el, minW = 40, minH = 30) => {
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return r.width >= minW && r.height >= minH &&
                           r.bottom > 0 && r.right > 0 &&
                           style.visibility !== 'hidden' &&
                           style.display !== 'none' &&
                           Number(style.opacity || 1) > 0;
                };
                const norm = (s) => (s || '')
                    .toLowerCase()
                    .normalize('NFD')
                    .replace(/[\u0300-\u036f]/g, '')
                    .replace(/\s+/g, ' ')
                    .trim();
                const make = (type, message, confidence, selector) => ({
                    present: true,
                    type,
                    message,
                    confidence,
                    selector
                });

                const tikTokSelectors = [
                    '[id*="secsdk" i]',
                    '[class*="secsdk" i]',
                    '[id*="verify-bar" i]',
                    '[class*="verify-wrap" i]',
                    '[class*="captcha_verify" i]',
                    '[class*="captcha" i]',
                    '[id*="captcha" i]',
                    '[aria-label*="captcha" i]'
                ];
                for (const selector of tikTokSelectors) {
                    for (const el of Array.from(document.querySelectorAll(selector))) {
                        if (visible(el, 50, 40)) {
                            const text = norm(el.innerText || el.textContent || '');
                            let type = 'unknown';
                            if (selector.includes('secsdk') || selector.includes('verify') ||
                                text.includes('slider') || text.includes('puzzle') ||
                                text.includes('keo thanh truot')) {
                                type = 'tiktok_slider';
                            }
                            return make(type, text || 'Visible CAPTCHA container', 0.92, selector);
                        }
                    }
                }

                const iframeSelectors = [
                    'iframe[src*="captcha" i]',
                    'iframe[src*="challenge" i]',
                    'iframe[src*="recaptcha" i]',
                    'iframe[src*="hcaptcha" i]',
                    'iframe[title*="captcha" i]',
                    'iframe[title*="verification" i]'
                ];
                for (const selector of iframeSelectors) {
                    const frame = Array.from(document.querySelectorAll(selector)).find(el => visible(el, 50, 40));
                    if (frame) {
                        const src = frame.getAttribute('src') || '';
                        const title = frame.getAttribute('title') || '';
                        let type = 'unknown';
                        if (/recaptcha/i.test(src + title)) type = 'recaptcha';
                        else if (/hcaptcha/i.test(src + title)) type = 'hcaptcha';
                        return make(type, title || src || 'Visible CAPTCHA iframe', 0.9, selector);
                    }
                }

                const bodyText = norm(document.body ? document.body.innerText : '');
                const textSignals = [
                    ['tiktok_slider', 'drag the slider'],
                    ['tiktok_slider', 'drag the puzzle'],
                    ['tiktok_slider', 'slide to verify'],
                    ['tiktok_slider', 'keo thanh truot'],
                    ['tiktok_slider', 'ghep hinh'],
                    ['unknown', 'verify to continue'],
                    ['unknown', 'complete verification'],
                    ['unknown', 'security verification'],
                    ['unknown', 'captcha']
                ];
                for (const [type, text] of textSignals) {
                    if (bodyText.includes(text)) {
                        return make(type, text, 0.75, 'body-text');
                    }
                }

                return {present: false, type: 'none', message: '', confidence: 0, selector: ''};
            })()
            """)
            if isinstance(result, dict):
                return {**default, **result}
        except Exception:
            pass
        return default

    def _captcha_solver_config(self) -> dict:
        config = {}
        if isinstance(self.feed_settings, dict):
            feed_config = self.feed_settings.get("captcha_solver", {}) or {}
            if isinstance(feed_config, dict):
                config.update(feed_config)
        if isinstance(self.profile_data, dict):
            profile_config = self.profile_data.get("captcha_solver", {}) or {}
            if isinstance(profile_config, dict):
                config.update(profile_config)
            for key in (
                "captcha_solver_enabled",
                "captcha_solver_provider",
                "captcha_api_key",
                "captcha_solver_timeout",
            ):
                if key in self.profile_data:
                    config[key] = self.profile_data.get(key)

        enabled = bool(config.get("enabled") or config.get("captcha_solver_enabled"))
        provider = str(config.get("provider") or config.get("captcha_solver_provider") or "manual").strip().lower()
        try:
            timeout = int(config.get("timeout") or config.get("captcha_solver_timeout") or 120)
        except Exception:
            timeout = 120
        return {
            "enabled": enabled and provider not in ("", "manual", "none"),
            "provider": provider,
            "api_key": config.get("api_key") or config.get("captcha_api_key") or "",
            "timeout": timeout,
        }

    async def _try_solve_captcha_with_provider(self, cdp, captcha_info: dict, config: dict) -> dict:
        """Adapter boundary for future third-party CAPTCHA APIs."""
        provider = config.get("provider", "manual")
        if not config.get("enabled"):
            return {"status": "need_manual", "message": "manual"}

        self.status_update.emit(
            f"CAPTCHA solver '{provider}' chua duoc tich hop - cho giai thu cong.",
            "orange"
        )
        return {"status": "need_manual", "message": "provider_not_implemented"}

    def _emit_login_error(self, error_msg: str):
        error_msg = str(error_msg or "Dang nhap that bai").strip()
        self._last_login_error = error_msg
        self._last_error = error_msg
        # Login lá»—i khÃ´ng Ä‘Æ°á»£c xÃ³a cookie/tiktok_id cÅ©; Ä‘Ã³ cÃ³ thá»ƒ lÃ  phiÃªn tá»‘t Ä‘á»ƒ khÃ´i phá»¥c.
        self.profile_update_signal.emit({"login_error": error_msg})

    async def _hold_browser_for_login_recovery(self, cdp, reason: str) -> bool:
        """Keep Orbita open after auto-login fails so the user can recover manually."""
        reason = str(reason or self._last_login_error or "Dang nhap that bai").strip()
        if reason:
            self._emit_login_error(reason)
        if self._is_batch_run():
            self.status_update.emit(
                f"Auto-login loi: {reason}. Batch mode bo qua cho dang nhap tay.",
                "orange",
            )
            return False

        self.status_update.emit(
            f"Auto-login loi: {reason}. Giu browser mo de dang nhap tay.",
            "orange",
        )
        self.status_update.emit("Dang cho ban xu ly tren browser. Bam Dung de dong profile.", "orange")

        while not self._stop_flag:
            try:
                if not self._browser_alive():
                    self.status_update.emit("Browser da dong trong khi cho dang nhap tay.", "gray")
                    return False
            except Exception:
                pass

            await asyncio.sleep(3)
            try:
                if await self._check_logged_in(cdp):
                    self.status_update.emit("Phat hien dang nhap tay thanh cong.", "green")
                    await self._extract_profile_info(cdp)
                    await self._persist_tiktok_cookies(cdp)
                    return True
            except Exception:
                if not self._browser_alive():
                    self.status_update.emit("Browser da dong trong khi cho dang nhap tay.", "gray")
                    return False

        return False

    async def _hold_browser_for_action_issue(self, cdp, reason: str) -> None:
        """Keep Orbita open when an action stops before its target is completed."""
        reason = str(reason or "Chá»©c nÄƒng chÆ°a hoÃ n táº¥t").strip()
        self._last_error = reason
        if self._is_batch_run():
            self.status_update.emit(
                f"Action loi: {reason}. Batch mode dong profile de chay tiep.",
                "orange",
            )
            return
        self.status_update.emit(
            f"âš ï¸ {reason}. Giá»¯ browser má»Ÿ Ä‘á»ƒ kiá»ƒm tra, báº¥m Dá»«ng khi muá»‘n Ä‘Ã³ng.",
            "orange",
        )

        while not self._stop_flag:
            try:
                if not self._browser_alive():
                    self.status_update.emit("Browser Ä‘Ã£ Ä‘Ã³ng trong khi chá» kiá»ƒm tra lá»—i.", "gray")
                    return
            except Exception:
                pass
            await asyncio.sleep(2)

    async def _handle_captcha_gate(self, cdp, state: dict, remaining: int, poll: int = 2) -> dict:
        """Block login/OTP/error checks while a CAPTCHA is visible."""
        captcha = await self._detect_captcha(cdp)
        if not captcha.get("present"):
            if state.get("active"):
                self.status_update.emit("CAPTCHA da bien mat - tiep tuc kiem tra dang nhap.", "green")
            state.update({
                "active": False,
                "notified": False,
                "provider_attempted": False,
                "started_at": 0.0,
                "last_type": "none",
            })
            return {"blocked": False, "failed": False}

        now = time.time()
        if not state.get("active"):
            state.update({
                "active": True,
                "notified": False,
                "provider_attempted": False,
                "started_at": now,
                "last_type": captcha.get("type", "unknown"),
            })

        captcha_type = captcha.get("type", "unknown")
        if not state.get("notified"):
            self.status_update.emit(
                f"CAPTCHA detected ({captcha_type}) - tam dung login, cho xu ly...",
                "orange"
            )
            state["notified"] = True
        else:
            self.status_update.emit(
                f"Dang cho giai CAPTCHA ({captcha_type})... ({max(0, remaining)}s)",
                "orange"
            )

        config = self._captcha_solver_config()
        if config.get("enabled") and not state.get("provider_attempted"):
            state["provider_attempted"] = True
            solver_result = await self._try_solve_captcha_with_provider(cdp, captcha, config)
            status = solver_result.get("status")
            if status == "solved":
                await asyncio.sleep(2)
                verify = await self._detect_captcha(cdp)
                if not verify.get("present"):
                    self.status_update.emit("CAPTCHA solver da xu ly xong.", "green")
                    state.update({"active": False, "notified": False, "started_at": 0.0})
                    return {"blocked": False, "failed": False}
            elif status in ("failed", "timeout"):
                self.status_update.emit("CAPTCHA solver that bai - chuyen sang cho thu cong.", "orange")

        if state.get("extra_time", 0) < state.get("max_extra_time", 180):
            state["extra_time"] = min(
                state.get("max_extra_time", 180),
                state.get("extra_time", 0) + poll,
            )

        timeout = state.get("timeout", 300)
        if timeout and now - state.get("started_at", now) >= timeout:
            error_msg = f"Ket CAPTCHA qua {int(timeout)}s"
            self.status_update.emit(error_msg, "red")
            if state.get("emit_login_error", True):
                self._emit_login_error(error_msg)
            return {"blocked": True, "failed": True}

        return {"blocked": True, "failed": False}

    async def _wait_captcha_clear_for_action(self, cdp, context: str, timeout: int = 300) -> bool:
        """Pause an automation action while CAPTCHA is visible."""
        state = {
            "active": False,
            "notified": False,
            "provider_attempted": False,
            "started_at": 0.0,
            "last_type": "none",
            "extra_time": 0,
            "max_extra_time": 0,
            "timeout": timeout,
            "emit_login_error": False,
        }
        poll = 2
        deadline = time.time() + max(1, timeout)

        while not self._stop_flag:
            remaining = int(max(0, deadline - time.time()))
            gate = await self._handle_captcha_gate(cdp, state, remaining, poll=poll)
            if gate.get("failed"):
                self.status_update.emit(f"Bo qua {context} vi ket CAPTCHA.", "red")
                return False
            if not gate.get("blocked"):
                return True
            await asyncio.sleep(poll)

        return False

    async def _ensure_logged_in_for_feature(self, cdp, feature_name: str, login_ok: bool = False) -> bool:
        """Verify login before running account actions that require a TikTok session."""
        if login_ok:
            return True

        if not await self._wait_captcha_clear_for_action(cdp, f"{feature_name} pre-check"):
            return False

        self.status_update.emit(f"Kiem tra dang nhap truoc khi chay {feature_name}...", "blue")
        logged = await self._check_logged_in(cdp)
        if logged:
            self.status_update.emit(f"Da dang nhap - bat dau {feature_name}.", "green")
            return True

        self.status_update.emit(
            f"Chua dang nhap - bo qua {feature_name}. Hay tick Dang nhap hoac cap nhat cookie.",
            "orange"
        )
        return False

    # â”€â”€â”€ Login Flow â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    async def _click_tiktok_button_by_text(self, cdp, labels, timeout: float = 12) -> bool:
        """Click the first visible button/link whose text or aria-label matches one of labels."""
        labels_json = _json.dumps([str(label).lower() for label in labels])
        deadline = time.time() + max(1, timeout)
        while time.time() < deadline and not self._stop_flag:
            clicked = await cdp.evaluate(f"""
            (() => {{
                const labels = new Set({labels_json});
                const candidates = Array.from(document.querySelectorAll(
                    'button, a, [role="button"], label, div[tabindex], span[tabindex]'
                ));
                const visible = (el) => {{
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                }};
                for (const el of candidates) {{
                    if (!visible(el)) continue;
                    const txt = ((el.innerText || el.textContent || el.getAttribute('aria-label') || '') + '').trim().toLowerCase();
                    if (!txt) continue;
                    for (const label of labels) {{
                        if (txt === label || txt.includes(label)) {{
                            el.scrollIntoView({{block: 'center', inline: 'center'}});
                            el.click();
                            return true;
                        }}
                    }}
                }}
                return false;
            }})()
            """) or False
            if clicked:
                return True
            await asyncio.sleep(0.5)
        return False

    async def _find_file_input_node(self, cdp):
        try:
            doc = await cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
            root_id = doc.get("root", {}).get("nodeId")
            if not root_id:
                return None
            for selector in ('input[type="file"][accept*="image"]', 'input[type="file"]'):
                result = await cdp.send("DOM.querySelector", {
                    "nodeId": root_id,
                    "selector": selector,
                })
                node_id = result.get("nodeId", 0)
                if node_id:
                    return node_id
        except Exception:
            return None
        return None

    async def _wait_for_file_input_node(self, cdp, timeout: float = 10):
        deadline = time.time() + max(1, timeout)
        while time.time() < deadline and not self._stop_flag:
            node_id = await self._find_file_input_node(cdp)
            if node_id:
                return node_id
            await asyncio.sleep(0.5)
        return None

    async def _open_tiktok_edit_profile(self, cdp) -> bool:
        self.status_update.emit("Dang mo trang profile TikTok...", "blue")
        await cdp.navigate("https://www.tiktok.com/profile", timeout=35)
        await asyncio.sleep(4)
        await self._skip_tiktok_popup(cdp)

        opened = await cdp.evaluate("""
        (() => {
            const selectors = [
                'button[data-e2e="edit-profile-entrance"]',
                '[data-e2e="edit-profile-entrance"]',
                'button[aria-label*="Edit" i]',
                'button[aria-label*="Sá»­a" i]'
            ];
            for (const selector of selectors) {
                const el = document.querySelector(selector);
                if (el) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) {
                        el.scrollIntoView({block: 'center', inline: 'center'});
                        el.click();
                        return true;
                    }
                }
            }
            return false;
        })()
        """) or False
        if not opened:
            opened = await self._click_tiktok_button_by_text(
                cdp,
                ["Edit profile", "Sá»­a há»“ sÆ¡", "Sua ho so"],
                timeout=10,
            )
        if not opened:
            self.status_update.emit("Khong tim thay nut Edit profile.", "red")
            return False

        await asyncio.sleep(2)
        return True

    async def _prepare_avatar_file_input(self, cdp):
        node_id = await self._wait_for_file_input_node(cdp, timeout=4)
        if node_id:
            return node_id

        self.status_update.emit("Dang mo khung chon avatar...", "blue")
        clicked = await self._click_tiktok_button_by_text(
            cdp,
            [
                "Change photo", "Edit photo", "Upload photo",
                "Thay Ä‘á»•i áº£nh", "Thay doi anh", "Äá»•i áº£nh", "Doi anh",
                "Avatar", "Photo", "áº¢nh", "Anh",
            ],
            timeout=4,
        )
        if not clicked:
            clicked = await cdp.evaluate("""
            (() => {
                const modal = document.querySelector('[role="dialog"]') || document.body;
                const candidates = Array.from(modal.querySelectorAll('img, svg, button, div[role="button"], label'));
                const visible = (el) => {
                    const r = el.getBoundingClientRect();
                    return r.width > 20 && r.height > 20;
                };
                for (const el of candidates) {
                    if (!visible(el)) continue;
                    const r = el.getBoundingClientRect();
                    if (r.top < window.innerHeight * 0.75 && r.left < window.innerWidth * 0.75) {
                        el.scrollIntoView({block: 'center', inline: 'center'});
                        el.click();
                        return true;
                    }
                }
                return false;
            })()
            """) or False

        if clicked:
            await asyncio.sleep(1.5)
        return await self._wait_for_file_input_node(cdp, timeout=8)

    async def _change_tiktok_avatar(self, cdp) -> bool:
        avatar_path = os.path.abspath((self.profile_data.get("avatar_path") or "").strip())
        if not avatar_path:
            msg = "Thieu avatar_path - hay chon avatar trong Add/Edit profile hoac menu chuot phai."
            self.status_update.emit(msg, "red")
            self.profile_update_signal.emit({"avatar_status": "failed", "avatar_last_error": msg})
            return False
        if not os.path.isfile(avatar_path):
            msg = f"File avatar khong ton tai: {avatar_path}"
            self.status_update.emit(msg, "red")
            self.profile_update_signal.emit({"avatar_status": "failed", "avatar_last_error": msg})
            return False
        if os.path.splitext(avatar_path)[1].lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            msg = "Avatar chi ho tro .jpg, .jpeg, .png, .webp"
            self.status_update.emit(msg, "red")
            self.profile_update_signal.emit({"avatar_status": "failed", "avatar_last_error": msg})
            return False

        try:
            self.status_update.emit("Bat dau doi avatar TikTok...", "blue")
            if not await self._open_tiktok_edit_profile(cdp):
                self.profile_update_signal.emit({
                    "avatar_status": "failed",
                    "avatar_last_error": "Khong tim thay nut Edit profile",
                })
                return False

            node_id = await self._prepare_avatar_file_input(cdp)
            if not node_id:
                msg = "Khong tim thay input upload avatar."
                self.status_update.emit(msg, "red")
                self.profile_update_signal.emit({"avatar_status": "failed", "avatar_last_error": msg})
                return False

            self.status_update.emit("Dang upload avatar...", "blue")
            await cdp.send("DOM.setFileInputFiles", {
                "nodeId": node_id,
                "files": [avatar_path],
            })
            await asyncio.sleep(3)

            await self._click_tiktok_button_by_text(
                cdp,
                ["Apply", "Confirm", "Done", "Ãp dá»¥ng", "Ap dung", "Xong"],
                timeout=4,
            )
            await asyncio.sleep(1.5)

            self.status_update.emit("Dang luu avatar...", "blue")
            saved = await self._click_tiktok_button_by_text(cdp, ["Save", "LÆ°u", "Luu"], timeout=12)
            if not saved:
                msg = "Khong tim thay nut Save/Luu avatar."
                self.status_update.emit(msg, "red")
                self.profile_update_signal.emit({"avatar_status": "failed", "avatar_last_error": msg})
                return False

            await asyncio.sleep(5)
            error_text = await cdp.evaluate("""
            (() => {
                const markers = ['couldn\\'t update', 'failed', 'error', 'khÃ´ng thá»ƒ', 'loi', 'lá»—i'];
                const nodes = Array.from(document.querySelectorAll('[role="alert"], [data-e2e*="toast"], [class*="toast"], [class*="error"]'));
                for (const node of nodes) {
                    const r = node.getBoundingClientRect();
                    if (r.width <= 0 || r.height <= 0) continue;
                    const text = (node.innerText || node.textContent || '').toLowerCase();
                    for (const marker of markers) {
                        if (text.includes(marker)) return marker;
                    }
                }
                return '';
            })()
            """) or ""
            if error_text:
                msg = f"TikTok bao loi sau khi luu avatar: {error_text}"
                self.status_update.emit(msg, "red")
                self.profile_update_signal.emit({"avatar_status": "failed", "avatar_last_error": msg})
                return False

            updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
            self.profile_data["avatar_status"] = "success"
            self.profile_data["avatar_updated_at"] = updated_at
            self.profile_update_signal.emit({
                "avatar_path": avatar_path,
                "avatar_status": "success",
                "avatar_updated_at": updated_at,
                "avatar_last_error": "",
            })
            self.status_update.emit("Doi avatar TikTok thanh cong.", "green")
            return True
        except Exception as exc:
            msg = f"Loi doi avatar: {str(exc)[:180]}"
            self.status_update.emit(msg, "red")
            self.profile_update_signal.emit({"avatar_status": "failed", "avatar_last_error": msg})
            return False

    async def _do_login(self, cdp):
        """ÄÄƒng nháº­p TikTok â€” 3 trÆ°á»ng há»£p:
        TH0: ÄÃ£ login tá»« phiÃªn trÆ°á»›c â†’ bá» qua
        TH1: Auto login (cÃ³ credentials) â†’ bÆ°á»›c 1â†’5 â†’ chá»‰ check sau submit
        TH2: Manual login (user tá»± nháº­p) â†’ grace period â†’ polling check
        """
        cookie_str = self.profile_data.get("cookie", "")
        username   = self.profile_data.get("username", "").strip()
        password   = self.profile_data.get("password", "").strip()

        # â”€â”€ TH0: ÄÃ£ Ä‘Äƒng nháº­p tá»« phiÃªn trÆ°á»›c? â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # Chá» TikTok render Ä‘áº§y Ä‘á»§ (cáº§n 5-7s trÃªn káº¿t ná»‘i thÆ°á»ng)
        await asyncio.sleep(6)

        # â˜… Retry 3 láº§n cÃ¡ch nhau 3 giÃ¢y (tá»•ng ~15s chá»)
        already_logged = False
        for attempt in range(3):
            if await self._check_logged_in(cdp):
                already_logged = True
                break
            if attempt < 2:
                self.status_update.emit(
                    f"ðŸ” Kiá»ƒm tra Ä‘Äƒng nháº­p... (láº§n {attempt+2})", "blue"
                )
                await asyncio.sleep(3)

        if already_logged:
            # â˜… Verify láº§n cuá»‘i: chá» page render xong háº³n rá»“i check DOM
            await asyncio.sleep(2)
            final_has_login = await cdp.evaluate("""
            (() => {
                const btns = document.querySelectorAll(
                    'button[data-e2e="top-login-button"], #header-login-button'
                );
                for (const btn of btns) {
                    const r = btn.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) return true;
                }
                const all = document.querySelectorAll('button, a');
                for (const btn of all) {
                    const r = btn.getBoundingClientRect();
                    if (r.y < 80 && r.width > 0 &&
                        (btn.textContent.trim() === 'Log in' || btn.textContent.trim() === 'ÄÄƒng nháº­p'))
                        return true;
                }
                return false;
            })()
            """) or False

            if final_has_login:
                # NÃºt Log in váº«n hiá»‡n â†’ session thá»±c sá»± háº¿t háº¡n
                self.status_update.emit("âš ï¸ Session háº¿t háº¡n â€” cáº§n Ä‘Äƒng nháº­p láº¡i...", "orange")
                already_logged = False
            else:
                self.status_update.emit("ðŸ” PhÃ¡t hiá»‡n phiÃªn trÆ°á»›c â€” láº¥y thÃ´ng tin...", "blue")
                verified_id = await self._extract_profile_info(cdp, need_reload=False)
                if verified_id:
                    self.status_update.emit(f"âœ… ÄÃ£ Ä‘Äƒng nháº­p tá»« phiÃªn trÆ°á»›c! ({verified_id})", "green")
                else:
                    self.status_update.emit("âœ… ÄÃ£ Ä‘Äƒng nháº­p (Ä‘ang láº¥y há»“ sÆ¡...)", "green")
                # â˜… FIX 1: Biáº¿n session cookies â†’ persistent (30 ngÃ y)
                await self._persist_tiktok_cookies(cdp)
                return True

        # â”€â”€ ChÆ°a login â†’ Chá»‰ lÃºc nÃ y má»›i thá»­ cookie dá»± phÃ²ng tá»« tool â”€â”€
        cookie_sources = [
            ("cookie chÃ­nh trong tool", cookie_str),
            ("cookie backup trÆ°á»›c Ä‘Ã³", self.profile_data.get("cookie_backup", "")),
        ]
        tried_cookie_recovery = False
        seen_cookie_sources = set()
        for label, saved_cookie in cookie_sources:
            saved_cookie = str(saved_cookie or "").strip()
            if len(saved_cookie) <= 20 or saved_cookie in seen_cookie_sources:
                continue
            seen_cookie_sources.add(saved_cookie)
            tried_cookie_recovery = True
            if await self._try_cookie_login_from_string(cdp, saved_cookie, label):
                return True

        if tried_cookie_recovery:
            self.status_update.emit("âš ï¸ Cookie Ä‘Ã£ lÆ°u khÃ´ng dÃ¹ng Ä‘Æ°á»£c â€” chuyá»ƒn sang Ä‘Äƒng nháº­p...", "orange")

        # â”€â”€ Kiá»ƒm tra cÃ³ credentials Ä‘á»ƒ auto login khÃ´ng â”€â”€â”€â”€â”€â”€â”€â”€â”€
        has_credentials = bool(username and password)

        if not has_credentials:
            if self._is_batch_run():
                self._emit_login_error("Khong co email/password de dang nhap lai trong batch")
            # KhÃ´ng cÃ³ credentials â†’ chuyá»ƒn tháº³ng sang chá» thá»§ cÃ´ng
            self.status_update.emit("ðŸ‘¤ KhÃ´ng cÃ³ Email/Password â€” chá» Ä‘Äƒng nháº­p thá»§ cÃ´ng...", "orange")
            return await self._wait_manual_login(cdp)

        self.status_update.emit("Mo thang trang TikTok email login...", "blue")
        return await self._do_login_direct(cdp, username, password, force_direct_url=True)


        # â”€â”€ BÆ¯á»šC 1: Click nÃºt [Log in] trÃªn trang hiá»‡n táº¡i â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # Trang Ä‘Ã£ load sáºµn tá»« _run_cdp_automation â†’ KHÃ”NG navigate láº¡i
        await asyncio.sleep(1)

        self.status_update.emit("ðŸ‘† BÆ°á»›c 1: Click Log in...", "blue")
        login_btn_pos = await self._get_login_button_center(cdp)
            
        if not login_btn_pos:
            self.status_update.emit("âš ï¸ KhÃ´ng tÃ¬m tháº¥y nÃºt Log in â€” thá»­ direct URL", "orange")
            return await self._do_login_direct(cdp, username, password)

        await self._human_move_and_click(cdp, *login_btn_pos, "Click nÃºt Log in")
        
        # Äá»£i Modal Ä‘Äƒng nháº­p hiá»‡n lÃªn
        modal_opened = False
        for _ in range(5):
            await asyncio.sleep(1)
            # Check xem modal Ä‘Ã£ hiá»‡n chÆ°a
            modal = await self._get_login_method_option_center(cdp)
            if not modal:
                modal = await self._get_center_by_texts(cdp, [
                    "Use phone / email / username",
                    "Use phone or email",
                    "Use phone/email",
                    "Sá»­ dá»¥ng sá»‘ Ä‘iá»‡n thoáº¡i hoáº·c email",
                    "Sá»­ dá»¥ng sá»‘ Ä‘iá»‡n thoáº¡i / email / tÃªn ngÆ°á»i dÃ¹ng",
                    "Sá»­ dá»¥ng Ä‘iá»‡n thoáº¡i / email / tÃªn ngÆ°á»i dÃ¹ng",
                    "Tiáº¿p tá»¥c báº±ng Ä‘iá»‡n thoáº¡i hoáº·c email",
                ])
            if modal:
                modal_opened = True
                break

        if not modal_opened:
            self.status_update.emit("âš ï¸ JS Click dá»± phÃ²ng (Log in)...", "orange")
            await cdp.evaluate("""
            (() => {
                const btn = document.querySelector('button[data-e2e="top-login-button"], #header-login-button');
                if (btn) btn.click();
            })()
            """)
            await asyncio.sleep(2)
            modal = await self._get_login_method_option_center(cdp)
            if not modal:
                modal = await self._get_center_by_texts(cdp, [
                    "Use phone / email / username",
                    "Use phone or email",
                    "Use phone/email",
                    "Sá»­ dá»¥ng sá»‘ Ä‘iá»‡n thoáº¡i hoáº·c email",
                    "Sá»­ dá»¥ng sá»‘ Ä‘iá»‡n thoáº¡i / email / tÃªn ngÆ°á»i dÃ¹ng",
                    "Sá»­ dá»¥ng Ä‘iá»‡n thoáº¡i / email / tÃªn ngÆ°á»i dÃ¹ng",
                    "Tiáº¿p tá»¥c báº±ng Ä‘iá»‡n thoáº¡i hoáº·c email",
                ])
            if modal: modal_opened = True

        if not modal_opened:
            self.status_update.emit("âš ï¸ Modal khÃ´ng má»Ÿ â€” thá»­ direct URL", "orange")
            return await self._do_login_direct(cdp, username, password)

        # â”€â”€ BÆ¯á»šC 2: Click [Use phone / email / username] â”€â”€â”€â”€â”€â”€â”€â”€
        self.status_update.emit("ðŸ‘† BÆ°á»›c 2: Click Use phone/email...", "blue")
        phone_pos = await self._get_login_method_option_center(cdp)
        if not phone_pos:
            phone_pos = await self._get_center_by_texts(cdp, [
                "Use phone / email / username",
                "Use phone or email",
                "Use phone/email",
                "Sá»­ dá»¥ng sá»‘ Ä‘iá»‡n thoáº¡i hoáº·c email",
                "Sá»­ dá»¥ng sá»‘ Ä‘iá»‡n thoáº¡i / email / tÃªn ngÆ°á»i dÃ¹ng",
                "Sá»­ dá»¥ng Ä‘iá»‡n thoáº¡i / email / tÃªn ngÆ°á»i dÃ¹ng",
                "Tiáº¿p tá»¥c báº±ng Ä‘iá»‡n thoáº¡i hoáº·c email",
            ])
        if not phone_pos:
            phone_pos = await self._get_center_by_texts(cdp, [
                "Continue with phone",
                "Use phone or email",
                "Tiáº¿p tá»¥c báº±ng Ä‘iá»‡n thoáº¡i",
                "Sá»­ dá»¥ng sá»‘ Ä‘iá»‡n thoáº¡i hoáº·c email",
            ])

        if not phone_pos:
            self.status_update.emit("âš ï¸ KhÃ´ng tÃ¬m tháº¥y Use phone/email â€” thá»­ direct URL", "orange")
            return await self._do_login_direct(cdp, username, password)

        await self._human_move_and_click(cdp, *phone_pos, "Click Use phone/email")
        await asyncio.sleep(1)

        # Náº¿u click tá»a Ä‘á»™ khÃ´ng Äƒn, dÃ¹ng JS click Ä‘Ãºng item Ä‘Ã£ match text.
        tab_probe = await self._get_center_by_texts(cdp, [
            "Log in with email or username",
            "Use email or username",
            "Use email",
            "Sá»­ dá»¥ng email hoáº·c tÃªn ngÆ°á»i dÃ¹ng",
            "ÄÄƒng nháº­p báº±ng email hoáº·c tÃªn ngÆ°á»i dÃ¹ng",
            "Log in with email",
            "ÄÄƒng nháº­p báº±ng email",
        ])
        if not tab_probe:
            if await self._click_login_method_option_js(cdp):
                self.status_update.emit("ðŸ‘† JS Click Use phone/email...", "blue")
                await asyncio.sleep(1.5)
        
        # Äá»£i tab email hiá»‡n lÃªn
        email_ready = False
        for _ in range(4):
            await asyncio.sleep(1)
            tab = await self._get_center_by_texts(cdp, [
                "Log in with email or username",
                "Use email or username",
                "Use email",
                "Sá»­ dá»¥ng email hoáº·c tÃªn ngÆ°á»i dÃ¹ng",
                "ÄÄƒng nháº­p báº±ng email hoáº·c tÃªn ngÆ°á»i dÃ¹ng",
                "Log in with email",
                "ÄÄƒng nháº­p báº±ng email",
            ])
            if tab:
                email_ready = True
                break
                
        if not email_ready:
            self.status_update.emit("âš ï¸ JS Click dá»± phÃ²ng (Use phone)...", "orange")
            await cdp.evaluate("""
            (() => {
                const all = document.querySelectorAll('div, button, a, p, span, label');
                for (const el of all) {
                    if (el.innerText && (
                        el.innerText.includes('Use phone / email') ||
                        el.innerText.includes('Use phone or email') ||
                        el.innerText.includes('Use phone/email') ||
                        el.innerText.includes('Sá»­ dá»¥ng sá»‘ Ä‘iá»‡n thoáº¡i hoáº·c email') ||
                        el.innerText.includes('Sá»­ dá»¥ng sá»‘ Ä‘iá»‡n thoáº¡i / email / tÃªn ngÆ°á»i dÃ¹ng') ||
                        el.innerText.includes('Sá»­ dá»¥ng Ä‘iá»‡n thoáº¡i / email / tÃªn ngÆ°á»i dÃ¹ng') ||
                        el.innerText.includes('Tiáº¿p tá»¥c báº±ng Ä‘iá»‡n thoáº¡i hoáº·c email')
                    )) {
                        el.click(); break;
                    }
                }
            })()
            """)
            await asyncio.sleep(2)
            tab = await self._get_center_by_texts(cdp, [
                "Log in with email or username",
                "Use email or username",
                "Use email",
                "Sá»­ dá»¥ng email hoáº·c tÃªn ngÆ°á»i dÃ¹ng",
                "ÄÄƒng nháº­p báº±ng email hoáº·c tÃªn ngÆ°á»i dÃ¹ng",
                "Log in with email",
                "ÄÄƒng nháº­p báº±ng email",
            ])
            if tab: email_ready = True

        # â”€â”€ BÆ¯á»šC 3: Click [Log in with email or username] â”€â”€â”€â”€â”€â”€â”€
        if email_ready:
            self.status_update.emit("ðŸ‘† BÆ°á»›c 3: Click tab email/username...", "blue")
            email_tab_pos = await self._get_center_by_texts(cdp, [
                "Log in with email or username",
                "Use email or username",
                "Use email",
                "Sá»­ dá»¥ng email hoáº·c tÃªn ngÆ°á»i dÃ¹ng",
                "ÄÄƒng nháº­p báº±ng email hoáº·c tÃªn ngÆ°á»i dÃ¹ng",
                "Log in with email",
                "ÄÄƒng nháº­p báº±ng email",
            ])
            
            if email_tab_pos:
                await self._human_move_and_click(cdp, *email_tab_pos, "Click tab email/username")
            await asyncio.sleep(random.uniform(1.0, 1.5))
        else:
            self.status_update.emit("âš ï¸ KhÃ´ng tháº¥y tab email â€” thá»­ direct URL", "orange")
            return await self._do_login_direct(cdp, username, password)

        # â”€â”€ BÆ¯á»šC 4: Nháº­p Email + Password â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        return await self._do_login_direct(cdp, username, password)

    async def _do_login_direct(self, cdp, username, password, force_direct_url=False):
        """BÆ°á»›c cuá»‘i: nháº­p email/pass vÃ  submit (dÃ¹ng Ä‘Æ°á»£c Ä‘á»™c láº­p náº¿u Ä‘Ã£ á»Ÿ form)."""
        # Chá» Ã´ email/username. Náº¿u modal khÃ´ng má»Ÿ Ä‘Ãºng cÃ¡ch, ráº½ sang URL login email tháº­t sá»±.
        if force_direct_url:
            try:
                await self._navigate_like_human(
                    cdp,
                    "https://www.tiktok.com/login/phone-or-email/email",
                    wait=5,
                )
            except Exception:
                try:
                    await cdp.navigate("https://www.tiktok.com/login/phone-or-email/email")
                    await asyncio.sleep(5)
                except Exception:
                    pass

        email_pos = await self._wait_login_username_input(cdp, timeout=12)
        if not email_pos:
            self.status_update.emit("âš ï¸ ChÆ°a tháº¥y form login â€” má»Ÿ URL Ä‘Äƒng nháº­p email trá»±c tiáº¿p...", "orange")
            try:
                await self._navigate_like_human(
                    cdp,
                    "https://www.tiktok.com/login/phone-or-email/email",
                    wait=5,
                )
            except Exception:
                try:
                    await cdp.navigate("https://www.tiktok.com/login/phone-or-email/email")
                    await asyncio.sleep(5)
                except Exception:
                    pass

            email_pos = await self._wait_login_username_input(cdp, timeout=15)
            if not email_pos:
                self._emit_login_error("Timeout cho form dang nhap TikTok")
                self.status_update.emit("âŒ Timeout chá» form Ä‘Äƒng nháº­p", "red")
                return False

        await asyncio.sleep(random.uniform(0.5, 1.0))

        # GÃµ email
        self.status_update.emit("âŒ¨ï¸ BÆ°á»›c 4a: Nháº­p Email...", "blue")
        pos = email_pos or await self._get_center(cdp, 'input[name="username"]')
        if pos:
            await self._human_move_and_click(cdp, *pos, "Click Ã´ Email")
        await asyncio.sleep(random.uniform(0.3, 0.6))
        if not await self._type_active_input_exact(cdp, username, "Email"):
            self._emit_login_error("Tool khong nhap dung Email - da dung de tranh mat luot thu")
            return False
        await asyncio.sleep(random.uniform(0.4, 0.8))

        # GÃµ password
        self.status_update.emit("âŒ¨ï¸ BÆ°á»›c 4b: Nháº­p Password...", "blue")
        pos = await self._get_center(cdp, 'input[type="password"]')
        if pos:
            await self._human_move_and_click(cdp, *pos, "Click Ã´ Password")
        await asyncio.sleep(random.uniform(0.3, 0.6))
        if not await self._type_active_input_exact(cdp, password, "Password", secret=True):
            self._emit_login_error("Tool khong nhap dung Password - da dung de tranh mat luot thu")
            return False
        await asyncio.sleep(random.uniform(0.5, 1.0))

        # Click nÃºt Login
        self.status_update.emit("ðŸ‘† BÆ°á»›c 5: Click nÃºt Log in...", "blue")
        form_state = await self._verify_login_form_values(cdp, username, password)
        if not form_state.get("username_ok") or not form_state.get("password_ok"):
            self.status_update.emit(
                "Khong submit vi form login khong khop du lieu da luu "
                f"(email_len={form_state.get('username_len')}, pass_len={form_state.get('password_len')})",
                "red"
            )
            self._emit_login_error("Form login khong khop du lieu da luu - khong bam Login")
            return False

        pos = await self._get_center(cdp, 'button[data-e2e="login-button"]')
        if not pos:
            pos = await self._get_center_by_texts(cdp, ["Log in", "ÄÄƒng nháº­p"])
        if pos:
            await self._human_move_and_click(cdp, *pos, "Click Login")
        else:
            self._emit_login_error("Khong tim thay nut Log in")
            self.status_update.emit("âŒ KhÃ´ng tÃ¬m tháº¥y nÃºt Log in", "red")
            return False

        # â”€â”€ BÆ¯á»šC 5: Chá» káº¿t quáº£ â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        TOTAL_WAIT = 120   # giÃ¢y (sáº½ tÄƒng thÃªm khi cÃ³ CAPTCHA)
        POLL = 2
        otp_handled = False
        verify_clicked = False
        mail_code_requested = False
        captcha_state = {
            "active": False,
            "notified": False,
            "provider_attempted": False,
            "started_at": 0.0,
            "last_type": "none",
            "extra_time": 0,
            "max_extra_time": 180,
            "timeout": 300,
        }

        for step in range(999):  # VÃ²ng láº·p má»Ÿ, thoÃ¡t báº±ng Ä‘iá»u kiá»‡n
            elapsed = (step + 1) * POLL
            if elapsed > TOTAL_WAIT + captcha_state.get("extra_time", 0):
                if captcha_state.get("active"):
                    error_msg = f"Ket CAPTCHA qua {captcha_state.get('timeout', 300)}s"
                    self.status_update.emit(error_msg, "red")
                    self._emit_login_error(error_msg)
                    return False
                break  # Háº¿t thá»i gian

            await asyncio.sleep(POLL)
            if self._stop_flag:
                return

            remaining = TOTAL_WAIT + captcha_state.get("extra_time", 0) - elapsed

            # CAPTCHA gate: khi CAPTCHA cÃ²n hiá»‡n thÃ¬ khÃ´ng check lá»—i/OTP/success.
            captcha_gate = await self._handle_captcha_gate(cdp, captcha_state, remaining, poll=POLL)
            if captcha_gate.get("failed"):
                return False
            if captcha_gate.get("blocked"):
                continue

            self.status_update.emit(f"â³ Chá» Ä‘Äƒng nháº­p... ({remaining}s)", "blue")

            # â”€ KIá»‚M TRA Lá»–I TRÆ¯á»šC â€” dÃ²ng chá»¯ Ä‘á» trÃªn form login â”€
            if not verify_clicked:
                needs_verify = await cdp.evaluate(r"""
                (() => {
                    const body = document.body.innerText || '';
                    const clean = (body || '')
                        .toLowerCase()
                        .normalize('NFD')
                        .replace(/[\u0300-\u036f]/g, '')
                        .replace(/\s+/g, ' ')
                        .trim();
                    return body.includes("Verify it's really you") ||
                           body.includes("verify your identity") ||
                           clean.includes("xac minh do la ban") ||
                           clean.includes("xac minh do thuc su la ban") ||
                           clean.includes("xac minh danh tinh") ||
                           body.includes("XÃ¡c minh Ä‘Ã³ lÃ  báº¡n") ||
                           body.includes("XÃ¡c minh danh tÃ­nh") ||
                           body.includes("XÃ¡c minh Ä‘Ã³ thá»±c sá»± lÃ  báº¡n") ||
                           body.includes("XÃ¡c minh danh tÃ­nh");
                })()
                """)
                if needs_verify:
                    verify_clicked = True
                    self.status_update.emit("TikTok yeu cau xac minh danh tinh - chon Email...", "orange")
                    email_btn = await self._get_verify_email_option_center(cdp)
                    if not email_btn:
                        email_btn = await self._get_center_by_texts(cdp, [
                            "Email",
                            "Gui ma qua email",
                            "Gá»­i mÃ£ qua email",
                        ])
                    if email_btn:
                        await self._human_move_and_click(cdp, *email_btn, "Chon xac minh Email")
                    else:
                        await cdp.evaluate("""
                        (() => {
                            const all = Array.from(document.querySelectorAll('*'));
                            for(const el of all) {
                                if(el.innerText &&
                                   (el.innerText.includes('Email') || el.innerText.includes('email')) &&
                                   el.childElementCount < 4) {
                                    el.click(); return;
                                }
                            }
                        })()
                        """)
                    await asyncio.sleep(1.5)
                    await self._click_verify_continue_if_present(cdp)
                    await asyncio.sleep(2)
                    continue

            error_msg = await cdp.evaluate("""
            (() => {
                // === CÃ¡ch 1: TÃ¬m element cÃ³ text mÃ u Ä‘á» trÃªn form login ===
                const redEls = document.querySelectorAll(
                    'span[style*="color: rgb(255"], span[style*="color: red"],' +
                    ' p[style*="color: rgb(255"], p[style*="color: red"],' +
                    ' div[class*="error" i], span[class*="error" i],' +
                    ' p[class*="error" i], div[class*="Error"],' +
                    ' [class*="StatusMessage"], [class*="status-message"]'
                );
                for (const el of redEls) {
                    const t = (el.innerText || '').trim();
                    if (t.length > 5 && el.getBoundingClientRect().width > 0)
                        return t;
                }

                // === CÃ¡ch 2: Text-based detection ===
                const body = document.body.innerText || '';

                // Sai máº­t kháº©u / sai tÃ i khoáº£n (CHÃNH XÃC nhÆ° screenshot TikTok)
                if(body.includes("Incorrect account or password") || body.includes("Incorrect password")
                   || body.includes("doesn't match our records") || body.includes("Sai máº­t kháº©u")
                   || body.includes("password is incorrect") || body.includes("Sai tÃ i khoáº£n"))
                    return "Sai tÃ i khoáº£n hoáº·c máº­t kháº©u";

                // CÃ²n bao nhiÃªu láº§n thá»­
                const attemptsMatch = body.match(/(\\d+)\\s*attempts?\\s*remaining/i);
                if (attemptsMatch)
                    return "Sai tÃ i khoáº£n/máº­t kháº©u. CÃ²n " + attemptsMatch[1] + " láº§n thá»­";

                // Email/Username khÃ´ng tá»“n táº¡i
                if(body.includes("Couldn't find your account") || body.includes("account not found")
                   || body.includes("user does not exist") || body.includes("khÃ´ng tÃ¬m tháº¥y tÃ i khoáº£n")
                   || body.includes("This username isn't registered")
                   || body.includes("TÃªn ngÆ°á»i dÃ¹ng nÃ y chÆ°a Ä‘Æ°á»£c Ä‘Äƒng kÃ½"))
                    return "Email/Username khÃ´ng tá»“n táº¡i";

                // VÆ°á»£t quÃ¡ sá»‘ láº§n thá»­
                if(body.includes("Maximum number of attempts") || body.includes("vÆ°á»£t quÃ¡ sá»‘ láº§n")
                   || body.includes("Too many attempts") || body.includes("too many failed attempts")
                   || body.includes("0 attempts remaining"))
                    return "VÆ°á»£t quÃ¡ sá»‘ láº§n thá»­";

                // TÃ i khoáº£n bá»‹ khÃ³a/Ä‘Ã¬nh chá»‰
                if(body.includes("Account currently locked") || body.includes("táº¡m thá»i bá»‹ khÃ³a")
                   || body.includes("account has been suspended") || body.includes("tÃ i khoáº£n Ä‘Ã£ bá»‹ Ä‘Ã¬nh chá»‰")
                   || body.includes("account has been banned") || body.includes("permanently banned"))
                    return "TÃ i khoáº£n bá»‹ khÃ³a/Ä‘Ã¬nh chá»‰";

                // Lá»—i máº¡ng / há»‡ thá»‘ng
                if(body.includes("Something went wrong") || body.includes("ÄÃ£ xáº£y ra lá»—i")
                   || body.includes("network error") || body.includes("try again later"))
                    return "Lá»—i há»‡ thá»‘ng TikTok";

                return '';
            })()
            """)
            if error_msg:
                self.status_update.emit(f"âŒ {error_msg}", "red")
                # Emit lá»—i vá» Dashboard Ä‘á»ƒ cáº­p nháº­t cá»™t Logged
                self._emit_login_error(error_msg)
                return False

            # â”€ Kiá»ƒm tra Ä‘Äƒng nháº­p thÃ nh cÃ´ng (CHá»ˆ sau khi xÃ¡c nháº­n KHÃ”NG cÃ³ lá»—i) â”€
            if await self._check_logged_in(cdp):
                self.status_update.emit("âœ… ÄÄƒng nháº­p thÃ nh cÃ´ng!", "green")
                await self._extract_profile_info(cdp)
                # â˜… FIX 1: Biáº¿n session cookies â†’ persistent (30 ngÃ y)
                await self._persist_tiktok_cookies(cdp)
                return True

            # â”€ Xá»­ lÃ½ popup "Verify it's really you" (chá»n Email) â”€
            if not verify_clicked:
                needs_verify = await cdp.evaluate(r"""
                (() => {
                    const body = document.body.innerText || '';
                    const clean = (body || '')
                        .toLowerCase()
                        .normalize('NFD')
                        .replace(/[\u0300-\u036f]/g, '')
                        .replace(/\s+/g, ' ')
                        .trim();
                    return body.includes("Verify it's really you") ||
                           body.includes("verify your identity") ||
                           clean.includes("xac minh do la ban") ||
                           clean.includes("xac minh do thuc su la ban") ||
                           clean.includes("xac minh danh tinh") ||
                           body.includes("XÃ¡c minh Ä‘Ã³ thá»±c sá»± lÃ  báº¡n") ||
                           body.includes("XÃ¡c minh danh tÃ­nh");
                })()
                """)
                if needs_verify:
                    verify_clicked = True
                    self.status_update.emit("âš ï¸ TikTok yÃªu cáº§u xÃ¡c minh danh tÃ­nh...", "orange")
                    email_btn = await self._get_verify_email_option_center(cdp)
                    if not email_btn:
                        email_btn = await self._get_center_by_texts(cdp, [
                        "Email",
                        "Gá»­i mÃ£ qua email",
                        ])
                    if email_btn:
                        await self._human_move_and_click(cdp, *email_btn, "Chá»n xÃ¡c minh Email")
                    else:
                        await cdp.evaluate("""
                        (() => {
                            const all = Array.from(document.querySelectorAll('*'));
                            for(const el of all) {
                                if(el.innerText &&
                                   (el.innerText.includes('Email') || el.innerText.includes('email')) &&
                                   el.childElementCount < 4) {
                                    el.click(); return;
                                }
                            }
                        })()
                        """)
                    await asyncio.sleep(1.5)
                    await self._click_verify_continue_if_present(cdp)
                    await asyncio.sleep(2)
                    continue # Chá» trang OTP load

            # â”€ Náº¿u TikTok hiá»‡n popup/nÃºt "Gá»­i mÃ£" thÃ¬ pháº£i báº¥m trÆ°á»›c khi IMAP cÃ³ mail â”€
            if not mail_code_requested:
                if await self._click_mail_code_send_button_if_present(cdp):
                    mail_code_requested = True
                    self.status_update.emit("ðŸ“§ ÄÃ£ báº¥m gá»­i mÃ£ email â€” chá» mail OTP...", "blue")
                    await asyncio.sleep(5)
                    continue

            # â”€ Kiá»ƒm tra OTP (chá»‰ xá»­ lÃ½ 1 láº§n) â”€
            if not otp_handled:
                try:
                    has_otp = await cdp.evaluate(r"""
                    (() => {
                        const el = document.querySelector(
                            'input[autocomplete="one-time-code"], input[name="code"], input[placeholder*="6"]'
                        );
                        if (el && el.getBoundingClientRect().width > 0) return true;
                        const body = document.body.innerText || '';
                        const clean = (body || '')
                            .toLowerCase()
                            .normalize('NFD')
                            .replace(/[\u0300-\u036f]/g, '')
                            .replace(/\s+/g, ' ')
                            .trim();
                        return body.includes('Enter the 6-digit code') ||
                               body.includes('verification code') ||
                               body.includes('6-digit') ||
                               clean.includes('nhap ma gom 6 chu so') ||
                               clean.includes('ma xac minh') ||
                               body.includes('Nháº­p mÃ£ gá»“m 6 chá»¯ sá»‘') ||
                               body.includes('mÃ£ xÃ¡c minh');
                    })()
                    """)
                    if has_otp:
                        otp_handled = True
                        if not mail_code_requested:
                            if await self._click_mail_code_send_button_if_present(cdp):
                                mail_code_requested = True
                                self.status_update.emit("ðŸ“§ ÄÃ£ báº¥m gá»­i mÃ£ email â€” chá» mail OTP...", "blue")
                                await asyncio.sleep(5)
                        self.status_update.emit("ðŸ“§ Cáº§n OTP â€” Ä‘ang láº¥y qua IMAP...", "orange")
                        imap_pass = self.profile_data.get("password_mail", "") or password
                        otp_code  = await self._get_tiktok_code_via_imap(username, imap_pass)
                        if otp_code:
                            self.status_update.emit(f"ðŸ”‘ Nháº­p OTP: {otp_code}", "blue")
                            pos = await self._get_center(cdp,
                                'input[autocomplete="one-time-code"], input[name="code"], input[placeholder*="6"]')
                            if pos:
                                await self._human_move_and_click(cdp, *pos, "Click Ã´ OTP")
                            await cdp.type_text(otp_code, delay=random.randint(80, 150))
                            await asyncio.sleep(random.uniform(0.8, 1.2))
                            if not await self._click_otp_submit_button(cdp):
                                self.status_update.emit("âš ï¸ KhÃ´ng click Ä‘Æ°á»£c nÃºt Tiáº¿p OTP â€” thá»­ Enter", "orange")
                                await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter"})
                                await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter"})
                        else:
                            self.status_update.emit("âŒ IMAP khÃ´ng láº¥y Ä‘Æ°á»£c OTP", "red")
                            return False
                except Exception:
                    pass

        # â”€â”€ TH2: Auto login tháº¥t báº¡i â†’ chá» ngÆ°á»i dÃ¹ng tá»± nháº­p â”€â”€
        if self._is_batch_run() and not self._last_login_error:
            self._emit_login_error("Auto login khong hoan tat")
        return await self._wait_manual_login(cdp)

    async def _wait_manual_login(self, cdp):
        if self._is_batch_run():
            reason = self._last_login_error or "Can dang nhap thu cong"
            self._emit_login_error(reason)
            self.status_update.emit(
                f"Batch mode bo qua cho dang nhap thu cong: {reason}",
                "orange",
            )
            return False
        """TH2: Chá» ngÆ°á»i dÃ¹ng tá»± Ä‘Äƒng nháº­p thá»§ cÃ´ng (5 phÃºt).
        Polling má»—i 3 giÃ¢y kiá»ƒm tra _check_logged_in().
        """
        self.status_update.emit("ðŸ‘¤ Chá» Ä‘Äƒng nháº­p thá»§ cÃ´ng (5 phÃºt)...", "orange")
        GRACE = 300  # 5 phÃºt
        GRACE_POLL = 3
        for step in range(GRACE // GRACE_POLL):
            await asyncio.sleep(GRACE_POLL)
            if self._stop_flag:
                return False
            remaining = GRACE - (step + 1) * GRACE_POLL
            self.status_update.emit(f"ðŸ‘¤ Chá» Ä‘Äƒng nháº­p thá»§ cÃ´ng... ({remaining}s)", "orange")
            if await self._check_logged_in(cdp):
                self.status_update.emit("âœ… PhÃ¡t hiá»‡n Ä‘Äƒng nháº­p thÃ nh cÃ´ng!", "green")
                await self._extract_profile_info(cdp)
                # â˜… FIX 1: Biáº¿n session cookies â†’ persistent (30 ngÃ y)
                await self._persist_tiktok_cookies(cdp)
                return True

        self.status_update.emit("âŒ Háº¿t thá»i gian â€” KhÃ´ng Ä‘Äƒng nháº­p Ä‘Æ°á»£c", "red")
        return False


    async def _check_logged_in(self, cdp) -> bool:
        """Kiá»ƒm tra Ä‘Ã£ Ä‘Äƒng nháº­p TikTok chÆ°a â€” sessionid + DOM verify."""
        try:
            # Äá»c cookie qua CDP (Ä‘á»c Ä‘Æ°á»£c HttpOnly!)
            cookies = await cdp.get_cookies()
            has_auth_cookie = self._has_valid_tiktok_auth_cookie(cookies)
            if not has_auth_cookie:
                return False

            # â˜… CÃ³ cookie Ä‘Äƒng nháº­p â†’ check DOM (nÃºt "Log in" cÃ³ hiá»‡n khÃ´ng?)
            # TikTok sau inject cookie: nÃºt Login váº«n hiá»‡n 2-3s rá»“i má»›i áº©n
            # â†’ Retry tá»‘i Ä‘a 3 láº§n, má»—i láº§n 1s
            for attempt in range(3):
                has_login_btn = await cdp.evaluate("""
                (() => {
                    const loginBtns = document.querySelectorAll(
                        'button[data-e2e="top-login-button"], #header-login-button'
                    );
                    for (const btn of loginBtns) {
                        const r = btn.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) return true;
                    }
                    const allBtns = document.querySelectorAll('button, a');
                    for (const btn of allBtns) {
                        const r = btn.getBoundingClientRect();
                        if (r.y < 80 && r.width > 0 &&
                            (btn.textContent.trim() === 'Log in' || btn.textContent.trim() === 'ÄÄƒng nháº­p'))
                            return true;
                    }
                    return false;
                })()
                """) or False

                if not has_login_btn:
                    # CÃ³ auth cookie + KHÃ”NG cÃ³ nÃºt Login â†’ ÄÃƒ LOGIN âœ…
                    return True

                # CÃ³ auth cookie + CÃ“ nÃºt Login â†’ TikTok chÆ°a xá»­ lÃ½ xong
                if attempt < 2:
                    await asyncio.sleep(1)  # Chá» TikTok process cookie

            # Sau 3 láº§n váº«n cÃ³ Login btn â†’ cookie tháº­t sá»± háº¿t háº¡n
            return False
        except Exception:
            return False

    async def _extract_profile_info(self, cdp, need_reload=True) -> str:
        """Láº¥y @username + cookie â€” click avatar â†’ vÃ o profile â†’ Ä‘á»c URL.
        need_reload: True khi login thÆ°á»ng (cáº§n reload hiá»‡n avatar), False khi bÆ¡m cookie (Ä‘Ã£ reload).
        Returns: tiktok_id (str) náº¿u thÃ nh cÃ´ng, rá»—ng náº¿u tháº¥t báº¡i."""
        try:
            # â˜… BÆ¯á»šC 1: Reload trang CHá»ˆ KHI login thÆ°á»ng (TikTok bug: avatar chÆ°a hiá»‡n)
            if need_reload:
                self.status_update.emit("ðŸ”„ Reload trang Ä‘á»ƒ hiá»ƒn thá»‹ há»“ sÆ¡...", "blue")
                try:
                    await cdp.send("Page.reload")
                except Exception:
                    await self._navigate_like_human(cdp, "tiktok.com", wait=5)
                await asyncio.sleep(5)
            else:
                # BÆ¡m cookie xong â†’ skip popup trÆ°á»›c khi tÃ¬m avatar
                await self._skip_tiktok_popup(cdp)
                await asyncio.sleep(1)

            # â”€ Láº¥y cookie qua CDP â”€
            cookies = await cdp.get_cookies()
            tiktok_cookies = [c for c in cookies if 'tiktok' in c.get('domain', '')]
            cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in tiktok_cookies])
            if not cookie_str:
                cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])

            # â˜… BÆ¯á»šC 2: Click vÃ o avatar/profile icon á»Ÿ sidebar
            self.status_update.emit("ðŸ‘¤ Click vÃ o há»“ sÆ¡...", "blue")

            profile_pos = await cdp.evaluate(r"""
            (() => {
                // CÃ¡ch 1: Link profile chÃ­nh thá»©c
                const navProfile = document.querySelector('a[data-e2e="nav-profile"]');
                if (navProfile) {
                    const r = navProfile.getBoundingClientRect();
                    if (r.width > 0) return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)};
                }

                // CÃ¡ch 2: Link /@username trong sidebar (x < 100)
                const sideLinks = document.querySelectorAll('a[href*="/@"]');
                for (const a of sideLinks) {
                    const r = a.getBoundingClientRect();
                    if (r.width > 0 && r.x < 100) {
                        // Æ¯u tiÃªn link á»Ÿ dÆ°á»›i cÃ¹ng (profile thÆ°á»ng á»Ÿ cuá»‘i sidebar)
                        if (r.y > 400)
                            return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)};
                    }
                }
                // Fallback: link /@... Ä‘áº§u tiÃªn trong sidebar
                for (const a of sideLinks) {
                    const r = a.getBoundingClientRect();
                    if (r.width > 0 && r.x < 100)
                        return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)};
                }

                // CÃ¡ch 3: Avatar nhá» á»Ÿ sidebar
                const imgs = document.querySelectorAll('img[class*="avatar" i], img[class*="Avatar"]');
                for (const img of imgs) {
                    const r = img.getBoundingClientRect();
                    if (r.width > 0 && r.width < 50 && r.x < 100 && r.y > 400) {
                        const link = img.closest('a');
                        if (link) {
                            const lr = link.getBoundingClientRect();
                            return {x: Math.round(lr.x + lr.width/2), y: Math.round(lr.y + lr.height/2)};
                        }
                    }
                }
                return null;
            })()
            """)

            tiktok_id = ""
            if profile_pos:
                await self._human_move_and_click(cdp, profile_pos['x'], profile_pos['y'], "Click há»“ sÆ¡")
                await asyncio.sleep(3)

                # â˜… BÆ¯á»šC 3: Äá»c @username tá»« URL (chÃ­nh xÃ¡c 100%)
                tiktok_id = await cdp.evaluate(r"""
                (() => {
                    const match = location.pathname.match(/^\/@([^/?]+)/);
                    if (match) return '@' + match[1];
                    return '';
                })()
                """) or ""

            # â˜… Fallback: localStorage
            display_name = ""
            if not tiktok_id:
                self.status_update.emit("ðŸ” Thá»­ localStorage...", "blue")
                tiktok_id = await cdp.evaluate("""
                (() => {
                    try {
                        const d = JSON.parse(localStorage.getItem('webapp_user_info') || '{}');
                        const uid = d.uniqueId || d.unique_id || '';
                        if (uid) return '@' + uid;
                    } catch(e) {}
                    return '';
                })()
                """) or ""

            # â”€ Káº¿t quáº£ â”€
            if tiktok_id:
                self.status_update.emit(f"âœ… LÆ°u há»“ sÆ¡: {tiktok_id}", "green")
            else:
                self.status_update.emit("âš ï¸ KhÃ´ng láº¥y Ä‘Æ°á»£c @username", "orange")
            # â˜… KHÃ”NG navigate Ä‘i Ä‘Ã¢u â€” giá»¯ nguyÃªn trang, Feed tá»± xá»­ lÃ½ sau

            # â”€ Emit â†’ Dashboard â”€
            self.profile_update_signal.emit({
                "tiktok_id": tiktok_id,
                "cookie": cookie_str,
                "display_name": display_name,
                "refresh_token": self.profile_data.get("refresh_token", ""),
            })
            return tiktok_id

        except Exception as e:
            self.status_update.emit(f"âŒ Lá»—i láº¥y há»“ sÆ¡: {str(e)[:60]}", "red")
            return ""

    async def _get_tiktok_code_via_imap(self, email, password):
        """Láº¥y OTP báº±ng module dÃ¹ng chung vá»›i báº£ng ÄÄƒng KÃ½."""
        try:
            from hotmail_otp import fetch_otp_from_email
        except Exception as e:
            self.status_update.emit(f"âŒ KhÃ´ng import Ä‘Æ°á»£c hotmail_otp: {str(e)[:60]}", "red")
            return None

        email = (email or "").strip()
        mailbox_password = (password or "").strip()
        refresh_token = self.profile_data.get("refresh_token", "").strip()
        client_id = self.profile_data.get("client_id", "").strip()

        if not email:
            self.status_update.emit("âŒ KhÃ´ng cÃ³ email Ä‘á»ƒ láº¥y OTP", "red")
            return None
        if not mailbox_password and not refresh_token:
            self.status_update.emit("âŒ Cáº§n password_mail hoáº·c refresh_token Ä‘á»ƒ láº¥y OTP", "red")
            return None

        def progress(message):
            self.status_update.emit(str(message), "blue")

        try:
            result = await asyncio.to_thread(
                fetch_otp_from_email,
                email=email,
                password=mailbox_password,
                refresh_token=refresh_token,
                client_id=client_id,
                keyword="tiktok",
                max_retries=4,
                wait_seconds=5,
                progress_callback=progress,
            )
        except Exception as e:
            self.status_update.emit(f"âŒ Láº¥y OTP lá»—i: {str(e)[:80]}", "red")
            return None

        if not isinstance(result, dict):
            self.status_update.emit("âŒ Láº¥y OTP khÃ´ng tráº£ vá» káº¿t quáº£ há»£p lá»‡", "red")
            return None

        new_rt = (result.get("new_refresh_token") or "").strip()
        if new_rt and new_rt != refresh_token:
            self.profile_data["refresh_token"] = new_rt
            self.profile_update_signal.emit({"refresh_token": new_rt})
            self.status_update.emit("ðŸ”„ ÄÃ£ cáº­p nháº­t refresh_token má»›i", "blue")

        otp = (result.get("otp") or "").strip()
        if result.get("status") == "success" and otp:
            return otp

        message = result.get("message") or "KhÃ´ng láº¥y Ä‘Æ°á»£c OTP"
        self.status_update.emit(f"âŒ {message}", "red")
        return None

    async def _get_microsoft_oauth_token(self, email, password):
        """Láº¥y OAuth2 access token cho Hotmail/Outlook.
        Æ¯u tiÃªn: refresh_token â†’ ROPC (fallback).
        Äá»c refresh_token vÃ  client_id tá»« profile_data.
        """
        try:
            import msal
        except ImportError:
            self.status_update.emit("âŒ Thiáº¿u msal: pip install msal", "red")
            return None

        try:
            # Láº¥y config tá»« profile
            refresh_token = self.profile_data.get("refresh_token", "").strip()
            client_id = self.profile_data.get("client_id", "").strip()

            # Fallback Client ID: Thunderbird public client
            if not client_id:
                client_id = "08162f7c-0fd2-4200-a84a-f25a4db0b584"

            AUTHORITY = "https://login.microsoftonline.com/consumers"
            SCOPES = ["https://outlook.office365.com/IMAP.AccessAsUser.All"]

            app = msal.PublicClientApplication(client_id, authority=AUTHORITY)

            # â•â•â• CÃ¡ch 1: DÃ¹ng Refresh Token (Æ°u tiÃªn) â•â•â•
            if refresh_token:
                self.status_update.emit("ðŸ”‘ Äang láº¥y token báº±ng Refresh Token...", "blue")
                result = app.acquire_token_by_refresh_token(
                    refresh_token=refresh_token,
                    scopes=SCOPES
                )
                if "access_token" in result:
                    # Cáº­p nháº­t refresh_token má»›i (Microsoft tráº£ vá» má»›i má»—i láº§n)
                    new_rt = result.get("refresh_token", "")
                    if new_rt and new_rt != refresh_token:
                        self.profile_data["refresh_token"] = new_rt
                        self.status_update.emit("ðŸ”„ Refresh token Ä‘Ã£ Ä‘Æ°á»£c cáº­p nháº­t", "blue")
                    self.status_update.emit("âœ… OAuth token OK (refresh)", "green")
                    return result["access_token"]
                else:
                    error = result.get("error_description", result.get("error", ""))
                    self.status_update.emit(f"âš ï¸ Refresh token lá»—i: {str(error)[:50]}", "orange")
                    # Refresh token háº¿t háº¡n â†’ thá»­ ROPC

            # â•â•â• CÃ¡ch 2: ROPC flow (fallback â€” cáº§n tÃ i khoáº£n khÃ´ng cÃ³ 2FA) â•â•â•
            if password:
                self.status_update.emit("ðŸ”‘ Thá»­ ROPC flow...", "blue")
                result = app.acquire_token_by_username_password(
                    username=email,
                    password=password,
                    scopes=SCOPES
                )
                if "access_token" in result:
                    # LÆ°u refresh_token má»›i Ä‘á»ƒ láº§n sau dÃ¹ng
                    new_rt = result.get("refresh_token", "")
                    if new_rt:
                        self.profile_data["refresh_token"] = new_rt
                        self.status_update.emit("ðŸ”„ ÄÃ£ láº¥y Ä‘Æ°á»£c refresh token má»›i", "blue")
                    self.status_update.emit("âœ… OAuth token OK (ROPC)", "green")
                    return result["access_token"]
                else:
                    error = result.get("error_description", result.get("error", "Unknown"))
                    self.status_update.emit(f"âŒ OAuth lá»—i: {str(error)[:60]}", "red")
                    return None

            self.status_update.emit("âŒ KhÃ´ng cÃ³ refresh_token vÃ  password_mail", "red")
            return None

        except Exception as e:
            self.status_update.emit(f"âŒ OAuth exception: {str(e)[:60]}", "red")
            return None

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    #  HELPER: kiá»ƒm tra tá»‰ lá»‡ %
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

    def _hit(self, key: str) -> bool:
        """True náº¿u ngáº«u nhiÃªn rÆ¡i vÃ o tá»‰ lá»‡ % cÃ i trong feed_settings."""
        pct = self.feed_settings.get(key, 0)
        return pct > 0 and random.randint(1, 100) <= pct

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    #  HELPER: PhÃ¡t hiá»‡n loáº¡i video (LIVE / Ads / restricted / normal)
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

    async def _detect_video_type(self, cdp) -> str:
        """
        PhÃ¡t hiá»‡n loáº¡i video Ä‘ang hiá»ƒn thá»‹ trÃªn mÃ n hÃ¬nh.
        Returns: 'normal' | 'live' | 'ads' | 'restricted' | 'no_comment'
        """
        vtype = await cdp.evaluate("""
        (() => {
            // â”€â”€ LIVE? Chá»‰ kiá»ƒm tra badge LIVE rÃµ rÃ ng â”€â”€
            const liveEls = document.querySelectorAll(
                '[data-e2e*="live"], [class*="LiveBadge"], [class*="LiveTag"]'
            );
            for (const el of liveEls) {
                const t = (el.innerText || '').trim();
                if (t === 'LIVE' || t === 'LIVE now' || t.includes('watch LIVE'))
                    if (el.offsetWidth > 0) return 'live';
            }

            // â”€â”€ Ads / Sponsored? Chá»‰ kiá»ƒm tra label trá»±c tiáº¿p â”€â”€
            const adSels = [
                '[class*="Sponsored"]', '[class*="sponsored"]',
                '[class*="AdBadge"]', '[data-e2e*="ad-"]'
            ];
            for (const sel of adSels) {
                const el = document.querySelector(sel);
                if (el && el.offsetWidth > 0) return 'ads';
            }
            // Text-based fallback cho "Promotional content" (TikTok ads)
            const spans = document.querySelectorAll('span, div');
            for (const sp of spans) {
                const t = (sp.innerText || '').trim();
                if ((t === 'Sponsored' || t === 'Promoted' || t === 'Promotional content')
                    && sp.offsetWidth > 0 && sp.children.length < 2)
                    return 'ads';
            }

            // â”€â”€ Video bÃ¬nh thÆ°á»ng: kiá»ƒm tra comment kháº£ dá»¥ng â”€â”€
            // Mode 1: ForYou feed â€” cÃ³ icon comment trÃªn sidebar
            const cmtIcon = document.querySelector(
                '[data-e2e="comment-icon"]'
            );
            if (cmtIcon && cmtIcon.getBoundingClientRect().width > 0)
                return 'normal';

            // Mode 2: Full-page video â€” comment panel Ä‘Ã£ má»Ÿ sáºµn bÃªn pháº£i
            const cmtPanel = document.querySelector(
                '[data-e2e="comment-input"], [data-e2e="comment-list"],' +
                ' div[class*="CommentListContainer"], div[class*="comment-list"],' +
                ' div[contenteditable="true"]'
            );
            if (cmtPanel && cmtPanel.getBoundingClientRect().width > 0)
                return 'normal';

            // Mode 3: Kiá»ƒm tra sá»‘ comment hiá»ƒn thá»‹ (text "937" bÃªn cáº¡nh icon)
            const cmtCount = document.querySelector(
                '[data-e2e="comment-count"], strong[data-e2e="comment-count"]'
            );
            if (cmtCount && cmtCount.getBoundingClientRect().width > 0)
                return 'normal';

            return 'no_comment';
        })()
        """)
        result = vtype or 'normal'
        self.status_update.emit(f"ðŸ” Video type: {result}", "blue")
        return result

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    #  HELPER: TÆ°Æ¡ng tÃ¡c Comment (má»Ÿ panel, like, clone, view more)
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

    EMOJIS = ["ðŸ˜‚","â¤ï¸","ðŸ”¥","ðŸ˜","ðŸ‘","ðŸŽ‰","ðŸ’¯","ðŸ˜­","ðŸ¥°",
              "âœ¨","ðŸ¤£","ðŸ˜Š","ðŸ‘","ðŸ’•","ðŸ™Œ","ðŸ«¶","ðŸ˜®","ðŸ˜˜","ðŸ¤©","ðŸ«€"]

    async def _open_comment_panel(self, cdp) -> bool:
        """Click icon comment Ä‘á»ƒ má»Ÿ panel. Tráº£ True náº¿u Ä‘Ã£ má»Ÿ sáºµn hoáº·c má»Ÿ thÃ nh cÃ´ng."""

        # â”€â”€ HÃ m check panel Ä‘Ã£ má»Ÿ â”€â”€
        async def _is_panel_open():
            return await cdp.evaluate("""
            (() => {
                // ForYou: comment panel má»Ÿ sáºµn bÃªn pháº£i (comment list hiá»ƒn thá»‹)
                const listSels = [
                    '[data-e2e="comment-list"]',
                    'div[class*="CommentListContainer"]',
                    'div[class*="comment-list"]',
                    'div[class*="DivCommentListContainer"]',
                    'div[class*="CommentContainer"]'
                ];
                for (const sel of listSels) {
                    const el = document.querySelector(sel);
                    if (el && el.getBoundingClientRect().width > 50) return 'list';
                }

                // Ã” nháº­p comment
                const inputSels = [
                    '[data-e2e="comment-input"]',
                    'div[contenteditable="true"][class*="comment" i]',
                    'div[contenteditable="true"][class*="Comment"]',
                    'div[contenteditable="true"][class*="DraftEditor"]'
                ];
                for (const sel of inputSels) {
                    const el = document.querySelector(sel);
                    if (el && el.getBoundingClientRect().width > 0) return 'input';
                }

                // Placeholder "Add comment..."
                const placeholders = document.querySelectorAll(
                    '[placeholder*="comment" i], [data-placeholder*="comment" i],' +
                    ' [placeholder*="bÃ¬nh luáº­n" i]'
                );
                for (const el of placeholders) {
                    if (el.getBoundingClientRect().width > 0) return 'placeholder';
                }

                return null;
            })()
            """)

        # â”€â”€ Check 1: panel Ä‘Ã£ má»Ÿ sáºµn? â”€â”€
        status = await _is_panel_open()
        if status:
            self.status_update.emit(f"ðŸ’¬ Comment panel Ä‘Ã£ má»Ÿ ({status})", "blue")
            return True

        # â”€â”€ Check 2: click icon comment â”€â”€
        self.status_update.emit("ðŸ’¬ Má»Ÿ comment panel...", "blue")
        icon_sels = [
            '[data-e2e="comment-icon"]',
            'span[data-e2e="comment-icon"]',
            'button[data-e2e="comment-icon"]',
            '[data-e2e="comment-count"]',
        ]
        for sel in icon_sels:
            pos = await self._get_center(cdp, sel)
            if pos:
                await self._human_move_and_click(cdp, *pos, "Click icon ðŸ’¬")
                await asyncio.sleep(random.uniform(2.0, 3.0))

                # Verify panel Ä‘Ã£ má»Ÿ
                status = await _is_panel_open()
                if status:
                    self.status_update.emit(f"ðŸ’¬ Panel Ä‘Ã£ má»Ÿ sau click ({status})", "green")
                    return True

        # â”€â”€ Check 3: Fallback â€” ForYou cÃ³ thá»ƒ hiá»ƒn thá»‹ comment text trá»±c tiáº¿p â”€â”€
        has_comments = await cdp.evaluate("""
        (() => {
            const cmts = document.querySelectorAll(
                '[data-e2e="comment-level-1"], [class*="CommentItem"],' +
                ' [class*="comment-item"], [class*="DivCommentItem"]'
            );
            return cmts.length > 0;
        })()
        """) or False

        if has_comments:
            self.status_update.emit("ðŸ’¬ Comment items hiá»‡n diá»‡n â€” panel má»Ÿ", "green")
            return True

        self.status_update.emit("âš ï¸ KhÃ´ng má»Ÿ Ä‘Æ°á»£c comment panel", "orange")
        return False

    async def _like_comments(self, cdp, video_idx: int):
        """Tháº£ tim ngáº«u nhiÃªn N comment (giá»›i háº¡n max_like_cmt)."""
        max_n = self.feed_settings.get('max_like_cmt', 5)
        n = random.randint(1, max(1, max_n))
        self.status_update.emit(f"â¤ï¸ Video #{video_idx}: Tháº£ tim {n} comment...", "blue")

        # Láº¥y danh sÃ¡ch icon tim cá»§a comment
        like_positions = await cdp.evaluate("""
        (() => {
            const btns = document.querySelectorAll(
                '[data-e2e="comment-like-icon"], [data-e2e="like-icon-comment"],'+
                ' span[class*="LikeIcon"], button[class*="like"][class*="comment"]'
            );
            const result = [];
            for (const b of btns) {
                const r = b.getBoundingClientRect();
                if (r.width > 0 && r.height > 0)
                    result.push({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
            }
            return result.slice(0, 20);
        })()
        """) or []

        picked = random.sample(like_positions, min(n, len(like_positions)))
        for pos in picked:
            if self._stop_flag:
                return
            await self._human_move_and_click(cdp, pos['x'], pos['y'], "Tim comment")
            await asyncio.sleep(random.uniform(1.0, 2.5))

    async def _view_more_replies(self, cdp, video_idx: int):
        """Click nÃºt 'View more replies' / 'Xem thÃªm' comment."""
        self.status_update.emit(f"ðŸ‘€ Video #{video_idx}: Xem thÃªm comment...", "blue")
        pos = await self._get_center_by_text(cdp, "View more replies")
        if not pos:
            pos = await self._get_center_by_text(cdp, "Xem thÃªm")
        if not pos:
            # TÃ¬m báº¥t ká»³ nÃºt "view more" kiá»ƒu generic
            pos = await cdp.evaluate("""
            (() => {
                const all = Array.from(document.querySelectorAll('*'));
                for (const el of all) {
                    if (el.innerText && (
                        el.innerText.includes('View more') ||
                        el.innerText.includes('replies') ||
                        el.innerText.includes('Xem thÃªm')
                    ) && el.childElementCount < 3) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0 && r.width < 400)
                            return {x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2)};
                    }
                }
                return null;
            })()
            """)
            if pos:
                pos = (pos['x'], pos['y'])

        if pos:
            await self._human_move_and_click(cdp, *pos, "View more replies")
            await asyncio.sleep(random.uniform(2.0, 3.5))

    async def _clone_comment(self, cdp, video_idx: int):
        """
        Clone 1 comment nhÆ° ngÆ°á»i tháº­t:
        1. LÆ°á»›t xem comment trong panel (scroll mÆ°á»£t)
        2. Thu tháº­p danh sÃ¡ch comment
        3. Nguá»“n clone: 70% tá»« video hiá»‡n táº¡i, 30% tá»« bank (video trÆ°á»›c)
        4. Biáº¿n thá»ƒ: 50/50 giá»¯ nguyÃªn hoáº·c thÃªm emoji
        5. Chá»‘ng trÃ¹ng láº·p + verify + detect rate limit
        """
        # â”€â”€ Rate limit cooldown? â”€â”€
        if self._comment_cooldown:
            self.status_update.emit(f"â³ Video #{video_idx}: Äang cooldown comment...", "orange")
            return

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        #  BÆ¯á»šC 1: LÆ°á»›t xem comment (giá»‘ng ngÆ°á»i tháº­t Ä‘á»c)
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        self.status_update.emit(f"ðŸ’¬ Video #{video_idx}: Äá»c comment...", "blue")

        # â˜… TÃ¬m vÃ¹ng comment panel (tá»a Ä‘á»™ thá»±c táº¿)
        panel_bounds = await cdp.evaluate("""
        (() => {
            const sels = [
                '[data-e2e="comment-list"]',
                'div[class*="CommentListContainer"]',
                'div[class*="DivCommentListContainer"]',
                'div[class*="CommentContainer"]'
            ];
            for (const sel of sels) {
                const el = document.querySelector(sel);
                if (el) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 50) return {
                        x: Math.round(r.x + r.width/2),
                        top: Math.round(r.top),
                        bottom: Math.round(r.bottom),
                        left: Math.round(r.left),
                        right: Math.round(r.right)
                    };
                }
            }
            // Fallback: vÃ¹ng bÃªn pháº£i (ForYou)
            return {x: 800, top: 100, bottom: 550, left: 680, right: 950};
        })()
        """) or {"x": 800, "top": 100, "bottom": 550, "left": 680, "right": 950}

        px = panel_bounds['x']
        p_top = panel_bounds['top']
        p_bot = panel_bounds['bottom']
        p_left = panel_bounds.get('left', 680)
        p_right = panel_bounds.get('right', 950)
        p_left, p_right = sorted((int(p_left), int(p_right)))
        p_top, p_bot = sorted((int(p_top), int(p_bot)))
        if (p_right - p_left) < 80:
            pad_x = max(20, (80 - (p_right - p_left)) // 2)
            p_left -= pad_x
            p_right += pad_x
        if (p_bot - p_top) < 160:
            pad_y = max(40, (160 - (p_bot - p_top)) // 2)
            p_top = max(0, p_top - pad_y)
            p_bot += pad_y

        # â•â•â• LÆ°á»›t Ä‘á»c comment tá»± nhiÃªn (lÃªn/xuá»‘ng xen káº½) â•â•â•
        # Giá»‘ng ngÆ°á»i tháº­t: scroll xuá»‘ng â†’ dá»«ng Ä‘á»c â†’ scroll lÃªn xem láº¡i â†’ xuá»‘ng tiáº¿p
        browse_actions = [
            ("down", 200, 400),   # Scroll xuá»‘ng â€” Ä‘á»c comment Ä‘áº§u
            ("pause", 0, 0),       # Dá»«ng Ä‘á»c 1-2s
            ("down", 250, 450),   # Xuá»‘ng tiáº¿p â€” Ä‘á»c thÃªm
            ("hover", 0, 0),       # Di chuá»™t vÃ o 1 comment (tÃ² mÃ²)
            ("up", 100, 250),     # Scroll LÃŠN â€” xem láº¡i comment trÆ°á»›c
            ("pause", 0, 0),       # Äá»c láº¡i
            ("down", 300, 500),   # Xuá»‘ng háº³n â€” xem comment má»›i
            ("hover", 0, 0),       # Di chuá»™t vÃ o comment khÃ¡c
        ]

        for idx_a, (action, scroll_min, scroll_max) in enumerate(browse_actions):
            if self._stop_flag:
                return
            if action == "up":
                action = "pause"

            if action == "down":
                # Scroll xuá»‘ng
                scroll_amount = random.randint(scroll_min, scroll_max)
                mx = _safe_randint(p_left + 20, p_right - 20)
                my = _safe_randint(p_top + 50, p_bot - 50)
                self.status_update.emit(f"ðŸ‘ï¸ Äá»c comment... â¬‡ï¸ scroll xuá»‘ng", "blue")
                await self._smooth_mouse_drift(cdp, mx, my)
                await cdp.evaluate(f"""
                (() => {{
                    const panels = Array.from(document.querySelectorAll(
                        '[data-e2e="comment-list"], div[class*="CommentListContainer"], ' +
                        'div[class*="DivCommentListContainer"], div[class*="CommentContainer"]'
                    ));
                    const panel = panels.find(el => {{
                        const r = el.getBoundingClientRect();
                        return r.width > 120 && r.height > 120;
                    }});
                    if (panel) panel.scrollTop += {scroll_amount};
                }})()
                """)
                await asyncio.sleep(random.uniform(1.2, 2.2))

            elif False and action == "up":
                # Scroll LÃŠN (giá»‘ng Ä‘ang xem láº¡i comment hay)
                scroll_amount = random.randint(scroll_min, scroll_max)
                mx = _safe_randint(p_left + 20, p_right - 20)
                my = _safe_randint(p_top + 50, p_bot - 50)
                self.status_update.emit(f"ðŸ‘ï¸ Xem láº¡i comment... â¬†ï¸ scroll lÃªn", "blue")
                await self._smooth_mouse_drift(cdp, mx, my)
                await asyncio.sleep(0)
                await asyncio.sleep(random.uniform(1.2, 2.0))

            elif action == "hover":
                # Di chuá»™t vÃ o 1 comment cá»¥ thá»ƒ (tÃ² mÃ² Ä‘á»c)
                hx = _safe_randint(p_left + 30, p_right - 30)
                hy = _safe_randint(p_top + 80, p_bot - 100)
                self.status_update.emit(f"ðŸ–±ï¸ Xem 1 comment...", "blue")
                await self._smooth_mouse_drift(cdp, hx, hy)
                await asyncio.sleep(random.uniform(1.0, 1.8))

            elif action == "pause":
                # Dá»«ng Ä‘á»c (máº¯t dá»«ng á»Ÿ 1 comment)
                self.status_update.emit(f"ðŸ‘ï¸ Äang Ä‘á»c comment...", "blue")
                await asyncio.sleep(random.uniform(1.2, 2.2))

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        #  BÆ¯á»šC 2: Thu tháº­p comment tá»« video hiá»‡n táº¡i
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        self.status_update.emit(f"ðŸ“ Video #{video_idx}: Láº¥y danh sÃ¡ch comment...", "blue")

        current_comments = await cdp.evaluate("""
        (() => {
            const sels = [
                '[data-e2e="comment-level-1"] [data-e2e="comment-text"]',
                '[data-e2e="comment-level-1"] p',
                '[data-e2e="comment-item"] span',
                '[class*="CommentContentText"]',
                '[class*="CommentText"]',
                '[class*="comment-text"]',
                '[class*="CommentItemText"]'
            ];
            const texts = new Set();
            for (const sel of sels) {
                const items = document.querySelectorAll(sel);
                for (const el of items) {
                    const t = (el.innerText || '').trim();
                    // Chá»‰ láº¥y text comment thá»±c (2-200 kÃ½ tá»±, khÃ´ng pháº£i username/time)
                    if (t.length > 2 && t.length < 200 &&
                        !t.match(/^\\d+[smhd]?\\s*(ago)?$/) &&   // Bá» "2h ago"
                        !t.startsWith('@') &&                   // Bá» @username
                        !t.match(/^Reply$/i) &&                 // Bá» "Reply"
                        !t.match(/^View \\d+ replies/i))         // Bá» "View 14 replies"
                        texts.add(t);
                }
                if (texts.size >= 10) break;
            }
            return [...texts].slice(0, 30);
        })()
        """) or []

        self.status_update.emit(
            f"ðŸ“ Video #{video_idx}: {len(current_comments)} comment | Bank: {len(self._comment_bank)}",
            "blue"
        )

        # LÆ°u vÃ o bank Ä‘á»ƒ dÃ¹ng cho video sau (giá»¯ tá»‘i Ä‘a 100)
        for c in current_comments:
            if c not in self._comment_bank:
                self._comment_bank.append(c)
        if len(self._comment_bank) > 100:
            self._comment_bank = self._comment_bank[-100:]

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        #  BÆ¯á»šC 3: Chá»n nguá»“n clone (video hiá»‡n táº¡i vs bank)
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        chosen = None
        source = ""

        # Lá»c bá» comment Ä‘Ã£ dÃ¹ng (chá»‘ng trÃ¹ng láº·p)
        avail_current = [c for c in current_comments if c not in self._comment_history]
        avail_bank = [c for c in self._comment_bank
                      if c not in self._comment_history and c not in current_comments]

        # Quyáº¿t Ä‘á»‹nh nguá»“n: 70% video hiá»‡n táº¡i, 30% bank (náº¿u cÃ³)
        use_bank = (random.random() < 0.3) and len(avail_bank) > 0

        if use_bank:
            chosen = random.choice(avail_bank)
            source = "ðŸ“¦ Tá»« bank (video trÆ°á»›c)"
        elif avail_current:
            chosen = random.choice(avail_current)
            source = "ðŸŽ¬ Tá»« video hiá»‡n táº¡i"
        elif avail_bank:
            # Fallback: video hiá»‡n táº¡i háº¿t comment má»›i â†’ dÃ¹ng bank
            chosen = random.choice(avail_bank)
            source = "ðŸ“¦ Fallback bank"
        else:
            self.status_update.emit(
                f"âš ï¸ Video #{video_idx}: Háº¿t comment chÆ°a dÃ¹ng (bank + hiá»‡n táº¡i)", "orange"
            )
            return

        # ÄÃ¡nh dáº¥u Ä‘Ã£ dÃ¹ng
        self._comment_history.add(chosen)

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        #  BÆ¯á»šC 4: Biáº¿n thá»ƒ (50/50 thÃªm emoji)
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        if random.random() < 0.5:
            chosen = chosen + " " + random.choice(self.EMOJIS)
            self.status_update.emit(f"âœï¸ {source}: {chosen[:30]}... +emoji", "blue")
        else:
            self.status_update.emit(f"âœï¸ {source}: {chosen[:30]}...", "blue")

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        #  BÆ¯á»šC 5: Click Ã´ comment vÃ  gÃµ
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        await asyncio.sleep(random.uniform(0.8, 1.5))

        # TÃ¬m Ã´ "Add comment..." â€” TikTok cÃ³ nhiá»u variant
        input_pos = await cdp.evaluate("""
        (() => {
            // CÃ¡ch 1: data-e2e comment-input (Ã´ nháº­p chÃ­nh)
            const sels = [
                '[data-e2e="comment-input"]',
                '[data-e2e="comment-input"] div[contenteditable="true"]',
                '[data-e2e="comment-input"] [contenteditable="true"]',
                'div[class*="DraftEditor"][contenteditable="true"]',
                'div[contenteditable="true"][class*="comment"]',
                'div[contenteditable="true"][class*="Comment"]'
            ];
            for (const sel of sels) {
                const el = document.querySelector(sel);
                if (el) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0)
                        return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)};
                }
            }

            // CÃ¡ch 2: TÃ¬m placeholder "Add comment"
            const allEditable = document.querySelectorAll(
                '[contenteditable], [role="textbox"], textarea, input[type="text"]'
            );
            for (const el of allEditable) {
                const r = el.getBoundingClientRect();
                const ph = el.getAttribute('placeholder') || el.getAttribute('data-placeholder') || '';
                const text = (el.innerText || el.textContent || '').toLowerCase();
                if (r.width > 0 && r.height > 0 && r.y > 300 && (
                    ph.toLowerCase().includes('comment') ||
                    ph.toLowerCase().includes('bÃ¬nh luáº­n') ||
                    text.includes('add comment') ||
                    text.includes('thÃªm bÃ¬nh luáº­n')
                )) {
                    return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)};
                }
            }

            // CÃ¡ch 3: Ã” contenteditable á»Ÿ dÆ°á»›i cÃ¹ng comment panel (y > 500)
            for (const el of allEditable) {
                const r = el.getBoundingClientRect();
                if (r.width > 50 && r.height > 0 && r.y > 500)
                    return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)};
            }
            return null;
        })()
        """)

        if not input_pos:
            self.status_update.emit("âš ï¸ KhÃ´ng tÃ¬m tháº¥y Ã´ comment", "orange")
            return

        # Click vÃ o Ã´ comment
        await self._human_move_and_click(cdp, input_pos['x'], input_pos['y'], "Click Ã´ comment")
        await asyncio.sleep(random.uniform(0.8, 1.2))

        # â˜… Sau khi click, TikTok chuyá»ƒn placeholder â†’ contenteditable
        # Chá» Ã´ focus vÃ  sáºµn sÃ ng nháº­n text
        for _ in range(3):
            focused = await cdp.evaluate("""
            (() => {
                const active = document.activeElement;
                if (active && (active.contentEditable === 'true' || active.tagName === 'TEXTAREA'))
                    return true;
                // Thá»­ focus trá»±c tiáº¿p
                const ce = document.querySelector(
                    '[data-e2e="comment-input"] [contenteditable="true"],' +
                    ' div[contenteditable="true"][class*="DraftEditor"],' +
                    ' div[contenteditable="true"][class*="comment"],' +
                    ' div[contenteditable="true"][class*="Comment"]'
                );
                if (ce) { ce.focus(); ce.click(); return true; }
                return false;
            })()
            """) or False
            if focused:
                break
            # Click láº¡i náº¿u chÆ°a focus
            await self._human_move_and_click(cdp, input_pos['x'], input_pos['y'], "Re-click Ã´ comment")
            await asyncio.sleep(0.5)

        await asyncio.sleep(0.3)

        # GÃµ tá»«ng kÃ½ tá»± (mÃ´ phá»ng ngÆ°á»i tháº­t)
        await cdp.type_text(chosen, delay=random.randint(50, 110))
        await asyncio.sleep(random.uniform(0.5, 0.8))

        # â˜… Verify text Ä‘Ã£ Ä‘Æ°á»£c nháº­p chÆ°a
        has_text = await cdp.evaluate("""
        (() => {
            const ce = document.querySelector(
                '[data-e2e="comment-input"] [contenteditable="true"],' +
                ' div[contenteditable="true"][class*="DraftEditor"],' +
                ' div[contenteditable="true"][class*="comment"],' +
                ' div[contenteditable="true"][class*="Comment"]'
            );
            if (ce) {
                const t = (ce.innerText || ce.textContent || '').trim();
                return t.length > 0;
            }
            return false;
        })()
        """) or False

        # Náº¿u type_text khÃ´ng hoáº¡t Ä‘á»™ng â†’ fallback insertText
        if not has_text:
            self.status_update.emit("ðŸ”„ Fallback: insertText...", "blue")
            await cdp.send("Input.dispatchKeyEvent", {
                "type": "keyDown", "key": "a", "code": "KeyA",
                "modifiers": 2  # Ctrl
            })
            await asyncio.sleep(0.1)
            await cdp.send("Input.dispatchKeyEvent", {
                "type": "keyUp", "key": "a", "code": "KeyA"
            })
            await cdp.send("Input.insertText", {"text": chosen})
            await asyncio.sleep(0.5)

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        #  BÆ¯á»šC 6: Gá»­i comment (Enter / Click nÃºt Post)
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        self.status_update.emit(f"ðŸ“¤ Video #{video_idx}: Gá»­i comment...", "blue")
        comment_sent = False

        # CÃ¡ch 1: TÃ¬m nÃºt Post/ÄÄƒng vÃ  click
        send_pos = await cdp.evaluate("""
        (() => {
            // NÃºt Post chÃ­nh thá»©c
            const sels = [
                '[data-e2e="comment-post"]',
                'div[data-e2e="comment-post"]',
                'button[data-e2e="comment-post"]',
                '[class*="PostButton"]',
                '[class*="postButton"]',
                '[class*="DivPostButton"]',
                'div[class*="CommentPost"]',
                'button[class*="CommentPost"]'
            ];
            for (const sel of sels) {
                const el = document.querySelector(sel);
                if (el) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0)
                        return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), method: 'selector'};
                }
            }
            // TÃ¬m nÃºt cÃ³ text "Post" hoáº·c "ÄÄƒng" gáº§n Ã´ comment
            const btns = document.querySelectorAll('button, div[role="button"], span[role="button"]');
            for (const btn of btns) {
                const text = (btn.textContent || '').trim();
                const r = btn.getBoundingClientRect();
                if (r.width > 0 && r.height > 0 && r.y > 400) {
                    if (text === 'Post' || text === 'ÄÄƒng' || text === 'Gá»­i' || text === 'Send')
                        return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), method: 'text'};
                }
            }
            // NÃºt icon gá»­i (arrow/send icon) á»Ÿ cuá»‘i Ã´ comment
            const icons = document.querySelectorAll(
                'svg[class*="send"], svg[class*="Send"], svg[class*="post"], svg[class*="Post"],' +
                ' [class*="SendIcon"], [class*="sendIcon"]'
            );
            for (const icon of icons) {
                const r = icon.getBoundingClientRect();
                if (r.width > 0 && r.y > 400)
                    return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), method: 'icon'};
            }
            return null;
        })()
        """)

        if send_pos:
            self.status_update.emit(f"ðŸ“¤ Click nÃºt Post ({send_pos.get('method','')})", "blue")
            await self._human_move_and_click(cdp, send_pos['x'], send_pos['y'], "Post comment")
            comment_sent = True
        else:
            # CÃ¡ch 2: Enter key trÃªn contenteditable â€” gá»­i comment
            self.status_update.emit("ðŸ“¤ Enter Ä‘á»ƒ gá»­i comment...", "blue")

            # Focus láº¡i Ã´ comment trÆ°á»›c khi Enter
            await cdp.evaluate("""
            (() => {
                const ce = document.querySelector(
                    '[data-e2e="comment-input"] [contenteditable="true"],' +
                    ' div[contenteditable="true"][class*="DraftEditor"],' +
                    ' div[contenteditable="true"][class*="comment"],' +
                    ' div[contenteditable="true"][class*="Comment"]'
                );
                if (ce) ce.focus();
            })()
            """)
            await asyncio.sleep(0.2)

            # Gá»­i Enter Ä‘áº§y Ä‘á»§ tham sá»‘
            for key_type in ["rawKeyDown", "keyDown"]:
                await cdp.send("Input.dispatchKeyEvent", {
                    "type": key_type,
                    "key": "Enter",
                    "code": "Enter",
                    "windowsVirtualKeyCode": 13,
                    "nativeVirtualKeyCode": 13,
                    "text": "\r",
                    "unmodifiedText": "\r"
                })
                await asyncio.sleep(0.02)
            await cdp.send("Input.dispatchKeyEvent", {
                "type": "keyUp", "key": "Enter", "code": "Enter",
                "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13
            })
            comment_sent = True

        # CÃ¡ch 3 (backup): JS Enter event trá»±c tiáº¿p trÃªn contenteditable
        if comment_sent:
            await asyncio.sleep(0.3)
            # Kiá»ƒm tra náº¿u text váº«n cÃ²n trong Ã´ â†’ Enter chÆ°a gá»­i Ä‘Æ°á»£c â†’ dispatch JS event
            still_has_text = await cdp.evaluate("""
            (() => {
                const ce = document.querySelector(
                    '[data-e2e="comment-input"] [contenteditable="true"],' +
                    ' div[contenteditable="true"][class*="DraftEditor"],' +
                    ' div[contenteditable="true"][class*="comment"],' +
                    ' div[contenteditable="true"][class*="Comment"]'
                );
                if (ce) {
                    const t = (ce.innerText || ce.textContent || '').trim();
                    if (t.length > 0) {
                        // Text váº«n cÃ²n â†’ Enter chÆ°a gá»­i â†’ dispatch JS event
                        ce.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true}));
                        ce.dispatchEvent(new KeyboardEvent('keypress', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true}));
                        ce.dispatchEvent(new KeyboardEvent('keyup', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true}));
                        return 'retried';
                    }
                    return 'sent';
                }
                return 'no_input';
            })()
            """)
            if still_has_text == 'retried':
                self.status_update.emit("ðŸ“¤ Retry Enter (JS event)...", "blue")

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        #  BÆ¯á»šC 7: Verify + Detect Rate Limit
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        await asyncio.sleep(random.uniform(2.0, 3.0))

        # Kiá»ƒm tra rate limit trÆ°á»›c
        rate_limited = await cdp.evaluate("""
        (() => {
            const body = (document.body.innerText || '').toLowerCase();
            const phrases = [
                'commenting too fast', 'too frequently', 'try again later',
                'comment failed', 'unable to post', "can't post",
                'bÃ¬nh luáº­n quÃ¡ nhanh', 'thá»­ láº¡i sau', 'bÃ¬nh luáº­n tháº¥t báº¡i'
            ];
            for (const p of phrases) {
                if (body.includes(p)) return true;
            }
            // Kiá»ƒm tra toast/popup lá»—i
            const toasts = document.querySelectorAll(
                '[class*="Toast"], [class*="toast"], [role="alert"],' +
                ' [class*="Snackbar"], [class*="Notification"]'
            );
            for (const t of toasts) {
                const txt = (t.innerText || '').toLowerCase();
                if (txt.includes('fast') || txt.includes('frequently') ||
                    txt.includes('failed') || txt.includes('nhanh'))
                    return true;
            }
            return false;
        })()
        """) or False

        if rate_limited:
            cooldown = random.randint(60, 120)
            self.status_update.emit(
                f"ðŸš« Video #{video_idx}: TikTok rate limit! Táº¡m dá»«ng comment {cooldown}s...", "orange"
            )
            self._comment_cooldown = True
            await asyncio.sleep(cooldown)
            self._comment_cooldown = False
            return

        # Verify: kiá»ƒm tra comment Ä‘Ã£ xuáº¥t hiá»‡n trong DOM chÆ°a
        check_text = chosen[:20].replace("'", "\\'")
        verified = await cdp.evaluate(f"""
        (() => {{
            const items = document.querySelectorAll(
                '[data-e2e="comment-level-1"] [data-e2e="comment-text"],' +
                ' [class*="CommentText"], [class*="comment-text"],' +
                ' [class*="CommentContentText"]'
            );
            for (const el of items) {{
                const t = (el.innerText || '').trim();
                if (t.includes('{check_text}')) return true;
            }}
            return false;
        }})()

        """) or False

        if verified:
            self.status_update.emit(f"âœ… Video #{video_idx}: Comment thÃ nh cÃ´ng!", "green")
        else:
            self.status_update.emit(
                f"âš ï¸ Video #{video_idx}: ÄÃ£ gá»­i nhÆ°ng chÆ°a tháº¥y xuáº¥t hiá»‡n (cÃ³ thá»ƒ bá»‹ lá»c)", "orange"
            )

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    #  FEED INTERACTION â€” Luá»“ng chÃ­nh
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

    async def _do_feed_interaction(self, cdp):
        """NuÃ´i nick Feed â€” tÆ°Æ¡ng tÃ¡c nhÆ° ngÆ°á»i tháº­t."""
        try:
            feed_type = int(self.feed_settings.get('feed_type', 1))
        except (TypeError, ValueError):
            feed_type = 1
        if feed_type == 0:
            self.status_update.emit("âš ï¸ Feed Ä‘ang táº¯t trong cÃ i Ä‘áº·t, khÃ´ng cháº¡y tÆ°Æ¡ng tÃ¡c.", "orange")
            return False

        if not await self._wait_captcha_clear_for_action(cdp, "Feed start"):
            return False

        # Chá»‰ persist session hiá»‡n táº¡i; khÃ´ng bÆ¡m cookie DB vÃ o phiÃªn GoLogin Ä‘ang cháº¡y.
        await self._persist_tiktok_cookies(cdp)

        if feed_type == 1:
            # â”€â”€ Kiá»ƒm tra Ä‘Ã£ á»Ÿ foryou chÆ°a â€” náº¿u rá»“i thÃ¬ KHÃ”NG click Home â”€â”€
            current_path = await cdp.evaluate("location.pathname") or ""
            if current_path in ('/', '/foryou') or current_path.startswith('/foryou'):
                self.status_update.emit("ðŸ  ÄÃ£ á»Ÿ trang For You â€” báº¯t Ä‘áº§u xem video", "blue")
            else:
                # Chá»‰ click Home khi CHÆ¯A á»Ÿ foryou
                self.status_update.emit("ðŸ  Click vÃ o icon Home...", "blue")
                home_pos = await cdp.evaluate("""
                (() => {
                    const hrefs = ['/', '/foryou', '/?'];
                    for (const href of hrefs) {
                        const el = document.querySelector(`a[href="${href}"], a[href^="${href}?"]`);
                        if (el) {
                            const r = el.getBoundingClientRect();
                            if (r.width > 0 && r.height > 0)
                                return {x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2)};
                        }
                    }
                    const e2e = document.querySelector('[data-e2e="nav-home"]');
                    if (e2e) {
                        const r = e2e.getBoundingClientRect();
                        if (r.width > 0) return {x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2)};
                    }
                    const navLinks = document.querySelectorAll(
                        '[class*="sidebar"] a, [class*="SideBar"] a, [class*="SideNav"] a, nav a'
                    );
                    for (const a of navLinks) {
                        const href = (a.getAttribute('href') || '');
                        if (href === '/' || href.startsWith('/foryou') || href.startsWith('/?')) {
                            const r = a.getBoundingClientRect();
                            if (r.width > 0 && r.height > 0)
                                return {x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2)};
                        }
                    }
                    return null;
                })()
                """)
                if home_pos and isinstance(home_pos, dict):
                    home_pos = (home_pos['x'], home_pos['y'])

                if home_pos:
                    await self._human_move_and_click(cdp, *home_pos, "Click icon ðŸ  Home")
                    await asyncio.sleep(random.uniform(2.5, 4.0))
                else:
                    self.status_update.emit("âš ï¸ KhÃ´ng tÃ¬m tháº¥y icon Home", "orange")
                    await self._navigate_like_human(cdp, "tiktok.com/foryou", wait=random.uniform(3.0, 4.5))

        else:
            # â”€â”€ Click icon ðŸ§­ Explore (la bÃ n) trÃªn sidebar â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            self.status_update.emit("ðŸ§­ Click vÃ o icon Explore...", "blue")
            explore_pos = await cdp.evaluate("""
            (() => {
                // TÃ¬m link Explore trong sidebar theo href
                const el = document.querySelector('a[href="/explore"], a[href^="/explore?"]');
                if (el) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0)
                        return {x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2)};
                }
                // Fallback: data-e2e
                const e2e = document.querySelector('[data-e2e="nav-explore"]');
                if (e2e) {
                    const r = e2e.getBoundingClientRect();
                    if (r.width > 0) return {x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2)};
                }
                // Fallback: quÃ©t sidebar tÃ¬m link /explore
                const navLinks = document.querySelectorAll(
                    '[class*="sidebar"] a, [class*="SideBar"] a, [class*="SideNav"] a, nav a'
                );
                for (const a of navLinks) {
                    const href = (a.getAttribute('href') || '');
                    if (href.startsWith('/explore')) {
                        const r = a.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0)
                            return {x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2)};
                    }
                }
                return null;
            })()
            """)
            if explore_pos and isinstance(explore_pos, dict):
                explore_pos = (explore_pos['x'], explore_pos['y'])

            if explore_pos:
                await self._human_move_and_click(cdp, *explore_pos, "Click icon ðŸ§­ Explore")
                await asyncio.sleep(random.uniform(2.5, 4.0))
            else:
                self.status_update.emit("âš ï¸ KhÃ´ng tÃ¬m tháº¥y icon Explore", "orange")
                await self._navigate_like_human(cdp, "tiktok.com/explore", wait=random.uniform(3.0, 4.5))



        # Sá»‘ video sáº½ xem
        view_min = int(self.feed_settings.get('view_min', 3) or 3)
        view_max = int(self.feed_settings.get('view_max', 5) or 5)
        if view_min > view_max:
            view_min, view_max = view_max, view_min
        n_videos = random.randint(view_min, view_max)
        self.status_update.emit(f"ðŸ“º Sáº½ xem {n_videos} video...", "blue")

        # Tá»•ng thá»i gian tá»‘i thiá»ƒu (giÃ¢y) náº¿u báº­t tÃ¹y chá»n thá»i gian
        use_time  = self.feed_settings.get('use_time', False)
        time_min = int(self.feed_settings.get('time_min', 3) or 3)
        time_max = int(self.feed_settings.get('time_max', 5) or 5)
        if time_min > time_max:
            time_min, time_max = time_max, time_min
        total_time_target = random.randint(time_min * 60, time_max * 60) if use_time else 0
        session_elapsed = 0.0

        if feed_type == 2:
            # â•â•â•â• EXPLORE: Grid layout â†’ click thumbnail â†’ xem â†’ back â•â•â•â•
            feed_completed = await self._watch_explore_feed(cdp, n_videos, use_time, total_time_target)
        else:
            # â•â•â•â• FOR YOU: Cuá»™n dá»c tá»«ng video â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            feed_completed = await self._watch_foryou_feed(cdp, n_videos, use_time, total_time_target)

        if feed_completed:
            self.status_update.emit("âœ… Xong Feed!", "green")
        else:
            self.status_update.emit("âš ï¸ Feed dá»«ng sá»›m, chÆ°a Ä‘á»§ má»¥c tiÃªu Ä‘Ã£ cÃ i.", "orange")
        return bool(feed_completed)

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    #  FOR YOU: cuá»™n dá»c tá»«ng video
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

    async def _get_current_feed_signature(self, cdp) -> str:
        """Return a stable-ish signature for the currently visible TikTok item."""
        try:
            return await cdp.evaluate(r"""
            (() => {
                const visibleArea = (r) => {
                    const left = Math.max(0, r.left);
                    const top = Math.max(0, r.top);
                    const right = Math.min(window.innerWidth, r.right);
                    const bottom = Math.min(window.innerHeight, r.bottom);
                    return Math.max(0, right - left) * Math.max(0, bottom - top);
                };
                const videos = Array.from(document.querySelectorAll('video')).map(v => {
                    const r = v.getBoundingClientRect();
                    return {v, r, area: visibleArea(r)};
                }).filter(x => x.area > 12000).sort((a, b) => b.area - a.area);
                const active = videos.length ? videos[0] : null;
                const v = active ? active.v : null;
                const r = active ? active.r : null;
                const videoSrc = v ? (v.currentSrc || v.src || v.poster || '') : '';
                const rectKey = r ? [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)].join(',') : '';
                const owner = v ? v.closest(
                    'article,[data-e2e*="video"],div[class*="DivItemContainer"],div[class*="DivVideo"],div[class*="Feed"]'
                ) : null;
                const itemLinks = owner ? Array.from(owner.querySelectorAll('a[href*="/video/"],a[href*="/@"]'))
                    .map(a => a.href || '').filter(Boolean).slice(0, 6).join(',') : '';
                const itemText = ((owner && owner.innerText) || '')
                    .replace(/\s+/g, ' ')
                    .trim()
                    .slice(0, 450);
                const visibleImg = Array.from(document.querySelectorAll('img')).find(img => {
                    const r = img.getBoundingClientRect();
                    return r.width > 150 && r.height > 150 && r.top < window.innerHeight && r.bottom > 0;
                });
                const imgSrc = visibleImg ? (visibleImg.currentSrc || visibleImg.src || '') : '';
                const bodyText = ((document.body && document.body.innerText) || '')
                    .replace(/\s+/g, ' ')
                    .trim()
                    .slice(0, 450);
                return [location.pathname, location.search, videoSrc, rectKey, itemLinks, imgSrc, itemText || bodyText].join('|');
            })()
            """) or ""
        except Exception:
            return ""

    async def _is_foryou_feed_usable(self, cdp) -> bool:
        try:
            return bool(await cdp.evaluate(r"""
            (() => {
                const path = location.pathname || '';
                if (path.includes('/video/')) return false;
                const visibleArea = (r) => {
                    const left = Math.max(0, r.left);
                    const top = Math.max(0, r.top);
                    const right = Math.min(window.innerWidth, r.right);
                    const bottom = Math.min(window.innerHeight, r.bottom);
                    return Math.max(0, right - left) * Math.max(0, bottom - top);
                };
                const active = Array.from(document.querySelectorAll('video')).map(v => {
                    const r = v.getBoundingClientRect();
                    return {r, area: visibleArea(r)};
                }).filter(x => x.area > 12000).sort((a, b) => b.area - a.area)[0];
                if (!active) return false;
                const r = active.r;
                return r.width > 120 && r.height > 120 && r.bottom > 80 && r.top < window.innerHeight - 80;
            })()
            """))
        except Exception:
            return False

    async def _get_current_feed_identity(self, cdp) -> str:
        """Return a stable identity for the visible feed item, without layout-only data."""
        try:
            return await cdp.evaluate(r"""
            (() => {
                const visibleArea = (r) => {
                    const left = Math.max(0, r.left);
                    const top = Math.max(0, r.top);
                    const right = Math.min(window.innerWidth, r.right);
                    const bottom = Math.min(window.innerHeight, r.bottom);
                    return Math.max(0, right - left) * Math.max(0, bottom - top);
                };
                const active = Array.from(document.querySelectorAll('video')).map(v => {
                    const r = v.getBoundingClientRect();
                    return {v, r, area: visibleArea(r)};
                }).filter(x => x.area > 12000).sort((a, b) => b.area - a.area)[0];
                const v = active ? active.v : null;
                const owner = v ? v.closest(
                    'article,[data-e2e*="video"],div[class*="DivItemContainer"],div[class*="DivVideo"],div[class*="Feed"]'
                ) : null;
                const videoSrc = v ? (v.currentSrc || v.src || v.poster || '') : '';
                const itemLinks = owner ? Array.from(owner.querySelectorAll('a[href*="/video/"],a[href*="/@"]'))
                    .map(a => a.href || '').filter(Boolean).slice(0, 8).join(',') : '';
                const itemText = ((owner && owner.innerText) || '')
                    .replace(/\s+/g, ' ')
                    .trim()
                    .slice(0, 500);
                const visibleImg = Array.from(document.querySelectorAll('img')).find(img => {
                    const r = img.getBoundingClientRect();
                    return r.width > 150 && r.height > 150 && r.top < window.innerHeight && r.bottom > 0;
                });
                const imgSrc = visibleImg ? (visibleImg.currentSrc || visibleImg.src || '') : '';
                const detailPath = (location.pathname || '').includes('/video/') ? location.pathname : '';
                return [detailPath, videoSrc, itemLinks, imgSrc, itemText].join('|').slice(0, 1400);
            })()
            """) or ""
        except Exception:
            return ""

    async def _get_safe_feed_focus_point(self, cdp):
        """Pick a click point inside the visible video that avoids controls and overlays."""
        try:
            return await cdp.evaluate(r"""
            (() => {
                const visibleArea = (r) => {
                    const left = Math.max(0, r.left);
                    const top = Math.max(0, r.top);
                    const right = Math.min(window.innerWidth, r.right);
                    const bottom = Math.min(window.innerHeight, r.bottom);
                    return Math.max(0, right - left) * Math.max(0, bottom - top);
                };
                const active = Array.from(document.querySelectorAll('video')).map(v => {
                    const r = v.getBoundingClientRect();
                    return {v, r, area: visibleArea(r)};
                }).filter(x => x.area > 12000).sort((a, b) => b.area - a.area)[0];

                const isBadPoint = (x, y) => {
                    if (x < 2 || y < 2 || x > window.innerWidth - 2 || y > window.innerHeight - 2) return true;
                    const el = document.elementFromPoint(x, y);
                    if (!el) return false;
                    const bad = el.closest(
                        'button,a,input,textarea,select,[role="button"],[role="menu"],[role="menuitem"],' +
                        '[contenteditable="true"],[data-e2e*="like"],[data-e2e*="comment"],[data-e2e*="share"],' +
                        '[data-e2e*="favorite"],[data-e2e*="more"],[class*="Action"],[class*="Comment"],' +
                        '[class*="Share"],[class*="Menu"],[class*="Popover"],[class*="Popup"],[class*="Modal"]'
                    );
                    return !!bad;
                };

                if (active) {
                    const r = active.r;
                    const width = Math.max(1, r.width);
                    const height = Math.max(1, r.height);
                    const wide = width / height > 1.15;
                    const xs = wide ? [0.34, 0.40, 0.46, 0.30, 0.52] : [0.42, 0.36, 0.48, 0.32, 0.54];
                    const ys = wide ? [0.58, 0.50, 0.66, 0.42] : [0.50, 0.58, 0.42, 0.66];
                    for (const py of ys) {
                        for (const px of xs) {
                            const x = Math.round(r.left + width * px);
                            const y = Math.round(r.top + height * py);
                            if (!isBadPoint(x, y)) return {x, y, source: 'video'};
                        }
                    }
                }

                const fallback = [
                    [0.38, 0.56], [0.34, 0.50], [0.42, 0.62],
                    [0.30, 0.48], [0.46, 0.54]
                ];
                for (const [px, py] of fallback) {
                    const x = Math.round(window.innerWidth * px);
                    const y = Math.round(window.innerHeight * py);
                    if (!isBadPoint(x, y)) return {x, y, source: 'viewport'};
                }
                return {x: Math.round(window.innerWidth * 0.38), y: Math.round(window.innerHeight * 0.55), source: 'last'};
            })()
            """)
        except Exception:
            return None

    async def _feed_scroll_down(self, cdp, intensity: int = 1):
        """Scroll to the next feed item from a safe point; no JS synthetic wheel fallback."""
        self._native_focus_embedded_browser(click_content=False)
        point = await self._get_safe_feed_focus_point(cdp)
        if isinstance(point, dict):
            x = int(point.get("x") or self.container_width // 2)
            y = int(point.get("y") or self.container_height // 2)
        else:
            x = int(self.container_width * 0.42)
            y = int(self.container_height * 0.55)
        try:
            await cdp.evaluate("""
            (() => {
                try { if (document.activeElement) document.activeElement.blur(); } catch(e) {}
                try {
                    window.focus();
                    document.body.setAttribute('tabindex', '-1');
                    document.body.focus();
                } catch(e) {}
            })()
            """)
        except Exception:
            pass
        sign = -1 if getattr(self, "_feed_scroll_delta_sign", 1) < 0 else 1
        delta = sign * random.randint(900, 1350) * max(1, int(intensity))
        await cdp.scroll(x, y, 0, delta)

    async def _close_comment_panel_and_focus_video(self, cdp):
        """Close comment UI if possible, then focus the video area before moving next."""
        self._native_focus_embedded_browser(click_content=True)
        async def _feed_state():
            try:
                return await cdp.evaluate(r"""
                (() => {
                    const path = location.pathname || '';
                    const commentOpen = !!document.querySelector(
                        '[data-e2e="comment-list"], div[class*="CommentListContainer"], ' +
                        'div[class*="DivCommentListContainer"], div[class*="CommentContainer"], ' +
                        '[data-e2e="comment-input"], div[contenteditable="true"][class*="comment" i], ' +
                        'div[contenteditable="true"][class*="Comment"], div[contenteditable="true"][class*="DraftEditor"]'
                    );
                    const overlayOpen = Array.from(document.querySelectorAll(
                        '[role="menu"], [role="dialog"], div[class*="Menu"], div[class*="Popover"], ' +
                        'div[class*="Popup"], div[class*="Modal"], div[class*="DivVideoSetting"]'
                    )).some(el => {
                        const r = el.getBoundingClientRect();
                        return r.width > 80 && r.height > 60 && r.bottom > 0 && r.right > 0 &&
                            r.top < window.innerHeight && r.left < window.innerWidth;
                    });
                    const hasVideo = !!document.querySelector('video');
                    return {path, commentOpen, overlayOpen, hasVideo};
                })()
                """) or {}
            except Exception:
                return {}

        async def _press_escape():
            try:
                await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape"})
                await asyncio.sleep(0.05)
                await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape", "code": "Escape"})
                await asyncio.sleep(random.uniform(0.4, 0.7))
            except Exception:
                pass

        state = await _feed_state()
        if state.get("commentOpen") or state.get("overlayOpen"):
            await _press_escape()
            state = await _feed_state()
            if state.get("commentOpen") or state.get("overlayOpen"):
                await _press_escape()

        state = await _feed_state()
        try:
            close_pos = await cdp.evaluate(r"""
            (() => {
                const isVisible = (el) => {
                    const r = el.getBoundingClientRect();
                    return r.width > 18 && r.height > 18 && r.top >= 0 && r.left >= 0;
                };
                const candidates = Array.from(document.querySelectorAll(
                    'button[aria-label*="Close" i], button[aria-label*="ÄÃ³ng" i], ' +
                    'div[role="button"][aria-label*="Close" i], div[role="button"][aria-label*="ÄÃ³ng" i], ' +
                    'button, div[role="button"]'
                ));
                for (const el of candidates) {
                    if (!isVisible(el)) continue;
                    const r = el.getBoundingClientRect();
                    const txt = ((el.innerText || el.textContent || el.getAttribute('aria-label') || '') + '').trim().toLowerCase();
                    const looksClose = txt === 'x' || txt === 'Ã—' || txt.includes('close') || txt.includes('Ä‘Ã³ng') || txt.includes('dong');
                    if (r.x > window.innerWidth * 0.55 && r.y < window.innerHeight * 0.45 && (looksClose || r.width <= 60))
                        return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
                }
                return null;
            })()
            """)
            if close_pos and (state.get("commentOpen") or state.get("overlayOpen")):
                await self._human_move_and_click(
                    cdp, int(close_pos["x"]), int(close_pos["y"]), "ÄÃ³ng comment panel"
                )
                await asyncio.sleep(random.uniform(0.6, 1.0))
        except Exception:
            pass

        state = await _feed_state()
        if state.get("commentOpen"):
            await _press_escape()

        state = await _feed_state()
        if "/video/" in str(state.get("path") or ""):
            self.status_update.emit("â†©ï¸ Äang á»Ÿ trang video, quay láº¡i Feed trÆ°á»›c khi chuyá»ƒn tiáº¿p...", "blue")
            try:
                await cdp.evaluate("window.location.href = 'https://www.tiktok.com/foryou'")
                await asyncio.sleep(random.uniform(1.5, 2.5))
            except Exception:
                pass

        state = await _feed_state()
        if "/video/" in str(state.get("path") or ""):
            self.status_update.emit("ðŸ  Má»Ÿ láº¡i For You Ä‘á»ƒ thoÃ¡t trang video...", "orange")
            try:
                await self._navigate_like_human(cdp, "tiktok.com/foryou", wait=random.uniform(3.0, 4.5))
            except Exception:
                try:
                    await cdp.navigate("https://www.tiktok.com/foryou")
                    await asyncio.sleep(random.uniform(3.0, 4.5))
                except Exception:
                    pass

        try:
            await cdp.evaluate("""
            (() => {
                try { if (document.activeElement) document.activeElement.blur(); } catch(e) {}
                try {
                    window.focus();
                    document.body.setAttribute('tabindex', '-1');
                    document.body.focus();
                } catch(e) {}
            })()
            """)
        except Exception:
            pass

        try:
            focus_pos = await self._get_safe_feed_focus_point(cdp)
            if focus_pos:
                await self._human_move_and_click(
                    cdp, int(focus_pos["x"]), int(focus_pos["y"]), "Focus vÃ¹ng video"
                )
                await asyncio.sleep(random.uniform(0.3, 0.6))
        except Exception:
            pass

    async def _click_feed_down_button(self, cdp) -> bool:
        """Click TikTok's visible down/next button if it is present."""
        try:
            pos = await cdp.evaluate(r"""
            (() => {
                const norm = (s) => (s || '').toLowerCase()
                    .normalize('NFD').replace(/[\u0300-\u036f]/g, '');
                const items = [];
                for (const el of Array.from(document.querySelectorAll('button, div[role="button"]'))) {
                    const r = el.getBoundingClientRect();
                    if (r.width < 28 || r.width > 90 || r.height < 28 || r.height > 90) continue;
                    if (r.x < window.innerWidth * 0.45 || r.y < window.innerHeight * 0.35) continue;
                    const label = norm([
                        el.getAttribute('aria-label') || '',
                        el.getAttribute('title') || '',
                        el.innerText || '',
                        el.textContent || ''
                    ].join(' '));
                    const looksDown = label.includes('next') || label.includes('down') || label.includes('xuá»‘ng');
                    const hasSvg = false; // Do not click generic right-side action buttons by SVG only.
                    if (looksDown || hasSvg) items.push({
                        x: Math.round(r.x + r.width / 2),
                        y: Math.round(r.y + r.height / 2),
                        top: r.top
                    });
                }
                if (!items.length) return null;
                items.sort((a, b) => b.top - a.top);
                return {x: items[0].x, y: items[0].y};
            })()
            """)
            if not pos:
                return False
            await self._human_move_and_click(cdp, int(pos["x"]), int(pos["y"]), "Click nÃºt xuá»‘ng")
            return True
        except Exception:
            return False

    async def _click_feed_down_button_by_position(self, cdp) -> bool:
        """Click the lower far-right TikTok navigation button, avoiding action buttons."""
        try:
            pos = await cdp.evaluate(r"""
            (() => {
                const candidates = [];
                for (const el of Array.from(document.querySelectorAll('button, div[role="button"]'))) {
                    const r = el.getBoundingClientRect();
                    if (r.width < 30 || r.width > 86 || r.height < 30 || r.height > 86) continue;
                    const cx = r.left + r.width / 2;
                    const cy = r.top + r.height / 2;
                    if (cx < window.innerWidth * 0.86) continue;
                    if (cy < window.innerHeight * 0.38 || cy > window.innerHeight * 0.86) continue;
                    const text = [
                        el.getAttribute('aria-label') || '',
                        el.getAttribute('title') || '',
                        el.innerText || '',
                        el.textContent || ''
                    ].join(' ').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');
                    if (text.includes('up') || text.includes('previous') || text.includes('back') || text.includes('len')) {
                        continue;
                    }
                    const downScore =
                        (text.includes('down') || text.includes('next') || text.includes('xuong') || text.includes('tiep')) ? 1000 : 0;
                    candidates.push({
                        x: Math.round(cx),
                        y: Math.round(cy),
                        score: downScore + Math.round(cx) + Math.round(cy * 1.5)
                    });
                }
                if (!candidates.length) return null;
                candidates.sort((a, b) => b.score - a.score);
                return {x: candidates[0].x, y: candidates[0].y};
            })()
            """)
            if not pos:
                return False
            await self._human_move_and_click(cdp, int(pos["x"]), int(pos["y"]), "Click nut xuong Feed")
            return True
        except Exception:
            return False

    async def _advance_foryou_video_legacy(self, cdp, next_idx: int) -> bool:
        """Move to next For You item and verify the visible item changed."""
        before = await self._get_current_feed_signature(cdp)
        await self._close_comment_panel_and_focus_video(cdp)
        after_cleanup = await self._get_current_feed_signature(cdp)
        if after_cleanup and after_cleanup != before:
            before = after_cleanup

        for attempt in range(4):
            self.status_update.emit(f"â¬‡ï¸ Chuyá»ƒn sang video #{next_idx}... (láº§n {attempt + 1})", "blue")
            if attempt == 0:
                await cdp.send("Input.dispatchKeyEvent", {
                    "type": "keyDown", "key": "ArrowDown", "code": "ArrowDown",
                    "windowsVirtualKeyCode": 40, "nativeVirtualKeyCode": 40,
                })
                await asyncio.sleep(random.uniform(0.03, 0.08))
                await cdp.send("Input.dispatchKeyEvent", {
                    "type": "keyUp", "key": "ArrowDown", "code": "ArrowDown",
                    "windowsVirtualKeyCode": 40, "nativeVirtualKeyCode": 40,
                })
            elif attempt == 1:
                viewport = await cdp.evaluate("({w: window.innerWidth || 900, h: window.innerHeight || 650})") or {}
                mid_x = int(viewport.get("w") or self.container_width or 900) // 2
                mid_y = int(viewport.get("h") or self.container_height or 650) // 2
                await cdp.scroll(mid_x, mid_y, 0, random.randint(900, 1600))
                try:
                    await cdp.evaluate("""
                    (() => {
                        window.dispatchEvent(new WheelEvent('wheel', {
                            deltaY: 900,
                            bubbles: true,
                            cancelable: true
                        }));
                        window.scrollBy({top: Math.round(window.innerHeight * 0.9), behavior: 'smooth'});
                    })()
                    """)
                except Exception:
                    pass
            elif attempt == 2 and await self._click_feed_down_button(cdp):
                pass
            else:
                await cdp.send("Input.dispatchKeyEvent", {
                    "type": "keyDown", "key": "PageDown", "code": "PageDown",
                    "windowsVirtualKeyCode": 34, "nativeVirtualKeyCode": 34,
                })
                await asyncio.sleep(random.uniform(0.03, 0.08))
                await cdp.send("Input.dispatchKeyEvent", {
                    "type": "keyUp", "key": "PageDown", "code": "PageDown",
                    "windowsVirtualKeyCode": 34, "nativeVirtualKeyCode": 34,
                })

            await asyncio.sleep(random.uniform(2.0, 3.0))
            after = await self._get_current_feed_signature(cdp)
            if after and after != before:
                self.status_update.emit(f"âœ… ÄÃ£ chuyá»ƒn sang video #{next_idx}", "green")
                return True

        self.status_update.emit("ðŸ  KhÃ´ng xÃ¡c nháº­n Ä‘Æ°á»£c video Ä‘á»•i, má»Ÿ láº¡i For You Ä‘á»ƒ tiáº¿p tá»¥c...", "orange")
        if await self._is_foryou_feed_usable(cdp):
            self.status_update.emit("Feed van dung duoc nhung khong xac nhan duoc chu ky video, tiep tuc de tranh dung phien.", "orange")
            return True

        try:
            await self._navigate_like_human(cdp, "tiktok.com/foryou", wait=random.uniform(3.0, 4.5))
            await self._close_comment_panel_and_focus_video(cdp)
            after = await self._get_current_feed_signature(cdp)
            if after and after != before:
                self.status_update.emit(f"âœ… ÄÃ£ khÃ´i phá»¥c Feed vÃ  chuyá»ƒn tiáº¿p video #{next_idx}", "green")
                return True
            if await self._is_foryou_feed_usable(cdp):
                self.status_update.emit("âš ï¸ Feed váº«n dÃ¹ng Ä‘Æ°á»£c nhÆ°ng khÃ´ng xÃ¡c nháº­n Ä‘Æ°á»£c chá»¯ kÃ½ video, tiáº¿p tá»¥c Ä‘á»ƒ trÃ¡nh dá»«ng phiÃªn.", "orange")
                return True
        except Exception:
            pass

        self.status_update.emit("âš ï¸ KhÃ´ng xÃ¡c nháº­n Ä‘Æ°á»£c video Ä‘Ã£ Ä‘á»•i â€” dá»«ng Ä‘á»ƒ trÃ¡nh log áº£o", "orange")
        return False

    async def _advance_foryou_video(self, cdp, next_idx: int, seen_identities=None) -> bool:
        """Move down to a new For You item. Normal Feed flow never scrolls upward."""
        seen = seen_identities if isinstance(seen_identities, set) else set(seen_identities or [])
        before = await self._get_current_feed_signature(cdp)
        before_identity = await self._get_current_feed_identity(cdp)

        await self._close_comment_panel_and_focus_video(cdp)
        cleanup_sig = await self._get_current_feed_signature(cdp)
        cleanup_identity = await self._get_current_feed_identity(cdp)
        if cleanup_sig:
            before = cleanup_sig
        if cleanup_identity:
            before_identity = cleanup_identity

        for attempt in range(8):
            self.status_update.emit(f"Chuyen xuong video #{next_idx}... lan {attempt + 1}", "blue")

            await self._feed_scroll_down(cdp, intensity=1 if attempt < 3 else 2)
            await asyncio.sleep(random.uniform(1.8, 2.8))
            after = await self._get_current_feed_signature(cdp)
            after_identity = await self._get_current_feed_identity(cdp)

            if after_identity and after_identity in seen:
                self.status_update.emit("Gap lai video da xem, chi cuon xuong tiep va khong tinh video nay.", "orange")
                before = after or before
                before_identity = after_identity
                continue

            if after_identity and after_identity != before_identity:
                self.status_update.emit(f"Da chuyen sang video #{next_idx}", "green")
                return True

        self.status_update.emit("Khong xac nhan duoc video moi, dung de tranh xem lai video cu.", "orange")
        return False

    async def _watch_foryou_feed(self, cdp, n_videos: int, use_time: bool, time_target: int) -> bool:
        _session_start = time.time()  # â˜… Thá»i gian báº¯t Ä‘áº§u thá»±c táº¿
        session_elapsed = 0.0
        skipped = 0
        seen_identities = set()
        duplicate_retries = 0

        # â˜… Náº¿u user báº­t comment â†’ giáº£m skip rate Ä‘á»ƒ Ä‘áº£m báº£o comment hoáº¡t Ä‘á»™ng
        has_comment = self.feed_settings.get('clone_cmt', 0) > 0
        skip_rate = 0.10 if has_comment else 0.40

        # â˜… Khi báº­t use_time: xem Ä‘áº¿n khi Äá»¦ THá»œI GIAN (khÃ´ng giá»›i háº¡n sá»‘ video)
        # Khi táº¯t use_time: xem Ä‘Ãºng n_videos rá»“i dá»«ng
        max_videos = 50 if use_time else n_videos  # Safety cap: tá»‘i Ä‘a 50 video
        i = 0
        finish_grace_seconds = 12

        if use_time:
            self.status_update.emit(
                f"ðŸ“º Sáº½ xem tá»‘i thiá»ƒu {n_videos} video, má»¥c tiÃªu {time_target//60} phÃºt", "blue"
            )

        while i < max_videos:
            if self._stop_flag:
                return False
            if not await self._wait_captcha_clear_for_action(cdp, f"Feed video #{i+1}"):
                return False

            # â”€â”€ Kiá»ƒm tra Ä‘Ã£ Ä‘á»§ thá»i gian chÆ°a â”€â”€
            session_elapsed = time.time() - _session_start
            if use_time and session_elapsed >= time_target:
                self.status_update.emit(
                    f"âœ… Äá»§ {session_elapsed/60:.1f}/{time_target//60} phÃºt sau {i} video!", "green"
                )
                return True
            elif not use_time and i >= n_videos:
                return True

            # â”€â”€ Random skip â€” video Ä‘áº§u KHÃ”NG BAO GIá»œ skip â”€â”€
            if use_time and (time_target - session_elapsed) <= finish_grace_seconds:
                remaining_wait = max(0.0, time_target - session_elapsed)
                if remaining_wait > 0:
                    self.status_update.emit(
                        f"Gan du thoi gian Feed, xem tiep {remaining_wait:.0f}s tren video hien tai roi ket thuc.",
                        "blue",
                    )
                    await asyncio.sleep(remaining_wait)
                return True

            current_identity = await self._get_current_feed_identity(cdp)
            if current_identity and current_identity in seen_identities:
                duplicate_retries += 1
                if duplicate_retries >= 5:
                    self.status_update.emit("Feed bi lap lai video cu qua nhieu lan, dung de kiem tra.", "orange")
                    return False
                self.status_update.emit("Feed gap lai video cu, bo qua va luot xuong tiep.", "orange")
                if not await self._advance_foryou_video(cdp, i + 1, seen_identities):
                    return False
                continue
            if current_identity:
                seen_identities.add(current_identity)
                duplicate_retries = 0

            if i > 0 and random.random() < skip_rate:
                skipped += 1
                skip_glance = random.uniform(1.0, 3.0)
                remaining = f" | CÃ²n {(time_target - session_elapsed)/60:.1f}p" if use_time else ""
                self.status_update.emit(
                    f"â© Video #{i+1}: LÆ°á»›t qua ({skip_glance:.1f}s){remaining}", "blue"
                )
                await asyncio.sleep(skip_glance)
            else:
                session_elapsed = time.time() - _session_start
                remaining = f" | CÃ²n {(time_target - session_elapsed)/60:.1f}p" if use_time else ""
                self.status_update.emit(
                    f"ðŸŽ¬ Video #{i+1} â€” Äang xem...{remaining}", "blue"
                )

                # Xem video
                await self._watch_current_video(cdp, i + 1)

                # Kiá»ƒm tra thá»i gian TRÆ¯á»šC KHI tÆ°Æ¡ng tÃ¡c (trÃ¡nh lá»‘)
                session_elapsed = time.time() - _session_start
                if use_time and session_elapsed >= time_target:
                    break

                # TÆ°Æ¡ng tÃ¡c (chá»‰ khi xem, khÃ´ng tÆ°Æ¡ng tÃ¡c video skip)
                if use_time and (time_target - session_elapsed) <= finish_grace_seconds:
                    remaining_wait = max(0.0, time_target - session_elapsed)
                    if remaining_wait > 0:
                        self.status_update.emit(
                            f"Gan xong Feed, bo qua tuong tac cuoi va xem tiep {remaining_wait:.0f}s.",
                            "blue",
                        )
                        await asyncio.sleep(remaining_wait)
                    return True

                await self._interact_current_video(cdp, i + 1)

            i += 1
            session_elapsed = time.time() - _session_start
            if use_time and session_elapsed >= time_target:
                self.status_update.emit(
                    f"âœ… Äá»§ {session_elapsed/60:.1f}/{time_target//60} phÃºt sau {i} video!", "green"
                )
                return True
            if not use_time and i >= n_videos:
                return True
            if i >= max_videos:
                break

            if not await self._wait_captcha_clear_for_action(cdp, f"Feed next #{i+1}"):
                return False
            if use_time and (time_target - session_elapsed) <= finish_grace_seconds:
                remaining_wait = max(0.0, time_target - session_elapsed)
                if remaining_wait > 0:
                    self.status_update.emit(
                        f"Gan xong Feed, khong luot video moi nua; doi {remaining_wait:.0f}s roi ket thuc.",
                        "blue",
                    )
                    await asyncio.sleep(remaining_wait)
                return True
            if not await self._advance_foryou_video(cdp, i + 1, seen_identities):
                return False

        if self._stop_flag:
            return False
        if use_time:
            session_elapsed = time.time() - _session_start
            if session_elapsed >= time_target:
                return True
            self.status_update.emit(
                f"âš ï¸ Feed cháº¡m giá»›i háº¡n {max_videos} video nhÆ°ng chÆ°a Ä‘á»§ {time_target//60} phÃºt.",
                "orange",
            )
            return False
        return i >= n_videos

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    #  EXPLORE: click thumbnail â†’ xem full â†’ back vá» grid
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

    async def _watch_explore_feed(self, cdp, n_videos: int, use_time: bool, time_target: int) -> bool:
        _session_start = time.time()  # â˜… FIX: dÃ¹ng real wall-clock time
        opened_hrefs: set = set()
        processed = 0

        max_videos = 50 if use_time else n_videos
        if use_time:
            self.status_update.emit(
                f"ðŸ“º Explore: sáº½ xem tá»‘i thiá»ƒu {n_videos} video, má»¥c tiÃªu {time_target//60} phÃºt", "blue"
            )

        for i in range(max_videos):
            if self._stop_flag:
                return False
            if not await self._wait_captcha_clear_for_action(cdp, f"Explore video #{i+1}"):
                return False
            session_elapsed = time.time() - _session_start  # â˜… FIX: real elapsed
            if use_time and session_elapsed >= time_target:
                self.status_update.emit(f"âœ… Äá»§ thá»i gian Explore ({time_target//60}p), dá»«ng sá»›m.", "green")
                return True
            if not use_time and processed >= n_videos:
                return True

            cards = await self._collect_explore_video_cards(cdp, opened_hrefs)

            # Náº¿u Ã­t thumbnail hoáº·c Ä‘Ã£ click háº¿t â†’ scroll xuá»‘ng load thÃªm
            if len(cards) < 3:
                await cdp.scroll(400, 400, 0, random.randint(400, 700))
                await asyncio.sleep(2)
                continue

            if not cards:
                opened_hrefs.clear()
                await cdp.scroll(400, 400, 0, random.randint(600, 900))
                await asyncio.sleep(2)
                continue

            chosen = random.choice(cards)
            current_idx = processed + 1

            self.status_update.emit(f"ðŸŽ¬ Explore #{current_idx}/{n_videos} â€” Má»Ÿ video...", "blue")
            if not await self._open_explore_card(cdp, chosen, current_idx):
                self.status_update.emit("âš ï¸ KhÃ´ng má»Ÿ Ä‘Æ°á»£c video Explore, bá» qua item nÃ y.", "orange")
                await asyncio.sleep(random.uniform(0.5, 0.9))
                continue
            href = (chosen.get("href") or "").strip()
            if href:
                opened_hrefs.add(href)

            # Xem video Ä‘ang phÃ¡t
            elapsed = await self._watch_current_video(cdp, current_idx)
            # session_elapsed now computed from wall-clock time (line above)

            # TÆ°Æ¡ng tÃ¡c (like, follow, comment...)
            await self._interact_current_video(cdp, current_idx)
            processed += 1

            if not await self._wait_captcha_clear_for_action(cdp, f"Explore back #{current_idx}"):
                return False

            # Nháº¥n Back vá» trang Explore (nÃºt trÃ¬nh duyá»‡t hoáº·c Escape)
            self.status_update.emit("â†©ï¸ Quay vá» Explore...", "blue")
            await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape"})
            await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp",   "key": "Escape"})
            await asyncio.sleep(0.3)
            # DÃ¹ng History.back() Ä‘á»ƒ quay vá» grid
            await cdp.evaluate("window.history.back()")
            await asyncio.sleep(random.uniform(2.5, 4.0))

            if use_time and (time.time() - _session_start) >= time_target:
                self.status_update.emit(f"âœ… Äá»§ thá»i gian ({time_target//60}p), dá»«ng sá»›m.", "green")
                return True
            if not use_time and processed >= n_videos:
                return True

        if self._stop_flag:
            return False
        if use_time:
            return (time.time() - _session_start) >= time_target
        return processed >= n_videos

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    #  HELPER: Xem video hiá»‡n táº¡i (di chuá»™t lá» Ä‘á» mÆ°á»£t mÃ )
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

    async def _watch_current_video(self, cdp, video_idx: int) -> float:
        """
        Xem video thÃ´ng minh â€” detect thá»i lÆ°á»£ng tháº­t vÃ  láº·p tá»± nhiÃªn.
        - LIVE:          xem 5-10s rá»“i skip
        - Ads:           xem 5-8s
        - Slideshow:     xem 5-10s
        - Video â‰¤ 15s:   láº·p 2 láº§n
        - Video 15-30s:  láº·p 1 láº§n (xem háº¿t 1 vÃ²ng)
        - Video > 30s:   khÃ´ng láº·p (xem háº¿t 1 láº§n)
        - Safety:        tá»‘i Ä‘a 120s
        """
        await self._ensure_cursor_dot(cdp)
        if not await self._wait_captcha_clear_for_action(cdp, f"Watch video #{video_idx}"):
            return 0.0

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        #  BÆ¯á»šC 1: Äá»c thÃ´ng tin video
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        video_info = None
        for _retry in range(3):
            video_info = await cdp.evaluate("""
            (() => {
                const v = document.querySelector('video');
                if (!v) return null;

                // â˜… Detect slideshow/photo post
                const isSlideshow = !!(
                    document.querySelector('[class*="SlideShow"]') ||
                    document.querySelector('[class*="ImageCarousel"]') ||
                    document.querySelector('[class*="carousel"]') ||
                    document.querySelector('[class*="photo-card"]') ||
                    document.querySelector('[class*="ImageContainer"]') ||
                    document.querySelector('button[aria-label*="Go to next slide"]') ||
                    document.querySelector('button[aria-label*="slide"]')
                );

                return {
                    duration: v.duration,
                    currentTime: v.currentTime,
                    paused: v.paused,
                    readyState: v.readyState,
                    isSlideshow: isSlideshow
                };
            })()
            """)
            if video_info and video_info.get('duration') and video_info['duration'] > 0:
                break
            await asyncio.sleep(1)

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        #  BÆ¯á»šC 2: Quyáº¿t Ä‘á»‹nh xem bao lÃ¢u
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        duration = 0
        target_loops = 1
        use_loop_detect = False  # True = Ä‘á»£i video háº¿t, False = sleep cá»‘ Ä‘á»‹nh

        # â˜… Detect slideshow/áº£nh: video paused ngay tá»« Ä‘áº§u HOáº¶C cÃ³ carousel
        is_photo = False
        if video_info:
            is_photo = video_info.get('isSlideshow', False)
            if video_info.get('paused', False) and video_info.get('currentTime', 0) < 0.5:
                is_photo = True  # Video pause á»Ÿ Ä‘áº§u = slideshow

        if is_photo:
            # â˜… SLIDESHOW / áº¢NH â†’ lÆ°á»›t qua nhanh 2-3s
            watch_sec = random.uniform(2, 3)
            use_loop_detect = False
            self.status_update.emit(
                f"ðŸ–¼ï¸ Video #{video_idx}: Slideshow/áº¢nh â€” lÆ°á»›t qua {watch_sec:.0f}s", "blue"
            )
        elif not video_info or not video_info.get('duration'):
            watch_sec = random.uniform(2, 3)
            self.status_update.emit(
                f"ðŸ–¼ï¸ Video #{video_idx}: KhÃ´ng cÃ³ video â€” lÆ°á»›t qua {watch_sec:.0f}s", "blue"
            )
        else:
            duration = video_info['duration']

            if duration != duration:  # NaN check
                watch_sec = random.uniform(2, 3)
                self.status_update.emit(
                    f"âš ï¸ Video #{video_idx}: Duration NaN â€” lÆ°á»›t qua {watch_sec:.0f}s", "orange"
                )
            elif duration > 1e8 or duration == float('inf'):
                # â”€â”€ LIVE STREAM â”€â”€
                watch_sec = random.uniform(5, 10)
                self.status_update.emit(
                    f"ðŸ”´ Video #{video_idx}: LIVE â€” xem {watch_sec:.0f}s", "blue"
                )
            elif duration <= 3:
                # â”€â”€ Clip cá»±c ngáº¯n / slideshow â”€â”€
                watch_sec = random.uniform(2, 3)
                self.status_update.emit(
                    f"ðŸ–¼ï¸ Video #{video_idx}: Clip {duration:.1f}s â€” lÆ°á»›t qua", "blue"
                )
            elif duration <= 15:
                # â”€â”€ Video ngáº¯n â†’ láº·p 2 láº§n â”€â”€
                target_loops = 2
                watch_sec = duration * target_loops + random.uniform(1, 3)
                use_loop_detect = True
                self.status_update.emit(
                    f"ðŸŽ¬ Video #{video_idx}: {duration:.0f}s Ã— {target_loops} láº§n", "blue"
                )
            elif duration <= 30:
                # â”€â”€ Video trung bÃ¬nh â†’ xem háº¿t 1 láº§n â”€â”€
                target_loops = 1
                watch_sec = duration + random.uniform(1, 3)
                use_loop_detect = True
                self.status_update.emit(
                    f"ðŸŽ¬ Video #{video_idx}: {duration:.0f}s Ã— 1 láº§n", "blue"
                )
            elif duration <= 60:
                # â”€â”€ Video dÃ i 30-60s â†’ xem 50-80% â”€â”€
                pct = random.uniform(0.5, 0.8)
                watch_sec = duration * pct
                watch_sec = max(15, min(watch_sec, 35))
                use_loop_detect = False
                self.status_update.emit(
                    f"ðŸŽ¬ Video #{video_idx}: {duration:.0f}s â€” xem {watch_sec:.0f}s ({pct*100:.0f}%)", "blue"
                )
            else:
                # â”€â”€ Video ráº¥t dÃ i > 60s â†’ xem 20-40s rá»“i lÆ°á»›t â”€â”€
                watch_sec = random.uniform(20, 40)
                use_loop_detect = False
                self.status_update.emit(
                    f"ðŸŽ¬ Video #{video_idx}: {duration:.0f}s (dÃ i) â€” xem {watch_sec:.0f}s rá»“i lÆ°á»›t", "blue"
                )

        # Safety cap: tá»‘i Ä‘a 30s má»—i video (trÃ¡nh lá»‘ thá»i gian)
        watch_sec = min(watch_sec, 30)

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        #  BÆ¯á»šC 3: Xem video + drift chuá»™t
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        elapsed = 0.0
        loops_done = 0
        last_ct = video_info['currentTime'] if video_info else 0
        stall_count = 0  # Äáº¿m sá»‘ láº§n currentTime khÃ´ng Ä‘á»•i (buffer)

        while elapsed < watch_sec:
            if self._stop_flag:
                return elapsed
            if not await self._wait_captcha_clear_for_action(cdp, f"Watch video #{video_idx}"):
                return elapsed

            # Drift chuá»™t mÆ°á»£t (giá»‘ng ngÆ°á»i Ä‘ang xem)
            cx = getattr(self, '_mouse_x', 400)
            cy = getattr(self, '_mouse_y', 300)
            tx = max(80, min(cx + random.randint(-120, 120), 750))
            ty = max(80, min(cy + random.randint(-80, 80), 500))
            await self._smooth_mouse_drift(cdp, tx, ty)

            pause = random.uniform(2, 4)
            await asyncio.sleep(pause)
            elapsed += pause

            # â”€â”€ Theo dÃµi tiáº¿n trÃ¬nh video (náº¿u dÃ¹ng loop detect) â”€â”€
            if use_loop_detect:
                state = await cdp.evaluate("""
                (() => {
                    const v = document.querySelector('video');
                    if (!v) return null;
                    return { ct: v.currentTime, paused: v.paused };
                })()
                """)

                if state:
                    ct = state['ct']

                    # Video bá»‹ pause â†’ Ä‘áº¿m, KHÃ”NG trá»« elapsed vÃ´ háº¡n
                    if state['paused']:
                        stall_count += 1
                        if stall_count >= 3:
                            # Pause quÃ¡ lÃ¢u (slideshow/áº£nh) â†’ thoÃ¡t
                            self.status_update.emit(
                                f"ðŸ–¼ï¸ Video #{video_idx}: Video pause â€” lÆ°á»›t qua", "blue"
                            )
                            break
                        continue

                    # Buffering: currentTime Ä‘á»©ng yÃªn
                    if abs(ct - last_ct) < 0.1:
                        stall_count += 1
                        if stall_count >= 5:
                            # Buffer quÃ¡ lÃ¢u â†’ thoÃ¡t
                            self.status_update.emit(
                                f"âš ï¸ Video #{video_idx}: Buffer quÃ¡ lÃ¢u â€” bá» qua", "orange"
                            )
                            break
                    else:
                        stall_count = 0

                    # Detect loop: currentTime nháº£y ngÆ°á»£c > 1s
                    if ct < last_ct - 1:
                        loops_done += 1
                        self.status_update.emit(
                            f"ðŸ”„ Video #{video_idx}: Láº·p láº§n {loops_done}/{target_loops}", "blue"
                        )
                        if loops_done >= target_loops:
                            # ÄÃ£ xem Ä‘á»§ sá»‘ láº§n â†’ dá»«ng
                            break

                    last_ct = ct

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        #  BÆ¯á»šC 4: Káº¿t thÃºc
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        if use_loop_detect and loops_done > 0:
            self.status_update.emit(
                f"âœ… Video #{video_idx}: Xem xong {loops_done} láº§n ({elapsed:.0f}s)", "green"
            )
        else:
            self.status_update.emit(
                f"âœ… Video #{video_idx}: Xem {elapsed:.0f}s", "green"
            )
        return elapsed

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    #  HELPER: TÆ°Æ¡ng tÃ¡c vá»›i video hiá»‡n táº¡i (Like/Fav/Repost/Follow/Cmt)
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

    async def _interact_current_video(self, cdp, video_idx: int):
        """Cháº¡y táº¥t cáº£ tÆ°Æ¡ng tÃ¡c theo tá»‰ lá»‡ % cho video Ä‘ang xem."""
        if not await self._wait_captcha_clear_for_action(cdp, f"Interact video #{video_idx}"):
            return

        # â”€â”€ PhÃ¡t hiá»‡n loáº¡i video trÆ°á»›c â”€â”€
        vtype = await self._detect_video_type(cdp)
        self.status_update.emit(f"ðŸ” Video #{video_idx}: type={vtype}", "blue")

        if vtype == 'live':
            self.status_update.emit(f"â­ï¸ Video #{video_idx}: LIVE â€” bá» qua tÆ°Æ¡ng tÃ¡c", "orange")
            return
        if vtype == 'ads':
            self.status_update.emit(f"â­ï¸ Video #{video_idx}: Quáº£ng cÃ¡o â€” bá» qua", "orange")
            return
        if vtype == 'restricted':
            self.status_update.emit(f"â­ï¸ Video #{video_idx}: Bá»‹ háº¡n cháº¿ â€” bá» qua", "orange")
            return

        # â”€â”€ Roll dice 1 Láº¦N DUY NHáº¤T cho má»—i tÃ­nh nÄƒng â”€â”€
        do_like_video = self._hit('like_video')
        do_fav_video  = self._hit('fav_video')
        do_repost     = self._hit('repost_video')
        do_follow     = self._hit('follow')
        do_clone_cmt  = self._hit('clone_cmt')
        do_like_cmt   = self._hit('like_cmt')
        do_view_more  = self._hit('view_more_cmt')

        # Like video
        if do_like_video:
            try:
                pos = await self._get_center(cdp, '[data-e2e="like-icon"]')
                if pos:
                    await self._human_move_and_click(cdp, *pos, f"â¤ï¸ Like #{video_idx}")
                    await asyncio.sleep(random.uniform(0.6, 1.2))
            except Exception:
                pass

        # ThÃªm vÃ o yÃªu thÃ­ch
        if do_fav_video:
            try:
                for sel in ['[data-e2e="undefined-icon"]', '[data-e2e="favorite-icon"]',
                            'span[class*="Favorite"]']:
                    pos = await self._get_center(cdp, sel)
                    if pos:
                        await self._human_move_and_click(cdp, *pos, f"ðŸ”– YÃªu thÃ­ch #{video_idx}")
                        await asyncio.sleep(random.uniform(0.5, 1.0))
                        break
            except Exception:
                pass

        # Repost
        if do_repost:
            try:
                share_pos = await self._get_center(cdp, '[data-e2e="share-icon"]')
                if share_pos:
                    await self._human_move_and_click(cdp, *share_pos, "ðŸ” Má»Ÿ share")
                    await asyncio.sleep(random.uniform(1.2, 1.8))
                    rp = await self._get_center_by_text(cdp, "Repost")
                    if rp:
                        await self._human_move_and_click(cdp, *rp, f"ðŸ” Repost #{video_idx}")
                    await asyncio.sleep(random.uniform(0.8, 1.5))
                    await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape"})
                    await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp",   "key": "Escape"})
                    await asyncio.sleep(0.5)
            except Exception:
                pass

        # Follow kÃªnh
        if do_follow:
            try:
                for sel in ['[data-e2e="follow-button"]', 'button[class*="follow"]']:
                    pos = await self._get_center(cdp, sel)
                    if pos:
                        await self._human_move_and_click(cdp, *pos, f"âž• Follow #{video_idx}")
                        await asyncio.sleep(random.uniform(0.8, 1.5))
                        break
            except Exception:
                pass

        # â”€â”€ TÆ°Æ¡ng tÃ¡c comment (dÃ¹ng káº¿t quáº£ Ä‘Ã£ roll á»Ÿ trÃªn) â”€â”€
        need_comment = do_clone_cmt or do_like_cmt or do_view_more

        if need_comment:
            self.status_update.emit(
                f"ðŸ’¬ Video #{video_idx}: Cáº§n comment (clone={do_clone_cmt}, like={do_like_cmt}, view={do_view_more})",
                "blue"
            )
            # Cho phÃ©p comment cáº£ khi vtype='no_comment' â€” thá»­ má»Ÿ panel dÃ¹ sao
            # VÃ¬ _detect_video_type cÃ³ thá»ƒ nháº­n diá»‡n sai trÃªn ForYou feed
            opened = await self._open_comment_panel(cdp)
            if opened:
                # Chá» panel render Ä‘áº§y Ä‘á»§
                await asyncio.sleep(random.uniform(1.0, 1.5))

                if do_like_cmt:
                    await self._like_comments(cdp, video_idx)
                if do_view_more:
                    await self._view_more_replies(cdp, video_idx)
                if do_clone_cmt:
                    await self._clone_comment(cdp, video_idx)

                # ÄÃ³ng panel comment
                await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape"})
                await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp",   "key": "Escape"})
                await asyncio.sleep(random.uniform(0.5, 1.0))
            else:
                self.status_update.emit(f"âš ï¸ Video #{video_idx}: KhÃ´ng má»Ÿ Ä‘Æ°á»£c panel comment", "orange")
        else:
            self.status_update.emit(
                f"ðŸŽ² Video #{video_idx}: KhÃ´ng trÃºng tá»‰ lá»‡ comment (clone_cmt={self.feed_settings.get('clone_cmt',0)}%)",
                "blue"
            )




    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    #  KEYWORD INTERACTION
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

    async def _do_keyword_interaction(self, cdp):
        """
        â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        TÆ¯Æ NG TÃC THEO Tá»ª KHÃ“A â€” Luá»“ng 4 giai Ä‘oáº¡n (Human-like)
        â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        GÄ1: Khá»Ÿi Ä‘á»™ng â†’ TÃ¬m Search Box â†’ GÃµ tá»«ng kÃ½ tá»± keyword â†’ Enter
        GÄ2: Click video Ä‘áº§u tiÃªn â†’ Chuyá»ƒn sang Theater Mode (ná»n Ä‘en)
        GÄ3: VÃ²ng láº·p nuÃ´i nick: Xem video + Like/Fav + ArrowDown
        GÄ4: ÄÃ³ng gÃ³i â€” ThoÃ¡t Theater Mode, tráº£ káº¿t quáº£
        """
        if not await self._wait_captcha_clear_for_action(cdp, "Keyword start"):
            return False

        # â”€â”€ Láº¥y danh sÃ¡ch tá»« khÃ³a tá»« cÃ i Ä‘áº·t â”€â”€
        keywords = self.feed_settings.get('keywords', [])
        if not keywords:
            self.status_update.emit("âš ï¸ ChÆ°a cÃ³ tá»« khÃ³a. HÃ£y vÃ o CÃ i Ä‘áº·t Ä‘á»ƒ thÃªm!", "orange")
            return False

        # â”€â”€ Láº¥y cáº¥u hÃ¬nh sá»‘ video xem má»—i tá»« khÃ³a â”€â”€
        kw_min = int(self.feed_settings.get('keyword_min_videos', 3) or 3)
        kw_max = int(self.feed_settings.get('keyword_max_videos', 8) or 8)
        if kw_min > kw_max:
            kw_min, kw_max = kw_max, kw_min

        self.status_update.emit(
            f"ðŸ” Báº¯t Ä‘áº§u tÃ¬m kiáº¿m {len(keywords)} tá»« khÃ³a ({kw_min}-{kw_max} video/tá»« khÃ³a)", "blue"
        )

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        #  LOOP QUA Tá»ªNG Tá»ª KHÃ“A
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        completed_keywords = 0
        for kw_idx, keyword in enumerate(keywords):
            if self._stop_flag:
                break
            if not await self._wait_captcha_clear_for_action(cdp, f"Keyword {kw_idx+1}/{len(keywords)}"):
                break

            self.status_update.emit(
                f"ðŸ” [{kw_idx+1}/{len(keywords)}] Tá»« khÃ³a: \"{keyword}\"", "blue"
            )

            try:
                success = await self._search_and_interact_one_keyword(cdp, keyword, kw_min, kw_max, kw_idx + 1, len(keywords))
                if not success:
                    self.status_update.emit(
                        f"âš ï¸ Tá»« khÃ³a \"{keyword[:20]}\" khÃ´ng thÃ nh cÃ´ng â€” tiáº¿p tá»¥c tá»« khÃ³a káº¿", "orange"
                    )
                else:
                    completed_keywords += 1
                # Nghá»‰ giá»¯a cÃ¡c tá»« khÃ³a (giá»‘ng ngÆ°á»i tháº­t Ä‘á»•i chá»§ Ä‘á»)
                if kw_idx < len(keywords) - 1 and not self._stop_flag:
                    pause = random.uniform(3, 6)
                    self.status_update.emit(f"â¸ï¸ Nghá»‰ {pause:.0f}s trÆ°á»›c tá»« khÃ³a káº¿...", "blue")
                    await asyncio.sleep(pause)
            except Exception as e:
                self.status_update.emit(f"âŒ Lá»—i tá»« khÃ³a \"{keyword[:20]}\": {str(e)[:50]}", "red")
                continue

        if self._stop_flag:
            return False
        if completed_keywords <= 0:
            self.status_update.emit("âš ï¸ ChÆ°a hoÃ n táº¥t tá»« khÃ³a nÃ o.", "orange")
            return False

        self.status_update.emit("âœ… Xong táº¥t cáº£ tá»« khÃ³a!", "green")
        return True

    async def _collect_keyword_video_cards(self, cdp, clicked_hrefs: set, limit: int = 10):
        """Collect visible search result video/photo cards with stable hrefs."""
        cards = await cdp.evaluate(r"""
        (() => {
            const result = [];
            const seen = new Set();
            const selectors = [
                'a[href*="/video/"]',
                'a[href*="/photo/"]',
                '[data-e2e="search_video-item"] a[href]',
                'div[class*="DivVideoCard"] a[href]',
                'div[class*="video-card"] a[href]'
            ];
            for (const sel of selectors) {
                for (const a0 of document.querySelectorAll(sel)) {
                    const a = a0.closest('a[href]') || a0;
                    const href = a.href || a.getAttribute('href') || '';
                    if (!href || (!href.includes('/video/') && !href.includes('/photo/'))) continue;
                    if (seen.has(href)) continue;
                    const r = a.getBoundingClientRect();
                    if (r.width < 60 || r.height < 70) continue;
                    if (r.bottom < 80 || r.top > window.innerHeight - 20) continue;
                    seen.add(href);
                    result.push({
                        href,
                        x: Math.round(r.x + r.width / 2),
                        y: Math.round(r.y + Math.min(r.height * 0.45, r.height / 2)),
                        top: r.top
                    });
                }
            }
            result.sort((a, b) => a.top - b.top);
            return result.slice(0, 20);
        })()
        """) or []
        return [c for c in cards if c.get("href") and c.get("href") not in clicked_hrefs][:limit]

    async def _is_keyword_results_page(self, cdp) -> bool:
        try:
            return bool(await cdp.evaluate(r"""
            (() => {
                const path = location.pathname.toLowerCase();
                if (path.includes('/search')) return true;
                const hasSearchCards = document.querySelectorAll('a[href*="/video/"], a[href*="/photo/"]').length > 0;
                const hasSearchInput = !!document.querySelector('input[type="search"], input[name="q"], input[data-e2e="search-user-input"]');
                return hasSearchCards && hasSearchInput;
            })()
            """))
        except Exception:
            return False

    def _keyword_search_url(self, keyword: str) -> str:
        keyword = str(keyword or "").strip()
        return f"https://www.tiktok.com/search?q={quote(keyword, safe='')}"

    async def _open_keyword_results_url(self, cdp, keyword: str, kw_num: int = 0, kw_total: int = 0, timeout: float = 12) -> bool:
        """Open keyword results directly in the current tab and verify the grid is ready."""
        keyword = str(keyword or "").strip()
        if not keyword:
            return False

        prefix = f"[{kw_num}/{kw_total}] " if kw_num and kw_total else ""
        url = self._keyword_search_url(keyword)
        self.status_update.emit(f"{prefix}Mo ket qua tu khoa: \"{keyword[:25]}\"", "blue")

        try:
            current_url = await cdp.evaluate("window.location.href") or ""
            if current_url.startswith("chrome://") or current_url.startswith("chrome-search://"):
                await cdp.navigate("about:blank")
                await asyncio.sleep(0.5)

            await cdp.evaluate(f"window.location.replace({_json.dumps(url)})")
            self._last_nav_url = url
            self._last_nav_ts = time.time()
            await asyncio.sleep(random.uniform(3.2, 5.0))
        except Exception:
            try:
                await cdp.navigate(url)
                self._last_nav_url = url
                self._last_nav_ts = time.time()
                await asyncio.sleep(random.uniform(3.8, 5.5))
            except Exception:
                return False

        return await self._wait_keyword_results_ready(cdp, timeout=timeout)

    async def _wait_keyword_results_ready(self, cdp, timeout: float = 12) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            if await self._is_keyword_results_page(cdp):
                return True
            await asyncio.sleep(0.8)
        return False

    async def _collect_explore_video_cards(self, cdp, opened_hrefs: set, limit: int = 18):
        """Collect visible Explore cards with href and a safer in-card click point."""
        cards = await cdp.evaluate(r"""
        (() => {
            const result = [];
            const seen = new Set();
            const selectors = [
                '[data-e2e="explore-item"] a[href]',
                'div[class*="DivVideoCard"] a[href]',
                'div[class*="video-card"] a[href]',
                'a[href*="/video/"]',
                'a[href*="/photo/"]'
            ];
            const pickPoint = (el, r) => {
                const left = Math.max(0, r.left);
                const top = Math.max(0, r.top);
                const right = Math.min(window.innerWidth, r.right);
                const bottom = Math.min(window.innerHeight, r.bottom);
                const width = Math.max(0, right - left);
                const height = Math.max(0, bottom - top);
                if (width < 20 || height < 20) return null;
                const xs = [0.50, 0.42, 0.58];
                const ys = [0.34, 0.42, 0.50];
                for (const py of ys) {
                    for (const px of xs) {
                        const x = Math.round(left + width * px);
                        const y = Math.round(top + height * py);
                        const topEl = document.elementFromPoint(x, y);
                        if (topEl && (topEl === el || el.contains(topEl) || topEl.contains(el))) {
                            return {x, y};
                        }
                    }
                }
                return {x: Math.round(left + width / 2), y: Math.round(top + Math.min(height * 0.42, height / 2))};
            };

            for (const sel of selectors) {
                for (const node of document.querySelectorAll(sel)) {
                    const a = node.closest('a[href]') || node;
                    const href = a.href || a.getAttribute('href') || '';
                    if (!href || (!href.includes('/video/') && !href.includes('/photo/'))) continue;
                    if (seen.has(href)) continue;
                    const r = a.getBoundingClientRect();
                    if (r.width < 60 || r.height < 70) continue;
                    if (r.bottom < 80 || r.top > window.innerHeight - 20) continue;
                    const point = pickPoint(a, r);
                    if (!point) continue;
                    seen.add(href);
                    result.push({href, x: point.x, y: point.y, top: r.top});
                }
            }
            result.sort((a, b) => a.top - b.top);
            return result.slice(0, 24);
        })()
        """) or []
        return [c for c in cards if c.get("href") and c.get("href") not in (opened_hrefs or set())][:limit]

    async def _verify_keyword_video_opened(self, cdp, expected_href: str = "", timeout: float = 10) -> bool:
        start = time.time()
        expected_path = ""
        expected_tail = ""
        try:
            from urllib.parse import urlparse
            expected_path = (urlparse(expected_href).path or "").rstrip("/")
            expected_tail = expected_path.split("/")[-1] if expected_path else ""
        except Exception:
            expected_path = ""
            expected_tail = ""

        while time.time() - start < timeout:
            try:
                opened = await cdp.evaluate(r"""
                (() => {
                    const path = location.pathname;
                    const hasVideoPath = path.includes('/video/') || path.includes('/photo/');
                    const hasVideo = !!document.querySelector('video');
                    const hasActions = !!document.querySelector('[data-e2e="like-icon"], [data-e2e="comment-icon"]');
                    return {url: location.href, path, ok: hasVideoPath || hasVideo || hasActions};
                })()
                """) or {}
                if opened.get("ok"):
                    opened_path = (opened.get("path") or "").rstrip("/")
                    if expected_path and (opened_path == expected_path or opened_path.endswith(expected_path) or (expected_tail and expected_tail in opened_path)):
                        return True
                    if not expected_path:
                        return True
            except Exception:
                pass
            await asyncio.sleep(0.8)
        return False

    async def _open_keyword_card(self, cdp, card: dict, video_idx: int, kw_num: int, kw_total: int) -> bool:
        """Open one keyword result by URL first to avoid focus/click mixups across embedded browsers."""
        href = (card.get("href") or "").strip()
        if not href:
            return False

        self.status_update.emit(
            f"[{kw_num}/{kw_total}] Má»Ÿ video #{video_idx} báº±ng URL káº¿t quáº£...", "blue"
        )

        for attempt in range(2):
            try:
                current_url = await cdp.evaluate("window.location.href") or ""
                if current_url.startswith("chrome://") or current_url.startswith("chrome-search://"):
                    await cdp.navigate("about:blank")
                    await asyncio.sleep(0.5)

                await cdp.evaluate(f"window.location.href = {_json.dumps(href)}")
                await asyncio.sleep(random.uniform(3.8, 5.5))
            except Exception:
                try:
                    await cdp.navigate(href)
                    await asyncio.sleep(random.uniform(4.0, 5.5))
                except Exception:
                    await asyncio.sleep(random.uniform(0.8, 1.3))
                    continue

            if await self._verify_keyword_video_opened(cdp, href, timeout=9):
                return True

            self.status_update.emit(
                f"[{kw_num}/{kw_total}] Video #{video_idx} chÆ°a khá»›p URL, thá»­ láº¡i ({attempt + 1}/2)", "orange"
            )
            await asyncio.sleep(random.uniform(0.8, 1.3))

        return False

    async def _open_explore_card(self, cdp, card: dict, video_idx: int) -> bool:
        """Open one Explore card by URL first, then safe-point click as fallback."""
        href = (card.get("href") or "").strip()
        if href:
            for _ in range(2):
                try:
                    current_url = await cdp.evaluate("window.location.href") or ""
                    if current_url.startswith("chrome://") or current_url.startswith("chrome-search://"):
                        await cdp.navigate("about:blank")
                        await asyncio.sleep(0.5)
                    await cdp.evaluate(f"window.location.href = {_json.dumps(href)}")
                    await asyncio.sleep(random.uniform(3.2, 4.8))
                except Exception:
                    try:
                        await cdp.navigate(href)
                        await asyncio.sleep(random.uniform(3.8, 5.2))
                    except Exception:
                        await asyncio.sleep(random.uniform(0.5, 0.9))
                        continue

                if await self._verify_keyword_video_opened(cdp, href, timeout=8):
                    return True
                await asyncio.sleep(random.uniform(0.5, 0.9))

        x = int(card.get("x") or 0)
        y = int(card.get("y") or 0)
        if x > 0 and y > 0:
            await self._human_move_and_click(cdp, x, y, f"Click thumbnail Explore #{video_idx}")
            await asyncio.sleep(random.uniform(2.0, 3.0))
            if await self._verify_keyword_video_opened(cdp, href, timeout=6):
                return True
        return False

    async def _return_to_keyword_results(self, cdp, keyword: str, kw_num: int, kw_total: int) -> bool:
        """Return to keyword results without browser history, so old videos are not reopened."""
        self.status_update.emit(f"[{kw_num}/{kw_total}] Táº£i láº¡i trang káº¿t quáº£ tá»« khÃ³a...", "blue")
        try:
            await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape"})
            await asyncio.sleep(0.05)
            await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Escape", "code": "Escape"})
        except Exception:
            pass
        await asyncio.sleep(random.uniform(0.4, 0.8))
        return await self._open_keyword_results_url(cdp, keyword, kw_num, kw_total, timeout=12)

    async def _search_by_clicking(self, cdp, keyword: str) -> bool:
        """
        TÃ¬m kiáº¿m báº±ng cÃ¡ch click icon ðŸ” kÃ­nh lÃºp trÃªn sidebar TikTok.
        Luá»“ng: Click icon â†’ má»Ÿ trang search â†’ gÃµ tá»« khÃ³a â†’ Enter â†’ chá» káº¿t quáº£.
        """
        try:
            if not await self._wait_captcha_clear_for_action(cdp, f"Search keyword {keyword[:20]}"):
                return False
            self.status_update.emit(f"ðŸ” Click icon kÃ­nh lÃºp Ä‘á»ƒ tÃ¬m: \"{keyword[:25]}\"", "blue")

            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            #  BÆ¯á»šC 1: TÃ¬m icon kÃ­nh lÃºp ðŸ” trÃªn sidebar/header
            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            search_icon_pos = await cdp.evaluate("""
            (() => {
                // 1. TÃ¬m link Explore/Search trÃªn sidebar (TikTok desktop)
                //    Icon kÃ­nh lÃºp thÆ°á»ng lÃ  <a> vá»›i href="/search" hoáº·c "/explore"
                const searchSelectors = [
                    'a[href="/search"]',
                    'a[href^="/search?"]',
                    '[data-e2e="nav-search"]',
                    '[data-e2e="search-icon"]',
                ];
                for (const sel of searchSelectors) {
                    const el = document.querySelector(sel);
                    if (el) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0)
                            return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), type: 'link'};
                    }
                }

                // 2. TÃ¬m icon SVG kÃ­nh lÃºp trong sidebar (quÃ©t táº¥t cáº£ link trÃªn sidebar)
                const sidebarLinks = document.querySelectorAll(
                    '[class*="sidebar"] a, [class*="SideBar"] a, [class*="SideNav"] a, ' +
                    'nav a, [class*="Navigation"] a'
                );
                for (const a of sidebarLinks) {
                    const href = (a.getAttribute('href') || '').toLowerCase();
                    if (href.includes('/search') || href.includes('/explore')) {
                        const r = a.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0)
                            return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), type: 'sidebar'};
                    }
                }

                // 3. TÃ¬m icon kÃ­nh lÃºp báº±ng SVG path (TikTok dÃ¹ng SVG cho icon)
                const svgs = document.querySelectorAll('svg');
                for (const svg of svgs) {
                    const parent = svg.closest('a, button, div[role="button"]');
                    if (!parent) continue;
                    const parentHref = (parent.getAttribute('href') || '').toLowerCase();
                    const ariaLabel = (parent.getAttribute('aria-label') || '').toLowerCase();
                    if (parentHref.includes('search') || ariaLabel.includes('search') || ariaLabel.includes('tÃ¬m')) {
                        const r = parent.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0)
                            return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), type: 'svg'};
                    }
                }

                // 4. Fallback: tÃ¬m báº¥t ká»³ element nÃ o giá»‘ng nÃºt search
                const all = document.querySelectorAll('a, button');
                for (const el of all) {
                    const r = el.getBoundingClientRect();
                    if (r.width < 10 || r.height < 10 || r.x > 150) continue; // Sidebar thÆ°á»ng á»Ÿ trÃ¡i, x < 150
                    const href = (el.getAttribute('href') || '');
                    const text = (el.textContent || '').toLowerCase();
                    if (href.includes('/search') || text.includes('search') || text.includes('tÃ¬m kiáº¿m')) {
                        return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), type: 'fallback'};
                    }
                }

                return null;
            })()
            """)

            if not search_icon_pos:
                self.status_update.emit("âš ï¸ KhÃ´ng tÃ¬m tháº¥y icon kÃ­nh lÃºp trÃªn sidebar", "orange")
                return False

            self.status_update.emit(
                f"ðŸ‘† TÃ¬m tháº¥y icon search ({search_icon_pos.get('type','?')}) â€” click...", "blue"
            )
            await self._human_move_and_click(
                cdp, search_icon_pos['x'], search_icon_pos['y'], "Click icon ðŸ” Search"
            )

            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            #  BÆ¯á»šC 2: Chá» trang search má»Ÿ â†’ tÃ¬m Ã´ input
            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            await asyncio.sleep(random.uniform(1.5, 2.5))

            # TÃ¬m Ã´ input search sau khi click icon
            search_input = None
            for attempt in range(5):
                search_input = await cdp.evaluate("""
                (() => {
                    // Æ¯u tiÃªn: input Ä‘ang active (focus)
                    const active = document.activeElement;
                    if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA')) {
                        const r = active.getBoundingClientRect();
                        if (r.width > 50) return {x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2)};
                    }
                    // TÃ¬m input search
                    const selectors = [
                        'input[data-e2e="search-user-input"]',
                        'input[type="search"]',
                        'input[placeholder*="Search" i]',
                        'input[placeholder*="TÃ¬m" i]',
                        'input[name="q"]'
                    ];
                    for (const sel of selectors) {
                        const inp = document.querySelector(sel);
                        if (inp) {
                            const r = inp.getBoundingClientRect();
                            if (r.width > 50) return {x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2)};
                        }
                    }
                    // Fallback: báº¥t ká»³ input nÃ o cÃ³ placeholder search
                    for (const inp of document.querySelectorAll('input')) {
                        const ph = (inp.placeholder || '').toLowerCase();
                        if (ph.includes('search') || ph.includes('tÃ¬m')) {
                            const r = inp.getBoundingClientRect();
                            if (r.width > 50) return {x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2)};
                        }
                    }
                    return null;
                })()
                """)
                if search_input:
                    break
                await asyncio.sleep(0.8)

            if not search_input:
                self.status_update.emit("âš ï¸ KhÃ´ng tÃ¬m tháº¥y Ã´ nháº­p search sau khi click icon", "orange")
                return False

            # Click vÃ o Ã´ input search
            await self._human_move_and_click(cdp, search_input['x'], search_input['y'], "Click Ã´ nháº­p search")
            await asyncio.sleep(random.uniform(0.5, 0.8))

            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            #  BÆ¯á»šC 3: XÃ³a text cÅ© + GÃµ tá»« khÃ³a + Enter
            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            # Ctrl+A â†’ Backspace (xÃ³a text cÅ© náº¿u cÃ³)
            await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a", "code": "KeyA", "modifiers": 2})
            await asyncio.sleep(0.05)
            await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a", "code": "KeyA"})
            await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Backspace", "code": "Backspace"})
            await asyncio.sleep(0.05)
            await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Backspace", "code": "Backspace"})
            await asyncio.sleep(random.uniform(0.3, 0.5))

            # GÃµ tá»«ng kÃ½ tá»± (100-300ms delay â€” giá»‘ng ngÆ°á»i tháº­t)
            self.status_update.emit(f"âŒ¨ï¸ GÃµ: \"{keyword}\"", "blue")
            await cdp.type_text(keyword, delay=random.randint(100, 300))
            await asyncio.sleep(random.uniform(1.0, 2.0))

            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            #  BÆ¯á»šC 4: Chá» dropdown gá»£i Ã½ â†’ Click dÃ²ng gá»£i Ã½
            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            self.status_update.emit("ðŸ‘† Chá» dropdown gá»£i Ã½ hiá»‡n ra...", "blue")

            # Chá» dropdown suggestions xuáº¥t hiá»‡n (tá»‘i Ä‘a 5 giÃ¢y)
            suggestion_clicked = False
            for wait_attempt in range(6):
                suggestion_pos = await cdp.evaluate("""
                (() => {
                    const results = [];

                    // 1. TÃ¬m link "Xem táº¥t cáº£ káº¿t quáº£" / "View all results" â€” Æ°u tiÃªn cao nháº¥t
                    const allEls = document.querySelectorAll('a, div[role="link"], div[role="button"], span, p, div');
                    for (const el of allEls) {
                        const text = (el.textContent || '').trim().toLowerCase();
                        if ((text.includes('xem táº¥t cáº£') || text.includes('view all') ||
                             text.includes('táº¥t cáº£ káº¿t quáº£') || text.includes('all results')) &&
                            text.length < 100) {
                            const r = el.getBoundingClientRect();
                            if (r.width > 50 && r.height > 10 && r.y > 50)
                                results.push({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2), type: 'view_all', text: text.substring(0,40)});
                        }
                    }

                    // 2. TÃ¬m cÃ¡c dÃ²ng gá»£i Ã½ cÃ³ icon kÃ­nh lÃºp (Q) â€” trong dropdown suggestions
                    //    TikTok thÆ°á»ng dÃ¹ng <a> hoáº·c <div> vá»›i class chá»©a "SearchSuggestion" hoáº·c tÆ°Æ¡ng tá»±
                    const suggestionSelectors = [
                        '[class*="suggestion" i] a',
                        '[class*="suggestion" i] div[role="button"]',
                        '[class*="Suggestion" i] a',
                        '[class*="search-suggest" i] a',
                        '[class*="SearchSuggest" i] a',
                        '[data-e2e="search-suggest"] a',
                        '[data-e2e*="suggest"] a',
                        // TÃ¬m link cÃ³ text matching tá»« khÃ³a
                    ];
                    for (const sel of suggestionSelectors) {
                        for (const el of document.querySelectorAll(sel)) {
                            const r = el.getBoundingClientRect();
                            if (r.width > 50 && r.height > 15 && r.y > 50)
                                results.push({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2), type: 'suggest_sel', text: (el.textContent||'').trim().substring(0,40)});
                        }
                    }

                    // 3. Fallback: tÃ¬m cÃ¡c <a> hoáº·c div bÃªn dÆ°á»›i search input cÃ³ ná»™i dung liÃªn quan
                    const searchInput = document.querySelector('input[data-e2e="search-user-input"], input[type="search"], input[placeholder*="Search" i], input[placeholder*="TÃ¬m" i]');
                    if (searchInput) {
                        const inputRect = searchInput.getBoundingClientRect();
                        // TÃ¬m container cha chá»©a cáº£ input vÃ  dropdown
                        let container = searchInput.closest('[class*="search" i], [class*="Search" i], form') || searchInput.parentElement;
                        if (container) {
                            // Má»Ÿ rá»™ng lÃªn vÃ i cáº¥p náº¿u cáº§n
                            for (let i = 0; i < 5 && container.parentElement; i++) {
                                const links = container.querySelectorAll('a[href], div[role="button"], div[role="link"]');
                                if (links.length > 2) break;
                                container = container.parentElement;
                            }
                            const items = container.querySelectorAll('a[href], div[role="button"], div[role="link"]');
                            for (const item of items) {
                                const r = item.getBoundingClientRect();
                                // Chá»‰ láº¥y cÃ¡c item DÆ¯á»šI search input (dropdown)
                                if (r.y > inputRect.bottom - 5 && r.width > 50 && r.height > 15 && r.y < inputRect.bottom + 500) {
                                    const text = (item.textContent || '').trim();
                                    if (text.length > 2 && text.length < 100) {
                                        results.push({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2), type: 'dropdown_item', text: text.substring(0,40)});
                                    }
                                }
                            }
                        }
                    }

                    if (results.length === 0) return null;

                    // Æ¯u tiÃªn: view_all > suggest_sel > dropdown_item
                    // NhÆ°ng náº¿u cÃ³ suggest, click dÃ²ng 1 hoáº·c 2 (ngáº«u nhiÃªn, giá»‘ng ngÆ°á»i tháº­t)
                    const viewAll = results.find(r => r.type === 'view_all');
                    const suggests = results.filter(r => r.type !== 'view_all');

                    if (suggests.length > 0) {
                        // Click dÃ²ng 1 hoáº·c 2 ngáº«u nhiÃªn
                        const idx = Math.min(Math.floor(Math.random() * 2), suggests.length - 1);
                        return suggests[idx];
                    }
                    if (viewAll) return viewAll;
                    return results[0];
                })()
                """)

                if suggestion_pos:
                    self.status_update.emit(
                        f"ðŸ‘† Click gá»£i Ã½: \"{suggestion_pos.get('text','')}\" ({suggestion_pos.get('type','')})", "blue"
                    )
                    await self._human_move_and_click(
                        cdp, suggestion_pos['x'], suggestion_pos['y'],
                        f"Click gá»£i Ã½ search"
                    )
                    suggestion_clicked = True
                    break

                await asyncio.sleep(0.8)

            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            #  BÆ¯á»šC 5: Náº¿u khÃ´ng tÃ¬m tháº¥y dropdown â†’ thá»­ Enter
            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            if not suggestion_clicked:
                self.status_update.emit("âš ï¸ KhÃ´ng tháº¥y dropdown gá»£i Ã½ â€” thá»­ nháº¥n Enter...", "orange")
                # Focus láº¡i input
                await cdp.evaluate("""
                (() => {
                    const inp = document.querySelector('input[data-e2e="search-user-input"], input[type="search"]');
                    if (inp) inp.focus();
                })()
                """)
                await asyncio.sleep(0.2)
                # Submit báº±ng form submit (JS)
                submitted = await cdp.evaluate("""
                (() => {
                    const inp = document.querySelector('input[data-e2e="search-user-input"], input[type="search"]');
                    if (inp) {
                        const form = inp.closest('form');
                        if (form) { form.submit(); return 'form'; }
                        // Dispatch Enter event trÃªn input
                        inp.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true}));
                        inp.dispatchEvent(new KeyboardEvent('keypress', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true}));
                        inp.dispatchEvent(new KeyboardEvent('keyup', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true}));
                        return 'event';
                    }
                    return null;
                })()
                """)
                self.status_update.emit(f"â†µ Submit method: {submitted}", "blue")

            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            #  BÆ¯á»šC 6: Chá» káº¿t quáº£ tÃ¬m kiáº¿m
            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            self.status_update.emit("â³ Chá» káº¿t quáº£ tÃ¬m kiáº¿m...", "blue")
            await asyncio.sleep(random.uniform(3.0, 5.0))

            if await self._wait_keyword_results_ready(cdp, timeout=8):
                return True

            # Fallback cuá»‘i cÃ¹ng: navigate URL
            self.status_update.emit("ðŸ”„ Fallback: má»Ÿ URL tÃ¬m kiáº¿m...", "orange")
            return await self._open_keyword_results_url(cdp, keyword, timeout=10)

        except Exception as e:
            self.status_update.emit(f"âš ï¸ Search by click lá»—i: {str(e)[:80]}", "orange")
            return False

    async def _search_and_interact_one_keyword(self, cdp, keyword: str, min_videos: int, max_videos: int, kw_num: int, kw_total: int) -> bool:
        """
        TÃ¬m kiáº¿m vÃ  tÆ°Æ¡ng tÃ¡c cho 1 tá»« khÃ³a.
        Luá»“ng: Search â†’ Grid káº¿t quáº£ â†’ Cuá»™n lÃªn/xuá»‘ng tá»± nhiÃªn â†’ Click ngáº«u nhiÃªn video
               â†’ Xem + tÆ°Æ¡ng tÃ¡c â†’ Escape quay láº¡i grid â†’ Láº·p láº¡i.
        """
        try:
            if not await self._wait_captcha_clear_for_action(cdp, f"Keyword {kw_num}/{kw_total} start"):
                return False

            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            #  GIAI ÄOáº N 1: Má»Ÿ trang chá»§ TikTok â†’ Chá» load â†’ TÃ¬m kiáº¿m
            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            # â˜… LUÃ”N navigate vá» trang chá»§ TikTok trÆ°á»›c (giá»‘ng ngÆ°á»i tháº­t)
            self.status_update.emit(f"ðŸ  [{kw_num}/{kw_total}] Má»Ÿ trang chá»§ TikTok...", "blue")
            await self._navigate_like_human(cdp, "tiktok.com", wait=random.uniform(3, 5))

            # â˜… Persist láº¡i session cookies
            await self._persist_tiktok_cookies(cdp)

            # Chá» thÃªm 2-4s cho trang load hoÃ n toÃ n (giá»‘ng ngÆ°á»i tháº­t má»Ÿ TikTok lÃªn rá»“i lÆ°á»›t vÃ i giÃ¢y)
            self.status_update.emit(f"â³ [{kw_num}/{kw_total}] Chá» TikTok load...", "blue")
            await asyncio.sleep(random.uniform(2, 4))

            # áº¨n viá»n focus
            await cdp.evaluate("""
            (() => {
                const s = document.createElement('style');
                s.textContent = '*:focus,*:focus-visible{outline:none!important;box-shadow:none!important;}';
                document.head.appendChild(s);
            })()
            """)

            # GÄ1: Má»Ÿ trang káº¿t quáº£ báº±ng URL trÆ°á»›c; click search chá»‰ lÃ  fallback.
            self.status_update.emit(f"ðŸ”Ž [{kw_num}/{kw_total}] TÃ¬m kiáº¿m: \"{keyword}\"...", "blue")
            if not await self._open_keyword_results_url(cdp, keyword, kw_num, kw_total, timeout=12):
                self.status_update.emit(f"âš ï¸ [{kw_num}/{kw_total}] URL search chÆ°a sáºµn sÃ ng, thá»­ search báº±ng giao diá»‡n", "orange")
                if not await self._search_by_clicking(cdp, keyword):
                    self.status_update.emit(f"âŒ [{kw_num}/{kw_total}] KhÃ´ng tÃ¬m kiáº¿m Ä‘Æ°á»£c \"{keyword[:20]}\"", "red")
                    return False
            if not await self._wait_keyword_results_ready(cdp, timeout=8):
                self.status_update.emit(f"âŒ [{kw_num}/{kw_total}] KhÃ´ng tÃ¬m kiáº¿m Ä‘Æ°á»£c \"{keyword[:20]}\"", "red")
                return False

            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            #  GIAI ÄOáº N 2: Duyá»‡t grid káº¿t quáº£ â€” cuá»™n lÃªn/xuá»‘ng tá»± nhiÃªn
            #  rá»“i click ngáº«u nhiÃªn video â†’ xem â†’ back â†’ láº·p láº¡i
            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            n_videos = random.randint(min_videos, max_videos)
            self.status_update.emit(
                f"ðŸ“º [{kw_num}/{kw_total}] Sáº½ xem {n_videos} video cho \"{keyword[:15]}\"", "blue"
            )

            clicked_hrefs = set()  # Chá»‘ng click trÃ¹ng video
            watched_count = 0
            attempt_count = 0
            max_attempts = max(n_videos * 3, n_videos + 3)

            while watched_count < n_videos and attempt_count < max_attempts:
                attempt_count += 1
                video_no = watched_count + 1
                if self._stop_flag:
                    break
                if not await self._wait_captcha_clear_for_action(cdp, f"Keyword video #{video_no}"):
                    break

                # â”€â”€ BÆ°á»›c 2.1: Cuá»™n trang káº¿t quáº£ tá»± nhiÃªn (giá»‘ng ngÆ°á»i duyá»‡t) â”€â”€
                self.status_update.emit(
                    f"ðŸ‘ï¸ [{kw_num}/{kw_total}] Video #{video_no}/{n_videos} â€” Duyá»‡t káº¿t quáº£...", "blue"
                )

                # Cuá»™n xuá»‘ng ngáº«u nhiÃªn Ä‘á»ƒ xem thÃªm káº¿t quáº£
                n_scrolls = random.randint(1, 3)
                for s in range(n_scrolls):
                    if self._stop_flag:
                        break
                    # Di chuá»™t vÃ o vÃ¹ng grid trÆ°á»›c khi cuá»™n
                    mx = random.randint(200, 700)
                    my = random.randint(200, 500)
                    await self._smooth_mouse_drift(cdp, mx, my)
                    # Cuá»™n xuá»‘ng
                    scroll_amount = random.randint(200, 500)
                    await cdp.scroll(mx, my, 0, scroll_amount)
                    await asyncio.sleep(random.uniform(1.0, 2.0))

                # Thá»‰nh thoáº£ng cuá»™n lÃªn (30% cÆ¡ há»™i) â€” giá»‘ng ngÆ°á»i xem láº¡i
                if False and random.random() < 0.3:
                    mx = random.randint(200, 700)
                    my = random.randint(200, 500)
                    await self._smooth_mouse_drift(cdp, mx, my)
                    await asyncio.sleep(0)
                    await asyncio.sleep(random.uniform(0.8, 1.5))

                # â”€â”€ BÆ°á»›c 2.2: Láº¥y danh sÃ¡ch video/photo card Ä‘ang hiá»ƒn thá»‹ vÃ  chÆ°a click â”€â”€
                available = await self._collect_keyword_video_cards(cdp, clicked_hrefs)
                if not available:
                    self.status_update.emit("â¬ Scroll thÃªm Ä‘á»ƒ tÃ¬m video má»›i...", "blue")
                    await cdp.scroll(400, 400, 0, random.randint(500, 850))
                    await asyncio.sleep(random.uniform(1.5, 2.3))
                    available = await self._collect_keyword_video_cards(cdp, clicked_hrefs)
                if not available:
                    self.status_update.emit("âš ï¸ ChÆ°a báº¯t Ä‘Æ°á»£c card video â€” tÃ¬m kiáº¿m láº¡i", "orange")
                    if await self._open_keyword_results_url(cdp, keyword, kw_num, kw_total, timeout=10):
                        await asyncio.sleep(random.uniform(1.2, 2.0))
                        available = await self._collect_keyword_video_cards(cdp, clicked_hrefs)

                if not available:
                    self.status_update.emit(f"âš ï¸ Háº¿t video Ä‘á»ƒ xem cho \"{keyword[:15]}\"", "orange")
                    break

                # â”€â”€ BÆ°á»›c 2.3: Click ngáº«u nhiÃªn 1 video tá»« danh sÃ¡ch â”€â”€
                chosen = random.choice(available)
                clicked_hrefs.add(chosen.get('href', ''))

                if not await self._open_keyword_card(cdp, chosen, video_no, kw_num, kw_total):
                    self.status_update.emit("âš ï¸ KhÃ´ng má»Ÿ Ä‘Æ°á»£c video tá»« card nÃ y â€” bá» qua", "orange")
                    await self._open_keyword_results_url(cdp, keyword, kw_num, kw_total, timeout=8)
                    continue

                # â”€â”€ BÆ°á»›c 2.4: Xem video (trong Theater Mode / full page) â”€â”€
                self.status_update.emit(
                    f"ðŸŽ¬ [{kw_num}/{kw_total}] Äang xem video #{video_no}/{n_videos}...", "blue"
                )
                await self._watch_current_video(cdp, video_no)
                watched_count += 1

                # â”€â”€ BÆ°á»›c 2.5: TÆ°Æ¡ng tÃ¡c (Like/Fav/Comment theo tá»‰ lá»‡ %) â”€â”€
                await self._interact_current_video(cdp, video_no)

                if not await self._wait_captcha_clear_for_action(cdp, f"Keyword back #{video_no}"):
                    break

                # â”€â”€ BÆ°á»›c 2.6: Quay láº¡i trang káº¿t quáº£ báº±ng URL, khÃ´ng dÃ¹ng history.back â”€â”€
                if watched_count < n_videos:
                    if not await self._return_to_keyword_results(cdp, keyword, kw_num, kw_total):
                        self.status_update.emit("âš ï¸ KhÃ´ng thá»ƒ quay láº¡i káº¿t quáº£, dá»«ng keyword nÃ y", "orange")
                        break

            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            #  GIAI ÄOáº N 3: HoÃ n thÃ nh tá»« khÃ³a nÃ y
            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            if watched_count < min_videos:
                self.status_update.emit(
                    f"âŒ [{kw_num}/{kw_total}] ChÆ°a Ä‘á»§ video cho \"{keyword[:20]}\" ({watched_count}/{min_videos})", "red"
                )
                return False

            if watched_count < n_videos:
                self.status_update.emit(
                    f"âš ï¸ [{kw_num}/{kw_total}] HoÃ n thÃ nh má»™t pháº§n \"{keyword[:20]}\" ({watched_count}/{n_videos} video)", "orange"
                )
            else:
                self.status_update.emit(
                    f"âœ… [{kw_num}/{kw_total}] HoÃ n thÃ nh \"{keyword[:20]}\" ({watched_count} video)", "green"
                )
            return True

        except Exception as e:
            self.status_update.emit(
                f"âŒ [{kw_num}/{kw_total}] Lá»—i \"{keyword[:15]}\": {str(e)[:50]}", "red"
            )
            return False




    def _hide_browser_windows(self):
        """áº¨n browser báº±ng cÃ¡ch di chuyá»ƒn ra ngoÃ i mÃ n hÃ¬nh (váº«n render cho screencast)."""
        if not self._process:
            return
        try:
            import win32gui, win32con, win32process
            pid = self._process.pid

            # Láº¥y táº¥t cáº£ PID (parent + children)
            all_pids = {pid}
            try:
                import psutil
                parent = psutil.Process(pid)
                for child in parent.children(recursive=True):
                    all_pids.add(child.pid)
            except Exception:
                pass

            def callback(hwnd, hwnds):
                _, found_pid = win32process.GetWindowThreadProcessId(hwnd)
                if found_pid in all_pids and win32gui.IsWindowVisible(hwnd):
                    hwnds.append(hwnd)
                return True

            hwnds = []
            win32gui.EnumWindows(callback, hwnds)
            for hwnd in hwnds:
                # Di chuyá»ƒn ra ngoÃ i mÃ n hÃ¬nh (Chrome váº«n render)
                win32gui.SetWindowPos(
                    hwnd, None,
                    -32000, -32000, 0, 0,
                    win32con.SWP_NOSIZE | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
                )

            if hwnds:
                self.status_update.emit(f"ðŸ‘â€ðŸ—¨ áº¨n {len(hwnds)} cá»­a sá»• browser", "green")
        except Exception as e:
            self.status_update.emit(f"âš ï¸ KhÃ´ng áº©n Ä‘Æ°á»£c browser: {str(e)[:40]}", "orange")

    def stop(self):
        """Dá»«ng worker + Ä‘Ã³ng browser GRACEFULLY (khÃ´ng block UI)."""
        self._stop_flag = True
        if self._async_close_started:
            return
        self._async_close_started = True
        self.status_update.emit("Dang don trinh duyet cu, vui long cho Orbita/GoLogin dong xong...", "orange")

        # LÆ°u ref process trÆ°á»›c khi clear
        proc = self._process
        self._process = None
        gologin = self._gologin
        self._gologin = None
        self._using_gologin_api = False
        debug_port = self._debug_port
        profile_dir = self._profile_dir  # â˜… LÆ°u Ä‘á»ƒ patch sau khi kill
        manager_acquired = self._browser_manager_acquired
        self._browser_manager_acquired = False
        known_pids = set(self._browser_pids)
        if self._process_pid:
            known_pids.add(self._process_pid)

        def _graceful_close():
            """Cháº¡y trong background thread â€” Ä‘Ã³ng Chrome graceful."""
            import time
            # BÆ°á»›c 1: Gá»­i Browser.close qua HTTP
            try:
                import http.client
                conn = http.client.HTTPConnection("127.0.0.1", debug_port, timeout=2)
                conn.request("GET", "/json/close/all")
                conn.close()
                time.sleep(3)  # â˜… Chá» Chrome flush cookie ra Ä‘Ä©a
            except Exception:
                pass

            if gologin:
                try:
                    gologin.stop()
                except Exception:
                    pass

            # BÆ°á»›c 2: Unlock/kill qua BrowserManager náº¿u váº«n cÃ²n sá»‘ng
            # GoLogin SDK chi kill PID goc; Orbita child co the con song.
            self._force_close_browser_processes(debug_port, profile_dir, known_pids)

            if manager_acquired:
                try:
                    from browser_manager import BrowserManager
                    BrowserManager().close_browser(self._browser_id, profile_dir)
                except Exception:
                    if proc and proc.poll() is None:
                        try: proc.kill()
                        except Exception: pass
            else:
                if proc and proc.poll() is None:
                    try: proc.kill()
                    except Exception: pass

            # â˜… BÆ°á»›c 3: Patch exit_type=Normal SAU KHI kill
            # Chrome ghi "Crashed" khi bá»‹ kill â†’ pháº£i ghi Ä‘Ã¨ láº¡i ngay
            time.sleep(0.5)  # Chá» Chrome flush xong
            try:
                import json as _json, os as _os
                prefs_path = _os.path.join(profile_dir, "Default", "Preferences") if profile_dir else ""
                if prefs_path and _os.path.exists(prefs_path):
                    prefs = _json.load(open(prefs_path, encoding="utf-8"))
                    prefs.setdefault("profile", {})["exit_type"] = "Normal"
                    prefs["profile"]["exited_cleanly"] = True
                    open(prefs_path, "w", encoding="utf-8").write(
                        _json.dumps(prefs, ensure_ascii=False)
                    )
            except Exception:
                pass

        # â˜… daemon=False â†’ thread KHÃ”NG bá»‹ kill khi app Ä‘Ã³ng
            self._process_pid = 0
            self._browser_pids.clear()
            self._emit_browser_closed_once("closed")

        import threading
        self._close_thread = threading.Thread(target=_graceful_close, daemon=False)
        self._close_thread.start()

        # Dá»«ng local proxy
        if hasattr(self, '_local_proxy') and self._local_proxy:
            try: self._local_proxy.stop()
            except Exception: pass


