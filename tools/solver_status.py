#!/usr/bin/env python3
"""solver_status.py —— 求解进度快照（只读）。

用法：python tools/solver_status.py
"""
from __future__ import annotations
import json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_cfg():
    base = os.environ.get("BENCHMARK_BASE_URL") or os.environ.get("TSEC_BASE_URL") or ""
    token = os.environ.get("BENCHMARK_TOKEN") or os.environ.get("TSEC_TOKEN") or ""
    for cand in (Path.home() / ".pi-ctf/agent/tsecbench.json", ROOT / ".pi/tsecbench.json"):
        if cand.is_file() and (not base or not token):
            cfg = json.loads(cand.read_text(encoding="utf-8"))
            base = base or cfg.get("base_url", "")
            token = token or cfg.get("token", "")
    return base, token


def main() -> int:
    from tsec_benchmark import TSecBenchmark
    base, token = load_cfg()
    with TSecBenchmark(base_url=base, token=token) as c:
        chs = c.list_challenges()
    done = [x for x in chs if x.is_completed]
    avail = [x for x in chs if x.container_status == "available"]
    print(f"通关 {len(done)}/{len(chs)}  活跃容器 {len(avail)}")
    for x in avail:
        print(f"  running: {x.unique_code} {x.difficulty} {x.container_addr}")
    by = {}
    for x in done:
        by[x.difficulty] = by.get(x.difficulty, 0) + 1
    print("已通关分布:", json.dumps(by, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())