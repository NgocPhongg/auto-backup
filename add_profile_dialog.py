from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
    QTextEdit, QPushButton, QWidget, QRadioButton, QFrame,
    QGridLayout, QSizePolicy, QFileDialog
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

class AddProfileDialog(QDialog):
    def __init__(self, parent=None, existing_data=None):
        super().__init__(parent)
        self.setWindowTitle("Chỉnh sửa hồ sơ" if existing_data else "Thêm mới hồ sơ")
        self.existing_data = existing_data or {}
        self.resize(900, 650)
        # Bỏ dấu chấm hỏi ở góc trên bên phải
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        
        self.init_ui()
        self.apply_styles()
        self.prefill_data()

    def init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(30)

        # ================= CỘT TRÁI =================
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        title_lbl = QLabel("Thêm mới hồ sơ")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title_lbl.setFont(title_font)
        left_layout.addWidget(title_lbl)

        sub_title_lbl = QLabel("Thêm hồ sơ với các thông tin cần thiết của bạn và hãy chọn kiểu trình duyệt mà bạn muốn nhé!")
        sub_title_lbl.setWordWrap(True)
        left_layout.addWidget(sub_title_lbl)
        
        left_layout.addSpacing(10)

        # Các trường nhập liệu cột trái
        self.inputs = {}
        
        self.add_left_input(left_layout, "Tên hồ sơ:", "ten_ho_so")
        self.add_left_input(left_layout, "Username hoặc Email:", "username")
        self.add_left_input(left_layout, "Password hiện tại:", "password")
        self.add_left_input(left_layout, "Password cũ trước đó(trường này sẽ update khi bạn sử dụng chức năng đổi mật khẩu):", "old_password")
        
        # 2FA
        fa_layout = QVBoxLayout()
        fa_layout.setSpacing(2)
        lbl_fa = QHBoxLayout()
        lbl_fa.addWidget(QLabel("2FA:"))
        lbl_fa.addStretch()
        lbl_fa.addWidget(self.create_link_label("Lấy mã"))
        fa_layout.addLayout(lbl_fa)
        self.inputs["2fa"] = QLineEdit()
        self.inputs["2fa"].setPlaceholderText("không có thì bỏ trống..")
        fa_layout.addWidget(self.inputs["2fa"])
        left_layout.addLayout(fa_layout)

        # Password mail
        pmail_layout = QVBoxLayout()
        pmail_layout.setSpacing(2)
        lbl_pmail = QHBoxLayout()
        lbl_pmail.addWidget(QLabel("Password mail:"))
        lbl_pmail.addStretch()
        lbl_pmail.addWidget(self.create_link_label("Kiểm tra hòm thư (hotmail/mailtm)"))
        pmail_layout.addLayout(lbl_pmail)
        self.inputs["password_mail"] = QLineEdit()
        self.inputs["password_mail"].setPlaceholderText("không có thì bỏ trống..")
        pmail_layout.addWidget(self.inputs["password_mail"])
        left_layout.addLayout(pmail_layout)

        # Ghi chú
        left_layout.addWidget(QLabel("Ghi chú cho tài khoản này:"))
        self.inputs["note"] = QTextEdit()
        self.inputs["note"].setMaximumHeight(80)
        left_layout.addWidget(self.inputs["note"])

        left_layout.addStretch()
        btn_layout = QHBoxLayout()
        btn_submit = QPushButton("Lưu hồ sơ" if self.existing_data else "Thêm mới hồ sơ")
        btn_submit.setObjectName("btn_submit")
        btn_submit.clicked.connect(self.accept)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_submit)
        left_layout.addLayout(btn_layout)

        main_layout.addWidget(left_widget, 1) # Tỷ lệ 1

        # ================= CỘT PHẢI =================
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(15)

        # Panel Fingerprint (Nền đen)
        fp_panel = QFrame()
        fp_panel.setObjectName("fp_panel")
        fp_layout = QVBoxLayout(fp_panel)
        
        fp_title = QLabel("Fingerprint(Vân tay browser)")
        fp_title.setStyleSheet("color: white; font-size: 18px; font-weight: bold;")
        fp_title.setAlignment(Qt.AlignCenter)
        fp_layout.addWidget(fp_title)
        
        fp_texts = [
            "Mỗi 1 hồ sơ khi được tạo sẽ tự động có 1 Fingerprint riêng",
            "Bạn không nên thay đổi Fingerprint liên tục nếu mọi thứ vẫn đang bình thường!",
            "Chỉ nên thay đổi Fingerprint nếu đăng nhập bị Maximum hoặc đã có mục đích riêng"
        ]
        for t in fp_texts:
            lbl = QLabel(t)
            lbl.setStyleSheet("color: white; font-size: 12px;")
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setWordWrap(True)
            fp_layout.addWidget(lbl)
            
        right_layout.addWidget(fp_panel)

        # Proxy và ID Trình duyệt
        proxy_id_layout = QGridLayout()
        
        # Row 1
        proxy_lbl_layout = QHBoxLayout()
        proxy_lbl_layout.addWidget(QLabel("Proxy:"))
        proxy_lbl_layout.addStretch()
        self.radio_http = QRadioButton("http")
        self.radio_http.setChecked(True)
        self.radio_socks5 = QRadioButton("socks5")
        proxy_lbl_layout.addWidget(self.radio_http)
        proxy_lbl_layout.addWidget(self.radio_socks5)
        
        proxy_id_layout.addLayout(proxy_lbl_layout, 0, 0)
        proxy_id_layout.addWidget(QLabel("ID Browser nội bộ (Tự động)"), 0, 1)
        
        # Row 2
        self.inputs["proxy"] = QLineEdit()
        proxy_id_layout.addWidget(self.inputs["proxy"], 1, 0)
        
        self.inputs["browser_id"] = QLineEdit()
        self.inputs["browser_id"].setPlaceholderText("Sẽ tự động tạo khi bấm Thêm mới...")
        self.inputs["browser_id"].setReadOnly(True)
        self.inputs["browser_id"].setStyleSheet("background-color: #f0f0f0; color: #666;")
        proxy_id_layout.addWidget(self.inputs["browser_id"], 1, 1)
        
        right_layout.addLayout(proxy_id_layout)

        right_layout.addWidget(QLabel("GoLogin Profile ID (nếu dùng GoLogin cloud):"))
        self.inputs["gologin_profile_id"] = QLineEdit()
        self.inputs["gologin_profile_id"].setPlaceholderText("Profile ID trong bảng GoLogin")
        right_layout.addWidget(self.inputs["gologin_profile_id"])

        right_layout.addWidget(QLabel("Avatar TikTok:"))
        avatar_layout = QHBoxLayout()
        self.inputs["avatar_path"] = QLineEdit()
        self.inputs["avatar_path"].setPlaceholderText("Chọn file ảnh avatar (.jpg, .png, .webp)")
        btn_avatar = QPushButton("Chọn ảnh")
        btn_avatar.setFixedWidth(90)
        btn_avatar.clicked.connect(self.choose_avatar_file)
        avatar_layout.addWidget(self.inputs["avatar_path"])
        avatar_layout.addWidget(btn_avatar)
        right_layout.addLayout(avatar_layout)

        # Mail khôi phục
        right_layout.addWidget(QLabel("Mail khôi phục:"))
        self.inputs["recovery_mail"] = QLineEdit()
        self.inputs["recovery_mail"].setPlaceholderText("không có thì bỏ trống..")
        right_layout.addWidget(self.inputs["recovery_mail"])

        # Cookie
        right_layout.addWidget(QLabel("Cookie"))
        self.inputs["cookie"] = QTextEdit()
        right_layout.addWidget(self.inputs["cookie"])

        # RefreshToken
        rt_layout = QVBoxLayout()
        rt_layout.setSpacing(2)
        lbl_rt = QHBoxLayout()
        lbl_rt.addWidget(QLabel("RefreshToken Hotmail/Outlook:"))
        lbl_rt.addStretch()
        lbl_rt.addWidget(self.create_link_label("Đọc qua dongvan read_mail_box"))
        rt_layout.addLayout(lbl_rt)
        self.inputs["refresh_token"] = QLineEdit()
        self.inputs["refresh_token"].setPlaceholderText("trống..")
        rt_layout.addWidget(self.inputs["refresh_token"])
        right_layout.addLayout(rt_layout)

        # ClientID
        ci_layout = QVBoxLayout()
        ci_layout.setSpacing(2)
        lbl_ci = QHBoxLayout()
        lbl_ci.addWidget(QLabel("ClientID Hotmail/Outlook:"))
        lbl_ci.addStretch()
        lbl_ci.addWidget(self.create_link_label("Đọc qua dongvan get_code_mail"))
        ci_layout.addLayout(lbl_ci)
        self.inputs["client_id"] = QLineEdit()
        self.inputs["client_id"].setPlaceholderText("trống..")
        ci_layout.addWidget(self.inputs["client_id"])
        right_layout.addLayout(ci_layout)

        main_layout.addWidget(right_widget, 1)

    def prefill_data(self):
        """Điền dữ liệu cũ vào form nếu đang ở chế độ Edit"""
        if not self.existing_data:
            return
            
        for key, widget in self.inputs.items():
            val = self.existing_data.get(key, "")
            if isinstance(widget, QLineEdit):
                widget.setText(val)
            elif isinstance(widget, QTextEdit):
                widget.setPlainText(val)
                
        # Handle Radio Buttons for Proxy
        proxy_type = self.existing_data.get("proxy_type", "http")
        if proxy_type == "socks5":
            self.radio_socks5.setChecked(True)
        else:
            self.radio_http.setChecked(True)

    def add_left_input(self, layout, label_text, key):
        layout.addWidget(QLabel(label_text))
        self.inputs[key] = QLineEdit()
        layout.addWidget(self.inputs[key])

    def create_link_label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet("color: red; font-size: 11px;")
        return lbl

    def choose_avatar_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Chon anh avatar TikTok",
            "",
            "File ?nh (*.png *.jpg *.jpeg *.webp);;T?t c? file (*)"
        )
        if path:
            self.inputs["avatar_path"].setText(path)

    def apply_styles(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #ffffff;
            }
            QLabel {
                font-size: 13px;
                color: #333333;
            }
            QLineEdit, QTextEdit {
                border: 1px solid #ced4da;
                border-radius: 4px;
                padding: 6px;
                background-color: #ffffff;
            }
            QLineEdit:focus, QTextEdit:focus {
                border: 1px solid #80bdff;
            }
            #fp_panel {
                background-color: #2b2b2b;
                border-radius: 8px;
                padding: 15px;
            }
            QPushButton {
                background-color: #f8f9fa;
                border: 1px solid #ced4da;
                border-radius: 4px;
                padding: 8px;
                font-weight: bold;
                color: #333;
            }
            QPushButton:hover {
                background-color: #e2e6ea;
            }
        """)

    def get_data(self):
        """Trả về dữ liệu đã nhập dưới dạng dictionary"""
        return {
            "ten_ho_so": self.inputs["ten_ho_so"].text(),
            "username": self.inputs["username"].text(),
            "password": self.inputs["password"].text(),
            "old_password": self.inputs["old_password"].text(),
            "2fa": self.inputs["2fa"].text(),
            "password_mail": self.inputs["password_mail"].text(),
            "note": self.inputs["note"].toPlainText(),
            "proxy": self.inputs["proxy"].text(),
            "proxy_type": "socks5" if self.radio_socks5.isChecked() else "http",
            "recovery_mail": self.inputs["recovery_mail"].text(),
            "cookie": self.inputs["cookie"].toPlainText(),
            "refresh_token": self.inputs["refresh_token"].text(),
            "client_id": self.inputs["client_id"].text(),
            "browser_id": self.inputs["browser_id"].text(),
            "gologin_profile_id": self.inputs["gologin_profile_id"].text(),
            "avatar_path": self.inputs["avatar_path"].text()
        }
