from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QGroupBox, QCheckBox, 
    QLabel, QSpinBox, QDateTimeEdit, QDateEdit, QRadioButton, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox, QAbstractItemView
)
from PyQt5.QtCore import Qt, QDateTime, QDate

def round_qdatetime_to_tiktok_minutes(dt):
    """TikTok Studio only exposes minute values in 5-minute steps."""
    remainder = dt.time().minute() % 5
    if not remainder:
        return dt
    rounded = dt.addSecs((5 - remainder) * 60)
    rounded.setTime(rounded.time().addSecs(-rounded.time().second()))
    return rounded

class SchedulerDialog(QDialog):
    def __init__(self, parent=None, video_data=None):
        super().__init__(parent)
        self.setWindowTitle("Trình lên lịch hàng loạt")
        self.resize(900, 600)
        
        # Lưu dữ liệu truyền vào từ bảng chính
        # video_data là list of dict: [{'title': x, 'duration': y, 'upload_to': z}, ...]
        self.video_data = video_data or []
        
        self.init_ui()
        self.populate_table()
        
    def init_ui(self):
        main_layout = QVBoxLayout(self)
        
        # --- Phần trên: Cấu hình lên lịch ---
        top_layout = QHBoxLayout()
        
        # Group 1: Cách đoạn
        group_interval = QGroupBox()
        layout_interval = QVBoxLayout(group_interval)
        self.chk_interval = QCheckBox("Cách đoạn")
        layout_interval.addWidget(self.chk_interval)
        layout_interval.addWidget(QLabel("Lên lịch hàng loạt cách nhau theo phút:"))
        
        row_interval = QHBoxLayout()
        row_interval.addWidget(QLabel("Bắt đầu từ"))
        self.time_start = QDateTimeEdit(round_qdatetime_to_tiktok_minutes(QDateTime.currentDateTime()))
        self.time_start.setDisplayFormat("MM/dd/yyyy HH:mm")
        row_interval.addWidget(self.time_start)
        row_interval.addWidget(QLabel("cách nhau:"))
        self.spin_interval = QSpinBox()
        self.spin_interval.setRange(1, 1440)
        self.spin_interval.setValue(30)
        row_interval.addWidget(self.spin_interval)
        row_interval.addStretch()
        layout_interval.addLayout(row_interval)
        
        top_layout.addWidget(group_interval)
        
        # Group 2: Theo ngày
        group_daily = QGroupBox()
        layout_daily = QVBoxLayout(group_daily)
        self.chk_daily = QCheckBox("Theo ngày")
        layout_daily.addWidget(self.chk_daily)
        layout_daily.addWidget(QLabel("Lên lịch hàng loạt theo số lượng video cần đăng trong ngày: (Tối đa 14 video)"))
        
        row_daily = QHBoxLayout()
        row_daily.addWidget(QLabel("Số lượng video"))
        self.spin_daily_limit = QSpinBox()
        self.spin_daily_limit.setRange(1, 14)
        self.spin_daily_limit.setValue(5)
        row_daily.addWidget(self.spin_daily_limit)
        
        row_daily.addWidget(QLabel("Bắt đầu từ"))
        self.date_start = QDateEdit(QDate.currentDate())
        self.date_start.setDisplayFormat("MM/dd/yyyy")
        row_daily.addWidget(self.date_start)
        
        self.radio_next_day = QRadioButton("ngày tiếp")
        self.radio_same_day = QRadioButton("trong ngày")
        self.radio_next_day.setChecked(True)
        row_daily.addWidget(self.radio_next_day)
        row_daily.addWidget(self.radio_same_day)
        row_daily.addStretch()
        layout_daily.addLayout(row_daily)
        
        top_layout.addWidget(group_daily)
        
        main_layout.addLayout(top_layout)
        
        # --- Toolbar ---
        toolbar_layout = QHBoxLayout()
        btns = [
            ("Áp dụng vào bảng này", self.apply_to_table),
            ("Nhân bản lịch", None),
            ("Áp dụng nhưng theo phần Upload To", None),
            ("Đặt tất cả về công khai", self.set_all_public),
            ("Random", None),
            ("Chọn tất cả", self.select_all),
            ("Bỏ chọn tất cả", self.deselect_all)
        ]
        for text, func in btns:
            btn = QPushButton(text)
            if func:
                btn.clicked.connect(func)
            toolbar_layout.addWidget(btn)
        toolbar_layout.addStretch()
        main_layout.addLayout(toolbar_layout)
        
        # --- Bảng Video ---
        self.table = QTableWidget()
        headers = ["Run", "Tiêu đề", "Hiển thị", "Lên lịch", "Duration", "Upload To"]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        
        main_layout.addWidget(self.table)
        
        # --- Phần dưới: Nút áp dụng ---
        bottom_layout = QHBoxLayout()
        btn_apply_main = QPushButton("Áp dụng vào bảng chính")
        btn_apply_main.clicked.connect(self.accept)  # Đóng dialog và trả về code Accepted
        bottom_layout.addWidget(btn_apply_main)
        
        btn_set_public_main = QPushButton("Đặt tất cả trên bảng chính thành Công khai")
        bottom_layout.addWidget(btn_set_public_main)
        
        bottom_layout.addWidget(QLabel("Bạn cần áp dụng vào bảng này trước khi áp dụng vào bảng chính"))
        bottom_layout.addStretch()
        
        lbl_help = QLabel("<a href='#'>Hướng dẫn chi tiết</a>")
        lbl_help.setStyleSheet("color: red;")
        lbl_help.setOpenExternalLinks(False)
        bottom_layout.addWidget(lbl_help)
        
        main_layout.addLayout(bottom_layout)
        
    def populate_table(self):
        self.table.setRowCount(len(self.video_data))
        for i, data in enumerate(self.video_data):
            # Cột 0: Checkbox Run
            chk = QCheckBox()
            chk.setChecked(True)
            chk_widget = QWidget()
            chk_layout = QHBoxLayout(chk_widget)
            chk_layout.addWidget(chk)
            chk_layout.setAlignment(Qt.AlignCenter)
            chk_layout.setContentsMargins(0,0,0,0)
            self.table.setCellWidget(i, 0, chk_widget)
            
            # Cột 1: Tiêu đề
            self.table.setItem(i, 1, QTableWidgetItem(data.get("title", "")))
            
            # Cột 2: Hiển thị (Combobox Schedule / Public)
            combo = QComboBox()
            combo.addItems(["Schedule", "Public"])
            self.table.setCellWidget(i, 2, combo)
            
            # Cột 3: Lên lịch (DateTimeEdit)
            dt_edit = QDateTimeEdit(round_qdatetime_to_tiktok_minutes(QDateTime.currentDateTime()))
            dt_edit.setDisplayFormat("MM/dd/yyyy HH:mm")
            self.table.setCellWidget(i, 3, dt_edit)
            
            # Cột 4: Duration
            self.table.setItem(i, 4, QTableWidgetItem(data.get("duration", "")))
            
            # Cột 5: Upload To
            upload_to = f"[{data.get('upload_to', '')}]"
            item = QTableWidgetItem(upload_to)
            item.setBackground(Qt.green if upload_to != "[]" else Qt.white)
            self.table.setItem(i, 5, item)
            
    def apply_to_table(self):
        """Logic tính toán ngày giờ đưa vào cột Lên lịch dựa theo cấu hình"""
        if self.chk_interval.isChecked():
            start_dt = round_qdatetime_to_tiktok_minutes(self.time_start.dateTime())
            self.time_start.setDateTime(start_dt)
            interval = self.spin_interval.value()
            
            checked_count = 0
            for i in range(self.table.rowCount()):
                chk_widget = self.table.cellWidget(i, 0)
                chk = chk_widget.findChild(QCheckBox) if chk_widget else None
                if chk and chk.isChecked():
                    dt_edit = self.table.cellWidget(i, 3)
                    combo = self.table.cellWidget(i, 2)
                    if dt_edit and combo:
                        combo.setCurrentText("Schedule")
                        new_dt = round_qdatetime_to_tiktok_minutes(start_dt.addSecs(checked_count * interval * 60))
                        dt_edit.setDateTime(new_dt)
                    checked_count += 1

    def set_all_public(self):
        for i in range(self.table.rowCount()):
            combo = self.table.cellWidget(i, 2)
            if combo:
                combo.setCurrentText("Public")
                
    def select_all(self):
        for i in range(self.table.rowCount()):
            chk_widget = self.table.cellWidget(i, 0)
            if chk_widget:
                chk = chk_widget.findChild(QCheckBox)
                if chk: chk.setChecked(True)
                
    def deselect_all(self):
        for i in range(self.table.rowCount()):
            chk_widget = self.table.cellWidget(i, 0)
            if chk_widget:
                chk = chk_widget.findChild(QCheckBox)
                if chk: chk.setChecked(False)

    def get_scheduled_data(self):
        """Trả về dữ liệu đã lên lịch để cập nhật bảng chính"""
        results = []
        for i in range(self.table.rowCount()):
            chk_widget = self.table.cellWidget(i, 0)
            chk = chk_widget.findChild(QCheckBox)
            if chk and chk.isChecked():
                combo = self.table.cellWidget(i, 2)
                dt_edit = self.table.cellWidget(i, 3)
                
                state = combo.currentText() if combo else "Schedule"
                if dt_edit:
                    rounded_dt = round_qdatetime_to_tiktok_minutes(dt_edit.dateTime())
                    dt_edit.setDateTime(rounded_dt)
                    dt_str = rounded_dt.toString("MM/dd/yyyy HH:mm")
                else:
                    dt_str = ""
                
                results.append({
                    "row_index": i,
                    "state": state,
                    "schedule_time": dt_str
                })
        return results
