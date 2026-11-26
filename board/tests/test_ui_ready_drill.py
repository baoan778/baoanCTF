#!/usr/bin/env python3
"""test_ui_ready_drill.py —— UI「就绪计数可对账」的**纯逻辑层**回归（借 OS2.0 的 X/Y=Σ明细 思路）。

为什么先把逻辑抽出来测
--------------------
OS2.0 控制台的就绪计数是 `X/Y = Σ(工具 + 包 + 镜像 + 模型 + 配置)`，且能点开看明细；
N=0 时按钮禁用而不是"点了没反应"。本仓 work/ui/server.mjs 里 scattered 着若干
`ready/limit`、`ok/err` 之类的计数，但**没有一条统一口径**，也点不开。

直接改前端（React）成本高且测不动；先把**判据**收成一个纯函数模块，
让"数字怎么来的"可穷举，前端只负责渲染 —— 这也是本仓一贯做法
（reflectwatch / idlewatch / lantern 都是纯逻辑 + 薄扩展）。

守什么
------
  ① 总数 = 各分类之和（不是手写常量，会漂）；
  ② 每一类都能给出明细条目（点开能看见"哪几个缺"）；
  ③ 全绿时 ratio=1 且 available=true；零就绪时 available=false（N=0 不许假装能点）；
  ④ 缺项必须**指名道姓**（report 里说出哪个缺），不许只给一个数字；

    python3 board/tests/test_ui_ready_drill.py
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys as _sys
import tempfile

_sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _pkgpaths import find_node  # noqa: E402

try:
    for _s in (_sys.stdout, _sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = pathlib.Path(__file__).resolve().parents[2]
MOD = LAB / "tools" / "readiness.mjs"
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
    print("✓ UI 就绪计数可对账（纯逻辑）：全部通过")
    return 0


def main() -> int:
    ok("tools/readiness.mjs 存在", MOD.is_file())
    ok("node 可用", bool(NODE))
    if not (MOD.is_file() and NODE):
        return _report()

    # ① 模块自带自检
    p = subprocess.run([NODE, str(MOD), "--selftest"], capture_output=True,  # noqa: S603
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    ok("readiness.mjs --selftest 全绿", p.returncode == 0 and "OK" in p.stdout,
       (p.stdout + p.stderr)[-300:])

    # ② 运行期：四种形态
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        drv = root / "drv.mjs"
        js = ""
        js += "const m = await import(process.env.MOD_URL);\n"
        js += "const cats = [\n"
        js += "  { key: \"tools\", label: \"外部工具\", items: [\n"
        js += "    { name: \"nmap\", ok: true }, { name: \"ffuf\", ok: true } ] },\n"
        js += "  { key: \"deps\", label: \"Python 依赖\", items: [\n"
        js += "    { name: \"pwntools\", ok: true }, { name: \"angr\", ok: false, why: \"未安装\" } ] },\n"
        js += "  { key: \"images\", label: \"镜像\", items: [ { name: \"pictf\", ok: true } ] },\n"
        js += "];\n"
        js += "const all = m.summarize(cats);\n"
        js += "const none = m.summarize([{ key: \"a\", label: \"x\", items: [\n"
        js += "  { name: \"p\", ok: false }, { name: \"q\", ok: false } ] }]);\n"
        js += "const empty = m.summarize([]);\n"
        js += "console.log(JSON.stringify({ all, none, empty,\n"
        js += "  drill: m.drill(cats, \"deps\") }));\n"
        drv.write_text(js, encoding="utf-8")
        # ★ 必须传**完整** os.environ：Windows 上 node 只给 PATH 会崩在
        #   InitializeOncePerProcess（缺 SystemRoot 等）。本测试第一版就这么踩了。
        _env = dict(__import__("os").environ)
        _env["MOD_URL"] = MOD.as_uri()
        p2 = subprocess.run([NODE, "--no-warnings", str(drv)], capture_output=True,  # noqa: S603
                            text=True, encoding="utf-8", errors="replace", timeout=120,
                            cwd=str(root), env=_env)
        ok("运行期脚本成功", p2.returncode == 0, (p2.stdout + p2.stderr)[-400:])
        if p2.returncode == 0:
            o = json.loads(p2.stdout.strip().splitlines()[-1])
            a = o.get("all") or {}
            ok("总数 = 各分类之和（5 = 2+2+1）", a.get("total") == 5, str(a))
            ok("就绪数正确（4）", a.get("ready") == 4, str(a))
            ok("ratio 是就绪/总数", abs((a.get("ratio") or 0) - 0.8) < 1e-9, str(a.get("ratio")))
            ok("有缺项时 available=true（可查看明细）", a.get("available") is True, str(a))
            ok("缺项被指名道姓", "angr" in json.dumps(a, ensure_ascii=False), json.dumps(a, ensure_ascii=False))
            ok("每类都带自己的计数", all("ready" in c and "total" in c for c in a.get("categories") or []),
               json.dumps(a, ensure_ascii=False))

            n = o.get("none") or {}
            ok("零就绪 → available=false（N=0 不许假装能点）", n.get("available") is False, str(n))
            ok("零就绪 → ratio=0", n.get("ratio") == 0, str(n))

            e = o.get("empty") or {}
            ok("无分类 → total=0 且不崩", e.get("total") == 0, str(e))
            ok("无分类 → available=false", e.get("available") is False, str(e))

            d = o.get("drill") or {}
            ok("drill 给某类明细", d.get("key") == "deps", str(d))
            ok("drill 列出每个条目", len(d.get("items") or []) == 2, json.dumps(d, ensure_ascii=False))

    return _report()


if __name__ == "__main__":
    raise SystemExit(main())
