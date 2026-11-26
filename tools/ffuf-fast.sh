#!/bin/bash
# ffuf-fast.sh <url-with-FUZZ> [ffuf 额外参数] —— 目录扫描的唯一推荐入口
#
# 为什么要有（2026-11-19，optimizations/02-目录扫描失控.md）：
#   实测单次全量 ffuf 吃掉 70.1s / 50.2s / 17.6s，>10s 的调用占工具总时长 35%（298s/859s），
#   而它们几乎没有带来新攻击面 —— 字典太大了，且没有硬上限，agent 会一直扫到预算烧完。
#   本脚本把「限时」变成不可能忘记的事：硬上限 20s（可用 FFUF_SEC 覆盖）。
#
# ★ 字典只能是**文件**，不能只给目录：
#   tools/wordlists/dirsx/ 下只有 Version.txt / dicts / windows_start.bat，
#   写成 dirsx/ 会让 ffuf 拿 Version.txt 当字典，结果恒为空（实测踩过）。

set -u
U="${1:?用法: ffuf-fast.sh <url-with-FUZZ> [额外参数]}"; shift
SEC="${FFUF_SEC:-20}"
DIR=/opt/pi-ctf/tools/wordlists/dirsx/dicts
SMALL="$DIR/fuzzing-dirs-dirsearch.txt"     # 9638 行，快
MID="$DIR/fuzzing-dirs-common.txt"          # 177694 行，全但慢

WL="$SMALL"
[ -f "$WL" ] || WL="$MID"
if [ ! -f "$WL" ]; then
  echo "[ffuf-fast] 找不到字典（$SMALL / $MID）" >&2
  exit 2
fi

echo "[ffuf-fast] 字典=$(basename "$WL") 上限=${SEC}s 目标=$U"
timeout "$SEC" ffuf -s -mc 200,204,301,302,307,401,403 \
  -t 50 -timeout 5 -w "$WL" -u "$U" "$@" 2>/dev/null
rc=$?
if [ $rc -eq 124 ]; then
  echo "[ffuf-fast] 到 ${SEC}s 上限已停止 —— ★ 不要再加大字典重扫，换攻击面（登录/IDOR/注入/上传）" >&2
fi
exit $rc
