#!/usr/bin/env python3
r"""test_prompt_injection.py — L11 提示词注入参考的回归测试

为什么要有这一份
----------------
2026-11-09 拆 OpenSecurity examples/AI安全 的 payload 得出三条结构手法，写进了
`skills/ctf-ai-ml/references/llm-attacks.md`。但「写进文档」不等于「能用」：
  · 手法可能被后续编辑删掉（文档无测试就没有护栏）；
  · **成功判据**最容易退化 —— 没有它会写出「模型说了句客气话 = 打穿了」的假阳性；
  · 原始 payload 是解题资产，丢了就再也对不上。
所以本文件守四件事：三手法在、模板在、判据四格在、原始 payload 在。

    python3 board/tests/test_prompt_injection.py
"""
from __future__ import annotations

import pathlib
import re
import sys

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = pathlib.Path(__file__).resolve().parents[2]
REF = LAB / "skills" / "ctf-ai-ml" / "references" / "llm-attacks.md"
SKILL = LAB / "skills" / "ctf-ai-ml" / "SKILL.md"

checks: list = []


def ok(name, cond, extra=""):
    checks.append((name, bool(cond), str(extra)))


def _report():
    bad = [c for c in checks if not c[1]]
    for name, good, extra in checks:
        print(f"  {'✓' if good else '✗'} {name}" + (f"   {extra[:300]}" if extra and not good else ""))
    print(f"\n通过 {len(checks) - len(bad)} / 失败 {len(bad)}")
    if bad:
        print("失败项：" + "、".join(c[0] for c in bad))
        return 1
    print("✓ L11 提示词注入参考：全部通过")
    return 0


def main() -> int:
    ok("llm-attacks.md 存在", REF.is_file())
    if not REF.is_file():
        return _report()
    doc = REF.read_text(encoding="utf-8", errors="replace")

    # ── 1. 三手法在 ──────────────────────────────────────────────
    ok("有一级章节「结构化注入」", "结构化注入" in doc)
    ok("手法一：伪造权威边界", "伪造权威边界" in doc, doc[:200])
    ok("手法二：伪造流程合法性", "伪造流程合法性" in doc)
    ok("手法三：给祈使指令", "祈使指令" in doc)

    # 三手法各自要讲清「为什么有效」，不是只列名字
    ok("手法一解释了为什么有效（训练语料的模式）",
       "训练" in doc and ("模式" in doc or "共现" in doc))
    ok("手法二说明引用不需要真实存在",
       "不需要" in doc or "形状" in doc)
    ok("手法三给了错误写法做对照", "诉求" in doc and "request" in doc)

    # ── 2. 原始 payload 在（解题资产）────────────────────────────
    for frag in ("Instructor Assessment Record", "Do Not Remove From Submission",
                 "2024-1847", "Academic Policy 12.4", "End Assessment Record"):
        ok(f"原始 payload 保留「{frag}」", frag in doc)
    ok("payload 标了来源（哪道题 / 哪个文件）",
       "UltimateAIEssayGrader" in doc and "final_payload.docx" in doc)

    # ── 3. 组合模板在 ────────────────────────────────────────────
    ok("给了可填空的组合模板", "组合模板" in doc)
    ok("模板含期望输出形态占位", "<期望输出形态>" in doc)
    ok("提醒注入位置（正文中段，别放首尾）",
       "中间" in doc and ("开头" in doc or "首尾" in doc))
    ok("提醒期望值要具体（不是 a good score）",
       "具体" in doc and "good score" in doc)

    # ── 4. 成功判据四格（**最容易被删的一节**）──────────────────
    ok("有「成功判据」小节", "成功判据" in doc)
    four = {
        "非自然值": "非自然值" in doc,
        "可复现": "可复现" in doc,
        "对照组": "对照组" in doc,
        "不是规则命中": "规则命中" in doc or "规则漏洞" in doc,
    }
    for k, v in four.items():
        ok(f"判据含「{k}」", v, str([x for x, y in four.items() if not y]))
    ok("四格全过才算打穿（不是过一格就算）",
       "四格全过" in doc, doc[-200:])
    ok("点名了假阳性这个失败形态", "假阳性" in doc)
    ok("给了可执行的测法（基线/加注入/复现/反向对照）",
       "基线" in doc and "反向对照" in doc)

    # 反例列必须存在，否则判据只是口号
    ok("判据表有「反例」列", "反例" in doc)
    ok("反例里点名了「会认真评估」这类废话", "认真评估" in doc)

    # ── 5. 边界：模型输出不能免检 ───────────────────────────────
    ok("明确模型输出也要走 fgs verify",
       "fgs verify" in doc, doc[-400:])
    ok("与证据三级的边界写清了",
       "schema.md" in doc or "证据三级" in doc)

    # ── 6. 挂到了 SKILL.md（否则 agent 不知道有这一节）───────────
    ok("SKILL.md 存在", SKILL.is_file())
    sk = SKILL.read_text(encoding="utf-8", errors="replace") if SKILL.is_file() else ""
    ok("SKILL.md 的 references 清单点出了结构化注入",
       "结构化注入" in sk, sk[:300])
    ok("SKILL.md 的「坑」里提醒了注入成功判据",
       "判据" in sk and "非自然值" in sk)
    ok("SKILL.md 未超 9000 字节预算",
       len(sk.encode()) <= 9000, f"{len(sk.encode())} 字节")

    # ── 7. 没有与既有内容重复建设 ───────────────────────────────
    ok("没有另建 prompt-injection.md（复用 llm-attacks.md）",
       not (LAB / "skills" / "ctf-ai-ml" / "references" / "prompt-injection.md").exists())
    heads = re.findall(r"^## (.+)$", doc, re.M)
    ok("章节标题无重复", len(heads) == len(set(heads)), str([h for h in heads if heads.count(h) > 1]))

    return _report()


if __name__ == "__main__":
    sys.exit(main())
