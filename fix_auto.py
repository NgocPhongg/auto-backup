import sys

with open('automation_dashboard.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

replacement = """        selected_rows = []
        for row in range(self.table.rowCount()):
            chk = self.table.item(row, 0)
            if chk and chk.checkState() == Qt.Checked:
                selected_rows.append(row)

        if not selected_rows:
            return

        embed_browser = self.chk_browser.isChecked()
        for idx, row in enumerate(selected_rows):
            acc_info = self.accounts_data[row] if row < len(self.accounts_data) else {}
            profile_data = acc_info.get("profile_data", {})
            profile_name = profile_data.get("ten_ho_so", "") or acc_info.get("columns", {}).get("1", f"Profile_{row}")
            tiktok_id = acc_info.get("columns", {}).get("5", "")

            # Đảm bảo browser_id luôn có giá trị duy nhất
            browser_id = profile_data.get("browser_id", "")
            if not browser_id:
                browser_id = f"auto_{uuid.uuid4().hex[:8]}"
                profile_data["browser_id"] = browser_id
                acc_info.setdefault("profile_data", {})["browser_id"] = browser_id

            # Tạo BrowserPreviewWidget KHÔNG có chức năng automation
            preview = BrowserPreviewWidget(profile_name, tiktok_id, profile_data,
                                           selected_features=[],  # Không chạy task nào
                                           feed_settings={},
                                           profile_index=row,
                                           account_row=row,
                                           embed_browser=embed_browser)
            preview.data_updated.connect(self._on_preview_data_updated)
            preview.status_updated.connect(self._on_preview_status_updated)
            r, c = idx // max_cols, idx % max_cols
            self.browser_grid_layout.addWidget(preview, r, c)
            self.browser_widgets[browser_id] = preview

            # Mở browser (chỉ launch + embed, không chạy automation)
            delay_ms = idx * 1000  # Mỗi profile cách 1 giây
            QTimer.singleShot(delay_ms, preview.open_browser_only)

        total_rows = (len(selected_rows) - 1) // max_cols + 1
        for col in range(max_cols):
            self.browser_grid_layout.setColumnStretch(col, 0)
        row_height = 737
        self.browser_grid_container.setMinimumHeight(total_rows * row_height)
"""

# Lines are 0-indexed in array. 
# 708 is line index 707
# 782 is line index 781
start_index = 707
end_index = 782  # line 783 is _load_feed_settings, index 782

new_lines = lines[:start_index] + [replacement + '\n'] + lines[end_index:]

with open('automation_dashboard.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
print('Fixed lines 708-782 in automation_dashboard.py')
