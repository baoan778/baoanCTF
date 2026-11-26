#!/usr/bin/env bash
# evalbench/ab.sh —— 顺序跑多个臂（**必须顺序**：4 个靶场端口是固定的 18081-18084，
# 两臂并行会 OSError: [Errno 98] Address already in use，实测踩过）。
# 用法：bash tools/evalbench/ab.sh [n] [budget]
set -u
N="${1:-6}"
BUDGET="${2:-150}"
cd /opt/pi-ctf || exit 1
rm -rf work/evalsolve log/evalbench

run_arm() {
  local arm="$1" thk="$2" slim="$3" ex="$4"
  echo "=== arm=$arm thinking=${thk:-<pi默认>} slim=$slim exclude=$ex"
  PI_SOLVE_THINKING="$thk" PI_SOLVE_SLIM="$slim" PI_SOLVE_EXCLUDE="$ex" \
    timeout 2400 python3 tools/evalbench/run.py --arm "$arm" --n "$N" --budget "$BUDGET" 2>&1 | tail -2
}

# baseline：生产默认（优化 06 slim + 优化 12 exclude）
run_arm baseline "" 1 default
# think-off：其余相同，只把 thinking 关掉
run_arm think-off "off" 1 default
# no-slim：等价于优化 06/12 之前的线上行为
run_arm no-slim "" 0 0

echo
python3 tools/evalbench/run.py --report
