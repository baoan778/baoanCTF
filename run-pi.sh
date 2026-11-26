#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# run-pi.sh —— 终端启动器 + 保活层（崩溃自动重启 / --continue 续跑 / 冷却闸门）
#
# 为什么需要（血账 2026-09-29）：WSL 从外部把整台发行版拆掉时 pi 吃 SIGKILL（rc=137），
# 主会话死在半截 toolCall 上、现场全丢。保活层把「人自己收工」和「被外部打死」分开：
#   人收工（0 / 1 / 129 / 130）→ 不拉起，退出码原样透传
#   崩溃（137 / 134 / 143 …）  → 退避后带 --continue 续跑
#   同一窗口连崩到 PI_LAB_KEEPALIVE_MAX 次 → 停下并打印怎么接着跑（冷却闸门）
#
# 用法：
#   bash run-pi.sh                  # 正常起
#   bash run-pi.sh --continue       # 手动续跑上次会话
#   bash run-pi.sh --session <id8>  # 钉住会话（此时**不再**叠加 --continue）
#
# 环境变量（board/tests/test_keepalive.py 用同名注入做隔离）：
#   PI_LAB_PI_BIN            pi 可执行体（默认 tools/bin/pi → 退到 PATH 上的 pi）
#   PI_LAB_LOG_DIR           日志目录（默认 <仓库根>/log）；台账 $PI_LAB_LOG_DIR/pi-restarts.jsonl
#   PI_LAB_SESSIONS          会话目录（导出成 PI_SESSION_DIR）
#   PI_LAB_KEEPALIVE_DELAY   退避初始秒数（默认 3，按 2 的幂递增）
#   PI_LAB_KEEPALIVE_MAX     同窗口最大重启次数（默认 3）
#   PI_LAB_KEEPALIVE=0       关掉保活：只跑一次，退出码原样透传
#
# 台账 pi-restarts.jsonl 每行一个 JSON：
#   {"ts":..,"action":"restart|stop","rc":137,"reason":"crash|cooldown","delay":1,"pinned":false}
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
LOG_DIR=${PI_LAB_LOG_DIR:-$ROOT/log}
SESS_DIR=${PI_LAB_SESSIONS:-${PI_SESSION_DIR:-$HOME/.pi-ctf/sessions}}
DELAY_BASE=${PI_LAB_KEEPALIVE_DELAY:-3}
KEEP_MAX=${PI_LAB_KEEPALIVE_MAX:-3}
KEEPALIVE=${PI_LAB_KEEPALIVE:-1}
RESTARTS="$LOG_DIR/pi-restarts.jsonl"
# ★ 重启预算**必须落盘**（R-2，2026-11-02）。
#   原来 `restarts` 只是 shell 内存变量：pi 崩了 -> 本脚本重启它没问题；
#   但**本脚本自己被外部打死再被拉起**（systemd/手敲/dsh 会话重启）时计数归零，
#   冷却闸门就形同虚设 —— 环境坏了会无限重启、无限烧钱。
#   sapper 也犯过这个错（watchdog 纯内存计数），清单里明确标为「必须避开」。
BUDGET_FILE=${PI_LAB_KEEPALIVE_BUDGET:-$LOG_DIR/keepalive-budget.json}
BUDGET_WINDOW=${PI_LAB_KEEPALIVE_WINDOW:-1800}   # 窗口秒数：窗口外的旧计数不算数

# ── P2-3：守护从「1 类」扩到「3 类」（2026-11-03）──────────────────────────
# 1) 进程死     → **已有**，行为原样不动（退避重启 + --continue + 冷却闸门）
# 2) 卡死不产出 → ★ **只报警，绝不动作**（不 kill、不重启、不写脏状态）
# 3) 额度墙     → 报警并**停手**（不无脑重启烧钱）
#
# 优先级（互斥且明确）：**额度墙 > 进程死 > 卡死**。
#   为什么必须这个顺序：欠费/限流常常表现为进程自己退出，若按"崩溃"处理就会
#   无限重启、每轮都烧一次失败的请求；所以额度判断必须站在崩溃判断**前面**。
#
# ★★ 为什么「卡死不产出」刻意做成一事不做：
#   守护层只负责"活下来"，**判断权必须集中在唯一一处** —— 两个大脑打架的代价是
#   杀掉正在深挖的会话（一个长跑的 exploit 调试看起来就是"很久没输出"）。
#   参考项目 watchdog 的 stall 分支明文写 `do NOTHING`；这里取同一取舍。
#
# ★ 诚实边界：「无新事件」用**会话文件不再增长**来近似。pi 的 TUI 心跳不落盘，
#   所以这个判据是**近似**的、会漏判（真的在思考但没写盘）。正因为是近似判据，
#   它才只许报警、不许据它做任何不可逆动作。
STALL_MIN=${PI_LAB_STALL_MIN:-10}          # 无事件多少分钟算卡死；0=关闭此检查
STALL_POLL=${PI_LAB_STALL_POLL:-15}        # 轮询秒数
EVENT_FILE=${PI_LAB_EVENT_FILE:-}          # 事件源；空=自动取 $SESS_DIR 最新会话文件
QUOTA_FILE=${PI_LAB_QUOTA_FILE:-}          # 额外额度标记来源；空=只查事件源
# 只认**真实可观测**的形态（HTTP 状态码 / 各家通用文案），不编造平台字段
QUOTA_MARKERS=${PI_LAB_QUOTA_MARKERS:-rate[ _-]?limit|too many requests|\b429\b|quota|insufficient|余额不足|额度不足|欠费|exceeded your current}
STALL_LOG="$LOG_DIR/pi-stall.jsonl"
QUOTA_LOG="$LOG_DIR/pi-quota.jsonl"

# pi 本体：显式指定 > 仓库入口 > PATH
PI_BIN=${PI_LAB_PI_BIN:-}
if [ -z "$PI_BIN" ]; then
  if [ -x "$ROOT/tools/bin/pi" ]; then PI_BIN="$ROOT/tools/bin/pi"
  else PI_BIN=$(command -v pi 2>/dev/null || true); fi
fi
if [ -z "$PI_BIN" ]; then
  echo "✗ run-pi.sh: 找不到 pi（设 PI_LAB_PI_BIN，或先跑 install.sh）" >&2
  exit 2
fi

mkdir -p "$LOG_DIR" "$SESS_DIR" 2>/dev/null || true
export PI_SESSION_DIR="$SESS_DIR"

# ★ 2026-11-02 修（实测坐实，不是推测）：**必须导 PI_LAB_ROOT**。
#   11 个扩展都写 `const LAB = process.env.PI_LAB_ROOT || process.cwd()`，靠 LAB 去找
#   <LAB>/tools/*.mjs 干活。本脚本此前**从不导出**它、也不 cd 进仓库 —— 于是从别处
#   （桌面、家目录、任何非仓库路径）启动时 LAB 落到错目录，扩展的 import 静默失败、
#   走 fail-open：**闸门一声不响地变成哑弹**，台账里连一条 arm 都没有。
#   实测：cwd=C:\Users\Administrator → 12 次工具调用 0 拦截；cwd=仓库根 → 正常拦 3 次。
#   start.sh（UI 模式）早就导出了，只有终端启动器漏 —— 这就是「要拦截却没拦截」的真因。
export PI_LAB_ROOT="$ROOT"

# ★ 2026-11-02 修（比上面那条更致命，实测坐实）：**必须导 PI_CODING_AGENT_DIR**。
#   不导就会落到 pi 默认的 ~/.pi/agent —— 那个目录**没有 extensions/**（它是另一个空壳）。
#   实测 `pi config` 对比，同一份 vendor pi、同一个 LAB：
#     · 不设 AGENT_DIR（= 本脚本修前的现状）→ "User (" 段 **0 个**，只剩 4 个内置扩展
#     · 设成 ~/.pi-ctf/agent              → "User (" 段 1 个，14 个自定义扩展在场
#   即：终端模式（run-pi.sh）下 **lane-watch / flag-watch / ctf-* 全都根本没被加载**，
#   不是"缺一个 .ts"，是**整个扩展目录换成了空壳**。这才是「要拦截却没拦截」的第一层。
#   start.sh（UI 模式）第 7 行早就导出了 —— 两个启动器长期不一致，而这个不一致
#   只在终端模式暴露，且表现为"安静地什么都没发生"。
export PI_CODING_AGENT_DIR="${PI_CODING_AGENT_DIR:-$HOME/.pi-ctf/agent}"

# ★ 2026-11-14 修 P2：把「启动时自动同步」**真正接上**。
#   脚本头 :14 与 docs/状态快照.md §0.6 都写「run-pi.sh / UI start.sh 启动时自动调」，
#   但实测两个启动器里**一处引用都没有** —— 于是 agent-template 改了不生效、
#   容器真身与仓库各自漂移，全靠人记得手跑。这里补上（失败不阻塞启动：
#   同步是便利设施，起不来 pi 也得能跑），并把输出收进 log/ 备查。
if [ -x "$ROOT/tools/pi-agent-sync.sh" ] || [ -f "$ROOT/tools/pi-agent-sync.sh" ]; then
  _sync_log="$ROOT/log/pi-agent-sync.log"
  mkdir -p "$ROOT/log" 2>/dev/null || true
  if bash "$ROOT/tools/pi-agent-sync.sh" >>"$_sync_log" 2>&1; then
    printf '[run-pi] pi-agent-sync OK（日志 %s）\n' "$_sync_log"
  else
    printf '[run-pi] ⚠ pi-agent-sync 失败（不阻塞启动，见 %s）\n' "$_sync_log" >&2
  fi
fi

# ★ 2026-11-01 修：仓库里的 .env（DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / PI_PROVIDER / PI_MODEL）
#   此前没有任何脚本加载它 → 交互式启动一路 "No models available"。
#   只补缺失变量，不覆盖调用方已显式导出的值（env 优先于 .env 是常识口径）。
if [ -f "$ROOT/.env" ]; then
  while IFS= read -r _line || [ -n "$_line" ]; do
    case "$_line" in ''|\#*) continue ;; esac
    _k=${_line%%=*}; _v=${_line#*=}
    case "$_k" in ''|*[!A-Za-z0-9_]*) continue ;; esac
    if [ -z "$(eval "printf %s \"\${$_k:-}\"")" ]; then export "$_k=$_v"; fi
  done < "$ROOT/.env"
  unset _line _k _v
fi

# ── 参数：--session 表示调用方钉住会话 ────────────────────────────────────────
ARGS=("$@")

# ── 默认参数：永远带一个非空参数（pi 会据此判定「人已授权本仓的 AGENTS.md / skills」）──
# 没有它时 pi 每次启动都要人手动点「信任项目文件」，无人值守场景会卡在信任提示上。
# 想退回旧行为：PI_LAB_PI_APPROVE=0
BASE=()
if [ "${PI_LAB_PI_APPROVE:-1}" != "0" ]; then
  has_approve=0
  for a in "${ARGS[@]:-}"; do
    case "$a" in --approve|-a|--no-approve|-na) has_approve=1 ;; esac
  done
  [ "$has_approve" = "1" ] || BASE+=("--approve")
fi
PINNED=""
for ((i = 0; i < ${#ARGS[@]}; i++)); do
  case "${ARGS[$i]}" in
    --session)   PINNED="${ARGS[$((i + 1))]:-}" ;;
    --session=*) PINNED="${ARGS[$i]#--session=}" ;;
  esac
done
PINNED_JSON=false
[ -n "$PINNED" ] && PINNED_JSON=true

# 台账：一行一个 JSON（人看得懂，jq 也吃）
log_row() {   # log_row <action> <rc> <reason> <delay> <pinned>
  printf '{"ts":"%s","action":"%s","rc":%s,"reason":"%s","delay":%s,"pinned":%s}\n' \
    "$(date -Is)" "$1" "$2" "$3" "$4" "$5" >> "$RESTARTS"
}

echo "[run-pi] pi=$PI_BIN  sessions=$SESS_DIR  log=$LOG_DIR" >&2

# ── 落盘的重启预算（R-2 修复）────────────────────────────────────────────────
# 只依赖 bash + date + 普通文件，不引 jq/python（启动器必须零依赖才跑得起来）。
budget_load() {
  # 输出「窗口内的重启次数」；文件不存在/坏掉一律当 0（不因台账坏了就不启动）
  [ -f "$BUDGET_FILE" ] || { echo 0; return; }
  local now_ts first_ts count
  now_ts=$(date +%s)
  # 第一行时间戳 + 第二行计数
  first_ts=$(sed -n '1p' "$BUDGET_FILE" 2>/dev/null)
  count=$(sed -n '2p' "$BUDGET_FILE" 2>/dev/null)
  case "$first_ts" in ''|*[!0-9]*) echo 0; return;; esac
  case "$count" in ''|*[!0-9]*) echo 0; return;; esac
  if [ $((now_ts - first_ts)) -gt "$BUDGET_WINDOW" ]; then
    echo 0                     # 窗口已过：旧计数作废（这就是「窗口」的意义）
  else
    echo "$count"
  fi
}

budget_save() {   # budget_save <count> —— count=0 时重置窗口起点
  local c="$1" now_ts
  now_ts=$(date +%s)
  if [ "$c" = "0" ]; then
    printf '%s\n0\n' "$now_ts" > "$BUDGET_FILE" 2>/dev/null || true
  else
    local first_ts
    first_ts=$(sed -n '1p' "$BUDGET_FILE" 2>/dev/null)
    case "$first_ts" in ''|*[!0-9]*) first_ts=$now_ts;; esac
    if [ $((now_ts - first_ts)) -gt "$BUDGET_WINDOW" ]; then first_ts=$now_ts; fi
    printf '%s\n%s\n' "$first_ts" "$c" > "$BUDGET_FILE" 2>/dev/null || true
  fi
}

attempt=0
restarts=$(budget_load)      # ★ 从盘上接着算，不从头开始
[ "$restarts" -gt 0 ] && echo "[run-pi] 本窗口内已有 $restarts 次重启（从 $BUDGET_FILE 恢复）" >&2

# ── P2-3 判据实现 ───────────────────────────────────────────────────────────
# 事件源：优先显式给，否则取会话目录里**最近改动**的那个文件（pi 会写会话 JSONL）
_event_source() {
  if [ -n "$EVENT_FILE" ]; then printf '%s' "$EVENT_FILE"; return; fi
  local newest=""
  if [ -d "$SESS_DIR" ]; then
    newest=$(ls -t "$SESS_DIR"/*.jsonl 2>/dev/null | head -1)
  fi
  printf '%s' "${newest:-}"
}

# 额度墙判据：只扫**事件源尾部**的文本 + 显式额度标记文件。
# 返回 0=命中额度墙。★ 只读，不动任何状态。
_quota_hit() {
  if [ -n "$QUOTA_FILE" ] && [ -f "$QUOTA_FILE" ]; then return 0; fi
  local src; src=$(_event_source)
  [ -n "$src" ] && [ -f "$src" ] || return 1
  # 只看尾部 64KB：额度提示一定是最后发生的事，全文件扫既慢又容易被历史日志误伤
  if tail -c 65536 "$src" 2>/dev/null | grep -qiE "$QUOTA_MARKERS"; then return 0; fi
  return 1
}

# 卡死判据：事件源 mtime 距今超过 STALL_MIN 分钟。
# ★ 只返回判定，**调用方只许报警**（见文件头 do NOTHING 的取舍说明）。
_stall_hit() {
  [ "$STALL_MIN" -gt 0 ] 2>/dev/null || return 1
  local src; src=$(_event_source)
  [ -n "$src" ] && [ -f "$src" ] || return 1
  local now mt age
  now=$(date +%s)
  mt=$(date -r "$src" +%s 2>/dev/null) || return 1
  age=$((now - mt))
  [ "$age" -ge $((STALL_MIN * 60)) ]
}

_stall_watch() {   # 后台看门狗：**只报警**，不碰主进程
  local pid="$1" warned=0
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$warned" = "0" ] && _stall_hit; then
      local src age_min
      src=$(_event_source)
      age_min=$(( ( $(date +%s) - $(date -r "$src" +%s) ) / 60 ))
      printf '{"ts":"%s","kind":"stall","minutes":%s,"src":"%s"}\n' \
        "$(date -Is)" "$age_min" "$src" >> "$STALL_LOG" 2>/dev/null || true
      echo "! [run-pi] 疑似卡死：${age_min} 分钟无新事件（阈值 ${STALL_MIN}m，源 ${src}）" >&2
      echo "    ★ 只报警，**不重启也不 kill** —— 长跑的深挖看起来就是这样，判断权留给人。" >&2
      echo "    要继续观察就不管；真确认死了再人工作业（bash run-pi.sh --continue）。" >&2
      warned=1        # 同一个 stall 事件只报一次，不刷屏
    fi
    sleep "$STALL_POLL" 2>/dev/null || sleep 5
  done
}

while :; do
  attempt=$((attempt + 1))
  CALL=("${BASE[@]}" "${ARGS[@]}")
  if [ "$attempt" -gt 1 ] && [ -z "$PINNED" ]; then
    CALL+=("--continue")        # 崩溃后是**续跑**，不是新开一个会话
  fi

  # 起后台 stall 看门狗（只报警）；进程结束就跟着收
  STALL_WD=""
  if [ "$STALL_MIN" -gt 0 ] 2>/dev/null; then
    _stall_watch $$ &
    STALL_WD=$!
  fi

  "$PI_BIN" "${CALL[@]}"
  rc=$?

  if [ -n "$STALL_WD" ]; then kill "$STALL_WD" 2>/dev/null || true; wait "$STALL_WD" 2>/dev/null || true; fi

  case "$rc" in
    0 | 1 | 129 | 130)
      # 人自己收工：正常结束 / 参数或密钥错 / SIGHUP / Ctrl+C —— 重试无用，别刷屏
      budget_save 0             # 干净收工 = 预算清零（下次是新的窗口）
      exit "$rc"
      ;;
  esac

  # ── 优先级 1：额度墙（**必须排在崩溃判断前**）────────────────────────────
  # 欠费/限流常表现为进程自己退出；若先按崩溃处理就会无限重启、每轮烧一次失败请求。
  if _quota_hit; then
    printf '{"ts":"%s","kind":"quota","rc":%s}\n' "$(date -Is)" "$rc" >> "$QUOTA_LOG" 2>/dev/null || true
    budget_save 0
    log_row stop "$rc" quota 0 "$PINNED_JSON"
    echo "✗ [run-pi] 疑似额度墙（rc=$rc）：命中限流/余额类特征 —— **停手，不再自动拉起**。" >&2
    echo "    这不是崩溃，重启只会继续烧钱。先确认额度/密钥，再人工续跑：bash run-pi.sh --continue" >&2
    echo "    （判据来源 ${QUOTA_FILE:-$(_event_source)}；台账 $QUOTA_LOG）" >&2
    exit "$rc"
  fi

  # ── 优先级 2：进程死（原有行为，原样保留）──────────────────────────────
  if [ "$KEEPALIVE" = "0" ]; then
    echo "[run-pi] 保活已关闭（PI_LAB_KEEPALIVE=0），退出码原样透传：rc=$rc" >&2
    exit "$rc"
  fi
  printf '%s rc=%s attempt=%s\n' "$(date -Is)" "$rc" "$attempt" >> "$LOG_DIR/pi-oom.txt" 2>/dev/null || true

  if [ $((restarts + 1)) -ge "$KEEP_MAX" ]; then
    budget_save $((restarts + 1))
    log_row stop "$rc" cooldown 0 "$PINNED_JSON"
    echo "✗ [run-pi] 同一窗口连崩 $((restarts + 1)) 次（rc=$rc）—— 冷却闸门停手，不再自动拉起。" >&2
    echo "    先看现场：log/pi-oom.txt、log/pi-restarts.jsonl" >&2
    echo "    要接着跑，人自己确认环境好了以后再敲：bash run-pi.sh --continue" >&2
    echo "    （预算在 $BUDGET_FILE，窗口 ${BUDGET_WINDOW}s；删掉它可手工清零）" >&2
    exit "$rc"
  fi

  delay=$((DELAY_BASE * (1 << restarts)))
  restarts=$((restarts + 1))
  budget_save "$restarts"
  log_row restart "$rc" crash "$delay" "$PINNED_JSON"
  echo "! [run-pi] pi 崩了（rc=$rc，第 $restarts 次）—— ${delay}s 后带 --continue 续跑（台账 $RESTARTS）" >&2
  [ "$delay" -gt 0 ] && sleep "$delay"
done
