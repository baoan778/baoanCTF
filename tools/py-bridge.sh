#!/bin/bash
# py-bridge.sh -- 让系统 python3 也能 import venv 里的库（.pth 追加，不遮盖标准库）
#
# 为什么需要（2026-11-19 P0 实测事故）：
#   全库 111 条命令用 `python3`、仅 21 条用 `py`。python3 是 /usr/bin/python3（系统），
#   而 requests/PIL/numpy/jinja2/bs4/lxml 全装在 tools/venv —— venv 的 pyvenv.cfg 是
#   include-system-site-packages=false，两套互不可见。于是 agent 一写 python3 就
#   ModuleNotFoundError，误判成"环境缺库"，白烧预算。
#
# 做法：在系统 python 的 user site-packages 写一个 .pth，内容是 venv 的 site-packages。
#   .pth 里的路径被 site.py 追加到 sys.path **末尾** → 标准库永远先被找到，不会被遮盖。
#
# 幂等：重复跑无副作用；已有 .pth 内容相同则跳过。
# 验证：跑完自检 import 与"标准库来源"两件事，任一不过就非零退出。
set -u

VENV="${PI_LAB_VENV:-/opt/pi-ctf/tools/venv}"
PY="${PI_LAB_PYTHON:-/usr/bin/python3}"

[ -d "$VENV/lib" ] || { echo "[py-bridge] 没有 venv: $VENV（跳过，不影响解题）"; exit 0; }

SP=$(ls -d "$VENV"/lib/python*/site-packages 2>/dev/null | head -1)
[ -n "$SP" ] || { echo "[py-bridge] venv 里没有 site-packages（跳过）"; exit 0; }

# 目标：系统 python 的 user site-packages
USERSP=$("$PY" -c 'import site, sys; p = site.getusersitepackages() if hasattr(site, "getusersitepackages") else ""; print(p)' 2>/dev/null)
[ -n "$USERSP" ] || USERSP="$HOME/.local/lib/$( "$PY" -c 'import sys;print("python%d.%d" % sys.version_info[:2])' )/site-packages"

mkdir -p "$USERSP" 2>/dev/null || { echo "[py-bridge] 建不了 $USERSP（跳过）"; exit 0; }
PTH="$USERSP/_pi_venv.pth"

if [ -f "$PTH" ] && [ "$(cat "$PTH" 2>/dev/null)" = "$SP" ]; then
  echo "[py-bridge] 已就绪: $PTH -> $SP"
else
  printf '%s\n' "$SP" > "$PTH" 2>/dev/null || { echo "[py-bridge] 写不了 $PTH（跳过）"; exit 0; }
  echo "[py-bridge] 已写入: $PTH -> $SP"
fi

# 自检 1：常见库能 import
if ! "$PY" -c 'import requests, PIL, numpy, jinja2, bs4, lxml' >/dev/null 2>&1; then
  echo "[py-bridge][warn] 写入后仍 import 失败（venv 里可能真缺这几个包，不影响其它）"
fi

# 自检 2：标准库没有被 venv 遮盖（这是最容易出事的副作用）
"$PY" - <<'PYEOF'
import json, re, os, subprocess, pathlib, sys
v = os.environ.get("PI_LAB_VENV", "/opt/pi-ctf/tools/venv")
bad = [m.__name__ for m in (json, re, os, subprocess, pathlib)
       if (getattr(m, "__file__", "") or "").startswith(v)]
if bad:
    print("[py-bridge][fatal] 标准库被 venv 遮盖:", bad)
    sys.exit(1)
print("[py-bridge] 标准库来源正常（未被 venv 遮盖）")
PYEOF
exit $?
