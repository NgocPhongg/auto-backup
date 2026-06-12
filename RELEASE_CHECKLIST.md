# AutoBackup Release And Test Checklist

## Quick syntax check

Chay truoc moi lan dong goi:

```powershell
python .\codex_skills\autobackup-maintainer\scripts\check_project.py "D:\auto - backup"
```

## Quick UI smoke

Chay de check import + khoi tao nhanh 2 dashboard chinh:

```powershell
python .\scripts\smoke_ui_quick.py
```

Neu muon thu khoi tao ca cua so chinh:

```powershell
python .\scripts\smoke_ui_quick.py --full-main
```

Luu y:

- Script nay khong thay the test tay voi GoLogin/TikTok.
- `--full-main` co the tao/doc data app nhu luc mo app that.

## GoLogin smoke test

- Mo app va kiem tra menu API/Cookie co GoLogin API key.
- Them/sua profile co GoLogin ID that.
- Neu profile khong co proxy:
  - JSON trong `%APPDATA%/AutoBackup/accounts_data.json` cot proxy rong.
  - GoLogin cloud profile `proxy.mode = none`.
  - Log khi mo browser co dong ep direct: `--no-proxy-server`.
- Neu them proxy:
  - Proxy duoc check song truoc khi luu.
  - Luu len GoLogin bang `PATCH /browser/{profileId}/proxy`.
  - Mo lai browser thay IP proxy.
- Neu xoa proxy:
  - Log co `Da xoa proxy trong GoLogin profile`.
  - Mo lai browser khong con IP proxy cu.

## Dashboard/UI smoke test

- Mo `Bang theo doi` tu du an co it nhat 2 profile.
- Double-click ten du an trong combobox de doi ten:
  - khong doi duoc muc `Tat ca tai khoan`.
  - khong cho trung ten du an cu.
  - bang theo doi dang chay task thi bi chan doi ten.
- Kiem tra cot `Geo` van sua duoc bang dropdown va hien thi dung mau.
- Kiem tra upload dashboard khong con label UI thua/placeholder.

## Upload smoke test

- Chon video va account co GoLogin ID.
- Task upload phai group theo `gologin:<profile_id>`, khong chi theo ten ho so.
- Neu ten ho so trung nhau, phai canh bao/chon lai dung account.
- Upload worker doc proxy tu GoLogin profile, khong day proxy cache JSON len cloud khi mo.
- Neu account dung Local Chrome + proxy auth:
  - worker tao proxy bridge local truoc khi launch.
  - browser nhan proxy local thay vi chuoi `host:port:user:pass`.
  - sau khi stop/upload xong, proxy bridge duoc dong.

## Local Chrome smoke test

- Tao 1 profile backend `Local Chrome`.
- Mo browser va dang nhap thu cong, dong lai, mo lai:
  - session/cookie van con.
  - user data nam trong `local_chrome_profiles/<browser_id>`.
- Xoa profile trong app:
  - metadata JSON bi xoa.
  - neu co nut/xu ly xoa du lieu local thi folder browser_id phai bi xoa dung profile.


## EDIT_1 smoke test

- Mo `EDIT_1/main.py`.
- Them 1 video nho vao queue.
- Test cac option lien quan layout:
  - background image.
  - text box/text overlay.
  - composed fit/crop.
- Render thu 1 clip ngan va kiem tra FFmpeg command khong loi `filter_complex`/audio map.

## Build release

Kiem tra version trong `app_version.py`, sau do:

```powershell
.\scripts\build_release.ps1 <version>
```

Build script se:

- chay syntax check + smoke UI mac dinh truoc khi build (co the bo qua bang `-SkipPreflight` neu dang rebuild nhanh).
- chay PyInstaller bang `AutoBackup.spec`.
- copy `EDIT_1` vao app dist.
- copy `Creator Now Cut`/`Creator Now Cut 14112025` vao app dist.
- copy `chrome-win64` va `stealth_firefox` neu runtime co san trong workspace.
- audit bundle de chan data/session bi lot vao zip.
- tao zip trong `release/`.

## Sau khi build

- Giai nen zip ra thu muc sach.
- Mo app tu zip.
- Kiem tra app doc data tu `%APPDATA%/AutoBackup`.
- Kiem tra zip app khong chua `accounts_data.json`, `projects.json`, `local_chrome_profiles`, `stealth_firefox_profiles`.
- Test nhanh GoLogin profile co proxy va khong proxy.
- Test mo bang theo doi upload.
- Test mo EDIT_1 tu app.
