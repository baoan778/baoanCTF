#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_constraints_at_creation.py — #51 约束落在创建路径上，不在提示词里

为什么（2026-11-17）：模型在压力下第一批跳过的就是「请记得」。ARTEX 把血缘校验放在
addOneIntent 写入路径（parent_id 必须是已存在的 fact/finding），把「宣布整体完成」这类
权重过大的工具默认设为不绑任何 agent（defaultUnbound）。baoan 已同款，但**纪律要写下来防退化**：
一旦哪天为了方便把校验挪回提示词，门禁就变成了自觉。

判据（跑出来的行为）：
  A. 血缘在写入那一刻被校验：挂到错误类型节点 / 不存在的节点 → 非 0 退出。
  B. verified 但没给 --from → 软提醒（#51）+ 审计 fact.verified-no-source，仍写入。
  C. 给了 --from 的 verified 不产生该审计；非 verified 也不产生。
  D. 「放弃必须写 WHY」仍是硬拒（--why 空串）。
  E. 交卷只能由主会话做：solved-orphan 门禁仍在（孤点 Fact 挡在交卷前）。
  F. 静态兜底：schema.md 的设计原则小节在、四条约束在、本测试被它点名。
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


def audits(lab, pid):
    p = Path(lab) / pid / "log.jsonl"
    out = []
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def main():
    lab = tempfile.mkdtemp(prefix="create51-")
    fgs(lab, "init", "p1", "--kind", "ctf", "--goal", "g")

    print("A) 血缘写入时校验")
    fgs(lab, "step-add", "p1", "打入口")
    rc, out = fgs(lab, "fact-add", "p1", "挂到 step 上", "--from", "s001")
    ck("A1 fact --from step 被拒", rc != 0 and "端点矩阵" in out, out[:200])
    rc, out = fgs(lab, "fact-add", "p1", "挂到不存在的节点", "--from", "f999")
    ck("A2 --from 不存在被拒", rc != 0, out[:200])
    rc, out = fgs(lab, "relate", "p1", "s001", "unknown_node")
    ck("A3 relate 未知节点被拒", rc != 0, out[:300])
    rc, out = fgs(lab, "step-add", "p1", "依赖不存在的 step", "--depends", "s999")
    ck("A4 依赖不存在被拒", rc != 0, out[:200])

    print("B) verified 没来源：软提醒 + 审计")
    rc, out = fgs(lab, "fact-add", "p1", "独立观察", "--confidence", "verified")
    ck("B1 仍写入 rc=0", rc == 0, out[:200])
    ck("B2 软提醒点名 #51 与 --from", "#51" in out and "--from" in out, out[:400])
    ck("B3 明说软提醒不拦", "不拦" in out, out[:400])
    keys = [x.get("op") for x in audits(lab, "p1")]
    ck("B4 审计 fact.verified-no-source", "fact.verified-no-source" in keys, keys[-6:])

    print("C) 给了来源 / 不是 verified 都不触发")
    rc, out = fgs(lab, "fact-add", "p1", "有来源的结论", "--confidence", "verified", "--from", "f001")
    ck("C1 有 --from 写入成功", rc == 0, out[:300])
    ck("C2 不出现 #51 提醒", "#51" not in out, out[:300])
    rc, out = fgs(lab, "fact-add", "p1", "只是个怀疑", "--confidence", "inferred")
    ck("C3 inferred 不触发", rc == 0 and "#51" not in out, out[:300])
    n = len([x for x in audits(lab, "p1") if x.get("op") == "fact.verified-no-source"])
    ck("C4 审计只记了 B 段那一条", n == 1, n)

    print("D) 放弃必须写 WHY（硬拒）")
    fgs(lab, "step-claim", "p1", "s001", "--by", "w1")
    rc, out = fgs(lab, "step-abandon", "p1", "s001", "--wh""y", "")
    ck("D1 空 WHY 被拒", rc != 0 and "--why" in out, out[:300])
    rc, out = fgs(lab, "step-abandon", "p1", "s001", "--reason", "timeout", "--wh""y", "时间到")
    ck("D2 写了 WHY 就能放弃", rc == 0, out[:300])

    print("E) 交卷门禁：孤点 Fact 挡在 solved 前")
    rc, out = fgs(lab, "solved", "p1", "--fact", "f002", "--no-lanes", "冒烟")
    ck("E1 孤点不能授权 solved", rc != 0, out[:400])
    src = open(FGS, encoding="utf-8").read()
    ck("E2 门禁名还在", "solved-orphan" in src, "")

    print("F) 静态兜底：设计原则写下来了")
    sch = open(os.path.join(ROOT, "board", "schema.md"), encoding="utf-8").read()
    ck("F1 小节标题在", "### 设计原则：约束落在创建路径上，不在提示词里" in sch, "")
    for i, kw in enumerate(("check_relation", "fact.verified-no-source", "step-abandon --why",
                            "defaultUnbound"), 1):
        ck("F2.%d 约束写了 %s" % (i, kw), kw in sch, "")
    ck("F3 本测试被 schema 点名防退化", "test_constraints_at_creation.py" in sch, "")
    ck("F4 审计键在代码里", '"fact.verified-no-source"' in src, "")

    print()
    print("test_constraints_at_creation: %d passed, %d failed" % (_passed, _failed))
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
