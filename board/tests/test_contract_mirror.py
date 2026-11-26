#!/usr/bin/env python3
"""test_contract_mirror.py —— 认知契约镜像自检（借 OS2.0 的 cognition.verifyMirrors 思路）。

为什么要有它
-----------
语义常量在本仓有**多个物理副本**：
  · 证据等级三档：board/fgs.py 的 CONFIDENCE（代码唯一真源）
      ↔ board/schema.md（契约文档）↔ AGENTS.md（常驻提示词）
  · 台账三段节名：tools/ledger.mjs 的 SECTIONS ↔ board/fgs.py 的 LEDGER_SECTIONS
两份副本各自演进 → 文档写一套、代码跑另一套，模型读文档办事就会被门禁拒。
这类漂移**不会报错**，只会让人困惑半天（"文档明明写可以"）。

与 OS2.0 的差别（如实标注）
--------------------------
它的 verifyMirrors() 在**插件启动时**跑，且在 TypeScript 里逐字比对静态 md；
本仓是多语言（py + mjs + md），启动期跨语言比对代价大，所以放在 selfcheck
（每次体检跑一次）。**能力等价、时机不同** —— 这点写清楚，不假装照搬。

    python3 board/tests/test_contract_mirror.py
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys as _sys
import tempfile

try:
    for _s in (_sys.stdout, _sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = pathlib.Path(__file__).resolve().parents[2]
FGS = LAB / "board" / "fgs.py"
SCHEMA = LAB / "board" / "schema.md"
AGENTS = LAB / "AGENTS.md"
LEDGER = LAB / "tools" / "ledger.mjs"

checks: list = []


def ok(name, cond, extra=""):
    checks.append((name, bool(cond), str(extra)))


def _report():
    bad = [c for c in checks if not c[1]]
    for name, good, extra in checks:
        mark = "✓" if good else "✗"
        print(f"  {mark} {name}" + (f"   {extra[:240]}" if extra and not good else ""))
    print(f"\n通过 {len(checks) - len(bad)} / 失败 {len(bad)}")
    if bad:
        print("失败项：" + "、".join(c[0] for c in bad))
        return 1
    print("✓ 认知契约镜像：全部通过")
    return 0


DQ = chr(34)


def main() -> int:
    for p in (FGS, SCHEMA, AGENTS, LEDGER):
        ok(p.name + " 存在", p.is_file())
    if not all(p.is_file() for p in (FGS, SCHEMA, AGENTS, LEDGER)):
        return _report()

    fgs = FGS.read_text(encoding="utf-8", errors="replace")
    schema = SCHEMA.read_text(encoding="utf-8", errors="replace")
    agents = AGENTS.read_text(encoding="utf-8", errors="replace")
    ledger = LEDGER.read_text(encoding="utf-8", errors="replace")

    # ① 证据等级：代码 vs 文档
    m = re.search(r"^CONFIDENCE\s*=\s*\(([^)]*)\)", fgs, re.M)
    ok("fgs.py 有 CONFIDENCE 常量", bool(m))
    code_set = set(re.findall("[" + DQ + chr(39) + "]([a-z_]+)[" + DQ + chr(39) + "]", m.group(1))) if m else set()
    ok("代码里是 3 档", len(code_set) == 3, str(sorted(code_set)))

    want = {"verified", "inferred", "unverified"}
    doc_set = {lv for lv in want if ("`" + lv + "`") in schema}
    ok("schema.md 三档齐全", doc_set == want, str(sorted(doc_set)))
    ok("代码与文档三档一致", code_set == doc_set,
       "code=" + str(sorted(code_set)) + " doc=" + str(sorted(doc_set)))

    # ② 台账三段节名：ledger.mjs vs fgs.py
    seg = ledger.split("export const SECTIONS", 1)
    ok("ledger.mjs 有 SECTIONS", len(seg) == 2)
    body = seg[1].split("};", 1)[0] if len(seg) == 2 else ""
    js_names = set(re.findall(DQ + "([^" + DQ + "]+)" + DQ, body))
    py_secs = re.search(r"^LEDGER_SECTIONS\s*=\s*\[([^\]]*)\]", fgs, re.M)
    py_names = set(re.findall(DQ + "([^" + DQ + "]+)" + DQ, py_secs.group(1))) if py_secs else set()
    ok("ledger.mjs 有三段节名", len(js_names) == 3, str(sorted(js_names)))
    ok("fgs.py 有三段节名", len(py_names) == 3, str(sorted(py_names)))
    ok("两侧节名完全一致（漂移会静默失效）", js_names == py_names,
       "mjs=" + str(sorted(js_names)) + " py=" + str(sorted(py_names)))

    # ③ verified 是门禁根（文档两处都要在）
    bt = chr(96)
    ok("schema.md 明写只有 verified 能 solved",
       ("只有这个能宣告 solved" in schema) or (bt + "verified" + bt + " 能当结论") in schema)
    ok("AGENTS.md 复述了这条硬规则",
       (bt + "verified" + bt + " 算结论") in agents)

    # ③b 角色配置镜像（2026-11-13 加）：同一角色有**两份物理副本**
    #     agent-template/roles/*.json（下发给 ~/.pi-ctf/agent/roles）
    #     work/ui/roles-pack/*.json（UI「重置角色」时的内置预设）
    #     实测踩到：ctf 的 autoCompaction 一边 true 一边 false —— 用户在 UI 里点一次重置，
    #     自动压缩就被关掉，而长题跑到上下文墙是**不可恢复**的（ledger-compact 也失去触发点）。
    #     这类漂移不报错、不崩，只在最贵的时候发作，所以必须机器看住。
    tpl_roles = LAB / "agent-template" / "roles"
    pack_roles = LAB / "work" / "ui" / "roles-pack"
    ok("两侧角色目录都在", tpl_roles.is_dir() and pack_roles.is_dir())
    if tpl_roles.is_dir() and pack_roles.is_dir():
        pairs = []
        for f in sorted(tpl_roles.glob("*.json")):
            g = pack_roles / f.name
            if g.is_file():
                pairs.append((f, g))
        ok("至少有一对可比角色", len(pairs) >= 1, str(len(pairs)))
        for f, g in pairs:
            try:
                a = json.loads(f.read_text(encoding="utf-8", errors="replace"))
                b = json.loads(g.read_text(encoding="utf-8", errors="replace"))
            except Exception as e:
                ok(f.name + " 两侧都能解析", False, str(e)[:120])
                continue
            for key in ("autoCompaction", "thinkingLevel", "id"):
                ok(f.name + " 的 " + key + " 两侧同值",
                   a.get(key) == b.get(key),
                   "template=" + str(a.get(key)) + " pack=" + str(b.get(key)))

    # ④ 台账四档 vs fgs 三档：差异必须显式交代
    ok("ledger.mjs 有 EVIDENCE_LEVELS", "EVIDENCE_LEVELS" in ledger)
    ok("台账四档含 observed/assumption（比 fgs 细）",
       ("observed" in ledger) and ("assumption" in ledger))
    ok("两套等级差异有文档交代", ("observed" in schema) or ("台账" in schema))

    # ⑤ 运行期造病：非 verified 授权 solved 必须被拒
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        env = dict(os.environ)
        env["PI_LAB_BOARD_DIR"] = str(root / "projects")
        env["PI_LAB_WORK_DIR"] = str(root / "work")
        env["PYTHONIOENCODING"] = "utf-8"

        def fgs_run(*args):
            return subprocess.run([_sys.executable, str(FGS), *args],  # noqa: S603
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=120, cwd=str(LAB), env=env)

        fgs_run("init", "cc", "--goal", "契约")
        fgs_run("step-add", "cc", "探面")
        # step-claim 必须带 --by（签名实测：step-claim --by BY project step）
        r = fgs_run("step-claim", "cc", "s001", "--by", "contract-test")
        ok("冒烟前置：认领 step 成功", r.returncode == 0, (r.stdout + r.stderr)[-200:])

        # step-conclude 的 fact 是**位置参数**（--fact 不存在，实测踩过）
        r1 = fgs_run("step-conclude", "cc", "s001", "契约冒烟事实",
                     "--confidence", "inferred")
        ok("inferred 能落 Fact（允许，只是不能当结论）", r1.returncode == 0,
           (r1.stdout + r1.stderr)[-240:])
        mm = re.search(r"(f\d+)", r1.stdout)
        if mm:
            fid = mm.group(1)
            r2 = fgs_run("solved", "cc", "--fact", fid, "--desc", "越级",
                         "--no-lanes", "契约测试")
            ok("非 verified 的 Fact 授权 solved 必被拒（rc=3）", r2.returncode == 3,
               "rc=" + str(r2.returncode) + " " + (r2.stdout + r2.stderr)[-200:])
        else:
            ok("能取到 Fact id（否则上一条无法验证）", False, r1.stdout[-200:])

        r3 = fgs_run("step-conclude", "cc", "s001", "x",
                     "--confidence", "made_up_level")
        ok("非法证据等级被拒（rc!=0）", r3.returncode != 0,
           "rc=" + str(r3.returncode) + " " + (r3.stdout + r3.stderr)[-200:])

    return _report()


if __name__ == "__main__":
    raise SystemExit(main())
