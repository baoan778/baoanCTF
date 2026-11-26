#!/bin/bash
# 重建 web 前端产物（默认 work/ui/web/dist）。server.mjs 的 DIST 就指向这里。
#
# ★ 2026-11-18 起：compose 把 dist **只读**挂进容器（宿主那份就是容器跑的那份），
#   所以容器里不能直接往 dist 写 —— 用 --out 指到可写目录，再回填宿主。
#   日常别手搓这几步，直接跑 `uibuild`（tools/hand/uibuild，已收口）。
#
# 用法：bash build.sh [--out <目录>] [--install]
#   --out <目录>  产物落到哪（默认 dist）
#   --install     强制先 npm install（改了 package.json 时用）
set -euo pipefail
cd "$(dirname "$0")/web"

OUT="dist"
INSTALL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --out)     OUT="${2:?--out 需要目录参数}"; shift 2 ;;
    --out=*)   OUT="${1#--out=}"; shift ;;
    --install) INSTALL=1; shift ;;
    -h|--help) sed -n '2,11p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) echo "[build] 不认识的参数: $1（可用：--out <目录> / --install）" >&2; exit 2 ;;
  esac
done

if [ ! -d node_modules ] || [ "$INSTALL" = "1" ]; then
  echo "[build] 安装依赖…"
  npm install --no-audit --no-fund --registry=https://registry.npmmirror.com
fi
echo "[build] tsc 类型检查…"
npx tsc -p tsconfig.json --noEmit

# vite 对「root 之外的 outDir」不会自动清空（只告警），残留的上一版 hash 文件会让
# 「index.html 引的 JS 到底在不在」这类校验失真 → 先自己删干净。
rm -rf "$OUT"
echo "[build] vite build → $OUT …"
npx vite build --outDir "$OUT" --emptyOutDir
echo "[build] 完成 → $OUT"
