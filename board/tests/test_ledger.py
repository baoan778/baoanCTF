#!/usr/bin/env python3
"""test_ledger.py —— 分析台账（X-2 压缩穿越 + X-4 台账结构）回归。

治什么
------
上下文压缩由总结器重写，会**洗掉限定词**（"仅 GET，未测 POST" → "注入点在 search"），
之后推理全建立在变宽的前提上。所以要有一份**原样穿越**的分析台账。

三条红线（逐条守）
------------------
  ① 结构完整才注入（缺段 / 空段 → 不注入，宁缺勿误导）；
  ② 有界（超预算逐行截断 + 标全文路径，绝不变成新上下文炸弹）；
  ③ 只读（fgs ledger 不写图、不涨 revision、不进门禁）。

    python3 board/tests/test_ledger.py
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys as _sys
import tempfile

_sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _pkgpaths import find_node, require_ext  # noqa: E402

try:
    for _s in (_sys.stdout, _sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = pathlib.Path(__file__).resolve().parents[2]
MOD = LAB / "tools" / "ledger.mjs"
FGS = LAB / "board" / "fgs.py"
EXT = require_ext("ledger-compact.ts")
NODE = find_node()

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
    print("✓ 分析台账（X-2 压缩穿越 / X-4 台账结构）：全部通过")
    return 0


def fgs(board: str, work: str, *args: str):
    env = dict(os.environ)
    env["PI_LAB_BOARD_DIR"] = board
    env["PI_LAB_WORK_DIR"] = work
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(  # noqa: S603
        [_sys.executable, str(FGS), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, cwd=str(LAB), env=env,
    )


def main() -> int:
    ok("tools/ledger.mjs 存在", MOD.is_file())
    ok("ledger-compact.ts 存在", EXT.is_file())
    ok("board/fgs.py 存在", FGS.is_file())
    ok("node 可用", bool(NODE))
    if not (MOD.is_file() and EXT.is_file() and FGS.is_file() and NODE):
        return _report()

    # ① 纯逻辑自检
    p = subprocess.run([NODE, str(MOD), "--selftest"], capture_output=True,  # noqa: S603
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    ok("ledger.mjs --selftest 全绿", p.returncode == 0 and "OK" in p.stdout,
       (p.stdout + p.stderr)[-300:])

    # ② 扩展静态守卫（穿越能力靠它）
    src = EXT.read_text(encoding="utf-8", errors="replace")
    ok("挂 context（每次 LLM 调用前）", 'pi.on("context"' in src)
    ok("挂 session_before_compact（压缩时打标）", 'pi.on("session_before_compact"' in src)
    ok("压缩后必注（pendingCompact）", "pendingCompact" in src)
    ok("替换语义带回原 messages", "...event.messages" in src)
    ok("有界：maxTokens 4000", "4000" in src)
    ok("fail-open 注释在", "fail-open" in src)
    ok("逃生门：.no-ledger", ".no-ledger" in src)
    ok("逃生门：PI_LAB_LEDGER", "PI_LAB_LEDGER" in src)
    ok("逃生门：/ledger 命令", 'registerCommand("ledger"' in src)

    # ③ fgs ledger 只读 + 三段结构
    fsrc = FGS.read_text(encoding="utf-8", errors="replace")
    ok("fgs 有 ledger 子命令", "def cmd_ledger(" in fsrc)
    ok("三段节名齐全",
       all(s in fsrc for s in ("结论台账", "未测条件", "观测记录")))
    ok("与 flag 申报账本区分（注释里写明）", "申报台账" in fsrc)

    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        board = str(root / "projects")
        work = str(root / "work")
        (root / "projects").mkdir(parents=True, exist_ok=True)
        (root / "work").mkdir(parents=True, exist_ok=True)

        r = fgs(board, work, "init", "lt", "--goal", "冒烟")
        ok("建板成功", r.returncode == 0, (r.stdout + r.stderr)[-200:])

        # 未建台账 → rc=3 且提示建法
        r = fgs(board, work, "ledger", "lt")
        ok("未建台账 rc=3", r.returncode == 3, str(r.returncode))
        ok("未建台账提示 --init", "--init" in (r.stdout + r.stderr),
           (r.stdout + r.stderr)[-200:])

        # 建模板
        r = fgs(board, work, "ledger", "lt", "--init", "--goal", "冒烟")
        ok("建台账成功", r.returncode == 0, (r.stdout + r.stderr)[-200:])
        lf = root / "work" / "lt" / "ledger.md"
        ok("落在 work/<pid>/ledger.md", lf.is_file(), str(lf))

        # 空模板 → 不注入
        r = fgs(board, work, "ledger", "lt")
        ok("空模板提示不会被注入", "不会被注入" in r.stdout, r.stdout[-200:])
        r = fgs(board, work, "ledger", "lt", "--json")
        # 2026-11-21 (R7-D2): json.loads(r.stdout) 在 stdout 为空时抛
        # JSONDecodeError（全量回归里这么红过一次，重跑又绿）。
        # 空 stdout 通常意味着 fgs 崩了或被信号打死 —— 直接解析只会丢掉真正的错误信息。
        ok("ledger --json 有输出", bool(r.stdout.strip()),
           "rc=%s out=%r err=%r" % (r.returncode, (r.stdout or "")[:200], (r.stderr or "")[-300:]))
        lj = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}
        ok("空模板 ok=true（结构对）", lj.get("ok") is True, r.stdout[:200])
        ok("空模板三节都未填充",
           all(not v["filled"] for v in lj["sections"].values()), json.dumps(lj["sections"], ensure_ascii=False))

        # 填内容 → 会被注入
        t = lf.read_text(encoding="utf-8")
        t = t.replace("- （暂无）", "- SQLi 注入点=search ｜ observed ｜ 仅 GET，POST 未测", 1)
        t = t.replace("- （暂无）", "- POST body 未测 / 时间盲注未测", 1)
        t = t.replace("- （暂无）", "- GET /search?q=1 返回 500，含 MySQL 版本", 1)
        lf.write_text(t, encoding="utf-8")
        r = fgs(board, work, "ledger", "lt", "--json")
        lj = json.loads(r.stdout)
        ok("填了内容三节都 filled",
           all(v["filled"] for v in lj["sections"].values()), json.dumps(lj["sections"], ensure_ascii=False))
        r = fgs(board, work, "ledger", "lt")
        ok("人读视图给出注入提示", "原样" in r.stdout, r.stdout[-240:])

        # 缺段 → ok=false（不注入的依据）
        lf.write_text("# 只有结论\n\n## 结论台账\n- x\n", encoding="utf-8")
        r = fgs(board, work, "ledger", "lt", "--json")
        lj2 = json.loads(r.stdout)
        ok("缺段 ok=false", lj2.get("ok") is False)
        ok("缺段列出 missing", set(lj2.get("missing") or []) == {"未测条件", "观测记录"},
           str(lj2.get("missing")))

        # 只读：不涨 revision
        rev_before = fgs(board, work, "show", "lt", "--json")
        n1 = json.loads(rev_before.stdout).get("revision")
        fgs(board, work, "ledger", "lt")
        rev_after = fgs(board, work, "show", "lt", "--json")
        n2 = json.loads(rev_after.stdout).get("revision")
        ok("fgs ledger 只读（revision 不涨）", n1 == n2, f"{n1} -> {n2}")

        # 运行期：renderForInject 对空模板返回 null，对真内容返回字符串
        js = ""
        js += "const m = await import(process.env.MOD_URL);\n"
        js += "const tpl = m.buildTemplate(\"lt\", \"冒烟\");\n"
        js += "const real = tpl.replace(m.SECTIONS ? \"- （暂无）\" : \"- （暂无）\", \"- 真结论 ｜ observed ｜ 仅 GET\");\n"
        js += "const out = { tplNull: m.renderForInject(tpl), realStr: typeof m.renderForInject(real, { path: \"/x/ledger.md\" }),\n"
        js += "  hasDecl: !!(m.renderForInject(real) || \"\").includes(\"原样穿越\") };\n"
        js += "console.log(JSON.stringify(out));\n"
        drv = root / "drv.mjs"
        drv.write_text(js, encoding="utf-8")
        p2 = subprocess.run([NODE, "--no-warnings", str(drv)], capture_output=True,  # noqa: S603
                            text=True, encoding="utf-8", errors="replace", timeout=120,
                            cwd=str(root), env={**os.environ, "MOD_URL": MOD.as_uri()})
        ok("运行期脚本成功", p2.returncode == 0, (p2.stdout + p2.stderr)[-300:])
        if p2.returncode == 0:
            o = json.loads(p2.stdout.strip().splitlines()[-1])
            ok("空模板 renderForInject = null", o.get("tplNull") is None)
            ok("有内容 renderForInject 给字符串", o.get("realStr") == "string")
            ok("注入块含穿越声明", o.get("hasDecl") is True)

    return _report()


if __name__ == "__main__":
    raise SystemExit(main())
