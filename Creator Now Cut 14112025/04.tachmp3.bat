for %%t in ("00.videogoc\*.*") DO ( 
    ffmpeg -y -i "%%t" -vn -c:a libmp3lame -q:a 0 "04.tachmp3\%%~nt.mp3"
)

