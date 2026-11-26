#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_evidence_addressing.py — 证据正文按内容寻址（能被读者自己重算）

为什么（2026-11-17）：之前 verify 只记 16 位幂等键，正文从不落盘。
「事后改 log 再声称逐字通过」在结构上是可能的 —— 因为没有任何东西可被重算。
改造后：正文落 evidence/blobs/<前两位>/<sha256>.bin，solved/confirm 前重算。

断言（全部是跑出来的行为）：
  A. verify 后 blob 存在且 byte-equal，meta.evidence_sha/evidence_len 正确。
  B. 同输入重复 verify：rc=0、提示幂等、blob 不重复。
  C. blob 被删后重跑 verify 会补回（内容寻址幂等）。
  D. 篡改 blob（同长度换字节）→ solved / confirm 都拒，且点名「证据正文校验失败」。
  E. 缺 blob → solved 拒，且点名「证据正文缺失」。
  F. doctor：孤儿 blob → ⚠；缺失 blob → ✗ 且 rc=1。
  G. 旧章（之前盖章、无 evidence_sha）→ solved 仍放行，只提醒补指纹。
"""
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


def run(lab, *args):
    env = dict(os.environ, PI_LAB_BOARD_DIR=lab, PI_FGS_TOOFAST_SEC="0")
    r = subprocess.run([sys.executable, FGS, *args], capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       cwd=ROOT, env=env, timeout=180)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def graph_path(lab):
    return Path(lab) / "e1" / "graph.json"


def read_graph(lab):
    return json.loads(graph_path(lab).read_text(encoding="utf-8"))


def write_graph(lab, g):
    graph_path(lab).write_text(json.dumps(g, ensure_ascii=False, indent=2),
                               encoding="utf-8")


def blob_count(lab):
    root = Path(lab) / "e1" / "evidence" / "blobs"
    return len(list(root.rglob("*.bin"))) if root.exists() else 0


def main():
    lab = tempfile.mkdtemp(prefix="evaddr-")
    try:
        body = "GET /challenge -> 200 OK\nbody: flag{ev9_addr}\n"
        ev = os.path.join(lab, "raw.txt")
        with open(ev, "w", encoding="utf-8") as fh:
            fh.write(body)
        raw = body.encode("utf-8")
        sha = hashlib.sha256(raw).hexdigest()

        rc, out = run(lab, "kickoff", "e1", "--kind", "ctf", "--goal", "证据正文",
                      "--target", "http://demo")
        ck("A0 建板", rc == 0, out[-200:])
        rc, out = run(lab, "fact-add", "e1", "flag{ev9_addr}",
                      "--confidence", "verified", "--tool", "curl")
        ck("A0b fact-add", rc == 0, out[-200:])
        rc, out = run(lab, "relate", "e1", "--type", "satisfies",
                      "--from", "f001", "--to", "g001")
        ck("A0c 结论挂到 goal", rc == 0, out[-200:])

        print("A) verify 落盘：按内容寻址，正文 byte-equal")
        rc, out = run(lab, "verify", "e1", "f001", "--from", ev)
        ck("A1 rc=0", rc == 0, out[-300:])
        ck("A2 提示已落盘", "按内容落盘" in out, out[-300:])
        blob = Path(lab) / "e1" / "evidence" / "blobs" / sha[:2] / (sha + ".bin")
        ck("A3 blob 路径 <前两位>/<sha>.bin", blob.exists(), str(blob))
        ck("A4 blob 内容 byte-equal", blob.exists() and blob.read_bytes() == raw)
        m = (read_graph(lab)["nodes"]["f001"].get("meta") or {})
        ck("A5 meta.evidence_sha 是全量 sha", m.get("evidence_sha") == sha, str(m.get("evidence_sha")))
        ck("A6 meta.evidence_len 正确", m.get("evidence_len") == len(raw), str(m.get("evidence_len")))
        ck("A7 没有 .staging 残留",
           not list((Path(lab) / "e1" / "evidence").rglob("*.staging")))

        print("B) 重复 verify 幂等")
        rc, out = run(lab, "verify", "e1", "f001", "--from", ev)
        ck("B1 rc=0", rc == 0, out[-300:])
        ck("B2 提示幂等", "幂等" in out, out[-300:])
        ck("B3 blob 没变多", blob_count(lab) == 1, f"count={blob_count(lab)}")

        print("C) blob 被误删 → 重跑 verify 补回")
        blob.unlink()
        ck("C1 已删掉", not blob.exists())
        rc, out = run(lab, "verify", "e1", "f001", "--from", ev)
        ck("C2 rc=0", rc == 0, out[-300:])
        ck("C3 blob 补回且内容一致", blob.exists() and blob.read_bytes() == raw)

        print("D) 篡改正文 → solved / confirm 都拒（同长度换字节）")
        bad = bytearray(raw)
        bad[0] = ord("P") if bad[0] != ord("P") else ord("Q")
        blob.write_bytes(bytes(bad))
        ck("D0 长度没变", blob.stat().st_size == len(raw))
        rc, out = run(lab, "solved", "e1", "--fact", "f001", "--desc", "解出",
                      "--no-lanes", "单线测试")
        ck("D1 solved rc!=0", rc != 0, f"rc={rc}")
        ck("D2 点名证据正文校验失败", "证据正文校验失败" in out, out[-400:])
        rc, out = run(lab, "confirm", "e1", "--flag", "flag{ev9_addr}")
        ck("D3 confirm rc!=0", rc != 0, f"rc={rc}")
        ck("D4 confirm 也点名校验失败", "证据正文校验失败" in out, out[-400:])

        print("E) 正文缺失 → solved 拒")
        blob.unlink()
        rc, out = run(lab, "solved", "e1", "--fact", "f001", "--desc", "解出",
                      "--no-lanes", "单线测试")
        ck("E1 rc!=0", rc != 0, f"rc={rc}")
        ck("E2 点名证据正文缺失", "证据正文缺失" in out, out[-400:])

        print("F) doctor：孤儿 → ⚠；缺失 → ✗ 且 rc=1")
        rc, out = run(lab, "doctor", "e1")
        ck("F1 缺失时报 bad 且 rc=1", rc == 1 and "证据正文缺失" in out, out[-500:])
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(raw)
        orphan = Path(lab) / "e1" / "evidence" / "blobs" / "ff" / ("f" * 64 + ".bin")
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_bytes(b"orphan")
        rc, out = run(lab, "doctor", "e1")
        ck("F2 孤儿 blob 有提醒", "孤儿证据正文" in out, out[-600:])
        ck("F3 孤儿不算致命", rc == 0, f"rc={rc} {out[-300:]}")
        orphan.unlink()

        print("G) 旧章（无 evidence_sha）放行，只提醒补指纹")
        g = read_graph(lab)
        g["nodes"]["f001"]["meta"].pop("evidence_sha", None)
        g["nodes"]["f001"]["meta"].pop("evidence_len", None)
        write_graph(lab, g)
        rc, out = run(lab, "solved", "e1", "--fact", "f001", "--desc", "解出",
                      "--no-lanes", "单线测试")
        ck("G1 仍放行（历史板子不能被新规则锁死）", rc == 0, out[-400:])
        ck("G2 提醒旧章", "未记指纹" in out, out[-400:])

        print(f"\n{_passed}/{_passed + _failed} 断言通过")
        return 0 if _failed == 0 else 1
    finally:
        shutil.rmtree(lab, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
