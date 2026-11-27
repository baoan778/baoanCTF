#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_supervisor_heartbeat.py — 该轮 该缺陷：守护死了要有人管

事故（某轮复盘 3.1b）：supervisor.log 14:13:20 后 **118.5 分钟零 tick**，
全场 **27.1 分钟零活动**，靠人肉重启。根因不是 supervise 逻辑错，
而是**没有任何东西检查 supervise 自己还活着**。

本测试守什么
------------
A. supervise 每跳写心跳（beat 在 tick 开头，覆盖所有 return 分支）。
B. 心跳新鲜 = grace（给观察窗口），心跳过期 + 无进程 = 死亡。
C. 有 supervise 进程但心跳停滞 = 卡顿，**不重启**（可能是长任务）。
D. 有停止标记时 watchdog 什么都不做（尊重停机）。
E. 阈值可通过 PI_SUP_STALE_S 调整，默认 90s = 3 tick。
F. 看门狗只拉起自己，不碰子进程（边界）。
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import time
from pathlib import Path

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = Path(os.path.dirname(os.path.dirname(HERE)))
WD = ROOT / "tools" / "watchdog.py"
SUP = ROOT / "tools" / "supervise.py"

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


def main():
    for p in (WD, SUP):
        if not p.exists():
            print("SKIP  %s 不在发布包里" % p.name)
            return 77

    sup_src = SUP.read_text(encoding="utf-8")
    wd_src = WD.read_text(encoding="utf-8")

    print("A) supervise 写心跳")
    ck("A1 有 HEARTBEAT 常量", "HEARTBEAT =" in sup_src)
    ck("A2 有 beat 函数", "def beat(" in sup_src)
    ck("A3 beat 在 tick 开头被调用", "    beat()" in sup_src)
    # beat 必须在第一行之后、任何 return 之前
    _i_tick = sup_src.find("def tick(")
    _i_beat = sup_src.find("    beat()", _i_tick)
    _i_first_ret = sup_src.find("return ", _i_tick)
    ck("A4 beat 早于 tick 内第一个 return", 0 < _i_beat < _i_first_ret, (_i_beat, _i_first_ret))
    ck("A5 beat 失败不抛（吞 OSError）", "except OSError:" in sup_src)
    ck("A6 有 heartbeat_age 读取", "def heartbeat_age(" in sup_src)

    print("B) watchdog 判据")
    ck("B1 心跳过期才判死", "_stale_s()" in wd_src or "stale" in wd_src)
    ck("B2 有 grace 窗口", "grace" in wd_src)
    ck("B3 默认阈值 90s（=3 tick）", "90.0" in wd_src)
    ck("B4 可被 PI_SUP_STALE_S 覆盖", "PI_SUP_STALE_S" in wd_src)
    ck("B5 用 procscan 识别进程（不靠子串）", "procscan" in wd_src)

    print("C) 卡顿不重启")
    ck("C1 有进程+心跳停 → 判定卡顿", "卡顿" in wd_src)
    ck("C2 卡顿时不 kill", "kill" not in wd_src.lower() or "不杀" in wd_src)

    print("D) 尊重停机")
    ck("D1 有 STOP_MARK", "STOP_MARK" in wd_src)
    ck("D2 命中停止标记直接返回", 'return "stopped"' in wd_src)

    print("E) 阈值可调")
    wdm = load(WD, "wd_ut")
    ck("E1 默认 90", abs(wdm._stale_s() - 90.0) < 1e-9, wdm._stale_s())
    _old = os.environ.get("PI_SUP_STALE_S")
    try:
        os.environ["PI_SUP_STALE_S"] = "120"
        ck("E2 覆盖生效", abs(wdm._stale_s() - 120.0) < 1e-9, wdm._stale_s())
        os.environ["PI_SUP_STALE_S"] = "abc"
        ck("E3 非法值回落 90", abs(wdm._stale_s() - 90.0) < 1e-9, wdm._stale_s())
    finally:
        if _old is None:
            os.environ.pop("PI_SUP_STALE_S", None)
        else:
            os.environ["PI_SUP_STALE_S"] = _old

    print("F) 边界：只拉起 supervise，不碰子进程")
    ck("F1 只 spawn supervise.py", "supervise.py" in wd_src)
    # 只看**代码行**（去掉注释与 docstring），否则注释里提到 sdkshard 就会误报。
    _in_doc = False
    _code_lines = []
    for ln in wd_src.splitlines():
        _q = ln.count(chr(34) * 3)
        if _q:
            _in_doc = not _in_doc
            # 三引号行本身也可能是注释性质（docstring 首/尾），整行跳过
            continue
        if _in_doc or not ln.strip() or ln.lstrip().startswith("#"):
            continue
        _code_lines.append(ln)
    _code = "\n".join(_code_lines)
    ck("F2 代码里不 spawn sdkshard", "sdkshard" not in _code,
       [ln for ln in _code_lines if "sdkshard" in ln][:2])
    ck("F3 start_new_session（脱离本进程组）", "start_new_session" in wd_src)
    ck("F4 dry-run 不拉起", "dry_run" in wd_src)

    print("G) heartbeat_age 语义")
    with tempfile.TemporaryDirectory() as td:
        wdm.HEARTBEAT = Path(td) / ".hb"
        ck("G1 文件不存在 → inf", wdm.heartbeat_age() == float("inf"))
        wdm.HEARTBEAT.write_text("%d now" % int(time.time()), encoding="utf-8")
        ck("G2 新鲜 → 小于阈值", wdm.heartbeat_age() < 5, wdm.heartbeat_age())
        wdm.HEARTBEAT.write_text("garbage", encoding="utf-8")
        ck("G3 坏内容 → inf（不抛）", wdm.heartbeat_age() == float("inf"))

    print("\n%s  %d passed, %d failed" % ("OK" if _failed == 0 else "FAIL", _passed, _failed))
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())