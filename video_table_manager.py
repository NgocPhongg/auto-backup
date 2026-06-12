import os
import subprocess
from PyQt5.QtWidgets import QMessageBox, QInputDialog, QFileDialog, QTableWidgetItem, QMenu
from PyQt5.QtCore import Qt

from scheduler_dialog import SchedulerDialog
from title_editor_dialog import TitleEditorDialog
from account_selector_dialog import AccountSelectorDialog
from upload_dashboard import UploadDashboard
from gologin_profile_utils import first_real_gologin_profile_id
from browser_backend_utils import (
    LOCAL_CHROME_BACKEND,
    GOLOGIN_BACKEND,
    STEALTH_FIREFOX_BACKEND,
    ensure_profile_backend_defaults,
    normalize_browser_backend,
)


class VideoTableManager:
    """Quản lý các thao tác liên quan đến bảng Video (Hẹn giờ/Đăng bài)"""
    def __init__(self, main_gui):
        self.gui = main_gui

    def _table_text(self, table, row, col):
        item = table.item(row, col)
        return item.text().strip() if item else ""

    def _account_upload_identity(self, row):
        name_item = self.gui.acc_table.item(row, 1)
        try:
            data = ensure_profile_backend_defaults(dict(name_item.data(Qt.UserRole) or {})) if name_item else {}
        except Exception:
            data = {}

        ten_ho_so = (
            self._table_text(self.gui.acc_table, row, 1)
            or str(data.get("ten_ho_so") or "").strip()
            or f"Row {row + 1}"
        )
        browser_id = (
            str(data.get("browser_id") or "").strip()
            or self._table_text(self.gui.acc_table, row, 4)
        )
        backend = normalize_browser_backend(data.get("browser_backend"))
        gologin_profile_id = ""
        if backend == LOCAL_CHROME_BACKEND:
            task_browser_id = browser_id
            profile_key = f"local_chrome:{browser_id}" if browser_id else f"row:{row}"
        elif backend == STEALTH_FIREFOX_BACKEND:
            task_browser_id = browser_id
            profile_key = f"stealth_firefox:{browser_id}" if browser_id else f"row:{row}"
        else:
            gologin_profile_id = first_real_gologin_profile_id(
                data.get("gologin_profile_id"),
                browser_id,
            )
            task_browser_id = gologin_profile_id or browser_id
            profile_key = (
                f"gologin:{gologin_profile_id}"
                if gologin_profile_id
                else f"row:{row}"
            )
            backend = GOLOGIN_BACKEND

        return {
            "ten_ho_so": ten_ho_so,
            "source_row": row,
            "profile_key": profile_key,
            "browser_backend": backend,
            "browser_id": task_browser_id,
            "gologin_profile_id": gologin_profile_id,
            "proxy": self._table_text(self.gui.acc_table, row, 3),
            "proxy_type": str(data.get("proxy_type") or "").strip(),
            "cookie": str(data.get("cookie") or "").strip(),
            "id_tiktok": self._table_text(self.gui.acc_table, row, 5),
            "tong_views": self._table_text(self.gui.acc_table, row, 8),
            "views_30": self._table_text(self.gui.acc_table, row, 6),
            "tinh_trang": self._table_text(self.gui.acc_table, row, 12),
            "stt": self._table_text(self.gui.acc_table, row, 25) or str(row + 1),
        }

    def _make_upload_to_item(self, account_info):
        item = QTableWidgetItem(account_info.get("ten_ho_so", ""))
        item.setData(Qt.UserRole, {
            "profile_key": account_info.get("profile_key", ""),
            "source_row": account_info.get("source_row", -1),
            "browser_backend": account_info.get("browser_backend", ""),
            "browser_id": account_info.get("browser_id", ""),
            "gologin_profile_id": account_info.get("gologin_profile_id", ""),
            "proxy": account_info.get("proxy", ""),
            "proxy_type": account_info.get("proxy_type", ""),
            "cookie": account_info.get("cookie", ""),
        })
        return item

    def handle_choose_video_folder(self):
        """Action 1: Chọn thư mục Video và lưu vào profile"""
        selected_rows = self.gui.acc_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self.gui, "Cảnh báo", "Vui lòng chọn ít nhất 1 tài khoản để gán thư mục!")
            return
            
        folder_path = QFileDialog.getExistingDirectory(self.gui, "Chọn thư mục chứa Video MP4")
        if not folder_path:
            return
            
        for index in selected_rows:
            row = index.row()
            self.gui.acc_table.setItem(row, 11, QTableWidgetItem(folder_path))
            
            name_item = self.gui.acc_table.item(row, 1)
            if name_item:
                data = name_item.data(Qt.UserRole) or {}
                data["thu_muc_upload"] = folder_path
                name_item.setData(Qt.UserRole, data)
                
        self.gui.save_accounts_to_db()
        self.gui.log(f"Đã gán thư mục: {folder_path} cho {len(selected_rows)} profile.", "green")

    def scan_video_folder(self, folder_path, profile_name):
        """Bước 2: Xử lý file bằng FFprobe (chỉ lấy mp4)"""
        videos = []
        if not os.path.exists(folder_path):
            return videos
            
        for file in os.listdir(folder_path):
            if file.lower().endswith('.mp4'):
                full_path = os.path.join(folder_path, file)
                try:
                    cmd = [
                        "ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of",
                        "default=noprint_wrappers=1:nokey=1", full_path
                    ]
                    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=0x08000000)
                    duration_sec = float(result.stdout.strip())
                    
                    m, s = divmod(int(duration_sec), 60)
                    h, m = divmod(m, 60)
                    duration_str = f"{h:02d}:{m:02d}:{s:02d}"
                except Exception as e:
                    print(f"ffprobe lỗi với {file}: {e}")
                    duration_str = "Unknown"
                    
                title = os.path.splitext(file)[0]
                videos.append({
                    "title": title,
                    "duration": duration_str,
                    "upload_to": profile_name,
                    "full_path": full_path
                })
        return videos

    def handle_load_videos(self):
        """Action 2: Load Video xuống bảng chờ (có kiểm tra trùng lặp)"""
        selected_rows = self.gui.acc_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self.gui, "Cảnh báo", "Vui lòng chọn profile để load video!")
            return
            
        limit, ok = QInputDialog.getInt(self.gui, "Số lượng video", "Nhập số video muốn load cho mỗi profile\n(0 = Load toàn bộ thư mục):", 0, 0, 10000, 1)
        if not ok:
            return
            
        total_loaded = 0
        existing_keys = set()
        for i in range(self.gui.video_table.rowCount()):
            t_item = self.gui.video_table.item(i, 0)
            p_item = self.gui.video_table.item(i, 3)
            if t_item and p_item:
                try:
                    p_meta = dict(p_item.data(Qt.UserRole) or {})
                except Exception:
                    p_meta = {}
                existing_keys.add(f"{t_item.text()}_{p_meta.get('profile_key') or p_item.text()}")
        
        for index in selected_rows:
            row = index.row()
            folder_item = self.gui.acc_table.item(row, 11)
            name_item = self.gui.acc_table.item(row, 1)
            
            if not folder_item or not folder_item.text().strip():
                continue
                
            folder_path = folder_item.text().strip()
            account_info = self._account_upload_identity(row)
            profile_name = account_info.get("ten_ho_so") or (name_item.text() if name_item else "Unknown")
            
            if not os.path.exists(folder_path):
                QMessageBox.warning(self.gui, "Lỗi", f"Thư mục không tồn tại:\n{folder_path}")
                continue
                
            self.gui.log(f"Đang quét thư mục: {folder_path}...")
            videos = self.scan_video_folder(folder_path, profile_name)
            
            loaded_for_profile = 0
            for v in videos:
                if limit > 0 and loaded_for_profile >= limit:
                    break
                    
                key = f"{v['title']}_{account_info.get('profile_key') or v['upload_to']}"
                if key in existing_keys:
                    continue 
                    
                v_row = self.gui.video_table.rowCount()
                self.gui.video_table.insertRow(v_row)
                
                self.gui.video_table.setItem(v_row, 0, QTableWidgetItem(v['title']))
                self.gui.video_table.setItem(v_row, 1, QTableWidgetItem(v['duration']))
                self.gui.video_table.setItem(v_row, 2, QTableWidgetItem("Đang chờ"))
                self.gui.video_table.setItem(v_row, 3, self._make_upload_to_item(account_info))
                self.gui.video_table.setItem(v_row, 4, QTableWidgetItem("Ready"))
                self.gui.video_table.setItem(v_row, 6, QTableWidgetItem(v['full_path']))
                
                existing_keys.add(key)
                total_loaded += 1
                loaded_for_profile += 1
                
        if total_loaded > 0:
            self.gui.log(f"Đã load {total_loaded} video mới xuống bảng chờ.", "green")
            QMessageBox.information(self.gui, "Thành công", f"Đã load {total_loaded} video xuống bảng chờ!")
        else:
            self.gui.log("Không có video mới nào được load (hoặc đã load hết rồi).", "yellow")

    def handle_clear_video_table(self):
        self.gui.video_table.setRowCount(0)
        self.gui.log("Đã xóa bảng video chờ.", "green")

    def handle_schedule_videos(self):
        if self.gui.video_table.rowCount() == 0:
            QMessageBox.warning(self.gui, "Cảnh báo", "Bảng video trống! Vui lòng load video trước.")
            return
            
        video_data = []
        for i in range(self.gui.video_table.rowCount()):
            t_item = self.gui.video_table.item(i, 0)
            d_item = self.gui.video_table.item(i, 1)
            u_item = self.gui.video_table.item(i, 3)
            
            video_data.append({
                "title": t_item.text() if t_item else "",
                "duration": d_item.text() if d_item else "",
                "upload_to": u_item.text() if u_item else ""
            })
            
        dialog = SchedulerDialog(self.gui, video_data=video_data)
        if dialog.exec_() == dialog.Accepted:
            scheduled_data = dialog.get_scheduled_data()
            for sd in scheduled_data:
                row = sd["row_index"]
                state = sd["state"]
                time_str = sd["schedule_time"]
                
                if state == "Schedule" and time_str:
                    self.gui.video_table.setItem(row, 2, QTableWidgetItem(f"Schedule: {time_str}"))
                else:
                    self.gui.video_table.setItem(row, 2, QTableWidgetItem("Public"))
                    
            self.gui.log(f"Đã áp dụng lịch cho {len(scheduled_data)} video.", "green")

    def show_video_context_menu(self, pos):
        menu = QMenu(self.gui)
        
        action_edit_titles = menu.addAction("📝 Sửa thông tin hàng loạt cho các video này")
        action_edit_titles.triggered.connect(self.handle_batch_edit_titles)
        
        action_choose_account = menu.addAction("👤 Chọn tài khoản Upload cho các video này")
        action_choose_account.triggered.connect(self.handle_select_upload_account)
        
        action_load_queue = menu.addAction("📥 Tải các video này vào hàng chờ")
        action_load_queue.triggered.connect(self.handle_open_upload_dashboard)
        
        copy_menu = menu.addMenu("📋 Sao chép thông tin")
        copy_menu.addAction("Sao chép link gốc")
        copy_menu.addAction("Sao chép tiêu đề")
        
        menu.addAction("📂 Chuyển các video này sang dự án khác")
        menu.addAction("🔄 Đặt lại trạng thái các video này")
        menu.addAction("🔀 Đổi chỗ ngẫu nhiên vị trí các video này")
        
        action_download = menu.addAction("⬇️ Tải xuống các video này")
        action_download.triggered.connect(self.gui.handle_download_video)
        
        menu.addAction("✏️ Đổi tên file theo tiêu đề")
        menu.addAction("🛑 Dừng tải xuống các video này")
        menu.addAction("🔗 Cập nhật link tải xuống các video này")
        
        action_delete = menu.addAction("🗑️ Xóa các video này")
        action_delete.triggered.connect(self.handle_delete_selected_videos)
        
        menu.exec_(self.gui.video_table.viewport().mapToGlobal(pos))

    def handle_batch_edit_titles(self):
        selected_rows = self.gui.video_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self.gui, "Cảnh báo", "Vui lòng chọn ít nhất 1 video để sửa tiêu đề!")
            return
            
        row_indices = [index.row() for index in selected_rows]
        dialog = TitleEditorDialog(self.gui, selected_rows=row_indices)
        if dialog.exec_():
            self.gui.log(f"Đã sửa tiêu đề cho {len(row_indices)} video.", "green")

    def handle_delete_selected_videos(self):
        selected_rows = self.gui.video_table.selectionModel().selectedRows()
        if not selected_rows:
            return
        for index in sorted(selected_rows, key=lambda x: x.row(), reverse=True):
            self.gui.video_table.removeRow(index.row())
        self.gui.log(f"Đã xóa {len(selected_rows)} video khỏi bảng.")

    def handle_select_upload_account(self):
        import math
        import random
        selected_rows = self.gui.video_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self.gui, "Cảnh báo", "Vui lòng chọn ít nhất 1 video ở bảng dưới!")
            return
            
        video_indices = [idx.row() for idx in selected_rows]
        
        # Build account data
        account_data = []
        for r in range(self.gui.acc_table.rowCount()):
            account_data.append(self._account_upload_identity(r))
            
        dialog = AccountSelectorDialog(self.gui, account_data=account_data, selected_video_indices=video_indices)
        if dialog.exec_():
            res = dialog.result_data
            mode = res["mode"]
            accounts = res["accounts"]
            num_vids = len(video_indices)
            num_accs = len(accounts)
            
            for i, row_index in enumerate(video_indices):
                assigned_acc = {}
                if mode == "single":
                    # Chia mảng tuần tự (ví dụ 5 video, 2 acc -> acc1 có 3, acc2 có 2)
                    chunk_size = math.ceil(num_vids / num_accs)
                    acc_idx = i // chunk_size
                    if acc_idx >= num_accs: acc_idx = num_accs - 1
                    assigned_acc = accounts[acc_idx]
                elif mode == "interleave":
                    assigned_acc = accounts[i % num_accs]
                elif mode == "random":
                    assigned_acc = random.choice(accounts)
                    
                if not isinstance(assigned_acc, dict):
                    assigned_acc = {"ten_ho_so": str(assigned_acc or "")}
                self.gui.video_table.setItem(row_index, 3, self._make_upload_to_item(assigned_acc))
                
            self.gui.log(f"Đã gán tài khoản upload cho {num_vids} video theo kiểu '{mode}'.", "green")

    def handle_open_upload_dashboard(self):
        selected_rows = self.gui.video_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self.gui, "Cảnh báo", "Vui lòng chọn ít nhất 1 video để đưa vào hàng chờ upload!")
            return

        profile_lookup = {}
        name_lookup = {}
        for r in range(self.gui.acc_table.rowCount()):
            info = self._account_upload_identity(r)
            profile_lookup[info["profile_key"]] = info
            name_lookup.setdefault(info["ten_ho_so"], []).append(info)

        video_tasks = []
        for index in selected_rows:
            row = index.row()
            title = self.gui.video_table.item(row, 0).text() if self.gui.video_table.item(row, 0) else ""
            status = self.gui.video_table.item(row, 2).text() if self.gui.video_table.item(row, 2) else ""
            upload_item = self.gui.video_table.item(row, 3)
            upload_to = upload_item.text() if upload_item else ""
            file_path = self.gui.video_table.item(row, 6).text() if self.gui.video_table.item(row, 6) else ""

            schedule_time = "Public"
            status_text = (status or "").strip()
            if status_text.lower().startswith("schedule:"):
                schedule_time = status_text.split(":", 1)[1].strip()

            # Lấy thông tin profile
            upload_meta = {}
            if upload_item:
                try:
                    upload_meta = dict(upload_item.data(Qt.UserRole) or {})
                except Exception:
                    upload_meta = {}
            profile_key = upload_meta.get("profile_key", "")
            pinfo = profile_lookup.get(profile_key, {}) if profile_key else {}
            if not pinfo and upload_meta.get("gologin_profile_id"):
                pinfo = upload_meta
            if not pinfo and upload_to:
                matches = name_lookup.get(upload_to, [])
                if len(matches) == 1:
                    pinfo = matches[0]
                elif len(matches) > 1:
                    QMessageBox.warning(
                        self.gui,
                        "Trung ten profile",
                        f"Video '{title}' dang gan voi ten profile bi trung: {upload_to}.\n"
                        "Hay chon lai tai khoan Upload cho video nay de gan dung GoLogin ID.",
                    )
                    return

            video_tasks.append({
                "title": title,
                "schedule_time": schedule_time,
                "upload_to": upload_to,
                "file_path": file_path,
                "upload_profile_key": pinfo.get("profile_key", ""),
                "source_account_row": pinfo.get("source_row", -1),
                "browser_backend": pinfo.get("browser_backend", "gologin"),
                "browser_id": pinfo.get("browser_id", ""),
                "gologin_profile_id": pinfo.get("gologin_profile_id", ""),
                "proxy": pinfo.get("proxy", ""),
                "proxy_type": pinfo.get("proxy_type", ""),
                "cookie": pinfo.get("cookie", "")
            })
            
        dashboard = UploadDashboard(self.gui, video_tasks=video_tasks)
        dashboard.exec_()
