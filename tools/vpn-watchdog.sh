#!/usr/bin/env bash
# =============================================================================
# vpn-watchdog.sh —— 隧道守护：healthcheck 失败就自愈（该轮修复 修法）
#
# 为什么需要（2026-11-14 实测）：隧道 12:39 建好，12:53 / 13:03 / 13:25 被
#   三次 SIGTERM 打死，每次都让「靶场不可达」而没人发现 —— 容器 healthy、
#   tsec list 照常能列题（那是平台 API，不走隧道），只有真去打靶场才 000。
#
# 与 tsec vpn up 的分工：up 已改成幂等（隧道健康就直接返回），所以这里可以
#   放心地「不通就 up」，不会互杀。
#
# 用法：bash tools/vpn-watchdog.sh            # 前台循环（默认 60s 一次）
#       bash tools/vpn-watchdog.sh --once      # 只检查一次（给 cron/selfcheck 用）
#       PI_LAB_VPN_WD_INTERVAL=120 bash tools/vpn-watchdog.sh
# =============================================================================
set -uo pipefail
INTERVAL="${PI_LAB_VPN_WD_INTERVAL:-60}"
ONCE=0
[ "${1:-}" = "--once" ] && ONCE=1

log() { printf "[vpn-wd %s] %s\n" "$(date +%H:%M:%S)" "$*"; }

check_and_heal() {
  if tsec healthcheck >/dev/null 2>&1; then
    return 0
  fi
  log "隧道不通 → 尝试自愈"
  if tsec vpn up >/dev/null 2>&1 && tsec healthcheck >/dev/null 2>&1; then
    log "✅ 自愈成功"
    return 0
  fi
  log "❌ 自愈失败（ovpn 缺失/凭证过期/网关不可达）—— 人工介入"
  return 1
}

if [ "$ONCE" = 1 ]; then
  check_and_heal && { log "OK"; exit 0; } || exit 1
fi

log "守护启动，间隔 ${INTERVAL}s（Ctrl-C 退出）"
fails=0
while :; do
  if check_and_heal; then fails=0; else fails=$((fails+1)); fi
  if [ "$fails" -ge 5 ]; then
    log "连续 5 次自愈失败 → 退出（不空转）"
    exit 1
  fi
  sleep "$INTERVAL"
done
