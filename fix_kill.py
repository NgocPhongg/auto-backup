import sys

with open('cdp_worker.py', 'r', encoding='utf-8') as f:
    content = f.read()

kill_func = '''    def _kill_stale_port(self):
        try:
            import psutil
            port_str = str(self._debug_port)
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    name = (proc.info.get('name') or '').lower()
                    if name not in {"chrome.exe", "orbita-browser.exe", "chromium.exe"}:
                        continue
                    cmdline = proc.info.get('cmdline') or []
                    # Kill if same port OR same profile dir
                    has_port = any(f"--remote-debugging-port={port_str}" in c for c in cmdline)
                    profile_dir_str = str(self._profile_dir).replace('\\\\', '/')
                    has_profile = False
                    for c in cmdline:
                        c_norm = str(c).replace('\\\\', '/')
                        if "--user-data-dir=" in c_norm and profile_dir_str.lower() in c_norm.lower():
                            has_profile = True
                            break
                    if has_port or has_profile:
                        proc.kill()
                except Exception:
                    pass
        except Exception:
            pass'''

old_func_start = content.find('    def _kill_stale_port(self):')
old_func_end = content.find('    def _find_pids_by_debug_port(self, port):')

if old_func_start != -1 and old_func_end != -1:
    content = content[:old_func_start] + kill_func + '\n\n' + content[old_func_end:]
    with open('cdp_worker.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Replaced _kill_stale_port successfully')
else:
    print('Could not find _kill_stale_port')
