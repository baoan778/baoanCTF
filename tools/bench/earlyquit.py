import json, re
from pathlib import Path
from datetime import datetime
ROOT = Path("/opt/pi-ctf")
LOG = ROOT / "log" / "shard-20261119.log"
lines = LOG.read_text(errors="replace").splitlines()
# shard 日志没有时间戳，只能按顺序推断。改从会话 jsonl 拿时间。
import urllib.request
req = urllib.request.Request("https://tsecbench.zc.tencent.com/openapi/v1/challenges",
                             headers={"BENCHMARK_TOKEN": "1b0b3f85-c2ca-4905-8d32-3b7fbccc699a"})
items = json.loads(urllib.request.urlopen(req, timeout=30).read())
diff = {i["unique_code"]: (i.get("difficulty") or "?").lower() for i in items}
done = {i["unique_code"] for i in items if i.get("is_completed")}

rows = []
for d in sorted((ROOT / "work/solve").iterdir()):
    if not d.is_dir() or not d.name.startswith("xben-"):
        continue
    js = sorted(d.glob("*.jsonl"))
    if not js:
        continue
    # 取最后一个会话，算首末消息时间差与工具调用数
    f = js[-1]
    ts = []
    tools = 0
    for ln in f.read_text(errors="replace").splitlines():
        try:
            o = json.loads(ln)
        except Exception:
            continue
        t = o.get("timestamp")
        if isinstance(t, str) and t.endswith("Z"):
            try:
                from datetime import datetime as _dt
                ts.append(_dt.strptime(t[:23], "%Y-%m-%dT%H:%M:%S.%f").timestamp())
            except Exception:
                pass
        m = o.get("message") or {}
        for part in (m.get("content") or []):
            if isinstance(part, dict) and part.get("type") == "toolCall":
                tools += 1
    if not ts:
        continue
    dur = (max(ts) - min(ts))
    rows.append((diff.get(d.name, "?"), d.name, d.name in done, dur, tools, len(js)))

rows.sort(key=lambda r: (r[0] != "medium", -r[3]))
print("%-14s %-7s %-4s %6s %5s %5s" % ("code", "diff", "done", "秒", "工具", "会话"))
for r in rows[:30]:
    print("%-14s %-7s %-4s %6.0f %5d %5d" % (r[1], r[0], "OK" if r[2] else "-", r[3], r[4], r[5]))

print()
med = [r for r in rows if r[0] == "medium"]
if med:
    print("medium 已跑 %d 题，平均时长 %.0fs，平均工具数 %.1f" % (
        len(med), sum(r[3] for r in med)/len(med), sum(r[4] for r in med)/len(med)))
    short = [r for r in med if r[3] < 100 and not r[2]]
    print("  其中「<100s 且未通关」= 早退: %d 题" % len(short), [r[1] for r in short])
