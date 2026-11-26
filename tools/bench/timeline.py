import json, sys
from pathlib import Path
code = sys.argv[1]
d = Path("/opt/pi-ctf/work/solve") / code
js = sorted(d.glob("*.jsonl"))
for ln in js[-1].read_text(errors="replace").splitlines():
    try: o = json.loads(ln)
    except Exception: continue
    m = o.get("message") or {}
    role = m.get("role")
    if role != "assistant": continue
    for p in (m.get("content") or []):
        if not isinstance(p, dict): continue
        if p.get("type") == "toolCall":
            args = p.get("arguments") or {}
            cmd = args.get("command") or json.dumps(args, ensure_ascii=False)[:200]
            print("--- TOOL:", p.get("name"))
            print(cmd[:700])
        elif p.get("type") == "text":
            print("--- TEXT:", (p.get("text") or "")[:300])
        elif p.get("type") == "thinking":
            print("--- THINK:", (p.get("thinking") or "")[:200].replace(chr(10), " "))
