#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_chain_view.py — 利用链视图（跨唤醒只认一张表）

为什么（2026-11-17）：多步利用链（拿 session → 打内网 → 上传 → RCE）跨唤醒会重头跑。
ARTEX 把 Todos 存在 session 之外（actool.NewTodoStore 经 opts.Todos 注入，planner 每轮 List()），
session 是新的清单不是。baoan 原先有 step/依赖/fact，但没有「按依赖序排好、带产出」的链视图，
压缩交接后接任者只能靠回忆。

判据（跑出来的行为）：
  A. 三条链 s001→s002→s003：依赖序与深度正确，产出 fact 挂在产出它的那步上。
  B. 只有 s002 可派；s003 报「等 s002」。s002 做完后 s003 变可派。
  C. claimed 的步标 ▶ 且带 by；abandoned 的步标 × 且不可派。
  D. --json 是合法 JSON，字段齐全（id/depth/text/status/claimed_by/deps/blocking/ready/why/facts）。
  E. 空板提示怎么建第一步，rc=0（不是报错）。
  F. STATE 的 §0 链存在，且五段式骨架仍在；§0 里能读到前序与可派。
  G. 文档 §4.6h 存在且写了纪律（只派可派的 / 同一件事别拆两条 / 探了不写回=没做）。
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


def main():
    lab = tempfile.mkdtemp(prefix="chain-")
    fgs(lab, "kickoff", "p1", "--kind", "ctf", "--goal", "g", "--target", "lab.test")
    fgs(lab, "step-add", "p1", "打 /api/login 拿 session", "--target", "lab.test")
    fgs(lab, "step-add", "p1", "用 session 打 /admin", "--target", "lab.test", "--depends", "s001")
    fgs(lab, "step-add", "p1", "上传 webshell", "--target", "lab.test", "--depends", "s002")

    print("A) 依赖序 / 深度 / 产出")
    rc, out = fgs(lab, "chain", "p1")
    ck("A1 rc=0", rc == 0, out[:200])
    ck("A2 标题在", "利用链" in out, out[:200])
    lines = [l for l in out.splitlines() if l.strip().startswith(("✓", "·", "▶", "×"))]
    ck("A3 三步都在", len(lines) == 3, lines)
    ck("A4 深度靠缩进", lines[1].startswith("  ") and not lines[0].startswith("  "), lines)
    ck("A5 顺序 s001→s002→s003", [l.split()[1] for l in lines if len(l.split()) > 1] == ["s001", "s002", "s003"], lines)
    rc, outp = fgs(lab, "chain", "p1", "--json")
    try:
        rows = json.loads(outp[outp.index("["):])
        okjson = True
    except Exception as e:
        rows, okjson = [], False
        print("   json err:", repr(e), outp[:200])
    ck("A6 --json 合法", okjson and len(rows) == 3, outp[:200])
    ck("A7 字段齐全", okjson and all(set(["id", "depth", "text", "status", "claimed_by", "deps", "blocking", "ready", "why", "facts"]).issubset(r) for r in rows), rows[:1])
    ck("A8 深度 1/2/3", okjson and [r["depth"] for r in rows] == [1, 2, 3], [r.get("depth") for r in rows])
    ck("A9 s003 前序是 s002", okjson and rows[2]["deps"] == ["s002"], rows[2:])

    print("B) 只派前置满足的")
    ck("B1 s001 可派", okjson and rows[0]["ready"] is True, rows[0:1])
    ck("B2 s002 不可派", okjson and rows[1]["ready"] is False, rows[1:2])
    ck("B3 s002 报等 s001", okjson and rows[1]["blocking"] == ["s001"], rows[1:2])
    ck("B3b s003 报等 s002", okjson and rows[2]["blocking"] == ["s002"], rows[2:3])
    ck("B4 输出点名下一步可派 s001", "下一步可派" in out and "s001" in out.split("下一步可派")[1], out[-200:])
    fgs(lab, "step-claim", "p1", "s001", "--by", "w1")
    fgs(lab, "step-conclude", "p1", "s001", "拿到 admin session", "--evidence", "Set-Cookie: x")
    rc, out = fgs(lab, "chain", "p1")
    ck("B5 产出 fact 挂在 s001", "f001" in out.split("s002")[0], out[:400])
    rc, outp = fgs(lab, "chain", "p1", "--json")
    rows = json.loads(outp[outp.index("["):])
    ck("B6 完成后 s002 可派", rows[1]["ready"] is True, rows[1])
    fgs(lab, "step-claim", "p1", "s002", "--by", "w2")
    fgs(lab, "step-conclude", "p1", "s002", "进到 admin 后台")
    rc, outp = fgs(lab, "chain", "p1", "--json")
    rows = json.loads(outp[outp.index("["):])
    ck("B7 s002 做完后 s003 可派", rows[2]["ready"] is True, rows[2])

    print("C) 状态标记")
    rc, out = fgs(lab, "step-claim", "p1", "s003", "--by", "w3")
    rc, out = fgs(lab, "chain", "p1")
    ck("C1 claimed 标 ▶", "▶ s003" in out, out[:400])
    ck("C2 claimed 带 by", "by w3" in out, out[:400])
    lab2 = tempfile.mkdtemp(prefix="chain2-")
    fgs(lab2, "kickoff", "p2", "--kind", "ctf", "--goal", "g", "--target", "lab.test")
    fgs(lab2, "step-add", "p2", "试 /old 入口")
    fgs(lab2, "step-claim", "p2", "s001", "--by", "w1")
    _, out = fgs(lab2, "chain", "p2", "--json")
    rows = json.loads(out[out.index("["):])
    ck("C3 claimed 的步 ready=false", rows[0]["ready"] is False and rows[0]["status"] == "claimed", rows[0])

    print("D) 空板")
    lab3 = tempfile.mkdtemp(prefix="chain3-")
    fgs(lab3, "kickoff", "p3", "--kind", "ctf", "--goal", "g", "--target", "lab.test")
    rc, out = fgs(lab3, "chain", "p3")
    ck("D1 rc=0", rc == 0, out[:200])
    ck("D2 教怎么建第一步", "step-add" in out, out[:200])

    print("E) STATE 的 §0 链")
    rc, out = fgs(lab, "checkpoint", "p1", "--state")
    ck("E1 有 §0 链", "## 0. 链" in out, out[:400])
    ck("E2 §0 写前序", "前序 s002" in out, out[:800])
    ck("E3 五段式仍在", all(s in out for s in ["1. Allocation", "2. Parked", "3. Intel log", "4. Queue", "5. Next moves"]), out[:200])
    ck("E4 §0 在 Allocation 之前", out.index("## 0. 链") < out.index("1. Allocation"), "")

    print("F) 文档纪律")
    doc = Path(ROOT, "docs", "并行工作.md").read_text(encoding="utf-8", errors="replace")
    ck("F1 §4.6h 存在", "4.6h 利用链视图" in doc)
    ck("F2 写「只派可派」", "只派 `可派`" in doc or "只派可派" in doc)
    ck("F3 写「同一件事别拆两条」", "同一件事别拆两条" in doc)
    ck("F4 写诚实边界", "探了不写回 = 没做" in doc)
    src = Path(FGS).read_text(encoding="utf-8", errors="replace")
    ck("F5 CLI 注册 chain", '"chain", cmd_chain' in src)
    ck("F6 环不静默丢", "历史数据里的环" in src)

    print("")
    print(("OK" if _failed == 0 else "FAIL") + "  " + str(_passed) + " passed, " + str(_failed) + " failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
