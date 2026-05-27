import sys
import uuid
import time
import win32gui
import win32con
from PyQt5.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QPushButton, QLabel
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from playwright.sync_api import sync_playwright

class BrowserWorker(QThread):
    title_ready = pyqtSignal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.playwright = None
        self.browser = None
        self.page = None

    def run(self):
        user_data_dir = "./mre_profile"
        
        # --- FIX: SỬA FILE PREFERENCES ĐỂ ẨN POPUP "RESTORE PAGES" ---
        import os, json
        pref_path = os.path.join(user_data_dir, "Default", "Preferences")
        if os.path.exists(pref_path):
            try:
                with open(pref_path, 'r', encoding='utf-8') as f:
                    prefs = json.load(f)
                # Đặt lại trạng thái đóng trình duyệt là Bình thường
                if 'profile' not in prefs:
                    prefs['profile'] = {}
                prefs['profile']['exit_type'] = 'Normal'
                prefs['profile']['exited_cleanly'] = True
                with open(pref_path, 'w', encoding='utf-8') as f:
                    json.dump(prefs, f)
            except:
                pass
        # -------------------------------------------------------------

        # 1. Khởi tạo Playwright trong Worker Thread
        self.playwright = sync_playwright().start()
        
        # 2. Cấu hình chống Crash và ẩn Popup

        self.browser = self.playwright.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,
            args=[
                '--disable-gpu', 
                '--no-sandbox', 
                '--disable-software-rasterizer', 
                '--disable-dev-shm-usage', 
                '--window-position=0,0',
                '--hide-crash-restore-bubble',
                '--disable-infobars',
                '--disable-session-crashed-bubble'
            ],
            ignore_default_args=['--enable-automation']
        )
        
        self.page = self.browser.pages[0] if self.browser.pages else self.browser.new_page()
        self.page.goto("https://www.tiktok.com")
        
        # 3. Thủ thuật tìm Window Handle
        unique_id = uuid.uuid4().hex
        self.page.evaluate(f"document.title = '{unique_id}'")
        
        # Gửi ID về Main Thread
        self.title_ready.emit(unique_id)
        
        # Giữ luồng chạy liên tục để trình duyệt không bị tắt
        while not self.isInterruptionRequested():
            self.page.wait_for_timeout(100)

    def stop(self):
        self.requestInterruption()
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
        self.wait()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Playwright Embed MRE")
        self.resize(1024, 768)
        
        main_widget = QWidget()
        layout = QVBoxLayout(main_widget)
        
        self.btn_start = QPushButton("Khởi động & Nhúng Chrome")
        self.btn_start.clicked.connect(self.start_browser)
        layout.addWidget(self.btn_start)
        
        self.status = QLabel("Trạng thái: Chưa chạy")
        layout.addWidget(self.status)
        
        # Container để chứa Chrome
        self.browser_container = QWidget()
        self.browser_container.setStyleSheet("background-color: #222;")
        layout.addWidget(self.browser_container, stretch=1)
        
        self.setCentralWidget(main_widget)
        self.worker = None

    def start_browser(self):
        self.btn_start.setEnabled(False)
        self.status.setText("Trạng thái: Đang khởi chạy Playwright (Background Thread)...")
        
        self.worker = BrowserWorker()
        self.worker.title_ready.connect(self.embed_browser)
        self.worker.start()

    def embed_browser(self, unique_id):
        self.status.setText(f"Trạng thái: Đang tìm HWND với UUID: {unique_id}...")
        
        chrome_hwnd = 0
        timeout = 100  # Chờ tối đa ~10 giây
        
        # 4. Vòng lặp kết hợp processEvents để không đơ UI
        while timeout > 0:
            QApplication.processEvents()
            chrome_hwnd = win32gui.FindWindow(None, unique_id)
            if chrome_hwnd != 0:
                break
            time.sleep(0.1)
            timeout -= 1
            
        if chrome_hwnd == 0:
            self.status.setText("Trạng thái: Lỗi - Không tìm thấy cửa sổ Chrome!")
            self.btn_start.setEnabled(True)
            return
            
        self.status.setText("Trạng thái: Đã bắt được HWND. Đang nhúng...")
        
        container_hwnd = int(self.browser_container.winId())
        
        # Win32 API: Lấy style hiện tại và Cắt viền
        style = win32gui.GetWindowLong(chrome_hwnd, win32con.GWL_STYLE)
        style &= ~win32con.WS_CAPTION
        style &= ~win32con.WS_THICKFRAME
        style &= ~win32con.WS_POPUP
        style |= win32con.WS_CHILD
        win32gui.SetWindowLong(chrome_hwnd, win32con.GWL_STYLE, style)
        
        # Win32 API: Đặt parent
        win32gui.SetParent(chrome_hwnd, container_hwnd)
        
        # Win32 API: Phóng to lấp đầy container
        rect = self.browser_container.rect()
        win32gui.MoveWindow(chrome_hwnd, 0, 0, rect.width(), rect.height(), True)
        
        self.status.setText("Trạng thái: Nhúng hoàn tất!")
        self.chrome_hwnd = chrome_hwnd

    def resizeEvent(self, event):
        """Giữ cho Chrome luôn tự động co giãn theo giao diện Qt"""
        super().resizeEvent(event)
        if hasattr(self, 'chrome_hwnd') and self.chrome_hwnd:
            rect = self.browser_container.rect()
            win32gui.MoveWindow(self.chrome_hwnd, 0, 0, rect.width(), rect.height(), True)

    def closeEvent(self, event):
        """Đảm bảo tắt Playwright sạch sẽ khi tắt app"""
        if self.worker:
            self.worker.stop()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
