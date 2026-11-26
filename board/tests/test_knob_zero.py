#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_knob_zero.py — #19 哑旋钮：设 0 必须真的生效（不是「等于没设」）

为什么（2026-11-17）：int(os.environ.get("X") or 默认) 把「设 0」与「没设」当成同一件事。
PI_FGS_BUDGET_WARN=0 本意是关掉快照告警，旧写法下阈值仍是 16000 —— 假的关闭开关。
ARTEX db/notification.go:133-146 是反面参照：0 = 不限流是**合法配置**，默认值只在字段缺省时由调用方填。

断言（跑行为，不读注释）：
  A. 图超 16000 token 时默认会告警（先把闸门自己验一遍，否则 B 通过是假阳性）。
  B. PI_FGS_BUDGET_WARN=0 → 不告警。
  C. PI_FGS_BUDGET_WARN=999999 → 不告警（往上拧也有效）。
  D. PI_FGS_BUDGET_WARN=abc → 回退默认且**仍然告警**（配置写错不等于关闭）。
  E. PI_FGS_MAX_READ_BYTES=0 → 回退默认 32MB，且在 stderr 说清 0 无意义。
  F. PI_FGS_MAX_READ_BYTES=1024 → 真的把 8KB 的 --from 拒掉（旋钮有实际效果）。
"""
import os
import shutil
import subprocess
import sys
import tempfile

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
FGS = os.path.join(ROOT, "board", "fgs.py")
WARN = "⚠ 快照"
_passed, _failed = 0, 0


def ck(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print("  ✗ %s   [%s]" % (name, detail))


def base_env(lab):
    env = dict(os.environ)
    env["PI_LAB_BOARD_DIR"] = lab
    env.pop("PI_FGS_BUDGET_WARN", None)
    env.pop("PI_FGS_MAX_READ_BYTES", None)
    return env


def run(lab, *args, extra_env=None):
    env = base_env(lab)
    if extra_env:
        env.update(extra_env)
    r = subprocess.run([sys.executable, FGS, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=ROOT, env=env, timeout=180)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


INFLATE = r"""
import sys
sys.path.insert(0, sys.argv[1])
import fgs
pid = sys.argv[2]
g = fgs.load(pid)
for i in range(30):
    nid = "f%03d" % (i + 1)
    g["nodes"][nid] = {"id": nid, "type": "fact", "text": "归并素材 " * 300,
                       "confidence": "unverified", "created_at": fgs.now(), "updated_at": fgs.now()}
fgs.save(pid, g)
print("nodes=%d" % len(g["nodes"]))
"""


def inflate(lab, pid):
    r = subprocess.run([sys.executable, "-c", INFLATE, os.path.join(ROOT, "board"), pid],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=ROOT, env=base_env(lab), timeout=180)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main():
    lab = tempfile.mkdtemp(prefix="knobzero-")
    try:
        rc, out = run(lab, "kickoff", "k1", "--kind", "ctf", "--goal", "旋钮", "--target", "http://demo")
        ck("0 建板 rc=0", rc == 0, out[-300:])
        rc, out = inflate(lab, "k1")
        ck("0 图已撑大", rc == 0 and "nodes=" in out, out[-300:])

        print("A) 默认阈值下超线要告警")
        rc, out = run(lab, "snapshot", "k1")
        ck("A1 snapshot rc=0", rc == 0, out[-300:])
        ck("A2 超 16000 会告警", WARN in out, out[-300:])

        print("B) PI_FGS_BUDGET_WARN=0 = 关闭（旧写法这里是假关闭）")
        rc, out = run(lab, "snapshot", "k1", extra_env={"PI_FGS_BUDGET_WARN": "0"})
        ck("B1 rc=0", rc == 0, out[-300:])
        ck("B2 不告警", WARN not in out, out[-300:])

        print("C) 往上拧阈值也有效")
        rc, out = run(lab, "snapshot", "k1", extra_env={"PI_FGS_BUDGET_WARN": "999999"})
        ck("C1 不告警", rc == 0 and WARN not in out, out[-300:])

        print("D) 写错 ≠ 关闭（仍回退默认并告警）")
        rc, out = run(lab, "snapshot", "k1", extra_env={"PI_FGS_BUDGET_WARN": "abc"})
        ck("D1 仍告警", WARN in out, out[-300:])
        ck("D2 说明回退", "不是整数" in out, out[-300:])

        print("E) 读上限设 0：无「关闭」档位，应回退默认并说明")
        env = base_env(lab)
        env["PI_FGS_MAX_READ_BYTES"] = "0"
        r = subprocess.run([sys.executable, "-c",
                            "import sys; sys.path.insert(0, %r); import fgs; print(fgs.READ_CAP_DEFAULT)"
                            % os.path.join(ROOT, "board")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=ROOT, env=env, timeout=120)
        out = (r.stdout or "") + (r.stderr or "")
        ck("E1 回退 32MB", r.returncode == 0 and "33554432" in (r.stdout or ""), out[-300:])
        ck("E2 stderr 说清 0 无意义", "无意义" in (r.stderr or ""), out[-300:])

        print("F) 读上限设 1024：真的拦住 8KB 的 --from")
        big = os.path.join(lab, "big.txt")
        with open(big, "w", encoding="utf-8") as fh:
            fh.write("A" * 8192)
        rc, out = run(lab, "verify", "k1", "f001", "--from", big,
                      extra_env={"PI_FGS_MAX_READ_BYTES": "1024"})
        ck("F1 被拒", rc != 0, "rc=%d" % rc)
        ck("F2 提到上限", "上限" in out, out[-300:])

        print("\n%d/%d 断言通过" % (_passed, _passed + _failed))
        return 0 if _failed == 0 else 1
    finally:
        shutil.rmtree(lab, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
