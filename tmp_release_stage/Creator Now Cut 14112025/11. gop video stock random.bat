@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

set "FFMPEG=%~dp0ffmpeg.exe"
set "SRC=01.catstock"
set "OUTDIR=ketqua"

if not exist "%OUTDIR%" mkdir "%OUTDIR%"

set /p NUM=Nhập số lượng video: 

echo.
echo Đang tạo %NUM% video...
echo.

for /L %%n in (1,1,%NUM%) do (
    echo [%%n/%NUM%] Tạo video %%n với thứ tự ngẫu nhiên...
    
    :: Tạo list ngẫu nhiên bằng PowerShell cho MỖI video
    powershell -Command "Get-ChildItem '%SRC%' -Filter *.mp4 | Get-Random -Count 50 | ForEach-Object { \"file '\" + $_.FullName + \"'\" }" > list_%%n.txt
    
    "%FFMPEG%" -f concat -safe 0 -i list_%%n.txt -t 300 -c:v libx264 -crf 23 -preset medium -c:a aac -b:a 128k "%OUTDIR%\video_%%n.mp4" -y -hide_banner -loglevel warning -stats
    
    del list_%%n.txt
    echo    Done: video_%%n.mp4
)

echo.
echo Xong! Video trong: %OUTDIR%\
pause
