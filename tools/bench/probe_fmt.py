import json, sys
from pathlib import Path
d = Path("/opt/pi-ctf/work/solve/xben-028-24")
js = sorted(d.glob("*.jsonl"))
print(js)
n = 0
for ln in js[-1].read_text(errors="replace").splitlines():
    try: o = json.loads(ln)
    except Exception: continue
    m = o.get("message") or {}
    if m.get("role") == "assistant" or (m.get("content") and isinstance(m.get("content"), list)):
        parts = m.get("content")
        types = [p.get("type") for p in parts if isinstance(p, dict)] if isinstance(parts, list) else [type(parts).__name__]
        print(o.get("type"), m.get("role"), types)
        n += 1
        if n > 12: break
print("---sample tool result---")
cnt = 0
for ln in js[-1].read_text(errors="replace").splitlines():
    try: o = json.loads(ln)
    except Exception: continue
    m = o.get("message") or {}
    c = m.get("content")
    if isinstance(c, list):
        for p in c:
            if isinstance(p, dict) and p.get("type") not in ("text",):
                print(json.dumps(p, ensure_ascii=False)[:600])
                cnt += 1
    if cnt > 3: break
