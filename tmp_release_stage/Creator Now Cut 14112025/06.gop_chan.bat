@echo off
setlocal enabledelayedexpansion

set "input_folder=01.catstock"
set "output_folder=06.gop_chan"
set "output_file=%output_folder%\video_gop_chan.mp4"
set "concat_list=concat_chan.txt"

REM Tạo thư mục output nếu chưa có
if not exist "%output_folder%" mkdir "%output_folder%"

REM Xóa danh sách cũ nếu có
if exist "%concat_list%" del "%concat_list%"

REM Duyệt qua tất cả file .mp4 trong thư mục nguồn
for %%f in (%input_folder%\*.mp4) do (
    set "name=%%~nf"
    set /a num=!name!
    set /a mod=!num! %% 2
    if !mod! EQU 0 echo file '%%f' >> "%concat_list%"
)

REM Gộp video số chẵn
ffmpeg -f concat -safe 0 -i "%concat_list%" -c copy "%output_file%"

REM Xóa file tạm
del "%concat_list%"
