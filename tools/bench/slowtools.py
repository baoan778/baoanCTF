#!/usr/bin/env python3
"""找出最慢的工具调用（真实执行时长 + 命令前缀），定位可优化的扫描/等待。"""
import glob
import json
import os
from datetime import datetime

NL = chr(10)


def ts(s):
    for f in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(s, f)
        except ValueError:
            pass
    return None


calls = []
for p in glob.glob("/opt/pi-ctf/work/solve/*/*.jsonl"):
    code = os.path.basename(os.path.dirname(p))
    pending = None
    for line in open(p, encoding="utf-8"):
        if not line.strip():
            continue
        d = json.loads(line)
        if d.get("type") != "message":
            continue
        m = d.get("message") or {}
        role = m.get("role")
        t = ts(d["timestamp"])
        if role == "assistant" and pending is None:
            for pt in (m.get("content") or []):
                if pt.get("type") == "toolCall":
                    cmd = str((pt.get("arguments") or {}).get("command", ""))
                    pending = (t, cmd[:160].replace(NL, " "))
                    break
        elif role == "toolResult" and pending is not None:
            calls.append((((t - pending[0]).total_seconds()), code, pending[1]))
            pending = None

calls.sort(key=lambda c: -c[0])
print("工具调用总数 %d；总执行 %.0fs；平均 %.2fs" % (
    len(calls), sum(c[0] for c in calls), sum(c[0] for c in calls) / max(len(calls), 1)))
print(NL + "最慢 20 次:")
for dur, code, cmd in calls[:20]:
    print("  %6.1fs %-13s %s" % (dur, code, cmd[:110]))
print(NL + "慢调用(>10s)合计 %.0fs，占工具总时长 %.0f%%" % (
    sum(c[0] for c in calls if c[0] > 10),
    100.0 * sum(c[0] for c in calls if c[0] > 10) / max(sum(c[0] for c in calls), 1)))