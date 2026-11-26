#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_ui_model_switch.py — #27 运行中换模型（RPC 切换，不重启、不丢上下文）

为什么值得一条测试：这条链路最容易「看起来能用」而实际把上下文扔掉 —— 如果谁把它改成
调 /api/start（applyStart 换启动参数会重启 pi），界面上仍然显示「已切到 X」，但当前会话
进程里已经收集的证据与推理全没了。所以测试的重点不是「有没有下拉」，而是**不许走重启**。

断言：
  A. server.mjs 路由：POST /api/models/active → 参数校验 / 无进程 409 / set_model RPC 20000ms
     / before-after 回读 / 生效边界文案 / 失败回包；**不含 applyStart 或 /api/start**
  B. api.ts：有 modelsActive 且打的是这个端点
  C. ChatPage：下拉数据只拉一次、切换回执落成 note、失败回滚、生效边界写进 title

    python3 board/tests/test_ui_model_switch.py
"""
import os
import sys

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
UI = os.path.join(ROOT, "work", "ui")
SRC = os.path.join(UI, "web", "src")

_passed, _failed = 0, 0


def ck(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print("  x " + name + (("   [" + str(detail) + "]") if detail else ""))


def text(p):
    if not os.path.exists(p):
        return ""
    with open(p, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def main():
    srv = text(os.path.join(UI, "server.mjs"))
    i = srv.find('p === "/api/models/active"')
    ck("A0 路由注册了 /api/models/active", i > 0)
    j = srv.find('if (p === "/api/models/provider" && req.method === "POST")', i)
    blk = srv[i:j] if i > 0 and j > i else ""
    ck("A1 只接 POST", 'req.method === "POST"' in srv[max(0, i - 80):i + 80])
    ck("A2 读 JSON body", "readJsonBody(req)" in blk)
    ck("A3 缺参数 400", "缺少 provider 或 modelId" in blk and "400" in blk)
    ck("A4 没有进程时 409 并说清怎么办", "409" in blk and "先发一条指令" in blk)
    ck("A5 走 set_model RPC", 'type: "set_model"' in blk)
    ck("A6 RPC 超时 20000ms", "20000" in blk)
    ck("A7 切换前回读 before", "before" in blk and 'type: "get_state"' in blk)
    ck("A8 切换后回读 after 并判 landed", "landed" in blk and "after.provider === provider" in blk)
    ck("A9 生效边界写进回执", "从下一轮 LLM 调用生效" in blk)
    ck("A10 未收敛要如实警告", "可能下一轮才收敛" in blk)
    ck("A11 RPC 拒绝要回 ok:false", "rec.success === false" in blk)
    ck("A12 不许走重启（applyStart）", "applyStart" not in blk)
    ck("A13 不许借 /api/start 换模型", "/api/start" not in blk)
    ck("A14 有运行日志行", "运行中换模型" in blk)

    api = text(os.path.join(SRC, "api.ts"))
    ck("B1 api.ts 有 modelsActive", "modelsActive:" in api)
    ck("B2 打的是 /api/models/active", 'post("/api/models/active"' in api)

    cp = text(os.path.join(SRC, "pages", "ChatPage.tsx"))
    ck("C1 有 curModel/modelChoices 状态", "const [curModel, setCurModel]" in cp and "const [modelChoices, setModelChoices]" in cp)
    seg = cp[cp.find("modelChoices"):cp.find("async function switchModel")]
    ck("C2 下拉数据挂载时只拉一次（不轮询）", "api.models()) as {" in seg and "usePoll" not in seg)
    ck("C3 切换调用 modelsActive", "api.modelsActive({ provider, modelId })" in cp)
    ck("C4 成功回执落成 note（用服务端的文案）", "r.note" in cp)
    ck("C5 失败回滚下拉", "setCurModel(prev)" in cp)
    ck("C6 失败要说「仍用原来的模型」", "仍用原来的模型" in cp)
    ck("C7 下拉 title 写清只对下一轮生效", "只对下一轮 LLM 调用生效" in cp)
    ck("C8 无可用模型时禁用下拉", "disabled={modelChoices.length === 0}" in cp)
    ck("C9 当前模型不在列表时也要能显示", "（当前）" in cp)

    print("")
    if _failed:
        print("%d passed / %d failed" % (_passed, _failed))
        return 1
    print("%d passed / 0 failed" % _passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())