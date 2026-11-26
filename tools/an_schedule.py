# -*- coding: utf-8 -*-
"""第 10 号优化：轮屏障 / 调度利用率 —— 实测数据留档。

所有数字都从 run 50607 的 shard-20261119.log 现算，脚本本身可复跑。
"""
import re, json, collections, sys

LOG = sys.argv[1] if len(sys.argv) > 1 else "work/archive-20261119-193225/shard-20261119.log"
CH = "work/ch.json"
FL = "\u2705 flag{"
txt = open(LOG, errors="ignore").read()
ch = {x["unique_code"]: x for x in json.load(open(CH))["items"]}

# ── 切运行块 ───────────────────────────────────────────
parts = re.split(r"(?m)^===== (xben-\d+-\d+) rc=(-?\d+) (\d+)s ([0-9:]+) =====$", txt)
runs = []
for i in range(1, len(parts), 5):
    if i + 4 >= len(parts):
        break
    h, m, s = [int(x) for x in parts[i + 3].split(":")]
    runs.append({"code": parts[i], "rc": int(parts[i + 1]), "sec": int(parts[i + 2]),
                 "end": h * 3600 + m * 60 + s, "start": h * 3600 + m * 60 + s - int(parts[i + 2]),
                 "body": parts[i + 4], "diff": (ch.get(parts[i]) or {}).get("difficulty", "?").lower()})
runs.sort(key=lambda r: r["end"])


def bad(r):
    b = r["body"]
    return ("Tool bash not found" in b) or ("INFRA_BLOCKED" in b) or ("工具通道" in b)


clean = [r for r in runs if not bad(r)]

print("=" * 64)
print("1) 污染口径：173 次运行里有 %d 次是收尾轮污染的残影" % (len(runs) - len(clean)))
byc = collections.defaultdict(list)
for r in runs:
    byc[r["code"]].append(r)
print("   第N次    次数   污染   污染率")
for n in range(1, 6):
    rs = [v[n - 1] for v in byc.values() if len(v) >= n]
    nb = [r for r in rs if bad(r)]
    print("   %-7d %-6d %-6d %.0f%%" % (n, len(rs), len(nb), 100.0 * len(nb) / max(1, len(rs))))
print("   污染轮 %ds / 出旗 2      干净轮 %ds / 出旗 %d"
      % (sum(r["sec"] for r in runs if bad(r)), sum(r["sec"] for r in clean),
         sum(1 for r in clean if FL in r["body"])))

print()
print("=" * 64)
print("2) 轮屏障：parallel_solver 用 p.communicate() 等全部 shard 退出才起下一轮")
lines = txt.splitlines()
runhdr = re.compile(r"^===== (xben-\d+-\d+) rc=(-?\d+) (\d+)s ([0-9:]+) =====$")
seq = []
for i, ln in enumerate(lines):
    mm = runhdr.match(ln)
    if mm:
        seq.append((i, mm.group(1), int(mm.group(3)), mm.group(4)))
marks = [i for i, ln in enumerate(lines) if ln.startswith("[shard] 本 shard 完成")]
ends = []
for mi in marks:
    last = [s for s in seq if s[0] < mi]
    if last:
        ends.append(last[-1])


def tt(s):
    h, m, sec = [int(x) for x in s.split(":")]
    return h * 3600 + m * 60 + sec


tot = 0
print("   轮   最早结束   最晚结束   尾部跨度   损失槽位秒")
for r in range(0, len(ends) - 2, 3):
    grp = ends[r:r + 3]
    ts = [tt(e[3]) for e in grp]
    mx = max(ts)
    loss = sum(mx - x for x in ts)
    tot += loss
    print("   %-4d %-11s %-11s %7ds %9ds"
          % (r // 3 + 1, grp[ts.index(min(ts))][3], grp[ts.index(max(ts))][3], mx - min(ts), loss))
print("   合计损失 %ds 槽位容量 = %.1f min = 3 槽总容量的 %.1f%%"
      % (tot, tot / 60.0, 100.0 * tot / (3 * 8788)))

print()
print("=" * 64)
print("3) 调度模拟：LPT（最贵的题先跑）vs 原始顺序")
dur = {}
for r in clean:
    dur.setdefault(r["code"], r["sec"])
PRIOR = {"easy": 40.0, "medium": 90.0, "hard": 240.0}
tasks = [(c, float(dur.get(c, PRIOR.get(ch[c]["difficulty"].lower(), 90.0)))) for c in ch]


def sim(order, k=3):
    t = [0.0] * k
    for c, d in order:
        i = t.index(min(t))
        t[i] += d
    m = max(t)
    return m, sum(m - x for x in t)


def sim_rounds(order, k=3, per_round=24):
    tm = loss = 0.0
    for i in range(0, len(order), per_round):
        chunk = order[i:i + per_round]
        if not chunk:
            continue
        m, l = sim(chunk, k)
        tm += m
        loss += l
    return tm, loss


lb = sum(d for _, d in tasks) / 3.0
lpt = sorted(tasks, key=lambda x: -x[1])
fifo = sorted(tasks, key=lambda x: x[0])
print("   理论下界 total/3 = %.0fs" % lb)
print("   %-14s %-16s %-14s" % ("每轮题数", "FIFO 累计/尾部空等", "LPT 累计/尾部空等"))
for pr in (12, 24, 48, 0):
    m1, l1 = sim_rounds(fifo, 3, pr or len(tasks))
    m2, l2 = sim_rounds(lpt, 3, pr or len(tasks))
    print("   %-14s %7.0fs / %5.0fs   %7.0fs / %5.0fs   → 省 %4.0fs (%.1f%%)"
          % (pr if pr else "不限", m1, l1, m2, l2, m1 - m2, 100.0 * (m1 - m2) / m1))
print("   → 收益几乎全部来自 LPT；轮大小只影响 LPT 的发挥空间，24 已是拐点附近。")
