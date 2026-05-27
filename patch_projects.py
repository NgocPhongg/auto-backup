import json
import os
from PyQt5.QtWidgets import QInputDialog, QMessageBox

# This script will patch main_gui.py to add the Project features.

def patch_main_gui():
    filepath = 'main_gui.py'
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # 1. Update setup_account_section to connect buttons and add project logic
    target_toolbar = """        # Toolbar Tài khoản
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Dự án:"))
        self.proj_combo = QComboBox()
        self.proj_combo.setMinimumWidth(150)
        self.proj_combo.addItems(["Tổng tài khoản", "Dự án 01", "Dự án 02"])
        toolbar.addWidget(self.proj_combo)
        
        toolbar.addWidget(QPushButton("🔄"))
        toolbar.addWidget(QPushButton("➕"))"""

    replacement_toolbar = """        # Toolbar Tài khoản
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Dự án:"))
        self.proj_combo = QComboBox()
        self.proj_combo.setMinimumWidth(150)
        self.proj_combo.currentTextChanged.connect(self.filter_by_project)
        toolbar.addWidget(self.proj_combo)
        
        btn_refresh_proj = QPushButton("🔄")
        btn_refresh_proj.clicked.connect(self.load_projects)
        toolbar.addWidget(btn_refresh_proj)
        
        btn_add_proj = QPushButton("➕")
        btn_add_proj.clicked.connect(self.handle_add_project)
        toolbar.addWidget(btn_add_proj)"""

    content = content.replace(target_toolbar, replacement_toolbar)

    # 2. Add Project Management methods inside SSMAToolGUI class
    # Find load_accounts_from_db to insert project methods near it
    target_load = """    def load_accounts_from_db(self):"""
    
    project_methods = """    # ================= QUẢN LÝ DỰ ÁN =================
    def load_projects(self):
        try:
            with open('projects.json', 'r', encoding='utf-8') as f:
                projects = json.load(f)
        except:
            projects = []
        
        current = self.proj_combo.currentText()
        self.proj_combo.blockSignals(True)
        self.proj_combo.clear()
        self.proj_combo.addItem("Tổng tài khoản")
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
            if proj_name == "Tổng tài khoản":
                return
            try:
                with open('projects.json', 'r', encoding='utf-8') as f:
                    projects = json.load(f)
            except:
                projects = []
                
            if proj_name not in projects:
                projects.append(proj_name)
                with open('projects.json', 'w', encoding='utf-8') as f:
                    json.dump(projects, f, ensure_ascii=False)
                self.load_projects()
                self.proj_combo.setCurrentText(proj_name)

    def filter_by_project(self, _=None):
        project = self.proj_combo.currentText()
        is_all = (project == "Tổng tài khoản")
        
        for row in range(self.acc_table.rowCount()):
            name_item = self.acc_table.item(row, 1)
            if name_item:
                data = name_item.data(Qt.UserRole) or {}
                acc_project = data.get("project", "")
                if is_all or acc_project == project:
                    self.acc_table.setRowHidden(row, False)
                else:
                    self.acc_table.setRowHidden(row, True)

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
            QMessageBox.warning(self, "Lỗi", "Bạn chưa có dự án nào. Hãy tạo dự án trước.")
            return
            
        projects.insert(0, "Tổng tài khoản (Xóa khỏi dự án)")
            
        item, ok = QInputDialog.getItem(self, "Chuyển dự án", "Chọn dự án:", projects, 0, False)
        if ok and item:
            target_proj = "" if "Tổng tài khoản" in item else item
            
            for row in selected_rows:
                name_item = self.acc_table.item(row, 1)
                if name_item:
                    data = name_item.data(Qt.UserRole) or {}
                    data["project"] = target_proj
                    name_item.setData(Qt.UserRole, data)
                    
            self.save_accounts_to_db()
            self.filter_by_project()
            self.log(f"Đã chuyển {len(selected_rows)} tài khoản sang dự án '{item}'.")

    def load_accounts_from_db(self):"""
    
    content = content.replace(target_load, project_methods)

    # 3. Call load_projects at the end of __init__
    target_init = """        self.active_workers = []"""
    replacement_init = """        self.active_workers = []
        self.load_projects()"""
    content = content.replace(target_init, replacement_init)

    # 4. Update open_add_profile_dialog to assign newly created profiles to the current project
    target_add = """            proxy_str = data.get("proxy", "")"""
    replacement_add = """            # Assign to current project
            current_proj = self.proj_combo.currentText()
            data["project"] = "" if current_proj == "Tổng tài khoản" else current_proj
            
            proxy_str = data.get("proxy", "")"""
    content = content.replace(target_add, replacement_add)

    # 5. Add Context Menu action
    target_menu = """        acc_feat = menu.addMenu("👤 2. Tính năng tài khoản")"""
    replacement_menu = """        menu.addAction("📂 Chuyển sang dự án...").triggered.connect(self.assign_to_project)
        
        acc_feat = menu.addMenu("👤 2. Tính năng tài khoản")"""
    content = content.replace(target_menu, replacement_menu)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
        
patch_main_gui()
print("Thành công")
