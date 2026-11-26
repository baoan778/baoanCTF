#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_role_tool_boundary.py — #45 工具职责边界靠注册表强制

为什么（2026-11-17）：子代理**拿到什么工具 = 它能干什么**。靠系统提示自觉说
「你是只读侦察」是纸防线 —— 真边界是 agent-template/roles/*.json 的 tools 白名单。
退化形态很安静：给只读角色配了 bash/write，或把子代理角色写成 tools=null（全量），
它从此能干任何事，而任务书里还写着「只读」——这就是要机器判死的那种退化。

判据（跑出来的行为）：
  A. 现状注册表干净：roles_lint() 为空，且 ctf-* 角色都有显式白名单。
  B. 白名单读得准：ctf-recon 只读联网（无 bash/write）、ctf-route 有 bash 无 write、
     ctf-wp 有 write/edit、ctf.json 是主会话（tools=null）。
  C. 坏注册表能查出来：子代理角色 tools=null / tools 不是列表 / 缺 name / 空目录。
  D. 派活那一刻核对角色：不存在 rc=2 并列出可用角色；存在则把白名单原文落进登记。
  E. 任务书带工具边界行（--role 时给白名单，未给时提示去给）。
  F. doctor 第 14 段：坏注册表 → 硬问题 rc!=0，提示带文件名。
  G. 静态兜底：PI_FGS_ROLE_DIR / CLI --role / 文档 §4.6q。
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


def fgs(lab, *args, roles=None):
    env = dict(os.environ)
    env["PI_LAB_BOARD_DIR"] = lab
    env["PI_FGS_TOOFAST_SEC"] = "0"
    env["PI_LAB_WORK_DIR"] = os.path.join(lab, "work")   # 别把 work/<pid>/ 落进仓里
    if roles:
        env["PI_FGS_ROLE_DIR"] = roles
    r = subprocess.run([sys.executable, FGS, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=ROOT, env=env, timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def write_role(d, name, obj):
    p = Path(d) / (name + ".json")
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return p


def main():
    lab = tempfile.mkdtemp(prefix="fgsrole-")
    rd = os.path.join(ROOT, "agent-template", "roles")

    # ── A. 现状注册表
    rc, out = fgs(lab, "init", "p1")
    ck("A0 init", rc == 0, out)
    names = sorted(p[:-5] for p in os.listdir(rd) if p.endswith(".json"))
    ck("A1 角色文件 >=8", len(names) >= 8, names)
    ck("A2 三个主力角色在", {"ctf-recon", "ctf-route", "ctf-wp"} <= set(names), names)

    # ── B. 白名单内容
    def tools_of(n):
        return json.loads(Path(rd, n + ".json").read_text(encoding="utf-8")).get("tools")

    recon = tools_of("ctf-recon")
    ck("B1 ctf-recon 只读联网", recon == ["read", "web_search", "fetch_content"], recon)
    ck("B2 ctf-recon 没有 bash", "bash" not in (recon or []), recon)
    route = tools_of("ctf-route")
    ck("B3 ctf-route 有 bash", "bash" in (route or []), route)
    ck("B4 ctf-route 没有 write", "write" not in (route or []), route)
    wp = tools_of("ctf-wp")
    ck("B5 ctf-wp 能写盘", "write" in (wp or []) and "edit" in (wp or []), wp)
    ck("B6 ctf 是主会话全量", tools_of("ctf") is None, tools_of("ctf"))

    # ── D. 派活核对角色
    rc, out = fgs(lab, "step-add", "p1", "摸入口")
    ck("D1 step-add", rc == 0, out)
    rc, out = fgs(lab, "run-register", "p1", "s001", "--task", "分析目标", "--role", "nope")
    ck("D2 未知角色 rc=2", rc == 2, (rc, out))
    ck("D3 列出可用角色", "可用：" in out and "ctf-recon" in out, out)
    rc, out = fgs(lab, "run-register", "p1", "s001", "--task", "分析目标", "--role", "ctf-recon")
    ck("D4 已知角色 rc=0", rc == 0, out)
    ck("D5 打印白名单", "工具白名单（ctf-recon）" in out and "read / web_search" in out, out)
    ck("D6 说明白名单外别指望", "白名单外" in out, out)
    runp = Path(lab) / "p1" / "runs.jsonl"
    recs = [json.loads(x) for x in runp.read_text(encoding="utf-8").splitlines() if x.strip()]
    ck("D7 登记里落了 tools", recs and recs[-1].get("tools") == ["read", "web_search", "fetch_content"],
       recs[-1] if recs else None)

    # ── E. 任务书带工具边界
    rc, out = fgs(lab, "brief", "p1", "--step", "s001", "--as-task", "--role", "ctf-recon")
    ck("E1 brief rc=0", rc == 0, out)
    ck("E2 任务书有白名单行", "工具白名单（ctf-recon）" in out, out[-400:])
    rc, out = fgs(lab, "brief", "p1", "--step", "s001", "--as-task")
    ck("E3 未给 --role 提示去给", "派活时给 --role" in out, out[-400:])

    # ── C. 坏注册表
    bad1 = tempfile.mkdtemp(prefix="roleg-")
    write_role(bad1, "ctf-x", {"name": "x", "tools": None})
    write_role(bad1, "ok", {"name": "ok", "tools": ["read"]})
    rc, out = fgs(lab, "doctor", "p1", roles=bad1)
    ck("C1 子代理角色全量 → 硬问题", rc != 0 and "角色注册表：" in out, out[-600:])
    ck("C2 点名 ctf-x", "ctf-x" in out, out[-600:])

    bad2 = tempfile.mkdtemp(prefix="roleg2-")
    write_role(bad2, "a", {"name": "a", "tools": "read"})
    rc, out = fgs(lab, "doctor", "p1", roles=bad2)
    ck("C3 tools 不是列表 → 报", "tools 不是列表" in out, out[-600:])

    bad3 = tempfile.mkdtemp(prefix="roleg3-")
    write_role(bad3, "b", {"tools": ["read"]})
    rc, out = fgs(lab, "doctor", "p1", roles=bad3)
    ck("C4 缺 name → 报", "缺 name" in out, out[-600:])

    bad4 = tempfile.mkdtemp(prefix="roleg4-")
    rc, out = fgs(lab, "doctor", "p1", roles=bad4)
    ck("C5 空目录 → 报", "角色注册表是空的" in out, out[-600:])

    bad5 = tempfile.mkdtemp(prefix="roleg5-")
    write_role(bad5, "c", {"name": "c", "tools": []})
    rc, out = fgs(lab, "doctor", "p1", roles=bad5)
    ck("C6 空白名单 → 报", "空列表" in out, out[-600:])

    # 好注册表不该报
    okd = tempfile.mkdtemp(prefix="rolegok-")
    write_role(okd, "ctf-route", {"name": "route", "tools": ["read", "bash"]})
    write_role(okd, "ctf", {"name": "main", "tools": None})
    rc, out = fgs(lab, "doctor", "p1", roles=okd)
    ck("C7 合规注册表不报", "角色注册表：" not in out, out[-400:])

    # ── G. 静态兜底
    src = open(FGS, encoding="utf-8").read()
    ck("G1 角色目录可覆盖", 'ROLE_DIR_ENV = "PI_FGS_ROLE_DIR"' in src, "")
    ck("G2 role_tools/role_policy_line 在", "def role_tools(" in src and "def role_policy_line(" in src, "")
    ck("G3 run-register 核对角色", "rtools, rerr = role_tools(role)" in src, "")
    ck("G4 登记落 tools", '"role": a.role, "tools": rtools' in src, "")
    ck("G5 brief 有 --role", 'p.add_argument("--role", default=None,' in src, "")
    ck("G6 doctor 第 14 段", "# 14) 工具职责边界的注册表（#45）" in src, "")
    doc = open(os.path.join(ROOT, "docs", "并行工作.md"), encoding="utf-8").read()
    ck("G7 文档 §4.6q", "4.6q" in doc, "")
    ck("G8 文档讲三档", "全量" in doc and "白名单" in doc, "")

    print("test_role_tool_boundary: " + str(_passed) + " passed, " + str(_failed) + " failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
