#!/usr/bin/env python3
"""latency.py —— 把 agent 的墙钟时间拆成：工具执行 vs 模型（API+思考）。

session jsonl 的事实（实测 xben-028-24）：
  · message.toolResult 带 durationMs（工具自身耗时，权威值），带 timestamp(epoch ms)
  · message.assistant 带 toolCall（camelCase）与 timestamp
  · 外层 o["timestamp"] 是 ISO 字符串，o["message"]["timestamp"] 是 epoch ms
拆法：wall = 最后一条 - 第一条；tool = sum(durationMs)；其余 = 模型/调度/收尾轮。
"""
import json, sys
from pathlib import Path

ROOT = Path("/opt/pi-ctf/work/solve")

def analyze(code):
    d = ROOT / code
    js = sorted(d.glob("*.jsonl"))
    if not js:
        return None
    ts, tool_ms, calls, by_name, rounds = [], 0.0, 0, {}, 0
    think_chars = 0
    for ln in js[-1].read_text(errors="replace").splitlines():
        try:
            o = json.loads(ln)
        except Exception:
            continue
        m = o.get("message") or {}
        t = m.get("timestamp")
        if isinstance(t, (int, float)):
            ts.append(t / 1000.0)
        role = m.get("role")
        if role == "toolResult":
            dur = m.get("durationMs") or 0
            tool_ms += dur
            calls += 1
            nm = m.get("toolName") or "?"
            by_name[nm] = by_name.get(nm, 0.0) + dur / 1000.0
        elif role == "assistant":
            rounds += 1
            for p in (m.get("content") or []):
                if isinstance(p, dict) and p.get("type") == "thinking":
                    think_chars += len(p.get("thinking") or "")
    if not ts:
        return None
    wall = max(ts) - min(ts)
    return {"code": code, "wall": wall, "tool": tool_ms / 1000.0, "calls": calls,
            "rounds": rounds, "by_name": by_name, "think": think_chars}

rows = []
codes = sys.argv[1:]
if not codes:
    codes = sorted(p.name for p in ROOT.iterdir() if p.is_dir())
for c in codes:
    r = analyze(c)
    if r and r["wall"] > 5:
        rows.append(r)
if not rows:
    print("没有可分析会话"); sys.exit()

tot_w = sum(r["wall"] for r in rows)
tot_t = sum(r["tool"] for r in rows)
tot_c = sum(r["calls"] for r in rows)
tot_r = sum(r["rounds"] for r in rows)
print("题数 %d  墙钟合计 %.0fs" % (len(rows), tot_w))
print("  工具执行 %.0fs (%.0f%%)   模型/调度/收尾 %.0fs (%.0f%%)"
      % (tot_t, tot_t*100/tot_w, tot_w-tot_t, (tot_w-tot_t)*100/tot_w))
print("  工具调用 %d 次 (%.1fs/次)；assistant 回合 %d (%.1f 次/题)"
      % (tot_c, tot_t/max(tot_c,1), tot_r, tot_r/len(rows)))
print("  平均每题：墙钟 %.0fs  工具 %.0fs  模型 %.0fs"
      % (tot_w/len(rows), tot_t/len(rows), (tot_w-tot_t)/len(rows)))
print()
agg = {}
for r in rows:
    for k, v in r["by_name"].items():
        agg[k] = agg.get(k, 0.0) + v
print("工具耗时 Top 10：")
for k, v in sorted(agg.items(), key=lambda x: -x[1])[:10]:
    print("  %-10s %7.1fs  %5.1f%%" % (k, v, v*100/max(tot_t,1)))
print()
rows.sort(key=lambda r: -(r["wall"]-r["tool"]))
print("模型耗时最长的 12 题：")
for r in rows[:12]:
    print("  %-14s 墙钟 %4.0fs  工具 %4.0fs  模型 %4.0fs  回合 %2d  工具 %2d 次"
          % (r["code"], r["wall"], r["tool"], r["wall"]-r["tool"], r["rounds"], r["calls"]))
print()
print("模型耗时/回合 最贵的 10 题（每回合秒数）：")
per = [r for r in rows if r["rounds"] >= 3]
per.sort(key=lambda r: -((r["wall"]-r["tool"])/r["rounds"]))
for r in per[:10]:
    print("  %-14s %.1fs/回合  (墙钟 %.0fs, 回合 %d)" % (r["code"], (r["wall"]-r["tool"])/r["rounds"], r["wall"], r["rounds"]))
