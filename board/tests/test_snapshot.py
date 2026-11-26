#!/usr/bin/env python3
"""回归测试：快照的确定性语义哈希（ADR 0004）

跑法：
    python3 board/tests/test_snapshot.py

守的是什么
----------
`fgs snapshot --hash` 给 Worker 一个判断「**我上次读到的图，和现在读到的，是不是同一份**」的依据。
要成立必须满足：**同一语义状态 → 相同字节 → 相同哈希**。

最容易破功的地方是「把非语义的东西也算进哈希」——`revision`（第几次写的计数器）、
时间戳、`dict` 的迭代顺序。任何一条漏进来，哈希就会在语义没变时乱跳，这个信号就废了。

  T1 同状态重复读 → 哈希稳定
  T2 语义变化（加 Fact / 改 step 状态）→ 哈希变
  T3 **只动元数据**（checkpoint 让 revision+1）→ 哈希**不变** ★ 核心
  T4 关系顺序不影响哈希（确定性排序）
  T5 Fact 的结构化 meta 参与语义 → 出现/变化都会改哈希
  T6 --json 里的 semantic_hash 与 --hash 一致
  T7 形状固定（16 位 hex）
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
#   自包含（自己 import sys），不依赖本文件是否已 import sys 或用别名。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
FGS = ROOT / "fgs.py"
PID = "_test_snapshot"

# ★ 隔离：临时项目别建到真 board/projects（否则并行时污染别的测试）
TMPDIR = Path(tempfile.mkdtemp(prefix="pilab-snapshot-"))
PDIR = TMPDIR / "projects" / PID
# ★ 2026-11-12 修隔离：snapshot --ref 走 `_work_dir()`，不设 WORK_DIR 会写真实 work/<pid>
ENV = dict(os.environ, PI_LAB_BOARD_DIR=str(TMPDIR / "projects"),
           PI_LAB_WORK_DIR=str(TMPDIR / "work"))

passed, failed = [], []


def run(*args):
    p = subprocess.run([sys.executable, str(FGS), *args], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=ENV)
    return p.returncode, p.stdout.strip(), p.stderr


def h():
    rc, out, err = run("snapshot", PID, "--hash")
    assert rc == 0, err
    return out


def rev():
    return json.loads((PDIR / "graph.json").read_text(encoding="utf-8"))["revision"]


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail and not cond else ""))


def main():
    shutil.rmtree(TMPDIR, ignore_errors=True)
    rc, _, err = run("init", PID, "--kind", "ctf", "--goal", "语义哈希测试")
    assert rc == 0, err

    print("T1 同状态重复读 → 哈希稳定")
    a, b = h(), h()
    check("两次相同", a == b, f"{a} vs {b}")
    check("形状是 16 位 hex", len(a) == 16 and all(c in "0123456789abcdef" for c in a), a)

    print("T2 语义变化 → 哈希变")
    run("fact-add", PID, "第一条事实", "--confidence", "verified")
    c = h()
    check("加 Fact 后变了", c != a, f"{a} -> {c}")
    run("step-add", PID, "试一下换个入口")
    d = h()
    check("加 Step 后又变了", d != c, f"{c} -> {d}")

    print("T3 只动元数据（revision+1）→ 哈希不变  ★ 核心")
    r_before, e = rev(), h()
    run("checkpoint", PID)                      # 只写 reason_checkpoint（非语义，不进快照）
    r_after, f = rev(), h()
    check("revision 确实变了", r_after > r_before, f"{r_before} -> {r_after}")
    check("哈希没变", e == f, f"{e} vs {f}")

    print("T4 关系顺序不影响哈希（确定性排序）")
    run("fact-add", PID, "第二条事实", "--confidence", "verified")
    run("relate", PID, "--type", "supports", "--from", "f001", "--to", "f002", "--reason", "r1")
    g = h()
    run("relate", PID, "--type", "derived_from", "--from", "f002", "--to", "f001")
    i = h()
    check("加关系后变了", i != g, f"{g} -> {i}")
    check("再读稳定", h() == i)

    print("T5 Fact 的结构化 meta 参与语义")
    run("fact-add", PID, "带 meta 的事实", "--confidence", "verified",
        "--phase", "triage", "--tool", "ss", "--ioc", "IP=203.0.113.9")
    j = h()
    check("加带 meta 的 Fact 后哈希变", j != i, f"{i} -> {j}")
    rc, out, _ = run("snapshot", PID, "--json")
    snap = json.loads(out)
    f3 = [v for k, v in snap["facts"].items() if "带 meta" in v["text"]]
    check("快照里能看到 meta", bool(f3) and f3[0].get("meta", {}).get("phase") == "triage",
          json.dumps(f3, ensure_ascii=False)[:200])

    print("T6 --json 里的 semantic_hash 与 --hash 一致")
    check("一致", snap.get("semantic_hash") == j, f"{snap.get('semantic_hash')} vs {j}")

    print("T7 快照里不带非语义字段（白名单）")
    node = json.loads((PDIR / "graph.json").read_text(encoding="utf-8"))["nodes"]["f001"]
    check("图里有 provenance", "provenance" in node)
    check("快照里没有 provenance", "provenance" not in json.dumps(snap["facts"]["f001"]),
          json.dumps(snap["facts"]["f001"], ensure_ascii=False))
    check("快照里没有 created_at", "created_at" not in json.dumps(snap["facts"]["f001"]))

    shutil.rmtree(TMPDIR, ignore_errors=True)
    print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ 快照语义哈希：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
