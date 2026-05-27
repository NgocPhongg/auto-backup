import sys

with open('cdp_worker.py', 'r', encoding='utf-8') as f:
    content = f.read()

target = '''            for hwnd, wpid in hwnds:
                if wpid in all_pids:
                    target_hwnd = hwnd
                    break
                try:
                    import psutil
                    proc = psutil.Process(wpid)
                        name = (proc.name() or "").lower()
                        if name in {"chrome.exe", "orbita-browser.exe", "chromium.exe"}:
                            created_at = proc.create_time()
                            if not self._launch_started_at or created_at >= self._launch_started_at - 5:
                                fallback_candidates.append((created_at, hwnd, wpid))
                    except Exception:
                        pass'''

replacement = '''            for hwnd, wpid in hwnds:
                if wpid in all_pids:
                    target_hwnd = hwnd
                    break
                try:
                    import psutil
                    proc = psutil.Process(wpid)
                    name = (proc.name() or "").lower()
                    if name in {"chrome.exe", "orbita-browser.exe", "chromium.exe"}:
                        created_at = proc.create_time()
                        if not self._launch_started_at or created_at >= self._launch_started_at - 5:
                            fallback_candidates.append((created_at, hwnd, wpid))
                except Exception:
                    pass'''

if target in content:
    content = content.replace(target, replacement)
    with open('cdp_worker.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Fixed indentation')
else:
    print('Target not found')
