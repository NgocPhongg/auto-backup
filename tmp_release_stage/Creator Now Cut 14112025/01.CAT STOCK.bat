@echo off
setlocal EnableDelayedExpansion
if not exist "01.catstock" mkdir "01.catstock"

for %%t in ("00.videogoc\*.*") do (
    echo ---
    echo Đang xu ly File: %%t
    
    REM 1. Lấy tổng thời lượng của video (làm tròn thành số nguyên giây)
    set "total_duration=0"
    for /f "delims=." %%a in ('ffprobe -v error -show_entries format^=duration -of default^=noprint_wrappers^=1:nokey^=1 "%%t"') do set "total_duration=%%a"

    REM Gọi hàm con để xử lý cắt toàn bộ video này
    call :ProcessVideo "%%t" "%%~nt" !total_duration!
)

echo ---
echo Hoan thanh tat ca video!
pause
exit /b

:ProcessVideo
set "file_path=%~1"
set "file_name=%~2"
set "dur=%~3"

REM Đặt mốc thời gian bắt đầu từ 0 và đánh số phần (part)
set "start_time=0"
set "part=1"

:CutLoop
if %start_time% lss %dur% (
    REM 2. Random thời lượng cắt từ 300 giây (5 phút) đến 360 giây (6 phút)
    set /a "clip_duration=(!RANDOM! %% 61) + 776"
    
    REM 3. Kiểm tra xem thời gian còn lại của video có đủ để cắt clip_duration không
    set /a "time_left=dur - start_time"
    if !time_left! lss !clip_duration! (
        REM Nếu thời gian còn lại ít hơn thời lượng random, lấy luôn phần còn lại để làm đoạn cuối
        set "clip_duration=!time_left!"
    )

    echo [Thông tin] Cat Part !part!: Dai !clip_duration! giay (Vi tri bat dau tu giay thu !start_time!^)
    
    REM 4. Tiến hành cắt đoạn
    ffmpeg -y -ss !start_time! -i "!file_path!" -t !clip_duration! -c copy "01.catstock\!file_name!_part!part!.mp4"
    
    REM 5. Cộng dồn thời gian bắt đầu cho đoạn cắt tiếp theo và tăng số thứ tự part
    set /a "start_time+=clip_duration"
    set /a "part+=1"
    
    REM Quay lại vòng lặp để cắt đoạn tiếp theo
    goto CutLoop
)
exit /b