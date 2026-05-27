"""
Upload Worker — Tự động hóa đăng video lên TikTok Studio.
Tái sử dụng CDP engine từ cdp_worker.py.
"""
import os
import time
import math
import random
import asyncio
import shutil
import json as _json
import zipfile
import tempfile
import threading
from datetime import datetime, timedelta
from PyQt5.QtCore import QThread, pyqtSignal
from gologin_config import load_gologin_settings
from app_paths import gologin_base_dir, gologin_profile_dir, resource_path

GOLOGIN_BASE_DIR = str(gologin_base_dir())
ZERO_PROFILE_ZIP = str(resource_path("gologin_zeroprofile.zip"))
TIKTOK_STUDIO_URL = "https://www.tiktok.com/tiktokstudio/upload?from=webapp"
_GOLOGIN_START_LOCK = threading.RLock()

# Stealth JS (giống cdp_worker.py)
STEALTH_JS = r"""
Object.defineProperty(navigator, 'webdriver', {get: () => false, configurable: true});
if (!window.chrome) {
    window.chrome = {runtime: {onConnect: {addListener: function(){}}, onMessage: {addListener: function(){}}}, loadTimes: function(){return {};}, csi: function(){return {};}, app: {isInstalled: false}};
}
for (const prop of Object.keys(window)) { if (prop.match(/^cdc_/) || prop.match(/^\$cdc_/)) delete window[prop]; }
const origQ = window.navigator.permissions.query;
window.navigator.permissions.query = (p) => (p.name === 'notifications' ? Promise.resolve({state: Notification.permission}) : origQ(p));
Object.defineProperty(navigator, 'plugins', {get: () => [{name:'Chrome PDF Plugin'},{name:'Chrome PDF Viewer'},{name:'Native Client'}], configurable: true});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en'], configurable: true});
"""


def _profile_dir_for_browser_id(browser_id):
    if not browser_id:
        return ""
    return str(gologin_profile_dir(browser_id))


def _parse_cookie_header(cookie_header):
    cookies = []
    for part in (cookie_header or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        if not name or any(ch in name for ch in " \t\r\n;,"):
            continue
        cookies.append((name, value.strip()))
    return cookies


def _has_valid_tiktok_auth_cookie(cookies):
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


def _parse_schedule_datetime(value):
    text = (value or "").strip()
    formats = (
        "%m/%d/%Y %I:%M %p",  # legacy UI: 05/14/2026 03:30 PM
        "%m/%d/%Y %H:%M",     # 24h UI:     05/14/2026 15:30
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %I:%M %p",
    )
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    raise ValueError(f"Invalid schedule datetime: {value}")


def _format_schedule_datetime(dt):
    return dt.strftime("%m/%d/%Y %H:%M")


def _parse_debug_port(debugger_address):
    text = str(debugger_address or "").strip()
    if not text:
        return 0
    try:
        if text.startswith("ws://") or text.startswith("wss://") or "://" in text:
            from urllib.parse import urlparse
            parsed = urlparse(text)
            return int(parsed.port or 0)
        if ":" in text:
            return int(text.rsplit(":", 1)[1].split("/", 1)[0])
        return int(text)
    except Exception:
        return 0


def _normalize_proxy_mode(proxy_type):
    proxy_type = (proxy_type or "http").strip().lower()
    if proxy_type == "socks5h":
        proxy_type = "socks5"
    if proxy_type == "https":
        proxy_type = "http"
    if proxy_type not in ("http", "socks4", "socks5"):
        proxy_type = "http"
    return proxy_type


def _parse_proxy_string(proxy_str, proxy_type="http"):
    """Parse host:port, host:port:user:pass, scheme://host:port:user:pass, or user:pass@host:port."""
    proxy_str = (proxy_str or "").strip()
    if not proxy_str:
        return None

    proxy_type = _normalize_proxy_mode(proxy_type)
    if "://" in proxy_str:
        scheme, proxy_str = proxy_str.split("://", 1)
        proxy_type = _normalize_proxy_mode(scheme)

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


def _proxy_display_text(payload):
    if not payload:
        return ""
    return f"{payload.get('mode')}://{payload.get('host')}:{payload.get('port')}"


def _sync_gologin_profile_proxy(token, profile_id, task):
    proxy_payload = _parse_proxy_string(
        task.get("proxy", ""),
        task.get("proxy_type", "http"),
    )
    if not proxy_payload:
        return True, "Khong co proxy de dong bo"

    proxy_payload["changeIpUrl"] = ""
    profile_name = (task.get("upload_to") or "").strip()
    if profile_name:
        proxy_payload["customName"] = profile_name[:80]

    body = {
        "proxies": [
            {
                "profileId": profile_id,
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
    except Exception as exc:
        return False, f"Loi ket noi API GoLogin khi dong bo proxy: {exc}"

    if response.status_code in (200, 201, 204):
        return True, _proxy_display_text(proxy_payload)

    detail = (response.text or "").strip().replace("\n", " ")[:300]
    return False, f"GoLogin proxy API loi HTTP {response.status_code}: {detail}"


class UploadWorker(QThread):
    """Worker thread xử lý upload video lên TikTok."""
    status_updated = pyqtSignal(int, str, str)    # (task_index, message, color)
    progress_updated = pyqtSignal(int, int)        # (task_index, percent)
    task_completed = pyqtSignal(int, bool, str)    # (task_index, success, detail)
    all_done = pyqtSignal()
    browser_ready_signal = pyqtSignal(dict)

    def __init__(self, tasks, settings, parent=None, preview_targets=None):
        super().__init__(parent)
        self.tasks = tasks
        # tasks: [{title, file_path, upload_to, schedule_time, gologin_profile_id, browser_id, proxy, proxy_type}, ...]
        # settings: {delete_on_success, max_threads, delay_min, delay_max}
        self.settings = settings
        self.preview_targets = preview_targets or {}
        self._embedded_hwnds = {}
        self._stop_flag = False
        self._profile_locks = {}  # {profile_name: bool}
        self._embed_done_events = {}
        self._embed_results = {}
        self._current_embed_idx = None


    def notify_embed_result(self, success=False, hwnd=0, pid=0, message="", task_index=None):
        try:
            idx = int(task_index if task_index is not None else -1)
        except Exception:
            idx = -1
        if idx < 0 and self._current_embed_idx is not None:
            idx = int(self._current_embed_idx)
        elif idx < 0 and len(self._embed_done_events) == 1:
            idx = next(iter(self._embed_done_events.keys()))
        self._embed_results[idx] = {
            "success": bool(success),
            "hwnd": int(hwnd or 0),
            "pid": int(pid or 0),
            "message": str(message or ""),
        }
        if success and hwnd:
            self._embedded_hwnds[idx] = int(hwnd)
        event = self._embed_done_events.get(idx)
        if event:
            event.set()

    def _request_browser_embed_from_ui(self, idx, task, debug_port, browser_pid, timeout=30.0):
        preview_target = self.preview_targets.get(idx, {})
        widget_id = int(preview_target.get("widget_id") or 0)
        if not widget_id:
            return False
        event = threading.Event()
        self._current_embed_idx = idx
        self._embed_done_events[idx] = event
        self._embed_results[idx] = {"success": False, "hwnd": 0, "pid": 0, "message": ""}
        payload = {
            "worker_id": id(self),
            "task_index": int(idx),
            "widget_id": widget_id,
            "debug_port": int(debug_port or 0),
            "process_pid": int(browser_pid or 0),
            "profile_dir": str(task.get("profile_dir") or ""),
            "profile_id": str(task.get("gologin_profile_id") or task.get("browser_id") or ""),
            "container_width": int(preview_target.get("width") or 1280),
            "container_height": int(preview_target.get("height") or 800),
            "timeout": float(timeout),
        }
        self.browser_ready_signal.emit(payload)
        try:
            deadline = time.time() + float(timeout) + 3.0
            while not self._stop_flag and time.time() < deadline:
                if event.wait(0.1):
                    return bool(self._embed_results.get(idx, {}).get("success"))
            return False
        finally:
            self._embed_done_events.pop(idx, None)
            if self._current_embed_idx == idx:
                self._current_embed_idx = None

    def stop(self):
        self._stop_flag = True

    def run(self):
        """Main entry — validate rồi chạy async loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._process_queue())
        except Exception as e:
            print(f"[UploadWorker] Fatal: {e}")
        finally:
            loop.close()
            self.all_done.emit()

    # ═══════════════════════════════════════════════
    #  PHASE 0: PRE-FLIGHT VALIDATION
    # ═══════════════════════════════════════════════
    def _validate_task(self, idx, task):
        """Kiểm tra 1 task trước khi upload. Return (ok, reason)."""
        fp = task.get("file_path", "")

        # Check 1: File tồn tại
        if not fp or not os.path.exists(fp):
            return False, f"File không tồn tại: {fp}"

        # Check 2: File bị khóa
        try:
            with open(fp, "rb") as f:
                f.read(1)
        except Exception:
            return False, "File đang bị khóa bởi ứng dụng khác"

        # Check 3: Dung lượng <= 10GB
        size_gb = os.path.getsize(fp) / (1024**3)
        if size_gb > 10:
            return False, f"File quá lớn: {size_gb:.1f}GB (max 10GB)"

        # Check 4: Profile hợp lệ
        if not task.get("upload_to"):
            return False, "Chưa gán tài khoản Upload To"

        # Check 5: GoLogin profile ID. Upload must use the real GoLogin/Orbita profile.
        gologin_profile_id = (
            task.get("gologin_profile_id")
            or task.get("browser_id")
            or ""
        )
        gologin_profile_id = str(gologin_profile_id).strip()
        if not gologin_profile_id:
            return False, "Profile chua co GoLogin Profile ID"
        task["gologin_profile_id"] = gologin_profile_id

        # Check 6: Schedule >= 30 phút

        sched = task.get("schedule_time", "")
        if sched and sched != "Public":
            try:
                sched_dt = _parse_schedule_datetime(sched)
                minute_remainder = sched_dt.minute % 5
                if minute_remainder:
                    sched_dt = (sched_dt + timedelta(minutes=5 - minute_remainder)).replace(second=0, microsecond=0)
                task["schedule_time"] = _format_schedule_datetime(sched_dt)
                diff = (sched_dt - datetime.now()).total_seconds()
                if diff < 30 * 60:
                    return False, f"Schedule phải cách hiện tại >= 30 phút (hiện chỉ {int(diff/60)} phút)"
                if diff > 10 * 24 * 3600:
                    return False, "Schedule không được quá 10 ngày trong tương lai"
            except ValueError:
                return False, f"Sai định dạng thời gian schedule: {sched}"

        return True, "OK"

    # ═══════════════════════════════════════════════
    #  PHASE 7: QUEUE MANAGEMENT
    async def _process_queue(self):
        """Process queued uploads by account, reusing one browser per account."""
        valid_tasks = []
        for i, task in enumerate(self.tasks):
            ok, reason = self._validate_task(i, task)
            if not ok:
                self.status_updated.emit(i, f"❌ {reason}", "red")
                self.task_completed.emit(i, False, reason)
            else:
                valid_tasks.append((i, task))

        if not valid_tasks:
            return

        def sort_key(item):
            sched = item[1].get("schedule_time", "")
            if not sched or sched == "Public":
                return datetime.min
            try:
                return _parse_schedule_datetime(sched)
            except ValueError:
                return datetime.min

        valid_tasks.sort(key=sort_key)

        grouped_tasks = []
        profile_map = {}
        for idx, task in valid_tasks:
            profile = task.get("upload_to", "")
            if profile not in profile_map:
                profile_map[profile] = []
                grouped_tasks.append((profile, profile_map[profile]))
            profile_map[profile].append((idx, task))

        for profile, profile_tasks in grouped_tasks:
            if self._stop_flag:
                for idx, _task in profile_tasks:
                    self.status_updated.emit(idx, "⏹ Đã dừng bởi user", "orange")
                break

            while self._profile_locks.get(profile, False):
                if self._stop_flag:
                    break
                await asyncio.sleep(1)

            self._profile_locks[profile] = True
            try:
                await self._upload_profile_videos(profile, profile_tasks)
            except Exception as e:
                for idx, _task in profile_tasks:
                    self.status_updated.emit(idx, f"❌ Lỗi: {str(e)[:80]}", "red")
                    self.task_completed.emit(idx, False, str(e))
            finally:
                self._profile_locks[profile] = False

    def _gologin_install_help(self):
        return "Thieu GoLogin SDK. Cai truoc bang lenh: python -m pip install gologin"

    def _launch_gologin_profile(self, idx, task, width=1280, height=800):
        """Open the real GoLogin/Orbita profile and return (gl, debug_port, browser_pid)."""
        token = (load_gologin_settings().get("api_key") or "").strip()
        if not token:
            raise RuntimeError("Thieu GoLogin API Key. Vao menu API | Cookie de nhap token.")

        gologin_profile_id = (
            task.get("gologin_profile_id")
            or task.get("browser_id")
            or ""
        )
        gologin_profile_id = str(gologin_profile_id).strip()
        if not gologin_profile_id:
            raise RuntimeError("Profile nay chua co GoLogin Profile ID.")

        if (task.get("proxy") or "").strip():
            ok, message = _sync_gologin_profile_proxy(token, gologin_profile_id, task)
            if not ok:
                raise RuntimeError(message)
            if message:
                self.status_updated.emit(
                    idx,
                    f"GoLogin proxy synced: {message}",
                    "blue",
                )

        try:
            from gologin import GoLogin
        except Exception as exc:
            raise RuntimeError(self._gologin_install_help()) from exc

        extra_params = [
            f"--window-size={int(width)},{int(height)}",
            "--window-position=0,0",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-session-crashed-bubble",
            "--hide-crash-restore-bubble",
            "--disable-infobars",
        ]

        self.status_updated.emit(
            idx,
            f"Mo GoLogin profile {gologin_profile_id} bang Local SDK...",
            "blue",
        )
        gl = GoLogin({
            "token": token,
            "profile_id": gologin_profile_id,
            "spawn_browser": True,
            "uploadCookiesToServer": True,
            "writeCookiesFromServer": True,
            "restore_last_session": True,
            "extra_params": extra_params,
        })

        try:
            with _GOLOGIN_START_LOCK:
                if self._stop_flag:
                    raise RuntimeError("Da dung truoc khi mo GoLogin.")
                debugger_address = gl.start()
        except Exception:
            try:
                gl.stop()
            except Exception:
                pass
            raise

        debug_port = _parse_debug_port(debugger_address)
        if not debug_port:
            try:
                gl.stop()
            except Exception:
                pass
            raise RuntimeError(f"GoLogin khong tra ve CDP port hop le: {debugger_address}")

        browser_pid = int(getattr(gl, "pid", 0) or 0)
        profile_path = getattr(gl, "profile_path", "")
        if profile_path:
            task["profile_dir"] = profile_path
        self.status_updated.emit(
            idx,
            f"GoLogin SDK da mo profile (CDP port {debug_port}).",
            "green",
        )
        return gl, debug_port, browser_pid

    def _stop_gologin_profile(self, gl, debug_port=0):
        if not gl:
            return
        try:
            gl.stop()
            return
        except Exception:
            pass

        # Last resort: close the process that owns the CDP port.
        if not debug_port:
            return
        try:
            import psutil
            for conn in psutil.net_connections(kind="tcp"):
                try:
                    if not conn.laddr or conn.laddr.port != int(debug_port) or not conn.pid:
                        continue
                    proc = psutil.Process(conn.pid)
                    for child in proc.children(recursive=True):
                        try:
                            child.terminate()
                        except Exception:
                            pass
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    return
                except Exception:
                    continue
        except Exception:
            pass

    async def _upload_profile_videos(self, profile, profile_tasks):
        """Open one browser for a profile and upload all its videos sequentially."""
        from cdp_client import CDPClient

        first_idx, first_task = profile_tasks[0]
        debug_port = 0
        browser_pid = 0
        gl = None
        cdp = None
        preview_target = self.preview_targets.get(first_idx, {})
        if preview_target:
            first_task["_container_width"] = preview_target.get("width", 1280)
            first_task["_container_height"] = preview_target.get("height", 800)

        try:
            self.status_updated.emit(first_idx, f"🚀 Mở GoLogin/Orbita cho {profile}...", "blue")
            try:
                gl, debug_port, browser_pid = self._launch_gologin_profile(
                    first_idx,
                    first_task,
                    width=first_task.get("_container_width", 1280),
                    height=first_task.get("_container_height", 800),
                )
            except Exception as e:
                detail = str(e)
                lowered = detail.lower()
                if "using" in lowered or "already" in lowered or "đang" in lowered or "dang" in lowered:
                    detail = "Loi: GoLogin profile dang duoc su dung hoac chua dong xong"
                for idx, _task in profile_tasks:
                    self.status_updated.emit(idx, f"❌ {detail}", "red")
                    self.task_completed.emit(idx, False, detail)
                return

            if not debug_port:
                for idx, _task in profile_tasks:
                    self.task_completed.emit(idx, False, "Khong mo duoc GoLogin browser")
                return

            if preview_target:
                self._request_browser_embed_from_ui(first_idx, first_task, debug_port, browser_pid, timeout=30.0)
                hwnd = self._embedded_hwnds.get(first_idx)
                if hwnd:
                    for idx, _task in profile_tasks:
                        self._embedded_hwnds[idx] = hwnd

            await asyncio.sleep(4)
            self.status_updated.emit(first_idx, f"🔌 Kết nối CDP port {debug_port}...", "blue")
            cdp = CDPClient(port=debug_port)
            await cdp.connect(timeout=15)
            self.status_updated.emit(first_idx if "first_idx" in locals() else idx, "GoLogin strict: giu nguyen fingerprint profile", "green")

            if first_task.get("cookie"):
                self.status_updated.emit(
                    first_idx,
                    "Co cookie backup trong tool, upload khong nap som de giu session GoLogin.",
                    "blue",
                )

            for pos, (idx, task) in enumerate(profile_tasks):
                if self._stop_flag:
                    self.status_updated.emit(idx, "⏹ Đã dừng bởi user", "orange")
                    break

                if pos > 0:
                    self.status_updated.emit(idx, "↩ Quay lại trang upload cho video tiếp theo...", "blue")

                ok = await self._upload_video_in_open_browser(cdp, idx, task)
                if not ok:
                    self.status_updated.emit(idx, "⛔ Dừng queue vì video hiện tại bị lỗi", "red")
                    break

                if pos < len(profile_tasks) - 1 and not self._stop_flag:
                    d_min = self.settings.get("delay_min", 5)
                    d_max = self.settings.get("delay_max", 10)
                    delay = random.randint(d_min, d_max)
                    self.status_updated.emit(idx, f"⏳ Chờ {delay}s trước video tiếp...", "blue")
                    await asyncio.sleep(delay)
        finally:
            if cdp:
                try:
                    await cdp.disconnect()
                except Exception:
                    pass
            self._stop_gologin_profile(gl, debug_port)

    async def _upload_video_in_open_browser(self, cdp, idx, task):
        """Upload one video using an already-open browser session."""
        task["_last_error"] = ""

        self.status_updated.emit(idx, "🌐 Vào TikTok Studio Upload...", "blue")
        await cdp.send("Page.navigate", {"url": TIKTOK_STUDIO_URL})
        await asyncio.sleep(5)

        session_state = await self._wait_tiktok_studio_ready(cdp, idx=idx, timeout=30)
        if not session_state.get("ok"):
            reason = session_state.get("reason") or "TikTok Studio chưa sẵn sàng"
            task["_last_error"] = reason
            self.status_updated.emit(idx, f"❌ {reason}", "red")
            recovered = await self._hold_browser_for_upload_issue(cdp, idx, reason)
            if not recovered:
                self.task_completed.emit(idx, False, reason)
                return False

        await self._dismiss_popups(cdp, idx)
        self.status_updated.emit(idx, "✅ Đã vào TikTok Studio", "green")

        file_path = task["file_path"].replace("\\", "/")
        self.status_updated.emit(idx, f"📤 Đang upload: {os.path.basename(file_path)}", "blue")
        upload_ok = await self._inject_file(cdp, file_path)
        if not upload_ok:
            self.task_completed.emit(idx, False, "Không tìm thấy input file")
            return False

        file_size_mb = os.path.getsize(task["file_path"]) / (1024**2)
        timeout_sec = max(120, int(file_size_mb / 50 * 60) + 60)
        render_ok = await self._wait_upload_complete(cdp, idx, timeout_sec)
        if not render_ok:
            await self._try_save_draft(cdp, idx)
            self.task_completed.emit(idx, False, "Upload/Render timeout")
            return False
        await self._dismiss_popups(cdp, idx)

        title = task.get("title", "")
        if title:
            self.status_updated.emit(idx, f"📝 Điền tiêu đề: {title[:40]}...", "blue")
            await self._fill_caption(cdp, title)
            await self._dismiss_popups(cdp, idx)

        sched = task.get("schedule_time", "")
        is_schedule = bool(sched and sched != "Public")
        if is_schedule:
            self.status_updated.emit(idx, f"📅 Lên lịch: {sched}", "blue")
            schedule_ok = await self._set_schedule(cdp, sched, idx)
            if not schedule_ok:
                task["_last_error"] = "Không đặt được lịch"
                self.task_completed.emit(idx, False, "Không đặt được lịch")
                return False
            await asyncio.sleep(1)
            post_clicked = await self._click_post_button(cdp, is_schedule=True, idx=idx)
        else:
            self.status_updated.emit(idx, "🚀 Đăng Public...", "blue")
            post_clicked = await self._click_post_button(cdp, is_schedule=False, idx=idx)

        if not post_clicked:
            task["_last_error"] = "Không bấm được nút Đăng"
            await self._try_save_draft(cdp, idx)
            self.task_completed.emit(idx, False, "Không bấm được nút Đăng")
            return False

        await asyncio.sleep(3)
        success = await self._verify_post_success(cdp, idx)
        if success:
            self.status_updated.emit(idx, "✅ Đăng thành công!", "green")
            self.task_completed.emit(idx, True, "OK")
            if self.settings.get("delete_on_success", False):
                try:
                    os.remove(task["file_path"])
                    self.status_updated.emit(idx, "🗑️ Đã xóa file gốc", "blue")
                except Exception:
                    pass
            return True

        await self._try_save_draft(cdp, idx)
        self.task_completed.emit(idx, False, "Không xác nhận được thành công")
        return False

    async def _upload_single_video(self, idx, task):
        """Toàn bộ flow upload cho 1 video."""
        from cdp_client import CDPClient

        debug_port = 0
        browser_pid = 0
        gl = None
        preview_target = self.preview_targets.get(idx, {})
        if preview_target:
            task["_container_width"] = preview_target.get("width", 1280)
            task["_container_height"] = preview_target.get("height", 800)

        try:
            # ── PHASE 1: Launch Browser ──
            self.status_updated.emit(idx, "🚀 Đang mở GoLogin/Orbita...", "blue")
            try:
                gl, debug_port, browser_pid = self._launch_gologin_profile(
                    idx,
                    task,
                    width=task.get("_container_width", 1280),
                    height=task.get("_container_height", 800),
                )
            except Exception as e:
                detail = str(e)
                lowered = detail.lower()
                if "using" in lowered or "already" in lowered or "đang" in lowered or "dang" in lowered:
                    detail = "Loi: GoLogin profile dang duoc su dung hoac chua dong xong"
                self.status_updated.emit(idx, f"❌ {detail}", "red")
                self.task_completed.emit(idx, False, detail)
                return
            if preview_target and browser_pid:
                self._request_browser_embed_from_ui(idx, task, debug_port, browser_pid, timeout=30.0)

            if not debug_port:
                self.task_completed.emit(idx, False, "Khong mo duoc GoLogin browser")
                return

            await asyncio.sleep(4)  # Chờ browser ready

            # ── Kết nối CDP ──
            self.status_updated.emit(idx, f"🔌 Kết nối CDP port {debug_port}...", "blue")
            cdp = CDPClient(port=debug_port)
            await cdp.connect(timeout=15)

            # Inject stealth
            self.status_updated.emit(first_idx if "first_idx" in locals() else idx, "GoLogin strict: giu nguyen fingerprint profile", "green")

            # ── PHASE 2: Navigate to TikTok Studio ──
            self.status_updated.emit(idx, "🌐 Đang vào TikTok Studio...", "blue")
            if task.get("cookie"):
                self.status_updated.emit(
                    idx,
                    "Co cookie backup trong tool, upload khong nap som de giu session GoLogin.",
                    "blue",
                )

            await cdp.send("Page.navigate", {"url": TIKTOK_STUDIO_URL})
            await asyncio.sleep(5)

            # Kiểm tra phiên thật trong GoLogin trước khi upload.
            session_state = await self._wait_tiktok_studio_ready(cdp, idx=idx, timeout=30)
            if not session_state.get("ok"):
                reason = session_state.get("reason") or "TikTok Studio chưa sẵn sàng"
                self.status_updated.emit(idx, f"❌ {reason}", "red")
                recovered = await self._hold_browser_for_upload_issue(cdp, idx, reason)
                if not recovered:
                    self.task_completed.emit(idx, False, reason)
                    return

            # Đóng popup nếu có
            await self._dismiss_popups(cdp, idx)
            self.status_updated.emit(idx, "✅ Đã vào TikTok Studio", "green")

            # ── PHASE 3: Upload File ──
            file_path = task["file_path"].replace("\\", "/")
            self.status_updated.emit(idx, f"📤 Đang upload: {os.path.basename(file_path)}", "blue")

            upload_ok = await self._inject_file(cdp, file_path)
            if not upload_ok:
                self.task_completed.emit(idx, False, "Không tìm thấy input file")
                return

            # Chờ upload + render hoàn tất
            file_size_mb = os.path.getsize(task["file_path"]) / (1024**2)
            timeout_sec = max(120, int(file_size_mb / 50 * 60) + 60)
            render_ok = await self._wait_upload_complete(cdp, idx, timeout_sec)
            if not render_ok:
                # Thử save draft
                await self._try_save_draft(cdp, idx)
                self.task_completed.emit(idx, False, "Upload/Render timeout")
                return
            await self._dismiss_popups(cdp, idx)

            # ── PHASE 4: Điền tiêu đề ──
            title = task.get("title", "")
            if title:
                self.status_updated.emit(idx, f"📝 Điền tiêu đề: {title[:40]}...", "blue")
                await self._fill_caption(cdp, title)
                await self._dismiss_popups(cdp, idx)

            # ── PHASE 5: Public hoặc Schedule ──
            sched = task.get("schedule_time", "")
            is_schedule = sched and sched != "Public"

            if is_schedule:
                self.status_updated.emit(idx, f"📅 Đặt lịch: {sched}", "blue")
                schedule_ok = await self._set_schedule(cdp, sched, idx)
                if not schedule_ok:
                    self.task_completed.emit(idx, False, "Không đặt được lịch")
                    return
                await asyncio.sleep(1)
                # Click nút Schedule
                post_clicked = await self._click_post_button(cdp, is_schedule=True, idx=idx)
            else:
                self.status_updated.emit(idx, "🚀 Đăng Public...", "blue")
                post_clicked = await self._click_post_button(cdp, is_schedule=False, idx=idx)

            if not post_clicked:
                await self._try_save_draft(cdp, idx)
                self.task_completed.emit(idx, False, "Không bấm được nút Đăng")
                return

            # ── PHASE 6: Xác nhận ──
            await asyncio.sleep(3)
            success = await self._verify_post_success(cdp, idx)

            if success:
                self.status_updated.emit(idx, "✅ Đăng thành công!", "green")
                self.task_completed.emit(idx, True, "OK")

                # Xóa file nếu được tick
                if self.settings.get("delete_on_success", False):
                    try:
                        os.remove(task["file_path"])
                        self.status_updated.emit(idx, "🗑️ Đã xóa file gốc", "blue")
                    except Exception:
                        pass
            else:
                # Fallback: save draft
                await self._try_save_draft(cdp, idx)
                self.task_completed.emit(idx, False, "Không xác nhận được thành công")

            await cdp.disconnect()

        finally:
            # Đóng browser
            self._stop_gologin_profile(gl, debug_port)

    # ═══════════════════════════════════════════════
    #  HELPERS
    # ═══════════════════════════════════════════════

    def _prepare_profile_dir(self, profile_dir):
        """Ensure the Chrome user-data dir exists before validation passes."""
        try:
            if os.path.exists(profile_dir):
                self._fix_chrome_exit_type(profile_dir)
                return True

            parent_dir = os.path.dirname(profile_dir)
            os.makedirs(parent_dir, exist_ok=True)

            if os.path.exists(ZERO_PROFILE_ZIP):
                with tempfile.TemporaryDirectory(dir=parent_dir) as tmp_dir:
                    with zipfile.ZipFile(ZERO_PROFILE_ZIP, "r") as zf:
                        zf.extractall(tmp_dir)
                    extracted = os.path.join(tmp_dir, "gologin_zeroprofile")
                    if os.path.exists(extracted):
                        shutil.move(extracted, profile_dir)
                    else:
                        os.makedirs(profile_dir, exist_ok=True)
            else:
                os.makedirs(profile_dir, exist_ok=True)

            self._fix_chrome_exit_type(profile_dir)
            return os.path.exists(profile_dir)
        except Exception as e:
            print(f"[UploadWorker] prepare profile failed: {e}")
            return False

    async def _apply_tiktok_cookies(self, cdp, cookie_header):
        cookies = _parse_cookie_header(cookie_header)
        if not cookies:
            return
        try:
            await cdp.send("Network.enable", {})
            for name, value in cookies:
                params = {
                    "name": name,
                    "value": value,
                    "domain": ".tiktok.com",
                    "path": "/",
                    "secure": True,
                    "httpOnly": name.lower() in {
                        "sessionid", "sessionid_ss", "sid_tt", "sid_guard",
                        "sid_ucp_v1", "ssid_ucp_v1", "odin_tt"
                    },
                    "sameSite": "None",
                    "url": "https://www.tiktok.com/"
                }
                try:
                    await cdp.send("Network.setCookie", params)
                except Exception:
                    params.pop("sameSite", None)
                    await cdp.send("Network.setCookie", params)
        except Exception as e:
            print(f"[UploadWorker] apply cookies failed: {e}")

    async def _get_browser_cookies(self, cdp):
        try:
            result = await cdp.send("Network.getAllCookies")
            return result.get("cookies", [])
        except Exception:
            try:
                return await cdp.get_cookies()
            except Exception:
                return []

    async def _profile_has_tiktok_session(self, cdp):
        cookies = await self._get_browser_cookies(cdp)
        return _has_valid_tiktok_auth_cookie(cookies)

    async def _persist_tiktok_cookies(self, cdp, idx=None):
        try:
            cookies = await self._get_browser_cookies(cdp)
            tiktok_cookies = [c for c in cookies if "tiktok" in str(c.get("domain") or "").lower()]
            if not tiktok_cookies or not _has_valid_tiktok_auth_cookie(tiktok_cookies):
                return

            expires_epoch = time.time() + 30 * 24 * 3600
            persisted = 0
            for cookie in tiktok_cookies:
                if cookie.get("expires", 0) > 0 and not cookie.get("session", False):
                    continue
                payload = {
                    "name": cookie.get("name", ""),
                    "value": cookie.get("value", ""),
                    "domain": cookie.get("domain", ".tiktok.com"),
                    "path": cookie.get("path", "/"),
                    "secure": cookie.get("secure", True),
                    "httpOnly": cookie.get("httpOnly", False),
                    "expires": expires_epoch,
                }
                if not payload["name"]:
                    continue
                same_site = cookie.get("sameSite")
                if same_site in ("Strict", "Lax", "None"):
                    payload["sameSite"] = same_site
                try:
                    await cdp.send("Network.setCookie", payload)
                    persisted += 1
                except Exception:
                    pass

            if persisted and idx is not None:
                self.status_updated.emit(idx, f"🔒 Đã giữ {persisted} session cookie TikTok", "blue")
        except Exception:
            pass

    async def _wait_tiktok_studio_ready(self, cdp, idx=None, timeout=30):
        deadline = time.time() + max(1, timeout)
        last_reason = "TikTok Studio chưa load xong"

        while not self._stop_flag and time.time() < deadline:
            try:
                url = await cdp.get_url()
                state = await cdp.evaluate(r"""
                    (() => {
                        const body = document.body ? document.body.innerText : '';
                        const lower = body.toLowerCase();
                        const visible = (el) => {
                            if (!el) return false;
                            const r = el.getBoundingClientRect();
                            const s = getComputedStyle(el);
                            return r.width > 0 && r.height > 0 &&
                                s.display !== 'none' && s.visibility !== 'hidden';
                        };
                        const uploadInput = !!document.querySelector('input[type="file"]');
                        const uploadTexts = [
                            'chọn video', 'select video', 'select files', 'chọn tệp',
                            'upload video', 'tải lên', 'drag and drop'
                        ];
                        const ready = uploadInput || uploadTexts.some(t => lower.includes(t));
                        const loginVisible = Array.from(document.querySelectorAll('button, a, [role="button"]'))
                            .some(el => {
                                if (!visible(el)) return false;
                                const text = ((el.innerText || el.textContent || el.getAttribute('aria-label') || '') + '')
                                    .trim().toLowerCase();
                                return text === 'log in' || text === 'login' || text === 'đăng nhập' || text.includes('log in');
                            });
                        const pleaseWait = lower.includes('please wait') || lower.includes('just a moment');
                        const blocked = lower.includes('access denied') || lower.includes('403') ||
                            lower.includes('forbidden') || lower.includes('unusual traffic') ||
                            lower.includes('too many requests');
                        return {ready, loginVisible, pleaseWait, blocked, bodyLen: body.length, href: location.href};
                    })()
                """) or {}

                href = str(state.get("href") or url or "").lower()
                if state.get("ready"):
                    await self._persist_tiktok_cookies(cdp, idx=idx)
                    return {"ok": True, "reason": ""}

                if "/login" in href or state.get("loginVisible"):
                    return {"ok": False, "reason": "Profile GoLogin chưa đăng nhập TikTok"}

                if state.get("blocked"):
                    return {"ok": False, "reason": "TikTok Studio bị chặn hoặc load lỗi"}

                if state.get("pleaseWait"):
                    last_reason = "TikTok Studio kẹt ở Please wait"
                elif not state.get("bodyLen"):
                    last_reason = "TikTok Studio đang trắng/chưa render"
                else:
                    last_reason = "TikTok Studio chưa hiện khung upload"
            except Exception as exc:
                last_reason = f"Không kiểm tra được TikTok Studio: {str(exc)[:80]}"
                break

            await asyncio.sleep(1)

        if self._stop_flag:
            return {"ok": False, "reason": "Đã dừng bởi user"}
        return {"ok": False, "reason": last_reason}

    async def _hold_browser_for_upload_issue(self, cdp, idx, reason):
        reason = str(reason or "Upload chưa hoàn tất").strip()
        self.status_updated.emit(
            idx,
            f"⚠️ {reason}. Giữ browser mở để kiểm tra; đăng nhập tay xong tool sẽ thử chạy tiếp.",
            "orange",
        )
        self.status_updated.emit(idx, "Bấm Dừng nếu muốn đóng profile upload này.", "orange")

        while not self._stop_flag:
            await asyncio.sleep(3)
            try:
                state = await self._wait_tiktok_studio_ready(cdp, idx=idx, timeout=4)
                if state.get("ok"):
                    self.status_updated.emit(idx, "✅ TikTok Studio đã sẵn sàng, tiếp tục upload.", "green")
                    return True

                if await self._profile_has_tiktok_session(cdp):
                    self.status_updated.emit(idx, "🔄 Phát hiện session TikTok, mở lại Studio Upload...", "blue")
                    await cdp.send("Page.navigate", {"url": TIKTOK_STUDIO_URL})
                    await asyncio.sleep(5)
                    state = await self._wait_tiktok_studio_ready(cdp, idx=idx, timeout=8)
                    if state.get("ok"):
                        self.status_updated.emit(idx, "✅ TikTok Studio đã sẵn sàng, tiếp tục upload.", "green")
                        return True
            except Exception:
                self.status_updated.emit(idx, "Browser upload đã đóng trong lúc chờ kiểm tra.", "gray")
                return False

        return False

    def _fix_chrome_exit_type(self, profile_dir):
        """Fix Preferences để Chrome giữ session cookies."""
        prefs_path = os.path.join(profile_dir, "Default", "Preferences")
        if not os.path.exists(prefs_path):
            return
        try:
            with open(prefs_path, "r", encoding="utf-8") as f:
                prefs = _json.load(f)
            profile = prefs.get("profile", {})
            profile["exit_type"] = "Normal"
            profile["exited_cleanly"] = True
            prefs["profile"] = profile
            session = prefs.get("session", {})
            session["restore_on_startup"] = 5
            prefs["session"] = session
            with open(prefs_path, "w", encoding="utf-8") as f:
                _json.dump(prefs, f, ensure_ascii=False)
        except Exception:
            pass

    def _move_os_cursor_to_browser_point(self, idx, x, y):
        # Intentionally no-op: all clicks use CDP Input.dispatchMouseEvent.
        # Moving the real Windows cursor is disruptive while the dashboard runs.
        return

    async def _show_visible_cursor(self, cdp, x, y, idx=None):
        try:
            await cdp.evaluate(f"""
                (() => {{
                    let cursor = document.getElementById('__ssma_visible_cursor');
                    if (!cursor) {{
                        cursor = document.createElement('div');
                        cursor.id = '__ssma_visible_cursor';
                        cursor.style.cssText = [
                            'position:fixed',
                            'z-index:2147483647',
                            'width:18px',
                            'height:18px',
                            'border:3px solid ' + '#ff1744',
                            'border-radius:50%',
                            'background:rgba(255,23,68,.18)',
                            'box-shadow:0 0 0 4px rgba(255,23,68,.25)',
                            'pointer-events:none',
                            'transition:left .12s linear, top .12s linear, transform .08s linear'
                        ].join(';');
                        document.documentElement.appendChild(cursor);
                    }}
                    cursor.style.left = ({int(x)} - 9) + 'px';
                    cursor.style.top = ({int(y)} - 9) + 'px';
                    cursor.style.transform = 'scale(1.35)';
                    setTimeout(() => {{ cursor.style.transform = 'scale(1)'; }}, 120);
                }})()
            """)
        except Exception:
            pass

    async def _check_login(self, cdp):
        """Compatibility wrapper: true khi TikTok Studio upload đã sẵn sàng."""
        state = await self._wait_tiktok_studio_ready(cdp, timeout=10)
        return bool(state.get("ok"))

    async def _dismiss_popups(self, cdp, idx=None, press_escape=True):
        """Close non-critical TikTok Studio coachmarks/popups that can cover upload controls."""
        try:
            result = await cdp.evaluate(r"""
                (() => {
                    const visible = (el) => {
                        if (!el) return false;
                        const style = getComputedStyle(el);
                        if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
                        const rect = el.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                    };
                    const normalize = (text) => String(text || '')
                        .toLowerCase()
                        .normalize('NFD')
                        .replace(/[\u0300-\u036f]/g, '')
                        .replace(/đ/g, 'd')
                        .replace(/\s+/g, ' ')
                        .trim();
                    const textOf = (el) => normalize(
                        el.innerText || el.textContent || el.value ||
                        el.getAttribute('aria-label') || el.getAttribute('title') || ''
                    );
                    const dismissTexts = [
                        'da hieu', 'got it', 'ok', 'okay', 'xong', 'done',
                        'dong', 'close', 'skip', 'bo qua', 'not now', 'de sau',
                        'maybe later', 'understood', 'continue'
                    ];
                    const popupTexts = [
                        'bo sung tinh nang chinh sua moi',
                        'tao video chuyen nghiep',
                        'new editing',
                        'create professional videos',
                        'what is new',
                        'whats new'
                    ];
                    const clicked = [];

                    const clickEl = (el) => {
                        try {
                            el.scrollIntoView({block: 'center', inline: 'center'});
                            el.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
                            el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                            el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                            el.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                            return true;
                        } catch (_) {
                            try { el.click(); return true; } catch (_) { return false; }
                        }
                    };

                    const candidates = Array.from(document.querySelectorAll(
                        'button, [role="button"], [aria-label], [data-e2e], [class*="close"], [class*="Close"]'
                    )).filter(visible);

                    for (const el of candidates) {
                        const t = textOf(el);
                        const aria = normalize(el.getAttribute('aria-label') || '');
                        const className = (el.className && el.className.baseVal) ? el.className.baseVal : el.className;
                        const cls = normalize(className || '');
                        const isShortDismiss = t && t.length <= 40 && dismissTexts.some(x => t === x || t.includes(x));
                        const isCloseIcon = ['x', '×'].includes(t) || aria.includes('close') || aria.includes('dong') || cls.includes('close');
                        if (isShortDismiss || isCloseIcon) {
                            if (clickEl(el)) clicked.push(t || aria || 'close');
                        }
                    }

                    // Some TikTok coachmarks are not tagged as modal/dialog. If their dismiss
                    // button did not respond, hide only known marketing/tutorial overlays.
                    const overlays = Array.from(document.querySelectorAll(
                        '[role="dialog"], [aria-modal="true"], [class*="modal"], [class*="Modal"], ' +
                        '[class*="dialog"], [class*="Dialog"], [class*="overlay"], [class*="Overlay"], ' +
                        '[class*="coach"], [class*="Coach"], [class*="guide"], [class*="Guide"]'
                    )).filter(visible);
                    for (const el of overlays) {
                        const t = textOf(el);
                        if (popupTexts.some(x => t.includes(x))) {
                            el.style.setProperty('display', 'none', 'important');
                            el.style.setProperty('pointer-events', 'none', 'important');
                            clicked.push('hidden tutorial popup');
                        }
                    }

                    return {count: clicked.length, clicked};
                })()
            """) or {}
            count = int(result.get("count") or 0) if isinstance(result, dict) else 0
            if count and idx is not None:
                labels = ", ".join((result.get("clicked") or [])[:2]) if isinstance(result, dict) else ""
                self.status_updated.emit(idx, f"🧹 Đã bỏ qua popup TikTok{(': ' + labels) if labels else ''}", "blue")
            if count:
                await asyncio.sleep(0.8)
            elif press_escape:
                try:
                    await cdp.press_key("Escape")
                except Exception:
                    pass
                await asyncio.sleep(0.2)
            return count > 0
        except Exception:
            return False

    async def _allow_schedule_permission_popup(self, cdp, idx=None):
        """Accept schedule-related confirmation popups that appear after selecting 'Lên lịch'."""
        try:
            result = await cdp.evaluate(r"""
                (() => {
                    const visible = (el) => {
                        if (!el) return false;
                        const style = getComputedStyle(el);
                        if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
                        const rect = el.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                    };
                    const normalize = (text) => String(text || '')
                        .toLowerCase()
                        .normalize('NFD')
                        .replace(/[\u0300-\u036f]/g, '')
                        .replace(/đ/g, 'd')
                        .replace(/\s+/g, ' ')
                        .trim();
                    const textOf = (el) => normalize(
                        el.innerText || el.textContent || el.value ||
                        el.getAttribute('aria-label') || el.getAttribute('title') || ''
                    );
                    const clickEl = (el) => {
                        try {
                            el.scrollIntoView({block: 'center', inline: 'center'});
                            el.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
                            el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                            el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                            el.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                            return true;
                        } catch (_) {
                            try { el.click(); return true; } catch (_) { return false; }
                        }
                    };

                    const allowTexts = [
                        'cho phep', 'allow', 'dong y', 'agree', 'xac nhan', 'confirm',
                        'tiep tuc', 'continue', 'ok', 'okay', 'got it', 'save', 'luu',
                        'enable', 'turn on', 'bat'
                    ];
                    const rejectTexts = [
                        'khong', 'cancel', 'huy', 'bo qua', 'skip', 'not now', 'de sau',
                        'close', 'dong', 'later', 'tu choi'
                    ];
                    const popupHints = [
                        'len lich', 'schedule', 'thoi diem dang', 'save', 'luu',
                        'video', 'bai dang', 'draft', 'ban nhap', 'permission', 'quyen'
                    ];

                    const overlays = Array.from(document.querySelectorAll(
                        '[role="dialog"], [aria-modal="true"], [class*="modal"], [class*="Modal"], ' +
                        '[class*="dialog"], [class*="Dialog"], [class*="popup"], [class*="Popup"], ' +
                        '[class*="popover"], [class*="Popover"], [class*="drawer"], [class*="Drawer"]'
                    ))
                        .filter(visible)
                        .map(el => {
                            const r = el.getBoundingClientRect();
                            return {el, r, text: textOf(el), area: r.width * r.height};
                        })
                        .sort((a, b) => b.area - a.area);

                    for (const overlay of overlays) {
                        const body = overlay.text;
                        const hintCount = popupHints.filter(x => body.includes(x)).length;
                        if (hintCount < 2 && !(body.includes('schedule') || body.includes('len lich'))) continue;

                        const buttons = Array.from(overlay.el.querySelectorAll('button, [role="button"], [aria-label]'))
                            .filter(visible)
                            .map(el => {
                                const t = textOf(el);
                                const r = el.getBoundingClientRect();
                                return {el, t, area: r.width * r.height};
                            })
                            .filter(x => x.t && x.area >= 300);

                        const positives = buttons.filter(x =>
                            allowTexts.some(t => x.t === t || x.t.includes(t))
                            && !rejectTexts.some(t => x.t === t || x.t.includes(t))
                        );
                        positives.sort((a, b) => a.t.length - b.t.length || b.area - a.area);

                        for (const item of positives) {
                            if (clickEl(item.el)) {
                                return {clicked: true, label: item.t || 'allow'};
                            }
                        }
                    }
                    return {clicked: false};
                })()
            """) or {}
            clicked = bool(result.get("clicked")) if isinstance(result, dict) else False
            if clicked and idx is not None:
                label = (result.get("label") or "allow") if isinstance(result, dict) else "allow"
                self.status_updated.emit(idx, f"✅ Đã cho phép popup lịch: {label}", "blue")
            if clicked:
                await asyncio.sleep(0.8)
            return clicked
        except Exception:
            return False

    async def _inject_file(self, cdp, file_path):
        """Bơm file video vào input[type=file] ẩn qua CDP."""
        # Tìm input file
        for attempt in range(10):
            node_id = await cdp.query_selector('input[type="file"]')
            if node_id:
                break
            # Thử selector khác
            node_id = await cdp.query_selector('input[accept*="video"]')
            if node_id:
                break
            await asyncio.sleep(1)

        if not node_id:
            return False

        # Lấy backendNodeId hoặc dùng resolve
        try:
            result = await cdp.send("DOM.resolveNode", {"nodeId": node_id})
            object_id = result.get("object", {}).get("objectId")
            if object_id:
                await cdp.send("DOM.setFileInputFiles", {
                    "files": [file_path],
                    "objectId": object_id
                })
            else:
                await cdp.send("DOM.setFileInputFiles", {
                    "files": [file_path],
                    "nodeId": node_id
                })
            return True
        except Exception as e:
            print(f"[Upload] inject file error: {e}")
            return False

    async def _wait_upload_complete(self, cdp, idx, timeout_sec):
        """Chờ upload + render hoàn tất."""
        start = time.time()
        last_pct = -1

        while time.time() - start < timeout_sec:
            if self._stop_flag:
                return False

            # Kiểm tra progress
            info = await cdp.evaluate("""
                (() => {
                    const body = document.body ? document.body.innerText : '';
                    // Tìm phần trăm upload
                    const match = body.match(/(\\d+)\\s*%/);
                    const pct = match ? parseInt(match[1]) : -1;
                    // Kiểm tra đã ready chưa (nút Post/Schedule enabled)
                    const btns = document.querySelectorAll('button');
                    let postReady = false;
                    for (const b of btns) {
                        const t = b.innerText.toLowerCase().trim();
                        if ((t === 'post' || t === 'đăng' || t === 'schedule' || t === 'lên lịch' || t === 'đăng bài') && !b.disabled) {
                            postReady = true;
                            break;
                        }
                    }
                    // Kiểm tra lỗi
                    const hasError = body.includes('Upload failed') || body.includes('Tải lên thất bại');
                    return {pct, postReady, hasError};
                })()
            """)

            if info:
                if info.get("hasError"):
                    self.status_updated.emit(idx, "❌ TikTok báo upload thất bại", "red")
                    return False

                pct = info.get("pct", -1)
                if pct != last_pct and pct >= 0:
                    self.status_updated.emit(idx, f"📤 Uploading... {pct}%", "blue")
                    self.progress_updated.emit(idx, pct)
                    last_pct = pct

                if info.get("postReady"):
                    self.status_updated.emit(idx, "✅ Upload + Render hoàn tất!", "green")
                    return True

            await asyncio.sleep(3)

        self.status_updated.emit(idx, "⏰ Upload timeout", "red")
        return False

    async def _fill_caption(self, cdp, title):
        """Điền tiêu đề vào ô caption (DraftJS editor)."""
        # Focus vào editor
        await cdp.evaluate("""
            (() => {
                const editor = document.querySelector('div[contenteditable="true"]');
                if (editor) { editor.focus(); editor.click(); }
            })()
        """)
        await asyncio.sleep(0.5)

        # Select all + Delete (xóa text cũ - TikTok tự điền filename)
        await cdp.send("Input.dispatchKeyEvent", {
            "type": "keyDown", "key": "a", "code": "KeyA",
            "windowsVirtualKeyCode": 65, "modifiers": 2  # Ctrl
        })
        await cdp.send("Input.dispatchKeyEvent", {
            "type": "keyUp", "key": "a", "code": "KeyA",
            "windowsVirtualKeyCode": 65, "modifiers": 2
        })
        await asyncio.sleep(0.2)
        await cdp.press_key("Backspace")
        await asyncio.sleep(0.3)

        # Gõ tiêu đề mới
        await cdp.type_text(title, delay=30)
        await asyncio.sleep(0.5)

    async def _set_schedule_legacy_dom(self, cdp, schedule_time):
        """Click radio Schedule và set ngày/giờ."""
        # Tìm và click radio/toggle Schedule
        await cdp.evaluate("""
            (() => {
                const labels = document.querySelectorAll('label, div[role="radio"], div[role="switch"], span, div');
                for (const el of labels) {
                    const t = (el.innerText || '').toLowerCase().trim();
                    if (t === 'schedule' || t === 'lên lịch' || t.includes('schedule')) {
                        el.click();
                        return true;
                    }
                }
                // Thử tìm radio button
                const radios = document.querySelectorAll('input[type="radio"]');
                for (const r of radios) {
                    const label = r.closest('label') || r.parentElement;
                    if (label && (label.innerText || '').toLowerCase().includes('schedule')) {
                        r.click();
                        return true;
                    }
                }
                return false;
            })()
        """)
        await asyncio.sleep(1)

        # Parse schedule_time
        try:
            dt = _parse_schedule_datetime(schedule_time)
        except ValueError:
            return

        # Set date và time qua các input trên trang
        date_str = dt.strftime("%m/%d/%Y")
        time_str = dt.strftime("%H:%M")

        await cdp.evaluate(f"""
            (() => {{
                // Tìm các input date/time
                const inputs = document.querySelectorAll('input');
                for (const inp of inputs) {{
                    const type = inp.type || '';
                    const ph = (inp.placeholder || '').toLowerCase();
                    if (type === 'date' || ph.includes('date') || ph.includes('ngày')) {{
                        const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                        nativeInputValueSetter.call(inp, '{date_str}');
                        inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                        inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                    }}
                }}
            }})()
        """)
        await asyncio.sleep(0.5)

    async def _set_schedule_legacy_input(self, cdp, schedule_time):
        """Select Schedule and set both date and time."""
        try:
            dt = _parse_schedule_datetime(schedule_time)
        except ValueError:
            return False

        date_iso = dt.strftime("%Y-%m-%d")
        date_us = dt.strftime("%m/%d/%Y")
        time_str = dt.strftime("%H:%M")

        result = await cdp.evaluate(f"""
            (() => {{
                const result = {{radio: false, date: false, time: false}};
                const isVisible = (el) => {{
                    const r = el.getBoundingClientRect();
                    const st = window.getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
                }};
                const textOf = (el) => ((el.innerText || el.textContent || el.getAttribute('aria-label') || '') + '').toLowerCase().trim();
                const clickSchedule = () => {{
                    const candidates = Array.from(document.querySelectorAll('label, button, [role="radio"], [role="switch"], span, div'));
                    for (const el of candidates) {{
                        const t = textOf(el);
                        if (!t) continue;
                        if (t === 'schedule' || t === 'lên lịch' || t.includes('schedule') || t.includes('lên lịch')) {{
                            const input = el.querySelector('input[type="radio"]') || el.closest('label')?.querySelector('input[type="radio"]');
                            (input || el).click();
                            result.radio = true;
                            return true;
                        }}
                    }}
                    const radios = Array.from(document.querySelectorAll('input[type="radio"]'));
                    if (radios.length >= 2) {{
                        radios[1].click();
                        result.radio = true;
                        return true;
                    }}
                    return false;
                }};
                const setInput = (el, value) => {{
                    const proto = el instanceof HTMLTextAreaElement ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
                    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                    el.scrollIntoView({{block: 'center'}});
                    el.focus();
                    el.click();
                    if (setter) setter.call(el, value); else el.value = value;
                    el.dispatchEvent(new Event('input', {{bubbles: true}}));
                    el.dispatchEvent(new Event('change', {{bubbles: true}}));
                    el.blur();
                }};
                const labelNear = (el) => {{
                    let cur = el;
                    for (let i = 0; i < 4 && cur; i++, cur = cur.parentElement) {{
                        const t = textOf(cur);
                        if (t) return t;
                    }}
                    return '';
                }};

                clickSchedule();

                const inputs = Array.from(document.querySelectorAll('input')).filter(isVisible);
                for (const inp of inputs) {{
                    const type = (inp.type || '').toLowerCase();
                    const ph = ((inp.placeholder || '') + ' ' + (inp.getAttribute('aria-label') || '') + ' ' + labelNear(inp)).toLowerCase();
                    if (!result.time && (type === 'time' || ph.includes('time') || ph.includes('giờ') || /^\\d{{1,2}}:\\d{{2}}/.test(inp.value || ''))) {{
                        setInput(inp, '{time_str}');
                        result.time = true;
                    }}
                    if (!result.date && (type === 'date' || ph.includes('date') || ph.includes('ngày') || /^\\d{{4}}-\\d{{2}}-\\d{{2}}/.test(inp.value || '') || /^\\d{{1,2}}\\/\\d{{1,2}}\\/\\d{{4}}/.test(inp.value || ''))) {{
                        setInput(inp, type === 'date' ? '{date_iso}' : '{date_iso}');
                        result.date = true;
                    }}
                }}

                if (!result.time && inputs.length >= 1) {{
                    const timeInput = inputs.find(inp => /^\\d{{1,2}}:\\d{{2}}/.test(inp.value || '') || (inp.placeholder || '').includes(':'));
                    if (timeInput) {{
                        setInput(timeInput, '{time_str}');
                        result.time = true;
                    }}
                }}
                if (!result.date && inputs.length >= 2) {{
                    const dateInput = inputs.find(inp => (inp.value || '').includes('-') || (inp.value || '').includes('/'));
                    if (dateInput) {{
                        setInput(dateInput, '{date_iso}');
                        result.date = true;
                    }}
                }}

                document.body.click();
                return result;
            }})()
        """)
        await asyncio.sleep(1)

        ok = bool(result and result.get("radio") and result.get("date") and result.get("time"))
        return ok

    async def _set_schedule(self, cdp, schedule_time, idx=None):
        """Select TikTok Studio schedule controls using the time/date from the upload table."""
        try:
            dt = _parse_schedule_datetime(schedule_time)
        except ValueError:
            return False

        original_dt = dt
        minute_remainder = dt.minute % 5
        if minute_remainder:
            dt = (dt + timedelta(minutes=5 - minute_remainder)).replace(second=0, microsecond=0)

        time_text = dt.strftime("%H:%M")
        hour_text = dt.strftime("%H")
        hour_alt = str(dt.hour)
        minute_text = dt.strftime("%M")
        minute_alt = str(dt.minute)
        day_text = str(dt.day)
        day_alt = dt.strftime("%d")
        date_text = dt.strftime("%Y-%m-%d")
        date_us = dt.strftime("%m/%d/%Y")
        target_schedule = {
            "hour": dt.hour,
            "minute": dt.minute,
            "year": dt.year,
            "month": dt.month,
            "day": dt.day,
        }
        today = datetime.now()
        day_pick_bias = "last" if (dt.year, dt.month) > (today.year, today.month) else "first"

        def step(msg, color="blue"):
            if idx is not None:
                self.status_updated.emit(idx, msg, color)

        if dt != original_dt:
            step(
                f"Phut {original_dt.strftime('%M')} khong co tren TikTok, doi sang {dt.strftime('%H:%M')}...",
                "orange",
            )

        async def click_center(pos):
            if not pos:
                return False
            x = int(pos["x"])
            y = int(pos["y"])
            await self._show_visible_cursor(cdp, x, y, idx)
            await asyncio.sleep(0.18)
            await cdp.click_at(x, y)
            return True

        async def scroll_schedule_to_bottom():
            await cdp.evaluate("""
                (() => {
                    const root = document.scrollingElement || document.documentElement || document.body;
                    if (root) root.scrollTop = root.scrollHeight;
                    window.scrollTo(0, Math.max(document.body.scrollHeight, document.documentElement.scrollHeight));
                })()
            """)
            await asyncio.sleep(0.35)

        async def get_schedule_control_state(kind):
            return await cdp.evaluate(f"""
                (() => {{
                    const useKind = {_json.dumps(kind)};
                    const visible = (el) => {{
                        const r = el.getBoundingClientRect();
                        const st = window.getComputedStyle(el);
                        return r.width > 0 && r.height > 0 &&
                            r.right > 0 && r.bottom > 0 && r.left < window.innerWidth && r.top < window.innerHeight &&
                            st.display !== 'none' && st.visibility !== 'hidden';
                    }};
                    const textOf = (el) => ((el.value || el.innerText || el.textContent || el.getAttribute('aria-label') || '') + '').trim();
                    const parseTime = (text) => {{
                        const m = (text || '').match(/\\b(\\d{{1,2}})\\s*:\\s*(\\d{{2}})\\b/);
                        if (!m) return null;
                        return {{hour: Number(m[1]), minute: Number(m[2]), text: m[0]}};
                    }};
                    const parseDate = (text) => {{
                        let m = (text || '').match(/\\b(\\d{{4}})\\s*-\\s*(\\d{{1,2}})\\s*-\\s*(\\d{{1,2}})\\b/);
                        if (m) return {{year: Number(m[1]), month: Number(m[2]), day: Number(m[3]), text: m[0]}};
                        m = (text || '').match(/\\b(\\d{{1,2}})\\s*\\/\\s*(\\d{{1,2}})\\s*\\/\\s*(\\d{{4}})\\b/);
                        if (m) return {{year: Number(m[3]), month: Number(m[1]), day: Number(m[2]), text: m[0]}};
                        return null;
                    }};
                    const controls = Array.from(document.querySelectorAll(
                        'button, input, [role="button"], [role="combobox"], [aria-haspopup], [aria-expanded], div, span'
                    ))
                        .filter(visible)
                        .map(el => {{
                            const r = el.getBoundingClientRect();
                            const t = textOf(el);
                            return {{el, r, t}};
                        }})
                        .filter(x => x.t && x.r.width >= 60 && x.r.width <= 280 && x.r.height >= 24 && x.r.height <= 90);
                    let matches = controls
                        .map(x => {{
                            const parsed = useKind === 'time' ? parseTime(x.t) : parseDate(x.t);
                            if (!parsed) return null;
                            return {{...x, ...parsed}};
                        }})
                        .filter(Boolean);
                    let scoped = matches.filter(x => x.r.top >= window.innerHeight * 0.35);
                    if (scoped.length) matches = scoped;
                    matches.sort((a, b) => b.r.top - a.r.top || (useKind === 'time' ? a.r.left - b.r.left : b.r.left - a.r.left));
                    const best = matches[0];
                    if (!best) return null;
                    return {{
                        x: best.r.left + best.r.width / 2,
                        y: best.r.top + best.r.height / 2,
                        left: best.r.left,
                        top: best.r.top,
                        width: best.r.width,
                        height: best.r.height,
                        text: best.t,
                        hour: best.hour,
                        minute: best.minute,
                        year: best.year,
                        month: best.month,
                        day: best.day,
                    }};
                }})()
            """)

        step("Scroll xuong khu vuc cai dat lich...", "blue")
        await scroll_schedule_to_bottom()

        step("🖱️ Click radio Lên lịch...", "blue")
        await cdp.evaluate("""
            (() => {
                const norm = (s) => (s || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').toLowerCase().trim();
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                let node;
                while ((node = walker.nextNode())) {
                    if (!norm(node.nodeValue).includes('thoi diem dang')) continue;
                    const el = node.parentElement;
                    if (el && el.scrollIntoView) el.scrollIntoView({block: 'center'});
                    break;
                }
            })()
        """)
        await asyncio.sleep(0.4)

        radio_points = await cdp.evaluate("""
            (() => {
                const norm = (s) => (s || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').toLowerCase().trim();
                const visible = (el) => {
                    const r = el.getBoundingClientRect();
                    const st = window.getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && st.display !== 'none' && st.visibility !== 'hidden';
                };
                const center = (el) => {
                    const r = el.getBoundingClientRect();
                    return {x: r.left + r.width / 2, y: r.top + r.height / 2};
                };
                const points = [];
                const push = (p) => {
                    if (!p) return;
                    if (p.x < 0 || p.y < 0 || p.x > window.innerWidth || p.y > window.innerHeight) return;
                    if (!points.some(q => Math.abs(q.x - p.x) < 4 && Math.abs(q.y - p.y) < 4)) points.push(p);
                };
                const rectCenter = (r) => ({x: r.left + r.width / 2, y: r.top + r.height / 2});

                const textCenter = (needle) => {
                    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                    let node;
                    while ((node = walker.nextNode())) {
                        const text = norm(node.nodeValue);
                        if (!text.includes(needle)) continue;
                        const range = document.createRange();
                        range.selectNodeContents(node);
                        const rects = Array.from(range.getClientRects()).filter(r => r.width > 0 && r.height > 0);
                        range.detach();
                        if (!rects.length) continue;
                        const r = rects[0];
                        if (r.top < 0 || r.left < 0 || r.top > window.innerHeight || r.left > window.innerWidth) continue;
                        return {
                            text: {x: r.left + r.width / 2, y: r.top + r.height / 2},
                            radio: {x: Math.max(1, r.left - 16), y: r.top + r.height / 2}
                        };
                    }
                    return null;
                };

                const body = norm(document.body.innerText || '');
                const hasScheduleText = body.includes('len lich') || body.includes('schedule');

                const byText = textCenter('len lich') || textCenter('schedule');
                if (byText) {
                    push(byText.radio);
                    push(byText.text);
                }
                const nowText = textCenter('bay gio') || textCenter('now');
                if (nowText && byText) {
                    // Click the schedule radio on the same row, derived from the actual label positions.
                    push({x: byText.radio.x, y: byText.radio.y});
                    push({x: byText.text.x - 18, y: byText.text.y});
                    push({x: byText.text.x - 28, y: byText.text.y});
                }

                const allRadios = Array.from(document.querySelectorAll('input[type="radio"]'));
                const radios = allRadios.filter(visible);
                for (const r of radios) {
                    const label = r.closest('label') || r.parentElement;
                    if (label && norm(label.innerText).includes('len lich')) {
                        push(center(r));
                        push(center(label));
                    }
                }

                const candidates = Array.from(document.querySelectorAll('label, [role="radio"], button, span, div')).filter(visible);
                for (const el of candidates) {
                    const t = norm(el.innerText || el.textContent || el.getAttribute('aria-label'));
                    if ((t === 'len lich' || t.includes('len lich') || t === 'schedule') && t.length < 80) {
                        const target = el.closest('label') || el.closest('[role="radio"]') || el;
                        const r = target.getBoundingClientRect();
                        push({x: Math.max(1, r.left + 12), y: r.top + r.height / 2});
                        push(center(target));
                    }
                }

                if (radios.length >= 2) {
                    push(center(radios[1]));
                    push(center(radios[1].closest('label') || radios[1]));
                }
                if (allRadios.length >= 2) {
                    const r = allRadios[1];
                    const label = r.closest('label') || r.parentElement || r;
                    if (visible(label)) push(center(label));
                    let cur = r.parentElement;
                    for (let i = 0; i < 4 && cur; i++, cur = cur.parentElement) {
                        if (visible(cur)) {
                            const box = cur.getBoundingClientRect();
                            if (box.width > 40 && box.width < 600 && box.height > 14 && box.height < 140) {
                                push({x: box.left + 18, y: box.top + box.height / 2});
                                push(center(cur));
                                break;
                            }
                        }
                    }
                }
                if (!points.length && nowText && byText) {
                    push({x: byText.text.x - 18, y: byText.text.y});
                }
                return points;
            })()
        """)
        if False and not radio_points:
            step("❌ Không tìm thấy radio Lên lịch", "red")
            return False

        debug_schedule = await cdp.evaluate("""
            (() => {
                const body = document.body ? document.body.innerText : '';
                return {
                    hasLenLich: body.includes('Lên lịch') || body.includes('Len lich') || body.includes('Schedule'),
                    hasBayGio: body.includes('Bây giờ') || body.includes('Bay gio') || body.includes('Now'),
                    viewport: {w: window.innerWidth, h: window.innerHeight},
                    sample: body.slice(0, 800)
                };
            })()
        """)
        if debug_schedule and not debug_schedule.get("hasLenLich"):
            step(f"Trang chua thay chu Len lich | debug={debug_schedule}", "red")
            return False
        if not radio_points:
            vp = (debug_schedule or {}).get("viewport", {}) if isinstance(debug_schedule, dict) else {}
            vw = int(vp.get("w", 946) or 946)
            vh = int(vp.get("h", 534) or 534)
            radio_points = [
                {"x": round(vw * 0.135), "y": round(vh * 0.515)},
                {"x": round(vw * 0.155), "y": round(vh * 0.515)},
                {"x": round(vw * 0.175), "y": round(vh * 0.515)},
                {"x": round(vw * 0.145), "y": round(vh * 0.555)},
            ]
            step(f"Dung toa do fallback Len lich: {radio_points}", "orange")

        state = None
        for point in radio_points[:8]:
            await click_center(point)
            await asyncio.sleep(0.45)
            await self._allow_schedule_permission_popup(cdp, idx)
            state = await cdp.evaluate(r"""
                (() => {
                    const body = document.body ? document.body.innerText : '';
                    const values = Array.from(document.querySelectorAll('input, button, [role="button"], [aria-haspopup], [class*="date"], [class*="Date"], [class*="time"], [class*="Time"], [class*="pick"], [class*="Pick"], [class*="schedule"], [class*="Schedule"]'))
                        .map(el => ((el.value || el.innerText || el.textContent || el.getAttribute('aria-label') || '') + '').trim())
                        .join('\n');
                    const all = body + '\n' + values;
                    const hasTime = /\b\d{1,2}:\d{2}\b/.test(all);
                    // Detect date in multiple formats: YYYY-MM-DD, MM/DD/YYYY, DD/MM/YYYY, or localized
                    const hasDateISO = /\b\d{4}-\d{2}-\d{2}\b/.test(all);
                    const hasDateUS = /\b\d{1,2}\/\d{1,2}\/\d{4}\b/.test(all);
                    const hasDateLocale = /\b(thg|thang|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)/i.test(all);
                    // Also detect date picker UI elements (calendar icon, date container)
                    const hasDatePicker = document.querySelector('[class*="calendar"], [class*="Calendar"], [class*="datepicker"], [class*="DatePicker"], [class*="date-pick"], [aria-label*="date"], [aria-label*="ngày"], [data-e2e*="date"], [class*="schedule"] [class*="date"], [class*="Schedule"] [class*="Date"]') !== null;
                    const hasDate = hasDateISO || hasDateUS || hasDateLocale || hasDatePicker;
                    return {
                        hasTime,
                        hasDate,
                        hasDateISO, hasDateUS, hasDateLocale, hasDatePicker,
                        nowChecked: Array.from(document.querySelectorAll('input[type="radio"]')).map(x => x.checked)
                    };
                })()
            """)
            if state and state.get("hasTime") and state.get("hasDate"):
                break
        if False and (not state or not state.get("hasTime") or not state.get("hasDate")):
            step(f"❌ Click radio chưa bung dropdown giờ/ngày: {state}", "red")
            return False
        step("✅ Đã bật radio Lên lịch", "green")

        if not (state and state.get("hasTime") and state.get("hasDate")):
            step("Radio chua bat bang chuot, fallback click radio thu 2...", "orange")
            # Fallback 1: Click radio thứ 2 bằng JS + React-compatible events
            await cdp.evaluate(r"""
                (() => {
                    const radios = Array.from(document.querySelectorAll('input[type="radio"]'));
                    const target = radios[1];
                    if (!target) return;
                    const label = target.closest('label') || target.parentElement || target;
                    // Scroll into view
                    label.scrollIntoView({block: 'center'});
                    // Fire native events on label
                    const fireNative = (el, type) => el.dispatchEvent(new MouseEvent(type, {bubbles: true, cancelable: true, view: window}));
                    fireNative(label, 'pointerdown');
                    fireNative(label, 'mousedown');
                    fireNative(label, 'pointerup');
                    fireNative(label, 'mouseup');
                    fireNative(label, 'click');
                    // Fire on radio input itself
                    fireNative(target, 'pointerdown');
                    fireNative(target, 'mousedown');
                    fireNative(target, 'pointerup');
                    fireNative(target, 'mouseup');
                    target.click();
                    // Force checked state and dispatch React-compatible events
                    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'checked');
                    if (nativeSetter && nativeSetter.set) {
                        nativeSetter.set.call(target, true);
                    } else {
                        target.checked = true;
                    }
                    target.dispatchEvent(new Event('input', {bubbles: true}));
                    target.dispatchEvent(new Event('change', {bubbles: true}));
                    // Also try clicking any sibling/parent clickable container (TikTok custom radio)
                    let cur = target.parentElement;
                    for (let i = 0; i < 5 && cur; i++, cur = cur.parentElement) {
                        const role = cur.getAttribute('role');
                        const cls = ((cur.className && String(cur.className)) || '').toLowerCase();
                        if (role === 'radio' || cls.includes('radio') || cls.includes('switch') || cls.includes('toggle')) {
                            fireNative(cur, 'click');
                            break;
                        }
                    }
                })()
            """)
            # Wait longer for React to re-render the schedule panel
            await asyncio.sleep(1.5)
            await self._allow_schedule_permission_popup(cdp, idx)

            # Fallback 2: Also try clicking any element with text "Schedule"/"Lên lịch"
            await cdp.evaluate(r"""
                (() => {
                    const norm = (s) => (s || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').toLowerCase().trim();
                    const candidates = Array.from(document.querySelectorAll('label, [role="radio"], [role="switch"], button, span, div'));
                    for (const el of candidates) {
                        const t = norm(el.innerText || el.textContent || el.getAttribute('aria-label') || '');
                        if ((t === 'len lich' || t === 'schedule') && t.length < 30) {
                            el.click();
                            break;
                        }
                    }
                })()
            """)
            await asyncio.sleep(1.2)
            await self._allow_schedule_permission_popup(cdp, idx)

            # Re-check state with expanded date detection
            state = await cdp.evaluate(r"""
                (() => {
                    const body = document.body ? document.body.innerText : '';
                    const values = Array.from(document.querySelectorAll('input, button, [role="button"], [aria-haspopup], [class*="date"], [class*="Date"], [class*="time"], [class*="Time"], [class*="pick"], [class*="Pick"], [class*="schedule"], [class*="Schedule"]'))
                        .map(el => ((el.value || el.innerText || el.textContent || el.getAttribute('aria-label') || '') + '').trim())
                        .join('\n');
                    const all = body + '\n' + values;
                    const hasTime = /\b\d{1,2}:\d{2}\b/.test(all);
                    const hasDateISO = /\b\d{4}-\d{2}-\d{2}\b/.test(all);
                    const hasDateUS = /\b\d{1,2}\/\d{1,2}\/\d{4}\b/.test(all);
                    const hasDateLocale = /\b(thg|thang|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)/i.test(all);
                    const hasDatePicker = document.querySelector('[class*="calendar"], [class*="Calendar"], [class*="datepicker"], [class*="DatePicker"], [class*="date-pick"], [aria-label*="date"], [aria-label*="ngày"], [data-e2e*="date"], [class*="schedule"] [class*="date"], [class*="Schedule"] [class*="Date"]') !== null;
                    const hasDate = hasDateISO || hasDateUS || hasDateLocale || hasDatePicker;
                    return {
                        hasTime,
                        hasDate,
                        hasDateISO, hasDateUS, hasDateLocale, hasDatePicker,
                        nowChecked: Array.from(document.querySelectorAll('input[type="radio"]')).map(x => x.checked)
                    };
                })()
            """)
            if not state or not (state.get("hasTime") or state.get("hasDate")):
                # Last resort: if radio is checked but UI didn't update, try scrolling up a bit
                step(f"Fallback radio ket qua: {state}, thu scroll lai...", "orange")
                await cdp.evaluate(r"""
                    (() => {
                        const norm = (s) => (s || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').toLowerCase().trim();
                        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                        let node;
                        while ((node = walker.nextNode())) {
                            if (!norm(node.nodeValue).includes('thoi diem dang') && !norm(node.nodeValue).includes('when to post')) continue;
                            const el = node.parentElement;
                            if (el && el.scrollIntoView) el.scrollIntoView({block: 'start'});
                            break;
                        }
                    })()
                """)
                await asyncio.sleep(1.0)
                # One more check
                state = await cdp.evaluate(r"""
                    (() => {
                        const body = document.body ? document.body.innerText : '';
                        const values = Array.from(document.querySelectorAll('input, button, [role="button"], [aria-haspopup], [class*="date"], [class*="Date"], [class*="time"], [class*="Time"], [class*="pick"], [class*="Pick"]'))
                            .map(el => ((el.value || el.innerText || el.textContent || el.getAttribute('aria-label') || '') + '').trim())
                            .join('\n');
                        const all = body + '\n' + values;
                        const hasTime = /\b\d{1,2}:\d{2}\b/.test(all);
                        const hasDateISO = /\b\d{4}-\d{2}-\d{2}\b/.test(all);
                        const hasDateUS = /\b\d{1,2}\/\d{1,2}\/\d{4}\b/.test(all);
                        const hasDateLocale = /\b(thg|thang|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)/i.test(all);
                        const hasDatePicker = document.querySelector('[class*="calendar"], [class*="Calendar"], [class*="datepicker"], [class*="DatePicker"], [class*="date-pick"], [aria-label*="date"], [aria-label*="ngày"], [data-e2e*="date"]') !== null;
                        const hasDate = hasDateISO || hasDateUS || hasDateLocale || hasDatePicker;
                        const radios = Array.from(document.querySelectorAll('input[type="radio"]'));
                        const scheduleRadioChecked = radios.length >= 2 && radios[1].checked;
                        return {
                            hasTime, hasDate,
                            hasDateISO, hasDateUS, hasDateLocale, hasDatePicker,
                            scheduleRadioChecked,
                            nowChecked: radios.map(x => x.checked)
                        };
                    })()
                """)
            if not state or not (state.get("hasTime") or state.get("hasDate")):
                # Final: If radio IS checked, proceed anyway — the time/date dropdowns
                # may render without detectable text (custom React components)
                radio_checked = state and state.get("scheduleRadioChecked")
                if radio_checked:
                    step(f"Radio da check nhung chua phat hien date/time text, van thu tiep: {state}", "orange")
                else:
                    step(f"Fallback radio thu 2 van chua bung dropdown: {state}", "red")
                    return False

        await self._allow_schedule_permission_popup(cdp, idx)
        step("Canh lai trang xuong cuoi de do vi tri o schedule...", "blue")
        await scroll_schedule_to_bottom()

        async def click_dropdown(kind):
            control = await get_schedule_control_state(kind)
            if control:
                return await click_center(control)
            _kj = _json.dumps(kind)
            js = (
                "(() => {"
                "  const kind = " + _kj + ";"
                "  const visible = (el) => {"
                "    const r = el.getBoundingClientRect();"
                "    const st = window.getComputedStyle(el);"
                "    return r.width>0 && r.height>0 && r.right>0 && r.bottom>0"
                "        && r.left<window.innerWidth && r.top<window.innerHeight"
                '        && st.display!==\'none\' && st.visibility!==\'hidden\';'
                "  };"
                "  const center = (el) => { const r=el.getBoundingClientRect(); return {x:r.left+r.width/2,y:r.top+r.height/2}; };"
                "  const cleanText = (el) => {"
                '    const t=((el.innerText||el.value||el.textContent||el.getAttribute(\'aria-label\')||\'\')+\'\').trim();'
                "    return t.replace(/[^\\x00-\\x7E\\u00C0-\\u024F]/g,\' \').replace(/\\s+/g,\' \').trim();"
                "  };"
                "  const hasTime=(t)=>/\\b\\d{1,2}:\\d{2}\\b/.test(t);"
                "  const hasDate=(t)=>/\\b\\d{4}-\\d{2}-\\d{2}\\b/.test(t)||/\\b\\d{1,2}\\/\\d{1,2}\\/\\d{4}\\b/.test(t)||/\\b(thg|thang|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\\b/i.test(t);"
                "  const controls=Array.from(document.querySelectorAll('button,input,[role=\"button\"],[role=\"combobox\"],[aria-haspopup],[aria-expanded],div,span'))"
                "    .filter(visible).map(el=>{const r=el.getBoundingClientRect();return {el,r,t:cleanText(el)};})"
                "    .filter(x=>x.r.width>=30&&x.r.width<=450&&x.r.height>=20&&x.r.height<=80);"
                "  let matches=controls.filter(x=>kind==='time'?hasTime(x.t):hasDate(x.t));"
                "  if(!matches.length){"
                "    const sel=kind==='time'"
                "      ? '[aria-label*=\"time\" i],[aria-label*=\"gio\" i],[class*=\"TimePicker\"]'"
                "      : '[aria-label*=\"date\" i],[aria-label*=\"ngay\" i],[data-e2e*=\"date\"],[class*=\"DatePicker\"]';"  
                "    try{"
                "      const ba=Array.from(document.querySelectorAll(sel)).filter(visible)"
                "        .map(el=>{const r=el.getBoundingClientRect();return {el,r,t:cleanText(el)};})"
                "        .filter(x=>x.r.width>=30&&x.r.width<=450&&x.r.height>=20&&x.r.height<=80);"
                "      if(ba.length)matches=ba;"
                "    }catch(e){}"
                "  }"
                "  if(!matches.length){"
                "    const drps=controls.filter(x=>{const st=window.getComputedStyle(x.el);"
                "      return st.cursor==='pointer'||x.el.getAttribute('aria-haspopup')||x.el.getAttribute('role')==='combobox'||x.el.getAttribute('role')==='button';"
                "    }).sort((a,b)=>b.r.top-a.r.top);"
                "    if(drps.length>=2){"
                "      const b2=drps.slice(0,2).sort((a,b)=>a.r.left-b.r.left);"
                "      const pick=kind==='time'?b2[0]:b2[b2.length-1];"
                "      if(pick)matches=[pick];"
                "    }"
                "  }"
                "  if(!matches.length)return null;"
                "  matches.sort((a,b)=>a.r.top-b.r.top||(kind==='time'?a.r.left-b.r.left:b.r.left-a.r.left));"
                "  return center(matches[0].el);"
                "})()"
            )
            pos = await cdp.evaluate(js)
            return await click_center(pos)

        async def time_popup_open():
            return bool(await cdp.evaluate("""
                (() => {
                    const visible = (el) => {
                        const r = el.getBoundingClientRect();
                        const st = window.getComputedStyle(el);
                        return r.width > 0 && r.height > 0 &&
                            r.right > 0 && r.bottom > 0 && r.left < window.innerWidth && r.top < window.innerHeight &&
                            st.display !== 'none' && st.visibility !== 'hidden';
                    };
                    const nums = Array.from(document.querySelectorAll('[role="option"], li, button, div, span'))
                        .filter(visible)
                        .map(el => {
                            const r = el.getBoundingClientRect();
                            const t = ((el.innerText || el.value || el.textContent || el.getAttribute('aria-label') || '') + '').trim();
                            return {r, t, cx: r.left + r.width / 2};
                        })
                        .filter(x => /^\\d{1,2}$/.test(x.t) && x.r.width >= 18 && x.r.width <= 120 && x.r.height >= 16 && x.r.height <= 70);
                    if (nums.length < 6) return false;
                    const xs = nums.map(x => x.cx);
                    return Math.max(...xs) - Math.min(...xs) > 18;
                })()
            """))

        async def nudge_mouse_in_time_popup():
            pos = await cdp.evaluate("""
                (() => {
                    const visible = (el) => {
                        const r = el.getBoundingClientRect();
                        const st = window.getComputedStyle(el);
                        return r.width > 0 && r.height > 0 &&
                            r.right > 0 && r.bottom > 0 && r.left < window.innerWidth && r.top < window.innerHeight &&
                            st.display !== 'none' && st.visibility !== 'hidden';
                    };
                    const nums = Array.from(document.querySelectorAll('[role="option"], li, button, div, span'))
                        .filter(visible)
                        .map(el => {
                            const r = el.getBoundingClientRect();
                            const t = ((el.innerText || el.value || el.textContent || el.getAttribute('aria-label') || '') + '').trim();
                            return {r, t, cx: r.left + r.width / 2, cy: r.top + r.height / 2};
                        })
                        .filter(x => /^\\d{1,2}$/.test(x.t) && x.r.width >= 18 && x.r.width <= 120 && x.r.height >= 16 && x.r.height <= 70);
                    if (!nums.length) return null;
                    const xs = nums.map(x => x.cx).sort((a, b) => a - b);
                    const ys = nums.map(x => x.cy).sort((a, b) => a - b);
                    const x = xs[Math.floor(xs.length * 0.75)] || xs[xs.length - 1];
                    const y = ys[Math.floor(ys.length / 2)] || ys[0];
                    return {x: Math.min(window.innerWidth - 4, x + 6), y: Math.min(window.innerHeight - 4, y + 4)};
                })()
            """)
            if not pos:
                return
            x = int(pos["x"])
            y = int(pos["y"])
            await self._show_visible_cursor(cdp, x, y, idx)
            try:
                await cdp.mouse_move(x, y)
            except Exception:
                pass
            await asyncio.sleep(0.25)

        async def choose_time_part(value, alt_value, part):
            aliases = []
            for item in (value, alt_value):
                if item and item not in aliases:
                    aliases.append(item)

            for attempt in range(18):
                state = await cdp.evaluate(f"""
                    (() => {{
                        const aliases = {_json.dumps(aliases)};
                        const part = {_json.dumps(part)};
                        const visible = (el) => {{
                            const r = el.getBoundingClientRect();
                            const st = window.getComputedStyle(el);
                            return r.width > 0 && r.height > 0 &&
                                r.right > 0 && r.bottom > 0 && r.left < window.innerWidth && r.top < window.innerHeight &&
                                st.display !== 'none' && st.visibility !== 'hidden';
                        }};
                        const toItem = (el) => {{
                            const r = el.getBoundingClientRect();
                            const t = ((el.innerText || el.value || el.textContent || el.getAttribute('aria-label') || '') + '').trim();
                            return {{el, r, t, cx: r.left + r.width / 2, cy: r.top + r.height / 2, n: parseInt(t, 10)}};
                        }};
                        const numericItemsIn = (root) => Array.from(root.querySelectorAll('[role="option"], li, button, div, span'))
                            .filter(visible)
                            .map(toItem)
                            .filter(x => /^\\d{{1,2}}$/.test(x.t) && x.r.width >= 18 && x.r.width <= 120 && x.r.height >= 16 && x.r.height <= 70);
                        const popup = Array.from(document.querySelectorAll('[role="listbox"], [role="menu"], [class*="pop"], [class*="Pop"], [class*="select"], [class*="Select"], [class*="dropdown"], [class*="Dropdown"], div'))
                            .filter(visible)
                            .map(el => {{
                                const r = el.getBoundingClientRect();
                                const nums = numericItemsIn(el);
                                const xs = nums.map(x => x.cx);
                                const twoColumns = nums.length >= 6 && xs.length && (Math.max(...xs) - Math.min(...xs) > 24);
                                return {{el, r, nums, twoColumns, score: nums.length}};
                            }})
                            .filter(x => x.twoColumns && x.r.width >= 110 && x.r.width <= 260 && x.r.height >= 140 && x.r.height <= 320)
                            .sort((a, b) => (a.r.width * a.r.height) - (b.r.width * b.r.height) || b.score - a.score)[0];
                        if (!popup) return {{found: false, missingPopup: true}};
                        const items = popup.nums;
                        const box = {{left: popup.r.left, right: popup.r.right, top: popup.r.top, bottom: popup.r.bottom}};

                        if (!items.length) return {{found: false, missingItems: true, box}};
                        const xs = items.map(x => x.cx);
                        const minX = Math.min(...xs);
                        const maxX = Math.max(...xs);
                        const threshold = (minX + maxX) / 2;
                        const hasTwoColumns = (maxX - minX) > 18;

                        let columnItems = hasTwoColumns
                            ? items.filter(x => part === 'hour' ? x.cx <= threshold : x.cx >= threshold)
                            : items.slice();
                        if (!columnItems.length) columnItems = items.slice();

                        let matches = columnItems.filter(x => aliases.includes(x.t));
                        if (!matches.length) matches = items.filter(x => aliases.includes(x.t));
                        if (matches.length) {{
                            matches.sort((a, b) => Math.abs(a.r.width * a.r.height - 900) - Math.abs(b.r.width * b.r.height - 900) || a.r.top - b.r.top);
                            const best = matches[0];
                            return {{found: true, x: best.cx, y: best.cy, box}};
                        }}

                        const wanted = parseInt(aliases[0], 10);
                        const nums = columnItems.map(x => x.n).filter(n => Number.isFinite(n));
                        const minValue = nums.length ? Math.min(...nums) : 0;
                        const maxValue = nums.length ? Math.max(...nums) : 59;
                        const left = Math.min(...columnItems.map(x => x.r.left));
                        const right = Math.max(...columnItems.map(x => x.r.right));
                        const top = Math.min(...columnItems.map(x => x.r.top));
                        const bottom = Math.max(...columnItems.map(x => x.r.bottom));
                        let delta = wanted < minValue ? -320 : 320;
                        const safeX = Math.min(Math.max((left + right) / 2, box.left + 8), box.right - 8);
                        const safeY = Math.min(Math.max(top + (bottom - top) * 0.72, box.top + 8), box.bottom - 8);
                        return {{found: false, x: safeX, y: safeY, delta, box}};
                    }})()
                """)
                if state and state.get("found"):
                    box = state.get("box") or {}
                    sx = float(state.get("x") or -1)
                    sy = float(state.get("y") or -1)
                    if not (
                        box.get("left", -1) - 2 <= sx <= box.get("right", -1) + 2
                        and box.get("top", -1) - 2 <= sy <= box.get("bottom", -1) + 2
                    ):
                        return False
                    await click_center(state)
                    await asyncio.sleep(0.35)
                    return True

                if state:
                    if state.get("missingPopup") or state.get("missingItems"):
                        return False
                    delta = int(state.get("delta") or 320)
                    if part == "minute":
                        if attempt < 7:
                            delta = 260
                        elif attempt < 14:
                            delta = -260
                        else:
                            delta = 520 if attempt % 2 == 0 else -520
                    elif attempt >= 12:
                        delta = -delta
                    await cdp.scroll(int(state.get("x") or 400), int(state.get("y") or 360), 0, delta)
                else:
                    return False
                await asyncio.sleep(0.22)
            return False

        async def choose_calendar_day(value, alt_value):
            aliases = []
            for item in (value, alt_value):
                if item and item not in aliases:
                    aliases.append(item)

            for _ in range(5):
                pos = await cdp.evaluate(f"""
                    (() => {{
                        const aliases = {_json.dumps(aliases)};
                        const bias = {_json.dumps(day_pick_bias)};
                        const target = {_json.dumps(target_schedule)};
                        const visible = (el) => {{
                            const r = el.getBoundingClientRect();
                            const st = window.getComputedStyle(el);
                            return r.width > 0 && r.height > 0 && st.display !== 'none' && st.visibility !== 'hidden';
                        }};
                        const inBox = (r, box) => !box || (r.left >= box.left - 4 && r.right <= box.right + 4 && r.top >= box.top - 4 && r.bottom <= box.bottom + 4);
                        const parseDateText = (text) => {{
                            const raw = (text || '').toString();
                            const lower = raw.normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').toLowerCase();
                            let m = lower.match(/\\b(\\d{{4}})\\s*-\\s*(\\d{{1,2}})\\s*-\\s*(\\d{{1,2}})\\b/);
                            if (m) return {{year: Number(m[1]), month: Number(m[2]), day: Number(m[3])}};
                            m = lower.match(/\\b(\\d{{1,2}})\\s*\\/\\s*(\\d{{1,2}})\\s*\\/\\s*(\\d{{4}})\\b/);
                            if (m) return {{year: Number(m[3]), month: Number(m[1]), day: Number(m[2])}};
                            m = lower.match(/\\b(\\d{{1,2}})\\s*thang\\s*(\\d{{1,2}})\\D+(\\d{{4}})\\b/);
                            if (m) return {{year: Number(m[3]), month: Number(m[2]), day: Number(m[1])}};
                            const months = {{
                                jan: 1, january: 1, feb: 2, february: 2, mar: 3, march: 3,
                                apr: 4, april: 4, may: 5, jun: 6, june: 6, jul: 7, july: 7,
                                aug: 8, august: 8, sep: 9, sept: 9, september: 9,
                                oct: 10, october: 10, nov: 11, november: 11, dec: 12, december: 12
                            }};
                            m = lower.match(/\\b([a-z]+)\\s+(\\d{{1,2}}),?\\s+(\\d{{4}})\\b/);
                            if (m && months[m[1]]) return {{year: Number(m[3]), month: months[m[1]], day: Number(m[2])}};
                            m = lower.match(/\\b(\\d{{1,2}})\\s+([a-z]+),?\\s+(\\d{{4}})\\b/);
                            if (m && months[m[2]]) return {{year: Number(m[3]), month: months[m[2]], day: Number(m[1])}};
                            return null;
                        }};
                        const popup = Array.from(document.querySelectorAll('[role="dialog"], [role="grid"], [class*="calendar"], [class*="Calendar"], [class*="picker"], [class*="Picker"], [class*="pop"], [class*="Pop"], div'))
                            .filter(visible)
                            .map(el => {{
                                const r = el.getBoundingClientRect();
                                const txt = (el.innerText || '').trim();
                                const score = (txt.match(/\\b\\d{{1,2}}\\b/g) || []).length;
                                return {{el, r, score, area: r.width * r.height}};
                            }})
                            .filter(x => x.r.width >= 180 && x.r.height >= 150 && x.score >= 12)
                            .sort((a, b) => b.score - a.score || a.area - b.area)[0];
                        const box = popup ? popup.r : null;

                        const cells = Array.from(document.querySelectorAll('button, [role="gridcell"], td, div, span'))
                            .filter(visible)
                            .map(el => {{
                                const r = el.getBoundingClientRect();
                                const st = window.getComputedStyle(el);
                                const label = [
                                    el.getAttribute('aria-label') || '',
                                    el.getAttribute('title') || '',
                                    el.getAttribute('data-date') || '',
                                    el.getAttribute('datetime') || '',
                                    el.value || '',
                                    el.innerText || '',
                                    el.textContent || ''
                                ].filter(Boolean).join(' ');
                                const t = ((el.innerText || el.value || el.textContent || el.getAttribute('aria-label') || '') + '').trim();
                                const cls = ((el.className && String(el.className)) || '').toLowerCase();
                                const colorNums = (st.color.match(/\\d+(?:\\.\\d+)?/g) || []).map(Number);
                                const lightText = colorNums.length >= 3 && colorNums[0] > 150 && colorNums[1] > 150 && colorNums[2] > 150;
                                const disabled = el.disabled || el.getAttribute('aria-disabled') === 'true' || !!el.closest('[aria-disabled="true"], [disabled]');
                                const muted = disabled || /disabled|outside|other|prev|next|muted/.test(cls) || parseFloat(st.opacity || '1') < 0.65 || lightText;
                                const tagScore = (el.tagName === 'BUTTON' || el.getAttribute('role') === 'gridcell' || el.tagName === 'TD') ? 0 : 1;
                                const parsed = parseDateText(label);
                                const exact = parsed && parsed.year === target.year && parsed.month === target.month && parsed.day === target.day;
                                return {{el, r, t, muted, disabled, tagScore, area: r.width * r.height, exact}};
                            }})
                            .filter(x => (x.exact || aliases.includes(x.t)) && x.r.width >= 16 && x.r.height >= 16 && x.r.width <= 110 && x.r.height <= 90 && inBox(x.r, box));
                        if (!cells.length) return null;
                        const exactCells = cells.filter(x => x.exact);
                        if (exactCells.length) cells.splice(0, cells.length, ...exactCells);
                        const rowOrder = bias === 'last'
                            ? ((a, b) => (b.r.top - a.r.top) || (b.r.left - a.r.left))
                            : ((a, b) => (a.r.top - b.r.top) || (a.r.left - b.r.left));
                        cells.sort((a, b) => (b.exact - a.exact) || (a.disabled - b.disabled) || rowOrder(a, b) || (a.muted - b.muted) || a.tagScore - b.tagScore || Math.abs(a.area - 900) - Math.abs(b.area - 900));
                        const best = cells[0];
                        return {{x: best.r.left + best.r.width / 2, y: best.r.top + best.r.height / 2}};
                    }})()
                """)
                if pos:
                    await click_center(pos)
                    await asyncio.sleep(0.45)
                    return True
                await asyncio.sleep(0.25)
            return False

        async def choose_dropdown_value(value, alt_value=None):
            for attempt in range(14):
                pos = await cdp.evaluate(f"""
                    (() => {{
                        const wanted = {_json.dumps(value)};
                        const alt = {_json.dumps(alt_value or value)};
                        const visible = (el) => {{
                            const r = el.getBoundingClientRect();
                            const st = window.getComputedStyle(el);
                            return r.width > 0 && r.height > 0 && st.display !== 'none' && st.visibility !== 'hidden';
                        }};
                        const center = (el) => {{
                            const r = el.getBoundingClientRect();
                            return {{x: r.left + r.width / 2, y: r.top + r.height / 2}};
                        }};
                        const items = Array.from(document.querySelectorAll('[role="option"], li, button, div, span')).filter(visible);
                        for (const el of items) {{
                            const t = ((el.innerText || el.value || el.textContent || el.getAttribute('aria-label') || '') + '').trim();
                            if (t === wanted || t === alt || t.includes(wanted) || t.includes(alt)) {{
                                const r = el.getBoundingClientRect();
                                if (r.width < 20 || r.height < 12 || r.width > 500 || r.height > 120) continue;
                                return center(el);
                            }}
                        }}
                        return null;
                    }})()
                """)
                if pos:
                    await click_center(pos)
                    await asyncio.sleep(0.5)
                    return True
                scroll_pos = await cdp.evaluate("""
                    (() => {
                        const visible = (el) => {
                            const r = el.getBoundingClientRect();
                            const st = window.getComputedStyle(el);
                            return r.width > 0 && r.height > 0 && st.display !== 'none' && st.visibility !== 'hidden';
                        };
                        const candidates = Array.from(document.querySelectorAll('[role="listbox"], [role="menu"], [class*="pop"], [class*="Pop"], [class*="select"], [class*="Select"], [class*="dropdown"], [class*="Dropdown"], div'))
                            .filter(visible)
                            .map(el => {
                                const r = el.getBoundingClientRect();
                                const txt = (el.innerText || '').trim();
                                return {r, txt, score: (txt.match(/\\d{1,2}:\\d{2}|\\d{4}-\\d{2}-\\d{2}/g) || []).length};
                            })
                            .filter(x => x.r.width >= 80 && x.r.height >= 60 && x.score >= 2)
                            .sort((a, b) => b.score - a.score || b.r.height - a.r.height);
                        const target = candidates[0];
                        if (target) return {x: target.r.left + target.r.width / 2, y: target.r.top + Math.min(target.r.height - 10, target.r.height * 0.75)};
                        return {x: Math.round(window.innerWidth * 0.35), y: Math.round(window.innerHeight * 0.55)};
                    })()
                """)
                if attempt < 11:
                    await cdp.scroll(int(scroll_pos["x"]), int(scroll_pos["y"]), 0, 360)
                else:
                    await cdp.scroll(int(scroll_pos["x"]), int(scroll_pos["y"]), 0, -360)
                await asyncio.sleep(0.25)
            return False

        async def type_active_value(value):
            await cdp.send("Input.dispatchKeyEvent", {
                "type": "keyDown", "key": "a", "code": "KeyA",
                "windowsVirtualKeyCode": 65, "modifiers": 2
            })
            await cdp.send("Input.dispatchKeyEvent", {
                "type": "keyUp", "key": "a", "code": "KeyA",
                "windowsVirtualKeyCode": 65, "modifiers": 2
            })
            await asyncio.sleep(0.1)
            await cdp.type_text(value, delay=20)
            await cdp.press_key("Enter")
            await asyncio.sleep(0.5)

        async def read_selected_time():
            return await get_schedule_control_state("time")

        async def get_time_wheel_point(part):
            return await cdp.evaluate(f"""
                (() => {{
                    const part = {_json.dumps(part)};
                    const visible = (el) => {{
                        const r = el.getBoundingClientRect();
                        const st = window.getComputedStyle(el);
                        return r.width > 0 && r.height > 0 &&
                            r.right > 0 && r.bottom > 0 && r.left < window.innerWidth && r.top < window.innerHeight &&
                            st.display !== 'none' && st.visibility !== 'hidden';
                    }};
                    const toItem = (el) => {{
                        const r = el.getBoundingClientRect();
                        const t = ((el.innerText || el.value || el.textContent || el.getAttribute('aria-label') || '') + '').trim();
                        return {{r, t, cx: r.left + r.width / 2, cy: r.top + r.height / 2}};
                    }};
                    const numsIn = (root) => Array.from(root.querySelectorAll('[role="option"], li, button, div, span'))
                        .filter(visible)
                        .map(toItem)
                        .filter(x => /^\\d{{1,2}}$/.test(x.t) && x.r.width >= 18 && x.r.width <= 120 && x.r.height >= 16 && x.r.height <= 70);
                    const popup = Array.from(document.querySelectorAll('[role="listbox"], [role="menu"], [class*="pop"], [class*="Pop"], [class*="select"], [class*="Select"], [class*="dropdown"], [class*="Dropdown"], div'))
                        .filter(visible)
                        .map(el => {{
                            const r = el.getBoundingClientRect();
                            const nums = numsIn(el);
                            const xs = nums.map(x => x.cx);
                            const twoColumns = nums.length >= 6 && xs.length && (Math.max(...xs) - Math.min(...xs) > 24);
                            return {{r, nums, score: nums.length, twoColumns}};
                        }})
                        .filter(x => x.twoColumns && x.r.width >= 80 && x.r.width <= 320 && x.r.height >= 100 && x.r.height <= 420)
                        .sort((a, b) => b.score - a.score || (a.r.width * a.r.height) - (b.r.width * b.r.height))[0];
                    if (!popup) return null;
                    const xs = popup.nums.map(x => x.cx);
                    const minX = Math.min(...xs);
                    const maxX = Math.max(...xs);
                    const threshold = (minX + maxX) / 2;
                    const col = popup.nums.filter(x => part === 'hour' ? x.cx <= threshold : x.cx >= threshold);
                    const source = col.length ? col : popup.nums;
                    const left = Math.min(...source.map(x => x.r.left));
                    const right = Math.max(...source.map(x => x.r.right));
                    const top = Math.min(...source.map(x => x.r.top));
                    const bottom = Math.max(...source.map(x => x.r.bottom));
                    const ys = Array.from(new Set(source.map(x => Math.round(x.cy)).sort((a, b) => a - b)));
                    const gaps = [];
                    for (let i = 1; i < ys.length; i++) {{
                        const gap = ys[i] - ys[i - 1];
                        if (gap >= 14 && gap <= 80) gaps.push(gap);
                    }}
                    gaps.sort((a, b) => a - b);
                    const step = gaps.length ? gaps[Math.floor(gaps.length / 2)] : 32;
                    return {{
                        x: Math.round((left + right) / 2),
                        y: Math.round((popup.r.top + popup.r.bottom) / 2),
                        step: Math.max(24, Math.min(80, Math.round(step))),
                    }};
                }})()
            """)

        async def wheel_time_part_to_target(part):
            target_value = target_schedule["hour"] if part == "hour" else target_schedule["minute"]
            unit = 1 if part == "hour" else 5
            current = await read_selected_time()
            current_value = current.get(part) if current else None
            if current_value is None:
                step("Khong doc duoc thoi gian hien tai tren o hen gio", "red")
                return False
            if current_value == target_value:
                return True

            point = await get_time_wheel_point(part)
            if not point:
                return False

            base_step = int(point.get("step") or 32)
            base_step = max(24, min(96, base_step))
            wheel_sign = 1
            stagnant = 0
            max_loops = 40 if part == "hour" else 24

            for _ in range(max_loops):
                if current_value == target_value:
                    return True

                diff_steps = (target_value - current_value) // unit
                if diff_steps == 0:
                    return True

                point = await get_time_wheel_point(part)
                if not point:
                    return False

                direction = 1 if diff_steps > 0 else -1
                delta = int(base_step * direction * wheel_sign)
                await cdp.scroll(int(point["x"]), int(point["y"]), 0, delta)
                await asyncio.sleep(0.18)

                updated = await read_selected_time()
                new_value = updated.get(part) if updated else None

                if new_value is None or new_value == current_value:
                    await cdp.scroll(int(point["x"]), int(point["y"]), 0, int(delta * 1.5))
                    await asyncio.sleep(0.2)
                    updated = await read_selected_time()
                    new_value = updated.get(part) if updated else None

                if new_value is None or new_value == current_value:
                    stagnant += 1
                    if stagnant >= 2:
                        await click_center(point)
                        await asyncio.sleep(0.2)
                    if stagnant >= 4:
                        return False
                    continue

                stagnant = 0
                observed_steps = (new_value - current_value) // unit
                if observed_steps and observed_steps * direction < 0:
                    wheel_sign *= -1
                current_value = new_value

            return current_value == target_value

        async def select_full_time_once():
            step(f"Chon gio {hour_text}...", "blue")
            await scroll_schedule_to_bottom()
            if not await click_dropdown("time"):
                step("Khong mo duoc dropdown gio", "red")
                return False
            await asyncio.sleep(0.45)
            if not await time_popup_open():
                await click_dropdown("time")
                await asyncio.sleep(0.35)
            if not await wheel_time_part_to_target("hour"):
                step("Khong chon duoc cot gio", "red")
                return False
            await nudge_mouse_in_time_popup()

            step(f"Chon phut {minute_text}...", "blue")
            if not await time_popup_open():
                if not await click_dropdown("time"):
                    step("Khong mo lai duoc dropdown gio de chon phut", "red")
                    return False
                await asyncio.sleep(0.35)
            if not await wheel_time_part_to_target("minute"):
                step("Khong chon duoc cot phut", "red")
                return False
            await asyncio.sleep(0.45)
            selected = await read_selected_time()
            return bool(selected and selected.get("hour") == target_schedule["hour"] and selected.get("minute") == target_schedule["minute"])

        time_ok = False
        for attempt in range(2):
            time_ok = await select_full_time_once()
            if time_ok:
                break
            actual = await read_selected_time()
            step(f"Gio sau khi chon chua khop, thu lai lan {attempt + 2}: actual={actual}", "orange")
            try:
                await cdp.press_key("Escape")
                await asyncio.sleep(0.25)
            except Exception:
                pass
        if not time_ok:
            step("Khong dat duoc gio/phut dung voi du lieu", "red")
            return False
        step("Da xu ly gio/phut", "green")

        step(f"Chon ngay {date_text}...", "blue")
        await scroll_schedule_to_bottom()
        if not await click_dropdown("date"):
            step("Khong mo duoc dropdown ngay", "red")
            return False
        await asyncio.sleep(0.45)
        if not await choose_calendar_day(day_text, day_alt):
            step("Khong chon duoc ngay trong lich", "red")
            return False
        step("Da xu ly ngay", "green")

        try:
            await cdp.press_key("Escape")
            await asyncio.sleep(0.25)
        except Exception:
            pass

        verify = await cdp.evaluate(f"""
            (() => {{
                const target = {_json.dumps(target_schedule)};
                const visible = (el) => {{
                    const r = el.getBoundingClientRect();
                    const st = window.getComputedStyle(el);
                    return r.width > 0 && r.height > 0 && st.display !== 'none' && st.visibility !== 'hidden';
                }};
                const textOf = (el) => ((el.value || el.innerText || el.textContent || el.getAttribute('aria-label') || '') + '').trim();
                const controls = Array.from(document.querySelectorAll('input, button, [role="button"], [aria-haspopup], [aria-label]'))
                    .filter(visible)
                    .map(el => {{
                        const r = el.getBoundingClientRect();
                        return {{text: textOf(el), x: r.left, y: r.top, w: r.width, h: r.height}};
                    }})
                    .filter(x => x.text && x.w >= 35 && x.w <= 320 && x.h >= 18 && x.h <= 90);

                const parseTime = (text) => {{
                    const m = (text || '').match(/\\b(\\d{{1,2}})\\s*:\\s*(\\d{{2}})\\b/);
                    if (!m) return null;
                    return {{hour: Number(m[1]), minute: Number(m[2]), text}};
                }};
                const parseDate = (text) => {{
                    let m = (text || '').match(/\\b(\\d{{4}})\\s*-\\s*(\\d{{1,2}})\\s*-\\s*(\\d{{1,2}})\\b/);
                    if (m) return {{year: Number(m[1]), month: Number(m[2]), day: Number(m[3]), text}};
                    m = (text || '').match(/\\b(\\d{{1,2}})\\s*\\/\\s*(\\d{{1,2}})\\s*\\/\\s*(\\d{{4}})\\b/);
                    if (m) return {{year: Number(m[3]), month: Number(m[1]), day: Number(m[2]), text}};
                    return null;
                }};

                const timeCandidates = controls.map(x => parseTime(x.text)).filter(Boolean);
                const dateCandidates = controls.map(x => parseDate(x.text)).filter(Boolean);
                const timeMatch = timeCandidates.some(x => x.hour === target.hour && x.minute === target.minute);
                const dateMatch = dateCandidates.some(x => x.year === target.year && x.month === target.month && x.day === target.day);

                const body = document.body ? document.body.innerText : '';
                const all = body + '\\n' + controls.map(x => x.text).join('\\n');
                const exactDateFallback =
                    all.includes('{date_text}') ||
                    all.includes('{date_us}') ||
                    all.includes('{dt.year}-{dt.month}-{dt.day}') ||
                    all.includes('{dt.month}/{dt.day}/{dt.year}');
                const exactTimeFallback =
                    all.includes('{time_text}') ||
                    all.includes('{dt.hour}:{dt.minute:02d}');

                return {{
                    ok: (timeMatch || exactTimeFallback) && (dateMatch || exactDateFallback),
                    timeMatch,
                    dateMatch,
                    timeCandidates: timeCandidates.map(x => x.text).slice(0, 8),
                    dateCandidates: dateCandidates.map(x => x.text).slice(0, 8),
                    expected: target,
                }};
            }})()
        """)
        if not verify or not verify.get("ok"):
            actual_times = ",".join((verify or {}).get("timeCandidates", [])[:4])
            actual_dates = ",".join((verify or {}).get("dateCandidates", [])[:4])
            step(f"❌ Verify gio/ngay khong khop | expected={time_text} {date_text} | actual_time={actual_times} | actual_date={actual_dates}", "red")
            return False
        step("✅ Đặt giờ/ngày lên lịch OK", "green")
        return True

    async def _click_post_button(self, cdp, is_schedule=False, idx=None):
        """Click nút Post hoặc Schedule."""
        target_texts = ["schedule", "lên lịch"] if is_schedule else ["post", "đăng", "đăng bài", "đăng video"]

        if is_schedule:
            target_texts.extend(["schedule", "lên lịch"])
        else:
            target_texts.extend(["post", "đăng", "đăng bài", "đăng video"])

        for attempt in range(5):
            await self._dismiss_popups(cdp, idx, press_escape=(attempt == 0))
            clicked = await cdp.evaluate(f"""
                (() => {{
                    const visible = (el) => {{
                        if (!el) return false;
                        const style = getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style.display !== 'none' && style.visibility !== 'hidden' &&
                               rect.width > 0 && rect.height > 0;
                    }};
                    const fire = (el) => {{
                        el.scrollIntoView({{behavior: 'smooth', block: 'center'}});
                        el.dispatchEvent(new MouseEvent('mouseover', {{bubbles: true}}));
                        el.dispatchEvent(new MouseEvent('mousedown', {{bubbles: true}}));
                        el.dispatchEvent(new MouseEvent('mouseup', {{bubbles: true}}));
                        el.dispatchEvent(new MouseEvent('click', {{bubbles: true}}));
                    }};
                    const targets = {_json.dumps(target_texts)};
                    const btns = document.querySelectorAll('button');
                    for (const btn of btns) {{
                        const t = btn.innerText.toLowerCase().trim();
                        if (visible(btn) && targets.some(x => t === x || t.includes(x)) && !btn.disabled) {{
                            fire(btn);
                            return true;
                        }}
                    }}
                    return false;
                }})()
            """)
            if clicked:
                await asyncio.sleep(1)
                await self._dismiss_popups(cdp, idx, press_escape=False)
                return True
            await asyncio.sleep(2)

        return False

    async def _verify_post_success(self, cdp, idx=None):
        """Kiểm tra đăng bài thành công."""
        for _ in range(10):
            await self._dismiss_popups(cdp, idx, press_escape=False)
            url = await cdp.get_url()
            if "/content" in url or "/manage" in url:
                return True

            body_check = await cdp.evaluate("""
                (() => {
                    const body = document.body ? document.body.innerText : '';
                    if (body.includes('đã được đăng') || body.includes('successfully') ||
                        body.includes('đã được lên lịch') || body.includes('scheduled') ||
                        body.includes('Quản lý bài đăng') || body.includes('Manage your posts')) return true;
                    return false;
                })()
            """)
            if body_check:
                return True
            await asyncio.sleep(1.5)
        return False

    async def _try_save_draft(self, cdp, idx):
        """Thử save draft khi post thất bại."""
        self.status_updated.emit(idx, "💾 Thử lưu Draft...", "orange")
        await self._dismiss_popups(cdp, idx)
        saved = await cdp.evaluate("""
            (() => {
                const visible = (el) => {
                    if (!el) return false;
                    const style = getComputedStyle(el);
                    const rect = el.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden' &&
                           rect.width > 0 && rect.height > 0;
                };
                const fire = (el) => {
                    el.scrollIntoView({behavior: 'smooth', block: 'center'});
                    el.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
                    el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
                    el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                    el.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                };
                const btns = document.querySelectorAll('button');
                for (const btn of btns) {
                    const t = btn.innerText.toLowerCase().trim();
                    if (visible(btn) && (t.includes('draft') || t.includes('save draft') || t.includes('lưu nháp') || t.includes('bản nháp'))) {
                        fire(btn);
                        return true;
                    }
                }
                return false;
            })()
        """)
        if saved:
            self.status_updated.emit(idx, "💾 Đã lưu Draft", "orange")
            await asyncio.sleep(1)
        return bool(saved)
