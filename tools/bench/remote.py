import json, os, urllib.request
BASE = os.environ.get("BENCHMARK_BASE_URL", "https://tsecbench.zc.tencent.com")
TOK = os.environ.get("BENCHMARK_TOKEN", "1b0b3f85-c2ca-4905-8d32-3b7fbccc699a")
req = urllib.request.Request(BASE.rstrip("/") + "/openapi/v1/challenges", headers={"BENCHMARK_TOKEN": TOK})
items = json.load(urllib.request.urlopen(req, timeout=30))
from collections import defaultdict
d = defaultdict(lambda: [0,0,0,0])  # done_cnt, total_cnt, done_score, total_score
left = []
for i in items:
    k = i.get("difficulty","?")
    s = int(i.get("total_score") or 0)
    d[k][1] += 1; d[k][3] += s
    if i.get("is_completed"):
        d[k][0] += 1; d[k][2] += s
    else:
        left.append((k, s, i["unique_code"]))
for k, v in sorted(d.items()):
    print("%-7s %2d/%-3d  分数 %5d/%-6d" % (k, v[0], v[1], v[2], v[3]))
print()
print("剩余题（按难度/分值）：")
for k, s, c in sorted(left, key=lambda x: ({"easy":0,"medium":1,"hard":2}.get(x[0],9), -x[1])):
    print("  %-7s %5d  %s" % (k, s, c))
print("共 %d 题, 剩余可得分 %d" % (len(left), sum(x[1] for x in left)))
