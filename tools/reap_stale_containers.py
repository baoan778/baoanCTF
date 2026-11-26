#!/usr/bin/env python3
"""reap_stale_containers.py —— 启动求解器前回收「没人用」的活跃容器名额。

为什么必须有（2026-11-14 实测死锁）：
  pkill 掉 shard 后，它已经 start 过的题目容器**不会自动关闭**，仍占 3 个名额之一。
  新一轮 shard 起不来 → 全卡在「活跃容器已满」重试 → 0 个 pi 在跑，看着进程都在。
  所以开跑前必须先问平台「现在谁占着名额」，把不归本轮管的关掉。

用法：
  python3 tools/reap_stale_containers.py            # 关掉所有 available（开跑前清场）
  python3 tools/reap_stale_containers.py --dry-run  # 只看不关
"""
from __future__ import annotations
import argparse, json, os, sys
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    from tsec_benchmark import TSecBenchmark, TSecError
    base, token = load_cfg()
    with TSecBenchmark(base_url=base, token=token) as c:
        chs = c.list_challenges()
        stale = [x for x in chs if x.container_status in ("available", "pending", "stop_pending")]
        if not stale:
            print("没有占用名额的容器")
            return 0
        for x in stale:
            if args.dry_run:
                print(f"[dry] 会关闭 {x.unique_code} ({x.container_status})")
                continue
            try:
                c.close_challenge(x.unique_code)
                print(f"已关闭 {x.unique_code}（释放名额）")
            except TSecError as e:
                print(f"关闭 {x.unique_code} 失败：{type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())