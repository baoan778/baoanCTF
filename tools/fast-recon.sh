#!/bin/bash
# fast-recon.sh —— 一条命令做完初始侦察 + 按指纹给下一步动作 + 直接给现成 payload
set -uo pipefail
T="${1:-}"
[ -n "$T" ] || { echo "用法: fast-recon.sh <ip:port>"; exit 2; }
U="$T"
if [ "${T#http}" = "$T" ]; then U="http://$T"; fi

echo "=== 1) 响应头与指纹"
HDR=$(curl -sS -m 6 -D- -o /dev/null "$U/" 2>&1 | head -12)
printf "%s\n" "$HDR"

echo "=== 2) 首页要素"
BODY=$(curl -sS -m 6 "$U/" 2>/dev/null)
printf "%s" "$BODY" | grep -oiE "<title>[^<]*</title>|<form[^>]*>|<input[^>]*>|<textarea[^>]*>" 2>/dev/null | head -14
echo "--- flag 形态串"
printf "%s" "$BODY" | grep -oE "(flag|FLAG|ctf|CTF|key|KEY|sec|SEC)[{][^}]{4,200}[}]" 2>/dev/null | head -5
echo "--- HTML 注释（★ 重点看：常藏测试账号/密码/路径）"
CMT=$(printf "%s" "$BODY" | grep -oE "<!.{0,300}-->" 2>/dev/null | head -8)
printf "%s\n" "$CMT"
# ★ 2026-11-14 补：注释里出现 user:pass 形态时直接提炼成可跑命令（088 实测泄露 test:test）
# 去掉尾部标点（实测 "test:test)." 会带出 ")."），只保留字母数字和 ._-
CRED=$(printf "%s" "$CMT" \
  | grep -oiE "[A-Za-z0-9_.-]{2,20}:[A-Za-z0-9_.!@#$%^&*-]{2,30}" \
  | sed -E "s/[.,;:)]+$//" \
  | grep -viE "https|http|//|localhost|127\.0\.0\.1|xmlns|DOCTYPE" \
  | head -3)
if [ -n "$CRED" ]; then
  echo "  ★★ 注释里疑似凭据："
  printf "%s\n" "$CRED" | while IFS= read -r c; do
    u="${c%%:*}"; p="${c#*:}"
    echo "     $u / $p   → bash /opt/pi-ctf/tools/brute-login.sh $U<登录路径> username password  或直接手打这两条"
  done
fi
echo "--- JS/链接"
printf "%s" "$BODY" | grep -oiE "(href|src)=[^\" ]{1,80}" 2>/dev/null | head -14

echo "=== 3) 常见路径"
for p in /robots.txt /.git/HEAD /admin /api /api/v1 /login /flag /flag.txt /console /debug /.env /backup /health /server-status /__NEXT_DATA__ /accounts/register /accounts/login; do
  code=$(curl -sS -m 3 -o /dev/null -w "%{http_code}" "$U$p" 2>/dev/null)
  if [ "$code" != "404" ] && [ "$code" != "000" ]; then echo "$p -> $code"; fi
done

echo "=== 4) 错误页指纹"
ERR=$(curl -sS -m 6 -D- -o /dev/null "$U/__nonexistent__" 2>&1 | grep -iE "server:|x-powered|set-cookie|flask|django|express|werkzeug|uvicorn|gunicorn" | head -6)
printf "%s\n" "$ERR"

ALL="$HDR$BODY$ERR"
PL=/opt/pi-ctf/tools/payload.py

echo "=== 5) 按指纹给下一步 + 现成 payload（照做，别自己瞎凑）"
if printf "%s" "$ALL" | grep -qi "_next/static\|__NEXT_DATA__\|next.js"; then
  echo "  [Next.js] 抓源码映射与 API 路由："
  echo "    for js in $(curl -s $U/ | grep -oE "/_next/[^\"]+\.js" | head -8); do curl -s \"$U$js.map\" | head -c 300; echo; done"
  echo "    for p in /api /api/users /api/flag /api/auth /api/admin; do echo -n \"$p \"; curl -s -o /dev/null -w \"%{http_code}\\n\" $U$p; done"
fi
if printf "%s" "$ALL" | grep -qi "werkzeug\|flask"; then
  echo "  [Flask/Werkzeug] /console 调试 PIN；SSTI 试 {{7*7}}；"
  echo "    session 用 python3 /opt/pi-ctf/tools/flask-session.py decode <cookie> | brute <cookie> <词典>"
  python3 "$PL" search "jinja" 2>/dev/null | head -4
fi
if printf "%s" "$ALL" | grep -qi "django\|wsgiserver"; then
  echo "  [Django] /admin/ 、?next= 开放重定向、DEBUG 页 SECRET_KEY、/accounts/register 提权、/static/ 遍历"
  python3 "$PL" search "django" 2>/dev/null | head -4
fi
if printf "%s" "$ALL" | grep -qi "apache"; then
  echo "  [Apache] /.git/HEAD /.env /server-status /backup.zip ；PHP 试 ?file=/etc/passwd 与上传绕过"
fi
if printf "%s" "$ALL" | grep -qiE "php|\\.php"; then
  python3 "$PL" search "php filter" 2>/dev/null | head -3
  python3 "$PL" search "bypass" 2>/dev/null | head -3
fi
ACT=$(printf "%s" "$BODY" | grep -oiE "action=[^ >]+" | head -1 | cut -c8- | tr -d "\"\x27")
# ★ 2026-11-14 P1-3：action 为空时不能干等，要按当前 URL 推断 POST 端点（xben-089 实测 405）
if [ -z "$ACT" ] && printf "%s" "$BODY" | grep -qi "<form[^>]*method=[\"\x27]*post"; then
  ACT="$U"
  echo "  [表单无 action] POST 回当前地址：$ACT"
fi
if [ -n "$ACT" ]; then
  echo "  [有表单] action=$ACT"
  echo "    登录类：bash /opt/pi-ctf/tools/brute-login.sh $U$ACT username password"
  echo "    留言/发信类：试邮件头注入（换行注入 Bcc/Cc）、模板注入、命令拼接"
  python3 "$PL" search "header injection" 2>/dev/null | head -3
fi
echo "  （更多：python3 $PL list / search \"<关键词>\" / show <类别>）"
echo "=== 6) 侦察完成"