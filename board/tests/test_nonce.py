#!/usr/bin/env python3
"""T5 · nonce 能力验证回归（2026-11-11）。

要验的核心不是「函数能跑」，而是**五种判定的边界不能串档**：
  executed / reflected / absent / mismatch / reused

尤其是：**回显场景下绝不能判成 executed** —— 这是本机制存在的唯一理由。
"""
from __future__ import annotations

import hashlib
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

LAB = Path(__file__).resolve().parents[2]
FGS = LAB / "board" / "fgs.py"
PY = sys.executable or "python3"

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    mark = "\u2713 " if cond else "\u2717 "
    print("  " + mark + name + (("   " + str(detail)[:240]) if detail and not cond else ""))


def run(board, *args, ok=(0,)):
    env = dict(os.environ)
    env["PI_LAB_BOARD_DIR"] = str(board)
    env["PI_LAB_WORK_DIR"] = str(Path(board).parent / "work")
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PI_SESSION_ID", None)
    p = subprocess.run([PY, str(FGS), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120,
                       cwd=str(LAB), env=env)
    if "Traceback" in (p.stderr or ""):
        check("cmd has no Traceback: " + " ".join(args[:2]), False, (p.stderr or "")[-200:])
    elif p.returncode not in ok:
        check("rc in " + str(ok) + " for " + " ".join(args[:2]), False,
              "rc=%s %s" % (p.returncode, (p.stderr or "")[:200]))
    return p


PROBE = """
import hashlib, json, sys
sys.path.insert(0, %r)
import fgs
seed = bytes(range(16))
ch = fgs.make_nonce_challenge(seed)
exp = ch["expected"]
nonce = ch["nonce"]
out = {
  "expected": exp,
  "nonce_len": len(nonce),
  "expected_len": len(exp),
  "is_sha_prefix": exp == hashlib.sha256(seed).hexdigest()[:fgs.NONCE_PREFIX],
  "nonce_ne_expected": nonce != exp,
  "executed": list(fgs.nonce_matches(dict(ch), "result: " + exp)),
  "reflected": list(fgs.nonce_matches(dict(ch), "echo: " + nonce)),
  "absent": list(fgs.nonce_matches(dict(ch), "")),
  "absent_none": list(fgs.nonce_matches(dict(ch), None)),
  "mismatch": list(fgs.nonce_matches(dict(ch), "deadbeefdeadbeef")),
  "reused": list(fgs.nonce_matches(dict(ch, used=True), exp)),
  "echo_plus_exp": list(fgs.nonce_matches(dict(ch), nonce + " " + exp)),
}
print(json.dumps(out))
""" % str(LAB / "board")


def report():
    print("")
    print("\u901a\u8fc7 %d / \u5931\u8d25 %d" % (len(passed), len(failed)))
    if failed:
        print("failed: " + "\u3001".join(failed))
        return 1
    print("\u2713 T5 nonce \u80fd\u529b\u9a8c\u8bc1\uff1a\u5168\u90e8\u901a\u8fc7")
    return 0


def main():
    check("board/fgs.py exists", FGS.is_file())
    if not FGS.is_file():
        return report()
    src = FGS.read_text(encoding="utf-8", errors="replace")

    print("A) pure functions present")
    for sym in ("def make_nonce_challenge", "def nonce_matches",
                "NONCE_BYTES", "NONCE_PREFIX", "NONCE_TTL_S", "class CallTimeout"):
        check("has " + sym, sym in src)

    print("B) five verdicts (calls the real implementation)")
    p = subprocess.run([PY, "-c", PROBE], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    if "Traceback" in (p.stderr or ""):
        check("import fgs + nonce_matches no crash", False, (p.stderr or "")[-300:])
        return report()
    try:
        d = json.loads((p.stdout or "").strip().splitlines()[-1])
    except Exception as e:
        check("probe output parses", False, str(e))
        return report()

    check("expected == sha256(seed)[:16]", d["is_sha_prefix"])
    check("nonce != expected", d["nonce_ne_expected"])
    check("nonce is 32 hex (16 bytes)", d["nonce_len"] == 32, d["nonce_len"])
    check("expected is 16 hex", d["expected_len"] == 16, d["expected_len"])
    check("target returns expected -> executed", d["executed"][0] == "executed", d["executed"])
    check("target echoes nonce -> reflected (NOT executed)", d["reflected"][0] == "reflected", d["reflected"])
    check("empty -> absent (valid negative)", d["absent"][0] == "absent", d["absent"])
    check("None -> absent", d["absent_none"][0] == "absent", d["absent_none"])
    check("wrong value -> mismatch", d["mismatch"][0] == "mismatch", d["mismatch"])
    check("used challenge -> reused", d["reused"][0] == "reused", d["reused"])
    check("echo + correct digest -> still reflected", d["echo_plus_exp"][0] == "reflected", d["echo_plus_exp"])

    print("C) command end-to-end")
    tmp = Path(tempfile.mkdtemp(prefix="fgs-nonce-"))
    try:
        board = tmp / "projects"
        board.mkdir(parents=True, exist_ok=True)
        run(board, "init", "np", "--kind", "ctf", "--goal", "nonce test")

        p = run(board, "nonce", "np", "--json")
        gen = json.loads(p.stdout or "{}")
        cid = gen.get("id")
        check("generate returns id", bool(cid), str(gen)[:200])
        check("generate returns nonce + expected", bool(gen.get("nonce")) and bool(gen.get("expected")) and gen.get("nonce") != gen.get("expected"), str(gen)[:200])

        p = run(board, "nonce", "np", "--check", str(cid),
                "--text", "answer " + gen["expected"], "--json")
        res = json.loads(p.stdout or "{}")
        check("check expected -> executed", res.get("verdict") == "executed", str(res)[:200])
        check("executed ok=true", res.get("ok") is True, str(res)[:200])
        check("executed rc=0", p.returncode == 0, p.returncode)

        p2 = run(board, "nonce", "np", "--check", str(cid),
                 "--text", "answer " + gen["expected"], "--json", ok=(0, 3))
        res2 = json.loads(p2.stdout or "{}")
        check("same challenge twice -> reused", res2.get("verdict") == "reused", str(res2)[:200])
        check("reused rc=3", p2.returncode == 3, p2.returncode)

        p = run(board, "nonce", "np", "--json")
        gen2 = json.loads(p.stdout or "{}")
        p3 = run(board, "nonce", "np", "--check", gen2["id"],
                 "--text", "echoed: " + gen2["nonce"], "--json", ok=(0, 3))
        res3 = json.loads(p3.stdout or "{}")
        check("cmd: echo -> reflected", res3.get("verdict") == "reflected", str(res3)[:200])
        check("cmd: reflected rc=3 (not a pass)", p3.returncode == 3, p3.returncode)

        p = run(board, "nonce", "np", "--list", "--json")
        lst = json.loads(p.stdout or "{}")
        check("--list shows 2", lst.get("count") == 2, str(lst)[:200])

        p = run(board, "nonce", "np", "--check", "nope", "--text", "x", ok=(3,))
        check("unknown challenge rc=3", p.returncode == 3, p.returncode)

        p = run(board, "nonce", "nosuchboard", ok=(2,))
        check("no board rc=2", p.returncode == 2, p.returncode)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("D) constraints")
    # ★ 用函数边界切，不用固定字符窗：固定窗会切进下一条命令，
    #   把别人的 save( 算到 nonce 头上（第一版就是这么假红的）。
    _start = src.find("def cmd_nonce")
    _end = src.find("\ndef ", _start + 1)
    body = src[_start:_end if _end > 0 else len(src)]
    check("nonce does not fire HTTP", "requests." not in body and "urlopen" not in body)
    check("nonce does not write graph", "\nsave(" not in body and "dump_graph" not in body)
    check("challenges live under work/",
          "def _nonce_path" in src and "_work_dir(pid)" in src)

    return report()


if __name__ == "__main__":
    sys.exit(main())
