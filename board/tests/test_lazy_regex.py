#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_lazy_regex.py — 该轮 该缺陷：导入期 re.compile SIGSEGV 的确定性缓解

背景（某轮复盘 1.1-1.2 的证据链）
--------------------------------
崩溃栈停在 re/_compiler.py:582 _code，调用点是 fgs.py 的**模块级** re.compile；
内核 dmesg 也记录 Capturing crash ... signal: 11。R5~R8 把这误记成「FLAKY/吃时序」。
崩溃是概率性的，所以「重跑绿」从来不等于「没问题」。

本测试守什么
------------
A. 导入期**不再执行任何 C 级编译**（这正是那条崩溃路径）。
B. 惰性对象与 re.Pattern 行为**等价**。
C. 编译失败时**不抛、不崩**，查询返回「不匹配」——进程活着。
D. fgs.py 里不再有模块级 re.compile（防回退）。

诚实边界：这挡的是导入期崩溃；运行期首次编译若崩，仍会死当前线程。
"""
from __future__ import annotations

import importlib.util
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
FGS = ROOT / "board" / "fgs.py"

_passed, _failed = 0, 0


def ck(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print("  x " + name + (("   [" + str(detail)[:300] + "]") if detail else ""))


def load():
    spec = importlib.util.spec_from_file_location("fgs_lazy_ut", str(FGS))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    if not FGS.exists():
        print("SKIP  board/fgs.py 不在发布包里")
        return 77
    m = load()
    LR = m._LazyRegex

    print("A) 导入期不编译（惰性）")
    obj = LR(r"abc(\d+)")
    ck("A1 构造后未编译", obj._rx is None and obj._failed is False, repr(obj))
    ck("A2 repr 显示 lazy", "lazy" in repr(obj), repr(obj))
    obj.search("abc123")
    ck("A3 首次使用后才编译", obj._rx is not None, repr(obj))
    ck("A4 repr 变为 ok", "ok" in repr(obj), repr(obj))

    print("B) 与 re.Pattern 行为等价")
    pat = r"(?P<w>[a-z]+)-(\d+)"
    s = "tag-42 and other-7"
    ref, got = re.compile(pat), LR(pat)
    ck("B1 search 结果一致", ref.search(s) is not None and got.search(s).group(0) == ref.search(s).group(0))
    ck("B2 match 一致（有匹配）", got.match("abc-1").group(0) == ref.match("abc-1").group(0))
    ck("B3 match 一致（无匹配）", got.match("!!!!") is None and ref.match("!!!!") is None)
    ck("B4 findall 一致", got.findall(s) == ref.findall(s))
    ck("B5 finditer 一致", [x.group(0) for x in got.finditer(s)] == [x.group(0) for x in ref.finditer(s)])
    ck("B6 sub 一致", got.sub("#", s) == ref.sub("#", s))
    ck("B7 split 一致", got.split(s) == ref.split(s))
    ck("B8 pattern 一致", got.pattern == ref.pattern)
    ck("B9 flags 一致", got.flags == ref.flags)
    ck("B10 fullmatch 一致", got.fullmatch("abc-1").group(0) == ref.fullmatch("abc-1").group(0))

    print("C) 编译失败不崩（该缺陷的关键性质）")
    bad = LR(r"(unclosed")
    ck("C1 search 返回 None 不抛", bad.search("x") is None)
    ck("C2 已标记 failed", bad._failed is True, repr(bad))
    ck("C3 match 返回 None", bad.match("x") is None)
    ck("C4 findall 返回空列表", bad.findall("x") == [])
    ck("C5 finditer 可迭代且为空", list(bad.finditer("x")) == [])
    ck("C6 sub 原样返回不破坏数据", bad.sub("Y", "xyz") == "xyz")
    ck("C7 split 返回原串", bad.split("xyz") == ["xyz"])
    ck("C8 pattern 仍可取", bad.pattern == r"(unclosed")
    ck("C9 repr 显示 failed", "failed" in repr(bad), repr(bad))
    ck("C10 不重复尝试编译（不反复崩）", bad._rx is None and bad._failed is True)

    print("D) fgs.py 无模块级 re.compile（防回退）")
    src = FGS.read_text(encoding="utf-8")
    mod_level = [ln for ln in src.splitlines()
                 if re.match(r"^_[A-Za-z0-9_]+ *= *re\.compile\(", ln)]
    ck("D1 模块级 re.compile 归零", mod_level == [], mod_level)
    ck("D2 存在 _rx 入口", "def _rx(" in src)
    ck("D3 存在 _LazyRegex", "class _LazyRegex" in src)
    ck("D4 注释写明缺陷编号", "该缺陷" in src)

    print("E) 真实模块正则仍工作")
    ck("E1 NEGATIVE_PAT 命中否定结论", bool(m.NEGATIVE_PAT.search("端口关闭")) is True)
    ck("E2 NEGATIVE_PAT 不误伤正面", bool(m.NEGATIVE_PAT.search("已拿到 flag")) is False)
    ck("E3 _WS_RX 折行成空格", m._WS_RX.sub(" ", "a\nb\tc") == "a b c")
    ck("E4 _CONTROL_RX 去控制符", m._CONTROL_RX.sub("", "a\x00b") == "ab")

    print("\n%s  %d passed, %d failed" % ("OK" if _failed == 0 else "FAIL", _passed, _failed))
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())