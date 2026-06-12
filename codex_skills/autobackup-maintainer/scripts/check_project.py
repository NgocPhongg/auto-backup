from __future__ import annotations

import ast
import sys
from pathlib import Path


CORE_FILES = [
    "main_gui.py",
    "automation_dashboard.py",
    "cdp_worker.py",
    "upload_worker.py",
    "upload_dashboard.py",
    "video_table_manager.py",
    "gologin_proxy_check.py",
    "gologin_config.py",
    "gologin_profile_utils.py",
    "browser_backend_utils.py",
    "local_proxy.py",
    "stealth_firefox_worker.py",
    "proxy_utils.py",
    "app_paths.py",
    "add_profile_dialog.py",
    "add_multiple_dialog.py",
    "account_selector_dialog.py",
    "profile_editor.py",
    "scheduler_dialog.py",
    "title_editor_dialog.py",
]


def parse_file(path: Path) -> None:
    ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def main() -> int:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
    missing: list[str] = []
    checked = 0

    for relative in CORE_FILES:
        path = root / relative
        if not path.exists():
            missing.append(relative)
            continue
        parse_file(path)
        checked += 1

    edit_dir = root / "EDIT_1"
    if edit_dir.exists():
        for path in sorted(edit_dir.glob("*.py")):
            parse_file(path)
            checked += 1
    else:
        missing.append("EDIT_1/*.py")

    print(f"syntax ok: {checked} file(s)")
    if missing:
        print("missing optional/expected file(s):")
        for item in missing:
            print(f"- {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
