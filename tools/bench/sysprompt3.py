import json
from pathlib import Path
d = Path("/opt/pi-ctf/work/solve/xben-029-24")
js = sorted(d.glob("*.jsonl"))
for ln in js[-1].read_text(errors="replace").splitlines():
    o = json.loads(ln)
    m = o.get("message") or {}
    if m.get("role") != "system": continue
    secs = m.get("sections") or {}
    print("=== sections keys ===")
    for k, v in secs.items():
        s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        print("  %-28s %7d 字节  %s" % (k, len(s), s[:100].replace(chr(10)," ")))
    ta = m.get("toolsAdded") or []
    print()
    print("=== tools (%d 个) ===" % len(ta))
    for t in ta:
        s = json.dumps(t, ensure_ascii=False)
        print("  %-16s %7d 字节" % (t.get("name"), len(s)))
    break
