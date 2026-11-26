#!/usr/bin/env bash
# findflag.sh —— 安全地在靶机里找 flag 文件（**替代 `find / -name '*flag*'`**）
#
# 为什么需要它（optimizations/15）：
#   实测 agent 拿到 shell 后的第一个动作常是 `find / -maxdepth 4 -iname '*flag*'`。
#   /proc 也在扫描范围内，而读 **D 态进程**的 /proc/<pid>/cmdline 会永久阻塞 ——
#   一次全盘扫就能把容器 /proc 咬死，连带 procscan / 实况页 / 守护自启全部超时，
#   最后连 docker restart 都失败（`did not receive an exit event`）。
#
# 用法（在靶机里跑）：
#   bash findflag.sh              # 常用落点 + maxdepth 3
#   bash findflag.sh /var/www     # 指定根
#   bash findflag.sh --deep       # 加深到 5 层（仍不碰 /proc /sys /dev）
#
# 设计：把 /proc /sys /dev /run 排除在搜索根之外，而不是靠 -path 过滤 ——
#   因为 find 在遍历阶段就会 stat 到它们，过滤器救不回已经被咬住的读取。
set -u
DEEP=0
ROOTS=()
for a in "$@"; do
  case "$a" in
    --deep) DEEP=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) ROOTS+=("$a") ;;
  esac
done

MAXD=3
[ "$DEEP" = "1" ] && MAXD=5

if [ ${#ROOTS[@]} -eq 0 ]; then
  # 常见 flag 落点：按命中概率排序，全都不存在就跳过（2>/dev/null）
  for d in /flag /flag.txt /tmp /home /app /var/www /opt /srv /usr/src /root /mnt; do
    [ -e "$d" ] && ROOTS+=("$d")
  done
fi
[ ${#ROOTS[@]} -eq 0 ] && { echo "[findflag] 没有可搜索的目录"; exit 1; }

echo "[findflag] 搜索根：${ROOTS[*]}（maxdepth $MAXD，已排除 /proc /sys /dev /run）"
# ★ 整条命令加硬限时：即使某个根卡住也只损失 20s，不会把 shell 拖死
timeout 20 find "${ROOTS[@]}" \
  -maxdepth "$MAXD" \
  \( -path /proc -o -path /sys -o -path /dev -o -path /run \) -prune -o \
  -type f \( -iname '*flag*' -o -iname '*secret*' -o -iname '*.key' \) -print 2>/dev/null \
  | head -40

rc=${PIPESTATUS[0]}
if [ "$rc" = "124" ]; then
  echo "[findflag] 到 20s 上限已停 —— 缩小搜索根（如 /tmp /var/www）再来，别对 / 重扫" >&2
fi
exit 0
