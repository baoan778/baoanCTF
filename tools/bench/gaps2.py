import json
from pathlib import Path
ROOT = Path("/opt/pi-ctf/work/solve")
gaps = []
per = {}
for d in sorted(ROOT.iterdir()):
    if not d.is_dir(): continue
    js = sorted(d.glob("*.jsonl"))
    if not js: continue
    rows = []
    for ln in js[-1].read_text(errors="replace").splitlines():
        try: o = json.loads(ln)
        except Exception: continue
        m = o.get("message") or {}
        t = m.get("timestamp")
        if not isinstance(t, (int, float)): continue
        rows.append((t/1000.0, m.get("role"), m.get("durationMs") or 0))
    rows.sort()
    prev = None
    for t, role, dur in rows:
        if prev is not None and role in ("toolResult",) or (prev is not None and role == "assistant"):
            g = t - prev
            if 0 <= g < 600:
                gaps.append(g)
                per.setdefault(d.name, []).append(g)
        prev = t + (dur/1000.0 if role == "toolResult" else 0)
gaps.sort()
n = len(gaps)
print("回合间隔样本 %d 个" % n)
for p in (50, 75, 90, 95, 99):
    print("  p%-3d %.1fs" % (p, gaps[int(n*p/100)-1]))
print("  均值 %.1fs  合计 %.0fs" % (sum(gaps)/n, sum(gaps)))
print("  >20s 的间隔 %d 个，合计 %.0fs" % (sum(1 for g in gaps if g > 20), sum(g for g in gaps if g > 20)))
print()
print("间隔最大的 15 个（每题）：")
big = sorted(((g, c) for c, gs in per.items() for g in gs if g > 25), reverse=True)[:15]
for g, c in big:
    print("  %5.0fs  %s" % (g, c))
