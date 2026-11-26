#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_evalbench_evidence.py — R10 P0-B：评测结果必须可复核（缺陷 50）

为什么
------
README #14 声称「A/B 三轮（n=24/臂）」，但本工作区与容器里 log/evalbench/ 与
work/evalsolve/ 都不存在 —— 结论无法复核。根因不是"没写落盘语句"，而是：
  ① 结果落在会被清理的 log/ 下；
  ② 没有 summary、没有索引、没有对比工具。

判据
----
A. 证据目录必须耐久（在 历史复盘 data 下），不能是 log/。
B. summary 必须含 n/ok/中位/均值/p90/成本估值，且成本标 is_estimate。
C. 单次异常必须落成 status=failed（否则"跑崩"会被误读成"没跑过"）。
D. PI_EVALBENCH_NOSAVE=1 能整臂关掉落盘（回滚开关）。
E. compare 只认 summary；缺证据时明确报缺，不猜。
F. compare 的分母是墙钟秒，且必须打印"成本为估算"。
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = Path(os.path.dirname(os.path.dirname(HERE)))
EVAL = ROOT / "tools" / "evalbench"
RUNPY = EVAL / "run.py"
CMPPY = EVAL / "compare.py"

_passed, _failed = 0, 0


def ck(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print("  x " + name + (("   [" + str(detail)[:300] + "]") if detail else ""))


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ROWS = [
    {"code": "c1", "arm": "A", "ok": True, "wall_sec": 10.0, "cost_est_usd": 0.02, "tokens": 500},
    {"code": "c1", "arm": "A", "ok": False, "wall_sec": 30.0, "cost_est_usd": 0.05,
     "tokens": 900, "status": "failed"},
    {"code": "c2", "arm": "A", "ok": True, "wall_sec": 20.0, "cost_est_usd": 0.01, "tokens": 100},
]


def main():
    if not RUNPY.exists() or not CMPPY.exists():
        print("SKIP  evalbench 不在发布包里")
        return 77
    cmp_mod = load(CMPPY, "evb_cmp_ut")

    print("A) 证据目录耐久性")
    run_src = RUNPY.read_text(encoding="utf-8")
    ck("A1 run.py 有 RESULT_DIR", "RESULT_DIR" in run_src)
    # ★ 目录必须落在容器**真正 rw 共享**的路径下。R10 用 docker inspect 实测：
    #   容器只共享 kb/log/tools/workspace/work/{intake,solve}；
    #   历史复盘 与 work/ 根都没有挂载 —— 第一版写 历史复盘 data 已踩过。
    ck("A2 证据落在 tools/evalbench/results 下",
       '"tools" / "evalbench" / "results"' in run_src, "not shared with container")
    ck("A3 compare 默认读同一目录",
       '"tools" / "evalbench" / "results"' in CMPPY.read_text(encoding="utf-8"))
    ck("A5 目录可用 PI_EVALBENCH_RESULT_DIR 覆盖",
       "PI_EVALBENCH_RESULT_DIR" in run_src)
    ck("A4 明确指出 log/ 会被清理的原因", "清理" in run_src)

    print("B) summary 内容")
    agg = cmp_mod.aggregate("A", ROWS)
    ck("B1 n=3", agg["n"] == 3, agg["n"])
    ck("B2 ok=2", agg["ok"] == 2, agg["ok"])
    ck("B3 中位 20.0", agg["wall_median_s"] == 20.0, agg["wall_median_s"])
    ck("B4 均值 20.0", agg["wall_mean_s"] == 20.0, agg["wall_mean_s"])
    ck("B5 p90=30.0", agg["wall_p90_s"] == 30.0, agg["wall_p90_s"])
    ck("B6 成本 0.08", abs(agg["cost_est_usd"] - 0.08) < 1e-9, agg["cost_est_usd"])
    ck("B7 成本标估算", agg.get("cost_is_estimate") is True)

    print("C) 崩溃也进分母")
    ck("C1 failed 行仍计入 n", agg["n"] == 3)
    ck("C2 failed 不计入 ok", agg["ok"] == 2)
    ck("C3 run.py 记录 status=failed", '"status": "failed"' in run_src)
    ck("C4 run.py 捕获异常不吞", "except Exception as exc" in run_src)
    ck("C5 异常码进 summary 元数据", '"errors": errors[:20]' in run_src)

    print("D) 回滚开关")
    ck("D1 有 _nosave()", "def _nosave()" in run_src)
    ck("D2 NOSAVE 变量名正确", "PI_EVALBENCH_NOSAVE" in run_src)
    ck("D3 write_summary 受开关保护", "if _nosave():" in run_src)

    print("E) 缺证据不猜")
    with tempfile.TemporaryDirectory() as td:
        arms = cmp_mod.load_summaries(td)
        ck("E1 空目录 -> 空", arms == {}, arms)
        rc = cmp_mod.show(arms)
        ck("E2 show 返回非零表示缺证据", rc == 1, rc)

    print("F) 只认最新一份 summary + 缺臂报错")
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        (base / "A-20260101-000000-summary.json").write_text(
            json.dumps({"arm": "A", "n": 1, "ok": 0, "finished_at": "2026-01-01T00:00:00"}),
            encoding="utf-8")
        (base / "A-20260102-000000-summary.json").write_text(
            json.dumps({"arm": "A", "n": 9, "ok": 9, "finished_at": "2026-01-02T00:00:00"}),
            encoding="utf-8")
        arms = cmp_mod.load_summaries(base)
        ck("F1 取最新（n=9）", arms.get("A", {}).get("n") == 9, arms.get("A", {}).get("n"))
        rc = cmp_mod.diff("A", "B", arms)
        ck("F2 缺臂返回 2", rc == 2, rc)

    print("G) 差值表与口径提示")
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        for arm, med in (("cur", 90.0), ("rev", 120.0)):
            (base / (arm + "-s-summary.json")).write_text(json.dumps({
                "arm": arm, "n": 12, "ok": 10, "ok_rate": 0.8333,
                "wall_median_s": med, "wall_mean_s": med, "wall_p90_s": med + 30,
                "cost_est_usd": 0.5, "cost_is_estimate": True,
                "per_code": {"c1": {"n": 12, "ok": 10}},
                "finished_at": "2026-11-22T00:00:00"}), encoding="utf-8")
        arms = cmp_mod.load_summaries(base)
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cmp_mod.diff("cur", "rev", arms)
        out = buf.getvalue()
        ck("G1 diff 成功", rc == 0, rc)
        ck("G2 表头含两臂", "cur" in out and "rev" in out)
        ck("G3 中位差值 -30", "-30" in out, out[:200])
        ck("G4 提示成本是估算", "估算" in out and "totalFromProvider=false" in out)

    print("H) 旧 jsonl 回退可聚合")
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        with (base / "old.jsonl").open("w", encoding="utf-8") as fh:
            for rr in ROWS:
                fh.write(json.dumps(rr) + "\n")
            fh.write("{bad json\n")
        arms = cmp_mod.load_from_log(base)
        ck("H1 聚合出 old", "old" in arms, sorted(arms))
        ck("H2 坏行被跳过（n=3）", arms.get("old", {}).get("n") == 3,
           arms.get("old", {}).get("n"))

    print("I) run.py 语法可直接执行")
    rc = subprocess.call([sys.executable, "-m", "py_compile", str(RUNPY)])
    ck("I1 py_compile 通过", rc == 0, rc)
    rc = subprocess.call([sys.executable, "-m", "py_compile", str(CMPPY)])
    ck("I2 compare py_compile 通过", rc == 0, rc)

    print("\n%s  %d passed, %d failed" % ("OK" if _failed == 0 else "FAIL", _passed, _failed))
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())