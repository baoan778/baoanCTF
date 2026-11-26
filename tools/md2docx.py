#!/usr/bin/env python3
"""md2docx.py —— 把简单的 markdown 渲染成排版统一的 docx（中文比赛/资料文档用）。

为什么要有它：这几天给赛项做的三份 Word 都是同一个版式（微软雅黑标题 + 宋体正文 +
16.6cm 表格 + 灰底代码块 + 红色行内代码），每写一份就复制一遍代码是浪费。
固化成一个工具，以后"改内容 → 一条命令重出 Word"。

支持的语法（够用即可，不追求完整 CommonMark）：
    # 标题（第一行大标题，第二行副标题）   ## 一级   ### 二级
    - 列表       1. 有序列表      > 引用
    ``` 代码块 ```            | 表格 | 表头 | + | --- | 分隔行
    **加粗**      `行内代码`（渲染成红色等宽）

用法：
    python3 tools/md2docx.py 输入.md 输出.docx
    python3 tools/md2docx.py 输入.md 输出.docx --check   # 顺带打印结构统计
"""
from __future__ import annotations

import argparse
import os
import re
import sys

try:
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor
    _DOCX_ERR = None
except ModuleNotFoundError as _e:      # 2026-11-03：缺依赖时给出可执行修法，而不是裸 traceback
    # 为什么这么写：`selfcheck` 第 7 组把「工具实跑崩」当硬失败（★"存在但跑不了"），
    # 而原来的裸 ModuleNotFoundError 只甩一行栈，看不出该装什么、装到哪。
    # ★ 不在这里直接退出：`--help` 必须仍然可用（参数说明本身不依赖 python-docx），
    #   否则"查用法"这件最轻的事也被依赖卡住。真正用到 docx 时在 main() 里报错。
    _DOCX_ERR = _e

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
#   自包含（自己 import sys），不依赖本文件是否已 import sys 或用别名。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CJK_H, CJK_B, MONO = "微软雅黑", "宋体", "Consolas"
TEXT_W = 16.6          # 版心 17.0cm，留 0.4cm 余量，任何渲染器都不越界
CODE_BG = "F2F2F2"


def cjk(run, name: str) -> None:
    run.font.name = name
    rf = run._element.get_or_add_rPr().get_or_add_rFonts()
    for k in ("w:eastAsia", "w:ascii", "w:hAnsi"):
        rf.set(qn(k), name)


def shade(obj) -> None:
    el = obj._element
    pr = el.get_or_add_tcPr() if hasattr(el, "get_or_add_tcPr") else el.get_or_add_pPr()
    sh = OxmlElement("w:shd")
    sh.set(qn("w:val"), "clear")
    sh.set(qn("w:fill"), CODE_BG)
    pr.append(sh)


def new_doc() -> Document:
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.top_margin = sec.bottom_margin = Cm(1.8)
    sec.left_margin = sec.right_margin = Cm(2.0)
    normal = doc.styles["Normal"]
    normal.font.size = Pt(10)
    normal.font.name = CJK_B
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), CJK_B)
    for lvl, size in ((0, 18), (1, 13), (2, 11.5)):
        try:
            st = doc.styles["Heading %d" % lvl]
        except KeyError:
            continue
        st.font.size = Pt(size)
        st.font.color.rgb = RGBColor(0x1F, 0x3B, 0x63)
        st.element.rPr.rFonts.set(qn("w:eastAsia"), CJK_H)
    return doc


def add_runs(p, text: str, size: float = 10, base_font: str = CJK_B, bold: bool = False):
    """支持 **加粗** 与 `行内代码`；两者可嵌套（粗体里嵌代码会递归处理）。"""
    for part in re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            add_runs(p, part[2:-2], size, base_font, True)
        elif part.startswith("`") and part.endswith("`"):
            r = p.add_run(part[1:-1]); cjk(r, MONO); r.font.size = Pt(size - 0.5)
            r.font.color.rgb = RGBColor(0xB0, 0x30, 0x30); r.bold = bold
        else:
            r = p.add_run(part); cjk(r, base_font); r.font.size = Pt(size); r.bold = bold
    return p


def render(md_path: str, out_path: str) -> dict:
    doc = new_doc()
    lines = open(md_path, encoding="utf-8").read().split("\n")
    i, first_h1, stats = 0, True, {"h": 0, "tables": 0, "codes": 0, "bullets": 0}
    while i < len(lines):
        s = lines[i].strip()
        if not s:
            i += 1; continue

        if s.startswith("```"):                                  # 代码块
            i += 1; buf = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i].rstrip()); i += 1
            i += 1
            stats["codes"] += 1
            for c in buf:
                p = doc.add_paragraph(); p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.left_indent = Cm(0.6)
                r = p.add_run(c if c.strip() else " "); cjk(r, MONO); r.font.size = Pt(9)
                shade(p)
            continue

        if s.startswith("| "):                                      # 表格
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            n = max(len(r) for r in rows)
            if n <= 2:
                widths = [5.6, TEXT_W - 5.6]
            elif n == 3:
                widths = [2.8, (TEXT_W - 2.8) / 2, (TEXT_W - 2.8) / 2]
            else:
                widths = [2.8] + [(TEXT_W - 2.8) / (n - 1)] * (n - 1)
            t = doc.add_table(rows=1, cols=n); t.style = "Table Grid"
            t.alignment = WD_TABLE_ALIGNMENT.LEFT; t.autofit = False
            for j in range(n):
                c = t.rows[0].cells[j]; c.text = ""
                add_runs(c.paragraphs[0], rows[0][j] if j < len(rows[0]) else "", 9, CJK_H)
                for r in c.paragraphs[0].runs:
                    r.bold = True
                shade(c)
            for row in rows[1:]:
                cells = t.add_row().cells
                for j in range(n):
                    cells[j].text = ""
                    add_runs(cells[j].paragraphs[0], row[j] if j < len(row) else "", 9)
            for j, w in enumerate(widths):
                t.columns[j].width = Cm(w)
            for row in t.rows:
                for j, w in enumerate(widths):
                    row.cells[j].width = Cm(w)
            doc.add_paragraph().paragraph_format.space_after = Pt(6)
            stats["tables"] += 1
            continue

        if s.startswith("### "):
            doc.add_heading(s[4:], level=2); stats["h"] += 1
        elif s.startswith("## "):
            doc.add_heading(s[3:], level=1); stats["h"] += 1
        elif s.startswith("# "):
            p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_after = Pt(2 if first_h1 else 10)
            add_runs(p, s[2:], 15 if first_h1 else 13, CJK_H)
            for r in p.runs:
                r.bold = True
            first_h1 = False; stats["h"] += 1
        elif s.startswith("> "):
            p = doc.add_paragraph(); p.paragraph_format.left_indent = Cm(0.6)
            p.paragraph_format.space_after = Pt(4)
            add_runs(p, s[2:], 9.5)
            for r in p.runs:
                r.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
        elif s.startswith("- "):
            p = doc.add_paragraph(style="List Bullet"); p.paragraph_format.space_after = Pt(2)
            add_runs(p, s[2:], 10); stats["bullets"] += 1
        elif re.match(r"^\d+\. ", s):
            p = doc.add_paragraph(); p.paragraph_format.space_after = Pt(2)
            p.paragraph_format.left_indent = Cm(0.5)
            add_runs(p, s, 10)
        else:
            p = doc.add_paragraph(); p.paragraph_format.space_after = Pt(4)
            add_runs(p, s, 10)
        i += 1

    doc.save(out_path)
    stats["bytes"] = os.path.getsize(out_path)
    stats["paragraphs"] = len(doc.paragraphs)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="markdown -> 统一版式 docx")
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--check", action="store_true", help="打印结构统计")
    a = ap.parse_args()
    if _DOCX_ERR is not None:
        # 到这里才真要用 docx —— 给可执行修法，并且**退出码非 0**（selfcheck 靠它判"跑不了"）
        sys.stderr.write(
            f"✗ md2docx 缺依赖：{_DOCX_ERR.name}（python-docx 没装）\n"
            f"  本机修法（任选其一）：\n"
            f"    {sys.executable} -m pip install python-docx\n"
            f"    tools/venv/bin/pip install python-docx      # 装进仓库 venv\n"
            f"  装完自检：{sys.executable} tools/md2docx.py --help\n"
            f"  （本工具只用于出 Word 交付物，不影响解题链路）\n")
        return 2
    st = render(a.input, a.output)
    print("SAVED: %s (%d bytes) | 标题 %d 表格 %d 代码块 %d" %
          (a.output, st["bytes"], st["h"], st["tables"], st["codes"]))
    if a.check:
        print("段落 %d / 列表 %d" % (st["paragraphs"], st["bullets"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
