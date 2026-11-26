#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compare.py — R10 P0-B：把两臂评测结果做成**可复核的差值表**。

## 为什么必须存在（缺陷 50）

README #14 声称「A/B 三轮（n=24/臂，三臂 0 异常样本）」，但本工作区与容器里
log/evalbench/ 和 work/evalsolve/ 都不存在 —— 结论无法复核。
根因不是"没写落盘语句"（run.py 有），而是：①结果落在会被清理的 log/ 下；
②没有 summary、没有索引、没有对比工具，人只能读散行 jsonl。

本工具的定位：**只吃 summary.json，不猜**。缺证据就说缺证据。

## 用法

    python3 tools/evalbench/compare.py                      # 列出所有臂
    python3 tools/evalbench/compare.py --diff cur,revert    # 两臂差值（含 md）
    python3 tools/evalbench/compare.py --from-log           # 回退读旧 jsonl

## 判据（写死，避免口算）

- 分母是**墙钟秒**，不是"槽位秒"，也不是"调用耗时累加"（R10-01 §2）。
- 只比同一批 code 的交集；某臂缺某题 -> 该题**不参与**中位数，但要在 missing 里列出。
- 成功率分母是**跑了几次**，崩溃臂也必须计入（status=failed 也算分母）。
- 成本一律标 ESTIMATE（usage.cost.totalFromProvider=false）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RESULT_DIR = Path(os.environ.get("PI_EVALBENCH_RESULT_DIR",
                                 str(ROOT / "tools" / "evalbench" / "results")))
LOG_DIR = ROOT / "log" / "evalbench"


def _pct(vals, p):
    if not vals:
        return 0.0
    v = sorted(vals)
    k = max(0, min(len(v) - 1, int(round((len(v) - 1) * p))))
    return float(v[k])


def load_summaries(result_dir=RESULT_DIR):
    """读 summary.json；每个臂取**最新一份**（按 finished_at）。"""
    out = {}
    if not Path(result_dir).is_dir():
        return out
    for p in sorted(Path(result_dir).glob("*-summary.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        arm = d.get("arm") or p.stem
        prev = out.get(arm)
        if prev is None or str(d.get("finished_at", "")) >= str(prev.get("finished_at", "")):
            out[arm] = d
    return out


def load_from_log(log_dir=LOG_DIR):
    """旧路径回退：把 log/evalbench/<arm>.jsonl 聚合成同结构。"""
    out = {}
    if not Path(log_dir).is_dir():
        return out
    for p in sorted(Path(log_dir).glob("*.jsonl")):
        rows = []
        for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rows.append(json.loads(ln))
            except Exception:
                continue
        if rows:
            out[p.stem] = aggregate(p.stem, rows)
    return out


def aggregate(arm, rows):
    ok = [r for r in rows if r.get("ok")]
    walls = [float(r.get("wall_sec", 0) or 0) for r in rows]
    cost = sum(float(r.get("cost_est_usd", 0) or 0) for r in rows)
    per_code = {}
    for r in rows:
        per_code.setdefault(r.get("code", "?"), []).append(r)
    return {
        "arm": arm,
        "n": len(rows),
        "ok": len(ok),
        "ok_rate": round(len(ok) / len(rows), 4) if rows else 0.0,
        "wall_mean_s": round(sum(walls) / len(walls), 1) if walls else 0.0,
        "wall_median_s": round(_pct(walls, 0.5), 1),
        "wall_p90_s": round(_pct(walls, 0.9), 1),
        "cost_est_usd": round(cost, 6),
        "cost_is_estimate": True,
        "per_code": {c: {"n": len(v), "ok": sum(1 for x in v if x.get("ok"))}
                     for c, v in per_code.items()},
        "finished_at": max((str(r.get("finished_at") or r.get("ts") or "") for r in rows), default=""),
    }


def show(arms):
    if not arms:
        print("[compare] 没有任何臂的结果。先跑 run.py，或检查 " + str(RESULT_DIR))
        return 1
    print("%-16s %4s %6s %9s %9s %9s %11s" % ("arm", "n", "ok", "median_s", "mean_s", "p90_s", "cost_EST"))
    for arm in sorted(arms):
        d = arms[arm]
        print("%-16s %4d %6d %9.1f %9.1f %9.1f %11s" % (
            arm, d.get("n", 0), d.get("ok", 0),
            d.get("wall_median_s", 0), d.get("wall_mean_s", 0), d.get("wall_p90_s", 0),
            "$" + ("%.4f" % float(d.get("cost_est_usd", 0)))))
    print("\n分母口径：墙钟秒（wall）；成本为本地费率估算，非账单。")
    return 0


def diff(a, b, arms):
    if a not in arms or b not in arms:
        print("[compare] 缺臂：" + ", ".join(x for x in (a, b) if x not in arms))
        return 2
    A, B = arms[a], arms[b]
    keys = ["n", "ok", "ok_rate", "wall_median_s", "wall_mean_s", "wall_p90_s", "cost_est_usd"]
    print("# 两臂差值：" + a + " vs " + b + "\n")
    print("| 指标 | " + a + " | " + b + " | 差值 | 相对 |")
    print("|---|---|---|---|---|")
    for k in keys:
        va, vb = float(A.get(k, 0) or 0), float(B.get(k, 0) or 0)
        rel = ("%+.1f%%" % ((va - vb) / vb * 100.0)) if vb else "n/a"
        print("| %s | %.4g | %.4g | %+.4g | %s |" % (k, va, vb, va - vb, rel))
    ca = set((A.get("per_code") or {}).keys())
    cb = set((B.get("per_code") or {}).keys())
    only_a, only_b = sorted(ca - cb), sorted(cb - ca)
    print("\n只在一臂出现的题：" + ("无" if not only_a and not only_b
                                  else a + ":" + str(only_a) + " " + b + ":" + str(only_b)))
    print("\n> 成本为估算（totalFromProvider=false），不可当账单引用。")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--diff", default="", help="两臂，逗号分隔：A,B")
    ap.add_argument("--from-log", action="store_true", help="回退读 log/evalbench/*.jsonl")
    ap.add_argument("--dir", default="", help="summary 目录")
    a = ap.parse_args()
    d = Path(a.dir) if a.dir else RESULT_DIR
    arms = load_from_log() if a.from_log else load_summaries(d)
    if a.diff:
        parts = [x.strip() for x in a.diff.split(",") if x.strip()]
        if len(parts) != 2:
            print("[compare] --diff 需要两个臂名，用逗号分隔")
            return 2
        return diff(parts[0], parts[1], arms)
    return show(arms)


if __name__ == "__main__":
    sys.exit(main())