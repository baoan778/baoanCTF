#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_graph_compress.py — 图压缩：digest 节点 + covers 边 + 可逆展开

为什么（2026-11-17）：baoan 原先只有「归并候选清单」（fgs snapshot 会列出孤点/低置信事实），
但**没有任何折叠动作**，也没有还原路径 —— 建议是给人看的，图照旧膨胀，Worker 的快照越来越贵。
ARTEX 的冷图压缩（agent/coldgraph.go）给了三件事：冷块折叠成 digest、被折成员**保留并标
superseded_by**、渲染时按活性复活（hotAtRender）。

判据（跑出来的行为）：
  A. 折叠：冷节点折成 digest 节点（covers/signature/generation），成员一个字节不删、
     只加 superseded_by；covers 边是 active；show 隐藏成员并给 🗂 段；snapshot 的 facts 里没有它们。
  B. 拒绝：热的（goal/open step/verified fact/刚写过）、不足 K=2、已折叠、节点不存在、折 digest。
  C. 复活：成员被标 verified 之后，渲染自动重现（不因被压过就看不见）。
  D. 还原：expand 删 digest 与 covers 边、清 superseded_by；非 digest / 不存在 rc=2。
  E. 签名：折叠后成员被改 → expand 默认拒 rc=3；--force 才行。
  F. doctor：孤儿 digest / superseded_by 指向不存在 / 可折未折 都报。
  G. covers 不许手连（relate 拒）。
  H. 静态兜底：常量、函数、CLI、schema / 文档、show 的 🗂 段都在。
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


def fgs(lab, *args, env_extra=None):
    env = dict(os.environ)
    env["PI_LAB_BOARD_DIR"] = lab
    env["PI_FGS_TOOFAST_SEC"] = "0"
    env["PI_FGS_COLD_SEC"] = "0"          # 冷一切非结构节点：不靠等 30 分钟
    for k, v in (env_extra or {}).items():
        env[k] = str(v)
    r = subprocess.run([sys.executable, FGS, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=ROOT, env=env, timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def graph(lab, pid="p1"):
    return json.loads(Path(lab, pid, "graph.json").read_text(encoding="utf-8", errors="replace"))


def write_graph(lab, g, pid="p1"):
    Path(lab, pid, "graph.json").write_text(
        json.dumps(g, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fresh(prefix, facts=3):
    lab = tempfile.mkdtemp(prefix=prefix)
    fgs(lab, "kickoff", "p1", "--kind", "ctf", "--goal", "fold 目标", "--target", "lab.test")
    for i in range(1, facts + 1):
        fgs(lab, "fact-add", "p1", "冷事实 " + str(i))
    return lab


def main():
    print("A) 折叠：成员保留、covers 边、渲染隐藏")
    lab = fresh("gcomp-")
    rc, out = fgs(lab, "fold", "p1", "f001", "f002", "--title", "两条冷事实的摘要")
    ck("A1 rc=0", rc == 0, out[:300])
    ck("A2 打印 digest id", "d001" in out, out[:400])
    ck("A3 明说原节点未删", "原节点保留" in out, out[:400])
    ck("A4 给出还原命令", "fgs expand p1 d001" in out, out[:400])
    g = graph(lab)
    d = g["nodes"].get("d001") or {}
    ck("A5 digest 节点在", d.get("type") == "digest", d)
    ck("A6 covers 字段", d.get("covers") == ["f001", "f002"], d.get("covers"))
    ck("A7 signature 16 位", len(d.get("signature") or "") == 16, d.get("signature"))
    ck("A8 generation=1", d.get("generation") == 1, d.get("generation"))
    ck("A9 成员正文还在", g["nodes"]["f001"].get("text") == "冷事实 1", g["nodes"]["f001"])
    ck("A10 成员标 superseded_by", g["nodes"]["f001"].get("superseded_by") == "d001"
       and g["nodes"]["f002"].get("superseded_by") == "d001", g["nodes"]["f001"])
    ce = [e for e in g["edges"] if e.get("type") == "covers"]
    ck("A11 covers 边两条且 active", len(ce) == 2 and all(e.get("state") == "active" for e in ce), ce)
    rc, out = fgs(lab, "show", "p1")
    ck("A12 show 隐藏冷成员", "冷事实 1" not in out and "冷事实 2" not in out, out[:800])
    ck("A13 show 有 🗂 段", "🗂" in out and "d001" in out, out[:800])
    ck("A14 show 标签名一致", "签名 ✓ 一致" in out, out[:800])
    ck("A15 第三条未折的还在", "冷事实 3" in out, out[:800])
    rc, out = fgs(lab, "snapshot", "p1", "--json")
    snap = json.loads(out[out.index("{"):]) if "{" in out else {}
    ck("A16 快照 facts 不含折叠成员", "f001" not in (snap.get("facts") or {}), list((snap.get("facts") or {}).keys()))
    ck("A17 快照有 digests", any(x.get("id") == "d001" for x in (snap.get("digests") or [])), snap.get("digests"))
    ck("A18 快照 digests 带 covers", (snap.get("digests") or [{}])[0].get("covers") == ["f001", "f002"], snap.get("digests"))
    rc, out = fgs(lab, "snapshot", "p1")
    ck("A19 人读快照有 🗂 行", "🗂 d001" in out and "covers 2 条" in out, out[:900])
    h1 = json.loads(fgs(lab, "snapshot", "p1", "--json")[1][fgs(lab, "snapshot", "p1", "--json")[1].index("{"):])

    print("B) 拒绝：热的 / 太少 / 已折叠 / 不存在 / digest")
    rc, out = fgs(lab, "fold", "p1", "g001", "--title", "目标")
    ck("B1 goal 是热的拒 rc=2", rc == 2 and "活跃节点不折叠" in out, (rc, out[:300]))
    rc, out = fgs(lab, "fold", "p1", "f003", "--title", "单个")
    ck("B2 不足 K=2 拒 rc=2", rc == 2 and "不叫压缩" in out, (rc, out[:300]))
    rc, out = fgs(lab, "fold", "p1", "f001", "f002", "--title", "重复折")
    ck("B3 已折叠拒 rc=2", rc == 2 and "已经折过" in out, (rc, out[:300]))
    rc, out = fgs(lab, "fold", "p1", "f999", "--title", "x")
    ck("B4 不存在 rc=2", rc == 2 and "没有这个节点" in out, (rc, out[:300]))
    rc, out = fgs(lab, "fold", "p1", "d001", "f003", "--title", "x")
    ck("B5 digest 不能再折", rc == 2 and "不能再被折进 digest" in out, (rc, out[:300]))
    rc, out = fgs(lab, "fold", "p1", "f003", "h001", "--title", "x")
    ck("B6 缺 --title 被 argparse 拒", rc == 2, (rc, out[:200]))
    rc, out = fgs(lab, "fold", "p1", "--title", "只给标题")
    ck("B7 没给节点被 argparse 拒（rc=2）", rc == 2 and "nodes" in out, (rc, out[:300]))

    print("C) 复活：成员变热后自动重现")
    rc, out = fgs(lab, "fact-add", "p1", "另一条", "--confidence", "verified")
    rc, out = fgs(lab, "show", "p1")
    ck("C0 基线：verified 的直接出现", "另一条" in out, out[:600])
    g = graph(lab)
    g["nodes"]["f001"]["confidence"] = "verified"
    write_graph(lab, g)
    rc, out = fgs(lab, "show", "p1")
    ck("C1 复活的成员重新出现", "冷事实 1" in out, out[:900])
    ck("C2 标出成员已复活", "成员已复活" in out and "f001" in out, out[:900])
    ck("C3 未复活的仍隐藏", "冷事实 2" not in out, out[:900])
    rc, out = fgs(lab, "doctor", "p1")
    ck("C4 doctor 报已复活", "已复活" in out, out[-800:])

    print("D) 还原：expand 清干净")
    lab2 = fresh("gcomp2-")
    fgs(lab2, "fold", "p1", "f001", "f002", "--title", "待还原")
    rc, out = fgs(lab2, "expand", "p1", "d001")
    ck("D1 rc=0", rc == 0, out[:300])
    ck("D2 说明回到 Facts", "回到 Facts" in out, out[:300])
    g = graph(lab2)
    ck("D3 digest 节点没了", "d001" not in g["nodes"], list(g["nodes"]))
    ck("D4 covers 边没了", not [e for e in g["edges"] if e.get("type") == "covers"], g["edges"])
    ck("D5 superseded_by 清掉", "superseded_by" not in g["nodes"]["f001"], g["nodes"]["f001"])
    rc, out = fgs(lab2, "show", "p1")
    ck("D6 show 里又看得见", "冷事实 1" in out and "🗂" not in out, out[:700])
    rc, out = fgs(lab2, "expand", "p1", "f001")
    ck("D7 非 digest rc=2", rc == 2 and "不是 digest" in out, (rc, out[:300]))
    rc, out = fgs(lab2, "expand", "p1", "d999")
    ck("D8 不存在 rc=2", rc == 2, (rc, out[:300]))

    print("E) 签名：折叠后被改过 → 拒还原")
    lab3 = fresh("gcomp3-")
    fgs(lab3, "fold", "p1", "f001", "f002", "--title", "会被改")
    g = graph(lab3)
    g["nodes"]["f002"]["text"] = "被悄悄改过的正文"
    write_graph(lab3, g)
    rc, out = fgs(lab3, "expand", "p1", "d001")
    ck("E1 默认拒 rc=3", rc == 3, (rc, out[:300]))
    ck("E2 报签名变化", "签名" in out and "->" not in out.replace("→", "") or "签名" in out, out[:300])
    ck("E3 提示 --force", "--force" in out, out[:300])
    rc, out = fgs(lab3, "expand", "p1", "d001", "--force")
    ck("E4 --force 能过", rc == 0, (rc, out[:300]))
    ck("E5 强制展开有警告", "签名不符" in out, out[:400])
    g = graph(lab3)
    ck("E6 改后的正文原样保留", g["nodes"]["f002"]["text"] == "被悄悄改过的正文", g["nodes"]["f002"])
    rc, out = fgs(lab3, "doctor", "p1")
    ck("E7 doctor 无硬问题", "没有硬问题" in out or "全部不变量通过" in out, out[-600:])

    print("F) doctor：孤儿摘要 / superseded 悬空 / 可折未折")
    lab4 = fresh("gcomp4-")
    fgs(lab4, "fold", "p1", "f001", "f002", "--title", "孤儿")
    g = graph(lab4)
    g["edges"] = [e for e in g["edges"] if e.get("type") != "covers"]
    write_graph(lab4, g)
    rc, out = fgs(lab4, "doctor", "p1")
    ck("F1 孤儿 digest 提醒", "孤儿 digest" in out, out[-800:])
    g = graph(lab4)
    g["nodes"]["f003"]["superseded_by"] = "d404"
    write_graph(lab4, g)
    rc, out = fgs(lab4, "doctor", "p1")
    ck("F2 悬空 superseded_by 是硬问题", "指向不存在的 d404" in out, out[-800:])
    lab5 = fresh("gcomp5-")
    rc, out = fgs(lab5, "doctor", "p1")
    ck("F3 可折未折会给命令", "可折叠" in out and "fgs fold" in out, out[-800:])

    print("G) covers 不许手连")
    rc, out = fgs(lab2, "relate", "p1", "--type", "covers", "--from", "g001", "--to", "f001")
    ck("G1 rc=2", rc == 2, (rc, out[:300]))
    ck("G2 明说只能 fold 生成", "fgs fold" in out, out[:300])

    print("H) 静态兜底")
    src = Path(FGS).read_text(encoding="utf-8", errors="replace")
    for k in ("DIGEST_TYPE = \"digest\"", "DIGEST_MIN_COVERS = 2", "COLD_SECONDS",
              "def _digest_sig(", "def folded_map(", "def _node_hot(",
              "def cold_candidates(", "def _digest_rows(", "def cmd_fold(", "def cmd_expand("):
        ck("H1 " + k, k in src, "")
    ck("H2 CLI 注册 fold/expand", 'add("fold"' in src and 'add("expand"' in src, "")
    ck("H3 covers 在关系矩阵", '"covers":' in src, "")
    ck("H4 快照 token 算 digests", "'facts', 'digests'" in src, "")
    schema = Path(ROOT, "board", "schema.md").read_text(encoding="utf-8", errors="replace")
    ck("H5 schema 有 digest", "digest" in schema and "covers" in schema, "")
    docs = Path(ROOT, "docs", "并行工作.md").read_text(encoding="utf-8", errors="replace")
    ck("H6 文档 §4.6t", "4.6t" in docs, "")

    print("")
    print("ALL PASSED" if _failed == 0 else ("FAILED " + str(_failed)), "·", _passed, "assertions")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
