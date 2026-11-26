#!/bin/bash
set -u
BIN=$(command -v openvpn)
CFG=/home/ctf/.pi-ctf/tsec/run-current.ovpn
LOG=/home/ctf/.pi-ctf/tsec/vpn3.log
: > "$LOG"
nohup "$BIN" --config "$CFG" --auth-nocache --verb 3 --log "$LOG" >/dev/null 2>&1 &
echo "launched pid=$!"
for i in $(seq 1 25); do
  if ip link show tun0 >/dev/null 2>&1; then echo "TUN_UP ${i}s"; ip -4 addr show tun0 | grep inet; exit 0; fi
  sleep 1
done
echo TUN_DOWN; tail -10 "$LOG"