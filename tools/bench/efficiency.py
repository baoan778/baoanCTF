import json, re
from pathlib import Path
from collections import defaultdict
ROOT = Path("/opt/pi-ctf")
import urllib.request
BASE = "https://tsecbench.zc.tencent.com"
TOK = "1b0b3f85-c2ca-4905-8d32-3b7fbccc699a"
req = urllib.request.Request(BASE + "/openapi/v1/challenges", headers={"BENCHMARK_TOKEN": TOK})
items = json.loads(urllib.request.urlopen(req, timeout=30).read())
diff = {i["unique_code"]: (i.get("difficulty") or "?").lower() for i in items}
sc = {i["unique_code"]: int(i.get("total_score") or 0) for i in items}
done = {i["unique_code"] for i in items if i.get("is_completed")}

led = json.loads((ROOT / "work/solve/ledger.json").read_text())
print("%-14s %-7s %-5s %-6s %-7s %s" % ("code", "diff", "ok", "sec", "runs", "产出(分/秒)"))
by = defaultdict(list)
for c, v in sorted(led.items(), key=lambda kv: diff.get(kv[0], "?")):
    d = diff.get(c, "?")
    ok = c in done
    sec = v.get("total_sec", 0)
    by[d].append((ok, sec, sc.get(c, 0), v.get("runs", 0)))

print()
for d in ("easy", "medium", "hard"):
    lst = by.get(d, [])
    if not lst: continue
    win = [x for x in lst if x[0]]
    lose = [x for x in lst if not x[0]]
    tw = sum(x[1] for x in win); tl = sum(x[1] for x in lose)
    pts = sum(x[2] for x in win)
    print(f"== {d}: 跑过 {len(lst)} (成 {len(win)} 败 {len(lose)})")
    print(f"   成功耗时合计 {tw:.0f}s 均值 {tw/max(len(win),1):.0f}s | 失败耗时合计 {tl:.0f}s 均值 {tl/max(len(lose),1):.0f}s")
    print(f"   得分 {pts} → 每秒 {pts/max(tw+tl,1):.2f} 分/秒（含失败浪费）")
    if win:
        ss = sorted(x[1] for x in win)
        print(f"   成功耗时 分位: min {ss[0]:.0f} p50 {ss[len(ss)//2]:.0f} p90 {ss[int(len(ss)*0.9)]:.0f} max {ss[-1]:.0f}")
    for ok, sec, s, runs in lst[:0]:
        pass
