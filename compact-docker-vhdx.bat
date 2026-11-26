@echo off
chcp 65001 >nul
setlocal
echo ================================================
echo  压缩 Docker Desktop 数据盘 (docker_data.vhdx)
echo  必须"以管理员身份运行"
echo ================================================
echo.

net session >nul 2>&1
if errorlevel 1 (
  echo [X] 当前不是管理员。请右键此文件 - 以管理员身份运行。
  pause
  exit /b 1
)

set "VHDX=D:\DockerData\DockerDesktopWSL\disk\docker_data.vhdx"
if not exist "%VHDX%" (
  echo [X] 找不到 %VHDX%
  pause
  exit /b 1
)

for %%A in ("%VHDX%") do set "BEFORE=%%~zA"
echo [1/3] 关闭 WSL ...
wsl --shutdown
timeout /t 5 /nobreak >nul

echo [2/3] 压缩虚拟磁盘（23 GB 约需 1-3 分钟）...
powershell -NoProfile -Command "Optimize-VHD -Path '%VHDX%' -Mode Full" 
if errorlevel 1 (
  echo      Optimize-VHD 失败，改用 diskpart ...
  (echo select vdisk file="%VHDX%"
   echo compact vdisk
   echo exit) > "%TEMP%\compact.txt"
  diskpart /s "%TEMP%\compact.txt"
)

for %%A in ("%VHDX%") do set "AFTER=%%~zA"
echo.
echo [3/3] 完成
powershell -NoProfile -Command "$b=%BEFORE%; $a=%AFTER%; '{0:N2} GiB -> {1:N2} GiB  释放 {2:N2} GiB' -f ($b/1GB),($a/1GB),(($b-$a)/1GB)"
echo.
echo 接下来回到终端执行： docker compose up -d  恢复服务
pause
