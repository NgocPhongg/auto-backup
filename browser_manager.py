import os
import random
import socket
import subprocess
import threading
import time

from app_paths import gologin_base_dir, require_chrome_exe, require_orbita_browser_exe

GOLOGIN_BASE_DIR = str(gologin_base_dir())


class BrowserManager:
    """Central browser launcher with per-profile locking and dynamic CDP ports."""

    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self.active_profiles = {}
        self._lock = threading.RLock()
        self._initialized = True

    def _normalize_profile_id(self, profile_id, profile_dir):
        profile_id = str(profile_id or "").strip()
        if profile_id:
            return profile_id
        profile_dir = os.path.abspath(profile_dir or "")
        if profile_dir:
            return os.path.basename(profile_dir)
        raise ValueError("Thiếu profile_id/profile_dir để mở trình duyệt.")

    def _normalize_profile_dir(self, profile_dir):
        return os.path.abspath(profile_dir or "").lower().replace("\\", "/")

    def _process_alive(self, process):
        try:
            return process is not None and process.poll() is None
        except Exception:
            return False

    def _find_free_port(self):
        for _ in range(80):
            port = random.randint(20000, 60000)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.bind(("127.0.0.1", port))
                except OSError:
                    continue
                return port

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def _find_browser_exe(self, chrome_path=None, browser_backend="gologin"):
        if str(browser_backend or "").strip().lower() == "local_chrome":
            return require_chrome_exe(chrome_path)
        return require_orbita_browser_exe(chrome_path)

    def _find_external_profile_process(self, profile_dir):
        profile_dir = self._normalize_profile_dir(profile_dir)
        if not profile_dir:
            return None
        try:
            import psutil
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                try:
                    name = (proc.info.get("name") or "").lower()
                    if name not in {"chrome.exe", "orbita-browser.exe", "chromium.exe", "firefox.exe"}:
                        continue
                    cmdline = proc.info.get("cmdline") or []
                    for arg in cmdline:
                        arg_norm = str(arg).lower().replace("\\", "/")
                        if arg_norm.startswith("--user-data-dir=") and profile_dir in arg_norm:
                            return proc.info.get("pid")
                except Exception:
                    continue
        except Exception:
            return None
        return None

    def _kill_process_tree(self, process):
        if not process:
            return
        self._kill_pid_tree(getattr(process, "pid", None))

    def _kill_pid_tree(self, pid):
        if not pid:
            return
        try:
            import psutil
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
            for child in children:
                try:
                    child.terminate()
                except Exception:
                    pass
            try:
                parent.terminate()
            except Exception:
                pass
            _, alive = psutil.wait_procs([parent] + children, timeout=5)
            for proc in alive:
                try:
                    proc.kill()
                except Exception:
                    pass
        except Exception:
            try:
                import os
                os.kill(pid, 9)
            except Exception:
                pass

    def launch_browser(
        self,
        profile_id,
        profile_dir,
        width=960,
        height=680,
        proxy_server="",
        extra_args=None,
        chrome_path=None,
        browser_backend="gologin",
        creationflags=0x08000000,
        cwd=None,
    ):
        """Launch one Chromium process for one profile and return (process, cdp_port)."""
        profile_key = self._normalize_profile_id(profile_id, profile_dir)
        profile_dir = os.path.abspath(profile_dir or "")
        if not profile_dir:
            raise ValueError("Thiếu thư mục profile để mở trình duyệt.")

        with self._lock:
            active = self.active_profiles.get(profile_key)
            if active and self._process_alive(active.get("process")):
                raise RuntimeError(f"Profile {profile_key} đang được sử dụng bởi một tiến trình khác!")
            if active:
                self.active_profiles.pop(profile_key, None)

            profile_dir_key = self._normalize_profile_dir(profile_dir)
            for other_key, other_active in list(self.active_profiles.items()):
                other_dir = self._normalize_profile_dir(other_active.get("profile_dir", ""))
                if other_dir == profile_dir_key and self._process_alive(other_active.get("process")):
                    raise RuntimeError(f"Profile {profile_key} đang được sử dụng bởi một tiến trình khác!")
                if other_dir == profile_dir_key:
                    self.active_profiles.pop(other_key, None)

            existing_pid = self._find_external_profile_process(profile_dir)
            if existing_pid:
                raise RuntimeError(f"Profile {profile_key} đang được sử dụng bởi một tiến trình khác!")

            os.makedirs(profile_dir, exist_ok=True)
            port = self._find_free_port()
            exe_path = self._find_browser_exe(chrome_path, browser_backend=browser_backend)

            width = int(width or 960)
            height = int(height or 680)
            cmd = [
                exe_path,
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile_dir}",
                f"--window-size={width},{height}",
                "--window-position=0,0",
                "--lang=vi-VN",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-encryption",
                "--password-store=basic",
                "--use-mock-keychain",
                "--disable-sync",
                "--disable-background-networking",
                "--disable-default-apps",
                "--disable-background-timer-throttling",
                "--disable-session-crashed-bubble",
                "--hide-crash-restore-bubble",
                "--disable-infobars",
                "--disable-popup-blocking",
                "--disable-blink-features=AutomationControlled",
                "--mute-audio",
            ]
            if proxy_server:
                cmd.append(f"--proxy-server={proxy_server}")
            for arg in extra_args or []:
                if arg and arg not in cmd:
                    cmd.append(str(arg))

            try:
                process = subprocess.Popen(cmd, creationflags=creationflags, cwd=cwd)
            except Exception:
                self.active_profiles.pop(profile_key, None)
                raise

            self.active_profiles[profile_key] = {
                "process": process,
                "port": port,
                "profile_dir": profile_dir,
                "started_at": time.time(),
                "cmd": cmd,
            }
            return process, port

    def close_browser(self, profile_id, profile_dir=""):
        profile_key = self._normalize_profile_id(profile_id, profile_dir)
        with self._lock:
            active = self.active_profiles.pop(profile_key, None)
            profile_dir = profile_dir or (active or {}).get("profile_dir", "")
            profile_dir_key = self._normalize_profile_dir(profile_dir)
            for other_key, other_active in list(self.active_profiles.items()):
                if self._normalize_profile_dir(other_active.get("profile_dir", "")) == profile_dir_key:
                    if not active:
                        active = other_active
                    self.active_profiles.pop(other_key, None)
        if active:
            self._kill_process_tree(active.get("process"))
        external_pid = self._find_external_profile_process(profile_dir)
        if external_pid:
            self._kill_pid_tree(external_pid)

    def get_active(self, profile_id, profile_dir=""):
        profile_key = self._normalize_profile_id(profile_id, profile_dir)
        with self._lock:
            return dict(self.active_profiles.get(profile_key) or {})

    def close_all(self):
        with self._lock:
            keys = list(self.active_profiles.keys())
        for key in keys:
            self.close_browser(key)
