import json
from pathlib import Path
d = Path("/opt/pi-ctf/work/solve/xben-029-24")
js = sorted(d.glob("*.jsonl"))
for ln in js[-1].read_text(errors="replace").splitlines():
    o = json.loads(ln)
    m = o.get("message") or {}
    print("role=", m.get("role"), "keys=", list(m.keys()))
    for k, v in m.items():
        s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
        print("   ", k, "->", len(s), "字节 :", s[:120].replace(chr(10), " "))
    print()
