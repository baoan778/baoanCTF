#!/usr/bin/env bash
# =============================================================================
# vpn-tunnel-up.sh —— 宿主侧一键起「VPN + CONNECT 代理」并打印容器该用的环境变量
#
# 用法（WSL/MyLinux 侧）：
#   bash docker/vpn-tunnel-up.sh --file /path/to/task.ovpn
#   bash docker/vpn-tunnel-up.sh                # 不起 VPN，只起代理（VPN 已手动起过）
#   bash docker/vpn-tunnel-up.sh --down         # 收拾干净
#
# 它做什么（按顺序，每步都验）：
#   1) 确认 openvpn / tun 就位
#   2) 起 VPN（给了 --file 才起），等 "Initialization Sequence Completed"
#   3) 起 CONNECT 代理（tools/tunnel-proxy.py，默认 8888）
#   4) 打印宿主机 IP + 容器要 export 的三个变量
#   5) 探一次 healthcheck，明确告诉你是「平台应答」还是「代理 502」
#
# 为什么是代理而不是 --network host（2026-11-03 实测证伪，别走回头路）：
#   A1 `--network host`：host 容器看到 Docker Desktop VM 的 eth0
#       （MAC de:5d:34:7e:db:4e / gw 192.168.65.1），不是 MyLinux 的
#       （00:15:5d:93:0e:ed / 172.28.208.1）→ 宿主起 VPN 容器照样不通。
#   A2 容器内静态路由：`ip route add ... via 172.28.208.177` → 内核
#       `Nexthop has invalid gateway`；加 onlink → `No route to host`
#       （容器与 MyLinux 不在同一二层，不能互为下一跳）。
#   A3 代理（本脚本）：容器**能** TCP 直连 MyLinux（实测收到 I-AM-MYLINUX），
#       所以把连接受托给宿主侧代理即可。实测容器经代理读到 TARGET-OK。
# =============================================================================
set -uo pipefail

LAB="${PI_LAB_ROOT:-/mnt/d/baoan}"
PROXY_PORT="${TSEC_PROXY_PORT:-8888}"
VPN_DIR="${TSEC_VPN_DIR:-$HOME/tsec}"
OVPN=""
DOWN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --file) OVPN="${2:-}"; shift 2 ;;
    --port) PROXY_PORT="${2:-8888}"; shift 2 ;;
    --down) DOWN=1; shift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

ok()   { printf '  [OK]   %s\n' "$*"; }
bad()  { printf '  [FAIL] %s\n' "$*"; }
warn() { printf '  [WARN] %s\n' "$*"; }

host_ip() {
  ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1); exit}'
}

# ── --down：收拾 ──
if [ "$DOWN" = 1 ]; then
  echo "=== 清理 ==="
  pkill -f "tunnel-proxy.py" 2>/dev/null && ok "代理已停" || warn "代理本来没在跑"
  if [ -f "$VPN_DIR/vpn.pid" ]; then
    kill "$(cat "$VPN_DIR/vpn.pid")" 2>/dev/null && ok "VPN 已停" || warn "VPN pid 失效"
    rm -f "$VPN_DIR/vpn.pid"
  else
    warn "没有 vpn.pid，跳过"
  fi
  exit 0
fi

echo "=== 1. 前置检查 ==="
command -v openvpn >/dev/null 2>&1 && ok "openvpn: $(command -v openvpn)" \
  || { bad "openvpn 没装：sudo apt-get install -y openvpn"; exit 1; }
[ -c /dev/net/tun ] && ok "/dev/net/tun" || { bad "没有 /dev/net/tun"; exit 1; }

echo
echo "=== 2. VPN ==="
if [ -n "$OVPN" ]; then
  [ -f "$OVPN" ] || { bad "ovpn 不存在：$OVPN"; exit 1; }
  mkdir -p "$VPN_DIR"
  if [ -f "$VPN_DIR/vpn.pid" ] && kill -0 "$(cat "$VPN_DIR/vpn.pid")" 2>/dev/null; then
    warn "VPN 已在跑（pid $(cat "$VPN_DIR/vpn.pid")），不重复起"
  else
    : > "$VPN_DIR/vpn.log"
    nohup openvpn --config "$OVPN" --log "$VPN_DIR/vpn.log" --writepid "$VPN_DIR/vpn.pid" \
      --daemon >/dev/null 2>&1
    for i in $(seq 1 30); do
      if grep -q "Initialization Sequence Completed" "$VPN_DIR/vpn.log" 2>/dev/null; then
        ok "VPN 起来了（第 ${i}s）"; break
      fi
      [ "$i" = 30 ] && { bad "30s 内没看到 Initialization Sequence Completed，看 $VPN_DIR/vpn.log"; exit 1; }
      sleep 1
    done
  fi
else
  warn "没给 --file，跳过起 VPN（假定你已手动起过）"
fi

echo
echo "=== 3. CONNECT 代理 ==="
if ss -lnt 2>/dev/null | grep -q ":${PROXY_PORT} "; then
  warn "端口 ${PROXY_PORT} 已被占用，跳过"
else
  nohup python3 "$LAB/tools/tunnel-proxy.py" --port "$PROXY_PORT" >/tmp/tsec-proxy.log 2>&1 &
  sleep 1
  if ss -lnt 2>/dev/null | grep -q ":${PROXY_PORT} "; then
    ok "代理在 ${PROXY_PORT}（日志 /tmp/tsec-proxy.log）"
  else
    bad "代理没起来，看 /tmp/tsec-proxy.log"; exit 1
  fi
fi

echo
echo "=== 4. 容器该用的环境变量 ==="
HIP=$(host_ip)
if [ -z "$HIP" ]; then bad "取不到宿主 IP"; exit 1; fi
echo "  宿主 IP = $HIP"
cat <<EOF

  在 compose.yaml 的 environment: 下加（或 docker exec -e 传）：

    http_proxy:  http://${HIP}:${PROXY_PORT}
    https_proxy: http://${HIP}:${PROXY_PORT}
    no_proxy:    localhost,127.0.0.1,::1

  仅对 tsec 生效（不改容器全局代理）时的用法：

    docker exec -u ctf -e TSEC_PROXY=http://${HIP}:${PROXY_PORT} pictf tsec healthcheck

EOF

echo "=== 5. 从容器探一次 ==="
CODE=$(docker exec pictf bash -lc \
  "curl -sS -o /dev/null -m 8 -w '%{http_code}' -x http://${HIP}:${PROXY_PORT} http://10.0.100.58 2>/dev/null || echo 000" \
  | tr -d '\r')
case "$CODE" in
  2*|3*) ok "容器经代理拿到 HTTP $CODE —— 平台真的应答了" ;;
  502|503) warn "容器经代理拿到 HTTP $CODE —— 仍是代理/网关层，VPN 未真正通到靶场" ;;
  *)     warn "容器经代理拿到 HTTP $CODE —— 未通" ;;
esac
