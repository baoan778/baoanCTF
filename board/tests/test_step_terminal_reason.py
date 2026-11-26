#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_step_terminal_reason.py — #40 ① 终态按原因分流（证伪 ≠ 超时）

为什么（2026-11-17）：`abandoned` 一个状态混装两件事 ——「这条真路走不通」(refuted) 与
「这次没跑完」(timeout)。而 AGENTS.md §六 纪律 3 说「图上写了试过不行的就换攻击面」，
于是**一次超时会被读成一条路封死**，规划者永久绕开一条从未被否定过的路 ——
这是选错题级别的自伤。更糟的是：改造前**根本没有 step-abandon 子命令**
（只有 6 处文案提到它），worker 停在途中无法收口。

判据（跑出来的行为）：
  A. --why 必填：不带 --why 时 rc=2 且提示「空 WHY」。
  B. 默认 reason=refuted；落盘 abandon_reason 与 abandon_why；审计键 step.abandon 带 reason。
  C. refuted 不能直接重开（rc=2、指向 --force-refuted）；--force-refuted 可重开且审计记 force=true。
  D. timeout 可直接重开；重开后回到 open、abandon_* 字段清掉、能被重新 claim。
  E. fgs chain：timeout 标 ⏱、refuted 标 ×；--json 带 reason/abandon_why。
  F. STATE §2 Parked 五列：refuted 写「别再来」、timeout 写「可重开」并给出命令。
  G. fgs next：前序是 timeout 时报「前序只是没跑完（可 step-reopen 续上）」，refuted 时报「前序未完成」。
  H. 老数据（无 abandon_reason）按 refuted 读，Parked 自报「未填」，不冒充 timeout。
  I. 静态兜底：常量 / CLI 注册 / schema.md / docs §4.6l。
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
        print("  x " + name + ("   [" + str(detail)[:300] + "]" if detail else ""))


def fgs(lab, *args):
    env = dict(os.environ)
    env["PI_LAB_BOARD_DIR"] = lab
    env["PI_FGS_TOOFAST_SEC"] = "0"
    r = subprocess.run([sys.executable, FGS, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=ROOT, env=env, timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def node(lab, pid, sid):
    g = json.loads((Path(lab) / pid / "graph.json").read_text(encoding="utf-8"))
    return g, g["nodes"][sid]


def audit_ops(lab, pid):
    p = Path(lab) / pid / "log.jsonl"
    if not p.is_file():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except Exception:
            pass
    return out


def main():
    lab = tempfile.mkdtemp(prefix="abandon-")
    fgs(lab, "kickoff", "p1", "--kind", "ctf", "--goal", "g", "--target", "lab.test")
    fgs(lab, "step-add", "p1", "打 /api/login 拿 session", "--target", "lab.test")
    fgs(lab, "step-add", "p1", "用 session 打 /admin", "--target", "lab.test", "--depends", "s001")
    fgs(lab, "step-add", "p1", "上传 webshell", "--target", "lab.test", "--depends", "s002")

    print("A) --why 必填")
    rc, out = fgs(lab, "step-abandon", "p1", "s001")
    ck("A1 没 --why 被拒 rc=2", rc == 2, out[:200])
    ck("A2 提示空 WHY", "WHY" in out and "猜" in out, out[:300])
    rc, out = fgs(lab, "step-abandon", "p1", "s001", "--why", "   ")
    ck("A3 空白 --why 也算空", rc == 2, out[:200])
    rc, out = fgs(lab, "step-abandon", "p1", "s001", "--reason", "bogus", "--why", "x")
    ck("A4 非法 reason 被拒", rc == 2 and "refuted" in out, out[:200])

    print("B) 默认 refuted 落盘 + 审计")
    rc, out = fgs(lab, "step-abandon", "p1", "s001", "--why", "403 固定，换 payload 也一样")
    ck("B1 rc=0", rc == 0, out[:300])
    g, s = node(lab, "p1", "s001")
    ck("B2 status=abandoned", s.get("status") == "abandoned", s.get("status"))
    ck("B3 默认 reason=refuted", s.get("abandon_reason") == "refuted", s.get("abandon_reason"))
    ck("B4 WHY 落盘", "403" in (s.get("abandon_why") or ""), s.get("abandon_why"))
    ck("B5 打印「证伪」", "证伪" in out and "refuted" in out, out[-300:])
    ops = [e for e in audit_ops(lab, "p1") if e.get("op") == "step.abandon"]
    ck("B6 审计 step.abandon", len(ops) == 1 and ops[0].get("id") == "s001", ops[-1:])
    ck("B7 审计带 reason", bool(ops and ops[0].get("reason") == "refuted"), ops[-1:])
    rc, out = fgs(lab, "step-abandon", "p1", "s001", "--why", "再来一次")
    ck("B8 已 abandoned 再放弃被拒", rc == 2 and "step-reopen" in out, out[:300])

    print("C) refuted 需 --force-refuted 才能重开")
    rc, out = fgs(lab, "step-reopen", "p1", "s001")
    ck("C1 直接重开被拒 rc=2", rc == 2, out[:200])
    ck("C2 指向 --force-refuted", "--force-refuted" in out, out[:300])
    ck("C3 说清是证伪不是超时", "证伪" in out, out[:300])
    rc, out = fgs(lab, "step-reopen", "p1", "s001", "--force-refuted", "--why", "拿到反例：管理员路径变了")
    ck("C4 强制重开 rc=0", rc == 0, out[:300])
    g, s = node(lab, "p1", "s001")
    ck("C5 回到 open", s.get("status") == "open", s.get("status"))
    ops = [e for e in audit_ops(lab, "p1") if e.get("op") == "step.reopen"]
    ck("C6 审计记 force=true", bool(ops and ops[-1].get("force") is True), ops[-1:])
    ck("C7 审计带 prev_reason", bool(ops and ops[-1].get("prev_reason") == "refuted"), ops[-1:])

    print("D) timeout 可直接重开")
    rc, out = fgs(lab, "step-abandon", "p1", "s002", "--reason", "timeout",
                  "--why", "容器到点被杀，还没试完 SSRF")
    ck("D1 rc=0", rc == 0, out[:300])
    ck("D2 打印「没跑完」", "没跑完" in out, out[-300:])
    ck("D3 提示 step-reopen", "step-reopen" in out, out[-300:])
    g, s = node(lab, "p1", "s002")
    ck("D4 reason=timeout", s.get("abandon_reason") == "timeout", s.get("abandon_reason"))
    rc, out = fgs(lab, "step-reopen", "p1", "s002")
    ck("D5 无需 force 就能重开 rc=0", rc == 0, out[:300])
    g, s = node(lab, "p1", "s002")
    ck("D6 回到 open", s.get("status") == "open", s.get("status"))
    ck("D7 abandon_* 清干净", not any(k in s for k in ("abandon_reason", "abandon_why")), s)
    rc, out = fgs(lab, "step-claim", "p1", "s002", "--by", "w1", "--ignore-deps")
    ck("D8 重开后能重新 claim（前序还没 done，用 --ignore-deps 放行）", rc == 0, out[:300])
    fgs(lab, "step-release", "p1", "s002")

    print("E) chain 标记：⏱ vs ×")
    fgs(lab, "step-abandon", "p1", "s001", "--why", "再次确认走不通")
    fgs(lab, "step-abandon", "p1", "s002", "--reason", "timeout", "--why", "到点")
    rc, out = fgs(lab, "chain", "p1")
    ck("E1 rc=0", rc == 0, out[:200])
    ck("E2 timeout 标 ⏱", "⏱ s002" in out, out[:600])
    ck("E3 refuted 标 ×", "× s001" in out, out[:600])
    rc, outp = fgs(lab, "chain", "p1", "--json")
    rows = json.loads(outp[outp.index("["):]) if "[" in outp else []
    by = {r["id"]: r for r in rows}
    ck("E4 --json 带 reason", by.get("s001", {}).get("reason") == "refuted"
       and by.get("s002", {}).get("reason") == "timeout", by.get("s002"))
    ck("E5 --json 带 abandon_why", "到点" in (by.get("s002", {}).get("abandon_why") or ""), by.get("s002"))

    print("F) STATE §2 Parked 按原因分列")
    rc, out = fgs(lab, "checkpoint", "p1", "--state")
    ck("F1 rc=0", rc == 0, out[:200])
    ck("F2 五列表头", "终止原因" in out and "什么情况可重开" in out, out[:1200])
    ck("F3 refuted 行写「别再来」", "refuted（证伪）" in out and "别再来" in out, out[:2500])
    ck("F4 timeout 行写「可重开」", "timeout（没跑完" in out and "可重开" in out, out[:2500])
    ck("F5 Parked 里能看到 WHY 原文", "到点" in out, out[:2500])
    ck("F6 timeout 行给出 step-reopen 命令", "step-reopen p1 s002" in out, out[:2500])

    print("G) next：两种前序分别措辞（单独一块板，直接前序就是它们）")
    lab3 = tempfile.mkdtemp(prefix="abandon-next-")
    fgs(lab3, "kickoff", "p3", "--kind", "ctf", "--goal", "g", "--target", "lab.test")
    fgs(lab3, "step-add", "p3", "先拿 session", "--target", "lab.test")
    fgs(lab3, "step-add", "p3", "再打 /admin", "--target", "lab.test", "--depends", "s001")
    fgs(lab3, "step-abandon", "p3", "s001", "--reason", "timeout", "--why", "容器到点")
    rc, out = fgs(lab3, "next", "p3")
    ck("G1 rc=0", rc == 0, out[:300])
    ck("G2 timeout 前序标「超时」", "超时" in out, out[:1500])
    ck("G3 timeout 前序给 step-reopen", "step-reopen" in out, out[:1500])
    ck("G4 timeout 不被说成「前序未完成」", "前序未完成" not in out, out[:1500])
    fgs(lab3, "step-reopen", "p3", "s001")
    fgs(lab3, "step-abandon", "p3", "s001", "--why", "403 固定")
    rc, out = fgs(lab3, "next", "p3")
    ck("G5 refuted 前序说「前序未完成」", "前序未完成" in out, out[:1500])
    ck("G6 refuted 不给 step-reopen 假希望", "step-reopen" not in out, out[:1500])

    print("H) 老数据（无 abandon_reason）按 refuted 读")
    lab2 = tempfile.mkdtemp(prefix="abandon-legacy-")
    fgs(lab2, "kickoff", "p2", "--kind", "ctf", "--goal", "g", "--target", "lab.test")
    fgs(lab2, "step-add", "p2", "老数据那一步", "--target", "lab.test")
    gp = Path(lab2) / "p2" / "graph.json"
    g2 = json.loads(gp.read_text(encoding="utf-8"))
    g2["nodes"]["s001"]["status"] = "abandoned"      # 模拟 #40 之前的落盘：没有 abandon_reason
    gp.write_text(json.dumps(g2, ensure_ascii=False, indent=2), encoding="utf-8")
    rc, out = fgs(lab2, "step-reopen", "p2", "s001")
    ck("H1 老数据当 refuted（需 force）", rc == 2 and "证伪" in out, out[:300])
    rc, out = fgs(lab2, "checkpoint", "p2", "--state")
    ck("H2 Parked 标 refuted", "refuted（证伪）" in out, out[:2500])
    ck("H3 自报 WHY 未填", "未填" in out, out[:2500])
    ck("H4 不冒充 timeout", "timeout（没跑完" not in out, out[:2500])

    print("I) 静态兜底")
    src = Path(FGS).read_text(encoding="utf-8", errors="replace")
    ck("I1 ABANDON_REASONS 有 timeout", "ABANDON_REASONS" in src and chr(34) + "timeout" + chr(34) in src, "")
    ck("I2 有 step_abandon_reason", "def step_abandon_reason(" in src, "")
    ck("I3 CLI 注册 step-abandon", chr(34) + "step-abandon" + chr(34) in src, "")
    ck("I4 CLI 注册 step-reopen", chr(34) + "step-reopen" + chr(34) in src, "")
    ck("I5 --reason 带 choices", "choices=ABANDON_REASONS" in src, "")
    schema = Path(ROOT, "board", "schema.md").read_text(encoding="utf-8", errors="replace")
    ck("I6 schema 写了两类原因", "abandon_reason" in schema and "timeout" in schema and "refuted" in schema, "")
    doc = Path(ROOT, "docs", "并行工作.md").read_text(encoding="utf-8", errors="replace")
    ck("I7 文档 §4.6l 在", "### 4.6l" in doc and "证伪 ≠ 超时" in doc, "")
    ck("I8 文档写了默认为什么是 refuted", "不对称" in doc, "")
    ck("I9 文档写了诚实边界（不恢复上下文）", "不恢复该路的上下文" in doc, "")

    print("")
    print("PASSED %d  FAILED %d" % (_passed, _failed))
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())