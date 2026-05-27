"""
Worker CDP — Thay thế GoLoginWorker.
Dùng CDP trực tiếp (websockets) thay Playwright.
Hỗ trợ screencast live preview.
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
import uuid
from urllib.parse import quote
from PyQt5.QtCore import QThread, pyqtSignal
import win32gui
import win32con
import win32process
from gologin_config import load_gologin_settings
from app_paths import gologin_base_dir, gologin_profile_dir, resource_path

GOLOGIN_BASE_DIR = str(gologin_base_dir())
ZERO_PROFILE_ZIP = str(resource_path("gologin_zeroprofile.zip"))

BROWSER_WIDTH = 960
BROWSER_HEIGHT = 680
APP_TITLEBAR_HEIGHT = 0  # 0 = hiện thanh trình duyệt

# Viewport ảo — lừa TikTok render layout desktop 3 cột chuẩn
VIRTUAL_VIEWPORT_W = 1280
VIRTUAL_VIEWPORT_H = 720
_GOLOGIN_START_LOCK = threading.RLock()


def _is_port_open(port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


class CDPWorker(QThread):
    """Worker dùng CDP + Native Window Embedding (SetParent) — 60 FPS mượt mà."""
    status_update = pyqtSignal(str, str)       # (message, color)
    finished_signal = pyqtSignal(str)           # "success" / "error"
    profile_update_signal = pyqtSignal(dict)    # {"tiktok_id": ..., "cookie": ...}
    browser_ready_signal = pyqtSignal(dict)      # browser launched; UI thread should embed HWND
    browser_closed_signal = pyqtSignal(str)      # emitted only after browser cleanup is really done

    def __init__(self, profile_index, profile_data, selected_features, feed_settings,
                 container_width=0, container_height=0, widget_id=0, parent=None,
                 manual_only=False):
        super().__init__(parent)
        self.profile_index = profile_index
        self.profile_data = profile_data
        self.selected_features = selected_features
        self.feed_settings = feed_settings
        self.container_width = container_width or BROWSER_WIDTH
        self.container_height = container_height or BROWSER_HEIGHT
        self.widget_id = widget_id  # HWND của QWidget container
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

        # Comment bank: lưu comment từ các video trước để clone chéo
        self._comment_bank = []       # list comment đã thu thập từ video cũ
        self._comment_history = set() # chống trùng lặp trong phiên
        self._comment_cooldown = False  # rate limit flag

        # Parse proxy
        self._proxy_host = ""
        self._proxy_port = ""
        self._proxy_user = ""
        self._proxy_pass = ""
        self._proxy_type = (self.profile_data.get('proxy_type', 'http') or 'http').strip().lower()
        parsed_proxy = self._parse_proxy_string(self.profile_data.get('proxy', ''), self._proxy_type)
        if parsed_proxy:
            self._proxy_type = parsed_proxy["mode"]
            self._proxy_host = parsed_proxy["host"]
            self._proxy_port = str(parsed_proxy["port"])
            self._proxy_user = parsed_proxy.get("username", "")
            self._proxy_pass = parsed_proxy.get("password", "")

        # Thư mục profile — luôn dựa trên browser_id duy nhất
        browser_id = self.profile_data.get('browser_id', '')
        if not browser_id:
            # Tự tạo browser_id nếu chưa có (dựa trên row index)
            browser_id = f"auto_{uuid.uuid4().hex[:8]}"
            self.profile_data['browser_id'] = browser_id
        self._browser_id = browser_id
        self._profile_dir = str(gologin_profile_dir(browser_id))
        self._gologin_profile_id = (self.profile_data.get("gologin_profile_id") or "").strip()
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
        proxy_type = (proxy_type or "http").strip().lower()
        if proxy_type == "socks5h":
            proxy_type = "socks5"
        # GoLogin uses "http" mode for common HTTP/HTTPS CONNECT proxies.
        if proxy_type == "https":
            proxy_type = "http"
        if proxy_type not in ("http", "socks4", "socks5"):
            proxy_type = "http"
        return proxy_type

    def _parse_proxy_string(self, proxy_str, proxy_type="http"):
        """Parse proxy formats: host:port, host:port:user:pass, scheme://host:port:user:pass, or user:pass@host:port."""
        proxy_str = (proxy_str or "").strip()
        if not proxy_str:
            return None

        proxy_type = self._normalize_proxy_mode(proxy_type)
        if "://" in proxy_str:
            scheme, proxy_str = proxy_str.split("://", 1)
            proxy_type = self._normalize_proxy_mode(scheme)

        username = ""
        password = ""
        if "@" in proxy_str:
            auth_part, proxy_str = proxy_str.rsplit("@", 1)
            if ":" in auth_part:
                username, password = auth_part.split(":", 1)
            else:
                username = auth_part

        parts = proxy_str.split(":", 3)
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

    def _get_proxy_payload(self, for_gologin_api=False):
        payload = self._parse_proxy_string(
            self.profile_data.get("proxy", ""),
            self.profile_data.get("proxy_type", self._proxy_type),
        )
        if not payload:
            return None
        payload["changeIpUrl"] = ""
        if for_gologin_api:
            profile_name = (self.profile_data.get("ten_ho_so") or "").strip()
            if profile_name:
                payload["customName"] = profile_name[:80]
        else:
            payload["autoProxyRegion"] = ""
            payload["torProxyRegion"] = ""
        return payload

    def _proxy_display_text(self, payload):
        if not payload:
            return ""
        return f"{payload.get('mode')}://{payload.get('host')}:{payload.get('port')}"

    def _sync_gologin_profile_proxy(self):
        """Persist the current proxy to the GoLogin cloud profile before starting it."""
        proxy_payload = self._get_proxy_payload(for_gologin_api=True)
        if not proxy_payload:
            return True, "Không có proxy để đồng bộ"

        token, _, _ = self._get_gologin_api_settings()
        if not token:
            return False, "Thiếu GoLogin API key nên không thể đồng bộ proxy."
        if not self._gologin_profile_id:
            return False, "Thiếu GoLogin Profile ID nên không thể đồng bộ proxy."

        body = {
            "proxies": [
                {
                    "profileId": self._gologin_profile_id,
                    "proxy": proxy_payload,
                }
            ]
        }

        try:
            import requests
            response = requests.patch(
                "https://api.gologin.com/browser/proxy/many/v2",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=25,
            )
        except Exception as e:
            return False, f"Lỗi kết nối API GoLogin khi đồng bộ proxy: {e}"

        if response.status_code in (200, 204):
            display = self._proxy_display_text(proxy_payload)
            try:
                self.profile_update_signal.emit({"gologin_proxy_synced": display})
            except Exception:
                pass
            return True, display

        detail = (response.text or "").strip().replace("\n", " ")[:300]
        return False, f"GoLogin proxy API lỗi HTTP {response.status_code}: {detail}"

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
            self.status_update.emit(f"Trình duyệt đang mở qua BrowserManager (port {dynamic_port})...", "blue")
            time.sleep(0.25)
            return True
        except Exception as e:
            msg = str(e)
            if "đang được sử dụng" in msg:
                msg = "Lỗi: Không thể chạy, Profile đang bận up video/nuôi nick"
            self.status_update.emit(f"❌ {msg}", "red")
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

    def _launch_browser_via_gologin_sdk(self):
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
                "Profile nay chua co gologin_profile_id nen khong the mo bang GoLogin.",
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

        if self.profile_data.get("proxy", "").strip():
            ok, message = self._sync_gologin_profile_proxy()
            if not ok:
                self.status_update.emit(message, "red")
                self.finished_signal.emit("error")
                return False
            if message:
                self.status_update.emit(f"GoLogin proxy synced: {message}", "blue")
            if strict_mode:
                self.status_update.emit(
                    "GoLogin pass-through strict: da sync proxy, van giu nguyen fingerprint/header/cookie.",
                    "blue"
                )

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
        try:
            # Kiểm tra thông tin đăng nhập
            if "Đăng nhập" in self.selected_features:
                username = self.profile_data.get('username', '').strip()
                password = self.profile_data.get('password', '').strip()
                cookie = self.profile_data.get('cookie', '').strip()
                if not cookie and (not username or not password):
                    missing = []
                    if not username: missing.append("Username/Email")
                    if not password: missing.append("Password")
                    profile_name = self.profile_data.get('ten_ho_so', '')
                    self.status_update.emit(
                        f"❌ [{profile_name}] Thiếu {', '.join(missing)} - Bỏ qua!", "red"
                    )

            browser_id = self._browser_id
            self.status_update.emit(
                f"[{browser_id}] Mo profile bang GoLogin Local SDK",
                "blue"
            )

            # BƯỚC 2: Mở browser
            embedded = False
            with _GOLOGIN_START_LOCK:
                if self._stop_flag:
                    return
                self._launch_started_at = time.time()
                if not self._launch_browser_via_gologin_sdk():
                    return
                if self._stop_flag:
                    return

                # Keep the next GoLogin launch waiting until this window is embedded.
                if self.widget_id:
                    self.status_update.emit("📺 Đang bắt cửa sổ browser vào dashboard...", "blue")
                    embedded = self._request_browser_embed_from_ui(timeout=30.0)
                else:
                    self.status_update.emit("Đã tắt nhúng browser, browser sẽ mở ngoài dashboard.", "orange")

            if self.manual_only:
                if embedded:
                    self.status_update.emit("Browser đã nhúng - bạn tự thao tác", "green")
                else:
                    self.status_update.emit(
                        "⚠️ Không nhúng được browser. Browser có thể đang mở ngoài dashboard.",
                        "orange"
                    )
                while not self._stop_flag:
                    if not self._browser_alive():
                        break
                    time.sleep(0.5)
                if not self._stop_flag:
                    self.finished_signal.emit("success")
                return

            # BƯỚC 4: Chạy automation bằng CDP
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._run_cdp_automation())
            loop.close()

        except Exception as e:
            self.status_update.emit(f"Lỗi: {e}", "red")
            self.finished_signal.emit(f"error: {e}")
        finally:
            if not self._async_close_started:
                self._release_browser_session()
                self._emit_browser_closed_once("closed")

    def _prepare_profile_dir(self):
        """Tạo profile từ zero template."""
        self._process = None
        if not os.path.exists(self._profile_dir):
            self.status_update.emit(f"Tạo profile: {os.path.basename(self._profile_dir)}", "blue")
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
                self.status_update.emit("Profile từ template OK", "green")
            else:
                os.makedirs(self._profile_dir, exist_ok=True)

        # ★ FIX CRITICAL: Chrome xóa session cookies khi thấy exit_type="Crashed"
        # Patch Preferences để Chrome nghĩ lần trước đóng bình thường
        self._fix_chrome_exit_type()

    def _fix_chrome_exit_type(self):
        """Fix Preferences để Chrome giữ session cookies.
        
        TikTok dùng SESSION cookies (không có expiry) → Chrome xóa khi đóng.
        Fix: bật 'Continue where you left off' (restore_on_startup=1)
        → Chrome GIỮ session cookies qua các lần khởi động.
        """
        prefs_path = os.path.join(self._profile_dir, "Default", "Preferences")
        if not os.path.exists(prefs_path):
            return
        try:
            import json
            with open(prefs_path, "r", encoding="utf-8") as f:
                prefs = json.load(f)

            changed = False

            # Fix 1: exit_type = Normal (tránh crash recovery)
            profile = prefs.get("profile", {})
            if profile.get("exit_type", "") != "Normal":
                profile["exit_type"] = "Normal"
                profile["exited_cleanly"] = True
                prefs["profile"] = profile
                changed = True

            # ★ Fix 2: restore_on_startup = 1 ("Continue where you left off")
            # Đây là fix THẬT SỰ: Chrome sẽ GIỮ session cookies khi có setting này
            session = prefs.get("session", {})
            if session.get("restore_on_startup") != 5:
                session["restore_on_startup"] = 5  # 5 = Open New Tab page
                prefs["session"] = session
                changed = True

            if changed:
                with open(prefs_path, "w", encoding="utf-8") as f:
                    json.dump(prefs, f, ensure_ascii=False)
                self.status_update.emit("🔧 Đã bật giữ session cookies", "blue")
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
            browser_names = {"chrome.exe", "orbita-browser.exe", "chromium.exe"}
            expanded = set(pids)
            for pid in list(pids):
                try:
                    proc = psutil.Process(pid)
                    for child in proc.children(recursive=True):
                        expanded.add(child.pid)
                    for parent in proc.parents():
                        try:
                            if (parent.name() or "").lower() in browser_names:
                                expanded.add(parent.pid)
                        except Exception:
                            continue
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
                    f"Browser nhúng OK! pid={target_pid}, hwnd={target_hwnd}, port={self._debug_port}",
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
        """Liên tục ép browser về vị trí (0,-tb) — ẩn title bar + chống kéo."""
        tb = APP_TITLEBAR_HEIGHT
        last_width = 0
        last_height = 0
        last_sync = 0.0
        while not self._stop_flag:
            try:
                # Dừng nếu browser hoặc container đã bị đóng
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
        """Main automation loop dùng CDP trực tiếp."""
        from cdp_client import CDPClient

        self.status_update.emit(f"Kết nối CDP (port {self._debug_port})...", "blue")

        try:
            self._cdp = CDPClient(port=self._debug_port)
            await self._cdp.connect(timeout=15)
            self.status_update.emit("✅ CDP kết nối thành công!", "green")
        except Exception as e:
            self.status_update.emit(f"❌ CDP lỗi: {str(e)[:60]}", "red")
            self._release_browser_session()
            self.finished_signal.emit("error")
            return

        cdp = self._cdp

        # ═══════════════════════════════════════════════════════
        #  STEALTH INJECTION — Chạy TRƯỚC MỌI navigation
        #  Patch navigator.webdriver, window.chrome, v.v.
        # ═══════════════════════════════════════════════════════
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
                // ★ Patch navigator.webdriver = false (quan trọng nhất)
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => false,
                    configurable: true
                });

                // ★ Fake window.chrome (Chrome tự có nhưng automation mode thiếu)
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

                // ★ Xóa cdc_ markers (chromedriver fingerprint)
                for (const prop of Object.keys(window)) {
                    if (prop.match(/^cdc_/) || prop.match(/^\$cdc_/)) {
                        delete window[prop];
                    }
                }

                // ★ Fake permissions query (notification, push)
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications'
                        ? Promise.resolve({ state: Notification.permission })
                        : originalQuery(parameters)
                );

                // ★ Fake plugins (trình duyệt thật có plugins)
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [
                        { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                        { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
                        { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' }
                    ],
                    configurable: true
                });

                // ★ Fake languages
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en'],
                    configurable: true
                });

                // ★ Patch iframe contentWindow.navigator.webdriver
                const originalAttachShadow = Element.prototype.attachShadow;
                Element.prototype.attachShadow = function() {
                    return originalAttachShadow.apply(this, arguments);
                };

                // ★ Hide sourceURL traces of injected scripts
                // Cloudflare/TikTok checks Error stack traces for puppeteer/CDP markers
                const _Error = Error;
                const nativeErrorToString = Error.prototype.toString;
                """
            })
                self.status_update.emit("🛡️ Stealth injection OK (navigator.webdriver patched)", "green")
        except Exception as e:
            self.status_update.emit(f"⚠️ Stealth injection lỗi: {str(e)[:50]}", "orange")

        # ═══════════════════════════════════════════════════════
        #  PROXY AUTH HANDLER — Tự điền credentials khi có proxy
        #  (Không cần xử lý khi không có proxy — --no-proxy-server đã fix)
        # ═══════════════════════════════════════════════════════
        try:
            proxy_payload = self._get_proxy_payload()
            if proxy_payload:
                proxy_user = proxy_payload.get("username", "")
                proxy_pass = proxy_payload.get("password", "")
                if proxy_user and proxy_pass:
                    # ★ Có proxy có auth → bật Fetch để tự điền credentials
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
                        """Continue intercepted requests (bắt buộc khi Fetch.enable)."""
                        request_id = params.get("requestId", "")
                        if request_id:
                            try:
                                await cdp.send("Fetch.continueRequest", {"requestId": request_id})
                            except Exception:
                                pass

                    cdp.on("Fetch.authRequired", _provide_auth)
                    cdp.on("Fetch.requestPaused", _continue_request)
                    self.status_update.emit("🔑 Proxy auth handler: tự điền credentials", "blue")
        except Exception as e:
            self.status_update.emit(f"⚠️ Proxy auth setup: {str(e)[:50]}", "orange")

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

            # ★ BƯỚC 1: Ở lại New Tab — chờ 3s như người thật
            self.status_update.emit("🌐 Browser đã mở — đang ở New Tab...", "blue")
            if not await self._verify_proxy_in_browser(cdp):
                self.finished_signal.emit("error")
                return

            await asyncio.sleep(3)

            # ★ BƯỚC 2: Mở TikTok bằng đúng session đang nằm trong profile GoLogin.
            # Cookie trong tool chỉ là bản dự phòng, không bơm sớm để tránh ghi đè phiên thật.
            if has_saved_cookie:
                self.status_update.emit(
                    "🍪 Có cookie dự phòng trong tool, chưa nạp sớm để giữ nguyên phiên GoLogin.",
                    "blue",
                )

            # ★ BƯỚC 3: Mở Google ngắn, sau đó vào thẳng TikTok.
            # Không tìm/click TikTok trên Google để tránh Google unusual-traffic/CAPTCHA.
            if not await self._warmup_google_then_tiktok_direct(cdp):
                self.status_update.emit("⚠️ Warm-up lỗi — vào TikTok trực tiếp", "orange")
                await self._type_url_in_addressbar("tiktok.com", wait=6, cdp=cdp)
            if not await self._wait_for_tiktok_ready(cdp):
                reason = "TikTok kẹt ở Please wait"
                self.status_update.emit(f"❌ {reason}", "red")
                await self._hold_browser_for_action_issue(cdp, reason)
                if not self._stop_flag:
                    self.finished_signal.emit(f"error: {reason}")
                return

            # ★ BƯỚC 4: Kiểm tra session thật của profile GoLogin trước.
            profile_logged_in = await self._check_logged_in(cdp)
            if profile_logged_in:
                self.status_update.emit("✅ Profile GoLogin đang có phiên TikTok hợp lệ.", "green")
                await self._persist_tiktok_cookies(cdp)
            else:
                if has_saved_cookie and "Đăng nhập" in self.selected_features:
                    self.status_update.emit(
                        "⚠️ Profile GoLogin chưa đăng nhập; cookie dự phòng chỉ dùng trong bước Đăng nhập.",
                        "orange",
                    )
                else:
                    self.status_update.emit(
                        "⚠️ Profile GoLogin chưa đăng nhập; không nạp cookie dự phòng ở bước khởi động.",
                        "orange",
                    )

            # Ẩn viền focus/outline khi bot click
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

            # Bỏ qua popup chọn chủ đề
            await self._skip_tiktok_popup(cdp)
            await asyncio.sleep(1)

            # Chạy các chức năng
            self.status_update.emit(f"📋 Chức năng đã chọn: {self.selected_features}", "blue")
            login_ok = bool(profile_logged_in)
            any_feature_ran = False
            feature_failures = []

            if "Đăng nhập" in self.selected_features:
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
                    self.status_update.emit("❌ Đăng nhập thất bại, dừng profile", "red")
                    self.finished_signal.emit("error")
                    return

            if "Cập nhật thống kê" in self.selected_features:
                any_feature_ran = True
                login_ok = await self._ensure_logged_in_for_feature(cdp, "Cập nhật thống kê", login_ok)
                if login_ok:
                    self.status_update.emit("📊 Bắt đầu cập nhật thống kê tài khoản...", "blue")
                    stats_ok = await self._update_tiktok_stats(cdp)
                    if not stats_ok and not self._stop_flag:
                        feature_failures.append("Cập nhật thống kê chưa hoàn tất")
                elif not self._stop_flag:
                    feature_failures.append("Cập nhật thống kê chưa chạy vì profile chưa đăng nhập")

            if "Cập nhật thông tin" in self.selected_features:
                any_feature_ran = True
                login_ok = await self._ensure_logged_in_for_feature(cdp, "Cập nhật thông tin", login_ok)
                if login_ok:
                    self.status_update.emit("💰 Bắt đầu cập nhật thông tin tài khoản...", "blue")
                    info_ok = await self._update_account_financial_info(cdp)
                    if not info_ok and not self._stop_flag:
                        feature_failures.append("Cập nhật thông tin chưa hoàn tất")
                elif not self._stop_flag:
                    feature_failures.append("Cập nhật thông tin chưa chạy vì profile chưa đăng nhập")

            if "Đổi avatar" in self.selected_features:
                any_feature_ran = True
                login_ok = await self._ensure_logged_in_for_feature(cdp, "Doi avatar", login_ok)
                if login_ok:
                    if await self._wait_captcha_clear_for_action(cdp, "Doi avatar"):
                        ok = await self._change_tiktok_avatar(cdp)
                        if not ok:
                            self.status_update.emit("Doi avatar that bai - xem log chi tiet.", "red")
                            if not self._stop_flag:
                                feature_failures.append("Đổi avatar thất bại")
                    elif not self._stop_flag:
                        feature_failures.append("Đổi avatar bị dừng vì CAPTCHA/challenge")
                elif not self._stop_flag:
                    feature_failures.append("Đổi avatar chưa chạy vì profile chưa đăng nhập")

            if "Tương tác ở Feed" in self.selected_features:
                any_feature_ran = True
                login_ok = await self._ensure_logged_in_for_feature(cdp, "Feed", login_ok)
                if login_ok:
                    if await self._wait_captcha_clear_for_action(cdp, "Feed"):
                        feed_ok = await self._do_feed_interaction(cdp)
                        if not feed_ok and not self._stop_flag:
                            feature_failures.append("Tương tác Feed chưa hoàn tất")
                    elif not self._stop_flag:
                        feature_failures.append("Tương tác Feed bị dừng vì CAPTCHA/challenge")
                elif not self._stop_flag:
                    feature_failures.append("Tương tác Feed chưa chạy vì profile chưa đăng nhập")

            # ★ Flexible matching cho keyword feature — khớp cả (key), (kw), hay bất kỳ biến thể nào
            has_keyword_feature = any("từ khóa" in f for f in self.selected_features)
            if has_keyword_feature:
                any_feature_ran = True
                login_ok = await self._ensure_logged_in_for_feature(cdp, "Keyword", login_ok)
                if login_ok:
                    if await self._wait_captcha_clear_for_action(cdp, "Keyword"):
                        self.status_update.emit("🔍 Bắt đầu tương tác theo từ khóa...", "blue")
                        keyword_ok = await self._do_keyword_interaction(cdp)
                        if not keyword_ok and not self._stop_flag:
                            feature_failures.append("Tương tác từ khóa chưa hoàn tất")
                    elif not self._stop_flag:
                        feature_failures.append("Tương tác từ khóa bị dừng vì CAPTCHA/challenge")
                elif not self._stop_flag:
                    feature_failures.append("Tương tác từ khóa chưa chạy vì profile chưa đăng nhập")

            for feat in ["KYC(gologin)(pro)", "Set riêng tư(pro)", "Đổi password(firefox)(pro)",
                         "Login mail(pro)", "Xóa tài khoản(pro)"]:
                if feat in self.selected_features:
                    any_feature_ran = True
                    self.status_update.emit(f"{feat} - Đang phát triển...", "orange")
                    feature_failures.append(f"{feat} chưa triển khai")

            # Nếu KHÔNG có chức năng nào chạy → giữ browser mở
            if not self.selected_features or not any_feature_ran:
                if self.selected_features and not any_feature_ran:
                    self.status_update.emit(f"⚠️ Không có chức năng nào khớp! Features: {self.selected_features}", "orange")
                self.status_update.emit("🌍 Browser đang mở — bạn tự do sử dụng", "green")
                while not self._stop_flag:
                    await asyncio.sleep(2)

            if feature_failures and not self._stop_flag:
                reason = "; ".join(feature_failures)
                await self._hold_browser_for_action_issue(cdp, reason)
                if not self._stop_flag:
                    self.finished_signal.emit(f"error: {reason}")
                return
            # ══════════════════════════════════════════════════════
            #  KẾT THÚC: Lưu cookie → Browser.close (graceful) → Kill
            # ══════════════════════════════════════════════════════

            # ★ FIX 2 + FIX 4: LƯU COOKIE + STORAGE TRƯỚC KHI ĐÓNG
            self.status_update.emit("🍪 Lưu dữ liệu phiên trước khi đóng...", "blue")
            try:
                # Chốt TikTok ID trước khi đóng browser. Bước này click vào hồ sơ,
                # đọc @username từ URL/localStorage và emit về UI để lưu DB.
                final_logged_in = bool(login_ok)
                if not final_logged_in:
                    final_logged_in = await self._check_logged_in(cdp)
                if final_logged_in:
                    self.status_update.emit("👤 Lấy User ID trước khi đóng browser...", "blue")
                    await self._extract_profile_info(cdp, need_reload=False)

                    # Persist session cookies → 30 ngày TRƯỚC (quan trọng nhất)
                    await self._persist_tiktok_cookies(cdp)
                    # Re-save cookie text vào DB
                    await self._resave_cookies_to_db(cdp)
                    # Backup localStorage/sessionStorage
                    await self._save_tiktok_storage(cdp)
                    self.status_update.emit("✅ Đã lưu tất cả dữ liệu phiên", "green")
                else:
                    self.status_update.emit(
                        "⚠️ Browser đang chưa đăng nhập, bỏ qua lưu cookie/storage để không ghi đè phiên cũ.",
                        "orange",
                    )
            except Exception as e:
                self.status_update.emit(f"⚠️ Lưu phiên lỗi: {str(e)[:40]}", "orange")

            # BƯỚC 1: Browser.close (graceful — cho Chrome ghi cookie ra đĩa)
            self.status_update.emit("🔒 Đóng browser (graceful — chờ Chrome flush cookies)...", "blue")
            try:
                await cdp.send("Browser.close")
                await asyncio.sleep(5)  # ★ Chờ 5s cho Chrome ghi cookie + SQLite ra đĩa
            except Exception:
                pass

            # BƯỚC 2: Kill process nếu vẫn còn sống
            self._stop_flag = True
            self._release_browser_session()
            self._process = None

            # BƯỚC 3: Emit finished
            self.status_update.emit("✅ Hoàn thành!", "green")
            self.finished_signal.emit("success")

        except Exception as e:
            err_msg = str(e)
            _closed = ("connectionreset", "connectionclosed", "websocket",
                       "connection refused", "brokenpipe", "oserror",
                       "errno", "closed", "disconnect",
                       "no close frame received or sent")
            if any(s in err_msg.lower() for s in _closed):
                self.status_update.emit("🔒 Browser đã đóng.", "blue")
                self.finished_signal.emit("success")
            else:
                self.status_update.emit(f"Lỗi: {err_msg[:80]}", "red")
                self.finished_signal.emit(f"error: {e}")
        finally:
            try: await cdp.disconnect()
            except Exception: pass

    # ─── TikTok Automation ──────────────────────────────


    async def _update_account_financial_info(self, cdp):
        """Cập nhật follow, quốc gia và thông tin tiền/balance của tài khoản TikTok."""
        try:
            if not await self._check_logged_in(cdp):
                self.status_update.emit("⚠️ Chưa đăng nhập, bỏ qua cập nhật thông tin để không ghi dữ liệu rỗng.", "orange")
                return False

            info = {
                "t_follows": "",
                "country": "",
                "currency": "",
                "earned": "",
                "balance": "",
            }

            self.status_update.emit("👤 Mở trang profile để lấy follow...", "blue")
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

            self.status_update.emit("🌍 Mở cài đặt tài khoản để lấy quốc gia...", "blue")
            await self._navigate_like_human(cdp, "tiktok.com/setting/account", wait=4)
            await asyncio.sleep(1)
            country = await cdp.evaluate(r"""
            (() => {
                const labels = ['country/region', 'country', 'region', 'quốc gia', 'khu vực'];
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
                        .replace(/quốc gia/i, '')
                        .replace(/khu vực/i, '')
                        .replace(/[:：]/g, '')
                        .trim();
                    if (value && value.length <= 60) return value;
                    const parent = el.parentElement;
                    if (parent) {
                        const pText = clean(parent.innerText || parent.textContent || '');
                        value = pText
                            .replace(/country\/region/i, '')
                            .replace(/country/i, '')
                            .replace(/region/i, '')
                            .replace(/quốc gia/i, '')
                            .replace(/khu vực/i, '')
                            .replace(/[:：]/g, '')
                            .trim();
                        if (value && value.length <= 80) return value;
                    }
                }
                return '';
            })()
            """) or ""
            info["country"] = country

            self.status_update.emit("💵 Mở Balance để lấy tiền tệ/số dư...", "blue")
            await self._navigate_like_human(cdp, "tiktok.com/setting/balance", wait=5)
            await asyncio.sleep(1)
            money_info = await cdp.evaluate(r"""
            (() => {
                const body = (document.body && document.body.innerText) || '';
                const lines = body.split(/\n+/).map(s => s.trim()).filter(Boolean);
                const moneyRe = /(?:[$€£¥₫]\s*[0-9][0-9.,]*|[0-9][0-9.,]*\s*(?:USD|EUR|GBP|VND|JPY|US\$))/i;
                const moneyLines = lines.filter(line => moneyRe.test(line));
                const firstMoney = moneyLines[0] || '';
                let currency = '';
                const curMatch = firstMoney.match(/USD|EUR|GBP|VND|JPY|US\$|[$€£¥₫]/i);
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
                self.status_update.emit("⚠️ Không lấy được thông tin hợp lệ, giữ nguyên dữ liệu cũ.", "orange")
                return False

            self.profile_data.update(update_data)
            self.profile_update_signal.emit(self.profile_data)
            self.status_update.emit(
                f"✅ Info: Follow={info['t_follows'] or 'N/A'} | QG={info['country'] or 'N/A'} | Balance={info['balance'] or 'N/A'}",
                "green"
            )
            return True
        except Exception as e:
            self.status_update.emit(f"⚠️ Lỗi cập nhật thông tin: {str(e)[:60]}", "red")
            return False


    async def _update_tiktok_stats(self, cdp):
        """Quét trang Profile và Studio để cập nhật Follow, Views, Video."""
        try:
            if not await self._check_logged_in(cdp):
                self.status_update.emit("⚠️ Chưa đăng nhập, bỏ qua cập nhật thống kê để không ghi dữ liệu rỗng.", "orange")
                return False

            self.status_update.emit("🔄 Đang vào trang cá nhân...", "blue")
            await cdp.navigate("https://www.tiktok.com/profile")
            await asyncio.sleep(4)
            
            # Đợi load xong hoặc redirect xong
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
                            // Cố đếm thẻ video nếu giao diện đổi
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
                self.status_update.emit("⚠️ Không lấy được thông số từ trang cá nhân", "orange")
                return False

            if not stats.get("hasProfileSignal"):
                self.status_update.emit("⚠️ Trang profile chưa load đúng, giữ nguyên thống kê cũ.", "orange")
                return False

            followers = str(stats.get('followers', '') or '').strip()
            likes = str(stats.get('likes', '') or '').strip()
            videos = str(stats.get('videos', '') or '').strip()
            if not any([followers, likes, videos]):
                self.status_update.emit("⚠️ Thống kê trả về rỗng, giữ nguyên dữ liệu cũ.", "orange")
                return False
            
            self.status_update.emit(f"✅ Follow: {followers} | Likes: {likes} | Videos: {videos}", "green")
            
            # Gửi signal về UI để update table
            # Ta dùng profile_update_signal hoặc tự update vào profile_data
            update_data = {}
            if followers:
                update_data["t_follows"] = followers
            if likes:
                update_data["t_views"] = likes # Tạm dùng Likes cho cột T.Views vì profile chỉ hiện Likes
            if videos:
                update_data["t_video"] = videos
            self.profile_data.update(update_data)
            self.profile_update_signal.emit(self.profile_data)
            return True
            
        except Exception as e:
            self.status_update.emit(f"⚠️ Lỗi cập nhật thống kê: {str(e)[:50]}", "red")
            return False

    async def _skip_tiktok_popup(self, cdp):
        """Bỏ qua popup chọn chủ đề."""
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
                self.status_update.emit("✅ Đã bỏ qua popup chủ đề", "green")
                return
        except Exception:
            pass

        try:
            # Chọn 1 ô rồi bấm Continue
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
            self.status_update.emit("✅ Đã bỏ qua popup (Continue)", "green")
        except Exception:
            pass

    async def _reinject_cookies(self, cdp, cookie_str=None):
        """Nạp cookie dự phòng vào browser khi luồng đăng nhập/khôi phục cần dùng."""
        if cookie_str is None:
            cookie_str = self.profile_data.get("cookie", "")
        cookie_str = str(cookie_str or "")
        if not cookie_str or len(cookie_str) <= 20:
            return 0

        try:
            import time as _time
            expires_epoch = _time.time() + 30 * 24 * 3600  # 30 ngày
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
        """Dùng cookie dự phòng chỉ trong luồng đăng nhập/khôi phục."""
        cookie_str = str(cookie_str or "").strip()
        if len(cookie_str) <= 20:
            return False

        self.status_update.emit(f"🍪 Thử khôi phục phiên bằng {label}...", "blue")
        await self._restore_tiktok_storage(cdp)
        injected_count = await self._reinject_cookies(cdp, cookie_str=cookie_str)
        if injected_count <= 0:
            self.status_update.emit(f"⚠️ {label} không có cookie hợp lệ để nạp.", "orange")
            return False

        self.status_update.emit(f"🍪 Đã nạp {injected_count} cookie từ {label}", "blue")
        try:
            await cdp.send("Page.reload")
        except Exception:
            await self._navigate_like_human(cdp, "tiktok.com", wait=5)
        await asyncio.sleep(5)

        if await self._check_logged_in(cdp):
            self.status_update.emit("🔍 Cookie hợp lệ — đang xác minh ID...", "blue")
            verified_id = await self._extract_profile_info(cdp, need_reload=False)
            if verified_id:
                self.status_update.emit(f"✅ Đăng nhập cookie thành công! ({verified_id})", "green")
            else:
                self.status_update.emit("✅ Cookie login OK (chưa lấy được @username)", "green")
            await self._persist_tiktok_cookies(cdp)
            return True

        self.status_update.emit(f"⚠️ {label} không khôi phục được phiên.", "orange")
        return False

    async def _type_url_in_addressbar(self, url: str, wait: float = 6.0, cdp=None):
        """★ Mở URL từ New Tab — dùng JS location.href (an toàn đa luồng).

        Mỗi CDP session là độc lập → không conflict khi chạy nhiều profile.
        Từ chrome://newtab → chuyển về about:blank trước → rồi location.href.
        """
        text_to_type = url.replace("https://", "").replace("http://", "").replace("www.", "")
        full_url = f"https://www.{text_to_type}"
        now_ts = time.time()

        if not cdp:
            return

        # Chặn spam điều hướng liên tiếp gây loop reload.
        if self._last_nav_url == full_url and (now_ts - self._last_nav_ts) < 8:
            return

        try:
            # Chrome://newtab là trang đặc biệt — JS evaluate có thể fail
            # → chuyển về about:blank trước (trang thường)
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
                # Nếu evaluate fail → chắc chắn đang ở trang đặc biệt
                await cdp.navigate("about:blank")
                await asyncio.sleep(0.5)

            # ★ Dùng JS location.href — giống người gõ URL rồi Enter
            self.status_update.emit(f"⏳ Đang tải {text_to_type}...", "blue")
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
            f"Kiểm tra proxy trong browser: {expected_type}://{expected_host}:{proxy_payload.get('port')}",
            "blue"
        )

        try:
            await cdp.navigate(f"https://api.ipify.org?format=json&_r={int(time.time())}")
            await asyncio.sleep(3)
            raw = await cdp.evaluate("document.body ? document.body.innerText : ''") or ""
        except Exception as e:
            self.status_update.emit(f"❌ Browser không kiểm tra được proxy: {str(e)[:80]}", "red")
            return False

        try:
            data = _json.loads(raw.strip())
            browser_ip = str(data.get("ip", "")).strip()
        except Exception:
            match = re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", raw)
            browser_ip = match.group(0) if match else ""

        if not browser_ip:
            self.status_update.emit(f"❌ Proxy check không trả về IP: {raw[:80]}", "red")
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
                f"❌ Browser vẫn dùng IP máy thật ({browser_ip}), proxy chưa được áp dụng.",
                "red"
            )
            return False

        expected_is_ip = bool(re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", expected_host))
        if expected_is_ip and browser_ip != expected_host:
            self.status_update.emit(
                f"Proxy exit IP khác gateway ({browser_ip} != {expected_host}); có thể bình thường với proxy xoay/mobile.",
                "orange"
            )
            return True

        self.status_update.emit(f"✅ Browser đang đi qua proxy IP: {browser_ip}", "green")
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
            self.status_update.emit("🌐 Mở Google trước khi vào TikTok...", "blue")
            await self._type_url_in_addressbar("google.com", wait=random.uniform(2.0, 3.0), cdp=cdp)

            if await self._is_google_traffic_challenge(cdp):
                self._google_warmup_blocked = True
                self.status_update.emit("⚠️ Google hiện CAPTCHA/unusual traffic — bỏ qua và vào TikTok trực tiếp", "orange")
            else:
                await asyncio.sleep(random.uniform(0.8, 1.4))

            self.status_update.emit("➡️ Vào TikTok trực tiếp...", "blue")
            await self._type_url_in_addressbar("tiktok.com", wait=random.uniform(6.0, 7.0), cdp=cdp)
            return True
        except Exception as e:
            self.status_update.emit(f"⚠️ Warm-up Google→TikTok lỗi: {str(e)[:60]}", "orange")
            try:
                await self._type_url_in_addressbar("tiktok.com", wait=6, cdp=cdp)
                return True
            except Exception:
                return False

    async def _warmup_tiktok_via_google(self, cdp) -> bool:
        """Open Google, search TikTok, then click a TikTok result."""
        try:
            self._google_warmup_blocked = False
            self.status_update.emit("🌐 Warm-up: mở Google trước khi vào TikTok...", "blue")
            await self._type_url_in_addressbar("google.com", wait=random.uniform(2.5, 3.5), cdp=cdp)
            if await self._is_google_traffic_challenge(cdp):
                self._google_warmup_blocked = True
                self.status_update.emit("❌ Google chặn unusual traffic/CAPTCHA ngay khi mở", "red")
                return False

            # Handle Google consent if it appears.
            try:
                consent_pos = await cdp.evaluate(r"""
                (() => {
                    const norm = (s) => (s || '').toLowerCase().replace(/\s+/g, ' ').trim();
                    const labels = ['accept all', 'i agree', 'agree', 'chấp nhận tất cả', 'tôi đồng ý'];
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
                self.status_update.emit("⚠️ Warm-up: không thấy ô tìm kiếm Google", "orange")
                return False

            await self._human_move_and_click(cdp, *search_pos, "Click ô Google Search")
            await asyncio.sleep(random.uniform(0.4, 0.8))
            await cdp.type_text("TikTok", delay=random.randint(90, 180))
            await asyncio.sleep(random.uniform(0.8, 1.2))
            if await self._is_google_traffic_challenge(cdp):
                self._google_warmup_blocked = True
                self.status_update.emit("❌ Google chặn unusual traffic/CAPTCHA sau khi nhập tìm kiếm", "red")
                return False

            suggestion_pos = None
            for _ in range(8):
                if await self._is_google_traffic_challenge(cdp):
                    self._google_warmup_blocked = True
                    self.status_update.emit("❌ Google chặn unusual traffic/CAPTCHA ở trang gợi ý", "red")
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
                self.status_update.emit("👆 Warm-up: click gợi ý TikTok đầu tiên...", "blue")
                await self._human_move_and_click(
                    cdp, int(suggestion_pos["x"]), int(suggestion_pos["y"]), "Click gợi ý Google TikTok"
                )
            else:
                self.status_update.emit("⚠️ Warm-up: không thấy gợi ý Google — dùng Enter", "orange")
                await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter"})
                await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter"})

            await asyncio.sleep(random.uniform(3.0, 4.5))
            if await self._is_google_traffic_challenge(cdp):
                self._google_warmup_blocked = True
                self.status_update.emit("❌ Google chặn unusual traffic/CAPTCHA ở trang kết quả", "red")
                return False

            tiktok_pos = None
            for _ in range(8):
                if await self._is_google_traffic_challenge(cdp):
                    self._google_warmup_blocked = True
                    self.status_update.emit("❌ Google chặn unusual traffic/CAPTCHA khi tìm link TikTok", "red")
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
                self.status_update.emit("⚠️ Warm-up: không tìm thấy link TikTok trên Google", "orange")
                return False

            self.status_update.emit("👆 Warm-up: click kết quả TikTok từ Google...", "blue")
            await self._human_move_and_click(
                cdp, int(tiktok_pos["x"]), int(tiktok_pos["y"]), "Click Google result TikTok"
            )
            await asyncio.sleep(random.uniform(6.0, 8.0))
            return True
        except Exception as e:
            self.status_update.emit(f"⚠️ Warm-up lỗi: {str(e)[:60]}", "orange")
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
                        f"⚠️ Chrome kẹt lỗi 403 — làm mới cache TikTok, giữ nguyên cookie... ({elapsed}s, lần {chrome403_retries})",
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
                    self.status_update.emit(f"⏳ TikTok đang Please wait... ({elapsed}s)", "orange")

                    # ── Lần reload 1: ở giây 12 ──
                    if reload_count == 0 and elapsed >= 12:
                        reload_count = 1
                        self.status_update.emit("🔄 Reload TikTok lần 1 do kẹt Please wait...", "orange")
                        try:
                            await cdp.send("Page.reload")
                        except Exception:
                            await cdp.navigate("https://www.tiktok.com/")
                        await asyncio.sleep(5)
                        continue

                    # ── Lần reload 2: ở giây 25 — re-inject stealth trước khi reload ──
                    if reload_count == 1 and elapsed >= 25:
                        reload_count = 2
                        if self._should_preserve_gologin_fingerprint():
                            self.status_update.emit("🔄 Reload TikTok lần 2 (giu nguyen fingerprint GoLogin)...", "orange")
                        else:
                            self.status_update.emit("🔄 Reload TikTok lần 2 + re-inject stealth...", "orange")
                            try:
                                # Re-inject stealth trước khi reload
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
        """★ Navigate tới URL giống người thật — KHÔNG dùng Page.navigate (CDP).

        TikTok phát hiện `Page.navigate` (CDP programmatic) và đá session cookie.
        Giải pháp: Dùng window.location.href (JS) để chuyển trang —
        giống hệt người dùng gõ URL vào address bar rồi nhấn Enter.
        Browser vẫn ở tab hiện tại (New Tab) → chuyển sang TikTok tự nhiên.
        """
        full_url = f"https://www.{url}" if not url.startswith("http") else url
        now_ts = time.time()
        if self._last_nav_url == full_url and (now_ts - self._last_nav_ts) < 8:
            return

        # ═══════════════════════════════════════════════
        #  CÁCH 1: window.location.href (JS navigation)
        #  Chuyển trang NGAY trên tab hiện tại — giống gõ URL → Enter
        #  Browser ở New Tab → chuyển sang TikTok (cùng tab)
        # ═══════════════════════════════════════════════
        try:
            # Kiểm tra trang hiện tại — chrome://newtab không cho phép JS navigate
            current_url = await cdp.evaluate("window.location.href") or ""
            if current_url.startswith("chrome://") or current_url.startswith("chrome-search://"):
                # Trang New Tab đặc biệt → dùng cdp.navigate tới about:blank trước
                await cdp.navigate("about:blank")
                await asyncio.sleep(0.5)

            await cdp.evaluate(f'window.location.href = "{full_url}"')
            self._last_nav_url = full_url
            self._last_nav_ts = time.time()
            await asyncio.sleep(wait)
            return
        except Exception:
            pass

        # ═══════════════════════════════════════════════
        #  CÁCH 2 (Fallback): Target.createTarget (tab mới)
        # ═══════════════════════════════════════════════
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

        # ═══════════════════════════════════════════════
        #  CÁCH 3 (Last resort): cdp.navigate
        # ═══════════════════════════════════════════════
        await cdp.navigate(full_url)
        self._last_nav_url = full_url
        self._last_nav_ts = time.time()
        await asyncio.sleep(wait)


    # ─── Helper: Human-like Click với chấm đỏ ──────────────────────────────


    async def _persist_tiktok_cookies(self, cdp):
        """★ FIX 1: Biến session cookies → persistent cookies (30 ngày).

        TikTok set sessionid, sid_tt, sid_guard dưới dạng SESSION cookies
        (không có expires) → Chrome XÓA khi đóng browser.
        Fix: đọc tất cả TikTok cookies → ghi lại với expires = 30 ngày
        → Chrome lưu persistent vào đĩa, không xóa khi đóng.
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
                    "⚠️ Không thấy cookie đăng nhập TikTok, bỏ qua persist cookie.",
                    "orange",
                )
                return

            expires_epoch = _time.time() + 30 * 24 * 3600  # 30 ngày
            persisted = 0

            for cookie in tiktok_cookies:
                # Chỉ convert cookies chưa có expires (session cookies)
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
                    f"🔒 Đã chuyển {persisted} session cookie → persistent (30 ngày)", "green"
                )
        except Exception as e:
            self.status_update.emit(f"⚠️ Persist cookie lỗi: {str(e)[:40]}", "orange")

    async def _resave_cookies_to_db(self, cdp):
        """★ FIX 2: Đọc cookie mới nhất từ Chrome → lưu vào DB trước khi đóng browser.

        Đảm bảo DB luôn có bản copy cookie mới nhất để inject lại lần sau.
        """
        try:
            cookies_result = await cdp.send("Network.getAllCookies")
            cookies = cookies_result.get("cookies", [])

            tiktok_cookies = [c for c in cookies if 'tiktok' in c.get('domain', '')]
            if not tiktok_cookies:
                return
            if not self._has_valid_tiktok_auth_cookie(tiktok_cookies):
                self.status_update.emit(
                    "⚠️ Không lưu cookie mới vì browser không còn cookie đăng nhập TikTok.",
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
                    f"🍪 Đã lưu {len(tiktok_cookies)} cookie vào DB", "green"
                )
        except Exception as e:
            self.status_update.emit(f"⚠️ Lưu cookie lỗi: {str(e)[:40]}", "orange")

    async def _save_tiktok_storage(self, cdp):
        """★ FIX 4 (Trường hợp A): Backup localStorage + sessionStorage → file JSON.

        TikTok lưu thông tin user (webapp_user_info, cookie_consent, v.v.)
        trong localStorage. Dữ liệu này giúp TikTok nhận diện phiên đăng nhập
        mà không cần dựa 100% vào cookies.
        Backup dữ liệu này vào file JSON trong profile dir → restore lần sau.
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
                    f"💾 Đã backup Storage (LS:{ls_count} + SS:{ss_count})", "green"
                )
        except Exception as e:
            self.status_update.emit(f"⚠️ Backup storage lỗi: {str(e)[:40]}", "orange")

    async def _restore_tiktok_storage(self, cdp):
        """★ FIX 4 (Trường hợp A): Restore localStorage + sessionStorage từ file JSON.

        Phải gọi SAU KHI navigate tới tiktok.com (vì localStorage phụ thuộc origin).
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
                f"💾 Đã restore Storage (LS:{len(ls_data)} + SS:{len(ss_data)})", "green"
            )
        except Exception as e:
            self.status_update.emit(f"⚠️ Restore storage lỗi: {str(e)[:40]}", "orange")

    async def _ensure_cursor_dot(self, cdp):
        """Tạo cursor dot SVG 1 lần duy nhất (nếu chưa có). Dùng transform để update vị trí."""
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
        """Update vị trí cursor bằng transform (GPU accelerated, không layout reflow)."""
        await cdp.evaluate(f"""
        (() => {{
            const d = document.getElementById('__cursor_dot__');
            if (d) d.style.transform = 'translate({x}px, {y}px)';
        }})()
        """)

    async def _show_cursor_dot(self, cdp, x, y):
        """Compat wrapper — đảm bảo cursor tồn tại rồi move."""
        await self._ensure_cursor_dot(cdp)
        await self._move_cursor_dot(cdp, x, y)

    async def _smooth_mouse_drift(self, cdp, tx, ty, steps=None):
        """Di chuyển chuột mượt từ vị trí hiện tại → (tx, ty) bằng micro-bezier."""
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
        """Di chuyển chuột theo cubic bezier + smoothstep rồi click — giống người thật."""
        if label:
            self.status_update.emit(f"🖱️ {label}", "blue")

        await self._ensure_cursor_dot(cdp)

        # Lấy vị trí chuột hiện tại (gần giữa màn hình nếu chưa có)
        cur_x = getattr(self, '_mouse_x', self.container_width // 2)
        cur_y = getattr(self, '_mouse_y', self.container_height // 2)

        # Khoảng cách di chuyển → jitter tỷ lệ theo khoảng cách
        dist = max(1, ((x - cur_x)**2 + (y - cur_y)**2) ** 0.5)
        jitter_scale = min(1.0, dist / 300)  # Khoảng cách ngắn → jitter ít

        # 2 điểm điều khiển cubic bezier (jitter nhỏ hơn, tự nhiên hơn)
        jx = int(30 * jitter_scale)
        jy = int(15 * jitter_scale)
        ctrl1_x = cur_x + (x - cur_x) * random.uniform(0.2, 0.4) + random.randint(-jx, jx)
        ctrl1_y = cur_y + (y - cur_y) * random.uniform(0.2, 0.4) + random.randint(-jy, jy)
        ctrl2_x = cur_x + (x - cur_x) * random.uniform(0.6, 0.8) + random.randint(-jx//2, jx//2)
        ctrl2_y = cur_y + (y - cur_y) * random.uniform(0.6, 0.8) + random.randint(-jy//2, jy//2)

        # Nhiều bước hơn → mượt hơn
        steps = random.randint(18, 30)
        for i in range(steps + 1):
            t = i / steps
            # ★ Smoothstep easing: chậm đầu → nhanh giữa → chậm cuối
            t_ease = t * t * (3.0 - 2.0 * t)

            # Cubic bezier (4 điểm)
            bx = int((1-t_ease)**3 * cur_x + 3*(1-t_ease)**2*t_ease * ctrl1_x +
                     3*(1-t_ease)*t_ease**2 * ctrl2_x + t_ease**3 * x)
            by = int((1-t_ease)**3 * cur_y + 3*(1-t_ease)**2*t_ease * ctrl1_y +
                     3*(1-t_ease)*t_ease**2 * ctrl2_y + t_ease**3 * y)
            await cdp.send("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": bx, "y": by,
            })
            await self._move_cursor_dot(cdp, bx, by)

            # ★ Delay phi tuyến: chậm ở đầu/cuối, nhanh ở giữa
            if t < 0.15 or t > 0.85:
                await asyncio.sleep(random.uniform(0.018, 0.035))
            else:
                await asyncio.sleep(random.uniform(0.006, 0.015))

        # Lưu vị trí cuối — dùng tọa độ gốc (không jitter)
        self._mouse_x = x
        self._mouse_y = y

        # Dừng nhỏ trước khi click (giống người suy nghĩ)
        await asyncio.sleep(random.uniform(0.08, 0.18))

        # Click — chính xác vào tâm element
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
        """Lấy tọa độ center của element theo selector. Trả về (x, y) hoặc None."""
        pos = await cdp.evaluate(f"""
        (() => {{
            const el = document.querySelector('{selector}');
            if (!el) return null;
            el.scrollIntoView({{block: 'center', inline: 'center'}});
            const r = el.getBoundingClientRect();
            if (r.width === 0) return null;
            return {{x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)}};
        }})()
        """)
        if pos:
            return int(pos["x"]), int(pos["y"])
        return None

    async def _get_center_by_text(self, cdp, text):
        """Tìm element chứa text cụ thể → trả về center (x, y)."""
        pos = await cdp.evaluate(f"""
        (() => {{
            const all = Array.from(document.querySelectorAll('*'));
            for (const el of all) {{
                const tag = el.tagName.toLowerCase();
                // Bỏ qua các thẻ container lớn có nhiều con
                if ((tag === 'div' || tag === 'main' || tag === 'body') && el.childElementCount > 2) continue;

                if (el.innerText && el.innerText.trim() === '{text}') {{
                    const r = el.getBoundingClientRect();
                    // Đảm bảo không phải là khung nền to (width < 500, height < 150)
                    if (r.width > 0 && r.height > 0 && r.width < 500 && r.height < 150)
                        return {{x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)}};
                }}
            }}
            
            // Thử chứa text (fallback)
            for (const el of all) {{
                const tag = el.tagName.toLowerCase();
                if ((tag === 'div' || tag === 'main' || tag === 'body') && el.childElementCount > 2) continue;

                if (el.innerText && el.innerText.includes('{text}')) {{
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0 && r.width < 500 && r.height < 150)
                        return {{x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)}};
                }}
            }}
            return null;
        }})()
        """)
        if pos:
            return int(pos["x"]), int(pos["y"])
        return None

    async def _get_center_by_texts(self, cdp, texts):
        """Tìm theo nhiều text (ưu tiên theo thứ tự)."""
        for text in texts:
            pos = await self._get_center_by_text(cdp, text)
            if pos:
                return pos
        return None

    async def _get_login_method_option_center(self, cdp):
        """Tìm ô 'Use phone/email/username' theo cả EN/VI, chịu được xuống dòng."""
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
        """Click trực tiếp đúng ô phone/email/username, tránh bắt nhầm QR option."""
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
        """Click nút gửi mã email nếu TikTok yêu cầu bấm trước khi mail OTP được gửi."""
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
        """Lấy tâm nút Log in/Đăng nhập (ưu tiên selector ổn định)."""
        for sel in ['button[data-e2e="top-login-button"]', 'button#header-login-button']:
            pos = await self._get_center(cdp, sel)
            if pos:
                return pos

        pos = await cdp.evaluate("""
        (() => {
            const labels = ['log in', 'đăng nhập'];
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
        """Lấy ô email/username, tránh bắt nhầm ô số điện thoại hoặc mã OTP."""
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
        # Login lỗi không được xóa cookie/tiktok_id cũ; đó có thể là phiên tốt để khôi phục.
        self.profile_update_signal.emit({"login_error": error_msg})

    async def _hold_browser_for_login_recovery(self, cdp, reason: str) -> bool:
        """Keep Orbita open after auto-login fails so the user can recover manually."""
        reason = str(reason or self._last_login_error or "Dang nhap that bai").strip()
        if reason:
            self._emit_login_error(reason)

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
        reason = str(reason or "Chức năng chưa hoàn tất").strip()
        self._last_error = reason
        self.status_update.emit(
            f"⚠️ {reason}. Giữ browser mở để kiểm tra, bấm Dừng khi muốn đóng.",
            "orange",
        )

        while not self._stop_flag:
            try:
                if not self._browser_alive():
                    self.status_update.emit("Browser đã đóng trong khi chờ kiểm tra lỗi.", "gray")
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

    # ─── Login Flow ─────────────────────────────────────────────────────────

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
                'button[aria-label*="Sửa" i]'
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
                ["Edit profile", "Sửa hồ sơ", "Sua ho so"],
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
                "Thay đổi ảnh", "Thay doi anh", "Đổi ảnh", "Doi anh",
                "Avatar", "Photo", "Ảnh", "Anh",
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
                ["Apply", "Confirm", "Done", "Áp dụng", "Ap dung", "Xong"],
                timeout=4,
            )
            await asyncio.sleep(1.5)

            self.status_update.emit("Dang luu avatar...", "blue")
            saved = await self._click_tiktok_button_by_text(cdp, ["Save", "Lưu", "Luu"], timeout=12)
            if not saved:
                msg = "Khong tim thay nut Save/Luu avatar."
                self.status_update.emit(msg, "red")
                self.profile_update_signal.emit({"avatar_status": "failed", "avatar_last_error": msg})
                return False

            await asyncio.sleep(5)
            error_text = await cdp.evaluate("""
            (() => {
                const markers = ['couldn\\'t update', 'failed', 'error', 'không thể', 'loi', 'lỗi'];
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
        """Đăng nhập TikTok — 3 trường hợp:
        TH0: Đã login từ phiên trước → bỏ qua
        TH1: Auto login (có credentials) → bước 1→5 → chỉ check sau submit
        TH2: Manual login (user tự nhập) → grace period → polling check
        """
        cookie_str = self.profile_data.get("cookie", "")
        username   = self.profile_data.get("username", "").strip()
        password   = self.profile_data.get("password", "").strip()

        # ── TH0: Đã đăng nhập từ phiên trước? ──────────────────
        # Chờ TikTok render đầy đủ (cần 5-7s trên kết nối thường)
        await asyncio.sleep(6)

        # ★ Retry 3 lần cách nhau 3 giây (tổng ~15s chờ)
        already_logged = False
        for attempt in range(3):
            if await self._check_logged_in(cdp):
                already_logged = True
                break
            if attempt < 2:
                self.status_update.emit(
                    f"🔍 Kiểm tra đăng nhập... (lần {attempt+2})", "blue"
                )
                await asyncio.sleep(3)

        if already_logged:
            # ★ Verify lần cuối: chờ page render xong hẳn rồi check DOM
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
                        (btn.textContent.trim() === 'Log in' || btn.textContent.trim() === 'Đăng nhập'))
                        return true;
                }
                return false;
            })()
            """) or False

            if final_has_login:
                # Nút Log in vẫn hiện → session thực sự hết hạn
                self.status_update.emit("⚠️ Session hết hạn — cần đăng nhập lại...", "orange")
                already_logged = False
            else:
                self.status_update.emit("🔍 Phát hiện phiên trước — lấy thông tin...", "blue")
                verified_id = await self._extract_profile_info(cdp, need_reload=False)
                if verified_id:
                    self.status_update.emit(f"✅ Đã đăng nhập từ phiên trước! ({verified_id})", "green")
                else:
                    self.status_update.emit("✅ Đã đăng nhập (đang lấy hồ sơ...)", "green")
                # ★ FIX 1: Biến session cookies → persistent (30 ngày)
                await self._persist_tiktok_cookies(cdp)
                return True

        # ── Chưa login → Chỉ lúc này mới thử cookie dự phòng từ tool ──
        cookie_sources = [
            ("cookie chính trong tool", cookie_str),
            ("cookie backup trước đó", self.profile_data.get("cookie_backup", "")),
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
            self.status_update.emit("⚠️ Cookie đã lưu không dùng được — chuyển sang đăng nhập...", "orange")

        # ── Kiểm tra có credentials để auto login không ─────────
        has_credentials = bool(username and password)

        if not has_credentials:
            # Không có credentials → chuyển thẳng sang chờ thủ công
            self.status_update.emit("👤 Không có Email/Password — chờ đăng nhập thủ công...", "orange")
            return await self._wait_manual_login(cdp)

        self.status_update.emit("Mo thang trang TikTok email login...", "blue")
        return await self._do_login_direct(cdp, username, password, force_direct_url=True)


        # ── BƯỚC 1: Click nút [Log in] trên trang hiện tại ──────────
        # Trang đã load sẵn từ _run_cdp_automation → KHÔNG navigate lại
        await asyncio.sleep(1)

        self.status_update.emit("👆 Bước 1: Click Log in...", "blue")
        login_btn_pos = await self._get_login_button_center(cdp)
            
        if not login_btn_pos:
            self.status_update.emit("⚠️ Không tìm thấy nút Log in — thử direct URL", "orange")
            return await self._do_login_direct(cdp, username, password)

        await self._human_move_and_click(cdp, *login_btn_pos, "Click nút Log in")
        
        # Đợi Modal đăng nhập hiện lên
        modal_opened = False
        for _ in range(5):
            await asyncio.sleep(1)
            # Check xem modal đã hiện chưa
            modal = await self._get_login_method_option_center(cdp)
            if not modal:
                modal = await self._get_center_by_texts(cdp, [
                    "Use phone / email / username",
                    "Use phone or email",
                    "Use phone/email",
                    "Sử dụng số điện thoại hoặc email",
                    "Sử dụng số điện thoại / email / tên người dùng",
                    "Sử dụng điện thoại / email / tên người dùng",
                    "Tiếp tục bằng điện thoại hoặc email",
                ])
            if modal:
                modal_opened = True
                break

        if not modal_opened:
            self.status_update.emit("⚠️ JS Click dự phòng (Log in)...", "orange")
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
                    "Sử dụng số điện thoại hoặc email",
                    "Sử dụng số điện thoại / email / tên người dùng",
                    "Sử dụng điện thoại / email / tên người dùng",
                    "Tiếp tục bằng điện thoại hoặc email",
                ])
            if modal: modal_opened = True

        if not modal_opened:
            self.status_update.emit("⚠️ Modal không mở — thử direct URL", "orange")
            return await self._do_login_direct(cdp, username, password)

        # ── BƯỚC 2: Click [Use phone / email / username] ────────
        self.status_update.emit("👆 Bước 2: Click Use phone/email...", "blue")
        phone_pos = await self._get_login_method_option_center(cdp)
        if not phone_pos:
            phone_pos = await self._get_center_by_texts(cdp, [
                "Use phone / email / username",
                "Use phone or email",
                "Use phone/email",
                "Sử dụng số điện thoại hoặc email",
                "Sử dụng số điện thoại / email / tên người dùng",
                "Sử dụng điện thoại / email / tên người dùng",
                "Tiếp tục bằng điện thoại hoặc email",
            ])
        if not phone_pos:
            phone_pos = await self._get_center_by_texts(cdp, [
                "Continue with phone",
                "Use phone or email",
                "Tiếp tục bằng điện thoại",
                "Sử dụng số điện thoại hoặc email",
            ])

        if not phone_pos:
            self.status_update.emit("⚠️ Không tìm thấy Use phone/email — thử direct URL", "orange")
            return await self._do_login_direct(cdp, username, password)

        await self._human_move_and_click(cdp, *phone_pos, "Click Use phone/email")
        await asyncio.sleep(1)

        # Nếu click tọa độ không ăn, dùng JS click đúng item đã match text.
        tab_probe = await self._get_center_by_texts(cdp, [
            "Log in with email or username",
            "Use email or username",
            "Use email",
            "Sử dụng email hoặc tên người dùng",
            "Đăng nhập bằng email hoặc tên người dùng",
            "Log in with email",
            "Đăng nhập bằng email",
        ])
        if not tab_probe:
            if await self._click_login_method_option_js(cdp):
                self.status_update.emit("👆 JS Click Use phone/email...", "blue")
                await asyncio.sleep(1.5)
        
        # Đợi tab email hiện lên
        email_ready = False
        for _ in range(4):
            await asyncio.sleep(1)
            tab = await self._get_center_by_texts(cdp, [
                "Log in with email or username",
                "Use email or username",
                "Use email",
                "Sử dụng email hoặc tên người dùng",
                "Đăng nhập bằng email hoặc tên người dùng",
                "Log in with email",
                "Đăng nhập bằng email",
            ])
            if tab:
                email_ready = True
                break
                
        if not email_ready:
            self.status_update.emit("⚠️ JS Click dự phòng (Use phone)...", "orange")
            await cdp.evaluate("""
            (() => {
                const all = document.querySelectorAll('div, button, a, p, span, label');
                for (const el of all) {
                    if (el.innerText && (
                        el.innerText.includes('Use phone / email') ||
                        el.innerText.includes('Use phone or email') ||
                        el.innerText.includes('Use phone/email') ||
                        el.innerText.includes('Sử dụng số điện thoại hoặc email') ||
                        el.innerText.includes('Sử dụng số điện thoại / email / tên người dùng') ||
                        el.innerText.includes('Sử dụng điện thoại / email / tên người dùng') ||
                        el.innerText.includes('Tiếp tục bằng điện thoại hoặc email')
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
                "Sử dụng email hoặc tên người dùng",
                "Đăng nhập bằng email hoặc tên người dùng",
                "Log in with email",
                "Đăng nhập bằng email",
            ])
            if tab: email_ready = True

        # ── BƯỚC 3: Click [Log in with email or username] ───────
        if email_ready:
            self.status_update.emit("👆 Bước 3: Click tab email/username...", "blue")
            email_tab_pos = await self._get_center_by_texts(cdp, [
                "Log in with email or username",
                "Use email or username",
                "Use email",
                "Sử dụng email hoặc tên người dùng",
                "Đăng nhập bằng email hoặc tên người dùng",
                "Log in with email",
                "Đăng nhập bằng email",
            ])
            
            if email_tab_pos:
                await self._human_move_and_click(cdp, *email_tab_pos, "Click tab email/username")
            await asyncio.sleep(random.uniform(1.0, 1.5))
        else:
            self.status_update.emit("⚠️ Không thấy tab email — thử direct URL", "orange")
            return await self._do_login_direct(cdp, username, password)

        # ── BƯỚC 4: Nhập Email + Password ──────────────────────
        return await self._do_login_direct(cdp, username, password)

    async def _do_login_direct(self, cdp, username, password, force_direct_url=False):
        """Bước cuối: nhập email/pass và submit (dùng được độc lập nếu đã ở form)."""
        # Chờ ô email/username. Nếu modal không mở đúng cách, rẽ sang URL login email thật sự.
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
            self.status_update.emit("⚠️ Chưa thấy form login — mở URL đăng nhập email trực tiếp...", "orange")
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
                self.status_update.emit("❌ Timeout chờ form đăng nhập", "red")
                return False

        await asyncio.sleep(random.uniform(0.5, 1.0))

        # Gõ email
        self.status_update.emit("⌨️ Bước 4a: Nhập Email...", "blue")
        pos = email_pos or await self._get_center(cdp, 'input[name="username"]')
        if pos:
            await self._human_move_and_click(cdp, *pos, "Click ô Email")
        await asyncio.sleep(random.uniform(0.3, 0.6))
        if not await self._type_active_input_exact(cdp, username, "Email"):
            self._emit_login_error("Tool khong nhap dung Email - da dung de tranh mat luot thu")
            return False
        await asyncio.sleep(random.uniform(0.4, 0.8))

        # Gõ password
        self.status_update.emit("⌨️ Bước 4b: Nhập Password...", "blue")
        pos = await self._get_center(cdp, 'input[type="password"]')
        if pos:
            await self._human_move_and_click(cdp, *pos, "Click ô Password")
        await asyncio.sleep(random.uniform(0.3, 0.6))
        if not await self._type_active_input_exact(cdp, password, "Password", secret=True):
            self._emit_login_error("Tool khong nhap dung Password - da dung de tranh mat luot thu")
            return False
        await asyncio.sleep(random.uniform(0.5, 1.0))

        # Click nút Login
        self.status_update.emit("👆 Bước 5: Click nút Log in...", "blue")
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
            pos = await self._get_center_by_texts(cdp, ["Log in", "Đăng nhập"])
        if pos:
            await self._human_move_and_click(cdp, *pos, "Click Login")
        else:
            self._emit_login_error("Khong tim thay nut Log in")
            self.status_update.emit("❌ Không tìm thấy nút Log in", "red")
            return False

        # ── BƯỚC 5: Chờ kết quả ────────────────────────────────
        TOTAL_WAIT = 120   # giây (sẽ tăng thêm khi có CAPTCHA)
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

        for step in range(999):  # Vòng lặp mở, thoát bằng điều kiện
            elapsed = (step + 1) * POLL
            if elapsed > TOTAL_WAIT + captcha_state.get("extra_time", 0):
                if captcha_state.get("active"):
                    error_msg = f"Ket CAPTCHA qua {captcha_state.get('timeout', 300)}s"
                    self.status_update.emit(error_msg, "red")
                    self._emit_login_error(error_msg)
                    return False
                break  # Hết thời gian

            await asyncio.sleep(POLL)
            if self._stop_flag:
                return

            remaining = TOTAL_WAIT + captcha_state.get("extra_time", 0) - elapsed

            # CAPTCHA gate: khi CAPTCHA còn hiện thì không check lỗi/OTP/success.
            captcha_gate = await self._handle_captcha_gate(cdp, captcha_state, remaining, poll=POLL)
            if captcha_gate.get("failed"):
                return False
            if captcha_gate.get("blocked"):
                continue

            self.status_update.emit(f"⏳ Chờ đăng nhập... ({remaining}s)", "blue")

            # ─ KIỂM TRA LỖI TRƯỚC — dòng chữ đỏ trên form login ─
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
                           body.includes("Xác minh đó là bạn") ||
                           body.includes("Xác minh danh tính") ||
                           body.includes("Xác minh đó thực sự là bạn") ||
                           body.includes("Xác minh danh tính");
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
                            "Gửi mã qua email",
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
                // === Cách 1: Tìm element có text màu đỏ trên form login ===
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

                // === Cách 2: Text-based detection ===
                const body = document.body.innerText || '';

                // Sai mật khẩu / sai tài khoản (CHÍNH XÁC như screenshot TikTok)
                if(body.includes("Incorrect account or password") || body.includes("Incorrect password")
                   || body.includes("doesn't match our records") || body.includes("Sai mật khẩu")
                   || body.includes("password is incorrect") || body.includes("Sai tài khoản"))
                    return "Sai tài khoản hoặc mật khẩu";

                // Còn bao nhiêu lần thử
                const attemptsMatch = body.match(/(\\d+)\\s*attempts?\\s*remaining/i);
                if (attemptsMatch)
                    return "Sai tài khoản/mật khẩu. Còn " + attemptsMatch[1] + " lần thử";

                // Email/Username không tồn tại
                if(body.includes("Couldn't find your account") || body.includes("account not found")
                   || body.includes("user does not exist") || body.includes("không tìm thấy tài khoản")
                   || body.includes("This username isn't registered")
                   || body.includes("Tên người dùng này chưa được đăng ký"))
                    return "Email/Username không tồn tại";

                // Vượt quá số lần thử
                if(body.includes("Maximum number of attempts") || body.includes("vượt quá số lần")
                   || body.includes("Too many attempts") || body.includes("too many failed attempts")
                   || body.includes("0 attempts remaining"))
                    return "Vượt quá số lần thử";

                // Tài khoản bị khóa/đình chỉ
                if(body.includes("Account currently locked") || body.includes("tạm thời bị khóa")
                   || body.includes("account has been suspended") || body.includes("tài khoản đã bị đình chỉ")
                   || body.includes("account has been banned") || body.includes("permanently banned"))
                    return "Tài khoản bị khóa/đình chỉ";

                // Lỗi mạng / hệ thống
                if(body.includes("Something went wrong") || body.includes("Đã xảy ra lỗi")
                   || body.includes("network error") || body.includes("try again later"))
                    return "Lỗi hệ thống TikTok";

                return '';
            })()
            """)
            if error_msg:
                self.status_update.emit(f"❌ {error_msg}", "red")
                # Emit lỗi về Dashboard để cập nhật cột Logged
                self._emit_login_error(error_msg)
                return False

            # ─ Kiểm tra đăng nhập thành công (CHỈ sau khi xác nhận KHÔNG có lỗi) ─
            if await self._check_logged_in(cdp):
                self.status_update.emit("✅ Đăng nhập thành công!", "green")
                await self._extract_profile_info(cdp)
                # ★ FIX 1: Biến session cookies → persistent (30 ngày)
                await self._persist_tiktok_cookies(cdp)
                return True

            # ─ Xử lý popup "Verify it's really you" (chọn Email) ─
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
                           body.includes("Xác minh đó thực sự là bạn") ||
                           body.includes("Xác minh danh tính");
                })()
                """)
                if needs_verify:
                    verify_clicked = True
                    self.status_update.emit("⚠️ TikTok yêu cầu xác minh danh tính...", "orange")
                    email_btn = await self._get_verify_email_option_center(cdp)
                    if not email_btn:
                        email_btn = await self._get_center_by_texts(cdp, [
                        "Email",
                        "Gửi mã qua email",
                        ])
                    if email_btn:
                        await self._human_move_and_click(cdp, *email_btn, "Chọn xác minh Email")
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
                    continue # Chờ trang OTP load

            # ─ Nếu TikTok hiện popup/nút "Gửi mã" thì phải bấm trước khi IMAP có mail ─
            if not mail_code_requested:
                if await self._click_mail_code_send_button_if_present(cdp):
                    mail_code_requested = True
                    self.status_update.emit("📧 Đã bấm gửi mã email — chờ mail OTP...", "blue")
                    await asyncio.sleep(5)
                    continue

            # ─ Kiểm tra OTP (chỉ xử lý 1 lần) ─
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
                               body.includes('Nhập mã gồm 6 chữ số') ||
                               body.includes('mã xác minh');
                    })()
                    """)
                    if has_otp:
                        otp_handled = True
                        if not mail_code_requested:
                            if await self._click_mail_code_send_button_if_present(cdp):
                                mail_code_requested = True
                                self.status_update.emit("📧 Đã bấm gửi mã email — chờ mail OTP...", "blue")
                                await asyncio.sleep(5)
                        self.status_update.emit("📧 Cần OTP — đang lấy qua IMAP...", "orange")
                        imap_pass = self.profile_data.get("password_mail", "") or password
                        otp_code  = await self._get_tiktok_code_via_imap(username, imap_pass)
                        if otp_code:
                            self.status_update.emit(f"🔑 Nhập OTP: {otp_code}", "blue")
                            pos = await self._get_center(cdp,
                                'input[autocomplete="one-time-code"], input[name="code"], input[placeholder*="6"]')
                            if pos:
                                await self._human_move_and_click(cdp, *pos, "Click ô OTP")
                            await cdp.type_text(otp_code, delay=random.randint(80, 150))
                            await asyncio.sleep(random.uniform(0.8, 1.2))
                            if not await self._click_otp_submit_button(cdp):
                                self.status_update.emit("⚠️ Không click được nút Tiếp OTP — thử Enter", "orange")
                                await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter"})
                                await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter"})
                        else:
                            self.status_update.emit("❌ IMAP không lấy được OTP", "red")
                            return False
                except Exception:
                    pass

        # ── TH2: Auto login thất bại → chờ người dùng tự nhập ──
        return await self._wait_manual_login(cdp)

    async def _wait_manual_login(self, cdp):
        """TH2: Chờ người dùng tự đăng nhập thủ công (5 phút).
        Polling mỗi 3 giây kiểm tra _check_logged_in().
        """
        self.status_update.emit("👤 Chờ đăng nhập thủ công (5 phút)...", "orange")
        GRACE = 300  # 5 phút
        GRACE_POLL = 3
        for step in range(GRACE // GRACE_POLL):
            await asyncio.sleep(GRACE_POLL)
            if self._stop_flag:
                return False
            remaining = GRACE - (step + 1) * GRACE_POLL
            self.status_update.emit(f"👤 Chờ đăng nhập thủ công... ({remaining}s)", "orange")
            if await self._check_logged_in(cdp):
                self.status_update.emit("✅ Phát hiện đăng nhập thành công!", "green")
                await self._extract_profile_info(cdp)
                # ★ FIX 1: Biến session cookies → persistent (30 ngày)
                await self._persist_tiktok_cookies(cdp)
                return True

        self.status_update.emit("❌ Hết thời gian — Không đăng nhập được", "red")
        return False


    async def _check_logged_in(self, cdp) -> bool:
        """Kiểm tra đã đăng nhập TikTok chưa — sessionid + DOM verify."""
        try:
            # Đọc cookie qua CDP (đọc được HttpOnly!)
            cookies = await cdp.get_cookies()
            has_auth_cookie = self._has_valid_tiktok_auth_cookie(cookies)
            if not has_auth_cookie:
                return False

            # ★ Có cookie đăng nhập → check DOM (nút "Log in" có hiện không?)
            # TikTok sau inject cookie: nút Login vẫn hiện 2-3s rồi mới ẩn
            # → Retry tối đa 3 lần, mỗi lần 1s
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
                            (btn.textContent.trim() === 'Log in' || btn.textContent.trim() === 'Đăng nhập'))
                            return true;
                    }
                    return false;
                })()
                """) or False

                if not has_login_btn:
                    # Có auth cookie + KHÔNG có nút Login → ĐÃ LOGIN ✅
                    return True

                # Có auth cookie + CÓ nút Login → TikTok chưa xử lý xong
                if attempt < 2:
                    await asyncio.sleep(1)  # Chờ TikTok process cookie

            # Sau 3 lần vẫn có Login btn → cookie thật sự hết hạn
            return False
        except Exception:
            return False

    async def _extract_profile_info(self, cdp, need_reload=True) -> str:
        """Lấy @username + cookie — click avatar → vào profile → đọc URL.
        need_reload: True khi login thường (cần reload hiện avatar), False khi bơm cookie (đã reload).
        Returns: tiktok_id (str) nếu thành công, rỗng nếu thất bại."""
        try:
            # ★ BƯỚC 1: Reload trang CHỈ KHI login thường (TikTok bug: avatar chưa hiện)
            if need_reload:
                self.status_update.emit("🔄 Reload trang để hiển thị hồ sơ...", "blue")
                try:
                    await cdp.send("Page.reload")
                except Exception:
                    await self._navigate_like_human(cdp, "tiktok.com", wait=5)
                await asyncio.sleep(5)
            else:
                # Bơm cookie xong → skip popup trước khi tìm avatar
                await self._skip_tiktok_popup(cdp)
                await asyncio.sleep(1)

            # ─ Lấy cookie qua CDP ─
            cookies = await cdp.get_cookies()
            tiktok_cookies = [c for c in cookies if 'tiktok' in c.get('domain', '')]
            cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in tiktok_cookies])
            if not cookie_str:
                cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])

            # ★ BƯỚC 2: Click vào avatar/profile icon ở sidebar
            self.status_update.emit("👤 Click vào hồ sơ...", "blue")

            profile_pos = await cdp.evaluate(r"""
            (() => {
                // Cách 1: Link profile chính thức
                const navProfile = document.querySelector('a[data-e2e="nav-profile"]');
                if (navProfile) {
                    const r = navProfile.getBoundingClientRect();
                    if (r.width > 0) return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)};
                }

                // Cách 2: Link /@username trong sidebar (x < 100)
                const sideLinks = document.querySelectorAll('a[href*="/@"]');
                for (const a of sideLinks) {
                    const r = a.getBoundingClientRect();
                    if (r.width > 0 && r.x < 100) {
                        // Ưu tiên link ở dưới cùng (profile thường ở cuối sidebar)
                        if (r.y > 400)
                            return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)};
                    }
                }
                // Fallback: link /@... đầu tiên trong sidebar
                for (const a of sideLinks) {
                    const r = a.getBoundingClientRect();
                    if (r.width > 0 && r.x < 100)
                        return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)};
                }

                // Cách 3: Avatar nhỏ ở sidebar
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
                await self._human_move_and_click(cdp, profile_pos['x'], profile_pos['y'], "Click hồ sơ")
                await asyncio.sleep(3)

                # ★ BƯỚC 3: Đọc @username từ URL (chính xác 100%)
                tiktok_id = await cdp.evaluate(r"""
                (() => {
                    const match = location.pathname.match(/^\/@([^/?]+)/);
                    if (match) return '@' + match[1];
                    return '';
                })()
                """) or ""

            # ★ Fallback: localStorage
            display_name = ""
            if not tiktok_id:
                self.status_update.emit("🔍 Thử localStorage...", "blue")
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

            # ─ Kết quả ─
            if tiktok_id:
                self.status_update.emit(f"✅ Lưu hồ sơ: {tiktok_id}", "green")
            else:
                self.status_update.emit("⚠️ Không lấy được @username", "orange")
            # ★ KHÔNG navigate đi đâu — giữ nguyên trang, Feed tự xử lý sau

            # ─ Emit → Dashboard ─
            self.profile_update_signal.emit({
                "tiktok_id": tiktok_id,
                "cookie": cookie_str,
                "display_name": display_name,
                "refresh_token": self.profile_data.get("refresh_token", ""),
            })
            return tiktok_id

        except Exception as e:
            self.status_update.emit(f"❌ Lỗi lấy hồ sơ: {str(e)[:60]}", "red")
            return ""

    async def _get_tiktok_code_via_imap(self, email, password):
        """Lấy OTP bằng module dùng chung với bảng Đăng Ký."""
        try:
            from hotmail_otp import fetch_otp_from_email
        except Exception as e:
            self.status_update.emit(f"❌ Không import được hotmail_otp: {str(e)[:60]}", "red")
            return None

        email = (email or "").strip()
        mailbox_password = (password or "").strip()
        refresh_token = self.profile_data.get("refresh_token", "").strip()
        client_id = self.profile_data.get("client_id", "").strip()

        if not email:
            self.status_update.emit("❌ Không có email để lấy OTP", "red")
            return None
        if not mailbox_password and not refresh_token:
            self.status_update.emit("❌ Cần password_mail hoặc refresh_token để lấy OTP", "red")
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
            self.status_update.emit(f"❌ Lấy OTP lỗi: {str(e)[:80]}", "red")
            return None

        if not isinstance(result, dict):
            self.status_update.emit("❌ Lấy OTP không trả về kết quả hợp lệ", "red")
            return None

        new_rt = (result.get("new_refresh_token") or "").strip()
        if new_rt and new_rt != refresh_token:
            self.profile_data["refresh_token"] = new_rt
            self.profile_update_signal.emit({"refresh_token": new_rt})
            self.status_update.emit("🔄 Đã cập nhật refresh_token mới", "blue")

        otp = (result.get("otp") or "").strip()
        if result.get("status") == "success" and otp:
            return otp

        message = result.get("message") or "Không lấy được OTP"
        self.status_update.emit(f"❌ {message}", "red")
        return None

    async def _get_microsoft_oauth_token(self, email, password):
        """Lấy OAuth2 access token cho Hotmail/Outlook.
        Ưu tiên: refresh_token → ROPC (fallback).
        Đọc refresh_token và client_id từ profile_data.
        """
        try:
            import msal
        except ImportError:
            self.status_update.emit("❌ Thiếu msal: pip install msal", "red")
            return None

        try:
            # Lấy config từ profile
            refresh_token = self.profile_data.get("refresh_token", "").strip()
            client_id = self.profile_data.get("client_id", "").strip()

            # Fallback Client ID: Thunderbird public client
            if not client_id:
                client_id = "08162f7c-0fd2-4200-a84a-f25a4db0b584"

            AUTHORITY = "https://login.microsoftonline.com/consumers"
            SCOPES = ["https://outlook.office365.com/IMAP.AccessAsUser.All"]

            app = msal.PublicClientApplication(client_id, authority=AUTHORITY)

            # ═══ Cách 1: Dùng Refresh Token (ưu tiên) ═══
            if refresh_token:
                self.status_update.emit("🔑 Đang lấy token bằng Refresh Token...", "blue")
                result = app.acquire_token_by_refresh_token(
                    refresh_token=refresh_token,
                    scopes=SCOPES
                )
                if "access_token" in result:
                    # Cập nhật refresh_token mới (Microsoft trả về mới mỗi lần)
                    new_rt = result.get("refresh_token", "")
                    if new_rt and new_rt != refresh_token:
                        self.profile_data["refresh_token"] = new_rt
                        self.status_update.emit("🔄 Refresh token đã được cập nhật", "blue")
                    self.status_update.emit("✅ OAuth token OK (refresh)", "green")
                    return result["access_token"]
                else:
                    error = result.get("error_description", result.get("error", ""))
                    self.status_update.emit(f"⚠️ Refresh token lỗi: {str(error)[:50]}", "orange")
                    # Refresh token hết hạn → thử ROPC

            # ═══ Cách 2: ROPC flow (fallback — cần tài khoản không có 2FA) ═══
            if password:
                self.status_update.emit("🔑 Thử ROPC flow...", "blue")
                result = app.acquire_token_by_username_password(
                    username=email,
                    password=password,
                    scopes=SCOPES
                )
                if "access_token" in result:
                    # Lưu refresh_token mới để lần sau dùng
                    new_rt = result.get("refresh_token", "")
                    if new_rt:
                        self.profile_data["refresh_token"] = new_rt
                        self.status_update.emit("🔄 Đã lấy được refresh token mới", "blue")
                    self.status_update.emit("✅ OAuth token OK (ROPC)", "green")
                    return result["access_token"]
                else:
                    error = result.get("error_description", result.get("error", "Unknown"))
                    self.status_update.emit(f"❌ OAuth lỗi: {str(error)[:60]}", "red")
                    return None

            self.status_update.emit("❌ Không có refresh_token và password_mail", "red")
            return None

        except Exception as e:
            self.status_update.emit(f"❌ OAuth exception: {str(e)[:60]}", "red")
            return None

    # ════════════════════════════════════════════════════════════════
    #  HELPER: kiểm tra tỉ lệ %
    # ════════════════════════════════════════════════════════════════

    def _hit(self, key: str) -> bool:
        """True nếu ngẫu nhiên rơi vào tỉ lệ % cài trong feed_settings."""
        pct = self.feed_settings.get(key, 0)
        return pct > 0 and random.randint(1, 100) <= pct

    # ════════════════════════════════════════════════════════════════
    #  HELPER: Phát hiện loại video (LIVE / Ads / restricted / normal)
    # ════════════════════════════════════════════════════════════════

    async def _detect_video_type(self, cdp) -> str:
        """
        Phát hiện loại video đang hiển thị trên màn hình.
        Returns: 'normal' | 'live' | 'ads' | 'restricted' | 'no_comment'
        """
        vtype = await cdp.evaluate("""
        (() => {
            // ── LIVE? Chỉ kiểm tra badge LIVE rõ ràng ──
            const liveEls = document.querySelectorAll(
                '[data-e2e*="live"], [class*="LiveBadge"], [class*="LiveTag"]'
            );
            for (const el of liveEls) {
                const t = (el.innerText || '').trim();
                if (t === 'LIVE' || t === 'LIVE now' || t.includes('watch LIVE'))
                    if (el.offsetWidth > 0) return 'live';
            }

            // ── Ads / Sponsored? Chỉ kiểm tra label trực tiếp ──
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

            // ── Video bình thường: kiểm tra comment khả dụng ──
            // Mode 1: ForYou feed — có icon comment trên sidebar
            const cmtIcon = document.querySelector(
                '[data-e2e="comment-icon"]'
            );
            if (cmtIcon && cmtIcon.getBoundingClientRect().width > 0)
                return 'normal';

            // Mode 2: Full-page video — comment panel đã mở sẵn bên phải
            const cmtPanel = document.querySelector(
                '[data-e2e="comment-input"], [data-e2e="comment-list"],' +
                ' div[class*="CommentListContainer"], div[class*="comment-list"],' +
                ' div[contenteditable="true"]'
            );
            if (cmtPanel && cmtPanel.getBoundingClientRect().width > 0)
                return 'normal';

            // Mode 3: Kiểm tra số comment hiển thị (text "937" bên cạnh icon)
            const cmtCount = document.querySelector(
                '[data-e2e="comment-count"], strong[data-e2e="comment-count"]'
            );
            if (cmtCount && cmtCount.getBoundingClientRect().width > 0)
                return 'normal';

            return 'no_comment';
        })()
        """)
        result = vtype or 'normal'
        self.status_update.emit(f"🔍 Video type: {result}", "blue")
        return result

    # ════════════════════════════════════════════════════════════════
    #  HELPER: Tương tác Comment (mở panel, like, clone, view more)
    # ════════════════════════════════════════════════════════════════

    EMOJIS = ["😂","❤️","🔥","😍","👏","🎉","💯","😭","🥰",
              "✨","🤣","😊","👍","💕","🙌","🫶","😮","😘","🤩","🫀"]

    async def _open_comment_panel(self, cdp) -> bool:
        """Click icon comment để mở panel. Trả True nếu đã mở sẵn hoặc mở thành công."""

        # ── Hàm check panel đã mở ──
        async def _is_panel_open():
            return await cdp.evaluate("""
            (() => {
                // ForYou: comment panel mở sẵn bên phải (comment list hiển thị)
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

                // Ô nhập comment
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
                    ' [placeholder*="bình luận" i]'
                );
                for (const el of placeholders) {
                    if (el.getBoundingClientRect().width > 0) return 'placeholder';
                }

                return null;
            })()
            """)

        # ── Check 1: panel đã mở sẵn? ──
        status = await _is_panel_open()
        if status:
            self.status_update.emit(f"💬 Comment panel đã mở ({status})", "blue")
            return True

        # ── Check 2: click icon comment ──
        self.status_update.emit("💬 Mở comment panel...", "blue")
        icon_sels = [
            '[data-e2e="comment-icon"]',
            'span[data-e2e="comment-icon"]',
            'button[data-e2e="comment-icon"]',
            '[data-e2e="comment-count"]',
        ]
        for sel in icon_sels:
            pos = await self._get_center(cdp, sel)
            if pos:
                await self._human_move_and_click(cdp, *pos, "Click icon 💬")
                await asyncio.sleep(random.uniform(2.0, 3.0))

                # Verify panel đã mở
                status = await _is_panel_open()
                if status:
                    self.status_update.emit(f"💬 Panel đã mở sau click ({status})", "green")
                    return True

        # ── Check 3: Fallback — ForYou có thể hiển thị comment text trực tiếp ──
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
            self.status_update.emit("💬 Comment items hiện diện — panel mở", "green")
            return True

        self.status_update.emit("⚠️ Không mở được comment panel", "orange")
        return False

    async def _like_comments(self, cdp, video_idx: int):
        """Thả tim ngẫu nhiên N comment (giới hạn max_like_cmt)."""
        max_n = self.feed_settings.get('max_like_cmt', 5)
        n = random.randint(1, max(1, max_n))
        self.status_update.emit(f"❤️ Video #{video_idx}: Thả tim {n} comment...", "blue")

        # Lấy danh sách icon tim của comment
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
        """Click nút 'View more replies' / 'Xem thêm' comment."""
        self.status_update.emit(f"👀 Video #{video_idx}: Xem thêm comment...", "blue")
        pos = await self._get_center_by_text(cdp, "View more replies")
        if not pos:
            pos = await self._get_center_by_text(cdp, "Xem thêm")
        if not pos:
            # Tìm bất kỳ nút "view more" kiểu generic
            pos = await cdp.evaluate("""
            (() => {
                const all = Array.from(document.querySelectorAll('*'));
                for (const el of all) {
                    if (el.innerText && (
                        el.innerText.includes('View more') ||
                        el.innerText.includes('replies') ||
                        el.innerText.includes('Xem thêm')
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
        Clone 1 comment như người thật:
        1. Lướt xem comment trong panel (scroll mượt)
        2. Thu thập danh sách comment
        3. Nguồn clone: 70% từ video hiện tại, 30% từ bank (video trước)
        4. Biến thể: 50/50 giữ nguyên hoặc thêm emoji
        5. Chống trùng lặp + verify + detect rate limit
        """
        # ── Rate limit cooldown? ──
        if self._comment_cooldown:
            self.status_update.emit(f"⏳ Video #{video_idx}: Đang cooldown comment...", "orange")
            return

        # ════════════════════════════════════════════
        #  BƯỚC 1: Lướt xem comment (giống người thật đọc)
        # ════════════════════════════════════════════
        self.status_update.emit(f"💬 Video #{video_idx}: Đọc comment...", "blue")

        # ★ Tìm vùng comment panel (tọa độ thực tế)
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
            // Fallback: vùng bên phải (ForYou)
            return {x: 800, top: 100, bottom: 550, left: 680, right: 950};
        })()
        """) or {"x": 800, "top": 100, "bottom": 550, "left": 680, "right": 950}

        px = panel_bounds['x']
        p_top = panel_bounds['top']
        p_bot = panel_bounds['bottom']
        p_left = panel_bounds.get('left', 680)
        p_right = panel_bounds.get('right', 950)

        # ═══ Lướt đọc comment tự nhiên (lên/xuống xen kẽ) ═══
        # Giống người thật: scroll xuống → dừng đọc → scroll lên xem lại → xuống tiếp
        browse_actions = [
            ("down", 200, 400),   # Scroll xuống — đọc comment đầu
            ("pause", 0, 0),       # Dừng đọc 1-2s
            ("down", 250, 450),   # Xuống tiếp — đọc thêm
            ("hover", 0, 0),       # Di chuột vào 1 comment (tò mò)
            ("up", 100, 250),     # Scroll LÊN — xem lại comment trước
            ("pause", 0, 0),       # Đọc lại
            ("down", 300, 500),   # Xuống hẳn — xem comment mới
            ("hover", 0, 0),       # Di chuột vào comment khác
        ]

        for idx_a, (action, scroll_min, scroll_max) in enumerate(browse_actions):
            if self._stop_flag:
                return
            if action == "up":
                action = "pause"

            if action == "down":
                # Scroll xuống
                scroll_amount = random.randint(scroll_min, scroll_max)
                mx = random.randint(p_left + 20, p_right - 20)
                my = random.randint(p_top + 50, p_bot - 50)
                self.status_update.emit(f"👁️ Đọc comment... ⬇️ scroll xuống", "blue")
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
                # Scroll LÊN (giống đang xem lại comment hay)
                scroll_amount = random.randint(scroll_min, scroll_max)
                mx = random.randint(p_left + 20, p_right - 20)
                my = random.randint(p_top + 50, p_bot - 50)
                self.status_update.emit(f"👁️ Xem lại comment... ⬆️ scroll lên", "blue")
                await self._smooth_mouse_drift(cdp, mx, my)
                await asyncio.sleep(0)
                await asyncio.sleep(random.uniform(1.2, 2.0))

            elif action == "hover":
                # Di chuột vào 1 comment cụ thể (tò mò đọc)
                hx = random.randint(p_left + 30, p_right - 30)
                hy = random.randint(p_top + 80, p_bot - 100)
                self.status_update.emit(f"🖱️ Xem 1 comment...", "blue")
                await self._smooth_mouse_drift(cdp, hx, hy)
                await asyncio.sleep(random.uniform(1.0, 1.8))

            elif action == "pause":
                # Dừng đọc (mắt dừng ở 1 comment)
                self.status_update.emit(f"👁️ Đang đọc comment...", "blue")
                await asyncio.sleep(random.uniform(1.2, 2.2))

        # ════════════════════════════════════════════
        #  BƯỚC 2: Thu thập comment từ video hiện tại
        # ════════════════════════════════════════════
        self.status_update.emit(f"📝 Video #{video_idx}: Lấy danh sách comment...", "blue")

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
                    // Chỉ lấy text comment thực (2-200 ký tự, không phải username/time)
                    if (t.length > 2 && t.length < 200 &&
                        !t.match(/^\\d+[smhd]?\\s*(ago)?$/) &&   // Bỏ "2h ago"
                        !t.startsWith('@') &&                   // Bỏ @username
                        !t.match(/^Reply$/i) &&                 // Bỏ "Reply"
                        !t.match(/^View \\d+ replies/i))         // Bỏ "View 14 replies"
                        texts.add(t);
                }
                if (texts.size >= 10) break;
            }
            return [...texts].slice(0, 30);
        })()
        """) or []

        self.status_update.emit(
            f"📝 Video #{video_idx}: {len(current_comments)} comment | Bank: {len(self._comment_bank)}",
            "blue"
        )

        # Lưu vào bank để dùng cho video sau (giữ tối đa 100)
        for c in current_comments:
            if c not in self._comment_bank:
                self._comment_bank.append(c)
        if len(self._comment_bank) > 100:
            self._comment_bank = self._comment_bank[-100:]

        # ════════════════════════════════════════════
        #  BƯỚC 3: Chọn nguồn clone (video hiện tại vs bank)
        # ════════════════════════════════════════════
        chosen = None
        source = ""

        # Lọc bỏ comment đã dùng (chống trùng lặp)
        avail_current = [c for c in current_comments if c not in self._comment_history]
        avail_bank = [c for c in self._comment_bank
                      if c not in self._comment_history and c not in current_comments]

        # Quyết định nguồn: 70% video hiện tại, 30% bank (nếu có)
        use_bank = (random.random() < 0.3) and len(avail_bank) > 0

        if use_bank:
            chosen = random.choice(avail_bank)
            source = "📦 Từ bank (video trước)"
        elif avail_current:
            chosen = random.choice(avail_current)
            source = "🎬 Từ video hiện tại"
        elif avail_bank:
            # Fallback: video hiện tại hết comment mới → dùng bank
            chosen = random.choice(avail_bank)
            source = "📦 Fallback bank"
        else:
            self.status_update.emit(
                f"⚠️ Video #{video_idx}: Hết comment chưa dùng (bank + hiện tại)", "orange"
            )
            return

        # Đánh dấu đã dùng
        self._comment_history.add(chosen)

        # ════════════════════════════════════════════
        #  BƯỚC 4: Biến thể (50/50 thêm emoji)
        # ════════════════════════════════════════════
        if random.random() < 0.5:
            chosen = chosen + " " + random.choice(self.EMOJIS)
            self.status_update.emit(f"✏️ {source}: {chosen[:30]}... +emoji", "blue")
        else:
            self.status_update.emit(f"✏️ {source}: {chosen[:30]}...", "blue")

        # ════════════════════════════════════════════
        #  BƯỚC 5: Click ô comment và gõ
        # ════════════════════════════════════════════
        await asyncio.sleep(random.uniform(0.8, 1.5))

        # Tìm ô "Add comment..." — TikTok có nhiều variant
        input_pos = await cdp.evaluate("""
        (() => {
            // Cách 1: data-e2e comment-input (ô nhập chính)
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

            // Cách 2: Tìm placeholder "Add comment"
            const allEditable = document.querySelectorAll(
                '[contenteditable], [role="textbox"], textarea, input[type="text"]'
            );
            for (const el of allEditable) {
                const r = el.getBoundingClientRect();
                const ph = el.getAttribute('placeholder') || el.getAttribute('data-placeholder') || '';
                const text = (el.innerText || el.textContent || '').toLowerCase();
                if (r.width > 0 && r.height > 0 && r.y > 300 && (
                    ph.toLowerCase().includes('comment') ||
                    ph.toLowerCase().includes('bình luận') ||
                    text.includes('add comment') ||
                    text.includes('thêm bình luận')
                )) {
                    return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)};
                }
            }

            // Cách 3: Ô contenteditable ở dưới cùng comment panel (y > 500)
            for (const el of allEditable) {
                const r = el.getBoundingClientRect();
                if (r.width > 50 && r.height > 0 && r.y > 500)
                    return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)};
            }
            return null;
        })()
        """)

        if not input_pos:
            self.status_update.emit("⚠️ Không tìm thấy ô comment", "orange")
            return

        # Click vào ô comment
        await self._human_move_and_click(cdp, input_pos['x'], input_pos['y'], "Click ô comment")
        await asyncio.sleep(random.uniform(0.8, 1.2))

        # ★ Sau khi click, TikTok chuyển placeholder → contenteditable
        # Chờ ô focus và sẵn sàng nhận text
        for _ in range(3):
            focused = await cdp.evaluate("""
            (() => {
                const active = document.activeElement;
                if (active && (active.contentEditable === 'true' || active.tagName === 'TEXTAREA'))
                    return true;
                // Thử focus trực tiếp
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
            # Click lại nếu chưa focus
            await self._human_move_and_click(cdp, input_pos['x'], input_pos['y'], "Re-click ô comment")
            await asyncio.sleep(0.5)

        await asyncio.sleep(0.3)

        # Gõ từng ký tự (mô phỏng người thật)
        await cdp.type_text(chosen, delay=random.randint(50, 110))
        await asyncio.sleep(random.uniform(0.5, 0.8))

        # ★ Verify text đã được nhập chưa
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

        # Nếu type_text không hoạt động → fallback insertText
        if not has_text:
            self.status_update.emit("🔄 Fallback: insertText...", "blue")
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

        # ════════════════════════════════════════════
        #  BƯỚC 6: Gửi comment (Enter / Click nút Post)
        # ════════════════════════════════════════════
        self.status_update.emit(f"📤 Video #{video_idx}: Gửi comment...", "blue")
        comment_sent = False

        # Cách 1: Tìm nút Post/Đăng và click
        send_pos = await cdp.evaluate("""
        (() => {
            // Nút Post chính thức
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
            // Tìm nút có text "Post" hoặc "Đăng" gần ô comment
            const btns = document.querySelectorAll('button, div[role="button"], span[role="button"]');
            for (const btn of btns) {
                const text = (btn.textContent || '').trim();
                const r = btn.getBoundingClientRect();
                if (r.width > 0 && r.height > 0 && r.y > 400) {
                    if (text === 'Post' || text === 'Đăng' || text === 'Gửi' || text === 'Send')
                        return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), method: 'text'};
                }
            }
            // Nút icon gửi (arrow/send icon) ở cuối ô comment
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
            self.status_update.emit(f"📤 Click nút Post ({send_pos.get('method','')})", "blue")
            await self._human_move_and_click(cdp, send_pos['x'], send_pos['y'], "Post comment")
            comment_sent = True
        else:
            # Cách 2: Enter key trên contenteditable — gửi comment
            self.status_update.emit("📤 Enter để gửi comment...", "blue")

            # Focus lại ô comment trước khi Enter
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

            # Gửi Enter đầy đủ tham số
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

        # Cách 3 (backup): JS Enter event trực tiếp trên contenteditable
        if comment_sent:
            await asyncio.sleep(0.3)
            # Kiểm tra nếu text vẫn còn trong ô → Enter chưa gửi được → dispatch JS event
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
                        // Text vẫn còn → Enter chưa gửi → dispatch JS event
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
                self.status_update.emit("📤 Retry Enter (JS event)...", "blue")

        # ════════════════════════════════════════════
        #  BƯỚC 7: Verify + Detect Rate Limit
        # ════════════════════════════════════════════
        await asyncio.sleep(random.uniform(2.0, 3.0))

        # Kiểm tra rate limit trước
        rate_limited = await cdp.evaluate("""
        (() => {
            const body = (document.body.innerText || '').toLowerCase();
            const phrases = [
                'commenting too fast', 'too frequently', 'try again later',
                'comment failed', 'unable to post', "can't post",
                'bình luận quá nhanh', 'thử lại sau', 'bình luận thất bại'
            ];
            for (const p of phrases) {
                if (body.includes(p)) return true;
            }
            // Kiểm tra toast/popup lỗi
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
                f"🚫 Video #{video_idx}: TikTok rate limit! Tạm dừng comment {cooldown}s...", "orange"
            )
            self._comment_cooldown = True
            await asyncio.sleep(cooldown)
            self._comment_cooldown = False
            return

        # Verify: kiểm tra comment đã xuất hiện trong DOM chưa
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
            self.status_update.emit(f"✅ Video #{video_idx}: Comment thành công!", "green")
        else:
            self.status_update.emit(
                f"⚠️ Video #{video_idx}: Đã gửi nhưng chưa thấy xuất hiện (có thể bị lọc)", "orange"
            )

    # ════════════════════════════════════════════════════════════════
    #  FEED INTERACTION — Luồng chính
    # ════════════════════════════════════════════════════════════════

    async def _do_feed_interaction(self, cdp):
        """Nuôi nick Feed — tương tác như người thật."""
        try:
            feed_type = int(self.feed_settings.get('feed_type', 1))
        except (TypeError, ValueError):
            feed_type = 1
        if feed_type == 0:
            self.status_update.emit("⚠️ Feed đang tắt trong cài đặt, không chạy tương tác.", "orange")
            return False

        if not await self._wait_captcha_clear_for_action(cdp, "Feed start"):
            return False

        # Chỉ persist session hiện tại; không bơm cookie DB vào phiên GoLogin đang chạy.
        await self._persist_tiktok_cookies(cdp)

        if feed_type == 1:
            # ── Kiểm tra đã ở foryou chưa — nếu rồi thì KHÔNG click Home ──
            current_path = await cdp.evaluate("location.pathname") or ""
            if current_path in ('/', '/foryou') or current_path.startswith('/foryou'):
                self.status_update.emit("🏠 Đã ở trang For You — bắt đầu xem video", "blue")
            else:
                # Chỉ click Home khi CHƯA ở foryou
                self.status_update.emit("🏠 Click vào icon Home...", "blue")
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
                    await self._human_move_and_click(cdp, *home_pos, "Click icon 🏠 Home")
                    await asyncio.sleep(random.uniform(2.5, 4.0))
                else:
                    self.status_update.emit("⚠️ Không tìm thấy icon Home", "orange")
                    await self._navigate_like_human(cdp, "tiktok.com/foryou", wait=random.uniform(3.0, 4.5))

        else:
            # ── Click icon 🧭 Explore (la bàn) trên sidebar ───────────
            self.status_update.emit("🧭 Click vào icon Explore...", "blue")
            explore_pos = await cdp.evaluate("""
            (() => {
                // Tìm link Explore trong sidebar theo href
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
                // Fallback: quét sidebar tìm link /explore
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
                await self._human_move_and_click(cdp, *explore_pos, "Click icon 🧭 Explore")
                await asyncio.sleep(random.uniform(2.5, 4.0))
            else:
                self.status_update.emit("⚠️ Không tìm thấy icon Explore", "orange")
                await self._navigate_like_human(cdp, "tiktok.com/explore", wait=random.uniform(3.0, 4.5))



        # Số video sẽ xem
        view_min = int(self.feed_settings.get('view_min', 3) or 3)
        view_max = int(self.feed_settings.get('view_max', 5) or 5)
        if view_min > view_max:
            view_min, view_max = view_max, view_min
        n_videos = random.randint(view_min, view_max)
        self.status_update.emit(f"📺 Sẽ xem {n_videos} video...", "blue")

        # Tổng thời gian tối thiểu (giây) nếu bật tùy chọn thời gian
        use_time  = self.feed_settings.get('use_time', False)
        time_min = int(self.feed_settings.get('time_min', 3) or 3)
        time_max = int(self.feed_settings.get('time_max', 5) or 5)
        if time_min > time_max:
            time_min, time_max = time_max, time_min
        total_time_target = random.randint(time_min * 60, time_max * 60) if use_time else 0
        session_elapsed = 0.0

        if feed_type == 2:
            # ════ EXPLORE: Grid layout → click thumbnail → xem → back ════
            feed_completed = await self._watch_explore_feed(cdp, n_videos, use_time, total_time_target)
        else:
            # ════ FOR YOU: Cuộn dọc từng video ════════════════════════
            feed_completed = await self._watch_foryou_feed(cdp, n_videos, use_time, total_time_target)

        if feed_completed:
            self.status_update.emit("✅ Xong Feed!", "green")
        else:
            self.status_update.emit("⚠️ Feed dừng sớm, chưa đủ mục tiêu đã cài.", "orange")
        return bool(feed_completed)

    # ════════════════════════════════════════════════════════════════
    #  FOR YOU: cuộn dọc từng video
    # ════════════════════════════════════════════════════════════════

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
                    'button[aria-label*="Close" i], button[aria-label*="Đóng" i], ' +
                    'div[role="button"][aria-label*="Close" i], div[role="button"][aria-label*="Đóng" i], ' +
                    'button, div[role="button"]'
                ));
                for (const el of candidates) {
                    if (!isVisible(el)) continue;
                    const r = el.getBoundingClientRect();
                    const txt = ((el.innerText || el.textContent || el.getAttribute('aria-label') || '') + '').trim().toLowerCase();
                    const looksClose = txt === 'x' || txt === '×' || txt.includes('close') || txt.includes('đóng') || txt.includes('dong');
                    if (r.x > window.innerWidth * 0.55 && r.y < window.innerHeight * 0.45 && (looksClose || r.width <= 60))
                        return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
                }
                return null;
            })()
            """)
            if close_pos and (state.get("commentOpen") or state.get("overlayOpen")):
                await self._human_move_and_click(
                    cdp, int(close_pos["x"]), int(close_pos["y"]), "Đóng comment panel"
                )
                await asyncio.sleep(random.uniform(0.6, 1.0))
        except Exception:
            pass

        state = await _feed_state()
        if state.get("commentOpen"):
            await _press_escape()

        state = await _feed_state()
        if "/video/" in str(state.get("path") or ""):
            self.status_update.emit("↩️ Đang ở trang video, quay lại Feed trước khi chuyển tiếp...", "blue")
            try:
                await cdp.evaluate("window.location.href = 'https://www.tiktok.com/foryou'")
                await asyncio.sleep(random.uniform(1.5, 2.5))
            except Exception:
                pass

        state = await _feed_state()
        if "/video/" in str(state.get("path") or ""):
            self.status_update.emit("🏠 Mở lại For You để thoát trang video...", "orange")
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
                    cdp, int(focus_pos["x"]), int(focus_pos["y"]), "Focus vùng video"
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
                    const looksDown = label.includes('next') || label.includes('down') || label.includes('xuống');
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
            await self._human_move_and_click(cdp, int(pos["x"]), int(pos["y"]), "Click nút xuống")
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
            self.status_update.emit(f"⬇️ Chuyển sang video #{next_idx}... (lần {attempt + 1})", "blue")
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
                self.status_update.emit(f"✅ Đã chuyển sang video #{next_idx}", "green")
                return True

        self.status_update.emit("🏠 Không xác nhận được video đổi, mở lại For You để tiếp tục...", "orange")
        if await self._is_foryou_feed_usable(cdp):
            self.status_update.emit("Feed van dung duoc nhung khong xac nhan duoc chu ky video, tiep tuc de tranh dung phien.", "orange")
            return True

        try:
            await self._navigate_like_human(cdp, "tiktok.com/foryou", wait=random.uniform(3.0, 4.5))
            await self._close_comment_panel_and_focus_video(cdp)
            after = await self._get_current_feed_signature(cdp)
            if after and after != before:
                self.status_update.emit(f"✅ Đã khôi phục Feed và chuyển tiếp video #{next_idx}", "green")
                return True
            if await self._is_foryou_feed_usable(cdp):
                self.status_update.emit("⚠️ Feed vẫn dùng được nhưng không xác nhận được chữ ký video, tiếp tục để tránh dừng phiên.", "orange")
                return True
        except Exception:
            pass

        self.status_update.emit("⚠️ Không xác nhận được video đã đổi — dừng để tránh log ảo", "orange")
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
        _session_start = time.time()  # ★ Thời gian bắt đầu thực tế
        session_elapsed = 0.0
        skipped = 0
        seen_identities = set()
        duplicate_retries = 0

        # ★ Nếu user bật comment → giảm skip rate để đảm bảo comment hoạt động
        has_comment = self.feed_settings.get('clone_cmt', 0) > 0
        skip_rate = 0.10 if has_comment else 0.40

        # ★ Khi bật use_time: xem đến khi ĐỦ THỜI GIAN (không giới hạn số video)
        # Khi tắt use_time: xem đúng n_videos rồi dừng
        max_videos = 50 if use_time else n_videos  # Safety cap: tối đa 50 video
        i = 0
        finish_grace_seconds = 12

        if use_time:
            self.status_update.emit(
                f"📺 Sẽ xem tối thiểu {n_videos} video, mục tiêu {time_target//60} phút", "blue"
            )

        while i < max_videos:
            if self._stop_flag:
                return False
            if not await self._wait_captcha_clear_for_action(cdp, f"Feed video #{i+1}"):
                return False

            # ── Kiểm tra đã đủ thời gian chưa ──
            session_elapsed = time.time() - _session_start
            if use_time and session_elapsed >= time_target:
                self.status_update.emit(
                    f"✅ Đủ {session_elapsed/60:.1f}/{time_target//60} phút sau {i} video!", "green"
                )
                return True
            elif not use_time and i >= n_videos:
                return True

            # ── Random skip — video đầu KHÔNG BAO GIỜ skip ──
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
                remaining = f" | Còn {(time_target - session_elapsed)/60:.1f}p" if use_time else ""
                self.status_update.emit(
                    f"⏩ Video #{i+1}: Lướt qua ({skip_glance:.1f}s){remaining}", "blue"
                )
                await asyncio.sleep(skip_glance)
            else:
                session_elapsed = time.time() - _session_start
                remaining = f" | Còn {(time_target - session_elapsed)/60:.1f}p" if use_time else ""
                self.status_update.emit(
                    f"🎬 Video #{i+1} — Đang xem...{remaining}", "blue"
                )

                # Xem video
                await self._watch_current_video(cdp, i + 1)

                # Kiểm tra thời gian TRƯỚC KHI tương tác (tránh lố)
                session_elapsed = time.time() - _session_start
                if use_time and session_elapsed >= time_target:
                    break

                # Tương tác (chỉ khi xem, không tương tác video skip)
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
                    f"✅ Đủ {session_elapsed/60:.1f}/{time_target//60} phút sau {i} video!", "green"
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
                f"⚠️ Feed chạm giới hạn {max_videos} video nhưng chưa đủ {time_target//60} phút.",
                "orange",
            )
            return False
        return i >= n_videos

    # ════════════════════════════════════════════════════════════════
    #  EXPLORE: click thumbnail → xem full → back về grid
    # ════════════════════════════════════════════════════════════════

    async def _watch_explore_feed(self, cdp, n_videos: int, use_time: bool, time_target: int) -> bool:
        _session_start = time.time()  # ★ FIX: dùng real wall-clock time
        clicked_indices: set = set()
        processed = 0

        max_videos = 50 if use_time else n_videos
        if use_time:
            self.status_update.emit(
                f"📺 Explore: sẽ xem tối thiểu {n_videos} video, mục tiêu {time_target//60} phút", "blue"
            )

        for i in range(max_videos):
            if self._stop_flag:
                return False
            if not await self._wait_captcha_clear_for_action(cdp, f"Explore video #{i+1}"):
                return False
            session_elapsed = time.time() - _session_start  # ★ FIX: real elapsed
            if use_time and session_elapsed >= time_target:
                self.status_update.emit(f"✅ Đủ thời gian Explore ({time_target//60}p), dừng sớm.", "green")
                return True
            if not use_time and processed >= n_videos:
                return True

            # Lấy danh sách thumbnail trên trang Explore
            thumbnails = await cdp.evaluate("""
            (() => {
                const items = document.querySelectorAll(
                    '[data-e2e="explore-item"], '+
                    'div[class*="DivVideoCard"] a, '+
                    'div[class*="video-card"] a, '+
                    'a[href*="/video/"]'
                );
                const result = [];
                for (const el of items) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 50 && r.height > 50 && r.top >= 0 && r.bottom <= window.innerHeight + 200)
                        result.push({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2)});
                }
                return result;
            })()
            """) or []

            # Nếu ít thumbnail hoặc đã click hết → scroll xuống load thêm
            if len(thumbnails) < 3:
                await cdp.scroll(400, 400, 0, random.randint(400, 700))
                await asyncio.sleep(2)
                continue

            # Chọn ngẫu nhiên 1 thumbnail chưa click
            available = [t for idx, t in enumerate(thumbnails) if idx not in clicked_indices]
            if not available:
                # Reset và scroll thêm
                clicked_indices.clear()
                await cdp.scroll(400, 400, 0, random.randint(600, 900))
                await asyncio.sleep(2)
                continue

            chosen = random.choice(available)
            clicked_indices.add(thumbnails.index(chosen))
            current_idx = processed + 1

            self.status_update.emit(f"🎬 Explore #{current_idx}/{n_videos} — Click vào video...", "blue")
            await self._human_move_and_click(cdp, chosen['x'], chosen['y'], "Click thumbnail Explore")
            await asyncio.sleep(random.uniform(2.0, 3.0))

            # Xem video đang phát
            elapsed = await self._watch_current_video(cdp, current_idx)
            # session_elapsed now computed from wall-clock time (line above)

            # Tương tác (like, follow, comment...)
            await self._interact_current_video(cdp, current_idx)
            processed += 1

            if not await self._wait_captcha_clear_for_action(cdp, f"Explore back #{current_idx}"):
                return False

            # Nhấn Back về trang Explore (nút trình duyệt hoặc Escape)
            self.status_update.emit("↩️ Quay về Explore...", "blue")
            await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape"})
            await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp",   "key": "Escape"})
            await asyncio.sleep(0.3)
            # Dùng History.back() để quay về grid
            await cdp.evaluate("window.history.back()")
            await asyncio.sleep(random.uniform(2.5, 4.0))

            if use_time and (time.time() - _session_start) >= time_target:
                self.status_update.emit(f"✅ Đủ thời gian ({time_target//60}p), dừng sớm.", "green")
                return True
            if not use_time and processed >= n_videos:
                return True

        if self._stop_flag:
            return False
        if use_time:
            return (time.time() - _session_start) >= time_target
        return processed >= n_videos

    # ════════════════════════════════════════════════════════════════
    #  HELPER: Xem video hiện tại (di chuột lờ đờ mượt mà)
    # ════════════════════════════════════════════════════════════════

    async def _watch_current_video(self, cdp, video_idx: int) -> float:
        """
        Xem video thông minh — detect thời lượng thật và lặp tự nhiên.
        - LIVE:          xem 5-10s rồi skip
        - Ads:           xem 5-8s
        - Slideshow:     xem 5-10s
        - Video ≤ 15s:   lặp 2 lần
        - Video 15-30s:  lặp 1 lần (xem hết 1 vòng)
        - Video > 30s:   không lặp (xem hết 1 lần)
        - Safety:        tối đa 120s
        """
        await self._ensure_cursor_dot(cdp)
        if not await self._wait_captcha_clear_for_action(cdp, f"Watch video #{video_idx}"):
            return 0.0

        # ══════════════════════════════════════════
        #  BƯỚC 1: Đọc thông tin video
        # ══════════════════════════════════════════
        video_info = None
        for _retry in range(3):
            video_info = await cdp.evaluate("""
            (() => {
                const v = document.querySelector('video');
                if (!v) return null;

                // ★ Detect slideshow/photo post
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

        # ══════════════════════════════════════════
        #  BƯỚC 2: Quyết định xem bao lâu
        # ══════════════════════════════════════════
        duration = 0
        target_loops = 1
        use_loop_detect = False  # True = đợi video hết, False = sleep cố định

        # ★ Detect slideshow/ảnh: video paused ngay từ đầu HOẶC có carousel
        is_photo = False
        if video_info:
            is_photo = video_info.get('isSlideshow', False)
            if video_info.get('paused', False) and video_info.get('currentTime', 0) < 0.5:
                is_photo = True  # Video pause ở đầu = slideshow

        if is_photo:
            # ★ SLIDESHOW / ẢNH → lướt qua nhanh 2-3s
            watch_sec = random.uniform(2, 3)
            use_loop_detect = False
            self.status_update.emit(
                f"🖼️ Video #{video_idx}: Slideshow/Ảnh — lướt qua {watch_sec:.0f}s", "blue"
            )
        elif not video_info or not video_info.get('duration'):
            watch_sec = random.uniform(2, 3)
            self.status_update.emit(
                f"🖼️ Video #{video_idx}: Không có video — lướt qua {watch_sec:.0f}s", "blue"
            )
        else:
            duration = video_info['duration']

            if duration != duration:  # NaN check
                watch_sec = random.uniform(2, 3)
                self.status_update.emit(
                    f"⚠️ Video #{video_idx}: Duration NaN — lướt qua {watch_sec:.0f}s", "orange"
                )
            elif duration > 1e8 or duration == float('inf'):
                # ── LIVE STREAM ──
                watch_sec = random.uniform(5, 10)
                self.status_update.emit(
                    f"🔴 Video #{video_idx}: LIVE — xem {watch_sec:.0f}s", "blue"
                )
            elif duration <= 3:
                # ── Clip cực ngắn / slideshow ──
                watch_sec = random.uniform(2, 3)
                self.status_update.emit(
                    f"🖼️ Video #{video_idx}: Clip {duration:.1f}s — lướt qua", "blue"
                )
            elif duration <= 15:
                # ── Video ngắn → lặp 2 lần ──
                target_loops = 2
                watch_sec = duration * target_loops + random.uniform(1, 3)
                use_loop_detect = True
                self.status_update.emit(
                    f"🎬 Video #{video_idx}: {duration:.0f}s × {target_loops} lần", "blue"
                )
            elif duration <= 30:
                # ── Video trung bình → xem hết 1 lần ──
                target_loops = 1
                watch_sec = duration + random.uniform(1, 3)
                use_loop_detect = True
                self.status_update.emit(
                    f"🎬 Video #{video_idx}: {duration:.0f}s × 1 lần", "blue"
                )
            elif duration <= 60:
                # ── Video dài 30-60s → xem 50-80% ──
                pct = random.uniform(0.5, 0.8)
                watch_sec = duration * pct
                watch_sec = max(15, min(watch_sec, 35))
                use_loop_detect = False
                self.status_update.emit(
                    f"🎬 Video #{video_idx}: {duration:.0f}s — xem {watch_sec:.0f}s ({pct*100:.0f}%)", "blue"
                )
            else:
                # ── Video rất dài > 60s → xem 20-40s rồi lướt ──
                watch_sec = random.uniform(20, 40)
                use_loop_detect = False
                self.status_update.emit(
                    f"🎬 Video #{video_idx}: {duration:.0f}s (dài) — xem {watch_sec:.0f}s rồi lướt", "blue"
                )

        # Safety cap: tối đa 30s mỗi video (tránh lố thời gian)
        watch_sec = min(watch_sec, 30)

        # ══════════════════════════════════════════
        #  BƯỚC 3: Xem video + drift chuột
        # ══════════════════════════════════════════
        elapsed = 0.0
        loops_done = 0
        last_ct = video_info['currentTime'] if video_info else 0
        stall_count = 0  # Đếm số lần currentTime không đổi (buffer)

        while elapsed < watch_sec:
            if self._stop_flag:
                return elapsed
            if not await self._wait_captcha_clear_for_action(cdp, f"Watch video #{video_idx}"):
                return elapsed

            # Drift chuột mượt (giống người đang xem)
            cx = getattr(self, '_mouse_x', 400)
            cy = getattr(self, '_mouse_y', 300)
            tx = max(80, min(cx + random.randint(-120, 120), 750))
            ty = max(80, min(cy + random.randint(-80, 80), 500))
            await self._smooth_mouse_drift(cdp, tx, ty)

            pause = random.uniform(2, 4)
            await asyncio.sleep(pause)
            elapsed += pause

            # ── Theo dõi tiến trình video (nếu dùng loop detect) ──
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

                    # Video bị pause → đếm, KHÔNG trừ elapsed vô hạn
                    if state['paused']:
                        stall_count += 1
                        if stall_count >= 3:
                            # Pause quá lâu (slideshow/ảnh) → thoát
                            self.status_update.emit(
                                f"🖼️ Video #{video_idx}: Video pause — lướt qua", "blue"
                            )
                            break
                        continue

                    # Buffering: currentTime đứng yên
                    if abs(ct - last_ct) < 0.1:
                        stall_count += 1
                        if stall_count >= 5:
                            # Buffer quá lâu → thoát
                            self.status_update.emit(
                                f"⚠️ Video #{video_idx}: Buffer quá lâu — bỏ qua", "orange"
                            )
                            break
                    else:
                        stall_count = 0

                    # Detect loop: currentTime nhảy ngược > 1s
                    if ct < last_ct - 1:
                        loops_done += 1
                        self.status_update.emit(
                            f"🔄 Video #{video_idx}: Lặp lần {loops_done}/{target_loops}", "blue"
                        )
                        if loops_done >= target_loops:
                            # Đã xem đủ số lần → dừng
                            break

                    last_ct = ct

        # ══════════════════════════════════════════
        #  BƯỚC 4: Kết thúc
        # ══════════════════════════════════════════
        if use_loop_detect and loops_done > 0:
            self.status_update.emit(
                f"✅ Video #{video_idx}: Xem xong {loops_done} lần ({elapsed:.0f}s)", "green"
            )
        else:
            self.status_update.emit(
                f"✅ Video #{video_idx}: Xem {elapsed:.0f}s", "green"
            )
        return elapsed

    # ════════════════════════════════════════════════════════════════
    #  HELPER: Tương tác với video hiện tại (Like/Fav/Repost/Follow/Cmt)
    # ════════════════════════════════════════════════════════════════

    async def _interact_current_video(self, cdp, video_idx: int):
        """Chạy tất cả tương tác theo tỉ lệ % cho video đang xem."""
        if not await self._wait_captcha_clear_for_action(cdp, f"Interact video #{video_idx}"):
            return

        # ── Phát hiện loại video trước ──
        vtype = await self._detect_video_type(cdp)
        self.status_update.emit(f"🔍 Video #{video_idx}: type={vtype}", "blue")

        if vtype == 'live':
            self.status_update.emit(f"⏭️ Video #{video_idx}: LIVE — bỏ qua tương tác", "orange")
            return
        if vtype == 'ads':
            self.status_update.emit(f"⏭️ Video #{video_idx}: Quảng cáo — bỏ qua", "orange")
            return
        if vtype == 'restricted':
            self.status_update.emit(f"⏭️ Video #{video_idx}: Bị hạn chế — bỏ qua", "orange")
            return

        # ── Roll dice 1 LẦN DUY NHẤT cho mỗi tính năng ──
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
                    await self._human_move_and_click(cdp, *pos, f"❤️ Like #{video_idx}")
                    await asyncio.sleep(random.uniform(0.6, 1.2))
            except Exception:
                pass

        # Thêm vào yêu thích
        if do_fav_video:
            try:
                for sel in ['[data-e2e="undefined-icon"]', '[data-e2e="favorite-icon"]',
                            'span[class*="Favorite"]']:
                    pos = await self._get_center(cdp, sel)
                    if pos:
                        await self._human_move_and_click(cdp, *pos, f"🔖 Yêu thích #{video_idx}")
                        await asyncio.sleep(random.uniform(0.5, 1.0))
                        break
            except Exception:
                pass

        # Repost
        if do_repost:
            try:
                share_pos = await self._get_center(cdp, '[data-e2e="share-icon"]')
                if share_pos:
                    await self._human_move_and_click(cdp, *share_pos, "🔁 Mở share")
                    await asyncio.sleep(random.uniform(1.2, 1.8))
                    rp = await self._get_center_by_text(cdp, "Repost")
                    if rp:
                        await self._human_move_and_click(cdp, *rp, f"🔁 Repost #{video_idx}")
                    await asyncio.sleep(random.uniform(0.8, 1.5))
                    await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape"})
                    await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp",   "key": "Escape"})
                    await asyncio.sleep(0.5)
            except Exception:
                pass

        # Follow kênh
        if do_follow:
            try:
                for sel in ['[data-e2e="follow-button"]', 'button[class*="follow"]']:
                    pos = await self._get_center(cdp, sel)
                    if pos:
                        await self._human_move_and_click(cdp, *pos, f"➕ Follow #{video_idx}")
                        await asyncio.sleep(random.uniform(0.8, 1.5))
                        break
            except Exception:
                pass

        # ── Tương tác comment (dùng kết quả đã roll ở trên) ──
        need_comment = do_clone_cmt or do_like_cmt or do_view_more

        if need_comment:
            self.status_update.emit(
                f"💬 Video #{video_idx}: Cần comment (clone={do_clone_cmt}, like={do_like_cmt}, view={do_view_more})",
                "blue"
            )
            # Cho phép comment cả khi vtype='no_comment' — thử mở panel dù sao
            # Vì _detect_video_type có thể nhận diện sai trên ForYou feed
            opened = await self._open_comment_panel(cdp)
            if opened:
                # Chờ panel render đầy đủ
                await asyncio.sleep(random.uniform(1.0, 1.5))

                if do_like_cmt:
                    await self._like_comments(cdp, video_idx)
                if do_view_more:
                    await self._view_more_replies(cdp, video_idx)
                if do_clone_cmt:
                    await self._clone_comment(cdp, video_idx)

                # Đóng panel comment
                await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape"})
                await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp",   "key": "Escape"})
                await asyncio.sleep(random.uniform(0.5, 1.0))
            else:
                self.status_update.emit(f"⚠️ Video #{video_idx}: Không mở được panel comment", "orange")
        else:
            self.status_update.emit(
                f"🎲 Video #{video_idx}: Không trúng tỉ lệ comment (clone_cmt={self.feed_settings.get('clone_cmt',0)}%)",
                "blue"
            )




    # ════════════════════════════════════════════════════════════════
    #  KEYWORD INTERACTION
    # ════════════════════════════════════════════════════════════════

    async def _do_keyword_interaction(self, cdp):
        """
        ════════════════════════════════════════════════════════════
        TƯƠNG TÁC THEO TỪ KHÓA — Luồng 4 giai đoạn (Human-like)
        ════════════════════════════════════════════════════════════
        GĐ1: Khởi động → Tìm Search Box → Gõ từng ký tự keyword → Enter
        GĐ2: Click video đầu tiên → Chuyển sang Theater Mode (nền đen)
        GĐ3: Vòng lặp nuôi nick: Xem video + Like/Fav + ArrowDown
        GĐ4: Đóng gói — Thoát Theater Mode, trả kết quả
        """
        if not await self._wait_captcha_clear_for_action(cdp, "Keyword start"):
            return False

        # ── Lấy danh sách từ khóa từ cài đặt ──
        keywords = self.feed_settings.get('keywords', [])
        if not keywords:
            self.status_update.emit("⚠️ Chưa có từ khóa. Hãy vào Cài đặt để thêm!", "orange")
            return False

        # ── Lấy cấu hình số video xem mỗi từ khóa ──
        kw_min = int(self.feed_settings.get('keyword_min_videos', 3) or 3)
        kw_max = int(self.feed_settings.get('keyword_max_videos', 8) or 8)
        if kw_min > kw_max:
            kw_min, kw_max = kw_max, kw_min

        self.status_update.emit(
            f"🔍 Bắt đầu tìm kiếm {len(keywords)} từ khóa ({kw_min}-{kw_max} video/từ khóa)", "blue"
        )

        # ════════════════════════════════════════════════════
        #  LOOP QUA TỪNG TỪ KHÓA
        # ════════════════════════════════════════════════════
        completed_keywords = 0
        for kw_idx, keyword in enumerate(keywords):
            if self._stop_flag:
                break
            if not await self._wait_captcha_clear_for_action(cdp, f"Keyword {kw_idx+1}/{len(keywords)}"):
                break

            self.status_update.emit(
                f"🔍 [{kw_idx+1}/{len(keywords)}] Từ khóa: \"{keyword}\"", "blue"
            )

            try:
                success = await self._search_and_interact_one_keyword(cdp, keyword, kw_min, kw_max, kw_idx + 1, len(keywords))
                if not success:
                    self.status_update.emit(
                        f"⚠️ Từ khóa \"{keyword[:20]}\" không thành công — tiếp tục từ khóa kế", "orange"
                    )
                else:
                    completed_keywords += 1
                # Nghỉ giữa các từ khóa (giống người thật đổi chủ đề)
                if kw_idx < len(keywords) - 1 and not self._stop_flag:
                    pause = random.uniform(3, 6)
                    self.status_update.emit(f"⏸️ Nghỉ {pause:.0f}s trước từ khóa kế...", "blue")
                    await asyncio.sleep(pause)
            except Exception as e:
                self.status_update.emit(f"❌ Lỗi từ khóa \"{keyword[:20]}\": {str(e)[:50]}", "red")
                continue

        if self._stop_flag:
            return False
        if completed_keywords <= 0:
            self.status_update.emit("⚠️ Chưa hoàn tất từ khóa nào.", "orange")
            return False

        self.status_update.emit("✅ Xong tất cả từ khóa!", "green")
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
            f"[{kw_num}/{kw_total}] Mở video #{video_idx} bằng URL kết quả...", "blue"
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
                f"[{kw_num}/{kw_total}] Video #{video_idx} chưa khớp URL, thử lại ({attempt + 1}/2)", "orange"
            )
            await asyncio.sleep(random.uniform(0.8, 1.3))

        return False

    async def _return_to_keyword_results(self, cdp, keyword: str, kw_num: int, kw_total: int) -> bool:
        """Return to keyword results without browser history, so old videos are not reopened."""
        self.status_update.emit(f"[{kw_num}/{kw_total}] Tải lại trang kết quả từ khóa...", "blue")
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
        Tìm kiếm bằng cách click icon 🔍 kính lúp trên sidebar TikTok.
        Luồng: Click icon → mở trang search → gõ từ khóa → Enter → chờ kết quả.
        """
        try:
            if not await self._wait_captcha_clear_for_action(cdp, f"Search keyword {keyword[:20]}"):
                return False
            self.status_update.emit(f"🔍 Click icon kính lúp để tìm: \"{keyword[:25]}\"", "blue")

            # ═══════════════════════════════════════════════════
            #  BƯỚC 1: Tìm icon kính lúp 🔍 trên sidebar/header
            # ═══════════════════════════════════════════════════
            search_icon_pos = await cdp.evaluate("""
            (() => {
                // 1. Tìm link Explore/Search trên sidebar (TikTok desktop)
                //    Icon kính lúp thường là <a> với href="/search" hoặc "/explore"
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

                // 2. Tìm icon SVG kính lúp trong sidebar (quét tất cả link trên sidebar)
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

                // 3. Tìm icon kính lúp bằng SVG path (TikTok dùng SVG cho icon)
                const svgs = document.querySelectorAll('svg');
                for (const svg of svgs) {
                    const parent = svg.closest('a, button, div[role="button"]');
                    if (!parent) continue;
                    const parentHref = (parent.getAttribute('href') || '').toLowerCase();
                    const ariaLabel = (parent.getAttribute('aria-label') || '').toLowerCase();
                    if (parentHref.includes('search') || ariaLabel.includes('search') || ariaLabel.includes('tìm')) {
                        const r = parent.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0)
                            return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), type: 'svg'};
                    }
                }

                // 4. Fallback: tìm bất kỳ element nào giống nút search
                const all = document.querySelectorAll('a, button');
                for (const el of all) {
                    const r = el.getBoundingClientRect();
                    if (r.width < 10 || r.height < 10 || r.x > 150) continue; // Sidebar thường ở trái, x < 150
                    const href = (el.getAttribute('href') || '');
                    const text = (el.textContent || '').toLowerCase();
                    if (href.includes('/search') || text.includes('search') || text.includes('tìm kiếm')) {
                        return {x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), type: 'fallback'};
                    }
                }

                return null;
            })()
            """)

            if not search_icon_pos:
                self.status_update.emit("⚠️ Không tìm thấy icon kính lúp trên sidebar", "orange")
                return False

            self.status_update.emit(
                f"👆 Tìm thấy icon search ({search_icon_pos.get('type','?')}) — click...", "blue"
            )
            await self._human_move_and_click(
                cdp, search_icon_pos['x'], search_icon_pos['y'], "Click icon 🔍 Search"
            )

            # ═══════════════════════════════════════════════════
            #  BƯỚC 2: Chờ trang search mở → tìm ô input
            # ═══════════════════════════════════════════════════
            await asyncio.sleep(random.uniform(1.5, 2.5))

            # Tìm ô input search sau khi click icon
            search_input = None
            for attempt in range(5):
                search_input = await cdp.evaluate("""
                (() => {
                    // Ưu tiên: input đang active (focus)
                    const active = document.activeElement;
                    if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA')) {
                        const r = active.getBoundingClientRect();
                        if (r.width > 50) return {x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2)};
                    }
                    // Tìm input search
                    const selectors = [
                        'input[data-e2e="search-user-input"]',
                        'input[type="search"]',
                        'input[placeholder*="Search" i]',
                        'input[placeholder*="Tìm" i]',
                        'input[name="q"]'
                    ];
                    for (const sel of selectors) {
                        const inp = document.querySelector(sel);
                        if (inp) {
                            const r = inp.getBoundingClientRect();
                            if (r.width > 50) return {x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2)};
                        }
                    }
                    // Fallback: bất kỳ input nào có placeholder search
                    for (const inp of document.querySelectorAll('input')) {
                        const ph = (inp.placeholder || '').toLowerCase();
                        if (ph.includes('search') || ph.includes('tìm')) {
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
                self.status_update.emit("⚠️ Không tìm thấy ô nhập search sau khi click icon", "orange")
                return False

            # Click vào ô input search
            await self._human_move_and_click(cdp, search_input['x'], search_input['y'], "Click ô nhập search")
            await asyncio.sleep(random.uniform(0.5, 0.8))

            # ═══════════════════════════════════════════════════
            #  BƯỚC 3: Xóa text cũ + Gõ từ khóa + Enter
            # ═══════════════════════════════════════════════════
            # Ctrl+A → Backspace (xóa text cũ nếu có)
            await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a", "code": "KeyA", "modifiers": 2})
            await asyncio.sleep(0.05)
            await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a", "code": "KeyA"})
            await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Backspace", "code": "Backspace"})
            await asyncio.sleep(0.05)
            await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Backspace", "code": "Backspace"})
            await asyncio.sleep(random.uniform(0.3, 0.5))

            # Gõ từng ký tự (100-300ms delay — giống người thật)
            self.status_update.emit(f"⌨️ Gõ: \"{keyword}\"", "blue")
            await cdp.type_text(keyword, delay=random.randint(100, 300))
            await asyncio.sleep(random.uniform(1.0, 2.0))

            # ═══════════════════════════════════════════════════
            #  BƯỚC 4: Chờ dropdown gợi ý → Click dòng gợi ý
            # ═══════════════════════════════════════════════════
            self.status_update.emit("👆 Chờ dropdown gợi ý hiện ra...", "blue")

            # Chờ dropdown suggestions xuất hiện (tối đa 5 giây)
            suggestion_clicked = False
            for wait_attempt in range(6):
                suggestion_pos = await cdp.evaluate("""
                (() => {
                    const results = [];

                    // 1. Tìm link "Xem tất cả kết quả" / "View all results" — ưu tiên cao nhất
                    const allEls = document.querySelectorAll('a, div[role="link"], div[role="button"], span, p, div');
                    for (const el of allEls) {
                        const text = (el.textContent || '').trim().toLowerCase();
                        if ((text.includes('xem tất cả') || text.includes('view all') ||
                             text.includes('tất cả kết quả') || text.includes('all results')) &&
                            text.length < 100) {
                            const r = el.getBoundingClientRect();
                            if (r.width > 50 && r.height > 10 && r.y > 50)
                                results.push({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2), type: 'view_all', text: text.substring(0,40)});
                        }
                    }

                    // 2. Tìm các dòng gợi ý có icon kính lúp (Q) — trong dropdown suggestions
                    //    TikTok thường dùng <a> hoặc <div> với class chứa "SearchSuggestion" hoặc tương tự
                    const suggestionSelectors = [
                        '[class*="suggestion" i] a',
                        '[class*="suggestion" i] div[role="button"]',
                        '[class*="Suggestion" i] a',
                        '[class*="search-suggest" i] a',
                        '[class*="SearchSuggest" i] a',
                        '[data-e2e="search-suggest"] a',
                        '[data-e2e*="suggest"] a',
                        // Tìm link có text matching từ khóa
                    ];
                    for (const sel of suggestionSelectors) {
                        for (const el of document.querySelectorAll(sel)) {
                            const r = el.getBoundingClientRect();
                            if (r.width > 50 && r.height > 15 && r.y > 50)
                                results.push({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2), type: 'suggest_sel', text: (el.textContent||'').trim().substring(0,40)});
                        }
                    }

                    // 3. Fallback: tìm các <a> hoặc div bên dưới search input có nội dung liên quan
                    const searchInput = document.querySelector('input[data-e2e="search-user-input"], input[type="search"], input[placeholder*="Search" i], input[placeholder*="Tìm" i]');
                    if (searchInput) {
                        const inputRect = searchInput.getBoundingClientRect();
                        // Tìm container cha chứa cả input và dropdown
                        let container = searchInput.closest('[class*="search" i], [class*="Search" i], form') || searchInput.parentElement;
                        if (container) {
                            // Mở rộng lên vài cấp nếu cần
                            for (let i = 0; i < 5 && container.parentElement; i++) {
                                const links = container.querySelectorAll('a[href], div[role="button"], div[role="link"]');
                                if (links.length > 2) break;
                                container = container.parentElement;
                            }
                            const items = container.querySelectorAll('a[href], div[role="button"], div[role="link"]');
                            for (const item of items) {
                                const r = item.getBoundingClientRect();
                                // Chỉ lấy các item DƯỚI search input (dropdown)
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

                    // Ưu tiên: view_all > suggest_sel > dropdown_item
                    // Nhưng nếu có suggest, click dòng 1 hoặc 2 (ngẫu nhiên, giống người thật)
                    const viewAll = results.find(r => r.type === 'view_all');
                    const suggests = results.filter(r => r.type !== 'view_all');

                    if (suggests.length > 0) {
                        // Click dòng 1 hoặc 2 ngẫu nhiên
                        const idx = Math.min(Math.floor(Math.random() * 2), suggests.length - 1);
                        return suggests[idx];
                    }
                    if (viewAll) return viewAll;
                    return results[0];
                })()
                """)

                if suggestion_pos:
                    self.status_update.emit(
                        f"👆 Click gợi ý: \"{suggestion_pos.get('text','')}\" ({suggestion_pos.get('type','')})", "blue"
                    )
                    await self._human_move_and_click(
                        cdp, suggestion_pos['x'], suggestion_pos['y'],
                        f"Click gợi ý search"
                    )
                    suggestion_clicked = True
                    break

                await asyncio.sleep(0.8)

            # ═══════════════════════════════════════════════════
            #  BƯỚC 5: Nếu không tìm thấy dropdown → thử Enter
            # ═══════════════════════════════════════════════════
            if not suggestion_clicked:
                self.status_update.emit("⚠️ Không thấy dropdown gợi ý — thử nhấn Enter...", "orange")
                # Focus lại input
                await cdp.evaluate("""
                (() => {
                    const inp = document.querySelector('input[data-e2e="search-user-input"], input[type="search"]');
                    if (inp) inp.focus();
                })()
                """)
                await asyncio.sleep(0.2)
                # Submit bằng form submit (JS)
                submitted = await cdp.evaluate("""
                (() => {
                    const inp = document.querySelector('input[data-e2e="search-user-input"], input[type="search"]');
                    if (inp) {
                        const form = inp.closest('form');
                        if (form) { form.submit(); return 'form'; }
                        // Dispatch Enter event trên input
                        inp.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true}));
                        inp.dispatchEvent(new KeyboardEvent('keypress', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true}));
                        inp.dispatchEvent(new KeyboardEvent('keyup', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true}));
                        return 'event';
                    }
                    return null;
                })()
                """)
                self.status_update.emit(f"↵ Submit method: {submitted}", "blue")

            # ═══════════════════════════════════════════════════
            #  BƯỚC 6: Chờ kết quả tìm kiếm
            # ═══════════════════════════════════════════════════
            self.status_update.emit("⏳ Chờ kết quả tìm kiếm...", "blue")
            await asyncio.sleep(random.uniform(3.0, 5.0))

            if await self._wait_keyword_results_ready(cdp, timeout=8):
                return True

            # Fallback cuối cùng: navigate URL
            self.status_update.emit("🔄 Fallback: mở URL tìm kiếm...", "orange")
            return await self._open_keyword_results_url(cdp, keyword, timeout=10)

        except Exception as e:
            self.status_update.emit(f"⚠️ Search by click lỗi: {str(e)[:80]}", "orange")
            return False

    async def _search_and_interact_one_keyword(self, cdp, keyword: str, min_videos: int, max_videos: int, kw_num: int, kw_total: int) -> bool:
        """
        Tìm kiếm và tương tác cho 1 từ khóa.
        Luồng: Search → Grid kết quả → Cuộn lên/xuống tự nhiên → Click ngẫu nhiên video
               → Xem + tương tác → Escape quay lại grid → Lặp lại.
        """
        try:
            if not await self._wait_captcha_clear_for_action(cdp, f"Keyword {kw_num}/{kw_total} start"):
                return False

            # ════════════════════════════════════════════════════
            #  GIAI ĐOẠN 1: Mở trang chủ TikTok → Chờ load → Tìm kiếm
            # ════════════════════════════════════════════════════
            # ★ LUÔN navigate về trang chủ TikTok trước (giống người thật)
            self.status_update.emit(f"🏠 [{kw_num}/{kw_total}] Mở trang chủ TikTok...", "blue")
            await self._navigate_like_human(cdp, "tiktok.com", wait=random.uniform(3, 5))

            # ★ Persist lại session cookies
            await self._persist_tiktok_cookies(cdp)

            # Chờ thêm 2-4s cho trang load hoàn toàn (giống người thật mở TikTok lên rồi lướt vài giây)
            self.status_update.emit(f"⏳ [{kw_num}/{kw_total}] Chờ TikTok load...", "blue")
            await asyncio.sleep(random.uniform(2, 4))

            # Ẩn viền focus
            await cdp.evaluate("""
            (() => {
                const s = document.createElement('style');
                s.textContent = '*:focus,*:focus-visible{outline:none!important;box-shadow:none!important;}';
                document.head.appendChild(s);
            })()
            """)

            # GĐ1: Mở trang kết quả bằng URL trước; click search chỉ là fallback.
            self.status_update.emit(f"🔎 [{kw_num}/{kw_total}] Tìm kiếm: \"{keyword}\"...", "blue")
            if not await self._open_keyword_results_url(cdp, keyword, kw_num, kw_total, timeout=12):
                self.status_update.emit(f"⚠️ [{kw_num}/{kw_total}] URL search chưa sẵn sàng, thử search bằng giao diện", "orange")
                if not await self._search_by_clicking(cdp, keyword):
                    self.status_update.emit(f"❌ [{kw_num}/{kw_total}] Không tìm kiếm được \"{keyword[:20]}\"", "red")
                    return False
            if not await self._wait_keyword_results_ready(cdp, timeout=8):
                self.status_update.emit(f"❌ [{kw_num}/{kw_total}] Không tìm kiếm được \"{keyword[:20]}\"", "red")
                return False

            # ════════════════════════════════════════════════════
            #  GIAI ĐOẠN 2: Duyệt grid kết quả — cuộn lên/xuống tự nhiên
            #  rồi click ngẫu nhiên video → xem → back → lặp lại
            # ════════════════════════════════════════════════════
            n_videos = random.randint(min_videos, max_videos)
            self.status_update.emit(
                f"📺 [{kw_num}/{kw_total}] Sẽ xem {n_videos} video cho \"{keyword[:15]}\"", "blue"
            )

            clicked_hrefs = set()  # Chống click trùng video
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

                # ── Bước 2.1: Cuộn trang kết quả tự nhiên (giống người duyệt) ──
                self.status_update.emit(
                    f"👁️ [{kw_num}/{kw_total}] Video #{video_no}/{n_videos} — Duyệt kết quả...", "blue"
                )

                # Cuộn xuống ngẫu nhiên để xem thêm kết quả
                n_scrolls = random.randint(1, 3)
                for s in range(n_scrolls):
                    if self._stop_flag:
                        break
                    # Di chuột vào vùng grid trước khi cuộn
                    mx = random.randint(200, 700)
                    my = random.randint(200, 500)
                    await self._smooth_mouse_drift(cdp, mx, my)
                    # Cuộn xuống
                    scroll_amount = random.randint(200, 500)
                    await cdp.scroll(mx, my, 0, scroll_amount)
                    await asyncio.sleep(random.uniform(1.0, 2.0))

                # Thỉnh thoảng cuộn lên (30% cơ hội) — giống người xem lại
                if False and random.random() < 0.3:
                    mx = random.randint(200, 700)
                    my = random.randint(200, 500)
                    await self._smooth_mouse_drift(cdp, mx, my)
                    await asyncio.sleep(0)
                    await asyncio.sleep(random.uniform(0.8, 1.5))

                # ── Bước 2.2: Lấy danh sách video/photo card đang hiển thị và chưa click ──
                available = await self._collect_keyword_video_cards(cdp, clicked_hrefs)
                if not available:
                    self.status_update.emit("⏬ Scroll thêm để tìm video mới...", "blue")
                    await cdp.scroll(400, 400, 0, random.randint(500, 850))
                    await asyncio.sleep(random.uniform(1.5, 2.3))
                    available = await self._collect_keyword_video_cards(cdp, clicked_hrefs)
                if not available:
                    self.status_update.emit("⚠️ Chưa bắt được card video — tìm kiếm lại", "orange")
                    if await self._open_keyword_results_url(cdp, keyword, kw_num, kw_total, timeout=10):
                        await asyncio.sleep(random.uniform(1.2, 2.0))
                        available = await self._collect_keyword_video_cards(cdp, clicked_hrefs)

                if not available:
                    self.status_update.emit(f"⚠️ Hết video để xem cho \"{keyword[:15]}\"", "orange")
                    break

                # ── Bước 2.3: Click ngẫu nhiên 1 video từ danh sách ──
                chosen = random.choice(available)
                clicked_hrefs.add(chosen.get('href', ''))

                if not await self._open_keyword_card(cdp, chosen, video_no, kw_num, kw_total):
                    self.status_update.emit("⚠️ Không mở được video từ card này — bỏ qua", "orange")
                    await self._open_keyword_results_url(cdp, keyword, kw_num, kw_total, timeout=8)
                    continue

                # ── Bước 2.4: Xem video (trong Theater Mode / full page) ──
                self.status_update.emit(
                    f"🎬 [{kw_num}/{kw_total}] Đang xem video #{video_no}/{n_videos}...", "blue"
                )
                await self._watch_current_video(cdp, video_no)
                watched_count += 1

                # ── Bước 2.5: Tương tác (Like/Fav/Comment theo tỉ lệ %) ──
                await self._interact_current_video(cdp, video_no)

                if not await self._wait_captcha_clear_for_action(cdp, f"Keyword back #{video_no}"):
                    break

                # ── Bước 2.6: Quay lại trang kết quả bằng URL, không dùng history.back ──
                if watched_count < n_videos:
                    if not await self._return_to_keyword_results(cdp, keyword, kw_num, kw_total):
                        self.status_update.emit("⚠️ Không thể quay lại kết quả, dừng keyword này", "orange")
                        break

            # ════════════════════════════════════════════════════
            #  GIAI ĐOẠN 3: Hoàn thành từ khóa này
            # ════════════════════════════════════════════════════
            if watched_count < min_videos:
                self.status_update.emit(
                    f"❌ [{kw_num}/{kw_total}] Chưa đủ video cho \"{keyword[:20]}\" ({watched_count}/{min_videos})", "red"
                )
                return False

            if watched_count < n_videos:
                self.status_update.emit(
                    f"⚠️ [{kw_num}/{kw_total}] Hoàn thành một phần \"{keyword[:20]}\" ({watched_count}/{n_videos} video)", "orange"
                )
            else:
                self.status_update.emit(
                    f"✅ [{kw_num}/{kw_total}] Hoàn thành \"{keyword[:20]}\" ({watched_count} video)", "green"
                )
            return True

        except Exception as e:
            self.status_update.emit(
                f"❌ [{kw_num}/{kw_total}] Lỗi \"{keyword[:15]}\": {str(e)[:50]}", "red"
            )
            return False




    def _hide_browser_windows(self):
        """Ẩn browser bằng cách di chuyển ra ngoài màn hình (vẫn render cho screencast)."""
        if not self._process:
            return
        try:
            import win32gui, win32con, win32process
            pid = self._process.pid

            # Lấy tất cả PID (parent + children)
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
                # Di chuyển ra ngoài màn hình (Chrome vẫn render)
                win32gui.SetWindowPos(
                    hwnd, None,
                    -32000, -32000, 0, 0,
                    win32con.SWP_NOSIZE | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
                )

            if hwnds:
                self.status_update.emit(f"👁‍🗨 Ẩn {len(hwnds)} cửa sổ browser", "green")
        except Exception as e:
            self.status_update.emit(f"⚠️ Không ẩn được browser: {str(e)[:40]}", "orange")

    def stop(self):
        """Dừng worker + đóng browser GRACEFULLY (không block UI)."""
        self._stop_flag = True
        if self._async_close_started:
            return
        self._async_close_started = True
        self.status_update.emit("Dang don trinh duyet cu, vui long cho Orbita/GoLogin dong xong...", "orange")

        # Lưu ref process trước khi clear
        proc = self._process
        self._process = None
        gologin = self._gologin
        self._gologin = None
        self._using_gologin_api = False
        debug_port = self._debug_port
        profile_dir = self._profile_dir  # ★ Lưu để patch sau khi kill
        manager_acquired = self._browser_manager_acquired
        self._browser_manager_acquired = False
        known_pids = set(self._browser_pids)
        if self._process_pid:
            known_pids.add(self._process_pid)

        def _graceful_close():
            """Chạy trong background thread — đóng Chrome graceful."""
            import time
            # Bước 1: Gửi Browser.close qua HTTP
            try:
                import http.client
                conn = http.client.HTTPConnection("127.0.0.1", debug_port, timeout=2)
                conn.request("GET", "/json/close/all")
                conn.close()
                time.sleep(3)  # ★ Chờ Chrome flush cookie ra đĩa
            except Exception:
                pass

            if gologin:
                try:
                    gologin.stop()
                except Exception:
                    pass

            # Bước 2: Unlock/kill qua BrowserManager nếu vẫn còn sống
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

            # ★ Bước 3: Patch exit_type=Normal SAU KHI kill
            # Chrome ghi "Crashed" khi bị kill → phải ghi đè lại ngay
            time.sleep(0.5)  # Chờ Chrome flush xong
            try:
                import json as _json, os as _os
                prefs_path = _os.path.join(profile_dir, "Default", "Preferences")
                if _os.path.exists(prefs_path):
                    prefs = _json.load(open(prefs_path, encoding="utf-8"))
                    prefs.setdefault("profile", {})["exit_type"] = "Normal"
                    prefs["profile"]["exited_cleanly"] = True
                    open(prefs_path, "w", encoding="utf-8").write(
                        _json.dumps(prefs, ensure_ascii=False)
                    )
            except Exception:
                pass

        # ★ daemon=False → thread KHÔNG bị kill khi app đóng
            self._process_pid = 0
            self._browser_pids.clear()
            self._emit_browser_closed_once("closed")

        import threading
        self._close_thread = threading.Thread(target=_graceful_close, daemon=False)
        self._close_thread.start()

        # Dừng local proxy
        if hasattr(self, '_local_proxy') and self._local_proxy:
            try: self._local_proxy.stop()
            except Exception: pass


