#!/usr/bin/env python3
"""handoff_card 的回归锁 —— 治「交接卡自己说谎」。

跑法：    python3 board/tests/test_handoff_card.py

为什么要有它
-----------
交接卡的全部价值在于「下一个会话照它开工」。它一旦说错话，错的是**接手者**，
不是它自己 —— 所以每条判据都要能用假数据反向验证：

  · 空行不许被当成第一块（本工具真踩过：整段待办渲染成「待办为空」）
  · 块顺序信文件、时效只做提醒（踩过：按块内最大日期排序会挑中最老那块）
  · 非 git 仓库 / 缺 work/solve 也要出卡（降级而不是崩）
  · --check 在提交号对不上时必须 rc=3（过期要能被机器抓到）
  · --advise 的 5 条触发判据逐条正反打（触发 / 不触发）
  · 只读：跑完整轮不许改仓库里的任何文件

★ 全部用临时目录造数据，不碰真仓库（PI_LAB_ROOT 指向 tmp）。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
IMPL = ROOT / "tools" / "handoff_card.py"
passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print("  %s %s%s" % ("OK" if cond else "XX", name,
                         ("   " + detail) if detail and not cond else ""))


def run(args, cwd=None, env=None):
    e = dict(os.environ)
    e.pop("PI_LAB_ROOT", None)
    if env:
        e.update(env)
    p = subprocess.run([sys.executable, str(IMPL), *args],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120, cwd=str(cwd or ROOT), env=e)
    return p.returncode, p.stdout, p.stderr


def make_repo(tmp: Path, *, agents=True, solve=True, snap=None, dirty=False):
    """造一个最小仓库。snap 为 None 则不写状态快照。"""
    (tmp / "docs").mkdir(parents=True, exist_ok=True)
    (tmp / "work" / "solve").mkdir(parents=True, exist_ok=True)
    if agents:
        (tmp / "AGENTS.md").write_text("x" * 18000, encoding="utf-8")
    if snap is not None:
        (tmp / "docs" / "状态快照.md").write_text(snap, encoding="utf-8")
    if solve:
        (tmp / "work" / "solve" / ".balance.json").write_text(json.dumps({
            "ok": True, "total": 1.12, "currency": "CNY", "threshold": 2.0,
            "allow": False, "reason": "余额 1.12 CNY < 阈值 2.00",
            "ts": 1795434625221}), encoding="utf-8")
        led = {("x%03d" % i): {"attempts": 1, "fails": 0, "rejected": 1,
                              "total_sec": 100, "runs": 2, "total_cost": 0.01}
               for i in range(5)}
        (tmp / "work" / "solve" / "ledger.json").write_text(
            json.dumps(led), encoding="utf-8")
        (tmp / "work" / "solve" / "_platform_cache.json").write_text(json.dumps({
            "ts": 1795166020,
            "items": [{"code": "a", "done": True}, {"code": "b", "done": False}]}),
            encoding="utf-8")
    return tmp


SNAP_TWO_BLOCKS = """# 快照

## 0.5 待办（关机前留的）

> ★ **2026-10-11 夜 · 第一块**

第一块正文，写着 **2026-11-19** 的批注。

> ★ **2026-10-10 晚 · 第二块**

第二块正文。

---

## 4. 已知坑
不该被搬进卡里。
"""


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pilab-handoff-"))

    print("1) 正常仓库：五段齐全、行数受限、句柄正确")
    r1 = make_repo(tmp / "ok", snap=SNAP_TWO_BLOCKS)
    rc, out, err = run(["--root", str(r1)])
    check("正常仓库 rc=0", rc == 0, err[-300:])
    for seg in ("## 1. 项目", "## 2. 机器核对过的状态",
                "## 3. 下一步", "## 4. 新对话只读这些", "## 5. 坑"):
        check("有 %s" % seg, seg in out)
    check("行数 ≤ 60", len(out.splitlines()) <= 60, str(len(out.splitlines())))
    check("搬到了第一块正文", "第一块正文" in out)
    check("第二块没被搬进来（只放第一块）", "第二块正文" not in out)
    check("说了还有几块没搬", "共 2 块" in out, out[:400])

    print("2) 反向：空行不许被当成第 0 块")
    # 小节标题后紧跟空行 —— 正是当初吃掉整段待办的形态
    rc, out, err = run(["--root", str(r1), "--json"])
    card = json.loads(out)
    check("todo 非空（空行没被当成块）", bool(card["todo"].strip()),
          "todo=%r" % card["todo"][:80])
    check("todo 里是正文不是空行", "第一块正文" in card["todo"])

    print("3) 反向：块顺序信文件，不按块内最大日期重排")
    snap_shuffled = """## 0.5 待办

> ★ **2026-10-11 · 旧块头**

旧块正文，含最新批注 2026-11-19。

> ★ **2026-10-10 · 更旧块头**

更旧块正文。
"""
    r2 = make_repo(tmp / "order", snap=snap_shuffled)
    rc, out, _ = run(["--root", str(r2), "--json"])
    card = json.loads(out)
    check("取的是文件里的第一块（不是最大日期那块）",
          "旧块正文" in card["todo"] and "更旧块正文" not in card["todo"])
    check("时效做成提醒（提到 2026-11-19）", "2026-11-19" in card["todo_note"],
          card["todo_note"])

    print("4) 降级：不是 git 仓库 / 没有 work/solve 也要出卡")
    r3 = make_repo(tmp / "nogit", agents=False, solve=False)
    rc, out, err = run(["--root", str(r3)])
    check("无 git 无 solve 仍 rc=0", rc == 0, err[-300:])
    check("明说不是仓库", "不是仓库" in out or "git 不在" in out)
    check("缺余额闸门时写「缺」", "余额闸门：缺" in out, out[:600])
    check("待办为空时明说", "待办为空" in out)

    print("5) --check：比「待办指纹」而不是 HEAD（commit 不该让它过期）")
    r4 = make_repo(tmp / "gitrepo", snap=SNAP_TWO_BLOCKS)
    subprocess.run(["git", "init", "-q", str(r4)], capture_output=True)
    subprocess.run(["git", "-C", str(r4), "config", "user.email", "t@t"], capture_output=True)
    subprocess.run(["git", "-C", str(r4), "config", "user.name", "t"], capture_output=True)
    subprocess.run(["git", "-C", str(r4), "add", "-A"], capture_output=True)
    subprocess.run(["git", "-C", str(r4), "commit", "-qm", "init"], capture_output=True)
    rc, out, _ = run(["--root", str(r4), "--out", "docs/交接卡.md"])
    check("--out 写盘 rc=0", rc == 0, out[-200:])
    cardp = r4 / "docs" / "交接卡.md"
    check("卡文件真的生成了", cardp.is_file())
    rc, _, _ = run(["--root", str(r4), "--check", "docs/交接卡.md"])
    check("新鲜卡 --check rc=0", rc == 0, "rc=%d" % rc)

    # ★ 反向①：随便来个新提交**不该**让卡过期（否则每次 commit 都假红，检查会被关掉）
    (r4 / "later.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(r4), "add", "-A"], capture_output=True)
    subprocess.run(["git", "-C", str(r4), "commit", "-qm", "later"], capture_output=True)
    rc, out, _ = run(["--root", str(r4), "--check", "docs/交接卡.md"])
    check("新提交后卡仍算新鲜（比的是待办指纹不是 HEAD）", rc == 0,
          "rc=%d out=%s" % (rc, (out or "")[-150:]))

    # ★ 反向②：人改了 §0.5 → 必须 rc=3
    snap = r4 / "docs" / "状态快照.md"
    snap.write_text(snap.read_text(encoding="utf-8").replace(
        "第一块正文", "第一块正文（已改）"), encoding="utf-8")
    rc, out, _ = run(["--root", str(r4), "--check", "docs/交接卡.md"])
    check("改了待办 --check rc=3", rc == 3, "rc=%d out=%s" % (rc, (out or "")[-150:]))
    check("过期时点名是待办指纹变了", "待办指纹" in (out or ""), (out or "")[-150:])
    check("过期时给了重生成命令", "handoff-card --out" in (out or ""))

    # ★ 反向③：旧格式的卡（没有指纹）不许装作能判
    oldp = r4 / "docs" / "旧卡.md"
    oldp.write_text("# 交接卡\n\n- 分支/提交：master @ deadbee xyz\n", encoding="utf-8")
    rc, out, _ = run(["--root", str(r4), "--check", "docs/旧卡.md"])
    check("旧格式卡 --check rc=3 且要求重生成", rc == 3 and "重跑" in (out or ""),
          "rc=%d out=%s" % (rc, (out or "")[-150:]))

    print("6) --advise：5 条判据逐条正反打")
    sess_dir = tmp / "sessions"
    sess_dir.mkdir(parents=True, exist_ok=True)

    def write_sess(name, *, turns=1, tokens=0, repeat=1, ts=None):
        p = sess_dir / name
        lines = []
        for i in range(turns):
            lines.append(json.dumps({"role": "user", "timestamp": ts or 0}))
        if tokens:
            lines.append(json.dumps({"role": "assistant",
                                     "usage": {"input": tokens, "output": 0}}))
        for i in range(repeat):
            lines.append(json.dumps({"tool_calls": [
                {"name": "bash", "arguments": {"command": "curl x"}}]}))
        p.write_text("\n".join(lines), encoding="utf-8")
        return p

    calm = write_sess("calm.jsonl", turns=2, tokens=1000, repeat=1)
    rc, out, _ = run(["--root", str(r1), "--advise", "--session", str(calm), "--json"])
    adv = json.loads(out)
    check("平静会话：判据全不触发", adv["verdict"] == "可以继续" and not adv["hits"],
          str(adv["hits"]))

    busy = write_sess("busy.jsonl", turns=40, tokens=3_000_000, repeat=6)
    rc, out, _ = run(["--root", str(r1), "--advise", "--session", str(busy), "--json"])
    adv = json.loads(out)
    ids = {h["id"] for h in adv["hits"]}
    check("轮次触发 T1", any("T1" in i for i in ids), str(ids))
    check("上下文触发 T2", any("T2" in i for i in ids), str(ids))
    check("原地打转触发 T3", any("T3" in i for i in ids), str(ids))
    check("判定为建议开新对话", adv["verdict"] == "建议开新对话")
    check("每条都带 action", all(h.get("action") for h in adv["hits"]))

    # T4：账本花费（造一份贵的账本）
    r5 = make_repo(tmp / "costly", snap=SNAP_TWO_BLOCKS)
    led = json.loads((r5 / "work/solve/ledger.json").read_text(encoding="utf-8"))
    for v in led.values():
        v["total_cost"] = 1.0
    (r5 / "work/solve/ledger.json").write_text(json.dumps(led), encoding="utf-8")
    rc, out, _ = run(["--root", str(r5), "--advise", "--json"])
    adv = json.loads(out)
    check("花费触发 T4", any("T4" in h["id"] for h in adv["hits"]),
          str([h["id"] for h in adv["hits"]]))

    # T5：隔了很久（时间戳取 20 小时前）
    import time as _t
    old_ts = int((_t.time() - 20 * 3600) * 1000)
    stale = write_sess("stale.jsonl", turns=2, ts=old_ts)
    rc, out, _ = run(["--root", str(r1), "--advise", "--session", str(stale), "--json"])
    adv = json.loads(out)
    check("隔夜触发 T5", any("T5" in h["id"] for h in adv["hits"]),
          str([h["id"] for h in adv["hits"]]))

    # 会话文件不存在：不许崩
    rc, out, err = run(["--root", str(r1), "--advise",
                        "--session", str(sess_dir / "nope.jsonl"), "--json"])
    check("会话不存在也 rc=0（降级）", rc == 0, err[-200:])

    print("7) 安全不变量：只读、不越界")
    before = {p: p.stat().st_mtime for p in r1.rglob("*") if p.is_file()}
    run(["--root", str(r1)])
    run(["--root", str(r1), "--json"])
    run(["--root", str(r1), "--advise"])
    after = {p: p.stat().st_mtime for p in r1.rglob("*") if p.is_file()}
    check("不带 --out 时不写任何文件", before == after,
          str({k: (before.get(k), after.get(k)) for k in before if before.get(k) != after.get(k)}))
    # 上限：--max-lines 真的生效
    rc, out, _ = run(["--root", str(r1), "--max-lines", "5"])
    check("--max-lines 生效", len(out.splitlines()) <= 6,
          str(len(out.splitlines())))
    check("截断时说清截了什么", "截掉" in out, out[-200:])

    print("8) 用法错误：不静默吞掉")
    rc, _, _ = run(["--root", str(r1), "--no-such-flag"])
    check("未知参数 rc!=0", rc != 0, "rc=%d" % rc)
    rc, out, _ = run(["--help"])
    check("--help rc=0 且有用法", rc == 0 and "用法" in out)

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    print("=" * 40)
    print("通过 %d / 失败 %d" % (len(passed), len(failed)))
    if failed:
        print("失败项：" + " | ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
