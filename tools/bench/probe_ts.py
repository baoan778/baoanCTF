import json
from pathlib import Path
ROOT = Path("/opt/pi-ctf")
d = ROOT / "work/solve" / "xben-015-24"
js = sorted(d.glob("*.jsonl"))
print("files:", js)
f = js[-1]
n = 0
for ln in f.read_text(errors="replace").splitlines():
    try:
        o = json.loads(ln)
    except Exception:
        continue
    n += 1
    if n <= 3:
        print("keys:", list(o.keys()), "ts=", repr(o.get("timestamp"))[:60])
print("lines:", n)
