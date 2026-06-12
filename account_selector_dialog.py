from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QPushButton, QRadioButton, QLabel, QCheckBox, QMessageBox, QAbstractItemView
)
from PyQt5.QtCore import Qt

class AccountSelectorDialog(QDialog):
    def __init__(self, parent=None, account_data=None, selected_video_indices=None):
        super().__init__(parent)
        self.setWindowTitle("Bảng chọn tài khoản để upload")
        self.resize(850, 500)
        
        # account_data is a list of dicts: [{'row': index, 'ten_ho_so': ..., 'proxy': ..., 'id_tiktok': ..., 'tong_views': ..., 'views_30': ..., 'tinh_trang': ..., 'stt': ...}, ...]
        self.account_data = account_data or []
        self.selected_video_indices = selected_video_indices or []
        
        self.init_ui()
        self.populate_table()
        
    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        # --- Bảng Account ---
        self.table = QTableWidget()
        headers = ["Run", "Tên hồ sơ", "Proxy", "ID Tiktok", "Tổng views", "Views 30", "Tình trạng", "ID"]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        
        # Giao diện bảng màu sáng (trắng/xanh nhạt) như yêu cầu
        self.table.setStyleSheet("""
            QTableWidget {
                background-color: white;
                color: black;
                gridline-color: #cccccc;
            }
            QHeaderView::section {
                background-color: #333333;
                color: white;
                font-weight: bold;
                border: 1px solid #555555;
            }
        """)
        
        main_layout.addWidget(self.table)
        
        # --- Các nút bấm (Hàng 1) ---
        btn_layout = QHBoxLayout()
        
        btn_apply = QPushButton("Thực hiện")
        btn_apply.setStyleSheet("background-color: white; color: black; font-weight: bold;")
        btn_apply.clicked.connect(self.apply_changes)
        
        btn_select_all = QPushButton("Chọn tất cả")
        btn_select_all.setStyleSheet("background-color: white; color: black;")
        btn_select_all.clicked.connect(self.select_all)
        
        btn_deselect_all = QPushButton("Bỏ chọn tất cả")
        btn_deselect_all.setStyleSheet("background-color: white; color: black;")
        btn_deselect_all.clicked.connect(self.deselect_all)

        btn_layout.addWidget(btn_apply)
        btn_layout.addWidget(btn_select_all)
        btn_layout.addWidget(btn_deselect_all)
        
        main_layout.addLayout(btn_layout)
        
        # --- Các Radio Option (Hàng 2) ---
        opt_layout = QHBoxLayout()
        
        self.radio_single = QRadioButton("rải đơn lẻ")
        self.radio_single.setChecked(True)
        self.radio_interleave = QRadioButton("rải đan xen")
        self.radio_random = QRadioButton("rải ngẫu nhiên")
        self.radio_off = QRadioButton("Tắt")
        
        opt_layout.addWidget(self.radio_single)
        opt_layout.addWidget(self.radio_interleave)
        opt_layout.addWidget(self.radio_random)
        opt_layout.addWidget(self.radio_off)
        opt_layout.addStretch()
        
        lbl_info = QLabel(f"Bạn đang chuẩn bị áp dụng cho các video có thứ tự trong bảng dưới đây:\n{', '.join(str(i+1) for i in self.selected_video_indices)}")
        lbl_info.setStyleSheet("color: white;")
        opt_layout.addWidget(lbl_info)
        
        main_layout.addLayout(opt_layout)
        
        # Background chung của Dialog (Dark)
        self.setStyleSheet("""
            QDialog {
                background-color: #1e1e1e;
            }
            QRadioButton, QLabel {
                color: white;
            }
            QPushButton {
                padding: 5px 10px;
                border: 1px solid #aaaaaa;
            }
            QPushButton:hover {
                background-color: #dddddd;
            }
        """)

    def populate_table(self):
        self.table.setRowCount(len(self.account_data))
        for i, acc in enumerate(self.account_data):
            # Cột 0: Checkbox
            chk = QCheckBox()
            chk_widget = QWidget()
            chk_layout = QHBoxLayout(chk_widget)
            chk_layout.addWidget(chk)
            chk_layout.setAlignment(Qt.AlignCenter)
            chk_layout.setContentsMargins(0,0,0,0)
            self.table.setCellWidget(i, 0, chk_widget)
            
            # Cột 1: Tên hồ sơ
            self.table.setItem(i, 1, QTableWidgetItem(acc.get("ten_ho_so", "")))
            self.table.setItem(i, 2, QTableWidgetItem(acc.get("proxy", "")))
            self.table.setItem(i, 3, QTableWidgetItem(acc.get("id_tiktok", "")))
            self.table.setItem(i, 4, QTableWidgetItem(acc.get("tong_views", "")))
            self.table.setItem(i, 5, QTableWidgetItem(acc.get("views_30", "")))
            
            # Tình trạng
            tinh_trang = acc.get("tinh_trang", "")
            item_tt = QTableWidgetItem(tinh_trang)
            self.table.setItem(i, 6, item_tt)
            
            self.table.setItem(i, 7, QTableWidgetItem(acc.get("stt", str(i+1))))
            
            # Đổi màu nền (ví dụ màu xanh lam nhạt như trong hình cho các row)
            for col in range(1, 8):
                item = self.table.item(i, col)
                if item:
                    item.setBackground(Qt.lightGray) # Màu nhạt
            
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

    def apply_changes(self):
        # Thu thập các account được check
        selected_accounts = []
        for i in range(self.table.rowCount()):
            chk_widget = self.table.cellWidget(i, 0)
            if chk_widget:
                chk = chk_widget.findChild(QCheckBox)
                if chk and chk.isChecked():
                    selected_accounts.append(dict(self.account_data[i]))
                    
        if not selected_accounts:
            QMessageBox.warning(self, "Cảnh báo", "Bạn chưa chọn tài khoản nào!")
            return
            
        if self.radio_off.isChecked():
            # Nếu chọn Tắt, không làm gì cả
            self.reject()
            return
            
        # Trả về dữ liệu
        mode = "single"
        if self.radio_interleave.isChecked():
            mode = "interleave"
        elif self.radio_random.isChecked():
            mode = "random"
            
        self.result_data = {
            "mode": mode,
            "accounts": selected_accounts
        }
        self.accept()
