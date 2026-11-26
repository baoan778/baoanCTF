#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_step_dup_claim.py — #44 同目标只跑一个（claim 前扫入口指纹）

为什么（2026-11-17）：ARTEX 用部分唯一索引（db/schema.sql:1127-1129）保证「同一目标同时只有
一个复测」；baoan 原先只拦「同一个 step 被别人 claim」，两个**不同** step 做同一件事完全放行，
于是三路并行时两路在同一入口上重复磨（AGENTS.md §六「同一道题不许派两路做同一件事」
在代码层没有对应门禁）。

判据（跑出来的行为）：
  A. 同一目标 + 同一入口（文本里的 /path）的第二条 claim → rc=2，文案含「正在打同一个入口」
     与 --allow-dup，且 gates.jsonl 记一条 claim-dup。
  B. 加 --allow-dup → rc=0，log.jsonl 记 step.claim.dup-bypass（放行也留痕）。
  C. 弱指纹（只有 --target、文本里没有入口）撞了 → 只警告 rc=0（同面不同手法机器判不了）。
  D. 入口不同 → 不警告 rc=0（换入口就是换面）。
  E. 同一个人对同一条 step 再 claim → 不受影响（排除自身）。
  F. 既没 --target 也没入口 → 指纹空串，不猜不报警 rc=0。
  G. 静态兜底：GATES 有 claim-dup、CLI 有 --allow-dup、清单第 44 行有落地标记。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

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
        print("  x " + name + (("   [" + str(detail)[:300] + "]") if detail else ""))


def fgs(lab, *args):
    env = dict(os.environ)
    env["PI_LAB_BOARD_DIR"] = lab
    env["PI_FGS_TOOFAST_SEC"] = "0"
    r = subprocess.run([sys.executable, FGS, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=ROOT, env=env, timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def rows(path):
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def main():
    print("A) 同目标 + 同入口的第二条 claim 默认拒")
    lab = tempfile.mkdtemp(prefix="dupclaim-")
    d = Path(lab) / "p1"
    fgs(lab, "kickoff", "p1", "--kind", "ctf", "--goal", "g", "--target", "lab.test")
    fgs(lab, "step-add", "p1", "对 /api/login 试 SQLi", "--target", "lab.test")
    fgs(lab, "step-add", "p1", "对 /api/login 试弱口令", "--target", "lab.test")
    rc, out = fgs(lab, "step-claim", "p1", "s001", "--by", "w1")
    ck("A0 第一条 claim rc=0", rc == 0, out[:200])
    rc, out = fgs(lab, "step-claim", "p1", "s002", "--by", "w2")
    ck("A1 rc=2", rc == 2, "rc=" + str(rc))
    ck("A2 含「正在打同一个入口」", "正在打同一个入口" in out, out[:300])
    ck("A3 指向真正在跑的那条", "s001" in out, out[:300])
    ck("A4 给出出路 --allow-dup", "--allow-dup" in out, out[:300])
    ck("A5 指纹带入口", "/api/login" in out, out[:300])
    gl = rows(d / "gates.jsonl")
    ck("A6 门禁自证 claim-dup", any(r.get("g") == "claim-dup" for r in gl), gl[-2:])
    ck("A7 被拒的 step 没被写成 claimed", "s002" not in [r.get("why") for r in rows(d / "log.jsonl") if r.get("op") == "step.claim"], "")

    print("B) --allow-dup 放行并留审计")
    rc, out = fgs(lab, "step-claim", "p1", "s002", "--by", "w2", "--allow-dup")
    ck("B1 rc=0", rc == 0, "rc=" + str(rc) + " " + out[:200])
    ck("B2 提示已留审计", "留审计" in out, out[:300])
    lg = rows(d / "log.jsonl")
    ck("B3 log 记 dup-bypass", any(r.get("op") == "step.claim.dup-bypass" for r in lg), [r.get("op") for r in lg][-4:])
    ck("B4 dup-bypass 带被撞的 step", any(r.get("op") == "step.claim.dup-bypass" and "s001" in (r.get("dups") or []) for r in lg), "")

    print("C) 弱指纹（只有 target，没有入口）只警告")
    lab2 = tempfile.mkdtemp(prefix="dupclaim2-")
    fgs(lab2, "kickoff", "p2", "--kind", "ctf", "--goal", "g", "--target", "lab.test")
    fgs(lab2, "step-add", "p2", "打登录口", "--target", "lab.test")
    fgs(lab2, "step-add", "p2", "翻备份文件", "--target", "lab.test")
    fgs(lab2, "step-claim", "p2", "s001", "--by", "w1")
    rc, out = fgs(lab2, "step-claim", "p2", "s002", "--by", "w2")
    ck("C1 弱指纹不拒 rc=0", rc == 0, "rc=" + str(rc) + " " + out[:200])
    ck("C2 给出相近提醒", "目标/入口相近" in out, out[:300])
    ck("C3 提醒里点名 s001", "s001" in out, out[:300])

    print("D) 入口不同不警告")
    lab3 = tempfile.mkdtemp(prefix="dupclaim3-")
    fgs(lab3, "kickoff", "p3", "--kind", "ctf", "--goal", "g", "--target", "lab.test")
    fgs(lab3, "step-add", "p3", "打 /api/login", "--target", "lab.test")
    fgs(lab3, "step-add", "p3", "打 /admin/upload", "--target", "lab.test")
    fgs(lab3, "step-claim", "p3", "s001", "--by", "w1")
    rc, out = fgs(lab3, "step-claim", "p3", "s002", "--by", "w2")
    ck("D1 rc=0", rc == 0, "rc=" + str(rc) + " " + out[:200])
    ck("D2 没有重复警告", ("同一个入口" not in out) and ("目标/入口相近" not in out), out[:300])

    print("E) 那一路已 release（不再在跑）就不再算重复")
    lab5 = tempfile.mkdtemp(prefix="dupclaim5-")
    fgs(lab5, "kickoff", "p5", "--kind", "ctf", "--goal", "g", "--target", "lab.test")
    fgs(lab5, "step-add", "p5", "对 /api/login 试 SQLi", "--target", "lab.test")
    fgs(lab5, "step-add", "p5", "对 /api/login 试弱口令", "--target", "lab.test")
    fgs(lab5, "step-claim", "p5", "s001", "--by", "w1")
    fgs(lab5, "step-release", "p5", "s001")
    rc, out = fgs(lab5, "step-claim", "p5", "s002", "--by", "w2")
    ck("E1 release 后 rc=0", rc == 0, "rc=" + str(rc) + " " + out[:250])
    ck("E2 没有重复警告", "同一个入口" not in out, out[:250])

    print("F) 无 target 无入口：不猜不报警")
    lab4 = tempfile.mkdtemp(prefix="dupclaim4-")
    fgs(lab4, "kickoff", "p4", "--kind", "ctf", "--goal", "g", "--target", "lab.test")
    fgs(lab4, "step-add", "p4", "先看看登录口")
    fgs(lab4, "step-add", "p4", "再看看上传口")
    fgs(lab4, "step-claim", "p4", "s001", "--by", "w1")
    rc, out = fgs(lab4, "step-claim", "p4", "s002", "--by", "w2")
    ck("F1 rc=0", rc == 0, "rc=" + str(rc) + " " + out[:200])
    ck("F2 无指纹就不报警", ("同一个入口" not in out) and ("目标/入口相近" not in out), out[:300])

    print("G) 静态兜底")
    src = Path(FGS).read_text(encoding="utf-8", errors="replace")
    ck("G1 GATES 登记 claim-dup", chr(34) + "claim-dup" + chr(34) in src)
    ck("G2 CLI 有 --allow-dup", "--allow-dup" in src)
    ck("G3 claim 前调 _step_surface", "_step_surface(s)" in src)
    ck("G4 指纹只取入口不取整段文本", "_ENTRY_RX" in src and "换 payload 不算换路线" in src)
    try:
        doc = Path(ROOT, "docs", "并行工作.md").read_text(encoding="utf-8", errors="replace")
        ck("G5 文档记了这条纪律", "同目标只跑一个" in doc and "--allow-dup" in doc)
    except Exception as e:
        ck("G5 文档记了这条纪律", False, repr(e))

    print("")
    print(("OK" if _failed == 0 else "FAIL") + "  " + str(_passed) + " passed, " + str(_failed) + " failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
