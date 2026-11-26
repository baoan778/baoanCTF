#!/usr/bin/env python3
"""就绪等待与解题耗时的精确分位：容器起来后服务多久可用、agent 多久出 flag。"""
import glob
import json
import os
import re
import statistics
from datetime import datetime

SESSION_GLOB = "/opt/pi-ctf/work/solve/*/*.jsonl"
FAIL_RE = re.compile(r"Couldn.t connect|Connection refused|conn-refused|Failed to connect|no output")
OK_RE = re.compile(r"HTTP/1\.[01] \d{3}|=== 1\) 响应头|Server:|uvicorn|Apache/")
FLAG_RE = re.compile(r"(flag|ctf|key|sec)\{[^}\n]{4,200}\}")
NL = chr(10)


def ts(s):
    for f in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(s, f)
        except ValueError:
            pass
    return None


def analyze(path):
    code = os.path.basename(os.path.dirname(path))
    msgs = []
    for line in open(path, encoding="utf-8"):
        if not line.strip():
            continue
        d = json.loads(line)
        if d.get("type") == "message":
            msgs.append(d)
    if len(msgs) < 2:
        return None
    t0 = ts(msgs[0]["timestamp"])
    tend = ts(msgs[-1]["timestamp"])
    first_ok = None
    first_flag = None
    fails = 0
    for d in msgs:
        m = d.get("message") or {}
        c = m.get("content")
        role = m.get("role")
        if not isinstance(c, list):
            continue
        for p in c:
            s = json.dumps(p, ensure_ascii=False)
            if role == "toolResult":
                if FAIL_RE.search(s):
                    fails += 1
                    continue
                if first_ok is None and OK_RE.search(s):
                    first_ok = ts(d["timestamp"])
                if first_flag is None and FLAG_RE.search(s):
                    mt = FLAG_RE.search(s)
                    if "FOUND_FLAG" not in mt.group(0):
                        first_flag = ts(d["timestamp"])
            elif p.get("type") == "text" and first_flag is None and FLAG_RE.search(s):
                first_flag = ts(d["timestamp"])
    return dict(code=code,
                ready=(first_ok - t0).total_seconds() if first_ok and t0 else None,
                solve=(first_flag - t0).total_seconds() if first_flag and t0 else None,
                dur=(tend - t0).total_seconds() if tend and t0 else 0,
                fails=fails)


rows = [r for r in (analyze(p) for p in glob.glob(SESSION_GLOB)) if r]
ready = [r["ready"] for r in rows if r["ready"] is not None]
solve = [r["solve"] for r in rows if r["solve"] is not None]
print("会话 %d；首次可达 %d 个样本" % (len(rows), len(ready)))
if ready:
    ready.sort()
    print("就绪等待 p50=%.1f p75=%.1f p90=%.1f max=%.1f  合计 %.0fs" % (
        statistics.median(ready), ready[int(len(ready) * .75)],
        ready[int(len(ready) * .9)], ready[-1], sum(ready)))
if solve:
    solve.sort()
    print("从启动到见到 flag p50=%.1f p75=%.1f p90=%.1f max=%.1f" % (
        statistics.median(solve), solve[int(len(solve) * .75)],
        solve[int(len(solve) * .9)], solve[-1]))
print("连接失败探测合计 %d 次" % sum(r["fails"] for r in rows))
print(NL + "最慢 8 题（按总时长）:")
for r in sorted(rows, key=lambda x: -x["dur"])[:8]:
    print("  %-13s dur=%6.1f ready=%5s solve=%5s fails=%d" % (
        r["code"], r["dur"],
        "-" if r["ready"] is None else "%.1f" % r["ready"],
        "-" if r["solve"] is None else "%.1f" % r["solve"], r["fails"]))