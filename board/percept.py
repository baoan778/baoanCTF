#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""percept.py —— LLM 感知结果的交叉验证（OCR 防幻觉 · 可实际执行的那一半）

为什么要有它
------------
vision-LLM 会产生「场景级合理文本幻觉」：从整图场景先验编造出看似合理、
可自洽复现、反相稳定的内容。**一致性不能证明真实** —— 同一模型问三次
给同一个答案，恰恰是幻觉的典型形态（它每次都从同一套先验出发）。

源协议：OpenSecurity-2.0 的 `llm-perception-verification.md`（39 行）。
本文件是它在 baoan 落地的**可执行版本**，并诚实标注了降级点（见下）。

★ 降级点（必须知道）
------------------
原协议的核心判据是「双经典引擎零检出」：tesseract 与 RapidOCR 各跑一遍。
**本机只有 tesseract**（实测：容器内 `tesseract 5.3.4`，无 rapidocr/easyocr/
paddleocr；pip 受 PEP 668 管控，临时装不了）。
→ 所以本文件用「**同一引擎的多配置复现**」替代「双引擎」，可信度**低于**原协议。
   它能抓到的：LLM 读出的东西在经典引擎任何配置下都不存在（强幻觉信号）。
   它**抓不到**的：两个引擎都会漏的低对比度真文字（此时需要像素终裁兜底）。
   这个区别必须写在结论里，不能假装我们做了双引擎验证。

用法
----
  py board/percept.py verify --img shot.png --claim "sun{abc}"        # 验证一个声称
  py board/percept.py scan   --img shot.png                          # 引擎能看到什么
  py board/percept.py pixels --img shot.png --region 100,200,400,60   # 像素终裁辅助
  py board/percept.py --selftest                                      # 离线自检

退出码：0=通过（引擎命中）/ 3=高度可疑（零命中）/ 4=输入错误 / 5=环境缺依赖
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ── 多配置：同一引擎、彼此独立的读法（PSM 差异足够大才算"独立"）──────────
# psm 3=全自动版面 / psm 6=单块 / psm 11=稀疏 / psm 7=单行
# 为什么要 4 种：单一 PSM 会系统性漏某一类排版；4 种里至少一种能覆盖真文字，
#   而幻觉文本在**任何**配置下都不存在（这正是我们要的信号）。
CONFIGS = [
    ("psm3", ["--psm", "3"]),
    ("psm6", ["--psm", "6"]),
    ("psm11", ["--psm", "11"]),
    ("psm7", ["--psm", "7"]),
]

# 归一化时忽略的字符：空白与常见 OCR 噪声标点
_STRIP = re.compile(r"[\s\n\r\t]+")


def _norm(s: str) -> str:
    return _STRIP.sub("", s or "")


def have_tesseract() -> bool:
    return bool(shutil.which("tesseract"))


def ocr(img: Path, extra: list[str], timeout: int = 60) -> tuple[str, str]:
    """跑一次 tesseract，返回 (文本, 错误摘要)。"""
    try:
        p = subprocess.run(  # noqa: S603
            ["tesseract", str(img), "-", *extra],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        return (p.stdout or ""), (p.stderr or "")[:300]
    except Exception as e:               # noqa: BLE001
        return "", f"{e.__class__.__name__}: {e}"


def scan(img: Path) -> dict:
    """四种配置各跑一遍，返回 {config: text}。"""
    out = {}
    for name, extra in CONFIGS:
        txt, _err = ocr(img, extra)
        out[name] = txt
    return out


def match_claim(claim: str, texts: dict) -> dict:
    """声称的文本是否在任一配置里出现（归一化后比对 + 逐字符覆盖率）。

    三级判定（由强到弱）：
      exact  —— 归一化后完整出现（最硬）
      chars  —— 至少 80% 的字符出现在同一配置里（顺序大致保留）
      none   —— 都不沾边
    """
    c = _norm(claim)
    if not c:
        return {"level": "none", "hits": [], "coverage": 0.0, "detail": []}

    hits, detail = [], []
    best_cov, best_cfg = 0.0, None
    for name, raw in texts.items():
        n = _norm(raw)
        if c in n:
            hits.append(name)
            detail.append(f"{name}: 完整命中")
            best_cov, best_cfg = 1.0, name
            continue
        cov = _char_coverage(c, n)
        detail.append(f"{name}: 字符覆盖 {cov:.0%}")
        if cov > best_cov:
            best_cov, best_cfg = cov, name

    if hits:
        level = "exact"
    elif best_cov >= 0.8:
        level = "chars"
    else:
        level = "none"
    return {
        "level": level, "hits": hits,
        "coverage": round(best_cov, 3),
        "best_config": best_cfg,
        "detail": detail,
    }


def _char_coverage(claim: str, pool: str) -> float:
    """claim 的字符有多少比例出现在 pool 里（多重集口径，避免重复字符作弊）。"""
    if not claim:
        return 0.0
    from collections import Counter
    c, p = Counter(claim), Counter(pool)
    got = sum(min(v, p.get(k, 0)) for k, v in c.items())
    return got / len(claim)


def verdict_of(claim: str, texts: dict) -> dict:
    """把 match 结果翻成一句可照做的结论（这是本工具的价值所在）。"""
    m = match_claim(claim, texts)
    if m["level"] == "exact":
        tail = (f"✓ 通过：{', '.join(m['hits'])} 完整读出了该串。"
                f"（验证方式：tesseract 多配置复现，**非双引擎**）")
        ok = True
    elif m["level"] == "chars":
        tail = (f"⚠ 可疑但不足以否决：最高字符覆盖 {m['coverage']:.0%}（{m['best_config']}），"
                f"未完整命中。做像素终裁再定 —— `py board/percept.py pixels`。")
        ok = False
    else:
        tail = (f"🚨 高度可疑：四种配置全部零命中（最高覆盖 {m['coverage']:.0%}）。"
                f"按协议判幻觉，**不要写进结论**；除非像素终裁能找到字形结构。")
        ok = False
    return {"claim": claim, "match": m, "ok": ok, "verdict": tail}


# ── 像素终裁：不依赖 PIL/numpy（本机没有），用 tesseract 自身的二值化能力 ──
def pixel_hint(img: Path, region: str | None) -> dict:
    """给"字形在不在"提供一个低成本旁证。

    真正的像素终裁需要高通滤波 + ASCII 渲染（原协议用 PIL/numpy 做）。
    本机没有 PIL/numpy（实测），所以这里退一步：用 tesseract 的
    `--psm 10`（单字符）与 `makebox` 输出的**置信度**，判断"这个区域里
    引擎认为有没有字"。

    ★ 这是旁证不是终裁：置信度低 ≠ 一定没字（低对比度真字也会低），
      但"引擎明确说这里有个字且置信度高"是强证据。
    """
    out = {"engine_confidence": {}, "boxes": None}
    for name, extra in [("psm10", ["--psm", "10"]), ("psm6", ["--psm", "6"])]:
        # tesseract 的 tsv 输出带 conf 列
        try:
            p = subprocess.run(  # noqa: S603
                ["tesseract", str(img), "-", *extra, "tsv"],
                capture_output=True, text=True, timeout=60,
                encoding="utf-8", errors="replace",
            )
            confs = []
            for line in (p.stdout or "").splitlines()[1:]:
                parts = line.split("\t")
                if len(parts) >= 11:
                    try:
                        c = float(parts[10])
                    except ValueError:
                        continue
                    t = parts[11] if len(parts) > 11 else ""
                    if t.strip():
                        confs.append((c, t.strip()))
            out["engine_confidence"][name] = confs[:20]
        except Exception:
            out["engine_confidence"][name] = []
    return out


def _selftest() -> int:
    """离线自检：不依赖真图片，直接测比对逻辑（穷举边界）。"""
    fail = []

    def eq(name, got, want):
        if got != want:
            fail.append(f"{name}: got={got!r} want={want!r}")

    # 1) 归一化：空白必须被忽略（OCR 常在花括号内塞空格）
    eq("norm 去空白", _norm("SUN{abc 123}\n"), "SUN{abc123}")

    # 2) 完整命中
    r = match_claim("SUN{test123}", {"psm6": "SUN{test123}\n"})
    eq("完整命中 level", r["level"], "exact")
    eq("完整命中 hits", r["hits"], ["psm6"])

    # 3) 花括号内空格仍算完整命中（真实 OCR 输出形态）
    r = match_claim("SUN{test123}", {"psm11": "SUN{test123 }\n"})
    eq("空格容错", r["level"], "exact")

    # 4) 零命中 → 高度可疑
    r = match_claim("SUN{deadbeef}", {"psm6": "hello world"})
    eq("零命中 level", r["level"], "none")

    # 5) 部分覆盖
    #    `SUN{test123}` 共 12 字符，`SUN{test` 覆盖 8/12 = 67% < 80% → 判 none。
    #    （我第一版这里期望 chars，是断言写错了：67% 本来就不该算「像是同一串」。
    #      真要命中 chars 级，得覆盖 ≥80%。）
    r = match_claim("SUN{test123}", {"psm6": "SUN{test"})
    eq("67% 覆盖 level", r["level"], "none")
    # 覆盖 ≥80% 才进 chars 级：10/12 = 83%
    r = match_claim("SUN{test123}", {"psm6": "SUN{test12"})
    eq("83% 覆盖 level", r["level"], "chars")

    # 6) 结论措辞：零命中必须说"判幻觉、不要写进结论"
    v = verdict_of("SUN{deadbeef}", {"psm3": "x", "psm6": "y", "psm11": "z", "psm7": "w"})
    eq("零命中 ok", v["ok"], False)
    if "幻觉" not in v["verdict"]:
        fail.append("零命中结论没提幻觉")
    if "不要写进结论" not in v["verdict"]:
        fail.append("零命中结论没给可照做的动作")

    # 7) 通过时必须声明「非双引擎」（诚实边界，防止后人误以为做了双引擎）
    v = verdict_of("SUN{abc}", {"psm6": "SUN{abc}"})
    eq("通过 ok", v["ok"], True)
    if "非双引擎" not in v["verdict"]:
        fail.append("通过结论没声明非双引擎（诚实边界缺失）")

    # 8) 空 claim 不炸
    eq("空 claim", match_claim("", {"psm6": "x"})["level"], "none")
    eq("空 pool", match_claim("abc", {"psm6": ""})["level"], "none")

    # 9) 字符覆盖不能因重复字符作弊
    #    claim 里 4 个 a，pool 里只有 1 个 a → 覆盖 25%，不是 100%
    eq("重复字符覆盖", round(_char_coverage("aaaa", "a"), 2), 0.25)

    if fail:
        print("FAIL")
        for f in fail:
            print("  " + f)
        return 1
    print("OK percept selftest: 12 组断言全绿")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM 感知结果交叉验证（OCR 防幻觉）")
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")

    p1 = sub.add_parser("verify", help="验证一个声称的文本是否真在图上")
    p1.add_argument("--img", required=True)
    p1.add_argument("--claim", required=True)

    p2 = sub.add_parser("scan", help="看引擎能在这张图里读到什么")
    p2.add_argument("--img", required=True)

    p3 = sub.add_parser("pixels", help="像素终裁辅助（置信度旁证）")
    p3.add_argument("--img", required=True)
    p3.add_argument("--region", default=None, help="x,y,w,h（当前版本仅记录，不裁剪）")

    a = ap.parse_args()

    if a.selftest:
        return _selftest()

    if not a.cmd:
        ap.print_help()
        return 4

    if not have_tesseract():
        print("环境缺依赖：找不到 tesseract（本协议的多配置复现全靠它）")
        return 5

    img = Path(a.img)
    if not img.is_file():
        print(f"输入错误：找不到图片 {img}")
        return 4

    if a.cmd == "scan":
        texts = scan(img)
        hit = False
        for name, _extra in CONFIGS:
            t = (texts.get(name) or "").strip()
            print(f"--- {name} ---")
            print(t if t else "（无输出）")
            hit = hit or bool(t)
        if not hit:
            print("\n⚠ 四种配置全部无输出 —— 图上没有引擎可读的文字。"
                  "若 LLM 读出了内容，按协议高度可疑。")
        return 0

    if a.cmd == "verify":
        texts = scan(img)
        v = verdict_of(a.claim, texts)
        print(f"声称：{a.claim}")
        for d in v["match"]["detail"]:
            print("  " + d)
        print()
        print(v["verdict"])
        return 0 if v["ok"] else 3

    if a.cmd == "pixels":
        info = pixel_hint(img, a.region)
        print(f"图片：{img}" + (f"（区域 {a.region}，当前版本不裁剪）" if a.region else ""))
        for name, confs in info["engine_confidence"].items():
            print(f"--- {name} ---")
            if not confs:
                print("  （引擎没认到任何字符）")
            for c, t in confs[:10]:
                print(f"  conf={c:5.1f}  {t!r}")
        print("\n★ 这是旁证不是终裁：置信度高=强证据「有字」；"
              "置信度低不等于没字（低对比度真字也会低）。")
        return 0

    return 4


if __name__ == "__main__":
    raise SystemExit(main())
