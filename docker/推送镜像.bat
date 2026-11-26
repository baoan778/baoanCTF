@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
if "%~1"=="" (
  echo 用法: 推送镜像.bat ^<仓库地址^>
  echo 例:   推送镜像.bat registry.cn-hangzhou.aliyuncs.com/pictf/webui
  echo.
  echo 先做: 阿里云 ^> 容器镜像服务 ^> 个人版 ^> 开通 ^> 建命名空间+仓库
  echo       控制台会给地址，形如 registry.cn-hangzhou.aliyuncs.com/你的命名空间/webui
  pause & exit /b 1
)
set REPO=%~1
for /f "tokens=1 delims=/" %%i in ("%REPO%") do set REG=%%i
echo [*] 登录 %REG% （用户名=账号全名，密码=控制台设的镜像密码）
docker login %REG% || (echo 登录失败 & pause & exit /b 1)
echo [*] 打标签
docker tag pictf-webui:0.1.1 %REPO%:0.1.1 || (echo 本机还没有镜像：先在本目录跑 docker compose build & pause & exit /b 1)
echo [*] 推送（首次约 1GB，几分钟）
docker push %REPO%:0.1.1 || (echo 推送失败 & pause & exit /b 1)
echo.
echo [√] 完成。朋友的命令：
echo     docker pull %REPO%:0.1.1
echo     docker run -d --name pictf -p 127.0.0.1:30142:30142 --cap-add=SYS_PTRACE --security-opt seccomp=unconfined -v pictf-agent:/home/ctf/.pi-ctf %REPO%:0.1.1
pause
