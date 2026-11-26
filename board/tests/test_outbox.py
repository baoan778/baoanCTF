#!/usr/bin/env python3
"""回归测试：outbox 投递终态（ADR 0021 / 0023）

跑法：
    python3 board/tests/test_outbox.py

覆盖的是**首版缺失的那几条**（实盘压测踩出来的）：
  T1 applied                     —— 正常通路
  T2 rejected                   —— 校验不过：图不动、exit 3、但**必须有终态留痕**
  T3 action_required            —— 同批合法条目被连坐时，也要有终态（不能只变散文）
  T4 attempt 计数不重置          —— 同一 seq 第 N 次尝试要能看出来
  T5 --force-partial 不静默销毁  —— 被拒条目归档，原因保留
  T6 duplicate 不二次写图        —— ADR 0021：迟到的结果不能改变黑板状态
  T7 审计动作名不被载荷覆盖       —— log.jsonl 里要能看出是 outbox.rejected 而不是 fact/relate

为什么值得留：这四条是"幻觉防火墙有没有真的留痕"的问题 ——
没有终态，被拒的写入就等于没发生过，下一个人会重新踩。
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

ROOT = Path(__file__).resolve().parents[1]          # board/
FGS = ROOT / "fgs.py"
PID = "_test_outbox"

# ★ 隔离：临时项目别建到真 board/projects（否则并行时污染别的测试）
TMPDIR = Path(tempfile.mkdtemp(prefix="pilab-outbox-"))
PDIR = TMPDIR / "projects" / PID
ENV = dict(os.environ, PI_LAB_BOARD_DIR=str(TMPDIR / "projects"))

passed, failed = [], []


def run(*args):
    p = subprocess.run([sys.executable, str(FGS), *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=ENV)
    return p.returncode, p.stdout, p.stderr


def graph():
    return json.loads((PDIR / "graph.json").read_text(encoding="utf-8"))


def ledger():
    f = PDIR / "deliveries.jsonl"
    if not f.exists():
        return []
    return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]


def audit_ops():
    f = PDIR / "log.jsonl"
    if not f.exists():
        return []
    return [json.loads(l)["op"] for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail and not cond else ""))


def reset():
    shutil.rmtree(TMPDIR, ignore_errors=True)
    rc, _, err = run("init", PID, "--kind", "ctf", "--goal", "outbox 回归测试")
    assert rc == 0, err


def main():
    reset()
    n0 = len(graph()["nodes"])

    print("T1 applied：正常通路")
    run("publish", PID, "--by", "w1", "--op",
        json.dumps({"op": "fact", "text": "基线事实", "confidence": "verified"}))
    rc, out, _ = run("apply", PID)
    check("exit=0", rc == 0, f"rc={rc}")
    check("图里多了一个节点", len(graph()["nodes"]) == n0 + 1)
    check("台账记 applied", any(e["outcome"] == "applied" for e in ledger()))
    check("审计动作名 = outbox.applied", "outbox.applied" in audit_ops())

    print("T2 rejected：校验不过")
    n1 = len(graph()["nodes"])
    run("publish", PID, "--by", "w1", "--op",
        json.dumps({"op": "relate", "type": "supports", "from": "f001", "to": "f001"}))
    rc, _, _ = run("apply", PID)
    check("exit=3", rc == 3, f"rc={rc}")
    check("图一个字节没动", len(graph()["nodes"]) == n1)
    check("台账记 rejected", any(e["outcome"] == "rejected" for e in ledger()))
    check("拒绝带原因", any("自环" in (e.get("reason") or "") for e in ledger()))
    check("outbox 保留待修", len((PDIR / "outbox.jsonl").read_text(encoding="utf-8", errors="replace").splitlines()) == 1)

    print("T3 action_required：同批合法条目被连坐")
    run("publish", PID, "--by", "w1", "--op",
        json.dumps({"op": "fact", "text": "混批里的合法条目", "confidence": "inferred"}))
    rc, _, _ = run("apply", PID)
    check("exit=3（整批拒绝）", rc == 3, f"rc={rc}")
    check("台账记 action_required", any(e["outcome"] == "action_required" for e in ledger()))
    check("合法条目没被偷偷合入", len(graph()["nodes"]) == n1)

    print("T4 attempt 计数不重置")
    run("apply", PID)                       # 再 apply 一次，同一批
    tries = [e["attempts"] for e in ledger() if e["outcome"] == "rejected"]
    check("同一 seq 出现 try#2", max(tries) >= 2, f"attempts={tries}")

    print("T5 --force-partial 不静默销毁被拒条目")
    rc, out, _ = run("apply", PID, "--force-partial")
    check("exit=0", rc == 0, f"rc={rc}")
    rej = PDIR / "outbox-rejected.jsonl"
    lines = [json.loads(l) for l in rej.read_text(encoding="utf-8").splitlines() if l.strip()]
    check("被拒条目归档了", len(lines) >= 1, f"{len(lines)} 条")
    check("归档里带拒绝原因", all("_reason" in x for x in lines))
    check("outbox 已清空", (PDIR / "outbox.jsonl").read_text(encoding="utf-8", errors="replace").strip() == "")

    print("T6 duplicate 不二次写图")
    n2 = len(graph()["nodes"])
    run("publish", PID, "--by", "w2", "--op",
        json.dumps({"op": "fact", "text": "基线事实", "confidence": "verified"}))
    rc, _, _ = run("apply", PID)
    check("exit=0", rc == 0, f"rc={rc}")
    check("节点数没变", len(graph()["nodes"]) == n2, f"{n2} -> {len(graph()['nodes'])}")
    check("台账记 duplicate", any(e["outcome"] == "duplicate" for e in ledger()))

    print("T7 审计动作名不被载荷覆盖")
    ops = set(audit_ops())
    check("audit 里能看到 outbox.rejected", "outbox.rejected" in ops)
    check("audit 里没有把 fact/relate 当动作名", not (ops & {"fact", "relate"}))

    print("T8 Fact 结构化元数据（应急报告/时间线/IOC 表的数据源）")
    run("publish", PID, "--by", "w1", "--op", json.dumps({
        "op": "fact", "text": "异常外联 pool.bad.xyz", "confidence": "verified",
        "meta": {"phase": "triage", "action": "collect", "tool": "ss",
                 "target": "srv-01", "time": "2026-09-01T03:12:00Z",
                 "ioc": ["域名=pool.bad.xyz", "IP=203.0.113.77"]}}))
    rc, _, _ = run("apply", PID)
    check("exit=0", rc == 0, f"rc={rc}")
    m = next((n.get("meta") for n in graph()["nodes"].values()
              if (n.get("meta") or {}).get("ioc")), None)
    check("meta 落进图里", m is not None)
    check("phase/action 保留", (m or {}).get("phase") == "triage" and (m or {}).get("action") == "collect")
    check("ioc 是列表且两项", isinstance((m or {}).get("ioc"), list) and len(m["ioc"]) == 2)

    print("T9 非法 meta 要被拒（幻觉防火墙对 meta 同样生效）")
    n_before = len(graph()["nodes"])
    run("publish", PID, "--by", "w1", "--op", json.dumps({
        "op": "fact", "text": "meta 里放了不认识的键", "confidence": "verified",
        "meta": {"阶段": "triage"}}))
    rc, _, err = run("apply", PID)
    check("exit=3", rc == 3, f"rc={rc}")
    check("图没动", len(graph()["nodes"]) == n_before)
    check("原因指向 meta 键", "meta 不认识的键" in err, err[:120])

    print("T10 非法 phase / ioc 格式要被拒")
    for bad_meta, why in [({"phase": "triage2"}, "phase 枚举"),
                          ({"ioc": ["没有等号"]}, "ioc 格式")]:
        run("publish", PID, "--by", "w1", "--op", json.dumps(
            {"op": "fact", "text": f"坏 meta：{why}", "confidence": "verified", "meta": bad_meta}))
        rc, _, err = run("apply", PID)
        check(f"{why} -> exit=3", rc == 3, f"rc={rc}")
        check(f"{why} -> 有说明", "必须是" in err or "格式" in err or "写成" in err, err[:120])

    print("T12 未结算投递会挡住派活（v2-spec：Finish readiness reports outstanding delivery）")
    # 造一条会一直留在 outbox 的坏条目（默认路径下被拒条目不会被清掉）
    run("publish", PID, "--by", "w1", "--op", json.dumps(
        {"op": "relate", "type": "supports", "from": "f001", "to": "f001"}))
    run("apply", PID)                       # exit 3，条目留在 outbox
    check("outbox 非空", len((PDIR / "outbox.jsonl").read_text(encoding="utf-8", errors="replace").splitlines()) >= 1)
    rc, out, _ = run("next", PID, "--as", "tester")
    check("next 给 wait", "wait" in out, out[:120])
    check("指路到 apply", "apply" in out, out[:200])
    check("指路到 withdraw", "withdraw" in out, out[:200])

    print("T13 withdraw 是唯一能清被拒条目的通道（没有它就会死锁）")
    seq = json.loads((PDIR / "outbox.jsonl").read_text(encoding="utf-8", errors="replace").splitlines()[0])["_seq"]
    rc, out, err = run("outbox", PID, "--withdraw", seq)
    check("缺 --reason 时拒绝", rc != 0, f"rc={rc}")
    check("说清为什么要 reason", "reason" in (out + err), (out + err)[:120])
    rc, out, err = run("outbox", PID, "--withdraw", seq, "--reason", "测试用坏条目")
    check("exit=0", rc == 0, f"rc={rc} {err}")
    # 前面几条测试（T2/T3/T10）默认路径下把被拒条目留在了 outbox —— 一并撤回，才能测"解除阻塞"
    for _ in range(20):
        rest = [l for l in (PDIR / "outbox.jsonl").read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
        if not rest:
            break
        s2 = json.loads(rest[0])["_seq"]
        run("outbox", PID, "--withdraw", s2, "--reason", "测试收尾")
    check("outbox 清空", (PDIR / "outbox.jsonl").read_text(encoding="utf-8", errors="replace").strip() == "")
    check("终态 withdrawn", any(e["outcome"] == "withdrawn" for e in ledger()))
    check("原文归档了", "测试用坏条目" in (PDIR / "outbox-rejected.jsonl").read_text(encoding="utf-8", errors="replace"))
    rc, out, _ = run("next", PID, "--as", "tester")
    check("解除阻塞（不再是 wait）", "wait" not in out, out[:120])

    print("T14 --resolves 记下「哪一批修了哪一批」")
    run("publish", PID, "--by", "w1", "--resolves", seq, "--op", json.dumps(
        {"op": "fact", "text": "修好后的那一条", "confidence": "verified"}))
    run("apply", PID)
    check("台账记了 resolves",
          any(e.get("resolves") == seq for e in ledger()), str([e.get("resolves") for e in ledger()]))
    check("被拒那条状态不变（终态不可改写）",
          any(e["seq"] == seq and e["outcome"] == "withdrawn" for e in ledger()))

    print("T15 fgs outbox 视图可用")
    rc, out, _ = run("outbox", PID)
    check("exit=0", rc == 0, f"rc={rc}")
    check("打印了合计", "合计" in out)

    shutil.rmtree(TMPDIR, ignore_errors=True)

    print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ outbox 投递终态：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
