import json
from pathlib import Path
from collections import Counter
ROOT = Path("/opt/pi-ctf/work/solve")
names = Counter(); durs = Counter()
for d in ROOT.iterdir():
    if not d.is_dir(): continue
    js = sorted(d.glob("*.jsonl"))
    if not js: continue
    for ln in js[-1].read_text(errors="replace").splitlines():
        try: o = json.loads(ln)
        except Exception: continue
        m = o.get("message") or {}
        if m.get("role") != "toolResult": continue
        n = m.get("toolName") or "?"
        names[n] += 1
        durs[n] += (m.get("durationMs") or 0)/1000.0
print("%-14s %6s %9s %8s" % ("工具", "次数", "总秒", "均秒"))
for n, c in names.most_common():
    print("%-14s %6d %9.0f %8.1f" % (n, c, durs[n], durs[n]/c))
