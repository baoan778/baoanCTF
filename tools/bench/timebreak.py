#!/usr/bin/env python3
"""拆解一次会话的时间去向：模型思考 vs 工具执行 vs 就绪等待。

这是决定「优化哪里」的唯一依据：
  · 模型思考占比高 → 减少轮数（合并命令 / 一次拿到更多信息）；
  · 工具执行占比高 → 工具本身慢（nmap/ffuf/sqlmap 全量扫），要限时或换快工具。
"""
import glob
import json
import os
import statistics
from datetime import datetime

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
            d["_t"] = ts(d["timestamp"])
            msgs.append(d)
    if len(msgs) < 3:
        return None
    t0 = msgs[0]["_t"]
    think = 0.0
    execu = 0.0
    turns = 0
    prev = None
    for d in msgs:
        m = d.get("message") or {}
        role = m.get("role")
        if role == "assistant":
            turns += 1
            has_call = any(p.get("type") == "toolCall"
                           for p in (m.get("content") or []))
            if has_call and prev is not None:
                think += (d["_t"] - prev).total_seconds()
            prev = d["_t"]
        elif role == "toolResult":
            if prev is not None:
                execu += (d["_t"] - prev).total_seconds()
            prev = d["_t"]
    total = (msgs[-1]["_t"] - t0).total_seconds()
    return dict(code=code, total=total, think=think, exec_=execu, turns=turns)


rows = [r for r in (analyze(p) for p in glob.glob("/opt/pi-ctf/work/solve/*/*.jsonl")) if r]
rows.sort(key=lambda r: -r["total"])
print("%-13s %7s %7s %7s %6s %6s" % ("code", "total", "think", "exec", "turns", "其他"))
th = ex = ot = 0.0
for r in rows[:20]:
    other = r["total"] - r["think"] - r["exec_"]
    th += r["think"]
    ex += r["exec_"]
    ot += other
    print("%-13s %7.1f %7.1f %7.1f %6d %6.1f" % (r["code"], r["total"], r["think"],
                                                 r["exec_"], r["turns"], other))
T = sum(r["total"] for r in rows)
TH = sum(r["think"] for r in rows)
EX = sum(r["exec_"] for r in rows)
print(NL + "全 %d 题：总 %.0fs；模型思考 %.0fs (%.0f%%)；工具执行 %.0fs (%.0f%%)；其余 %.0fs"
      % (len(rows), T, TH, TH * 100 / T, EX, EX * 100 / T, T - TH - EX))
print("每题平均轮数 %.1f；平均思考/轮 %.1fs；平均工具/轮 %.1fs" % (
    statistics.mean(r["turns"] for r in rows),
    TH / max(sum(r["turns"] for r in rows), 1),
    EX / max(sum(r["turns"] for r in rows), 1)))