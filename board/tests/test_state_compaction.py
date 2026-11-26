#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_state_compaction.py — 图压缩纵深三纪律（活性重查 / 指纹复用 / 摘要可回溯）

为什么（2026-11-17）：STATE 是压缩交接的唯一真相，但它原先每次都无条件重画，三个缺口——
① 不看活性：已经没人追的冷事实和正在追的链挤在同一张表里，读的人分不清哪条还在用；
   （ARTEX agent/compaction.go 在 commit 前重查成员活性、coldgraph.go 有块溶解阈值）
② 不看内容：图没变也重新推导一遍，接任者不知道「上轮结论还有效」；
   （ARTEX 用 signature 复用：内容没变就直接用旧 digest，零 LLM 调用）
③ 不可回溯：折叠掉的事实没有 id，折完就查不回来。

判据（跑出来的行为）：
  A. 指纹：首次记「首次记录」；图上没变时第二次「与上次相同」；图变了才「与上次不同」。
  B. 旁挂：<lab>/<pid>/.state-digest 存在、内容与 --digest 打印一致，且**不进 graph.json**。
  C. 活性：在追的链（open s002 ← done s001）产出的 f001 进 §3 表并带血缘 ← s001；冷事实折叠但留 id。
  D. 阈值：PI_FGS_STATE_FOLD_MIN 调大后冷的全列不折（折 3 行不如多列一行）。
  E. 租约：PI_FGS_STEP_TTL=-1 时 §1 标「租约过期」，默认 TTL 不标。
  F. 静态兜底：常量、摘要纪律行、文档 §4.6i、模板纪律块都在。
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
    for k, v in (env_extra or {}).items():
        env[k] = str(v)
    r = subprocess.run([sys.executable, FGS, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=ROOT, env=env, timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main():
    lab = tempfile.mkdtemp(prefix="scomp-")
    fgs(lab, "kickoff", "p1", "--kind", "ctf", "--goal", "g", "--target", "lab.test")
    fgs(lab, "step-add", "p1", "打 /api/login 拿 session", "--target", "lab.test")

    print("A) 指纹只跟图变")
    rc, out = fgs(lab, "checkpoint", "p1", "--state")
    ck("A1 --state rc=0", rc == 0, out[:200])
    ck("A2 首次记录", "首次记录" in out, out[:600])
    ck("A3 打完指纹写旁挂", (Path(lab, "p1", ".state-digest")).exists(), lab)
    rc, out = fgs(lab, "checkpoint", "p1", "--state")
    ck("A4 图没变就与上次相同", "与上次相同" in out, out[:600])
    rc, out = fgs(lab, "checkpoint", "p1", "--digest")
    ck("A5 --digest rc=0", rc == 0, out[:200])
    parts = out.strip().splitlines()[0].split("\t")
    ck("A6 --digest 三列", len(parts) == 3, parts)
    dg = parts[0]
    ck("A7 指纹是 12 位 hex", len(dg) == 12 and all(c in "0123456789abcdef" for c in dg), dg)
    ck("A8 与旁挂一致", Path(lab, "p1", ".state-digest").read_text(encoding="utf-8").strip() == dg, dg)
    fgs(lab, "fact-add", "p1", "登录页无验证码", "--confidence", "verified")
    rc, out = fgs(lab, "checkpoint", "p1", "--digest")
    ck("A9 图变了就不同", "与上次不同" in out, out[:300])
    graph = Path(lab, "p1", "graph.json").read_text(encoding="utf-8", errors="replace")
    ck("A10 指纹不进 graph.json", ".state-digest" not in graph and dg not in graph, "graph 里不该有指纹")

    print("B) 活性重查：活的不折、冷的留 id")
    lab2 = tempfile.mkdtemp(prefix="scomp2-")
    fgs(lab2, "kickoff", "p2", "--kind", "ctf", "--goal", "g", "--target", "lab.test")
    fgs(lab2, "step-add", "p2", "第一步", "--target", "lab.test")
    fgs(lab2, "step-claim", "p2", "s001", "--by", "w1")
    fgs(lab2, "step-conclude", "p2", "s001", "拿到 session")
    fgs(lab2, "step-add", "p2", "第二步", "--target", "lab.test", "--depends", "s001")
    for i in range(2, 10):
        fgs(lab2, "fact-add", "p2", "冷事实 " + str(i), "--confidence", "verified")
    rc, out = fgs(lab2, "checkpoint", "p2", "--state", env_extra={"PI_FGS_STATE_ACTIVE_MIN": "0"})
    ck("B1 rc=0", rc == 0, out[:200])
    sec3 = out.split("## 3. Intel log")[1].split("## 4.")[0] if "## 3. Intel log" in out else ""
    ck("B2 活链产出的事实进表", "拿到 session" in sec3, sec3[:600])
    ck("B3 带血缘 ← s001", "← s001" in sec3, sec3[:600])
    ck("B4 写「永不折叠」", "永不折叠" in out, out[:900])
    ck("B5 冷事实折叠但点名", "另有" in sec3 and "折叠" in sec3, sec3[:900])
    ck("B6 折叠行留 id", "f002" in sec3 or "f00" in sec3, sec3[:900])
    rc, out = fgs(lab2, "checkpoint", "p2", "--state",
                  env_extra={"PI_FGS_STATE_ACTIVE_MIN": "0", "PI_FGS_STATE_FOLD_MIN": "50"})
    sec3 = out.split("## 3. Intel log")[1].split("## 4.")[0] if "## 3. Intel log" in out else ""
    ck("B7 阈值调大后全列不折", "另有" not in sec3, sec3[:900])
    ck("B8 全列时冷事实在", "冷事实" in sec3, sec3[:900])

    print("C) 租约过期标警")
    fgs(lab2, "step-claim", "p2", "s002", "--by", "w2")
    rc, out = fgs(lab2, "checkpoint", "p2", "--state")
    ck("C1 默认 TTL 不标过期", "租约过期" not in out, out[:900])
    ck("C1b 认作在跑的", "claimed" in out, out[:900])
    rc, out = fgs(lab2, "checkpoint", "p2", "--state", env_extra={"PI_FGS_STEP_TTL": "-1"})
    ck("C2 TTL 过期时标警", "租约过期" in out, out[:900])
    ck("C3 只标警不自动释放", "claimed" in out, out[:900])

    print("D) 静态兜底")
    src = Path(FGS).read_text(encoding="utf-8", errors="replace")
    ck("D1 常量在", "STATE_FOLD_MIN" in src and "STATE_DIGEST_NAME" in src, "")
    ck("D2 指纹不含时间戳（只算结构）", "刻意不算 created_at" in src.split("def _state_digest(g")[1].split("def _state_digest_read")[0], "")
    ck("D3 摘要纪律表尾", "摘要纪律" in src, "")
    doc = Path(ROOT, "docs", "并行工作.md").read_text(encoding="utf-8", errors="replace")
    ck("D4 文档 §4.6i", "4.6i 压缩纵深三纪律" in doc, "")
    ck("D5 文档写折叠阈值", "PI_FGS_STATE_FOLD_MIN" in doc, "")
    ck("D6 文档写诚实边界", "不是省 token" in doc, "")
    tpl = Path(ROOT, "templates", "commander-state.md").read_text(encoding="utf-8", errors="replace")
    ck("D7 模板纪律块", "渲染器已实现的纪律" in tpl and "永不折叠" in tpl, "")
    ck("D8 模板提到 --digest", "--digest" in tpl, "")

    print("")
    print(("OK" if _failed == 0 else "FAIL") + "  " + str(_passed) + " passed, " + str(_failed) + " failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
