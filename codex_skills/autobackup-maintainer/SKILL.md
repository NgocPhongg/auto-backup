---
name: autobackup-maintainer
description: Maintain, debug, refactor, and release the AutoBackup project in D:/auto - backup. Use when working on this Python/PyQt app, especially tasks involving GoLogin/Orbita profile state, proxy sync, upload dashboard, CDP workers, EDIT_1 video rendering, release packaging, project cleanup, or creating tests/checklists for this codebase.
---

# AutoBackup Maintainer

## Core Workflow

1. Start in `D:/auto - backup`.
2. Read `references/project-map.md` before changing code. If this repo-local skill has not been installed, read root `PROJECT_MAP.md`.
3. Identify the domain: GoLogin/proxy, Local Chrome, upload, EDIT_1, GUI/data, or release.
4. Read only the relevant reference in this skill:
   - Project map: `references/project-map.md`
   - Release checklist: `references/release-checklist.md`
   - GoLogin state plan: `references/gologin-state-sync.md`
   - GoLogin/proxy: `references/gologin.md`
   - Local Chrome backend: `references/local-chrome.md`
   - EDIT_1/render: `references/edit1.md`
   - Release/build: `references/release.md`
   - General debugging/checks: `references/debugging.md`
5. Use `rg` to trace call sites before editing.
6. Keep edits scoped to the owning module.
7. Run syntax checks and any targeted smoke checks before final response.

## Project Rules

- Treat GoLogin cloud/profile as the real state for login, fingerprint, and proxy.
- Treat JSON as control/cache only. App runtime data is normally in `%APPDATA%/AutoBackup`.
- Do not silently push stale JSON proxy into GoLogin when opening a profile.
- Do not create local GoLogin profile dirs as the real state when a profile has a real GoLogin ID.
- For Local Chrome, one shared `chrome-win64/chrome.exe` is used; each profile owns a stable `%APPDATA%/AutoBackup/local_chrome_profiles/<browser_id>` user-data dir.
- For upload account mapping, prefer GoLogin profile ID/source row over display name.
- For EDIT_1 layout changes, update preview and render command behavior together.
- Avoid broad refactors while fixing production bugs.

## Useful Scripts

- `scripts/check_project.py`: run syntax checks for core files and EDIT_1 files from a workspace path.

Example:

```powershell
python .\codex_skills\autobackup-maintainer\scripts\check_project.py "D:\auto - backup"
```

## Expected Final Checks

- For Python edits: run AST syntax checks on changed files or `scripts/check_project.py`.
- For GoLogin/proxy edits: verify both `%APPDATA%/AutoBackup/accounts_data.json` and GoLogin cloud/profile behavior if possible.
- For release edits: inspect `scripts/build_release.ps1` and `AutoBackup.spec`; build only when explicitly needed.
