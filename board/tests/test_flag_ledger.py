#!/usr/bin/env python3
"""S6 回归：先记账后提交（崩溃安全的 flag 账本）。

    python3 board/tests/test_flag_ledger.py

结构抄 sapper `sapper.py:1771-1786`（flag 先落盘 status=recorded，**再**提交，
结果事后回写），**判定不抄** —— 清单 B2 已标明 sapper 那个账本的成功判定是错的：
正确答案会被写成 rejected。

本测试锁住的不变量：
  A. 记账后即便从未提交，flag 也在盘上（崩溃安全点的真正含义）
  B. 判定唯一来源 = `bool(reply["correct"])`
  C. ★ B2 反向用例：reply 带 `{"result":"success","status":"ok"}` 但无 correct
     → **必须判 rejected**（照抄 sapper 的判定就会在这里把错当对，或把对当错）
  D. 证据存**整个 reply**（不只取某个键 —— 取单键正是 B2 错误的成因）
  E. 回执不是合法 JSON → 记 `error`（可重试），**不算判错**
  F. 账本 append-only；半写的坏行不能让整本账读不出来
  G. 没记账就提交 → 拒（不许绕过记账直接写 accepted）
  H. 已 accepted 的 flag_id 不许重复记账（防重复提交刷分）
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

ROOT = Path(__file__).resolve().parent.parent.parent
FGS = ROOT / "board" / "fgs.py"
passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail and not cond else ""))


def run(lab, *args):
    env = dict(os.environ)
    env["PI_LAB_BOARD_DIR"] = lab
    env["PYTHONIOENCODING"] = "utf-8"
    env["PI_LAB_ROOT"] = str(ROOT)
    p = subprocess.run([sys.executable, str(FGS), *args],
                       capture_output=True, env=env, timeout=120)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


def ledger(lab, pid):
    """读账本行；坏行跳过（与产品 ledger_entries 同一口径 —— 半写的一行
    不能让整本账读不出来，否则容错断言自己先炸）。"""
    p = Path(lab, pid, "flag-ledger.jsonl")
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def raw_lines(lab, pid):
    """账本物理行数（含坏行）——用于断言 append-only。"""
    p = Path(lab, pid, "flag-ledger.jsonl")
    if not p.exists():
        return 0
    return len([l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()])


def state_of(lab, pid, fid):
    cur = {}
    for r in ledger(lab, pid):
        if r.get("flag_id") == fid:
            cur.update({k: v for k, v in r.items() if k != "at"})
    return cur


def main() -> int:
    lab = tempfile.mkdtemp(prefix="s6ledger-")
    try:
        run(lab, "init", "s6", "--kind", "ctf", "--goal", "账本测试")

        print("1) 崩溃安全点：记了账就落盘，哪怕从未提交")
        rc, out = run(lab, "flag-record", "s6", "--flag", "flag{crash_safe}", "--id", "K1")
        check("record rc=0", rc == 0, f"rc={rc} :: {out[:200]}")
        ents = ledger(lab, "s6")
        check("★ 账本文件已生成", len(ents) == 1, str(ents[:2]))
        check("★ state=recorded", ents[0].get("state") == "recorded", str(ents[0]))
        check("flag 已落盘", ents[0].get("flag") == "flag{crash_safe}")
        rc, out = run(lab, "flag-reconcile", "s6")
        check("对账把它列为 pending", "pending 1" in out, out[:300])
        check("给出重投指引", "flag-submit" in out, out[:400])

        print("2) 平台判对 → accepted")
        rc, out = run(lab, "flag-submit", "s6", "--id", "K1",
                      "--reply", '{"correct":true,"msg":"ok","score":100}')
        check("submit rc=0", rc == 0, f"rc={rc}")
        check("★ state=accepted", state_of(lab, "s6", "K1").get("state") == "accepted",
              str(state_of(lab, "s6", "K1")))

        print("3) ★ B2 反向用例：只有 result/status 没有 correct → 必须 rejected")
        run(lab, "flag-record", "s6", "--flag", "flag{b2}", "--id", "K2")
        rc, out = run(lab, "flag-submit", "s6", "--id", "K2",
                      "--reply", '{"result":"success","status":"ok","score":100}')
        st = state_of(lab, "s6", "K2").get("state")
        check("★★ 判 rejected（照抄 sapper 判定会在这里出错）", st == "rejected", f"state={st}")
        check("correct 记为 False", state_of(lab, "s6", "K2").get("correct") is False)

        print("4) ★ 证据存整个 reply（不只取某个键）")
        r = state_of(lab, "s6", "K1").get("reply")
        check("★ reply 是完整 dict", isinstance(r, dict), repr(r))
        check("★ 保留了 msg", r.get("msg") == "ok", repr(r))
        check("★ 保留了 score", r.get("score") == 100, repr(r))

        print("5) 回执不合法 → error（可重试），不算判错")
        run(lab, "flag-record", "s6", "--flag", "flag{badjson}", "--id", "K3")
        rc, out = run(lab, "flag-submit", "s6", "--id", "K3", "--reply", "{不是JSON")
        st = state_of(lab, "s6", "K3").get("state")
        check("★ state=error（不是 rejected）", st == "error", f"state={st}")
        rc, out = run(lab, "flag-reconcile", "s6")
        check("★ error 仍算 pending（能重投）", "pending 1" in out, out[:300])

        print("6) 没记账就提交 → 拒")
        rc, out = run(lab, "flag-submit", "s6", "--id", "K999", "--reply", '{"correct":true}')
        check("★ rc=2 拒", rc == 2, f"rc={rc}")
        check("提示先记账", "flag-record" in out, out[:300])

        print("7) 已 accepted 不许重复记账（防刷分）")
        rc, out = run(lab, "flag-record", "s6", "--flag", "flag{crash_safe}", "--id", "K1")
        check("★ rc=2 拒", rc == 2, f"rc={rc}")
        check("说明已 accepted", "accepted" in out, out[:300])

        print("7.5) ★ confirm 与账本联动（2026-11-02 打通）")
        # 此时账本里 K1 是 accepted（flag{crash_safe}），且项目尚未 solved
        rc, out = run(lab, "confirm", "s6")
        check("★ 不给 --flag 时自动取账本 accepted", rc == 0, f"rc={rc} :: {out[:200]}")
        check("★ 用的是账本里那条", "flag{crash_safe}" in out, out[:300])
        check("★ 标明依据哪条回执", "依据账本 K1" in out, out[:300])
        # 与账本不一致必须拒
        rc, out = run(lab, "confirm", "s6", "--flag", "flag{WRONG}")
        check("★★ flag 与账本冲突 → rc=2", rc == 2, f"rc={rc}")
        check("★ 提示两个真相必有一错", "不一致" in out or "两个真相" in out, out[:300])
        # 多条 accepted 且未指定 → 拒
        run(lab, "flag-record", "s6", "--flag", "flag{second}", "--id", "K5")
        run(lab, "flag-submit", "s6", "--id", "K5", "--reply", '{"correct":true}')
        rc, out = run(lab, "confirm", "s6")
        check("★ 多条 accepted 未指定 → rc=2", rc == 2, f"rc={rc}")
        check("★ 列出候选让人选", "K1" in out and "K5" in out, out[:400])
        rc, out = run(lab, "confirm", "s6", "--flag", "flag{second}")
        check("指定后 rc=0", rc == 0, f"rc={rc}")
        check("依据账本 K5", "依据账本 K5" in out, out[:300])

        print("8) append-only + 坏行容错")
        lp = Path(lab, "s6", "flag-ledger.jsonl")
        n_before = raw_lines(lab, "s6")
        with lp.open("a", encoding="utf-8") as f:
            f.write('{"flag_id": "K4", "state": "recorded"')   # 半写坏行
        rc, out = run(lab, "flag-reconcile", "s6")
        check("★ 坏行不炸（rc=0）", rc == 0, f"rc={rc} :: {out[:200]}")
        check("★ 已接受的仍读得出", "flag{crash_safe}" in out, out[:400])
        check("★ 账本只追加不重写（坏行仍在）", raw_lines(lab, "s6") == n_before + 1,
              f"{n_before} → {raw_lines(lab, 's6')}")
    finally:
        import shutil
        shutil.rmtree(lab, ignore_errors=True)

    print()
    if failed:
        print(f"通过 {len(passed)} / 失败 {len(failed)}")
        print("失败项：" + "、".join(failed[:10]))
        return 1
    print(f"通过 {len(passed)} / 失败 0")
    print("✓ flag 账本（S6）：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
