import json
from pathlib import Path
for code in ("xben-029-24", "xben-012-24", "xben-071-24"):
    d = Path("/opt/pi-ctf/work/solve") / code
    js = sorted(d.glob("*.jsonl"))
    if not js: continue
    rows = []
    for ln in js[-1].read_text(errors="replace").splitlines():
        try: o = json.loads(ln)
        except Exception: continue
        m = o.get("message") or {}
        t = m.get("timestamp")
        if not isinstance(t, (int, float)): continue
        role = m.get("role")
        rows.append((t/1000.0, role, m.get("durationMs") or 0,
                     len(json.dumps(m, ensure_ascii=False))))
    rows.sort()
    print("=====", code, "条数", len(rows))
    prev = None
    for t, role, dur, sz in rows:
        gap = "" if prev is None else "  间隔 %5.1fs" % (t - prev)
        print("  %-11s 自身 %6.1fs%s  (msg %5d 字节)" % (role, dur/1000 if role=="toolResult" else 0, gap, sz))
        prev = t
    print()
