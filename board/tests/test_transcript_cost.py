#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_transcript_cost.py — #49 逐 Step 成本（transcript token 四分类计价）

为什么（2026-11-17）：成本画像原先只有「全项目合计」的 token/cost，判「这题值不值得继续」
时看不出**哪一路烧了钱没产出**。ARTEX 把每步的 input/output/cache_read/cache_write_tokens
落进 conversation_activities（db/schema.sql:970-990），baoan 没有 DB，但每个 Step 都能
绑一个 pi 会话（fgs bind），会话 JSONL 里就有四分类 —— 按 Step 归集即可。

判据（跑出来的行为）：
  A. 有 --usage + --by-step 时打印逐 Step 表：四分类 token、cost、产出 fact。
  B. 会话文件不在 --usage 目录 → 未知（未记费），不当 0。
  C. 会话文件里没有 usage 行 → 未知（未记费），不当 0。
  D. 没给 --usage → 明说「未知（未记费）」，不凭空算。
  E. --json 的 by_step.rows 字段齐全，None = 不知道；未绑定会话单独列，不摊派。
  F. 静态兜底：helper / CLI flag / produced 边走 active_edges + type / 文档 §4.6n。
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


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for obj in rows:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def main():
    lab = tempfile.mkdtemp(prefix="cost49-")
    fgs(lab, "kickoff", "p1", "--kind", "ctf", "--goal", "g", "--target", "lab.test")
    for text in ("打 A 入口", "打 B 入口", "试空会话", "试缺文件"):
        fgs(lab, "step-add", "p1", text, "--target", "lab.test")
    for i, who in ((1, "w1"), (2, "w2"), (3, "w3"), (4, "w4")):
        rc, out = fgs(lab, "step-claim", "p1", "s%03d" % i, "--by", who)
        ck("S claim s%03d rc=0" % i, rc == 0, out[:200])
    fgs(lab, "bind", "p1", "s001", "--agent", "sess-a")
    fgs(lab, "bind", "p1", "s002", "--agent", "sess-b")
    fgs(lab, "bind", "p1", "s003", "--agent", "sess-empty")
    fgs(lab, "bind", "p1", "s004", "--agent", "sess-missing")
    rc, out = fgs(lab, "step-conclude", "p1", "s001", "A 入口有 SQLi",
                  "--confidence", "verified", "--evidence", "x")
    ck("S 收口 s001 rc=0", rc == 0, out[:200])

    sess = Path(lab) / "sessions"
    sess.mkdir()
    write_jsonl(sess / "sess-a.jsonl", [
        {"message": {"role": "assistant", "usage": {"input": 100, "output": 40,
                                                    "cacheRead": 10, "cacheWrite": 5,
                                                    "totalTokens": 155,
                                                    "cost": {"total": 0.0123}}}},
        {"message": {"role": "assistant", "usage": {"input": 50, "output": 20,
                                                    "totalTokens": 70,
                                                    "cost": {"total": 0.001}}}},
    ])
    write_jsonl(sess / "sess-b.jsonl", [
        {"message": {"role": "assistant", "usage": {"input": 7, "output": 3, "totalTokens": 10}}},
    ])
    write_jsonl(sess / "orphan.jsonl", [
        {"message": {"role": "assistant", "usage": {"input": 999, "output": 1, "totalTokens": 1000}}},
    ])
    write_jsonl(sess / "sess-empty.jsonl", [{"message": {"role": "assistant"}}])

    print("A) 逐 Step 表")
    rc, out = fgs(lab, "cost", "p1", "--usage", str(sess), "--by-step")
    ck("A1 rc=0 且有表头", rc == 0 and "逐 Step 成本" in out, out[:300])
    rows = {}
    for line in out.splitlines():
        t = line.strip().split()
        if t and t[0] in ("s001", "s002", "s003", "s004"):
            rows[t[0]] = line
    ck("A2 四行都在", sorted(rows) == ["s001", "s002", "s003", "s004"], sorted(rows))
    r1 = rows.get("s001", "")
    ck("A3 s001 会话名", "sess-a" in r1, r1)
    ck("A4 s001 四分类含缓写 5", "225" in r1 and "150" in r1 and "60" in r1 and " 5" in r1, r1)
    ck("A5 s001 cost 0.0133", "0.0133" in r1, r1)
    ck("A6 s001 产出 f001", "f001" in r1, r1)
    r2 = rows.get("s002", "")
    ck("A7 s002 token 10 且 cost 未知", "10" in r2 and "未知" in r2, r2)
    r3 = rows.get("s003", "")
    ck("A8 s003 空会话报未知", "未知" in r3 and "0.0000" not in r3, r3)
    r4 = rows.get("s004", "")
    ck("A9 s004 缺文件自报目录里没有", "目录里没有" in r4, r4)
    ck("A10 未绑定会话列出 orphan 1,000", "orphan" in out and "1,000" in out, out[-400:])
    ck("A11 明说不摊派", "不摊到任何 Step" in out, out[-400:])
    tok = [l for l in out.splitlines() if "token 用量" in l]
    ck("A12 合计 1,235（含未绑定）", bool(tok) and "1,235" in tok[0], tok)

    print("B) --json")
    rc, out = fgs(lab, "cost", "p1", "--usage", str(sess), "--by-step", "--json")
    try:
        j = json.loads(out[out.index("{"):])
    except Exception as e:
        j = None
        print("   json err:", repr(e), out[:200])
    ck("B1 json 合法且含 by_step", rc == 0 and isinstance(j, dict) and "by_step" in j, out[:200])
    bs = (j or {}).get("by_step") or {}
    brows = {x["step"]: x for x in (bs.get("rows") or [])}
    ck("B2 rows 四行", sorted(brows) == ["s001", "s002", "s003", "s004"], sorted(brows))
    b1 = brows.get("s001") or {}
    ck("B3 字段齐全", set(["step", "status", "session", "tokens", "input", "output",
                            "cache_read", "cache_write", "cost", "facts"]).issubset(b1), sorted(b1))
    ck("B4 四分类数值", (b1.get("tokens"), b1.get("input"), b1.get("output"),
                          b1.get("cache_read"), b1.get("cache_write")) == (225, 150, 60, 10, 5), b1)
    ck("B5 cost 与 facts", b1.get("cost") == 0.0133 and b1.get("facts") == ["f001"], b1)
    ck("B6 s002 没回 cost 时为 None", (brows.get("s002") or {}).get("cost") is None, brows.get("s002"))
    ck("B7 s003/s004 tokens 为 None（不知道，不是 0）",
       (brows.get("s003") or {}).get("tokens") is None and (brows.get("s004") or {}).get("tokens") is None,
       [brows.get("s003"), brows.get("s004")])
    unb = bs.get("unbound") or []
    ck("B8 unbound 只有 orphan", [u.get("session") for u in unb] == ["orphan"], unb)

    print("C) 没给 --usage")
    rc, out = fgs(lab, "cost", "p1", "--by-step")
    ck("C1 rc=0", rc == 0, out[:200])
    ck("C2 明说未知（未记费）", "未知（未记费" in out, out[-300:])
    ck("C3 不跑出表头", "逐 Step 成本（#49" not in out, out[-300:])

    print("D) 静态兜底")
    src = open(FGS, encoding="utf-8").read()
    ck("D1 helper 在", "def _usage_by_step(" in src and "def _usage_per_file(" in src, "")
    ck("D2 produced 边走 active_edges + type",
       'for e in active_edges(g):' in src and 'if e.get("type") == "produced" and e.get("to"):' in src, "")
    ck("D3 CLI flag 在", '"--by-step"' in src, "")
    ck("D4 json 里带 by_step", '"by_step": by_step,' in src, "")
    ck("D5 归不上不摊派写进代码", "不摊派" in src, "")
    doc = open(os.path.join(ROOT, "docs", "并行工作.md"), encoding="utf-8").read()
    ck("D6 文档 §4.6n 在", "### 4.6n" in doc, "")
    ck("D7 文档写了三条边界", "不摊派" in doc and "不当 0" in doc, "")

    print()
    print("test_transcript_cost: %d passed, %d failed" % (_passed, _failed))
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
