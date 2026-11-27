#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_solve_budget.py — R3-A 进展感知预算（有进展才加时）

为什么（2026-11-20 实测 历史会话 回放）
-------------------------------------
旧策略：第 N 次尝试预算 = base × 1.5^min(N,3)（medium base 300 → 675 / 640）。
可 27 个超时轮**全部零候选零事实**，它们只是「再来一遍同样会超时」，
下一轮却因为 attempts+1 拿到更大的预算 —— 越没进展喂得越贵。
回放证明：把升级条件改成「上一轮有进展（rc==0 / 有候选 / 有事实）」后，
**26900 分一分不丢**，墙钟 24281s → 23261s、省 $0.52。
反例也验过：按「仅候选」升级会丢 029/088 两个满分题，所以判据必须是三者取或。

断言分五组
  A. budget_for 的新旧两种口径（lvl=None 必须与旧公式逐值相等 —— 三个调用方靠它）
  B. Ledger.note_round / level / rounds 的档位机（首轮 → 1；有进展 +1；无进展回 0）
  C. 接线：parallel_solver 只在有轮次记录时传 lvl；sdkshard 收尾按三判据记账
  D. 文档/回滚不漂移（README 与 某轮复盘 里没有留下会误导的旧口径）
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
POLICY = ROOT / "tools" / "solve_policy.py"
LEDGER = ROOT / "tools" / "solve_ledger.py"
PAR = ROOT / "tools" / "parallel_solver.py"
SDK = ROOT / "tools" / "sdkshard.py"

_passed, _failed = 0, 0


def ck(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  ✗ {name}" + (f"   [{detail}]" if detail else ""))


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    for p in (POLICY, LEDGER, PAR, SDK):
        if not p.exists():
            print(f"SKIP  {p.name} 不在发布包里")
            return 77

    policy = load(POLICY, "solve_policy_ut")

    print("A) budget_for 的两种口径")
    bf = policy.budget_for
    # A1–A5：lvl=None 必须与旧公式逐值相等（bench/live.py、bench/todo.py 靠它）
    ck("A1 lvl=None 首轮 = base", bf("medium", 0) == 300, bf("medium", 0))
    ck("A2 lvl=None 第2轮 = 450（旧口径）", bf("medium", 1) == 450, bf("medium", 1))
    # ★ 口径订正（某轮复盘 记录）：某轮复盘 的验收判据写成「medium,2 == 675」是笔误 ——
    #   旧公式也带 cap（medium 封顶 640），所以 300×1.5² = 675 被截成 640。
    #   这里按**真实旧值**断言，否则测试会把一个从未存在过的行为当成基线。
    ck("A3 lvl=None 第3轮 = 640（旧口径，含 cap）", bf("medium", 2) == 640, bf("medium", 2))
    # ★ R4-C（2026-11-21）：第 4 轮起硬封顶 180s —— “封顶值”只在 tries<3 时成立，
    #   所以这里用 tries=2 验旧公式，tries>=3 的封顶另由 A20–A24 验。
    ck("A4 lvl=None cap hard=960（tries=2，未进重试封顶档）", bf("hard", 2) == 960, bf("hard", 2))
    ck("A5 lvl=None cap 其它=640（tries=2）", bf("medium", 2) == 640, bf("medium", 2))
    # A6–A10：给 lvl 时按档位算
    ck("A6 lvl=0 → base（没有进展不许加时）", bf("medium", 2, 0) == 300, bf("medium", 2, 0))
    ck("A7 lvl=1 → 450", bf("medium", 1, 1) == 450, bf("medium", 1, 1))
    ck("A8 lvl=2 → 640（300×2.25 被 cap 截断，与旧口径同值）", bf("medium", 2, 2) == 640, bf("medium", 2, 2))
    ck("A9 lvl=3 → 640（medium 封顶）", bf("medium", 2, 3) == 640, bf("medium", 2, 3))
    ck("A10 lvl=9 → 640（档位也要封顶）", bf("medium", 2, 9) == 640, bf("medium", 2, 9))
    ck("A11 hard 给 lvl 也封 960", bf("hard", 2, 5) == 960, bf("hard", 2, 5))
    ck("A12 首轮（tries=0）即便给了 lvl 也不加时", bf("medium", 0, 3) == 300, bf("medium", 0, 3))
    ck("A13 未知难度退回默认 150", bf("weird", 0, 0) == 150, bf("weird", 0, 0))
    ck("A14 负数 lvl 当 0 处理", bf("medium", 2, -3) == 300, bf("medium", 2, -3))
    # A15：旧行为完全不变的关键回归 —— 历史会话 回放靠这条不让 bench 漂移
    same = all(
        bf(d, t) == bf(d, t, None) for d in ("easy", "medium", "hard", "weird")
        for t in range(0, 6)
    )
    ck("A15 lvl 缺省与显式 None 完全一致", same)
    # A16–A19：回放里最关键的四个真值（就是 某轮复盘 §7 的验收判据）
    ck("A16 medium tries=0 lvl=0 = 300", bf("medium", 0, 0) == 300)
    ck("A17 medium tries=1 lvl=1 = 450", bf("medium", 1, 1) == 450)
    ck("A18 medium tries=2 lvl=0 = 300（旧公式给 640）", bf("medium", 2, 0) == 300)
    ck("A19 medium tries=2 lvl=3 = 640", bf("medium", 2, 3) == 640)
    # ★ R4-C：第 4 轮（tries=3）起一律 180s —— 不看难度、不看 lvl（最后一道天花板）。
    ck("A20 R4-C medium tries=3 → 180", bf("medium", 3) == 180, bf("medium", 3))
    ck("A21 R4-C hard tries=3 → 180", bf("hard", 3) == 180, bf("hard", 3))
    ck("A22 R4-C easy tries=9 → 180", bf("easy", 9) == 180, bf("easy", 9))
    ck("A23 R4-C tries=2 不封顶（medium=640）", bf("medium", 2) == 640, bf("medium", 2))
    ck("A24 R4-C 封顶压过 lvl（hard tries=3 lvl=3 → 180）", bf("hard", 3, 3) == 180, bf("hard", 3, 3))

    print("B) 账本档位机")
    tmp = Path(tempfile.mkdtemp(prefix="budget-"))
    old_ledger = None
    try:
        lg = load(LEDGER, "solve_ledger_ut")
        old_ledger = lg.LEDGER
        lg.LEDGER = tmp / "ledger.json"
        L = lg.Ledger()
        ck("B0 新题 level=0 / rounds=0", L.level("x1") == 0 and L.rounds("x1") == 0)
        # 第一枪跑完：不管有没有进展，档位固定 1（下一轮按「第 1 档」给基础加时）
        L.note_round("x1", False)
        ck("B1 首轮无进展也置 1（下一轮给基础加时，不是原价重跑）", L.level("x1") == 1, L.level("x1"))
        ck("B2 轮次计数 +1", L.rounds("x1") == 1, L.rounds("x1"))
        L.note_round("x1", False)
        ck("B3 再一轮无进展 → 回 0（超时轮不再涨价）", L.level("x1") == 0, L.level("x1"))
        ck("B4 轮次继续累计", L.rounds("x1") == 2, L.rounds("x1"))
        L.note_round("x1", True)
        ck("B5 有进展 → 升到 1", L.level("x1") == 1, L.level("x1"))
        L.note_round("x1", True)
        ck("B6 连续有进展 → 2", L.level("x1") == 2, L.level("x1"))
        L.note_round("x1", True)
        L.note_round("x1", True)
        ck("B7 档位封顶在 3", L.level("x1") == 3, L.level("x1"))
        L.note_round("x1", False)
        ck("B8 一旦无进展立刻打回 0（不保留历史惯性）", L.level("x1") == 0, L.level("x1"))
        # 落盘/重载
        L2 = lg.Ledger()
        ck("B9 档位与轮次能落盘重载", L2.level("x1") == 0 and L2.rounds("x1") == 7,
           f"lvl={L2.level('x1')} rounds={L2.rounds('x1')}")
        # 与既有字段互不干扰
        L2.record("x1", False)
        ck("B10 record() 不改档位/轮次", L2.level("x1") == 0 and L2.rounds("x1") == 7)
        ck("B11 未跑过的题取档位不炸", L2.level("never") == 0 and L2.rounds("never") == 0)
        raw = json.loads((tmp / "ledger.json").read_text(encoding="utf-8"))
        ck("B12 落盘结构里有 lvl/rounds", "lvl" in raw["x1"] and "rounds" in raw["x1"], str(raw["x1"]))
        ck("B13 记了 last_progress 供事后复盘", raw["x1"].get("last_progress") == "no", str(raw["x1"]))
    finally:
        if old_ledger is not None:
            pass
        shutil.rmtree(tmp, ignore_errors=True)

    print("C) 接线（谁能改预算，就这一处）")
    psrc = PAR.read_text(encoding="utf-8")
    ssrc = SDK.read_text(encoding="utf-8")
    ck("C1 parallel_solver 问的是 solve_policy", "_policy_budget(" in psrc)
    ck("C2 传了 lvl", "_policy_budget(c.difficulty or \"\", tries, lvl)" in psrc, psrc[psrc.find("_policy_budget(c"):][:90])
    ck("C3 首次尝试不传 lvl（保持老口径）", "rounds(c.unique_code) > 0" in psrc)
    ck("C4 读档位用的是 Ledger.level", "_lg.level(" in psrc)
    ck("C5 sdkshard 收尾记账", "note_round(" in ssrc and "bool(rc == 0 or cands or _facts)" in ssrc)
    ck("C6 判据是三者取或（rc==0 / 候选 / 事实）",
       "rc == 0 or cands or _facts" in ssrc, "not found")
    ck("C7 记账在异常兜底里（不许因记账失败丢结果）",
       "档位记账失败" in ssrc)
    ck("C8 日志能看到档位（事后可复盘）", "下一轮档位" in ssrc)

    print("E) R4-B pro 门控 / R4-C 封顶边界")
    af = policy.asset_for
    mf = policy.model_for
    ck("E1 未跑过视为有资产（保持老行为）", af(None) is True)
    ck("E2 上一轮 rc=0 → 有资产", af(0) is True)
    ck("E3 超时但有候选 → 有资产", af(124, 1) is True)
    ck("E4 超时零候选零事实 → 无资产", af(124, 0, 0) is False)
    ck("E5 无资产 + tries>=2 → flash", mf(2, False) == "deepseek-flash")
    ck("E6 有资产 + tries>=2 → pro", mf(2, True) == "deepseek-v4-pro")
    ck("E7 tries<2 永远 flash", mf(1, True) == "deepseek-flash")
    ck("E8 retry_cap 边界：tries=2 None / tries=3 180",
       policy.retry_cap(2) is None and policy.retry_cap(3) == 180)

    print("D4) 连续 N 轮零资产早停（2026-11-23 用户拍板）")
    # 事故/依据：k>=4 的 6 个轮全部零产出却各拿 596~929s（合计 4400s）；
    # 而 k>=4 唯一命中（056 k=5，出旗）那一轮是**有资产**的，不在这条判据射程内。
    import os as _os
    ck("D4-1 阈值默认 3", policy.ZERO_STREAK_STOP == 3, policy.ZERO_STREAK_STOP)
    ck("D4-2 0/1/2 轮不停", [policy.stop_by_zero_rounds(n) for n in (0, 1, 2)] == [False]*3)
    ck("D4-3 第 3 轮起停", policy.stop_by_zero_rounds(3) and policy.stop_by_zero_rounds(9))
    ck("D4-4 非法输入不抛且不停", policy.stop_by_zero_rounds(None) is False)

    # 账本：只涨不清零，真拿到资产才归零
    import tempfile as _tf
    import pathlib as _pl
    _lm = load(LEDGER, "solve_ledger_d4_ut")
    if hasattr(_lm, "LEDGER"):
        _old = _lm.LEDGER
        _d = _pl.Path(_tf.mkdtemp()) / "ledger.json"
        _lm.LEDGER = _d
        try:
            L = _lm.Ledger()
            L.note_round("d4a", False, rc=124, cands=0, facts=0)
            L.note_round("d4a", False, rc=124, cands=0, facts=0)
            L.note_round("d4a", False, rc=124, cands=0, facts=0)
            ck("D4-5 三轮零资产 → streak=3 且触发", L.zero_streak("d4a") == 3
               and policy.stop_by_zero_rounds(L.zero_streak("d4a")))
            L.note_round("d4b", False, rc=124, cands=0, facts=5)
            ck("D4-6 facts 单独不算资产（与 asset_for 同源）", L.zero_streak("d4b") == 1)
            # ★★ 2026-11-23 实测修正：只按 rc/候选 会**误杀 056**
            #   （它 4 轮全 124、facts 只 1 条，但 5 轮共用会话累积上下文，第 5 轮 22s 出旗）。
            #   所以"上下文还在长"必须算资产。下面用真实量级：056 每轮 +92K~218K 字节。
            L.note_round("d4ctx", False, rc=124, cands=0, facts=0, ctx_growth=92495)
            L.note_round("d4ctx", False, rc=124, cands=0, facts=0, ctx_growth=182676)
            ck("D4-14 上下文增长算资产（056 型题不许误杀）",
               L.zero_streak("d4ctx") == 0, L.zero_streak("d4ctx"))
            L.note_round("d4idle", False, rc=1, cands=0, facts=0, ctx_growth=200)
            L.note_round("d4idle", False, rc=1, cands=0, facts=0, ctx_growth=300)
            L.note_round("d4idle", False, rc=1, cands=0, facts=0, ctx_growth=250)
            ck("D4-15 真空转（上下文不涨）仍会被停",
               L.zero_streak("d4idle") == 3
               and policy.stop_by_zero_rounds(L.zero_streak("d4idle")))
            L.note_round("d4mix", False, rc=1, cands=0, facts=0, ctx_growth=100)
            L.note_round("d4mix", False, rc=1, cands=0, facts=0, ctx_growth=100)
            L.note_round("d4mix", False, rc=1, cands=0, facts=0, ctx_growth=50000)
            ck("D4-16 中途开始干活 → 计数清零", L.zero_streak("d4mix") == 0)
            L.note_round("d4c", False, rc=124, cands=0, facts=0)
            L.note_round("d4c", True, rc=0, cands=1, facts=0)
            ck("D4-7 有资产 → 清零（056 型题不许误杀）", L.zero_streak("d4c") == 0)
            ck("D4-8 没跑过的题 streak=0", L.zero_streak("never-run") == 0)
        finally:
            _lm.LEDGER = _old

    # 接线：调度器必须**调用**它，且与成本上限是"或"关系
    src_ps = (ROOT / "tools" / "parallel_solver.py").read_text(encoding="utf-8")
    ck("D4-9 调度器接了 stop_by_zero_rounds", "stop_by_zero_rounds as _policy_zero" in src_ps)
    ck("D4-10 调度器调用它", "_policy_zero(_lg.zero_streak(c))" in src_ps)
    ck("D4-11 早停会打印原因（可归因）", 'print(f"[parallel] 早停 {_q}：{_why}"' in src_ps)
    ck("D4-12 早停后的 todo 过滤仍在", "todo = [c for c in todo if c not in {d[0] for d in _dropped}]" in src_ps)
    ck("D4-13 可用环境变量关掉（A/B）", "PI_SOLVE_ZERO_STREAK" in (ROOT / "tools" / "solve_policy.py").read_text(encoding="utf-8"))

    # 原 D) 段断言的是内部复盘的某一节；那份复盘不随仓库分发，断言已无意义，删除。

    print(f"\n{'OK' if _failed == 0 else 'FAIL'}  {_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
