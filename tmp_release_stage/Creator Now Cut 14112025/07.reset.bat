@echo off
title XOA FILE TRONG CAC FOLDER

for %%d in (
    "01.catstock"
    "02.Edit"
    "03.tachanh"
    "04.tachmp3"
    "05.gop_le"
    "06.gop_chan"
    "ketqua"
) do (
    echo Đang xóa file trong %%d...
    del /q /s "%%d\*.*"
)

echo.
echo ✅ Đã xóa toàn bộ file trong các thư mục!
