"""
Worker thread để khởi động AdsPower browser và kết nối Playwright.

Trick nhốt trình duyệt:
1. Tạo tab mới → goto data:text/html,<title>MÃ_BÍ_MẬT</title>  (khóa cứng title)
2. bring_to_front() → Windows nhận diện title
3. EnumWindows tìm class Chrome_WidgetWin_1 + title chứa mã → SetParent → SW_MAXIMIZE
4. Nhốt xong mới cho vào TikTok
"""
import time
import random
import asyncio
import requests
import win32gui
import win32con
from PyQt5.QtCore import QThread, pyqtSignal

ADSPOWER_API = "http://local.adspower.net:50325/api/v1"


def nhot_browser_vao_form_v2(ma_bi_mat, widget_id):
    """
    Tìm cửa sổ Chrome có chứa mã bí mật và nhốt vào Widget PyQt5.
    Kiểm tra cả Class Name (Chrome_WidgetWin_1) để không bắt nhầm.
    """
    hwnds = []

    def enum_cb(hwnd, results):
        if win32gui.IsWindowVisible(hwnd):
            # KIỂM TRA 1: Phải là cửa sổ gốc Chromium
            if win32gui.GetClassName(hwnd) == "Chrome_WidgetWin_1":
                title = win32gui.GetWindowText(hwnd)
                # KIỂM TRA 2: Có chứa mã bí mật không
                if ma_bi_mat in title:
                    results.append(hwnd)

    print(f"[*] Đang đi săn cửa sổ có mã: {ma_bi_mat}...")

    for _ in range(30):  # Tìm tối đa 3 giây
        win32gui.EnumWindows(enum_cb, hwnds)
        if hwnds:
            break
        time.sleep(0.1)

    if hwnds:
        chrome_hwnd = hwnds[0]
        print(f"[V] Bắt được rồi! HWND: {chrome_hwnd}. Đang nhét vào Form...")

        try:
            # Lột viền
            style = win32gui.GetWindowLong(chrome_hwnd, win32con.GWL_STYLE)
            style = style & ~win32con.WS_CAPTION & ~win32con.WS_THICKFRAME
            win32gui.SetWindowLong(chrome_hwnd, win32con.GWL_STYLE, style)

            # Nhét vào ô đen
            win32gui.SetParent(chrome_hwnd, widget_id)
            win32gui.ShowWindow(chrome_hwnd, win32con.SW_MAXIMIZE)
            return chrome_hwnd
        except Exception as e:
            print(f"[X] Lỗi ép khung: {e}")
            return 0
    else:
        print("[X] Tìm mù mắt không thấy cái cửa sổ nào chứa mã bí mật!")
        return 0


class AdsPowerWorker(QThread):
    status_update = pyqtSignal(str, str)
    hwnd_found = pyqtSignal(int)
    finished_signal = pyqtSignal(str)

    def __init__(self, browser_id, profile_data, selected_features, feed_settings, widget_id, parent=None):
        super().__init__(parent)
        self.browser_id = browser_id
        self.profile_data = profile_data
        self.selected_features = selected_features
        self.feed_settings = feed_settings
        self.widget_id = widget_id
        self.page = None
        self._stop_flag = False
        self._ma_bi_mat = f"SSMA_TARGET_{widget_id}"

    def run(self):
        try:
            self.status_update.emit(f"Khởi động AdsPower ID: {self.browser_id}...", "blue")
            ws_endpoint = self._start_adspower()
            if not ws_endpoint:
                return

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._run_automation(ws_endpoint))
            loop.close()

        except Exception as e:
            self.status_update.emit(f"Lỗi: {e}", "red")
            self.finished_signal.emit(f"error: {e}")

    def _start_adspower(self):
        try:
            url = f"{ADSPOWER_API}/browser/start?user_id={self.browser_id}"
            resp = requests.get(url, timeout=30)
            data = resp.json()
            if data.get("code") != 0:
                self.status_update.emit(f"Lỗi AdsPower: {data.get('msg')}", "red")
                self.finished_signal.emit("error")
                return None
            ws = data["data"]["ws"]["puppeteer"]
            self.status_update.emit("AdsPower đã mở!", "blue")
            return ws
        except Exception as e:
            self.status_update.emit(f"Lỗi kết nối AdsPower: {e}", "red")
            self.finished_signal.emit("error")
            return None

    async def _run_automation(self, ws_endpoint):
        from playwright.async_api import async_playwright, Error

        # Đợi AdsPower browser khởi động xong hẳn
        self.status_update.emit("Chờ trình duyệt khởi động...", "blue")
        time.sleep(3)

        async with async_playwright() as p:
            # Thử kết nối CDP, retry tối đa 5 lần nếu browser chưa sẵn sàng
            browser = None
            for attempt in range(5):
                try:
                    browser = await p.chromium.connect_over_cdp(ws_endpoint)
                    break
                except Exception as e:
                    if attempt < 4:
                        self.status_update.emit(f"Chờ kết nối CDP... (lần {attempt+1})", "blue")
                        await asyncio.sleep(2)
                    else:
                        self.status_update.emit(f"Lỗi kết nối CDP: {e}", "red")
                        self.finished_signal.emit("error")
                        return

            context = browser.contexts[0]

            # ====================================================
            # BƯỚC 1: TẠO TAB MỚI + KHÓA CỨNG TITLE BẰNG DATA URI
            # ====================================================
            self.page = await context.new_page()
            self.status_update.emit(f"Tiêm mã bí mật: {self._ma_bi_mat}...", "blue")

            # Mở trang HTML ảo chỉ có mỗi cái Title = mã bí mật
            # Trang data: không cần mạng, không bị redirect, title gán cứng tức thì
            await self.page.goto(f"data:text/html,<title>{self._ma_bi_mat}</title>")

            # Ép tab này nổi lên trên cùng để Windows nhận diện được Title
            await self.page.bring_to_front()
            await asyncio.sleep(1)  # Chờ 1s cho Windows kịp cập nhật

            # ====================================================
            # BƯỚC 2: BẮT NHỐT TRÌNH DUYỆT VÀO Ô ĐEN
            # ====================================================
            chrome_hwnd = nhot_browser_vao_form_v2(self._ma_bi_mat, self.widget_id)
            if chrome_hwnd:
                self.hwnd_found.emit(chrome_hwnd)
                self.status_update.emit("Đã nhốt trình duyệt vào form!", "green")
            else:
                self.status_update.emit("⚠️ Không nhốt được trình duyệt!", "red")
                self.finished_signal.emit("error")
                return

            # ====================================================
            # BƯỚC 3: XÓA HẾT TAB THỪA, CHỈ GIỮ TAB LÀM VIỆC
            # ====================================================
            self.status_update.emit("Dọn dẹp tab thừa...", "blue")
            all_pages = context.pages
            for pg in all_pages:
                if pg != self.page:
                    try:
                        await pg.close()
                    except:
                        pass
            await asyncio.sleep(0.5)

            # ====================================================
            # BƯỚC 4: NHỐT XONG, DỌN XONG → CHO CHẠY VÀO TIKTOK
            # ====================================================
            if "Đăng nhập" in self.selected_features:
                await self._do_login()

            if "Tương tác ở Feed" in self.selected_features:
                await self._do_feed_interaction()

            self.status_update.emit("✅ Hoàn thành!", "green")
            self.finished_signal.emit("success")

            # Giữ thread sống
            while not self._stop_flag:
                await asyncio.sleep(1)

    async def _safe_goto(self, url):
        """Truy cập URL, tự bắt lỗi redirect của TikTok."""
        from playwright.async_api import Error
        try:
            await self.page.goto(url, timeout=60000, wait_until="domcontentloaded")
        except Error as e:
            if "interrupted by another navigation" in str(e).lower():
                await self.page.wait_for_timeout(3000)
            else:
                raise e

    async def _do_login(self):
        cookie_str = self.profile_data.get("cookie", "")
        username = self.profile_data.get("username", "")
        password = self.profile_data.get("password", "")

        if cookie_str and len(cookie_str) > 20:
            self.status_update.emit("Bơm Cookie...", "blue")
            cookies = []
            for pair in cookie_str.split(';'):
                pair = pair.strip()
                if '=' in pair:
                    name, value = pair.split('=', 1)
                    cookies.append({"name": name.strip(), "value": value.strip(),
                                    "domain": ".tiktok.com", "path": "/"})
            try:
                await self.page.context.add_cookies(cookies)
            except:
                pass

        self.status_update.emit("Đang truy cập TikTok...", "blue")
        await self._safe_goto("https://www.tiktok.com/")
        await asyncio.sleep(3)

        if await self._check_logged_in():
            self.status_update.emit("✅ Đã đăng nhập thành công!", "green")
            return

        if not username or not password:
            self.status_update.emit("❌ Cookie hết hạn, không có Email/Password.", "red")
            return

        self.status_update.emit("Mở trang đăng nhập...", "blue")
        await self._safe_goto("https://www.tiktok.com/login/phone-or-email/email")
        await asyncio.sleep(2)

        self.status_update.emit("Gõ Email...", "blue")
        try:
            await self.page.locator('input[name="username"]').first.click()
            await self.page.keyboard.type(username, delay=random.randint(50, 120))
        except:
            self.status_update.emit("Lỗi gõ email", "red"); return
        await asyncio.sleep(1)

        self.status_update.emit("Gõ Password...", "blue")
        try:
            await self.page.locator('input[type="password"]').first.click()
            await self.page.keyboard.type(password, delay=random.randint(50, 120))
        except:
            self.status_update.emit("Lỗi gõ password", "red"); return
        await asyncio.sleep(1)

        self.status_update.emit("Click Login...", "blue")
        try:
            btn = self.page.locator('button[data-e2e="login-button"]').first
            await btn.hover(); await asyncio.sleep(0.5); await btn.click()
        except:
            self.status_update.emit("Lỗi click login", "red"); return

        for _ in range(10):
            await asyncio.sleep(3)
            if await self._check_logged_in():
                self.status_update.emit("✅ Đăng nhập thành công!", "green")
                return
        self.status_update.emit("⚠️ Chưa xác nhận được đăng nhập.", "orange")

    async def _check_logged_in(self):
        try:
            return await self.page.evaluate("""() => {
                const hasProfile = !!(document.querySelector('[data-e2e="profile-icon"]') ||
                                      document.querySelector('[data-e2e="nav-profile"]'));
                const hasLoginBtn = !!(document.querySelector('[data-e2e="top-login-button"]') || 
                                       document.getElementById('header-login-button'));
                return hasProfile && !hasLoginBtn;
            }""")
        except: return False

    async def _do_feed_interaction(self):
        feed_type = self.feed_settings.get('feed_type', 1)
        if feed_type == 0:
            return

        url = "https://www.tiktok.com/foryou" if feed_type == 1 else "https://www.tiktok.com/explore"
        self.status_update.emit("Đang lướt Feed...", "blue")
        await self._safe_goto(url)
        await asyncio.sleep(4)

        num_videos = random.randint(
            self.feed_settings.get('view_min', 3),
            self.feed_settings.get('view_max', 5)
        )

        for i in range(num_videos):
            if self._stop_flag: break
            self.status_update.emit(f"Feed #{i+1}/{num_videos} - Đang xem...", "blue")

            for _ in range(random.randint(15, 45) // 5):
                if self._stop_flag: break
                await self.page.mouse.move(random.randint(200, 800), random.randint(200, 500))
                await asyncio.sleep(random.uniform(3, 6))

            if random.randint(1, 100) <= self.feed_settings.get('like_video', 0):
                self.status_update.emit(f"Feed #{i+1} - Thả tim...", "blue")
                try:
                    btn = self.page.locator('span[data-e2e="like-icon"]').first
                    if await btn.count() > 0:
                        await btn.hover(); await asyncio.sleep(0.5); await btn.click()
                except: pass

            self.status_update.emit(f"Feed #{i+1} - Lướt tiếp...", "blue")
            await self.page.mouse.wheel(0, random.randint(800, 1200))
            await asyncio.sleep(random.randint(2, 4))

        self.status_update.emit("✅ Hoàn thành lướt Feed!", "green")

    def stop(self):
        self._stop_flag = True
        try:
            requests.get(f"{ADSPOWER_API}/browser/stop?user_id={self.browser_id}", timeout=5)
        except:
            pass
