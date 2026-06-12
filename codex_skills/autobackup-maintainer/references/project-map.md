# AutoBackup Project Map

## Muc tieu

Tai lieu nay la ban do nhanh de doc/sua project theo dung khu vuc. Uu tien cap nhat file nay khi them module lon, doi luong GoLogin, upload, EDIT_1 hoac build release.

## Entry points

- `main_gui.py`: GUI chinh, menu, bang tai khoan, tao/sua profile, mo dashboard, luu `accounts_data.json`.
- `automation_dashboard.py`: bang theo doi automation/profile preview, nhung browser, nhan update tu `CDPWorker`.
- `upload_dashboard.py`: bang theo doi upload video.
- `EDIT_1/main.py`: app render/chinh video phu.
- `scripts/build_release.ps1`: build PyInstaller va dong goi zip release.

## Codex skill bundle

- `codex_skills/autobackup-maintainer/SKILL.md`: skill repo-local theo cau truc Codex.
- `codex_skills/autobackup-maintainer/references/`: ban gom tai lieu `.md` cho project, release, GoLogin, EDIT_1 va debugging.
- `codex_skills/autobackup-maintainer/scripts/check_project.py`: syntax smoke check cho core files va EDIT_1.
- `codex_skills/autobackup-maintainer/agents/openai.yaml`: metadata UI cho skill.

## Du lieu va cau hinh

- `app_paths.py`: quy tac duong dan runtime. Du lieu that cua app nam trong `%APPDATA%/AutoBackup`, khong phai JSON o root repo sau khi app da migrate.
- `%APPDATA%/AutoBackup/accounts_data.json`: du lieu tai khoan dang duoc app release/dev su dung.
- `accounts_data.json`: file legacy/root, chi dung de migrate lan dau neu AppData chua co data.
- `gologin_config.py`: doc/ghi GoLogin API key va cau hinh cloud.
- `app_version.py`: version bat buoc khop tham so build release.

## GoLogin, Local Chrome va browser state

- `gologin_profile_utils.py`: chuan hoa GoLogin profile ID that.
- `gologin_proxy_check.py`: doc proxy tu GoLogin profile, validate proxy, set/clear proxy bang GoLogin API.
- `cdp_worker.py`: mo GoLogin/Orbita bang SDK, connect CDP, chay automation va preview.
- `upload_worker.py`: mo GoLogin/Orbita de upload video len TikTok.
- `browser_controller.py`: wrapper GoLogin/Playwright cu, hien khong phai luong chinh.
- `browser_manager.py`, `local_proxy.py`, `proxy_auth_ext/`, `proxy_ext_0/`: luong browser/proxy local legacy hoac phu tro.
- `codex_skills/autobackup-maintainer/references/local-chrome.md`: ke hoach backend Local Chrome voi `chrome-win64` dung chung va user-data-dir rieng theo `browser_id`.

Quy tac hien tai:

- GoLogin cloud/profile la state that cho login, fingerprint va proxy.
- JSON chi la cache/mapping dieu khien.
- Khi profile co GoLogin ID that, khong tao local profile dir lam state chinh.
- Khong tu dong day proxy cache trong JSON len GoLogin khi mo browser.
- Khi GoLogin cloud bao khong co proxy, worker them `--no-proxy-server` de tranh proxy cu con trong Orbita Preferences.
- Local Chrome neu trien khai se la backend rieng: khong dung GoLogin API, khong co fingerprint GoLogin, va moi profile phai giu mot `%APPDATA%/AutoBackup/local_chrome_profiles/<browser_id>` user-data-dir rieng.

## Upload

- `video_table_manager.py`: gan video voi account upload, map theo GoLogin profile key/source row.
- `upload_dashboard.py`: hien thi task upload va preview browser.
- `upload_worker.py`: worker upload, group task theo GoLogin profile key, validate proxy tu GoLogin profile.
- `account_selector_dialog.py`: chon account upload, tra metadata account.

## EDIT_1

- `EDIT_1/ui_main.py`: UI chinh cua tool render video.
- `EDIT_1/video_engine.py`: tao FFmpeg command va render.
- `EDIT_1/preview_engine.py`: preview frame/overlay.
- `EDIT_1/overlay_layout.py`: tinh layout overlay/text/background.
- `EDIT_1/queue_manager.py`: quan ly queue render.
- `EDIT_1/render_engine.py`, `EDIT_1/scene_detector.py`: helper render/scene.
- `EDIT_1/style.qss`: style.

Quy tac sua EDIT_1:

- Sua preview va render cung nhau neu thay doi layout.
- Neu them option UI moi, can check command FFmpeg sinh ra trong `video_engine.py`.
- Can test syntax rieng folder `EDIT_1`.

## Release

- `AutoBackup.spec`: cau hinh PyInstaller.
- `scripts/build_release.ps1`: build app, chay preflight syntax/smoke, stage `EDIT_1`, `Creator Now Cut`, runtime portable (`chrome-win64`/`stealth_firefox` neu co), audit bundle roi zip vao `release/`.
- `RELEASE_GUIDE.md`: ghi chu release cu.
- `tmp_release_stage/`: staging tam/legacy, khong sua lam source chinh.
- `dist/`, `build/`, `release/`: output build.

## File nen coi la legacy/tam

- `fix_*.py`, `patch_*.py`, `test_login*.py`, `test_poc.py`, `playwright_mre.py`, `_test_func.py`: script sua/test tam. Khong dung lam source chinh neu chua duoc xac nhan.
- `main_gui.py.*.bak`: backup cu.
- `gologin_worker.py`, `adspower_worker.py`: worker cu/legacy; doc khi can tham chieu hanh vi cu.

## Quy trinh doc code truoc khi sua

1. Xac dinh domain: GUI, GoLogin, upload, EDIT_1, release hay data.
2. Doc file map nay va plan domain lien quan.
3. Dung `rg` de tim luong goi, khong sua theo suy doan.
4. Sua nho, uu tien module dang so huu logic.
5. Chay syntax check cac file lien quan.
6. Neu dung GoLogin/proxy, test ca AppData va GoLogin cloud/profile.
