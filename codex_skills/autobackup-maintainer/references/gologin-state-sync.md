# GoLogin State Sync Plan

## Muc tieu

Dam bao tool va app GoLogin dung chung mot state that cua GoLogin/Orbita profile.

- JSON chi la lop dieu khien va mapping.
- GoLogin profile la lop state that: login session, cookies, local storage, fingerprint, proxy.
- "Trinh duyet that" o day la GoLogin/Orbita profile, khong phai Chrome/Edge cua may.
- Khong dung `%APPDATA%\AutoBackup\gologin_profiles` lam nguon state chinh nua.

## Nguyen tac chinh

1. Neu account co `gologin_profile_id` that, moi chuc nang GoLogin phai mo bang GoLogin SDK voi dung `profile_id`.
2. Khong tu fallback sang local `BrowserManager` hoac `--user-data-dir` local khi dang o che do GoLogin.
3. Neu account thieu GoLogin Profile ID that, hien loi ro rang va dung thao tac thay vi mo profile local ngam.
4. JSON chi luu cac thong tin dieu khien:
   - `gologin_profile_id`
   - ten ho so / TikTok ID / proxy / trang thai
   - cookie snapshot neu can tham chieu
5. Cookie JSON khong duoc nap de len session GoLogin that khi upload/mo trinh duyet, tru khi co chuc nang import cookie rieng va user chu dong chon.

## Tinh trang hien tai da check

### Upload monitor

Luong upload chinh hien dang di dung huong:

- `upload_worker.py` bat buoc co GoLogin Profile ID that truoc khi upload.
- `upload_worker.py` mo bang GoLogin SDK voi:
  - `profile_id`
  - `spawn_browser=True`
  - `uploadCookiesToServer=True`
  - `writeCookiesFromServer=True`
  - `restore_last_session=True`
- Upload worker khong fallback sang `BrowserManager`.
- Neu co cookie backup trong JSON, upload worker chi log va khong nap som de giu session GoLogin.
- Sau upload, worker goi `gl.stop()`.

### Rui ro trong upload monitor can sua

1. `video_table_manager.py` van tao/truyen `profile_dir` local bang `gologin_profile_dir(browser_id)`.
   - Anh huong: co the tao thu muc local `%APPDATA%\AutoBackup\gologin_profiles`.
   - Khong phai nguon state upload chinh, nhung gay nhieu va nen bo.

2. `video_table_manager.py` map account upload theo `ten_ho_so`.
   - Anh huong: neu co 2 account trung ten, video task co the lay nham GoLogin ID.
   - Can map bang key on dinh hon: GoLogin Profile ID hoac source row.

3. Preview trong upload dashboard nhan `profile_dir` de phu giup tim cua so nhung worker da cap nhat `profile_dir` tu `gl.profile_path` sau khi SDK mo thanh cong.
   - Sau khi bo `profile_dir` local o task creation, preview van co the dua vao CDP port, process pid va GoLogin ID.

## Ke hoach sua sau

### Buoc 1: Lam ro GoLogin strict mode

- Trong `cdp_worker.py`, neu co `gologin_profile_id` that thi luon dung `_launch_browser_via_gologin_sdk()`.
- Khong cho valid GoLogin profile roi roi ve `_launch_browser_via_manager()`.
- Neu thieu GoLogin Profile ID cho thao tac GoLogin, bao loi ro:
  - "Profile nay chua co GoLogin Profile ID that."

### Buoc 2: Bo local profile dir khoi upload task

- Sua `video_table_manager.py`:
  - Khong import/goi `gologin_profile_dir()` cho upload task.
  - Khong set `profile_dir` local trong `video_tasks`.
  - Chi truyen `gologin_profile_id`, `browser_id`, proxy va metadata.

### Buoc 3: Map upload task bang ID on dinh

- Khi chon account upload, luu them key an toan cho moi video:
  - Uu tien `gologin_profile_id`.
  - Neu can hien thi thi van dung `ten_ho_so`.
- Khi mo `UploadDashboard`, lookup profile bang ID/source row thay vi chi bang ten.
- Neu phat hien ten trung nhau ma video chi co `upload_to` theo ten, can canh bao de user chon lai account.

### Buoc 4: Don cac fallback local khong con can

- Review `BrowserManager` va cac cho dung `gologin_profile_dir()`.
- Giu fallback local chi neu co ly do legacy ro rang.
- Neu giu legacy mode, dat ten ro la "Local legacy profile", khong goi la GoLogin state.

### Buoc 5: Sua nhan/chuc nang gay hieu nham

- Chuc nang "Xoa cache/cookie" hien tai chi xoa cookie trong JSON, khong xoa state GoLogin that.
- Can doi label/log thanh:
  - "Xoa cookie backup trong tool"
  - hoac neu that su muon xoa GoLogin cookies thi lam chuc nang rieng, co confirm ro.

## Checklist test sau khi sua

- Mo dashboard nuoi tai khoan voi account co GoLogin Profile ID: phai mo dung profile da login san trong app GoLogin.
- Mo upload monitor va upload video: phai giu login TikTok da co trong GoLogin.
- Dong/moi lai tool: JSON van giu mapping, state login van nam trong GoLogin profile.
- Account thieu GoLogin Profile ID: tool bao loi, khong tao/mo profile local ngam.
- Hai account trung ten: upload task khong lay nham GoLogin ID.
- Check log khong con tao path `%APPDATA%\AutoBackup\gologin_profiles\profile_<id>` cho upload task moi.

## Da trien khai

Ngay 2026-06-09:

- `cdp_worker.py`: bo viec tao local `gologin_profile_dir()` khi co GoLogin ID that; `_profile_dir` chi nhan `gl.profile_path` sau khi SDK mo profile.
- `upload_worker.py`: bo helper local profile dir, validate task gan `upload_profile_key=gologin:<id>`, group/lock upload theo GoLogin ID that.
- `video_table_manager.py`: bo import `gologin_profile_dir()`, gan metadata GoLogin vao cot `Upload toi`, fallback theo ten chi khi ten la duy nhat.
- `upload_dashboard.py`: group preview theo `upload_profile_key`/GoLogin ID thay vi chi theo ten account.
- `account_selector_dialog.py`: tra ve ca metadata account duoc chon thay vi chi tra ve ten ho so.
- `main_gui.py`: doi nhan/log "Xoa Cache/Cookies" thanh "Xoa cookie backup trong tool" de khong gay hieu nham voi state that GoLogin.
- Kiem tra cu phap bang `ast.parse` cho cac file da sua: OK.

Ngay 2026-06-10:

- Them `gologin_proxy_check.py` de doc proxy dang nam trong GoLogin profile that va validate truoc khi mo Orbita.
- `cdp_worker.py`: neu proxy duoc set trong GoLogin app/cloud ma bi loi, tool bao loi do va dung truoc khi mo browser.
- `upload_worker.py`: upload monitor cung check proxy GoLogin profile that khi task khong co proxy rieng trong JSON.
- `automation_dashboard.py`: khong ghi de trang thai loi proxy bang "Da dong trinh duyet" khi browser tat/cleanup.

Ngay 2026-06-10 (proxy sync fix):

- `gologin_proxy_check.py`: them helper `set_profile_proxy()` va `clear_profile_proxy()` de sync/clear proxy len GoLogin cloud profile.
- `main_gui.py`: khi sua profile, neu proxy doi/bi xoa thi dong bo sang GoLogin ngay luc bam Luu; neu xoa proxy thi set ve `none`.
- `cdp_worker.py`: khi mo profile, khong tu sync proxy cache trong JSON len GoLogin nua; chi doc proxy dang co trong GoLogin de cap nhat lai tool.
- `upload_worker.py`: upload monitor cung bo sync proxy cache khi mo; chi tin vao proxy hien tai trong GoLogin profile.
- `automation_dashboard.py`: cap nhat lai cot proxy trong bang khi worker emit proxy rong/khong rong, de xoa proxy cu trong tool khi GoLogin da xoa proxy.

Ngay 2026-06-10 (proxy clear hardening):

- Nguyen nhan loi "xoa proxy roi nhung van dinh proxy cu": tool co nhieu nguon cache (`profile_data["proxy"]`, cot 3 trong bang, `gologin_proxy_synced`). Neu mot nguon da rong nhung nguon khac con proxy, nut Luu co the khong chay nhanh xoa proxy GoLogin hoac dashboard van giu trang thai sync cu.
- `main_gui.py`: khi mo dialog sua profile, merge proxy/browser_id tu cot bang vao `existing_data` neu UserRole dang thieu; khi proxy trong form rong nhung cot bang hoac `gologin_proxy_synced` con dau vet proxy cu thi bat buoc goi `clear_profile_proxy()`.
- `automation_dashboard.py`: cho phep ghi de `gologin_proxy_synced` ve chuoi rong khi worker bao GoLogin profile khong con proxy, tranh giu status proxy cu.
- `gologin_proxy_check.py`: sync/clear proxy dung endpoint SDK hien tai ho tro: `PATCH /browser/{profileId}/proxy`.

Ngay 2026-06-10 (stale Orbita Preferences proxy):

- Da test profile `6a2861e8dd158875ba5a7674`: JSON AppData va GoLogin cloud deu khong con proxy (`proxy.mode = none`), nhung browser van co the hien IP proxy cu.
- Nguyen nhan kha nang cao: GoLogin SDK/Orbita profile Preferences con field `proxy.fixed_servers` tu lan chay truoc; khi cloud profile khong co proxy, SDK khong xoa field proxy cu trong Preferences.
- `cdp_worker.py`: sau khi doc GoLogin cloud va thay profile khong co proxy, them `--no-proxy-server` truoc `gl.start()` de ep browser di direct.
- `upload_worker.py`: ap dung cung logic cho upload monitor.
