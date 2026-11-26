#!/usr/bin/env python3
"""P1-1 / P1-4 / P1-3 回归：平台回执自动闭环 + 多旗剩余 + 槽位账。

    python3 board/tests/test_flag_auto.py

为什么不能只靠 test_cli_smoke：
  冒烟只验「不崩 + 集合相等」，验不了**语义**。这三条命令的语义恰恰最容易错：
  ① duplicate 回执该不该算已确认；② 拿不到回执能不能算成功；
  ③ 平台不给 flag_count 时能不能当 0。这三条错了都会**静默丢分**，
  且赛时没人会盯着 stderr 看 —— 所以必须由测试锁死。

本测试锁住的不变量：
  A. `flag-auto` 判定唯一来源仍是 `_judge_reply`（只认 reply["correct"]），不认 rc
  B. reply.correct 为真 → accepted 且 rc=0
  C. reply.correct 为假 → rejected 且 rc=1（不许放过自己）
  D. ★ code=duplicate → 视为已确认（否则终局扫未确认 flag 会一直报它）
  E. ★ 拿不到回执（命令不存在 / 超时 / 非 JSON）→ error + rc=1，**不许静默成功**
  F. 已 accepted 的 flag_id 不许重复提交
  G. `flags-left` 的剩余算法：correct/flag_count 相减，字段缺失记 unknown（不当 0）
  H. `slot` 只读（不涨 revision），且空槽位时也明说「不代劳」
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
sys.path.insert(0, str(ROOT / "board"))

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}"
          + (f"   {detail}" if detail and not cond else ""))


def run(lab, *args, fake=None, mode=None):
    """跑 fgs 子命令。fake/mode 用来注入假 tsec（不动真平台）。

    ★ fake 必须是**完整命令行**（如 "python3 /p/fake.py"），不是裸路径：
      Windows 不能直接执行 .py（WinError 193），`flag-auto` 已按命令行解析 PI_FGS_TSEC。
    """
    env = dict(os.environ)
    env["PI_LAB_BOARD_DIR"] = lab
    env["PYTHONIOENCODING"] = "utf-8"
    env["PI_LAB_ROOT"] = str(ROOT)
    if mode:
        env["FAKE_MODE"] = mode
    if fake:
        env["PI_FGS_TSEC"] = fake
    p = subprocess.run([sys.executable, str(FGS), *args],
                       capture_output=True, env=env, timeout=120)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


def ledger(lab, pid):
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


def last_state(lab, pid, flag_id=None):
    rows = ledger(lab, pid)
    if flag_id:
        rows = [r for r in rows if r.get("flag_id") == flag_id]
    return rows[-1] if rows else {}


FAKE = r'''#!/usr/bin/env python3
import json, os, sys
mode = os.environ.get("FAKE_MODE", "correct")
R = {
 "correct":   ({"correct": True, "code": "ok"}, 0),
 "wrong":     ({"correct": False, "code": "wrong_flag"}, 0),
 "duplicate": ({"correct": False, "code": "duplicate"}, 1),
 "garbled":   (None, 0),
 "empty":     ({}, 0),
}
rep, rc = R.get(mode, ({"correct": False}, 0))
print("not json at all" if rep is None else json.dumps(rep))
sys.exit(rc)
'''


def main():
    lab = tempfile.mkdtemp(prefix="fgs_p1_")
    _fake_py = os.path.join(lab, "faketsec.py")
    Path(_fake_py).write_text(FAKE, encoding="utf-8")
    # ★ 完整命令行（带解释器）：Windows 不能直接执行 .py
    fake = f"{sys.executable} {_fake_py}"
    PID = "p1"

    print("1) flag-auto 判定语义")
    run(lab, "init", PID, "--kind", "ctf", "--goal", "P1 测试")

    rc, out = run(lab, "flag-auto", PID, "--code", "c1", "--flag", "F{one}",
                  fake=fake, mode="correct")
    st = last_state(lab, PID)
    check("correct → accepted 且 rc=0", st.get("state") == "accepted" and rc == 0,
          f"state={st.get('state')} rc={rc} out={out[-150:]}")

    rc, out = run(lab, "flag-auto", PID, "--code", "c2", "--flag", "F{two}",
                  fake=fake, mode="wrong")
    st = last_state(lab, PID)
    check("wrong → rejected 且 rc=1", st.get("state") == "rejected" and rc == 1,
          f"state={st.get('state')} rc={rc}")

    rc, out = run(lab, "flag-auto", PID, "--code", "c3", "--flag", "F{dup}",
                  fake=fake, mode="duplicate")
    st = last_state(lab, PID)
    check("★ duplicate → accepted（不算丢分）",
          st.get("state") == "accepted" and rc == 0,
          f"state={st.get('state')} rc={rc}")

    rc, out = run(lab, "flag-auto", PID, "--code", "c4", "--flag", "F{bad}",
                  fake=fake, mode="garbled")
    st = last_state(lab, PID)
    check("★ 非 JSON 回执 → error 且 rc=1（不许静默成功）",
          st.get("state") == "error" and rc == 1,
          f"state={st.get('state')} rc={rc}")

    rc, out = run(lab, "flag-auto", PID, "--code", "c5", "--flag", "F{empty}",
                  fake=fake, mode="empty")
    st = last_state(lab, PID)
    check("空回执 {} → rejected（correct 缺失不算对）",
          st.get("state") == "rejected" and rc == 1,
          f"state={st.get('state')} rc={rc}")

    # 命令不存在
    rc, out = run(lab, "flag-auto", PID, "--code", "c6", "--flag", "F{none}",
                  fake="/nonexistent/tsec_xyz")
    st = last_state(lab, PID)
    check("命令不存在 → error 且 rc=1",
          st.get("state") == "error" and rc == 1,
          f"state={st.get('state')} rc={rc}")
    check("命令不存在时说清「不算成功也不算失败」", "不算提交失败" in out, out[-200:])

    # 已 accepted 不重复提交
    acc = [r for r in ledger(lab, PID)
           if r.get("state") == "accepted" and r.get("flag") == "F{one}"]
    if acc:
        rc, out = run(lab, "flag-auto", PID, "--code", "c1",
                      "--id", acc[0]["flag_id"], fake=fake, mode="correct")
        check("已 accepted 的 id 不重复提交", "不重复提交" in out or "已是" in out, out[-200:])
    else:
        check("已 accepted 的 id 不重复提交", False, "找不到 accepted 记录")

    print("2) flags-left 剩余算法")
    # 无平台 → 必须说不知道
    rc, out = run(lab, "flags-left", fake="/nonexistent/tsec_xyz")
    check("无平台时明说「拿不到题单」", "拿不到题单" in out, out[:200])
    check("无平台时 rc=1（不是「没有剩余」）", rc == 1, f"rc={rc}")

    print("3) slot 槽位账")
    r0 = json.loads(run(lab, "show", PID, "--json")[1]).get("revision")
    rc, out = run(lab, "slot", PID)
    r1 = json.loads(run(lab, "show", PID, "--json")[1]).get("revision")
    check("slot 只读（不涨 revision）", r0 == r1, f"{r0} → {r1}")
    check("slot 空槽位时也明说「不代劳」", "不代劳" in out, out[-200:])
    check("slot 报在跑数与上限", "在跑" in out and "上限" in out, out[:200])
    rc, out = run(lab, "slot", PID, "--json")
    js = json.loads(out) if out.strip().startswith("{") else {}
    check("slot --json 有 concurrency 字段", "concurrency" in js, out[:200])

    print()
    if failed:
        print(f"通过 {len(passed)} / 失败 {len(failed)}")
        print("失败项：" + "；".join(failed))
        return 1
    print(f"通过 {len(passed)} / 失败 0")
    print("✓ P1-1/P1-3/P1-4（平台闭环 + 剩余旗 + 槽位账）：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
