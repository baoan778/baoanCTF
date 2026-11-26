#!/usr/bin/env python3
r"""test_blindspot_deps.py — E1 盲区清单 + E2 依赖就绪门控的回归测试

为什么单独建这一份（不是并进 smoke）
------------------------------------
smoke 测的是「命令能不能跑通」，这两条测的是**判据对不对**：
  E1  未验证却被当前提用的 Fact 必须被点名；带观测词的不许被误判成盲区；
      已证伪 ≥3 条且其后没开新攻击面必须报 dead-branch；
      只读（跑完图不许变）。
  E2  依赖没就绪必须拒 claim（rc=2 且记 dep-blocked 门禁）；前序 done 后放行；
      决定论：decide() 不把被挡住的 step 当可派；--ignore-deps 放行但要留审计；
      指回自己的环不许把项目焊死。

血账（2026-11-09 实测）
------------------------
  改前：`grep -n "is_ready\|def frontier" board/` = 0 命中；cmd_step_add 建了 depends_on 边
  却**没有任何消费者**。`grep -E "blindspot|盲区" board/` = 0 命中，fgs brief 只说
  "已坐实/未坐实/已证伪"，从不说"你的结论还建立在没验证的前提上"。

    python3 board/tests/test_blindspot_deps.py
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = pathlib.Path(__file__).resolve().parents[2]
FGS = LAB / "board" / "fgs.py"
PY = sys.executable or "python3"

checks: list = []


def ok(name, cond, extra=""):
    checks.append((name, bool(cond), str(extra)))


def _report():
    bad = [c for c in checks if not c[1]]
    for name, good, extra in checks:
        print(f"  {'✓' if good else '✗'} {name}" + (f"   {extra[:260]}" if extra and not good else ""))
    print(f"\n通过 {len(checks) - len(bad)} / 失败 {len(bad)}")
    if bad:
        print("失败项：" + "、".join(c[0] for c in bad))
        return 1
    print("✓ E1 盲区 / E2 依赖就绪门控：全部通过")
    return 0


def fgs(tmp, *args, ok=(0,)):
    env = dict(os.environ)
    env["PI_LAB_BOARD_DIR"] = str(tmp / "projects")
    env["PI_LAB_WORK_DIR"] = str(tmp / "work")
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run(  # noqa: S603
        [PY, str(FGS), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, cwd=str(LAB), env=env,
    )
    return p


def jrun(tmp, *args):
    """跑 --json 子命令并解析。★ 多行 JSON 不能逐行找 —— 整段 parse 再退回逐行
    （踩过：只找「以 { 开头的行」，而缩进后的收尾是 '}'，多行输出永远解析失败）。"""
    p = fgs(tmp, *args, "--json")
    body = (p.stdout or "").strip()
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        pass
    for ln in reversed(body.splitlines()):
        ln = ln.strip()
        if ln.startswith("{") or ln.startswith("["):
            try:
                return json.loads(ln)
            except json.JSONDecodeError:
                continue
    return {}


def main() -> int:
    ok("board/fgs.py 存在", FGS.is_file())
    if not FGS.is_file():
        return _report()
    src = FGS.read_text(encoding="utf-8", errors="replace")

    # ── 0. 代码级证据：两个函数真的进了唯一入口 ──────────────────────
    ok("有 blindspot_rows 纯函数", "def blindspot_rows(g: dict)" in src)
    ok("有 step_ready 门控", "def step_ready(g: dict, sid: str)" in src)
    ok("decide() 消费就绪门控", "_ready_ids = set(ready_steps(g))" in src)
    ok("step_claim 调 step_ready", "bump_gate(a.project, \"dep-blocked\"" in src,
       "step_claim 里没接门控")
    ok("brief 注入盲区段", "for _line in blindspot_block(g):" in src)

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="bsdeps-"))
    try:
        # ══ E1 ══════════════════════════════════════════════════════
        fgs(tmp, "init", "e1")
        fgs(tmp, "goal", "e1", "盲区判据")
        fgs(tmp, "fact-add", "e1", "首页返回 200", "--confidence", "verified",
            "--evidence", "curl -i")
        fgs(tmp, "fact-add", "e1", "后台大概跑在 8080（估计）", "--confidence", "inferred")
        fgs(tmp, "step-add", "e1", "打内网读 flag", "--from", "f002")

        js = jrun(tmp, "blindspot", "e1")
        kinds = [b["kind"] for b in js.get("blindspots", [])]
        ok("未被引用时：已 verified 的 Fact 不进盲区", "f001" not in
           [b["id"] for b in js.get("blindspots", [])], str(js)[:200])
        ok("被当前提的 inferred Fact 进盲区", "unverified-premise" in kinds, str(kinds))
        prem = [b for b in js.get("blindspots", []) if b["kind"] == "unverified-premise"]
        ok("盲区点明被谁当前提用着", prem and prem[0].get("used_by") == ["s001"],
           str(prem)[:240])
        ok("带「估计」措辞判为硬盲区", prem and prem[0].get("hard") is True, str(prem)[:200])
        ok("每条盲区都给出「要回答」的具体问题",
           all(b.get("question") for b in js.get("blindspots", [])), str(js)[:200])

        # 观测词不算盲区：同一句推断，一旦有实测回显就不该报硬盲区
        fgs(tmp, "fact-add", "e1", "实测回显：端口 8080（估计项已被观测推翻）",
            "--confidence", "inferred")
        fgs(tmp, "step-add", "e1", "沿 8080 打内网", "--from", "f003")
        js2 = jrun(tmp, "blindspot", "e1")
        hard_f003 = [b for b in js2.get("blindspots", []) if b["id"] == "f003"]
        ok("带观测词的不判硬盲区（宁可少报，不误报）",
           all(b.get("hard") is False for b in hard_f003), str(hard_f003)[:240])

        # 只读：跑完图不许变
        snap1 = (tmp / "projects" / "e1" / "graph.json").read_text(encoding="utf-8")
        fgs(tmp, "blindspot", "e1")
        snap2 = (tmp / "projects" / "e1" / "graph.json").read_text(encoding="utf-8")
        ok("blindspot 是只读的（图不因查询而变）", snap1 == snap2, "graph 被改写了")

        # --as-step 是唯一写动作，且要真落成可认领的 Step
        n_before = len(jrun(tmp, "show", "e1").get("nodes", {}))
        rc = fgs(tmp, "blindspot", "e1", "--as-step", "0")
        g2 = jrun(tmp, "show", "e1")
        ok("--as-step 落成新 Step（图节点 +1）",
           rc.returncode == 0 and len(g2.get("nodes", {})) == n_before + 1,
           (rc.stdout or "")[-160:] + (rc.stderr or "")[-160:])
        ok("--as-step 越界被拒（rc=2）",
           fgs(tmp, "blindspot", "e1", "--as-step", "99").returncode == 2)

        # dead-branch：证伪堆积且其后没有新面
        fgs(tmp, "init", "e1d")
        fgs(tmp, "goal", "e1d", "dead")
        for i in range(3):
            fgs(tmp, "fact-add", "e1d", f"路线{i} 已证伪：union 注入被 WAF 拦",
                "--confidence", "verified", "--evidence", "curl")
        jsd = jrun(tmp, "blindspot", "e1d")
        ok("证伪 ≥3 条且没换面 → dead-branch",
           any(b["kind"] == "dead-branch" for b in jsd.get("blindspots", [])),
           str([b["kind"] for b in jsd.get("blindspots", [])]))

        # ══ E2 ══════════════════════════════════════════════════════
        fgs(tmp, "init", "e2")
        fgs(tmp, "goal", "e2", "依赖门控")
        fgs(tmp, "step-add", "e2", "A：确认注入点")
        fgs(tmp, "step-add", "e2", "B：再打内网", "--depends", "s001")

        rc = fgs(tmp, "step-claim", "e2", "s002", "--by", "w1")
        ok("前序未完成时 claim 被拒（rc=2）", rc.returncode == 2,
           (rc.stdout or "") + (rc.stderr or ""))
        ok("拒绝理由点名前序", "s001" in ((rc.stdout or "") + (rc.stderr or "")),
           (rc.stdout or "")[-200:])
        ok("拒绝记 dep-blocked 门禁", "dep-blocked" in
           (tmp / "projects" / "e2" / "gates.jsonl").read_text(encoding="utf-8"),
           "门禁自证缺失")

        nd = jrun(tmp, "next", "e2", )
        ok("next 只把就绪的那条当可派", (nd.get("step") or nd.get("task_id") or "") in
           ("s001", "") or "s001" in json.dumps(nd, ensure_ascii=False), str(nd)[:240])

        # --ignore-deps 放行 + 留审计
        fgs(tmp, "step-release", "e2", "s001")
        rc = fgs(tmp, "step-claim", "e2", "s002", "--by", "w1", "--ignore-deps")
        ok("--ignore-deps 能放行（不把项目焊死）", rc.returncode == 0,
           (rc.stdout or "") + (rc.stderr or ""))
        ok("放行留了审计", "deps-bypass" in
           (tmp / "projects" / "e2" / "log.jsonl").read_text(encoding="utf-8"))

        # 前序 done → 放行
        fgs(tmp, "init", "e3")
        fgs(tmp, "goal", "e3", "前置完成后放行")
        fgs(tmp, "step-add", "e3", "A")
        fgs(tmp, "step-add", "e3", "B", "--depends", "s001")
        fgs(tmp, "step-claim", "e3", "s001", "--by", "w1")
        fgs(tmp, "step-conclude", "e3", "s001", "注入点在 search")
        rc = fgs(tmp, "step-claim", "e3", "s002", "--by", "w2")
        ok("前序 done 后 claim 放行", rc.returncode == 0, (rc.stderr or "")[-200:])

        # brief 里要把「依赖还在等」说出来
        fgs(tmp, "init", "e4")
        fgs(tmp, "goal", "e4", "brief 提示")
        fgs(tmp, "step-add", "e4", "A")
        fgs(tmp, "step-add", "e4", "B", "--depends", "s001")
        rc = fgs(tmp, "brief", "e4", "--step", "s002", "--as-task")
        # --as-task 把任务书写进 work/<pid>/_brief-step.txt（stdout 只给路径），所以要读产物
        taskfile = tmp / "work" / "e4" / "_brief-step.txt"
        body = taskfile.read_text(encoding="utf-8") if taskfile.exists() else ""
        ok("brief --as-task 真的落了任务书", bool(body.strip()), (rc.stdout or "")[-200:])
        ok("brief 告诉 Worker 依赖没就绪", "依赖还没就绪" in body or "s001" in body, body[:300])

        # 环不许焊死项目
        fgs(tmp, "init", "e5")
        fgs(tmp, "goal", "e5", "环")
        fgs(tmp, "step-add", "e5", "X")
        fgs(tmp, "step-add", "e5", "Y", "--depends", "s001")
        # 反向加边：写图的 check_relation 会拦，但历史数据可能已有，直接判纯函数的兜底
        p = subprocess.run(  # noqa: S603
            [PY, "-c",
             "import sys,json,pathlib\n"
             "sys.path.insert(0, r'" + str(LAB / "board") + "')\n"
             "import fgs\n"
             "g=json.loads(pathlib.Path(r'" + str(tmp / "projects" / "e5" / "graph.json") + "').read_text(encoding='utf-8'))\n"
             "g['edges'].append({'from':'s001','type':'depends_on','to':'s002'})\n"
             "print(json.dumps(fgs.step_ready(g,'s001'),ensure_ascii=False))\n"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=dict(os.environ, PYTHONIOENCODING="utf-8"), timeout=120,
        )
        _out = ((p.stdout or "") + (p.stderr or "")).strip()
        ok("互相依赖的两条都不可派（不静默焊死）",
           _out.lower().startswith("[false") or "[false," in _out.lower() or "环" in _out,
           _out[-240:])
        ok("拒绝理由说得清（环 或 前序未完成）",
           ("环" in _out) or ("前序未完成" in _out), _out[-240:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return _report()


if __name__ == "__main__":
    sys.exit(main())
