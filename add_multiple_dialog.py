from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
    QTextEdit, QPushButton, QComboBox, QMessageBox
)
from PyQt5.QtCore import Qt

class AddMultipleDialog(QDialog):
    def __init__(self, parent=None, projects=None, current_project=None, existing_names=None):
        super().__init__(parent)
        self.setWindowTitle("Thêm nhiều hồ sơ")
        self.resize(700, 500)
        self.projects = projects or ["Tổng tài khoản"]
        self.current_project = current_project or "Tổng tài khoản"
        self.existing_names = existing_names or []
        
        # Valid fields mapping
        self.VALID_FIELDS = [
            "ten_ho_so", "username", "password", "2fa", "password_mail", 
            "recovery_mail", "proxy", "cookie", "refresh_token", "client_id", "note"
        ]
        self.FIELD_ALIASES = {
            "pass_hotmail": "password_mail",
            "pass_mail": "password_mail",
            "hotmail_pass": "password_mail",
            "hotmail_password": "password_mail",
            "email": "username",
            "mail": "username",
            "hotmail": "username",
            "pass": "password",
        }
        
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # 1. Project selection
        proj_layout = QHBoxLayout()
        proj_layout.addWidget(QLabel("Thêm vào dự án:"))
        self.proj_combo = QComboBox()
        self.proj_combo.addItems(self.projects)
        if self.current_project in self.projects:
            self.proj_combo.setCurrentText(self.current_project)
        proj_layout.addWidget(self.proj_combo)
        proj_layout.addStretch()
        layout.addLayout(proj_layout)

        # 2. Format configuration
        format_layout = QVBoxLayout()
        format_layout.addWidget(QLabel("Định dạng dữ liệu (phân cách bởi dấu |, tab hoặc dấu -):"))
        self.format_input = QLineEdit()
        self.format_input.setText("username|password|password_mail|refresh_token|client_id")
        self.format_input.setPlaceholderText("VD: username|password|password_mail|2fa|proxy")
        format_layout.addWidget(self.format_input)
        
        lbl_hint = QLabel("Các trường hỗ trợ: " + ", ".join(self.VALID_FIELDS) + ", alias: email/mail/hotmail -> username, pass_hotmail/pass_mail -> password_mail")
        lbl_hint.setStyleSheet("color: gray; font-size: 11px;")
        lbl_hint.setWordWrap(True)
        format_layout.addWidget(lbl_hint)
        layout.addLayout(format_layout)

        # 3. Raw data input
        layout.addWidget(QLabel("Dán dữ liệu vào đây (mỗi tài khoản 1 dòng):"))
        self.data_input = QTextEdit()
        self.data_input.setPlaceholderText("kinnerfennig68@outlook.com|pass123|hotmailPass123|tokenABC|clientXYZ\nhoặc email - pass - token - client_id\n...")
        layout.addWidget(self.data_input)

        # 4. Buttons
        btn_layout = QHBoxLayout()
        self.btn_import = QPushButton("Nhập dữ liệu")
        self.btn_import.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 8px;")
        self.btn_import.clicked.connect(self.process_import)
        
        btn_cancel = QPushButton("Hủy")
        btn_cancel.clicked.connect(self.reject)
        
        btn_layout.addStretch()
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(self.btn_import)
        layout.addLayout(btn_layout)
        
        self.result_data = []

    def _normalize_field_name(self, field_name):
        field = (field_name or "").strip().lower()
        return self.FIELD_ALIASES.get(field, field)

    def _split_format(self, fmt_str):
        import re

        text = (fmt_str or "").strip()
        if not text:
            return []
        if "|" in text:
            parts = text.split("|")
        elif "\t" in text:
            parts = text.split("\t")
        else:
            parts = re.split(r"\s+-\s+", text)
        return [self._normalize_field_name(part) for part in parts if part.strip()]

    def _split_data_line(self, line, expected_cols):
        import re

        text = (line or "").strip()
        if "|" in text:
            parts = text.split("|", max(0, expected_cols - 1))
        elif "\t" in text:
            parts = text.split("\t", max(0, expected_cols - 1))
        else:
            # Common pasted format: email - password - refresh_token - client_id.
            # Split only on a hyphen surrounded by whitespace so token/client-id
            # hyphens are preserved.
            parts = re.split(r"\s+-\s+", text, maxsplit=max(0, expected_cols - 1))
        return [part.strip() for part in parts]

    def process_import(self):
        fmt_str = self.format_input.text().strip()
        raw_data = self.data_input.toPlainText().strip()
        
        if not fmt_str or not raw_data:
            QMessageBox.warning(self, "Lỗi", "Vui lòng nhập định dạng và dữ liệu!")
            return
            
        columns = self._split_format(fmt_str)
        if not columns:
            QMessageBox.warning(self, "Lỗi định dạng", "Định dạng không hợp lệ.")
            return

        # Validate columns
        invalid_cols = [c for c in columns if c not in self.VALID_FIELDS and c != "ignore"]
        if invalid_cols:
            QMessageBox.warning(self, "Lỗi định dạng", f"Các trường sau không hợp lệ: {', '.join(invalid_cols)}\n(Dùng 'ignore' nếu muốn bỏ qua cột đó)")
            return
            
        lines = raw_data.split("\n")
        parsed_profiles = []
        
        target_project = self.proj_combo.currentText()
        if target_project == "Tổng tài khoản":
            target_project = ""
            
        import time
        
        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
                
            parts = self._split_data_line(line, len(columns))
            if len(parts) != len(columns):
                QMessageBox.warning(self, "Lỗi dữ liệu", f"Dòng {i+1} không khớp với định dạng (có {len(parts)} cột thay vì {len(columns)}).\nDòng: {line}")
                return
                
            profile = {
                "project": target_project,
                "proxy_type": "http" # default
            }
            
            # Fill default values for all valid fields
            for field in self.VALID_FIELDS:
                profile[field] = ""
                
            # Parse row
            for col_name, val in zip(columns, parts):
                if col_name != "ignore":
                    profile[col_name] = val.strip()
                    
            # Auto-generate name if missing or empty
            is_auto_generated = False
            if not profile.get("ten_ho_so"):
                base_name = profile.get("username", f"Profile_{int(time.time())}_{i}")
                name = base_name
                counter = 1
                while name in self.existing_names or any(p["ten_ho_so"] == name for p in parsed_profiles):
                    name = f"{base_name}_{counter}"
                    counter += 1
                profile["ten_ho_so"] = name
                is_auto_generated = True
                
            # --- KIỂM TRA TRÙNG TÊN HỒ SƠ ---
            name = profile["ten_ho_so"]
            
            # Chỉ báo lỗi trùng nếu người dùng CHỦ ĐỘNG nhập tên hồ sơ (không phải do auto-gen)
            if not is_auto_generated:
                if name in self.existing_names:
                    QMessageBox.warning(self, "Trùng tên hồ sơ", f"Hồ sơ '{name}' ở dòng {i+1} đã tồn tại trong phần mềm!\nVui lòng sửa lại tên để tránh xung đột.")
                    return
                
                # Kiểm tra trùng lặp ngay trong danh sách đang thêm
                if any(p["ten_ho_so"] == name for p in parsed_profiles):
                    QMessageBox.warning(self, "Trùng tên hồ sơ", f"Hồ sơ '{name}' ở dòng {i+1} bị trùng lặp với một dòng khác ngay trong danh sách bạn vừa nhập!\nVui lòng sửa lại tên.")
                    return
                
            parsed_profiles.append(profile)
            
        self.result_data = parsed_profiles
        self.accept()
