#!/usr/bin/env bash
# =============================================================================
# PiCTF WebUI 容器入口
#
# 做的事（顺序不能换）：
#   1. 路径自检：PI_LAB_ROOT 下必须有 board/fgs.py —— 缺了说明挂错卷/镜像不完整
#   2. 首次启动铺 agent 配置（provision-agent.sh：写 models.json / settings.json）
#   3. 建运行时目录（sessions / board/projects / workspace）
#   4. 前台 exec 工作台服务
# =============================================================================
set -euo pipefail

ROOT="${PI_LAB_ROOT:-/opt/pi-ctf}"
AGENT="${PI_CODING_AGENT_DIR:-$HOME/.pi-ctf/agent}"
SESS="${PI_SESSION_DIR:-$HOME/.pi-ctf/sessions}"

log() { printf '[pictf] %s\n' "$*"; }
die() { printf '[pictf][FAIL] %s\n' "$*" >&2; exit 1; }

# ⓪ bind mount 权限修复（必须以 root 跑一次，然后立刻降权）
#    compose 把宿主的 ./workspace 与 ./board/projects 挂进容器，Docker Desktop 的
#    绑定层会把它们重建成 root:root 0755 —— 容器里的 ctf 用户完全不可写。
#    2026-11-01 实测：docker exec pictf touch /opt/pi-ctf/workspace/x
#      → bash: /opt/pi-ctf/workspace/.wtest: Permission denied
#    后果：文件上传落不了地、`fgs kickoff` 建不了 board/projects/<pid> → 黑板整条链路废掉。
#    镜像内是 root，入口先修属主/权限，再用 setpriv 降权到 ctf 执行真正的逻辑
#    （降权后 $0 重入，PICTF_ENTRY_DROPPED=1 防死循环）。
if [ "$(id -u)" = "0" ] && [ "${PICTF_ENTRY_DROPPED:-0}" != "1" ]; then
  for d in "$ROOT/workspace" "$ROOT/board/projects" "$ROOT/work/intake" "$ROOT/work/solve" "$ROOT/log"; do
    mkdir -p "$d" 2>/dev/null || true
    find "$d" ! -user ctf -exec chown ctf:ctf {} + 2>/dev/null || true
    chown ctf:ctf "$d" 2>/dev/null || true
    chmod 0775 "$d" 2>/dev/null || true
  done
  chown -R ctf:ctf "$(dirname "$AGENT")" 2>/dev/null || true
  # ★ 2026-11-19：HOME 下的运行时缓存也必须归 ctf —— 降权后 ctf 写不进去就是硬故障。
  #   实测：/home/ctf/.sage（root 建的 drwx------）→ sage 一启动就
  #     PermissionError: '/home/ctf/.sage//matplotlib-...'，crypto 的大整数分解/Coppersmith 全废；
  #   /home/ctf/.ruff_cache/0.17.5（root 建的）→ ruff 报 Permission denied，
  #     selfcheck 把它当成「代码有致命问题」（其实一行没错）。
  #   具体逻辑收在 tools/home-fix.sh（也能单独跑、带 --check），这里只调用以保证幂等且易复用。
  bash "$ROOT/tools/home-fix.sh" 2>/dev/null || true
  log "已修正 workspace / board/projects 属主（bind mount 被宿主重建成 root）"
  exec setpriv --reuid="$(id -u ctf)" --regid="$(id -g ctf)" --init-groups \
       env PICTF_ENTRY_DROPPED=1 HOME="$HOME" bash "$0" "$@"
fi

[ -f "$ROOT/board/fgs.py" ] || die "PI_LAB_ROOT=$ROOT 下找不到 board/fgs.py —— 镜像不完整或挂错了卷"
[ -f "$ROOT/work/ui/server.mjs" ] || die "找不到 work/ui/server.mjs（前端没带进镜像？）"

#   ★ 2026-11-20：work/solve 也要建 —— 它是 bind mount（compose 里新挂的），
#     宿主机目录不存在时 Docker 会自动建一个 root:root 的目录，
#     ctf 降权后写不进去 → 解题会话落不了盘、ledger 无法更新，表现是「一开跑就没反应」。
mkdir -p "$SESS" "$(dirname "$AGENT")" "$ROOT/workspace" "$ROOT/board/projects" "$ROOT/work/solve"

# ① agent 配置：卷是空的才铺（卷里已有用户配置时绝不覆盖）
if [ ! -d "$AGENT" ]; then
  mkdir -p "$AGENT"
  cp -a "$ROOT/agent-template/." "$AGENT/" 2>/dev/null || true
  log "已铺 agent 模板 → $AGENT"
fi
[ -e "$AGENT/skills" ] || ln -sfn "$ROOT/skills" "$AGENT/skills"

# ①b 老卷升级补齐：模板里新增的文件（如 sol-pi.json）在旧命名卷里没有，
#     而 `[ ! -d "$AGENT" ]` 只管「卷为空」那一次 —— 结果升版后扩展配置静默缺失。
#     只补**缺的**，绝不覆盖用户已改过的内容。
for f in "$ROOT"/agent-template/*.json; do
  b=$(basename "$f")
  [ "$b" = "models.json" ] && continue          # 模型配置归 provision 管，别抢
  [ -e "$AGENT/$b" ] || { cp -a "$f" "$AGENT/$b" && log "补齐模板配置 $b → $AGENT"; }
done

# ①c 扩展升级补齐（2026-11-05 加）：镜像里 agent-template/extensions 更新了，
#     但老卷里那份还是旧的 —— 「改了三态却看不到原始输出」就卡在这。
#     只在**缺文件或内容不同**时覆盖；模板扩展归仓库管，用户自己加的**也放 extensions/**
#     （只有 extensions/ 会被 pi 加载；历史上的 extensions-extra/ 2026-11-19 已删，它从来没被读过）。
# ①c 提示词同步（2026-11-21 R7-C）：agent-template/prompts/*.md
#   为什么单列一段：deepseek-flash-godmode.ts 从 $PI_CODING_AGENT_DIR/prompts/ 读词，
#   而镜像里那个目录**一直是空的** —— 于是每轮跑分日志都有一条「提示词缺失，已跳过注入」
#   （实测 138 次），flash 这个主力模型从来没被这份后缀调教过。
#   只补缺的、内容不同才覆盖（与扩展同一策略，不覆盖用户自己改过的）。
mkdir -p "$AGENT/prompts"
for f in "$ROOT"/agent-template/prompts/*.md; do
  [ -e "$f" ] || continue
  b=$(basename "$f")
  if [ ! -e "$AGENT/prompts/$b" ] || ! cmp -s "$f" "$AGENT/prompts/$b"; then
    cp -f "$f" "$AGENT/prompts/$b" && log "同步提示词 $b → $AGENT/prompts"
  fi
done

for f in "$ROOT"/agent-template/extensions/*.ts; do
  [ -e "$f" ] || continue
  b=$(basename "$f")
  if [ ! -e "$AGENT/extensions/$b" ] || ! cmp -s "$f" "$AGENT/extensions/$b"; then
    mkdir -p "$AGENT/extensions"
    cp -f "$f" "$AGENT/extensions/$b" && log "同步扩展 $b → $AGENT/extensions"
  fi
done

# ② 模型/默认设置：只在缺文件时生成（幂等；改过就不动）
bash /usr/local/bin/provision-agent.sh || die "provision-agent.sh 失败"

# ②·1 让系统 python3 也能 import venv 里的库（幂等；失败不阻断启动）
#   ★ 2026-11-19 P0：库全装在 tools/venv，而系统 python3 看不见 → agent 写 python3 就
#     ModuleNotFoundError，误判"缺库"白烧预算。脚本自带两条自检（import + 标准库未被遮盖）。
bash "$ROOT/tools/py-bridge.sh" || log "py-bridge.sh 失败（不阻断，python3 仍可用但少了 venv 库）"

# ②·2 自动恢复解题链路（幂等；失败不阻断启动）
#   ★ 2026-11-19 优化 07：容器重启后 parallel_solver 与 supervise.py 都不自动起 ——
#     实测 14:34 重启后空转 10 分钟才被人工发现，限时赛里等于直接丢分。
#   脚本自己有 4 道判据（停止标记 / 已在跑 / 平台已终结 / 平台在跑），
#   **宁可不启不可错启**：run 结束或全通关时绝不留下空转守护。
#   ★ 必须**后台化**：脚本里有平台探测（最长 60s），同步跑会把 UI 启动一起拖住。
#     setsid + nohup 是双保险：exec 之后本进程被替换，普通子进程会收 SIGHUP 被杀。
#   ★★ 2026-11-21 R6-P0-1（实测 27.1 分钟全场零活动）：原来只跑**一次**
#     solver-autostart.sh —— 它拉起的 supervise.py 一旦自己死了（容器重启/被 OOM/被误杀），
#     就再没有任何东西去看它。日志证据：supervisor.log 14:13:20 → 16:11:47 共
#     **118.5 分钟无 tick**，孤儿 shard 跑完后无人重启，靠人肉 16:11:47 才恢复。
#   现在改为常驻循环 autostart-loop.sh：每 60s 调一次 solver-autostart.sh，
#   幂等判据（停止标记/已在跑/run 已终结）全在那个脚本里，这里不重复实现，
#   所以循环本身随便崩、随便重启都不会重复拉起。
#   关掉循环（回到旧行为，只调一次）：PI_AUTOSTART_LOOP=off
if [ "${PICTF_AUTOSTART:-1}" != "0" ]; then
  setsid nohup bash "$ROOT/tools/autostart-loop.sh" >> "$ROOT/log/autostart.log" 2>&1 < /dev/null &
fi

# ③ 启动
log "工作台启动 → http://127.0.0.1:${UI_PORT:-30142}/  （root=$ROOT）"
exec node "$ROOT/work/ui/server.mjs"
