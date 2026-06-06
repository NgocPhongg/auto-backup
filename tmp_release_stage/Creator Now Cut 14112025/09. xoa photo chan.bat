@echo off
setlocal enabledelayedexpansion

rem === THIẾT LẬP ===
set "DIR=03.tachanh"
set "DRYRUN=0"   rem 1 = chỉ liệt kê, 0 = xóa thật

if not exist "%DIR%" (
  echo Khong tim thay thu muc "%DIR%".
  exit /b 1
)

for %%F in ("%DIR%\*.jpg") do (
  set "name=%%~nF"
  rem Trick: them so 1 o dau de tranh loi so 0 dan den octal
  set /a "odd=1!name! %% 2"
  if !odd! EQU 0 (
    if "!DRYRUN!"=="1" (
      echo [PREVIEW] se xoa: "%%~nxF"
    ) else (
      del /q "%%F"
    )
  )
)

echo Done.
endlocal
