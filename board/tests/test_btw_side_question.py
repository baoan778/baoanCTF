#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""#35 旁路提问（/btw）—— 静态回归：通道边界 + 纪律不许漂。"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fails = []


def ck(name, cond):
    if not cond:
        fails.append(name)


def rd(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read()


def main():
    srv = rd("work/ui/server.mjs")
    api = rd("work/ui/web/src/api.ts")
    chat = rd("work/ui/web/src/pages/ChatPage.tsx")
    doc = rd("docs/并行工作.md")

    i = srv.find("function runSideQuestion(")
    ck("A1 旁路函数存在", i > 0)
    seg = srv[i:i + 4000] if i > 0 else ""
    ck("A2 用 -p 带问题", '"-p", question' in seg)
    ck("A3 固定 --no-tools（没有工具执行路径）", '"--no-tools"' in seg)
    ck("A4 独立临时会话目录", '"--session-dir", dir' in seg and 'tmpdir(), "pictf-btw-"' in seg)
    ck("A5 超时即杀（SIGKILL）", 'child.kill("SIGKILL")' in seg)
    ck("A6 超时上限 120s", "const BTW_TIMEOUT_MS = 120000;" in srv)
    ck("A7 最多同时 2 路", "const BTW_MAX_CONCURRENT = 2;" in srv)
    ck("A8 超时会把已拿到的部分输出带回来", "已拿到的部分输出仍在下面" in seg)
    ck("A9 stdout 有上限（不许把一大段塞进界面）", "64 * 1024" in seg)
    ck("A10 空回答算失败", 'if (!answer) return finish' in seg)

    j = srv.find('if (p === "/api/btw"')
    ck("B1 路由存在且只接 POST", j > 0 and 'req.method === "POST"' in srv[j:j + 120])
    rseg = srv[j:j + 2200] if j > 0 else ""
    ck("B2 缺问题时 400", '缺少 question' in rseg)
    ck("B3 问题过长 400", '问题太长' in rseg)
    ck("B4 并发超限 429", "429);" in rseg and "BTW_MAX_CONCURRENT" in rseg)
    ck("B5 并发计数进出都动（try/finally）", "btwRunning += 1;" in rseg and "finally { btwRunning -= 1; }" in rseg)
    ck("B6 模型取当前会话的，可被 body 覆盖", 'st?.data?.model?.provider' in rseg and "body.provider" in rseg)
    ck("B7 回包自带「不进主线」提示", "不写当前会话、不进黑板、不占主线上下文" in rseg)
    ck("B8 路由不碰黑板（无 fgs / fact-add）", "fgs" not in rseg and "fact-add" not in rseg)

    ck("C1 api.ts 有 btw 方法", 'btw: (body: Json) => post("/api/btw", body),' in api)
    ck("C2 ChatPage 有 /btw 按钮", ">\n              /btw\n" in chat or "/btw\n" in chat)
    ck("C3 有旁路输入条", 'placeholder="旁路问一句' in chat)
    ck("C4 有 askBtw 且防重复提交", "async function askBtw()" in chat and "if (!q || btwBusy) return;" in chat)
    ck("C5 提问与回答都落成本地 note", 'kind: "note", id: nextId("btw-q")' in chat and 'kind: "note", id: nextId("btw-a")' in chat)
    ck("C6 回答头自带纪律提示", '旁路回答 · ' in chat and '不写会话' in chat)
    ck("C7 失败也在这一屏说清", '旁路提问失败：' in chat)
    ck("C8 输入条回车也提交且放行输入法", 'e.nativeEvent.isComposing' in chat)

    ck("D1 纪律章节落文档", "### 4.6c 旁路提问（/btw）纪律" in doc)
    ck("D2 文档写明只问不写", "**只问不写**" in doc)
    ck("D3 文档写明不带半截流式", "不带半截流式" in doc)
    ck("D4 文档写明旁路回答不是证据", "旁路的回答**不是证据**" in doc)

    total = 30
    if fails:
        print("%d passed / %d failed" % (total - len(fails), len(fails)))
        for f in fails:
            print("  FAIL " + f)
        return 1
    print("%d passed / 0 failed" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())