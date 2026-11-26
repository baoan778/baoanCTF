import json
from pathlib import Path
d = Path("/opt/pi-ctf/work/solve/xben-028-24")
js = sorted(d.glob("*.jsonl"))
cnt = 0
for ln in js[-1].read_text(errors="replace").splitlines():
    try: o = json.loads(ln)
    except Exception: continue
    m = o.get("message") or {}
    if m.get("role") == "toolResult":
        print(json.dumps({k: (v if k != "content" else [p.get("type") for p in v]) for k, v in m.items()}, ensure_ascii=False)[:500])
        cnt += 1
    if cnt > 2: break
