import sys

with open('automation_dashboard.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_content = """                    if gologin_proxy_synced:
                        pdata["gologin_proxy_synced"] = gologin_proxy_synced
                    name_item.setData(Qt.UserRole, pdata)

        # 4. Lưu file JSON (KHÔNG reload bảng → tránh trùng)
        if parent and hasattr(parent, "save_accounts_to_db"):
            parent.save_accounts_to_db()

    def _on_preview_status_updated(self, account_row, msg, color):
        \"\"\"Cập nhật trạng thái vào cột 5 (Trạng thái) của bảng theo dõi.\"\"\"
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
    \"\"\"Widget hiển thị live preview browser qua CDP Screencast.\"\"\"
    data_updated = pyqtSignal(int, dict)
    status_updated = pyqtSignal(int, str, str)
    
    def __init__(self, profile_name, tiktok_id, profile_data=None, selected_features=None, feed_settings=None, profile_index=0, account_row=0, embed_browser=True, parent=None):
        super().__init__(parent)
        self.profile_name = profile_name
        self.tiktok_id = tiktok_id
        self.profile_data = profile_data or {}
        self.selected_features = selected_features or []
        self.feed_settings = feed_settings or {}
        self.profile_index = profile_index
        self.account_row = account_row
        self.embed_browser = embed_browser
        self.worker = None

        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(\"\"\"
            BrowserPreviewWidget {
                background: #ffffff; border: none;
            }
        \"\"\")
        # Kích thước = container(960x680) + header(32) + status(24)
        self.setFixedSize(960, 736)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header bar (giữ nguyên)
        header = QWidget()
        header.setFixedHeight(32)
        header.setStyleSheet(\"\"\"
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #e8eaed, stop:1 #f0f2f5);
            border-top-left-radius: 6px; border-top-right-radius: 6px;
        \"\"\")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(8, 0, 8, 0)

        self.status_dot = QLabel("\\u25cf")
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

        btn_fp = QPushButton("\\U0001f511")
        btn_fp.setFixedSize(24, 24)
        btn_fp.setToolTip("Đổi Fingerprint")
        btn_fp.setStyleSheet("background: transparent; border: none; font-size: 13px;")
        h_layout.addWidget(btn_fp)
        layout.addWidget(header)

        # === BROWSER CONTAINER (Native Window Embedding — 60 FPS như SSMATool) ===
        self.browser_container = QWidget()
        self.browser_container.setFixedSize(960, 680)
        self.browser_container.setStyleSheet("background: #1a1a2e;")
        self.browser_container.setAttribute(Qt.WA_NativeWindow, True)
        # ★ KHÔNG set WA_TransparentForMouseEvents vì Chrome WS_CHILD tự nhận click
        # Chỉ cần forward keyboard focus khi user click vào container
        self.browser_container.setFocusPolicy(Qt.ClickFocus)
        self.browser_container.mousePressEvent = self._on_container_click
        layout.addWidget(self.browser_container, stretch=1)

        # Status bar (giữ nguyên)
        self.lbl_status = QLabel("\\u23f3 Chờ chạy...")
        self.lbl_status.setFixedHeight(24)
        self.lbl_status.setStyleSheet(\"\"\"
            color: #6b7280; padding: 0 8px; font-size: 11px;
            background: #f9fafb; border-top: 1px solid #e4e7ec;
            border-bottom-left-radius: 6px; border-bottom-right-radius: 6px;
        \"\"\")
        layout.addWidget(self.lbl_status)

    def update_status(self, text, color="gray"):
        color_map = {
            "blue": "#3b82f6", "green": "#16a34a", "red": "#ef4444",
            "orange": "#f59e0b", "gray": "#6b7280"
        }
        qt_color = color_map.get(color, color)

        dot_map = {"green": "#16a34a", "red": "#ef4444", "blue": "#3b82f6"}
        self.status_dot.setStyleSheet(f"color: {dot_map.get(color, '#9ca3af')}; font-size: 10px;")

        self.lbl_status.setText(text)
        self.lbl_status.setStyleSheet(f\"\"\"
            color: {qt_color}; padding: 0 8px; font-size: 11px;
            background: #f9fafb; border-top: 1px solid #e4e7ec;
            border-bottom-left-radius: 6px; border-bottom-right-radius: 6px;
        \"\"\")

    def start_automation(self):
        \"\"\"Khởi động CDP Worker + Native Window Embedding (60 FPS).\"\"\"
        from PyQt5.QtWidgets import QApplication
        QApplication.processEvents()

        container_w = self.browser_container.width()
        container_h = self.browser_container.height()
        if container_w < 100: container_w = 840
        if container_h < 100: container_h = 600

        # Lấy HWND của container để nhúng browser vào nếu chk_browser checked
        widget_id = int(self.browser_container.winId()) if self.embed_browser else 0

        from cdp_worker import CDPWorker
        self.worker = CDPWorker(
            profile_index=self.profile_index,
            profile_data=self.profile_data,
            selected_features=self.selected_features,
            feed_settings=self.feed_settings,
            container_width=container_w,
            container_height=container_h,
            widget_id=widget_id,
        )
        self.worker.status_update.connect(self._on_status)
        self.worker.finished_signal.connect(self._on_finished)
        self.worker.profile_update_signal.connect(self._on_profile_update)
        self.worker.start()

    def open_browser_only(self):
        \"\"\"Mở browser nhưng KHÔNG chạy task tự động (chế độ Manual).\"\"\"
        from PyQt5.QtWidgets import QApplication
        QApplication.processEvents()

        container_w = self.browser_container.width()
        container_h = self.browser_container.height()
        if container_w < 100: container_w = 840
        if container_h < 100: container_h = 600

        widget_id = int(self.browser_container.winId()) if self.embed_browser else 0

        from cdp_worker import CDPWorker
        self.worker = CDPWorker(
            profile_index=self.profile_index,
            profile_data=self.profile_data,
            selected_features=[],  # KHÔNG chạy automation
            feed_settings={},
            container_width=container_w,
            container_height=container_h,
            widget_id=widget_id,
            manual_only=True,
        )
"""

# Replace from 'if gologin_proxy_synced:' to the 'self.worker.status_update.connect(self._on_status)' line
start_idx = -1
for i, line in enumerate(lines):
    if '                    if gologin_proxy_synced:' in line:
        start_idx = i
        break

end_idx = -1
for i, line in enumerate(lines):
    if '        self.worker.status_update.connect(self._on_status)' in line:
        end_idx = i
        break

if start_idx != -1 and end_idx != -1:
    new_lines = lines[:start_idx] + [new_content] + lines[end_idx:]
    with open('automation_dashboard.py', 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    print("Fixed syntax error")
else:
    print(f"Could not find bounds: {start_idx}, {end_idx}")
