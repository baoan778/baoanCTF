#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# home-fix.sh —— 修正 $HOME 下**降权后写不了**的运行时缓存属主（幂等）
#
# 为什么要有它（2026-11-19 实测，同一类 bug 的三个受害者）：
#   entrypoint ⓪ 段已经是 root 起手、修完 bind mount 属主再 setpriv 降权到 ctf。
#   但它只修了 workspace / board/projects / work/intake / log —— **没修 $HOME**。
#   于是「root 阶段顺手建过的东西」留成 root:root，降权后 ctf 写不进去，而且报错
#   都长得像别的问题：
#     · /home/ctf/.sage（drwx------ root root）→ sage 一启动就
#         PermissionError: '/home/ctf/.sage//matplotlib-1.5.1'
#       → crypto 的大整数分解 / Coppersmith / 小根攻击全废（命令在、跑不通）。
#     · /home/ctf/.ruff_cache/0.17.5（root:root）→ ruff 写临时文件 Permission denied
#       → selfcheck 判「ruff 发现致命问题」，**而代码一行没错**（--no-cache 实跑全绿）。
#     · /home/ctf/.cache/.pwntools-cache-3.12、/home/ctf/.npm/... 同类。
#
# 判据：不是"目录存在"，是"属主对不对"。只 chown 属主不是 ctf 的路径，幂等可重跑。
# 用法：bash tools/home-fix.sh [--check]
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

TARGET_USER="${PI_LAB_USER:-ctf}"
HOME_DIR="${HOME:-/home/$TARGET_USER}"

# 只碰这些「工具运行时缓存」目录；不碰 .pi-ctf / .local 里的业务数据属主之外的东西
DIRS="$HOME_DIR/.sage $HOME_DIR/.cache $HOME_DIR/.npm $HOME_DIR/.config $HOME_DIR/.local"

bad=0
fixed=0
for d in $DIRS; do
  [ -d "$d" ] || continue
  # find ! -user 拿不准时（比如用户不存在）就跳过，宁可不修不可乱改
  id -u "$TARGET_USER" >/dev/null 2>&1 || { echo "[home-fix] 没有用户 $TARGET_USER，跳过"; exit 0; }
  n=$(find "$d" ! -user "$TARGET_USER" 2>/dev/null | wc -l)
  [ "$n" -eq 0 ] && continue
  bad=$((bad + n))
  if [ "$CHECK" = "1" ]; then
    echo "[home-fix] 属主异常 $n 项: $d"
    continue
  fi
  # 需要 root 才能 chown；非 root 时不报错刷屏，只提示
  if [ "$(id -u)" != "0" ]; then
    echo "[home-fix] 需要 root 才能修 $d（当前 uid=$(id -u)）——容器入口会以 root 跑这段"
    continue
  fi
  find "$d" ! -user "$TARGET_USER" -exec chown "$TARGET_USER:$TARGET_USER" {} + 2>/dev/null || true
  left=$(find "$d" ! -user "$TARGET_USER" 2>/dev/null | wc -l)
  fixed=$((fixed + n - left))
done

if [ "$CHECK" = "1" ]; then
  [ "$bad" -eq 0 ] && echo "[home-fix] 全部正常（$bad 项异常）"
  exit $([ "$bad" -eq 0 ] && echo 0 || echo 1)
fi
echo "[home-fix] 已修 $fixed 项 / 发现 $bad 项（HOME=$HOME_DIR user=$TARGET_USER）"
exit 0
