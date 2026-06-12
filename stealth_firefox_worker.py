from __future__ import annotations

import os
import threading
import time
import uuid

from PyQt5.QtCore import QThread, pyqtSignal

from app_paths import require_stealth_firefox_exe, stealth_firefox_profile_dir
from browser_backend_utils import (
    STEALTH_FIREFOX_BACKEND,
    ensure_profile_backend_defaults,
    make_stealth_firefox_browser_id,
    normalize_browser_backend,
)
from proxy_utils import parse_proxy_string


class StealthFirefoxWorker(QThread):
    status_update = pyqtSignal(str, str)
    finished_signal = pyqtSignal(str)
    profile_update_signal = pyqtSignal(dict)
    browser_ready_signal = pyqtSignal(dict)
    browser_closed_signal = pyqtSignal(str)

    def __init__(
        self,
        profile_index,
        profile_data,
        selected_features,
        feed_settings,
        container_width=0,
        container_height=0,
        widget_id=0,
        parent=None,
        manual_only=False,
        planned_profile_count=1,
    ):
        super().__init__(parent)
        self.profile_index = profile_index
        self.profile_data = ensure_profile_backend_defaults(dict(profile_data or {}))
        self.selected_features = list(selected_features or [])
        self.feed_settings = dict(feed_settings or {})
        self.container_width = int(container_width or 960)
        self.container_height = int(container_height or 680)
        self.widget_id = int(widget_id or 0)
        self.manual_only = bool(manual_only)
        self.planned_profile_count = max(1, int(planned_profile_count or 1))

        self._stop_flag = False
        self._stop_event = threading.Event()
        self._embed_done_event = threading.Event()
        self._embed_result = False
        self._embed_token = uuid.uuid4().hex
        self._launch_started_at = 0.0
        self._finished_emitted = False
        self._browser_closed_emitted = False
        self._closed_reason = "closed"

        self._context = None
        self._runtime = None
        self._playwright_owner = None
        self._page = None
        self._process_pid = 0
        self._browser_pids = set()
        self._embedded_hwnd = 0

        backend = normalize_browser_backend(self.profile_data.get("browser_backend"))
        if backend != STEALTH_FIREFOX_BACKEND:
            self.profile_data["browser_backend"] = STEALTH_FIREFOX_BACKEND

        browser_id = str(self.profile_data.get("browser_id") or "").strip()
        if not browser_id.startswith("stealth_firefox:"):
            browser_id = make_stealth_firefox_browser_id(self.profile_data.get("ten_ho_so", "") or f"profile_{profile_index}")
            self.profile_data["browser_id"] = browser_id
        self.profile_data["gologin_profile_id"] = ""
        self._browser_id = browser_id
        self._profile_dir = str(stealth_firefox_profile_dir(browser_id))

    def _emit_finished(self, result: str):
        if self._finished_emitted:
            return
        self._finished_emitted = True
        self.finished_signal.emit(str(result or "success"))

    def _emit_browser_closed(self, reason="closed"):
        if self._browser_closed_emitted:
            return
        self._browser_closed_emitted = True
        self.browser_closed_signal.emit(str(reason or "closed"))

    def notify_embed_result(self, success=False, hwnd=0, pid=0, message=""):
        try:
            self._embed_result = bool(success)
            self._embedded_hwnd = int(hwnd or 0) if success else 0
            if pid:
                self._browser_pids.add(int(pid))
            if message:
                self.status_update.emit(str(message), "green" if success else "orange")
        except Exception:
            self._embed_result = False
        finally:
            self._embed_done_event.set()

    def _norm_proc_path(self, path):
        try:
            return os.path.abspath(str(path or "")).replace("\\", "/").rstrip("/").lower()
        except Exception:
            return str(path or "").replace("\\", "/").rstrip("/").lower()

    def _find_pids_by_profile_path(self, profile_dir):
        target = self._norm_proc_path(profile_dir)
        if not target:
            return set()
        try:
            import psutil
        except Exception:
            return set()

        result = set()
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = str(proc.info.get("name") or "").lower()
                if name not in {"firefox.exe"}:
                    continue
                cmdline = " ".join(
                    str(arg) for arg in (proc.info.get("cmdline") or [])
                ).replace("\\", "/").lower()
                if target in cmdline:
                    result.add(int(proc.info.get("pid") or 0))
            except Exception:
                continue
        return {pid for pid in result if pid > 0}

    def _refresh_browser_pids(self):
        pids = set(self._browser_pids)
        if self._process_pid:
            pids.add(int(self._process_pid))
        pids.update(self._find_pids_by_profile_path(self._profile_dir))
        self._browser_pids = {int(pid) for pid in pids if int(pid) > 0}
        if self._browser_pids:
            if not self._process_pid or self._process_pid not in self._browser_pids:
                self._process_pid = max(self._browser_pids)
        return set(self._browser_pids)

    def _proxy_dict(self):
        proxy_str = str(self.profile_data.get("proxy") or "").strip()
        proxy_type = str(self.profile_data.get("proxy_type") or "http").strip()
        parsed = parse_proxy_string(proxy_str, proxy_type)
        if not parsed:
            return None
        data = {"server": f"{parsed['mode']}://{parsed['host']}:{parsed['port']}"}
        if parsed.get("username"):
            data["username"] = parsed["username"]
        if parsed.get("password"):
            data["password"] = parsed["password"]
        return data

    def _launch_runtime(self):
        from invisible_playwright import InvisiblePlaywright

        firefox_exe = require_stealth_firefox_exe()
        proxy_dict = self._proxy_dict()
        runtime = InvisiblePlaywright(
            profile_dir=self._profile_dir,
            binary_path=firefox_exe,
            proxy=proxy_dict,
            humanize=True,
            headless=False,
        )
        context_or_browser = runtime.__enter__()
        self._runtime = runtime
        self._context = context_or_browser
        return context_or_browser

    def _capture_existing_page(self):
        context = self._context
        if not context:
            self._page = None
            return
        pages = getattr(context, "pages", None)
        if isinstance(pages, list) and pages:
            self._page = pages[0]
            return
        if callable(pages):
            try:
                values = pages()
                if values:
                    self._page = values[0]
                    return
            except Exception:
                pass
        self._page = None

    def _emit_browser_ready(self):
        self._refresh_browser_pids()
        payload = {
            "worker_id": id(self),
            "debug_port": 0,
            "process_pid": int(self._process_pid or 0),
            "profile_dir": self._profile_dir,
            "profile_id": self._browser_id,
            "embed_token": self._embed_token,
            "launch_started_at": float(self._launch_started_at or 0.0),
            "timeout": 30.0,
        }
        self.browser_ready_signal.emit(payload)

    def _close_runtime(self):
        runtime = getattr(self, "_runtime", None)
        if runtime is not None:
            try:
                runtime.__exit__(None, None, None)
            except Exception:
                pass
            self._runtime = None

        for obj_name in ("_page", "_context", "_playwright_owner"):
            obj = getattr(self, obj_name, None)
            if not obj:
                continue
            try:
                close_fn = getattr(obj, "close", None)
                if callable(close_fn):
                    close_fn()
            except Exception:
                pass
            setattr(self, obj_name, None)

    def run(self):
        self._launch_started_at = time.time()

        if not self.manual_only and self.selected_features:
            self.status_update.emit(
                "Stealth Firefox ban dau chi ho tro mo browser + profile persistent; chua ho tro full automation.",
                "orange",
            )
            self._emit_finished("error: Stealth Firefox chua ho tro automation")
            return

        try:
            self.status_update.emit("Stealth Firefox: dang mo browser persistent...", "blue")
            self._launch_runtime()
            self._capture_existing_page()

            for _ in range(20):
                pids = self._refresh_browser_pids()
                if pids:
                    break
                if self._stop_event.wait(0.25):
                    break

            self.status_update.emit(
                "Stealth Firefox: browser da mo. Ban co the thao tac tay, dang nhap va session se duoc giu.",
                "green",
            )
            self._emit_browser_ready()

            missing_ticks = 0
            while not self._stop_event.wait(0.5):
                pids = self._refresh_browser_pids()
                if pids:
                    missing_ticks = 0
                else:
                    missing_ticks += 1
                    if missing_ticks >= 4:
                        self._closed_reason = "closed"
                        break

            if self._stop_flag:
                self._closed_reason = "stopped"
        except ImportError as exc:
            self.status_update.emit(
                f"Thieu invisible_playwright: {exc}. Cai bang lenh: python -m pip install invisible_playwright",
                "red",
            )
            self._closed_reason = "error"
            self._emit_finished("error: missing invisible_playwright")
            return
        except FileNotFoundError as exc:
            self.status_update.emit(str(exc), "red")
            self._closed_reason = "error"
            self._emit_finished(f"error: {exc}")
            return
        except Exception as exc:
            self.status_update.emit(f"Stealth Firefox loi: {str(exc)[:200]}", "red")
            self._closed_reason = "error"
            self._emit_finished(f"error: {exc}")
            return
        finally:
            self._close_runtime()
            self._emit_browser_closed(self._closed_reason)
            self._emit_finished("success" if self._closed_reason in {"closed", "stopped"} else "error")

    def stop(self):
        self._stop_flag = True
        self._stop_event.set()
        self.status_update.emit("Dang dong Stealth Firefox...", "orange")
