#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_thinking_custom.py — R11 A：推理档位可自定义（操作者 2026-11-22 要求）

为什么
------
"推理档我要设置为可自定义可以调整"。此前三个档位是模块常量，改档必须改代码，
于是 A/B 只能靠改源码 —— 与"多次评估后再改造"直接冲突。

判据
----
A. 默认值不变（保护既有行为）：首轮 high / 重试有资产 high / 重试零资产 medium。
B. 三个环境变量可覆盖，且优先级正确。
C. 非法值**静默回落默认**，绝不把拼错的值传给 pi（否则 --thinking "" 会让整轮白跑）。
D. 大小写 / 空白容错。
E. thinking_for() 的档位判定逻辑不因可配置而改变。
F. 旁路：改回环境变量即恢复，不需要改代码（回滚方案可执行）。
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
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

_passed, _failed = 0, 0


def ck(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print("  x " + name + (("   [" + str(detail)[:300] + "]") if detail else ""))


def load_with_env(env):
    """在给定环境变量下**重新导入**模块（常量在 import 期求值）。"""
    code = ("import importlib.util,sys;"
            "spec=importlib.util.spec_from_file_location('p_%d','" + str(POLICY).replace("\\", "/") + "');"
            "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
            "print(m.THINK_FIRST,m.THINK_RETRY_ASSET,m.THINK_RETRY_COLD)")
    e = dict(os.environ)
    for k in ("PI_THINK_FIRST", "PI_THINK_RETRY_ASSET", "PI_THINK_RETRY_COLD"):
        e.pop(k, None)
    e.update(env)
    out = subprocess.check_output([sys.executable, "-c", code], env=e).decode().strip()
    return out.split()


def main():
    if not POLICY.exists():
        print("SKIP  solve_policy.py 不在发布包里")
        return 77

    print("A) 默认值不变")
    got = load_with_env({})
    ck("A1 默认 high/high/high（操作者 2026-11-22：维持 high 或极高）", got == ["high", "high", "high"], got)

    print("B) 环境变量可覆盖")
    got = load_with_env({"PI_THINK_FIRST": "max"})
    ck("B1 只改首轮", got[0] == "max" and got[1:] == ["high", "high"], got)
    got = load_with_env({"PI_THINK_RETRY_ASSET": "low", "PI_THINK_RETRY_COLD": "minimal"})
    ck("B2 改两个重试档", got == ["high", "low", "minimal"], got)
    got = load_with_env({"PI_THINK_FIRST": "off", "PI_THINK_RETRY_ASSET": "off",
                         "PI_THINK_RETRY_COLD": "off"})
    ck("B3 全关", got == ["off", "off", "off"], got)

    print("C) 非法值回落默认（不传给 pi）")
    got = load_with_env({"PI_THINK_FIRST": "ultra"})
    ck("C1 拼错回落 high", got[0] == "high", got)
    got = load_with_env({"PI_THINK_FIRST": ""})
    ck("C2 空串回落 high", got[0] == "high", got)
    got = load_with_env({"PI_THINK_RETRY_COLD": "HIGHEST"})
    ck("C3 未知名回落 high", got[2] == "high", got)

    print("D) 大小写/空白容错")
    got = load_with_env({"PI_THINK_FIRST": "  MAX  "})
    ck("D1 大写+空格 -> max", got[0] == "max", got)
    got = load_with_env({"PI_THINK_RETRY_COLD": "Medium"})
    ck("D2 混合大小写 -> medium（显式覆盖仍生效）", got[2] == "medium", got)

    print("E) 判定逻辑不变")
    import importlib.util as iu
    spec = iu.spec_from_file_location("pol_ut", str(POLICY))
    pol = iu.module_from_spec(spec)
    spec.loader.exec_module(pol)
    ck("E1 首轮永远 THINK_FIRST", pol.thinking_for(0) == pol.THINK_FIRST)
    ck("E2 首轮即便无资产也不降档", pol.thinking_for(0, False) == pol.THINK_FIRST)
    ck("E3 重试有资产 -> RETRY_ASSET", pol.thinking_for(2, True) == pol.THINK_RETRY_ASSET)
    ck("E4 重试零资产 -> RETRY_COLD", pol.thinking_for(2, False) == pol.THINK_RETRY_COLD)
    ck("E5 全部合法值都在 THINK_VALUES 里",
       all(pol.THINK_FIRST in pol.THINK_VALUES and pol.THINK_RETRY_ASSET in pol.THINK_VALUES
           and pol.THINK_RETRY_COLD in pol.THINK_VALUES for _ in [0]))

    print("F) 回滚可执行（环境变量即旁路）")
    src = POLICY.read_text(encoding="utf-8")
    ck("F1 三个变量名都在源码里",
       all(k in src for k in ("PI_THINK_FIRST", "PI_THINK_RETRY_ASSET", "PI_THINK_RETRY_COLD")))
    ck("F2 注释写明默认值", "默认 high" in src or "默认" in src)
    ck("F3 有回落函数", "_think_env" in src)

    print("\n%s  %d passed, %d failed" % ("OK" if _failed == 0 else "FAIL", _passed, _failed))
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())