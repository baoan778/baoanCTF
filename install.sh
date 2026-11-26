#!/usr/bin/env bash
# ============================================================================
#  PiCTF WebUI 一键安装（WSL / Debian / Ubuntu）
#  做的事：系统工具 → Node → Python(venv+CTF库) → agent 配置 → 路径修正 → 自检
#  用法：  bash install.sh            （完整装：pwn/取证都给）
#          bash install.sh --minimal  （只装 web/misc/crypto 需要的最小集）
# ============================================================================
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
AGENT="$HOME/.pi-ctf/agent"
NODE_MIN_MAJ=22; NODE_MIN_MIN=19
MIRROR_PIP="https://pypi.tuna.tsinghua.edu.cn/simple"
MINIMAL=0
[ "${1:-}" = "--minimal" ] && MINIMAL=1

say(){ printf '\033[1;36m[install]\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
die(){ printf '\033[1;31m[fail]\033[0m %s\n' "$*"; exit 1; }

# ---------- 0. 环境 ----------
[ "$(uname -s)" = "Linux" ] || die "本包要在 Linux/WSL 里跑；Windows 请双击「安装.bat」"
command -v apt-get >/dev/null || die "只支持 Debian/Ubuntu（WSL 默认就是）"
command -v sudo >/dev/null || die "需要 sudo（apt 装系统工具要用）"
SUDO="sudo"

# ★ 2026-10-30 修（本机实测踩到）：如果用 root 跑本脚本（例如 wsl -u root），
#   $HOME 会变成 /root → agent 配置被铺到 /root/.pi-ctf，
#   而之后普通用户跑 start.sh 时 $HOME 是 /home/<user> → 直接报
#   「还没安装：先跑 bash install.sh」，而且看不出根因。
#   这里直接拒绝 root 执行并给出正确姿势，而不是把文件铺错地方。
if [ "$(id -u)" = 0 ]; then
  die "别用 root 跑 install.sh：agent 配置会铺到 /root/.pi-ctf，普通用户随后跑 start.sh 会找不到。
      正确做法二选一：
        · 普通用户直接跑：bash install.sh          （需要时脚本自己用 sudo 提权）
        · 确实要用 root：HOME=/home/<你的用户名> bash install.sh"
fi
export DEBIAN_FRONTEND=noninteractive
echo "wireshark-common wireshark-common/install-setuid boolean false" | $SUDO debconf-set-selections 2>/dev/null || true

# ---------- 1. 系统工具 ----------
say "1/5 安装系统工具（首次几分钟；--minimal 可跳过大部分）"
export DEBIAN_FRONTEND=noninteractive
$SUDO apt-get -o Acquire::http::Timeout=30 update -qq || warn "apt update 失败（继续试装本地缓存）"
BASE="python3 python3-venv python3-pip python3-dev file binutils xxd curl wget git jq zip unzip p7zip-full bc netcat-openbsd"
FULL="gdb patchelf nmap tshark binwalk squashfs-tools testdisk sleuthkit foremost ffmpeg sox multimon-ng qpdf poppler-utils mupdf-tools strace ltrace exiftool"
if [ "$MINIMAL" = 1 ]; then LIST="$BASE"; else LIST="$BASE $FULL"; fi
# shellcheck disable=SC2086
$SUDO apt-get install -y -qq -o Dpkg::Options::=--force-confdef --no-install-recommends $LIST \
  || warn "部分系统包装不上：不影响 web/misc，pwn/取证可能缺工具"
if [ "$MINIMAL" = 0 ]; then
  $SUDO apt-get install -y -qq -o Dpkg::Options::=--force-confdef --no-install-recommends \
    build-essential pkg-config cmake libssl-dev libffi-dev libglib2.0-dev libzbar0 libmagic1 libgmp-dev libmpfr-dev libmpc-dev \
    || warn "编译依赖没装全（个别 pip 包会失败）"
fi

# ---------- 2. Node（pi 要求 >= 22.19） ----------
say "2/5 检查 Node"
node_ok(){ [ -x "$1" ] || return 1; local v maj min rest; v=$("$1" -v 2>/dev/null | sed 's/^v//'); maj=${v%%.*}; rest=${v#*.}; min=${rest%%.*}
  { [ "${maj:-0}" -gt 22 ] || { [ "${maj:-0}" -eq 22 ] && [ "${min:-0}" -ge 19 ]; }; } 2>/dev/null; }
NODE=""
if command -v node >/dev/null 2>&1 && node_ok "$(command -v node)"; then NODE="$(command -v node)"; say "  用系统 Node: $($NODE -v)"
elif node_ok "$ROOT/vendor/node/node"; then NODE="$ROOT/vendor/node/node"; say "  用包内 Node: $($NODE -v)"
else
  say "  下载 Node v22（npmmirror）"
  mkdir -p "$ROOT/vendor/node" /tmp/pictf-node
  for U in "https://registry.npmmirror.com/-/binary/node/v22.23.2/node-v22.23.2-linux-x64.tar.xz" \
           "https://npmmirror.com/mirrors/node/v22.23.2/node-v22.23.2-linux-x64.tar.xz" \
           "https://nodejs.org/dist/v22.23.2/node-v22.23.2-linux-x64.tar.xz"; do
    if curl -fsSL --connect-timeout 20 -o /tmp/pictf-node/node.tar.xz "$U"; then
      tar -xJf /tmp/pictf-node/node.tar.xz -C "$ROOT/vendor/node" --strip-components=1 && NODE="$ROOT/vendor/node/bin/node" && break
    fi
  done
fi
[ -n "$NODE" ] || die "没有可用 Node（>=22.19）。把包内 vendor/node 保留，或自行安装 node 22 后重跑。"

# ---------- 3. Python 环境（分步装：任何一个包失败不会拖垮整批）----------
say "3/5 建 Python 环境（venv + CTF 库）"
PYS=""
for c in python3.12 python3.11 python3.10 python3; do command -v "$c" >/dev/null 2>&1 && PYS="$c" && break; done
[ -n "$PYS" ] || die "没有 python3"
say "  用 $PYS（$("$PYS" -V 2>&1)）—— 版本越老越不会遇到源码编译，慢属正常"
"$PYS" -m venv --system-site-packages "$ROOT/tools/venv" || die "venv 创建失败（缺 python3-venv？）"
VPIP="$ROOT/tools/venv/bin/pip"
MIRRORS="https://pypi.tuna.tsinghua.edu.cn/simple https://mirrors.cloud.tencent.com/pypi/simple https://mirrors.ustc.edu.cn/pypi/simple https://pypi.org/simple"
pip_try(){  # 用法：pip_try <参数...>   多镜像轮着来
  for M in $MIRRORS; do
    if "$VPIP" install -q --prefer-binary --timeout 60 --retries 2 -i "$M" "$@"; then return 0; fi
    warn "  镜像 $M 失败，换下一个"
  done
  return 1
}
pip_try -U pip || warn "pip 自升级失败（继续）"
say "  装核心库 pwntools/z3/pycryptodome/gmpy2 …（几分钟，别关窗）"
pip_try -r "$ROOT/requirements-core.txt" || warn "核心库没装全：pwn/crypto 会受影响，可稍后重跑 install.sh"
say "  装扩展库 unicorn/volatility3/yara/图片 …"
pip_try -r "$ROOT/requirements-extra.txt" || warn "扩展库部分失败（web/misc 不受影响）"
if [ -d "$ROOT/tools/gobin/RsaCtfTool" ]; then pip_try -e "$ROOT/tools/gobin/RsaCtfTool" --no-build-isolation >/dev/null 2>&1 || true; fi

# ---------- 4. agent 配置 ----------
say "4/5 铺 agent 配置（$AGENT）+ 建运行时目录"
# ★ 2026-10-30 修：包里没有 board/projects 与 workspace，而 server.mjs 的 boardIndex()
#   读不到会返回 error → 界面黑板页整页空白；workspace 缺失则题面文件无处可放。
mkdir -p "$ROOT/board/projects" "$ROOT/workspace" "$ROOT/log"
mkdir -p "$HOME/.pi-ctf" "$HOME/.pi-ctf/sessions"
if [ ! -d "$AGENT" ]; then cp -a "$ROOT/agent-template" "$AGENT"; say "  已铺（含子代理依赖，离线）"; else say "  已存在，保留你改过的内容"; fi
ln -sfn "$ROOT/skills" "$AGENT/skills"
[ -d "$AGENT/npm/node_modules/pi-subagents" ] || { mkdir -p "$AGENT/npm"; cp -a "$ROOT/agent-template/npm/node_modules" "$AGENT/npm/" 2>/dev/null || true; }

# ★ 2026-11-19 删 extensions-extra/：它是 extensions/ 的**整份旧快照**（2026-10-30 提交
#   a0698ac，14 个文件全部在 extensions/ 里有同名版本，独有文件数 = 0，其中 2 个还是旧代码）。
#   原先这里先铺 extra 再铺 extensions —— 同名必然被覆盖，等于白拷 14 个文件；
#   而 extra 不进 pi 的扩展发现路径（pi 只认 extensions/），删掉它对运行时零影响。
mkdir -p "$AGENT/extensions"
if [ -d "$ROOT/agent-template/extensions" ]; then
  cp -f "$ROOT/agent-template/extensions/"*.ts "$AGENT/extensions/" 2>/dev/null || true
  say "  扩展已铺进 extensions/（pi 只从 extensions/ 发现扩展）"
fi

# ---------- 5. 路径修正 + 自检 ----------
say "5/5 修正包内绝对路径 + 自检"
# ★ 2026-10-30 修：原范围漏了 prompts/ 与 agent-template/（实测各有 3 / 11 处硬编码路径），
#   漏掉时只有「打开某个提示词模板」时才炸，属于最难发现的那类。
for f in $(grep -rl "/mnt/d/AI/pi-lab" \
            "$ROOT/tools" "$ROOT/work/ui" "$ROOT/board" "$ROOT/skills" \
            "$ROOT/docs" "$ROOT/prompts" "$ROOT/agent-template" \
            "$ROOT/AGENTS.md" "$AGENT" 2>/dev/null \
          | grep -v node_modules | grep -v __pycache__ | grep -v '\.bak'); do
  sed -i "s|/mnt/d/AI/pi-lab|$ROOT|g" "$f"
done
chmod +x "$ROOT"/*.sh "$ROOT/tools/bin/"* 2>/dev/null || true
echo "  node: $($NODE -v)"
"$ROOT/tools/venv/bin/python" - <<'PYEOF' 2>/dev/null || warn "  py   : pwntools/z3 未就绪（web/misc 不受影响；可重跑 install.sh 补装）"
import pwn, z3, Crypto, gmpy2
print("  py   : pwntools", pwn.version.__version__ if hasattr(pwn, "version") else "", "| z3", z3.get_version_string(), "| gmpy2", gmpy2.version())
PYEOF
PI_LAB_ROOT="$ROOT" PI_LAB_KB_DIR="$ROOT/kb" "$ROOT/tools/venv/bin/python" "$ROOT/tools/kb.py" stats 2>/dev/null | head -2 || warn "  kb   : 自检失败"
echo
say "完成 ✅  启动：Windows 双击「启动UI.bat」 / Linux 跑 bash start.sh"
say "首次进入工作台后，在「模型」页填你自己的 baseUrl + apiKey（新会话生效）"
