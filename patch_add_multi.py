import re

filepath = 'main_gui.py'
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add import
if 'from add_multiple_dialog import AddMultipleDialog' not in content:
    content = content.replace('from add_profile_dialog import AddProfileDialog', 
                              'from add_profile_dialog import AddProfileDialog\nfrom add_multiple_dialog import AddMultipleDialog')

# 2. Update button setup
target_btn = 'toolbar.addWidget(QPushButton("Add nhiều"))'
replace_btn = '''        btn_add_multi = QPushButton("Add nhiều")
        btn_add_multi.clicked.connect(self.open_add_multiple_dialog)
        toolbar.addWidget(btn_add_multi)'''
content = content.replace(target_btn, replace_btn)

# 3. Add open_add_multiple_dialog method
method_code = '''
    def open_add_multiple_dialog(self):
        projects = []
        for i in range(self.proj_combo.count()):
            projects.append(self.proj_combo.itemText(i))
            
        dialog = AddMultipleDialog(self, projects=projects, current_project=self.proj_combo.currentText())
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
                        self.log("Dừng quá trình Import do lỗi API GoLogin.", "red")
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
'''

if 'def open_add_multiple_dialog' not in content:
    content = content.replace('    def open_add_profile_dialog(self):', method_code + '\n    def open_add_profile_dialog(self):')

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)
