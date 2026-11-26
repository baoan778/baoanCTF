#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# pi-agent-sync —— 同步「被 git 跟踪的 pi 配置」两份副本
#
#   仓库侧（git 跟踪，真源）  : <repo>/agent-template/   （旧写法 .pi/config/ 早不存在）
#   运行侧（pi 实际读取，ext4）: /root/pi-lab-agent/     ← PI_CODING_AGENT_DIR 指这里
#
# 为什么要有两份：pi 每次启动对 agent 目录做 ~2000 次文件访问（auth.json ×1106、
# npm 包目录 ×608…），`/mnt/d` 是 9p(drvfs)，每次 ~4ms → 冷启动 11~25s；ext4 上 <1s。
# 实测「ext4 真身 + 软链回仓库」比「ext4 放真文件」每启动多花 ~3.9s（9p 过路费），
# 所以运行侧放真文件，用本脚本在两个方向做「mtime 新的覆盖旧的」同步 ——
# 无论你在仓库里改，还是让 pi 直接改了运行侧，都不会丢。
#
# 用法：bash tools/pi-agent-sync.sh            # 一般不用手跑：run-pi.sh 启动时会自动调（2026-11-14 起真正接上；UI 侧见 start.sh）
#       PI_LAB_AGENT_DIR=/xxx bash tools/pi-agent-sync.sh   # 换真身位置时
#
# 注：不做删除传播（一边删了文件、另一边会留着）；删文件请两边都删。
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ★ 2026-11-02 修：SRC 原写死 `<repo>/.pi/config`，但**这个目录从来不存在** ——
#   于是第 25 行 `[ -d "$SRC" ] || exit 0` 静默跳过，roles/settings 一直没同步过。
#   现在按优先级探测真源，第一个存在的赢：
#     $PI_LAB_AGENT_SRC（显式指定）→ <repo>/agent-template（实际存在）→ <repo>/.pi/config（旧约定）
_pick_src() {
  local c
  for c in "${PI_LAB_AGENT_SRC:-}" "$ROOT/agent-template" "$ROOT/.pi/config"; do
    [ -n "$c" ] && [ -d "$c" ] && { printf '%s' "$c"; return 0; }
  done
  return 1
}

# ★ 2026-11-02 修：LIVE 原写死 /root/pi-lab-agent —— 本机非 root 用户根本不存在这个目录，
#   脚本静默跳过。现在按优先级探测：显式指定 → ~/.pi-ctf/agent（实测存在）→ /root/pi-lab-agent。
_pick_live() {
  local c
  for c in "${PI_LAB_AGENT_DIR:-}" "$HOME/.pi-ctf/agent" "/root/pi-lab-agent"; do
    [ -n "$c" ] && [ -d "$c" ] && { printf '%s' "$c"; return 0; }
  done
  return 1
}

# ★ 显式指定必须**严格生效**：若用户给了 $PI_LAB_AGENT_SRC/LIVE 而它不存在，
#   要直接报错退出，而不是悄悄回退到默认值 —— 否则「我明明指了路径却没生效」
#   这种更难查的坑就来了（与本次修的静默跳过是同一类病）。
if [ -n "${PI_LAB_AGENT_SRC:-}" ] && [ ! -d "$PI_LAB_AGENT_SRC" ]; then
  echo "[sync] ERROR: PI_LAB_AGENT_SRC=$PI_LAB_AGENT_SRC 不存在" >&2; exit 2
fi
if [ -n "${PI_LAB_AGENT_DIR:-}" ] && [ ! -d "$PI_LAB_AGENT_DIR" ]; then
  echo "[sync] ERROR: PI_LAB_AGENT_DIR=$PI_LAB_AGENT_DIR 不存在" >&2; exit 2
fi

SRC="$(_pick_src || true)"
LIVE="$(_pick_live || true)"

# 静默跳过是这次 bug 藏了这么久的根本原因 —— 现在跳不过去要喊出来（stderr，不打扰正常输出）
if [ -z "$SRC" ]; then
  echo "[sync] SKIP: 找不到仓库侧配置目录（试过 \$PI_LAB_AGENT_SRC / $ROOT/agent-template / $ROOT/.pi/config）" >&2
  exit 0
fi
if [ -z "$LIVE" ]; then
  echo "[sync] SKIP: 找不到运行侧 agent 目录（试过 \$PI_LAB_AGENT_DIR / $HOME/.pi-ctf/agent / /root/pi-lab-agent）" >&2
  exit 0
fi
if [ "$LIVE" = "$SRC" ]; then echo "[sync] SKIP: 源和目标是同一个目录（$SRC）" >&2; exit 0; fi
if ! command -v rsync >/dev/null 2>&1; then echo "[sync] SKIP: 没有 rsync" >&2; exit 0; fi

echo "[sync] 源: $SRC"
echo "[sync] 靶: $LIVE"

pushed=0; pulled=0
# 注：sol-pi.json = SoL-Pi 四个机制的开关（<agentDir>\sol-pi.json），也得跟着同步，否则仓库改了不生效
# ★ 2026-10-13 补 roles：角色定义（含 prompt 正文 / thinkingLevel / autoCompaction 这类声明）
#   以前**根本不在同步清单里** —— UI 是从 <agentDir>/roles 读的（server.mjs: ROLES_DIR），
#   于是仓库里改角色 = 改了个没人读的文件（实战踩到：roles-pack/ctf.json 改 autoCompaction 不生效）。
# ★ 2026-11-05 修：原实现「两边各跑一次 rsync -au」= mtime 新的赢，但真身(容器/实盘)往往
#   被 pi 运行时改写而 mtime 更新，于是实盘反向覆盖了仓库 settings.json —— 实测把
#   agent-template/settings.json 里 10 个角色的 agentOverrides 整段抹掉（模板从 11-02 退回 11-05）。
#   现在改为「单向下发 + 冲突告警」：
#     · settings.json / sol-pi.json / mcp-adapter.json —— 仓库是真源，永远仓库→真身；
#       若真身更新且内容不同，打印告警并保留真身为 .live-conflict 副本备查，不静默覆盖仓库。
#     · roles/ prompts/ themes/ extensions/ —— 两边都可能改（UI 会写 roles），保留双向但先备份。
# ★ 2026-11-06 补 agents/：pi-subagents **只**从 <agentDir>/agents/*.md 发现 agent 定义，
#   而 roles/ 它根本不读（roles/ 只被我们自己的 WebUI 读）。三档配置因此整整一周没生效。
#   agents/ 由 roles/+prompts/ 单向派生（tools/gen-agent-bridge.py），所以也是单向：仓库→真身。
#   若不派生就直接下发，会下发一份过期的 agents/ ——所以先跑派生，漂移就喊出来。
SINGLE_WAY="settings.json sol-pi.json mcp-adapter.json agents"
if [ -f "$ROOT/tools/gen-agent-bridge.py" ] && [ -d "$SRC/roles" ]; then
  if drv=$(python3 "$ROOT/tools/gen-agent-bridge.py" --check 2>&1); then
    echo "[sync] agents/ 与 roles/ 一致（派生无漂移）"
  else
    echo "[sync] ⚠ agents/ 与 roles/ 漂移 —— 先跑 python3 tools/gen-agent-bridge.py 再同步：" >&2
    printf '%s\n' "$drv" | head -5 | sed 's/^/[sync]   /' >&2
  fi
fi
for item in extensions themes prompts roles agents settings.json sol-pi.json mcp-adapter.json models.json.example; do
  [ -e "$SRC/$item" ] || continue
  case " $SINGLE_WAY " in
    *" $item "*)
      if [ -f "$LIVE/$item" ] && [ "$LIVE/$item" -nt "$SRC/$item" ] && ! cmp -s "$SRC/$item" "$LIVE/$item"; then
        echo "[sync] ⚠ 冲突: $item 真身更新且内容不同（真源=仓库，仍下发）" >&2
        # ★ 2026-11-14 修：内容没变就别重写备份，否则每次同步都重生一份垃圾
        #   （实测 .live-conflict 与 settings.json 逐字节相同，纯噪音）。
        if [ -f "$LIVE/$item.live-conflict" ] && cmp -s "$LIVE/$item" "$LIVE/$item.live-conflict"; then
          : # 备份已是最新，不重写（也就不再刷 mtime）
        else
          cp -a "$LIVE/$item" "$LIVE/$item.live-conflict"
        fi
        echo "[sync]   真身旧版已存为 $item.live-conflict（内容不同，未覆盖仓库）" >&2
      fi
      # ★ 2026-11-14 修 P2（实测）：原来是 `rsync -au` —— `-u` 是「目标更新就跳过」，
      #   正好和「单向下发、仓库是真源」相反：只要真身被 pi/UI 重写过一次（mtime 更新），
      #   仓库的修复就永远推不下去，而 :103 的注释还写着「仍下发」。
      #   实测：真身 settings.json 11-14 10:53 / 仓库 11-07 16:04，只差一个尾换行，
      #   `rsync -au --dry-run` 无输出（跳过），`rsync -a --dry-run` 才会下发。
      #   改为 --checksum：按内容而不是时间戳决定，真身内容真不同才覆盖。
      out=$(rsync -a --checksum --out-format='%n' "$SRC/$item" "$LIVE/" 2>/dev/null)
      [ -n "$out" ] && { pushed=$((pushed+1)); echo "[sync] 仓库→真身(单向): $item"; }
      ;;
    *)
      if [ -d "$SRC/$item" ]; then
        out=$(rsync -au --out-format='%n' "$SRC/$item/" "$LIVE/$item/" 2>/dev/null); [ -n "$out" ] && { pushed=$((pushed+1)); echo "[sync] 仓库→真身: $item"; }
        out=$(rsync -au --out-format='%n' "$LIVE/$item/" "$SRC/$item/" 2>/dev/null); [ -n "$out" ] && { pulled=$((pulled+1)); echo "[sync] 真身→仓库: $item"; }
      else
        out=$(rsync -au --out-format='%n' "$SRC/$item" "$LIVE/" 2>/dev/null); [ -n "$out" ] && { pushed=$((pushed+1)); echo "[sync] 仓库→真身: $item"; }
        out=$(rsync -au --out-format='%n' "$LIVE/$item" "$SRC/" 2>/dev/null);    [ -n "$out" ] && { pulled=$((pulled+1)); echo "[sync] 真身→仓库: $item"; }
      fi
      ;;
  esac
done

# 软链情形（历史遗留：真身里还是指向仓库的软链）——直接换成真文件，否则每次启动白交 9p 过路费
for item in extensions themes prompts roles settings.json sol-pi.json mcp-adapter.json models.json.example; do
  if [ -L "$LIVE/$item" ]; then
    target="$(readlink -f "$LIVE/$item" 2>/dev/null || true)"
    rm -f "$LIVE/$item"
    if [ -n "$target" ] && [ -e "$target" ]; then cp -a "$target" "$LIVE/$item"; else cp -a "$SRC/$item" "$LIVE/$item"; fi
    echo "[sync] 软链→真文件: $item"
  fi
done

[ "$pushed" = 0 ] && [ "$pulled" = 0 ] && echo "[sync] 已一致（仓库 ↔ $LIVE）"
exit 0
