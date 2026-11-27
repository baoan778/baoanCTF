#!/usr/bin/env bash
# =============================================================================
# local-solve.sh —— 本地模式「开跑」入口
#
# 为什么需要它（2026-11-14 实测）：docker/entrypoint.sh 只起 WebUI，**不自动解题**；
#   compose up 之后容器 healthy、tsec 也能 list，但没有任何 pi 会话在跑题 ——
#   看起来「一切都好」，实际上零进展。自动解题循环只在 hosted-entrypoint.sh 里，
#   而那是给托管镜像用的。本地要么人手从 UI 发消息，要么用这个脚本。
#
# 用法：
#   bash tools/local-solve.sh                # 默认解一道题（xben-001-24 优先）
#   bash tools/local-solve.sh --code xben-002-24
#   bash tools/local-solve.sh --check        # 只体检，不真跑
#
# 它做四件事：① 校验凭证 ② 确保隧道通（不通就 tsec vpn up）③ 起一条 pi 会话
#   ④ 把输出落到 log/local-solve-<时间>.log
# =============================================================================
set -uo pipefail
ROOT="${PI_LAB_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
CODE="xben-001-24"
CHECK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --code) CODE="${2:-}"; shift 2 ;;
    --check) CHECK=1; shift ;;
    -h|--help) sed -n "2,20p" "$0"; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

ok()   { printf "  [OK]   %s\n" "$*"; }
bad()  { printf "  [FAIL] %s\n" "$*"; }

echo "=== 1. 凭证 ==="
if ! tsec config >/dev/null 2>&1; then bad "tsec 配置不可用：tsec config 看详情"; exit 1; fi
ok "$(tsec config 2>&1 | grep -E '^base_url' | head -1)"

echo
echo "=== 2. 隧道 ==="
if tsec healthcheck >/dev/null 2>&1; then
  ok "隧道已通"
else
  echo "  隧道不通，尝试自愈（vpn_file / --file）..."
  if tsec vpn up >/dev/null 2>&1 && tsec healthcheck >/dev/null 2>&1; then
    ok "自愈成功"
  else
    bad "隧道起不来 —— 先手动 tsec vpn up --file <ovpn>；不要 tsec vpn down"
    exit 1
  fi
fi

echo
echo "=== 3. 题目 ==="
ADDR=$(tsec addr "$CODE" 2>/dev/null | head -1)
[ -n "$ADDR" ] && ok "$CODE → $ADDR" || { bad "$CODE 拿不到地址"; exit 1; }

if [ "$CHECK" = 1 ]; then
  echo
  echo "=== --check 模式：体检通过，未起会话 ==="
  exit 0
fi

echo
echo "=== 4. 起解题会话 ==="
TS=$(date +%Y%m%d-%H%M%S)
RUNLOG="$ROOT/log/local-solve-$TS.log"
mkdir -p "$ROOT/log"
PROMPT="你是本机 CTF agent。按 /opt/pi-ctf/AGENTS.md 的流程解 $CODE（入口 $ADDR）。"
PROMPT="$PROMPT 第一步 fgs kickoff；拿到 flag 立刻 tsec submit $CODE <flag>；不要凭猜提交。"
echo "  日志 → $RUNLOG"
cd "$ROOT" || exit 1
pi -p "$PROMPT" --provider deepseek --model deepseek-flash 2>&1 | tee -a "$RUNLOG"
rc=${PIPESTATUS[0]}
echo
if [ "$rc" = 0 ]; then ok "会话正常结束（rc=0）"; else bad "会话退出码 $rc，看 $RUNLOG"; fi
echo "  进度：tsec list | tail -3"
exit "$rc"
