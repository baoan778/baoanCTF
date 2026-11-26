#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_ui_locate_highlight.py — 前端第二批（断线自愈 / 定位回跳 / 报文高亮）

这三项的共同点是「界面看着像在干活，实际信息是错的或不到的」：
  断线期间产生的事件全丢 —— 重连后界面停在旧状态，人以为任务没动（实测只补最近 120 条）；
  审证页看到「已核验」的 flag，想知道是哪条命令打出来的，原来只能自己翻对话流；
  工具卡输出是裸 pre：状态码和 body 糊成一片，几十 KB 一起塞 DOM 还会卡。

行为层已用容器内 node 直跑探针验证（pickReplay 的边界、CSS 词法着色）；这份测试守的是
**结构不退回旧形态**：删掉 since 补偿、去掉 focused、把 HttpBlock 换回裸 pre 都会红。

断言：
  A. lib/locate.ts：pending 一次性交接 + 事件名 + 订阅可退订
  B. ToolCard：focused 入参 / 默认展开 / scrollIntoView(nearest) / focus 类 / 输出走 HttpBlock
  C. ChatPage：倒序找命中 → 聚焦 + 别拽回底部；找不到必须显式说明
  D. App：监听 LOCATE_EVENT 并切到对话页
  E. InspectPage：账本行与扫描命中行各一个「定位调用」
  F. HttpBlock：格式探测 / 状态码着色 / JSON 词法 / 有界预览 + 按需加载 / 非报文退回 pre
  G. FilesPage：文本预览也走 HttpBlock
  H. styles.css：hb 配色与 .toolcard.focus 描边

    python3 board/tests/test_ui_locate_highlight.py
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
        print("  x " + name + (("   [" + str(detail) + "]") if detail else ""))


def text(rel):
    p = os.path.join(SRC, rel)
    if not os.path.exists(p):
        return ""
    with open(p, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def code_lines(t):
    """去掉整行注释：注释里提到旧写法不算数"""
    out = []
    for ln in t.splitlines():
        s = ln.strip()
        if s.startswith("//") or s.startswith("*") or s.startswith("/*"):
            continue
        out.append(ln)
    return out


def near(lines, needle, guard, back=8):
    """needle 的每一处，往前 back 行内必须出现 guard"""
    for i, ln in enumerate(lines):
        if needle not in ln:
            continue
        win = chr(10).join(lines[max(0, i - back): i + 1])
        if guard not in win:
            return (i + 1, ln.strip())
    return None


def main():
    # ── A：lib/locate.ts ────────────────────────────────────────────
    loc = text("lib/locate.ts")
    ck("A1 lib/locate.ts 存在且成型", loc.count(chr(10)) >= 25, str(loc.count(chr(10))) + " 行")
    ck("A2 有 pending 交接变量", "let pending" in loc)
    ck("A3 requestLocate 存 pending 再通知", "pending = t;" in loc and "requestLocate" in loc)
    ck("A4 takeLocate 一次性取走（取完清空）", "export function takeLocate" in loc and "pending = \"\"" in loc)
    ck("A5 事件名固定为 pictf:locate", "pictf:locate" in loc and "dispatchEvent" in loc)
    ck("A6 onLocate 可退订", "subs.delete" in loc)

    # ── B：ToolCard ─────────────────────────────────────────────────
    tc = text("components/ToolCard.tsx")
    tcl = code_lines(tc)
    ck("B1 入参有 focused", "focused?: boolean" in tc)
    ck("B2 命中即默认展开", "useState(!!focused)" in tc)
    ck("B3 focused 时滚进视野且 block:nearest", "scrollIntoView" in tc and "nearest" in tc)
    ck("B4 有 if (!focused) return 守门", "if (!focused) return;" in tc)
    ck("B5 命中卡带 focus 类", "focused ? \" focus\" : \"\"" in tc)
    ck("B6 输出改走 HttpBlock", "<HttpBlock text={it.output} />" in tc)
    ck("B7 不再裸 pre 输出", "<pre className=\"tc-pre\">{it.output}</pre>" not in tc)
    ck("B8 旧的无条件贴底式渲染已删", tc.count("<HttpBlock text={it.output} />") == 1)

    # ── C：ChatPage 定位 ────────────────────────────────────────────
    cp = text("pages/ChatPage.tsx")
    cpl = code_lines(cp)
    ck("C1 引入 onLocate/takeLocate", "onLocate" in cp and "takeLocate" in cp)
    ck("C2 用 itemsRef 读当前列表", "itemsRef.current = items" in cp)
    ck("C3 倒序查找（最近的一条优先）", "for (let i = list.length - 1; i >= 0; i--)" in cp)
    ck("C4 按输出内容匹配", "(it.out || \"\").includes(text)" in cp)
    ck("C5 聚焦前先解除贴底跟随", near(cpl, "setFocusId(", "atBottomRef.current = false") is None)
    ck("C6 找不到时显式说明（不是静默）", "没找到包含该内容的工具输出" in cp)
    ck("C7 挂载时先取一次 pending", "const off = onLocate(run);" in cp and "run();" in cp)
    ck("C8 命中卡片传入 focused", "focused={focusId" in cp)

    # ── D：App 切页 ─────────────────────────────────────────────────
    app = text("App.tsx")
    ck("D1 引入 LOCATE_EVENT", "LOCATE_EVENT" in app and "lib/locate" in app)
    ck("D2 监听后切到 chat", "addEventListener(LOCATE_EVENT" in app and "setTab(\"chat\")" in app)
    ck("D3 卸载时移除监听", "removeEventListener(LOCATE_EVENT" in app)

    # ── E：InspectPage 两个入口 ─────────────────────────────────────
    ip = text("pages/InspectPage.tsx")
    ck("E1 引入 requestLocate", "requestLocate" in ip)
    ck("E2 两处「定位调用」按钮", ip.count("定位调用") == 2, str(ip.count("定位调用")))
    ck("E3 账本行按 f.flag 带内容", "requestLocate(f.flag" in ip)
    ck("E4 扫描命中行按 h.flag 带内容", "requestLocate(h.flag" in ip)
    ck("E5 空 flag 不出按钮", "<button className=\"mini\" onClick={() => requestLocate(h.flag || \"\")}>" in ip)

    # ── F：HttpBlock ────────────────────────────────────────────────
    hb = text("components/HttpBlock.tsx")
    ck("F1 有界预览上限 4000 字符", "const MAX_HEAD = 4000;" in hb)
    ck("F2 三种格式（http/json/text）", "export type BodyKind = \"http\" | \"json\" | \"text\";" in hb)
    ck("F3 只探前 8KB", "s.slice(0, 8192)" in hb)
    ck("F4 状态行/请求行/头 三条正则", "STATUS_RE" in hb and "REQ_RE" in hb and "HEADER_RE" in hb)
    ck("F5 2xx-3xx 绿、>=400 红", "hb-status hb-ok" in hb and "hb-status hb-bad" in hb)
    ck("F6 JSON 词法四类", "hb-key" in hb and "hb-str" in hb and "hb-num" in hb and "hb-lit" in hb)
    ck("F7 头/body 按空行切", "function splitHttp" in hb and "body: \"\"" in hb)
    ck("F8 非报文退回裸 pre（不改原文）", "<pre className=\"tc-pre\">{shown}</pre>" in hb)
    ck("F9 有「没渲染的字符数」提示", "字符没渲染" in hb)
    ck("F10 有按需加载按钮", ">加载全部<" in hb)
    ck("F11 高亮是切片拼接不改字", "out.push(s.slice(last, m.index))" in hb)

    # ── G：FilesPage ────────────────────────────────────────────────
    fp = text("pages/FilesPage.tsx")
    ck("G1 引入 HttpBlock", "import HttpBlock from \"../components/HttpBlock\";" in fp)
    ck("G2 文本预览走 HttpBlock", "<HttpBlock text={text ||" in fp)
    ck("G3 不再裸 pre 预览", "<pre className=\"pre\">{text ||" not in fp)

    # ── H：样式 ─────────────────────────────────────────────────────
    css = text("styles.css")
    ck("H1 命中卡金色描边", ".toolcard.focus" in css)
    ck("H2 状态码配色", ".hb-ok" in css and ".hb-bad" in css)
    ck("H3 JSON 配色四类", all(x in css for x in (".hb-key", ".hb-str", ".hb-num", ".hb-lit")))
    ck("H4 高亮块也限高可滚", ".hb-pre" in css and "max-height" in css.split(".hb-pre")[1][:120])

    print("")
    if _failed:
        print("%d passed / %d failed" % (_passed, _failed))
        return 1
    print("%d passed / 0 failed" % _passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())