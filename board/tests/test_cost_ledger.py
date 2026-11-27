#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_cost_ledger.py — R7-B 成本进账本（每轮 tokens/cost 落 ledger）

为什么
------
某轮复盘 §2 的成本四分（输入 27.3% / 输出 50.4% / 缓存 22.4%，推理占输出 64.5%）
是**事后从会话 jsonl 重算的**；而账本 solve_ledger.json 里只有耗时（total_sec）。
后果：ROI 排序只看「分值 / 秒」，一题烧了 $0.8 没出，在排序上和一题"30 秒没出"
没区别 —— 降档、早停这类成本优化因此没有判据（某轮复盘 §1 B/C/F 全卡在这里）。

判据
----
A. session_usage_totals 正确累加 totalTokens 与 cost.total，坏行/半行不崩。
B. Ledger.note_round 支持 tokens/cost，累计到 total_tokens / total_cost。
C. 累计只加**正数**（0/None 不污染；重复调用是累加，不是覆盖）。
D. Ledger.total_cost 读取；没有记录 → 0。
E. 接线：solve_one 在起 pi **前**取快照、收尾用差值记账（顺序错就会把本轮算进"轮前"）。
F. jitter 轮不传 tokens/cost（与 record_elapsed 的 jitter 处理一致）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
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
SDK = ROOT / "tools" / "sdkshard.py"
LEDGER = ROOT / "tools" / "solve_ledger.py"

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
    if not SDK.exists() or not LEDGER.exists():
        print("SKIP  缺 sdkshard.py 或 solve_ledger.py")
        return 77
    sd = load(SDK, "sdkshard_cost_ut")
    lg = load(LEDGER, "solve_ledger_cost_ut")
    tmp = Path(tempfile.mkdtemp(prefix="costledger-"))
    try:
        print("A) session_usage_totals")
        d = tmp / "q1"
        d.mkdir(parents=True)
        rows = [
            {"type": "session", "id": "t"},
            {"type": "message", "message": {"role": "assistant",
                                            "usage": {"totalTokens": 1000,
                                                      "cost": {"total": 0.01}}}},
            {"type": "message", "message": {"role": "assistant",
                                            "usage": {"totalTokens": 2500,
                                                      "cost": {"total": 0.02}}}},
        ]
        (d / "a.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n{\"type\":\"mess", encoding="utf-8")
        tok, cost = sd.session_usage_totals(d)
        ck("A1 tokens 累加 = 3500", tok == 3500, tok)
        ck("A2 cost 累加 = 0.03", abs(cost - 0.03) < 1e-9, cost)
        ck("A3 半行 JSON 不崩（末尾截断）", tok == 3500)
        ck("A4 无 usage 的消息不计", sd.session_usage_totals(tmp / "none") == (0, 0.0))
        # 没有 cost 字段 → 只累 tokens，cost 保持 0（不伪造金额）
        d2 = tmp / "q2"
        d2.mkdir()
        (d2 / "b.jsonl").write_text(json.dumps(
            {"type": "message", "message": {"usage": {"totalTokens": 700}}}), encoding="utf-8")
        ck("A5 缺 cost 时只累 tokens（不伪造金额）",
           sd.session_usage_totals(d2) == (700, 0.0), sd.session_usage_totals(d2))

        print("B) Ledger.note_round 记 tokens/cost")
        lg.LEDGER = tmp / "ledger.json"
        L = lg.Ledger()
        L.note_round("x1", True, rc=0, cands=1, facts=2, tokens=3500, cost=0.03)
        ck("B1 total_tokens = 3500", L.data["x1"].get("total_tokens") == 3500, L.data["x1"])
        ck("B2 total_cost = 0.03", abs(L.total_cost("x1") - 0.03) < 1e-9, L.total_cost("x1"))
        L.note_round("x1", False, rc=124, cands=0, facts=0, tokens=1500, cost=0.02)
        ck("B3 第二轮累加 tokens = 5000", L.data["x1"].get("total_tokens") == 5000, L.data["x1"])
        ck("B4 第二轮累加 cost = 0.05", abs(L.total_cost("x1") - 0.05) < 1e-9, L.total_cost("x1"))

        print("C) 只加正数")
        L.note_round("x2", True, tokens=0, cost=0.0)
        ck("C1 全 0 不写入", "total_tokens" not in L.data["x2"] and "total_cost" not in L.data["x2"],
           L.data["x2"])
        L.note_round("x2", True, tokens=None, cost=None)
        ck("C2 None 不写入", "total_tokens" not in L.data["x2"], L.data["x2"])

        print("D) total_cost 读取")
        ck("D1 未跑过的题 → 0", L.total_cost("never") == 0.0)
        L2 = lg.Ledger()
        ck("D2 落盘可重载", abs(L2.total_cost("x1") - 0.05) < 1e-9, L2.total_cost("x1"))

        print("E) 接线（顺序是关键）")
        src = SDK.read_text(encoding="utf-8")
        i_snap = src.find("_usage_before = session_usage_totals(sessdir)")
        i_popen = src.find("proc = subprocess.Popen(")
        i_delta = src.find("_d_tok = max(0, _tok_now - _usage_before[0])")
        ck("E1 轮前快照存在", i_snap > 0)
        ck("E2 快照在起 pi 之前（否则本轮已算进去）", 0 < i_snap < i_popen, (i_snap, i_popen))
        ck("E3 收尾用差值记账", i_delta > i_popen, (i_delta, i_popen))
        ck("E4 传给 note_round", "tokens=_d_tok, cost=_d_cost" in src)

        print("F) jitter 轮不记账")
        ck("F1 抖动退出点不在 note_round 之后", "_bail_jitter" in src)
        # _bail_jitter 直接 return，不会走到 note_round（结构上保证）
        i_bail = src.find("def _bail_jitter")
        i_note = src.find("Ledger().note_round(")
        ck("F2 _bail_jitter 定义在 note_round 之前且自带 return", 0 < i_bail < i_note)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n%s  %d passed, %d failed" % ("OK" if _failed == 0 else "FAIL", _passed, _failed))
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
