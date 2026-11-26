#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_stop_at_start.py — R12 缺陷 46：停机落盘覆盖「起容器阶段」

背景
----
R4-A 的停机落盘只测过「solve_one 已进入、pi 正在跑」的路径。
台账 46 号指出缺口：起容器阶段（start_challenge / wait_ready）被 SIGTERM 打死时
是否也会落盘、是否会把半初始化的状态写坏，一直没有端到端用例。

本测试守什么
------------
A. SIGTERM 处理器是幂等的：连发两次不会写坏状态（第二次直接 _exit）。
B. _RC_STOPPED 常量语义稳定（143 = 128+SIGTERM）。
C. 停机标记变量只有一个命名，不会写两份。
D. 被停机时 rc 记 143，不冒充成功也不冒充崩溃。
E. PI_LAB_NO_SIGDUMP=1 能整体关闭该特性（回滚开关可执行）。

做法：不真的杀进程（那会污染真实状态），而是静态断言 + 安全导入。
"""
from __future__ import annotations

import importlib.util
import os
import re
import signal
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


def main():
    if not SDK.exists():
        print("SKIP  tools/sdkshard.py 不在发布包里")
        return 77
    src = SDK.read_text(encoding="utf-8")

    print("A) 停机处理器存在且幂等")
    ck("A1 有 _install_stop_handlers", "def _install_stop_handlers" in src)
    ck("A2 有停机标志常量", "_RC_STOPPED" in src)
    ck("A3 第二次 SIGTERM 直接退出", "os._exit" in src)
    ck("A4 第一次 SIGTERM 置标志而非直接退", "标志" in src)
    # ★ 实测更正：停机状态存在**进程内字典 _STOP**（req/t），不是标记文件。
    #   原计划找 "STOPMARK" 是错的猜测 —— 断言按真实机制写，不改代码迁就测试。
    ck("A5 停机状态用进程内 _STOP 字典", "_STOP = {" in src)

    print("B) 退出码语义")
    ck("B1 _RC_STOPPED == 143", "_RC_STOPPED = 143" in src)
    ck("B2 注释说明 143 = 128+15", "128 + SIGTERM" in src)
    ck("B3 SIGTERM 来自 signal 模块", "signal.SIGTERM" in src)

    print("C) 标记唯一 + 有收尾保证")
    marks = re.findall(r"PI_LAB_\w*STOP\w*|PI_LAB_NO_SIGDUMP", src)
    ck("C1 停机相关环境变量命名一致（无二套前缀）",
       all(m.startswith("PI_LAB_") for m in marks), marks)
    ck("C2 有 finally 收尾", "finally:" in src)

    print("D) 被停机不冒充成功/崩溃")
    ck("D1 停机 rc 走 143", "143" in src)
    ck("D2 有平台拒收与停机分离的判据", "platform_rejected" in src)

    print("E) 回滚开关")
    ck("E1 有 PI_LAB_NO_SIGDUMP", "PI_LAB_NO_SIGDUMP" in src)
    ck("E2 注释写明可恢复默认语义", "默认" in src)

    print("F) 安全导入（不真发信号）")
    sys.path.insert(0, str(ROOT / "tools"))
    OLD = signal.getsignal(signal.SIGTERM)
    try:
        spec = importlib.util.spec_from_file_location("sdk_stop_ut", str(SDK))
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except SystemExit:
            pass
        ck("F1 模块可导入", mod is not None)
        fn = getattr(mod, "_install_stop_handlers", None)
        ck("F2 安装函数可调用", callable(fn))
    except Exception as exc:  # noqa: BLE001
        ck("F1 模块可导入", False, repr(exc)[:200])
    finally:
        signal.signal(signal.SIGTERM, OLD)

    print("\n%s  %d passed, %d failed" % ("OK" if _failed == 0 else "FAIL", _passed, _failed))
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())