# Release Reference

## Key files

- `scripts/build_release.ps1`: release build script.
- `AutoBackup.spec`: PyInstaller config.
- `app_version.py`: app version. Must match build argument.
- `PROJECT_MAP.md`: project layout.
- `RELEASE_CHECKLIST.md`: release/test checklist.

## Build behavior

`scripts/build_release.ps1`:

- reads version from `app_version.py`.
- runs syntax + smoke preflight by default before build.
- runs PyInstaller with `AutoBackup.spec`.
- stages `EDIT_1` into the dist app.
- stages `Creator Now Cut` or `Creator Now Cut 14112025` into the dist app.
- stages `chrome-win64` and `stealth_firefox` runtimes when they exist in the workspace.
- runs `scripts/audit_release_bundle.py` before zipping to catch user data or missing portable runtime files.
- creates zip in `release/`.

## Rules

- Do not build release unless the user asks or validation requires it.
- Default build is for a clean app zip only, not user data/session migration.
- When changing staged files, update `Stage-ExternalTools` in `scripts/build_release.ps1`.
- Do not treat `tmp_release_stage/` as source of truth.

## Commands

Syntax first:

```powershell
python C:\Users\ngocp\.codex\skills\autobackup-maintainer\scripts\check_project.py "D:\auto - backup"
```

Build:

```powershell
.\scripts\build_release.ps1 <version>
```

Quick rebuild without preflight:

```powershell
.\scripts\build_release.ps1 <version> -SkipPreflight
```
