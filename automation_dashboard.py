import sys
import os
import asyncio
import random
import json
import time
import re

from PyQt5.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox, QCheckBox,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QHeaderView,
    QScrollArea, QWidget, QGridLayout, QFrame, QAbstractItemView,
    QRadioButton, QButtonGroup, QFormLayout, QSizePolicy,
    QGroupBox, QDialogButtonBox, QTextEdit, QLineEdit, QMessageBox
)
from PyQt5.QtCore import Qt, QEvent, QThread, pyqtSignal, QUrl, QTimer, QPoint
from PyQt5.QtGui import QColor, QCursor
from PyQt5.QtNetwork import QNetworkCookie
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineProfile, QWebEnginePage
from gologin_profile_utils import first_real_gologin_profile_id
from app_paths import data_file, named_browser_profile_dir


def get_profile_dir(profile_name):
    return str(named_browser_profile_dir(profile_name))


# ============================================================
# HỆ THỐNG FINGERPRINT - TẠO VÂN TAY TRÌNH DUYỆT NGẪU NHIÊN
# ============================================================
class BrowserFingerprint:
    # Danh sách User-Agent thật (Windows + Mac)
    WIN_AGENTS = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.6533.100 Safari/537.36',
    ]
    MAC_AGENTS = [
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
    ]
    SCREENS = ['1920x1080', '1366x768', '1440x900', '1536x864', '1280x720', '2560x1440']
    LANGUAGES = ['en-US', 'en-GB', 'vi-VN', 'en']
    TIMEZONES = [-480, -420, -360, -300, -240, 0, 60, 420, 480]
    WEBGL_VENDORS = ['Google Inc. (NVIDIA)', 'Google Inc. (AMD)', 'Google Inc. (Intel)']
    WEBGL_RENDERERS = [
        'ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER, OpenGL 4.5)',
        'ANGLE (AMD, AMD Radeon RX 580, OpenGL 4.5)',
        'ANGLE (Intel, Intel UHD Graphics 630, OpenGL 4.5)',
        'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060, OpenGL 4.5)',
        'ANGLE (Intel, Intel Iris Xe Graphics, OpenGL 4.5)',
    ]

    @staticmethod
    def generate(platform='win'):
        """Tạo fingerprint ngẫu nhiên"""
        agents = BrowserFingerprint.WIN_AGENTS if platform == 'win' else BrowserFingerprint.MAC_AGENTS
        screen = random.choice(BrowserFingerprint.SCREENS)
        w, h = screen.split('x')
        return {
            'user_agent': random.choice(agents),
            'platform': 'Win32' if platform == 'win' else 'MacIntel',
            'screen_width': int(w),
            'screen_height': int(h),
            'language': random.choice(BrowserFingerprint.LANGUAGES),
            'timezone_offset': random.choice(BrowserFingerprint.TIMEZONES),
            'webgl_vendor': random.choice(BrowserFingerprint.WEBGL_VENDORS),
            'webgl_renderer': random.choice(BrowserFingerprint.WEBGL_RENDERERS),
            'canvas_noise': random.randint(1, 99999),
            'hardware_concurrency': random.choice([2, 4, 6, 8, 12, 16]),
            'device_memory': random.choice([2, 4, 8, 16]),
        }

    @staticmethod
    def load_or_create(profile_dir, platform='win'):
        """Load fingerprint từ file hoặc tạo mới"""
        fp_path = os.path.join(profile_dir, 'fingerprint.json')
        if os.path.exists(fp_path):
            try:
                with open(fp_path, 'r') as f:
                    return json.load(f)
            except:
                pass
        fp = BrowserFingerprint.generate(platform)
        BrowserFingerprint.save(profile_dir, fp)
        return fp

    @staticmethod
    def save(profile_dir, fp):
        os.makedirs(profile_dir, exist_ok=True)
        with open(os.path.join(profile_dir, 'fingerprint.json'), 'w') as f:
            json.dump(fp, f, indent=2)

    @staticmethod
    def regenerate(profile_dir, platform='win'):
        """Tạo fingerprint MỚI (khi bị Maximum)"""
        fp = BrowserFingerprint.generate(platform)
        BrowserFingerprint.save(profile_dir, fp)
        return fp

    @staticmethod
    def get_inject_js(fp):
        """Tạo JavaScript để inject fingerprint vào trang"""
        return """
        // Override navigator properties
        Object.defineProperty(navigator, 'userAgent', {get: function(){return '%s';}});
        Object.defineProperty(navigator, 'platform', {get: function(){return '%s';}});
        Object.defineProperty(navigator, 'language', {get: function(){return '%s';}});
        Object.defineProperty(navigator, 'hardwareConcurrency', {get: function(){return %d;}});
        Object.defineProperty(navigator, 'deviceMemory', {get: function(){return %d;}});
        Object.defineProperty(screen, 'width', {get: function(){return %d;}});
        Object.defineProperty(screen, 'height', {get: function(){return %d;}});
        // Canvas fingerprint noise
        var origToDataURL = HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL = function(type) {
            var ctx = this.getContext('2d');
            if (ctx) { var imgData = ctx.getImageData(0,0,1,1); imgData.data[0] = (imgData.data[0] + %d) %% 256; ctx.putImageData(imgData,0,0); }
            return origToDataURL.apply(this, arguments);
        };
        """ % (fp['user_agent'], fp['platform'], fp['language'],
               fp['hardware_concurrency'], fp['device_memory'],
               fp['screen_width'], fp['screen_height'], fp['canvas_noise'])


# ============================================================
# DIALOG CÀI ĐẶT NUÔI NICK NEW FEED
# ============================================================
class GoLoginFingerprintRefreshWorker(QThread):
    """Refresh a GoLogin cloud fingerprint without deleting local profile data."""
    finished_signal = pyqtSignal(bool, str, dict)

    def __init__(self, api_token, profile_id, parent=None):
        super().__init__(parent)
        self.api_token = (api_token or "").strip()
        self.profile_id = (profile_id or "").strip()

    def run(self):
        if not self.api_token:
            self.finished_signal.emit(False, "Thiếu GoLogin API key.", {})
            return
        if not self.profile_id:
            self.finished_signal.emit(False, "Thiếu GoLogin profile ID.", {})
            return

        try:
            import requests
            response = requests.patch(
                "https://api.gologin.com/browser/fingerprints",
                headers={
                    "Authorization": f"Bearer {self.api_token}",
                    "Content-Type": "application/json",
                },
                json={"browsersIds": [self.profile_id]},
                timeout=30,
            )
        except Exception as exc:
            self.finished_signal.emit(False, f"Loi ket noi GoLogin API: {exc}", {})
            return

        if response.status_code not in (200, 201, 204):
            detail = (response.text or "").strip().replace("\n", " ")[:300]
            self.finished_signal.emit(
                False,
                f"GoLogin API loi HTTP {response.status_code}: {detail}",
                {},
            )
            return

        self.finished_signal.emit(
            True,
            "Da lam moi van tay GoLogin. Dong/mo lai profile de ap dung.",
            {"profile_id": self.profile_id},
        )


class FeedSettingsDialog(QDialog):
    def __init__(self, parent=None, current_settings=None):
        super().__init__(parent)
        self.setWindowTitle("Cài đặt hàng chờ")
        self.resize(600, 500)
        self.settings = current_settings or {}
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        
        lbl_title = QLabel("<h2><u>Cài đặt nuôi nick New Feed</u></h2>")
        layout.addWidget(lbl_title)
        
        # Row 1: Tương tác random
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Tương tác random video ở Feed"))
        self.btn_group_feed = QButtonGroup(self)
        self.rad_off = QRadioButton("Tắt")
        self.rad_foryou = QRadioButton("ở For You")
        self.rad_explore = QRadioButton("ở Explore")
        self.btn_group_feed.addButton(self.rad_off, 0)
        self.btn_group_feed.addButton(self.rad_foryou, 1)
        self.btn_group_feed.addButton(self.rad_explore, 2)
        row1.addWidget(self.rad_off)
        row1.addWidget(self.rad_foryou)
        row1.addWidget(self.rad_explore)
        row1.addStretch()
        row1.addWidget(QLabel("Tỉ lệ là gì?"))
        layout.addLayout(row1)
        
        # Grid cho các cấu hình khác
        grid = QGridLayout()
        grid.setVerticalSpacing(15)
        
        grid.addWidget(QLabel("1. Số lần xem video tối thiểu trong khoảng từ"), 0, 0)
        self.spin_view_min = QSpinBox(); self.spin_view_min.setRange(1, 100)
        grid.addWidget(self.spin_view_min, 0, 1)
        grid.addWidget(QLabel("đến"), 0, 2)
        self.spin_view_max = QSpinBox(); self.spin_view_max.setRange(1, 100)
        grid.addWidget(self.spin_view_max, 0, 3)
        grid.addWidget(QLabel("lần"), 0, 4)
        
        row_time = QHBoxLayout()
        self.chk_time = QCheckBox()
        row_time.addWidget(self.chk_time)
        row_time.addWidget(QLabel("2. Thời gian tương tác tối thiểu trong khoảng từ"))
        grid.addLayout(row_time, 1, 0)
        self.spin_time_min = QSpinBox(); self.spin_time_min.setRange(1, 100)
        grid.addWidget(self.spin_time_min, 1, 1)
        grid.addWidget(QLabel("phút đến"), 1, 2)
        self.spin_time_max = QSpinBox(); self.spin_time_max.setRange(1, 100)
        grid.addWidget(self.spin_time_max, 1, 3)
        grid.addWidget(QLabel("phút"), 1, 4)
        
        row = 2
        num = 3
        def add_percent_row(label_text, spin_name):
            nonlocal num
            grid.addWidget(QLabel(f"{num}. {label_text}"), row, 0)
            grid.addWidget(QLabel("tỉ lệ"), row, 2, alignment=Qt.AlignRight | Qt.AlignVCenter)
            spin = QSpinBox(); spin.setRange(0, 100)
            setattr(self, spin_name, spin)
            grid.addWidget(spin, row, 3)
            grid.addWidget(QLabel("%"), row, 4)
            num += 1
            return row + 1

        row = add_percent_row("Tự động clone lại 1 comment bất kỳ trong video", "spin_clone_cmt")
        row = add_percent_row("Tự động click vào nút xem thêm comment", "spin_view_more_cmt")
        row = add_percent_row("Tự động thả tim comment", "spin_like_cmt")
        
        grid.addWidget(QLabel(f"{num}. Số lượng thả tim cmt tối đa"), row, 0)
        grid.addWidget(QLabel("số lượng"), row, 2, alignment=Qt.AlignRight | Qt.AlignVCenter)
        self.spin_max_like_cmt = QSpinBox(); self.spin_max_like_cmt.setRange(1, 100)
        grid.addWidget(self.spin_max_like_cmt, row, 3)
        grid.addWidget(QLabel("cái"), row, 4)
        num += 1
        row += 1
        
        row = add_percent_row("Tự động thả tim video", "spin_like_video")
        row = add_percent_row("Tự động thêm video vào yêu thích", "spin_fav_video")
        row = add_percent_row("Tự động REPOST video", "spin_repost_video")
        row = add_percent_row("Tự động follow kênh", "spin_follow")
        
        layout.addLayout(grid)
        layout.addStretch()

        # ── Nút Lưu / Hủy ──
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.button(QDialogButtonBox.Ok).setText("✅ Lưu")
        btn_box.button(QDialogButtonBox.Cancel).setText("❌ Hủy")
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self.load_settings()

    def load_settings(self):
        feed_type = self.settings.get('feed_type', 1)
        if feed_type == 0: self.rad_off.setChecked(True)
        elif feed_type == 1: self.rad_foryou.setChecked(True)
        else: self.rad_explore.setChecked(True)
        
        self.spin_view_min.setValue(self.settings.get('view_min', 3))
        self.spin_view_max.setValue(self.settings.get('view_max', 5))
        self.chk_time.setChecked(self.settings.get('use_time', False))
        self.spin_time_min.setValue(self.settings.get('time_min', 3))
        self.spin_time_max.setValue(self.settings.get('time_max', 5))
        
        self.spin_clone_cmt.setValue(self.settings.get('clone_cmt', 5))
        self.spin_view_more_cmt.setValue(self.settings.get('view_more_cmt', 5))
        self.spin_like_cmt.setValue(self.settings.get('like_cmt', 5))
        self.spin_max_like_cmt.setValue(self.settings.get('max_like_cmt', 5))
        self.spin_like_video.setValue(self.settings.get('like_video', 0))
        self.spin_fav_video.setValue(self.settings.get('fav_video', 0))
        self.spin_repost_video.setValue(self.settings.get('repost_video', 5))
        self.spin_follow.setValue(self.settings.get('follow', 0))
        
    def get_settings(self):
        result = {
            'feed_type': self.btn_group_feed.checkedId(),
            'view_min': self.spin_view_min.value(),
            'view_max': self.spin_view_max.value(),
            'use_time': self.chk_time.isChecked(),
            'time_min': self.spin_time_min.value(),
            'time_max': self.spin_time_max.value(),
            'clone_cmt': self.spin_clone_cmt.value(),
            'view_more_cmt': self.spin_view_more_cmt.value(),
            'like_cmt': self.spin_like_cmt.value(),
            'max_like_cmt': self.spin_max_like_cmt.value(),
            'like_video': self.spin_like_video.value(),
            'fav_video': self.spin_fav_video.value(),
            'repost_video': self.spin_repost_video.value(),
            'follow': self.spin_follow.value()
        }
        # ★ Giữ nguyên các field keyword từ settings gốc (không ghi đè)
        for key in ('keywords', 'keyword_min_videos', 'keyword_max_videos'):
            if key in self.settings:
                result[key] = self.settings[key]
        return result


# ============================================================
# DIALOG CÀI ĐẶT TỪ KHÓA (Keyword Settings)
# ============================================================
class KeywordSettingsDialog(QDialog):
    """Dialog cài đặt danh sách từ khóa và số lượng video xem mỗi từ khóa."""
    def __init__(self, parent=None, current_settings=None):
        super().__init__(parent)
        self.setWindowTitle("Cài đặt Tương tác theo Từ khóa")
        self.resize(550, 450)
        self.settings = current_settings or {}
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        lbl_title = QLabel("<h2><u>Cài đặt từ khóa tìm kiếm</u></h2>")
        layout.addWidget(lbl_title)

        # ── Hướng dẫn ──
        lbl_hint = QLabel(
            "Nhập mỗi từ khóa trên 1 dòng. Bot sẽ tìm kiếm lần lượt từng từ khóa,\n"
            "mở video đầu tiên và tương tác trong chế độ Theater Mode."
        )
        lbl_hint.setStyleSheet("color: #6b7280; font-size: 12px; margin-bottom: 8px;")
        layout.addWidget(lbl_hint)

        # ── Ô nhập từ khóa (multi-line) ──
        lbl_kw = QLabel("📝 Danh sách từ khóa (mỗi dòng 1 từ khóa):")
        lbl_kw.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout.addWidget(lbl_kw)

        self.txt_keywords = QTextEdit()
        self.txt_keywords.setPlaceholderText(
            "Ví dụ:\n"
            "tin tức phá án Nhật Bản\n"
            "drama mạng xã hội\n"
            "review phim hay 2026"
        )
        self.txt_keywords.setStyleSheet(
            "background: #f9fafb; border: 1px solid #d0d5dd; border-radius: 6px; "
            "padding: 8px; font-size: 13px; color: #1a1a2e;"
        )
        self.txt_keywords.setMinimumHeight(160)
        layout.addWidget(self.txt_keywords)

        # ── Số video xem mỗi từ khóa ──
        grid = QGridLayout()
        grid.setVerticalSpacing(10)

        grid.addWidget(QLabel("🎬 Số video xem mỗi từ khóa, từ"), 0, 0)
        self.spin_min = QSpinBox()
        self.spin_min.setRange(1, 50)
        self.spin_min.setFixedWidth(60)
        grid.addWidget(self.spin_min, 0, 1)
        grid.addWidget(QLabel("đến"), 0, 2)
        self.spin_max = QSpinBox()
        self.spin_max.setRange(1, 50)
        self.spin_max.setFixedWidth(60)
        grid.addWidget(self.spin_max, 0, 3)
        grid.addWidget(QLabel("video"), 0, 4)

        layout.addLayout(grid)
        layout.addStretch()

        # ── Nút Lưu / Hủy ──
        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.button(QDialogButtonBox.Ok).setText("✅ Lưu")
        btn_box.button(QDialogButtonBox.Cancel).setText("❌ Hủy")
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self.load_settings()

    def load_settings(self):
        """Load dữ liệu từ settings hiện tại vào UI."""
        keywords = self.settings.get('keywords', [])
        if keywords:
            self.txt_keywords.setPlainText("\n".join(keywords))
        self.spin_min.setValue(self.settings.get('keyword_min_videos', 3))
        self.spin_max.setValue(self.settings.get('keyword_max_videos', 8))

    def get_settings(self):
        """Trả về settings keyword đã cập nhật (merge vào feed_settings)."""
        # Parse từ khóa: mỗi dòng 1 keyword, bỏ dòng trống
        raw = self.txt_keywords.toPlainText()
        keywords = [line.strip() for line in raw.split('\n') if line.strip()]
        return {
            'keywords': keywords,
            'keyword_min_videos': self.spin_min.value(),
            'keyword_max_videos': self.spin_max.value(),
        }

# ============================================================
# GIAO DIỆN CHÍNH
# ============================================================
class AutomationDashboard(QDialog):
    dashboard_hidden = pyqtSignal()
    dashboard_closed = pyqtSignal()

    def __init__(self, parent=None, accounts_data=None, project_name="", dashboard_key=""):
        super().__init__(parent)
        self.accounts_data = accounts_data or []
        self.project_name = project_name or "Bảng theo dõi"
        self.dashboard_key = dashboard_key or self.project_name
        self.active_workers = []
        self._run_queue = []
        self._running_rows = set()
        self._pending_start_timers = {}
        self._row_profile_keys = {}
        self._run_cancelled = False
        self._run_mode = "automation"
        self._run_generation = 0
        self._next_grid_index = 0
        self._dashboard_preset = self._select_dashboard_preset()
        self._current_grid_columns = int(self._dashboard_preset["cols"])
        self._current_preview_width = int(self._dashboard_preset["preview_w"])
        self._current_browser_height = int(self._dashboard_preset["browser_h"])
        self._planned_profile_count = 0
        self._browser_grid_resize_pending = False
        self._blocked_profile_notice_keys = set()
        self._stopping_profile_keys = {}
        self._pending_restart_request = None
        self._save_pending = False
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._flush_parent_save)
        self._settings_path = str(data_file("feed_settings.json", {"gologin_passthrough_strict": True}))
        self.feed_settings = self._load_feed_settings()
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.setWindowTitle(f"Bảng theo dõi - {self.project_name}")
        self._apply_initial_window_geometry()
        self.init_ui()

    def _screen_available_geometry(self):
        screen = self.screen() or QApplication.primaryScreen()
        if screen:
            return screen.availableGeometry()
        return None

    def _select_dashboard_preset(self):
        available = self._screen_available_geometry()
        screen_w = available.width() if available else 1920
        screen_h = available.height() if available else 1080

        if screen_w >= 3000 or screen_h >= 1650:
            preset = {
                "key": "ultra",
                "label": "Ultra",
                "window_w": 3000,
                "window_h": 1180,
                "left_w": 520,
                "cols": 3,
                "preview_w": 820,
            }
        elif screen_w >= 2350 or screen_h >= 1300:
            preset = {
                "key": "large",
                "label": "Large",
                "window_w": 2200,
                "window_h": 1000,
                "left_w": 480,
                "cols": 2,
                "preview_w": 840,
            }
        else:
            preset = {
                "key": "compact",
                "label": "Compact",
                "window_w": 1880,
                "window_h": 930,
                "left_w": 430,
                "cols": 2,
                "preview_w": 720,
            }

        window_w = min(int(preset["window_w"]), int(screen_w))
        window_h = min(int(preset["window_h"]), int(screen_h))
        left_w = min(int(preset["left_w"]), max(380, window_w - 760))
        cols = max(1, int(preset["cols"]))
        right_w = max(1, window_w - left_w)
        preview_w = min(int(preset["preview_w"]), max(520, (right_w - max(0, cols - 1)) // cols))
        if preview_w < 600 and cols > 1:
            cols = 1
            preview_w = min(int(preset["preview_w"]), max(520, right_w))

        preset.update({
            "window_w": window_w,
            "window_h": window_h,
            "left_w": left_w,
            "cols": cols,
            "preview_w": preview_w,
            "browser_h": int(preview_w * 800 / 960),
            "screen_w": int(screen_w),
            "screen_h": int(screen_h),
        })
        return preset

    def _apply_initial_window_geometry(self):
        available = self._screen_available_geometry()
        if not available:
            self.setFixedSize(
                int(self._dashboard_preset["window_w"]),
                int(self._dashboard_preset["window_h"]),
            )
            return

        width = int(self._dashboard_preset["window_w"])
        height = int(self._dashboard_preset["window_h"])

        self.setFixedSize(width, height)
        self.move(
            available.x() + max(0, (available.width() - width) // 2),
            available.y() + max(0, (available.height() - height) // 2),
        )

    def _preferred_left_width(self):
        return int(self._dashboard_preset.get("left_w", 430))

    def init_ui(self):
        self.setStyleSheet("""
            QDialog { background: #f0f2f5; }
            QLabel { color: #1a1a2e; }
            QTableWidget {
                background: #ffffff; color: #1a1a2e; border: 1px solid #d0d5dd;
                gridline-color: #e4e7ec; selection-background-color: #dbeafe;
            }
            QTableWidget::item { padding: 4px; }
            QHeaderView::section {
                background: #e8eaed; color: #1a1a2e; padding: 6px;
                border: 1px solid #d0d5dd; font-weight: bold;
            }
            QCheckBox { color: #1a1a2e; spacing: 6px; }
            QCheckBox::indicator { width: 16px; height: 16px; }
            QPushButton {
                background: #ffffff; color: #1a1a2e; border: 1px solid #d0d5dd;
                padding: 6px 14px; border-radius: 4px; font-weight: bold;
            }
            QPushButton:hover { background: #e8eaed; }
            QSpinBox {
                background: #ffffff; color: #1a1a2e; border: 1px solid #d0d5dd;
                padding: 3px; border-radius: 3px;
            }
            QScrollArea { border: none; background: transparent; }
            QSplitter::handle { background: #d0d5dd; width: 2px; }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ===== TOP BAR =====
        top_bar_widget = QWidget()
        top_bar_widget.setStyleSheet("background: #ffffff; border-bottom: 2px solid #3b82f6;")
        top_bar_widget.setFixedHeight(42)
        top_bar = QHBoxLayout(top_bar_widget)
        top_bar.setContentsMargins(12, 0, 12, 0)

        lbl_title = QLabel("🎯 Bảng theo dõi")
        lbl_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #3b82f6;")
        top_bar.addWidget(lbl_title)

        top_bar.addWidget(QLabel("luồng"))
        self.spin_luong = QSpinBox(); self.spin_luong.setValue(1); self.spin_luong.setFixedWidth(50)
        top_bar.addWidget(self.spin_luong)
        top_bar.addWidget(QLabel("?? tr?"))
        self.spin_delay = QSpinBox(); self.spin_delay.setRange(1, 60); self.spin_delay.setValue(3); self.spin_delay.setFixedWidth(50)
        top_bar.addWidget(self.spin_delay)

        preset_label = QLabel(
            f"preset {self._dashboard_preset['label']} "
            f"{self._dashboard_preset['screen_w']}x{self._dashboard_preset['screen_h']}"
        )
        preset_label.setStyleSheet("font-size: 12px; color: #64748b; margin-left: 10px;")
        top_bar.addWidget(preset_label)

        self.lbl_project = QLabel(f"Dự án: {self.project_name}")
        self.lbl_project.setStyleSheet("font-size: 13px; color: #6b7280; margin-left: 20px;")
        top_bar.addWidget(self.lbl_project)
        top_bar.addStretch()

        self.chk_browser = QCheckBox("Đưa trình duyệt vào màn hình")
        self.chk_browser.setChecked(True)
        self.chk_browser.setStyleSheet("color: #16a34a;")
        top_bar.addWidget(self.chk_browser)

        self.chk_cache = QCheckBox("Tự động dọn cache")
        self.chk_cache.setChecked(True)
        top_bar.addWidget(self.chk_cache)

        btn_minimize = QPushButton("-")
        btn_minimize.setToolTip("Thu nhỏ / ẩn bảng theo dõi")
        btn_minimize.setStyleSheet("background: #64748b; color: #ffffff; font-weight: bold; border-radius: 4px;")
        btn_minimize.setFixedWidth(34)
        btn_minimize.clicked.connect(self.hide_dashboard)
        top_bar.addWidget(btn_minimize)

        btn_close = QPushButton("✕ Đóng")
        btn_close.setStyleSheet("background: #ef4444; color: #ffffff; font-weight: bold; border-radius: 4px;")
        btn_close.setFixedWidth(70)
        btn_close.clicked.connect(self.close)
        top_bar.addWidget(btn_close)
        main_layout.addWidget(top_bar_widget)

        # ===== CONTENT =====
        splitter = QSplitter(Qt.Horizontal)
        self.splitter = splitter
        splitter.setChildrenCollapsible(False)

        # ===== LEFT PANEL =====
        left_widget = QWidget()
        self.left_widget = left_widget
        left_widget.setStyleSheet("background: #ffffff;")
        left_widget.setFixedWidth(self._preferred_left_width())
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(2, 2, 2, 2)
        left_layout.setSpacing(6)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(["Chạy", "Tên hồ sơ", "ID TikTok", "Xong", "QG", "Trạng thái"])
        # Cột 0-4 co lại vừa nội dung, cột 5 (Trạng thái) chiếm hết chỗ trống
        header = self.table.horizontalHeader()
        for col in range(5):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet(self.table.styleSheet() + "QTableWidget { alternate-background-color: #f9fafb; }")
        left_layout.addWidget(self.table, 1)
        self.load_data_to_table()

        # Features section header
        feat_header = QLabel("⚙️ Chức năng")
        feat_header.setStyleSheet("font-size: 13px; font-weight: bold; color: #3b82f6; padding: 6px 0 2px 0;")
        left_layout.addWidget(feat_header)

        # Features
        feature_scroll = QScrollArea()
        feature_scroll.setWidgetResizable(True)
        feature_container = QWidget()
        feature_container.setStyleSheet("background: #ffffff;")
        self.feature_layout = QVBoxLayout(feature_container)
        self.feature_layout.setSpacing(2)
        self.feature_layout.setContentsMargins(0, 0, 0, 0)

        features = [
            ("Đăng nhập", "#16a34a"),
            ("Đổi avatar", "#8b5cf6"),
            ("Tương tác ở Feed", "#3b82f6"),
            ("Tương tác theo từ khóa", "#3b82f6"),
            ("KYC(gologin)(pro)", "#f59e0b"),
            ("Đặt riêng tư (pro)", "#f59e0b"),
            ("Đổi mật khẩu Firefox (pro)", "#f59e0b"),
            ("Đăng nhập mail (pro)", "#f59e0b"),
            ("Xóa tài khoản(pro)", "#ef4444"),
        ]
        self.feature_widgets = {}
        for feat, color in features:
            row_w = QWidget()
            row_w.setStyleSheet(f"background: #f9fafb; border-radius: 4px; border-left: 3px solid {color};")
            row_layout = QHBoxLayout(row_w)
            row_layout.setContentsMargins(8, 4, 8, 4)
            chk = QCheckBox(feat)
            btn_s = QPushButton("Cài đặt")
            btn_s.setFixedWidth(55)
            btn_s.setStyleSheet("font-size: 11px; padding: 3px 8px;")
            if feat == "Tương tác ở Feed":
                btn_s.clicked.connect(self.open_feed_settings)
            elif feat == "Tương tác theo từ khóa":
                btn_s.clicked.connect(self.open_keyword_settings)
            elif feat == "Đổi avatar":
                btn_s.clicked.connect(self.open_avatar_settings)
            lbl_s = QLabel(f"0/{len(self.accounts_data)}")
            lbl_s.setStyleSheet(f"color: {color}; font-size: 11px;")
            row_layout.addWidget(chk)
            row_layout.addWidget(btn_s)
            row_layout.addStretch()
            row_layout.addWidget(lbl_s)
            self.feature_layout.addWidget(row_w)
            self.feature_widgets[feat] = {"chk": chk, "status": lbl_s}

        self.feature_layout.addStretch()
        feature_scroll.setWidget(feature_container)
        left_layout.addWidget(feature_scroll, 1)

        # Buttons
        btn_container = QWidget()
        btn_container.setStyleSheet("background: #e8eaed; border-radius: 6px; padding: 4px;")
        grid_buttons = QGridLayout(btn_container)
        grid_buttons.setSpacing(4)

        btn_run = QPushButton("▶ Chạy(T)")
        btn_run.setStyleSheet("background: #16a34a; color: #ffffff; font-weight: bold;")
        btn_run_p = QPushButton("▶ Chạy(P)")
        btn_run_p.setStyleSheet("background: #f8fafc; color: #334155; font-weight: bold;")
        btn_stop = QPushButton("🛑 Dừng")
        btn_stop.setStyleSheet("background: #ef4444; color: #ffffff; font-weight: bold;")
        btn_run.clicked.connect(self.on_run_tasks)
        btn_run_p.clicked.connect(self.on_open_browser)
        btn_stop.clicked.connect(self.on_stop_tasks)

        grid_buttons.addWidget(btn_run, 0, 0)
        grid_buttons.addWidget(btn_run_p, 0, 1)
        grid_buttons.addWidget(btn_stop, 0, 2)

        btn_browser = QPushButton("🌍 Mở trình duyệt")
        btn_browser.clicked.connect(self.on_open_browser)
        btn_tach = QPushButton("🔀 Tách điều khiển")
        btn_done = QPushButton("👁 Ẩn/hiện mục xong")
        grid_buttons.addWidget(btn_browser, 1, 0)
        grid_buttons.addWidget(btn_tach, 1, 1)
        grid_buttons.addWidget(btn_done, 1, 2)

        left_layout.addWidget(btn_container)
        splitter.addWidget(left_widget)

        # ===== RIGHT PANEL - Browser Grid =====
        self.right_widget = QWidget()
        self.right_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.right_widget.setStyleSheet("background: transparent;")
        self.right_layout = QVBoxLayout(self.right_widget)
        self.right_layout.setContentsMargins(0, 0, 0, 0)
        self.browser_grid_container = QWidget()
        self.browser_grid_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.browser_grid_container.setMaximumSize(16777215, 16777215)
        self.browser_grid_layout = QGridLayout(self.browser_grid_container)
        self.browser_grid_layout.setContentsMargins(0, 0, 0, 0)
        self.browser_grid_layout.setSpacing(1)
        self.browser_grid_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.browser_scroll_area = QScrollArea()
        self.browser_scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.browser_scroll_area.setWidgetResizable(True)
        self.browser_scroll_area.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.browser_scroll_area.setFrameShape(QFrame.NoFrame)
        self.browser_scroll_area.setStyleSheet("QScrollArea { border: none; padding: 0; }")
        self.browser_scroll_area.setWidget(self.browser_grid_container)
        self.browser_scroll_area.viewport().installEventFilter(self)
        self.right_layout.addWidget(self.browser_scroll_area, 1)
        self.browser_widgets = {}
        splitter.addWidget(self.right_widget)
        left_width = self._preferred_left_width()
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([left_width, max(1, self.width() - left_width)])

        # Khóa cứng splitter
        for i in range(splitter.count()):
            handle = splitter.handle(i)
            if handle:
                handle.setEnabled(False)
        main_layout.addWidget(splitter)
        QTimer.singleShot(0, lambda: self._resize_browser_grid(self._planned_profile_count))

    def _load_feed_settings(self):
        try:
            if os.path.exists(self._settings_path):
                with open(self._settings_path, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                    if isinstance(settings, dict):
                        settings.setdefault("gologin_passthrough_strict", True)
                        return settings
        except: pass
        return {"gologin_passthrough_strict": True}

    def _save_feed_settings(self):
        try:
            with open(self._settings_path, 'w', encoding='utf-8') as f:
                json.dump(self.feed_settings, f, indent=2, ensure_ascii=False)
        except: pass

    def open_feed_settings(self):
        dialog = FeedSettingsDialog(self, self.feed_settings)
        if dialog.exec_() == QDialog.Accepted:
            self.feed_settings = dialog.get_settings()
            self._save_feed_settings()

    def open_keyword_settings(self):
        """Mở dialog cài đặt từ khóa tìm kiếm."""
        dialog = KeywordSettingsDialog(self, self.feed_settings)
        if dialog.exec_() == QDialog.Accepted:
            kw_settings = dialog.get_settings()
            # Merge keyword settings vào feed_settings (không ghi đè các field khác)
            self.feed_settings.update(kw_settings)
            self._save_feed_settings()

    def open_avatar_settings(self):
        QMessageBox.information(
            self,
            "Doi avatar",
            "Giao dien da san sang: chon avatar trong Add/Edit profile hoac menu chuot phai tren bang account.\n\n"
            "Backend doi avatar TikTok se duoc trien khai o buoc tiep theo."
        )

    def load_data_to_table(self):
        for acc in self.accounts_data:
            row = self.table.rowCount()
            self.table.insertRow(row)
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk_item.setCheckState(Qt.Checked)
            self.table.setItem(row, 0, chk_item)
            profile_name = acc.get("profile_data", {}).get("ten_ho_so", "")
            if not profile_name:
                profile_name = acc.get("columns", {}).get("1", "")
            self.table.setItem(row, 1, QTableWidgetItem(profile_name))
            self.table.setItem(row, 2, QTableWidgetItem(acc.get("columns", {}).get("5", "")))
            self.table.setItem(row, 3, QTableWidgetItem("[ ]"))
            self.table.setItem(row, 4, QTableWidgetItem(""))
            self.table.setItem(row, 5, QTableWidgetItem("Chưa thực hiện."))

    def has_active_tasks(self):
        if self._running_rows or self._pending_start_timers or self._stopping_profile_keys:
            return True
        for widget in list(self.browser_widgets.values()):
            worker = getattr(widget, "worker", None)
            if worker and worker.isRunning():
                return True
        return False

    def replace_accounts_data(self, accounts_data, project_name=None):
        if self.has_active_tasks():
            return False
        self.accounts_data = list(accounts_data or [])
        if project_name:
            self.project_name = project_name
            self.dashboard_key = self.dashboard_key or project_name
            self.setWindowTitle(f"Bảng theo dõi - {self.project_name}")
            if hasattr(self, "lbl_project"):
                self.lbl_project.setText(f"Dự án: {self.project_name}")

        self._cancel_pending_timers()
        self._clear_browser_grid()
        self._running_rows.clear()
        self._row_profile_keys.clear()
        self._stopping_profile_keys.clear()
        self._pending_restart_request = None
        self._run_queue.clear()
        self._last_table_status = {}
        self.table.setRowCount(0)
        self.load_data_to_table()
        for feature_state in self.feature_widgets.values():
            feature_state["status"].setText(f"0/{len(self.accounts_data)}")
        return True

    def on_run_tasks(self):
        self._start_profile_queue(manual_only=False)
        return
        max_cols = 2
        # Xóa grid cũ
        for i in reversed(range(self.browser_grid_layout.count())):
            w = self.browser_grid_layout.itemAt(i).widget()
            if w: w.deleteLater()
        self.browser_widgets.clear()

        selected_features = [f for f, w in self.feature_widgets.items() if w["chk"].isChecked()]
        selected_rows = []
        for row in range(self.table.rowCount()):
            chk = self.table.item(row, 0)
            if chk and chk.checkState() == Qt.Checked:
                selected_rows.append(row)
                self.table.setItem(row, 5, QTableWidgetItem("Đang chạy..."))

        if not selected_rows:
            return

        for idx, row in enumerate(selected_rows):
            # Lấy trực tiếp bằng row index (tránh trùng tên profile)
            acc_info = self.accounts_data[row] if row < len(self.accounts_data) else {}
            profile_data = acc_info.get("profile_data", {})
            profile_name = profile_data.get("ten_ho_so", "") or acc_info.get("columns", {}).get("1", f"Profile_{row}")
            tiktok_id = acc_info.get("columns", {}).get("5", "")

            # Đảm bảo browser_id luôn có giá trị duy nhất
            browser_id = profile_data.get("browser_id", "")
            if not browser_id:
                browser_id = ""
                profile_data["browser_id"] = browser_id
                # Lưu lại vào accounts_data để lần sau không tạo lại
                acc_info.setdefault("profile_data", {})["browser_id"] = browser_id

            # Tạo BrowserPreviewWidget với GoLogin offline
            # profile_index = row (hàng thật trong bảng) để đảm bảo đúng profile dir + port
            embed_browser = self.chk_browser.isChecked()
            preview = BrowserPreviewWidget(profile_name, tiktok_id, profile_data,
                                           selected_features, self.feed_settings,
                                           profile_index=row,
                                           account_row=row,
                                           embed_browser=embed_browser)
            preview.data_updated.connect(self._on_preview_data_updated)
            preview.status_updated.connect(self._on_preview_status_updated)
            r, c = idx // max_cols, idx % max_cols
            self.browser_grid_layout.addWidget(preview, r, c)
            # Dùng browser_id làm key unique (không dùng profile_name vì có thể trùng)
            self.browser_widgets[browser_id] = preview

            # Bắt đầu chạy với delay từ UI (mỗi profile cách nhau spin_delay giây)
            delay_ms = idx * self.spin_delay.value() * 1000
            QTimer.singleShot(delay_ms, preview.start_automation)

        # Hàng giữ chiều cao cố định và cuộn dọc
        total_rows = (len(selected_rows) - 1) // max_cols + 1
        for col in range(max_cols):
            self.browser_grid_layout.setColumnStretch(col, 0)
        # Đặt chiều cao tối thiểu cho container để scroll được
        row_height = 657  # 656 (widget) + 1 (spacing)
        self.browser_grid_container.setMinimumHeight(total_rows * row_height)

        # Lưu DB nếu có browser_id mới được tạo tự động
        parent = self.parent()
        if parent and hasattr(parent, "save_accounts_to_db"):
            parent.save_accounts_to_db()

    def on_stop_tasks(self):
        self._stop_profile_queue()
        return
        for name, widget in self.browser_widgets.items():
            widget.stop_automation()
        self.browser_widgets.clear()

    def on_open_browser(self):
        self._start_profile_queue(manual_only=True)
        return
        """Mở browser cho các profile được chọn (để check proxy/tài khoản, không chạy automation)."""
        max_cols = 2
        # Xóa grid cũ
        for i in reversed(range(self.browser_grid_layout.count())):
            w = self.browser_grid_layout.itemAt(i).widget()
            if w: w.deleteLater()
        self.browser_widgets.clear()

        selected_rows = []
        for row in range(self.table.rowCount()):
            chk = self.table.item(row, 0)
            if chk and chk.checkState() == Qt.Checked:
                selected_rows.append(row)

        if not selected_rows:
            return

        embed_browser = self.chk_browser.isChecked()
        for idx, row in enumerate(selected_rows):
            acc_info = self.accounts_data[row] if row < len(self.accounts_data) else {}
            profile_data = acc_info.get("profile_data", {})
            profile_name = profile_data.get("ten_ho_so", "") or acc_info.get("columns", {}).get("1", f"Profile_{row}")
            tiktok_id = acc_info.get("columns", {}).get("5", "")

            # Đảm bảo browser_id luôn có giá trị duy nhất
            browser_id = profile_data.get("browser_id", "")
            if not browser_id:
                browser_id = ""
                profile_data["browser_id"] = browser_id
                acc_info.setdefault("profile_data", {})["browser_id"] = browser_id

            # Tạo BrowserPreviewWidget KHÔNG có chức năng automation
            preview = BrowserPreviewWidget(profile_name, tiktok_id, profile_data,
                                           selected_features=[],  # Không chạy task nào
                                           feed_settings={},
                                           profile_index=row,
                                           account_row=row,
                                           embed_browser=embed_browser)
            preview.data_updated.connect(self._on_preview_data_updated)
            preview.status_updated.connect(self._on_preview_status_updated)
            r, c = idx // max_cols, idx % max_cols
            self.browser_grid_layout.addWidget(preview, r, c)
            self.browser_widgets[browser_id] = preview

            # Mở browser theo nhịp chậm hơn để tránh dồn GoLogin API/Chrome/IO cùng lúc.
            open_delay_ms = max(2000, int(self.spin_delay.value()) * 1000)
            delay_ms = idx * open_delay_ms
            if delay_ms:
                self.table.setItem(row, 5, QTableWidgetItem(f"Chờ mở sau {delay_ms // 1000}s..."))
            QTimer.singleShot(delay_ms, preview.open_browser_only)

        total_rows = (len(selected_rows) - 1) // max_cols + 1
        for col in range(max_cols):
            self.browser_grid_layout.setColumnStretch(col, 0)
        row_height = 737
        self.browser_grid_container.setMinimumHeight(total_rows * row_height)

    def _load_feed_settings(self):
        try:
            if os.path.exists(self._settings_path):
                with open(self._settings_path, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                    if isinstance(settings, dict):
                        settings.setdefault("gologin_passthrough_strict", True)
                        return settings
        except: pass
        return {"gologin_passthrough_strict": True}

    def _save_feed_settings(self):
        try:
            with open(self._settings_path, 'w', encoding='utf-8') as f:
                json.dump(self.feed_settings, f, indent=2, ensure_ascii=False)
        except: pass

    def open_feed_settings(self):
        dialog = FeedSettingsDialog(self, self.feed_settings)
        if dialog.exec_() == QDialog.Accepted:
            self.feed_settings = dialog.get_settings()
            self._save_feed_settings()

    def open_keyword_settings(self):
        """Mở dialog cài đặt từ khóa tìm kiếm."""
        dialog = KeywordSettingsDialog(self, self.feed_settings)
        if dialog.exec_() == QDialog.Accepted:
            kw_settings = dialog.get_settings()
            # Merge keyword settings vào feed_settings (không ghi đè các field khác)
            self.feed_settings.update(kw_settings)
            self._save_feed_settings()

    def load_data_to_table(self):
        for acc in self.accounts_data:
            row = self.table.rowCount()
            self.table.insertRow(row)
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk_item.setCheckState(Qt.Checked)
            self.table.setItem(row, 0, chk_item)
            profile_name = acc.get("profile_data", {}).get("ten_ho_so", "")
            if not profile_name:
                profile_name = acc.get("columns", {}).get("1", "")
            self.table.setItem(row, 1, QTableWidgetItem(profile_name))
            self.table.setItem(row, 2, QTableWidgetItem(acc.get("columns", {}).get("5", "")))
            self.table.setItem(row, 3, QTableWidgetItem("[ ]"))
            self.table.setItem(row, 4, QTableWidgetItem(""))
            self.table.setItem(row, 5, QTableWidgetItem("Chưa thực hiện."))

    def on_run_tasks(self):
        self._start_profile_queue(manual_only=False)
        return
        max_cols = 2
        # Xóa grid cũ
        for i in reversed(range(self.browser_grid_layout.count())):
            w = self.browser_grid_layout.itemAt(i).widget()
            if w: w.deleteLater()
        self.browser_widgets.clear()

        selected_features = [f for f, w in self.feature_widgets.items() if w["chk"].isChecked()]
        selected_rows = []
        for row in range(self.table.rowCount()):
            chk = self.table.item(row, 0)
            if chk and chk.checkState() == Qt.Checked:
                selected_rows.append(row)
                self.table.setItem(row, 5, QTableWidgetItem("Đang chạy..."))

        if not selected_rows:
            return

        embed_browser = self.chk_browser.isChecked()
        for idx, row in enumerate(selected_rows):
            # Lấy trực tiếp bằng row index (tránh trùng tên profile)
            acc_info = self.accounts_data[row] if row < len(self.accounts_data) else {}
            profile_data = acc_info.get("profile_data", {})
            profile_name = profile_data.get("ten_ho_so", "") or acc_info.get("columns", {}).get("1", f"Profile_{row}")
            tiktok_id = acc_info.get("columns", {}).get("5", "")

            # Đảm bảo browser_id luôn có giá trị duy nhất
            browser_id = profile_data.get("browser_id", "")
            if not browser_id:
                browser_id = ""
                profile_data["browser_id"] = browser_id
                # Lưu lại vào accounts_data để lần sau không tạo lại
                acc_info.setdefault("profile_data", {})["browser_id"] = browser_id

            # Tạo BrowserPreviewWidget với GoLogin offline
            # profile_index = row (hàng thật trong bảng) để đảm bảo đúng profile dir + port
            preview = BrowserPreviewWidget(profile_name, tiktok_id, profile_data,
                                           selected_features, self.feed_settings,
                                           profile_index=row,
                                           account_row=row,
                                           embed_browser=embed_browser)
            preview.data_updated.connect(self._on_preview_data_updated)
            preview.status_updated.connect(self._on_preview_status_updated)
            r, c = idx // max_cols, idx % max_cols
            self.browser_grid_layout.addWidget(preview, r, c)
            # Dùng browser_id làm key unique (không dùng profile_name vì có thể trùng)
            self.browser_widgets[browser_id] = preview

            # Bắt đầu chạy với delay từ UI (mỗi profile cách nhau spin_delay giây)
            delay_ms = idx * self.spin_delay.value() * 1000
            QTimer.singleShot(delay_ms, preview.start_automation)

        # Hàng giữ chiều cao cố định và cuộn dọc
        total_rows = (len(selected_rows) - 1) // max_cols + 1
        for col in range(max_cols):
            self.browser_grid_layout.setColumnStretch(col, 0)
        # Đặt chiều cao tối thiểu cho container để scroll được
        row_height = 657  # 656 (widget) + 1 (spacing)
        self.browser_grid_container.setMinimumHeight(total_rows * row_height)

        # Lưu DB nếu có browser_id mới được tạo tự động
        parent = self.parent()
        if parent and hasattr(parent, "save_accounts_to_db"):
            parent.save_accounts_to_db()

    def on_stop_tasks(self):
        self._stop_profile_queue()
        return
        for name, widget in self.browser_widgets.items():
            widget.stop_automation()
        self.browser_widgets.clear()

    def on_open_browser(self):
        self._start_profile_queue(manual_only=True)
        return
        """Mở browser cho các profile được chọn (để check proxy/tài khoản, không chạy automation)."""
        max_cols = 2
        # Xóa grid cũ
        for i in reversed(range(self.browser_grid_layout.count())):
            w = self.browser_grid_layout.itemAt(i).widget()
            if w: w.deleteLater()
        self.browser_widgets.clear()

        selected_rows = []
        for row in range(self.table.rowCount()):
            chk = self.table.item(row, 0)
            if chk and chk.checkState() == Qt.Checked:
                selected_rows.append(row)

        if not selected_rows:
            return

        embed_browser = self.chk_browser.isChecked()
        for idx, row in enumerate(selected_rows):
            acc_info = self.accounts_data[row] if row < len(self.accounts_data) else {}
            profile_data = acc_info.get("profile_data", {})
            profile_name = profile_data.get("ten_ho_so", "") or acc_info.get("columns", {}).get("1", f"Profile_{row}")
            tiktok_id = acc_info.get("columns", {}).get("5", "")

            # Đảm bảo browser_id luôn có giá trị duy nhất
            browser_id = profile_data.get("browser_id", "")
            if not browser_id:
                browser_id = ""
                profile_data["browser_id"] = browser_id
                acc_info.setdefault("profile_data", {})["browser_id"] = browser_id

            # Tạo BrowserPreviewWidget KHÔNG có chức năng automation
            preview = BrowserPreviewWidget(profile_name, tiktok_id, profile_data,
                                           selected_features=[],  # Không chạy task nào
                                           feed_settings={},
                                           profile_index=row,
                                           account_row=row,
                                           embed_browser=embed_browser)
            preview.data_updated.connect(self._on_preview_data_updated)
            preview.status_updated.connect(self._on_preview_status_updated)
            r, c = idx // max_cols, idx % max_cols
            self.browser_grid_layout.addWidget(preview, r, c)
            self.browser_widgets[browser_id] = preview

            # Mở browser theo nhịp chậm hơn để tránh dồn GoLogin API/Chrome/IO cùng lúc.
            open_delay_ms = max(2000, int(self.spin_delay.value()) * 1000)
            delay_ms = idx * open_delay_ms
            if delay_ms:
                self.table.setItem(row, 5, QTableWidgetItem(f"Chờ mở sau {delay_ms // 1000}s..."))
            QTimer.singleShot(delay_ms, preview.open_browser_only)

        total_rows = (len(selected_rows) - 1) // max_cols + 1
        for col in range(max_cols):
            self.browser_grid_layout.setColumnStretch(col, 0)
        row_height = 737
        self.browser_grid_container.setMinimumHeight(total_rows * row_height)

    def _set_table_status(self, row, message, color="black"):
        if 0 <= row < self.table.rowCount():
            item = QTableWidgetItem(message)
            item.setForeground(QColor(color) if color else QColor("black"))
            self.table.setItem(row, 5, item)

    def _selected_rows(self):
        rows = []
        for row in range(self.table.rowCount()):
            chk = self.table.item(row, 0)
            if chk and chk.checkState() == Qt.Checked:
                rows.append(row)
        return rows

    def _ensure_profile_identity(self, row):
        acc_info = self.accounts_data[row] if row < len(self.accounts_data) else {}
        columns = acc_info.setdefault("columns", {})
        profile_data = acc_info.setdefault("profile_data", {})

        browser_id = first_real_gologin_profile_id(
            profile_data.get("gologin_profile_id"),
            profile_data.get("browser_id"),
            columns.get("4"),
        )
        profile_data["browser_id"] = browser_id
        profile_data["gologin_profile_id"] = browser_id
        columns["4"] = browser_id

        source_row = acc_info.get("source_row")
        parent = self.parent()
        if parent and hasattr(parent, "acc_table") and isinstance(source_row, int):
            if 0 <= source_row < parent.acc_table.rowCount():
                parent.acc_table.setItem(source_row, 4, QTableWidgetItem(browser_id))
                name_item = parent.acc_table.item(source_row, 1)
                if name_item:
                    pdata = dict(name_item.data(Qt.UserRole) or {})
                    pdata["browser_id"] = browser_id
                    name_item.setData(Qt.UserRole, pdata)

        return acc_info, profile_data, columns, browser_id

    def _profile_key_for_account(self, row):
        acc_info, profile_data, columns, browser_id = self._ensure_profile_identity(row)
        gologin_id = first_real_gologin_profile_id(
            profile_data.get("gologin_profile_id"),
            browser_id,
            columns.get("4"),
        )
        profile_name = profile_data.get("ten_ho_so", "") or columns.get("1", f"Profile_{row}")
        if gologin_id:
            return f"gologin:{gologin_id}", profile_name
        if browser_id:
            return f"browser:{browser_id}", profile_name
        return f"name:{profile_name.lower()}", profile_name

    def _stable_profile_index(self, profile_key):
        import zlib
        value = zlib.crc32((profile_key or "").encode("utf-8")) % 4000
        return max(1, value)

    def _claim_profile(self, profile_key, profile_name):
        parent = self.parent()
        if parent and hasattr(parent, "claim_profile_for_dashboard"):
            return parent.claim_profile_for_dashboard(
                profile_key,
                self.dashboard_key,
                self.project_name,
                profile_name,
            )
        return True, ""

    def _running_profile_owner(self, profile_key):
        parent = self.parent()
        if parent and hasattr(parent, "get_running_profile_owner"):
            return parent.get_running_profile_owner(profile_key, self.dashboard_key)
        return ""

    def _show_profile_conflict_popup(self, conflicts):
        if not conflicts:
            return

        new_conflicts = []
        for profile_key, profile_name, owner in conflicts:
            notice_key = f"{profile_key}|{owner}"
            if notice_key in self._blocked_profile_notice_keys:
                continue
            self._blocked_profile_notice_keys.add(notice_key)
            new_conflicts.append((profile_name, owner))

        if not new_conflicts:
            return

        lines = [f"- {name} (dang chay o: {owner})" for name, owner in new_conflicts[:10]]
        if len(new_conflicts) > 10:
            lines.append(f"... va {len(new_conflicts) - 10} ho so khac")
        QMessageBox.warning(
            self,
            "Chan ho so trung",
            "Cac ho so sau dang chay o bang theo doi khac nen da bi chan:\n\n"
            + "\n".join(lines)
        )

    def _release_profile(self, profile_key):
        parent = self.parent()
        if parent and hasattr(parent, "release_profile_for_dashboard"):
            parent.release_profile_for_dashboard(profile_key, self.dashboard_key)

    def _mark_profile_stopping(self, row, profile_key):
        if not profile_key:
            return
        self._stopping_profile_keys[profile_key] = row
        self._set_table_status(
            row,
            "Dang don trinh duyet cu, vui long cho Orbita/GoLogin dong xong...",
            "#f59e0b",
        )

    def _on_preview_browser_closed(self, row, profile_key, reason="closed"):
        if not profile_key:
            profile_key = self._row_profile_keys.get(row)
        if not profile_key:
            return

        current_key = self._row_profile_keys.get(row)
        if current_key == profile_key:
            self._row_profile_keys.pop(row, None)
        self._stopping_profile_keys.pop(profile_key, None)
        self._running_rows.discard(row)
        self._release_profile(profile_key)
        self._set_table_status(row, "Da dong trinh duyet, co the chay lai", "#6b7280")

        if self._pending_restart_request and not self._stopping_profile_keys:
            request = self._pending_restart_request
            self._pending_restart_request = None
            rows = [
                r for r in request.get("rows", [])
                if isinstance(r, int) and 0 <= r < self.table.rowCount()
            ]
            if rows:
                for restart_row in rows:
                    self._set_table_status(restart_row, "Da don xong, dang chay lai...", "#3b82f6")
                QTimer.singleShot(
                    250,
                    lambda rows=rows, manual_only=bool(request.get("manual_only")):
                        self._start_profile_queue(
                            manual_only=manual_only,
                            preset_rows=rows,
                            from_pending=True,
                        )
                )
            return

        if not self._run_cancelled:
            self._schedule_next_profiles(initial=False)

    def _cancel_pending_timers(self):
        for timer in list(self._pending_start_timers.values()):
            timer.stop()
            timer.deleteLater()
        self._pending_start_timers.clear()

    def _check_gologin_local_requirements(self, selected_rows):
        try:
            from gologin_config import load_gologin_settings
            settings = load_gologin_settings()
        except Exception as e:
            QMessageBox.warning(
                self,
                "GoLogin Local SDK",
                f"Khong doc duoc cau hinh GoLogin:\n{e}",
            )
            return False

        if not (settings.get("api_key") or "").strip():
            QMessageBox.warning(
                self,
                "GoLogin Local SDK",
                "Thiếu GoLogin API Key. Vào menu API | Cookie để nhập token trước khi chạy.",
            )
            return False

        try:
            from gologin import GoLogin  # noqa: F401
        except Exception:
            QMessageBox.warning(
                self,
                "GoLogin Local SDK",
                "May nay chua cai GoLogin SDK.\n\n"
                "Cai truoc bang lenh:\n"
                "python -m pip install gologin",
            )
            return False

        browser_root = os.path.join(os.path.expanduser("~"), ".gologin", "browser")
        has_orbita = False
        if os.path.isdir(browser_root):
            for name in os.listdir(browser_root):
                exe_path = os.path.join(browser_root, name, "chrome.exe")
                if name.startswith("orbita-browser-") and os.path.exists(exe_path):
                    has_orbita = True
                    break
        if not has_orbita:
            QMessageBox.warning(
                self,
                "GoLogin Local SDK",
                "Chưa thấy Orbita local của GoLogin SDK.\n\n"
                "Hãy cài/mở GoLogin hoặc chạy GoLogin SDK lần đầu để tải Orbita trước.\n"
                f"Thư mục kiểm tra:\n{browser_root}",
            )
            return False

        missing = []
        for row in selected_rows:
            if row < 0 or row >= len(self.accounts_data):
                continue
            acc_info = self.accounts_data[row]
            profile_data = acc_info.get("profile_data", {})
            columns = acc_info.get("columns", {})
            resolved_id = first_real_gologin_profile_id(
                profile_data.get("gologin_profile_id"),
                profile_data.get("browser_id"),
                columns.get("4"),
            )
            if not resolved_id:
                name = (
                    profile_data.get("ten_ho_so")
                    or columns.get("1")
                    or f"Row {row + 1}"
                )
                missing.append(str(name))

        if missing:
            sample = "\n".join(missing[:8])
            more = "" if len(missing) <= 8 else f"\n... va {len(missing) - 8} profile khac"
            QMessageBox.warning(
                self,
                "GoLogin Local SDK",
                "Cac profile nay chua co GoLogin Profile ID that nen khong the chay bang GoLogin:\n\n"
                f"{sample}{more}",
            )
            return False

        return True

    def _clear_browser_grid(self):
        for i in reversed(range(self.browser_grid_layout.count())):
            widget = self.browser_grid_layout.itemAt(i).widget()
            if widget:
                widget.deleteLater()
        self.browser_widgets.clear()
        self._next_grid_index = 0
        self._planned_profile_count = 0
        self._resize_browser_grid(0)

    def _reset_browser_scroll(self):
        if not hasattr(self, "browser_scroll_area"):
            return
        self.browser_scroll_area.horizontalScrollBar().setValue(0)
        self.browser_scroll_area.verticalScrollBar().setValue(0)

    def _current_browser_grid_profile_count(self):
        return max(
            int(getattr(self, "_planned_profile_count", 0) or 0),
            len(getattr(self, "browser_widgets", {}) or {}),
        )

    def _queue_browser_grid_resize(self):
        if self._browser_grid_resize_pending:
            return
        self._browser_grid_resize_pending = True
        QTimer.singleShot(0, self._flush_browser_grid_resize)

    def _flush_browser_grid_resize(self):
        self._browser_grid_resize_pending = False
        self._resize_browser_grid(self._current_browser_grid_profile_count())

    def _browser_grid_available_size(self):
        width = 0
        height = 0
        try:
            viewport = self.browser_scroll_area.viewport()
            if viewport:
                width = int(viewport.width() or 0)
                height = int(viewport.height() or 0)
        except Exception:
            pass
        if width < 100:
            try:
                width = int(self.right_widget.width() or 0)
            except Exception:
                width = 0
        if height < 100:
            try:
                height = int(self.right_widget.height() or 0)
            except Exception:
                height = 0
        if width < 100:
            width = max(520, int(self._dashboard_preset["window_w"]) - int(self._preferred_left_width()))
        if height < 100:
            height = max(424, int(self._dashboard_preset["window_h"]) - 40)
        return width, height

    def _resize_browser_grid(self, profile_count, max_cols=None):
        spacing = max(0, int(self.browser_grid_layout.spacing()))
        requested_cols = max(1, int(max_cols or self._dashboard_preset["cols"]))
        cols = max(1, min(requested_cols, max(1, profile_count)))
        available_w, _available_h = self._browser_grid_available_size()
        min_multi_col_w = 420
        while cols > 1:
            per_col_w = (available_w - max(0, cols - 1) * spacing) // cols
            if per_col_w >= min_multi_col_w:
                break
            cols -= 1

        rows = max(1, (max(1, profile_count) - 1) // cols + 1)
        per_col_w = max(380, (available_w - max(0, cols - 1) * spacing) // cols)
        preset_preview_w = int(self._dashboard_preset["preview_w"])
        if profile_count <= 1 and cols == 1:
            preview_w = max(preset_preview_w, per_col_w)
        else:
            preview_w = min(preset_preview_w, per_col_w)

        browser_h = max(320, int(preview_w * 800 / 960))
        preview_h = browser_h + 56
        self._current_grid_columns = max(1, cols)
        self._current_preview_width = preview_w
        self._current_browser_height = browser_h

        if profile_count <= 0:
            self.browser_grid_container.setMinimumSize(1, 1)
            self.browser_grid_container.resize(1, 1)
            self._reset_browser_scroll()
            return

        rows = (profile_count - 1) // self._current_grid_columns + 1
        width = self._current_grid_columns * preview_w + max(0, self._current_grid_columns - 1) * spacing
        height = rows * preview_h + max(0, rows - 1) * spacing

        self.browser_grid_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        for col in range(max(3, self._current_grid_columns + 1)):
            self.browser_grid_layout.setColumnStretch(col, 0)
        for row in range(max(rows, 1)):
            self.browser_grid_layout.setRowStretch(row, 0)

        widgets = list(getattr(self, "browser_widgets", {}).values())
        for index, widget in enumerate(widgets):
            self.browser_grid_layout.removeWidget(widget)
            if hasattr(widget, "set_preview_size"):
                widget.set_preview_size(preview_w, browser_h)
            self.browser_grid_layout.addWidget(
                widget,
                index // self._current_grid_columns,
                index % self._current_grid_columns,
            )

        self.browser_grid_container.setMinimumSize(width, height)
        self.browser_grid_container.resize(width, height)
        self._reset_browser_scroll()
        QTimer.singleShot(0, self._reset_browser_scroll)

    def _schedule_parent_save(self, delay_ms=1200):
        parent = self.parent()
        if not parent or not hasattr(parent, "save_accounts_to_db"):
            return
        self._save_pending = True
        self._save_timer.start(max(0, int(delay_ms)))

    def _flush_parent_save(self):
        if self._save_timer.isActive():
            self._save_timer.stop()
        if not self._save_pending:
            return
        self._save_pending = False
        parent = self.parent()
        if parent and hasattr(parent, "save_accounts_to_db"):
            parent.save_accounts_to_db()

    def _stop_profile_queue(self, clear_pending_restart=True):
        if clear_pending_restart:
            self._pending_restart_request = None
        self._run_generation += 1
        self._run_cancelled = True
        self._cancel_pending_timers()
        self._run_queue.clear()
        for row, profile_key in list(self._row_profile_keys.items()):
            widget = self.browser_widgets.get(profile_key)
            if not widget:
                self._on_preview_browser_closed(row, profile_key, "missing_widget")
                continue
            worker = getattr(widget, "worker", None)
            if row not in self._running_rows and not (worker and worker.isRunning()):
                self._on_preview_browser_closed(row, profile_key, "not_started")
                continue
            self._mark_profile_stopping(row, profile_key)
            try:
                widget.stop_automation()
            except Exception:
                self._on_preview_browser_closed(row, profile_key, "stop_error")
        self._running_rows.clear()

    def cleanup_runtime_sessions(self):
        affected_rows = (
            set(self._running_rows)
            | set(self._pending_start_timers.keys())
            | set(self._run_queue)
            | set(self._row_profile_keys.keys())
        )
        preview_count = len(self.browser_widgets)
        timer_count = len(self._pending_start_timers)
        queued_count = len(self._run_queue)

        self._flush_parent_save()
        self._stop_profile_queue()
        if not self._stopping_profile_keys:
            self._clear_browser_grid()
        self._blocked_profile_notice_keys.clear()
        self._flush_parent_save()

        for row in affected_rows:
            if 0 <= row < self.table.rowCount():
                if row in self._stopping_profile_keys.values():
                    self._set_table_status(
                        row,
                        "Dang don trinh duyet cu, vui long cho Orbita/GoLogin dong xong...",
                        "#f59e0b",
                    )
                else:
                    self._set_table_status(row, "Da don phien trinh duyet", "#6b7280")

        return {
            "previews": preview_count,
            "timers": timer_count,
            "queued": queued_count,
            "rows": len(affected_rows),
        }

    def _filter_blocked_profile_rows(self, selected_rows):
        allowed_rows = []
        conflicts = []
        seen_keys = {}

        for row in selected_rows:
            profile_key, profile_name = self._profile_key_for_account(row)
            if profile_key in seen_keys:
                owner = "bang nay"
                self._set_table_status(row, "Bi chan - profile trung trong lan chay nay", "#f59e0b")
                conflicts.append((profile_key, profile_name, owner))
                continue

            if profile_key in self._stopping_profile_keys:
                self._set_table_status(
                    row,
                    "Dang don trinh duyet cu - cho dong xong roi chay lai",
                    "#f59e0b",
                )
                conflicts.append((profile_key, profile_name, "dang don trinh duyet cu"))
                continue

            owner = self._running_profile_owner(profile_key)
            if owner:
                self._set_table_status(row, f"Bi chan - dang chay o {owner}", "#f59e0b")
                conflicts.append((profile_key, profile_name, owner))
                continue

            seen_keys[profile_key] = row
            allowed_rows.append(row)

        self._show_profile_conflict_popup(conflicts)
        return allowed_rows

    def _start_profile_queue(self, manual_only=False, preset_rows=None, from_pending=False):
        selected_rows = list(preset_rows) if preset_rows is not None else self._selected_rows()
        if not selected_rows:
            return
        if not self._check_gologin_local_requirements(selected_rows):
            return

        active_profile_keys = set(self._row_profile_keys.values()) | set(self._stopping_profile_keys.keys())
        if active_profile_keys and not from_pending:
            self._pending_restart_request = {
                "manual_only": bool(manual_only),
                "rows": list(selected_rows),
            }
            self._stop_profile_queue(clear_pending_restart=False)
            for row in selected_rows:
                self._set_table_status(
                    row,
                    "Dang don trinh duyet cu, se chay lai khi Orbita/GoLogin dong xong...",
                    "#f59e0b",
                )
            return

        if self._stopping_profile_keys:
            self._pending_restart_request = {
                "manual_only": bool(manual_only),
                "rows": list(selected_rows),
            }
            for row in selected_rows:
                self._set_table_status(
                    row,
                    "Dang cho don trinh duyet cu truoc khi chay lai...",
                    "#f59e0b",
                )
            return

        self._stop_profile_queue()
        self._clear_browser_grid()
        self._run_generation += 1
        self._run_cancelled = False
        self._run_mode = "manual" if manual_only else "automation"

        selected_rows = self._filter_blocked_profile_rows(selected_rows)
        if not selected_rows:
            self._run_cancelled = True
            return

        self._run_queue = list(selected_rows)
        for row in selected_rows:
            self._set_table_status(row, "Cho chay...", "#6b7280")

        self._planned_profile_count = len(selected_rows)
        self._resize_browser_grid(len(selected_rows))
        self._prepare_preview_grid(selected_rows)

        self._schedule_parent_save()

        self._schedule_next_profiles(initial=True)

    def _create_profile_preview(
        self,
        row,
        profile_key,
        profile_name,
        selected_features,
        feed_settings,
        embed_browser,
        start_collapsed=False,
    ):
        acc_info = self.accounts_data[row] if row < len(self.accounts_data) else {}
        profile_data = acc_info.get("profile_data", {})
        columns = acc_info.get("columns", {})
        tiktok_id = columns.get("5", "")
        preview = BrowserPreviewWidget(
            profile_name,
            tiktok_id,
            profile_data,
            selected_features,
            feed_settings,
            profile_index=self._stable_profile_index(profile_key),
            account_row=row,
            embed_browser=embed_browser,
            preview_width=self._current_preview_width,
            browser_height=self._current_browser_height,
            planned_profile_count=max(1, int(self._planned_profile_count or 1)),
            start_collapsed=start_collapsed,
        )
        run_generation = self._run_generation
        preview.data_updated.connect(self._on_preview_data_updated)
        preview.status_updated.connect(self._on_preview_status_updated)
        preview.automation_finished.connect(
            lambda row, profile_key=profile_key, generation=run_generation:
                self._on_preview_finished(row, profile_key, generation)
        )
        preview.browser_closed.connect(
            lambda row, reason, profile_key=profile_key:
                self._on_preview_browser_closed(row, profile_key, reason)
        )
        return preview

    def _prepare_preview_grid(self, selected_rows):
        embed_browser = self.chk_browser.isChecked()
        selected_features = [] if self._run_mode == "manual" else [
            f for f, w in self.feature_widgets.items() if w["chk"].isChecked()
        ]
        # Keep GoLogin passthrough flags even in manual-open mode.
        feed_settings = dict(self.feed_settings or {})

        for row in selected_rows:
            profile_key, profile_name = self._profile_key_for_account(row)
            preview = self._create_profile_preview(
                row,
                profile_key,
                profile_name,
                selected_features,
                feed_settings,
                embed_browser,
                start_collapsed=False,
            )
            grid_index = self._next_grid_index
            self._next_grid_index += 1
            self.browser_grid_layout.addWidget(
                preview,
                grid_index // self._current_grid_columns,
                grid_index % self._current_grid_columns,
            )
            self.browser_widgets[profile_key] = preview
            self._row_profile_keys[row] = profile_key
            preview.update_status("Cho chay...", "gray")

    def _schedule_next_profiles(self, initial=False):
        if self._run_cancelled:
            return
        if not initial and self._pending_start_timers:
            return

        max_threads = max(1, int(self.spin_luong.value()))
        active_slots = (
            len(self._running_rows)
            + len(self._pending_start_timers)
            + len(self._stopping_profile_keys)
        )
        available = max_threads - active_slots
        if available <= 0:
            return

        delay_unit = max(0, int(self.spin_delay.value()) * 1000)
        for i in range(available):
            if not self._run_queue:
                break
            row = self._run_queue.pop(0)
            if initial and active_slots == 0:
                delay_ms = delay_unit * i
            else:
                delay_ms = delay_unit * (i + 1)

            if delay_ms <= 0:
                self._set_table_status(row, "Dang khoi dong...", "#3b82f6")
                self._start_scheduled_profile(row, None)
                continue

            if delay_ms:
                self._set_table_status(row, f"Ch? ch?y sau {delay_ms // 1000}s...", "#6b7280")
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda row=row, timer=timer: self._start_scheduled_profile(row, timer))
            self._pending_start_timers[row] = timer
            timer.start(delay_ms)

    def _start_scheduled_profile(self, row, timer):
        self._pending_start_timers.pop(row, None)
        if timer is not None:
            timer.deleteLater()
        if self._run_cancelled:
            return

        profile_key, profile_name = self._profile_key_for_account(row)
        if profile_key in self._stopping_profile_keys:
            self._set_table_status(
                row,
                "Dang don trinh duyet cu - cho dong xong roi chay lai",
                "#f59e0b",
            )
            self._schedule_next_profiles(initial=False)
            return
        owner_row = None
        for known_row, known_key in self._row_profile_keys.items():
            if known_key == profile_key:
                owner_row = known_row
                break
        if owner_row is not None and owner_row != row:
            self._set_table_status(row, "Bo qua - profile dang chay trong bang nay", "#f59e0b")
            self._schedule_next_profiles(initial=False)
            return
        self._schedule_parent_save()
        claimed, owner = self._claim_profile(profile_key, profile_name)
        if not claimed:
            self._set_table_status(row, f"Bo qua - dang chay o {owner}", "#f59e0b")
            self._show_profile_conflict_popup([(profile_key, profile_name, owner)])
            self._schedule_next_profiles(initial=False)
            return

        preview = self.browser_widgets.get(profile_key)
        if not preview:
            embed_browser = self.chk_browser.isChecked()
            selected_features = [] if self._run_mode == "manual" else [
                f for f, w in self.feature_widgets.items() if w["chk"].isChecked()
            ]
            preview = self._create_profile_preview(
                row,
                profile_key,
                profile_name,
                selected_features,
                {} if self._run_mode == "manual" else self.feed_settings,
                embed_browser,
                start_collapsed=False,
            )
            grid_index = self._next_grid_index
            self._next_grid_index += 1
            self.browser_grid_layout.addWidget(
                preview,
                grid_index // self._current_grid_columns,
                grid_index % self._current_grid_columns,
            )
            self.browser_widgets[profile_key] = preview
            self._row_profile_keys[row] = profile_key
            self._queue_browser_grid_resize()

        worker = getattr(preview, "worker", None)
        if worker and worker.isRunning():
            self._set_table_status(row, "Dang chay...", "#3b82f6")
            return

        self._running_rows.add(row)
        self._set_table_status(row, "Dang chay...", "#3b82f6")
        preview.update_status("Dang khoi dong worker...", "blue")

        try:
            if self._run_mode == "manual":
                preview.open_browser_only()
            else:
                preview.start_automation()
        except Exception as e:
            self._set_table_status(row, f"Loi khoi chay: {str(e)[:80]}", "#ef4444")
            self._on_preview_browser_closed(row, profile_key, "start_error")

    def _on_preview_finished(self, row, profile_key=None, generation=None):
        if generation is not None and generation != self._run_generation:
            return
        if not profile_key:
            profile_key = self._row_profile_keys.get(row)
        if profile_key:
            self._mark_profile_stopping(row, profile_key)
        self._running_rows.discard(row)

    def showEvent(self, event):
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
        self._queue_browser_grid_resize()
        parent = self.parent()
        if parent and hasattr(parent, "_update_dashboard_button"):
            parent._update_dashboard_button()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._queue_browser_grid_resize()

    def eventFilter(self, obj, event):
        if (
            hasattr(self, "browser_scroll_area")
            and obj is self.browser_scroll_area.viewport()
            and event.type() in (QEvent.Resize, QEvent.Show)
        ):
            self._queue_browser_grid_resize()
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event):
        self.raise_()
        self.activateWindow()
        super().mousePressEvent(event)

    def hide_dashboard(self):
        """Ẩn bảng theo dõi, giữ nguyên worker/browser đang chạy."""
        self.hide()
        self.dashboard_hidden.emit()

    def closeEvent(self, event):
        """Khi đóng dashboard → đóng tất cả browser GRACEFUL (chờ cookie flush)."""
        self._flush_parent_save()
        self.on_stop_tasks()
        self._flush_parent_save()

        # Browser close/cookie flush continues in each worker background thread.
        # Do not join here; blocking the GUI thread makes the dashboard Not Responding.
        self._flush_parent_save()
        self.dashboard_closed.emit()
        super().closeEvent(event)

    def _on_preview_data_updated(self, account_row, new_data):
        tiktok_id = new_data.get("tiktok_id", "")
        cookie = new_data.get("cookie", "")
        login_error = new_data.get("login_error", "")
        
        # 1. Update the table in Dashboard (bảng theo dõi)
        if 0 <= account_row < self.table.rowCount():
            if tiktok_id:
                self.table.setItem(account_row, 2, QTableWidgetItem(tiktok_id))
                
        # 2. Update self.accounts_data
        if 0 <= account_row < len(self.accounts_data):
            acc = self.accounts_data[account_row]
            if tiktok_id:
                acc.setdefault("columns", {})["5"] = tiktok_id
            if cookie:
                pdata = acc.setdefault("profile_data", {})
                old_cookie = pdata.get("cookie", "")
                if old_cookie and old_cookie != cookie:
                    pdata["cookie_backup"] = old_cookie
                    pdata["cookie_backup_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                pdata["cookie"] = cookie
                acc.setdefault("columns", {})["13"] = cookie
            # Lưu refresh_token mới (Microsoft xoay token mỗi lần dùng)
            refresh_token = new_data.get("refresh_token", "")
            if refresh_token:
                acc.setdefault("profile_data", {})["refresh_token"] = refresh_token
            if "proxy_type" in new_data and new_data.get("proxy_type"):
                acc.setdefault("profile_data", {})["proxy_type"] = new_data.get("proxy_type")
            gologin_proxy_synced = new_data.get("gologin_proxy_synced", "")
            if gologin_proxy_synced:
                acc.setdefault("profile_data", {})["gologin_proxy_synced"] = gologin_proxy_synced
            gologin_fp_refreshed = new_data.get("gologin_fingerprint_refreshed_at", "")
            if gologin_fp_refreshed:
                acc.setdefault("profile_data", {})["gologin_fingerprint_refreshed_at"] = gologin_fp_refreshed
            runtime_fp = new_data.get("gologin_runtime_fingerprint")
            if runtime_fp:
                acc.setdefault("profile_data", {})["gologin_runtime_fingerprint"] = runtime_fp
            for key in ("avatar_path", "avatar_status", "avatar_updated_at", "avatar_last_error"):
                if key in new_data:
                    acc.setdefault("profile_data", {})[key] = new_data.get(key, "")

        # 3. Tìm đúng dòng trong acc_table bằng browser_id (không dùng index)
        parent = self.parent()
        if parent and hasattr(parent, "acc_table"):
            t = parent.acc_table
            
            # Lấy browser_id từ accounts_data để tìm chính xác
            target_browser_id = ""
            source_row = None
            if 0 <= account_row < len(self.accounts_data):
                source_row = self.accounts_data[account_row].get("source_row")
                target_browser_id = self.accounts_data[account_row].get(
                    "profile_data", {}).get("browser_id", "")
            
            # Tìm dòng đúng trong bảng chính bằng browser_id (cột 4)
            target_row = -1
            if isinstance(source_row, int) and 0 <= source_row < t.rowCount():
                target_row = source_row
            if target_browser_id:
                for r in range(t.rowCount()):
                    item = t.item(r, 4)  # Cột 4 = ID AdsPower / browser_id
                    if item and item.text() == target_browser_id:
                        target_row = r
                        break
            
            # Fallback: dùng account_row nếu không tìm được bằng browser_id
            if target_row < 0:
                target_row = account_row
                
            if 0 <= target_row < t.rowCount():
                if tiktok_id:
                    # ✅ Đăng nhập thành công — có TikTok ID
                    t.setItem(target_row, 2, QTableWidgetItem("Yes"))
                    # Cột 5: ID TikTok
                    t.setItem(target_row, 5, QTableWidgetItem(tiktok_id))
                elif login_error:
                    # ❌ Đăng nhập lỗi — hiển thị lý do lỗi ở cột Logged
                    error_item = QTableWidgetItem(f"❌ {login_error}")
                    error_item.setForeground(QColor("#ef4444"))
                    t.setItem(target_row, 2, error_item)
                # Cột 13: Cookie
                if cookie:
                    t.setItem(target_row, 13, QTableWidgetItem(cookie))
                # UserRole: cập nhật profile_data
                name_item = t.item(target_row, 1)
                if name_item:
                    pdata = name_item.data(Qt.UserRole) or {}
                    if tiktok_id:
                        pdata["tiktok_id"] = tiktok_id
                    if cookie:
                        pdata["cookie"] = cookie
                    refresh_token = new_data.get("refresh_token", "")
                    if refresh_token:
                        pdata["refresh_token"] = refresh_token
                    if "proxy_type" in new_data and new_data.get("proxy_type"):
                        pdata["proxy_type"] = new_data.get("proxy_type")
                    gologin_proxy_synced = new_data.get("gologin_proxy_synced", "")
                    if gologin_proxy_synced:
                        pdata["gologin_proxy_synced"] = gologin_proxy_synced
                    gologin_fp_refreshed = new_data.get("gologin_fingerprint_refreshed_at", "")
                    if gologin_fp_refreshed:
                        pdata["gologin_fingerprint_refreshed_at"] = gologin_fp_refreshed
                    runtime_fp = new_data.get("gologin_runtime_fingerprint")
                    if runtime_fp:
                        pdata["gologin_runtime_fingerprint"] = runtime_fp
                    for key in ("avatar_path", "avatar_status", "avatar_updated_at", "avatar_last_error"):
                        if key in new_data:
                            pdata[key] = new_data.get(key, "")
                    if new_data.get("avatar_path"):
                        t.setItem(target_row, 0, QTableWidgetItem("Co"))
                    if new_data.get("avatar_status"):
                        status_text = (
                            "Avatar OK" if new_data.get("avatar_status") == "success"
                            else f"Avatar loi: {new_data.get('avatar_last_error', '')[:80]}"
                        )
                        t.setItem(target_row, 12, QTableWidgetItem(status_text))
                    name_item.setData(Qt.UserRole, pdata)

        # 4. Lưu file JSON (KHÔNG reload bảng → tránh trùng)
        self._schedule_parent_save()

    def _on_preview_status_updated(self, account_row, msg, color):
        if not hasattr(self, "_last_table_status"):
            self._last_table_status = {}
        payload = (msg, color)
        if self._last_table_status.get(account_row) == payload:
            return
        self._last_table_status[account_row] = payload
        """Cập nhật trạng thái vào cột 5 (Trạng thái) của bảng theo dõi."""
        # Tìm hàng trong bảng tương ứng account_row
        for table_row in range(self.table.rowCount()):
            # Dòng bảng khớp với account_row
            stt_item = self.table.item(table_row, 0)
            if stt_item and stt_item.data(Qt.UserRole) == account_row:
                item = QTableWidgetItem(msg)
                item.setForeground(QColor(color) if color else QColor("black"))
                self.table.setItem(table_row, 5, item)
                return
        # Fallback: dùng account_row trực tiếp nếu bảng khớp 1:1
        if 0 <= account_row < self.table.rowCount():
            item = QTableWidgetItem(msg)
            item.setForeground(QColor(color) if color else QColor("black"))
            self.table.setItem(account_row, 5, item)

# ============================================================
# WIDGET PREVIEW - CDP Screencast (Live Preview)
# ============================================================
class BrowserPreviewWidget(QFrame):
    """Widget hiển thị live preview browser qua CDP Screencast."""
    data_updated = pyqtSignal(int, dict)
    status_updated = pyqtSignal(int, str, str)
    automation_finished = pyqtSignal(int)
    browser_closed = pyqtSignal(int, str)
    embed_finished = pyqtSignal(bool, int, int, str)
    _EMBED_OVERSCAN_X = 2
    _EMBED_OVERSCAN_Y = 2
    _active_keyboard_widget = None
    
    def __init__(
        self,
        profile_name,
        tiktok_id,
        profile_data=None,
        selected_features=None,
        feed_settings=None,
        profile_index=0,
        account_row=0,
        embed_browser=True,
        preview_width=960,
        browser_height=680,
        planned_profile_count=1,
        start_collapsed=False,
        parent=None,
    ):
        super().__init__(parent)
        self.profile_name = profile_name
        self.tiktok_id = tiktok_id
        self.profile_data = profile_data or {}
        self.selected_features = selected_features or []
        self.feed_settings = feed_settings or {}
        self.profile_index = profile_index
        self.account_row = account_row
        self.embed_browser = embed_browser
        self.planned_profile_count = max(1, int(planned_profile_count or 1))
        self.worker = None
        self._last_browser_focus_ts = 0.0
        self._last_status_payload = None
        self._last_status_color = ""
        self._last_status_emit = None
        self._pending_status_emit = None
        self._embed_timer = None
        self._embed_state = None
        self._embed_last_wait_status = 0
        self._embed_position_timer = None
        self._embedded_hwnd = 0
        self._embed_insets = (0, 0, 0, 0)
        self._fingerprint_worker = None
        self._browser_surface_visible = True
        self._keyboard_target_hwnd = 0

        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet("""
            BrowserPreviewWidget {
                background: #ffffff; border: none;
            }
        """)
        # Kích thước = container(960x680) + header(32) + status(24)
        self._header_height = 32
        self._status_height = 24
        self._preview_width = max(520, int(preview_width or 960))
        self._browser_height = max(368, int(browser_height or 680))
        self.setFixedSize(self._preview_width, self._browser_height + self._header_height + self._status_height)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header bar (giữ nguyên)
        header = QWidget()
        header.setFixedHeight(self._header_height)
        header.setStyleSheet("""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #e8eaed, stop:1 #f0f2f5);
            border-top-left-radius: 6px; border-top-right-radius: 6px;
        """)
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(8, 0, 8, 0)

        self.status_dot = QLabel("\u25cf")
        self.status_dot.setStyleSheet("color: #9ca3af; font-size: 10px;")
        self.status_dot.setFixedWidth(14)
        h_layout.addWidget(self.status_dot)

        lbl_name = QLabel(f"{profile_name}")
        lbl_name.setStyleSheet("color: #1a1a2e; font-weight: bold; font-size: 12px;")
        h_layout.addWidget(lbl_name)

        self.lbl_id = QLabel(f"@{tiktok_id}" if tiktok_id else "")
        self.lbl_id.setStyleSheet("color: #6b7280; font-size: 11px; margin-left: 6px;")
        h_layout.addWidget(self.lbl_id)
        h_layout.addStretch()

        self.btn_fp = QPushButton("\U0001f511")
        btn_fp = self.btn_fp
        self.btn_fp.setFixedSize(24, 24)
        btn_fp.setToolTip("Đổi Fingerprint")
        self.btn_fp.setToolTip("Làm mới vân tay GoLogin")
        self.btn_fp.setStyleSheet("background: transparent; border: none; font-size: 13px;")
        self.btn_fp.clicked.connect(self._change_fingerprint)
        h_layout.addWidget(self.btn_fp)
        layout.addWidget(header)

        # === BROWSER CONTAINER (Native Window Embedding — 60 FPS như SSMATool) ===
        self.browser_container = QWidget()
        self.browser_container.setFixedSize(self._preview_width, self._browser_height)
        self.browser_container.setStyleSheet("background: #1a1a2e;")
        self.browser_container.setAttribute(Qt.WA_NativeWindow, True)
        # ★ KHÔNG set WA_TransparentForMouseEvents vì Chrome WS_CHILD tự nhận click
        # Chỉ cần forward keyboard focus khi user click vào container
        self.browser_container.setFocusPolicy(Qt.ClickFocus)
        self.browser_container.installEventFilter(self)
        self.browser_container.mousePressEvent = self._on_container_click
        layout.addWidget(self.browser_container, stretch=1)
        app = QApplication.instance()
        if app:
            app.installEventFilter(self)

        # Chrome is embedded as a native child window, so Qt may not receive the click event.
        # Poll only while the left mouse button is down and the cursor is inside this browser.
        self._focus_timer = QTimer(self)
        self._focus_timer.setInterval(650)
        self._focus_timer.timeout.connect(self._focus_browser_if_mouse_down)
        if self.embed_browser:
            self._focus_timer.start()

        self._keyboard_keepalive_timer = QTimer(self)
        self._keyboard_keepalive_timer.setInterval(300)
        self._keyboard_keepalive_timer.timeout.connect(self._keep_browser_keyboard_focus)

        self._status_emit_timer = QTimer(self)
        self._status_emit_timer.setSingleShot(True)
        self._status_emit_timer.timeout.connect(self._flush_status_emit)

        # Status bar (giữ nguyên)
        self.lbl_status = QLabel("\u23f3 Chờ chạy...")
        self.lbl_status.setFixedHeight(self._status_height)
        self.lbl_status.setStyleSheet("""
            color: #6b7280; padding: 0 8px; font-size: 11px;
            background: #f9fafb; border-top: 1px solid #e4e7ec;
            border-bottom-left-radius: 6px; border-bottom-right-radius: 6px;
        """)
        layout.addWidget(self.lbl_status)

        if start_collapsed:
            self._set_browser_surface_visible(False)

    def set_preview_size(self, preview_width, browser_height):
        self._preview_width = max(520, int(preview_width or self._preview_width))
        self._browser_height = max(368, int(browser_height or self._browser_height))
        self.browser_container.setFixedSize(self._preview_width, self._browser_height)
        total_height = self._header_height + self._status_height
        if self._browser_surface_visible:
            total_height += self._browser_height
        self.setFixedSize(self._preview_width, total_height)
        self._sync_embedded_browser_geometry()

    def _notify_dashboard_resize(self):
        parent = self.parent()
        depth = 0
        while parent and depth < 8:
            if hasattr(parent, "_queue_browser_grid_resize"):
                parent._queue_browser_grid_resize()
                return
            parent = parent.parent()
            depth += 1

    def _set_browser_surface_visible(self, visible):
        visible = bool(visible)
        if visible == self._browser_surface_visible:
            return
        self._browser_surface_visible = visible
        self.browser_container.setVisible(visible)
        total_height = self._header_height + self._status_height
        if visible:
            total_height += self._browser_height
        self.setFixedSize(self._preview_width, total_height)
        if visible:
            QTimer.singleShot(0, self._sync_embedded_browser_geometry)
        self._notify_dashboard_resize()

    def update_status(self, text, color="gray"):
        payload = (text, color)
        if payload == self._last_status_payload:
            return
        self._last_status_payload = payload

        color_map = {
            "blue": "#3b82f6", "green": "#16a34a", "red": "#ef4444",
            "orange": "#f59e0b", "gray": "#6b7280"
        }
        qt_color = color_map.get(color, color)

        if color != self._last_status_color:
            dot_map = {"green": "#16a34a", "red": "#ef4444", "blue": "#3b82f6"}
            self.status_dot.setStyleSheet(f"color: {dot_map.get(color, '#9ca3af')}; font-size: 10px;")
            self.lbl_status.setStyleSheet(f"""
                color: {qt_color}; padding: 0 8px; font-size: 11px;
                background: #f9fafb; border-top: 1px solid #e4e7ec;
                border-bottom-left-radius: 6px; border-bottom-right-radius: 6px;
            """)
            self._last_status_color = color

        if self.lbl_status.text() != text:
            self.lbl_status.setText(text)

    def _connect_worker_signals(self):
        self.worker.status_update.connect(self._on_status)
        self.worker.finished_signal.connect(self._on_finished)
        self.worker.profile_update_signal.connect(self._on_profile_update)
        if hasattr(self.worker, "browser_ready_signal"):
            self.worker.browser_ready_signal.connect(self._on_browser_ready)
        if hasattr(self.worker, "browser_closed_signal"):
            self.worker.browser_closed_signal.connect(self._on_browser_closed)

    def _on_browser_closed(self, reason="closed"):
        self.update_status("Da dong trinh duyet.", "gray")
        self._stop_embed_timers()
        self.browser_closed.emit(self.account_row, str(reason or "closed"))

    def _on_browser_ready(self, info):
        info = dict(info or {})
        worker = self.worker
        if not worker or int(info.get("worker_id") or 0) != id(worker):
            return

        if not self.embed_browser:
            if hasattr(worker, "notify_embed_result"):
                worker.notify_embed_result(False, 0, 0)
            return

        widget_id = int(self.browser_container.winId())
        info["widget_id"] = widget_id
        timeout = max(5.0, float(info.get("timeout") or 30.0))
        self._embed_state = {
            "worker": worker,
            "info": info,
            "attempt": 0,
            "deadline": time.time() + timeout,
            "last_error": "",
            "last_hwnd_count": 0,
            "last_pid_count": 0,
            "last_match_count": 0,
        }
        self._embed_last_wait_status = 0
        profile_hint = str(info.get("profile_id") or "unknown")[:12]
        self._on_status(
            f"Embed scan: port={info.get('debug_port') or 0}, profile={profile_hint}",
            "blue",
        )

        if not self._embed_timer:
            self._embed_timer = QTimer(self)
            self._embed_timer.setInterval(200)
            self._embed_timer.timeout.connect(self._poll_browser_embed)
        if not self._embed_timer.isActive():
            self._embed_timer.start()
        self._poll_browser_embed()

    def _stop_embed_timers(self):
        if self._embed_timer and self._embed_timer.isActive():
            self._embed_timer.stop()
        if self._embed_position_timer and self._embed_position_timer.isActive():
            self._embed_position_timer.stop()
        self._embed_state = None
        self._embedded_hwnd = 0
        self._embed_insets = (0, 0, 0, 0)

    def _finish_browser_embed(self, success, hwnd=0, pid=0, message="", color="orange"):
        state = self._embed_state or {}
        worker = state.get("worker") or self.worker
        if self._embed_timer and self._embed_timer.isActive():
            self._embed_timer.stop()
        self._embed_state = None

        if success:
            self._embedded_hwnd = int(hwnd or 0)
            self._start_embed_position_timer()
        elif self._embed_position_timer and self._embed_position_timer.isActive():
            self._embed_position_timer.stop()

        if message:
            self._on_status(message, color)

        has_embed_listener = False
        try:
            has_embed_listener = self.receivers(self.embed_finished) > 0
        except Exception:
            has_embed_listener = False
        self.embed_finished.emit(bool(success), int(hwnd or 0), int(pid or 0), str(message or ""))
        if not has_embed_listener and worker and hasattr(worker, "notify_embed_result"):
            worker.notify_embed_result(success, hwnd, pid)

    def _poll_browser_embed(self):
        state = self._embed_state
        if not state:
            if self._embed_timer and self._embed_timer.isActive():
                self._embed_timer.stop()
            return

        worker = state.get("worker")
        info = state.get("info") or {}
        if not worker or worker is not self.worker:
            self._finish_browser_embed(False, message="Worker đã thay đổi trước khi nhúng trình duyệt.")
            return
        if getattr(worker, "_stop_flag", False):
            self._finish_browser_embed(False)
            return

        attempt = int(state.get("attempt") or 0) + 1
        state["attempt"] = attempt

        if time.time() > float(state.get("deadline") or 0):
            detail = (
                f"hwnd={state.get('last_hwnd_count', 0)}, "
                f"pid={state.get('last_pid_count', 0)}, "
                f"match={state.get('last_match_count', 0)}, "
                f"port={info.get('debug_port') or 0}, "
                f"profile={str(info.get('profile_id') or 'unknown')[:12]}"
            )
            if state.get("last_error"):
                detail += f", err={state.get('last_error')}"
            self._finish_browser_embed(
                False,
                message=f"Khong tim thay cua so Orbita/Chrome dung GoLogin profile ({detail})",
                color="orange",
            )
            return

        target, stats = self._find_embed_target(state)
        state.update(stats)
        if not target:
            now = time.time()
            if attempt in (40, 100, 180, 300) or now - self._embed_last_wait_status > 2.5:
                self._embed_last_wait_status = now
                self._on_status(
                    f"Embed wait: hwnd={state.get('last_hwnd_count', 0)}, "
                    f"pid={state.get('last_pid_count', 0)}, port={info.get('debug_port') or 0}",
                    "blue",
                )
            return

        hwnd, pid = target
        ok, error = self._attach_browser_hwnd(hwnd, int(info.get("widget_id") or 0))
        if ok:
            if hasattr(worker, "_browser_pids") and pid:
                try:
                    worker._browser_pids.add(int(pid))
                except Exception:
                    pass
            try:
                worker._embedded_hwnd = int(hwnd)
            except Exception:
                pass
            self._finish_browser_embed(
                True,
                hwnd,
                pid,
                f"Browser nhúng OK! pid={pid}, hwnd={hwnd}, port={info.get('debug_port') or 0}",
                "green",
            )
            QTimer.singleShot(150, self._focus_browser)
            return

        state["last_error"] = error
        if attempt in (1, 40, 100, 180, 300):
            self._on_status(f"Nhúng trình duyệt thử lại: {error[:80]}", "orange")

    def _find_embed_target(self, state):
        import ctypes

        try:
            import psutil
            import win32gui
            import win32process
        except Exception as exc:
            return None, {"last_error": str(exc)[:80]}

        worker = state.get("worker")
        info = state.get("info") or {}
        attempt = int(state.get("attempt") or 0)
        debug_port = int(info.get("debug_port") or 0)
        profile_dir = info.get("profile_dir") or ""
        profile_id_hint = str(info.get("profile_id") or "").strip().lower()
        embed_token = str(info.get("embed_token") or "").strip().lower()
        launch_started_at = float(info.get("launch_started_at") or 0.0)
        known_pid = int(info.get("process_pid") or 0)
        browser_names = {"chrome.exe", "orbita-browser.exe", "chromium.exe"}

        def norm_path(path):
            if worker and hasattr(worker, "_norm_proc_path"):
                try:
                    return worker._norm_proc_path(path)
                except Exception:
                    pass
            try:
                return os.path.abspath(str(path or "")).replace("\\", "/").rstrip("/").lower()
            except Exception:
                return str(path or "").replace("\\", "/").rstrip("/").lower()

        profile_dir_norm = norm_path(profile_dir)
        all_pids = set()
        if known_pid:
            all_pids.add(known_pid)

        proc_cache = {}

        def proc_info(pid):
            if pid in proc_cache:
                return proc_cache[pid]
            data = {"name": "", "exe": "", "cmd": "", "created_at": 0.0}
            try:
                proc = psutil.Process(int(pid))
                data["name"] = (proc.name() or "").lower()
                try:
                    data["exe"] = norm_path(proc.exe())
                except Exception:
                    pass
                try:
                    data["cmd"] = " ".join(str(arg) for arg in proc.cmdline()).replace("\\", "/").lower()
                except Exception:
                    pass
                try:
                    data["created_at"] = float(proc.create_time())
                except Exception:
                    pass
            except Exception:
                pass
            proc_cache[pid] = data
            return data

        def collect_hint_pids():
            for proc in psutil.process_iter(["name", "pid", "cmdline"]):
                try:
                    name = (proc.info.get("name") or "").lower()
                    if name not in browser_names:
                        continue
                    cmd = " ".join(str(arg) for arg in (proc.info.get("cmdline") or [])).lower()
                    matched = False
                    if embed_token and embed_token in cmd:
                        matched = True
                    elif profile_dir_norm and profile_dir_norm in cmd.replace("\\", "/"):
                        matched = True
                    elif profile_id_hint and profile_id_hint in cmd:
                        matched = True
                    elif debug_port and f"remote-debugging-port={debug_port}" in cmd:
                        matched = True
                    elif known_pid and int(proc.info.get("pid") or 0) == known_pid:
                        matched = True
                    if not matched:
                        continue
                    all_pids.add(int(proc.info["pid"]))
                    for child in proc.children(recursive=True):
                        all_pids.add(int(child.pid))
                except Exception:
                    continue

        collect_hint_pids()

        hwnds = []

        def hwnd_area(hwnd):
            try:
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                return max(0, right - left) * max(0, bottom - top)
            except Exception:
                return 0

        def enum_cb(hwnd, _):
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
                ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
                hwnds.append((hwnd, int(process_id.value), area, win32gui.GetParent(hwnd)))
            except Exception:
                pass
            return True

        try:
            win32gui.EnumWindows(enum_cb, None)
        except Exception as exc:
            return None, {
                "last_error": str(exc)[:80],
                "last_hwnd_count": len(hwnds),
                "last_pid_count": len(all_pids),
                "last_match_count": 0,
            }

        def has_hint(data):
            text = f"{data.get('exe', '')} {data.get('cmd', '')}".lower()
            if embed_token and embed_token in text:
                return True
            if profile_dir_norm and profile_dir_norm in text:
                return True
            if profile_id_hint and profile_id_hint in text:
                return True
            if debug_port and f"remote-debugging-port={debug_port}" in text:
                return True
            return False

        exact = []
        hinted = []
        fresh = []
        for hwnd, wpid, area, parent_hwnd in hwnds:
            if wpid in all_pids:
                exact.append((area, hwnd, wpid))
                continue

            data = proc_info(wpid)
            if data["name"] not in browser_names:
                continue
            if launch_started_at and data["created_at"] < launch_started_at - 10:
                continue
            if has_hint(data):
                hinted.append((data["created_at"], area, hwnd, wpid))
            elif attempt >= 120 and not parent_hwnd:
                fresh.append((data["created_at"], area, hwnd, wpid))

        match_count = len(exact) + len(hinted) + len(fresh)
        stats = {
            "last_error": "",
            "last_hwnd_count": len(hwnds),
            "last_pid_count": len(all_pids),
            "last_match_count": match_count,
        }

        if exact:
            _area, hwnd, pid = max(exact, key=lambda item: item[0])
            return (hwnd, pid), stats
        if hinted:
            _created, _area, hwnd, pid = max(hinted, key=lambda item: (item[0], item[1]))
            return (hwnd, pid), stats
        if len(fresh) == 1:
            _created, _area, hwnd, pid = fresh[0]
            self._on_status(f"Embed fallback: fresh hwnd pid={pid}, port={debug_port}", "orange")
            return (hwnd, pid), stats
        return None, stats

    def _container_client_size(self, widget_id):
        try:
            import win32gui

            _left, _top, right, bottom = win32gui.GetClientRect(widget_id)
            width = max(1, int(right))
            height = max(1, int(bottom))
            if width >= 100 and height >= 100:
                return width, height
        except Exception:
            pass
        return int(self.browser_container.width() or self._preview_width), int(self.browser_container.height() or self._browser_height)

    def _measure_embedded_window_insets(self, hwnd):
        try:
            import win32gui

            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            client_left, client_top = win32gui.ClientToScreen(hwnd, (0, 0))
            client_width, client_height = win32gui.GetClientRect(hwnd)[2:4]
            client_right = int(client_left) + int(client_width)
            client_bottom = int(client_top) + int(client_height)
            return (
                max(0, int(client_left) - int(left)),
                max(0, int(client_top) - int(top)),
                max(0, int(right) - client_right),
                max(0, int(bottom) - client_bottom),
            )
        except Exception:
            return None

    def _embedded_window_rect(self, widget_id, hwnd=None):
        width, height = self._container_client_size(widget_id)
        insets = tuple(max(0, int(v)) for v in (self._embed_insets or (0, 0, 0, 0)))
        if hwnd:
            measured = self._measure_embedded_window_insets(hwnd)
            if measured and any(measured):
                insets = measured
                self._embed_insets = measured
        if not any(insets):
            overscan_x = max(0, int(self._EMBED_OVERSCAN_X))
            overscan_y = max(0, int(self._EMBED_OVERSCAN_Y))
            insets = (overscan_x, overscan_y, overscan_x, overscan_y)
        left_inset, top_inset, right_inset, bottom_inset = insets
        return (
            -left_inset,
            -top_inset,
            max(1, width + left_inset + right_inset),
            max(1, height + top_inset + bottom_inset),
        )

    def _attach_browser_hwnd(self, hwnd, widget_id):
        import ctypes

        try:
            import win32con
            import win32gui
        except Exception as exc:
            return False, str(exc)

        if not hwnd or not widget_id:
            return False, "missing hwnd/container"

        try:
            if not win32gui.IsWindow(widget_id):
                return False, "container hwnd invalid"
            if not win32gui.IsWindow(hwnd):
                return False, "browser hwnd invalid"

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            pos_x, pos_y, width, height = self._embedded_window_rect(widget_id, hwnd)
            old_style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
            old_ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)

            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

            parent_style = win32gui.GetWindowLong(widget_id, win32con.GWL_STYLE)
            parent_style |= (
                getattr(win32con, "WS_CLIPCHILDREN", 0x02000000) |
                getattr(win32con, "WS_CLIPSIBLINGS", 0x04000000)
            )
            win32gui.SetWindowLong(widget_id, win32con.GWL_STYLE, parent_style)

            style = old_style
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
            win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style)

            ex_style = old_ex_style
            ex_style &= ~(
                win32con.WS_EX_DLGMODALFRAME |
                win32con.WS_EX_WINDOWEDGE |
                win32con.WS_EX_CLIENTEDGE |
                win32con.WS_EX_STATICEDGE |
                getattr(win32con, "WS_EX_NOACTIVATE", 0x08000000) |
                getattr(win32con, "WS_EX_APPWINDOW", 0x00040000) |
                getattr(win32con, "WS_EX_TOPMOST", 0x00000008)
            )
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex_style)

            kernel32.SetLastError(0)
            win32gui.SetParent(hwnd, widget_id)
            if win32gui.GetParent(hwnd) != widget_id:
                try:
                    win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, old_style)
                    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, old_ex_style)
                except Exception:
                    pass
                return False, f"SetParent failed, winerr={kernel32.GetLastError()}"

            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            win32gui.SetWindowPos(
                hwnd,
                win32con.HWND_TOP,
                pos_x,
                pos_y,
                width,
                height,
                win32con.SWP_SHOWWINDOW |
                getattr(win32con, "SWP_FRAMECHANGED", 0x0020) |
                win32con.SWP_NOACTIVATE |
                getattr(win32con, "SWP_NOOWNERZORDER", 0x0200),
            )
            pos_x, pos_y, width, height = self._embedded_window_rect(widget_id, hwnd)
            win32gui.SetWindowPos(
                hwnd,
                None,
                pos_x,
                pos_y,
                width,
                height,
                win32con.SWP_NOZORDER |
                win32con.SWP_NOACTIVATE |
                getattr(win32con, "SWP_NOOWNERZORDER", 0x0200),
            )
            return True, ""
        except Exception as exc:
            try:
                if "old_style" in locals():
                    win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, old_style)
                if "old_ex_style" in locals():
                    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, old_ex_style)
            except Exception:
                pass
            return False, str(exc)

    def _start_embed_position_timer(self):
        if not self._embed_position_timer:
            self._embed_position_timer = QTimer(self)
            self._embed_position_timer.setInterval(120)
            self._embed_position_timer.timeout.connect(self._sync_embedded_browser_geometry)
        if not self._embed_position_timer.isActive():
            self._embed_position_timer.start()
        self._sync_embedded_browser_geometry()

    def _sync_embedded_browser_geometry(self):
        hwnd = int(self._embedded_hwnd or 0)
        if not hwnd:
            if self._embed_position_timer and self._embed_position_timer.isActive():
                self._embed_position_timer.stop()
            return
        try:
            import win32con
            import win32gui

            widget_id = int(self.browser_container.winId())
            if not win32gui.IsWindow(hwnd) or not win32gui.IsWindow(widget_id):
                self._embedded_hwnd = 0
                self._embed_position_timer.stop()
                return
            if win32gui.GetParent(hwnd) != widget_id:
                self._embedded_hwnd = 0
                self._embed_position_timer.stop()
                return
            pos_x, pos_y, width, height = self._embedded_window_rect(widget_id, hwnd)
            win32gui.SetWindowPos(
                hwnd,
                None,
                pos_x,
                pos_y,
                width,
                height,
                win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE,
            )
        except Exception:
            if self._embed_position_timer and self._embed_position_timer.isActive():
                self._embed_position_timer.stop()

    def start_automation(self):
        """Khởi động CDP Worker + Native Window Embedding (60 FPS)."""
        from PyQt5.QtWidgets import QApplication
        QApplication.processEvents()
        self._set_browser_surface_visible(True)
        self.update_status("Dang khoi dong worker...", "blue")

        container_w = self.browser_container.width()
        container_h = self.browser_container.height()
        if container_w < 100: container_w = 840
        if container_h < 100: container_h = 600

        # Lấy HWND của container để nhúng browser vào nếu chk_browser checked
        widget_id = int(self.browser_container.winId()) if self.embed_browser else 0

        from cdp_worker import CDPWorker
        self.update_status("Dang tao CDP worker...", "blue")
        self.worker = CDPWorker(
            profile_index=self.profile_index,
            profile_data=self.profile_data,
            selected_features=self.selected_features,
            feed_settings=self.feed_settings,
            container_width=container_w,
            container_height=container_h,
            widget_id=widget_id,
            planned_profile_count=self.planned_profile_count,
        )
        self._connect_worker_signals()
        self.worker.start()
        self.update_status("Worker da start, dang mo GoLogin/Orbita...", "blue")

    def _on_profile_update(self, data):
        tiktok_id = data.get("tiktok_id", "")
        cookie = data.get("cookie", "")
        refresh_token = data.get("refresh_token", "")
        if tiktok_id:
            self.lbl_id.setText(f"@{tiktok_id}")
            self.tiktok_id = tiktok_id
        if cookie:
            self.profile_data["cookie"] = cookie
        if refresh_token:
            self.profile_data["refresh_token"] = refresh_token
        for key in ("avatar_path", "avatar_status", "avatar_updated_at", "avatar_last_error"):
            if key in data:
                self.profile_data[key] = data.get(key, "")
        self.data_updated.emit(self.account_row, data)

    def _queue_status_emit(self, msg, color):
        payload = (msg, color)
        if payload == self._last_status_emit:
            return
        self._pending_status_emit = payload
        if not self._status_emit_timer.isActive():
            self._status_emit_timer.start(300)

    def _flush_status_emit(self):
        if not self._pending_status_emit:
            return
        msg, color = self._pending_status_emit
        self._pending_status_emit = None
        payload = (msg, color)
        if payload == self._last_status_emit:
            return
        self._last_status_emit = payload
        self.status_updated.emit(self.account_row, msg, color)

    def _on_status(self, msg, color):
        self.update_status(msg, color)
        self._queue_status_emit(msg, color)
        if "Browser nhúng OK" in msg or "Browser đã nhúng" in msg:
            QTimer.singleShot(150, self._focus_browser)

    def _on_finished(self, result):
        self._pending_status_emit = None
        self._status_emit_timer.stop()
        if result == "success":
            self.update_status("Hoan thanh.", "green")
            self.status_updated.emit(self.account_row, "Hoan thanh.", "#16a34a")
        else:
            detail = str(result or "error")
            if detail.startswith("error:"):
                detail = detail.split(":", 1)[1].strip() or "error"
            self.update_status(f"Loi: {detail}", "red")
            self.status_updated.emit(self.account_row, f"Loi: {detail}", "#ef4444")
        self.automation_finished.emit(self.account_row)
        return
        if result == "success":
            self.update_status("\u2705 Hoàn thành tất cả!", "green")

    def stop_automation(self):
        if self.worker:
            self.worker.stop()
            # KHÔNG wait() — tránh block UI
        self.update_status("Đã dừng.", "gray")

    def open_browser_only(self):
        """Chỉ mở browser (không chạy automation) — để check proxy/tài khoản."""
        from PyQt5.QtWidgets import QApplication
        QApplication.processEvents()
        self._set_browser_surface_visible(True)
        self.update_status("Dang khoi dong worker...", "blue")

        container_w = self.browser_container.width()
        container_h = self.browser_container.height()
        if container_w < 100: container_w = 960
        if container_h < 100: container_h = 680

        widget_id = int(self.browser_container.winId()) if self.embed_browser else 0

        from cdp_worker import CDPWorker
        self.update_status("Dang tao CDP worker...", "blue")
        self.worker = CDPWorker(
            profile_index=self.profile_index,
            profile_data=self.profile_data,
            selected_features=[],  # KHÔNG chạy automation
            feed_settings={},
            container_width=container_w,
            container_height=container_h,
            widget_id=widget_id,
            manual_only=True,
            planned_profile_count=self.planned_profile_count,
        )
        self._connect_worker_signals()
        self.worker.start()
        self.update_status("Worker da start, dang mo GoLogin/Orbita...", "blue")

    def _change_fingerprint(self):
        if self._fingerprint_worker and self._fingerprint_worker.isRunning():
            self.update_status("Đang làm mới vân tay GoLogin...", "blue")
            return

        profile_id = (self.profile_data.get("gologin_profile_id") or "").strip()
        if not profile_id:
            import re
            legacy_id = (self.profile_data.get("browser_id") or "").strip()
            if re.fullmatch(r"[a-fA-F0-9]{24}", legacy_id):
                profile_id = legacy_id
        if not profile_id:
            QMessageBox.warning(self, "GoLogin", "Profile nay chua co GoLogin Profile ID.")
            self.update_status("Thiếu GoLogin Profile ID.", "red")
            return

        try:
            from gologin_config import load_gologin_settings
            settings = load_gologin_settings()
        except Exception as exc:
            QMessageBox.warning(self, "GoLogin", f"Khong doc duoc cau hinh GoLogin:\n{exc}")
            self.update_status("Khong doc duoc cau hinh GoLogin.", "red")
            return

        api_token = (settings.get("api_key") or "").strip()
        if not api_token:
            QMessageBox.warning(self, "GoLogin", "Thiếu GoLogin API Key. Vào menu API | Cookie để nhập token.")
            self.update_status("Thiếu GoLogin API Key.", "red")
            return

        running_note = ""
        if self.worker and self.worker.isRunning():
            running_note = "\n\nProfile đang mở, vân tay mới sẽ áp dụng ở lần mở profile tiếp theo."
        reply = QMessageBox.question(
            self,
            "Làm mới vân tay GoLogin",
            "Làm mới vân tay GoLogin cho profile này?\n\n"
            "Tool chỉ gọi API fingerprint của GoLogin, không xóa cookie/local data."
            f"{running_note}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self.btn_fp.setEnabled(False)
        self.update_status("Đang làm mới vân tay GoLogin...", "blue")
        self.status_updated.emit(self.account_row, "Đang làm mới vân tay GoLogin...", "#3b82f6")

        worker = GoLoginFingerprintRefreshWorker(api_token, profile_id, self)
        self._fingerprint_worker = worker
        worker.finished_signal.connect(self._on_fingerprint_refresh_done)
        worker.finished.connect(self._clear_fingerprint_worker)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _clear_fingerprint_worker(self):
        self._fingerprint_worker = None

    def _on_fingerprint_refresh_done(self, success, message, data):
        self.btn_fp.setEnabled(True)
        message = str(message or "").strip()
        if success:
            refreshed_at = time.strftime("%Y-%m-%d %H:%M:%S")
            self.profile_data["gologin_fingerprint_refreshed_at"] = refreshed_at
            self.data_updated.emit(self.account_row, {
                "gologin_fingerprint_refreshed_at": refreshed_at,
                "gologin_fingerprint_profile_id": (data or {}).get("profile_id", ""),
            })
            self.update_status(message, "green")
            self.status_updated.emit(self.account_row, message, "#16a34a")
            return

        self.update_status(f"Lỗi làm mới vân tay: {message}", "red")
        self.status_updated.emit(self.account_row, f"Lỗi làm mới vân tay: {message}", "#ef4444")
        QMessageBox.warning(self, "GoLogin", message or "Làm mới vân tay GoLogin thất bại.")

    def _on_container_click(self, event):
        """★ Khi click vào browser container → chuyển focus sang Chrome HWND.
        Nếu không làm điều này, keyboard input sẽ ở lại PyQt5 app."""
        self._set_browser_keyboard_active(True)
        self._focus_browser()
        # Cho phép event tiếp tục xử lý bình thường
        QWidget.mousePressEvent(self.browser_container, event)

    def eventFilter(self, obj, event):
        event_type = event.type()

        if obj is self.browser_container and event_type in (
            QEvent.MouseButtonPress,
            QEvent.MouseButtonDblClick,
            QEvent.FocusIn,
        ):
            self._set_browser_keyboard_active(True)
            QTimer.singleShot(0, self._focus_browser)

        if (
            BrowserPreviewWidget._active_keyboard_widget is self
            and event_type == QEvent.MouseButtonPress
            and not self._cursor_is_over_browser_container()
        ):
            widget = QApplication.widgetAt(QCursor.pos())
            if not (widget and (widget is self.browser_container or self.browser_container.isAncestorOf(widget))):
                self._set_browser_keyboard_active(False)

        if (
            BrowserPreviewWidget._active_keyboard_widget is self
            and event_type in (QEvent.KeyPress, QEvent.KeyRelease)
            and self._should_forward_key_event(obj)
        ):
            if self._forward_key_to_browser(event):
                return True

        return super().eventFilter(obj, event)

    def _set_browser_keyboard_active(self, active=True):
        if active:
            BrowserPreviewWidget._active_keyboard_widget = self
            self.browser_container.setFocus(Qt.MouseFocusReason)
            if not self._keyboard_keepalive_timer.isActive():
                self._keyboard_keepalive_timer.start()
            return
        if BrowserPreviewWidget._active_keyboard_widget is self:
            BrowserPreviewWidget._active_keyboard_widget = None
        if self._keyboard_keepalive_timer.isActive():
            self._keyboard_keepalive_timer.stop()

    def _keep_browser_keyboard_focus(self):
        if BrowserPreviewWidget._active_keyboard_widget is not self:
            self._keyboard_keepalive_timer.stop()
            return
        if not (self.worker and getattr(self.worker, "_embedded_hwnd", 0)):
            return
        if not self.browser_container.isVisible():
            return
        focus_widget = QApplication.focusWidget()
        focus_inside = focus_widget and (focus_widget is self.browser_container or self.isAncestorOf(focus_widget))
        if self._cursor_is_over_browser_container() or focus_inside:
            self._focus_browser()

    def _cursor_is_over_browser_container(self):
        try:
            point = self.browser_container.mapFromGlobal(QCursor.pos())
            return self.browser_container.rect().contains(point)
        except Exception:
            return False

    def _should_forward_key_event(self, obj):
        if not (self.worker and getattr(self.worker, "_embedded_hwnd", 0)):
            return False
        if not self.browser_container.isVisible():
            return False
        if obj is self.browser_container or obj is self:
            return True

        focus_widget = QApplication.focusWidget()
        if focus_widget and (focus_widget is self.browser_container or self.isAncestorOf(focus_widget)):
            return True

        return self._cursor_is_over_browser_container()

    def _browser_keyboard_hwnd(self):
        try:
            import ctypes
            import win32gui
            import win32process

            user32 = ctypes.windll.user32
            root_hwnd = int(getattr(self.worker, "_embedded_hwnd", 0) or 0)
            if not (root_hwnd and win32gui.IsWindow(root_hwnd)):
                return 0

            foreground_hwnd = user32.GetForegroundWindow()
            candidate = user32.GetFocus()
            if candidate and (candidate == root_hwnd or user32.IsChild(root_hwnd, candidate)):
                self._keyboard_target_hwnd = int(candidate)
                return int(candidate)

            for thread_hwnd in (foreground_hwnd, root_hwnd):
                try:
                    tid = win32process.GetWindowThreadProcessId(int(thread_hwnd))[0]
                    focused = self._thread_focus_hwnd(tid, root_hwnd)
                    if focused:
                        self._keyboard_target_hwnd = int(focused)
                        return int(focused)
                except Exception:
                    pass

            cursor_hwnd = self._browser_hwnd_at_cursor(root_hwnd)
            if cursor_hwnd:
                self._keyboard_target_hwnd = int(cursor_hwnd)
                return int(cursor_hwnd)

            hwnd = int(self._keyboard_target_hwnd or 0)
            if hwnd and win32gui.IsWindow(hwnd) and (hwnd == root_hwnd or user32.IsChild(root_hwnd, hwnd)):
                return hwnd

            return root_hwnd
        except Exception:
            pass
        return 0

    def _thread_focus_hwnd(self, thread_id, root_hwnd):
        try:
            import ctypes
            import win32gui

            class GUITHREADINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.c_ulong),
                    ("flags", ctypes.c_ulong),
                    ("hwndActive", ctypes.c_void_p),
                    ("hwndFocus", ctypes.c_void_p),
                    ("hwndCapture", ctypes.c_void_p),
                    ("hwndMenuOwner", ctypes.c_void_p),
                    ("hwndMoveSize", ctypes.c_void_p),
                    ("hwndCaret", ctypes.c_void_p),
                    ("rcCaret", ctypes.c_long * 4),
                ]

            info = GUITHREADINFO()
            info.cbSize = ctypes.sizeof(GUITHREADINFO)
            if not ctypes.windll.user32.GetGUIThreadInfo(int(thread_id), ctypes.byref(info)):
                return 0
            for hwnd in (info.hwndFocus, info.hwndCaret, info.hwndActive):
                hwnd = int(hwnd or 0)
                if hwnd and win32gui.IsWindow(hwnd):
                    if hwnd == root_hwnd or ctypes.windll.user32.IsChild(int(root_hwnd), hwnd):
                        return hwnd
        except Exception:
            pass
        return 0

    def _browser_hwnd_at_cursor(self, root_hwnd=None):
        try:
            import ctypes
            import win32gui

            user32 = ctypes.windll.user32
            root_hwnd = int(root_hwnd or getattr(self.worker, "_embedded_hwnd", 0) or 0)
            if not (root_hwnd and win32gui.IsWindow(root_hwnd)):
                return 0
            cursor_pos = QCursor.pos()
            hwnd = win32gui.WindowFromPoint((cursor_pos.x(), cursor_pos.y()))
            if hwnd and (hwnd == root_hwnd or user32.IsChild(root_hwnd, hwnd)):
                return int(hwnd)
        except Exception:
            pass
        return 0

    def _qt_key_to_vk(self, key):
        try:
            import win32con
        except Exception:
            return 0

        special = {
            Qt.Key_Backspace: win32con.VK_BACK,
            Qt.Key_Tab: win32con.VK_TAB,
            Qt.Key_Return: win32con.VK_RETURN,
            Qt.Key_Enter: win32con.VK_RETURN,
            Qt.Key_Escape: win32con.VK_ESCAPE,
            Qt.Key_Delete: win32con.VK_DELETE,
            Qt.Key_Insert: win32con.VK_INSERT,
            Qt.Key_Home: win32con.VK_HOME,
            Qt.Key_End: win32con.VK_END,
            Qt.Key_PageUp: win32con.VK_PRIOR,
            Qt.Key_PageDown: win32con.VK_NEXT,
            Qt.Key_Left: win32con.VK_LEFT,
            Qt.Key_Up: win32con.VK_UP,
            Qt.Key_Right: win32con.VK_RIGHT,
            Qt.Key_Down: win32con.VK_DOWN,
            Qt.Key_Space: win32con.VK_SPACE,
        }
        if key in special:
            return special[key]
        if Qt.Key_A <= key <= Qt.Key_Z:
            return ord("A") + int(key - Qt.Key_A)
        if Qt.Key_0 <= key <= Qt.Key_9:
            return ord("0") + int(key - Qt.Key_0)
        if Qt.Key_F1 <= key <= Qt.Key_F12:
            return win32con.VK_F1 + int(key - Qt.Key_F1)
        return 0

    def _post_browser_key(self, hwnd, message, vk, flags=0):
        try:
            import ctypes

            ctypes.windll.user32.PostMessageW(int(hwnd), int(message), int(vk), int(flags))
            return True
        except Exception:
            return False

    def _send_input_unicode(self, text):
        if not text:
            return False
        try:
            import ctypes

            ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

            class KEYBDINPUT(ctypes.Structure):
                _fields_ = [
                    ("wVk", ctypes.c_ushort),
                    ("wScan", ctypes.c_ushort),
                    ("dwFlags", ctypes.c_ulong),
                    ("time", ctypes.c_ulong),
                    ("dwExtraInfo", ULONG_PTR),
                ]

            class INPUTUNION(ctypes.Union):
                _fields_ = [("ki", KEYBDINPUT)]

            class INPUT(ctypes.Structure):
                _fields_ = [("type", ctypes.c_ulong), ("union", INPUTUNION)]

            INPUT_KEYBOARD = 1
            KEYEVENTF_KEYUP = 0x0002
            KEYEVENTF_UNICODE = 0x0004
            inputs = []
            for ch in text:
                code = ord(ch)
                inputs.append(INPUT(INPUT_KEYBOARD, INPUTUNION(KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, 0))))
                inputs.append(INPUT(INPUT_KEYBOARD, INPUTUNION(KEYBDINPUT(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0))))
            array_type = INPUT * len(inputs)
            sent = ctypes.windll.user32.SendInput(len(inputs), array_type(*inputs), ctypes.sizeof(INPUT))
            return int(sent) == len(inputs)
        except Exception:
            return False

    def _send_input_vk(self, vk, key_up=False):
        if not vk:
            return False
        try:
            import ctypes

            ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

            class KEYBDINPUT(ctypes.Structure):
                _fields_ = [
                    ("wVk", ctypes.c_ushort),
                    ("wScan", ctypes.c_ushort),
                    ("dwFlags", ctypes.c_ulong),
                    ("time", ctypes.c_ulong),
                    ("dwExtraInfo", ULONG_PTR),
                ]

            class INPUTUNION(ctypes.Union):
                _fields_ = [("ki", KEYBDINPUT)]

            class INPUT(ctypes.Structure):
                _fields_ = [("type", ctypes.c_ulong), ("union", INPUTUNION)]

            INPUT_KEYBOARD = 1
            KEYEVENTF_KEYUP = 0x0002
            flags = KEYEVENTF_KEYUP if key_up else 0
            entry = INPUT(INPUT_KEYBOARD, INPUTUNION(KEYBDINPUT(int(vk), 0, flags, 0, 0)))
            sent = ctypes.windll.user32.SendInput(1, ctypes.byref(entry), ctypes.sizeof(INPUT))
            return int(sent) == 1
        except Exception:
            return False

    def _forward_key_to_browser(self, event):
        hwnd = self._browser_keyboard_hwnd()
        if not hwnd:
            return False

        try:
            import win32con
        except Exception:
            return False

        self._focus_browser()
        hwnd = self._browser_keyboard_hwnd() or hwnd

        key = int(event.key())
        modifiers = event.modifiers()
        is_press = event.type() == QEvent.KeyPress
        vk = self._qt_key_to_vk(key)

        if is_press:
            text = event.text() or ""
            plain_text = text and not (modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))
            if plain_text and key not in (Qt.Key_Backspace, Qt.Key_Tab, Qt.Key_Return, Qt.Key_Enter, Qt.Key_Escape):
                if self._send_input_unicode(text):
                    return True
                sent = False
                for ch in text:
                    sent = self._post_browser_key(hwnd, win32con.WM_CHAR, ord(ch), 1) or sent
                return sent

        if not vk:
            return False

        if is_press:
            modifier_vks = []
            if modifiers & Qt.ControlModifier:
                modifier_vks.append(win32con.VK_CONTROL)
            if modifiers & Qt.ShiftModifier:
                modifier_vks.append(win32con.VK_SHIFT)
            if modifiers & Qt.AltModifier:
                modifier_vks.append(win32con.VK_MENU)
            for mod_vk in modifier_vks:
                if not self._send_input_vk(mod_vk, key_up=False):
                    self._post_browser_key(hwnd, win32con.WM_KEYDOWN, mod_vk, 1)
            ok = self._send_input_vk(vk, key_up=False) or self._post_browser_key(hwnd, win32con.WM_KEYDOWN, vk, 1)
            for mod_vk in reversed(modifier_vks):
                if not self._send_input_vk(mod_vk, key_up=True):
                    self._post_browser_key(hwnd, win32con.WM_KEYUP, mod_vk, 0xC0000001)
            return ok

        return self._send_input_vk(vk, key_up=True) or self._post_browser_key(hwnd, win32con.WM_KEYUP, vk, 0xC0000001)

    def _focus_browser_if_mouse_down(self):
        if not (self.worker and getattr(self.worker, "_embedded_hwnd", 0)):
            return
        if not self.browser_container.isVisible():
            return
        try:
            import ctypes
            import time
            import win32gui

            user32 = ctypes.windll.user32
            VK_LBUTTON = 0x01
            button_state = user32.GetAsyncKeyState(VK_LBUTTON)
            if not ((button_state & 0x8000) or (button_state & 0x0001)):
                return

            now = time.time()
            if now - self._last_browser_focus_ts < 0.25:
                return

            cursor_pos = QCursor.pos()
            root_hwnd = getattr(self.worker, "_embedded_hwnd", 0)
            hwnd_at_cursor = win32gui.WindowFromPoint((cursor_pos.x(), cursor_pos.y()))
            if not (hwnd_at_cursor == root_hwnd or user32.IsChild(root_hwnd, hwnd_at_cursor)):
                return

            self._last_browser_focus_ts = now
            self._set_browser_keyboard_active(True)
            self._focus_browser()
        except Exception:
            pass

    def _focus_browser(self):
        """Chuyển keyboard focus sang cửa sổ Chrome đã nhúng.
        Dùng AttachThreadInput để chuyển focus xuyên thread (Win32 API)."""
        if self.worker and hasattr(self.worker, '_embedded_hwnd') and self.worker._embedded_hwnd:
            try:
                import ctypes
                import win32gui
                import win32process

                root_hwnd = self.worker._embedded_hwnd
                if not win32gui.IsWindow(root_hwnd):
                    return

                user32 = ctypes.windll.user32
                focus_hwnd = root_hwnd
                try:
                    cursor_hwnd = self._browser_hwnd_at_cursor(root_hwnd)
                    if cursor_hwnd:
                        focus_hwnd = cursor_hwnd

                    class POINT(ctypes.Structure):
                        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

                    pt = POINT()
                    if focus_hwnd == root_hwnd and user32.GetCursorPos(ctypes.byref(pt)):
                        hwnd_at_cursor = win32gui.WindowFromPoint((int(pt.x), int(pt.y)))
                        if hwnd_at_cursor and (
                            hwnd_at_cursor == root_hwnd or user32.IsChild(root_hwnd, hwnd_at_cursor)
                        ):
                            focus_hwnd = hwnd_at_cursor
                except Exception:
                    focus_hwnd = root_hwnd
                try:
                    focused = self._thread_focus_hwnd(
                        win32process.GetWindowThreadProcessId(focus_hwnd)[0],
                        root_hwnd,
                    )
                    if focused:
                        focus_hwnd = focused
                except Exception:
                    pass
                self._keyboard_target_hwnd = int(focus_hwnd or root_hwnd)

                # Lấy thread ID của cửa sổ Chrome và thread hiện tại
                chrome_tid = win32process.GetWindowThreadProcessId(focus_hwnd)[0]
                my_tid = user32.GetCurrentThreadId()
                foreground_hwnd = user32.GetForegroundWindow()
                foreground_tid = 0
                if foreground_hwnd:
                    foreground_tid = user32.GetWindowThreadProcessId(foreground_hwnd, None)

                # Attach thread input để cho phép SetFocus xuyên thread
                attached_chrome = False
                attached_foreground = False
                try:
                    if chrome_tid != my_tid:
                        attached_chrome = bool(user32.AttachThreadInput(my_tid, chrome_tid, True))
                    if foreground_tid and foreground_tid not in (my_tid, chrome_tid):
                        attached_foreground = bool(user32.AttachThreadInput(my_tid, foreground_tid, True))

                    try:
                        user32.BringWindowToTop(root_hwnd)
                        user32.SetActiveWindow(root_hwnd)
                        user32.SetFocus(focus_hwnd)
                    except Exception:
                        try:
                            user32.SetForegroundWindow(root_hwnd)
                            user32.SetActiveWindow(root_hwnd)
                            user32.SetFocus(root_hwnd)
                        except Exception:
                            pass
                finally:
                    if attached_foreground:
                        user32.AttachThreadInput(my_tid, foreground_tid, False)
                    if attached_chrome:
                        user32.AttachThreadInput(my_tid, chrome_tid, False)

            except Exception:
                pass


