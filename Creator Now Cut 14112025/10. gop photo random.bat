@echo off
setlocal enabledelayedexpansion

rem === Cấu hình ===
set "IMG_DIR=03.tachanh"
set "OUT_DIR=ketqua"
set "FPS=1"        rem Số ảnh mỗi giây (1 = mỗi ảnh 1 giây)
set "OUT_NAME=video_random.mp4"

rem Tạo thư mục kết quả nếu chưa có
if not exist "%OUT_DIR%" mkdir "%OUT_DIR%"

rem Tạo file danh sách tạm
set "LIST_FILE=%TEMP%\imagelist.txt"
if exist "%LIST_FILE%" del "%LIST_FILE%"

rem Chuyển vào thư mục ảnh và lấy danh sách ảnh ngẫu nhiên
pushd "%IMG_DIR%"
(for %%i in (*.jpg) do @echo %%i) > "%TEMP%\all_images.txt"
powershell -Command "Get-Content '%TEMP%\all_images.txt' | Get-Random -Count (Get-Content '%TEMP%\all_images.txt').Count | ForEach-Object { 'file ''%CD%\'+$_+'''' }" > "%LIST_FILE%"
popd

rem Ghép ảnh thành video
ffmpeg -y -r %FPS% -f concat -safe 0 -i "%LIST_FILE%" -vf "scale=1920:1080,format=yuv420p" "%OUT_DIR%\%OUT_NAME%"

rem Xóa file tạm
del "%LIST_FILE%"
del "%TEMP%\all_images.txt"

echo Hoan tat! Video da duoc luu tai: "%OUT_DIR%\%OUT_NAME%"
pause
endlocal
