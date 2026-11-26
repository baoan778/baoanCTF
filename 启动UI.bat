@echo off
chcp 65001 >nul
cd /d "%~dp0"
for /f "usebackq delims=" %%i in (`wsl.exe wslpath -a "%~dp0."`) do set "WDIR=%%i"
wsl.exe -e bash -lc "cd '%WDIR%' && bash start.sh"
timeout /t 2 >nul
start "" "http://127.0.0.1:30142/"
