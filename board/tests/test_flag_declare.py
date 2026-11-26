#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_flag_declare.py — T1 声明式提交闸门的穷举测试

★ 这是**独立脚本**，不是 pytest：`python3 board/tests/test_flag_declare.py`
    （本仓惯例，见 board/tests/_pkgpaths.py；跑 `python3 -m pytest` 会「no tests ran」）

验收口径（backlog §7 E1）：
  A. 未带 --source 直接拒（argparse required 层 + 值层双保险）
  B. --source 里没有该值 → 拒，并打出最接近的行（不是只报「失败」）
  C. 值在里面 → 落 verified Fact + 账本 recorded + 审计；gate 的逐字溯源项直接绿
  D. ★ 不替平台宣布 correct（本命令只到 recorded，correct 只由 reply.correct 决定）
  E. 收外部路径走 read_capped（/dev/zero、目录、超大文件必须被拒）
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
FGS = os.path.join(ROOT, "board", "fgs.py")

_pass, _fail = 0, 0
_fails = []


def ck(name, cond, detail=""):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        _fails.append(f"{name} {detail}")


def run(lab, *args, stdin=None):
    env = dict(os.environ)
    env["PI_LAB_BOARD_DIR"] = lab
    r = subprocess.run([sys.executable, FGS, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=ROOT, env=env, input=stdin,
                       timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main():
    lab = tempfile.mkdtemp(prefix="flagdeclare-")
    try:
        rc, out = run(lab, "kickoff", "d1", "--kind", "ctf",
                      "--goal", "T1 闸门", "--target", "http://demo")
        ck("建板 rc=0", rc == 0, out[-300:])

        ev = os.path.join(lab, "out.txt")
        with open(ev, "w", encoding="utf-8") as fh:
            fh.write("HTTP/1.1 200 OK\nserver: nginx\nX-Debug: FLAG{abc124}\ndone\n")

        # ── A. 未带 --source ──────────────────────────────────────────
        print("A) 未带 --source 必须拒")
        rc, out = run(lab, "flag-declare", "d1", "--value", "FLAG{abc124}")
        ck("A1 未带 --source rc≠0", rc != 0, f"rc={rc}")
        ck("A2 明确点名 --source 必填", "--source" in out, out[-200:])

        # ── B. 值不在证据里 ──────────────────────────────────────────
        print("B) 值不在证据里必须拒，且要定位差异")
        rc, out = run(lab, "flag-declare", "d1", "--value", "FLAG{abc123}", "--source", ev)
        ck("B1 rc=3（逐字核验失败专用码）", rc == 3, f"rc={rc}")
        ck("B2 明说没有逐字出现", "没有逐字出现" in out, out[-300:])
        ck("B3 ★ 打出最接近的那一行（能看出抄错一位）", "FLAG{abc124}" in out, out[-400:])
        ck("B4 明说相似度不是证据", "不是证据" in out, out[-400:])
        ck("B5 拒的时候不许落 Fact", not os.path.exists(os.path.join(lab, "d1", "flag-ledger.jsonl"))
           or "FLAG{abc123}" not in open(os.path.join(lab, "d1", "flag-ledger.jsonl"), encoding="utf-8").read())

        # ── C. 命中 → Fact + 账本 + gate ─────────────────────────────
        print("C) 命中时落 Fact + 记账")
        rc, out = run(lab, "flag-declare", "d1", "--value", "FLAG{abc124}", "--source", ev,
                      "--from", "g001", "--tool", "curl", "--target", "http://demo", "--id", "D1")
        ck("C1 rc=0", rc == 0, out[-300:])
        ck("C2 打印 byte-exact 通过", "byte-exact" in out, out[-300:])
        ck("C3 打印 Fact id", "f001" in out, out[-300:])
        ck("C4 打印账本 id 与 recorded", "D1" in out and "recorded" in out, out[-300:])
        ck("C5 ★ 明说不判平台对错", "不判平台对错" in out or "reply.correct" in out, out[-300:])

        g = json.load(open(os.path.join(lab, "d1", "graph.json"), encoding="utf-8"))
        fact = (g.get("nodes") or {}).get("f001") or {}
        ck("C6 Fact confidence=verified", fact.get("confidence") == "verified", str(fact.get("confidence")))
        ck("C7 Fact meta.verified_by=verbatim",
           (fact.get("meta") or {}).get("verified_by") == "verbatim", str(fact.get("meta")))
        ck("C8 Fact 记了证据来源",
           (fact.get("provenance") or {}).get("evidence") == ev, str(fact.get("provenance")))
        ck("C9 ★ Fact 挂到图上（孤点 Fact 不能授权 solved）",
           any(e.get("from") == "f001" and e.get("type") == "derived_from" for e in g.get("edges") or []),
           str((g.get("edges") or [])[:3]))

        lp = os.path.join(lab, "d1", "flag-ledger.jsonl")
        ck("C10 账本落盘", os.path.exists(lp), lp)
        led = open(lp, encoding="utf-8").read() if os.path.exists(lp) else ""
        ck("C11 账本 state=recorded", "\"recorded\"" in led, led[:200])
        ck("C12 账本存了值本身（能重投）", "FLAG{abc124}" in led, led[:200])
        ck("C13 账本回指 Fact", "\"fact\": \"f001\"" in led or "\"fact\":\"f001\"" in led, led[:200])

        # ── D. 不替平台宣布 correct ─────────────────────────────────
        print("D) ★ 不许替平台宣布 correct")
        ck("D1 账本里**没有** accepted", "accepted" not in led, led[:200])
        ck("D2 账本里**没有** correct 字段", "correct" not in led, led[:200])

        # ── E. read_capped 护栏 ─────────────────────────────────────
        print("E) 收外部路径必须走 read_capped")
        if os.name == "posix":
            rc, out = run(lab, "flag-declare", "d1", "--value", "FLAG{abc124}", "--source", "/dev/zero")
            ck("E1 /dev/zero 被拒（否则读爆 VM）", rc != 0 and ("普通文件" in out or "太大" in out), out[-200:])
        else:
            print("  （Windows 无 /dev/zero，跳过 E1；该项在 Git-Bash/容器侧由 test_cli_smoke 覆盖）")
        rc, out = run(lab, "flag-declare", "d1", "--value", "x", "--source", lab)
        ck("E2 目录被拒", rc != 0 and "目录" in out, out[-200:])
        rc, out = run(lab, "flag-declare", "d1", "--value", "x", "--source", os.path.join(lab, "nope.txt"))
        ck("E3 不存在的文件被拒", rc != 0 and "不存在" in out, out[-200:])

        # ── F. --source - 走 stdin ───────────────────────────────────
        print("F) --source - 走管道")
        rc, out = run(lab, "flag-declare", "d1", "--value", "FLAG{pipe99}", "--source", "-",
                      stdin="noise\nFLAG{pipe99}\nnoise\n")
        ck("F1 管道能通过", rc == 0 and "byte-exact" in out, out[-300:])

        # ── G. gate 逐字溯源项 ───────────────────────────────────────
        print("G) gate 的逐字溯源项直接绿")
        rc, out = run(lab, "step-add", "d1", "拿 flag", "--from", "g001")
        rc, out = run(lab, "step-claim", "d1", "s001", "--by", "ctf-recon")
        ck("G0 派活一路（gate 前置）", rc == 0, out[-200:])
        rc, out = run(lab, "solved", "d1", "--fact", "f001", "--desc", "X-Debug 泄漏",
                      "--chain", "X-Debug→curl→逐字命中")
        ck("G1 solved rc=0", rc == 0, out[-300:])
        rc, out = run(lab, "gate", "d1")
        ck("G2 gate rc=0", rc == 0, out[-500:])
        ck("G3 ★ gate 的逐字溯源项是绿",
           "✓ 逐字溯源" in out and "已盖 verbatim" in out, out[-600:])

    finally:
        shutil.rmtree(lab, ignore_errors=True)

    print("=" * 60)
    for f in _fails:
        print("FAIL:", f)
    print(f"pass={_pass} fail={_fail}")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
