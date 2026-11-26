#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_local_deny_tag.py — #5 拦截消息必须自报「本机管控·非靶机防御」

为什么（2026-11-17）：CTF 里有三个「本机在拒」的入口 —— lane_gate 拒 /
DSH 沙箱拒 / fgs gate 退 3。不标清楚就会被读成「靶机有 WAF/防护」→
换 payload 重试 = 纯烧时间（真实失分形态）。

断言（三条都是**跑出来的行为**，不是读注释）：
  A. lane_gate 拒绝时输出含「非靶机」。
  B. fgs gate 拒绝（退 3）时输出含「非靶机」。
  C. fgs verify 逐字核验失败时输出含「非靶机」。
  D. 静态兜底：子代理任务书硬条款里也有这条（0d）。
"""
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
LANE = os.path.join(ROOT, "tools", "lane_gate.py")
TAG = "非靶机"

_passed, _failed = 0, 0


def ck(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  ✗ {name}" + (f"   [{detail}]" if detail else ""))


def run(lab, *args, script=None):
    env = dict(os.environ)
    if lab:
        env["PI_LAB_BOARD_DIR"] = lab
    exe = sys.executable
    target = script or FGS
    r = subprocess.run([exe, target, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=ROOT, env=env, timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main():
    lab = tempfile.mkdtemp(prefix="localtag-")
    try:
        print("A) lane_gate 拒绝文案要自报本机")
        rc, out = run(None, "check", "rm -rf /", script=LANE)
        ck("A1 仍拒（rc=10）", rc == 10, f"rc={rc}")
        ck("A2 含「非靶机」", TAG in out, out[-300:])

        print("B) fgs gate 退 3 时也要自报本机")
        rc, out = run(lab, "kickoff", "d1", "--kind", "ctf",
                      "--goal", "文案", "--target", "http://demo")
        ck("B0 建板 rc=0", rc == 0, out[-200:])
        rc, out = run(lab, "gate", "d1")
        ck("B1 仍拒绝（rc=3）", rc == 3, f"rc={rc}")
        ck("B2 含「非靶机」", TAG in out, out[-300:])

        print("C) verify 逐字核验失败也要自报本机")
        run(lab, "fact-add", "d1", "flag{nope123}")
        r = subprocess.run([sys.executable, FGS, "verify", "d1", "f001",
                            "--text", "nothing here"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=ROOT,
                           env=dict(os.environ, PI_LAB_BOARD_DIR=lab), timeout=120)
        out = (r.stdout or "") + (r.stderr or "")
        ck("C1 仍拒（rc=3）", r.returncode == 3, f"rc={r.returncode}")
        ck("C2 含「非靶机」", TAG in out, out[-300:])

        print("D) 子代理任务书 0d 条（静态兜底）")
        with open(FGS, encoding="utf-8") as fh:
            src = fh.read()
        ck("D1 任务书里有 0d 条", "0d.【本机拒绝 ≠ 靶机防御】" in src)
        ck("D2 任务书里点名「非靶机」", "非靶机" in src)
        ck("D3 常量 LOCAL_TAG 至少用在 3 个拒绝分支", src.count("LOCAL_TAG") >= 4,
           f"出现 {src.count('LOCAL_TAG')} 次")

        print(f"\n{_passed}/{_passed + _failed} 断言通过")
        return 0 if _failed == 0 else 1
    finally:
        shutil.rmtree(lab, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
