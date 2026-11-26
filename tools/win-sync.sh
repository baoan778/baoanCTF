#!/bin/bash
# win-sync.sh —— 把 Windows 侧的逆向/取证工具同步进 tools/win/（仓内自用副本）
#
# 为什么存在：这些工具有 Windows 版、只有 Windows 版，而我在 WSL 里干活；
# 它们原先散在 D:\AI\...（别人的工具目录）—— 拷一份到自己目录，依赖才自洽。
# 幂等：已存在且体积一致就跳过；--check 只看不拷。
set -uo pipefail
T="$(cd "$(dirname "$0")" && pwd)"
DEST="$T/win"
SRCS=(
  "/mnt/d/AI/dsh/aigongju"
  "/mnt/d/AI/codex/shentou2/aigongju"
  "/mnt/d/AI/gongju"
)
TOOLS=(IDA_Pro_v8.3_Portable ghidra_11.4.1_PUBLIC CFF_Explorer GDA DIE_win64_portable_3.10_x64 ExeinfoPe LogParser)

CHECK=0; [ "${1:-}" = "--check" ] && CHECK=1
mkdir -p "$DEST"
missing=0
for name in "${TOOLS[@]}"; do
  src=""
  for s in "${SRCS[@]}"; do [ -d "$s/$name" ] && { src="$s/$name"; break; }; done
  if [ -z "$src" ]; then echo "✗ $name：候选源里都没有"; missing=1; continue; fi
  if [ -d "$DEST/$name" ]; then
    echo "✓ $name 已在（$(du -sh "$DEST/$name" | cut -f1)）"
    continue
  fi
  if [ "$CHECK" = 1 ]; then echo "· $name 缺（源：$src，$(du -sh "$src" | cut -f1)）"; missing=1; continue; fi
  echo "→ 复制 $name（$(du -sh "$src" | cut -f1)）"
  cp -r "$src" "$DEST/" || { echo "✗ $name 复制失败"; missing=1; }
done
echo
echo "tools/win/ 现状：$(du -sh "$DEST" 2>/dev/null | cut -f1)"
ls "$DEST" 2>/dev/null
echo
echo "验证：ida check   （会打印 IDA/Ghidra/Java 路径）"
exit $missing
