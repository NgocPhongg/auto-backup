# Local Chrome Portable Plan

## Muc tieu

Chuyen Local Chrome sang mo hinh portable ro rang:

- Dung `chrome-win64\chrome.exe` dat canh app.
- Khong uu tien Chrome he thong cua may nua neu `chrome-win64` da ton tai.
- Moi profile Local Chrome tao ra 1 thu muc browser rieng.
- Toan bo du lieu Local Chrome duoc luu tren o D, nam trong workspace/tool, de de zip va chuyen may.

## State mong muon

### Trinh duyet

- Binary Local Chrome: `D:\auto - backup\chrome-win64\chrome.exe`

### Du lieu profile

- Root profile Local Chrome: `D:\auto - backup\local_chrome_profiles\`
- Moi profile map theo `browser_id` sang 1 thu muc rieng ben trong root nay.

Vi du:

- `D:\auto - backup\local_chrome_profiles\test_1_20260611_xxxxxx`
- `D:\auto - backup\local_chrome_profiles\shop_us_20260611_xxxxxx`

Ben trong thu muc profile se chua du lieu that cua browser:

- Cookies
- Local storage
- Session dang nhap
- History/cache theo user-data-dir cua Chrome

## Thay doi ky thuat du kien

### 1. Doi noi luu Local Chrome profile

Sua trong `app_paths.py`:

- `local_chrome_profiles_root()`

Hien tai dang tro ve `%APPDATA%\AutoBackup\local_chrome_profiles`.

Can doi thanh:

- `app_root_dir() / "local_chrome_profiles"`

Muc tieu:

- Toan bo profile local nam ngay trong thu muc tool tren o D.
- Zip tool la co the mang ca browser data di cung.

### 2. Uu tien chrome-win64 portable

Kiem tra/sua trong `app_paths.py`:

- `find_chrome_exe()`

Thu tu uu tien mong muon:

1. `app_root_dir() / "chrome-win64" / "chrome.exe"`
2. `app_root_dir() / "browser" / "chrome.exe"`
3. Moi fallback den Chrome he thong neu user van muon giu du phong

Neu muon chot che do portable tuyet doi, co the bo fallback Chrome he thong o buoc sau.

### 3. Giu nguyen luong worker

Khong can doi logic lon trong:

- `browser_manager.py`
- `cdp_worker.py`
- `upload_worker.py`

Vi cac file nay da goi thong qua helper:

- `require_chrome_exe()`
- `local_chrome_profile_dir(browser_id)`

Nen neu doi dung root o `app_paths.py`, toan bo luong se tu dong di theo.

### 4. Xoa profile Local Chrome

Nut `Xoa profile` da duoc them truoc do.

Can dam bao sau khi doi root moi thi no se xoa dung thu muc tai:

- `D:\auto - backup\local_chrome_profiles\...`

## Rui ro can check

### 1. App dang mo browser

Neu profile dang duoc mo, xoa thu muc co the fail do file dang bi lock.

Can giu thong bao dang co:

- Bao ro Chrome con mo
- Khong xoa am tham

### 2. Zip sang may khac

Neu duong dan duoc dua vao workspace, khi zip sang may khac thi profile local se di cung.

Can check them:

- May moi co `chrome-win64\chrome.exe`
- Quyen doc/ghi trong thu muc tool

### 3. Dung luong lon

Khi de profile trong thu muc tool, kich thuoc zip co the tang nhanh do cache/browser data.

Co the xu ly sau bang cach:

- them nut don cache,
- hoac quy dinh folder nao can/khong can dong goi.

## Cach trien khai uu tien

### Pha 1

- Doi root Local Chrome profile sang `D:\auto - backup\local_chrome_profiles`
- Uu tien `chrome-win64\chrome.exe`
- Test tao profile moi, mo browser, dang nhap, tat app, mo lai

### Pha 2

- Xac nhan xoa profile xoa dung thu muc local tren o D
- Xac nhan upload/dashboard cung dung root moi

### Pha 3

- Neu can, bo fallback Chrome he thong de ep portable 100%

## Ket qua mong muon

Sau khi hoan tat:

- Tool dung `chrome-win64` portable.
- Moi profile Local Chrome co 1 thu muc browser rieng trong workspace tren o D.
- Dang nhap xong tat app mo lai van con session.
- Co the zip ca tool + `chrome-win64` + `local_chrome_profiles` de chuyen sang may khac.
