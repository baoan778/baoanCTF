#!/usr/bin/env python3
"""WebUI 安全边界回归（2026-11-18 改：口令闸门删除后守**另一头**）。

背景：2026-11-05 曾加过口令闸门（AUTH_ON / token.txt / /api/auth /login / 前端口令框），
2026-11-11 定案"页面不要口令"，2026-11-18 用户定案**整条通路删掉**（不是关）。
于是本测试从"守闸门开着"翻转为"守闸门不再回来的同时，同源校验没被一起删掉"。

判据（源码层，不需要容器）：
  1. server.mjs 里不得再出现 AUTH_ON / loadToken / needAuth / sameToken / tokenOf /
     authRequired / UI_REQUIRE_TOKEN —— 删干净才叫删，留开关就还会有人打开它。
  2. sameOrigin() 必须还在，且 /api 写请求闸门仍在调它（删口令时最容易连带删错这层）。
  3. 前端不得再有 LoginGate / authCheck / authLogin / needToken。

跑法：python3 board/tests/test_ui_auth.py
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = Path(__file__).resolve().parents[2]
UI = LAB / "work" / "ui" / "server.mjs"
WEB = LAB / "work" / "ui" / "web" / "src"

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    mark = "\u2713 " if cond else "\u2717 "
    print("  " + mark + name + (("   " + str(detail)[:240]) if detail and not cond else ""))


def report():
    print("")
    print("\u901a\u8fc7 %d / \u5931\u8d25 %d" % (len(passed), len(failed)))
    if failed:
        print("failed: " + "\u3001".join(failed))
        return 1
    print("\u2713 WebUI \u5b89\u5168\u8fb9\u754c\uff1a\u5168\u90e8\u901a\u8fc7")
    return 0


# 口令闸门的全部符号：任何一个还在，就说明删了一半
GATE_SYMBOLS = [
    "AUTH_ON", "loadToken", "needAuth", "sameToken", "tokenOf",
    "authRequired", "UI_REQUIRE_TOKEN", "pictf_token",
]
FRONT_SYMBOLS = ["LoginGate", "authCheck", "authLogin", "needToken"]


def main():
    check("server.mjs exists", UI.is_file())
    if not UI.is_file():
        return report()
    src = UI.read_text(encoding="utf-8", errors="replace")

    print("A) \u53e3\u4ee4\u95f8\u95e8\u5df2\u5220\u5e72\u51c0\uff08\u7559\u5f00\u5173 = \u8fd8\u4f1a\u88ab\u6253\u5f00\uff09")
    for sym in GATE_SYMBOLS:
        check("\u2605 server.mjs \u4e0d\u5f97\u518d\u6709 " + sym, sym not in src)

    print("B) \u540c\u6e90\u6821\u9a8c\u5fc5\u987b\u5b58\u6d3b\uff08\u5220\u53e3\u4ee4\u65f6\u6700\u5bb9\u6613\u8fde\u5e26\u5220\u9519\uff09")
    check("\u2605 sameOrigin() \u5b9a\u4e49\u8fd8\u5728", "function sameOrigin(" in src)
    check("\u2605 /api \u5199\u8bf7\u6c42\u95f8\u95e8\u4ecd\u5728\u8c03\u5b83",
          'sameOrigin(req)) return json(res, { error: "\u8de8\u7ad9\u5199\u8bf7\u6c42\u88ab\u62d2\uff08\u540c\u6e90\u6821\u9a8c\uff09" }' in src)
    check("\u2605 \u4e0d\u5f97\u628a /api/healthz \u7c7b\u767d\u540d\u5355\u7559\u4e0b\u6765\u5f53\u6b8b\u7559",
          "OPEN = new Set(" not in src)

    print("C) \u524d\u7aef\u4e0d\u5f97\u518d\u6709\u53e3\u4ee4\u6846")
    for f in ("App.tsx", "api.ts"):
        p = WEB / f
        txt = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
        check(f + " \u5b58\u5728", p.is_file())
        for sym in FRONT_SYMBOLS:
            check("\u2605 " + f + " \u4e0d\u5f97\u518d\u6709 " + sym, sym not in txt)

    return report()


if __name__ == "__main__":
    sys.exit(main())
