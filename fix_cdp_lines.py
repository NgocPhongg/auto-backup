import sys

with open('cdp_worker.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_method = '''    def _embed_browser_window(self):
        """Tìm HWND Chrome và nhúng vào QWidget container (SetParent — 60 FPS)."""
        import ctypes

        if not self.widget_id:
            return

        pid = self._process.pid if self._process else None
        user32 = ctypes.windll.user32
        width = self.container_width
        height = self.container_height

        def enum_cb(hwnd, results):
            if win32gui.IsWindowVisible(hwnd):
                if win32gui.GetClassName(hwnd) == "Chrome_WidgetWin_1":
                    process_id = ctypes.c_ulong()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
                    results.append((hwnd, process_id.value))

        # Tìm cửa sổ Chrome (chờ tối đa 10 giây)
        for attempt in range(200):
            if self._stop_flag:
                return
            hwnds = []
            win32gui.EnumWindows(enum_cb, hwnds)

            # Tìm theo PID gốc/child PID, hoặc theo CDP port khi browser do GoLogin SDK mở.
            target_hwnd = None
            all_pids = set()
            if pid:
                try:
                    import psutil
                    parent = psutil.Process(pid)
                    all_pids = {pid}
                    for child in parent.children(recursive=True):
                        all_pids.add(child.pid)
                except Exception:
                    all_pids = {pid}
            
            all_pids.update(self._find_pids_by_debug_port(self._debug_port))

            if all_pids:
                self._browser_pids.update(all_pids)

            fallback_candidates = []
            for hwnd, wpid in hwnds:
                if wpid in all_pids:
                    target_hwnd = hwnd
                    break
                if not all_pids:
                    try:
                        import psutil
                        proc = psutil.Process(wpid)
                        name = (proc.name() or "").lower()
                        if name in {"chrome.exe", "orbita-browser.exe", "chromium.exe"}:
                            created_at = proc.create_time()
                            if not self._launch_started_at or created_at >= self._launch_started_at - 5:
                                fallback_candidates.append((created_at, hwnd, wpid))
                    except Exception:
                        pass

            if not target_hwnd and fallback_candidates:
                _created_at, target_hwnd, fallback_pid = max(fallback_candidates, key=lambda item: item[0])
                self._browser_pids.add(fallback_pid)

            if target_hwnd:
                try:
                    self.status_update.emit("📺 Browser nhúng OK! (60 FPS)", "green")
                    # Bỏ viền, title bar
                    style = win32gui.GetWindowLong(target_hwnd, win32con.GWL_STYLE)
                    style &= ~(win32con.WS_CAPTION | win32con.WS_THICKFRAME |
                               win32con.WS_BORDER | win32con.WS_MINIMIZEBOX |
                               win32con.WS_MAXIMIZEBOX | win32con.WS_SYSMENU |
                               getattr(win32con, "WS_POPUP", 0x80000000))
                    style |= (win32con.WS_CHILD | win32con.WS_VISIBLE |
                              getattr(win32con, "WS_CLIPSIBLINGS", 0x04000000) |
                              getattr(win32con, "WS_CLIPCHILDREN", 0x02000000))
                    win32gui.SetWindowLong(target_hwnd, win32con.GWL_STYLE, style)

                    # Bỏ extended style
                    ex_style = win32gui.GetWindowLong(target_hwnd, win32con.GWL_EXSTYLE)
                    ex_style &= ~(win32con.WS_EX_DLGMODALFRAME | win32con.WS_EX_WINDOWEDGE |
                                  win32con.WS_EX_CLIENTEDGE | win32con.WS_EX_STATICEDGE |
                                  getattr(win32con, "WS_EX_NOACTIVATE", 0x08000000) |
                                  getattr(win32con, "WS_EX_APPWINDOW", 0x00040000) |
                                  getattr(win32con, "WS_EX_TOPMOST", 0x00000008))
                    win32gui.SetWindowLong(target_hwnd, win32con.GWL_EXSTYLE, ex_style)

                    # Thiết lập style cho container QWidget để hiển thị đúng
                    parent_style = win32gui.GetWindowLong(self.widget_id, win32con.GWL_STYLE)
                    parent_style |= (getattr(win32con, "WS_CLIPCHILDREN", 0x02000000) |
                                     getattr(win32con, "WS_CLIPSIBLINGS", 0x04000000))
                    win32gui.SetWindowLong(self.widget_id, win32con.GWL_STYLE, parent_style)

                    # Nhúng
                    win32gui.SetParent(target_hwnd, self.widget_id)

                    # Resize fit container — đẩy lên trên để ẩn title bar
                    SWP_FRAMECHANGED = 0x0020
                    tb = APP_TITLEBAR_HEIGHT
                    win32gui.SetWindowPos(
                        target_hwnd, win32con.HWND_TOP,
                        0, -tb, width, height + tb,
                        win32con.SWP_SHOWWINDOW | SWP_FRAMECHANGED |
                        win32con.SWP_NOACTIVATE |
                        getattr(win32con, "SWP_NOOWNERZORDER", 0x0200)
                    )

                    self.embedded_hwnd = target_hwnd

                    # Lấy focus vào browser ngay khi nhúng xong
                    self._focus_embedded_browser(target_hwnd)

                    # Tạo thead giữ browser luôn vừa container
                    import threading
                    threading.Thread(
                        target=self._lock_browser_position,
                        args=(target_hwnd, self.widget_id, width, height),
                        daemon=True
                    ).start()
                    return
                except Exception as e:
                    self.status_update.emit(f"⚠️ Nhúng lỗi: {str(e)[:40]}", "orange")
                    return

            time.sleep(0.05)

        self.status_update.emit("⚠️ Không tìm thấy cửa sổ Chrome", "orange")
'''

start_line = 590
end_line = 806

new_lines = lines[:start_line] + [new_method + '\n'] + lines[end_line:]

with open('cdp_worker.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
print('Done!')
