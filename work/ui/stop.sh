#!/usr/bin/env bash
# 停 UI 服务：按端口找进程，不用 pkill -f（那会匹配到自己的命令行）
#
# ★ 2026-10-13 加护栏（现场血账）：服务一停，它管着的 pi 子进程会连带死掉，
#   而**正在跑的子智能体 run 会被标成 stopped**（不是 paused，多半也续不回来）。
#   那天用户刚派出 5 路，被一次「stop.sh && start.sh」重启全部连坐 —— 用户看到的就是
#   「智能体派不出去了」。所以有活的时候**默认拒绝**，要停必须显式 --force。
LIVE="$(pgrep -fc 'subagent-runner-bootstrap' 2>/dev/null || true)"
LIVE="${LIVE:-0}"
if [ "$LIVE" -gt 0 ] && [ "${1:-}" != "--force" ] && [ "${1:-}" != "-f" ]; then
  echo "⛔ 现在有 $LIVE 个子智能体进程在跑 —— 停服务会把它们一起杀掉："
  pgrep -af 'subagent-runner-bootstrap' 2>/dev/null | head -6 | sed 's/^/     /'
  echo "   run 会变成 stopped（不是 paused，多半续不回来）。"
  echo "   确实要停：bash work/ui/stop.sh --force"
  exit 2
fi
pid="$(ss -ltnp 2>/dev/null | grep ':30142' | grep -oP 'pid=\K[0-9]+' | head -1)"
if [ -n "${pid:-}" ]; then kill "$pid" && echo "已停 pid=$pid"; else echo "没在跑"; fi
