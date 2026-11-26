#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_task_templates.py — #46 任务模板化（题型 preset）

为什么（2026-11-17）：同题型反复起题，每次都在重写「目标怎么算写清楚 / 这题型必查
哪些点 / 哪些约束不能忘」。漏一条必查点 = 漏一个攻击面；漏一条约束 = 越权风险。
ARTEX 把它做成 TaskTemplate（Name/NKey/Description/Goal/CategoryID/InterceptRules），
nkey=ToLower 大小写不敏感唯一、List 按 updated_at DESC 最近维护优先。

判据（跑出来的行为）：
  A. 内置模板 5 份（web/pwn/re/crypto/misc）在 templates/presets/ 且能 list/show；
     list 按 updated_at 降序；--json 是合法 JSON 数组。
  B. nkey 大小写不敏感：show WEB 等同 show web。
  C. save 从现成的板抓 kind/goal_rule/scope；重名不带 --force → rc=2；带 --force → rc=0。
  D. kickoff --template 把模板当默认值：kind 来自模板、scope 落到新板、必查点打印；
     --with-steps 才落成 Step；显式 --target/--kind 优先于模板。
  E. rm：本地模板可删；内置模板拒删（rc=2，含「内置模板不删」）；未知模板 rc=2。
  F. 静态兜底：cmd_preset/预设常量/CLI 参数/文档 §4.6m 都在。
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
BUILTIN = Path(ROOT) / "templates" / "presets"

_passed, _failed = 0, 0


def ck(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print("  x " + name + (("   [" + str(detail)[:300] + "]") if detail else ""))


def fgs(lab, pdir, *args):
    env = dict(os.environ)
    env["PI_LAB_BOARD_DIR"] = lab
    env["PI_FGS_TOOFAST_SEC"] = "0"
    env["PI_FGS_PRESET_DIR"] = pdir
    r = subprocess.run([sys.executable, FGS] + list(args), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=ROOT, env=env, timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main():
    lab = tempfile.mkdtemp(prefix="fgs-tpl-")
    pdir = os.path.join(lab, "presets")
    os.makedirs(pdir, exist_ok=True)
    bi = str(BUILTIN)

    # ── A. 内置模板库 ──────────────────────────────────────────────
    rc, out = fgs(lab, bi, "preset", "list")
    ck("A1 list rc=0", rc == 0, out)
    for k in ("web", "pwn", "re", "crypto", "misc"):
        ck("A2 list 含内置 " + k, k in out, out[:200])
    ck("A3 降序（misc 在 web 之前）", out.find("misc") < out.find("· web"), out[:400])
    rc, out = fgs(lab, bi, "preset", "list", "--json")
    try:
        arr = json.loads(out)
        ok_json = isinstance(arr, list) and len(arr) == 5
    except ValueError:
        arr, ok_json = [], False
    ck("A4 --json 是 5 条数组", ok_json, out[:200])
    ck("A5 每条有 nkey/description/checks",
       all(isinstance(x.get("nkey"), str) and "checks" in x for x in arr), arr[:1])
    for k in ("web", "pwn", "re", "crypto", "misc"):
        f = BUILTIN / (k + ".json")
        t = json.loads(f.read_text(encoding="utf-8"))
        ck("A6 内置 " + k + " 有 goal_rule 与 checks", bool(t.get("goal_rule")) and len(t.get("checks") or []) >= 3, t.get("nkey"))
        ck("A7 内置 " + k + " 不带真实目标/范围（不能把别人的资产写进模板）",
           not (t.get("target") or "") and not (t.get("scope") or []), t.get("target"))
        ck("A8 内置 " + k + " source=builtin", t.get("source") == "builtin", t.get("source"))

    # ── B. show 与 nkey 大小写不敏感 ───────────────────────────────
    rc, out = fgs(lab, bi, "preset", "show", "web")
    ck("B1 show rc=0", rc == 0, out)
    ck("B2 打印必查点与约束", "必查点 1." in out and "约束 ·" in out, out[:300])
    ck("B3 打印目标写法", "目标写法" in out, out[:300])
    ck("B4 打印 source=builtin", "source=builtin" in out, out[:200])
    rc2, out2 = fgs(lab, bi, "preset", "show", "WEB")
    ck("B5 show WEB 等同 show web", rc2 == 0 and "必查点 1." in out2, out2[:200])
    rc3, out3 = fgs(lab, bi, "preset", "show", "nope")
    ck("B6 未知模板 rc=2 且列出可用的", rc3 == 2 and "没有模板" in out3 and "web" in out3, out3[:200])

    # ── C. save：从现成的板抓默认值 ────────────────────────────────
    rc, out = fgs(lab, pdir, "init", "src")
    ck("C0 init 源板", rc == 0, out)
    fgs(lab, pdir, "scope", "src", "--set", "a.test,b.test")
    fgs(lab, pdir, "goal", "src", "打 a.test 登录接口拿 flag")
    rc, out = fgs(lab, pdir, "preset", "save", "My Lab", "--from", "src", "--desc", "本地模板")
    ck("C1 save rc=0", rc == 0, out)
    ck("C2 nkey 归一成 my-lab", "my-lab" in out, out)
    f = Path(pdir) / "my-lab.json"
    ck("C3 文件落盘", f.exists(), pdir)
    t = json.loads(f.read_text(encoding="utf-8"))
    ck("C4 抓到 goal_rule", "a.test" in (t.get("goal_rule") or ""), t.get("goal_rule"))
    ck("C5 抓到 scope", (t.get("scope") or []) == ["a.test", "b.test"], t.get("scope"))
    ck("C6 source=local 且不抄步骤", t.get("source") == "local" and (t.get("checks") or []) == [], t)
    rc, out = fgs(lab, pdir, "preset", "save", "my-lab", "--from", "src")
    ck("C7 重名不带 --force rc=2", rc == 2 and "--force" in out, out)
    rc, out = fgs(lab, pdir, "preset", "save", "my-lab", "--from", "src", "--force")
    ck("C8 带 --force 覆盖 rc=0", rc == 0, out)
    rc, out = fgs(lab, pdir, "preset", "save", "x")
    ck("C9 save 缺 --from rc=2", rc == 2 and "--from" in out, out)
    rc, out = fgs(lab, pdir, "preset", "save", "y", "--from", "nope")
    ck("C10 --from 不存在的板 rc=2", rc == 2 and "不存在" in out, out)

    # ── D. kickoff --template ─────────────────────────────────────
    tp = os.path.join(pdir, "pent.json")
    base = json.loads((Path(pdir) / "my-lab.json").read_text(encoding="utf-8"))
    base.update({"nkey": "pent", "kind": "pentest", "target": "tpl.test",
                 "checks": ["检查一", "检查二"]})
    Path(tp).write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")
    rc, out = fgs(lab, pdir, "kickoff", "t1", "--template", "pent")
    ck("D1 kickoff --template rc=0", rc == 0, out)
    ck("D2 kind 来自模板", "题型：pentest" in out, out[:400])
    ck("D3 必查点打印", "必查点 1. 检查一" in out, out[:600])
    ck("D4 未加 --with-steps 不落 Step", "只打印不落盘" in out, out[:600])
    ck("D5 模板 scope 落到新板", "授权范围按模板补了" in out, out[:600])
    rc, out = fgs(lab, pdir, "scope", "t1")
    ck("D6 新板 scope 真写进去了", "a.test" in out and "b.test" in out, out)
    rc, out = fgs(lab, pdir, "kickoff", "t2", "--template", "pent", "--with-steps")
    ck("D7 --with-steps rc=0", rc == 0, out)
    ck("D8 落成 2 个 Step", "已落成 2 个 Step" in out, out[:800])
    rc, out = fgs(lab, pdir, "chain", "t2")
    ck("D9 chain 里能看到必查点 Step", "检查一" in out and "检查二" in out, out)
    rc, out = fgs(lab, pdir, "kickoff", "t3", "--template", "pent", "--kind", "ctf", "--target", "given.test")
    ck("D10 显式 --kind 优先", "题型：ctf" in out, out[:400])
    g = json.loads((Path(lab) / "t3" / "graph.json").read_text(encoding="utf-8"))
    ck("D11 显式 --target 优先于模板", g.get("target") == "given.test", g.get("target"))
    rc, out = fgs(lab, pdir, "kickoff", "t4", "--template", "nope")
    ck("D12 未知模板 rc=2（建板前就拦）", rc == 2 and "没有模板" in out, out)
    ck("D13 拦住了就不该建出半块板", not (Path(lab) / "t4" / "graph.json").exists(), lab)

    # ── E. rm ────────────────────────────────────────────────────
    rc, out = fgs(lab, pdir, "preset", "rm", "pent")
    ck("E1 本地模板可删", rc == 0 and "已删模板 pent" in out, out)
    ck("E2 文件真没了", not Path(tp).exists(), tp)
    rc, out = fgs(lab, bi, "preset", "rm", "web")
    ck("E3 内置模板拒删 rc=2", rc == 2 and "内置模板不删" in out, out)
    ck("E4 内置文件还在", (BUILTIN / "web.json").exists(), bi)

    # ── F. 静态兜底 ───────────────────────────────────────────────
    src = (Path(ROOT) / "board" / "fgs.py").read_text(encoding="utf-8")
    ck("F1 有 preset_dir/preset_key/preset_load/cmd_preset",
       all(x in src for x in ("def preset_dir(", "def preset_key(", "def preset_load(", "def cmd_preset(")))
    ck("F2 CLI 注册了 preset", '"preset", cmd_preset' in src, "missing")
    ck("F3 kickoff 有 --template 与 --with-steps",
       '"--template"' in src and '"--with-steps"' in src)
    ck("F4 kickoff 调用了 _apply_preset", "_apply_preset(a, tpl)" in src)
    docs = Path(ROOT) / "docs" / "并行工作.md"
    dtext = docs.read_text(encoding="utf-8") if docs.exists() else ""
    ck("F5 文档 §4.6m 存在", "4.6m" in dtext, "missing")
    ck("F6 文档写了 nkey 大小写不敏感", "大小写不敏感" in dtext)
    ck("F7 文档写了「不抄步骤」的边界", "不抄" in dtext or "过拟合" in dtext)
    print("")
    print("passed " + str(_passed) + "  failed " + str(_failed))
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())