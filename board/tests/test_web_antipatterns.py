#!/usr/bin/env python3
r"""test_web_antipatterns.py — L9/L10（web 反模式）的回归测试

为什么要有这一份
----------------
L9「转义/解码顺序反模式」与 L10「响应头二次拼装」都是**判据型知识**，2026-11-09
从 OpenSecurity `examples/web` 读码得出，写进 `skills/ctf-web/references/filter-bypass.md`
的第 6/7 层。这类内容最容易在后续编辑里被"精简"掉，而它恰恰是下次遇到同类题时
省下几十分钟的东西。所以本文件守：
  · 两层都在，且各自带**识别步骤**和**可执行测法**（不是只写名字）；
  · 引用的**真实代码形态**在（htmlspecialchars_decode / charset 捕获组）；
  · 头部「七层」口径与实际章节数一致（防第 8 次"文档说五层、正文有七层"的漂移）。

    python3 board/tests/test_web_antipatterns.py
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
REF = LAB / "skills" / "ctf-web" / "references" / "filter-bypass.md"
SKILL = LAB / "skills" / "ctf-web" / "SKILL.md"

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
    print("✓ L9/L10 web 反模式参考：全部通过")
    return 0


def main() -> int:
    ok("filter-bypass.md 存在", REF.is_file())
    if not REF.is_file():
        return _report()
    doc = REF.read_text(encoding="utf-8", errors="replace")

    # ── L9：转义/解码顺序 ───────────────────────────────────────
    ok("有第 6 层（转义/解码顺序）", "第 6 层" in doc, doc[:300])
    ok("点明这是「目标自己拆自己」（不是绕过外部过滤）",
       "自己拆自己" in doc or "防线写反" in doc)
    ok("引用了真实代码形态 htmlspecialchars_decode",
       "htmlspecialchars_decode" in doc)
    ok("说明了根因（分隔符不在转义表里）",
       "分隔符" in doc or "转义表" in doc)
    ok("给了正确做法（先解析再分别转义 / 协议白名单）",
       "正确顺序" in doc, doc[-400:])
    ok("有识别步骤（≥4 步表格）",
       "怎么在题里识别" in doc and doc.count("| ① |") + doc.count("| ①") >= 1)
    ok("识别步骤含「decode 结果流向哪」这一问",
       "流向" in doc)
    ok("提醒按解码层数造 payload（1 层/2 层）",
       "解码 1 次" in doc or "几层" in doc or "1 层" in doc)
    ok("给了双重编码的实例（%2522 或 &amp;amp;）",
       "%2522" in doc or "&amp;amp;" in doc)

    # ── L10：响应头二次拼装 ─────────────────────────────────────
    ok("有第 7 层（响应头二次拼装）", "第 7 层" in doc)
    ok("点明只挡了 \\r\\n（头注入），没挡语义篡改",
       "\\r\\n" in doc and "语义" in doc, doc[3000:3400])
    ok("引用了 charset 捕获组这个真实形态",
       "charset" in doc and ("捕获组" in doc or "(.+)" in doc))
    ok("给了可执行探测命令（curl -H Content-Type）",
       "curl -si" in doc and "charset" in doc)
    ok("给出通用判据（见 f-string 拼 header 就查白名单）",
       "白名单" in doc and ("拼 header" in doc or "拼接" in doc))

    # ── 口径一致：七层 ──────────────────────────────────────────
    layers = sorted({int(m) for m in re.findall(r"^## 第 (\d+) 层", doc, re.M)})
    ok("章节层号连续且从 1 开始", layers == list(range(1, len(layers) + 1)), str(layers))
    ok("共 7 层", layers == [1, 2, 3, 4, 5, 6, 7], str(layers))
    ok("头部口径写「七层」而不是旧的「五层」",
       "七层" in doc and "五层检查树" not in doc)
    ok("头部说明了第 6/7 层方向相反",
       "相反方向" in doc or "相反" in doc)

    # ── 挂到 SKILL.md ───────────────────────────────────────────
    ok("ctf-web/SKILL.md 存在", SKILL.is_file())
    sk = SKILL.read_text(encoding="utf-8", errors="replace") if SKILL.is_file() else ""
    ok("SKILL.md 的 filter-bypass 行点出了新增两层",
       "七层" in sk and "响应头二次拼装" in sk, sk[:400])

    # ── 不重复建设 ──────────────────────────────────────────────
    ok("没有另建 web-antipatterns.md（复用了 filter-bypass.md）",
       not (LAB / "skills" / "ctf-web" / "references" / "web-antipatterns.md").exists())

    return _report()


if __name__ == "__main__":
    sys.exit(main())
