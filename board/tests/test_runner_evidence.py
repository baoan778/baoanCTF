#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_runner_evidence.py -- 该轮 回归证据不许被清理吃掉

为什么
------
某轮复盘 sec3 记了一次真事故：清 /tmp 时用 `rm -rf test*`，把回归自身的证据
（test_audit.tsv / test_full_*.log）一起删了。结论还在（job 输出里有），
但逐文件审计行没了，无法再复核。

这是"同一类错第二次"：某轮复盘 是「归档与删除写同一条命令」，这次是「通配符吃掉证据」。
两次的共同根因 = **删除前没有确认命中范围**。

判据
----
A. 证据落在独立目录 /tmp/_evidence，不再与测试 mkdtemp 前缀撞车。
B. 开跑前的清理是白名单式（只删已知测试前缀），不是宽通配。
C. 清理显式排除证据目录。
D. 清理会打印前后条目数（可核对删了多少）。
E. 提供逃生门 PI_TEST_KEEP_TMP=1。
F. 报告里的"全文日志"路径指向 EVID，不是 /tmp 根。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = Path(os.path.dirname(os.path.dirname(HERE)))
RUNNER = ROOT / "tools" / "run_all_tests.sh"

_passed, _failed = 0, 0


def ck(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print("  x " + name + (("   [" + str(detail)[:300] + "]") if detail else ""))


def main():
    if not RUNNER.exists():
        print("SKIP  tools/run_all_tests.sh 不在发布包里")
        return 77
    s = RUNNER.read_text(encoding="utf-8")

    print("A) 证据独立目录")
    ck("A1 定义 EVID", 'EVID="/tmp/_evidence"' in s)
    ck("A2 审计表写到 EVID", '"$EVID"/test_audit.tsv' in s)
    ck("A3 全文日志写到 EVID", 'full="$EVID/test_full_' in s)
    ck("A4 不再有裸 /tmp/test_audit.tsv", "/tmp/test_audit.tsv" not in s)

    print("B) 清理是白名单式")
    ck("B1 用 find -name 精确前缀", "find /tmp -maxdepth 1" in s)
    ck("B2 有 -maxdepth 1（不递归误伤）", "-maxdepth 1" in s)
    # 只检查**可执行的清理行**（跳过注释与 __pycache__ 清理那两处已知合法用法）
    _exe = [ln for ln in s.splitlines()
            if "rm -rf" in ln and not ln.lstrip().startswith("#")
            and "__pycache__" not in ln]
    ck("B3 没有宽通配 rm -rf test*（只看可执行行）",
       not any(re.search(r"rm\s+-\w*f\s+/tmp/test\*", ln) for ln in _exe), _exe[:3])

    print("C) 清理不碰证据目录")
    names = " ".join(re.findall(r'-name "([^"]+)"', s))
    ck("C1 -name 列表里没有 _evidence", "_evidence" not in names, names)
    ck("C2 清理块在 EVID 创建之后",
       s.find('mkdir -p "$EVID"') < s.find("find /tmp -maxdepth 1"))

    print("D) 可核对")
    ck("D1 打印清理前后条目数", "_before" in s and "_after" in s)
    ck("D2 输出点名证据目录未动", "证据目录 $EVID 未动" in s)

    print("E) 逃生门")
    ck("E1 PI_TEST_KEEP_TMP 可跳过清理", "PI_TEST_KEEP_TMP" in s)
    ck("E2 判断写成 != \"1\"", '!= "1"' in s)

    print("F) 报告路径")
    ck("F1 失败提示指向 $EVID", "$EVID/test_full_" in s)
    ck("F2 不再指向 /tmp 根", "/tmp/test_full_" not in s)

    print("G) 该轮 FLAKY 升级 / 该轮 崩溃观测")
    ck("G1 有 flaky_streak 状态文件", "flaky_streak.tsv" in s)
    ck("G2 连续两次升 FAIL", "_cnt" in s and "ge 2" in s and "FAIL" in s)
    ck("G3 升级后把 flaky 计入 fail", "fail=$((fail+_escalate))" in s)
    ck("G4 有 crash_watch 观测", "crash_watch.log" in s)
    ck("G5 抓的是 signal 11（段错误）", "signal: 11" in s)
    ck("G6 dmesg 不可用时不报错", "command -v dmesg" in s)

    print("".join(["\n", "OK" if _failed == 0 else "FAIL",
                   "  %d passed, %d failed" % (_passed, _failed)]))
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
