#!/usr/bin/env python3
"""solved 门禁回归：**孤点 Fact 不许授权 solved**（Cairn 对齐）。

跑法：
    python3 board/tests/test_solved_gate.py

为什么要有这个文件
------------------
Cairn 的 reason prompt 要求 `data.complete.from` 必须来自 `Valid facts` ——
即「结论必须挂在图上」。pi-lab 原先的 `fgs solved` 只查了两件事：
fact 存在、confidence 是 verified。于是**一个谁也没引用过的孤立 Fact 就能宣告 solved**，
图就退化成装饰。

2026-09-28 对着 Cairn 源码（`dispatcher/contracts.py: validate_reason_payload`）
做对齐审计时实测：**4 个已有项目的 `solved.by` 到 goal 全都不连通**。
所以补上这道门，并用这个文件把它钉住。

判据（照 Cairn 的语义）：
  * 孤点 fact + verified  → 拒，退出码 3
  * 挂上 `satisfies` 边   → 过
  * 通过**中间 fact 链**到 goal → 过（连通是传递的，不要求直连）
  * 边被 superseded（state != active）→ 不算通路
  * `fgs doctor` 也能抓出「已被写进图的孤儿 solved」（历史数据）

测试**不碰真产物**：一律 `PI_LAB_BOARD_DIR` 指向 `tempfile.mkdtemp()`。
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

BOARD = Path(__file__).resolve().parents[1]
FGS = BOARD / "fgs.py"

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail and not cond else ""))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="pilab-gate-"))
    env = dict(os.environ, PI_LAB_BOARD_DIR=str(tmp / "projects"),
               PI_LAB_WORK_DIR=str(tmp / "work"))
    PID = "gate"

    def fgs(*args, ok=(0,)):
        p = subprocess.run([sys.executable, str(FGS), *args],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=120)
        return p.returncode, p.stdout, p.stderr

    def graph():
        return json.loads((tmp / "projects" / PID / "graph.json").read_text(encoding="utf-8"))

    try:
        print("1) 准备：一块只有 goal + 1 个 verified fact 的黑板")
        rc, out, err = fgs("init", PID, "--kind", "ctf", "--goal", "拿到 flag")
        check("init 成功", rc == 0, err[:200])
        rc, _, err = fgs("fact-add", PID, "flag{GATE_TEST}", "--confidence", "verified")
        check("fact-add 成功", rc == 0, err[:200])

        print("2) 孤点 fact 宣告 solved → 必须拒（退出码 3）")
        rc, out, err = fgs("solved", PID, "--fact", "f001", "--desc", "flag{GATE_TEST}", "--no-lanes", "测试：单命令流程", ok=(0, 3))
        check("退出码是 3（校验失败）", rc == 3, f"rc={rc}")
        both = out + err
        check("报「孤点」并说明原因", "孤点" in both and "Cairn" in both, both[:300])
        check("给出可执行的修法（带 --type/--from/--to）",
              "--type satisfies" in both and "--from f001" in both, both[:300])
        check("图里没有写进 solved", not graph().get("solved"))

        print("3) doctor 也要能抓出**已被写进图的**孤儿 solved（历史数据）")
        g = graph()
        g["solved"] = {"by": "f001", "desc": "flag{GATE_TEST}", "at": "2026-09-28T00:00:00+00:00"}
        (tmp / "projects" / PID / "graph.json").write_text(
            json.dumps(g, ensure_ascii=False, indent=2), encoding="utf-8")
        rc, out, err = fgs("doctor", PID, ok=(0, 1))
        check("doctor 退出码 1", rc == 1, f"rc={rc}")
        check("doctor 指名孤点", "孤点" in out + err, (out + err)[:300])
        g.pop("solved")
        (tmp / "projects" / PID / "graph.json").write_text(
            json.dumps(g, ensure_ascii=False, indent=2), encoding="utf-8")

        print("4) 挂上 satisfies 边 → 必须过")
        rc, _, err = fgs("relate", PID, "--type", "satisfies", "--from", "f001", "--to", "g001")
        check("relate 成功", rc == 0, err[:200])
        rc, _, err = fgs("solved", PID, "--fact", "f001", "--desc", "flag{GATE_TEST}", "--no-lanes", "测试：单命令流程")
        check("加了边之后 solved 成功", rc == 0, err[:200])
        check("solved 确实写进图", (graph().get("solved") or {}).get("by") == "f001")

        print("5) 连通是**传递**的：中间 fact 链也算挂在图上")
        fgs("fact-add", PID, "中间结论：说明 flag 出处", "--confidence", "verified")
        fgs("fact-add", PID, "第二个孤点", "--confidence", "verified")
        fgs("relate", PID, "--type", "derived_from", "--from", "f003", "--to", "f001")
        rc, out, err = fgs("solved", PID, "--fact", "f002", "--desc", "换个依据", "--no-lanes", "测试：单命令流程", ok=(0, 3))
        check("f002→f001→g001 不连通仍被拒（f002 还是孤点）", rc == 3, f"rc={rc}")
        fgs("relate", PID, "--type", "derived_from", "--from", "f002", "--to", "f003")
        rc, _, err = fgs("solved", PID, "--fact", "f002", "--desc", "用中间链", "--no-lanes", "测试：单命令流程")
        check("f002→f003→f001→g001 连通后通过", rc == 0, err[:200])
        rc, out, err = fgs("solved", PID, "--fact", "f001", "--desc", "换个依据", "--no-lanes", "测试：单命令流程", ok=(0, 3))
        check("f001 与 goal 直连（旧的 satisfies 边还在），仍可用", rc == 0, f"rc={rc}")

        print("6) 被 superseded 的边不算通路")
        fgs("fact-add", PID, "第三个孤点", "--confidence", "verified")
        g = graph()
        for e in g["edges"]:
            if e["from"] == "f003" and e["to"] == "f001":
                e["state"] = "superseded"
        (tmp / "projects" / PID / "graph.json").write_text(
            json.dumps(g, ensure_ascii=False, indent=2), encoding="utf-8")
        rc, out, err = fgs("solved", PID, "--fact", "f003", "--desc", "x", "--no-lanes", "测试：单命令流程", ok=(0, 3))
        # f002 还连着 f003，而 f003 --superseded--> f001，所以 f002 仍可达 goal（经自己那条边）
        check("superseded 边不参与连通判定（f003 因此孤立）", rc == 3, f"rc={rc}")

        print("7) 内联快照 / 文件引用两种模式互不干扰")
        rc, path, err = fgs("snapshot", PID, "--ref")
        check("--ref 退出码 0", rc == 0, err[:200])
        p = Path(path.strip())
        check("--ref 只打印一行路径", len(path.strip().splitlines()) == 1, path[:200])
        check("快照文件已生成", p.is_file(), str(p))
        content = p.read_text(encoding="utf-8") if p.is_file() else ""
        check("文件头带 rev + semantic_hash", "semantic_hash=" in content and "rev=" in content)
        check("文件里提醒「不要把正文贴进 prompt」", "不要把正文贴进 prompt" in content)
        rc, out2, _ = fgs("snapshot", PID)
        check("不带 --ref 仍是内联（stdout 里有 '## facts'）", "## facts" in out2, out2[:200])
        rc, h, _ = fgs("snapshot", PID, "--hash")
        check("--hash 仍然只出哈希", len(h.strip()) == 16, h[:100])
        rc, custom, err = fgs("snapshot", PID, "--write", str(tmp / "sub" / "custom.txt"))
        check("--write 可以指定路径（含不存在的父目录）", rc == 0 and (tmp / "sub" / "custom.txt").is_file(), err[:200])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ solved 门禁（结论必须挂在图上）：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
