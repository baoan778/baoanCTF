#!/bin/bash
# 密钥泄漏扫描 —— 与 .github/workflows/ci.yml 用的是同一套判据
#
# 用法：bash tools/secret-scan.sh [--quiet]
# 退出码：0 干净 / 1 有命中
#
# ★ 2026-11-24：从 CI 的 inline grep 抽出来成独立脚本。
#   原因：那条 grep 的**模式串本身**就带着 "-----BEGIN ... PRIVATE KEY-----"，
#   于是「扫自己」时自匹配，git 历史扫描与 GitHub secret scanning 都会报它。
#   现在模式串只在这一处，CI 调这个脚本（并把它排除在扫描范围外）。
#
# ⚠ 排除目录的理由（都是第三方，不是我们的代码）：
#   venv / venv-win / vendor / knowledge / node_modules —— 这些天然含
#   "BEGIN PRIVATE KEY" 之类的**字符串字面量**（cryptography / ecdsa 的解析代码），
#   不排除就是 100% 假红（docs/经验-工程纪律.md §4c：检查器自己站不稳）。

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1

cd "$ROOT" || exit 2

# 只看**我们自己写的**文本文件
TARGETS=""
for p in board tools/*.py tools/bench docs README.md CONTRIBUTING.md SECURITY.md NOTICE CHANGELOG.md; do
  [ -e "$p" ] && TARGETS="$TARGETS $p"
done

HITS=$(grep -rInE \
  -e 'sk-[A-Za-z0-9]{20,}' \
  -e 'ghp_[A-Za-z0-9]{20,}' \
  -e 'AKIA[0-9A-Z]{16}' \
  -e '-----BEGIN [A-Z ]*PRIVATE KEY-----' \
  --include='*.py' --include='*.sh' --include='*.md' --include='*.json' \
  --exclude-dir=venv --exclude-dir=venv-win --exclude-dir=vendor \
  --exclude-dir=knowledge --exclude-dir=node_modules \
  --exclude='secret-scan.sh' \
  $TARGETS 2>/dev/null | grep -v 'REDACTED' | grep -v 'test_' || true)

if [ -n "$HITS" ]; then
  [ "$QUIET" = "0" ] && { echo "✗ 发现疑似密钥："; echo "$HITS" | head -20; }
  exit 1
fi
[ "$QUIET" = "0" ] && echo "✓ 无密钥泄漏"
exit 0
