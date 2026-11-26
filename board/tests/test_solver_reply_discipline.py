#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_solver_reply_discipline.py — R8-A 压自我复述 / R8-B 搜索次数上限

为什么
------
实测（§1）：上下文里 **30.7% 是模型自己的正文、27.5% 是思考**
（工具结果只占 29.5%）。模型写一遍进输出，下一轮作为历史再进一次输入 —— **双倍计费**，
这正是"第 3 轮单题成本是首轮 13.4 倍"的主因（R7-01 §1.1）。

同时量出：搜索次数分布 0/1/2/3/4/6，限到 3 只影响 4 道题，且它们用搜索也没解出来。

判据
----
A. 不变量 12/13/14 在（结论一行、不抄 facts、收尾三行）。
B. 不变量 15 在（search<=3 / fetch<=2），且给了离线库优先的说法。
C. **不许**出现"禁搜"或"不要 cd"这类会伤能力的措辞（后者是 R8-03 §3 的真实教训）。
D. lane_spec 里 cd 的说明与 pi 语义一致（每条命令都要 cd）。
E. 原有的"立刻打印 FOUND_FLAG"仍然在。
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
SDK = ROOT / "tools" / "sdkshard.py"

_passed, _failed = 0, 0


def ck(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print("  x " + name + (("   [" + str(detail)[:300] + "]") if detail else ""))


def load():
    spec = importlib.util.spec_from_file_location("sdkshard_reply_ut", str(SDK))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    if not SDK.exists():
        print("SKIP  tools/sdkshard.py 不在发布包里")
        return 77
    sd = load()
    S = getattr(sd, "SOLVER_SYSTEM", "")
    spec_txt = sd.lane_spec(Path("/tmp/scratch-demo"))

    print("A) 压自我复述的三条不变量")
    ck("A1 有不变量 12（结论一行/不复述）", "12." in S and "不复述" in S)
    ck("A2 明写双倍计费", "双倍计费" in S or "付两遍" in S)
    ck("A3 有不变量 13（facts 不抄）", "13." in S and "facts" in S)
    ck("A4 有不变量 14（收尾三行）", "14." in S and "三行" in S)
    ck("A5 收尾三行写了三段名", "已确认" in S and "已证伪" in S and "还差什么" in S)

    print("B) 搜索次数上限")
    ck("B1 有不变量 15（搜索上限）", "15." in S and "最多" in S)
    ck("B2 写了 web_search 3 次", "web_search" in S and "3 次" in S)
    ck("B3 写了 fetch_content 2 次", "fetch_content" in S)
    ck("B4 到上限要换攻击面（不是继续搜）", "换攻击面" in S)
    ck("B5 优先离线库（payload/kb/poc）",
       "payload.py" in S and "kb search" in S and "poc" in S)

    print("C) 不许出现会伤能力的措辞")
    ck("C1 没有禁搜措辞",
       "不要搜索" not in S and "禁用 web_search" not in S and "禁止搜索" not in S)
    ck("C2 没有「不要每条命令都 cd」（R8-03 §3 的真实教训）",
       "不要每条命令" not in spec_txt and "不要每条命令" not in S, spec_txt[:200])
    ck("C3 明写 cd 不持久（pi 每次新 shell）",
       "不持久" in spec_txt or "新 shell" in spec_txt, spec_txt[:300])
    ck("C4 建议 cd 与干活串一条（拆开则白费）", "串" in spec_txt)

    print("D) 原有底线没被砍掉")
    ck("D1 仍然「立刻单独打印 FOUND_FLAG」", "FOUND_FLAG" in S and "立刻" in S)
    ck("D2 仍然「不许猜 flag」", "不许猜" in S or "不猜" in S)
    ck("D3 仍然「不自己提交」", "不自己提交" in S)
    ck("D4 仍然有注入防护", "注入" in S)
    ck("D5 不变量到 15", "15." in S)

    print("E) 措辞可执行（不是空话）")
    ck("E1 12 条给了「说清什么」", "试了什么" in S)
    ck("E2 14 条否掉了「综上所述」", "综上所述" in S)
    ck("E3 13 条说明了为什么（下轮注入）", "注入" in S)

    print("\n%s  %d passed, %d failed" % ("OK" if _failed == 0 else "FAIL", _passed, _failed))
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
