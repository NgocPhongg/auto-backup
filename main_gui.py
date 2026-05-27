import sys
import time
import asyncio
import html
import json
import os
import inspect
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QSplitter, QTableWidget, QTableWidgetItem, QPushButton, 
    QLineEdit, QComboBox, QLabel, QMenuBar, QMenu, QStatusBar,
    QHeaderView, QAbstractItemView, QAction, QTextEdit, QFrame, QMessageBox, QDialog, QStackedWidget,
    QCheckBox, QFileDialog, QTextBrowser
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QSize, QProcess, QTimer
from PyQt5.QtGui import QIcon, QColor, QFont, QBrush, QPixmap, QPainter, QPen

# Nhúng các module backend
from account_manager import AccountManager
from multi_downloader import MultiDownloader
from monetization_manager import scrape_account_financial_info
from automation_dashboard import AutomationDashboard
from gologin_config import load_gologin_settings, save_gologin_settings, mask_secret
from video_table_manager import VideoTableManager
from add_profile_dialog import AddProfileDialog
from add_multiple_dialog import AddMultipleDialog
from app_paths import data_file, gologin_profiles_root, init_app_data, named_browser_profile_dir, require_orbita_browser_exe

# Disable gologin import for now
# from kyc_manager import auto_upload_kyc

# --- GENERIC WORKER THREAD ---
class GenericWorker(QThread):
    """Worker đa năng, có thể chạy cả hàm đồng bộ và bất đồng bộ"""
    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, object, object) # success, result, extra_data

    def __init__(self, target_func, extra_data=None, pass_progress=False, *args, **kwargs):
        super().__init__()
        self.target_func = target_func
        self.extra_data = extra_data
        self.pass_progress = pass_progress
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            self.progress.emit(f"Đang chuẩn bị tác vụ...")
            kwargs = dict(self.kwargs)
            if self.pass_progress:
                kwargs['progress_callback'] = self.progress.emit

            if inspect.iscoroutinefunction(self.target_func):
                # Chạy hàm async
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(self.target_func(*self.args, **kwargs))
                loop.close()
            else:
                # Chạy hàm sync
                result = self.target_func(*self.args, **kwargs)
            self.finished.emit(True, result, self.extra_data)
        except Exception as e:
            self.finished.emit(False, str(e), self.extra_data)


class InboxDialog(QDialog):
    def __init__(self, messages, profile_name="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Hộp thư hệ thống")
        self.resize(400, 500)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        title = QLabel("Hộp thư hệ thống 24h qua")
        title.setFont(QFont("Segoe UI", 12, QFont.Bold))
        layout.addWidget(title)

        if profile_name:
            profile_label = QLabel(str(profile_name))
            profile_label.setStyleSheet("color: #64748b;")
            layout.addWidget(profile_label)

        inbox_view = QTextBrowser()
        inbox_view.setOpenExternalLinks(False)
        inbox_view.setStyleSheet(
            "QTextBrowser { background: #f8fafc; border: 1px solid #cbd5e1; "
            "border-radius: 6px; padding: 10px; font-family: 'Segoe UI'; font-size: 13px; }"
        )
        if messages:
            body = "".join(
                f"<p style='margin: 0 0 12px 0; line-height: 1.45;'>{html.escape(str(msg))}</p>"
                for msg in messages
            )
            inbox_view.setHtml(body)
        else:
            inbox_view.setPlainText("Không có tin nhắn trong 24 giờ qua.")
        layout.addWidget(inbox_view)

        btn_close = QPushButton("Đóng")
        btn_close.clicked.connect(self.accept)
        btn_close.setStyleSheet(
            "QPushButton { background: #334155; color: white; font-weight: bold; "
            "border: none; border-radius: 4px; padding: 7px 14px; }"
            "QPushButton:hover { background: #1e293b; }"
        )
        layout.addWidget(btn_close, alignment=Qt.AlignRight)


class GoLoginSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cài đặt GoLogin API")
        self.resize(520, 230)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.settings = load_gologin_settings()

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("GoLogin API Key:"))
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setPlaceholderText("Dán API key GoLogin tại đây")
        self.api_key_input.setText(self.settings.get("api_key", ""))
        layout.addWidget(self.api_key_input)

        self.use_cloud_chk = QCheckBox("Dùng GoLogin cloud khi profile có GoLogin Profile ID")
        self.use_cloud_chk.setChecked(bool(self.settings.get("use_gologin_cloud", False)))
        layout.addWidget(self.use_cloud_chk)

        layout.addWidget(QLabel("Tên thư mục/folder GoLogin:"))
        self.folder_input = QLineEdit()
        self.folder_input.setPlaceholderText("Ví dụ: Hoang Phong_100")
        self.folder_input.setText(self.settings.get("gologin_folder_name", ""))
        layout.addWidget(self.folder_input)

        self.status_label = QLabel("")
        if self.settings.get("api_key"):
            self.status_label.setText(f"Đang có key: {mask_secret(self.settings.get('api_key'))}")
        layout.addWidget(self.status_label)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_cancel = QPushButton("Hủy")
        btn_cancel.clicked.connect(self.reject)
        btn_save = QPushButton("Lưu")
        btn_save.clicked.connect(self.accept)
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_save)
        layout.addLayout(btn_layout)

    def get_settings(self):
        return {
            "api_key": self.api_key_input.text().strip(),
            "use_gologin_cloud": self.use_cloud_chk.isChecked(),
            "gologin_folder_name": self.folder_input.text().strip(),
        }

def check_proxy_live(proxy_string, proxy_type="http"):
    """
    Kiểm tra xem proxy có hoạt động hay không.
    Trả về: (True, "IP thật") nếu sống, (False, "Lỗi") nếu chết.
    Tự động thử cả HTTP và SOCKS5 nếu có lỗi.
    """
    import requests
    if not proxy_string.strip():
        return True, "Không dùng Proxy"

    parts = proxy_string.strip().split(":", 3)
    if len(parts) < 2:
        return False, "Sai định dạng Proxy. Cần ít nhất IP:Port"
    
    ip, port = parts[0], parts[1]
    user = pwd = ""
    if len(parts) >= 4:
        user, pwd = parts[2], parts[3]

    def test_protocol(ptype):
        if user and pwd:
            p_url = f"{ptype}://{user}:{pwd}@{ip}:{port}"
        else:
            p_url = f"{ptype}://{ip}:{port}"
            
        proxies = {
            "http": p_url,
            "https": p_url
        }
        try:
            res = requests.get("https://api.ipify.org?format=json", proxies=proxies, timeout=5)
            if res.status_code == 200:
                return True, res.json().get('ip', 'Unknown IP')
        except:
            pass
        return False, ""

    # Thử protocol do user chọn trước
    is_live, msg = test_protocol(proxy_type)
    if is_live:
        return True, msg
        
    # Nếu thất bại, thử protocol còn lại
    other_type = "socks5" if proxy_type == "http" else "http"
    is_live, msg = test_protocol(other_type)
    if is_live:
        return True, f"{msg} (Auto-detected as {other_type.upper()})"
        
    return False, "Proxy không phản hồi, đã chết hoặc sai thông tin Auth!"


def detect_proxy_type_from_check_message(default_type, message):
    """Return proxy type, honoring check_proxy_live auto-detect result."""
    text = (message or "").lower()
    if "auto-detected as http" in text:
        return "http"
    if "auto-detected as socks5" in text:
        return "socks5"
    return (default_type or "http").lower()


def sync_profile_data_from_table_columns(profile_data, columns):
    """Sync editable table columns back into hidden profile_data before launching workers."""
    profile_data = dict(profile_data or {})
    columns = columns or {}
    old_proxy = (profile_data.get("proxy") or "").strip()

    table_to_profile = {
        "1": "ten_ho_so",
        "3": "proxy",
        "4": "browser_id",
        "10": "username",
        "13": "cookie",
        "20": "note",
    }
    for col_key, data_key in table_to_profile.items():
        if col_key in columns:
            profile_data[data_key] = columns[col_key]
        elif data_key == "proxy":
            profile_data["proxy"] = ""

    new_proxy = (profile_data.get("proxy") or "").strip()
    if "3" in columns and new_proxy and new_proxy != old_proxy:
        if "://" in new_proxy:
            profile_data["proxy_type"] = new_proxy.split("://", 1)[0].strip().lower()
        else:
            # Direct table edits have no proxy-type radio, so default to the app's HTTP proxy path.
            profile_data["proxy_type"] = "http"

    return profile_data


def make_fingerprint_icon(color="#2563eb"):
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color), 1.8)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)

    painter.drawArc(5, 4, 14, 17, 25 * 16, 295 * 16)
    painter.drawArc(7, 6, 10, 14, 35 * 16, 285 * 16)
    painter.drawArc(9, 8, 6, 10, 55 * 16, 250 * 16)
    painter.drawArc(3, 8, 18, 12, 200 * 16, 125 * 16)
    painter.drawLine(12, 13, 12, 20)
    painter.drawArc(8, 14, 8, 7, 200 * 16, 140 * 16)
    painter.end()

    return QIcon(pixmap)


# --- MAIN GUI ---
class SSMAToolGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        init_app_data()
        self.setWindowTitle("DNPTool Reup tiktok")
        self.resize(1300, 850)
        
        # Khởi tạo các module backend
        self.acc_manager = AccountManager()
        self.downloader = MultiDownloader()
        # Quản lý Video Table
        self.video_manager = VideoTableManager(self)
        self.automation_dashboard = None
        self.automation_dashboards = {}
        self.running_profiles = {}
        # Giữ ref worker để không bị garbage collected và tự dọn khi worker xong.
        self.active_workers = []

        # Build UI
        self.init_ui()
        self.apply_styles()
        
        # Load dữ liệu tài khoản từ file DB
        self.db_file = str(data_file("accounts_data.json", []))
        self.projects_file = str(data_file("projects.json", []))
        self.load_accounts_from_db()
        
        self.load_projects()

    def init_ui(self):
        # 1. Menu Bar
        menubar = self.menuBar()
        menu_api = menubar.addMenu('API / Cookie')
        action_gologin_api = menu_api.addAction("Khóa API GoLogin")
        action_gologin_api.triggered.connect(self.open_gologin_settings)
        
        menu_nuoi = menubar.addMenu('Cài đặt nuôi tài khoản')
        action_dashboard = menu_nuoi.addAction("Mở bảng theo dõi")
        action_dashboard.triggered.connect(self.open_automation_dashboard)
        
        menubar.addMenu('Cài đặt trình duyệt')
        menubar.addMenu('Hướng dẫn sử dụng')
        menubar.addMenu('Lỗi thường gặp')
        menubar.addMenu('Tiện ích')
        menu_browser_cleanup = menubar.addMenu('Dọn trình duyệt')
        action_cleanup_browsers = menu_browser_cleanup.addAction("Đóng các phiên trình duyệt đang chạy")
        action_cleanup_browsers.triggered.connect(self.handle_cleanup_browser_sessions)
        action_clear_log = menu_browser_cleanup.addAction("Xóa log giao diện")
        action_clear_log.triggered.connect(self.clear_log_output)
        
        menu_dangky = menubar.addMenu('Đăng ký')
        action_mo_dang_ky = menu_dangky.addAction("Mở bảng đăng ký")
        action_mo_dang_ky.triggered.connect(lambda: self.stacked_widget.setCurrentWidget(self.page_register))
        action_quay_lai = menu_dangky.addAction("Quay lại quản lý")
        action_quay_lai.triggered.connect(lambda: self.stacked_widget.setCurrentWidget(self.page_main))
        
        action_edit_video = menubar.addAction('EDIT_VIDEO')
        action_edit_video.triggered.connect(self.open_edit_video_tool)
        action_creator_now = menubar.addAction('CUT VIDEO')
        action_creator_now.triggered.connect(self.open_creator_now_tool)

        # 2. Status Bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage('Sẵn sàng | Đang chờ tác vụ...')

        # 3. Central Widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(5, 5, 5, 5)

        # Sử dụng QStackedWidget để chuyển đổi giữa các màn hình
        self.stacked_widget = QStackedWidget()
        main_layout.addWidget(self.stacked_widget)

        # ==========================================
        # TRANG 1: QUẢN LÝ TÀI KHOẢN (MAIN)
        # ==========================================
        self.page_main = QWidget()
        page_main_layout = QVBoxLayout(self.page_main)
        page_main_layout.setContentsMargins(0, 0, 0, 0)
        
        self.main_splitter = QSplitter(Qt.Vertical)
        page_main_layout.addWidget(self.main_splitter)

        # --- PHẦN TRÊN: QUẢN LÝ TÀI KHOẢN ---
        self.setup_account_section()

        # --- PHẦN DƯỚI: QUẢN LÝ VIDEO & LOG ---
        self.setup_bottom_section()
        
        self.stacked_widget.addWidget(self.page_main)

        # ==========================================
        # TRANG 2: BẢNG ĐĂNG KÝ
        # ==========================================
        self.page_register = QWidget()
        self.setup_register_section()
        self.stacked_widget.addWidget(self.page_register)

    def setup_account_section(self):
        acc_container = QWidget()
        acc_container.setObjectName("accountSection")
        layout = QVBoxLayout(acc_container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Toolbar Tài khoản
        toolbar_frame = QFrame()
        toolbar_frame.setObjectName("accountToolbar")
        toolbar = QHBoxLayout(toolbar_frame)
        toolbar.setContentsMargins(8, 6, 8, 6)
        toolbar.setSpacing(6)
        toolbar.addWidget(QLabel("Dự án:"))
        self.proj_combo = QComboBox()
        self.proj_combo.setMinimumWidth(150)
        self.proj_combo.currentTextChanged.connect(self.filter_by_project)
        toolbar.addWidget(self.proj_combo)
        
        btn_refresh_proj = QPushButton("↻")
        btn_refresh_proj.setFixedSize(30, 30)
        btn_refresh_proj.setProperty("variant", "icon")
        btn_refresh_proj.setToolTip("Tải lại danh sách dự án")
        btn_refresh_proj.clicked.connect(self.load_projects)
        toolbar.addWidget(btn_refresh_proj)
        
        btn_add_proj = QPushButton("+")
        btn_add_proj.setFixedSize(30, 30)
        btn_add_proj.setProperty("variant", "iconAccent")
        btn_add_proj.setToolTip("Tạo dự án mới")
        btn_add_proj.clicked.connect(self.handle_add_project)
        toolbar.addWidget(btn_add_proj)

        btn_delete_proj = QPushButton("X")
        btn_delete_proj.setToolTip("Xóa dự án đang chọn")
        btn_delete_proj.clicked.connect(self.handle_delete_project)
        btn_delete_proj.setFixedSize(30, 30)
        btn_delete_proj.setProperty("variant", "iconDanger")
        toolbar.addWidget(btn_delete_proj)

        btn_recycle = QPushButton()
        btn_recycle.setIcon(make_fingerprint_icon())
        btn_recycle.setIconSize(QSize(18, 18))
        btn_recycle.setFixedSize(30, 30)
        btn_recycle.setProperty("variant", "icon")
        btn_recycle.setToolTip("Tái chế profile: xóa dữ liệu và làm mới vân tay GoLogin")
        btn_recycle.clicked.connect(self.handle_recycle_profile)
        toolbar.addWidget(btn_recycle)
        toolbar.addStretch()
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Tìm id|profile|ghi chú|proxy...")
        self.search_input.setMinimumWidth(300)
        self.search_input.setMaximumWidth(430)
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self.apply_account_filters)
        toolbar.addWidget(self.search_input)

        btn_add_1 = QPushButton("Thêm 1 profile")
        btn_add_1.setProperty("variant", "secondary")
        btn_add_1.clicked.connect(self.open_add_profile_dialog)
        toolbar.addWidget(btn_add_1)
        
        btn_add_multi = QPushButton("Thêm nhiều")
        btn_add_multi.setProperty("variant", "secondary")
        btn_add_multi.clicked.connect(self.open_add_multiple_dialog)
        toolbar.addWidget(btn_add_multi)
        btn_show_all = QPushButton("Hiện tất cả")
        btn_show_all.setProperty("variant", "ghost")
        btn_show_all.setToolTip("Xóa bộ lọc và hiện tất cả profile")
        btn_show_all.clicked.connect(self.reset_account_filters)
        toolbar.addWidget(btn_show_all)
        
        btn_edit1_folder = QPushButton("📂 EDIT_VIDEO")
        btn_edit1_folder.setProperty("variant", "primary")
        btn_edit1_folder.setToolTip("Mở thư mục EDIT_1")
        btn_edit1_folder.clicked.connect(self.open_edit1_folder)
        toolbar.addWidget(btn_edit1_folder)

        btn_creator_now = QPushButton("CUT VIDEO")
        btn_creator_now.setObjectName("creatorNowButton")
        btn_creator_now.setMinimumWidth(122)
        btn_creator_now.setProperty("variant", "creatorNow")
        btn_creator_now.setToolTip("Mở Creator Now Studio")
        btn_creator_now.clicked.connect(self.open_creator_now_tool)
        toolbar.addWidget(btn_creator_now)
        layout.addWidget(toolbar_frame)

        summary_frame = QFrame()
        summary_frame.setObjectName("accountSummaryBar")
        summary_bar = QHBoxLayout(summary_frame)
        summary_bar.setContentsMargins(8, 0, 8, 4)
        summary_bar.setSpacing(6)
        self.account_summary_labels = {}
        for key, title in [
            ("total", "Tổng"),
            ("visible", "Đang hiện"),
            ("selected", "Đã chọn"),
            ("logged", "Đã login"),
            ("error", "Lỗi"),
            ("captcha", "CAPTCHA"),
        ]:
            label = QLabel(f"{title}: 0")
            label.setProperty("role", "summary")
            label.setMinimumHeight(24)
            self.account_summary_labels[key] = label
            summary_bar.addWidget(label)
        summary_bar.addStretch()
        layout.addWidget(summary_frame)

        # Bảng Tài khoản
        self.acc_table = QTableWidget()
        self.headers = [
            'Avatar', 'Profile', 'Logged', 'Proxy', 'GoLogin ID', 'TikTok ID',
            'Views(30d)', 'Follows', 'Views', 'Videos', 'Email',
            'Upload Folder', 'Status', 'Cookie', '$Earned', '$Balance',
            'C$', 'RPM', 'QG', 'KYC', 'Note', 'Re.Apply', 'Privacy',
            'VAT', 'TS', 'STT', 'VVP', 'Payout', 'Channel Status'
        ]
        self.channel_status_col = self.headers.index('Channel Status')
        self.acc_table.setColumnCount(len(self.headers))
        self.acc_table.setHorizontalHeaderLabels(self.headers)
        self.acc_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.acc_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.acc_table.setAlternatingRowColors(True)
        self.acc_table.setWordWrap(False)
        self.acc_table.setShowGrid(False)
        self.acc_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.acc_table.customContextMenuRequested.connect(self.show_acc_context_menu)
        self.acc_table.doubleClicked.connect(self.handle_edit_profile) # Click đúp để sửa
        self.acc_table.verticalHeader().setDefaultSectionSize(30)
        self.acc_table.verticalHeader().setMinimumSectionSize(28)
        self.acc_table.selectionModel().selectionChanged.connect(self.update_account_summary)
        
        # Ẩn các cột rác/không dùng tới để làm gọn giao diện
        # (Ẩn từ $Earned đến Payout, nhưng vẫn giữ lại cột Note=20 và STT=25)
        cols_to_hide = [14, 15, 16, 17, 18, 19, 21, 22, 23, 24, 26, 27]
        for col in cols_to_hide:
            self.acc_table.setColumnHidden(col, True)
        
        header = self.acc_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setDefaultAlignment(Qt.AlignLeft)
        header.setMinimumSectionSize(52)
        header.setStretchLastSection(False)
        channel_visual = header.visualIndex(self.channel_status_col)
        status_visual = header.visualIndex(12)
        if channel_visual >= 0 and status_visual >= 0:
            header.moveSection(channel_visual, status_visual + 1)
        account_widths = {
            0: 44, 1: 210, 2: 150, 3: 160, 4: 170, 5: 130,
            6: 92, 7: 96, 8: 92, 9: 86, 10: 190, 11: 145,
            12: 150, 13: 220, 20: 180, 25: 55, self.channel_status_col: 180,
        }
        for col, width in account_widths.items():
            if 0 <= col < self.acc_table.columnCount():
                self.acc_table.setColumnWidth(col, width)

        layout.addWidget(self.acc_table)
        self.main_splitter.addWidget(acc_container)
        self.main_splitter.setStretchFactor(0, 3)

    def setup_bottom_section(self):
        bottom_container = QWidget()
        bottom_container.setObjectName("bottomSection")
        layout = QVBoxLayout(bottom_container)
        layout.setContentsMargins(0, 0, 0, 0)

        bottom_splitter = QSplitter(Qt.Horizontal)
        self.bottom_splitter = bottom_splitter
        bottom_splitter.setChildrenCollapsible(False)
        
        # --- Bảng Video ---
        video_widget = QWidget()
        video_widget.setObjectName("videoPanel")
        v_layout = QVBoxLayout(video_widget)
        v_layout.setContentsMargins(8, 6, 8, 8)

        v_toolbar = QHBoxLayout()
        v_toolbar.setSpacing(6)
        video_title = QLabel("Bảng Video")
        video_title.setProperty("role", "sectionTitle")
        v_toolbar.addWidget(video_title)
        v_toolbar.addStretch()
        
        btn_download = QPushButton("Tải xuống")
        btn_download.setProperty("variant", "secondary")
        btn_download.clicked.connect(self.handle_download_video)
        v_toolbar.addWidget(btn_download)
        
        v_btns = ['Dừng', 'Lên lịch', 'Xuất', 'Nhập', 'Xóa bảng', 'Đặt lại thứ tự']
        for text in v_btns:
            btn = QPushButton(text)
            btn.setProperty("variant", "ghost")
            if text == 'Xóa bảng':
                btn.setProperty("variant", "danger")
                btn.clicked.connect(self.video_manager.handle_clear_video_table)
            elif text == 'Lên lịch':
                btn.setProperty("variant", "secondary")
                btn.clicked.connect(self.video_manager.handle_schedule_videos)
            v_toolbar.addWidget(btn)
        
        v_layout.addLayout(v_toolbar)

        self.video_table = QTableWidget()
        v_headers = ['Tiêu đề', 'Thời lượng', 'Trạng thái', 'Upload tới', 'Tình trạng', 'Nguồn', 'Link gốc', 'Video ID']
        self.video_table.setColumnCount(len(v_headers))
        self.video_table.setHorizontalHeaderLabels(v_headers)
        self.video_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.video_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.video_table.setAlternatingRowColors(True)
        self.video_table.setShowGrid(False)
        self.video_table.setWordWrap(False)
        self.video_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.video_table.customContextMenuRequested.connect(self.video_manager.show_video_context_menu)
        v_layout.addWidget(self.video_table)

        bottom_splitter.addWidget(video_widget)

        # --- Console Log ---
        log_widget = QWidget()
        log_widget.setObjectName("logPanel")
        l_layout = QVBoxLayout(log_widget)
        l_layout.setContentsMargins(8, 6, 8, 8)
        log_toolbar = QHBoxLayout()
        log_toolbar.setSpacing(6)
        log_title = QLabel("Nhật ký hoạt động")
        log_title.setProperty("role", "sectionTitle")
        log_toolbar.addWidget(log_title)
        log_toolbar.addStretch()
        btn_clear_log = QPushButton("Xóa log")
        btn_clear_log.setProperty("variant", "ghost")
        btn_clear_log.clicked.connect(self.clear_log_output)
        log_toolbar.addWidget(btn_clear_log)
        l_layout.addLayout(log_toolbar)
        
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMinimumWidth(320)
        self.log_output.document().setMaximumBlockCount(3000)
        self.log_output.setStyleSheet(
            "background-color: #171717; color: #e5e7eb; font-family: 'Consolas'; "
            "font-size: 11px; border: 1px solid #27272a; border-radius: 4px; padding: 6px;"
        )
        l_layout.addWidget(self.log_output)
        
        bottom_splitter.addWidget(log_widget)
        bottom_splitter.setStretchFactor(0, 3)
        bottom_splitter.setStretchFactor(1, 1)
        bottom_splitter.setSizes([920, 360])
        layout.addWidget(bottom_splitter)
        self.main_splitter.addWidget(bottom_container)
        self.main_splitter.setStretchFactor(1, 2)
        self.main_splitter.setSizes([500, 300])

    def setup_register_section(self):
        """Thiết lập giao diện cho bảng Đăng Ký (lấy mã OTP Hotmail)"""
        layout = QVBoxLayout(self.page_register)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # Tiêu đề & Nút bấm
        toolbar = QHBoxLayout()
        title_lbl = QLabel("BẢNG ĐĂNG KÝ - OTP HOTMAIL")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title_lbl.setFont(title_font)
        toolbar.addWidget(title_lbl)
        
        toolbar.addStretch()
        
        # Ô nhập từ khóa lọc mail
        toolbar.addWidget(QLabel("Từ khóa:"))
        self.otp_keyword_input = QLineEdit()
        self.otp_keyword_input.setText("tiktok")
        self.otp_keyword_input.setPlaceholderText("VD: tiktok, microsoft, google...")
        self.otp_keyword_input.setMinimumWidth(150)
        self.otp_keyword_input.setMaximumWidth(200)
        self.otp_keyword_input.setToolTip("Từ khóa lọc người gửi email (mặc định: tiktok)")
        toolbar.addWidget(self.otp_keyword_input)
        
        toolbar.addWidget(QLabel("  "))  # Spacer
        
        self.btn_add_reg = QPushButton("Thêm Email")
        self.btn_add_reg.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 6px 15px; border-radius: 4px;")
        toolbar.addWidget(self.btn_add_reg)
        
        self.btn_get_otp = QPushButton("Bắt đầu lấy mã")
        self.btn_get_otp.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; padding: 6px 15px; border-radius: 4px;")
        toolbar.addWidget(self.btn_get_otp)
        
        self.btn_stop_otp = QPushButton("Dừng")
        self.btn_stop_otp.setStyleSheet("background-color: #FF9800; color: white; font-weight: bold; padding: 6px 15px; border-radius: 4px;")
        self.btn_stop_otp.setEnabled(False)
        self.btn_stop_otp.setToolTip("Dừng tất cả tác vụ lấy OTP đang chạy")
        toolbar.addWidget(self.btn_stop_otp)
        
        self.btn_del_reg = QPushButton("Xóa dòng chọn")
        self.btn_del_reg.setStyleSheet("background-color: #f44336; color: white; font-weight: bold; padding: 6px 15px; border-radius: 4px;")
        toolbar.addWidget(self.btn_del_reg)
        
        layout.addLayout(toolbar)
        
        # Bảng Đăng Ký
        self.reg_table = QTableWidget()
        reg_headers = ['STT', 'Email Hotmail', 'Mật khẩu', 'Refresh Token', 'Client ID', 'OTP', 'Trạng thái']
        self.reg_table.setColumnCount(len(reg_headers))
        self.reg_table.setHorizontalHeaderLabels(reg_headers)
        
        # Cấu hình độ rộng cột: Trạng thái rộng nhất để đọc lỗi
        header = self.reg_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        self.reg_table.setColumnWidth(0, 40)    # STT
        self.reg_table.setColumnWidth(1, 220)   # Email
        self.reg_table.setColumnWidth(2, 100)   # Mật khẩu
        self.reg_table.setColumnWidth(3, 150)   # Refresh Token
        self.reg_table.setColumnWidth(4, 130)   # Client ID
        self.reg_table.setColumnWidth(5, 80)    # Mã OTP
        header.setSectionResizeMode(6, QHeaderView.Stretch)  # Trạng thái — chiếm hết phần còn lại
        
        self.reg_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.reg_table.setAlternatingRowColors(True)
        
        layout.addWidget(self.reg_table)

        # Biến quản lý worker OTP
        self._otp_workers = []        # Danh sách worker đang chạy
        self._otp_stop_flag = False    # Cờ dừng
        self._otp_running_count = 0    # Số worker đang chạy
        self._otp_total_count = 0      # Tổng số dòng cần xử lý
        self._otp_done_count = 0       # Số đã xong
        self._otp_success_count = 0    # Số lấy mã thành công

        # Kết nối sự kiện
        self.btn_add_reg.clicked.connect(self.handle_add_reg_emails)
        self.btn_del_reg.clicked.connect(self.handle_delete_reg_emails)
        self.btn_get_otp.clicked.connect(self.handle_get_otp_batch)
        self.btn_stop_otp.clicked.connect(self.handle_stop_otp)

        # File lưu dữ liệu bảng Đăng Ký
        self._reg_data_file = str(data_file("email_accounts.json", []))

        # Load dữ liệu đã lưu
        self._load_reg_table()

    def _parse_reg_email_line(self, line):
        """Parse one email row from common pasted formats."""
        import re

        text = (line or "").strip()
        if not text:
            return None

        if "|" in text:
            parts = text.split("|")
        elif "\t" in text:
            parts = text.split("\t")
        else:
            parts = re.split(r"\s+-\s+", text, maxsplit=3)

        parts = [part.strip() for part in parts]
        while len(parts) < 4:
            parts.append("")

        return {
            "email": parts[0],
            "password": parts[1],
            "refresh_token": parts[2],
            "client_id": parts[3],
        }

    def _normalize_reg_email_entry(self, entry):
        """Repair rows saved with the whole pasted line in the email field."""
        entry = entry or {}
        email = (entry.get("email") or "").strip()
        password = (entry.get("password") or "").strip()
        refresh_token = (entry.get("refresh_token") or "").strip()
        client_id = (entry.get("client_id") or "").strip()

        if email and not any((password, refresh_token, client_id)):
            parsed = self._parse_reg_email_line(email)
            if parsed and parsed.get("email"):
                return parsed

        return {
            "email": email,
            "password": password,
            "refresh_token": refresh_token,
            "client_id": client_id,
        }

    def _append_reg_email_row(self, entry):
        email = (entry.get("email") or "").strip()
        if not email:
            return False

        row = self.reg_table.rowCount()
        self.reg_table.insertRow(row)
        self.reg_table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
        self.reg_table.setItem(row, 1, QTableWidgetItem(email))
        self.reg_table.setItem(row, 2, QTableWidgetItem(entry.get("password", "")))
        self.reg_table.setItem(row, 3, QTableWidgetItem(entry.get("refresh_token", "")))
        self.reg_table.setItem(row, 4, QTableWidgetItem(entry.get("client_id", "")))
        self.reg_table.setItem(row, 5, QTableWidgetItem(""))
        self.reg_table.setItem(row, 6, QTableWidgetItem("Sẵn sàng"))
        return True

    def handle_add_reg_emails(self):
        """Mở dialog cho phép nhập nhiều dòng (mỗi dòng: email|pass|refresh_token|client_id)"""
        dialog = QDialog(self)
        dialog.setWindowTitle("Thêm Email Hotmail")
        dialog.resize(550, 300)
        
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(
            "Nhập danh sách định dạng:\n"
            "Email|Password\n"
            "hoặc Email|Password|Refresh_Token\n"
            "hoặc Email|Password|Refresh_Token|Client_ID\n"
            "hoặc Email - Password - Refresh_Token - Client_ID"
        ))
        
        txt_input = QTextEdit()
        layout.addWidget(txt_input)
        
        btn_layout = QHBoxLayout()
        btn_ok = QPushButton("Thêm")
        btn_cancel = QPushButton("Hủy")
        btn_layout.addWidget(btn_ok)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)
        
        btn_cancel.clicked.connect(dialog.reject)
        
        def process_input():
            text = txt_input.toPlainText().strip()
            if not text:
                dialog.reject()
                return
            
            lines = text.split('\n')
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                    
                entry = self._parse_reg_email_line(line)
                if entry:
                    self._append_reg_email_row(entry)
                
            dialog.accept()
            self._save_reg_table()  # Auto-save sau khi thêm
            
        btn_ok.clicked.connect(process_input)
        dialog.exec_()

    def handle_delete_reg_emails(self):
        """Xóa các dòng đang chọn trong bảng Đăng Ký"""
        selected_rows = set(item.row() for item in self.reg_table.selectedItems())
        for row in sorted(selected_rows, reverse=True):
            self.reg_table.removeRow(row)
            
        # Cập nhật lại cột STT
        for i in range(self.reg_table.rowCount()):
            self.reg_table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
        self._save_reg_table()  # Auto-save sau khi xóa

    # ================= LƯU / LOAD BẢNG ĐĂNG KÝ =================

    def _save_reg_table(self):
        """Lưu toàn bộ dữ liệu bảng Đăng Ký ra file JSON."""
        try:
            data = []
            for row in range(self.reg_table.rowCount()):
                entry = {
                    "email": (self.reg_table.item(row, 1).text() if self.reg_table.item(row, 1) else ""),
                    "password": (self.reg_table.item(row, 2).text() if self.reg_table.item(row, 2) else ""),
                    "refresh_token": (self.reg_table.item(row, 3).text() if self.reg_table.item(row, 3) else ""),
                    "client_id": (self.reg_table.item(row, 4).text() if self.reg_table.item(row, 4) else ""),
                }
                if entry["email"]:  # Chỉ lưu dòng có email
                    data.append(entry)

            import json
            with open(self._reg_data_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Save] Lỗi lưu bảng Đăng Ký: {e}")

    def _load_reg_table(self):
        """Load dữ liệu bảng Đăng Ký từ file JSON khi khởi động."""
        import json
        try:
            if not os.path.exists(self._reg_data_file):
                return

            with open(self._reg_data_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if not isinstance(data, list):
                return

            repaired = False
            loaded_count = 0
            for entry in data:
                normalized = self._normalize_reg_email_entry(entry)
                if not normalized.get("email"):
                    continue
                if normalized != {
                    "email": (entry.get("email") or "").strip(),
                    "password": (entry.get("password") or "").strip(),
                    "refresh_token": (entry.get("refresh_token") or "").strip(),
                    "client_id": (entry.get("client_id") or "").strip(),
                }:
                    repaired = True
                if self._append_reg_email_row(normalized):
                    loaded_count += 1

            if repaired:
                self._save_reg_table()

            if data:
                self.log(f"Đã tải {loaded_count} email từ file đã lưu.")
        except Exception as e:
            print(f"[Load] Lỗi load bảng Đăng Ký: {e}")

    # ================= LẤY MÃ OTP HÀNG LOẠT =================

    def handle_get_otp_batch(self):
        """Xử lý khi nhấn 'Bắt đầu lấy mã' — chỉ chạy các dòng đang CHỌN."""
        from hotmail_otp import fetch_otp_from_email, validate_email_format

        # ── VALIDATE: Chọn dòng chưa ──
        selected_rows = sorted(set(item.row() for item in self.reg_table.selectedItems()))
        total_rows = len(selected_rows)
        if not selected_rows:
            QMessageBox.warning(
                self, "Chưa chọn tài khoản",
                "Hãy chọn (click) dòng cần lấy mã trước khi nhấn 'Bắt đầu lấy mã'."
            )
            return

        # ── VALIDATE: Đang chạy rồi ──
        if self._otp_running_count > 0:
            QMessageBox.information(
                self, "Đang xử lý",
                f"Còn {self._otp_running_count} email đang lấy mã.\n"
                f"Vui lòng chờ hoàn thành hoặc nhấn 'Dừng'."
            )
            return

        # Lấy từ khóa
        keyword = self.otp_keyword_input.text().strip()
        if not keyword:
            keyword = "tiktok"  # Mặc định

        # ── Duyệt các dòng ĐƯỢC CHỌN, pre-validate ──
        rows_to_process = []
        skip_count = 0

        for row in selected_rows:
            email_item = self.reg_table.item(row, 1)
            pass_item = self.reg_table.item(row, 2)
            rt_item = self.reg_table.item(row, 3)
            cid_item = self.reg_table.item(row, 4)

            email = (email_item.text() if email_item else "").strip()
            password = (pass_item.text() if pass_item else "").strip()
            refresh_token = (rt_item.text() if rt_item else "").strip()
            client_id = (cid_item.text() if cid_item else "").strip()

            # ── VALIDATE V2: Email trống ──
            if not email:
                self._set_reg_status(row, "⏭️ Bỏ qua — thiếu email", "#FF9800")
                skip_count += 1
                continue

            # ── VALIDATE V4: Email sai format ──
            if not validate_email_format(email):
                self._set_reg_status(row, "❌ Email không hợp lệ", "#f44336")
                skip_count += 1
                continue

            # ── VALIDATE V3: Thiếu cả password lẫn refresh_token ──
            if not password and not refresh_token:
                self._set_reg_status(row, "❌ Cần mật khẩu hoặc Refresh Token", "#f44336")
                skip_count += 1
                continue

            rows_to_process.append({
                "row": row,
                "email": email,
                "password": password,
                "refresh_token": refresh_token,
                "client_id": client_id,
            })

        if not rows_to_process:
            QMessageBox.warning(
                self, "Không có email hợp lệ",
                f"Tất cả {total_rows} dòng đều không đủ điều kiện.\n"
                f"Kiểm tra lại email và mật khẩu."
            )
            return

        # ── Xác nhận ──
        valid_count = len(rows_to_process)
        msg = f"Sẽ lấy mã OTP cho {valid_count} email"
        if skip_count > 0:
            msg += f" (bỏ qua {skip_count} dòng lỗi)"
        msg += f".\nTừ khóa tìm mail: '{keyword}'\n\nBắt đầu?"

        reply = QMessageBox.question(
            self, "Xác nhận lấy mã OTP", msg,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
        )
        if reply != QMessageBox.Yes:
            return

        # ── KHỞI ĐỘNG BATCH ──
        self._otp_stop_flag = False
        self._otp_workers = []
        self._otp_total_count = valid_count
        self._otp_done_count = 0
        self._otp_success_count = 0
        self._otp_running_count = valid_count

        # Disable nút để tránh click lại
        self.btn_get_otp.setEnabled(False)
        self.btn_get_otp.setText(f"Đang chạy (0/{valid_count})...")
        self.btn_add_reg.setEnabled(False)
        self.btn_del_reg.setEnabled(False)
        self.btn_stop_otp.setEnabled(True)

        self.log(f"🚀 Bắt đầu lấy mã OTP cho {valid_count} email (keyword: {keyword})")

        # ── Tạo worker cho từng dòng ──
        for item in rows_to_process:
            if self._otp_stop_flag:
                break

            row = item["row"]
            self._set_reg_status(row, "⏳ Đang chờ...", "#2196F3")

            # Tạo GenericWorker chạy hàm sync fetch_otp_from_email
            worker = GenericWorker(
                fetch_otp_from_email,
                extra_data=row,
                pass_progress=True,
                email=item["email"],
                password=item["password"],
                refresh_token=item["refresh_token"],
                client_id=item["client_id"],
                keyword=keyword,
                max_retries=3,
                wait_seconds=5,
            )

            # Kết nối signal → slot
            worker.progress.connect(lambda msg, r=row: self._on_otp_progress(r, msg))
            worker.finished.connect(self._on_otp_finished)

            self._otp_workers.append(worker)
            worker.start()

    def handle_stop_otp(self):
        """Dừng tất cả worker OTP đang chạy."""
        self._otp_stop_flag = True
        self.log("🛑 Đang dừng tất cả tác vụ lấy mã OTP...", "orange")

        # Terminate tất cả worker
        for worker in self._otp_workers:
            if worker.isRunning():
                worker.terminate()
                worker.wait(1000)  # Chờ tối đa 1s cho mỗi worker

        # Đánh dấu các dòng đang chạy
        for row in range(self.reg_table.rowCount()):
            status_item = self.reg_table.item(row, 6)
            if status_item:
                status_text = status_item.text()
                if "Đang" in status_text or "Chờ" in status_text:
                    self._set_reg_status(row, "🛑 Đã dừng bởi người dùng", "#FF9800")

        self._finish_otp_batch(cancelled=True)

    def _set_reg_status(self, row, text, color="#000000"):
        """Cập nhật cột 'Trạng thái' (col 6) cho dòng row với màu sắc."""
        status_item = QTableWidgetItem(text)
        status_item.setForeground(QColor(color))
        self.reg_table.setItem(row, 6, status_item)

    def _on_otp_progress(self, row, message):
        """Callback khi worker cập nhật tiến trình (realtime)."""
        if self._otp_stop_flag:
            return

        # Cập nhật trạng thái dòng tương ứng
        self._set_reg_status(row, message, "#2196F3")

        # Cuộn đến dòng đang xử lý
        self.reg_table.scrollToItem(
            self.reg_table.item(row, 6),
            QAbstractItemView.EnsureVisible
        )

    def _on_otp_finished(self, success, result, row):
        """Callback khi 1 worker hoàn thành (thành công hoặc lỗi).

        Xử lý tất cả trường hợp:
          - success=True, result là dict từ fetch_otp_from_email
          - success=False, result là str (exception message)
        """
        if self._otp_stop_flag:
            return

        self._otp_done_count += 1
        self._otp_running_count = max(0, self._otp_running_count - 1)

        # Cập nhật nút
        self.btn_get_otp.setText(
            f"Đang chạy ({self._otp_done_count}/{self._otp_total_count})..."
        )

        if not success:
            # ── Worker bị exception (crash) ──
            error_msg = str(result)[:100] if result else "Lỗi không xác định"
            self._set_reg_status(row, f"❌ Lỗi hệ thống: {error_msg}", "#f44336")
            self.log(f"❌ Dòng {row+1}: Lỗi hệ thống — {error_msg}", "red")

        elif isinstance(result, dict):
            status = result.get("status", "error")
            message = result.get("message", "")
            otp = result.get("otp", "")
            new_rt = result.get("new_refresh_token", "")

            email_item = self.reg_table.item(row, 1)
            email_text = email_item.text() if email_item else f"Dòng {row+1}"

            if status == "success" and otp:
                # ═══ THÀNH CÔNG: Lấy được mã OTP ═══
                self._otp_success_count += 1

                # Ghi mã OTP vào cột 5
                otp_item = QTableWidgetItem(otp)
                otp_item.setForeground(QColor("#4CAF50"))
                otp_font = QFont()
                otp_font.setBold(True)
                otp_font.setPointSize(12)
                otp_item.setFont(otp_font)
                self.reg_table.setItem(row, 5, otp_item)

                # Kiểm tra cảnh báo mã cũ
                if "hết hạn" in message:
                    self._set_reg_status(row, f"⚠️ Mã: {otp} (có thể hết hạn)", "#FF9800")
                else:
                    self._set_reg_status(row, f"✅ Thành công!", "#4CAF50")

                self.log(f"✅ {email_text}: OTP = {otp}", "#00ff00")

                # Cập nhật Refresh Token mới nếu có (P2)
                if new_rt:
                    self.reg_table.setItem(row, 3, QTableWidgetItem(new_rt))
                    self.log(f"🔄 {email_text}: Refresh Token đã cập nhật")
                    self._save_reg_table()  # Auto-save RT mới

            elif status == "no_mail":
                # ═══ KHÔNG TÌM THẤY MAIL ═══
                self._set_reg_status(row, f"📭 {message}", "#FF9800")
                self.log(f"📭 {email_text}: {message}", "orange")

                # Cập nhật RT nếu OAuth thành công nhưng không tìm thấy mail
                if new_rt:
                    self.reg_table.setItem(row, 3, QTableWidgetItem(new_rt))
                    self._save_reg_table()  # Auto-save RT mới

            else:
                # ═══ LỖI ═══
                # Phân loại lỗi để hiển thị icon phù hợp
                msg_lower = message.lower()

                if "sai mật khẩu" in msg_lower or "50126" in msg_lower:
                    icon = "🔑"
                elif "bị khóa" in msg_lower or "50053" in msg_lower or "disabled" in msg_lower:
                    icon = "🔒"
                elif "mfa" in msg_lower or "50076" in msg_lower or "50079" in msg_lower:
                    icon = "🛡️"
                elif "hết hạn" in msg_lower or "700082" in msg_lower or "expired" in msg_lower:
                    icon = "⏰"
                elif "thiếu" in msg_lower or "cần" in msg_lower:
                    icon = "📝"
                elif "kết nối" in msg_lower or "timeout" in msg_lower or "ssl" in msg_lower:
                    icon = "🌐"
                elif "consent" in msg_lower or "65001" in msg_lower:
                    icon = "⚙️"
                elif "app password" in msg_lower:
                    icon = "🔐"
                else:
                    icon = "❌"

                self._set_reg_status(row, f"{icon} {message}", "#f44336")
                self.log(f"❌ {email_text}: {message}", "red")

                # Vẫn cập nhật RT nếu có (trường hợp RT OK nhưng IMAP lỗi)
                if new_rt:
                    self.reg_table.setItem(row, 3, QTableWidgetItem(new_rt))
                    self._save_reg_table()  # Auto-save RT mới
        else:
            # ── Kết quả không đúng format (phòng thủ) ──
            self._set_reg_status(row, f"❌ Kết quả bất thường: {str(result)[:60]}", "#f44336")

        # ── Kiểm tra đã xong hết chưa ──
        if self._otp_done_count >= self._otp_total_count:
            self._finish_otp_batch(cancelled=False)

    def _finish_otp_batch(self, cancelled=False):
        """Kết thúc batch lấy OTP — khôi phục UI và hiện tổng kết."""
        # Khôi phục nút
        self.btn_get_otp.setEnabled(True)
        self.btn_get_otp.setText("Bắt đầu lấy mã")
        self.btn_add_reg.setEnabled(True)
        self.btn_del_reg.setEnabled(True)
        self.btn_stop_otp.setEnabled(False)

        # Dọn workers
        self._otp_workers.clear()
        self._otp_running_count = 0

        # Tổng kết
        if cancelled:
            self.log(
                f"🛑 Đã dừng! Hoàn thành: {self._otp_done_count}/{self._otp_total_count}, "
                f"Thành công: {self._otp_success_count}",
                "orange"
            )
        else:
            color = "#00ff00" if self._otp_success_count > 0 else "orange"
            self.log(
                f"🏁 Hoàn thành {self._otp_total_count} email! "
                f"✅ Thành công: {self._otp_success_count} | "
                f"❌ Thất bại: {self._otp_total_count - self._otp_success_count}",
                color
            )

            # Hiện MessageBox tổng kết
            if self._otp_success_count == self._otp_total_count:
                QMessageBox.information(
                    self, "Hoàn thành",
                    f"✅ Tất cả {self._otp_total_count} email đã lấy mã thành công!"
                )
            elif self._otp_success_count > 0:
                QMessageBox.information(
                    self, "Hoàn thành",
                    f"Kết quả: {self._otp_success_count}/{self._otp_total_count} thành công.\n"
                    f"Kiểm tra cột 'Trạng thái' để xem chi tiết lỗi."
                )
            else:
                QMessageBox.warning(
                    self, "Thất bại",
                    f"Không lấy được mã OTP cho bất kỳ email nào.\n"
                    f"Kiểm tra mật khẩu, Refresh Token, và cột 'Trạng thái'."
                )

    # ================= QUẢN LÝ DỮ LIỆU (DATABASE) =================
    
    # ================= QUẢN LÝ DỰ ÁN =================
    def load_projects(self):
        try:
            with open(self.projects_file, 'r', encoding='utf-8') as f:
                projects = json.load(f)
        except:
            projects = []
        
        current = self.proj_combo.currentText()
        self.proj_combo.blockSignals(True)
        self.proj_combo.clear()
        self.proj_combo.addItem("Tất cả tài khoản")
        for p in projects:
            self.proj_combo.addItem(p)
        
        index = self.proj_combo.findText(current)
        if index >= 0:
            self.proj_combo.setCurrentIndex(index)
        else:
            self.proj_combo.setCurrentIndex(0)
        self.proj_combo.blockSignals(False)
        self.filter_by_project()

    def handle_add_project(self):
        from PyQt5.QtWidgets import QInputDialog
        import json
        text, ok = QInputDialog.getText(self, "Thêm dự án", "Nhập tên dự án mới:")
        if ok and text.strip():
            proj_name = text.strip()
            if self._is_all_project(proj_name):
                return
            try:
                with open(self.projects_file, 'r', encoding='utf-8') as f:
                    projects = json.load(f)
            except:
                projects = []
                
            if proj_name not in projects:
                projects.append(proj_name)
                with open(self.projects_file, 'w', encoding='utf-8') as f:
                    json.dump(projects, f, ensure_ascii=False)
                self.load_projects()
                self.proj_combo.setCurrentText(proj_name)

    def handle_delete_project(self):
        project_name = self.proj_combo.currentText().strip()
        if not project_name or self._is_all_project(project_name):
            QMessageBox.warning(self, "Không thể xóa", "Không thể xóa mục Tất cả tài khoản.")
            return

        reply = QMessageBox.question(
            self,
            "Xóa dự án",
            (
                f"Xóa dự án '{project_name}'?\n\n"
                "Các tài khoản trong dự án này sẽ được chuyển về Tất cả tài khoản. "
                "Tài khoản thật sẽ không bị xóa."
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            with open(self.projects_file, "r", encoding="utf-8") as f:
                projects = json.load(f)
        except Exception:
            projects = []

        projects = [p for p in projects if p != project_name]
        with open(self.projects_file, "w", encoding="utf-8") as f:
            json.dump(projects, f, ensure_ascii=False)

        moved_count = 0
        for row in range(self.acc_table.rowCount()):
            name_item = self.acc_table.item(row, 1)
            if not name_item:
                continue
            data = name_item.data(Qt.UserRole) or {}
            if data.get("project", "") == project_name:
                data["project"] = ""
                name_item.setData(Qt.UserRole, data)
                moved_count += 1

        self.save_accounts_to_db()
        self.load_projects()
        self.proj_combo.setCurrentIndex(0)
        self.filter_by_project()
        self.log(f"Đã xóa dự án '{project_name}'. Đã chuyển {moved_count} tài khoản về Tất cả tài khoản.")

    def filter_by_project(self, _=None):
        self.apply_account_filters()

    def reset_account_filters(self):
        if hasattr(self, "search_input"):
            self.search_input.clear()
        if hasattr(self, "proj_combo") and self.proj_combo.count() > 0:
            self.proj_combo.setCurrentIndex(0)
        self.apply_account_filters()

    def apply_account_filters(self, _=None):
        project = self.proj_combo.currentText()
        is_all = self._is_all_project()
        target_project = "" if is_all else self._project_storage_value(project)
        query = ""
        if hasattr(self, "search_input"):
            query = (self.search_input.text() or "").strip().lower()
        terms = [term for term in query.replace("|", " ").split() if term]
        
        for row in range(self.acc_table.rowCount()):
            name_item = self.acc_table.item(row, 1)
            row_visible = True
            if name_item:
                data = name_item.data(Qt.UserRole) or {}
                acc_project = (data.get("project", "") or "").strip()
                row_visible = is_all or acc_project == target_project
            if row_visible and terms:
                searchable = self._account_row_search_text(row)
                row_visible = all(term in searchable for term in terms)
            self.acc_table.setRowHidden(row, not row_visible)
        self.update_account_summary()

    def _account_row_search_text(self, row):
        values = []
        for col in (1, 2, 3, 4, 5, 10, 12, 20, self.channel_status_col):
            item = self.acc_table.item(row, col)
            if item:
                values.append(item.text())
        name_item = self.acc_table.item(row, 1)
        if name_item:
            data = name_item.data(Qt.UserRole) or {}
            if isinstance(data, dict):
                values.extend(str(v) for v in data.values() if isinstance(v, (str, int, float)))
        return " ".join(values).lower()

    def update_account_summary(self, *_):
        labels = getattr(self, "account_summary_labels", None)
        if not labels:
            return
        total = self.acc_table.rowCount()
        visible = 0
        logged = 0
        error = 0
        captcha = 0
        for row in range(total):
            if self.acc_table.isRowHidden(row):
                continue
            visible += 1
            item = self.acc_table.item(row, 2)
            text = (item.text() if item else "").strip()
            lowered = text.lower()
            if lowered == "yes":
                logged += 1
            elif text and lowered != "no":
                error += 1
                if "captcha" in lowered:
                    captcha += 1
        selected = len(self.acc_table.selectionModel().selectedRows()) if self.acc_table.selectionModel() else 0
        labels["total"].setText(f"Tổng: {total}")
        labels["visible"].setText(f"Đang hiện: {visible}")
        labels["selected"].setText(f"Đã chọn: {selected}")
        labels["logged"].setText(f"Đã login: {logged}")
        labels["error"].setText(f"Lỗi: {error}")
        labels["captcha"].setText(f"CAPTCHA: {captcha}")

    def assign_to_project(self):
        selected_rows = set(item.row() for item in self.acc_table.selectedItems())
        if not selected_rows:
            QMessageBox.warning(self, "Lỗi", "Vui lòng chọn tài khoản cần chuyển.")
            return
            
        from PyQt5.QtWidgets import QInputDialog
        projects = []
        for i in range(1, self.proj_combo.count()):
            projects.append(self.proj_combo.itemText(i))
            
        if not projects:
            QMessageBox.warning(self, "Lỗi", "Chưa có dự án. Hãy tạo dự án trước.")
            return
            
        projects.insert(0, "Tất cả tài khoản (bỏ khỏi dự án)")
            
        item, ok = QInputDialog.getItem(self, "Chuyển sang dự án", "Chọn dự án:", projects, 0, False)
        if ok and item:
            target_proj = "" if item.startswith("Tất cả tài khoản") or "All accounts" in item else item
            
            for row in selected_rows:
                name_item = self.acc_table.item(row, 1)
                if name_item:
                    data = name_item.data(Qt.UserRole) or {}
                    data["project"] = target_proj
                    name_item.setData(Qt.UserRole, data)
                    
            self.save_accounts_to_db()
            self.filter_by_project()
            self.log(f"Đã chuyển {len(selected_rows)} tài khoản sang dự án '{item}'.")

    def load_accounts_from_db(self):
        """Tải dữ liệu từ file JSON lên bảng"""
        if not os.path.exists(self.db_file):
            self.apply_account_filters()
            return
        try:
            # Xóa bảng cũ trước khi load lại để không bị trùng
            self.acc_table.setRowCount(0)

            with open(self.db_file, "r", encoding="utf-8") as f:
                accounts = json.load(f)
                for acc in accounts:
                    row = self.acc_table.rowCount()
                    self.acc_table.insertRow(row)
                    
                    # Hỗ trợ cả file JSON cũ (chỉ có string keys) và JSON mới (chia columns/profile_data)
                    if "columns" in acc and "profile_data" in acc:
                        cols = acc["columns"]
                        profile_data = acc["profile_data"]
                    else:
                        cols = acc
                        profile_data = {}
                        
                    for col_str, val in cols.items():
                        self.acc_table.setItem(row, int(col_str), QTableWidgetItem(str(val)))
                        
                    # Khôi phục dữ liệu ẩn UserRole
                    name_item = self.acc_table.item(row, 1)
                    if not name_item:
                        name_item = QTableWidgetItem(cols.get("1", ""))
                        self.acc_table.setItem(row, 1, name_item)
                        
                    # Backup tạo profile_data nếu db cũ không có
                    if not profile_data:
                        profile_data = {
                            "ten_ho_so": cols.get("1", ""),
                            "username": cols.get("10", ""),
                            "proxy": cols.get("3", ""),
                            "cookie": cols.get("13", ""),
                            "note": cols.get("20", "")
                        }
                    elif self._is_all_project(profile_data.get("project", "")):
                        profile_data["project"] = ""
                    
                    name_item.setData(Qt.UserRole, profile_data)
                    if profile_data.get("avatar_path"):
                        self.acc_table.setItem(row, 0, QTableWidgetItem("Co"))
                    
            self.apply_account_filters()
            self.log(f"Đã tải {len(accounts)} hồ sơ từ cơ sở dữ liệu.")
        except Exception as e:
            self.log(f"Lỗi khi đọc dữ liệu: {e}", "red")

    def open_edit1_folder(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        edit_dir = os.path.join(base_dir, "EDIT_1")
        if not os.path.isdir(edit_dir):
            QMessageBox.warning(self, "EDIT_1", f"Khong tim thay thu muc EDIT_1:\n{edit_dir}")
            return
        os.startfile(edit_dir)
        self.status_bar.showMessage("Da mo thu muc EDIT_1.", 5000)
        self.log(f"Da mo thu muc EDIT_1: {edit_dir}", "#00aa00")

    def open_edit_video_tool(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        edit_dir = os.path.join(base_dir, "EDIT_1")

        if not os.path.isdir(edit_dir):
            QMessageBox.warning(self, "EDIT video", f"Không tìm thấy thư mục EDIT_1:\n{edit_dir}")
            return

        entry_script = None
        for file_name in ("main.py", "ui_main.py"):
            candidate = os.path.join(edit_dir, file_name)
            if os.path.isfile(candidate):
                entry_script = candidate
                break

        if not entry_script:
            QMessageBox.warning(self, "EDIT video", "Không tìm thấy file main.py hoặc ui_main.py trong thư mục EDIT_1.")
            return

        program = sys.executable
        args = [entry_script]

        if getattr(sys, "frozen", False):
            import shutil
            program = shutil.which("python") or shutil.which("python3") or shutil.which("py")
            if not program:
                QMessageBox.warning(self, "EDIT video", "Không tìm thấy Python để chạy EDIT_1/main.py.")
                return
            if os.path.basename(program).lower() in ("py.exe", "py"):
                args = ["-3", entry_script]

        started = QProcess.startDetached(program, args, edit_dir)
        if isinstance(started, tuple):
            started = started[0]

        if started:
            self.status_bar.showMessage(f"Đã mở EDIT video: {os.path.basename(entry_script)}", 5000)
            self.log(f"Đã mở EDIT video từ {entry_script}", "#00aa00")
        else:
            QMessageBox.critical(self, "EDIT video", f"Không chạy được:\n{entry_script}")

    def open_creator_now_tool(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        tool_dir = os.path.join(base_dir, "Creator Now Cut 14112025")
        script = os.path.join(tool_dir, "creator_now_studio.py")

        if not os.path.isdir(tool_dir):
            QMessageBox.warning(self, "Creator Now", f"Khong tim thay thu muc Creator Now:\n{tool_dir}")
            return
        if not os.path.isfile(script):
            QMessageBox.warning(
                self,
                "Creator Now",
                "Khong tim thay creator_now_studio.py.",
            )
            return

        program = sys.executable
        pythonw = os.path.join(os.path.dirname(program), "pythonw.exe")
        if os.path.isfile(pythonw):
            program = pythonw
        elif os.path.basename(program).lower() == "python.exe":
            candidate = os.path.join(os.path.dirname(program), "pythonw.exe")
            if os.path.isfile(candidate):
                program = candidate

        started = QProcess.startDetached(program, [script], tool_dir)

        if isinstance(started, tuple):
            started = started[0]

        if started:
            self.status_bar.showMessage("Da mo Creator Now Studio.", 5000)
            self.log(f"Da mo Creator Now Studio tu {tool_dir}", "#00aa00")
        else:
            QMessageBox.critical(self, "Creator Now", f"Khong chay duoc Creator Now Studio:\n{tool_dir}")

    def _current_project_name(self):
        if hasattr(self, "proj_combo"):
            project = self.proj_combo.currentText().strip()
            if project:
                return project
        return "Tong tai khoan"

    def _all_project_label(self):
        if hasattr(self, "proj_combo") and self.proj_combo.count() > 0:
            return self.proj_combo.itemText(0).strip()
        return "Tong tai khoan"

    def _is_all_project(self, project_name=None):
        if hasattr(self, "proj_combo") and self.proj_combo.currentIndex() == 0:
            if project_name is None or project_name == self.proj_combo.currentText():
                return True
        if project_name is None and hasattr(self, "proj_combo") and self.proj_combo.currentIndex() == 0:
            return True
        project = (project_name if project_name is not None else self._current_project_name()) or ""
        project = project.strip()
        if not project:
            return True
        return project.lower() in {"all accounts", "tong tai khoan", "tổng tài khoản", "tất cả tài khoản"}

    def _project_storage_value(self, project_name=None):
        project = (project_name if project_name is not None else self._current_project_name()) or ""
        project = project.strip()
        return "" if self._is_all_project(project) else project

    def _dashboard_key_for_project(self, project_name):
        project = "Tổng tài khoản" if self._is_all_project(project_name) else (project_name or "").strip()
        return f"project:{project or 'Tong tai khoan'}"

    def _cleanup_dashboard_refs(self):
        dashboards = getattr(self, "automation_dashboards", {})
        for key, dashboard in list(dashboards.items()):
            try:
                dashboard.windowTitle()
            except RuntimeError:
                dashboards.pop(key, None)
        if self.automation_dashboard is not None:
            try:
                self.automation_dashboard.windowTitle()
            except RuntimeError:
                self.automation_dashboard = None

    def _get_dashboard(self, dashboard_key):
        self._cleanup_dashboard_refs()
        return self.automation_dashboards.get(dashboard_key)

    def _update_dashboard_button(self):
        btn = getattr(self, "btn_dashboard", None)
        if not btn:
            return
        self._cleanup_dashboard_refs()
        count = len(self.automation_dashboards)
        if count == 0:
            btn.setText("\U0001F4CA B\u1ea3ng theo d\u00f5i")
            btn.setToolTip("")
        elif count == 1:
            dashboard = next(iter(self.automation_dashboards.values()))
            if dashboard.isVisible():
                btn.setText("\U0001F4CA B\u1ea3ng \u0111ang m\u1edf")
            else:
                btn.setText("\U0001F4CA Hi\u1ec7n b\u1ea3ng")
            btn.setToolTip("Quản lý bảng theo dõi đang mở")
        else:
            btn.setText(f"\U0001F4CA B\u1ea3ng m\u1edf: {count}")
            btn.setToolTip("Chọn bảng theo dõi cần hiện")

    def handle_dashboard_button(self):
        self._cleanup_dashboard_refs()
        if not self.automation_dashboards:
            self.open_automation_dashboard()
            return

        current_project = self._current_project_name()
        current_key = self._dashboard_key_for_project(current_project)
        if len(self.automation_dashboards) == 1 and current_key in self.automation_dashboards:
            self.open_automation_dashboard()
            return

        menu = QMenu(self)
        action_current = menu.addAction(f"Mở/hiện bảng dự án hiện tại: {current_project}")
        menu.addSeparator()
        actions_by_key = {}
        for key, dashboard in self.automation_dashboards.items():
            label = getattr(dashboard, "project_name", key)
            action = menu.addAction(f"Hiện: {label}")
            actions_by_key[action] = key
        menu.addSeparator()
        action_show_all = menu.addAction("Hiện tất cả")

        chosen = menu.exec_(self.btn_dashboard.mapToGlobal(self.btn_dashboard.rect().bottomLeft()))
        if chosen == action_current:
            self.open_automation_dashboard()
        elif chosen == action_show_all:
            for key in list(self.automation_dashboards.keys()):
                self._show_existing_dashboard(key)
        elif chosen in actions_by_key:
            self._show_existing_dashboard(actions_by_key[chosen])

    def _on_dashboard_hidden(self, dashboard_key=None):
        self._update_dashboard_button()
        self.status_bar.showMessage("B?ng theo d?i ?? ?n, t?c v? v?n ti?p t?c ch?y.", 5000)

    def _on_dashboard_closed(self, dashboard_key=None, *args):
        if dashboard_key:
            self.automation_dashboards.pop(dashboard_key, None)
            self.release_dashboard_profile_locks(dashboard_key)
        if self.automation_dashboard is not None:
            try:
                self.automation_dashboard.windowTitle()
            except RuntimeError:
                self.automation_dashboard = None
        if self.automation_dashboard is not None and dashboard_key:
            if getattr(self.automation_dashboard, "dashboard_key", None) == dashboard_key:
                self.automation_dashboard = None
        self._update_dashboard_button()
        self.status_bar.showMessage("B?ng theo d?i ?? ??ng.", 5000)

    def _show_existing_dashboard(self, dashboard_key):
        dashboard = self._get_dashboard(dashboard_key)
        if dashboard is None:
            return False
        dashboard.showNormal()
        dashboard.raise_()
        dashboard.activateWindow()
        self.automation_dashboard = dashboard
        self._update_dashboard_button()
        return True

    def _show_automation_dashboard(self, accounts_data, project_name=None, dashboard_key=None, after_show=None):
        project_name = project_name or self._current_project_name()
        dashboard_key = dashboard_key or self._dashboard_key_for_project(project_name)
        accounts_data = self._filter_accounts_for_project(accounts_data, project_name)
        if not accounts_data:
            self.log(f"D? ?n '{project_name}' kh?ng c? profile ?? m? b?ng theo d?i.", "orange")
            return None, False
        existing = self._get_dashboard(dashboard_key)
        if existing is not None:
            refreshed = False
            if hasattr(existing, "replace_accounts_data"):
                refreshed = existing.replace_accounts_data(accounts_data, project_name=project_name)
            self._show_existing_dashboard(dashboard_key)
            if refreshed:
                self.log(f"?? c?p nh?t b?ng theo d?i '{project_name}' v?i {len(accounts_data)} profile.", "#2196F3")
                if after_show:
                    QTimer.singleShot(300, lambda d=existing: after_show(d))
            else:
                self.log("B?ng theo d?i ?ang ch?y, kh?ng refresh d? li?u ?? tr?nh m?t worker.", "orange")
            return existing, False

        dialog = AutomationDashboard(
            self,
            accounts_data,
            project_name=project_name,
            dashboard_key=dashboard_key,
        )
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
        dialog.dashboard_hidden.connect(lambda key=dashboard_key: self._on_dashboard_hidden(key))
        dialog.dashboard_closed.connect(lambda key=dashboard_key: self._on_dashboard_closed(key))
        dialog.destroyed.connect(lambda _obj=None, key=dashboard_key: self._on_dashboard_closed(key))
        self.automation_dashboards[dashboard_key] = dialog
        self.automation_dashboard = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        self._update_dashboard_button()
        if after_show:
            QTimer.singleShot(300, lambda d=dialog: after_show(d))
        return dialog, True

    def claim_profile_for_dashboard(self, profile_key, dashboard_key, project_name, profile_name):
        profile_key = (profile_key or "").strip()
        if not profile_key:
            return True, ""
        current = self.running_profiles.get(profile_key)
        if current and current.get("dashboard_key") != dashboard_key:
            return False, current.get("project_name") or current.get("dashboard_key") or "bang khac"
        self.running_profiles[profile_key] = {
            "dashboard_key": dashboard_key,
            "project_name": project_name,
            "profile_name": profile_name,
        }
        return True, ""

    def get_running_profile_owner(self, profile_key, dashboard_key=None):
        profile_key = (profile_key or "").strip()
        if not profile_key:
            return ""
        current = self.running_profiles.get(profile_key)
        if not current:
            return ""
        if dashboard_key and current.get("dashboard_key") == dashboard_key:
            return ""
        return current.get("project_name") or current.get("dashboard_key") or "bang khac"

    def release_profile_for_dashboard(self, profile_key, dashboard_key):
        current = self.running_profiles.get(profile_key)
        if current and current.get("dashboard_key") == dashboard_key:
            self.running_profiles.pop(profile_key, None)

    def release_dashboard_profile_locks(self, dashboard_key):
        for profile_key, current in list(self.running_profiles.items()):
            if current.get("dashboard_key") == dashboard_key:
                self.running_profiles.pop(profile_key, None)

    def _track_worker(self, worker):
        self.active_workers.append(worker)
        worker.finished.connect(lambda *_args, w=worker: self._remove_worker(w))
        return worker

    def _remove_worker(self, worker):
        try:
            if worker in self.active_workers:
                self.active_workers.remove(worker)
        except Exception:
            pass

    def _prune_finished_workers(self):
        self.active_workers = [
            worker for worker in self.active_workers
            if worker is not None and worker.isRunning()
        ]
        return len(self.active_workers)

    def handle_cleanup_browser_sessions(self):
        reply = QMessageBox.question(
            self,
            "Dọn dẹp trình duyệt",
            "Chức năng này sẽ dừng các task đang chờ/đang chạy và đóng Orbita/Chrome do app mở.\n\n"
            "Không xóa dữ liệu profile, không xóa cookie, không xóa GoLogin profile.\n\n"
            "Bạn muốn tiếp tục?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self.cleanup_browser_sessions()

    def cleanup_browser_sessions(self):
        self._cleanup_dashboard_refs()
        dashboard_count = 0
        preview_count = 0
        for dashboard in list(self.automation_dashboards.values()):
            try:
                if hasattr(dashboard, "cleanup_runtime_sessions"):
                    result = dashboard.cleanup_runtime_sessions() or {}
                    dashboard_count += 1
                    preview_count += int(result.get("previews", 0) or 0)
            except RuntimeError:
                continue
            except Exception as e:
                self.log(f"Lỗi dọn bảng theo dõi: {e}", "orange")

        self.running_profiles.clear()
        active_workers = self._prune_finished_workers()
        targets = self._collect_browser_cleanup_targets()

        worker = GenericWorker(
            self._cleanup_external_browser_processes,
            extra_data={"dashboards": dashboard_count, "previews": preview_count, "active_workers": active_workers},
            pass_progress=False,
            targets=targets,
        )
        worker.finished.connect(self.on_cleanup_browser_sessions_done)
        self._track_worker(worker)
        worker.start()
        self.log(
            f"Đang dọn phiên trình duyệt: {dashboard_count} bảng, {preview_count} preview. "
            "Dữ liệu profile được giữ nguyên.",
            "blue",
        )

    def _collect_browser_cleanup_targets(self):
        targets = []
        gologin_root = gologin_profiles_root()
        for row in range(self.acc_table.rowCount()):
            name_item = self.acc_table.item(row, 1)
            profile_data = dict(name_item.data(Qt.UserRole) or {}) if name_item else {}
            id_item = self.acc_table.item(row, 4)
            browser_id = (
                profile_data.get("browser_id")
                or (id_item.text().strip() if id_item else "")
                or ""
            ).strip()
            gologin_id = (profile_data.get("gologin_profile_id") or "").strip()
            profile_dir = str(gologin_root / f"profile_{browser_id}") if browser_id else ""
            targets.append({
                "browser_id": browser_id,
                "gologin_profile_id": gologin_id,
                "profile_dir": profile_dir,
            })
        return targets

    def _cleanup_external_browser_processes(self, targets):
        result = {"closed": 0, "errors": []}

        try:
            from browser_manager import BrowserManager
            BrowserManager().close_all()
        except Exception as e:
            result["errors"].append(f"BrowserManager: {e}")

        try:
            import psutil
        except Exception as e:
            result["errors"].append(f"Thiếu psutil: {e}")
            return result

        def norm_path(path):
            text = str(path or "").strip().strip('"').strip("'")
            if not text:
                return ""
            try:
                text = os.path.abspath(text)
            except Exception:
                pass
            return text.replace("\\", "/").rstrip("/").lower()

        target_dirs = {
            norm_path(item.get("profile_dir"))
            for item in (targets or [])
            if item.get("profile_dir")
        }
        target_ids = {
            str(value).strip().lower()
            for item in (targets or [])
            for value in (item.get("browser_id"), item.get("gologin_profile_id"))
            if value and len(str(value).strip()) >= 8
        }

        browser_names = {"chrome.exe", "orbita-browser.exe", "chromium.exe"}
        matched = []
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if name not in browser_names:
                    continue
                args = [str(arg) for arg in (proc.info.get("cmdline") or [])]
                cmdline = " ".join(args).lower().replace("\\", "/")

                has_match = False
                for index, arg in enumerate(args):
                    lower_arg = arg.lower()
                    if "--user-data-dir" not in lower_arg:
                        continue
                    if "=" in arg:
                        user_dir = arg.split("=", 1)[1]
                    elif index + 1 < len(args):
                        user_dir = args[index + 1]
                    else:
                        user_dir = ""
                    user_dir = norm_path(user_dir)
                    if user_dir and any(user_dir == target or user_dir.startswith(target + "/") for target in target_dirs):
                        has_match = True
                        break

                if not has_match and target_ids:
                    has_match = any(target_id in cmdline for target_id in target_ids)

                if has_match:
                    matched.append(proc)
                    matched.extend(proc.children(recursive=True))
            except Exception:
                continue

        unique = {}
        for proc in matched:
            try:
                unique[proc.pid] = proc
            except Exception:
                pass
        procs = list(unique.values())

        for proc in procs:
            try:
                proc.terminate()
            except Exception:
                pass
        _, alive = psutil.wait_procs(procs, timeout=4)
        for proc in alive:
            try:
                proc.kill()
            except Exception:
                pass

        result["closed"] = len(procs)
        self._patch_profile_exit_type_normal(target_dirs)
        return result

    def _patch_profile_exit_type_normal(self, target_dirs):
        for target_dir in target_dirs:
            if not target_dir:
                continue
            prefs_path = os.path.join(target_dir, "Default", "Preferences")
            if not os.path.exists(prefs_path):
                continue
            try:
                with open(prefs_path, "r", encoding="utf-8") as f:
                    prefs = json.load(f)
                prefs.setdefault("profile", {})["exit_type"] = "Normal"
                prefs["profile"]["exited_cleanly"] = True
                with open(prefs_path, "w", encoding="utf-8") as f:
                    json.dump(prefs, f, ensure_ascii=False)
            except Exception:
                pass

    def on_cleanup_browser_sessions_done(self, success, result, extra_data):
        info = extra_data if isinstance(extra_data, dict) else {}
        if success and isinstance(result, dict):
            closed = int(result.get("closed", 0) or 0)
            errors = result.get("errors", []) or []
            self.log(
                f"Đã dọn xong: {info.get('dashboards', 0)} bảng, "
                f"{info.get('previews', 0)} preview, đóng {closed} process trình duyệt còn sót.",
                "#16a34a",
            )
            for error in errors[:3]:
                self.log(f"Dọn trình duyệt cảnh báo: {error}", "orange")
        else:
            self.log(f"Lỗi dọn trình duyệt: {result}", "red")
        self.status_bar.showMessage("Đã dọn phiên trình duyệt. Dữ liệu profile được giữ nguyên.", 6000)

    def _collect_accounts_for_rows(self, row_numbers):
        accounts_data = []
        for row in row_numbers:
            name_item = self.acc_table.item(row, 1)
            if not name_item:
                continue
            profile_data = dict(name_item.data(Qt.UserRole) or {})

            columns = {}
            for col in range(self.acc_table.columnCount()):
                item = self.acc_table.item(row, col)
                if item:
                    columns[str(col)] = item.text()

            profile_data = sync_profile_data_from_table_columns(profile_data, columns)
            name_item.setData(Qt.UserRole, profile_data)

            accounts_data.append({
                "columns": columns,
                "profile_data": profile_data,
                "source_row": row,
            })
        return accounts_data

    def _filter_accounts_for_project(self, accounts_data, project_name=None):
        if self._is_all_project(project_name):
            return list(accounts_data or [])
        target_project = self._project_storage_value(project_name)
        return [
            acc for acc in (accounts_data or [])
            if ((acc.get("profile_data", {}).get("project", "") or "").strip() == target_project)
        ]

    def _current_project_rows(self):
        project_name = self._current_project_name()
        if self._is_all_project(project_name):
            return list(range(self.acc_table.rowCount()))
        target_project = self._project_storage_value(project_name)
        rows = []
        for row in range(self.acc_table.rowCount()):
            name_item = self.acc_table.item(row, 1)
            profile_data = dict(name_item.data(Qt.UserRole) or {}) if name_item else {}
            if (profile_data.get("project", "") or "").strip() == target_project:
                rows.append(row)
        return rows

    def open_automation_dashboard(self):
        project_name = self._current_project_name()
        project_rows = self._current_project_rows()
        accounts_data = self._collect_accounts_for_rows(project_rows)
        if not accounts_data:
            self.log("D? ?n hi?n t?i kh?ng c? profile ?? m? b?ng theo d?i.", "orange")
            return
        self.log(f"M? b?ng theo d?i '{project_name}' v?i {len(accounts_data)} profile.", "#00ff00")
        self._show_automation_dashboard(
            accounts_data,
            project_name=project_name,
            dashboard_key=self._dashboard_key_for_project(project_name),
        )
        return

    def save_accounts_to_db(self):
        """Lưu toàn bộ dữ liệu trên bảng vào file JSON bao gồm cả UserRole data"""
        accounts = []
        for row in range(self.acc_table.rowCount()):
            acc_data = {"columns": {}, "profile_data": {}}
            for col in range(self.acc_table.columnCount()):
                item = self.acc_table.item(row, col)
                if item and item.text():
                    acc_data["columns"][str(col)] = item.text()
                    
            name_item = self.acc_table.item(row, 1)
            if name_item:
                p_data = dict(name_item.data(Qt.UserRole) or {})
                p_data = sync_profile_data_from_table_columns(p_data, acc_data["columns"])
                if p_data:
                    acc_data["profile_data"] = p_data
                    name_item.setData(Qt.UserRole, p_data)
                    
            accounts.append(acc_data)
            
        try:
            with open(self.db_file, "w", encoding="utf-8") as f:
                json.dump(accounts, f, ensure_ascii=False, indent=4)
            self.update_account_summary()
        except Exception as e:
            self.log(f"Lỗi khi lưu dữ liệu: {e}", "red")

    # ================= LOGIC TÍCH HỢP TỰ ĐỘNG HÓA =================
    
    def log(self, message, color="white"):
        """Hàm in log ra console UI"""
        timestamp = time.strftime('%H:%M:%S')
        if color != "white":
            msg = f"<span style='color:{color}'>[{timestamp}] {message}</span>"
        else:
            msg = f"[{timestamp}] {message}"
        self.log_output.append(msg)
        self.log_output.verticalScrollBar().setValue(self.log_output.verticalScrollBar().maximum())

    def clear_log_output(self):
        self.log_output.clear()

    def open_gologin_settings(self):
        dialog = GoLoginSettingsDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            settings = dialog.get_settings()
            save_gologin_settings(
                api_key=settings["api_key"],
                use_gologin_cloud=settings["use_gologin_cloud"],
                gologin_folder_name=settings["gologin_folder_name"],
            )
            if settings["api_key"]:
                self.log(f"Đã lưu GoLogin API Key: {mask_secret(settings['api_key'])}", "#00ff00")
            else:
                self.log("Đã xóa GoLogin API Key.", "orange")
            QMessageBox.information(self, "GoLogin", "Đã lưu cấu hình GoLogin API.")

    def _create_adspower_profile(self, profile_name, proxy_string="", proxy_type="http"):
        """Gọi API AdsPower tạo profile mới. Trả về tuple (success, result).
        Thành công: (True, profile_id)
        Thất bại:   (False, error_message)
        """
        import requests
        api_url = "http://local.adspower.net:50325/api/v1/user/create"
        
        payload = {
            "group_id": "0",
            "name": f"_SSMATool-{profile_name}",
            "os": "win",
            "browser": "sun",
            "fingerprint_config": {
                "screen_resolution": "1920_1080",
                "language": ["en-US", "en"]
            }
        }
        
        if proxy_string and proxy_string.strip():
            parts = proxy_string.strip().split(":", 3)
            if len(parts) >= 2:
                payload["user_proxy_config"] = {
                    "proxy_soft": "other",
                    "proxy_type": proxy_type,
                    "proxy_host": parts[0],
                    "proxy_port": parts[1],
                    "proxy_user": parts[2] if len(parts) > 2 else "",
                    "proxy_password": parts[3] if len(parts) > 3 else ""
                }
            else:
                # Proxy nhập sai format, bỏ qua proxy
                payload["user_proxy_config"] = {"proxy_soft": "no_proxy"}
        else:
            # Không nhập proxy → báo rõ cho AdsPower
            payload["user_proxy_config"] = {"proxy_soft": "no_proxy"}
        
        try:
            response = requests.post(api_url, json=payload, timeout=10).json()
            if response.get("code") == 0:
                return True, response["data"]["id"]
            else:
                error_msg = response.get('msg', 'Lỗi không xác định từ AdsPower')
                return False, error_msg
        except requests.exceptions.ConnectionError:
            return False, "Không thể kết nối đến AdsPower.\nHãy chắc chắn app AdsPower đang được mở!"
        except Exception as e:
            return False, f"Lỗi hệ thống: {str(e)}"

    def _update_adspower_profile(self, browser_id, profile_name, proxy_string="", proxy_type="http"):
        """Gọi API AdsPower cập nhật profile."""
        import requests
        api_url = "http://local.adspower.net:50325/api/v1/user/update"
        
        payload = {
            "user_id": browser_id,
            "name": f"_SSMATool-{profile_name}",
        }
        
        if proxy_string and proxy_string.strip():
            parts = proxy_string.strip().split(":", 3)
            if len(parts) >= 2:
                payload["user_proxy_config"] = {
                    "proxy_soft": "other",
                    "proxy_type": proxy_type,
                    "proxy_host": parts[0],
                    "proxy_port": parts[1],
                    "proxy_user": parts[2] if len(parts) > 2 else "",
                    "proxy_password": parts[3] if len(parts) > 3 else ""
                }
            else:
                payload["user_proxy_config"] = {"proxy_soft": "no_proxy"}
        else:
            payload["user_proxy_config"] = {"proxy_soft": "no_proxy"}
        
        try:
            response = requests.post(api_url, json=payload, timeout=10).json()
            if response.get("code") == 0:
                return True, ""
            else:
                return False, response.get('msg', 'Lỗi không xác định từ AdsPower')
        except requests.exceptions.ConnectionError:
            return False, "Không thể kết nối đến AdsPower.\nHãy chắc chắn app AdsPower đang được mở!"
        except Exception as e:
            return False, f"Lỗi hệ thống: {str(e)}"

    def _get_existing_profile_names(self, exclude_row=-1):
        """Lấy danh sách tên hồ sơ hiện có (bỏ qua hàng exclude_row nếu đang sửa)"""
        names = []
        for row in range(self.acc_table.rowCount()):
            if row == exclude_row:
                continue
            item = self.acc_table.item(row, 1)
            if item and item.text().strip():
                names.append(item.text().strip())
        return names

    def _create_gologin_cloud_profile(self, profile_name):
        """Tạo profile GoLogin cloud và trả về ID profile."""
        settings = load_gologin_settings()
        api_key = (settings.get("api_key") or "").strip()
        use_cloud = bool(settings.get("use_gologin_cloud"))
        folder_name = (settings.get("gologin_folder_name") or "").strip()

        if not use_cloud:
            return False, "GoLogin cloud đang tắt trong cấu hình."
        if not api_key:
            return False, "Thiếu GoLogin API key."

        try:
            from gologin import GoLogin
            gl = GoLogin({"token": api_key})

            def _extract_profile_id(value):
                if not value:
                    return ""
                if isinstance(value, str):
                    return value.strip()
                if isinstance(value, dict):
                    for key in ("id", "profileId", "_id"):
                        v = value.get(key)
                        if v:
                            return str(v).strip()
                    data_obj = value.get("data")
                    if isinstance(data_obj, dict):
                        for key in ("id", "profileId", "_id"):
                            v = data_obj.get(key)
                            if v:
                                return str(v).strip()
                return ""

            errors = []

            # 1) Ưu tiên quick create: payload nhỏ, ít bị reject 400.
            try:
                quick_result = gl.createProfileRandomFingerprint({
                    "name": profile_name,
                    "os": "win",
                })
                profile_id = _extract_profile_id(quick_result)
                if profile_id:
                    return True, profile_id
                errors.append(f"quick_create_no_id: {quick_result}")
            except Exception as e:
                errors.append(f"quick_create_error: {e}")

            # 2) Fallback create empty profile.
            try:
                empty_result = gl.createEmptyProfile()
                profile_id = _extract_profile_id(empty_result)
                if profile_id:
                    return True, profile_id
                errors.append(f"empty_create_no_id: {empty_result}")
            except Exception as e:
                errors.append(f"empty_create_error: {e}")

            # 3) Fallback cuối: create payload đầy đủ (có folderName).
            try:
                create_options = {
                    "name": profile_name,
                    "os": "win",
                    "proxyEnabled": False,
                    "proxy": {
                        "mode": "none",
                        "host": "",
                        "port": 80,
                        "username": "",
                        "password": ""
                    }
                }
                if folder_name:
                    create_options["folderName"] = folder_name

                create_result = gl.create(create_options)
                profile_id = _extract_profile_id(create_result)
                if profile_id:
                    return True, profile_id
                errors.append(f"full_create_no_id: {create_result}")
            except Exception as e:
                errors.append(f"full_create_error: {e}")

            return False, " | ".join(errors)
        except Exception as e:
            return False, str(e)

    def _explain_gologin_error(self, err_text):
        txt = (err_text or "").lower()
        if "winerror 10013" in txt or "forbidden by its access permissions" in txt:
            return "Kết nối ra API GoLogin bị chặn bởi firewall/antivirus/proxy trên máy."
        if "max retries exceeded" in txt or "failed to establish a new connection" in txt:
            return "Không kết nối được đến API GoLogin (mạng DNS/VPN/proxy/firewall)."
        if "401" in txt or "unauthorized" in txt:
            return "API key GoLogin không hợp lệ hoặc đã hết quyền."
        if "403" in txt or "forbidden" in txt:
            return "Tài khoản/token không có quyền tạo profile."
        if "timeout" in txt:
            return "Kết nối API GoLogin bị timeout."
        return "GoLogin API trả lỗi."

    def _delete_gologin_cloud_profile(self, profile_id):
        """Delete a GoLogin cloud profile by ID."""
        profile_id = (profile_id or "").strip()
        if not profile_id:
            return False, "Thiếu GoLogin profile ID."

        settings = load_gologin_settings()
        api_key = (settings.get("api_key") or "").strip()
        if not api_key:
            return False, "Thiếu GoLogin API key."

        try:
            from gologin import GoLogin
            gl = GoLogin({"token": api_key})
            gl.delete(profile_id)
            return True, "OK"
        except Exception as e:
            return False, str(e)


    def open_add_multiple_dialog(self):
        projects = []
        for i in range(self.proj_combo.count()):
            projects.append(self.proj_combo.itemText(i))
            
        dialog = AddMultipleDialog(
            self, 
            projects=projects, 
            current_project=self.proj_combo.currentText(),
            existing_names=self._get_existing_profile_names()
        )
        if dialog.exec_() == QDialog.Accepted:
            profiles = dialog.result_data
            if not profiles:
                return
                
            success_count = 0
            
            for data in profiles:
                profile_name = data.get("ten_ho_so", "")
                
                # Check duplicate
                existing_names = self._get_existing_profile_names()
                if profile_name in existing_names:
                    self.log(f"Bỏ qua {profile_name} vì trùng tên", "orange")
                    continue
                    
                # Proxy check
                proxy_str = data.get("proxy", "")
                proxy_type = data.get("proxy_type", "http")
                if proxy_str:
                    is_live, msg = check_proxy_live(proxy_str, proxy_type)
                    if not is_live:
                        self.log(f"Bỏ qua {profile_name} vì proxy chết: {msg}", "red")
                        continue
                    data["proxy_type"] = detect_proxy_type_from_check_message(proxy_type, msg)
                
                # GoLogin
                success_cloud, cloud_result = self._create_gologin_cloud_profile(profile_name)
                if success_cloud:
                    profile_id = cloud_result
                    data["browser_id"] = profile_id
                    data["gologin_profile_id"] = profile_id
                    self.log(f"✅ Đã tạo GoLogin cloud profile: {profile_id}", "#00ff00")
                else:
                    settings = load_gologin_settings()
                    use_cloud = bool(settings.get("use_gologin_cloud"))
                    
                    if use_cloud and cloud_result not in ("GoLogin cloud đang tắt trong cấu hình.", "Thiếu GoLogin API key."):
                        self.log(f"❌ Không tạo được GoLogin cloud cho {profile_name}: {cloud_result}", "red")
                        self.log("Dừng quá trình nhập do lỗi API GoLogin.", "red")
                        QMessageBox.critical(self, "Lỗi API GoLogin", f"Lỗi API khi tạo profile {profile_name}. Quá trình thêm nhiều đã bị dừng giữa chừng.")
                        break # DỪNG LUÔN
                    else:
                        import time
                        local_id = f"gologin_{int(time.time())}_{success_count}"
                        data["browser_id"] = local_id
                        data["gologin_profile_id"] = ""
                        self.log(f"✅ Tạo hồ sơ offline: {local_id}", "#00ff00")
                
                self.add_profile_to_table(data)
                success_count += 1
                
            QMessageBox.information(self, "Hoàn tất", f"Đã thêm thành công {success_count} / {len(profiles)} hồ sơ.")

    def open_add_profile_dialog(self):
        """Mở popup thêm hồ sơ mới → Tự động tạo profile AdsPower → Lưu vào bảng"""
        dialog = AddProfileDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            data = dialog.get_data()
            
            profile_name = data.get("ten_ho_so", "Unnamed")
            if not profile_name.strip():
                QMessageBox.warning(self, "Cảnh báo", "Bác chưa nhập tên hồ sơ!")
                return
            
            # Kiểm tra trùng tên hồ sơ
            existing_names = self._get_existing_profile_names()
            if profile_name.strip() in existing_names:
                QMessageBox.warning(
                    self, "Trùng tên hồ sơ",
                    f"Tên hồ sơ \"{profile_name}\" đã tồn tại!\n"
                    f"Vui lòng đặt tên khác để tránh xung đột khi chạy automation."
                )
                return
            
            # Assign to current project
            current_proj = self.proj_combo.currentText()
            data["project"] = "" if current_proj == "Tổng tài khoản" else current_proj
            
            proxy_str = data.get("proxy", "")
            proxy_type = "socks5" if dialog.radio_socks5.isChecked() else "http"
            
            # 2. KIỂM TRA PROXY TRƯỚC KHI TẠO
            if proxy_str:
                self.log(f"Đang check Proxy: {proxy_str} ...")
                QApplication.processEvents() # Cập nhật UI
                is_live, msg = check_proxy_live(proxy_str, proxy_type)
                
                if not is_live:
                    QMessageBox.critical(self, "Proxy Chết", f"Proxy này không dùng được bác ơi!\nChi tiết: {msg}")
                    return # Dừng luôn, không cho tạo
                proxy_type = detect_proxy_type_from_check_message(proxy_type, msg)
                data["proxy_type"] = proxy_type
            
            success_cloud, cloud_result = self._create_gologin_cloud_profile(profile_name)
            if success_cloud:
                profile_id = cloud_result
                data["browser_id"] = profile_id
                data["gologin_profile_id"] = profile_id
                self.log(f"✅ Đã tạo GoLogin cloud profile: {profile_id}", "#00ff00")
                QMessageBox.information(self, "Thành công", f"Đã tạo GoLogin profile!\nID: {profile_id}")
            else:
                settings = load_gologin_settings()
                use_cloud = bool(settings.get("use_gologin_cloud"))
                
                if use_cloud and cloud_result not in ("GoLogin cloud đang tắt trong cấu hình.", "Thiếu GoLogin API key."):
                    # Lỗi API thực sự, hủy tạo profile
                    self.log(f"❌ Không tạo được GoLogin cloud: {cloud_result}", "red")
                    short_reason = self._explain_gologin_error(cloud_result)
                    QMessageBox.critical(
                        self,
                        "Lỗi tạo profile",
                        f"Không tạo được profile trên GoLogin cloud.\n"
                        f"Lý do: {short_reason}\n\n"
                        f"Chi tiết kỹ thuật:\n{cloud_result}\n\n"
                        "Quá trình thêm hồ sơ đã bị hủy."
                    )
                    return # Dừng, không tạo profile
                else:
                    # Người dùng chủ động tắt cloud hoặc chưa có key -> Tạo offline
                    import time
                    local_id = f"gologin_{int(time.time())}"
                    data["browser_id"] = local_id
                    if not data.get("gologin_profile_id"):
                        data["gologin_profile_id"] = ""

                    self.log(f"✅ Tạo hồ sơ offline: {local_id}", "#00ff00")
            
            self.add_profile_to_table(data)

    def handle_edit_profile(self, index):
        """Mở popup sửa hồ sơ đã có khi double click"""
        row = index.row()
        name_item = self.acc_table.item(row, 1)
        if not name_item: return
        
        existing_data = name_item.data(Qt.UserRole) or {}
        
        # Mở dialog ở chế độ Edit
        dialog = AddProfileDialog(self, existing_data=existing_data)
        if dialog.exec_() == QDialog.Accepted:
            new_data = dialog.get_data()
            
            proxy_str = new_data.get("proxy", "")
            proxy_type = "socks5" if dialog.radio_socks5.isChecked() else "http"
            browser_id = new_data.get("browser_id", "")
            profile_name = new_data.get("ten_ho_so", "Unnamed")
            
            # Kiểm tra trùng tên hồ sơ (bỏ qua chính hàng đang sửa)
            if not profile_name.strip():
                QMessageBox.warning(self, "Cảnh báo", "Bác chưa nhập tên hồ sơ!")
                return
            existing_names = self._get_existing_profile_names(exclude_row=row)
            if profile_name.strip() in existing_names:
                QMessageBox.warning(
                    self, "Trùng tên hồ sơ",
                    f"Tên hồ sơ \"{profile_name}\" đã tồn tại!\n"
                    f"Vui lòng đặt tên khác để tránh xung đột khi chạy automation."
                )
                return
            
            # Luôn check proxy nếu có nhập proxy (bất kể thay đổi hay không)
            if proxy_str:
                self.log(f"Đang check Proxy: {proxy_str} ...")
                QApplication.processEvents()
                is_live, msg = check_proxy_live(proxy_str, proxy_type)
                if not is_live:
                    QMessageBox.critical(self, "Proxy Chết", f"Proxy này không dùng được bác ơi!\nChi tiết: {msg}")
                    return
                else:
                    proxy_type = detect_proxy_type_from_check_message(proxy_type, msg)
                    new_data["proxy_type"] = proxy_type
                    self.log(f"✅ Proxy sống! IP thật: {msg}", "#00ff00")
                    
            # Không cần đồng bộ AdsPower nữa vì đã dùng Gologin Offline
            self.log("Đã lưu cấu hình proxy Offline.", "#00ff00")

            # Cập nhật lại dữ liệu
            name_item.setData(Qt.UserRole, new_data)
            self.acc_table.setItem(row, 1, QTableWidgetItem(new_data.get("ten_ho_so", "")))
            self.acc_table.item(row, 1).setData(Qt.UserRole, new_data) # Lưu lại data
            
            self.acc_table.setItem(row, 3, QTableWidgetItem(new_data.get("proxy", "")))
            self.acc_table.setItem(row, 4, QTableWidgetItem(new_data.get("browser_id", "")))
            self.acc_table.setItem(row, 10, QTableWidgetItem(new_data.get("username", "")))
            self.acc_table.setItem(row, 13, QTableWidgetItem(new_data.get("cookie", "")))
            self.acc_table.setItem(row, 20, QTableWidgetItem(new_data.get("note", "")))
            self.acc_table.setItem(row, 0, QTableWidgetItem("Co" if new_data.get("avatar_path") else ""))
            
            self.log(f"Đã cập nhật hồ sơ hàng {row+1}")
            self.save_accounts_to_db()

    def add_profile_to_table(self, data):
        row = self.acc_table.rowCount()
        self.acc_table.insertRow(row)
        
        # Cột 1: Tên hồ sơ
        name_item = QTableWidgetItem(data.get("ten_ho_so", ""))
        # Lưu TRỌN BỘ dữ liệu người dùng nhập (mật khẩu, mail, token,...) vào thuộc tính ẩn của Item
        name_item.setData(Qt.UserRole, data)
        self.acc_table.setItem(row, 1, name_item)
        
        self.acc_table.setItem(row, 0, QTableWidgetItem("Co" if data.get("avatar_path") else ""))
        self.acc_table.setItem(row, 2, QTableWidgetItem("No")) # Logged
        self.acc_table.setItem(row, 3, QTableWidgetItem(data.get("proxy", "")))
        self.acc_table.setItem(row, 4, QTableWidgetItem(data.get("browser_id", ""))) # ID AdsPower
        self.acc_table.setItem(row, 10, QTableWidgetItem(data.get("username", ""))) # Email
        self.acc_table.setItem(row, 12, QTableWidgetItem("Chưa check")) # Tình trạng
        self.acc_table.setItem(row, 13, QTableWidgetItem(data.get("cookie", ""))) # Cookie
        self.acc_table.setItem(row, 20, QTableWidgetItem(data.get("note", ""))) # Note
        self.acc_table.setItem(row, 25, QTableWidgetItem(str(row + 1))) # STT
        
        self.log(f"Đã thêm hồ sơ: {data.get('ten_ho_so', 'Unnamed')}")
        self.save_accounts_to_db() # Lưu vào file


    def handle_update_stats(self):
        """Cập nhật thống kê public TikTok bằng curl_cffi, không mở trình duyệt."""
        selected_rows = self.acc_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "Lỗi", "Vui lòng chọn ít nhất 1 tài khoản!")
            return

        reply = QMessageBox.question(
            self, "Xác nhận",
            f"Cập nhật nhanh thống kê public cho {len(selected_rows)} tài khoản đã chọn?\n"
            "Chức năng này không mở trình duyệt và chỉ lấy dữ liệu public.",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        username_by_row = {}
        profile_requests = []
        for index in selected_rows:
            row = index.row()
            username_item = self.acc_table.item(row, 5)
            username = username_item.text().strip() if username_item else ""
            name_item = self.acc_table.item(row, 1)
            profile_data = name_item.data(Qt.UserRole) if name_item else {}
            if not username:
                username = (profile_data or {}).get("tiktok_id", "")
            if not username:
                self.acc_table.setItem(row, 12, QTableWidgetItem("Thiếu ID TikTok"))
                self.log(f"Hàng {row+1}: thiếu ID TikTok, bỏ qua", "orange")
                continue
            username_by_row[row] = username
            cookie_item = self.acc_table.item(row, 13)
            cookie_str = cookie_item.text().strip() if cookie_item else ""
            if not cookie_str and isinstance(profile_data, dict):
                cookie_str = profile_data.get("cookie", "")
            profile_requests.append({"key": str(row), "username": username, "cookie": cookie_str})
            self.acc_table.setItem(row, 12, QTableWidgetItem("Đang cập nhật..."))

        if not username_by_row:
            return

        from tiktok_fast_profile import update_all_profiles_async

        self.log(f"Bắt đầu cập nhật nhanh {len(username_by_row)} profile TikTok...", "blue")
        worker = GenericWorker(
            update_all_profiles_async,
            extra_data=username_by_row,
            pass_progress=False,
            username_list=profile_requests,
            max_workers=15,
        )
        worker.finished.connect(self.on_fast_update_stats_done)
        self._track_worker(worker)
        worker.start()

    def on_fast_update_stats_done(self, success, result, username_by_row):
        if not success:
            self.log(f"Lỗi cập nhật nhanh thống kê: {result}", "red")
            for row in username_by_row:
                self.acc_table.setItem(row, 12, QTableWidgetItem("Lỗi cập nhật"))
            return

        result_by_original = result or {}
        for row, username in (username_by_row or {}).items():
            stats = result_by_original.get(str(row)) or result_by_original.get(username) or {}
            if not stats.get("ok"):
                error = stats.get("error", "unknown_error")
                self.acc_table.setItem(row, 12, QTableWidgetItem(f"Lỗi: {error}"))
                self.log(f"Hàng {row+1} @{username}: lỗi cập nhật nhanh ({error})", "orange")
                continue

            follower_count = stats.get("follower_count", -1)
            heart_count = stats.get("heart_count", -1)
            video_count = stats.get("video_count", -1)
            following_count = stats.get("following_count", -1)

            if follower_count >= 0:
                self.acc_table.setItem(row, 7, QTableWidgetItem(str(follower_count)))
            if heart_count >= 0:
                self.acc_table.setItem(row, 8, QTableWidgetItem(str(heart_count)))
            if video_count >= 0:
                self.acc_table.setItem(row, 9, QTableWidgetItem(str(video_count)))
            if following_count >= 0:
                self.acc_table.setItem(row, 6, QTableWidgetItem(str(following_count)))

            name_item = self.acc_table.item(row, 1)
            if name_item:
                profile_data = dict(name_item.data(Qt.UserRole) or {})
                profile_data.update({
                    "t_follows": str(follower_count) if follower_count >= 0 else "",
                    "t_views": str(heart_count) if heart_count >= 0 else "",
                    "t_video": str(video_count) if video_count >= 0 else "",
                    "following_count": str(following_count) if following_count >= 0 else "",
                })
                name_item.setData(Qt.UserRole, profile_data)

            self.acc_table.setItem(row, 12, QTableWidgetItem("Đã cập nhật"))
            self.log(
                f"Hàng {row+1} @{username}: Follow={follower_count}, Heart={heart_count}, Video={video_count}",
                "green"
            )

        self.save_accounts_to_db()

    def handle_check_status(self):
        """Xử lý sự kiện: Kiểm tra trạng thái cookie"""
        selected_rows = self.acc_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "Lỗi", "Vui lòng chọn ít nhất 1 tài khoản!")
            return
        
        for index in selected_rows:
            row = index.row()
            cookie_val = self.acc_table.item(row, 13) # Cột 13 là Cookie theo header mới
            cookie_str = cookie_val.text() if cookie_val else ""
            
            # Tạo dictionary giả lập giống AccountManager yêu cầu
            acc_data = {'Cookie': cookie_str, 'RowIndex': row}
            
            # Khởi tạo Worker để check (hàm sync)
            worker = GenericWorker(self.acc_manager.check_live_cookie, extra_data=row, pass_progress=False, account=acc_data)
            worker.progress.connect(self.log)
            worker.finished.connect(self.on_check_status_done)
            self._track_worker(worker)
            worker.start()
            
            self.acc_table.setItem(row, 12, QTableWidgetItem("Đang kiểm tra...")) # Cột 12 là Tình trạng
            self.log(f"Đang ném tác vụ check live cho Row {row} vào background...")

    def on_check_status_done(self, success, result, row):
        """Callback khi check status xong"""
        if success:
            color = "#00ff00" if result == 'Live' else "#ff4444"
            self.log(f"Row {row} - Kết quả: {result}", color)
            self.acc_table.setItem(row, 12, QTableWidgetItem(result)) # Cột 12 là Tình trạng
        else:
            self.log(f"Row {row} - Lỗi: {result}", "red")
            self.acc_table.setItem(row, 12, QTableWidgetItem("Lỗi kiểm tra"))
        
        self.save_accounts_to_db() # Cập nhật DB sau khi check xong

    def _get_profile_identity_and_cookie(self, row):
        name_item = self.acc_table.item(row, 1)
        profile_name = name_item.text() if name_item else f"Row {row + 1}"
        profile_data = dict(name_item.data(Qt.UserRole) or {}) if name_item else {}

        cookie_str = (profile_data.get("cookie", "") or "").strip()
        if not cookie_str:
            cookie_item = self.acc_table.item(row, 13)
            cookie_str = cookie_item.text().strip() if cookie_item else ""

        tiktok_item = self.acc_table.item(row, 5)
        tiktok_id = tiktok_item.text().strip() if tiktok_item else ""
        profile_id = (
            profile_data.get("browser_id")
            or profile_data.get("gologin_profile_id")
            or tiktok_id
            or profile_name
        )
        return profile_name, profile_id, cookie_str, profile_data

    def _set_profile_row_background(self, row, color=None):
        brush = QBrush(QColor(color)) if color else QBrush()
        for col in range(self.acc_table.columnCount()):
            item = self.acc_table.item(row, col)
            if item is None and color:
                item = QTableWidgetItem("")
                self.acc_table.setItem(row, col, item)
            if item:
                item.setBackground(brush)

    def _set_channel_status_item(self, row, text, color="#334155", background=None):
        col = getattr(self, "channel_status_col", self.acc_table.columnCount() - 1)
        self.acc_table.removeCellWidget(row, col)
        item = QTableWidgetItem(text)
        item.setForeground(QColor(color))
        if background:
            item.setBackground(QBrush(QColor(background)))
        self.acc_table.setItem(row, col, item)

    def _is_violation_result(self, status, messages):
        text = f"{status}\n" + "\n".join(str(msg) for msg in messages)
        lowered = text.lower()
        return any(keyword in lowered for keyword in self.acc_manager.violation_keywords)

    def _set_channel_status_button(self, row, status, messages, is_violation, profile_name):
        col = getattr(self, "channel_status_col", self.acc_table.columnCount() - 1)
        cell_text = f"{status} ({len(messages)})"
        item = QTableWidgetItem(cell_text)
        item.setForeground(QColor("#dc2626" if is_violation else "#2563eb"))
        self.acc_table.setItem(row, col, item)

        button = QPushButton(f"Xem Hộp Thư ({len(messages)})")
        button.setCursor(Qt.PointingHandCursor)
        button.setFocusPolicy(Qt.NoFocus)
        button.setMinimumHeight(26)
        if is_violation:
            button.setStyleSheet(
                "QPushButton { background: #dc2626; color: white; font-weight: bold; "
                "border: 1px solid #991b1b; border-radius: 4px; padding: 4px 8px; }"
                "QPushButton:hover { background: #b91c1c; }"
            )
        else:
            button.setStyleSheet(
                "QPushButton { background: #2563eb; color: white; font-weight: bold; "
                "border: 1px solid #1d4ed8; border-radius: 4px; padding: 4px 8px; }"
                "QPushButton:hover { background: #1d4ed8; }"
            )

        button.clicked.connect(
            lambda checked=False, inbox_messages=list(messages), inbox_profile=profile_name:
            self.open_inbox_dialog(inbox_messages, inbox_profile)
        )
        self.acc_table.setCellWidget(row, col, button)
        self.acc_table.setRowHeight(row, max(self.acc_table.rowHeight(row), 34))

    def open_inbox_dialog(self, messages, profile_name=""):
        dialog = InboxDialog(messages, profile_name, self)
        dialog.exec_()

    def handle_check_violation_api(self):
        """Check System Inbox TikTok bằng API nền, không mở trình duyệt."""
        selected_rows = self.acc_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "Lỗi", "Vui lòng chọn ít nhất 1 tài khoản!")
            return

        for index in selected_rows:
            row = index.row()
            profile_name, profile_id, cookie_str, profile_data = self._get_profile_identity_and_cookie(row)
            self._set_profile_row_background(row)

            if not cookie_str or len(cookie_str) < 20:
                self._set_channel_status_item(row, "Không có cookie", "#ef4444")
                self.log(f"[{profile_name}] Không có cookie để check vi phạm.", "orange")
                continue

            self._set_channel_status_item(row, "Đang kiểm tra...", "#f59e0b", "#fff7ed")
            if profile_data:
                profile_data["channel_status"] = "Đang kiểm tra..."
                name_item = self.acc_table.item(row, 1)
                if name_item:
                    name_item.setData(Qt.UserRole, profile_data)

            worker = GenericWorker(
                self.acc_manager.check_violation_api,
                extra_data={"row": row, "profile_name": profile_name},
                pass_progress=False,
                profile_id=profile_id,
                cookie_path=cookie_str,
            )
            worker.finished.connect(self.on_check_violation_done)
            self._track_worker(worker)
            worker.start()
            self.log(f"[{profile_name}] Đang check vi phạm bằng API nền...", "blue")

    def on_check_violation_done(self, success, result, extra_data):
        worker = self.sender()
        if worker in self.active_workers:
            self.active_workers.remove(worker)

        row = extra_data.get("row") if isinstance(extra_data, dict) else extra_data
        profile_name = extra_data.get("profile_name", f"Row {row + 1}") if isinstance(extra_data, dict) else f"Row {row + 1}"
        if row is None or row < 0 or row >= self.acc_table.rowCount():
            return

        if success:
            if isinstance(result, (tuple, list)) and len(result) >= 2:
                status = str(result[0])
                messages = list(result[1] or [])
            else:
                status = str(result)
                messages = []
        else:
            status = "Lỗi kiểm tra"
            messages = []
            self.log(f"[{profile_name}] Lỗi check vi phạm: {result}", "red")

        is_violation = self._is_violation_result(status, messages)
        lowered_status = status.lower()

        name_item = self.acc_table.item(row, 1)
        if name_item:
            profile_data = dict(name_item.data(Qt.UserRole) or {})
            profile_data["channel_status"] = status
            profile_data["channel_inbox_messages"] = messages
            name_item.setData(Qt.UserRole, profile_data)

        if is_violation:
            self._set_profile_row_background(row, "#fee2e2")
        else:
            self._set_profile_row_background(row)

        if messages:
            self._set_channel_status_button(row, status, messages, is_violation, profile_name)
        elif status in ("Bình thường", "Không có thư 24h"):
            self._set_channel_status_item(row, status, "#16a34a")
        elif "cookie" in lowered_status or "lỗi" in lowered_status or "thiếu" in lowered_status:
            self._set_channel_status_item(row, status, "#ef4444")
        else:
            self._set_channel_status_item(row, status, "#2563eb")

        if is_violation:
            self.log(f"[{profile_name}] Có vi phạm trong Inbox 24h.", "red")
            for msg in messages:
                self.log(f"[{profile_name}] {msg}", "red")
        elif messages:
            self.log(f"[{profile_name}] Có {len(messages)} thông báo Inbox 24h.", "#2563eb")
        else:
            log_color = "#16a34a" if status in ("Bình thường", "Không có thư 24h") else "orange"
            self.log(f"[{profile_name}] {status}", log_color)

        self.save_accounts_to_db()

    def handle_download_video(self):
        """Xử lý tải video từ danh sách bảng video"""
        selected_rows = self.video_table.selectionModel().selectedRows()
        if not selected_rows:
            # Nếu không chọn, tải dòng đầu tiên
            if self.video_table.rowCount() > 0:
                selected_rows = [self.video_table.model().index(0, 0)]
            else:
                return

        for index in selected_rows:
            row = index.row()
            link_item = self.video_table.item(row, 6) # Cột Link gốc
            if not link_item or not link_item.text():
                self.log(f"Hàng {row}: Không có link video.", "yellow")
                continue
            
            url = link_item.text()
            self.log(f"Bắt đầu tải video từ link: {url}")
            self.video_table.setItem(row, 4, QTableWidgetItem("Đang tải..."))
            
            # Dùng MultiDownloader chạy ở background
            worker = GenericWorker(self.downloader.download_video, extra_data=row, pass_progress=False, url=url, output_path="./downloads")
            worker.progress.connect(self.log)
            worker.finished.connect(self.on_download_done)
            self._track_worker(worker)
            worker.start()

    def on_download_done(self, success, result, row):
        if success and not result.startswith("Lỗi"):
            self.log(f"Tải thành công: {result}", "#00ff00")
            self.video_table.setItem(row, 4, QTableWidgetItem("Đã tải"))
        else:
            self.log(f"Lỗi tải video: {result}", "red")
            self.video_table.setItem(row, 4, QTableWidgetItem("Lỗi tải"))

    def handle_update_info(self):
        """Cập nhật nhanh thông tin public của profile TikTok, không mở trình duyệt."""
        selected_rows = self.acc_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "Lỗi", "Vui lòng chọn ít nhất 1 tài khoản!")
            return

        reply = QMessageBox.question(
            self, "Xác nhận",
            f"Cập nhật nhanh thông tin public cho {len(selected_rows)} tài khoản đã chọn?\n"
            "Chức năng này không mở trình duyệt và chỉ lấy dữ liệu public.",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        username_by_row = {}
        profile_requests = []
        for index in selected_rows:
            row = index.row()
            username_item = self.acc_table.item(row, 5)
            username = username_item.text().strip() if username_item else ""
            name_item = self.acc_table.item(row, 1)
            profile_data = name_item.data(Qt.UserRole) if name_item else {}
            if not username:
                username = (profile_data or {}).get("tiktok_id", "")
            if not username:
                self.acc_table.setItem(row, 12, QTableWidgetItem("Thiếu ID TikTok"))
                self.log(f"Hàng {row+1}: thiếu ID TikTok, bỏ qua", "orange")
                continue
            username_by_row[row] = username
            cookie_item = self.acc_table.item(row, 13)
            cookie_str = cookie_item.text().strip() if cookie_item else ""
            if not cookie_str and isinstance(profile_data, dict):
                cookie_str = profile_data.get("cookie", "")
            profile_requests.append({"key": str(row), "username": username, "cookie": cookie_str})
            self.acc_table.setItem(row, 12, QTableWidgetItem("Đang cập nhật..."))

        if not username_by_row:
            return

        from tiktok_fast_profile import update_all_profiles_async

        self.log(f"Bắt đầu cập nhật nhanh thông tin cho {len(username_by_row)} profile TikTok...", "blue")
        worker = GenericWorker(
            update_all_profiles_async,
            extra_data=username_by_row,
            pass_progress=False,
            username_list=profile_requests,
            max_workers=15,
        )
        worker.finished.connect(self.on_fast_update_stats_done)
        self._track_worker(worker)
        worker.start()

    # ================= LOGIC 3 TÍNH NĂNG TRÌNH DUYỆT =================

    def handle_change_account_info(self):
        """1. Đổi thông tin tài khoản"""
        selected_rows = self.acc_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "Lỗi", "Vui lòng chọn 1 tài khoản!")
            return
        row = selected_rows[0].row()
        self.log(f"Đang chạy tự động đổi thông tin tài khoản cho hàng {row}...")
        
        worker = GenericWorker(self.auto_change_info_task, extra_data=row, pass_progress=True)
        worker.progress.connect(self.log)
        worker.finished.connect(lambda success, res, r: self.log(f"Đổi thông tin xong: {res}", "#00ff00" if success else "red"))
        self._track_worker(worker)
        worker.start()

    def handle_login_account(self):
        """2. Đăng nhập tài khoản này"""
        selected_rows = self.acc_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "Lỗi", "Vui lòng chọn 1 tài khoản!")
            return
        row = selected_rows[0].row()
        
        # Lấy lại toàn bộ Data đã lưu ở hàm Add_profile_to_table
        name_item = self.acc_table.item(row, 1)
        profile_data = name_item.data(Qt.UserRole) if name_item else {}
        if not profile_data:
            profile_data = {} # Fallback
            
        cookie_val = self.acc_table.item(row, 13) # Cột Cookie
        cookie_str = cookie_val.text() if cookie_val else ""
        
        # Bổ sung cookie hiện tại của bảng vào data truyền đi
        profile_data['cookie'] = cookie_str 
        
        self.log(f"Đang khởi động trình duyệt để đăng nhập tự động cho hàng {row}...")
        
        worker = GenericWorker(self.auto_login_task, extra_data=row, pass_progress=True, profile_data=profile_data)
        worker.progress.connect(self.log)
        worker.finished.connect(self.on_login_done)
        self._track_worker(worker)
        worker.start()

    def on_login_done(self, success, result, row):
        """Cập nhật UI sau khi đăng nhập thành công"""
        if success and isinstance(result, dict):
            # Kết quả trả về là 1 dict chứa thông tin sau đăng nhập
            self.log(f"Đăng nhập thành công cho hàng {row+1}", "#00ff00")
            
            # 1. Cập nhật Logged = Yes CHỈ KHI có TikTok ID
            # 2. Cập nhật ID Tiktok
            if result.get("id_tiktok"):
                self.acc_table.setItem(row, 2, QTableWidgetItem("Yes"))
                self.acc_table.setItem(row, 5, QTableWidgetItem(result["id_tiktok"]))
                
            # 3. Lưu Cookie mới
            if result.get("new_cookie"):
                self.acc_table.setItem(row, 13, QTableWidgetItem(result["new_cookie"]))
                # Cập nhật lại vào UserRole data
                name_item = self.acc_table.item(row, 1)
                if name_item:
                    data = name_item.data(Qt.UserRole)
                    data['cookie'] = result["new_cookie"]
                    name_item.setData(Qt.UserRole, data)
            
            self.save_accounts_to_db()
        else:
            self.log(f"Lỗi đăng nhập hàng {row+1}: {result}", "red")

    def handle_open_browser(self):
        """3. Mở trình duyệt GoLogin và nhúng vào dashboard."""
        selected_rows = self.acc_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "Lỗi", "Vui lòng chọn ít nhất 1 tài khoản!")
            return

        accounts_data = self._collect_selected_accounts_data(selected_rows)
        if not accounts_data:
            self.log("Không lấy được dữ liệu profile để mở browser.", "red")
            return

        self.log(f"Mở GoLogin dashboard với {len(accounts_data)} profile đã chọn.", "#00ff00")
        self._show_automation_dashboard(
            accounts_data,
            project_name=self._current_project_name(),
            dashboard_key=self._dashboard_key_for_project(self._current_project_name()),
            after_show=lambda dashboard: dashboard.on_open_browser()
        )

    def _collect_selected_accounts_data(self, selected_rows):
        return self._collect_accounts_for_rows(index.row() for index in selected_rows)

    def _get_recycle_profile_info(self, row):
        name_item = self.acc_table.item(row, 1)
        profile_name = name_item.text() if name_item else f"Row {row + 1}"
        profile_data = dict(name_item.data(Qt.UserRole) or {}) if name_item else {}

        id_item = self.acc_table.item(row, 4)
        table_profile_id = id_item.text().strip() if id_item else ""
        profile_id = (
            profile_data.get("gologin_profile_id")
            or profile_data.get("browser_id")
            or table_profile_id
            or ""
        ).strip()

        return profile_name, profile_id, profile_data

    def _get_recycle_local_profile_dir(self, profile_id, profile_name=""):
        safe_name = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in (profile_name or ""))
        candidates = [
            str(gologin_profiles_root() / f"profile_{profile_id}"),
            os.path.join("D:\\", f"profile_{profile_id}"),
            os.path.join(r"D:\Testgologin", f"profile_{profile_id}"),
        ]
        if safe_name:
            candidates.append(str(named_browser_profile_dir(profile_name)))
            candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser_profiles", safe_name))

        for candidate in candidates:
            if os.path.isdir(candidate):
                return candidate
        return candidates[0]

    def recycle_gologin_profile(self, profile_id, api_token, local_profile_dir, progress_callback=None):
        """Random lại fingerprint GoLogin và xóa sạch dữ liệu local của profile."""
        import shutil
        import requests

        def progress(message):
            if progress_callback:
                progress_callback(message)

        profile_id = (profile_id or "").strip()
        api_token = (api_token or "").strip()
        if not profile_id:
            return {"ok": False, "message": "Thiếu GoLogin profile ID."}
        if not api_token:
            return {"ok": False, "message": "Thiếu GoLogin API key."}

        headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }
        payload = {"browsersIds": [profile_id]}

        progress("Đang random lại vân tay GoLogin...")
        try:
            response = requests.patch(
                "https://api.gologin.com/browser/fingerprints",
                headers=headers,
                json=payload,
                timeout=30,
            )
        except Exception as exc:
            return {"ok": False, "message": f"Lỗi kết nối GoLogin API: {exc}"}

        if response.status_code not in (200, 201, 204):
            detail = (response.text or "").strip().replace("\n", " ")[:300]
            return {"ok": False, "message": f"GoLogin API lỗi HTTP {response.status_code}: {detail}"}

        profile_dir = os.path.abspath(local_profile_dir or "")
        basename = os.path.basename(profile_dir).lower()
        if not profile_dir or not basename.startswith("profile_") or profile_id.lower() not in basename:
            return {"ok": False, "message": f"Đường dẫn profile local không an toàn: {profile_dir}"}

        deleted_items = 0
        progress(f"Đang xóa local data: {profile_dir}")
        try:
            os.makedirs(profile_dir, exist_ok=True)
            profile_root = os.path.abspath(profile_dir)
            for child_name in os.listdir(profile_root):
                child_path = os.path.abspath(os.path.join(profile_root, child_name))
                if not child_path.startswith(profile_root + os.sep):
                    return {"ok": False, "message": f"Đường dẫn con không an toàn: {child_path}"}

                if os.path.isdir(child_path) and not os.path.islink(child_path):
                    shutil.rmtree(child_path)
                else:
                    os.remove(child_path)
                deleted_items += 1
            os.makedirs(profile_root, exist_ok=True)
        except PermissionError as exc:
            return {
                "ok": False,
                "message": f"Không xóa được local data vì profile/browser còn đang mở: {exc}",
            }
        except Exception as exc:
            return {"ok": False, "message": f"Lỗi dọn local data: {exc}"}

        return {
            "ok": True,
            "message": "Đã tái chế profile thành công.",
            "profile_id": profile_id,
            "local_profile_dir": profile_dir,
            "deleted_items": deleted_items,
        }

    def handle_recycle_profile(self):
        """Tái chế GoLogin profile: đổi fingerprint và xóa sạch local data."""
        selected_rows = self.acc_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "Lỗi", "Vui lòng chọn ít nhất 1 profile!")
            return

        reply = QMessageBox.warning(
            self,
            "Cảnh báo tái chế Profile",
            "Hành động này sẽ XÓA SẠCH toàn bộ Cookie, lịch sử và thay đổi vân tay "
            "phần cứng của Profile đã chọn. Bạn không thể hoàn tác.\n\n"
            "Bạn có chắc chắn muốn tiếp tục?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        settings = load_gologin_settings()
        api_token = (settings.get("api_key") or "").strip()

        for index in selected_rows:
            row = index.row()
            profile_name, profile_id, profile_data = self._get_recycle_profile_info(row)
            if not profile_id:
                self.acc_table.setItem(row, 12, QTableWidgetItem("Thiếu profile ID"))
                self._set_channel_status_item(row, "Thiếu profile ID", "#ef4444")
                self.log(f"[{profile_name}] Không có GoLogin profile ID để tái chế.", "red")
                continue

            status_item = QTableWidgetItem("Đang tái chế...")
            status_item.setForeground(QColor("#f59e0b"))
            self.acc_table.setItem(row, 12, status_item)
            self._set_channel_status_item(row, "Đang tái chế...", "#f59e0b", "#fff7ed")

            local_profile_dir = self._get_recycle_local_profile_dir(profile_id, profile_name)
            worker = GenericWorker(
                self.recycle_gologin_profile,
                extra_data={"row": row, "profile_name": profile_name},
                pass_progress=True,
                profile_id=profile_id,
                api_token=api_token,
                local_profile_dir=local_profile_dir,
            )
            worker.progress.connect(lambda msg, name=profile_name: self.log(f"[{name}] {msg}", "orange"))
            worker.finished.connect(self.on_recycle_profile_done)
            self._track_worker(worker)
            worker.start()
            self.log(f"[{profile_name}] Bắt đầu tái chế profile {profile_id}...", "blue")

    def on_recycle_profile_done(self, success, result, extra_data):
        worker = self.sender()
        if worker in self.active_workers:
            self.active_workers.remove(worker)

        row = extra_data.get("row") if isinstance(extra_data, dict) else extra_data
        profile_name = extra_data.get("profile_name", f"Row {row + 1}") if isinstance(extra_data, dict) else f"Row {row + 1}"
        if row is None or row < 0 or row >= self.acc_table.rowCount():
            return

        ok = bool(success and isinstance(result, dict) and result.get("ok"))
        if ok:
            name_item = self.acc_table.item(row, 1)
            if name_item:
                profile_data = dict(name_item.data(Qt.UserRole) or {})
                profile_data["cookie"] = ""
                profile_data["channel_status"] = "Sẵn sàng (Đã tái chế)"
                profile_data["channel_inbox_messages"] = []
                name_item.setData(Qt.UserRole, profile_data)

            self.acc_table.setItem(row, 2, QTableWidgetItem("No"))
            self.acc_table.setItem(row, 5, QTableWidgetItem(""))
            self.acc_table.setItem(row, 12, QTableWidgetItem("Trống"))
            self.acc_table.setItem(row, 13, QTableWidgetItem(""))
            self._set_channel_status_item(row, "Sẵn sàng (Đã tái chế)", "#2563eb")
            self._set_profile_row_background(row)
            self.log(
                f"[{profile_name}] Đã tái chế xong. Local data đã xóa: {result.get('deleted_items', 0)} mục.",
                "#2563eb",
            )
            self.save_accounts_to_db()
            return

        message = result.get("message") if isinstance(result, dict) else str(result)
        self.acc_table.setItem(row, 12, QTableWidgetItem("Lỗi tái chế"))
        self._set_channel_status_item(row, "Lỗi tái chế", "#ef4444")
        self.log(f"[{profile_name}] Lỗi tái chế profile: {message}", "red")
        self.save_accounts_to_db()

    def handle_re_fingerprint(self):
        """Compatibility wrapper: Re-Fingerprint cũ nay chạy luồng GoLogin recycle."""
        self.handle_recycle_profile()

    def handle_clear_cache(self):
        """6. Xóa toàn bộ Cache & Cookie"""
        selected_rows = self.acc_table.selectionModel().selectedRows()
        if not selected_rows: return
        for index in selected_rows:
            row = index.row()
            self.acc_table.setItem(row, 13, QTableWidgetItem("")) # Xóa cột Cookie
            name_item = self.acc_table.item(row, 1)
            if name_item:
                data = name_item.data(Qt.UserRole) or {}
                data['cookie'] = ""
                name_item.setData(Qt.UserRole, data)
            self.log(f"Đã làm sạch môi trường (Xóa Cache/Cookie) cho hàng {row+1}.", "yellow")
        self.save_accounts_to_db()

    # --- CÁC HÀM ASYNC CHẠY PLAYWRIGHT ---
    
    def _get_profile_dir(self, profile_name):
        """Tạo thư mục profile riêng cho mỗi tài khoản (giống GoLogin)"""
        safe_name = "".join(c if c.isalnum() or c in ('_', '-') else '_' for c in profile_name)
        profile_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser_profiles", safe_name)
        os.makedirs(profile_dir, exist_ok=True)
        return profile_dir

    def _get_tiktok_code_via_imap(self, email, password, progress_callback=None):
        try:
            from imap_tools import MailBox, AND
            import re
        except ImportError:
            if progress_callback: progress_callback("[X] Thiếu thư viện imap_tools, vui lòng cài đặt: pip install imap-tools")
            return None
            
        if progress_callback: progress_callback(f"[*] Đang chui vào hòm thư {email} bằng IMAP...")
        try:
            # Xác định server IMAP
            imap_server = 'outlook.office365.com'
            if 'gmail.com' in email.lower(): imap_server = 'imap.gmail.com'
                
            with MailBox(imap_server).login(email, password) as mailbox:
                # Tìm email gửi từ TikTok mới nhất chưa đọc
                for msg in mailbox.fetch(AND(from_="tiktok", seen=False), reverse=True, limit=1):
                    subject = msg.subject
                    body = msg.text or msg.html
                    
                    if progress_callback: progress_callback(f"[*] Bắt được mail TikTok: {subject}")
                    
                    # Dùng Regex móc OTP (6 chữ số)
                    match = re.search(r'\b(\d{6})\b', subject + body)
                    if match:
                        code = match.group(1)
                        if progress_callback: progress_callback(f"[*] Thành công! Mã OTP là: {code}")
                        mailbox.flag(msg.uid, '\\Seen', True) # Đánh dấu đã đọc
                        return code
                        
            if progress_callback: progress_callback("[X] Chưa thấy mail TikTok nào mới tới.")
            return None
        except Exception as e:
            if progress_callback: progress_callback(f"[X] Lỗi đăng nhập IMAP (Sai pass hoặc bị khóa): {e}")
            return None

    def _find_orbita_path(self):
        return require_orbita_browser_exe()

    def _preserve_profile_fingerprint(self, profile_data=None):
        data = profile_data if isinstance(profile_data, dict) else {}
        strict_override = data.get("gologin_passthrough_strict")
        if strict_override is not None:
            text = str(strict_override).strip().lower()
            if text in {"1", "true", "yes", "y", "on"}:
                return True
            if text in {"0", "false", "no", "n", "off"}:
                return False
        return bool((data.get("gologin_profile_id") or "").strip())

    async def auto_change_info_task(self, progress_callback=None):
        if progress_callback: progress_callback("Khởi tạo Playwright (Trình duyệt hiện lên)...")
        from playwright.async_api import async_playwright
        from profile_editor import update_tiktok_profile
        orbita_path = require_orbita_browser_exe()
        async with async_playwright() as p:
            browser = await p.chromium.launch(executable_path=orbita_path, headless=False)
            page = await browser.new_page()
            if progress_callback: progress_callback("Đang gọi module 'profile_editor' để sửa Bio và Avatar...")
            success = await update_tiktok_profile(page, "Bio mới từ Tool Auto", "dummy_avatar.jpg")
            await browser.close()
            return success

    async def auto_login_task(self, profile_data, progress_callback=None):
        """Đăng nhập TikTok: Ưu tiên Cookie → nếu không có thì dùng Email/Password"""
        import random
        import subprocess
        import socket
        
        username = profile_data.get('username', '')
        password = profile_data.get('password', '')
        cookie_str = profile_data.get('cookie', '')
        profile_name = profile_data.get('ten_ho_so', 'default')
        preserve_fp = self._preserve_profile_fingerprint(profile_data)
        
        # ƯU TIÊN 1: Đăng nhập bằng Cookie (bypass hoàn toàn form login)
        if cookie_str and len(cookie_str) > 20:
            if progress_callback: progress_callback("Phát hiện Cookie → Đăng nhập bằng Cookie (không cần email/password)...")
            return await self._login_with_cookie(profile_data, progress_callback)
        
        # ƯU TIÊN 2: Đăng nhập bằng Email/Password
        if not username or not password:
            return "Lỗi: Tài khoản này chưa có Cookie hoặc Username/Password."

        browser_id = profile_data.get('browser_id', '')
        is_adspower = False
        chrome_process = None
        debugger_address = ""

        if browser_id:
            if progress_callback: progress_callback(f"Khởi động AdsPower profile ID: {browser_id}...")
            import requests
            api_url = f"http://local.adspower.net:50325/api/v1/browser/start?user_id={browser_id}"
            try:
                response = requests.get(api_url)
                data = response.json()
                if data.get("code") != 0:
                    return f"Lỗi mở trình duyệt AdsPower: {data.get('msg')}"
                debugger_address = data["data"]["ws"]["puppeteer"]
                is_adspower = True
            except Exception as e:
                return f"Lỗi kết nối Local API AdsPower: {e}"
        else:
            return "Lỗi: Tài khoản chưa được gắn ID AdsPower. Vui lòng tạo tài khoản qua form Add Profile để có ID AdsPower!"
        
        # Bước 5: Kết nối Playwright qua CDP
        from playwright.async_api import async_playwright
        
        try:
            async with async_playwright() as p:
                if progress_callback: progress_callback("Kết nối qua CDP...")
                browser = await p.chromium.connect_over_cdp(debugger_address)
                
                context = browser.contexts[0]
                page = context.pages[0] if context.pages else await context.new_page()
                
                if preserve_fp:
                    if progress_callback: progress_callback("GoLogin pass-through → bo qua geolocation/stealth override.")
                else:
                    try:
                        await context.grant_permissions(['geolocation'])
                        await context.set_geolocation({"longitude": -74.0059, "latitude": 40.7128})
                    except Exception:
                        pass

                    try:
                        from playwright_stealth import stealth_async
                        await stealth_async(page)
                    except ImportError:
                        pass

                async def inject_virtual_cursor(p):
                    cursor_js = """
                    () => {
                        if (document.getElementById('playwright-cursor')) return;
                        const cursor = document.createElement('div');
                        cursor.id = 'playwright-cursor';
                        cursor.style.width = '20px';
                        cursor.style.height = '20px';
                        cursor.style.borderRadius = '50%';
                        cursor.style.backgroundColor = 'rgba(255, 0, 0, 0.5)';
                        cursor.style.position = 'absolute';
                        cursor.style.pointerEvents = 'none';
                        cursor.style.zIndex = '999999';
                        cursor.style.transition = 'top 0.1s, left 0.1s';
                        document.body.appendChild(cursor);

                        document.addEventListener('mousemove', (e) => {
                            cursor.style.left = e.pageX + 'px';
                            cursor.style.top = e.pageY + 'px';
                        });
                    }
                    """
                    await p.evaluate(cursor_js)

                async def human_typing(p, selector, text):
                    await p.click(selector)
                    await asyncio.sleep(random.uniform(0.1, 0.3))
                    for char in text:
                        await p.keyboard.press(char)
                        await asyncio.sleep(random.uniform(0.05, 0.15))

                # Vào trang chủ TikTok trước
                if progress_callback: progress_callback("Truy cập TikTok trang chủ...")
                await page.goto("https://www.tiktok.com/", wait_until="domcontentloaded")
                await inject_virtual_cursor(page)
                await asyncio.sleep(random.uniform(3.0, 5.0))
                
                # Scroll như người thật
                await page.mouse.move(random.randint(200, 800), random.randint(200, 400))
                await page.mouse.wheel(0, random.randint(100, 300))
                await asyncio.sleep(random.uniform(1.0, 2.0))
                
                # Điều hướng đến form đăng nhập
                if progress_callback: progress_callback("Điều hướng tới form đăng nhập...")
                await page.goto("https://www.tiktok.com/login/phone-or-email/email", wait_until="domcontentloaded")
                await inject_virtual_cursor(page)
                await asyncio.sleep(random.uniform(2.0, 3.0))
                
                try:
                    # Gõ username
                    if progress_callback: progress_callback(f"Gõ Username: {username[:3]}***...")
                    await page.wait_for_selector('input[name="username"], input[type="text"]', timeout=10000)
                    await human_typing(page, 'input[name="username"], input[type="text"]', username)
                    await asyncio.sleep(random.uniform(0.8, 1.5))
                    
                    # Gõ password
                    if progress_callback: progress_callback("Gõ Password...")
                    pwd_input = await page.query_selector('input[type="password"]')
                    if pwd_input:
                        await human_typing(page, 'input[type="password"]', password)
                    
                    await asyncio.sleep(random.uniform(1.0, 2.0))
                    
                    # Click nút Login bằng chuột (Chrome thật không bị check event listener)
                    if progress_callback: progress_callback("Nhấn nút Đăng nhập...")
                    login_btn = await page.query_selector('button[data-e2e="login-button"]')
                    if login_btn:
                        box = await login_btn.bounding_box()
                        if box:
                            # Di chuột tới nút rồi click (giả lập tự nhiên)
                            await page.mouse.move(
                                box['x'] + box['width'] / 2 + random.uniform(-5, 5),
                                box['y'] + box['height'] / 2 + random.uniform(-2, 2),
                                steps=random.randint(10, 25)
                            )
                            await asyncio.sleep(random.uniform(0.3, 0.6))
                            await page.mouse.click(
                                box['x'] + box['width'] / 2,
                                box['y'] + box['height'] / 2
                            )
                    else:
                        await page.keyboard.press('Enter')
                    
                    if progress_callback: progress_callback("Đang chờ phản hồi (Tối đa 120s để bạn giải Captcha/OTP nếu có)...")
                    
                    # Kiểm tra đăng nhập thành công hoặc có đòi OTP không
                    login_success = False
                    has_clicked_send_email = False # Cờ để chỉ click nút Gửi Email 1 lần duy nhất
                    
                    for _ in range(40): # Đợi tối đa 40 * 3 = 120s
                        try:
                            # 1. Kiểm tra nếu đã login thành công
                            if await page.query_selector('div[data-e2e="profile-icon"], a[data-e2e="upload-icon"], [data-e2e="nav-profile"]'):
                                login_success = True
                                break
                            
                            # 1.5 Kiểm tra xem có popup "Xác minh đó là bạn" chặn ngang không
                            if not has_clicked_send_email:
                                try:
                                    # Chờ xem cái nút chọn "Email" có xuất hiện không
                                    # Dùng get_by_text cho an toàn vì TikTok hay đổi class name
                                    btn_chon_email = page.get_by_text("Email", exact=True)
                                    if await btn_chon_email.is_visible():
                                        if progress_callback: progress_callback("[*] Dính 2FA rồi! Đang click chọn gửi mã về Email...")
                                        await btn_chon_email.click()
                                        has_clicked_send_email = True
                                        await asyncio.sleep(4) # Đợi 4 giây cho TikTok xoay xoay và gửi mail đi
                                except Exception:
                                    pass

                            # 2. Kiểm tra nếu TikTok đã vào đến màn hình đòi gõ OTP
                            is_otp_required = False
                            try:
                                # Kiểm tra xem dòng chữ báo nhập mã CÓ ĐANG THỰC SỰ HIỂN THỊ trên màn hình không
                                # Không dùng page.content() nữa vì TikTok giấu sẵn các đoạn mã OTP bị ẩn bằng CSS
                                for text_check in ["6-digit", "mã 6", "gửi lại mã", "resend code"]:
                                    locator = page.get_by_text(text_check)
                                    # Dùng .first để tránh lỗi StrictMode nếu có nhiều cụm từ giống nhau
                                    if await locator.first.is_visible():
                                        is_otp_required = True
                                        break
                            except Exception:
                                pass
                                    
                            if is_otp_required:
                                if progress_callback: progress_callback("[*] Đã yêu cầu TikTok gửi mã. Bắt đầu dùng IMAP chui vào Hotmail lấy mã...")
                                await asyncio.sleep(7) # Đợi 7s cho mail bay tới
                                
                                # Ưu tiên dùng Password mail riêng (nếu người dùng có nhập), ngược lại dùng pass TikTok
                                password_mail = profile_data.get('password_mail', '').strip()
                                imap_pass = password_mail if password_mail else password
                                
                                otp_code = self._get_tiktok_code_via_imap(username, imap_pass, progress_callback)
                                
                                if otp_code:
                                    if progress_callback: progress_callback(f"[*] Tuyệt vời! Đã lấy được mã: {otp_code}. Đang điền vào TikTok...")
                                    
                                    # Click vào ô nhập mã đầu tiên để focus
                                    try:
                                        first_input = page.locator('input[type="text"]').first
                                        await first_input.click(timeout=3000)
                                    except:
                                        pass # Nếu không tìm thấy input thì gõ mù luôn (TikTok auto focus)
                                        
                                    await page.keyboard.type(otp_code, delay=150) # Gõ từng số delay 0.15s
                                    
                                    if progress_callback: progress_callback("[*] Điền mã xong! Chờ TikTok load vào trang chủ...")
                                    await asyncio.sleep(5)
                                    # Vòng lặp tiếp theo sẽ tự check lại xem login thành công chưa
                                else:
                                    if progress_callback: progress_callback("[X] IMAP không tìm thấy mã. Có thể Hotmail lỗi hoặc thư đến chậm.")
                                    # Nếu chưa có mail, đợi vòng lặp sau check lại (nó sẽ lại ngủ 3s + 7s)
                                    pass
                        except Exception:
                            pass
                        
                        await asyncio.sleep(3)

                    if not login_success:
                        await browser.close()
                        if is_adspower:
                            import requests
                            try: requests.get(f"http://local.adspower.net:50325/api/v1/browser/stop?user_id={browser_id}")
                            except: pass
                        elif chrome_process: chrome_process.terminate()
                        return "Lỗi: Đăng nhập thất bại (Hết thời gian chờ hoặc bị kẹt ở Captcha/OTP)."
                    
                    # Đăng nhập thành công!
                    if progress_callback: progress_callback("✅ Đăng nhập thành công! Trích xuất thông tin...")
                    
                    # Lấy Cookie thật
                    current_cookies = await context.cookies()
                    new_cookie = "; ".join([f"{c['name']}={c['value']}" for c in current_cookies])
                    
                    # Cào ID TikTok
                    extracted_id = ""
                    try:
                        profile_links = await page.query_selector_all('a[href*="/@"]')
                        for link in profile_links:
                            href = await link.get_attribute("href")
                            if href and "/@" in href and "video" not in href:
                                extracted_id = href.split('/@')[-1].split('?')[0]
                                break
                    except:
                        pass
                    if not extracted_id:
                        extracted_id = username.split('@')[0] if '@' in username else username
                    
                    await browser.close()
                    if is_adspower:
                        import requests
                        try: requests.get(f"http://local.adspower.net:50325/api/v1/browser/stop?user_id={browser_id}")
                        except: pass
                    elif chrome_process: chrome_process.terminate()
                    return {
                        "status": "success",
                        "new_cookie": new_cookie,
                        "id_tiktok": extracted_id
                    }
                    
                except Exception as e:
                    if progress_callback: progress_callback(f"Lỗi thao tác trình duyệt: {str(e)}")
                    try: await browser.close()
                    except: pass
                    if is_adspower:
                        import requests
                        try: requests.get(f"http://local.adspower.net:50325/api/v1/browser/stop?user_id={browser_id}")
                        except: pass
                    elif chrome_process: 
                        try: chrome_process.terminate()
                        except: pass
                    return f"Lỗi: {str(e)}"
                    
        except Exception as e:
            if is_adspower:
                import requests
                try: requests.get(f"http://local.adspower.net:50325/api/v1/browser/stop?user_id={browser_id}")
                except: pass
            elif chrome_process:
                try: chrome_process.terminate()
                except: pass
            if progress_callback: progress_callback(f"Lỗi kết nối/Automation: {str(e)}")
            return f"Lỗi: {str(e)}"

    async def _login_with_cookie(self, profile_data, progress_callback=None):
        """Đăng nhập TikTok bằng Cookie — bypass hoàn toàn form login"""
        import subprocess, socket, random
        from playwright.async_api import async_playwright
        
        cookie_str = profile_data.get('cookie', '')
        profile_name = profile_data.get('ten_ho_so', 'default')
        username = profile_data.get('username', '')
        
        # Parse cookie string thành danh sách dict cho Playwright
        cookies_list = []
        for pair in cookie_str.split(';'):
            pair = pair.strip()
            if '=' in pair:
                name, value = pair.split('=', 1)
                cookies_list.append({
                    'name': name.strip(),
                    'value': value.strip(),
                    'domain': '.tiktok.com',
                    'path': '/'
                })
        
        if not cookies_list:
            return "Lỗi: Cookie không hợp lệ (không parse được)."
        
        if progress_callback: progress_callback(f"Đã parse được {len(cookies_list)} cookie. Đang mở trình duyệt...")
        
        # Chi dung Orbita/GoLogin browser, khong fallback sang Chrome he thong.
        try:
            orbita_path = self._find_orbita_path()
        except FileNotFoundError as e:
            return f"Loi: {e}"
        profile_dir = self._get_profile_dir(profile_name)
        
        if orbita_path:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(('', 0))
            debug_port = sock.getsockname()[1]
            sock.close()
            
            browser_args = [
                orbita_path,
                f'--remote-debugging-port={debug_port}',
                f'--user-data-dir={profile_dir}',
                '--no-first-run',
                '--no-default-browser-check',
                '--window-size=1280,720',
                'about:blank'
            ]
            chrome_process = subprocess.Popen(browser_args)
            await asyncio.sleep(3)
            
            try:
                async with async_playwright() as p:
                    if progress_callback: progress_callback("Ket noi vao Orbita/GoLogin browser...")
                    browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{debug_port}")
                    context = browser.contexts[0]
                    page = context.pages[0] if context.pages else await context.new_page()
                    
                    # Chèn cookie TRƯỚC khi truy cập TikTok
                    if progress_callback: progress_callback("Đang chèn Cookie vào trình duyệt...")
                    await context.add_cookies(cookies_list)
                    
                    # Truy cập TikTok — nếu cookie hợp lệ, sẽ vào thẳng trang chủ đã đăng nhập
                    if progress_callback: progress_callback("Truy cập TikTok với Cookie...")
                    await page.goto("https://www.tiktok.com/", wait_until="domcontentloaded")
                    await asyncio.sleep(random.uniform(3.0, 5.0))
                    
                    # Kiểm tra đăng nhập thành công
                    login_success = False
                    try:
                        # 1. Tìm Nút Upload hoặc Profile Icon
                        await page.wait_for_selector(
                            'a[href*="/upload"], div[data-e2e="profile-icon"], [data-e2e="nav-profile"], a[aria-label^="Upload"]',
                            state="visible", timeout=15000
                        )
                        login_success = True
                    except:
                        pass
                        
                    # 2. Check chéo: Nếu không thấy nút Login ở góc phải nữa thì 99% là đã đăng nhập
                    if not login_success:
                        login_btn = await page.query_selector('button[data-e2e="top-login-button"], button#header-login-button')
                        if not login_btn:
                            login_success = True
                            
                    if not login_success:
                        if progress_callback: progress_callback("Cookie đã hết hạn hoặc không hợp lệ!")
                        await browser.close()
                        chrome_process.terminate()
                        return "Lỗi: Cookie không hợp lệ hoặc đã hết hạn. Vui lòng cập nhật Cookie mới."
                    
                    # Thành công!
                    if progress_callback: progress_callback("✅ Đăng nhập bằng Cookie thành công!")
                    
                    # Lấy cookie mới (refresh)
                    current_cookies = await context.cookies()
                    new_cookie = "; ".join([f"{c['name']}={c['value']}" for c in current_cookies])
                    
                    # Cào ID TikTok
                    extracted_id = ""
                    try:
                        profile_links = await page.query_selector_all('a[href*="/@"]')
                        for link in profile_links:
                            href = await link.get_attribute("href")
                            if href and "/@" in href and "video" not in href:
                                extracted_id = href.split('/@')[-1].split('?')[0]
                                break
                    except:
                        pass
                    if not extracted_id:
                        extracted_id = username.split('@')[0] if '@' in username else username
                    
                    await browser.close()
                    chrome_process.terminate()
                    return {"status": "success", "new_cookie": new_cookie, "id_tiktok": extracted_id}
                    
            except Exception as e:
                chrome_process.terminate()
                return f"Lỗi Cookie Login: {str(e)}"
        else:
            # Fallback: Dùng Playwright persistent context
            async with async_playwright() as p:
                context = await p.chromium.launch_persistent_context(
                    profile_dir, headless=False, executable_path=orbita_path,
                    viewport={'width': 1280, 'height': 720}
                )
                page = context.pages[0] if context.pages else await context.new_page()
                
                await context.add_cookies(cookies_list)
                await page.goto("https://www.tiktok.com/", wait_until="domcontentloaded")
                await asyncio.sleep(5)
                
                login_success = False
                try:
                    await page.wait_for_selector(
                        'a[href*="/upload"], div[data-e2e="profile-icon"], [data-e2e="nav-profile"], a[aria-label^="Upload"]',
                        state="visible", timeout=60000
                    )
                    login_success = True
                except:
                    pass
                    
                if not login_success:
                    login_btn = await page.query_selector('button[data-e2e="top-login-button"], button#header-login-button')
                    if not login_btn:
                        login_success = True
                
                if not login_success:
                    await context.close()
                    return "Lỗi: Cookie không hợp lệ hoặc đã hết hạn."
                
                current_cookies = await context.cookies()
                new_cookie = "; ".join([f"{c['name']}={c['value']}" for c in current_cookies])
                extracted_id = username.split('@')[0] if '@' in username else username
                
                await context.close()
                return {"status": "success", "new_cookie": new_cookie, "id_tiktok": extracted_id}

    async def _login_with_persistent_context(self, profile_data, progress_callback=None):
        """Phương pháp dự phòng: Dùng Playwright persistent context"""
        from playwright.async_api import async_playwright
        import random
        
        username = profile_data.get('username', '')
        password = profile_data.get('password', '')
        profile_name = profile_data.get('ten_ho_so', 'default')
        profile_dir = self._get_profile_dir(profile_name)
        orbita_path = require_orbita_browser_exe()
        preserve_fp = self._preserve_profile_fingerprint(profile_data)
        context_args = []
        if not preserve_fp:
            context_args = [
                '--disable-blink-features=AutomationControlled',
                '--disable-infobars',
                '--no-sandbox'
            ]
        
        async with async_playwright() as p:
            if progress_callback: progress_callback("Mở Persistent Browser Context...")
            context = await p.chromium.launch_persistent_context(
                profile_dir,
                headless=False,
                executable_path=orbita_path,
                viewport={'width': 1280, 'height': 720},
                args=context_args,
            )
            
            page = context.pages[0] if context.pages else await context.new_page()
            if preserve_fp:
                if progress_callback: progress_callback("GoLogin pass-through → bo qua stealth patch.")
            else:
                try:
                    from playwright_stealth import Stealth
                    await Stealth().apply_stealth_async(page)
                except ImportError:
                    pass
            
            if progress_callback: progress_callback("Truy cập TikTok...")
            await page.goto("https://www.tiktok.com/", wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(2.0, 4.0))
            
            await page.goto("https://www.tiktok.com/login/phone-or-email/email", wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(1.5, 2.5))
            
            try:
                if progress_callback: progress_callback("Gõ thông tin đăng nhập...")
                await page.type('input[name="username"]', username, delay=random.randint(80, 180))
                await asyncio.sleep(random.uniform(0.8, 1.5))
                
                pwd_input = await page.query_selector('input[type="password"]')
                if pwd_input:
                    await pwd_input.click()
                    await pwd_input.type(password, delay=random.randint(80, 150))
                
                await asyncio.sleep(random.uniform(1.0, 2.0))
                await page.keyboard.press('Enter')
                
                if progress_callback: progress_callback("Chờ phản hồi (60s cho Captcha)...")
                
                login_success = False
                try:
                    await page.wait_for_selector(
                        'div[data-e2e="profile-icon"], a[data-e2e="upload-icon"], [data-e2e="nav-profile"]',
                        state="visible", timeout=60000
                    )
                    login_success = True
                except:
                    pass
                
                if not login_success:
                    await context.close()
                    return "Lỗi: Đăng nhập thất bại."
                
                if progress_callback: progress_callback("✅ Đăng nhập thành công!")
                current_cookies = await context.cookies()
                new_cookie = "; ".join([f"{c['name']}={c['value']}" for c in current_cookies])
                
                extracted_id = username.split('@')[0] if '@' in username else username
                try:
                    profile_links = await page.query_selector_all('a[href*="/@"]')
                    for link in profile_links:
                        href = await link.get_attribute("href")
                        if href and "/@" in href and "video" not in href:
                            extracted_id = href.split('/@')[-1].split('?')[0]
                            break
                except:
                    pass
                
                await context.close()
                return {"status": "success", "new_cookie": new_cookie, "id_tiktok": extracted_id}
                
            except Exception as e:
                await context.close()
                return str(e)

    async def open_browser_task(self, profile_name, browser_id="", progress_callback=None):
        """Mở trình duyệt thật với profile riêng (AdsPower hoặc Chrome) để duyệt thủ công"""
        import subprocess, socket
        
        if progress_callback: progress_callback(f"Đang khởi động trình duyệt cho [{profile_name}]...")
        
        if browser_id:
            import requests
            api_url = f"http://local.adspower.net:50325/api/v1/browser/start?user_id={browser_id}"
            try:
                response = requests.get(api_url)
                data = response.json()
                if data.get("code") != 0:
                    return f"Lỗi mở trình duyệt AdsPower: {data.get('msg')}"
                if progress_callback: progress_callback(f"AdsPower [{profile_name}] đã mở thành công.")
                # Không tự đóng, để người dùng sử dụng
                return f"Đã mở AdsPower profile: {browser_id}"
            except Exception as e:
                return f"Lỗi kết nối Local API AdsPower: {e}"
        else:
            return "Lỗi: Tài khoản này chưa được liên kết với AdsPower (Trống ID)!"

    # ================= CONTEXT MENU =================

    def show_acc_context_menu(self, pos):
        from PyQt5.QtWidgets import QMenu
        menu = QMenu(self)
        
        # === MỤC ĐẦU TIÊN: Bảng theo dõi ===
        action_dashboard = menu.addAction("📊 Bảng theo dõi (nuôi tài khoản)")
        action_dashboard.triggered.connect(self.open_dashboard_for_selected)
        
        menu.addSeparator()
        menu.addAction("💡 0. Bảng chức năng trình duyệt")
        
        browser_feat = menu.addMenu("🌍 1. Tính năng trình duyệt")
        
        # 1. Đổi thông tin tài khoản
        action_change_info = browser_feat.addAction("📝 1. Đổi thông tin tài khoản")
        action_change_info.triggered.connect(self.handle_change_account_info)
        
        # 2. Đăng nhập tài khoản này
        action_login = browser_feat.addAction("👤 2. Đăng nhập tài khoản này")
        action_login.triggered.connect(self.handle_login_account)
        
        # 3. Mở trình duyệt tài khoản này
        action_open_browser = browser_feat.addAction("🌍 3. Mở trình duyệt tài khoản này")
        action_open_browser.triggered.connect(self.handle_open_browser)
        
        action_refinger = browser_feat.addAction("♻️ 4. Tái chế Profile (Xóa Data & Đổi Vân Tay)")
        action_refinger.triggered.connect(self.handle_recycle_profile)
        
        browser_feat.addAction("🍪 5. Gắn cookie TikTok cho các tài khoản này")
        
        action_clear = browser_feat.addAction("🧹 6. Xóa toàn bộ Cache & Cookies")
        action_clear.triggered.connect(self.handle_clear_cache)

        menu.addAction("📂 Chuyển sang dự án...").triggered.connect(self.assign_to_project)
        
        acc_feat = menu.addMenu("👤 2. Tính năng tài khoản")
        action_update = acc_feat.addAction("1. Cập nhật thông tin")
        action_update.triggered.connect(self.handle_update_info)
        action_check = acc_feat.addAction("2. Kiểm tra trạng thái (Live/Die)")
        action_check.triggered.connect(self.handle_check_status)
        action_stats = acc_feat.addAction("📊 3. Cập nhật thống kê kênh (Follow/View/Video)")
        action_stats.triggered.connect(self.handle_update_stats)
        action_avatar = acc_feat.addAction("4. Chọn avatar TikTok")
        action_avatar.triggered.connect(self.handle_choose_avatar_for_selected)
        action_cookie = acc_feat.addAction("🍪 3. Kiểm tra Cookie")
        action_cookie.triggered.connect(self.handle_check_cookie)
        action_violation = acc_feat.addAction("Check vi phạm (API Nhanh)")
        action_violation.triggered.connect(self.handle_check_violation_api)
        
        status_feat = menu.addMenu("👤 3. Đặt trạng thái tài khoản")
        status_feat.addAction("Đặt trạng thái: Live")
        status_feat.addAction("Đặt trạng thái: Die")

        video_feat = menu.addMenu("🎬 4. Quản lý Video (Hẹn giờ)")
        action_choose_folder = video_feat.addAction("📂 1. Chọn thư mục Video")
        action_choose_folder.triggered.connect(self.video_manager.handle_choose_video_folder)
        action_load_video = video_feat.addAction("📥 2. Tải Video xuống bảng chờ")
        action_load_video.triggered.connect(self.video_manager.handle_load_videos)

        menu.addSeparator()
        menu.addAction("📄 Copy thông tin")
        menu.addAction("📥 Tải vào hàng chờ")
        menu.addAction("🌱 Mang các account đã chọn đi SEEDING...")
        menu.addSeparator()
        
        action_delete = menu.addAction("🗑️ Xóa các account đã chọn")
        action_delete.triggered.connect(self.delete_selected_accounts)

        menu.exec_(self.acc_table.viewport().mapToGlobal(pos))

    def handle_choose_avatar_for_selected(self):
        selected_rows = self.acc_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "Avatar TikTok", "Vui lòng chọn ít nhất 1 profile.")
            return

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn ảnh avatar TikTok",
            "",
            "File ảnh (*.png *.jpg *.jpeg *.webp);;Tất cả file (*)"
        )
        if not path:
            return

        updated = 0
        for index in selected_rows:
            row = index.row()
            name_item = self.acc_table.item(row, 1)
            if not name_item:
                continue
            profile_data = dict(name_item.data(Qt.UserRole) or {})
            profile_data["avatar_path"] = path
            name_item.setData(Qt.UserRole, profile_data)
            self.acc_table.setItem(row, 0, QTableWidgetItem("Co"))
            updated += 1

        self.save_accounts_to_db()
        self.log(f"Da gan avatar cho {updated} profile: {path}", "#2563eb")

    def open_dashboard_for_selected(self):
        """Mở Bảng theo dõi chỉ với các profile đang được chọn trên bảng"""
        selected_rows = self.acc_table.selectionModel().selectedRows()
        selected_count = len(selected_rows)
        if not selected_rows:
            self.log("Vui lòng chọn ít nhất 1 profile trước khi mở Bảng theo dõi!", "red")
            return
        accounts_data = self._collect_selected_accounts_data(selected_rows)
        project_name = self._current_project_name()
        self.log(f"M? b?ng theo d?i v?i {selected_count} profile ?? ch?n.", "#00ff00")
        self._show_automation_dashboard(
            accounts_data,
            project_name=project_name,
            dashboard_key=self._dashboard_key_for_project(project_name),
        )
        return

    def handle_check_cookie(self):
        """Check cookie TikTok — parse sid_guard + HTTP verify trang chủ."""
        import time
        import urllib.request
        import urllib.parse

        selected = self.acc_table.selectionModel().selectedRows()
        if not selected:
            self.log("Vui lòng chọn ít nhất 1 tài khoản!", "red")
            return

        for index in selected:
            row = index.row()
            name_item = self.acc_table.item(row, 1)
            profile_name = name_item.text() if name_item else f"Row {row}"

            # Lấy cookie từ UserRole hoặc cột 13
            cookie_str = ""
            if name_item:
                pdata = name_item.data(Qt.UserRole) or {}
                cookie_str = pdata.get("cookie", "")
            if not cookie_str:
                cookie_item = self.acc_table.item(row, 13)
                cookie_str = cookie_item.text().strip() if cookie_item else ""

            if not cookie_str or len(cookie_str) < 20:
                item = QTableWidgetItem("Die")
                item.setForeground(QColor("#ef4444"))
                self.acc_table.setItem(row, 12, item)
                self.log(f"[{profile_name}] Không có cookie.", "orange")
                continue

            # Parse cookie thành dict
            cookie_dict = {}
            for pair in cookie_str.split(';'):
                pair = pair.strip()
                if '=' in pair:
                    k, v = pair.split('=', 1)
                    cookie_dict[k.strip()] = v.strip()

            # ── Bước 1: Kiểm tra sessionid tồn tại ──
            has_sessionid = bool(cookie_dict.get("sessionid", ""))
            if not has_sessionid:
                item = QTableWidgetItem("Die")
                item.setForeground(QColor("#ef4444"))
                self.acc_table.setItem(row, 12, item)
                self.log(f"[{profile_name}] ❌ Die — Thiếu sessionid", "red")
                continue

            # ── Bước 2: Parse sid_guard kiểm tra hết hạn ──
            expiry_info = ""
            sid_guard = cookie_dict.get("sid_guard", "")
            if sid_guard:
                try:
                    sid_decoded = urllib.parse.unquote(sid_guard)
                    parts = sid_decoded.split("|")
                    if len(parts) >= 3:
                        created_ts = int(parts[1])
                        ttl = int(parts[2])
                        expires_ts = created_ts + ttl
                        now_ts = int(time.time())
                        remaining = expires_ts - now_ts
                        if remaining <= 0:
                            item = QTableWidgetItem("Die")
                            item.setForeground(QColor("#ef4444"))
                            self.acc_table.setItem(row, 12, item)
                            self.log(f"[{profile_name}] ❌ Die — Cookie đã hết hạn", "red")
                            continue
                        else:
                            days_left = remaining // 86400
                            expiry_info = f"còn {days_left} ngày"
                except Exception:
                    expiry_info = ""

            # ── Bước 3: HTTP verify — gọi trang chủ TikTok ──
            self.log(f"[{profile_name}] 🔍 Đang kiểm tra online...", "blue")
            try:
                req = urllib.request.Request(
                    "https://www.tiktok.com/",
                    headers={
                        "Cookie": cookie_str,
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                                      "Chrome/120.0.0.0 Safari/537.36",
                        "Accept-Language": "en-US,en;q=0.9",
                    }
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")

                # Nếu HTML chứa uniqueId/username → cookie còn sống
                # Nếu HTML chứa "login" button nổi bật → cookie chết
                has_login_prompt = (
                    '"isLoginExpired":true' in html
                    or '"loginExpireType":' in html
                    or '"uniqueId":""' in html
                )
                has_user = '"uniqueId":"' in html and '"uniqueId":""' not in html

                if has_user and not has_login_prompt:
                    # ★ Thử lấy uniqueId từ HTML
                    uid = ""
                    try:
                        import re
                        m = re.search(r'"uniqueId":"([^"]+)"', html)
                        if m:
                            uid = m.group(1)
                    except Exception:
                        pass

                    item = QTableWidgetItem("Live")
                    item.setForeground(QColor("#16a34a"))
                    self.acc_table.setItem(row, 12, item)
                    extra = f" — @{uid}" if uid else ""
                    extra += f" ({expiry_info})" if expiry_info else ""
                    self.log(f"[{profile_name}] ✅ Live{extra}", "#00ff00")

                    # Cập nhật ID TikTok nếu chưa có
                    if uid:
                        id_item = self.acc_table.item(row, 5)
                        if not id_item or not id_item.text().strip():
                            self.acc_table.setItem(row, 5, QTableWidgetItem(f"@{uid}"))
                elif has_login_prompt:
                    item = QTableWidgetItem("Die")
                    item.setForeground(QColor("#ef4444"))
                    self.acc_table.setItem(row, 12, item)
                    self.log(f"[{profile_name}] ❌ Die — Cookie hết hạn", "red")
                else:
                    # Không xác định rõ → dựa vào sid_guard
                    if expiry_info:
                        item = QTableWidgetItem("Live")
                        item.setForeground(QColor("#f59e0b"))
                        self.acc_table.setItem(row, 12, item)
                        self.log(f"[{profile_name}] ⚠️ Có thể Live ({expiry_info})", "orange")
                    else:
                        item = QTableWidgetItem("???")
                        item.setForeground(QColor("#f59e0b"))
                        self.acc_table.setItem(row, 12, item)
                        self.log(f"[{profile_name}] ⚠️ Không xác định được", "orange")

            except Exception as e:
                # Lỗi mạng → fallback dùng sid_guard
                if expiry_info:
                    item = QTableWidgetItem("Live")
                    item.setForeground(QColor("#f59e0b"))
                    self.acc_table.setItem(row, 12, item)
                    self.log(f"[{profile_name}] ⚠️ Có thể Live ({expiry_info}) — offline check", "orange")
                else:
                    item = QTableWidgetItem("???")
                    item.setForeground(QColor("#f59e0b"))
                    self.acc_table.setItem(row, 12, item)
                    self.log(f"[{profile_name}] ⚠️ Lỗi mạng: {str(e)[:40]}", "orange")

    def delete_selected_accounts(self):
        selected = self.acc_table.selectionModel().selectedRows()
        if not selected:
            return
        
        count = len(selected)
        reply = QMessageBox.question(
            self, "Xác nhận xóa",
            f"Bạn có chắc muốn xóa {count} tài khoản?\n\n"
            f"⚠️ Thư mục trình duyệt (profile) trên ổ đĩa và GoLogin cloud profile cũng sẽ bị xóa!",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        
        import shutil
        deleted_folders = 0
        deleted_gologin = 0
        for index in reversed(selected):
            row = index.row()
            # Lấy browser_id từ cột 4 hoặc UserRole
            name_item = self.acc_table.item(row, 1)
            pdata = name_item.data(Qt.UserRole) if name_item else {}
            if not isinstance(pdata, dict):
                pdata = {}
            id_item = self.acc_table.item(row, 4)
            browser_id = id_item.text().strip() if id_item else ""
            if not browser_id:
                browser_id = pdata.get("browser_id", "")

            gologin_profile_id = (
                pdata.get("gologin_profile_id")
                or pdata.get("browser_id")
                or browser_id
                or ""
            ).strip()
            if gologin_profile_id:
                ok, msg = self._delete_gologin_cloud_profile(gologin_profile_id)
                if ok:
                    deleted_gologin += 1
                    self.log(f"🗑️ Đã xóa GoLogin profile: {gologin_profile_id}", "#ff9800")
                else:
                    self.log(f"⚠️ Không xóa được GoLogin profile {gologin_profile_id}: {msg}", "yellow")
            
            # Xóa thư mục profile trên ổ đĩa
            if browser_id:
                # Tìm folder profile_<browser_id>
                profile_dir = str(gologin_profiles_root() / f"profile_{browser_id}")
                if os.path.isdir(profile_dir):
                    try:
                        shutil.rmtree(profile_dir, ignore_errors=True)
                        deleted_folders += 1
                        self.log(f"🗑️ Đã xóa thư mục: {os.path.basename(profile_dir)}", "#ff9800")
                    except Exception as e:
                        self.log(f"⚠️ Không xóa được thư mục {profile_dir}: {e}", "yellow")
                else:
                    self.log(f"📂 Không tìm thấy thư mục profile: {profile_dir}", "gray")
            
            self.acc_table.removeRow(row)
        
        self.log(f"✅ Đã xóa {count} tài khoản, {deleted_gologin} GoLogin profile, {deleted_folders} thư mục trình duyệt.")
        self.save_accounts_to_db()





    def apply_styles(self):
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f3f4f6;
            }
            QWidget {
                font-family: "Segoe UI";
                font-size: 12px;
                color: #111827;
            }
            QMenuBar {
                background-color: #ffffff;
                border-bottom: 1px solid #d1d5db;
                padding: 2px 4px;
            }
            QMenuBar::item {
                padding: 5px 10px;
                border-radius: 4px;
            }
            QMenuBar::item:selected {
                background-color: #e5e7eb;
            }
            QFrame#accountToolbar,
            QWidget#videoPanel,
            QWidget#logPanel {
                background-color: #ffffff;
                border: 1px solid #d1d5db;
                border-radius: 6px;
            }
            QFrame#accountSummaryBar {
                background-color: #f9fafb;
                border-left: 1px solid #d1d5db;
                border-right: 1px solid #d1d5db;
                border-bottom: 1px solid #d1d5db;
                border-bottom-left-radius: 6px;
                border-bottom-right-radius: 6px;
            }
            QLabel[role="sectionTitle"] {
                font-weight: 700;
                color: #111827;
            }
            QLabel[role="summary"] {
                background-color: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 4px;
                padding: 2px 8px;
                color: #374151;
                font-weight: 600;
            }
            QTableWidget {
                background-color: #ffffff;
                alternate-background-color: #f9fafb;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                selection-background-color: #dbeafe;
                selection-color: #111827;
                gridline-color: #e5e7eb;
            }
            QTableWidget::item {
                padding: 4px 6px;
                border-bottom: 1px solid #f3f4f6;
            }
            QTableWidget::item:selected {
                background-color: #dbeafe;
                color: #111827;
            }
            QHeaderView::section {
                background-color: #eef2f7;
                color: #374151;
                padding: 6px;
                border: none;
                border-right: 1px solid #d1d5db;
                border-bottom: 1px solid #d1d5db;
                font-weight: 700;
            }
            QPushButton {
                min-height: 26px;
                padding: 4px 10px;
                border: 1px solid #cbd5e1;
                border-radius: 5px;
                background-color: #ffffff;
                color: #111827;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #f3f4f6;
                border-color: #94a3b8;
            }
            QPushButton:pressed {
                background-color: #e5e7eb;
            }
            QPushButton:disabled {
                background-color: #f3f4f6;
                color: #9ca3af;
                border-color: #e5e7eb;
            }
            QPushButton[variant="primary"] {
                background-color: #2563eb;
                color: #ffffff;
                border-color: #1d4ed8;
            }
            QPushButton[variant="primary"]:hover {
                background-color: #1d4ed8;
            }
            QPushButton[variant="creatorNow"] {
                background-color: #f97316;
                color: #ffffff;
                border-color: #ea580c;
                font-weight: 800;
                letter-spacing: 0px;
            }
            QPushButton[variant="creatorNow"]:hover {
                background-color: #ea580c;
                border-color: #c2410c;
            }
            QPushButton[variant="creatorNow"]:pressed {
                background-color: #c2410c;
                border-color: #9a3412;
            }
            QPushButton[variant="secondary"] {
                background-color: #ecfdf5;
                color: #065f46;
                border-color: #a7f3d0;
            }
            QPushButton[variant="secondary"]:hover {
                background-color: #d1fae5;
                border-color: #6ee7b7;
            }
            QPushButton[variant="ghost"],
            QPushButton[variant="icon"],
            QPushButton[variant="iconAccent"],
            QPushButton[variant="iconDanger"] {
                background-color: #f8fafc;
                color: #334155;
                border-color: #e2e8f0;
            }
            QPushButton[variant="ghost"]:hover,
            QPushButton[variant="icon"]:hover,
            QPushButton[variant="iconAccent"]:hover,
            QPushButton[variant="iconDanger"]:hover {
                background-color: #e2e8f0;
                border-color: #cbd5e1;
            }
            QPushButton[variant="icon"],
            QPushButton[variant="iconAccent"],
            QPushButton[variant="iconDanger"] {
                padding: 0px;
                font-weight: 700;
                font-size: 15px;
                min-width: 30px;
                max-width: 30px;
                min-height: 30px;
                max-height: 30px;
                border-radius: 5px;
            }
            QPushButton[variant="iconAccent"] {
                background-color: #f5f3ff;
                color: #6d28d9;
                border-color: #ddd6fe;
            }
            QPushButton[variant="iconAccent"]:hover {
                background-color: #ede9fe;
                border-color: #c4b5fd;
            }
            QPushButton[variant="iconDanger"] {
                background-color: #fef2f2;
                color: #991b1b;
                border-color: #fecaca;
            }
            QPushButton[variant="iconDanger"]:hover {
                background-color: #fee2e2;
                border-color: #fca5a5;
            }
            QPushButton[variant="danger"] {
                background-color: #fef2f2;
                color: #991b1b;
                border-color: #fecaca;
            }
            QPushButton[variant="danger"]:hover {
                background-color: #fee2e2;
                border-color: #fca5a5;
            }
            QLineEdit, QComboBox {
                min-height: 26px;
                border: 1px solid #cbd5e1;
                border-radius: 5px;
                padding: 3px 8px;
                background-color: #ffffff;
                selection-background-color: #bfdbfe;
            }
            QLineEdit:focus, QComboBox:focus {
                border-color: #2563eb;
            }
            QComboBox::drop-down {
                width: 22px;
                border: none;
            }
            QSplitter::handle {
                background-color: #d1d5db;
            }
            QSplitter::handle:horizontal {
                width: 6px;
            }
            QSplitter::handle:vertical {
                height: 6px;
            }
            QStatusBar {
                background-color: #ffffff;
                border-top: 1px solid #d1d5db;
                font-size: 11px;
                color: #4b5563;
            }
        """)

if __name__ == "__main__":
    # ── Fix DPI Scaling: ép Windows nhận diện High DPI ──
    # Tránh Windows tự scale → Chrome nhúng bị lệch viền
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-Monitor DPI Aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # Fallback cho Win8.0
        except Exception:
            pass

    app = QApplication(sys.argv)
    window = SSMAToolGUI()
    window.show()
    sys.exit(app.exec_())
