# GoLogin And Proxy Reference

## State model

- GoLogin cloud/profile is the real state for browser profile, login session, fingerprint, and proxy.
- `%APPDATA%/AutoBackup/accounts_data.json` stores mapping/control data for the tool.
- Root `accounts_data.json` is legacy/migration data and may be stale.
- The tool must not silently sync stale JSON proxy into GoLogin during browser launch.

## Key files

- `gologin_proxy_check.py`: convert GoLogin profile proxy data to connection data; validate, set, and clear profile proxy.
- `cdp_worker.py`: opens GoLogin/Orbita through SDK for automation/preview.
- `upload_worker.py`: opens GoLogin/Orbita for uploads.
- `main_gui.py`: add/edit profile dialog handling and profile proxy sync on save.
- `automation_dashboard.py`: receives worker profile updates and clears stale proxy cache/status.
- `gologin_profile_utils.py`: resolves real GoLogin profile IDs.
- `gologin_config.py`: loads API key and GoLogin cloud settings.

## Proxy rules

- Adding proxy:
  - Validate proxy first.
  - Save to GoLogin cloud with `PATCH /browser/{profileId}/proxy`.
  - Update JSON/table cache only after GoLogin sync succeeds.
- Clearing proxy:
  - Send payload with `mode: none`.
  - Clear `profile_data["proxy"]`, table column `3`, `proxy_type`, and `gologin_proxy_synced`.
- Opening browser:
  - Read proxy from `gl.getProfile()`.
  - If GoLogin cloud says no proxy, emit empty proxy data back to dashboard/tool.
  - Add `--no-proxy-server` when no proxy exists to avoid stale Orbita Preferences proxy.

## Common failure modes

- App shows no proxy but browser still uses proxy:
  - Check `%APPDATA%/AutoBackup/accounts_data.json`, not root JSON.
  - Check GoLogin cloud `proxy.mode`.
  - If both are empty/none, stale Orbita Preferences may be the cause; use `--no-proxy-server`.
- API 404 on proxy sync:
  - Check endpoint method/path. Current supported path is `PATCH /browser/{profileId}/proxy`.
- Proxy error hidden by browser close:
  - Dashboard should not overwrite red proxy errors with generic closed status.

## Commands

Find proxy code:

```powershell
rg -n "proxy|gologin_proxy_synced|set_profile_proxy|clear_profile_proxy|validate_profile_proxy" main_gui.py cdp_worker.py upload_worker.py automation_dashboard.py gologin_proxy_check.py
```

Check AppData account entry:

```powershell
$path = Join-Path $env:APPDATA "AutoBackup\accounts_data.json"
rg -n -C 20 "PROFILE_ID_OR_NAME" $path
```
