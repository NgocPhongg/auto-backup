from __future__ import annotations

import argparse
import sys
from pathlib import Path


FORBIDDEN_TOP_LEVEL_NAMES = {
    ".codex_tmp",
    "accounts_data.json",
    "build",
    "dist",
    "email_accounts.json",
    "feed_settings.json",
    "gologin_settings.json",
    "local_chrome_profiles",
    "profiles",
    "projects.json",
    "release",
    "stealth_firefox_profiles",
    "tmp_release_stage",
}

FORBIDDEN_DIR_NAMES_ANYWHERE = {
    "__pycache__",
    ".codex_tmp",
    "local_chrome_profiles",
    "stealth_firefox_profiles",
}

REQUIRED_ENTRY_GROUPS = (
    ("AutoBackup.exe", [Path("AutoBackup.exe")]),
    ("EDIT_1", [Path("EDIT_1/main.py")]),
    (
        "Creator Now",
        [
            Path("Creator Now Cut 14112025/creator_now_studio.py"),
            Path("Creator Now Cut/creator_now_studio.py"),
        ],
    ),
)

RUNTIME_NOISE_PATHS = (
    Path("chrome-win64/debug.log"),
    Path("chrome-win64/chrome_debug.log"),
    Path("chrome-win64/First Run"),
    Path("stealth_firefox/debug.log"),
    Path("stealth_firefox/First Run"),
)


def _first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def _runtime_expectations(source_root: Path | None) -> tuple[list[tuple[str, list[Path]]], list[str]]:
    required: list[tuple[str, list[Path]]] = []
    warnings: list[str] = []

    if source_root is None:
        warnings.append("Khong co source-root; bo qua kiem tra runtime portable.")
        return required, warnings

    chrome_source = source_root / "chrome-win64" / "chrome.exe"
    if chrome_source.is_file():
        required.append(("chrome-win64", [Path("chrome-win64/chrome.exe")]))
    else:
        warnings.append("Source khong co chrome-win64/chrome.exe; release se fallback Chrome he thong neu may dich co.")

    stealth_candidates = [
        source_root / "stealth_firefox" / "firefox.exe",
        source_root / "stealth_firefox" / "firefox" / "firefox.exe",
    ]
    if _first_existing(stealth_candidates):
        required.append(
            (
                "stealth_firefox",
                [
                    Path("stealth_firefox/firefox.exe"),
                    Path("stealth_firefox/firefox/firefox.exe"),
                ],
            )
        )
    else:
        warnings.append("Source khong co stealth_firefox runtime; backend nay se can bo sung thu cong tren may dich.")

    return required, warnings


def audit_bundle(app_dir: Path, source_root: Path | None) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if not app_dir.exists() or not app_dir.is_dir():
        return [f"Khong tim thay thu muc app dist: {app_dir}"], warnings

    for name in sorted(FORBIDDEN_TOP_LEVEL_NAMES):
        candidate = app_dir / name
        if candidate.exists():
            errors.append(f"Bundle dang chua du lieu/thu muc khong nen phat hanh: {candidate}")

    for label, relative_paths in REQUIRED_ENTRY_GROUPS:
        resolved = _first_existing([app_dir / rel for rel in relative_paths])
        if resolved is None:
            paths_text = ", ".join(str(path).replace("\\", "/") for path in relative_paths)
            errors.append(f"Thieu thanh phan bat buoc trong release ({label}): {paths_text}")

    runtime_requirements, runtime_warnings = _runtime_expectations(source_root)
    warnings.extend(runtime_warnings)
    for label, relative_paths in runtime_requirements:
        resolved = _first_existing([app_dir / rel for rel in relative_paths])
        if resolved is None:
            paths_text = ", ".join(str(path).replace("\\", "/") for path in relative_paths)
            errors.append(f"Source co runtime {label} nhung bundle dang thieu: {paths_text}")

    for relative_path in RUNTIME_NOISE_PATHS:
        candidate = app_dir / relative_path
        if candidate.exists():
            errors.append(f"Bundle dang chua file runtime tam/da tung chay: {candidate}")

    for path in app_dir.rglob("*"):
        if path.is_dir() and path.name in FORBIDDEN_DIR_NAMES_ANYWHERE:
            errors.append(f"Bundle dang chua thu muc tam/data khong nen dong goi: {path}")

    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit AutoBackup release bundle before zipping.")
    parser.add_argument("app_dir", help="Path to built app directory, e.g. dist/AutoBackup")
    parser.add_argument("--source-root", help="Workspace root to compare optional runtime presence")
    args = parser.parse_args(argv)

    app_dir = Path(args.app_dir).resolve()
    source_root = Path(args.source_root).resolve() if args.source_root else None

    errors, warnings = audit_bundle(app_dir, source_root)

    for warning in warnings:
        print(f"[WARN] {warning}")

    if errors:
        for error in errors:
            print(f"[FAIL] {error}")
        return 1

    print(f"[OK] release bundle clean: {app_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
