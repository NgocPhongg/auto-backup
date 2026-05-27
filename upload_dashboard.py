from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSpinBox, QCheckBox,
    QPushButton, QSplitter, QScrollArea, QWidget, QGridLayout, 
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit, QMessageBox
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor

from upload_worker import UploadWorker
from automation_dashboard import BrowserPreviewWidget

PREVIEW_WIDTH = 960
PREVIEW_HEIGHT = 680

class UploadDashboard(QDialog):
    def __init__(self, parent=None, video_tasks=None):
        super().__init__(parent)
        self.setWindowTitle("Màn hình theo dõi Upload Video")
        self.resize(1600, 900)
        self.video_tasks = video_tasks or []
        self._worker = None
        self._preview_containers = {}
        self._preview_widgets = {}
        self._task_widgets = []  # Lưu reference để update trạng thái
        self._closing_after_worker_stop = False
        
        self.init_ui()
        self.populate_tasks()
        
    def init_ui(self):
        self.setStyleSheet("""
            QDialog { background-color: #f5f6fa; }
            QLabel { color: #2f3640; }
            QPushButton {
                padding: 5px 10px;
                border: 1px solid #dcdde1;
                border-radius: 3px;
                background-color: white;
            }
            QPushButton:hover { background-color: #f1f2f6; }
            QSplitter::handle { background-color: #dcdde1; width: 2px; }
            QScrollArea { border: none; background-color: transparent; }
        """)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        # --- Top Header ---
        top_layout = QHBoxLayout()
        lbl_title = QLabel("<h2>Dự án: Tổng tài khoản</h2>")
        top_layout.addWidget(lbl_title)
        
        btn_render = QPushButton("Kết xuất")
        btn_aff = QPushButton("Chế độ AFF (Gắn aff)")
        top_layout.addWidget(btn_render)
        top_layout.addWidget(btn_aff)
        
        top_layout.addStretch()
        
        lbl_monitor = QLabel("<h2>Màn hình theo dõi</h2>")
        top_layout.addWidget(lbl_monitor)
        
        search_box = QLineEdit()
        search_box.setPlaceholderText("tìm màn hình theo tên hồ sơ | id tiktok")
        search_box.setFixedWidth(300)
        top_layout.addWidget(search_box)
        
        lbl_pro = QLabel("(pro đang mở)")
        top_layout.addWidget(lbl_pro)
        
        top_layout.addStretch()
        
        chk_browser = QCheckBox("Đưa vào màn hình")
        chk_browser.setChecked(True)
        btn_close = QPushButton("Đóng")
        btn_close.clicked.connect(self.close)
        
        top_layout.addWidget(QLabel("Upload bang GoLogin/Orbita:"))
        top_layout.addWidget(chk_browser)
        top_layout.addWidget(btn_close)
        
        main_layout.addLayout(top_layout)
        
        # --- Splitter ---
        splitter = QSplitter(Qt.Horizontal)
        
        # Left Panel (Task List & Controls)
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        
        # Task scroll area
        self.task_scroll = QScrollArea()
        self.task_scroll.setWidgetResizable(True)
        self.task_container = QWidget()
        self.task_layout = QVBoxLayout(self.task_container)
        self.task_layout.setAlignment(Qt.AlignTop)
        self.task_scroll.setWidget(self.task_container)
        
        left_layout.addWidget(self.task_scroll, 1)
        
        # Bottom Left Controls
        bottom_left = QWidget()
        bottom_left_layout = QVBoxLayout(bottom_left)
        bottom_left_layout.setContentsMargins(0, 10, 0, 0)
        
        chk_time = QCheckBox("Đến giờ đã set thì trình duyệt mở và tự đăng CÔNG KHAI")
        chk_time.setChecked(True)
        
        self.chk_delete_video = QCheckBox("Đăng video thành công thì XÓA VIDEO trong hàng chờ và FILE VIDEO")
        self.chk_delete_video.setChecked(True)
        self.chk_delete_video.setStyleSheet("color: #16a34a; font-weight: bold;")
        
        bottom_left_layout.addWidget(chk_time)
        bottom_left_layout.addWidget(self.chk_delete_video)
        
        ctrl_row = QHBoxLayout()
        self.btn_run = QPushButton("▶ Chạy")
        self.btn_run.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold;")
        self.btn_run.clicked.connect(self.on_run)
        
        self.btn_stop = QPushButton("⏹ Dừng")
        self.btn_stop.setStyleSheet("background-color: #c0392b; color: white; font-weight: bold;")
        self.btn_stop.clicked.connect(self.on_stop)
        self.btn_stop.setEnabled(False)
        
        ctrl_row.addWidget(self.btn_run)
        ctrl_row.addWidget(self.btn_stop)
        
        ctrl_row.addStretch()
        ctrl_row.addWidget(QLabel("Luồng"))
        self.spin_thread = QSpinBox()
        self.spin_thread.setValue(1)
        ctrl_row.addWidget(self.spin_thread)
        ctrl_row.addWidget(QLabel("Do tre"))
        self.spin_delay = QSpinBox()
        self.spin_delay.setRange(1, 60)
        self.spin_delay.setValue(5)
        ctrl_row.addWidget(self.spin_delay)
        
        bottom_left_layout.addLayout(ctrl_row)
        
        # Legend
        legend_row = QHBoxLayout()
        colors = [
            ("#3498db", "Uploading"),
            ("#2ecc71", "Uploaded"),
            ("#e74c3c", "Upload lỗi"),
            ("#f1c40f", "?ang k?t xu?t"),
            ("#e67e22", "?? k?t xu?t"),
            ("#9b59b6", "Render lỗi")
        ]
        for color, text in colors:
            lbl_color = QLabel("●")
            lbl_color.setStyleSheet(f"color: {color}; font-size: 16px;")
            legend_row.addWidget(lbl_color)
            legend_row.addWidget(QLabel(text))
        legend_row.addStretch()
        bottom_left_layout.addLayout(legend_row)
        
        left_layout.addWidget(bottom_left)
        splitter.addWidget(left_widget)
        
        # Right Panel (Log)
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(5, 5, 5, 5)
        
        right_layout.addWidget(QLabel("<b>📋 Log chi tiết</b>"))
        self.log_scroll = QScrollArea()
        self.log_scroll.setWidgetResizable(True)
        self.log_container = QWidget()
        self.log_layout = QVBoxLayout(self.log_container)
        self.log_layout.setAlignment(Qt.AlignTop)
        self.log_scroll.setWidget(self.log_container)
        right_layout.addWidget(self.log_scroll, 1)
        
        splitter.addWidget(right_widget)
        splitter.setSizes([1050, 550])
        main_layout.addWidget(splitter)
        
    def _populate_tasks_legacy_unused(self):
        for i in reversed(range(self.task_layout.count())): 
            widget = self.task_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)
        self._task_widgets = []
        self._preview_containers = {}
                
        if not self.video_tasks:
            self.task_layout.addWidget(QLabel("Không có video nào trong hàng chờ."))
            return
            
        for idx, task in enumerate(self.video_tasks):
            task_widget = QFrame()
            task_widget.setFrameShape(QFrame.StyledPanel)
            task_widget.setStyleSheet("background-color: white; border-radius: 5px; margin-bottom: 3px; border: 1px solid #dcdde1;")
            t_layout = QVBoxLayout(task_widget)
            t_layout.setContentsMargins(8, 4, 8, 4)
            
            h_layout = QHBoxLayout()
            lbl_name = QLabel(f"<b>{task.get('upload_to', 'Unknown')}</b>")
            h_layout.addWidget(lbl_name)
            h_layout.addStretch()
            
            lbl_status = QLabel("⏳ Chờ")
            lbl_status.setObjectName("status_label")
            h_layout.addWidget(lbl_status)
            t_layout.addLayout(h_layout)
            
            i_layout = QHBoxLayout()
            lbl_time = QLabel(task.get('schedule_time', 'Public'))
            lbl_time.setStyleSheet("background-color: #3498db; color: white; padding: 2px 6px; border-radius: 3px; font-size: 11px;")
            i_layout.addWidget(lbl_time)
            
            lbl_title = QLabel(task.get('title', '')[:50])
            lbl_title.setStyleSheet("background-color: #f1c40f; color: black; padding: 2px 6px; border-radius: 3px; font-size: 11px;")
            i_layout.addWidget(lbl_title)
            i_layout.addStretch()
            t_layout.addLayout(i_layout)

            browser_container = QWidget()
            browser_container.setObjectName("browser_container")
            browser_container.setFixedSize(PREVIEW_WIDTH, PREVIEW_HEIGHT)
            browser_container.setAttribute(Qt.WA_NativeWindow, True)
            browser_container.setFocusPolicy(Qt.ClickFocus)
            browser_container.setStyleSheet("background: #111827; border: 1px solid #1f2937;")
            browser_container.mousePressEvent = lambda event, task_idx=idx: self._focus_browser(task_idx)
            t_layout.addWidget(browser_container)
            self._preview_containers[idx] = browser_container
            
            self.task_layout.addWidget(task_widget)
            self._task_widgets.append(task_widget)

    def populate_tasks(self):
        for i in reversed(range(self.task_layout.count())):
            widget = self.task_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)
        self._task_widgets = [None] * len(self.video_tasks)
        self._preview_containers = {}
        self._preview_widgets = {}

        if not self.video_tasks:
            self.task_layout.addWidget(QLabel("Khong co video nao trong hang cho."))
            return

        account_groups = {}
        for idx, task in enumerate(self.video_tasks):
            account_name = task.get('upload_to') or 'Unknown'
            account_groups.setdefault(account_name, []).append((idx, task))

        for account_name, items in account_groups.items():
            account_widget = QFrame()
            account_widget.setFrameShape(QFrame.StyledPanel)
            account_widget.setStyleSheet("background-color: white; border-radius: 5px; margin-bottom: 3px; border: 1px solid #dcdde1;")
            account_layout = QVBoxLayout(account_widget)
            account_layout.setContentsMargins(8, 4, 8, 4)

            header_layout = QHBoxLayout()
            lbl_name = QLabel(f"<b>{account_name}</b>")
            header_layout.addWidget(lbl_name)
            header_layout.addStretch()
            lbl_count = QLabel(f"{len(items)} video")
            lbl_count.setStyleSheet("color: #6b7280; font-size: 11px;")
            header_layout.addWidget(lbl_count)
            account_layout.addLayout(header_layout)

            for idx, task in items:
                row_layout = QHBoxLayout()

                lbl_index = QLabel(f"Video {idx + 1}")
                lbl_index.setStyleSheet("color: #374151; font-size: 11px; font-weight: bold;")
                row_layout.addWidget(lbl_index)

                lbl_time = QLabel(task.get('schedule_time', 'Public'))
                lbl_time.setStyleSheet("background-color: #3498db; color: white; padding: 2px 6px; border-radius: 3px; font-size: 11px;")
                row_layout.addWidget(lbl_time)

                lbl_title = QLabel(task.get('title', '')[:50])
                lbl_title.setStyleSheet("background-color: #f1c40f; color: black; padding: 2px 6px; border-radius: 3px; font-size: 11px;")
                row_layout.addWidget(lbl_title)
                row_layout.addStretch()

                lbl_status = QLabel("Cho")
                lbl_status.setObjectName("status_label")
                row_layout.addWidget(lbl_status)

                row_widget = QWidget()
                row_widget.setLayout(row_layout)
                row_widget.setStyleSheet("background: transparent; border: none;")
                account_layout.addWidget(row_widget)
                self._task_widgets[idx] = row_widget

            task_indices = [idx for idx, _task in items]
            first_idx, first_task = items[0]
            profile_data = {
                "ten_ho_so": account_name,
                "gologin_profile_id": first_task.get("gologin_profile_id") or first_task.get("browser_id") or "",
                "browser_id": first_task.get("browser_id") or first_task.get("gologin_profile_id") or "",
            }
            preview = BrowserPreviewWidget(
                profile_name=account_name,
                tiktok_id=str(first_task.get("tiktok_id") or ""),
                profile_data=profile_data,
                selected_features=[],
                feed_settings={},
                profile_index=first_idx,
                account_row=first_idx,
                embed_browser=True,
                preview_width=PREVIEW_WIDTH,
                browser_height=PREVIEW_HEIGHT,
                parent=self,
            )
            preview.embed_finished.connect(
                lambda success, hwnd, pid, message, task_index=first_idx:
                    self._on_preview_embed_finished(task_index, success, hwnd, pid, message)
            )
            account_layout.addWidget(preview)

            for idx, _task in items:
                self._preview_widgets[idx] = preview
                self._preview_containers[idx] = preview.browser_container

            self.task_layout.addWidget(account_widget)


    def _on_preview_embed_finished(self, task_index, success, hwnd, pid, message):
        worker = self._worker
        if worker and hasattr(worker, "notify_embed_result"):
            worker.notify_embed_result(success, hwnd, pid, message, task_index=task_index)


    def _add_log(self, msg, color="black"):
        lbl = QLabel(msg)
        lbl.setStyleSheet(f"color: {color}; font-size: 11px; padding: 1px;")
        lbl.setWordWrap(True)
        self.log_layout.addWidget(lbl)
        # Auto scroll xuống
        self.log_scroll.verticalScrollBar().setValue(self.log_scroll.verticalScrollBar().maximum())

    # ═══ Signals ═══
    def on_run(self):
        if not self.video_tasks:
            QMessageBox.warning(self, "Cảnh báo", "Không có video nào!")
            return
            
        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self._add_log("▶ Bắt đầu upload...", "green")
        
        settings = {
            "delete_on_success": self.chk_delete_video.isChecked(),
            "max_threads": self.spin_thread.value(),
            "delay_min": self.spin_delay.value(),
            "delay_max": self.spin_delay.value() + 5,
        }

        from PyQt5.QtWidgets import QApplication
        QApplication.processEvents()
        preview_targets = {}
        for idx, container in self._preview_containers.items():
            preview_targets[idx] = {
                "widget_id": int(container.winId()),
                "width": container.width(),
                "height": container.height(),
            }
        
        self._worker = UploadWorker(self.video_tasks, settings, preview_targets=preview_targets)
        self._worker.status_updated.connect(self._on_status)
        self._worker.browser_ready_signal.connect(self._on_browser_ready)
        self._worker.task_completed.connect(self._on_task_done)
        self._worker.all_done.connect(self._on_all_done)
        self._worker.start()


    def _on_browser_ready(self, info):
        info = dict(info or {})
        idx = int(info.get("task_index") or 0)
        preview = self._preview_widgets.get(idx)
        worker = self._worker
        if not preview or not worker:
            if worker and hasattr(worker, "notify_embed_result"):
                worker.notify_embed_result(False, 0, 0, task_index=idx)
            return

        preview.worker = worker
        preview._on_browser_ready(info)

    def on_stop(self):
        if self._worker:
            self._worker.stop()
            self._add_log("⏹ Đã gửi lệnh dừng...", "orange")

    def _on_status(self, idx, msg, color):
        self._add_log(f"[Video {idx+1}] {msg}", color)
        # Update trạng thái trên task card
        if 0 <= idx < len(self._task_widgets):
            widget = self._task_widgets[idx]
            lbl = widget.findChild(QLabel, "status_label")
            if lbl:
                lbl.setText(msg[:40])
                lbl.setStyleSheet(f"color: {color};")

    def _on_task_done(self, idx, success, detail):
        color = "#2ecc71" if success else "#e74c3c"
        status = "✅ Thành công" if success else f"❌ {detail[:30]}"
        self._add_log(f"[Video {idx+1}] {status}", color)
        
        if 0 <= idx < len(self._task_widgets):
            border_color = "#2ecc71" if success else "#e74c3c"
            self._task_widgets[idx].setStyleSheet(
                f"background-color: white; border-radius: 5px; margin-bottom: 3px; border: 2px solid {border_color};"
            )

    def _on_all_done(self):
        self._add_log("Hoan tat tat ca video!", "green")
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._worker = None
        if self._closing_after_worker_stop:
            self._closing_after_worker_stop = False
            QTimer.singleShot(0, self.close)

    def _focus_browser_group(self, indices):
        for idx in reversed(indices):
            hwnd = getattr(self._worker, "_embedded_hwnds", {}).get(idx) if self._worker else None
            if not hwnd:
                continue
            try:
                import win32gui
                if win32gui.IsWindow(hwnd):
                    self._focus_browser(idx)
                    return
            except Exception:
                continue

    def _focus_browser(self, idx):
        if not self._worker:
            return
        hwnd = getattr(self._worker, "_embedded_hwnds", {}).get(idx)
        if not hwnd:
            return
        try:
            import ctypes
            import win32gui
            import win32process

            if not win32gui.IsWindow(hwnd):
                return

            user32 = ctypes.windll.user32
            chrome_tid = win32process.GetWindowThreadProcessId(hwnd)[0]
            my_tid = user32.GetCurrentThreadId()
            if chrome_tid != my_tid:
                user32.AttachThreadInput(my_tid, chrome_tid, True)
            try:
                win32gui.SetFocus(hwnd)
            finally:
                if chrome_tid != my_tid:
                    user32.AttachThreadInput(my_tid, chrome_tid, False)
        except Exception:
            pass

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            event.ignore()
            if not self._closing_after_worker_stop:
                self._closing_after_worker_stop = True
                self.btn_stop.setEnabled(False)
                self.btn_run.setEnabled(False)
                self._add_log("Dang dung upload, vui long cho trinh duyet dong xong...", "orange")
                self._worker.stop()
            return
        super().closeEvent(event)
