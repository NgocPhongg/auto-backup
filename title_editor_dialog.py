from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QGroupBox, QRadioButton, 
    QLabel, QSpinBox, QLineEdit, QTextEdit, QPushButton, QButtonGroup, QMessageBox
)
from PyQt5.QtCore import Qt
import random

class TitleEditorDialog(QDialog):
    def __init__(self, parent=None, selected_rows=None):
        super().__init__(parent)
        self.setWindowTitle("Trình chỉnh sửa tiêu đề hàng loạt")
        self.resize(700, 500)
        
        # Danh sách các QTableWidgetItem (cột Tiêu đề) cần được sửa
        self.selected_rows = selected_rows or []
        
        self.init_ui()
        
    def init_ui(self):
        main_layout = QVBoxLayout(self)
        
        # Tiêu đề lớn
        lbl_title = QLabel("Bạn muốn áp dụng thay đổi với tiêu đề như thế nào?")
        lbl_title.setStyleSheet("font-size: 18px; font-weight: bold; color: white;")
        main_layout.addWidget(lbl_title)
        
        # GroupBox 1: 1 cho tất cả
        self.group_1 = QGroupBox("1 cho tất cả")
        self.group_1.setCheckable(True)
        self.group_1.setChecked(True)
        layout_1 = QVBoxLayout(self.group_1)
        
        lbl_desc_1 = QLabel("Tiêu đề đó là gì? (Nếu bạn sử dụng tăng dần có nghĩa là nội dung thêm vào + bắt đầu từ tự tăng dần, ví dụ part 1, part#1...)")
        lbl_desc_1.setStyleSheet("color: #aaaaaa;")
        layout_1.addWidget(lbl_desc_1)
        
        row_1 = QHBoxLayout()
        self.input_base_title = QLineEdit()
        row_1.addWidget(self.input_base_title)
        
        self.radio_keep = QRadioButton("Giữ nguyên")
        self.radio_keep.setChecked(True)
        self.radio_inc = QRadioButton("Tăng dần")
        row_1.addWidget(self.radio_keep)
        row_1.addWidget(self.radio_inc)
        
        row_1.addWidget(QLabel("nội dung thêm vào"))
        self.input_inc_text = QLineEdit("part")
        self.input_inc_text.setFixedWidth(60)
        row_1.addWidget(self.input_inc_text)
        
        row_1.addWidget(QLabel("bắt đầu từ"))
        self.spin_inc_start = QSpinBox()
        self.spin_inc_start.setRange(0, 999999)
        row_1.addWidget(self.spin_inc_start)
        
        layout_1.addLayout(row_1)
        main_layout.addWidget(self.group_1)
        
        # GroupBox 2: Rải theo số lượng
        self.group_2 = QGroupBox("Rải theo số lượng")
        self.group_2.setCheckable(True)
        self.group_2.setChecked(False)
        layout_2 = QVBoxLayout(self.group_2)
        
        lbl_desc_2 = QLabel('Nhập tất cả tiêu đề bạn có vào đây và chọn kiểu rải. <span style="color: red;">Mỗi 1 dòng được coi là 1 tiêu đề</span>')
        layout_2.addWidget(lbl_desc_2)
        
        row_2 = QHBoxLayout()
        self.radio_top_down = QRadioButton("từ trên xuống")
        self.radio_top_down.setChecked(True)
        self.radio_random = QRadioButton("ngẫu nhiên")
        row_2.addWidget(self.radio_top_down)
        row_2.addWidget(self.radio_random)
        row_2.addStretch()
        layout_2.addLayout(row_2)
        
        self.text_titles = QTextEdit()
        layout_2.addWidget(self.text_titles)
        main_layout.addWidget(self.group_2)
        
        # GroupBox 3: Thêm hàng loạt vào vị trí nhắm đến
        self.group_3 = QGroupBox("Thêm hàng loạt vào vị trí nhắm đến")
        self.group_3.setCheckable(True)
        self.group_3.setChecked(False)
        layout_3 = QVBoxLayout(self.group_3)
        
        row_3 = QHBoxLayout()
        self.radio_add_start = QRadioButton("Đầu tiêu đề")
        self.radio_add_start.setChecked(True)
        self.radio_add_end = QRadioButton("cuối tiêu đề")
        row_3.addWidget(self.radio_add_start)
        row_3.addWidget(self.radio_add_end)
        
        lbl_desc_3 = QLabel("Bạn có thể thêm hashtag, text, hay bất cứ thứ gì tuỳ ý mình muốn tại đây.")
        lbl_desc_3.setStyleSheet("color: #aaaaaa;")
        row_3.addWidget(lbl_desc_3)
        row_3.addStretch()
        layout_3.addLayout(row_3)
        
        self.input_add_text = QLineEdit()
        layout_3.addWidget(self.input_add_text)
        main_layout.addWidget(self.group_3)
        
        # --- Make group boxes mutually exclusive (optional behavior based on UI standard) ---
        # Tuy nhiên, theo hình ảnh, các group box có vẻ hoạt động như RadioButton độc lập
        self.group_1.toggled.connect(lambda checked: self.handle_group_toggle(self.group_1, checked))
        self.group_2.toggled.connect(lambda checked: self.handle_group_toggle(self.group_2, checked))
        self.group_3.toggled.connect(lambda checked: self.handle_group_toggle(self.group_3, checked))
        
        # Nút Áp dụng và Hủy
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_apply = QPushButton("Áp dụng")
        btn_apply.setStyleSheet("background-color: #4CAF50; color: white; padding: 5px 15px; font-weight: bold;")
        btn_apply.clicked.connect(self.apply_changes)
        
        btn_cancel = QPushButton("Hủy")
        btn_cancel.clicked.connect(self.reject)
        
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_apply)
        main_layout.addLayout(btn_layout)

        # Style chung cho QGroupBox (dark theme)
        self.setStyleSheet("""
            QDialog {
                background-color: #1e1e1e;
                color: #ffffff;
            }
            QGroupBox {
                border: 1px solid #555555;
                margin-top: 10px;
                color: white;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 3px 0 3px;
            }
            QLabel, QRadioButton, QCheckBox {
                color: white;
            }
            QLineEdit, QTextEdit, QSpinBox {
                background-color: #2d2d2d;
                color: white;
                border: 1px solid #555555;
            }
        """)

    def handle_group_toggle(self, toggled_group, checked):
        """Đảm bảo chỉ 1 group được chọn tại một thời điểm (như radio button)"""
        if not checked:
            # Ngăn không cho người dùng tắt group cuối cùng (luôn phải có 1 cái bật)
            if not self.group_1.isChecked() and not self.group_2.isChecked() and not self.group_3.isChecked():
                toggled_group.setChecked(True)
            return
            
        if toggled_group == self.group_1:
            self.group_2.setChecked(False)
            self.group_3.setChecked(False)
        elif toggled_group == self.group_2:
            self.group_1.setChecked(False)
            self.group_3.setChecked(False)
        elif toggled_group == self.group_3:
            self.group_1.setChecked(False)
            self.group_2.setChecked(False)

    def apply_changes(self):
        """Xử lý logic khi bấm Áp dụng"""
        if not self.selected_rows:
            QMessageBox.warning(self, "Lỗi", "Không có video nào được chọn!")
            return
            
        # Lấy danh sách item cột Tiêu đề (Cột 0)
        # selected_rows là danh sách các object row index
        
        if self.group_1.isChecked():
            base_title = self.input_base_title.text().strip()
            inc_text = self.input_inc_text.text().strip()
            start_num = self.spin_inc_start.value()
            
            for i, row_index in enumerate(self.selected_rows):
                if self.radio_keep.isChecked():
                    new_title = base_title
                else: # Tăng dần
                    new_title = f"{base_title} {inc_text}{start_num + i}".strip()
                self.update_title_cell(row_index, new_title)
                
        elif self.group_2.isChecked():
            lines = [line.strip() for line in self.text_titles.toPlainText().split('\n') if line.strip()]
            if not lines:
                QMessageBox.warning(self, "Lỗi", "Danh sách tiêu đề trống!")
                return
                
            if self.radio_random.isChecked():
                for row_index in self.selected_rows:
                    new_title = random.choice(lines)
                    self.update_title_cell(row_index, new_title)
            else: # Từ trên xuống
                line_count = len(lines)
                for i, row_index in enumerate(self.selected_rows):
                    new_title = lines[i % line_count] # Lặp lại nếu thiếu
                    self.update_title_cell(row_index, new_title)
                    
        elif self.group_3.isChecked():
            add_text = self.input_add_text.text().strip()
            if not add_text:
                QMessageBox.warning(self, "Lỗi", "Vui lòng nhập nội dung cần thêm!")
                return
                
            for row_index in self.selected_rows:
                old_title = self.get_title_cell(row_index)
                if self.radio_add_start.isChecked():
                    new_title = f"{add_text} {old_title}"
                else:
                    new_title = f"{old_title} {add_text}"
                self.update_title_cell(row_index, new_title)
                
        self.accept()

    def get_title_cell(self, row_index):
        # self.parent() là tham chiếu đến MainGUI
        item = self.parent().video_table.item(row_index, 0)
        return item.text() if item else ""

    def update_title_cell(self, row_index, new_title):
        from PyQt5.QtWidgets import QTableWidgetItem
        self.parent().video_table.setItem(row_index, 0, QTableWidgetItem(new_title))
