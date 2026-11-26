import json, glob, os
from pathlib import Path
ROOT = Path("/opt/pi-ctf/work/solve")
d = ROOT / "xben-029-24"
js = sorted(d.glob("*.jsonl"))
for ln in js[-1].read_text(errors="replace").splitlines():
    o = json.loads(ln)
    m = o.get("message") or {}
    if m.get("role") == "system":
        c = m.get("content")
        txt = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
        print("system 总字节:", len(txt))
        # 拆分出各段：以 ## 标题切
        import re
        parts = re.split(r"(?m)^(#+ .*)$", txt)
        # parts: [pre, h1, body1, h2, body2...]
        print("---- 段列表 ----")
        print("  [pre] %d 字节" % len(parts[0]))
        for i in range(1, len(parts), 2):
            print("  %-60s %6d 字节" % (parts[i][:60], len(parts[i+1])))
        break
