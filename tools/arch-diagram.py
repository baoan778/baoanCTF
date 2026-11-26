#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""arch-diagram.py —— 把 pi-lab 的架构画成一张桌面 PNG（离线，只用 Pillow）

为什么有它：架构图在对话里是 ASCII，没法带走。这个脚本把同一份内容画成图片，
默认存到桌面，方便贴给别人或存档。**内容与 docs/状态快照.md、AGENTS.md 同源**，改架构时一起改。

版面：左边 7 层（人格 → 工程底座），右边四张卡（数据流 / 硬门禁 / 纪律 / 成本与停止）。
写得死的数字必然漂（今天已经漂过一次），所以能算的都现场算
（skills / tools / prompts / refs / tests / subs / projects）。

用法：
    py tools/arch-diagram.py                        # 输出到桌面 pi-lab-架构图.png
    py tools/arch-diagram.py --out /tmp/x.png
"""
from __future__ import annotations

import argparse
import re
import subprocess
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
DEFAULT_OUT = Path("/mnt/c/Users/幺零零/Desktop") / "pi-lab-架构图.png"

W = 1720
PAD = 48
GAP = 26
LEFT_W = 1046
RIGHT_W = W - PAD * 2 - LEFT_W - GAP

BG_TOP, BG_BOT = "#f7f9fc", "#e8eef5"
CARD = "#ffffff"
INK = "#13212e"
MUTED = "#5f7183"
FAINT = "#93a3b3"
ACCENT = "#2f6fb3"
LINE = 28  # 左卡正文字行高

# (序号, 标题, 副标题, 主色, 条目...)
LAYERS = [
    ("1", "人格 / 入口层", "pi 启动自动读，常驻上下文", "#2f6fb3", [
        "AGENTS.md 七节：身份 · skill 铁律 · SRC 五步 · 工作纪律 · flag 纪律 · 黑板 · WP",
        "扩展 ×4：lab-preflight 启动自检 · think-clock（/time）· ocgo-switch · godmode",
    ]),
    ("2", "知识层", "按需加载，不烧常驻上下文", "#0f9b8e", [
        "skills {skills} 个 = 10 个 ctf-* + awdp / ir-response / src-recon + ctf-kit / fgs-orchestrate / wp-writer",
        "各带 references/ 共 {refs} 篇深水资料 · 收洞三裁决：accepted→Fact ｜ ignored→已证伪 ｜ deepen→派生 Step",
        "手法库跨题沉淀（人审才进）· 外部经验只认「当年」的（2026 第二轮 19 条已在案）",
    ]),
    ("3", "工具层", "tools/bin {tools} 个命令 · TOOLS.md 是唯一事实来源", "#d98218", [
        "侦察 httpx / katana / gau　｜　扫描 nuclei / dirsearch / ffuf　｜　利用 dalfox / commix / ssrfmap / jwt_tool",
        "取证 volatility / binwalk / pycdc · poc 离线库 11290 条（骨架 + 判定式）· py = pwn/z3/Crypto venv",
        "selfcheck 自检 · handover 交接包 · fgs 黑板 · sesscan 会话康复 · sweep 僵尸进程回收",
    ]),
    ("4", "编排层 ★ 核心", "board/fgs.py · 纯标准库 · 契约 board/schema.md", "#c0392b", [
        "四节点 goal / step / fact / hint　｜　11 种关系（端点矩阵 + 4 种无环约束）",
        "三任务 bootstrap / explore / reason　｜　调度态 wait / reclaim / none（不是第四类任务）",
        "唯一写入者 = 主会话；Worker 只能 publish → outbox → apply 校验后合入　｜　子命令 {subs} 个",
        "solved 依据必须挂在图上（孤点拒绝）· step 租约 1800s + heartbeat · reason 不许空手（防自激）",
    ]),
    ("5", "执行层", "pi subagents + prompt 模板", "#7d5bbe", [
        "pi subagents：fresh context（不 fork 对话）· async（投完就走）",
        "prompts/ {prompts} 个：bootstrap / explore / *-conclude · reason · wp · 赛前检查",
        "SRC 六步：收集 → 指纹 → 匹配 PoC → 验证 → 复审（fresh 代理只看证据）→ 留痕",
    ]),
    ("6", "记忆 / 沉淀层", "真相源 + 审计 + 留档", "#2e8b57", [
        "graph.json（真相源）· log.jsonl（审计）· outbox/deliveries（四终态）· work/<pid>/ 留档 + _evidence/",
        "WP 代理（fresh context）→ WRITEUP.md + 手法库.md",
    ]),
    ("7", "工程底座", "可回滚 · 可自检 · 可交接", "#5a6570", [
        "git 只跟踪「人写的资产」· {tests} 个测试文件（并行 4 路）· doctor 对 {projects} 个项目全绿",
        "纪律：改完必跑 selfcheck → commit；坏了能 diff / revert",
    ]),
]

FLOW = [
    "人发题 / 给目标",
    "起题型 skill（只加载当前那个）",
    "fgs next → 唯一答案",
    "bootstrap / explore / reason",
    "Worker 干活 → publish",
    "outbox → apply 校验合入",
    "Fact 入图（只有 verified 算结论）",
    "fgs solved（我说解出来了）",
    "fgs confirm（人看平台回显）",
    "WP 代理 → 手法库",
]

GATES = [
    ("只有 verified 能当结论", "exit 3"),
    ("抢单冲突：已被 claim 的不能抢", "exit 2"),
    ("outbox 有一条不合法 → 整批拒绝", "exit 3"),
]

PROMISES = [
    "状态是系统资产，不是模型回忆（l3yx 2026-05）",
    "能机器判的就不靠自觉：门禁 fail-closed",
    "超时也算产出：留下「试过哪条路」的 Fact",
]

COST = [
    "单题投入超过它带来的分就换题（52 → 284 元/flag 的边际递减）",
    "「何时停」要有判据：打穿了（verified），或死路论证 Fact",
    "模型换代后复审门禁：拒不到真问题的，降级为提示",
]


def font(name: str, size: int):
    for p in (WIN_FONTS / name, WIN_FONTS / "Deng.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(str(p), size)
        except Exception:
            continue
    return ImageFont.load_default()


def wrap(text: str, f, max_w: int) -> list[str]:
    """按像素宽度折行：CJK 逐字断，ASCII 尽量整词断。"""
    out: list[str] = []
    cur, buf, tokens = "", "", []
    for ch in text:
        if ch.isascii() and not ch.isspace():
            buf += ch
        else:
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
    if buf:
        tokens.append(buf)
    for t in tokens:
        trial = cur + t
        if f.getlength(trial) <= max_w or not cur.strip():
            cur = trial
        else:
            out.append(cur.rstrip())
            cur = "" if t.isspace() else t
    if cur.strip():
        out.append(cur.rstrip())
    return out or [""]


def live_facts() -> dict:
    """写得死的数字必然漂 —— 能算的都现场算。"""
    tests = len(list((LAB / "board" / "tests").glob("test_*.py")))
    refs = len(list((LAB / "skills").glob("*/references/**/*.md")))
    skills = len(list((LAB / "skills").glob("*/SKILL.md")))
    bindir = LAB / "tools" / "bin"
    tools = len(list(bindir.iterdir())) if bindir.is_dir() else 0
    prompts = len(list((LAB / "prompts").glob("*.md")))
    pdir = LAB / "board" / "projects"
    projects = len([p for p in pdir.iterdir() if p.is_dir()]) if pdir.is_dir() else 0
    subs = "?"
    try:
        out = subprocess.run(["python3", str(LAB / "board" / "fgs.py"), "--help"],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=30).stdout
        m = re.search(r"\{([a-z0-9,\-]+)\}", out)
        if m:
            subs = len(m.group(1).split(","))
    except Exception:
        pass
    return {"tests": tests, "refs": refs, "subs": subs, "skills": skills,
            "tools": tools, "prompts": prompts, "projects": projects}


def head_sha() -> str:
    """只要 HEAD 短哈希。

    刻意**不**查 `git status`：drvfs（/mnt/d）上扫一遍 600+ 跟踪文件的 stat 要 10 秒以上
    （实测 `--porcelain` 10.6s），画图不值这个钱。要看干净与否请跑 `selfcheck`。
    """
    try:
        return subprocess.run(["git", "-C", str(LAB), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=30).stdout.strip() or "?"
    except Exception:
        return "?"


def tint(hex_color: str, ratio: float) -> str:
    """把颜色往白色混，得到**不透明**浅底色。

    为什么要这个函数：ImageDraw 在 RGBA 图上不做 alpha 合成 —— 给它 8 位 hex 的
    `#rrggbbaa`，它会把 alpha 直接**写进像素**，最后 `convert("RGB")` 丢掉 alpha，
    于是浅底变回饱和色、同色文字看不见（实测踩过：页签全是实心色块、卡头标题消失）。
    """
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    r, g, b = (int(c + (255 - c) * ratio) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def shadow_card(img: Image.Image, box, radius: int, fill: str, outline: str | None = None,
                width: int = 2, blur: int = 9, dy: int = 5, alpha: int = 34):
    """先画模糊阴影层再画卡片 —— 比纯描边好看，也不需要外部依赖。"""
    x0, y0, x1, y1 = box
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((x0, y0 + dy, x1, y1 + dy), radius=radius,
                                        fill=(15, 30, 50, alpha))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(blur)))
    if outline:
        ImageDraw.Draw(img).rounded_rectangle(box, radius=radius, fill=fill,
                                              outline=outline, width=width)
    else:
        ImageDraw.Draw(img).rounded_rectangle(box, radius=radius, fill=fill)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    a = ap.parse_args()

    f_title = font("msyhbd.ttc", 46)
    f_sub = font("msyh.ttc", 20)
    f_chip = font("msyhbd.ttc", 19)
    f_lhead = font("msyhbd.ttc", 25)
    f_lsub = font("msyh.ttc", 17)
    f_body = font("msyh.ttc", 19)
    f_small = font("msyh.ttc", 17)
    f_rhead = font("msyhbd.ttc", 23)
    f_rbody = font("msyh.ttc", 18)
    f_mono = font("msyh.ttc", 18)

    LIVE = live_facts()
    layers = []
    for n, t, sb, c, items in LAYERS:
        for k, v in LIVE.items():
            sb = sb.replace("{" + k + "}", str(v))
            items = [it.replace("{" + k + "}", str(v)) for it in items]
        layers.append((n, t, sb, c, items))

    # ── 量高度 ──
    inner_w = LEFT_W - 34 - 30
    lay_heights, left_h = [], 0
    for _n, _t, _sb, _c, items in layers:
        lines = sum(len(wrap(it, f_body, inner_w)) for it in items)
        h = 22 + 34 + lines * LINE + 18
        lay_heights.append(h)
        left_h += h + 18
    left_h -= 18

    def card_h(rows, inner, per=26, pad_top=60, pad_bot=16, extra=0):
        n = sum(len(wrap(t, f_rbody, inner)) for t, *_ in rows) if rows and isinstance(rows[0], tuple) \
            else sum(len(wrap(t, f_rbody, inner)) for t in rows)
        return pad_top + n * per + extra + pad_bot

    flow_h = 60 + len(FLOW) * 34 + 16
    gates_h = card_h(GATES, RIGHT_W - 108, per=27, extra=len(GATES) * 6)
    promise_h = card_h(PROMISES, RIGHT_W - 68, per=26) + 8
    cost_h = card_h(COST, RIGHT_W - 68, per=26) + 8
    right_h = flow_h + 18 + gates_h + 18 + promise_h + 18 + cost_h

    header_h = 148
    body_h = max(left_h, right_h)
    total_h = PAD + header_h + 26 + body_h + 30 + 54

    # ── 背景渐变 ──
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
    hbox = (PAD, PAD, W - PAD, PAD + header_h)
    shadow_card(img, hbox, 20, CARD, None, blur=12, dy=6, alpha=40)
    d.rounded_rectangle((PAD, PAD, PAD + 10, PAD + header_h), radius=5, fill=ACCENT)
    d.text((PAD + 34, PAD + 26), "pi-lab · 整体架构", font=f_title, fill=INK)
    sub = (f"CTF / SRC 渗透 / 应急响应 agent 环境　·　黑板编排（FGS，参考 Cairn）"
           f"　·　生成 {datetime.now(timezone(timedelta(hours=8))):%Y-%m-%d %H:%M}"
           f"　·　HEAD {head_sha()}")
    d.text((PAD + 36, PAD + 88), sub, font=f_sub, fill=MUTED)

    chips = [(LIVE["skills"], "skills", "#0f9b8e"), (LIVE["tools"], "tools", "#d98218"),
             (LIVE["refs"], "refs", ACCENT), (LIVE["tests"], "tests", "#5a6570"),
             (LIVE["projects"], "projects", "#c0392b")]
    texts = [f"{v} {lbl}" for v, lbl, _c in chips]
    widths = [f_chip.getlength(t) + 34 for t in texts]
    total_cw = sum(widths) + 10 * (len(chips) - 1)
    cx = W - PAD - 34 - total_cw
    for (v, lbl, col), text, cwid in zip(chips, texts, widths):
        d.rounded_rectangle((cx, PAD + 34, cx + cwid, PAD + 74), radius=20,
                            fill=tint(col, 0.88), outline=col, width=2)
        d.text((cx + 17, PAD + 42), text, font=f_chip, fill=col)
        cx += cwid + 10

    # ── 左列：7 层 ──
    x0, y = PAD, PAD + header_h + 26
    left_bottom = y + left_h
    for (num, title, subtitle, color, items), h in zip(layers, lay_heights):
        box = (x0, y, x0 + LEFT_W, y + h)
        shadow_card(img, box, 16, CARD, color, width=2)
        d.rounded_rectangle((x0, y, x0 + 8, y + h), radius=4, fill=color)
        d.ellipse((x0 + 24, y + 18, x0 + 58, y + 52), fill=color)
        nw = f_lhead.getlength(num)
        d.text((x0 + 41 - nw / 2, y + 23), num, font=f_lhead, fill="#ffffff")
        tx = x0 + 72
        d.text((tx, y + 20), title, font=f_lhead, fill=color)
        d.text((tx + f_lhead.getlength(title) + 16, y + 30), subtitle, font=f_lsub, fill=FAINT)
        ty = y + 56
        for it in items:
            for ln in wrap(it, f_body, inner_w):
                d.text((tx, ty), ln, font=f_body, fill=INK)
                ty += LINE
        if y + h + 18 < left_bottom:
            d.text((x0 + LEFT_W / 2 - 8, y + h + 1), "▼", font=f_small, fill="#b8c4d0")
        y += h + 18

    # ── 右列：四张卡 ──
    rx = PAD + LEFT_W + GAP
    ry = PAD + header_h + 26

    def right_card(rbox, head, color, height):
        shadow_card(img, rbox, 16, CARD, color, width=2)
        dd = ImageDraw.Draw(img)
        dd.rounded_rectangle((rbox[0] + 1, rbox[1] + 1, rbox[2] - 1, rbox[1] + 54), radius=15,
                             fill=tint(color, 0.9))
        dd.rectangle((rbox[0] + 1, rbox[1] + 40, rbox[2] - 1, rbox[1] + 54), fill=tint(color, 0.9))
        dd.rounded_rectangle((rbox[0], rbox[1], rbox[0] + 8, rbox[1] + height), radius=4, fill=color)
        dd.text((rbox[0] + 26, rbox[1] + 14), head, font=f_rhead, fill=color)

    # 卡 1：数据流
    box1 = (rx, ry, rx + RIGHT_W, ry + flow_h)
    right_card(box1, "数据流", ACCENT, flow_h)
    fy = ry + 64
    for i, s in enumerate(FLOW):
        last = i == len(FLOW) - 1
        d.ellipse((rx + 26, fy + 6, rx + 40, fy + 20), fill=ACCENT if i in (0, len(FLOW) - 1) else "#c9d6e4")
        for j, ln in enumerate(wrap(s, f_rbody, RIGHT_W - 92)):
            d.text((rx + 52, fy + j * 22), ln, font=f_rbody, fill=INK)
        if not last:
            d.line((rx + 33, fy + 22, rx + 33, fy + 34), fill="#c9d6e4", width=2)
        fy += 34
    ry += flow_h + 18

    # 卡 2：三道硬门禁
    box2 = (rx, ry, rx + RIGHT_W, ry + gates_h)
    right_card(box2, "三道硬门禁（机器判，实测会拒）", "#c0392b", gates_h)
    gy = ry + 64
    for g, code in GATES:
        d.rectangle((rx + 26, gy + 5, rx + 30, gy + 22), fill="#c0392b")
        lines = wrap(g, f_rbody, RIGHT_W - 108)
        for j, ln in enumerate(lines):
            d.text((rx + 40, gy + j * 22), ln, font=f_rbody, fill=INK)
        d.text((rx + RIGHT_W - 86, gy), code, font=f_mono, fill="#c0392b")
        gy += 27 * len(lines) + 6
    ry += gates_h + 18

    def bullet_card(rbox, head, color, rows):
        right_card(rbox, head, color, rbox[3] - rbox[1])
        yy = rbox[1] + 60
        for p in rows:
            dd = ImageDraw.Draw(img)
            dd.text((rbox[0] + 26, yy - 2), "·", font=f_rhead, fill=color)
            lines = wrap(p, f_rbody, RIGHT_W - 68)
            for j, ln in enumerate(lines):
                dd.text((rbox[0] + 44, yy + j * 22), ln, font=f_rbody, fill=INK)
            yy += 26 * len(lines)

    # 卡 3：三条不破的纪律
    box3 = (rx, ry, rx + RIGHT_W, ry + promise_h)
    bullet_card(box3, "三条不破的纪律", "#2e8b57", PROMISES)
    ry += promise_h + 18

    # 卡 4：成本与停止
    box4 = (rx, ry, rx + RIGHT_W, ry + cost_h)
    bullet_card(box4, "成本与「何时停」", "#7d5bbe", COST)

    # ── 页脚 ──
    d.line((PAD, total_h - 52, W - PAD, total_h - 52), fill="#d7dfe8", width=2)
    d.text((PAD, total_h - 38),
           "正本：D:\\AI\\pi-lab　·　契约 board/schema.md　·　自检 selfcheck　·　交接 handover　·　"
           "启动 run-pi.bat　·　会话康复 sesscan where",
           font=f_small, fill=MUTED)

    out = img.convert("RGB")
    out.save(a.out)
    print(f"✓ 已生成：{a.out}  ({out.size[0]}x{out.size[1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
