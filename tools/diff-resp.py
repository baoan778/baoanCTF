#!/usr/bin/env python3
"""diff-resp.py -- 同一点位并发打多个 payload，输出响应差异（找绕过点/盲注信号）。

为什么要有（2026-11-14 实测）：剩余题多为 MULTI_METHOD（方法够、卡利用细节），
  agent 只能靠肉眼看状态码/长度，效率极低。本工具把「一请求多 payload → 差异表」压成一次调用。

用法:
  python3 tools/diff-resp.py <url> --param id --payloads "1,2,3"
  python3 tools/diff-resp.py <url> --param username --method POST --payloads-file /tmp/p.txt
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import re
import urllib.error
import urllib.parse
import urllib.request


def fetch(url, method, data, headers, timeout=8.0):
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(200000)
            return r.status, len(raw), raw, dict(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read(200000)
        return e.code, len(raw), raw, dict(e.headers)
    except Exception as e:
        return 0, 0, str(e).encode(), {}


def sig(raw):
    text = raw.decode("utf-8", "replace")
    m = re.search(r"(flag|CTF|key)[{][^}]{4,200}[}]", text)
    if m:
        return "FLAG=" + m.group(0)
    low = text.lower()
    for pat in ("sql syntax", "warning", "traceback", "exception", "error", "denied", "invalid", "welcome", "success"):
        if pat in low:
            i = low.index(pat)
            return text[max(0, i - 30):i + 60].replace(chr(10), " ")[:90]
    return text[:60].replace(chr(10), " ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--param", default="id")
    ap.add_argument("--payloads", default="")
    ap.add_argument("--payloads-file", default="")
    ap.add_argument("--method", default="GET")
    ap.add_argument("--cookie", default="")
    ap.add_argument("--header", action="append", default=[])
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()

    pays = []
    if a.payloads:
        pays += [p for p in a.payloads.split(",") if p]
    if a.payloads_file:
        try:
            with open(a.payloads_file, encoding="utf-8", errors="replace") as fh:
                pays += [ln.rstrip(chr(10)) for ln in fh if ln.strip() and not ln.startswith(chr(35))]
        except OSError as e:
            print("read payloads failed:", e)
    pays = pays[:120]
    if not pays:
        print("no payload")
        return 2

    headers = {"User-Agent": "Mozilla/5.0"}
    if a.cookie:
        headers["Cookie"] = a.cookie
    for h in a.header:
        if ":" in h:
            k, v = h.split(":", 1)
            headers[k.strip()] = v.strip()

    base = a.url
    def one(p):
        if a.method.upper() == "GET":
            sep = "&" if "?" in base else "?"
            u = base + sep + urllib.parse.urlencode({a.param: p})
            return p, fetch(u, "GET", None, headers)
        return p, fetch(base, "POST", {a.param: p}, headers)

    results = []
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        for p, res in ex.map(one, pays):
            code, n, raw, hdrs = res
            results.append((p, code, n, sig(raw), (hdrs.get("Set-Cookie") or "")[:36]))

    print("%-24s %-5s %-7s %-12s %s" % ("payload", "code", "len", "cookie", "signature"))
    for p, code, n, s, ck in results:
        mark = "  <<< FLAG" if s.startswith("FLAG=") else ""
        print("%-24s %-5s %-7s %-12s %s%s" % (p[:24], code, n, ck, s[:70], mark))

    from collections import Counter
    print()
    print("status distribution:", dict(Counter(c for _, c, _, _, _ in results)))
    seen = set()
    for _, _, _, s, _ in results:
        if s.startswith("FLAG="):
            f = s.split("=", 1)[1]
            if f not in seen:
                seen.add(f)
                print("FOUND_FLAG=" + f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())