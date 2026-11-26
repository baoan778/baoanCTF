import json
from pathlib import Path
d = Path("/opt/pi-ctf/work/solve/xben-029-24")
js = sorted(d.glob("*.jsonl"))
tot_in = tot_cache = tot_out = 0
for ln in js[-1].read_text(errors="replace").splitlines():
    o = json.loads(ln)
    m = o.get("message") or {}
    if m.get("role") != "assistant": continue
    u = m.get("usage") or {}
    if not u: continue
    i, c, out = u.get("input",0), u.get("cacheRead",0), u.get("output",0)
    tot_in += i; tot_cache += c; tot_out += out
    print("  in=%6d cacheRead=%6d out=%4d dur=%5dms 含缓存率=%.0f%%" % (i, c, out, m.get("durationMs") or 0, 100.0*c/max(1,i+c)))
print("总计 in=%d cacheRead=%d out=%d  缓存命中率=%.1f%%" % (tot_in, tot_cache, tot_out, 100.0*tot_cache/max(1,tot_in+tot_cache)))
