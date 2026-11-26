#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_steer_kill.py — steer 纠偏通道 + kill 止损拒写

为什么（2026-11-17）：baoan 过去只有 fgs steer —— 它**生成一份转向简报**，人还得手工
steer_subagent 发过去，图里只数 step.steer 审计。三路并行时一路走偏只能杀掉重派，
那一路的进展全丢；而「杀掉」本身也没有语义（abandoned 混装证伪/超时/kill）。
更糟的是 kill 只改图、杀不掉进程：被打断的子代理可能已经交出一条 conclude，
若那条还能入图，图里就出现「已经止损的一路又交了一份结论」。

判据（跑出来的行为）：
  A. steer-send 入队到 <proj>/steer.jsonl；队列视图/--json/workers 计数一致；空消息拒收。
  B. steer-drain 严格 FIFO（st0001 → st0002），消费写审计 step.steer.drain；
     取空时明说「暂无」，rc=0（不能长得像取到了）。
  C. done / abandoned 的 Step 拒收纠偏（rc=2、指向 step-reopen）。
  D. 积压上限是**每个 Step** 50 条（静态常量 + 文案）。
  E. step-kill 只杀这一路：两路 claimed 时 kill 一路，另一路仍 claimed；--why 必填；
     done 拒；重复 kill 拒；审计 step.kill 带 why/worker。
  F. 止损后迟到写入双拒：publish rc=3；先 publish 后 kill 再 apply 被拒且图未改动。
  G. step-reopen 可直接续（不需要 --force-refuted），重开后能投并 apply。
  H. chain 标 ⛔、STATE Parked 标「止损/不是证伪」、next 说「前序只是没跑完（可 step-reopen 续上）」。
  I. 静态兜底：常量 / 四个新函数 / 四个 CLI 子命令 / schema / docs §4.6s / 任务书 drain 那句。
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
BT = chr(96)

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


def node(lab, pid, sid):
    g = json.loads((Path(lab) / pid / "graph.json").read_text(encoding="utf-8"))
    return g, g["nodes"][sid]


def audit_ops(lab, pid, op):
    p = Path(lab) / pid / "log.jsonl"
    if not p.is_file():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            ev = json.loads(ln)
        except Exception:
            continue
        if ev.get("op") == op:
            out.append(ev)
    return out


def steer_items(lab, pid):
    p = Path(lab) / pid / "steer.jsonl"
    if not p.is_file():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except Exception:
            pass
    return out


def publish(lab, pid, payload, by="w1"):
    return fgs(lab, "publish", pid, "--by", by, "--op", json.dumps(payload, ensure_ascii=False))


def main():
    # ── A/B：入队、队列视图、FIFO 取走 ─────────────────────────────────
    lab = tempfile.mkdtemp(prefix="steer-")
    fgs(lab, "kickoff", "p1", "--kind", "ctf", "--goal", "拿 flag 并提交成功", "--target", "lab.test")
    fgs(lab, "step-add", "p1", "试 /login 注入", "--target", "lab.test")
    fgs(lab, "step-claim", "p1", "s001", "--by", "w1")

    print("A) 入队 + 队列视图")
    rc, out = fgs(lab, "steer-send", "p1", "s001", "--text", "切到 /api/user 的 id 越权", "--by", "lead")
    ck("A1 rc=0", rc == 0, out[:300])
    ck("A2 打印入队 id 与待消费条数", "已入队 st0001" in out and "第 1 条待消费" in out, out[:300])
    ck("A3 steer.jsonl 落在项目目录", (Path(lab) / "p1" / "steer.jsonl").is_file(), "")
    rc, out = fgs(lab, "steer-send", "p1", "s001", "--text", "第二条：试 upload 接口", "--by", "lead")
    ck("A4 第二条 rc=0 且计数+1", rc == 0 and "第 2 条待消费" in out, out[:300])
    rc, out = fgs(lab, "steer-queue", "p1")
    ck("A5 队列人读", rc == 0 and "待消费 2 / 共 2" in out, out[:400])
    ck("A6 队列带正文", "id 越权" in out and "upload" in out, out[:600])
    rc, outj = fgs(lab, "steer-queue", "p1", "--json")
    j = json.loads(outj[outj.index("{"):])
    ck("A7 --json 结构", j.get("total") == 2 and len(j.get("pending") or []) == 2, j)
    rc, out = fgs(lab, "workers", "p1")
    ck("A8 workers 显示待取条数", "待取纠偏 2 条" in out, out[:600])
    rc, out = fgs(lab, "steer-send", "p1", "s001", "--text", "   ")
    ck("A9 空消息拒收 rc=2", rc == 2 and "不能空" in out, out[:300])

    print("B) drain 严格 FIFO + 审计 + 空读措辞")
    rc, out = fgs(lab, "steer-drain", "p1", "s001", "--by", "w1")
    ck("B1 第一条 rc=0 且是 st0001", rc == 0 and "【纠偏 st0001】" in out, out[:300])
    ck("B2 拿到的是第一条正文", "id 越权" in out and "upload" not in out, out[:400])
    ck("B3 提示按它改方向", "按它改方向重规划" in out, out[:600])
    rc, out = fgs(lab, "steer-drain", "p1", "s001", "--by", "w1")
    ck("B4 第二条是 st0002（FIFO 不是 LIFO）", "st0002" in out and "upload" in out, out[:300])
    rc, out = fgs(lab, "steer-drain", "p1", "s001", "--by", "w1")
    ck("B5 取空 rc=0", rc == 0, out[:200])
    ck("B6 空读明说暂无", "暂无纠偏消息" in out and "按原方向继续" in out, out[:300])
    drains = audit_ops(lab, "p1", "step.steer.drain")
    ck("B7 消费写审计 step.steer.drain 两条", len(drains) == 2, [d.get("msg") for d in drains])
    ck("B8 审计带 msg id 与 by", [d.get("msg") for d in drains] == ["st0001", "st0002"]
       and all(d.get("actor") == "w1" for d in drains), drains[-1:])
    items = steer_items(lab, "p1")
    ck("B9 队列落盘状态 consumed", len(items) == 2 and all(i.get("state") == "consumed" for i in items), items)
    ck("B10 消费记 consumed_by", all(i.get("consumed_by") == "w1" for i in items), items)
    rc, out = fgs(lab, "steer-queue", "p1")
    ck("B11 取完队列显示 待消费 0", "待消费 0 / 共 2" in out, out[:300])
    rc, out = fgs(lab, "workers", "p1")
    ck("B12 workers 计数归零", "待取纠偏 0 条" in out, out[:600])

    print("C) done / 不存在的 Step 拒收纠偏")
    lab2 = tempfile.mkdtemp(prefix="steer-done-")
    fgs(lab2, "kickoff", "p2", "--kind", "ctf", "--goal", "拿 flag", "--target", "lab.test")
    fgs(lab2, "step-add", "p2", "已完成那步", "--target", "lab.test")
    fgs(lab2, "step-claim", "p2", "s001", "--by", "w1")
    publish(lab2, "p2", {"op": "conclude", "step": "s001", "fact": "登录绕过已确认"})
    rc, out = fgs(lab2, "apply", "p2")
    ck("C1 前置：这一步已 done", rc == 0 and node(lab2, "p2", "s001")[1].get("status") == "done", out[:300])
    rc, out = fgs(lab2, "steer-send", "p2", "s001", "--text", "换个面")
    ck("C2 done 的 Step 拒收 rc=2", rc == 2 and "step-reopen" in out, out[:300])
    rc, out = fgs(lab2, "steer-send", "p2", "s999", "--text", "换个面")
    ck("C3 不存在的 Step rc=2", rc == 2, out[:300])
    ck("C4 拒收不落队列", not steer_items(lab2, "p2"), steer_items(lab2, "p2"))

    print("D) 积压上限是每步 50（静态 + 文案）")
    src = Path(FGS).read_text(encoding="utf-8", errors="replace")
    ck("D1 STEER_MAX_PENDING = 50", "STEER_MAX_PENDING = 50" in src, "")
    ck("D2 超限报「该止损」不是「再加一条」", "说明该止损" in src, "")
    ck("D3 上限按 Step 计（用该步的 pending 数比较）",
       "len(pend) >= STEER_MAX_PENDING" in src, "")

    print("E) step-kill 只杀这一路")
    lab3 = tempfile.mkdtemp(prefix="kill-")
    fgs(lab3, "kickoff", "p3", "--kind", "ctf", "--goal", "拿 flag", "--target", "lab.test")
    fgs(lab3, "step-add", "p3", "一路：打 /api/login", "--target", "lab.test")
    fgs(lab3, "step-add", "p3", "二路：打 /api/upload", "--target", "lab.test")
    fgs(lab3, "step-claim", "p3", "s001", "--by", "w1")
    fgs(lab3, "step-claim", "p3", "s002", "--by", "w2")
    rc, out = fgs(lab3, "step-kill", "p3", "s001")
    ck("E1 没 --why 拒收 rc=2", rc == 2 and "why" in out.lower(), out[:300])
    rc, out = fgs(lab3, "step-kill", "p3", "s001", "--why", "预算烧完，且已换面")
    ck("E2 kill rc=0", rc == 0, out[:400])
    ck("E3 打印只止损这一路", "只止损这一路" in out, out[:400])
    ck("E4 提示真去 interrupt", "interrupt" in out, out[:600])
    ck("E5 提示迟到写入被拒", "迟到写入已拒收" in out, out[:600])
    ck("E6 提示 step-reopen", "step-reopen p3 s001" in out, out[:800])
    g3, s1 = node(lab3, "p3", "s001")
    ck("E7 s001 = abandoned/killed", s1.get("status") == "abandoned"
       and s1.get("abandon_reason") == "killed", s1.get("abandon_reason"))
    ck("E8 kill 记 WHY", "换面" in (s1.get("abandon_why") or ""), s1.get("abandon_why"))
    ck("E9 另一路仍 claimed（不连坐）", g3["nodes"]["s002"].get("status") == "claimed",
       g3["nodes"]["s002"].get("status"))
    ck("E10 另一路 owner 未变", g3["nodes"]["s002"].get("claimed_by") == "w2", "")
    kills = audit_ops(lab3, "p3", "step.kill")
    ck("E11 审计 step.kill 带 why/worker", len(kills) == 1 and "换面" in (kills[0].get("why") or "")
       and kills[0].get("worker") == "w1", kills[-1:])
    rc, out = fgs(lab3, "step-kill", "p3", "s001", "--why", "again")
    ck("E12 重复 kill 拒收 rc=2", rc == 2 and "abandoned" in out, out[:300])
    rc, out = fgs(lab3, "step-kill", "p3", "s999", "--why", "x")
    ck("E13 不存在的 Step rc=2", rc == 2, out[:200])
    rc, out = fgs(lab3, "steer-send", "p3", "s001", "--text", "再试一下")
    ck("E14 killed 的 Step 拒收纠偏 rc=2", rc == 2 and "没意义" in out, out[:300])

    print("F) 止损后迟到写入双拒")
    rc, out = publish(lab3, "p3", {"op": "conclude", "step": "s001", "fact": "迟到的结论"})
    ck("F1 publish 迟到结论 rc=3", rc == 3, out[:400])
    ck("F2 报「已止损」并指向 step-reopen", "已止损" in out and "step-reopen" in out, out[:500])
    lab4 = tempfile.mkdtemp(prefix="late-")
    fgs(lab4, "kickoff", "p4", "--kind", "ctf", "--goal", "拿 flag", "--target", "lab.test")
    fgs(lab4, "step-add", "p4", "先投后杀那一步", "--target", "lab.test")
    fgs(lab4, "step-claim", "p4", "s001", "--by", "w1")
    rc, out = publish(lab4, "p4", {"op": "conclude", "step": "s001", "fact": "先投后杀的结论"})
    ck("F3 前置：publish 成功 rc=0", rc == 0, out[:300])
    fgs(lab4, "step-kill", "p4", "s001", "--why", "投完之后才决定止损")
    rc, out = fgs(lab4, "apply", "p4")
    ck("F4 kill 后 apply 拒收（rc!=0）", rc != 0, out[:400])
    ck("F5 报「已止损」", "已止损" in out, out[:500])
    g4, _ = node(lab4, "p4", "s001")
    facts4 = [n for n in g4["nodes"].values() if n.get("type") == "fact"
              and "先投后杀" in json.dumps(n, ensure_ascii=False)]
    ck("F6 图里没混进那条迟到结论", not facts4, facts4)

    print("G) reopen 可直接续并重新入图")
    rc, out = fgs(lab3, "step-reopen", "p3", "s001", "--why", "换面后还要用它")
    ck("G1 killed 可直接重开（不需要 force）rc=0", rc == 0, out[:400])
    g3, s1 = node(lab3, "p3", "s001")
    ck("G2 回到 open", s1.get("status") == "open", s1.get("status"))
    ck("G3 abandon_* 清干净", not any(k in s1 for k in ("abandon_reason", "abandon_why")), s1)
    fgs(lab3, "step-claim", "p3", "s001", "--by", "w1", "--allow-dup")
    rc, out = publish(lab3, "p3", {"op": "conclude", "step": "s001", "fact": "重开后交的结论"})
    ck("G4 重开后能投 rc=0", rc == 0, out[:300])
    rc, out = fgs(lab3, "apply", "p3")
    ck("G5 能 apply", rc == 0, out[:400])
    ck("G6 该步 done", node(lab3, "p3", "s001")[1].get("status") == "done", "")

    print("H) chain / STATE / next 的止损语义")
    fgs(lab3, "step-add", "p3", "三路：打 /api/debug", "--target", "lab.test")
    fgs(lab3, "step-add", "p3", "四路：要用三路的结论", "--target", "lab.test",
        "--depends", "s003")
    fgs(lab3, "step-kill", "p3", "s003", "--why", "这条路不值得再等")
    rc, out = fgs(lab3, "chain", "p3")
    ck("H1 chain rc=0", rc == 0, out[:200])
    ck("H2 kill 标 ⛔", "⛔ s003" in out, out[:800])
    rc, out = fgs(lab3, "checkpoint", "p3", "--state")
    ck("H3 STATE Parked 标 killed", "⛔ killed" in out, out[:3000])
    ck("H4 明说不是证伪", "不是证伪" in out, out[:3000])
    ck("H5 Parked 给重开命令", "step-reopen p3 s003" in out, out[:3000])
    rc, out = fgs(lab3, "next", "p3")
    ck("H6 next 说前序只是没跑完", "前序只是没跑完" in out or "可 step-reopen 续上" in out, out[:1500])

    print("I) 静态兜底")
    ck("I1 ABANDON_REASONS 含 killed", 'ABANDON_REASONS = ("refuted", "timeout", "killed")' in src, "")
    for fn in ("def cmd_step_kill(", "def cmd_steer_send(", "def cmd_steer_drain(",
               "def cmd_steer_queue(", "def steer_enqueue(", "def steer_drain(",
               "def steer_pending("):
        ck("I2 有 " + fn, fn in src, "")
    for sub in ("step-kill", "steer-send", "steer-drain", "steer-queue"):
        ck("I3 CLI 注册 " + sub, '"' + sub + '"' in src, "")
    ck("I4 任务书要求换面前 drain", "steer-drain" in src and "换一次攻击面" in src, "")
    schema = Path(os.path.join(ROOT, "board", "schema.md")).read_text(encoding="utf-8", errors="replace")
    ck("I5 schema 写 killed", "abandon_reason = killed" in schema or BT + "killed" + BT in schema, "")
    ck("I6 schema 写 steer.jsonl", "steer.jsonl" in schema, "")
    doc = Path(os.path.join(ROOT, "docs", "并行工作.md")).read_text(encoding="utf-8", errors="replace")
    ck("I7 docs 有 §4.6s", "### 4.6s" in doc, "")
    ck("I8 docs 写诚实边界（不是强制注入）", "不是强制注入" in doc or "不强制执行" in doc, "")
    ck("I9 docs 指向本测试", "test_steer_kill.py" in doc, "")

    print("")
    print("PASSED " + str(_passed) + "  FAILED " + str(_failed))
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
