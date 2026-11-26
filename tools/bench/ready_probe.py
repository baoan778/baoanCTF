#!/usr/bin/env python3
"""测容器就绪延迟：start_challenge 返回后，服务多久才真正能连上。"""
import os
import socket
import sys
import time

sys.path.insert(0, "/opt/pi-ctf/tools")
from tsec_benchmark import TSecBenchmark  # noqa: E402

BASE = os.environ["BENCHMARK_BASE_URL"]
TOK = os.environ["BENCHMARK_TOKEN"]
code = sys.argv[1] if len(sys.argv) > 1 else "xben-096-24"

with TSecBenchmark(base_url=BASE, token=TOK) as c:
    t0 = time.time()
    st = c.start_challenge(code)
    addrs = st.container_addr or []
    print("%s addrs=%s 起容器耗时 %.2fs" % (code, addrs, time.time() - t0))
    for a in addrs:
        host, _, port = a.rpartition(":")
        port = int(port or 80)
        t1 = time.time()
        ok_at = None
        while time.time() - t1 < 40:
            s = socket.socket()
            s.settimeout(1)
            try:
                s.connect((host, port))
                ok_at = time.time() - t1
                s.close()
                break
            except OSError:
                s.close()
                time.sleep(0.1)
        print("  %s TCP 就绪: %s" % (a, "-" if ok_at is None else "%.2fs" % ok_at))
    time.sleep(0.2)

print("注意：本脚本已把该题容器占住，需手动 close。")