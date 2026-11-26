@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo   PiCTF 一键部署（Docker）
echo ============================================================
echo.

REM ── 1. 检查 docker ──────────────────────────────────────────
echo [1/5] 检查 Docker...
docker --version >nul 2>&1
if errorlevel 1 (
  echo   [X] 没装 Docker Desktop
  echo       下载： https://www.docker.com/products/docker-desktop/
  echo       装完启动 Docker Desktop，再双击本文件。
  pause & exit /b 1
)
for /f "tokens=*" %%v in ('docker --version 2^>nul') do echo   [OK] %%v
docker info >nul 2>&1
if errorlevel 1 (
  echo   [X] Docker 引擎没启动 —— 打开 Docker Desktop 等它变成绿色，再双击本文件
  pause & exit /b 1
)
echo   [OK] Docker 引擎在跑

docker compose version >nul 2>&1
if errorlevel 1 (
  echo   [X] 缺 docker compose 插件（新版 Docker Desktop 自带，更新一下）
  pause & exit /b 1
)
echo   [OK] docker compose 可用
echo.

REM ── 2. 检查 .env ────────────────────────────────────────────
echo [2/5] 检查 .env...
if not exist ".env" (
  if exist ".env.example" (
    copy /y ".env.example" ".env" >nul
    echo.
    echo   ----------------------------------------------
    echo   已生成 .env，但里面还没有真实密钥。
    echo.
    echo   请用记事本打开 .env，填这两行（其余可留空）：
    echo.
    echo       DEEPSEEK_API_KEY=sk-你的key
    echo       DEEPSEEK_BASE_URL=https://api.deepseek.com
    echo.
    echo   填完保存，再双击本文件。
    echo   ----------------------------------------------
    notepad .env
    pause & exit /b 1
  ) else (
    echo   [X] 仓库里没有 .env.example —— 这个包不完整，重新下载
    pause & exit /b 1
  )
)
echo   [OK] .env 存在
echo.

REM ── 3. 检查镜像 ─────────────────────────────────────────────
echo [3/5] 检查镜像 pictf-webui:0.2.4 ...
docker image inspect pictf-webui:0.2.4 >nul 2>&1
if errorlevel 1 (
  echo.
  echo   首次构建需要下载并安装全套 CTF 工具链。
  echo   镜像约 13.5 GB，耗时 20-40 分钟（看网速）。
  echo   只有第一次需要；之后改代码不用重建。
  echo.
  echo   磁盘需要约 25 GB 空闲。现在开始构建（可以最小化，别关窗口）...
  echo.
  docker compose build pictf
  if errorlevel 1 (
    echo.
    echo   [X] 构建失败。常见原因：
    echo       - 磁盘不足（需要约 25 GB）
    echo       - 网络拉取超时（重试一次）
    echo   pause & exit /b 1
  )
  echo   [OK] 镜像构建完成
) else (
  echo   [OK] 镜像已存在，跳过构建
  echo       （要强制重建：docker compose build --no-cache pictf）
)
echo.

REM ── 4. 启动 ─────────────────────────────────────────────────
echo [4/5] 启动容器...
docker compose up -d pictf
if errorlevel 1 (
  echo   [X] 启动失败，看日志： docker compose logs --tail 50 pictf
  pause & exit /b 1
)
echo   [OK] 已启动
echo.

REM ── 5. 健康检查 ─────────────────────────────────────────────
echo [5/5] 等待健康检查（最多 90 秒）...
set /a tries=0
:waitloop
set /a tries+=1
for /f "tokens=*" %%s in ('docker inspect pictf --format "{{.State.Health.Status}}" 2^>nul') do set "HEALTH=%%s"
if "!HEALTH!"=="healthy" goto healthy
if "!HEALTH!"=="unhealthy" goto unhealthy
if !tries! GEQ 30 goto timeout
timeout /t 3 /nobreak >nul
goto waitloop

:healthy
echo   [OK] 容器 healthy
goto done
:unhealthy
echo   [!] 容器 unhealthy —— 看日志： docker compose logs --tail 50 pictf
goto done
:timeout
echo   [!] 90 秒内没等到 healthy（可能还在启动，稍后运行 部署状态.bat 查看）

:done
echo.
echo ============================================================
echo   部署完成
echo.
echo   访问：      http://127.0.0.1:30142/
echo   进容器：    docker exec -it pictf bash
echo   看日志：    docker compose logs -f pictf
echo   停止：      docker compose down
echo   自检：      docker exec pictf bash tools/bin/selfcheck --quick
echo ============================================================
echo.
pause
