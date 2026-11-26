#!/usr/bin/env bash
# PiCTF WebUI 启动：127.0.0.1:30142
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
export PI_LAB_ROOT="$ROOT"
export PI_LAB_KB_DIR="${PI_LAB_KB_DIR:-$ROOT/kb}"
export PI_CODING_AGENT_DIR="${PI_CODING_AGENT_DIR:-$HOME/.pi-ctf/agent}"

# ★ 2026-11-14 修 P2：把「UI 启动时自动同步」真正接上（此前两个启动器都没调，
#   导致 agent-template 改了不生效、真身与仓库各自漂移）。失败不阻塞启动。
if [ -f "$ROOT/tools/pi-agent-sync.sh" ]; then
  mkdir -p "$ROOT/log" 2>/dev/null || true
  if bash "$ROOT/tools/pi-agent-sync.sh" >>"$ROOT/log/pi-agent-sync.log" 2>&1; then
    echo "[ui] pi-agent-sync OK"
  else
    echo "[ui] ⚠ pi-agent-sync 失败（不阻塞启动，见 log/pi-agent-sync.log）" >&2
  fi
fi
export PI_SESSION_DIR="${PI_SESSION_DIR:-$HOME/.pi-ctf/sessions}"
export PI_CODING_AGENT_SESSION_DIR="${PI_CODING_AGENT_SESSION_DIR:-$HOME/.pi-ctf/sessions}"
export PATH="$ROOT/tools/bin:$PATH"
PORT="${UI_PORT:-30142}"

if [ ! -d "$PI_CODING_AGENT_DIR" ]; then echo "[ui] 还没安装：先跑 bash install.sh（Windows 双击 安装.bat）"; exit 1; fi
if ss -ltn 2>/dev/null | grep -q ":$PORT "; then echo "[ui] 已在运行 → http://127.0.0.1:$PORT/"; exit 0; fi
NODE="$(command -v node || true)"
if ! "$NODE" -e 'process.exit(0)' >/dev/null 2>&1; then NODE="$ROOT/vendor/node/node"; fi
[ -x "$NODE" ] || { echo "[ui] 找不到 Node：先跑 install.sh"; exit 1; }

mkdir -p /tmp/pictf-ui
nohup "$NODE" "$ROOT/work/ui/server.mjs" >> /tmp/pictf-ui/server.log 2>&1 &
sleep 1
if ss -ltn 2>/dev/null | grep -q ":$PORT "; then
  echo "[ui] 已启动 → http://127.0.0.1:$PORT/    （日志 /tmp/pictf-ui/server.log）"
else
  echo "[ui] 启动异常，看日志： tail -50 /tmp/pictf-ui/server.log"
fi
