#!/usr/bin/env python3
"""S9 回归：**逐节点**降级（坏一 node 不整图隔离、不抛 traceback）。

    python3 board/tests/test_node_degrade.py

为什么必须有这个文件：`test_degrade.py` 测的是**图级**降级（整份 JSON 坏 → 空图）。
2026-11-02 实测发现真正致命的是**节点级**：JSON 能解析、个别节点坏，此时
  ① 不走任何降级分支；② `fgs show` 直接 TypeError/KeyError 吐 traceback；
  ③ 不产生任何隔离产物 —— 轻伤比重伤更致命。

补上的不变量（逐条断言）：
  A. 各种节点损坏形态下，show/next 都**不吐 traceback**且 rc 有限
  B. 坏节点被**隔离并告警**（stderr 有「已隔离」+ 指路 `fgs audit`）
  C. `fgs audit` 能列出坏节点，**rc=1**（不是永远 0 —— 那等于没有判据）
  D. 坏节点**不落盘**成图字段（`_nodes_bad` 不许进 graph.json）
  E. 坏节点数据**不丢**（另存 nodes-quarantined-*.json）
  F. 隔离后新节点**不复用**坏 id（否则审计流水与隔离文件对不上号）
  G. 干净图 audit rc=0（不能把好的判坏 —— 假阳性比漏报更毁信任）
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

FGS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fgs.py")
passed, failed = [], []

# 各种"损坏形态"：键是名字，值是变异函数（拿到 graph dict，就地改坏 f001）
SHAPES = {
    "不是对象": lambda g: g["nodes"].__setitem__("f001", "not-a-dict"),
    "缺 type": lambda g: g["nodes"]["f001"].pop("type", None),
    "缺 id": lambda g: g["nodes"]["f001"].pop("id", None),
    "缺 text": lambda g: g["nodes"]["f001"].pop("text", None),
    "text 为 null": lambda g: g["nodes"]["f001"].__setitem__("text", None),
    "text 为空串": lambda g: g["nodes"]["f001"].__setitem__("text", "   "),
    "id 与键不一致": lambda g: g["nodes"]["f001"].__setitem__("id", "f999"),
    "type 非法": lambda g: g["nodes"]["f001"].__setitem__("type", "bogus"),
}


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail and not cond else ""))


def run(lab, *args):
    env = dict(os.environ)
    env["PI_LAB_BOARD_DIR"] = lab
    p = subprocess.run([sys.executable, FGS, *args], capture_output=True, env=env, timeout=120)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


def make(lab, pid, shape=None):
    """建一个带 1 个 goal + 1 个 fact 的项目，可选地把 f001 改坏。"""
    run(lab, "init", pid, "--goal", "降级测试")
    run(lab, "fact-add", pid, "原有事实", "--confidence", "verified")
    if shape:
        gp = Path(lab, pid, "graph.json")
        g = json.loads(gp.read_text(encoding="utf-8"))
        SHAPES[shape](g)
        gp.write_text(json.dumps(g, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="node-degrade-")
    try:
        print("0) 干净图不能被判坏（假阳性的代价最贵）")
        make(tmp, "clean")
        rc, out = run(tmp, "audit", "clean")
        check("干净图 audit rc=0", rc == 0, f"rc={rc} :: {out[:200]}")
        check("干净图 audit 说合法", "合法" in out, out[:200])
        rc, out = run(tmp, "show", "clean")
        check("干净图 show rc=0", rc == 0, f"rc={rc}")

        print("1) 八种节点损坏形态：都不许吐 traceback")
        for i, shape in enumerate(SHAPES):
            pid = f"s{i}"
            make(tmp, pid, shape)
            for cmd in ("show", "next"):
                rc, out = run(tmp, cmd, pid)
                check(f"[{shape}] {cmd} 不吐 traceback", "Traceback" not in out, out[-300:])
                check(f"[{shape}] {cmd} rc 有限", rc in (0, 1, 2, 3), f"rc={rc}")
            rc, out = run(tmp, "show", pid)
            check(f"[{shape}] 有「已隔离」警告", "已隔离" in out, out[:250])
            check(f"[{shape}] 警告指路 fgs audit", "fgs audit" in out, out[:250])

        print("2) audit 必须真的报出来（rc=1，不是永远 0）")
        for i, shape in enumerate(SHAPES):
            pid = f"a{i}"
            make(tmp, pid, shape)
            rc, out = run(tmp, "audit", pid)
            check(f"[{shape}] audit rc=1", rc == 1, f"rc={rc} :: {out[:150]}")
            check(f"[{shape}] audit 指名 f001", "f001" in out, out[:200])
            rc, out = run(tmp, "audit", pid, "-v")
            check(f"[{shape}] audit -v 打原值", "原值" in out, out[:200])

        print("3) 坏节点不落盘、不丢、不复用 id")
        make(tmp, "persist", "缺 text")
        rc, out = run(tmp, "fact-add", "persist", "新事实", "--confidence", "verified")
        check("写操作能成功（图仍可用）", rc == 0, f"rc={rc} :: {out[:200]}")
        gp = Path(tmp, "persist", "graph.json")
        g = json.loads(gp.read_text(encoding="utf-8"))
        check("★ _nodes_bad 没被写进 graph.json", "_nodes_bad" not in g,
              f"顶层键={sorted(g)}")
        check("★ _edges_bad 没被写进 graph.json", "_edges_bad" not in g)
        check("★ 坏节点数据没丢（另存 quarantined）",
              len(list(Path(tmp, "persist").glob("nodes-quarantined-*.json"))) == 1,
              str([p.name for p in Path(tmp, "persist").iterdir()]))
        qf = list(Path(tmp, "persist").glob("nodes-quarantined-*.json"))[0]
        qd = json.loads(qf.read_text(encoding="utf-8"))
        check("★ 隔离文件里含坏节点原值", "f001" in qd.get("_nodes_bad", {}),
              str(list(qd.get("_nodes_bad", {}))[:3]))
        check("★ 新节点不复用坏 id f001", "f001" not in g["nodes"],
              f"节点={sorted(g['nodes'])}")
        check("新节点是 f002", "f002" in g["nodes"], f"节点={sorted(g['nodes'])}")

        print("4) 边指向坏节点时也要摘（否则遍历边会 KeyError）")
        run(tmp, "init", "edgecase", "--goal", "边测试")
        run(tmp, "fact-add", "edgecase", "事实A", "--confidence", "verified")
        run(tmp, "fact-add", "edgecase", "事实B", "--confidence", "verified")
        run(tmp, "relate", "edgecase", "g001", "f002", "--rel", "supports") \
            if False else None       # relate 的参数形状不确定，直接用人工边更可靠
        gp = Path(tmp, "edgecase", "graph.json")
        g = json.loads(gp.read_text(encoding="utf-8"))
        g["edges"].append({"from": "f001", "to": "g001", "type": "supports",
                           "state": "active"})
        g["nodes"]["f001"].pop("text", None)          # f001 变坏
        gp.write_text(json.dumps(g, ensure_ascii=False, indent=2), encoding="utf-8")
        rc, out = run(tmp, "show", "edgecase")
        check("坏节点+其边 下 show 不崩", "Traceback" not in out, out[-300:])
        check("坏边也告警", "边" in out and "隔离" in out, out[:300])
        rc, out = run(tmp, "audit", "edgecase")
        check("audit 报坏边", "坏边" in out, out[:300])
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failed:
        print(f"通过 {len(passed)} / 失败 {len(failed)}")
        print("失败项：" + "、".join(failed[:10]))
        return 1
    print(f"通过 {len(passed)} / 失败 0")
    print("✓ 逐节点降级（S9）：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
