#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_lane_isolation.py — 会话隔离工作目录 + 工具大输出落盘

为什么（2026-11-17）：多路并行时所有 pi 的 cwd 都是仓库根（有实测理由，见 sdkshard.py），
于是「写文件」是最后一个没有隔离面的动作 —— 两路同写 /tmp/exp.py 或仓库根同名文件，
谁最后写谁赢，另一路的证据直接没了。ARTEX 的做法是每会话一个 workDir + cmd-output 溢出目录，
并把「写文件一律到工作目录」注入系统提示。

判据（跑出来的行为）：A lane_scratch / B solver_system / C solve_one 接线 / D tracewatch spill
/ E 扩展落盘 / F fgs trace 显示 / G 文档 §4.6o。
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
sys.path.insert(0, os.path.join(ROOT, "tools"))
import sdkshard as sdk  # noqa: E402

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
    r = subprocess.run([sys.executable, os.path.join(ROOT, "board", "fgs.py"), *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=ROOT, env=env, timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="iso47-"))
    # ★ R5-D2（2026-11-21，实测）：A5 会**故意**制造一次「建不出 scratch」，
    #   而 sdk.log() 会把失败写进真 log/shard-YYYYMMDD.log —— 全量回归跑一次就污染
    #   生产日志 5 行（实测 log/shard-20261121.log 里 5 条 "建 scratch 目录失败"，
    #   全是 /tmp/iso47-* 测试路径）。测试不许碰真产物：这里把日志路径改到临时目录。
    sdk._SHARD_LOG = tmp / "shard-test.log"

    print("A) lane_scratch：建目录 / 建不出来不崩")
    sess = tmp / "work" / "solve" / "xben-001"
    sc = sdk.lane_scratch(sess)
    ck("A1 返回路径", sc is not None, sc)
    ck("A2 目录真的建出来", bool(sc) and Path(sc).is_dir(), sc)
    ck("A3 名字是 scratch", Path(str(sc)).name == sdk.SCRATCH_DIRNAME, sc)
    ck("A4 重复调用幂等", str(sdk.lane_scratch(sess)) == str(sc), "")
    bad = tmp / "not-a-dir"
    bad.write_text("x", encoding="utf-8")
    ck("A5 建不出来返回 None（不抛）", sdk.lane_scratch(bad) is None, "")

    print("B) solver_system：规约带绝对路径")
    sysmsg = sdk.solver_system(sc)
    ck("B1 仍以不变量开头（基础系统提示没被换掉）", sysmsg.startswith(sdk.SOLVER_SYSTEM[:40]), sysmsg[:60])
    ck("B2 含本路绝对路径", str(sc) in sysmsg, "")
    ck("B3 含环境变量名", sdk.SCRATCH_ENV in sysmsg, "")
    ck("B4 明写不写 /tmp", "/tmp" in sysmsg, "")
    ck("B5 明写不写仓库根", "仓库根" in sysmsg, "")
    ck("B6 大输出先落盘", "落盘" in sysmsg, "")
    ck("B7 提到 #47", "#47" in sysmsg, "")
    no = sdk.solver_system(None)
    ck("B8 没有 scratch 时整段不写", no == sdk.SOLVER_SYSTEM, "")
    ck("B9 没有 scratch 时不出现不存在的路径话术", "文件输出规约" not in no, "")

    print("C) solve_one 接线（静态）")
    src = Path(ROOT, "tools", "sdkshard.py").read_text(encoding="utf-8")
    i_scr = src.find("_scratch = lane_scratch(sessdir)")
    i_sys = src.find("write_solver_system(sessdir, _scratch)")
    ck("C1 先建 scratch 再写 system.md", 0 < i_scr < i_sys, (i_scr, i_sys))
    ck("C2 环境变量注进子进程", "env[SCRATCH_ENV] = str(_scratch)" in src, "")
    ck("C3 Popen 真的用了这份 env", "start_new_session=True, env=_env" in src, "")
    ck("C4 只有 scratch 存在才注（不注 None）", "if _scratch is not None:" in src, "")

    print("D) tracewatch：超出部分给 spill 指针（--selftest 实跑）")
    r = subprocess.run(["node", os.path.join(ROOT, "tools", "tracewatch.mjs"), "--selftest"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=ROOT, timeout=120)
    out = (r.stdout or "") + (r.stderr or "")
    ck("D1 selftest 全绿", r.returncode == 0, out[-400:])
    tw = Path(ROOT, "tools", "tracewatch.mjs").read_text(encoding="utf-8")
    ck("D2 有 spillName 导出", "export function spillName" in tw, "")
    ck("D3 只在被裁时才给指针", "if (omitted > 0 && o.spill)" in tw, "")
    ck("D4 selftest 覆盖超长/短输出/显式关闭", "超长记录应给 spill 指针" in tw and "显式关闭无 spill" in tw, "")

    print("E) 扩展写原文（静态）")
    ext = Path(ROOT, "agent-template", "extensions", "tool-trace.ts").read_text(encoding="utf-8")
    ck("E1 有落盘上限常量", "MAX_SPILL_CHARS" in ext, "")
    ck("E2 真的写到 rec.spill 指向的位置", "path.join(path.dirname(file), rec.spill)" in ext, "")
    ck("E3 写不了就删指针（fail-open）", "delete rec.spill" in ext, "")
    ck("E4 文件名来自 makeRecord（不自己拼）", "rec.spill" in ext and "spillName(" not in ext, "")

    print("F) fgs trace 显示原文指针")
    lab = tmp / "lab"
    lab.mkdir()
    fgs(str(lab), "init", "p1", "--kind", "ctf", "--goal", "g")
    tr = tmp / "tr" / "day.jsonl"
    tr.parent.mkdir(parents=True, exist_ok=True)
    spill_rel = "cmd-output/20261117090000-bash.txt"
    (tmp / "tr" / "cmd-output").mkdir(parents=True, exist_ok=True)
    (tmp / "tr" / spill_rel).write_text("FULL-BODY", encoding="utf-8")
    recs = [
        {"ts": "2026-11-17T09:00:00+08:00", "tool": "bash", "cmd": "cat big", "is_error": False,
         "out_len": 20000, "omitted": 15000, "out": "A…TAIL", "pid": "p1", "spill": spill_rel},
        {"ts": "2026-11-17T09:00:01+08:00", "tool": "bash", "cmd": "ls", "is_error": False,
         "out_len": 2, "omitted": 0, "out": "ok", "pid": "p1"},
    ]
    tr.write_text("".join(json.dumps(x, ensure_ascii=False) + chr(10) for x in recs), encoding="utf-8")
    os.environ["PI_FGS_TRACE_PATH"] = str(tr)
    rc, out = fgs(str(lab), "trace", "p1")
    ck("F1 摘要行有 ⤓ 标记", rc == 0 and "⤓" in out, out[:400])
    ck("F2 没 spill 的那条没有标记", out.count("⤓") == 1, out[:400])
    rc, out = fgs(str(lab), "trace", "p1", "--step", "t0001")
    ck("F3 --step 打印原文路径", rc == 0 and "原文：" in out, out[:400])
    ck("F4 路径指向 cmd-output 文件", "cmd-output" in out and "20261117090000-bash.txt" in out, out[:600])
    ck("F5 仍自报省略了多少", "15000" in out, out[:400])
    rc, out = fgs(str(lab), "trace", "p1", "--json")
    js = []
    try:
        js = json.loads(out)
    except Exception:
        js = []
    ck("F6 --json 带 spill 字段", bool(js) and js[0].get("spill") == spill_rel, js[:1])
    ck("F7 --json 不带正文", bool(js) and "out" not in js[0], "")
    del os.environ["PI_FGS_TRACE_PATH"]

    print("G) 文档")
    doc = Path(ROOT, "docs", "并行工作.md").read_text(encoding="utf-8")
    ck("G1 §4.6o 在", "### 4.6o 会话隔离工作目录" in doc, "")
    for i, kw in enumerate(("PI_SOLVE_SCRATCH", "cmd-output", "⤓", "诚实边界"), 1):
        ck("G2.%d 文档写了 %s" % (i, kw), kw in doc, "")
    ck("G3 明说这是纪律不是沙箱", "不是沙箱" in doc, "")

    print()
    print("test_lane_isolation: %d passed, %d failed" % (_passed, _failed))
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
