# Debugging Reference

## First steps

1. Run `git status --short` and do not revert unrelated user changes.
2. Read `PROJECT_MAP.md`.
3. Use `rg` to find ownership and call sites.
4. Reproduce with the smallest check possible.
5. Patch narrowly and run syntax checks.

## Core syntax command

```powershell
python C:\Users\ngocp\.codex\skills\autobackup-maintainer\scripts\check_project.py "D:\auto - backup"
```

## Data location caution

The app migrates runtime JSON to `%APPDATA%/AutoBackup`. Root JSON files can be stale. When debugging user-reported current app behavior, inspect AppData first.

## File hygiene

- Keep docs in root or a docs folder.
- Keep tests in `tests/` when they become permanent.
- Keep scratch outputs in `.codex_tmp/`.
- Avoid adding new `fix_*.py` or `patch_*.py` scripts unless temporary and explicitly named for cleanup.

## Good review targets

- `main_gui.py`: save/load behavior, profile dialogs, release-facing UX.
- `cdp_worker.py`: browser launch, CDP automation, GoLogin state.
- `upload_worker.py`: upload execution, GoLogin profile grouping, proxy validation.
- `automation_dashboard.py`: status propagation and preview lifecycle.
- `EDIT_1/video_engine.py`: FFmpeg command construction.
