#!/usr/bin/env bash
# =============================================================================
# PiCTF 一键部署（Linux / macOS / WSL / Git-Bash）
#
#   bash deploy.sh              # 标准部署（首次会 build 镜像）
#   bash deploy.sh --fast       # 跳过 build（镜像已存在时）
#   bash deploy.sh --rebuild    # 强制重建镜像
#   bash deploy.sh --status     # 只看状态
#
# 它做的事（幂等，可以反复跑）：
#   1. 检查 docker / compose
#   2. 检查 .env（没有就生成，并告诉你填哪一行）
#   3. build 镜像（仅当不存在或 --rebuild）
#   4. 启动容器 + 等健康检查
#   5. 打印访问地址与常用命令
#
# 为什么要有它：README 的三条命令对熟手够了，但新手会卡在
#   「.env 为什么必须填」「build 要多久」「起来了没有」这三处。
#   脚本把这三处都变成明确的输出。
# =============================================================================
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
ROOT="$(pwd)"
IMAGE="pictf-webui:0.2.4"
SERVICE="pictf"
PORT="30142"
FAST=0; FORCE=0; STATUS_ONLY=0
for a in "$@"; do
  case "$a" in
    --fast) FAST=1 ;;
    --rebuild) FORCE=1 ;;
    --status) STATUS_ONLY=1 ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "未知参数：$a（--fast / --rebuild / --status / --help）"; exit 2 ;;
  esac
done

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ── 1. 环境检查 ──────────────────────────────────────────────────────────────
say "1/5 检查环境"
command -v docker >/dev/null 2>&1 || die "没装 docker。装好后重跑：https://docs.docker.com/get-docker/"
ok "docker $(docker --version 2>/dev/null | sed 's/^Docker version //;s/,.*//')"
docker info >/dev/null 2>&1 || die "docker 守护进程没起来。启动 Docker Desktop / sudo systemctl start docker 后重跑。"
ok "docker 守护进程在跑"

DC="docker compose"
if ! docker compose version >/dev/null 2>&1; then
  if command -v docker-compose >/dev/null 2>&1; then DC="docker-compose"; warn "用旧版 docker-compose"; else
    die "缺 compose 插件。装 docker-compose-plugin 后重跑。"; fi
fi
ok "$($DC version 2>/dev/null | head -1)"

# ── 2. .env ──────────────────────────────────────────────────────────────────
say "2/5 检查 .env"
if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    cp .env.example .env
    warn "已从 .env.example 生成 .env —— 但里面还没有真实密钥"
    cat >&2 <<'EOF'

  请编辑 .env，至少填这两行（其余可留空）：

      DEEPSEEK_API_KEY=sk-你的key
      DEEPSEEK_BASE_URL=https://api.deepseek.com

  填完重跑：bash deploy.sh

  为什么必须填：容器里的 agent 靠它调模型；不填能启动，但解题时会 401。
EOF
    exit 1
  else
    die "仓库里没有 .env.example —— 这个包不完整，重新 clone。"
  fi
fi
ok ".env 存在"

# 检查 key 是不是还停在占位符
KEYLINE=$(grep -E '^DEEPSEEK_API_KEY=' .env | head -1 | cut -d= -f2- | tr -d '"'"'"' \r')
if [ -z "$KEYLINE" ] || printf '%s' "$KEYLINE" | grep -qiE '^(sk-?x+|your|changeme|todo|xxx|placeholder)$'; then
  warn "DEEPSEEK_API_KEY 看起来没填（当前：'$KEYLINE'）"
  warn "容器能起来，但解题会 401。建议先填好再跑。"
fi

# ── 3. 镜像 ──────────────────────────────────────────────────────────────────
say "3/5 检查镜像"
HAS_IMAGE=0
docker image inspect "$IMAGE" >/dev/null 2>&1 && HAS_IMAGE=1

if [ "$STATUS_ONLY" = "1" ]; then
  [ "$HAS_IMAGE" = "1" ] && ok "$IMAGE 已存在" || warn "$IMAGE 不存在（需要先 bash deploy.sh）"
else
  if [ "$HAS_IMAGE" = "1" ] && [ "$FORCE" = "0" ] && [ "$FAST" = "0" ]; then
    ok "$IMAGE 已存在 —— 跳过 build（要重建加 --rebuild）"
  elif [ "$FAST" = "1" ] && [ "$HAS_IMAGE" = "1" ]; then
    ok "--fast：跳过 build"
  else
    echo
    echo "  首次构建需要下载并安装全套 CTF 工具链（nmap/sqlmap/ghidra/sage…）。"
    echo "  镜像约 13.5 GB，耗时 20~40 分钟（取决于网速与磁盘）。"
    echo "  只有第一次需要，之后改代码不用重建（board/ tools/ 是 bind mount）。"
    echo
    printf "  继续？[Y/n] "
    read -r ans </dev/tty || ans=Y
    case "$ans" in [nN]*) echo "  已取消。"; exit 0 ;; esac
    $DC build "$SERVICE" || die "build 失败。看上面的报错；常见原因：磁盘不足（需 ~25GB 空闲）或网络拉取失败。"
    ok "镜像构建完成"
  fi
fi

[ "$STATUS_ONLY" = "1" ] && { $DC ps; exit 0; }

# ── 4. 启动 ──────────────────────────────────────────────────────────────────
say "4/5 启动容器"
$DC up -d "$SERVICE" || die "启动失败。看：$DC logs --tail 50 $SERVICE"
ok "容器已启动"

# ── 5. 健康检查 ──────────────────────────────────────────────────────────────
say "5/5 等健康检查（最多 90s）"
for i in $(seq 1 30); do
  ST=$(docker inspect "$SERVICE" --format '{{.State.Health.Status}}' 2>/dev/null || echo none)
  case "$ST" in
    healthy) ok "容器 healthy"; break ;;
    unhealthy) warn "容器 unhealthy —— 看日志：$DC logs --tail 50 $SERVICE"; break ;;
  esac
  [ "$i" = "30" ] && warn "90s 内没等到 healthy（容器可能仍在启动，稍后看 $DC ps）"
  sleep 3
done

echo
echo "────────────────────────────────────────────────────────────"
echo "  访问：  http://127.0.0.1:$PORT/"
echo "  进容器：docker exec -it $SERVICE bash"
echo "  看日志：$DC logs -f $SERVICE"
echo "  停止：  $DC down"
echo "  自检：  docker exec $SERVICE bash tools/bin/selfcheck --quick"
echo "────────────────────────────────────────────────────────────"
