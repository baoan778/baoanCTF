#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_goal_discipline.py — #29 目标与约束的书写规范（软提醒，不拒收）

为什么（2026-11-17）：目标是一张板子的**验收判据**。写「尽量解出来」时，
fgs next / fgs solved / 子代理任务书全都挂在这句含糊话上 —— 它收不了尾，
而写目标的人只觉得「先写着，回头再改」。ARTEX 的做法是用模板逼着写
「目标=可判定验收 / 约束写数字 / 范围写小」，baoan 落在 kickoff 与 goal 两个入口。

判据（跑出来的行为）：
  A. goal_advice 四条：无可判定验收词 / 模糊词 / 无数字 / 全站类没最小样本。
  B. 好目标零提醒（不制造噪音 —— 全是提醒等于没有提醒）。
  C. 两个入口都打：kickoff 与 goal 都打印提醒，且**不拒收**（rc=0）。
  D. 静态兜底：常量、两处调用、文档 §4.6r。
"""
import importlib.util
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

_passed, _failed = 0, 0


def ck(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print("  x " + name + ("   [" + str(detail)[:300] + "]" if detail else ""))


def load_fgs():
    spec = importlib.util.spec_from_file_location("fgs_goal_mod", FGS)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def fgs(lab, *args):
    env = dict(os.environ)
    env["PI_LAB_BOARD_DIR"] = lab
    env["PI_FGS_TOOFAST_SEC"] = "0"
    r = subprocess.run([sys.executable, FGS, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=ROOT, env=env, timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main():
    m = load_fgs()

    # ── A. 四条检查
    a = m.goal_advice("")
    ck("A1 空目标提醒", len(a) >= 1 and "目标为空" in a[0], a)
    a = m.goal_advice("尽量看看这道 web 题")
    ck("A2 无可判定验收词", any("没有可判定的验收词" in x for x in a), a)
    ck("A3 模糊词点名", any("模糊词" in x and "尽量" in x for x in a), a)
    ck("A4 没数字", any("没有数字" in x for x in a), a)
    a = m.goal_advice("把全站数据全部拉下来")
    ck("A5 全站类没最小样本", any("最小样本" in x for x in a), a)
    a = m.goal_advice("只取 1 条证明能拿到，2 小时内完成并提交回执")
    ck("A6 有最小样本就不再抱怨", not any("最小样本" in x for x in a), a)

    # ── B. 好目标零提醒
    good = "拿到 flag 并提交，平台回执 accepted；2 小时内只打 1 个入口"
    ck("B1 好目标零提醒", m.goal_advice(good) == [], m.goal_advice(good))
    lines = m.goal_discipline_lines(good)
    ck("B2 好目标给肯定句", len(lines) == 1 and "可判定验收" in lines[0], lines)
    vague_lines = m.goal_discipline_lines("尽量看看")
    ck("B3 坏目标给规范行", any("规范：目标=可判定的验收" in x for x in vague_lines), vague_lines)
    ck("B4 坏目标提醒标 ⚠", any("⚠ 目标写法" in x for x in vague_lines), vague_lines)

    # ── C. 两个入口都打，且不拒收
    lab = tempfile.mkdtemp(prefix="fgsgoal-")
    rc, out = fgs(lab, "kickoff", "g1", "--kind", "ctf", "--goal", "尽量看看这道题")
    ck("C1 kickoff 坏目标 rc=0（软提醒不拦）", rc == 0, (rc, out[-400:]))
    ck("C2 kickoff 打了提醒", "⚠ 目标写法" in out, out[-400:])
    ck("C3 kickoff 打了规范", "规范：目标=可判定的验收" in out, out[-400:])
    rc, out = fgs(lab, "kickoff", "g2", "--kind", "ctf", "--goal", good)
    ck("C4 kickoff 好目标 rc=0", rc == 0, (rc, out[-300:]))
    ck("C5 kickoff 好目标给肯定", "✓ 目标写法" in out, out[-300:])
    rc, out = fgs(lab, "goal", "g1", "尽量研究一下")
    ck("C6 goal 坏目标 rc=0", rc == 0, (rc, out))
    ck("C7 goal 打了提醒", "⚠ 目标写法" in out, out)
    rc, out = fgs(lab, "goal", "g1", good)
    ck("C8 goal 好目标给肯定", rc == 0 and "✓ 目标写法" in out, (rc, out))

    # ── D. 静态兜底
    src = open(FGS, encoding="utf-8").read()
    ck("D1 常量在", "GOAL_ACCEPT_HINTS" in src and "GOAL_VAGUE" in src, "")
    ck("D2 四条检查在 goal_advice", src.count("out.append(") >= 4, src.count("out.append("))
    ck("D3 kickoff 调用", "goal_discipline_lines(a.goal)" in src, "")
    ck("D4 goal 调用", "goal_discipline_lines(a.text)" in src, "")
    doc = open(os.path.join(ROOT, "docs", "并行工作.md"), encoding="utf-8").read()
    ck("D5 文档 §4.6r", "4.6r" in doc, "")
    ck("D6 文档写不确定不写 deny", "不确定" in doc and "deny" in doc, "")
    ck("D7 文档写数字约束", "约束写数字" in doc, "")

    print("test_goal_discipline: " + str(_passed) + " passed, " + str(_failed) + " failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())