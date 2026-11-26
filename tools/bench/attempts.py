import json, re, sys
from pathlib import Path
from collections import defaultdict, Counter
ROOT = Path("/opt/pi-ctf")
LOG = ROOT / "log" / "shard-20261119.log"

# 平台 API（走 status.py 同样的口径）
import urllib.request
BASE = "https://tsecbench.zc.tencent.com"
TOK = "1b0b3f85-c2ca-4905-8d32-3b7fbccc699a"
req = urllib.request.Request(BASE + "/openapi/v1/challenges", headers={"BENCHMARK_TOKEN": TOK})
items = json.loads(urllib.request.urlopen(req, timeout=30).read())
diff = {i["unique_code"]: (i.get("difficulty") or "?").lower() for i in items}
done = {i["unique_code"] for i in items if i.get("is_completed")}
print("平台:", len(done), "/", len(items))

# 解析 shard 日志：每题每次尝试的结局
txt = LOG.read_text(errors="replace").splitlines()
starts = Counter()
succ = Counter()
rc124 = Counter()
cand0 = Counter()
tries_by = defaultdict(list)
cur = None
for ln in txt:
    m = re.search(r"\[shard\] (xben-\d+-24) → \[", ln)
    if m:
        cur = m.group(1)
        starts[cur] += 1
        continue
    m = re.search(r"\[shard\] (xben-\d+-24): agent rc=(\d+)，候选 (\d+) 个", ln)
    if m:
        c, rc, nc = m.group(1), int(m.group(2)), int(m.group(3))
        tries_by[c].append((rc, nc))
        if rc == 124:
            rc124[c] += 1
        if nc == 0:
            cand0[c] += 1
        continue
    if "✅" in ln and "flag{" in ln:
        pass

print()
print("== 按难度 ==")
for d in ("easy", "medium", "hard"):
    codes = [c for c in diff if diff[c] == d]
    st = sum(starts[c] for c in codes)
    ok = sum(1 for c in codes if c in done)
    print(f"{d}: 题 {len(codes)}  已过 {ok}  总启动 {st}  平均尝试 {st/max(len(codes),1):.2f}")
    if d != "easy":
        # 谁跑过、跑几次、结果
        rows = sorted(((starts[c], c, tries_by.get(c, [])) for c in codes if starts[c] > 0), reverse=True)
        for n, c, ts in rows[:20]:
            print(f"   {c} 启动{n} 结果{ts}")
