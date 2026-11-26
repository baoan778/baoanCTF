import json, os
from pathlib import Path
ROOT = Path("/opt/pi-ctf")
p = Path("/home/ctf/.pi-ctf/agent/tsecbench.json")
print("cfg exists:", p.exists())
if p.exists():
    cfg = json.loads(p.read_text())
    print("cfg keys:", list(cfg.keys()))
led = json.loads((ROOT / "work/solve/ledger.json").read_text())
print("ledger type:", type(led).__name__)
if isinstance(led, dict):
    ks = list(led.keys())
    print("top keys:", ks[:6], "n=", len(ks))
    first = led[ks[0]]
    print("sample:", ks[0], json.dumps(first, ensure_ascii=False)[:400])
