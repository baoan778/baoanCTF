#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_relate_idempotent.py — 连边幂等：同一条边写两次不连坐整批

血账（2026-11-17）：relate 第二次命中「已存在」直接 Fail(...,2) → rc=2。
在 apply / 三路并行场景下 rc=2 会**连坐整批**（board/schema.md:304 整批拒绝、
fgs gate 退 3、fgs.py 打印「整批拒绝」），轻则重跑、重则丢结论。

断言：
  A. 连写两次同一条边 → 两次 rc=0；第二次打印「幂等跳过」。
  B. 第二次不留第二条边上账：图里这条边只有 1 条。
  C. 第二次留下 relate.dedup 审计（可复查「我确实重试过」）。
  D. 真拒绝不许被幂等收编：自环 / 成环仍然 rc≠0。

    python3 board/tests/test_relate_idempotent.py
"""
import json
import os
import shutil
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
        print(f"  ✗ {name}" + (f"   [{detail}]" if detail else ""))


def run(lab, *args):
    env = dict(os.environ, PI_LAB_BOARD_DIR=lab)
    r = subprocess.run([sys.executable, FGS, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=ROOT, env=env, timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def graph(lab, pid="d1"):
    with open(os.path.join(lab, pid, "graph.json"), encoding="utf-8") as fh:
        return json.load(fh)


def main():
    lab = tempfile.mkdtemp(prefix="relateidem-")
    try:
        rc, out = run(lab, "kickoff", "d1", "--kind", "ctf",
                      "--goal", "连边幂等", "--target", "http://demo")
        ck("建板 rc=0", rc == 0, out[-200:])
        g = graph(lab)
        goal = [i for i, n in g["nodes"].items() if n["type"] == "goal"][0]

        rc, out = run(lab, "fact-add", "d1", "入口是 /api/download")
        ck("建 fact rc=0", rc == 0, out[-200:])
        fid = [i for i, n in graph(lab)["nodes"].items() if n["type"] == "fact"][0]

        # ── A + B + C ────────────────────────────────────────────────
        print("A) 同一条边写两次：两次都要 rc=0")
        rc1, out1 = run(lab, "relate", "d1", "--type", "about", "--from", fid, "--to", goal)
        ck("A1 首次 rc=0", rc1 == 0, f"rc={rc1} {out1[-200:]}")
        rc2, out2 = run(lab, "relate", "d1", "--type", "about", "--from", fid, "--to", goal)
        ck("A2 第二次 rc=0（幂等，不再 rc=2 连坐整批）", rc2 == 0, f"rc={rc2} {out2[-300:]}")
        ck("A3 明说「已存在（幂等跳过）」", "幂等跳过" in out2, out2[-300:])

        es = [e for e in graph(lab)["edges"]
              if e["from"] == fid and e["type"] == "about" and e["to"] == goal]
        ck("B1 图上只留 1 条边", len(es) == 1, f"实际 {len(es)} 条")

        with open(os.path.join(lab, "d1", "log.jsonl"), encoding="utf-8") as fh:
            ops = [json.loads(l)["op"] for l in fh.read().splitlines() if l.strip()]
        ck("C1 审计里有 relate.dedup", "relate.dedup" in ops, str(ops[-5:]))

        # ── D. 真拒绝不许被收编 ─────────────────────────────────────
        print("D) 自环 / 成环仍然要拒（幂等只收编「重复」，不收编「非法」）")
        rc, out = run(lab, "relate", "d1", "--type", "evidences", "--from", fid, "--to", fid)
        ck("D1 自环 rc≠0", rc != 0, f"rc={rc} {out[-200:]}")
        ck("D2 自环原因可见", "自环" in out, out[-200:])

        rc, out = run(lab, "fact-add", "d1", "另一条事实")
        ck("建第二条 fact rc=0", rc == 0, out[-200:])
        facts = [i for i, n in graph(lab)["nodes"].items() if n["type"] == "fact"]
        f2 = [i for i in facts if i != fid][0]
        rc, out = run(lab, "relate", "d1", "--type", "derived_from",
                      "--from", fid, "--to", f2)
        ck("D3 正向连边 rc=0", rc == 0, out[-200:])
        rc, out = run(lab, "relate", "d1", "--type", "derived_from",
                      "--from", f2, "--to", fid)
        ck("D4 成环 rc≠0", rc != 0, f"rc={rc} {out[-200:]}")
        ck("D5 成环原因可见", "成环" in out, out[-200:])

        print(f"\n{_passed}/{_passed + _failed} 断言通过")
        return 0 if _failed == 0 else 1
    finally:
        shutil.rmtree(lab, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
