#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_policy_cost_thinking.py — R7-A 思考档位分档 / R7-B 单题成本上限

为什么
------
两个策略都由**真实回放数据**定阈值（见 ）：
A. 推理 token 占输出 64.5%（≈$1.66 = 总成本 32%），且 reasoning/out 从首轮 56% 涨到第5轮 85%；
   但 80/99 已解题是**首轮**解出的 → 只对「零资产的重试轮」降档（回放省 12.6%）。
B. 4 道题烧掉 49.9% 成本。cap=$0.45 + 「上一轮零资产」双判据 → 回放省 11.0% 且**丢 0 题**；
   纯看钱会误杀 056（第 4 轮末 $0.4487 < 0.45，第 5 轮才出旗）。

判据
----
A. thinking_for：首轮永远 high；重试有资产 high；重试零资产 medium。
B. cost_cap：按分值线性、有下限；500 分题上限 0.5。
C. stop_by_cost：有资产 → 永不停（056 反例）；无资产且花超 → 停。
D. 接线：队列行 4 段格式、sdkshard 传 --thinking、parallel_solver 过滤超额题。
E. 旁路：PI_SOLVE_COST_CAP=0 关闭。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = Path(os.path.dirname(os.path.dirname(HERE)))
POLICY = ROOT / "tools" / "solve_policy.py"
PAR = ROOT / "tools" / "parallel_solver.py"
SDK = ROOT / "tools" / "sdkshard.py"

_passed, _failed = 0, 0


def ck(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print("  x " + name + (("   [" + str(detail)[:300] + "]") if detail else ""))


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    for p in (POLICY, PAR, SDK):
        if not p.exists():
            print("SKIP  %s 不在发布包里" % p.name)
            return 77
    pol = load(POLICY, "solve_policy_cost_ut")

    print("A) thinking_for")
    tf = pol.thinking_for
    ck("A1 首轮 high", tf(0) == "high", tf(0))
    ck("A2 首轮即便无资产也 high（80/99 首轮解出，不动它）",
       tf(0, False) == "high", tf(0, False))
    ck("A3 重试有资产 → high", tf(2, True) == "high", tf(2, True))
    # ★ 某轮复盘（2026-11-22）：A/B 实测否证了"降档省钱"——medium 既不快又贵 11.1%。
    #   用户要求默认维持 high 或极高，故默认改为 high。
    #   这里断言的是**默认值**；显式设 PI_THINK_RETRY_COLD 仍可降档（见 test_thinking_custom.py）。
    ck("A4 重试零资产 → high（某轮复盘 实测后改默认）", tf(2, False) == "high", tf(2, False))
    ck("A5 首轮之后均为 high（默认不降档）", all(tf(t, False) == "high" for t in (1, 2, 5)))

    print("B) cost_cap")
    cc = pol.cost_cap
    ck("B1 200 分 → 0.45", abs(cc(200) - 0.45) < 1e-9, cc(200))
    ck("B2 300 分 → 0.45", abs(cc(300) - 0.45) < 1e-9, cc(300))
    ck("B3 500 分 → 0.50", abs(cc(500) - 0.5) < 1e-9, cc(500))
    ck("B4 分值缺失/0 → 0.45", abs(cc(0) - 0.45) < 1e-9 and abs(cc(None) - 0.45) < 1e-9)
    ck("B5 非法分值不崩", abs(cc("abc") - 0.45) < 1e-9, cc("abc"))

    print("C) stop_by_cost")
    sb = pol.stop_by_cost
    # 056 反例：花了 0.4487（<0.45）且有资产 → 不许停
    ck("C1 056 反例：$0.4487 但有资产 → 不停", sb(0.4487, 200, True) is False)
    ck("C2 有资产即便花很多也不停（有进展不因钱被砍）", sb(2.0, 200, True) is False)
    ck("C3 零资产且超 $0.45 → 停", sb(0.46, 200, False) is True)
    ck("C4 零资产但没超 → 不停", sb(0.40, 200, False) is False)
    ck("C5 500 分题上限 0.50", sb(0.48, 500, False) is False and sb(0.51, 500, False) is True)
    ck("C6 spent 为 0/None → 不停", sb(0, 200, False) is False and sb(None, 200, False) is False)

    print("D) 接线")
    par = PAR.read_text(encoding="utf-8")
    sdk = SDK.read_text(encoding="utf-8")
    ck("D1 parallel_solver 下发 4 段队列行", ":{think}" in par)
    ck("D2 用同一个 asset 判据算档位", "_policy_thinking(tries, _asset)" in par)
    ck("D3 sdkshard 解析第 4 段", "think_lvl = parts[3]" in sdk)
    ck("D4 sdkshard 真的传 --thinking", '"--thinking", _th' in sdk)
    ck("D5 环境变量优先于队列行（可做 A/B）",
       'os.environ.get("PI_SOLVE_THINKING", "").strip() or (think or "").strip()' in sdk)
    ck("D6 parallel_solver 用 stop_by_cost 过滤", "_policy_stop(_spent" in par)
    ck("D7 过滤发生在切桶之前（否则照样会跑）",
       par.find("_policy_stop(_spent") < par.find("buckets = [todo[i::"))

    print("E) 旁路")
    ck("E1 PI_SOLVE_COST_CAP=0 可关", 'PI_SOLVE_COST_CAP' in par and "_cap > 0" in par)
    ck("E2 注释写明回放依据（省 11%、丢 0 题）", "11.0%" in par and "丢 0 题" in par)
    ck("E3 策略文件注释写明 64.5%/168 等实测值",
       "64.5%" in POLICY.read_text(encoding="utf-8"))

    print("\n%s  %d passed, %d failed" % ("OK" if _failed == 0 else "FAIL", _passed, _failed))
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())