# Stealth Firefox Backend Plan

## Muc tieu hien tai

Them mot browser backend moi dua tren repo `feder-cr/invisible_playwright`, nhung trien khai theo tung lop de giam rui ro:

- `gologin`
- `local_chrome`
- `stealth_firefox`

Backend nay la Firefox patched + Playwright persistent context, khong phai Chromium/CDP.

## Ket luan kien truc quan trong

`stealth_firefox` phai duoc coi la backend rieng, khong gop chung vao:

- GoLogin / Orbita
- Local Chrome
- `cdp_worker.py`

Ly do:

- luong hien tai cua app dang nghieng ve Chromium + CDP
- `invisible_playwright` dung Playwright API, khong phai CDP API
- neu nhet vao worker cu se rat de vo login, upload va preview

## State model

### JSON dieu khien

Moi profile du kien co dang:

```json
{
  "browser_backend": "stealth_firefox",
  "browser_id": "stealth_firefox:test_1_20260611_xxxxxx",
  "gologin_profile_id": "",
  "proxy": "",
  "proxy_type": "http"
}
```

JSON chi la lop dieu khien:

- chon backend nao
- map profile nao
- luu proxy/cau hinh ho tro

### State browser that

State that cua browser nam trong profile folder persistent.

Root profile du kien:

- `D:\auto - backup\stealth_firefox_profiles\`

Moi profile la 1 folder rieng:

- `D:\auto - backup\stealth_firefox_profiles\test_1_20260611_xxxxxx`
- `D:\auto - backup\stealth_firefox_profiles\shop_us_20260611_xxxxxx`

Ben trong se giu:

- cookies
- local storage
- session login
- browser cache
- prefs / history can thiet cua profile

## Binary browser

Co 2 huong:

### Huong A - de `invisible_playwright` tu fetch

- chay `python -m invisible_playwright fetch`
- binary nam trong cache user

Khong hop voi muc tieu zip tool sang may khac.

### Huong B - dong goi binary rieng cung tool

Du kien:

- `D:\auto - backup\stealth_firefox\firefox.exe`

Huong nay hop hon vi:

- de zip
- de chuyen may
- de khoa phien ban binary

Khuyen nghi hien tai: uu tien Huong B.

## Lo trinh de xuat

### Giai doan 1 - Feasibility spike

Muc tieu: chung minh backend nay mo duoc va giu duoc session truoc khi noi vao app chinh.

Viec can lam:

1. tao mot worker/module test rieng, chua dong vao `cdp_worker.py`
2. test `launch_persistent_context()` voi `profile_dir` co dinh
3. test proxy
4. test dong/mo lai van giu dang nhap

Chi can dat 4 tieu chi:

- browser mo duoc
- profile dir duoc tao dung
- login duoc giu
- profile khong bi mat state sau khi tat

### Giai doan 2 - Them backend vao data + UI

File chinh:

- `browser_backend_utils.py`
- `add_profile_dialog.py`
- `main_gui.py`

Viec can lam:

1. them constant:
   - `STEALTH_FIREFOX_BACKEND = "stealth_firefox"`
2. cap nhat normalize / label helper
3. them lua chon backend trong tao/sua profile
4. profile loai nay:
   - khong goi GoLogin API
   - khong can `gologin_profile_id`
   - tao `browser_id` rieng

### Giai doan 3 - Them path helper

File chinh:

- `app_paths.py`

Them helper:

- `stealth_firefox_profiles_root()`
- `stealth_firefox_profile_dir(browser_id)`
- `find_stealth_firefox_exe()`
- `require_stealth_firefox_exe()`

Muc tieu:

- profile nam trong workspace / o D
- binary tim thay tu thu muc tool

### Giai doan 4 - Worker mo browser rieng

Khong dua logic nay vao `cdp_worker.py`.

Nen them worker/module rieng, vi du:

- `stealth_firefox_worker.py`

Worker nay chi can xu ly ban dau:

- mo browser persistent
- ap proxy
- mo TikTok
- cho user login tay
- dong/mo lai van giu state

### Giai doan 5 - Tich hop dashboard muc toi thieu

File chinh:

- `automation_dashboard.py`
- `main_gui.py`

Ban dau chi can:

- mo browser
- dong browser
- hien trang thai profile

Chua noi full automation ngay.

### Giai doan 6 - Tach lop automation

Day la phan quan trong nhat neu muon "cung chuc nang, doi browser".

Can tach nghiep vu ra khoi CDP-specific code:

- mo TikTok
- check login
- upload video
- post video
- feed interaction

Sau do moi tao adapter:

- CDP adapter
- Playwright adapter

Neu bo qua buoc nay va noi truc tiep, code se rat kho bao tri.

### Giai doan 7 - Uu tien chuc nang theo thu tu

Thu tu nen lam:

1. manual open + persistent login
2. check login state
3. upload video
4. feed automation
5. preview / embed neu can

### Giai doan 8 - Release va dong goi

Neu dua vao release:

- ship binary Firefox patched cung app
- bo sung path tim binary trong build/release
- quyet dinh co ship profile mau hay khong

## Rui ro chinh

### 1. Khac engine voi stack hien tai

App hien tai dang chay nhieu logic Chromium/CDP:

- `cdp.evaluate`
- `cdp.send`
- `Input.dispatchMouseEvent`

Nhung do khong chuyen thang sang Playwright duoc.

### 2. Khong dung chung state voi Chrome / GoLogin

`stealth_firefox` la profile Firefox rieng.

Nen:

- khong dung chung cookie/session voi Local Chrome
- khong dung chung state that voi GoLogin

### 3. TikTok co the hanh xu khac tren Firefox

Can test lai:

- login
- upload
- popup
- file picker
- drag/drop neu co
- selector / timing

### 4. Dependency va compatibility

Can quan ly:

- `playwright`
- `invisible_playwright`
- binary Firefox patched

Can test that compatibility voi moi truong Python dang dung trong app truoc khi noi sau.

## Huong trien khai duoc khuyen nghi

Khuyen nghi hien tai:

- Ban 1: backend `stealth_firefox` + persistent profile + mo browser + giu login
- Ban 2: noi upload
- Ban 3: noi automation/feed

Khong nen co gang "doi browser nhung giu nguyen toan bo chuc nang ngay lap tuc", vi do la cach de tao them loi he thong.

### 4. UI preview/embed co the khac

Neu app dang quen voi luong embed Chrome/CDP, Firefox co the can xu ly rieng.

## Thu tu trien khai khuyen nghi

### Buoc 1

Hoan tat `Local Chrome portable` truoc.

### Buoc 2

Them `stealth_firefox` vao danh sach backend, nhung chi ho tro:

- tao profile
- mo browser
- luu session

### Buoc 3

Neu on, moi noi vao:

- dashboard
- upload
- automation them

## Ket qua mong muon

Sau khi hoan tat:

- Tool co them backend `Stealth Firefox`
- Moi profile Firefox co 1 folder data rieng tren o D
- Tat app mo lai van con session
- Khong anh huong toi Local Chrome va GoLogin hien co
- Co the mo rong thanh backend thu 3 cho cac truong hop can stealth hon
