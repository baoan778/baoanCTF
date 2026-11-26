#!/usr/bin/env bash
# =============================================================================
# vpn-host-check.sh —— Q18=A（VPN 归宿主）落地前的「能不能这么干」体检
#
# 用法（在 WSL 里跑，不是容器里）：
#   wsl.exe -d MyLinux -e bash -lc 'cd /mnt/d/baoan && bash docker/vpn-host-check.sh'
#
# 它只读不写，回答 4 个问题：
#   1) 宿主有没有 openvpn / tun 设备 / 可用的 sudo
#   2) 容器当前用哪种网络模式（bridge 还是 host）
#   3) 靶场网段 10.0.100.58 从「MyLinux / 容器」两处分别通不通
#   4) 结论：A 方案能不能直接跑；不能的话卡在哪一步、下一句命令是什么
#
# ★★ 为什么必须用 HTTP 语义探活，不能用 /dev/tcp（2026-11-03 实测踩到）：
#   本机存在**透明代理**，80 端口对**任意 IP 都 connect 成功**。
#   实测证据：
#     /dev/tcp/10.0.100.199/80  -> OPEN（10.0.100.199 是不存在的地址）
#     /dev/tcp/1.1.1.1/80       -> OPEN
#     /dev/tcp/10.0.100.58/1    -> TimeoutError（只有 80 被拦）
#     curl http://10.0.100.58   -> HTTP/1.1 502 Bad Gateway（代理回的）
#   所以「TCP 能连」在本机**不构成任何证据**。必须看 HTTP 状态码：
#     2xx/3xx = 平台真的应答；502/503 = 打到代理了，等于没通。
# =============================================================================
set -uo pipefail

LAB="${PI_LAB_ROOT:-/mnt/d/baoan}"
TARGET_IP="${TSEC_HEALTHCHECK_IP:-10.0.100.58}"
CONTAINER="${PICTF_CONTAINER:-pictf}"

ok()   { printf '  [OK]   %s\n' "$*"; }
bad()  { printf '  [FAIL] %s\n' "$*"; }
warn() { printf '  [WARN] %s\n' "$*"; }

# http_probe <url> -> 打印状态码；连不上打印 000
http_probe() {
  # 只取最后 3 个字符：curl 失败时自身会打印 000，再加兜底会变成 000000
  local c
  c=$(curl -sS -o /dev/null -m 6 -w '%{http_code}' "$1" 2>/dev/null)
  printf '%s' "${c: -3}"
}

# 判定：2xx/3xx 才算真通
verdict() {
  case "$1" in
    2*|3*) return 0 ;;
    *)     return 1 ;;
  esac
}

echo "=== 1. 宿主（WSL: ${WSL_DISTRO_NAME:-unknown}）==="
HAVE_OPENVPN=0; HAVE_TUN=0
if command -v openvpn >/dev/null 2>&1; then ok "openvpn: $(command -v openvpn)"; HAVE_OPENVPN=1
else bad "openvpn 没装 —— 装它需要 root：sudo apt-get install -y openvpn"; fi
if [ -c /dev/net/tun ]; then ok "/dev/net/tun 存在"; HAVE_TUN=1
else bad "/dev/net/tun 不存在 —— 没有 tun 就起不了 OpenVPN"; fi
if command -v sudo >/dev/null 2>&1; then
  if sudo -n true 2>/dev/null; then ok "sudo 免密可用"
  else warn "sudo 存在但要密码 —— 起 VPN 时会交互提示（人跑没问题，脚本跑会卡）"; fi
else bad "没有 sudo —— 非 root 用户装不了也起不了 VPN"; fi

echo
echo "=== 2. 容器网络模式（决定 A 方案能不能共享宿主路由）==="
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  NETMODE=$(docker inspect "$CONTAINER" --format '{{.HostConfig.NetworkMode}}')
  CNAME=$(docker inspect "$CONTAINER" --format '{{.Name}}' | sed 's#^/##')
  printf '  容器 %s 网络模式 = %s\n' "$CNAME" "$NETMODE"
  if [ "$NETMODE" = "host" ]; then
    ok "host 模式 —— 容器与 Docker VM 共享网络栈，宿主侧路由改动对它可见（A 可行）"
  else
    bad "bridge 模式（$NETMODE）—— 容器走 Docker Desktop VM 的 NAT，与 MyLinux 的 tun0 不是同一命名空间"
    printf '     -> A 方案要生效，必须二选一：\n'
    printf '       (a) 容器改 --network host（改 compose.yaml 属部署形态变更，需拍板）\n'
    printf '       (b) 容器内加静态路由：ip route add %s/32 via <MyLinux eth0 IP>\n' "$TARGET_IP"
  fi
else
  warn "容器 $CONTAINER 不存在，跳过"
fi

echo
echo "=== 3. 靶场探活（HTTP 语义，502 视为不通）==="
URL="http://${TARGET_IP}"
ML_CODE=$(http_probe "$URL"); printf '  MyLinux   %s -> HTTP %s\n' "$URL" "$ML_CODE"
if verdict "$ML_CODE"; then ML=1; else ML=0; fi
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  CT_CODE=$(docker exec "$CONTAINER" bash -lc "curl -sS -o /dev/null -m 6 -w '%{http_code}' '$URL' 2>/dev/null || echo 000" | tr -d '\r')
  printf '  容器内    %s -> HTTP %s\n' "$URL" "$CT_CODE"
  if verdict "$CT_CODE"; then CT=1; else CT=0; fi
else CT=0; CT_CODE="-"; fi
if [ "$ML_CODE" = "502" ] || [ "$CT_CODE" = "502" ]; then
  warn "出现 502 = 打到了本机透明代理，**不是平台应答**。等于没通。"
fi

echo
echo "=== 4. 结论 ==="
if [ "$HAVE_OPENVPN" = 0 ] || [ "$HAVE_TUN" = 0 ]; then
  bad "A 方案前置未满足：宿主缺 openvpn 或 tun。"
  echo "    下一句（需要人输入 sudo 密码）："
  echo "      sudo apt-get update && sudo apt-get install -y openvpn"
  exit 1
fi
if [ "${ML:-0}" = 0 ]; then
  warn "宿主有 openvpn 但 ${TARGET_IP} 不通（HTTP ${ML_CODE}）—— VPN 还没起。"
  echo "    下一句："
  echo "      cd $LAB && python3 tools/tsecbench.py vpn up --file <task.ovpn>"
  echo "      python3 tools/tsecbench.py healthcheck   # 通了才算 VPN 真的通"
  exit 1
fi
if [ "${CT:-0}" = 0 ]; then
  warn "宿主通了，但**容器里不通**（HTTP ${CT_CODE}）—— A 方案差最后一步：网络命名空间没打通。"
  echo "    按第 2 节的 (a)/(b) 二选一，改完重跑本脚本。"
  exit 1
fi
ok "A 方案已就绪：宿主通了，容器也通了。可以跑 tsec list / submit。"
exit 0
