#!/bin/bash
# ensure-tool.sh —— 缺什么装什么（把「缺工具就自己装」变成一条命令）
#
# 为什么要有（2026-11-14）：跑分容器里 py 环境缺 itsdangerous/flask/jwt，
#   agent 写 import 直接 ImportError，整轮 300 秒白烧。用户明确要求：
#   **解题时缺少工具要自行下载配置，不许卡住、不许让用户反复强调。**
#
# 用法:
#   bash /opt/pi-ctf/tools/ensure-tool.sh <python模块名> [<python模块名> ...]
#   bash /opt/pi-ctf/tools/ensure-tool.sh --apt <系统包名>
#   bash /opt/pi-ctf/tools/ensure-tool.sh --check        # 体检常用工具
set -uo pipefail
PY=/opt/pi-ctf/tools/venv/bin/python
export PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# 常见「模块名 → pip 包名」映射（import 名和包名不一致的那些）
pip_name() {
  case "$1" in
    jwt) echo pyjwt ;;
    Crypto) echo pycryptodome ;;
    pwn) echo pwntools ;;
    bs4) echo beautifulsoup4 ;;
    yaml) echo pyyaml ;;
    PIL) echo pillow ;;
    cv2) echo opencv-python-headless ;;
    websocket) echo websocket-client ;;
    MySQLdb) echo mysqlclient ;;
    psycopg2) echo psycopg2-binary ;;
    *) echo "$1" ;;
  esac
}

check_all() {
  echo "=== Python 库"
  for m in requests itsdangerous flask jwt Crypto pwn bs4 lxml z3 websocket paramiko; do
    if $PY -c "import $m" 2>/dev/null; then echo "  $m OK"; else echo "  $m 缺"; fi
  done
  echo "=== 命令行工具"
  for t in sqlmap ffuf nmap jwt_tool curl python3 nc; do
    if command -v "$t" >/dev/null 2>&1; then echo "  $t OK"; else echo "  $t 缺"; fi
  done
}

if [ "${1:-}" = "--check" ] || [ $# -eq 0 ]; then
  check_all
  exit 0
fi

if [ "${1:-}" = "--apt" ]; then
  shift
  echo "系统包需要 root，容器里若不可写就换成 pip 等价包：$*"
  exit 3
fi

rc=0
for mod in "$@"; do
  if $PY -c "import $mod" 2>/dev/null; then
    echo "$mod 已存在，跳过"
    continue
  fi
  pkg=$(pip_name "$mod")
  echo "安装 $pkg（导入名 $mod）..."
  if timeout 180 $PY -m pip install --no-input --retries 5 --timeout 30 "$pkg" 2>&1 | tail -3; then
    if $PY -c "import $mod" 2>/dev/null; then
      echo "$mod 安装成功"
    else
      echo "$mod 装完仍导入失败（可能包名不同，试 --check 看清单）"
      rc=1
    fi
  else
    echo "$mod 安装失败"
    rc=1
  fi
done
exit $rc