import json
from pathlib import Path
ROOT = Path("/opt/pi-ctf/work/solve")
rows = []
for d in sorted(ROOT.iterdir()):
    if not d.is_dir(): continue
    js = sorted(d.glob("*.jsonl"))
    if not js: continue
    for ln in js[-1].read_text(errors="replace").splitlines():
        try: o = json.loads(ln)
        except Exception: continue
        m = o.get("message") or {}
        if m.get("role") != "assistant": continue
        u = m.get("usage") or {}
        if not u: continue
        dur = (m.get("durationMs") or 0)/1000.0
        out = u.get("output",0)
        rows.append((dur, out, u.get("input",0)+u.get("cacheRead",0), m.get("thinkingLevel"), m.get("model"), d.name))
rows.sort(reverse=True)
print("assistant 回合 %d 个，总耗时 %.0fs" % (len(rows), sum(r[0] for r in rows)))
print()
print("=== 最慢 15 个回合 ===")
for dur, out, inp, th, model, code in rows[:15]:
    tps = out/dur if dur else 0
    print("  %6.1fs  out=%4d  in=%6d  %5.1f tok/s  think=%-6s %-14s %s" % (dur, out, inp, tps, th, model, code))
print()
import statistics
durs = [r[0] for r in rows]
print("durationMs p50=%.1fs p90=%.1fs p99=%.1fs max=%.1fs" % (
    statistics.median(durs), sorted(durs)[int(len(durs)*0.9)], sorted(durs)[int(len(durs)*0.99)], max(durs)))
slow = [r for r in rows if r[0] > 20]
print(">20s 回合 %d 个，合计 %.0fs（占总 %.0f%%）" % (len(slow), sum(r[0] for r in slow), 100*sum(r[0] for r in slow)/sum(durs)))
print()
from collections import Counter
print("thinkingLevel 分布:", Counter(r[3] for r in rows))
print("model 分布:", Counter(r[4] for r in rows))
