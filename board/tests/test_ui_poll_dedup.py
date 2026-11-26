#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_ui_poll_dedup.py — 前端四项（#18 贴底 / #22 轮询去重 / #23 截断容错 / #26 发送键位）

这四项改的都是「静默的体验缺陷」，坏掉时没有任何报错，只有人觉得别扭：
  #18 无条件贴底 —— 人手往上翻历史，每来一条流式增量就被拽回底部，等于读不了历史；
  #22 7 处 setInterval 无条件 setState —— payload 没变也重渲染，展开态/选中行/滚动位置被顶掉；
  #23 args 被后端截断 -> JSON.parse 必失败 -> 折叠行回落整段 JSON 原文，一行读不完 = 没显示；
  #26 只判 Ctrl/Cmd+Enter、不判 isComposing —— 中文输入法组字时回车是选词，会把半截指令发出去。

行为层（salvage 的正则/反转义、shouldSubmitOnKey 的五条判定顺序、sigOf 的签名与循环引用）
已在 2026-11-17 用容器内 node --experimental-strip-types 直接 import 源码逐条跑过（PROBE_OK /
PROBE2_OK）；这份测试守的是**结构不退回旧形态**，这样即使没人再跑一次探针，改坏也会红。

断言：
  A. lib/poll.ts：sigOf 用序列化签名、usePoll 有「签名相同就 return」、卸载清定时器
  B. lib/statusStore.ts：全站同一份 /api/status（publishStatus 去重 + useSharedStatus）
  C. lib/chat-send-mode.ts：两档键位 + isComposing 与 keyCode 229 先判 + 返回归一成 boolean
  D. ChatPage：贴底判定（-60 像素）+ 没有任何「无条件贴底」入口 + 发送走 shouldSubmitOnKey
  E. ToolCard：CMD_RE/unescapeJson/salvage 在位，catch 分支改成 salvage（不再回落原文）
  F. 轮询接入：7 处 usePoll；源码里除 lib/poll.ts 外不再有 setInterval
  G. /api/status 只由 App 轮询，SystemPage 订阅共享 store 并回灌

    python3 board/tests/test_ui_poll_dedup.py
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
SRC = os.path.join(ROOT, "work", "ui", "web", "src")

_passed, _failed = 0, 0


def ck(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  ✗ {name}" + (f"   [{detail}]" if detail else ""))


def text(rel):
    p = os.path.join(SRC, rel)
    if not os.path.exists(p):
        return ""
    with open(p, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def code_lines(t):
    """去掉整行注释，只留会被执行的代码行（注释里写 setInterval 不算数）"""
    out = []
    for ln in t.splitlines():
        s = ln.strip()
        if s.startswith("//") or s.startswith("*") or s.startswith("/*"):
            continue
        out.append(ln)
    return out


def near(lines, needle, guard, back=6):
    """needle 每次出现，往前 back 行内必须出现 guard（用来守「贴底判定在前」）"""
    for i, ln in enumerate(lines):
        if needle not in ln:
            continue
        win = "\n".join(lines[max(0, i - back): i + 1])
        if guard not in win:
            return (i + 1, ln.strip())
    return None


def main():
    # ── A：lib/poll.ts ────────────────────────────────────────────────
    poll = text("lib/poll.ts")
    ck("A1 lib/poll.ts 存在且成型", poll.count("\n") >= 50, f"{poll.count(chr(10))} 行")
    ck("A2 sigOf 用 JSON 序列化做签名", "export function sigOf" in poll and "JSON.stringify" in poll)
    ck("A3 签名取不到时回落 String（循环引用不抛）", "return String(v)" in poll)
    ck("A4 usePoll 存在", "export function usePoll" in poll)
    ck("A5 签名没变就不 apply", "if (s === last) return;" in poll)
    ck("A6 卸载清定时器", "clearInterval(t)" in poll and "alive = false" in poll)
    ck("A7 闭包存 ref（ms 不变不重建定时器）", "fnRef" in poll and "applyRef" in poll)

    # ── B：lib/statusStore.ts ────────────────────────────────────────
    store = text("lib/statusStore.ts")
    ck("B1 publishStatus 存在", "export function publishStatus" in store)
    ck("B2 同一个 payload 不惊动订阅者", "if (s === lastSig) return;" in store)
    ck("B3 useSharedStatus 走 useSyncExternalStore",
       "export function useSharedStatus" in store and "useSyncExternalStore" in store)

    # ── C：lib/chat-send-mode.ts ─────────────────────────────────────
    mode = text("lib/chat-send-mode.ts")
    ck("C1 两档键位", 'export type SendMode = "ctrl-enter" | "enter"' in mode)
    ck("C2 键位持久化", 'const KEY = "pictf.sendMode"' in mode and "localStorage.setItem" in mode)
    ck("C3 先判输入法组字", "if (e.nativeEvent?.isComposing || e.isComposing) return false;" in mode)
    ck("C4 再判 keyCode 229", "if (e.nativeEvent?.keyCode === 229) return false;" in mode)
    ck("C5 返回归一成 boolean（不是 undefined）", "return !!(e.ctrlKey || e.metaKey);" in mode)
    body = mode[mode.index("export function shouldSubmitOnKey"):]
    i_comp = body.find("isComposing")
    i_shift = body.find("shiftKey")
    ck("C6 组字判定在换行判定之前", 0 <= i_comp < i_shift, f"{i_comp} vs {i_shift}")

    # ── D：ChatPage（#18 贴底 + #26 发送）────────────────────────────
    chat = text("pages/ChatPage.tsx")
    clines = chat.splitlines()
    ck("D1 有贴底状态 ref", "atBottomRef" in chat)
    ck("D2 贴底判定带 60px 容差", ">= el.scrollHeight - 60" in chat)
    ck("D3 内容撑高走 MutationObserver", "new MutationObserver" in chat)
    bad = near(clines, "el.scrollTop = el.scrollHeight", "atBottomRef.current")
    ck("D4 没有无条件贴底入口（每处都先问贴底吗）", bad is None, str(bad))
    ck("D5 发送键位可切", "useSendMode()" in chat and "setSendMode(" in chat and "<select" in chat)
    ck("D6 发送走 shouldSubmitOnKey", "if (!shouldSubmitOnKey(e, mode)) return;" in chat)
    ck("D7 自己发消息后强制贴底（发完必跟）", chat.count("atBottomRef.current = true") >= 1)
    ck("D8 滚动时实时重算贴底", "atBottomRef.current = el.scrollTop + el.clientHeight" in chat)

    # ── E：ToolCard（#23 截断容错）───────────────────────────────────
    tc = text("components/ToolCard.tsx")
    ck("E1 抠 command 的正则", 'const CMD_RE = /"command"' in tc)
    ck("E2 单趟反转义", "function unescapeJson" in tc)
    ck("E3 salvage 兜底", "function salvage" in tc)
    ck("E4 cmdOf 与 prettyArgs 都改走 salvage", tc.count("return salvage(t)") >= 2)
    ck("E5 旧的「回落整段 JSON 原文」已删", "catch {\n    return t;" not in tc)
    ck("E6 反转义覆盖 \\u 与常见转义", "u[0-9a-fA-F]{4}" in tc and "case \"n\"" in tc)

    # ── F：7 处轮询接入 ─────────────────────────────────────────────
    wired = [
        ("App.tsx", "App"),
        ("pages/SystemPage.tsx", "SystemPage"),
        ("pages/InspectPage.tsx", "InspectPage"),
        ("pages/SessionsPage.tsx", "SessionsPage"),
        ("pages/SubagentsPage.tsx", "SubagentsPage"),
        ("components/NextMoveCard.tsx", "NextMoveCard"),
        ("components/OpsBar.tsx", "OpsBar"),
    ]
    for rel, label in wired:
        t = text(rel)
        ck(f"F1 {label} 用 usePoll", "usePoll(" in t, rel)
        ck(f"F2 {label} 带 onError", "onError:" in t, rel)

    stray = []
    for dirpath, _dirs, files in os.walk(SRC):
        for fn in files:
            if not fn.endswith((".ts", ".tsx")):
                continue
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, SRC).replace(os.sep, "/")
            if rel == "lib/poll.ts":
                continue
            for ln in code_lines(text(rel)):
                if "setInterval(" in ln:
                    stray.append(f"{rel}: {ln.strip()[:60]}")
    ck("F3 setInterval 只剩 lib/poll.ts 一处", stray == [], "; ".join(stray[:3]))

    # ── G：/api/status 单路 ─────────────────────────────────────────
    callers = []
    for dirpath, _dirs, files in os.walk(SRC):
        for fn in files:
            if not fn.endswith((".ts", ".tsx")):
                continue
            p = os.path.join(dirpath, fn)
            rel = os.path.relpath(p, SRC).replace(os.sep, "/")
            for ln in code_lines(text(rel)):
                if "api.status(" in ln:
                    callers.append(rel)
    ck("G1 只有 App 与 SystemPage 碰 /api/status", sorted(set(callers)) == ["App.tsx", "pages/SystemPage.tsx"], str(sorted(set(callers))))
    ck("G2 App 把 status 发进共享 store", "publishStatus(s)" in text("App.tsx"))
    ck("G3 SystemPage 订阅共享 store", "useSharedStatus<Status>()" in text("pages/SystemPage.tsx"))
    ck("G4 SystemPage 自己也回灌（挂载那一轮）", "publishStatus(s)" in text("pages/SystemPage.tsx"))

    print(f"\n{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
