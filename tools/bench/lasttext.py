import json, sys
from pathlib import Path
ROOT = Path("/opt/pi-ctf")
for code in ("xben-027-24", "xben-025-24", "xben-023-24", "xben-011-24"):
    d = ROOT / "work/solve" / code
    js = sorted(d.glob("*.jsonl"))
    if not js:
        continue
    texts = []
    for ln in js[-1].read_text(errors="replace").splitlines():
        try:
            o = json.loads(ln)
        except Exception:
            continue
        m = o.get("message") or {}
        if m.get("role") != "assistant":
            continue
        for part in (m.get("content") or []):
            if isinstance(part, dict) and part.get("type") == "text":
                t = (part.get("text") or "").strip()
                if t:
                    texts.append(t)
    print("=" * 20, code)
    print(texts[-1][:900] if texts else "(no text)")
    print()
