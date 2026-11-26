#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_fail_next_step.py — #24 「不是 step」类报错必须带下一步

为什么（2026-11-17）：worker 把 fact/goal 的 id 当 step 传、或抄错一位时，
旧文案只有一句「X 不是 step」：agent 无从下手，于是整轮卡在同一句报错上重试。
新文案列最接近的 step id（node() 那套「你是不是指」），没有 step 就告诉它怎么建。

断言（跑出来的行为，不读注释）：
  A. 传一个**存在的 fact id** → rc=3，文案含「不是 step」+「你是不是指」+ 真正的 s001。
  B. 板上还没有 step，传一个存在的 goal id → 文案含「还没有 step」+「fgs step-add」。
  C. 传一个不存在的 id → 仍由 node() 报「不存在」，不被静默吞掉（rc=2，node() 的约定码）。
  D. 正常的 step-claim 不受影响（rc=0）。
  E. #25 的接线在（ModelsPage 渲染 ms/next/health、server 记健康）：静态兜底。
"""
import os
import subprocess
import sys
import tempfile

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
FGS = os.path.join(ROOT, "board", "fgs.py")
UI = os.path.join(ROOT, "work", "ui")

_passed, _failed = 0, 0


def ck(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  ✗ {name}" + (f"   [{detail}]" if detail else ""))


def fgs(lab, *args):
    env = dict(os.environ)
    env["PI_LAB_BOARD_DIR"] = lab
    r = subprocess.run([sys.executable, FGS, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=ROOT, env=env, timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main():
    lab = tempfile.mkdtemp(prefix="failnext-")
    print("A) 存在的 fact id 当 step 传：要说清「你是不是指 s001」")
    fgs(lab, "kickoff", "p1", "--kind", "ctf", "--goal", "g", "--target", "http://x")
    fgs(lab, "step-add", "p1", "第一步")
    fgs(lab, "fact-add", "p1", "占位事实", "--confidence", "unverified")
    rc, out = fgs(lab, "step-claim", "p1", "f001", "--by", "w")
    ck("A1 rc=3", rc == 3, f"rc={rc}")
    ck("A2 含「不是 step」", "不是 step" in out, out[:200])
    ck("A3 含「你是不是指」", "你是不是指" in out, out[:200])
    ck("A4 指向真正的 s001", "s001" in out, out[:200])

    print("B) 板上没有 step：要教它怎么建")
    lab2 = tempfile.mkdtemp(prefix="failnext2-")
    fgs(lab2, "kickoff", "p2", "--kind", "ctf", "--goal", "g", "--target", "http://x")
    rc, out = fgs(lab2, "step-claim", "p2", "g001", "--by", "w")
    ck("B1 rc=3", rc == 3, f"rc={rc}")
    ck("B2 含「还没有 step」", "还没有 step" in out, out[:200])
    ck("B3 含 fgs step-add", "fgs step-add" in out, out[:200])

    print("C) 不存在的 id：仍报「不存在」，不能被静默吞")
    rc, out = fgs(lab, "step-claim", "p1", "zzz9", "--by", "w")
    ck("C1 rc=2", rc == 2, f"rc={rc}")
    ck("C2 含「不存在」", "不存在" in out, out[:200])

    print("D) 正常认领不受影响")
    rc, out = fgs(lab, "step-claim", "p1", "s001", "--by", "w")
    ck("D1 rc=0", rc == 0, f"rc={rc} {out[:160]}")
    ck("D2 文案含 claimed", "claim" in out.lower(), out[:160])

    print("E) #25 接线（静态兜底）")
    page = os.path.join(UI, "web", "src", "pages", "ModelsPage.tsx")
    srv = os.path.join(UI, "server.mjs")
    if os.path.exists(page) and os.path.exists(srv):
        pt = open(page, encoding="utf-8").read()
        st = open(srv, encoding="utf-8").read()
        ck("E1 前端渲染延迟", "testOut.ms" in pt)
        ck("E2 前端渲染下一步", "testOut.next" in pt)
        ck("E3 徽章读健康记录", "p.health" in pt and 'className={"dot "' in pt)
        ck("E4 server 打点成功", "noteModelOk" in st)
        ck("E5 server 打点失败", "noteModelFail" in st)
        ck("E6 /api/models 带回 health", "row.health = health[row.id]" in st)
    else:
        print("  （跳过 UI 静态检查：work/ui 不在发布包里）")

    print(f"\n{'OK' if _failed == 0 else 'FAIL'}  {_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
