#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_flagq.py — flagq 判分模块的穷举测试（T2 验收）

★ 这是**独立脚本**，不是 pytest：`python3 board/tests/test_flagq.py`
   （本仓惯例，见 board/tests/_pkgpaths.py；跑 `python3 -m pytest` 会「no tests ran」）
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from flagq import (FLAGISH_FLOOR, SPRAY_MIN, ambiguous_inner, decoy_prefix,
                   explain, flagishness, judge, natural_language_inner)

_pass, _fail = 0, 0
_fails = []


def ck(name, cond, detail=""):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        _fails.append(f"{name} {detail}")


# ── 1. 真 flag 形态必须通过 ────────────────────────────────────────────
REAL = [
    ("HTB{abc123def456}", {"htb"}),
    ("flag{a1b2c3d4e5f6}", set()),
    ("CTF{deadbeef00cafe11}", {"ctf"}),
    ("picoCTF{5a1l_4w4y_w1th_fl4gs}", {"picoctf"}),
    ("flag{01234567-89ab-cdef-0123-456789abcdef}", set()),
]
for tok, kp in REAL:
    ck(f"real:{tok}", flagishness(tok, kp) >= FLAGISH_FLOOR,
       f"score={flagishness(tok, kp):.1f}")
    ck(f"real_not_nl:{tok}", not natural_language_inner(tok))
    ck(f"real_not_decoy:{tok}", not decoy_prefix(tok))

# ── 2. 诱饵前缀必须被打死 ─────────────────────────────────────────────
DECOY = ["notflag{abc123def}", "fake_flag{a1b2c3d4}", "dummy{x9y8z7w6}",
         "decoy{deadbeef12}", "honeypot{aaaabbbbcccc}", "nope{1234abcd}"]
for tok in DECOY:
    ck(f"decoy_direct:{tok}", decoy_prefix(tok), explain(tok).__str__())
    ck(f"decoy_lowscore:{tok}", flagishness(tok, set()) < FLAGISH_FLOOR,
       f"score={flagishness(tok, set()):.1f}")

# ── 3. 自然语言内层必须被打死 ─────────────────────────────────────────
NL = ["flag{这是一段中文说明文字}", "flag{请把真正的flag填在这里}", "flag{flag goes here please}"]
for tok in NL:
    ck(f"nl:{tok}", natural_language_inner(tok), str(explain(tok)))

# ── 4. 代码片段（shell 元字符）必须低分 ───────────────────────────────
for tok in ["flag{a$b(c)}", "flag{x?a*b}", "flag{p|q;r}"]:
    ck(f"shellmeta:{tok}", flagishness(tok, set()) < FLAGISH_FLOOR,
       f"score={flagishness(tok, set()):.1f}")

# ── 5. 扫射：同形状 ≥ SPRAY_MIN 必须全部降级 ──────────────────────────
spray = [f"HTB{{abcd{i:04d}efgh}}" for i in range(SPRAY_MIN)]
res = judge(spray, {"htb"}, spray_min=SPRAY_MIN)
ck("spray_all_dropped", len(res["kept"]) == 0, f"kept={res['kept']}")
ck("spray_group_found", len(res["sprayed"]) >= 1, str(res["sprayed"]))
ck("spray_reason_mentions", any("扫射" in w for _, ws in res["dropped"] for w in ws))

# 少于阈值不算扫射
few = spray[:SPRAY_MIN - 1]
res2 = judge(few, {"htb"}, spray_min=SPRAY_MIN)
ck("spray_below_threshold_kept", len(res2["kept"]) == len(few),
   f"kept={len(res2['kept'])} of {len(few)}")

# ── 6. 占位/样例上下文 ────────────────────────────────────────────────
res3 = judge(["样例 CTF{placeholder} 不是真 flag"], {"ctf"})
ck("ctx_placeholder_dropped", len(res3["kept"]) == 0, str(res3))

# ── 7. judge 不修改输入 ───────────────────────────────────────────────
orig = ["HTB{abc123def456}", "notflag{zz9999}"]
snapshot = list(orig)
judge(orig, {"htb"})
ck("judge_pure_no_mutation", orig == snapshot, f"{orig}")

# ── 8. 空输入 / 畸形输入不崩 ──────────────────────────────────────────
ck("empty_ok", judge([])["kept"] == [])
ck("none_ok", judge(None)["kept"] == [])
ck("garbage_ok", judge(["", "   ", "no-braces-here", "{x}"]) is not None)
ck("malformed_score", flagishness("nobrace") == -99.0)

# ── 9. explain 必须给出人话理由 ───────────────────────────────────────
e = explain("notflag{abc123}")
ck("explain_has_reason", any("诱饵" in x for x in e), str(e))
e2 = explain("nobrace")
ck("explain_malformed", len(e2) == 1 and "形状" in e2[0], str(e2))

# ── 10. 已知前缀集合影响判分（不写死平台）─────────────────────────────
a = flagishness("XYZ{aaaabbbbccccdddd}", {"xyz"})
b = flagishness("XYZ{aaaabbbbccccdddd}", {"htb"})
ck("prefix_set_matters", a > b, f"known={a} unknown={b}")

print("=" * 60)
for f in _fails:
    print("FAIL:", f)
print(f"pass={_pass} fail={_fail}")
sys.exit(0 if _fail == 0 else 1)
