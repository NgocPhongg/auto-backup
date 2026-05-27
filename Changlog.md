# Changlog

## 2026-05-24 - Chuan hoa GoLogin, dashboard va Upload hang cho

### Muc tieu
- Giu fingerprint GoLogin/Orbita that hon khi chay tool.
- Dong bo cach nhung trinh duyet cua Upload hang cho voi Bang theo doi.
- Giam rui ro UI bi treo/crash khi dong man Upload luc worker con chay.

### Da lam GoLogin / fingerprint
- Trong `browser_controller.py`:
  - Don lai helper GoLogin theo huong sach hon.
  - Bo phu thuoc stealth patch trong helper nay.
- Trong `cdp_worker.py`:
  - Dat mac dinh `gologin_passthrough_strict=True`.
  - Khi chay GoLogin strict, khong sync proxy/header/cookie/stealth override vao profile.
  - Giu fingerprint, locale, header cua GoLogin profile lam nguon chinh.
- Trong `main_gui.py`:
  - Truyen co strict GoLogin qua luong mo dashboard/manual-open.

### Da lam Bang theo doi
- Trong `automation_dashboard.py`:
  - Hoan thien cau truc grid/preview cho browser nhung.
  - Cai thien focus chuot/ban phim vao browser nhung bang Win32 focus bridge.
  - Them signal `embed_finished` cho `BrowserPreviewWidget` de man khac co the nhan ket qua nhung browser bang API ro rang.
  - Giu co che resize/sync geometry cua browser nhung trong preview.

### Da lam Upload hang cho
- Trong `upload_dashboard.py`:
  - Chuyen preview Upload sang dung `BrowserPreviewWidget` giong Bang theo doi.
  - Moi nhom account/video dung chung mot preview browser.
  - Nhan ket qua nhung browser qua signal `embed_finished`, khong monkey-patch ham private nua.
  - Sua `closeEvent`: neu worker upload con chay thi gui lenh dung va cho worker ket thuc roi moi dong UI.
- Trong `upload_worker.py`:
  - Them `browser_ready_signal` de worker chi mo GoLogin/Orbita va bao UI nhung browser.
  - Them `notify_embed_result(...)` va `_request_browser_embed_from_ui(...)` de worker cho ket qua nhung tu UI.
  - Xoa luong embed cu trong worker (`_embed_browser_window`, `_focus_embedded_browser`, `_lock_browser_position`).
  - Tat inject `STEALTH_JS` trong luong Upload, giu GoLogin strict/fingerprint profile.
  - Xoa ham `_process_queue` trung ten, chi giu mot luong queue theo account/profile.

### Da lam UI / text / encoding
- Phuc hoi encoding tieng Viet chinh trong `main_gui.py` sau khi bi mojibake.
- Sua mot so chuoi mojibake trong `cdp_worker.py` va cac man lien quan.
- Viet hoa nhieu nut/menu chinh trong UI quan ly, bang dang ky OTP va upload.

### Da kiem tra
- `python -m py_compile upload_worker.py automation_dashboard.py upload_dashboard.py main_gui.py`: OK.
- `python -c "import upload_worker, automation_dashboard, upload_dashboard; print('import ok')"`: OK.
- Compile toan bo file `.py`: OK, con warning khong chan chay o `_test_func.py` ve invalid escape sequence `\s`.

### Can test thuc te
- Mo Upload hang cho voi 1 profile + 1 video de xac nhan Orbita/TikTok Studio render vao khung Upload.
- Test 1 profile co nhieu video de xac nhan dung chung 1 browser va queue upload dung thu tu.
- Test 2-3 profile de xac nhan moi account co preview rieng, khong nham HWND/focus.
- Test dong man Upload khi worker dang chay de xac nhan UI cho worker dung xong moi dong.

## 2026-05-21 - Them chuc nang doi avatar TikTok hang loat

### Muc tieu
- Cho phep gan avatar cho tung profile hoac nhieu profile cung luc.
- Them task `Doi avatar` trong Bang theo doi de doi avatar TikTok bang dung profile GoLogin/Orbita hien co.
- Giu luong backend tach rieng, khong dung API TikTok khong chinh thuc.

### Da lam giao dien
- Trong `add_profile_dialog.py`:
  - Them truong `Avatar TikTok`.
  - Them nut `Chon anh` de chon file `.jpg`, `.jpeg`, `.png`, `.webp`.
  - Luu duong dan vao `profile_data["avatar_path"]`.
- Trong `main_gui.py`:
  - Them menu chuot phai `Chon avatar TikTok` trong nhom `Tinh nang tai khoan`.
  - Ho tro chon nhieu profile roi gan cung mot avatar cho tat ca.
  - Cot `Avt` hien `Co` khi profile da co `avatar_path`.
- Trong `automation_dashboard.py`:
  - Them task `Doi avatar` vao danh sach chuc nang.
  - Nut cai dat task hien thong bao huong dan: avatar duoc chon trong Add/Edit profile hoac menu chuot phai bang account.

### Da lam backend
- Trong `cdp_worker.py`:
  - Them nhanh xu ly khi `selected_features` co `Doi avatar`.
  - Truoc khi chay, worker kiem tra login bang `_ensure_logged_in_for_feature()`.
  - Kiem tra CAPTCHA bang `_wait_captcha_clear_for_action()`.
  - Them `_change_tiktok_avatar()`:
    - validate `avatar_path`;
    - kiem tra file ton tai;
    - chi chap nhan `.jpg`, `.jpeg`, `.png`, `.webp`;
    - mo `https://www.tiktok.com/profile`;
    - click `Edit profile`;
    - tim input upload file bang CDP DOM;
    - goi `DOM.setFileInputFiles` de upload avatar;
    - bam `Apply/Confirm/Done` neu co crop/preview modal;
    - bam `Save/Luu`;
    - emit ket qua ve UI.
  - Them cac helper:
    - `_click_tiktok_button_by_text()`
    - `_find_file_input_node()`
    - `_wait_for_file_input_node()`
    - `_open_tiktok_edit_profile()`
    - `_prepare_avatar_file_input()`
- Trong `automation_dashboard.py`:
  - Luu lai cac field:
    - `avatar_path`
    - `avatar_status`
    - `avatar_updated_at`
    - `avatar_last_error`
  - Cap nhat bang chinh:
    - cot `Avt` = `Co` khi co avatar;
    - cot `Tinh trang` = `Avatar OK` hoac loi avatar.

### Luong su dung
- Gan avatar:
  - Add/Edit profile -> chon `Avatar TikTok`; hoac
  - chon nhieu profile -> chuot phai -> `Chon avatar TikTok`.
- Chay hang loat:
  - Mo `Bang theo doi`.
  - Tick `Doi avatar`.
  - Bam `Chay`.
  - So profile chay dong thoi theo cau hinh `Luong` cua dashboard.

### Da kiem tra
- `python -m py_compile .\cdp_worker.py .\automation_dashboard.py .\main_gui.py .\add_profile_dialog.py`: OK.

### Can test thuc te
- Test 1 profile da login truoc de kiem tra selector TikTok hien tai co khop khong.
- Test profile chua co `avatar_path` de xem bao loi dung.
- Test file avatar khong ton tai / sai dinh dang.
- Test 2-3 profile cung luc de xem queue va trang thai `Avatar OK`/loi co cap nhat dung khong.
- Neu TikTok doi UI, can dieu chinh selector nut `Edit profile`, nut doi anh, hoac nut `Save`.

## 2026-05-21 - Them snapshot kiem tra fingerprint runtime cua Orbita

### Muc tieu
- Kiem tra thong tin browser tool dang chay co khop voi thong tin GoLogin UI hay khong.

### Da lam
- Them `_emit_runtime_fingerprint_snapshot()` trong `cdp_worker.py`.
- Khi worker ket noi CDP, tool doc truc tiep cac gia tri browser-visible:
  - `navigator.userAgent`
  - `navigator.language/languages`
  - timezone
  - `screen.width/height`
  - `window.inner/outer`
  - `devicePixelRatio`
  - `navigator.webdriver`
  - hardware/device memory/plugins
- Status se hien cac dong dang:
  - `FP runtime: Chrome ..., lang ..., tz ..., screen ..., webdriver=...`
  - `FP viewport: inner ..., outer ..., dpr=..., plugins=...`
- Luu snapshot vao `profile_data["gologin_runtime_fingerprint"]` de doi chieu sau.

### Da kiem tra
- `python -m py_compile cdp_worker.py automation_dashboard.py main_gui.py`: OK.
- `python -c "import cdp_worker, automation_dashboard, main_gui; print('import ok')"`: OK.

## 2026-05-21 - Them nut Lam moi van tay GoLogin khong xoa cookie

### Muc tieu
- Lay chuc nang `Lam moi van tay` cua GoLogin vao tool.
- Tach rieng khoi luong `Tai che Profile`, vi luong tai che cu vua doi fingerprint vua xoa cookie/local data.

### Da lam trong `automation_dashboard.py`
- Them `GoLoginFingerprintRefreshWorker` chay nen bang `QThread`.
- Worker goi GoLogin API:
  - `PATCH https://api.gologin.com/browser/fingerprints`
  - payload: `{"browsersIds": [gologin_profile_id]}`
- Khong xoa cookie, khong xoa local profile, khong reset du lieu account.
- Noi nut bieu tuong van tay/chia khoa tren header preview vao `_change_fingerprint()`.
- Khi click nut:
  - kiem tra GoLogin Profile ID;
  - doc API key tu `gologin_config.load_gologin_settings()`;
  - hoi xac nhan truoc khi goi API;
  - neu browser dang mo thi bao ro van tay moi se ap dung o lan mo profile tiep theo;
  - luu `gologin_fingerprint_refreshed_at` vao `profile_data`.

### Da kiem tra
- `python -m py_compile automation_dashboard.py cdp_worker.py main_gui.py`: OK.
- `python -c "import automation_dashboard, cdp_worker, main_gui; print('import ok')"`: OK.

### Luu y su dung
- Nen dong/mo lai profile sau khi lam moi van tay de GoLogin/Orbita ap dung fingerprint moi.
- Khong nen lam moi van tay lien tuc tren account TikTok da co session on dinh.

## 2026-05-21 - Giu nguyen fingerprint GoLogin khi auto-login TikTok

### Nguyen nhan xac dinh lai
- Cung account/profile mo bang GoLogin app dang nhap duoc binh thuong, nen `Maximum number of attempts` khong phai cooldown that cua account.
- Loi chi xuat hien khi chay chuc nang `Dang nhap` trong tool, tuc la nam o lop automation.
- Trong `cdp_worker.py`, truoc login tool dang:
  - inject stealth JS rieng de patch `navigator.webdriver`, `plugins`, `languages`;
  - ep `Accept-Language` va `locale` thanh `en-US`;
  - inject CSS vao trang TikTok;
  - co fallback set input bang JS neu go CDP khong khop.
- Cac thao tac nay co the lam fingerprint GoLogin/Orbita bi sai lech voi profile that. TikTok co the tra `Maximum number of attempts` nhu mot loi risk/session, du manual GoLogin van dang nhap duoc.

### Da lam trong `cdp_worker.py`
- Them `_should_preserve_gologin_fingerprint()`.
- Khi profile chay bang GoLogin SDK:
  - bo qua stealth JS cua tool, de GoLogin tu quan ly fingerprint;
  - khong ep `Accept-Language`/`Emulation.setLocaleOverride`;
  - khong inject CSS focus/outline vao TikTok;
  - khong fallback set email/password bang JS, neu CDP go khong dung thi dung truoc khi submit.
- Giu luong mo thang `https://www.tiktok.com/login/phone-or-email/email` da them truoc do.

### Da kiem tra
- `python -m py_compile cdp_worker.py automation_dashboard.py main_gui.py`: OK.
- `python -c "import cdp_worker, automation_dashboard, main_gui; print('import ok')"`: OK.

### Can test thuc te
- Chay lai cung account/profile bang tool voi chuc nang `Dang nhap`.
- Trong status phai thay:
  - `GoLogin fingerprint mode: bo qua stealth JS cua tool (...)`
  - `GoLogin fingerprint mode: giu header/locale cua profile`
- Neu van bi `Maximum attempts`, buoc tiep theo nen test che do tool chi dien email/password nhung de nguoi dung bam Login tay de tach rieng loi nam o CDP click submit hay o CDP typing.

## 2026-05-21 - Di thang vao TikTok email login khi auto-login

### Nguyen nhan
- Anh hien `Maximum number of attempts reached. Try again later.` la loi TikTok tra ve sau khi submit form login.
- Day la cooldown/limit phia TikTok cho account/IP/profile/session, khong phai do khung preview den.
- Di thang trang login khong go cooldown hien tai, nhung giup bo qua cac buoc click modal va giam nguy co submit lap/nham.

### Da lam trong `cdp_worker.py`
- Sau khi xac dinh chua login va co email/password, tool mo thang:
  - `https://www.tiktok.com/login/phone-or-email/email`
- Them tham so `force_direct_url=True` cho `_do_login_direct()` de chu dong navigate vao trang email-login truoc khi nhap form.
- Giu cac fallback cu ben trong `_do_login_direct()` neu trang login direct load cham/khong hien input.

### Da kiem tra
- `python -m py_compile cdp_worker.py automation_dashboard.py main_gui.py`: OK.
- `python -c "import cdp_worker, automation_dashboard, main_gui; print('import ok')"`: OK.

### Luu y test
- Account dang bi `Maximum number of attempts` co the van phai cho TikTok het cooldown.
- Nen test them voi 1 account chua bi limit de xem luong direct-login co vao form nhanh hon khong.

## 2026-05-21 - Giu Orbita mo khi auto-login TikTok gap Maximum attempts

### Nguyen nhan xac dinh
- Anh den trong khung preview khong phai loi goc.
- TikTok bao `Maximum number of attempts` trong luong auto-login, sau do tool emit `finished_signal("error")`.
- `CDPWorker.run()` vao `finally` va goi cleanup GoLogin/Orbita, nen browser bi dong; khung nhung con lai thanh mau den.
- Dashboard lai hien `Loi: error`, lam mat ly do that su.

### Da lam
- Them `_last_login_error` de giu loi dang nhap that su thay vi mat thanh chuoi `error`.
- `_emit_login_error()` nay luu loi vao worker va van cap nhat cot Logged/bang theo doi.
- Khi auto-login tra ve `False`, worker chuyen sang `_hold_browser_for_login_recovery()`:
  - Giu Orbita/GoLogin mo.
  - Hien trang thai `Auto-login loi: ... Giu browser mo de dang nhap tay`.
  - Poll dang nhap tay; neu phat hien thanh cong thi luu TikTok ID/cookie roi dong profile nhu luong thanh cong.
  - Neu nguoi dung bam Dung hoac dong browser thi moi thoat.
- Dashboard cat tien to `error:` de hien `Loi: <ly do>` ro hon.

### Da kiem tra
- `python -m py_compile cdp_worker.py automation_dashboard.py main_gui.py`: OK.
- `python -c "import cdp_worker, automation_dashboard, main_gui; print('import ok')"`: OK.

### Can test thuc te
- Chay lai auto-login profile dang gap `Maximum number of attempts`.
- Khi TikTok bao loi, browser phai van nam trong preview, khong bi dong thanh man den.
- Bang theo doi/status phai hien ly do that, khong con chi `Loi: error`.
- Co the dang nhap tay ngay tren browser dang duoc giu mo; neu login thanh cong tool se luu session.

## 2026-05-21 - Fix auto-login TikTok bi Maximum attempts khi GoLogin manual van OK

### Nguyen nhan xac dinh
- Mo profile bang GoLogin app hoac mo browser manual trong tool deu vao duoc man `Xac minh do la ban`.
- Vi vay GoLogin SDK, Orbita, fingerprint va embed browser khong phai nguyen nhan chinh.
- Loi nam o luong auto-login trong `cdp_worker.py`:
  - Tool click vao o email/password roi go tiep, chua xoa sach gia tri cu/autofill truoc khi nhap.
  - Password co ky tu dac biet co the bi go sai qua `Input.dispatchKeyEvent`.
  - Tool chua verify gia tri trong form truoc khi bam Login, nen co nguy co submit sai nhieu lan va bi TikTok bao `Maximum number of attempts`.
  - Man `Xac minh do la ban` can duoc uu tien xu ly nhu mot buoc thanh cong sau password, khong duoc de nham voi loi login.

### Da lam trong `cdp_worker.py`
- Them helper nhap form an toan:
  - `_clear_active_text_input()` dung Ctrl+A + Backspace de xoa sach input dang focus.
  - `_active_input_value()` doc gia tri input dang focus.
  - `_set_active_input_value_js()` fallback set value qua JS va dispatch `input/change`.
  - `_type_active_input_exact()` nhap bang `Input.insertText`, hop hon voi password co ky tu dac biet, roi verify lai gia tri.
  - `_verify_login_form_values()` verify email/password trong form truoc khi submit.
- Trong `_do_login_direct()`:
  - Email/password duoc xoa sach truoc khi nhap.
  - Neu tool khong nhap dung email/password thi dung ngay, khong bam Login de tranh mat luot thu.
  - Truoc khi bam Login, neu form khong khop du lieu DB thi dung va ghi loi ro.
  - Uu tien detect man `Xac minh do la ban` truoc khi quet loi do tren form, sau do chon Email va di tiep luong OTP.

### Da kiem tra
- `python -m py_compile cdp_worker.py automation_dashboard.py main_gui.py`: OK.
- `python -c "import cdp_worker, automation_dashboard, main_gui; print('import ok')"`: OK.

### Can test thuc te
- Chay auto-login lai voi 1 account chua bi cooldown TikTok.
- Quan sat log:
  - `Da nhap Email`
  - `Da nhap Password (len=...)`
  - Neu form khong khop, tool phai dung truoc khi bam Login.
  - Neu TikTok hien `Xac minh do la ban`, tool phai chon Email va chuyen sang lay OTP.

## 2026-05-21 - Fix embed Orbita/GoLogin window vao dashboard

### Nguyen nhan xac dinh
- Browser/GoLogin mo thanh cong, CDP port da co, nhung cua so Orbita van noi ngoai dashboard.
- Loi nam o buoc bat HWND va SetParent vao QWidget preview.
- Code cu goi EnumWindows/SetWindowLong/SetParent/SetWindowPos trong `CDPWorker.run()` la worker thread, de gay Not Responding khi dung HWND cua Qt UI thread.
- Khi mo nhieu profile, logic match cua so theo PID/port/profile path chua du chac, co luc khong match dung Orbita window.

### Da lam trong `cdp_worker.py`
- Them `browser_ready_signal = pyqtSignal(dict)` de bao UI thread thuc hien embed browser.
- Them `_embed_token` rieng moi worker va day vao GoLogin `extra_params`:
  - `--ssmatool-embed-token=<token>`
- Them `_embed_done_event`, `_embed_result`.
- Them `notify_embed_result()` de UI thread bao lai worker sau khi embed thanh cong/that bai.
- Them `_request_browser_embed_from_ui()`:
  - emit thong tin `debug_port`, `process_pid`, `profile_dir`, `profile_id`, `embed_token`, `widget_id`
  - worker cho ket qua bang event nen khong block UI
  - van giu GoLogin start lock cho toi khi embed xong/timeout de tranh mo don nhieu profile.
- Trong `run()`, bo viec goi SetParent truc tiep tu worker thread.

### Da lam trong `automation_dashboard.py`
- `BrowserPreviewWidget` ket noi `browser_ready_signal`.
- Them retry embed bang `QTimer` trong Qt main thread:
  - scan HWND Chrome/Orbita
  - match uu tien theo embed token
  - match tiep theo profile dir/profile id/debug port
  - chi fallback fresh window khi that su chi co 1 ung vien moi
- Chuyen SetParent/SetWindowLong/SetWindowPos sang main UI thread.
- Them timer dong bo kich thuoc browser da nhung theo kich thuoc preview.
- Khi embed fail se hien ro `hwnd/pid/match/port/profile` thay vi ket im o `Embed scan`.
- Fix trang thai preview bi dung o `Cho chay...`:
  - nut `Chay(P)` da duoc connect ve luong mo browser/manual.
  - slot delay 0 khoi dong truc tiep, khong phu thuoc timer 0ms.
  - preview cap nhat ngay `Dang khoi dong worker...` khi bat dau start worker.
- Fix loi worker khong khoi tao sau khi them embed token:
  - `CDPWorker.__init__` da co `import uuid` cuc bo cu ben duoi.
  - Khi them `_embed_token = uuid.uuid4().hex` o dau ham, Python coi `uuid` la bien local va gay loi truoc khi browser mo.
  - Da bo import cuc bo, dung import global.

### Da kiem tra
- `python -m py_compile automation_dashboard.py cdp_worker.py main_gui.py`: OK.
- `python -c "import automation_dashboard, cdp_worker, main_gui; print('import ok')"`: OK.
- `python -c "from cdp_worker import CDPWorker; ..."` khoi tao worker truc tiep: OK.

### Can test thuc te
- Mo 2 profile lan dau, xem ca hai cua so Orbita co vao dung o preview khong.
- Mo 3-5 profile voi delay ngan, kiem tra dashboard khong Not Responding.
- Dong/chay lai ngay, kiem tra lifecycle cleanup cu van hoat dong.
- Neu con fail, doc status moi co `hwnd/pid/match/port/profile` de biet ket o scan hay SetParent.

## 2026-05-21 - Upload video bat buoc dung GoLogin Local SDK

### Nguyen nhan
- Man hinh upload video truoc do khong dung GoLogin SDK.
- `UploadWorker` mo browser qua `BrowserManager().launch_browser(...)`, tuc la browser local theo folder profile, khong phai phien GoLogin/Orbita that cua GoLogin SDK.

### Da lam trong `video_table_manager.py`
- Khi dua video vao hang cho upload, truyen them:
  - `gologin_profile_id`
  - giu `browser_id`, `profile_dir`, `cookie`, `proxy` cho metadata/backward compatibility.
- `gologin_profile_id` uu tien lay tu `profile_data["gologin_profile_id"]`, fallback `browser_id` neu du lieu cu dang luu GoLogin ID o cot browser_id.

### Da lam trong `upload_worker.py`
- Bo su dung `BrowserManager` trong worker upload.
- `_validate_task()` bat buoc task co GoLogin Profile ID.
- Them `_launch_gologin_profile()`:
  - doc API key tu `gologin_config.load_gologin_settings()`
  - mo profile bang `GoLogin({...}).start()`
  - lay CDP port SDK tra ve
  - dung `uploadCookiesToServer=True`, `writeCookiesFromServer=True`, `restore_last_session=True`
- Them `_stop_gologin_profile()` de dong bang `gl.stop()` sau upload.
- Ca luong upload theo group tai khoan va luong legacy 1-video deu da chuyen sang GoLogin SDK.
- `upload_worker.py` khong con tham chieu `BrowserManager/launch_browser/close_browser`.

### Da lam trong `upload_dashboard.py`
- Doi label UI de the hien upload bang GoLogin/Orbita.

### Da kiem tra
- `python -m py_compile upload_worker.py upload_dashboard.py video_table_manager.py main_gui.py`: OK.
- `python -c "import upload_worker, upload_dashboard, video_table_manager, main_gui; print('import ok')"`: OK.
- Khoi tao `UploadWorker` voi task gia co `gologin_profile_id`: OK.
- `rg "BrowserManager|launch_browser|close_browser" upload_worker.py upload_dashboard.py video_table_manager.py`: khong con ket qua trong upload worker/dashboard.

## 2026-05-21 - GoLogin/Orbita dashboard lifecycle

### Muc tieu
- Giam tinh trang dashboard bi do/Not Responding khi nhung Orbita/GoLogin.
- Xu ly truong hop nguoi dung dong/mo lai profile qua nhanh trong khi trinh duyet cu chua dong xong.
- Khong xoa du lieu profile, cookie, GoLogin profile hay profile data.

### Da lam trong `cdp_worker.py`
- Them `browser_closed_signal` de worker bao ve dashboard khi browser da cleanup xong that su.
- Them co trang thai dong bat dong bo:
  - `_async_close_started`
  - `_browser_closed_emitted`
  - `_close_signal_lock`
- Them helper `_emit_browser_closed_once()` de tranh emit nhieu lan.
- Them logic quet process cu theo profile hints:
  - `gologin_profile_id`
  - `browser_id`
  - `_profile_dir`
- Truoc khi mo GoLogin SDK, worker se goi `_cleanup_stale_profile_processes_before_start()` de don cac Orbita/Chrome con sot cua profile do.
- Trong `run()`, `finally` chi emit browser-closed sau khi `_release_browser_session()` xong.
- Trong `stop()`, khi nguoi dung dung/rerun:
  - emit status: `Dang don trinh duyet cu, vui long cho Orbita/GoLogin dong xong...`
  - dong browser trong background thread, khong block UI thread.
  - sau khi dong/kill/patch Preferences xong moi emit `browser_closed_signal`.

### Da lam trong `automation_dashboard.py`
- Them trang thai lifecycle rieng cho profile dang cleanup:
  - `_stopping_profile_keys`
  - `_pending_restart_request`
- `has_active_tasks()` tinh ca profile dang cleanup, de app khong hieu nham la da ranh.
- `_stop_profile_queue()` khong release profile lock ngay nua.
  - Thay vao do mark row/profile la dang don trinh duyet cu.
  - Goi `widget.stop_automation()` va cho worker bao `browser_closed_signal`.
- `_on_preview_browser_closed()` la diem release that su:
  - bo profile khoi `_row_profile_keys`
  - bo khoi `_stopping_profile_keys`
  - release lock o parent app
  - cap nhat bang: `Da dong trinh duyet, co the chay lai`
- Khi bam chay lai trong luc profile/browser cu dang dong:
  - Dashboard khong mo profile moi ngay.
  - Luu request vao `_pending_restart_request`.
  - Hien trang thai cho nguoi dung biet dang don Orbita/GoLogin cu.
  - Sau khi cleanup xong, dashboard tu chay lai request do.
- `_filter_blocked_profile_rows()` va `_start_scheduled_profile()` chan profile dang cleanup de tranh mo trung.
- `_schedule_next_profiles()` tinh ca slot dang cleanup, giup so luong luong chay khong bi vuot qua do profile cu chua dong.
- `cleanup_runtime_sessions()` giu UI o trang thai cleanup neu worker van dang dong browser, khong xoa grid qua som.

### Da lam trong `BrowserPreviewWidget`
- Them signal `browser_closed = pyqtSignal(int, str)`.
- Them `_connect_worker_signals()` de gom ket noi signal cua worker.
- Them `_on_browser_closed()` de forward tin hieu tu `CDPWorker.browser_closed_signal` len dashboard.
- Ket noi `browser_closed_signal` cho cac luong start worker:
  - `start_automation()`
  - `open_browser_only()` ban dau
  - `open_browser_only()` ban sau trong file

### Da kiem tra
- `python -c "import ast; ..."`: OK.
- `python -m py_compile automation_dashboard.py cdp_worker.py main_gui.py`: OK.
- `python -c "import automation_dashboard, cdp_worker, main_gui; print('import ok')"`: OK.

### Can test thuc te ngay mai
- Mo 3 profile, dong/chay lai ngay, xem co hien trang thai dang don trinh duyet cu khong.
- Mo 5 profile, dong/chay lai khi Orbita cu chua tat het, xem dashboard co tu cho va chay lai sau cleanup khong.
- Kiem tra profile lock khong bi release som khi GoLogin/Orbita van con process.
- Kiem tra nut `Don dep trinh duyet` trong app chinh khong xoa cookie/profile data.
- Neu van bi do, tiep tuc do:
  - thoi gian `gologin.stop()`
  - so process Orbita/Chrome con sot
  - luc nao `browser_closed_signal` duoc emit
  - co profile nao bi ket o cleanup qua lau hay khong

### Ghi chu quan trong
- Logic moi uu tien dung toc do voi Orbita/GoLogin: UI cho nguoi dung biet dang cleanup thay vi im lang va bi cam giac do.
- Profile chi duoc chay lai khi worker bao browser cu da dong xong.
- Khong co thao tac xoa du lieu profile/cookie/GoLogin profile trong phan nay.
