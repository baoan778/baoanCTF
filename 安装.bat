@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo  PiCTF WebUI 安装（在 WSL 里执行，首次几分钟）
echo ============================================
wsl.exe -e true >nul 2>&1
if errorlevel 1 (
  echo [!] 没检测到 WSL。请先用【管理员】PowerShell 运行：  wsl --install
  echo     装完重启一次，再双击本文件。
  pause & exit /b 1
)
for /f "usebackq delims=" %%i in (`wsl.exe wslpath -a "%~dp0."`) do set "WDIR=%%i"
echo 包目录: %WDIR%
wsl.exe -e bash -lc "cd '%WDIR%' && bash install.sh"
echo.
pause
