"""
Worker GoLogin Offline.
Flow: Attach vào trình duyệt GoLogin đang mở (nếu có) → hoặc mở mới.
      Nhốt vào dashboard (tìm bằng PID) → rồi mới vào TikTok.
"""
import os
import time
import shutil
import random
import asyncio
import subprocess
import ctypes
import socket
import urllib.request
import json as _json
import win32gui
import win32con
from PyQt5.QtCore import QThread, pyqtSignal
from app_paths import gologin_base_dir, gologin_profile_dir, require_orbita_browser_exe, resource_path

GOLOGIN_BASE_DIR = str(gologin_base_dir())
ZERO_PROFILE_ZIP = str(resource_path("gologin_zeroprofile.zip"))

# Kích thước cửa sổ trình duyệt (viewport theo DevTools)
BROWSER_WIDTH = 960
BROWSER_HEIGHT = 680


def _nhot_hwnd(hwnd, widget_id, width, height):
    """Lột TOÀN BỘ viền/title bar Chrome, ép vào khuôn QWidget chính xác từng pixel."""
    try:
        # === BƯỚC 1: Tước sạch Style viền ===
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
        style = style & ~win32con.WS_CAPTION        # Bỏ thanh tiêu đề
        style = style & ~win32con.WS_THICKFRAME      # Bỏ viền kéo giãn
        style = style & ~win32con.WS_BORDER           # Bỏ viền mỏng
        style = style & ~win32con.WS_MINIMIZEBOX      # Bỏ nút minimize
        style = style & ~win32con.WS_MAXIMIZEBOX      # Bỏ nút maximize
        style = style & ~win32con.WS_SYSMENU           # Bỏ menu hệ thống
        style = style | win32con.WS_CHILD              # Ép thành child window
        win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style)

        # === BƯỚC 2: Tước sạch Extended Style (bóng đổ, viền ngoài) ===
        ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        ex_style = ex_style & ~win32con.WS_EX_DLGMODALFRAME   # Bỏ viền dialog
        ex_style = ex_style & ~win32con.WS_EX_WINDOWEDGE       # Bỏ viền cạnh
        ex_style = ex_style & ~win32con.WS_EX_CLIENTEDGE       # Bỏ viền 3D chìm
        ex_style = ex_style & ~win32con.WS_EX_STATICEDGE       # Bỏ viền tĩnh
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex_style)

        # === BƯỚC 3: Đặt parent (nhúng vào QWidget container) ===
        win32gui.SetParent(hwnd, widget_id)

        # === BƯỚC 4: Ép kích thước + buộc Windows tính lại frame ===
        # SWP_FRAMECHANGED buộc Windows áp dụng style mới ngay lập tức
        SWP_FRAMECHANGED = 0x0020
        win32gui.SetWindowPos(
            hwnd, win32con.HWND_TOP,
            0, 0, width, height,
            win32con.SWP_SHOWWINDOW | SWP_FRAMECHANGED
        )
        return hwnd
    except Exception as e:
        print(f"[X] Lỗi nhốt: {e}")
        return 0


def tim_cua_so_theo_pid(pid, widget_id, width, height, target_port=None):
    """Tìm cửa sổ Chrome theo PID → nhốt vào ô đen với kích thước chính xác."""
    user32 = ctypes.windll.user32

    def enum_cb(hwnd, results):
        if win32gui.IsWindowVisible(hwnd):
            if win32gui.GetClassName(hwnd) == "Chrome_WidgetWin_1":
                process_id = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
                results.append((hwnd, process_id.value))

    # Tìm trong 5 giây (nhanh hơn: poll mỗi 50ms)
    for _ in range(100):
        hwnds = []
        win32gui.EnumWindows(enum_cb, hwnds)
        for hwnd, wpid in hwnds:
            if wpid == pid:
                print(f"[V] Tìm thấy HWND: {hwnd} (PID: {pid})")
                return _nhot_hwnd(hwnd, widget_id, width, height)
        time.sleep(0.05)

    # Fallback: Dùng port để tìm chính xác process của profile thay vì lấy bừa
    if target_port:
        print(f"[!] Fallback tìm HWND qua port {target_port}...")
        hwnd = _find_hwnd_by_port_global(target_port)
        if hwnd:
            print(f"[V] Fallback HWND: {hwnd}")
            return _nhot_hwnd(hwnd, widget_id, width, height)

    print("[X] Không tìm thấy cửa sổ Chrome!")
    return 0


def _find_hwnd_by_port_global(port: int) -> int:
    """Hàm hỗ trợ tìm HWND toàn cục bằng port CDP."""
    try:
        import psutil, re
        user32 = ctypes.windll.user32
        browser_names = {"chrome.exe", "orbita-browser.exe", "chromium.exe"}
        target_pids = set()
        for proc in psutil.process_iter(['name', 'pid', 'cmdline']):
            try:
                name = proc.info.get('name', '')
                if name and name.lower() in browser_names:
                    cmdline = " ".join(proc.info.get('cmdline') or [])
                    if f"--remote-debugging-port={port}" in cmdline:
                        target_pids.add(proc.info['pid'])
                        for child in proc.children(recursive=True):
                            target_pids.add(child.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if not target_pids:
            return 0

        found = []
        def enum_cb(hwnd, _):
            if win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) == "Chrome_WidgetWin_1":
                pid_val = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_val))
                if pid_val.value in target_pids:
                    found.append(hwnd)

        win32gui.EnumWindows(enum_cb, None)
        if found:
            return found[0]
    except Exception:
        pass
    return 0


def _is_port_open(port: int, timeout: float = 0.3) -> bool:
    """Kiểm tra nhanh xem một port TCP có đang mở không."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def detect_running_browser(profile_dir: str, debug_port: int) -> int | None:
    """
    Tìm trình duyệt GoLogin/Chrome đang chạy khớp với profile_dir.
    Trả về debug_port nếu tìm thấy, None nếu không.
    """
    norm_dir = os.path.normcase(os.path.normpath(profile_dir))

    # --- Bước 1: Dùng psutil quét process (Cách chuẩn nhất) ---
    try:
        import psutil
        import re
        browser_names = {"chrome.exe", "orbita-browser.exe", "chromium.exe"}
        for proc in psutil.process_iter(['name', 'pid', 'cmdline']):
            try:
                name = proc.info.get('name', '')
                if not name: continue
                if name.lower() in browser_names:
                    cmdline_list = proc.info.get('cmdline') or []
                    cmdline = " ".join(cmdline_list)
                    
                    # Trích xuất user-data-dir bằng Regex
                    dir_match = re.search(r'--user-data-dir=([^\s]+)', cmdline)
                    if dir_match:
                        # Làm sạch quote và normcase
                        found_dir = os.path.normcase(os.path.normpath(dir_match.group(1).strip('"\'')))
                        if found_dir == norm_dir:
                            # Nếu profile khớp chính xác, tìm port
                            port_match = re.search(r'--remote-debugging-port=(\d+)', cmdline)
                            if port_match:
                                port = int(port_match.group(1))
                                if _is_port_open(port):
                                    print(f"[V] Tìm thấy browser profile={os.path.basename(profile_dir)} trên port {port}")
                                    return port
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:
        print("[!] psutil chưa cài, fallback kiểm tra port...")

    # --- Bước 2: Kiểm tra đúng debug_port bằng CDP ---
    if _is_port_open(debug_port):
        try:
            import urllib.request
            url = f"http://127.0.0.1:{debug_port}/json/version"
            with urllib.request.urlopen(url, timeout=0.5) as resp:
                _json.loads(resp.read())
                print(f"[V] Port {debug_port} đang mở CDP - attach thẳng!")
                return debug_port
        except Exception:
            pass

    return None



class GoLoginWorker(QThread):
    status_update = pyqtSignal(str, str)
    hwnd_found = pyqtSignal(int)
    finished_signal = pyqtSignal(str)
    profile_update_signal = pyqtSignal(dict)

    def __init__(self, profile_index, profile_data, selected_features, feed_settings,
                 widget_id=0, container_width=0, container_height=0, parent=None):
        super().__init__(parent)
        self.profile_index = profile_index
        self.profile_data = profile_data
        self.selected_features = selected_features
        self.feed_settings = feed_settings
        self.widget_id = widget_id
        self.container_width = container_width
        self.container_height = container_height
        self.page = None
        self._stop_flag = False
        self._process = None
        self._debug_port = 9222 + profile_index

        # Parse proxy: host:port:user:pass
        self._proxy_host = ""
        self._proxy_port = ""
        self._proxy_user = ""
        self._proxy_pass = ""
        proxy_str = self.profile_data.get('proxy', '')
        if proxy_str:
            parts = proxy_str.split(':', 3)
            if len(parts) >= 2:
                self._proxy_host = parts[0]
                self._proxy_port = parts[1]
            if len(parts) >= 4:
                self._proxy_user = parts[2]
                self._proxy_pass = parts[3]

        # Thư mục profile riêng biệt cho mỗi browser_id
        browser_id = self.profile_data.get('browser_id', '')
        if browser_id:
            self._profile_dir = str(gologin_profile_dir(browser_id))
        elif profile_index == 0:
            self._profile_dir = str(gologin_profile_dir("", 0))
        else:
            self._profile_dir = str(gologin_profile_dir("", profile_index))

    def run(self):
        try:
            self._prepare_profile_dir()

            # === KIỂM TRA THÔNG TIN ĐĂNG NHẬP TRƯỚC KHI MỞ BROWSER ===
            if "Đăng nhập" in self.selected_features:
                username = self.profile_data.get('username', '').strip()
                password = self.profile_data.get('password', '').strip()
                cookie = self.profile_data.get('cookie', '').strip()
                profile_name = self.profile_data.get('ten_ho_so', '')

                # Nếu không có cookie VÀ thiếu username hoặc password → dừng
                if not cookie and (not username or not password):
                    missing = []
                    if not username: missing.append("Username/Email")
                    if not password: missing.append("Password")
                    self.status_update.emit(
                        f"❌ [{profile_name}] Thiếu {', '.join(missing)} - Bỏ qua!", "red"
                    )
                    self.finished_signal.emit("error")
                    return

            browser_id = self.profile_data.get('browser_id', '')
            self.status_update.emit(
                f"[{browser_id}] Mở trình duyệt (port {self._debug_port})...", "blue"
            )

            # BƯỚC 1: Dọn port cũ nếu bị chiếm bởi session trước
            self._kill_stale_port()

            # BƯỚC 2: Mở trình duyệt mới
            if not self._launch_browser():
                return

            # BƯỚC 3: Đợi CDP sẵn sàng (Chrome đã khởi tạo xong)
            if not self._wait_for_cdp_ready():
                self.status_update.emit("❌ CDP không sẵn sàng sau 15s!", "red")
                self.finished_signal.emit("error")
                return

            # BƯỚC 4: Nhốt HWND ngay sau CDP ready (Chrome đã ổn định, an toàn để nhốt)
            if self.widget_id and self._process:
                self.status_update.emit("Nhốt trình duyệt vào dashboard...", "blue")
                w = self.container_width if self.container_width else BROWSER_WIDTH
                h = self.container_height if self.container_height else BROWSER_HEIGHT
                chrome_hwnd = tim_cua_so_theo_pid(
                    self._process.pid, self.widget_id, w, h,
                    target_port=self._debug_port
                )
                if chrome_hwnd:
                    self.hwnd_found.emit(chrome_hwnd)
                    self.status_update.emit("Đã nhốt trình duyệt!", "green")
                else:
                    self.status_update.emit("⚠️ Không nhốt được", "orange")
                # Chờ Chrome ổn định sau khi thay đổi window style
                time.sleep(1)

            # BƯỚC 5: Kết nối Playwright → chạy automation
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._run_automation())
            loop.close()

        except Exception as e:
            self.status_update.emit(f"Lỗi: {e}", "red")
            self.finished_signal.emit(f"error: {e}")

    def _prepare_profile_dir(self):
        """Tạo thư mục profile từ zero template (nhanh hơn tạo mới từ đầu)"""
        self._process = None  # Reset process mỗi lần run
        if not os.path.exists(self._profile_dir):
            self.status_update.emit(f"Tạo profile mới: {os.path.basename(self._profile_dir)}", "blue")
            # Giải nén từ zero profile template nếu có
            if os.path.exists(ZERO_PROFILE_ZIP):
                import zipfile
                parent_dir = os.path.dirname(self._profile_dir)
                os.makedirs(parent_dir, exist_ok=True)
                with zipfile.ZipFile(ZERO_PROFILE_ZIP, 'r') as zf:
                    # Giải nén vào thư mục tạm rồi rename
                    zf.extractall(parent_dir)
                # Rename thư mục đã giải nén thành tên profile
                extracted = os.path.join(parent_dir, "gologin_zeroprofile")
                if os.path.exists(extracted):
                    shutil.move(extracted, self._profile_dir)
                else:
                    os.makedirs(self._profile_dir, exist_ok=True)
                self.status_update.emit("Profile từ template OK", "green")
            else:
                os.makedirs(self._profile_dir, exist_ok=True)
        # Luôn dọn Device Mode để tránh bị ép mobile emulation
        self._disable_device_mode()

    def _disable_device_mode(self):
        """Tắt DevTools Device Mode trong profile Preferences.
        Thanh 'Dimensions: Responsive' xuất hiện do DevTools lưu trạng thái
        'showDeviceMode: true' trong Preferences. Dọn nó trước khi mở Chrome."""
        prefs_path = os.path.join(self._profile_dir, "Default", "Preferences")
        if not os.path.exists(prefs_path):
            return
        try:
            with open(prefs_path, 'r', encoding='utf-8') as f:
                prefs = _json.load(f)

            changed = False
            # Tắt device mode trong devtools
            devtools = prefs.get("devtools", {})
            if devtools.get("preferences", {}).get("showDeviceMode") == "true":
                devtools["preferences"]["showDeviceMode"] = "false"
                changed = True
            # Xóa device metrics override nếu có
            if "device_metrics_override" in devtools:
                del devtools["device_metrics_override"]
                changed = True
            if changed:
                prefs["devtools"] = devtools
                with open(prefs_path, 'w', encoding='utf-8') as f:
                    _json.dump(prefs, f, indent=2, ensure_ascii=False)
                self.status_update.emit("Đã tắt Device Mode trong Preferences", "blue")
        except Exception:
            pass  # Profile mới chưa có Preferences → bỏ qua

    def _kill_stale_port(self):
        """Dọn Chrome zombie đang chiếm port của profile này."""
        if not _is_port_open(self._debug_port):
            return  # Port trống, không cần dọn
        try:
            import psutil
            browser_names = {"chrome.exe", "orbita-browser.exe", "chromium.exe"}
            for proc in psutil.process_iter(['name', 'pid', 'cmdline']):
                try:
                    name = (proc.info.get('name') or '').lower()
                    if name in browser_names:
                        cmdline = " ".join(proc.info.get('cmdline') or [])
                        if f"--remote-debugging-port={self._debug_port}" in cmdline:
                            self.status_update.emit(
                                f"Dọn Chrome cũ (PID {proc.info['pid']}) trên port {self._debug_port}", "orange"
                            )
                            proc.kill()
                            proc.wait(timeout=3)
                except Exception:
                    continue
        except ImportError:
            pass
        # Chờ port thực sự giải phóng
        for _wait in range(10):
            if not _is_port_open(self._debug_port):
                break
            time.sleep(0.3)

    def _launch_browser(self):
        try:
            exe_path = require_orbita_browser_exe()
        except FileNotFoundError as e:
            self.status_update.emit(f"ERROR: {e}", "red")
            self.finished_signal.emit("error")
            return False
        for candidate in [
            r"C:\Program Files\SSMATool\browser\orbita-browser.exe",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser", "orbita-browser.exe"),
        ]:
            if os.path.exists(candidate):
                exe_path = candidate; break

        if not os.path.exists(exe_path):
            self.status_update.emit("❌ Không tìm thấy trình duyệt!", "red")
            self.finished_signal.emit("error"); return False

        cmd = [
            exe_path,
            f"--remote-debugging-port={self._debug_port}",
            f"--user-data-dir={self._profile_dir}",
            f"--window-size={BROWSER_WIDTH},{BROWSER_HEIGHT}",
            "--no-first-run", "--no-default-browser-check",
            "--disable-background-timer-throttling",
            "--disable-session-crashed-bubble",
            "--disable-infobars",
            "--hide-crash-restore-bubble",
        ]

        # Proxy: dùng bridge trung gian (hỗ trợ cả HTTP và SOCKS5 có auth)
        if self._proxy_host and self._proxy_port:
            from local_proxy import create_local_proxy
            local_port = 18080 + self.profile_index
            proxy_str = self.profile_data.get('proxy', '')
            proxy_type = self.profile_data.get('proxy_type', 'socks5')
            self._local_proxy = create_local_proxy(local_port, proxy_str, proxy_type)
            cmd.append(f"--proxy-server=127.0.0.1:{local_port}")
            self.status_update.emit(f"{proxy_type.upper()} Bridge: :{local_port} -> {self._proxy_host}:{self._proxy_port}", "blue")

        try:
            self._process = subprocess.Popen(cmd)
            self.status_update.emit("Trình duyệt đang mở...", "blue")
            time.sleep(1)  # Chờ 1s đủ để Chrome khởi tạo window
            return True
        except Exception as e:
            self.status_update.emit(f"❌ Lỗi: {e}", "red")
            self.finished_signal.emit("error"); return False

    def _wait_for_cdp_ready(self, timeout=15):
        """Poll CDP endpoint cho đến khi browser thực sự sẵn sàng.
        Trả về True nếu CDP ready, False nếu timeout."""
        import urllib.request
        url = f"http://127.0.0.1:{self._debug_port}/json/version"
        start = time.time()
        while time.time() - start < timeout:
            try:
                with urllib.request.urlopen(url, timeout=1) as resp:
                    data = _json.loads(resp.read())
                    ws_url = data.get("webSocketDebuggerUrl", "")
                    if ws_url:
                        self.status_update.emit(f"CDP sẵn sàng (port {self._debug_port})", "green")
                        return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

    async def _run_automation(self):
        from playwright.async_api import async_playwright

        cdp_url = f"http://localhost:{self._debug_port}"
        self.status_update.emit(f"Kết nối Playwright (port {self._debug_port})...", "blue")

        async with async_playwright() as p:
            browser = None
            for attempt in range(5):
                try:
                    browser = await p.chromium.connect_over_cdp(cdp_url)
                    self.status_update.emit("✅ Kết nối CDP thành công!", "green")
                    break
                except:
                    if attempt < 4:
                        self.status_update.emit(f"Chờ CDP... ({attempt+1}/5)", "blue")
                        await asyncio.sleep(2)
                    else:
                        self.status_update.emit("❌ Không kết nối được!", "red")
                        self.finished_signal.emit("error"); return

            self._context = browser.contexts[0]
            self.page = self._context.pages[0] if self._context.pages else await self._context.new_page()

            # Dọn tab thừa, chỉ giữ 1 tab
            all_pages = self._context.pages
            for pg in all_pages:
                if pg != self.page:
                    try: await pg.close()
                    except: pass

            # (HWND đã được nhốt ở run() trước khi vào đây)

            # === TẮT DEVICE EMULATION (thanh "Responsive" trên DevTools) ===
            try:
                cdp_session = await self.page.context.new_cdp_session(self.page)
                # Xóa device metrics override (tắt mobile emulation)
                await cdp_session.send("Emulation.clearDeviceMetricsOverride")
                # Tắt touch emulation
                await cdp_session.send("Emulation.setEmitTouchEventsForMouse", {"enabled": False})
                await cdp_session.detach()
                self.status_update.emit("Desktop mode OK", "green")
            except Exception:
                pass  # Không critical

            # === ÉP VIEWPORT KHỚP KHUÔN CONTAINER ===
            try:
                vw = self.container_width if self.container_width else BROWSER_WIDTH
                vh = self.container_height if self.container_height else BROWSER_HEIGHT
                await self.page.set_viewport_size({"width": vw, "height": vh})
                self.status_update.emit(f"Viewport: {vw}x{vh}", "green")
            except Exception:
                pass

            # === Kiểm tra Page còn sống không trước khi navigate ===
            await self._ensure_page_alive()

            # === VÀO TIKTOK ===
            self.status_update.emit("Truy cập TikTok...", "blue")
            await self._safe_goto("https://www.tiktok.com/")
            await asyncio.sleep(3)

            # === BỎ QUA POPUP CHỌN CHỦ ĐỀ ===
            await self._skip_tiktok_popup()
            await asyncio.sleep(1)

            # === CHẠY TẤT CẢ CHỨC NĂNG ===
            if "Đăng nhập" in self.selected_features:
                await self._do_login()

            if "Tương tác ở Feed" in self.selected_features:
                await self._do_feed_interaction()

            if "Tương tác theo từ khóa(key)" in self.selected_features:
                await self._do_keyword_interaction()

            for feat in ["KYC(gologin)(pro)", "Set riêng tư(pro)", "Đổi password(firefox)(pro)",
                         "Login mail(pro)", "Xóa tài khoản(pro)"]:
                if feat in self.selected_features:
                    self.status_update.emit(f"{feat} - Đang phát triển...", "orange")

            self.status_update.emit("✅ Hoàn thành!", "green")
            self.finished_signal.emit("success")

    async def _ensure_page_alive(self):
        """Kiểm tra và khôi phục page nếu đã bị đóng."""
        try:
            # Thử evaluate đơn giản để xem page còn sống không
            await self.page.evaluate("() => document.readyState")
            return True
        except Exception:
            self.status_update.emit("⚠️ Page bị mất, đang khôi phục...", "orange")
            return await self._recover_page()

    async def _recover_page(self):
        """Khôi phục page từ context khi page hiện tại bị đóng."""
        try:
            if not hasattr(self, '_context') or not self._context:
                return False
            # Thử lấy page cuối từ context
            pages = self._context.pages
            if pages:
                self.page = pages[-1]
                self.status_update.emit("✅ Đã khôi phục page từ context", "green")
                return True
            else:
                # Không còn page nào, tạo mới
                self.page = await self._context.new_page()
                self.status_update.emit("✅ Đã tạo page mới", "green")
                return True
        except Exception as e:
            self.status_update.emit(f"❌ Không thể khôi phục page: {str(e)[:50]}", "red")
            return False

    async def _safe_goto(self, url, max_retries=3):
        """Goto với auto-retry 3 lần, timeout 60s, auto-recover page khi bị đóng."""
        from playwright.async_api import Error
        for attempt in range(max_retries):
            try:
                await self.page.goto(url, timeout=60000, wait_until="domcontentloaded")
                return  # Thành công
            except Error as e:
                err = str(e).lower()
                if "interrupted by another navigation" in err:
                    await self.page.wait_for_timeout(2000)
                    return
                elif "target page, context or browser has been closed" in err or \
                     "browser has been closed" in err:
                    self.status_update.emit(
                        f"⚠️ Page bị đóng (lần {attempt+1}/{max_retries}), khôi phục...", "orange"
                    )
                    # Thử khôi phục page trước khi retry
                    recovered = await self._recover_page()
                    if not recovered:
                        if attempt == max_retries - 1:
                            self.status_update.emit("❌ Browser sập hoàn toàn!", "red")
                            self.finished_signal.emit("error")
                            raise
                    await asyncio.sleep(2)
                else:
                    self.status_update.emit(f"⚠️ Goto lỗi (lần {attempt+1}): {str(e)[:60]}", "orange")
                    if attempt == max_retries - 1:
                        raise
                    await asyncio.sleep(3)

    async def _skip_tiktok_popup(self):
        """Bỏ qua popup chọn chủ đề (What would you like to watch) của TikTok."""
        try:
            # Thử nút Skip trước
            skip = self.page.locator('button:has-text("Skip")')
            if await skip.is_visible(timeout=4000):
                await skip.click()
                self.status_update.emit("✅ Đã bỏ qua popup chủ đề", "green")
                return
        except Exception:
            pass
        try:
            # Nếu không có Skip → chọn 1 ô bất kỳ rồi bấm Continue
            first_tag = self.page.locator('[data-e2e="interest-item"]').first
            if await first_tag.is_visible(timeout=2000):
                await first_tag.click()
                await asyncio.sleep(0.5)
                cont = self.page.locator('button:has-text("Continue")')
                if await cont.is_visible(timeout=2000):
                    await cont.click()
                self.status_update.emit("✅ Đã bỏ qua popup chủ đề (Continue)", "green")
        except Exception:
            pass  # Không có popup thì thôi

    async def _do_login(self):
        cookie_str = self.profile_data.get("cookie", "")
        username = self.profile_data.get("username", "")
        password = self.profile_data.get("password", "")

        # 1. Bơm cookie nếu có
        if cookie_str and len(cookie_str) > 20:
            self.status_update.emit("Bơm Cookie...", "blue")
            cookies = []
            for pair in cookie_str.split(';'):
                pair = pair.strip()
                if '=' in pair:
                    name, value = pair.split('=', 1)
                    cookies.append({"name": name.strip(), "value": value.strip(),
                                    "domain": ".tiktok.com", "path": "/"})
            try: await self.page.context.add_cookies(cookies)
            except: pass
            await self._safe_goto("https://www.tiktok.com/")
            await asyncio.sleep(2)

        # 2. Kiểm tra trạng thái đăng nhập → xác minh bằng TikTok ID
        if await self._check_logged_in():
            self.status_update.emit("🔍 Phát hiện phiên — đang xác minh ID...", "blue")
            verified_id = await self._extract_profile_info()
            if verified_id:
                self.status_update.emit(f"✅ Đã đăng nhập! ({verified_id})", "green")
                return
            else:
                self.status_update.emit("⚠️ Cookie/phiên hết hạn — không lấy được ID", "orange")

        if not username or not password:
            self.status_update.emit("❌ Không có Email/Password.", "red"); return

        # 3. Tiến hành đăng nhập bằng Email/Username
        self.status_update.emit("Mở trang đăng nhập...", "blue")
        await self._safe_goto("https://www.tiktok.com/login/phone-or-email/email")
        
        # Đợi ô username xuất hiện (tối đa 15s)
        try:
            user_input = await self.page.wait_for_selector('input[name="username"]', timeout=15000)
            if not user_input:
                self.status_update.emit("Lỗi: Không tìm thấy ô nhập Email", "red"); return
        except:
            self.status_update.emit("Lỗi: Timeout chờ trang đăng nhập", "red"); return

        self.status_update.emit("Gõ Email/Username...", "blue")
        try:
            await self.page.locator('input[name="username"]').first.click()
            await self.page.keyboard.type(username, delay=random.randint(50, 100))
        except: self.status_update.emit("Lỗi gõ email", "red"); return
        await asyncio.sleep(0.5)

        self.status_update.emit("Gõ Password...", "blue")
        try:
            await self.page.locator('input[type="password"]').first.click()
            await self.page.keyboard.type(password, delay=random.randint(50, 100))
        except: self.status_update.emit("Lỗi gõ password", "red"); return
        await asyncio.sleep(0.5)

        self.status_update.emit("Click Login...", "blue")
        try:
            btn = self.page.locator('button[data-e2e="login-button"]').first
            await btn.hover()
            await asyncio.sleep(0.5)
            await btn.click()
        except: self.status_update.emit("Lỗi click login", "red"); return

        # 4. Chờ kết quả đăng nhập — CAPTCHA sẽ được chờ thủ công
        self.status_update.emit("Đang chờ xác nhận...", "blue")
        TOTAL_WAIT = 60   # giây cơ bản (sẽ tăng khi có CAPTCHA)
        POLL = 2
        extra_time = 0     # Thời gian thêm khi có CAPTCHA
        captcha_notified = False

        for step in range(999):  # Vòng lặp mở, thoát bằng điều kiện
            elapsed = (step + 1) * POLL
            if elapsed > TOTAL_WAIT + extra_time:
                break

            await asyncio.sleep(POLL)
            remaining = TOTAL_WAIT + extra_time - elapsed

            # ═══ KIỂM TRA CAPTCHA TRƯỚC TIÊN ═══
            # Nếu CAPTCHA đang hiện → DỪNG mọi thứ, chờ user giải xong
            has_captcha = False
            try:
                has_captcha = await self.page.evaluate("""() => {
                    // 1. TikTok captcha container
                    const captchaEls = document.querySelectorAll(
                        '[class*="captcha" i], [class*="Captcha" i], [id*="captcha" i]'
                    );
                    for (const el of captchaEls) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 50 && r.height > 50) return true;
                    }

                    // 2. TikTok secsdk verify (puzzle/slide captcha)
                    const secsdkEls = document.querySelectorAll(
                        '[id*="secsdk"], [class*="secsdk"], [id*="verify-bar"],' +
                        ' [class*="verify-wrap"], [class*="VerifyBar"],' +
                        ' [class*="captcha_verify"], [class*="CaptchaVerify"]'
                    );
                    for (const el of secsdkEls) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 50 && r.height > 50) return true;
                    }

                    // 3. Iframe captcha
                    const iframes = document.querySelectorAll(
                        'iframe[src*="captcha"], iframe[src*="challenge"],' +
                        ' iframe[src*="recaptcha"], iframe[src*="hcaptcha"],' +
                        ' iframe[title*="captcha" i], iframe[title*="verification" i]'
                    );
                    if (iframes.length > 0) return true;

                    // 4. Text-based
                    const body = document.body.innerText || '';
                    if (body.includes('Drag the slider') || body.includes('Drag the puzzle')
                        || body.includes('Kéo thanh trượt') || body.includes('Slide to verify')
                        || body.includes('Verify to continue'))
                        return true;

                    return false;
                }""")
            except:
                pass

            if has_captcha:
                if not captcha_notified:
                    self.status_update.emit("🧩 CAPTCHA hiện ra! Hãy giải thủ công...", "orange")
                    captcha_notified = True
                else:
                    self.status_update.emit(f"🧩 Đang chờ giải CAPTCHA... ({remaining}s)", "orange")
                # Tăng timeout khi có CAPTCHA (tối đa +180s)
                if extra_time < 180:
                    extra_time += POLL
                continue  # KHÔNG check lỗi, OTP — chờ CAPTCHA biến mất

            # CAPTCHA đã biến mất → thông báo
            if captcha_notified:
                self.status_update.emit("✅ CAPTCHA đã giải xong! Đang kiểm tra...", "green")
                captcha_notified = False

            # ── KIỂM TRA LỖI TRƯỚC — dòng chữ đỏ trên form login ──
            try:
                error_msg = await self.page.evaluate("""() => {
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

                    // Sai mật khẩu / sai tài khoản (CHÍNH XÁC như TikTok hiển thị)
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

                    // Lỗi hệ thống
                    if(body.includes("Something went wrong") || body.includes("Đã xảy ra lỗi")
                       || body.includes("network error") || body.includes("try again later"))
                        return "Lỗi hệ thống TikTok";

                    return '';
                }""")
                if error_msg:
                    self.status_update.emit(f"❌ {error_msg}", "red")
                    # Emit lỗi về Dashboard để cập nhật cột Logged
                    self.profile_update_signal.emit({
                        "tiktok_id": "",
                        "cookie": "",
                        "login_error": error_msg,
                    })
                    return
            except:
                pass

            # ── Kiểm tra đăng nhập thành công (CHỈ sau khi xác nhận KHÔNG có lỗi) ──
            if await self._check_logged_in():
                self.status_update.emit("✅ Đăng nhập thành công!", "green")
                await self._extract_profile_info()
                return

            # ── Kiểm tra OTP ──
            try:
                otp_input = await self.page.query_selector('input[type="text"][autocomplete="one-time-code"], input[name="code"]')
                if otp_input or await self.page.evaluate('() => document.body.innerText.includes("Enter the 6-digit code")'):
                    self.status_update.emit("Đòi OTP! Dùng IMAP móc mã...", "orange")
                    imap_pass = self.profile_data.get("password_mail", "")
                    if not imap_pass: imap_pass = password  # Fallback dùng chung pass
                    
                    otp_code = await self._get_tiktok_code_via_imap(username, imap_pass)
                    if otp_code:
                        self.status_update.emit(f"Nhập mã OTP: {otp_code}", "blue")
                        try:
                            await self.page.keyboard.type(otp_code, delay=50)
                        except: pass
                        await asyncio.sleep(5)
                        if await self._check_logged_in():
                            self.status_update.emit("✅ Đăng nhập qua OTP thành công!", "green")
                            await self._extract_profile_info()
                            return
                    else:
                        self.status_update.emit("❌ IMAP không lấy được mã OTP", "red")
                        return
            except: pass

            self.status_update.emit(f"⏳ Chờ đăng nhập... ({remaining}s)", "blue")
            
        self.status_update.emit("⚠️ Hết thời gian chờ đăng nhập.", "orange")

    async def _get_tiktok_code_via_imap(self, email, password):
        """Lấy OTP từ IMAP.
        - Hotmail/Outlook: dùng OAuth2 (Basic Auth đã bị Microsoft tắt từ 09/2024)
        - Gmail: dùng Basic Auth (App Password)
        - Các provider khác: dùng Basic Auth
        """
        try:
            from imap_tools import MailBox, AND
            import re
        except ImportError:
            self.status_update.emit("❌ Thiếu imap_tools: pip install imap-tools", "red")
            return None

        email_lower = email.lower()
        is_microsoft = any(d in email_lower for d in [
            'hotmail.', 'outlook.', 'live.', 'msn.', 'passport.'
        ])

        await asyncio.sleep(5)  # Chờ mail tới

        try:
            for attempt in range(3):
                mailbox = None
                try:
                    if is_microsoft:
                        # ═══ OAUTH2 cho Hotmail/Outlook ═══
                        access_token = await self._get_microsoft_oauth_token(email, password)
                        if not access_token:
                            self.status_update.emit("❌ Không lấy được OAuth token", "red")
                            return None
                        mailbox = MailBox('imap-mail.outlook.com')
                        mailbox.xoauth2(email, access_token)
                    elif 'gmail.com' in email_lower:
                        # ═══ BASIC AUTH cho Gmail (App Password) ═══
                        mailbox = MailBox('imap.gmail.com')
                        mailbox.login(email, password)
                    else:
                        # ═══ BASIC AUTH cho provider khác ═══
                        domain = email_lower.split('@')[1]
                        imap_server = f'imap.{domain}'
                        mailbox = MailBox(imap_server)
                        mailbox.login(email, password)

                    # Tìm mail OTP từ TikTok
                    for msg in mailbox.fetch(AND(from_="tiktok", seen=False), reverse=True, limit=1):
                        body = msg.text or msg.html or ''
                        subject = msg.subject or ''
                        match = re.search(r'\b(\d{6})\b', subject + body)
                        if match:
                            mailbox.flag(msg.uid, '\\Seen', True)
                            code = match.group(1)
                            mailbox.logout()
                            return code

                    if mailbox:
                        mailbox.logout()

                except Exception as e:
                    err_str = str(e)[:80]
                    self.status_update.emit(f"⚠️ IMAP lần {attempt+1}: {err_str}", "orange")
                    if mailbox:
                        try: mailbox.logout()
                        except: pass

                if attempt < 2:
                    self.status_update.emit(f"📧 Chưa thấy mail OTP, chờ 5s (lần {attempt+2}/3)...", "blue")
                    await asyncio.sleep(5)

            return None

        except Exception as e:
            self.status_update.emit(f"❌ IMAP lỗi: {str(e)[:60]}", "red")
            return None

    async def _get_microsoft_oauth_token(self, email, password):
        """Lấy OAuth2 access token cho Hotmail/Outlook.
        Ưu tiên: refresh_token → ROPC (fallback).
        """
        try:
            import msal
        except ImportError:
            self.status_update.emit("❌ Thiếu msal: pip install msal", "red")
            return None

        try:
            refresh_token = self.profile_data.get("refresh_token", "").strip()
            client_id = self.profile_data.get("client_id", "").strip()

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
                    new_rt = result.get("refresh_token", "")
                    if new_rt and new_rt != refresh_token:
                        self.profile_data["refresh_token"] = new_rt
                        self.status_update.emit("🔄 Refresh token đã được cập nhật", "blue")
                    self.status_update.emit("✅ OAuth token OK (refresh)", "green")
                    return result["access_token"]
                else:
                    error = result.get("error_description", result.get("error", ""))
                    self.status_update.emit(f"⚠️ Refresh token lỗi: {str(error)[:50]}", "orange")

            # ═══ Cách 2: ROPC flow (fallback) ═══
            if password:
                self.status_update.emit("🔑 Thử ROPC flow...", "blue")
                result = app.acquire_token_by_username_password(
                    username=email,
                    password=password,
                    scopes=SCOPES
                )
                if "access_token" in result:
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

    async def _extract_profile_info(self) -> str:
        """Lấy thông tin username và cookie sau khi đăng nhập thành công.
        CHỈ coi là đăng nhập thành công khi thực sự lấy được TikTok ID.
        Returns: tiktok_id (str) nếu thành công, rỗng nếu thất bại."""
        try:
            self.status_update.emit("Đang lấy thông tin Profile...", "blue")
            
            # Chờ một lát để giao diện ổn định
            await asyncio.sleep(2)
            
            # Lấy cookie mới nhất
            cookies = await self.page.context.cookies()
            cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
            
            tiktok_id = ""
            
            # Thử tối đa 2 lần click vào profile để verify
            for attempt in range(2):
                # Click vào Avatar profile (hoặc Nav Profile) để vào trang cá nhân
                profile_btn = await self.page.query_selector('[data-e2e="nav-profile"], [data-e2e="profile-icon"]')
                if profile_btn:
                    await profile_btn.click()
                    # Chờ load trang cá nhân
                    try:
                        await self.page.wait_for_selector('[data-e2e="user-title"], [data-e2e="user-subtitle"]', timeout=10000)
                    except:
                        pass
                
                # Trích xuất username
                username_elem = await self.page.query_selector('[data-e2e="user-title"], [data-e2e="user-subtitle"]')
                if username_elem:
                    tiktok_id = await username_elem.inner_text()
                
                # Fallback: lấy từ URL
                if not tiktok_id:
                    current_url = self.page.url
                    import re
                    m = re.search(r'tiktok\.com/@([^/?]+)', current_url)
                    if m:
                        tiktok_id = f"@{m.group(1)}"
                
                if tiktok_id:
                    break  # Đã lấy được ID → thoát
                
                if attempt == 0:
                    self.status_update.emit("⚠️ Chưa lấy được ID, thử lại...", "orange")
                    await asyncio.sleep(2)
                    
            if tiktok_id:
                self.status_update.emit(f"✅ Đã lấy được: {tiktok_id}", "green")
            else:
                self.status_update.emit("❌ Không lấy được @username — KHÔNG đánh dấu Logged", "red")
                
            # Gửi dữ liệu về Dashboard: nếu tiktok_id rỗng thì dashboard sẽ KHÔNG set Logged=Yes
            self.profile_update_signal.emit({
                "tiktok_id": tiktok_id,
                "cookie": cookie_str,
                "refresh_token": self.profile_data.get("refresh_token", ""),
            })
            
            # Tắt màn hình sau 5 giây (user request)
            self.status_update.emit("Xong! Đóng sau 5s...", "gray")
            await asyncio.sleep(5)
            self._stop_flag = True # Báo hiệu ngừng thread
            return tiktok_id
            
        except Exception as e:
            print(f"Lỗi extract profile info: {e}")
            return ""

    async def _check_logged_in(self):
        """Kiểm tra trạng thái đăng nhập TikTok bằng nhiều cách"""
        try:
            return await self.page.evaluate("""() => {
                // Cách 1: Có nút Login => chưa đăng nhập
                const loginBtn = document.querySelector('[data-e2e="top-login-button"]') ||
                                 document.getElementById('header-login-button');
                if (loginBtn) {
                    // Kiểm tra nút login có thật sự hiển thị không
                    const rect = loginBtn.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) return false;
                }
                
                // Cách 2: Có icon profile trên sidebar/header
                const profileIcon = document.querySelector('[data-e2e="profile-icon"]') ||
                                    document.querySelector('[data-e2e="nav-profile"]');
                if (profileIcon) return true;
                
                // Cách 3: Sidebar có link Profile (chữ "Profile")
                const allLinks = document.querySelectorAll('a[href*="/@"]');
                for (const link of allLinks) {
                    if (link.closest('nav') || link.closest('[class*="sidebar"]')) return true;
                }
                
                // Cách 4: Có avatar user ở sidebar (hình tròn có ảnh)
                const avatarSidebar = document.querySelector('[data-e2e="nav-profile"] img, [class*="avatar"][class*="sidebar"]');
                if (avatarSidebar) return true;
                
                // Cách 5: Kiểm tra cookie sessionid
                if (document.cookie.includes('sessionid=') && !document.cookie.includes('sessionid=;')) return true;
                
                // Không tìm thấy nút Login cũng không tìm thấy Profile => mặc định chưa login
                return false;
            }""")
        except: return False

    async def _do_feed_interaction(self):
        feed_type = self.feed_settings.get('feed_type', 1)
        if feed_type == 0: return
        url = "https://www.tiktok.com/foryou" if feed_type == 1 else "https://www.tiktok.com/explore"
        self.status_update.emit("Lướt Feed...", "blue")
        await self._safe_goto(url); await asyncio.sleep(4)

        n = random.randint(self.feed_settings.get('view_min', 3), self.feed_settings.get('view_max', 5))
        for i in range(n):
            if self._stop_flag: break
            self.status_update.emit(f"Feed #{i+1}/{n} - Xem...", "blue")
            for _ in range(random.randint(15, 45) // 5):
                if self._stop_flag: break
                await self.page.mouse.move(random.randint(200, 800), random.randint(200, 500))
                await asyncio.sleep(random.uniform(3, 6))
            if random.randint(1, 100) <= self.feed_settings.get('like_video', 0):
                try:
                    btn = self.page.locator('span[data-e2e="like-icon"]').first
                    if await btn.count() > 0: await btn.click()
                except: pass
            await self.page.mouse.wheel(0, random.randint(800, 1200))
            await asyncio.sleep(random.randint(2, 4))
        self.status_update.emit("✅ Xong Feed!", "green")

    async def _do_keyword_interaction(self):
        keywords = self.feed_settings.get('keywords', [])
        if not keywords: self.status_update.emit("⚠️ Chưa có từ khóa.", "orange"); return
        for kw in keywords:
            if self._stop_flag: break
            self.status_update.emit(f"Tìm: {kw}...", "blue")
            await self._safe_goto(f"https://www.tiktok.com/search?q={kw.replace(' ', '%20')}")
            await asyncio.sleep(4)
            for i in range(random.randint(2, 4)):
                if self._stop_flag: break
                await self.page.mouse.move(random.randint(200, 800), random.randint(200, 500))
                await asyncio.sleep(random.randint(10, 25))
                await self.page.mouse.wheel(0, random.randint(500, 900))
                await asyncio.sleep(random.randint(2, 4))
        self.status_update.emit("✅ Xong từ khóa!", "green")

    def stop(self):
        self._stop_flag = True
        if self._process:
            try: self._process.terminate()
            except: pass
        # Dọn local proxy server
        if hasattr(self, '_local_proxy') and self._local_proxy:
            try: self._local_proxy.stop()
            except: pass
