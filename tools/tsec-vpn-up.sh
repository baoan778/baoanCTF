#!/bin/bash
set -u
CFG=${1:-/home/ctf/.pi-ctf/tsec/run-current.ovpn}
# ★ R11 教训：绝对不要给这份 ovpn 加 --tun-mtu / --mssfix / --auth / --cipher 覆盖。
#   服务器开了 --opt-verify，会严格比对 link-mtu/tun-mtu/cipher/auth 组成的 OCC 串；
#   任何覆盖都会让客户端报 1457 而服务端期望 1557 -> AUTH_FAILED 'Option inconsistency'。
#   实测：纯净配置 25s 内 Initialization Sequence Completed；加 --tun-mtu 1400 必败。
pkill -x openvpn 2>/dev/null || true
sleep 1
nohup openvpn --config "$CFG" --auth-nocache --verb 3 >> /home/ctf/.pi-ctf/tsec/vpn.log 2>&1 &
for i in $(seq 1 30); do
  if ip -4 addr show tun0 2>/dev/null | grep -q 'inet '; then
    echo "VPN UP after ${i}s"; ip -4 addr show tun0 | grep inet; exit 0
  fi
  sleep 1
done
echo "VPN FAILED (no tun0 in 30s)"; tail -5 /home/ctf/.pi-ctf/tsec/vpn.log; exit 1