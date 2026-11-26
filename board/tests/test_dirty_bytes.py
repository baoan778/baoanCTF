#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_dirty_bytes.py — #9 脏字节不许丢掉整轮记录

血账（2026-11-17）：把 b"flag{abc\xff\xfe_def}" 喂进 fact 正文 →
  rc=1，cmd_fact_add → save() → json.dumps(..., ensure_ascii=False) →
  UnicodeEncodeError: 'utf-8' codec can't encode characters ... surrogates not allowed。
效果不是「丢一条活动记录」，而是**整张图写不出去 = 整轮结论消失**。

三条断言（覆盖链路上两个真正会崩的点）：
  A. 进程内走 cmd_fact_add，正文含**孤立代理字符**（POSIX argv / 外部来源的典型形态）
     → 不抛异常，save() 成功，load() 能原样读回（ensure_ascii=True 的正中靶心）。
  B. CLI verify 读管道（sys.stdin.buffer.read().decode("utf-8","replace")）：
     stdin 里混 b"\xff\xfe" 仍要 rc=0 且逐字溯源通过（旧代码 UnicodeDecodeError）。
  C. 脏写入后 graph.json 仍是合法 UTF-8 / 合法 JSON，fgs show 能读。

    python3 board/tests/test_dirty_bytes.py
"""
import argparse
import importlib.util
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
        print(f"  ✗ {name}" + (f"   [{detail}]" if detail else ""))


def _load_fgs(lab):
    os.environ["PI_LAB_BOARD_DIR"] = lab
    spec = importlib.util.spec_from_file_location("fgs_dirtybytes", FGS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ns(**kw):
    base = dict(project=None, text=None, doc=None, confidence="unverified", frm=None,
                supersedes=None, evidence=None, actor="tester", phase=None, action=None,
                verdict=None, severity=None, proof_kind=None, origin=None,
                tool=None, target=None, time=None, ioc=None)
    base.update(kw)
    return argparse.Namespace(**base)


def main():
    lab = tempfile.mkdtemp(prefix="dirtybytes-")
    try:
        env = dict(os.environ, PI_LAB_BOARD_DIR=lab)
        r = subprocess.run([sys.executable, FGS, "kickoff", "d1", "--kind", "ctf",
                            "--goal", "脏字节", "--target", "http://demo"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=ROOT, env=env, timeout=120)
        ck("建板 rc=0", r.returncode == 0, (r.stdout or "")[-200:])

        # ── A. 孤立代理字符走 cmd_fact_add → save() ──────────────────
        print("A) fact 正文含孤立代理字符必须写得进、读得回")
        mod = _load_fgs(lab)
        dirty = "flag{abc\udcff\udcfe_def}"
        try:
            mod.cmd_fact_add(_ns(project="d1", text=dirty))
            ok_a = True
        except Exception as e:                      # noqa: BLE001
            ok_a = False
            print(f"    抛了：{type(e).__name__}: {e}")
        ck("A1 cmd_fact_add 不抛 UnicodeEncodeError", ok_a)
        g = mod.load("d1")
        texts = [n.get("text", "") for n in g["nodes"].values() if n.get("type") == "fact"]
        ck("A2 load() 读回原值（转义不丢信息）", dirty in texts, str(texts)[:200])

        # ── B. verify 读脏管道 ───────────────────────────────────────
        print("B) verify --stdin 混 b'\\xff\\xfe' 必须 rc=0")
        mod.cmd_fact_add(_ns(project="d1", text="flag{clean_def}"))
        fid = [i for i, n in mod.load("d1")["nodes"].items()
               if n.get("text") == "flag{clean_def}"][0]
        r = subprocess.run([sys.executable, FGS, "verify", "d1", fid],
                           input=b"HTTP/1.1 200 OK\r\nflag{clean_def}\xff\xfe tail\r\n",
                           capture_output=True, cwd=ROOT, env=env, timeout=120)
        out = (r.stdout + r.stderr).decode("utf-8", "replace")
        ck("B1 rc=0（旧代码在这里 UnicodeDecodeError）", r.returncode == 0,
           f"rc={r.returncode} {out[-300:]}")
        ck("B2 仍完成逐字溯源", "逐字溯源通过" in out, out[-300:])

        # ── C. 落盘产物仍是合法 UTF-8 / JSON ─────────────────────────
        print("C) graph.json 必须仍是合法 UTF-8 + 合法 JSON")
        raw = pathlib.Path(lab, "d1", "graph.json").read_bytes()
        try:
            raw.decode("utf-8")
            utf8_ok = True
        except UnicodeDecodeError:
            utf8_ok = False
        ck("C1 graph.json 是合法 UTF-8", utf8_ok)
        try:
            json.loads(raw.decode("utf-8"))
            json_ok = True
        except Exception:                           # noqa: BLE001
            json_ok = False
        ck("C2 graph.json 是合法 JSON", json_ok)
        r = subprocess.run([sys.executable, FGS, "show", "d1"], capture_output=True,
                           cwd=ROOT, env=env, timeout=120)
        ck("C3 fgs show 能读（图没坏）", r.returncode == 0,
           (r.stdout + r.stderr).decode("utf-8", "replace")[-200:])

        print(f"\n{_passed}/{_passed + _failed} 断言通过")
        return 0 if _failed == 0 else 1
    finally:
        shutil.rmtree(lab, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
