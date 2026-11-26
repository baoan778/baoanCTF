#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""调用次数与未命中清单 —— 静态回归（会话 JSONL 聚合 → /api/tools、/api/skills → 三处界面）。

为什么是静态断言：聚合函数的运行时行为已用 node 夹具探针验过（同一次调用同时有 toolCall 块与
toolResult 两条只算一次、没有会话文件回空表不抛、超上限标 truncated）；这里守的是接线不漂 ——
任何一处把 calls 字段丢了、或者界面上又回到「看不见调用次数」，这条就红。
"""
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
    toolcard = rd("work/ui/web/src/components/ToolCard.tsx")
    chat = rd("work/ui/web/src/pages/ChatPage.tsx")
    system = rd("work/ui/web/src/pages/SystemPage.tsx")
    skills = rd("work/ui/web/src/pages/SkillsPage.tsx")

    # ── A 服务端：聚合函数本身 ────────────────────────────────────────
    ck("A1 聚合函数带 512KB 上限", "async function sessionToolCounts(maxBytes = 512 * 1024)" in srv)
    ck("A2 数 toolResult 的 toolName", 'm.role === "toolResult" && typeof m.toolName === "string"' in srv)
    ck("A3 兼容 toolCall / tool_use 两种块", 'b.type !== "toolCall" && b.type !== "tool_use"' in srv)
    ck("A4 按 toolCallId 去重", "seen.has(id0)" in srv and "seen.has(id)" in srv)
    ck("A5 汇总 total", "Object.keys(calls).reduce" in srv)
    ck("A6 返回会话文件名", "session: path.basename(p)" in srv)
    ck("A7 标 truncated", "truncated = (await stat(p)).size > maxBytes" in srv)
    ck(
        "A8 没有会话时回空表不抛",
        '{ calls: {}, total: 0, session: "", truncated: false }' in srv,
    )
    ck("A9 只读尾部（复用 readSessionLines）", "const lines = await readSessionLines(p, maxBytes);" in srv)

    # ── B 服务端：两条路由接线 ────────────────────────────────────────
    i = srv.find('if (p === "/api/tools")')
    seg = srv[i:i + 900] if i > 0 else ""
    ck("B1 /api/tools 带 calls 映射", "calls: agg.calls" in seg)
    ck("B2 /api/tools 带 callsTotal", "callsTotal: agg.total" in seg)
    ck("B3 /api/tools 失败不拖垮目录页", "sessionToolCounts().catch(" in seg)
    j = srv.find('if (p === "/api/skills") {')
    seg2 = srv[j:j + 800] if j > 0 else ""
    ck("B4 /api/skills 给每个技能算 calls", "calls: agg.calls[s.id] || 0" in seg2)
    ck("B5 /api/skills 带 callsTotal", "callsTotal: agg.total" in seg2)
    ck(
        "B6 旧的单行 /api/skills 已经没了",
        'if (p === "/api/skills") return json(res, { skills: await listSkills() });' not in srv,
    )

    # ── C 工具卡：累计调用角标 ────────────────────────────────────────
    ck("C1 ToolCard 接 calls 属性", "calls?: number" in toolcard)
    ck("C2 角标渲染 ×次数", "×{calls}" in toolcard)
    ck("C3 只在重复调用时显示", "calls > 1" in toolcard)
    ck("C4 ChatPage 按卡片聚合 name→次数", "const callCounts = useMemo(" in chat)
    ck("C5 ChatPage 把次数传给工具卡", "calls={it.name ? callCounts[it.name] || 0 : 0}" in chat)
    ck("C6 ChatPage 仍 useMemo 已 import", "useMemo" in chat.split("\n")[0] or "useMemo," in chat.split("\n")[0])

    # ── D 系统页 / 技能页：自报真实总数与未命中 ──────────────────────
    ck("D1 SystemPage 有 callsTotal 状态", "const [callsTotal, setCallsTotal] = useState(0);" in system)
    ck("D2 SystemPage 从 /api/tools 取 callsTotal", "callsTotal?: number" in system)
    ck("D3 SystemPage 渲染本次会话调用次数", "本次会话工具调用 {callsTotal} 次" in system)
    ck("D4 SystemPage 数不出来显示 0 而不是 NaN", 'typeof tj.callsTotal === "number" ? tj.callsTotal : 0' in system)
    ck("D5 SkillsPage 技能带 calls 字段", "calls?: number" in skills)
    ck("D6 SkillsPage 标「未调用」", '未调用' in skills)
    ck("D7 SkillsPage 自报本次会话调用总数", "本次会话工具调用 {callsTotal} 次" in skills)
    ck("D8 SkillsPage 给未命中计数", "const missed = useMemo(" in skills)
    ck("D9 SkillsPage 右侧详情也显示次数", "本次会话调用 {sel.calls || 0} 次" in skills)

    if fails:
        print("%d passed / %d failed" % (28 - len(fails), len(fails)))
        for f in fails:
            print("  FAIL " + f)
        return 1
    print("28 passed / 0 failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())