#!/bin/bash
# pwnsetup —— 把题目二进制改成"用题目自带 libc/ld 跑"（pwninit 的轻量替代）
#
# 为什么不用 pwninit：本机没装，且它的 GitHub release 走 ghproxy 不稳（2026-10-10 实测）。
# 而 pwninit 干的核心就是三件事：拷 libc/ld 到题目目录 + patchelf 改 interpreter/rpath + 打印检查信息。
#
# 用法：
#   bash tools/pwnsetup.sh <chal> [libc.so.6] [ld-linux-x86-64.so.2] [目标目录]
#   例：bash tools/pwnsetup.sh ./chal ./libc.so.6 ./ld-2.31.so work/pwn-x
#   不给 libc/ld 时用**本机**的（只用于"本地先跑通"，靶机 libc 不同就要传对应的）
#
# 输出：目标目录里的 chal/libc/ld + 改好的 interpreter + ldd 结果 + 建议的运行命令
set -uo pipefail
CHAL="${1:?用法: pwnsetup.sh <chal> [libc] [ld] [dir]}"
LIBC="${2:-/lib/x86_64-linux-gnu/libc.so.6}"
LD="${3:-}"
if [ -z "$LD" ]; then
  for c in /lib64/ld-linux-x86-64.so.2 /lib/x86_64-linux-gnu/ld-linux-x86-64.so.2; do
    [ -f "$c" ] && LD="$c" && break
  done
fi
DIR="${4:-$(dirname "$CHAL")/pwnenv}"
[ -f "$CHAL" ] || { echo "✗ 找不到题目: $CHAL" >&2; exit 1; }
[ -f "$LIBC" ] || { echo "✗ 找不到 libc: $LIBC" >&2; exit 1; }
[ -f "$LD" ]   || { echo "✗ 找不到 ld: $LD（可显式传第 3 个参数）" >&2; exit 1; }
command -v patchelf >/dev/null || { echo "✗ 没有 patchelf" >&2; exit 1; }

mkdir -p "$DIR" && cp -f "$CHAL" "$DIR/chal" && cp -f "$LIBC" "$DIR/libc.so.6" && cp -f "$LD" "$DIR/ld.so"
chmod +x "$DIR/chal" "$DIR/ld.so"
patchelf --set-interpreter "$DIR/ld.so" --set-rpath "$DIR" "$DIR/chal" || { echo "✗ patchelf 失败" >&2; exit 1; }

echo "① 已就绪: $DIR/{chal,libc.so.6,ld.so}"
echo "② interpreter: $(patchelf --print-interpreter "$DIR/chal" 2>/dev/null)"
echo "③ rpath:       $(patchelf --print-rpath "$DIR/chal" 2>/dev/null)"
echo "④ libc 版本:   $("$DIR/libc.so.6" 2>/dev/null | head -1)"
echo "⑤ ldd:"; ldd "$DIR/chal" 2>/dev/null | sed 's/^/     /'
echo "⑥ 跑它:        cd $DIR && ./chal        # pwntools: p = process('./chal', cwd='$DIR')"
echo "   ⚠ 本机 glibc 是 2.43；若靶机 libc 不同（拿到 leak 后用 \`libcsearch\` 定版本并下载对应 libc.so.6），"
echo "     必须换成那份 libc 再 setup 一次 —— 否则本地能跑通、远程偏移全错。"
