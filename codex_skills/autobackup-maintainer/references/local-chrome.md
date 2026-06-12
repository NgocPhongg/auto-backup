# Local Chrome Backend Plan

## Goal

Add a separate `local_chrome` browser backend beside the existing GoLogin backend.

Use case: user may keep GoLogin profiles/API for anti-detect work, but can switch selected profiles to local Chrome when GoLogin API is unavailable or not needed.

## State model

Do not treat `1 profile = 1 chrome.exe`.

Correct model:

- One shared Chrome binary for the whole app.
- One stable JSON profile entry per account.
- One stable `browser_id` per local Chrome profile.
- One persistent Chrome `user-data-dir` folder per `browser_id`.

Example JSON for a local profile:

```json
{
  "ten_ho_so": "Acc US 01",
  "browser_backend": "local_chrome",
  "browser_id": "local_chrome:acc_us_01_20260610_a1b2c3",
  "gologin_profile_id": "",
  "proxy": "",
  "proxy_type": "http",
  "country": "US"
}
```

Example disk state:

```text
<app_root>/chrome-win64/chrome.exe
%APPDATA%/AutoBackup/local_chrome_profiles/acc_us_01_20260610_a1b2c3/
```

The `local_chrome_profiles/<id>/` folder is the real local browser state. It stores cookies, History, Local Storage, Preferences, cache, extensions, and login sessions like a normal Chrome profile.

## Chrome binary policy

Default lookup order:

1. `<app_root>/chrome-win64/chrome.exe`
2. `<app_root>/browser/chrome.exe` if added later
3. Installed Chrome as fallback only:
   - `C:/Program Files/Google/Chrome/Application/chrome.exe`
   - `C:/Program Files (x86)/Google/Chrome/Application/chrome.exe`

Reason: bundled/portable `chrome-win64` is more stable for automation, release packaging, and CDP behavior. System Chrome can auto-update and break flows.

Do not store profile data inside `chrome-win64/`. That folder is program binary only.

## Profile lifecycle flow

### Create profile

1. User chooses browser type: `GoLogin` or `Local Chrome`.
2. If `Local Chrome`:
   - Generate stable `browser_id` with prefix `local_chrome:`.
   - Save `browser_backend = "local_chrome"`.
   - Clear `gologin_profile_id`.
   - Create or lazily create `%APPDATA%/AutoBackup/local_chrome_profiles/<safe_id>/`.
3. `ten_ho_so` is display name only. It must not be the permanent storage key.

### Open profile first time

Launch shared Chrome binary:

```text
chrome.exe --user-data-dir=<profile_dir> --remote-debugging-port=<free_port> --no-first-run --no-default-browser-check
```

Chrome will create its own internal files under `<profile_dir>`:

- `Default/`
- `Cookies`
- `History`
- `Local Storage/`
- `Preferences`
- cache/session data

### User logs in

User logs in manually. Chrome writes the state into the profile directory. Closing and reopening the app must reuse the same `browser_id` and `user-data-dir`, so login/history remain.

### Reopen profile

Read `browser_backend` and `browser_id` from JSON, resolve the same profile directory, launch Chrome with the same `--user-data-dir`.

Rule:

```text
same browser_id -> same user-data-dir -> same browser state/session
```

### Rename profile

Changing `ten_ho_so` must not change `browser_id` or profile directory. Otherwise the user can appear logged out because a new empty folder is opened.

### Delete profile

When deleting a local Chrome profile, ask whether to:

- Remove only JSON/tool entry, keep local browser data.
- Remove JSON/tool entry and delete local Chrome data folder.

## Backend behavior rules

### GoLogin backend

- `browser_backend` missing or empty defaults to `gologin` for backward compatibility.
- GoLogin cloud/profile remains the real state for GoLogin sessions, fingerprint, and proxy.
- Use existing GoLogin SDK launch path.
- Keep existing proxy sync/clear rules.

### Local Chrome backend

- Never call GoLogin SDK/API for this profile.
- Do not require `gologin_profile_id`.
- Use `browser_id` as the real local browser key.
- Upload/dashboard/CDP should use an upload/account key like `local_chrome:<browser_id>`.
- Disable or hide GoLogin-only actions:
  - refresh fingerprint
  - GoLogin proxy sync/clear
  - GoLogin cloud proxy validation

## Risk cases to guard

1. Duplicate profile names:
   - Do not map storage by name. Use stable `browser_id`.

2. Rename profile:
   - Do not change local Chrome directory when display name changes.

3. Opening the same profile twice:
   - Chrome may lock the same `user-data-dir`.
   - Prefer profile-level lock or attach to already running CDP session.

4. Debug port collision:
   - Always allocate a free port for each process.

5. Proxy with username/password:
   - Chrome native command line handles simple host/port better than authenticated proxies.
   - Authenticated proxy may need an extension or local proxy bridge.
   - Warn user if proxy auth is not supported in first version.

6. Fingerprint expectations:
   - Local Chrome is not GoLogin/anti-detect.
   - Fingerprint, timezone, WebGL, canvas, and geolocation masking are weaker or absent.

7. Release/backup:
   - Backup must include `%APPDATA%/AutoBackup/local_chrome_profiles/` if user wants to preserve local sessions.
   - JSON alone is not enough to restore logged-in local Chrome state.

## Implementation checkpoints

1. Add path helpers in `app_paths.py`:
   - `find_chrome_exe()` / `require_chrome_exe()`
   - `local_chrome_profiles_root()`
   - `local_chrome_profile_dir(browser_id)`

2. Add UI option in `add_profile_dialog.py`:
   - Browser type combo: GoLogin / Local Chrome
   - Save `browser_backend`.
   - For new local profiles, generate stable `browser_id`.

3. Add backend routing in `cdp_worker.py`:
   - If backend is `gologin`, keep current SDK path.
   - If backend is `local_chrome`, launch Chrome with `--user-data-dir` and CDP port.

4. Update `main_gui.py`:
   - Preserve `browser_backend` and `browser_id` in table/data sync.
   - Make backend visible in UI if practical.
   - Do not run GoLogin-only menu actions for local profiles.

5. Update upload flow:
   - `upload_worker.py` should not require GoLogin ID for local Chrome.
   - Use `upload_profile_key = local_chrome:<browser_id>`.
   - Ensure upload dashboard grouping does not collide with GoLogin IDs or display names.

6. Test:
   - Create local profile, open Chrome, login, close, reopen, confirm login/history remain.
   - Rename profile and confirm state remains.
   - Open GoLogin profile and confirm existing behavior unchanged.
   - Upload with GoLogin profile unchanged.
   - Upload/local dashboard with local profile reaches CDP without GoLogin ID.
