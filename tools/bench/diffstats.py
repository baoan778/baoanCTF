import json, os, re, time
from pathlib import Path
from collections import defaultdict
ROOT = Path("/opt/pi-ctf")

# 1) 平台侧：拿难度分布
cfg = json.loads(Path("/home/ctf/.pi-ctf/agent/tsecbench.json").read_text())
import subprocess, urllib.request
req = urllib.request.Request(cfg["base_url"].rstrip("/") + "/openapi/v1/challenges",
                             headers={"BENCHMARK_TOKEN": cfg["token"]})
data = json.loads(urllib.request.urlopen(req, timeout=20).read())
chs = data["challenges"] if isinstance(data, dict) and "challenges" in data else data
print("challenge fields:", list(chs[0].keys()))
diff = {}
for c in chs:
    diff[c["unique_code"]] = (c.get("difficulty") or "?").lower()
from collections import Counter
print("difficulty counts:", Counter(diff.values()))

# 2) ledger 统计
led = json.loads((ROOT / "work/solve/ledger.json").read_text())
rows = []
for code, v in led.items():
    rows.append((diff.get(code, "?"), code, v.get("attempts", 0), v.get("total_sec", 0), v.get("runs", 0)))

by = defaultdict(list)
for d, code, a, s, r in rows:
    by[d].append((code, a, s, r))
print()
for d in ("easy", "medium", "hard"):
    lst = by.get(d, [])
    if not lst:
        continue
    tot = sum(x[2] for x in lst)
    print(f"{d}: 记录 {len(lst)} 题, 总耗时 {tot:.0f}s, 平均 {tot/max(len(lst),1):.0f}s")
    multi = [x for x in lst if x[3] >= 2]
    print(f"   跑过>=2轮的: {len(multi)}")
