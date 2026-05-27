import re

filepath = 'd:/auto - backup/main_gui.py'
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add context menu item
target_menu = '''        action_check = acc_feat.addAction("2. Kiểm tra trạng thái (Live/Die)")
        action_check.triggered.connect(self.handle_check_status)'''
replacement_menu = '''        action_check = acc_feat.addAction("2. Kiểm tra trạng thái (Live/Die)")
        action_check.triggered.connect(self.handle_check_status)
        action_stats = acc_feat.addAction("📊 3. Cập nhật thống kê kênh (Follow/View/Video)")
        action_stats.triggered.connect(self.handle_update_stats)'''
content = content.replace(target_menu, replacement_menu)

# 2. Add handle_update_stats method
method_code = '''
    def handle_update_stats(self):
        """Khởi chạy CDPWorker ngầm để lấy thông số kênh."""
        selected_rows = self.acc_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "Lỗi", "Vui lòng chọn ít nhất 1 tài khoản!")
            return
            
        from cdp_worker import CDPWorker
        
        reply = QMessageBox.question(
            self, "Xác nhận", 
            f"Bạn có chắc muốn tự động mở trình duyệt và cập nhật thông số cho {len(selected_rows)} tài khoản này không?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes: return

        self.log(f"Bắt đầu lấy thông số cho {len(selected_rows)} tài khoản...", "blue")
        
        for index in selected_rows:
            row = index.row()
            name_item = self.acc_table.item(row, 1)
            if not name_item: continue
            
            profile_name = name_item.text()
            profile_data = name_item.data(Qt.UserRole) or {}
            
            # Khởi chạy CDPWorker
            worker = CDPWorker(
                profile_index=row,
                profile_data=profile_data,
                selected_features=["Cập nhật thống kê"],
                feed_settings={},
                parent=self
            )
            
            # Kết nối các tín hiệu
            worker.status_update.connect(lambda msg, color, p=profile_name: self.log(f"[{p}] {msg}", color))
            
            # Slot để cập nhật UI khi có data mới
            def on_profile_update(new_data, r=row, ni=name_item):
                ni.setData(Qt.UserRole, new_data)
                self.acc_table.setItem(r, 7, QTableWidgetItem(str(new_data.get("t_follows", ""))))
                self.acc_table.setItem(r, 8, QTableWidgetItem(str(new_data.get("t_views", ""))))
                self.acc_table.setItem(r, 9, QTableWidgetItem(str(new_data.get("t_video", ""))))
                self.save_accounts_to_db()
                self.log(f"Đã cập nhật bảng cho hàng {r+1}", "green")
                
            worker.profile_update_signal.connect(on_profile_update)
            
            self.active_workers.append(worker)
            worker.start()
'''

target_insert = '''    def handle_check_status(self):'''
content = content.replace(target_insert, method_code + '\n' + target_insert)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)
