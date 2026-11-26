#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""arch-diagram-simple.py —— pi-lab 的「简版」运转图（给 Owner 复审用）

和 `arch-diagram.py`（详版）的分工：
  · 详版 = 组件清单，给"这系统里有什么"用；
  · 简版 = **谁跟谁说话 / 一次解题怎么转 / 哪些是硬判据哪些靠纪律 / 我认为该改的点**，
           给"哪里需要改"用 —— 特意把「薄弱点」画进去，方便直接在上面圈。

用法：
    py tools/arch-diagram-simple.py                    # 桌面 pi-lab-架构图-简版.png
    py tools/arch-diagram-simple.py --out /tmp/x.png

数字纪律：能从现场算的都现场算（skills / tools / refs / tests），别写死。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
#   自包含（自己 import sys），不依赖本文件是否已 import sys 或用别名。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = Path(__file__).resolve().parents[1]
WIN_FONTS = Path("/mnt/c/Windows/Fonts")
DEFAULT_OUT = Path("/mnt/c/Users/幺零零/Desktop") / "pi-lab-架构图-简版.png"

W, PAD = 1680, 48
BG_TOP, BG_BOT = "#f8fafc", "#e9eef5"
CARD, INK, MUTED, FAINT = "#ffffff", "#13212e", "#5f7183", "#93a3b3"
BLUE, GREEN, AMBER, RED, VIOLET, SLATE = "#2f6fb3", "#2e8b57", "#d98218", "#c0392b", "#7d5bbe", "#5a6570"

# 角色与通道： (标题, 副标题, 颜色)
ROLES = [
    ("你 · 人", "给目标 / 定范围 / 看回显确认", SLATE),
    ("主会话（我）", "唯一写入者 · 决策 · 派活", BLUE),
    ("黑板 FGS", "graph.json = 唯一真相", RED),
    ("Worker 子代理", "fresh context · 只出结论", VIOLET),
    ("工具链 + Skills", "poc/扫描/利用/取证 + 16 技能", AMBER),
    ("目标", "靶机 / 样本 / 镜像 / 日志", GREEN),
]
ROLE_ARROWS = ["发题", "next", "publish", "调用", "取证"]

# 一次解题：4 步
LOOP = [
    ("① 状态", "事实进图：谁、何时、看到什么", "verified 才算结论"),
    ("② 决策", "fgs next → 唯一答案（bootstrap/explore/reason）", "不许空手：给 Step 或死路 Fact"),
    ("③ 行动", "Worker 干活：命令 / 脚本 / 工具", "超时也算产出，留一条 Fact"),
    ("④ 反馈", "结论回图：publish → apply 校验合入", "一条不合法 → 整批拒绝"),
]

HARD = [
    ("只有 verified 能当结论", "exit 3"),
    ("抢单冲突不许抢", "exit 2"),
    ("outbox 有一条不合法 → 全拒", "exit 3"),
    ("产物必须逐字出现在真实输出", "fgs verify"),
    ("越界目标拦下（判据 fail-closed）", "scope"),
    ("去重宁可拦错，要写必须人放行", "--force-dup"),
]
SOFT = [
    ("连续 3 步无产出 → 换攻击面", "纪律"),
    ("每步要有判定式 + 时间盒", "纪律"),
    ("只加载当前题型 skill（少即是多）", "纪律"),
    ("单题投入超过收益就换题", "判断"),
    ("solved 我看 · confirm 你看平台回显", "人"),
]

# 薄弱点（我自己的判断，请直接在上面圈）
WEAK = [
    ("🔴", "主会话是唯一入口", "我卡住 / 挂了，图就停摆 —— 保活层只治了「进程被杀」，没治「人不在」", RED),
    ("🟡", "上下文只靠纪律", "worker 是 fresh，但主会话会越吃越满；没有机器化的预算/压缩阈值", AMBER),
    ("🟡", "成本是「看」不是「拦」", "fgs cost 出画像，但没有硬熔断（超预算自动停手）", AMBER),
    ("🟡", "门禁松紧不齐", "手法库是 fail-closed，outbox 有；reason/solved 仍偏靠自觉", AMBER),
    ("🟢", "失败不留结论", "WP 只在 solved+confirm 后起；没解出来的题没有「死路论证」强制产物", GREEN),
    ("🟢", "同机同权限", "worker 与主会话同一台 WSL、同一权限，没有隔离（跑飞 = 全飞）", GREEN),
]


def font(name: str, size: int):
    for p in (WIN_FONTS / name, WIN_FONTS / "Deng.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(str(p), size)
        except Exception:
            continue
    return ImageFont.load_default()


def wrap(text: str, f, max_w: int):
    out, cur, buf, toks = [], "", "", []
    for ch in text:
        if ch.isascii() and not ch.isspace():
            buf += ch
        else:
            if buf:
                toks.append(buf)
                buf = ""
            toks.append(ch)
    if buf:
        toks.append(buf)
    # 超长 ASCII 词（路径 / 组合词）先按字符拆开 —— 否则一个 25 字符的 token 会直接冲出方框
    blown = []
    for t in toks:
        if f.getlength(t) > max_w:
            blown.extend(list(t))
        else:
            blown.append(t)
    toks = blown
    for t in toks:
        trial = cur + t
        if f.getlength(trial) <= max_w or not cur.strip():
            cur = trial
        else:
            out.append(cur.rstrip())
            cur = "" if t.isspace() else t
    if cur.strip():
        out.append(cur.rstrip())
    return out or [""]


def tint(hex_color: str, ratio: float) -> str:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    r, g, b = (int(c + (255 - c) * ratio) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def live_facts() -> dict:
    def n(pat):
        return len(list(LAB.glob(pat)))
    return {"skills": n("skills/*/SKILL.md"), "tools": len(list((LAB / "tools" / "bin").iterdir())),
            "refs": n("skills/*/references/**/*.md"), "tests": n("board/tests/test_*.py")}


def shadow(img, box, radius, fill, outline=None, width=2, blur=8, dy=4, alpha=32):
    x0, y0, x1, y1 = box
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((x0, y0 + dy, x1, y1 + dy), radius=radius, fill=(15, 30, 50, alpha))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(blur)))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box, radius=radius, fill=fill,
                        outline=outline, width=width if outline else 0)


def arrow(d, x1, y1, x2, y2, color="#9fb0c0", width=3, head=9):
    d.line((x1, y1, x2, y2), fill=color, width=width)
    if abs(x2 - x1) >= abs(y2 - y1):                       # 水平
        s = 1 if x2 > x1 else -1
        d.polygon([(x2, y2), (x2 - s * head, y2 - head * 0.7), (x2 - s * head, y2 + head * 0.7)], fill=color)
    else:                                                   # 垂直
        s = 1 if y2 > y1 else -1
        d.polygon([(x2, y2), (x2 - head * 0.7, y2 - s * head), (x2 + head * 0.7, y2 - s * head)], fill=color)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    a = ap.parse_args()

    f_title, f_sub = font("msyhbd.ttc", 42), font("msyh.ttc", 19)
    f_box, f_boxs = font("msyhbd.ttc", 23), font("msyh.ttc", 17)
    f_sect = font("msyhbd.ttc", 25)
    f_body, f_small = font("msyh.ttc", 19), font("msyh.ttc", 17)
    f_tiny = font("msyh.ttc", 15)
    LIVE = live_facts()

    # ── 版面高度 ──
    head_h, roles_h = 124, 246
    mid_h = 440
    weak_h = 60 + len(WEAK) * 46 + 18
    total_h = PAD + head_h + 20 + roles_h + 20 + mid_h + 20 + weak_h + 34 + 44
    img = Image.new("RGBA", (W, total_h), BG_TOP)
    top = tuple(int(BG_TOP[i:i + 2], 16) for i in (1, 3, 5))
    bot = tuple(int(BG_BOT[i:i + 2], 16) for i in (1, 3, 5))
    grad = Image.new("RGB", (1, total_h))
    gd = ImageDraw.Draw(grad)
    for y in range(total_h):
        r = y / max(total_h - 1, 1)
        gd.point((0, y), fill=tuple(int(top[i] + (bot[i] - top[i]) * r) for i in range(3)))
    img.paste(grad.resize((W, total_h)), (0, 0))
    img = img.convert("RGBA")
    d = ImageDraw.Draw(img)

    # ── 页眉 ──
    shadow(img, (PAD, PAD, W - PAD, PAD + head_h), 18, CARD, blur=12, dy=5, alpha=38)
    d.rounded_rectangle((PAD, PAD, PAD + 10, PAD + head_h), radius=5, fill=BLUE)
    d.text((PAD + 32, PAD + 22), "pi-lab 怎么转起来（简版）", font=f_title, fill=INK)
    d.text((PAD + 34, PAD + 78),
           f"给 Owner 复审用：上面的关系 → 中间的循环 → 能拦的和只靠纪律的 → 最后是我认为该改的 6 处"
           f"　·　现场数字：{LIVE['skills']} skills / {LIVE['tools']} 工具 / {LIVE['tests']} 测试"
           f"　·　{datetime.now(timezone(timedelta(hours=8))):%m-%d %H:%M}",
           font=f_sub, fill=MUTED)
    y = PAD + head_h + 20

    # ── 一、角色与通道 ──
    shadow(img, (PAD, y, W - PAD, y + roles_h), 16, CARD, "#cfd9e4")
    d.text((PAD + 26, y + 16), "① 谁跟谁说话", font=f_sect, fill=INK)
    d.text((PAD + 26 + f_sect.getlength("① 谁跟谁说话") + 16, y + 24),
           "竖线是「产物」，横箭头是「动作」", font=f_small, fill=FAINT)
    bw, gap = 200, 56
    bx = PAD + 30
    by = y + 66
    for i, (title, sub, col) in enumerate(ROLES):
        box = (bx, by, bx + bw, by + 84)
        d.rounded_rectangle(box, radius=14, fill=tint(col, 0.93), outline=col, width=2)
        d.text((bx + 16, by + 12), title, font=f_box, fill=col)
        for j, ln in enumerate(wrap(sub, f_boxs, bw - 32)[:2]):
            d.text((bx + 16, by + 44 + j * 22), ln, font=f_boxs, fill=INK)
        if i < len(ROLES) - 1:
            ax1, ax2 = bx + bw + 6, bx + bw + gap - 6
            arrow(d, ax1, by + 42, ax2, by + 42, "#b9c6d3", 3)
            d.text(((ax1 + ax2) / 2 - f_tiny.getlength(ROLE_ARROWS[i]) / 2, by + 16),
                   ROLE_ARROWS[i], font=f_tiny, fill=MUTED)
        bx += bw + gap
    # 旁支：留档
    ly = by + 106
    d.rounded_rectangle((PAD + 30, ly, W - PAD - 30, ly + 40), radius=12,
                        fill=tint(SLATE, 0.94), outline=SLATE, width=2)
    d.text((PAD + 48, ly + 9),
           "旁支（只写不读）：work/<pid>/ 留档 + _evidence/　·　fgs log / deliveries 审计流水　·　"
           "handover 交接包　·　WP 代理 → 手法库", font=f_small, fill=SLATE)
    y += roles_h + 20

    # ── 二、循环 + 门禁 ──
    left_w = 906
    shadow(img, (PAD, y, PAD + left_w, y + mid_h), 16, CARD, "#cfd9e4")
    d.text((PAD + 26, y + 16), "② 一次解题怎么转", font=f_sect, fill=INK)
    d.text((PAD + 26 + f_sect.getlength("② 一次解题怎么转") + 16, y + 24),
           "状态 → 决策 → 行动 → 反馈，转不动就换攻击面", font=f_small, fill=FAINT)
    cols = [("① 状态", BLUE), ("② 决策", VIOLET), ("③ 行动", AMBER), ("④ 反馈", GREEN)]
    cw = (left_w - 60 - 3 * 34) / 4
    cy = y + 66
    for i, ((t, col), (_t2, prod, judge)) in enumerate(zip(cols, LOOP)):
        cx = PAD + 30 + i * (cw + 34)
        d.rounded_rectangle((cx, cy, cx + cw, cy + 190), radius=14, fill=tint(col, 0.94), outline=col, width=2)
        d.text((cx + 14, cy + 12), t, font=f_box, fill=col)
        for j, ln in enumerate(wrap(prod, f_small, cw - 28)[:3]):
            d.text((cx + 14, cy + 44 + j * 22), ln, font=f_small, fill=INK)
        d.line((cx + 14, cy + 126, cx + cw - 14, cy + 126), fill=tint(col, 0.75), width=2)
        for j, ln in enumerate(wrap("判据：" + judge, f_tiny, cw - 28)[:2]):
            d.text((cx + 14, cy + 134 + j * 20), ln, font=f_tiny, fill=col)
        if i < 3:
            arrow(d, cx + cw + 4, cy + 95, cx + cw + 30, cy + 95, "#b9c6d3", 3)
    # 回环箭头
    ry = cy + 202
    arrow(d, PAD + left_w - 60, cy + 190, PAD + left_w - 60, ry, "#b9c6d3", 3)
    d.line((PAD + 60, ry, PAD + left_w - 60, ry), fill="#b9c6d3", width=3)
    arrow(d, PAD + 60, ry, PAD + 60, cy + 190, "#b9c6d3", 3)
    d.text((PAD + left_w / 2 - 150, ry + 10),
           "转不动（连续 3 步没有 verified）→ 换攻击面，不是换 payload", font=f_small, fill=MUTED)
    d.text((PAD + 30, y + mid_h - 62),
           "谁在转：主会话做决策与合入，worker 只出结论（同一步同一时刻只有一个 claim）",
           font=f_small, fill=MUTED)
    d.text((PAD + 30, y + mid_h - 36),
           "上面四步的每个接口都挂了门禁 —— 右边那 6 条机器判据就是它们；右下 5 条暂时只靠纪律。",
           font=f_small, fill=MUTED)

    # 右侧：硬门禁 vs 靠纪律
    rx = PAD + left_w + 20
    rw = W - PAD - rx
    shadow(img, (rx, y, rx + rw, y + mid_h), 16, CARD, "#cfd9e4")
    d.text((rx + 26, y + 16), "③ 能拦的 vs 只靠纪律的", font=f_sect, fill=INK)
    hy = y + 62
    d.rounded_rectangle((rx + 26, hy, rx + rw - 26, hy + 28), radius=8, fill=tint(GREEN, 0.9))
    d.text((rx + 38, hy + 3), "● 机器判（会真的拒）", font=f_boxs, fill=GREEN)
    yy = hy + 36
    for t, code in HARD:
        d.ellipse((rx + 32, yy + 7, rx + 42, yy + 17), fill=GREEN)
        for j, ln in enumerate(wrap(t, f_tiny, rw - 170)[:2]):
            d.text((rx + 50, yy + j * 20), ln, font=f_tiny, fill=INK)
        d.text((rx + rw - 92, yy + 1), code, font=f_tiny, fill=GREEN)
        yy += 21
    yy += 8
    d.rounded_rectangle((rx + 26, yy, rx + rw - 26, yy + 28), radius=8, fill=tint(AMBER, 0.9))
    d.text((rx + 38, yy + 3), "● 只靠纪律 / 靠人", font=f_boxs, fill=AMBER)
    yy += 36
    for t, tag in SOFT:
        d.ellipse((rx + 32, yy + 7, rx + 42, yy + 17), fill=AMBER)
        for j, ln in enumerate(wrap(t, f_tiny, rw - 150)[:2]):
            d.text((rx + 50, yy + j * 20), ln, font=f_tiny, fill=INK)
        d.text((rx + rw - 86, yy + 1), tag, font=f_tiny, fill=AMBER)
        yy += 21
    y += mid_h + 20

    # ── 三、我认为该改的（请直接圈） ──
    shadow(img, (PAD, y, W - PAD, y + weak_h), 16, CARD, RED, width=2)
    d.text((PAD + 26, y + 16), "④ 我认为该改的 6 处", font=f_sect, fill=RED)
    d.text((PAD + 26 + f_sect.getlength("④ 我认为该改的 6 处") + 16, y + 24),
           "红=结构性问题　黄=机制缺一半　绿=优先级低（请直接在图上圈出你要先动的）", font=f_small, fill=FAINT)
    wy = y + 60
    for mark, title, desc, col in WEAK:
        d.ellipse((PAD + 30, wy + 12, PAD + 46, wy + 28), fill=col)
        d.text((PAD + 60, wy + 6), title, font=f_box, fill=col)
        d.text((PAD + 60 + f_box.getlength(title) + 18, wy + 12), desc, font=f_small, fill=INK)
        wy += 46

    # ── 页脚 ──
    d.line((PAD, total_h - 46, W - PAD, total_h - 46), fill="#d7dfe8", width=2)
    d.text((PAD, total_h - 34),
           "详版（组件清单）：pi-lab-架构图.png　·　正本：D:\\AI\\pi-lab　·　契约 board/schema.md　·　"
           "自检 selfcheck　·　改架构请同时改 tools/arch-diagram-simple.py",
           font=f_small, fill=MUTED)

    out = img.convert("RGB")
    out.save(a.out)
    print(f"✓ 已生成：{a.out}  ({out.size[0]}x{out.size[1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
