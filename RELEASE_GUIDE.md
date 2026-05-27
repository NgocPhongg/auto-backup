# AutoBackup Release Guide

## Build local

Run from the project root:

```powershell
.\scripts\build_release.ps1 1.0.0
```

Output:

```text
release\AutoBackup_v1.0.0.zip
```

## Upload GitHub Release

1. Open GitHub Releases.
2. Tag: `v1.0.0`
3. Title: `Auto Backup Ban chuan v1.0`
4. Attach `release\AutoBackup_v1.0.0.zip`
5. Publish release.

## User install

1. Download `AutoBackup_v1.0.0.zip`.
2. Extract the zip.
3. Run `AutoBackup.exe`.

Runtime data is stored in `%APPDATA%\AutoBackup`, so updating the app does not overwrite user data.
