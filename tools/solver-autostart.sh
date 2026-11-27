#!/usr/bin/env bash
# solver-autostart.sh —— 容器重启后自动恢复解题链路
#
# 为什么需要它（2026-11-19 实测事故）：
#   · 13:45 起的 parallel_solver + 3 个 sdkshard 在 14:34 容器重启里被全杀；
#   · 之后**没有任何机制把它们拉起来**，直到 14:45 手工重启 —— 空转约 10 分钟；
#   · 根因是架构性的：容器 PID1 是 UI 服务（entrypoint.sh 最后 exec node server.mjs），
#     parallel_solver 只是人肉 nohup 起的孤儿进程，容器一重启就永久没了。
#   限时赛里空转 10 分钟 = 直接丢分，所以必须自动化。
#
# 设计原则：**宁可不启，不可错启**
#   · run 已结束（平台 409 InvalidState）→ 绝不启动（否则留下永远空转的守护进程）；
#   · 已全部通关 → 不启动；
#   · 已有守护/解题进程在跑 → 不重复启动（幂等）；
#   · 平台暂时不可达（VPN 抖动）→ **仍然启动**守护，它会每 30s 重试直到恢复
#     —— 这正是最需要自动恢复的场景，不能因为一次探测失败就永久放弃。
#
# 用法：
#   bash tools/solver-autostart.sh              # 默认（入口脚本调用）
#   PI_AUTOSTART=force bash tools/solver-autostart.sh   # 无视判定，强制拉起
#
# 环境变量：SUP_SHARDS / SUP_ROUNDS / SUP_BUDGET / SUP_PER_ROUND 透传给 supervise.py
set -uo pipefail

ROOT="${ROOT:-/opt/pi-ctf}"
LOG="${ROOT}/log"
stopmark="${ROOT}/work/solve/.supervisor-stop"

log() { printf '[autostart %s] %s\n' "$(date +%H:%M:%S)" "$*"; }

mkdir -p "$LOG"

# ── 0. run 已终结标记：确定性信号，别再每 60s 重探一次 ────────────────
#   ★ 2026-11-24：平台返回 409 already finished 是**终态**，不会自己变回 running。
#   实测它被当成「每轮都重新判一次」的瞬时状态，于是 autostart.log 每分钟多一条
#   完全相同的「平台 run 已终结」，二十多条之后人已经看不出哪条是新的。
#   有这个标记就**跳掉平台探测**（省一次 60s 预算内的网络往返，也止住刷屏）。
#   标记由下面的 finished 分支写入；要恢复解题链路就删掉它。
_finmark="${ROOT}/work/solve/.run-finished"
if [ -f "$_finmark" ]; then
  if [ "${PI_AUTOSTART:-}" = "force" ]; then
    log "存在 run-已终结标记，但 PI_AUTOSTART=force → 无视它继续"
  else
    # 只在标记刚出现时打过一次 log（写在 finished 分支里），这里彻底静默
    exit 0
  fi
fi

# ── 1. 停止标记：人工明确停过就别自动拉起来 ─────────────────────────
# ★★ 2026-11-23 实测发现（真重启时抓到）：这个标记**没有过期语义**，
#   一旦人工停过一次，自动恢复就**永久失效**。实测证据：
#     .supervisor-stop 内容是 "2026-11-20 17:11:08"（三天前），
#     autostart.log 从 13:43 到 13:58 连续 16 分钟全打「存在停止标记 → 不启动」，
#     而这 16 分钟正是容器重启后最该恢复的窗口。
#   这直接攻击了某轮优化 的立身之本：它存在的理由就是"容器重启后自动恢复"，
#   而最常见的场景恰恰是"人先停掉 → 容器重启"。
#   修法不是删掉这个判据（人工停机必须被尊重），而是给它**过期语义**：
#     · 标记内容可写时间戳；超过 PI_STOPMARK_TTL（默认 2h）视为陈旧 → 自动解除并留痕；
#     · 显式写 "forever" 才是永久停机（人真的想永久停时用这个）；
#     · 陈旧标记不静默忽略，而是**重命名归档**（.stale-<时间>），可审计。
_stop_ttl="${PI_STOPMARK_TTL:-7200}"
if [ -f "$stopmark" ]; then
  _sm="$(cat "$stopmark" 2>/dev/null | tr -d '\r' | head -1)"
  if [ "$_sm" = "forever" ]; then
    log "存在**永久**停止标记 → 不启动（要恢复请删掉 $stopmark）"
    exit 0
  fi
  _sm_epoch="$(date -d "$_sm" +%s 2>/dev/null || echo 0)"
  _now="$(date +%s)"
  if [ "$_sm_epoch" -gt 0 ] && [ $(( _now - _sm_epoch )) -gt "$_stop_ttl" ]; then
    _arch="${stopmark}.stale-$(date +%Y%m%d-%H%M%S)"
    mv "$stopmark" "$_arch" 2>/dev/null || true
    log "停止标记已陈旧（${_sm}，超过 ${_stop_ttl}s）→ 自动解除，归档为 $(basename "$_arch")"
  else
    log "存在停止标记 $stopmark（内容：$_sm）→ 不启动"
    exit 0
  fi
fi

# ── 2. 幂等：已经有守护或解题进程在跑就别重复起 ─────────────────────
#   ★★ 不能用 `pgrep -f "tools/supervise.py"`：它匹配**命令行里出现这个字符串的任何进程**，
#      包括「shell 执行一段提到该路径的脚本」和本脚本自己 —— 实测就踩到了：外层
#      bash -lc 的脚本正文里含 "tools/supervise.py"，pgrep 立刻误判「已在运行」→ 跳过启动。
#      后果是「守护以为进程活着 → 永远不拉起」，比不写这段还糟。
#   判据改成 **argv 语义**：/proc/<pid>/cmdline 里 argv[0] 是 python 解释器、
#   且 argv[1] 就是那个脚本文件。只认真正在跑这个脚本的进程。
#   ★ 2026-11-20 某轮优化：这段判据已抽到 tools/procscan.py（第三份副本就在这里）。
#     本脚本、tools/supervise.py、tools/bench/live.py 当时各写一套「谁在跑」，
#     改一处忘两处（live.py 就因为按 /dist/bundle/cli.js 找 pi，恒数为 0）。
#     现在统一调 procscan，只保留一个失败时的内联兜底（procscan 缺失时仍能判）。
running_py_script() {
  if python3 "$ROOT/tools/procscan.py" --script "$(basename "$1")" >/dev/null 2>&1; then
    return 0
  fi
  python3 - "$1" <<'PY'
import os, sys
target = os.path.basename(sys.argv[1])
me = os.getpid()
for d in os.listdir("/proc"):
    if not d.isdigit() or int(d) == me:
        continue
    # ★ 2026-11-20 实测：读 **D 状态**进程的 cmdline 会永久阻塞（容器里有一批起不来的
    #   ps/find/pgrep 卡在 __access_remote_vm），而 /proc/<pid>/stat **永远不阻塞**。
    #   不先看状态，这段兜底会把「守护自检」变成「守护卡死」。
    try:
        with open("/proc/%s/stat" % d, "r", encoding="utf-8", errors="replace") as fh:
            _st = fh.read()
        if _st[_st.rindex(")") + 1:].split()[0] in ("Z", "D", "X", "x"):
            continue
    except Exception:
        continue
    try:
        with open("/proc/%s/cmdline" % d, "rb") as fh:
            argv = [a.decode("utf-8", "replace") for a in fh.read().split(b"\0") if a]
    except Exception:
        continue
    if len(argv) < 2:
        continue
    if not os.path.basename(argv[0]).startswith("python"):
        continue
    if os.path.basename(argv[1]) == target:
        print(argv[0], argv[1]); raise SystemExit(0)
raise SystemExit(1)
PY
}

if running_py_script "tools/supervise.py" >/dev/null 2>&1; then
  log "supervise.py 已在运行 → 不重复启动"
  exit 0
fi
if running_py_script "tools/parallel_solver.py" >/dev/null 2>&1; then
  log "parallel_solver.py 已在运行 → 不重复启动"
  exit 0
fi

# ── 3. 判定平台状态：run 结束了就别起（这是唯一「明确不该起」的信号）──
verdict="$(cd "$ROOT" && timeout 60 python3 - <<'PY' 2>&1
import sys
sys.path.insert(0, "/opt/pi-ctf/tools/bench")
try:
    from live import platform
except Exception as e:
    print("unknown", "import 失败: %s" % e); raise SystemExit(0)
# retries=1：默认 3 次 × 20s 超时 = 60s，正好被外层 timeout 60 掐断，
# 结果是 verdict 为空、日志没有信息量（实测过）。探测只用于**分类**，
# 快而准确比「多试几次但被打断」有用；真连不上时反正也会拉起守护。
ok, data = platform(net=True, retries=1)
if not ok:
    err = str(data)
    # 409 / InvalidState = run 已终结，这是确定性信号
    if "409" in err or "InvalidState" in err or "already finished" in err:
        print("finished", err[:120])
    else:
        print("unreachable", err[:120])
    raise SystemExit(0)
left = sum(1 for c in data if not c.get("done"))
print("running", left)
PY
)"

kind="$(printf '%s' "$verdict" | awk '{print $1}')"
detail="$(printf '%s' "$verdict" | cut -d' ' -f2-)"

# 空 kind 也要说清楚（不能打成「平台状态未知（）」这种没信息量的日志）
[ -z "$kind" ] && { kind="unknown"; detail="探测无输出（多半是超时）"; }

case "$kind" in
  finished)
    # ★ 2026-11-24：把「已终结」写成**状态**，别只打一行日志就退。
    #   实测：平台返回 409 already finished 之后，外层 autostart-loop.sh
    #   **每 60 秒**重跑本脚本一次，于是 autostart.log 被同一句
    #   「平台 run 已终结」刷满（实测连续 20+ 条），真正的信号淹在噪声里，
    #   人看日志时已分不清「刚终结」还是「早终结了」。
    #   现在落一个标记：后续巡检看到它就静默跳过，只在首次终结时打一次日志。
    #   标记带原因与时间，可审计；真要恢复时删掉它即可（同停止标记的心智）。
    _fin="${ROOT}/work/solve/.run-finished"
    if [ ! -f "$_fin" ]; then
      mkdir -p "$(dirname "$_fin")" 2>/dev/null || true
      printf "%s\n%s\n" "$(date "+%Y-%m-%d %H:%M:%S")" "$detail" > "$_fin" 2>/dev/null || true
      log "平台 run 已终结（$detail）→ 不启动解题链路；已记标记 $_fin（后续巡检静默跳过）"
    fi
    exit 0 ;;
  running)
    if [ "${detail:-0}" = "0" ]; then
      log "全部通关 → 不启动"
      exit 0
    fi
    log "平台在跑：还有 $detail 题未通关 → 拉起守护" ;;
  unreachable)
    log "平台暂不可达（$detail）→ 仍拉起守护（它会每 30s 重试直到恢复）" ;;
  *)
    log "平台状态未知（$detail）→ 仍拉起守护" ;;
esac

# ── 4. 拉起守护（setsid + 独立进程组，容器内 PID1 被杀也不连带）─────
setsid nohup python3 "$ROOT/tools/supervise.py" >> "$LOG/supervisor.log" 2>&1 < /dev/null &
sleep 2
if running_py_script "tools/supervise.py" >/dev/null 2>&1; then
  log "已拉起 supervise.py（日志：$LOG/supervisor.log）"
else
  log "⚠ supervise.py 拉起失败 —— 手工排查：python3 tools/supervise.py --once"
fi

# ── 4.5 拉起看门狗（该轮 该缺陷）─────────────────────────────────────────
# 为什么需要它：supervise 只监控子进程，**没有东西监控它自己**。
#   实测事故：supervisor.log 118.5 分钟零 tick，全场 27.1 分钟零活动，
#   靠人肉重启。看门狗只做一件事：心跳过期且确无 supervise 进程 → 拉起它。
#   它不碰 parallel_solver / shard / pi（那是 supervise 的职责），
#   也不在停止标记存在时动作（尊重停机）。
if running_py_script "tools/watchdog.py" >/dev/null 2>&1; then
  log "watchdog.py 已在运行 → 不重复启动"
else
  setsid nohup python3 "$ROOT/tools/watchdog.py" >> "$LOG/watchdog.log" 2>&1 < /dev/null &
  sleep 1
  if running_py_script "tools/watchdog.py" >/dev/null 2>&1; then
    log "已拉起 watchdog.py（日志：$LOG/watchdog.log）"
  else
    log "⚠ watchdog.py 拉起失败（不影响 supervise 本身，可手工补）"
  fi
fi
