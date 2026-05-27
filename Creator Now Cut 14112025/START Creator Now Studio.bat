@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw creator_now_studio.py
) else (
    start "" python creator_now_studio.py
)
