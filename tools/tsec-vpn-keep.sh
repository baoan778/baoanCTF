#!/bin/bash
set -u
pkill -x openvpn 2>/dev/null || true
sleep 1
setsid openvpn --config /home/ctf/.pi-ctf/tsec/run-current.ovpn --auth-nocache --verb 3 \
  >> /home/ctf/.pi-ctf/tsec/vpn.log 2>&1 < /dev/null &
for i in $(seq 1 25); do
  if ip link show tun0 >/dev/null 2>&1; then
    echo "TUN_UP after ${i}s"
    ip -4 addr show tun0 | grep inet
    exit 0
  fi
  sleep 1
done
echo TUN_DOWN
tail -5 /home/ctf/.pi-ctf/tsec/vpn.log
exit 1