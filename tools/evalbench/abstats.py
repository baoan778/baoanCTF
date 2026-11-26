# -*- coding: utf-8 -*-
"""abstats.py — R11 A/B 统计：把两臂的 wall_sec 做成可判定的结论。

为什么不能用"平均提速 X%"：
  R10-04 实测同配置两跑的 p90 差 13%、成本差 15.5%（n=2），
  所以单看均值会把噪声当效果。这里给三种判据：
    1) 中位数 + 四分位距 IQR（抗离群）
    2) 逐题配对差（同一题在两臂的差，n 个样本 -> 配对更有效）
    3) 符号检验（配对差的符号分布，p 二项尾概率）—— 只看"方向是否稳定"

判据（写死）：
  - 两臂 n 必须相等且 >= 8，否则只报描述统计、不下结论。
  - 解出率（ok/n）差异是**准确性**红线：降档若让 ok 下降，速度再快也不采纳。
  - 报告一律带 median 与 IQR，不用 mean 当主指标（离群会把结论带偏）。
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RES = ROOT / "tools" / "evalbench" / "results"


def load_arm(arm):
    """读该臂**最新**一份 summary 的原始明细；没有明细则回读 jsonl。"""
    p = sorted(RES.glob(arm + "-*-summary.json"))
    rows = []
    if p:
        d = json.loads(p[-1].read_text(encoding="utf-8"))
        rows = d.get("rows") or []
    if not rows:
        lg = ROOT / "log" / "evalbench" / (arm + ".jsonl")
        if lg.exists():
            for ln in lg.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    rows.append(json.loads(ln))
                except Exception:
                    continue
    return rows


def desc(vals):
    if not vals:
        return {}
    v = sorted(vals)
    q1 = v[len(v) // 4]
    q3 = v[(3 * len(v)) // 4]
    return {"n": len(v), "median": st.median(v), "q1": q1, "q3": q3,
            "iqr": q3 - q1, "min": v[0], "max": v[-1]}


def sign_test(diffs):
    """配对差的符号检验（双侧二项尾概率近似，n<=25 用精确枚举）。"""
    nz = [d for d in diffs if abs(d) > 1e-9]
    n = len(nz)
    if n == 0:
        return 1.0, n
    k = sum(1 for d in nz if d > 0)
    from math import comb
    tail = sum(comb(n, i) for i in range(0, min(k, n - k) + 1)) / (2 ** n)
    return min(1.0, 2 * tail), n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default="think-high")
    ap.add_argument("--b", default="think-medium")
    a = ap.parse_args()
    A, B = load_arm(a.a), load_arm(a.b)
    for nm, rows in ((a.a, A), (a.b, B)):
        print("arm %-16s rows=%d" % (nm, len(rows)))
    if not A or not B:
        print("\n[abstats] 缺数据：跑完 run.py 再来（或检查 " + str(RES) + "）")
        return 2

    print("\n== 总体（只看成功的运行）==")
    print("%-16s %5s %10s %9s %10s %8s %8s" % ("arm", "ok/n", "median_s", "IQR", "mean_s", "min", "max"))
    stats = {}
    for nm, rows in ((a.a, A), (a.b, B)):
        ok = [r for r in rows if r.get("ok")]
        d = desc([float(r.get("wall_sec", 0)) for r in ok])
        stats[nm] = (rows, ok, d)
        print("%-16s %5s %10.1f %9.1f %10.1f %8.1f %8.1f" % (
            nm, "%d/%d" % (len(ok), len(rows)), d.get("median", 0), d.get("iqr", 0),
            (sum(float(r.get("wall_sec", 0)) for r in ok) / len(ok)) if ok else 0,
            d.get("min", 0), d.get("max", 0)))

    print("\n== 准确性（红线：降档不许降 ok）==")
    for nm, (rows, ok, d) in stats.items():
        print("  %-16s ok=%d/%d = %.1f%%" % (nm, len(ok), len(rows),
                                             100.0 * len(ok) / len(rows) if rows else 0))

    print("\n== 逐题配对（同题两臂中位数之差）==")
    byA, byB = defaultdict(list), defaultdict(list)
    for r in stats[a.a][1]:
        byA[r.get("code")].append(float(r.get("wall_sec", 0)))
    for r in stats[a.b][1]:
        byB[r.get("code")].append(float(r.get("wall_sec", 0)))
    diffs = []
    print("  %-20s %10s %10s %9s" % ("code", a.a, a.b, "diff"))
    for code in sorted(set(byA) & set(byB)):
        ma, mb = st.median(byA[code]), st.median(byB[code])
        diffs.append(mb - ma)
        print("  %-20s %10.1f %10.1f %+9.1f" % (code, ma, mb, mb - ma))

    print("\n== 结论 ==")
    okA, okB = stats[a.a][1], stats[a.b][1]
    rateA = len(okA) / len(stats[a.a][0]) if stats[a.a][0] else 0
    rateB = len(okB) / len(stats[a.b][0]) if stats[a.b][0] else 0
    if rateB < rateA:
        print("  ✗ 准确性回退：%s 解出率 %.1f%% < %s %.1f%%" % (a.b, rateB * 100, a.a, rateA * 100))
        print("    => 无论快多少，都不采纳降档。")
    else:
        print("  ✓ 准确性未退（%.1f%% vs %.1f%%）" % (rateA * 100, rateB * 100))
    if diffs:
        md = st.median(diffs)
        print("  逐题中位差 = %+.1f s（负=乙臂更快），题数=%d" % (md, len(diffs)))
        nA, nB = len(stats[a.a][1]), len(stats[a.b][1])
        # ★ 判据修正（R11-02 实测踩到）：**逐题配对只有 2~4 个点**，
        #   拿它做符号检验毫无意义（n=2 时 p 恒为 0.5 或 1.0，永远不显著）。
        #   正确做法是两件事分开看：
        #     ① 题级中位差 = 方向（这 4 道题整体往哪边偏）
        #     ② **运行级**分布的 bootstrap 置信区间 = 差异是否超出噪声
        #   这里对运行级做 bootstrap（重采样 2000 次求中位差的 95% CI）。
        ra = [float(r.get("wall_sec", 0)) for r in stats[a.a][1]]
        rb = [float(r.get("wall_sec", 0)) for r in stats[a.b][1]]
        if len(ra) >= 8 and len(rb) >= 8:
            import random
            random.seed(42)
            boots = []
            for _ in range(2000):
                sa = [random.choice(ra) for _ in ra]
                sb = [random.choice(rb) for _ in rb]
                boots.append(st.median(sb) - st.median(sa))
            boots.sort()
            lo, hi = boots[int(0.025 * len(boots))], boots[int(0.975 * len(boots))]
            print("  运行级中位差 bootstrap 95%% CI = [%+.1f, %+.1f] s" % (lo, hi))
            if lo <= 0 <= hi:
                print("  ⚠ CI 跨 0 → **与噪声不可区分**，不许写「提速」")
            else:
                print("  ✓ CI 不跨 0 → 差异超出噪声（%s 更快）" % (a.b if hi < 0 else a.a))
        else:
            print("  ⚠ 运行级样本不足（每臂需 >=8）→ 只报描述，不下结论")
    return 0


if __name__ == "__main__":
    sys.exit(main())