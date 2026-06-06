title DANG X
@echo off
rem Xóa tất cả file .mp4, .mp3 và .png trong các thư mục b, b1, b2, mp3, b3, sanpham
for %%d in (b b1 b2 mp3 b3 sanpham) do (
    del /s /q "%%d\*.mp4"
    del /s /q "%%d\*.mp3"
    del /s /q "%%d\*.png"
) 

@echo off
setlocal enabledelayedexpansion
set "input_folder=00.videogoc"
set "image_output_folder=03.tachanh"

if not exist "%image_output_folder%" mkdir "%image_output_folder%"

for %%t in ("%input_folder%\*.mp4") do (
    echo Processing video %%t...
    ffmpeg -y -i "%%t" -vf "fps=1/6,nlmeans=s=11:p=3:pc=6,unsharp=3:3:1.0" -q:v 0 -threads 0 "%image_output_folder%\%%04d.jpg"
)

echo Completed extracting frames!