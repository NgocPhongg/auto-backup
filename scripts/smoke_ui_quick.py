from __future__ import annotations

from pathlib import Path
import sys
import traceback

from PyQt5.QtCore import QCoreApplication, Qt

QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)

from PyQt5.QtWebEngineWidgets import QWebEngineView  # noqa: F401
from PyQt5.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _print_ok(label: str) -> None:
    print(f"[OK] {label}")


def _print_fail(label: str, exc: BaseException) -> None:
    print(f"[FAIL] {label}: {exc}")
    traceback.print_exc()


def main() -> int:
    full_main = "--full-main" in sys.argv
    created_widgets = []

    try:
        import main_gui
        import automation_dashboard
        import upload_dashboard

        _print_ok("import main_gui")
        _print_ok("import automation_dashboard")
        _print_ok("import upload_dashboard")

        app = QApplication.instance() or QApplication([])

        created_widgets.append(
            upload_dashboard.UploadDashboard(parent=None, video_tasks=[])
        )
        _print_ok("init UploadDashboard(video_tasks=[])")

        created_widgets.append(
            automation_dashboard.AutomationDashboard(
                parent=None,
                accounts_data=[],
                project_name="Smoke Test",
                dashboard_key="smoke-test",
            )
        )
        _print_ok("init AutomationDashboard(accounts_data=[])")

        if full_main:
            created_widgets.append(main_gui.SSMAToolGUI())
            _print_ok("init SSMAToolGUI()")

        app.processEvents()
        return 0
    except Exception as exc:
        _print_fail("ui smoke", exc)
        return 1
    finally:
        app = QApplication.instance()
        for widget in reversed(created_widgets):
            try:
                widget.close()
            except Exception:
                pass
            try:
                widget.deleteLater()
            except Exception:
                pass
        if app is not None:
            try:
                app.processEvents()
            except Exception:
                pass
            try:
                app.quit()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
