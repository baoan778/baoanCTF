#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_trace_search.py — #38 工具留痕三档检索（摘要流 / 关键字命中 / 定 id 取全文）

为什么（2026-11-17）：会话被压缩后「当时那条命令的原始输出长什么样」就没了。
tools/tracewatch.mjs 把输出裁好写进 log/tool-trace.jsonl 之后，**却没有任何检索入口**：
接任者要么整文件读（几百 KB 塞进上下文），要么靠记忆复述（记忆正是被压缩掉的东西）。
ARTEX 的答案（agent/tools.go:1800-1817,1821,1860,1930）：只给 id 回摘要流（每步 ≤100 字），
+q 只回命中，+step_ids 回全文且 maxStepIDs=5，多的进 omitted_step_ids。

判据（跑出来的行为）：
  A. 摘要流：每条一行、含 tNNNN、≤100 字、✓/✗ 与大小；半行 JSON 跳过不崩。
  B. --q：只回命中的记录 + 命中处上下文；0 命中给提示而不是报错；命中在命令里就直说。
  C. --step：回全文；一次最多 5 条，多的列 omitted_step_ids；不存在的 id → rc=2 且给修法。
  D. --lane：只回该会话；**id 与不过滤时一致**（否则报告里引用会串味）。
  E. pid 过滤：别的板不出现；无 pid 的记录在指定 pid 时被排除。
  F. --json：字段齐，且**不含正文**（正文只能走 --step 按需取）。
  G. 缺留痕文件：rc=2 且说清是「没有留痕文件」，不是崩。
  H. 纯读：跑完文件字节与 mtime 都不变（不许悄悄创建/改写留痕）。
  I. 静态兜底：常量、CLI 注册、文档 §4.6k 三档说明都在（防止实现被改成空壳）。
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


def fgs(lab, trace, *args, timeout=120):
    env = dict(os.environ)
    env["PI_LAB_BOARD_DIR"] = lab
    env["PI_FGS_TOOFAST_SEC"] = "0"
    env["PI_FGS_TRACE_PATH"] = str(trace)
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, FGS, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=ROOT, env=env, timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def rec(i, pid="p1", session="s1", out="ok", cmd="echo hi", err=False, omitted=0):
    return {"ts": "2026-11-17T09:%02d:%02d+08:00" % (10 + i, i % 60),
            "tool": "bash", "cmd": cmd, "is_error": err, "out_len": len(out),
            "omitted": omitted, "out": out, "pid": pid, "session": session}


def write_trace(path, recs, half_line=True):
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in recs)
    if half_line:
        body += "\n{\"ts\": \"2026-11-17T09:59:59+08:00\", \"tool\": \"bash\""   # 被杀的半行
    path.write_text(body + "\n", encoding="utf-8")


def main():
    lab = tempfile.mkdtemp(prefix="trace-")
    tmp = Path(tempfile.mkdtemp(prefix="tracef-"))
    trace = tmp / "tool-trace.jsonl"
    write_trace(trace, [
        rec(1, session="s1", out="HTTP/1.1 200 OK\nSet-Cookie: sid=abc123\nadmin=1",
            cmd="curl -s http://10.0.0.5/login"),
        rec(2, session="s2", out="Traceback: FLAG_TOKEN missing", cmd="python3 exp.py", err=True),
        rec(3, pid="p2", session="s1", out="nothing here", cmd="curl -s http://other/"),
    ])

    print("A) 摘要流（只给 id 时）")
    rc, out = fgs(lab, trace, "trace", "p1")
    ck("A1 rc=0", rc == 0, out[:200])
    ck("A2 头部报条数", "工具留痕 2 条" in out, out[:200])
    ids = [l.split()[0] for l in out.splitlines() if l.startswith("t0")]
    ck("A3 两条且 id 有序", ids == ["t0001", "t0002"], ids)
    ck("A4 每条 ≤100 字", all(len(l) <= 100 for l in out.splitlines() if l.startswith("t0")),
       [len(l) for l in out.splitlines() if l.startswith("t0")])
    ck("A5 失败记录标 ✗", "✗" in out and "python3 exp.py" in out, out[:400])
    ck("A6 半行 JSON 被跳过不崩", "半行" not in out and rc == 0, out[:200])
    ck("A7 大小带单位", "B ·" in out, out[:300])

    print("B) --q 关键字命中")
    rc, out = fgs(lab, trace, "trace", "p1", "--q", "sid=abc123")
    ck("B1 rc=0", rc == 0, out[:200])
    ck("B2 只回命中 1 条", "命中 1 / 2 条" in out, out[:300])
    ck("B3 带命中上下文", "Set-Cookie: sid=abc123" in out, out[:400])
    rc, out = fgs(lab, trace, "trace", "p1", "--q", "zzz-not-here")
    ck("B4 0 命中不报错且给提示", rc == 0 and "没有命中" in out, out[:300])
    rc, out = fgs(lab, trace, "trace", "p1", "--q", "python3 exp.py")
    ck("B5 命中在命令里就直说", "命中在命令里" in out, out[:400])

    print("C) --step 取全文")
    rc, out = fgs(lab, trace, "trace", "p1", "--step", "t0001")
    ck("C1 rc=0", rc == 0, out[:200])
    ck("C2 正文完整出现", "Set-Cookie: sid=abc123" in out and "admin=1" in out, out[:300])
    ck("C3 命令在一起", "curl -s http://10.0.0.5/login" in out, out[:300])
    ck("C4 标题带 id 与工具", "t0001" in out and "bash" in out, out[:200])
    rc, out = fgs(lab, trace, "trace", "p1", "--step", "t0099")
    ck("C5 不存在的 id → rc=2", rc == 2, out[:200])
    ck("C6 不存在的 id 给修法", "没找到" in out and "tNNNN" in out, out[:300])
    many = tmp / "many.jsonl"
    write_trace(many, [rec(i, out="x%d" % i, cmd="echo %d" % i) for i in range(1, 8)],
                half_line=False)
    rc, out = fgs(lab, many, "trace", "p1", "--step", "t0001,t0002,t0003,t0004,t0005,t0006,t0007")
    ck("C7 7 条只要 5 条全文", rc == 0 and out.count("── t0") == 5, out[-300:])
    ck("C8 多出来的进 omitted_step_ids", "omitted_step_ids: t0006、t0007" in out, out[-300:])
    trim = tmp / "trim.jsonl"
    write_trace(trim, [rec(1, out="HEAD…TAIL", omitted=12345)], half_line=False)
    rc, out = fgs(lab, trim, "trace", "p1", "--step", "t0001")
    ck("C9 裁剪过的记录自报省略字节数", "12345" in out and "省略" in out, out[:400])

    print("D) --lane 会话过滤（id 必须稳定）")
    rc, out = fgs(lab, trace, "trace", "p1", "--lane", "s2")
    ck("D1 只回该会话", "工具留痕 1 条" in out and "python3 exp.py" in out, out[:300])
    ck("D2 id 仍是 t0002（不是过滤后重编号）", "t0002" in out and "t0001" not in out, out[:300])

    print("E) pid 过滤")
    rc, out = fgs(lab, trace, "trace", "p2")
    ck("E1 别的板的记录按 pid 取到", "other" in out and "10.0.0.5" not in out, out[:300])
    nopid = tmp / "nopid.jsonl"
    write_trace(nopid, [{"ts": "2026-11-17T09:00:00+08:00", "tool": "bash", "cmd": "no pid",
                         "is_error": False, "out_len": 2, "omitted": 0, "out": "x"}],
                half_line=False)
    rc, out = fgs(lab, nopid, "trace", "p1")
    ck("E2 无 pid 的记录在指定 pid 时被排除", "工具留痕 0 条" in out, out[:300])

    print("F) --json")
    rc, out = fgs(lab, trace, "trace", "p1", "--json")
    try:
        rows = json.loads(out[out.index("["):])
        okjson = True
    except Exception as e:
        rows, okjson = [], False
        print("   json err:", repr(e), out[:200])
    ck("F1 合法 JSON 两条", okjson and len(rows) == 2, out[:200])
    ck("F2 字段齐全", okjson and all(set(["id", "ts", "tool", "cmd", "is_error", "out_len",
                                          "omitted"]).issubset(r) for r in rows), rows[:1])
    ck("F3 不含正文（正文按需取）", okjson and all("out" not in r for r in rows), rows[:1])

    print("G) 缺留痕文件")
    rc, out = fgs(lab, tmp / "nope.jsonl", "trace")
    ck("G1 rc=2", rc == 2, out[:200])
    ck("G2 说清是没有留痕文件", "没有工具留痕文件" in out, out[:300])

    print("H) 纯读：不改写留痕")
    before = trace.read_bytes()
    before_m = trace.stat().st_mtime_ns
    fgs(lab, trace, "trace", "p1")
    fgs(lab, trace, "trace", "p1", "--q", "sid")
    fgs(lab, trace, "trace", "p1", "--step", "t0001")
    ck("H1 字节未变", trace.read_bytes() == before, "字节被改了")
    ck("H2 mtime 未变", trace.stat().st_mtime_ns == before_m, "文件被重写了")

    print("I) 静态兜底")
    src = Path(FGS).read_text(encoding="utf-8", errors="replace")
    ck("I1 摘要宽度常量", "TRACE_MAX_FULL = 5" in src and "TRACE_SUMMARY_CHARS = 100" in src, "")
    ck("I2 有 --q/--step/--lane 三个入口", all(x in src for x in ["--q", "--step", "--lane"]), "")
    ck("I3 CLI 注册了 trace", 'add("trace", cmd_trace' in src, "")
    ck("I4 cmd_trace 走统一读取", "def cmd_trace" in src and "_trace_records(" in src
       and "_trace_hit_ctx(" in src, "")
    doc = Path(os.path.join(ROOT, "docs", "并行工作.md")).read_text(encoding="utf-8", errors="replace")
    ck("I5 文档 §4.6k 存在", "### 4.6k" in doc and "#38" in doc, "")
    seg = doc.split("### 4.6k")[1].split("## 5. 收口")[0] if "### 4.6k" in doc else ""
    ck("I6 文档写了三档与纯读", all(x in seg for x in ["摘要", "命中", "全文", "纯读"]), seg[:200])

    print("")
    print("通过 %d / 失败 %d" % (_passed, _failed))
    if _failed:
        print("✗ trace 检索有问题")
        return 1
    print("✓ trace 三档检索：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

