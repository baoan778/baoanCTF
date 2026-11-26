#!/usr/bin/env bash
set -uo pipefail
PORT="${UI_PORT:-30142}"
PIDS="$(ss -ltnp 2>/dev/null | awk -v p=":$PORT" '$4 ~ p {print $NF}' | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u)"
if [ -z "$PIDS" ]; then echo "[ui] 没在跑（$PORT）"; exit 0; fi
for p in $PIDS; do kill "$p" 2>/dev/null && echo "[ui] 已停 pid=$p"; done
