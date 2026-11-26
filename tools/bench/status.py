#!/usr/bin/env python3
"""TSecBench 平台侧进度快照（宿主或容器内均可跑）。"""
import json
import os
import sys
import urllib.request

BASE = os.environ.get("BENCHMARK_BASE_URL", "https://tsecbench.zc.tencent.com")
TOK = os.environ.get("BENCHMARK_TOKEN", "1b0b3f85-c2ca-4905-8d32-3b7fbccc699a")


def fetch():
    req = urllib.request.Request(BASE.rstrip("/") + "/openapi/v1/challenges",
                                 headers={"BENCHMARK_TOKEN": TOK})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main():
    items = fetch()
    done = [i for i in items if i.get("is_completed")]
    score = sum(int(i.get("total_score") or 0) for i in done)
    total = sum(int(i.get("total_score") or 0) for i in items)
    byd = {}
    for i in items:
        d = i.get("difficulty", "?")
        byd.setdefault(d, [0, 0])
        byd[d][1] += 1
        if i.get("is_completed"):
            byd[d][0] += 1
    print("完成 %d/%d  得分 %d/%d (%.1f%%)" % (len(done), len(items), score, total,
                                                score * 100.0 / max(total, 1)))
    for d, (a, b) in sorted(byd.items()):
        print("  %-6s %d/%d" % (d, a, b))
    live = [i["unique_code"] for i in items
            if i.get("container_status") not in (None, "stopped")]
    print("活跃容器 %d: %s" % (len(live), ",".join(live)))
    if "--todo" in sys.argv:
        print("未完成: " + ",".join(i["unique_code"] for i in items
                                   if not i.get("is_completed")))
    if "--json" in sys.argv:
        print(json.dumps([{"code": i["unique_code"], "diff": i.get("difficulty"),
                           "score": i.get("total_score"),
                           "done": i.get("is_completed"),
                           "status": i.get("container_status")} for i in items],
                         ensure_ascii=False))


if __name__ == "__main__":
    main()
