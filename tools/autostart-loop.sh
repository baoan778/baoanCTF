#!/usr/bin/env bash
# autostart-loop.sh —— 守护进程的守护（R6-P0-1）
#
# 为什么需要它（2026-11-20/21 实测，见 实战复盘 §3.1b）：
#   · `log/supervisor.log` 在 14:13:20 → 16:11:47 之间**118.5 分钟零 tick**（tick=30s）；
#   · 期间 parallel_solver 作为孤儿继续跑到 15:45，跑完后**无人重启**；
#   · 于是 15:45:21 → 16:12:25 出现 **27.1 分钟全场零活动**，最后靠人肉重启。
#   根因不是调度算法，而是「守护自己死了没人管」——solver-autostart.sh 只在容器
#   启动时被调用一次（entrypoint 的 if 分支），之后没有任何东西再看它。
#
# 本脚本只做一件事：**每隔 N 秒调一次 solver-autostart.sh**。
#   · 幂等性完全由 solver-autostart.sh 负责（停止标记 / 已在跑 / run 已终结），
#     它自己就是「宁可不起，不可错起」的判据集合，这里不重复实现；
#   · 因此本脚本可以随便崩、随便重启，不会造成重复拉起；
#   · 单次调用有硬超时（默认 90s）：autostart 里有平台探测（最长 60s），
#     卡住时不能让整个循环一直挂着。
#
# 用法（entrypoint 里后台起；也可手工）：
#   setsid nohup bash tools/autostart-loop.sh >> log/autostart.log 2>&1 </dev/null &
#   AUTOSTART_INTERVAL=60 bash tools/autostart-loop.sh     # 改巡检间隔
#   PI_AUTOSTART_LOOP=off  → 不循环（等价于旧行为：只调一次）
set -uo pipefail

ROOT="${ROOT:-/opt/pi-ctf}"
LOG="${ROOT}/log"
INTERVAL="${AUTOSTART_INTERVAL:-60}"
ONCE="${PI_AUTOSTART_LOOP:-on}"

log() { printf '[autostart-loop %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
mkdir -p "$LOG"

# 间隔下限 10s：再小就是空转刷日志（autostart 自己要探测平台，单次就可能几秒）
case "$INTERVAL" in
  ''|*[!0-9]*) INTERVAL=60 ;;
esac
[ "$INTERVAL" -lt 10 ] && INTERVAL=10

run_once() {
  # 90s 硬顶：单次 autostart 里最长的是平台探测（timeout 60）+ 拉起后 sleep 2
  timeout 90 bash "$ROOT/tools/solver-autostart.sh"
  _rc=$?
  # 124 = 被 timeout 掐断。这不罕见（平台探测慢），只报告，不退出循环。
  if [ "$_rc" -eq 124 ]; then
    log "本次 autostart 超时（90s）→ 跳过，下一跳再试"
  elif [ "$_rc" -ne 0 ]; then
    log "本次 autostart rc=$_rc（不阻断循环）"
  fi
}

log "启动：interval=${INTERVAL}s once=${ONCE}"
if [ "$ONCE" = "off" ]; then
  run_once
  log "once=off → 只跑一次，退出"
  exit 0
fi
while :; do
  run_once
  sleep "$INTERVAL"
done
