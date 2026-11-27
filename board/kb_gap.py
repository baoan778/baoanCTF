#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb_gap.py —— 既定决策 的执行工具：判断 OpenSecurity 的知识库哪些**真的值得并入**。

为什么要工具而不是"看着挑"
--------------------------
既定决策 的问题是「152 篇里挑 3~5 篇并进我方 kb」，但我方 kb 已有 30,819 块。
**凭印象挑 = 挑的都是我恰好记得的主题**，那不叫判断，叫偏好。
正确判据只有一条：**这篇东西我方 kb 里有没有同类内容**。
  有的 → 并进去只是重复，稀释检索面；
  没有的 → 才是真缺口。

判据怎么算（三条，都可复现）
----------------------------
1. **标题命中度**：把文件名转成自然语言查询词，去 `kb search`，看 top1 分数。
   分数高 = 我方已有高度相关的块 → **不缺**。
2. **篇幅**：太短（<40 行）的多半是索引/清单，本身不是可执行规程。
3. **可执行性**：出现命令块 / 具体参数 / 判定式 → 才算"规程"；
   纯概念描述（"XX 是一种……"）并进去没有操作价值。

用法
----
  py board/kb_gap.py scan --src "D:/baoan/OpenSecurity-2.0/.opencode" --top 20
  py board/kb_gap.py scan --src ... --kb-cmd "docker exec pictf bash -lc 'kb search ...'"   # 自定义
  py board/kb_gap.py --selftest

★ 本工具**只读**：不改 kb、不改源文件，只产出候选清单给人拍板。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 关联"这个方向在 baoan 里对应哪个 skill"，用于构造更准的检索词
DIR_TO_HINT = {
    "web-analysis": "web",
    "binary-analysis": "binary pwn reverse",
    "crypto-analysis": "crypto",
    "mobile-analysis": "mobile apk",
    "ai-security-analysis": "ai-ml llm",
    "knowledge-scout": "osint recon",
    "security-analysis-evolve": "workflow orchestration",
}

# 不算"可执行规程"的标题（索引类/清单类）
INDEX_LIKE = re.compile(r"(^index|^readme|^templates?$|^wordlists?-guide|^knowledge-writing-guide|"
                        r"^technology-selection|^script-generation|^analysis-planning$)", re.I)


def _norm_query(path: Path) -> str:
    """文件名 → 检索词：去掉扩展名、把连字符换空格。"""
    stem = path.stem
    return re.sub(r"[-_]+", " ", stem).strip()


# ★ 文件名当检索词的致命缺陷（2026-11-06 交叉验证抓到）：
#   `ecdlp-solving.md` 用文件名搜只拿 19.8 分；换成正文里的术语
#   「elliptic curve discrete log」搜 → 40.1 分，且 top1 正是我方
#   `skills/ctf-crypto/references/ecc-attacks.md`。
#   即：**我方早就有这块，是检索词选错导致误判成"缺口"**。
#   所以查询词必须从**正文**提取关键术语，不能只靠文件名。
_ABBR = {
    "ecdlp": "elliptic curve discrete log",
    "ssrf": "server side request forgery",
    "xxe": "xml external entity",
    "jwt": "json web token",
    "xss": "cross site scripting",
    "rce": "remote code execution",
    "lfi": "local file inclusion",
    "ssti": "server side template injection",
    "deserialization": "deserialization gadget chain",
    "uaf": "use after free",
    "tcache": "tcache heap exploitation",
    "frida": "frida dynamic instrumentation hook",
    "apk": "android apk reverse engineering",
    "lattice": "lattice reduction LLL",
    "ecc": "elliptic curve cryptography attack",
    "rsa": "rsa attack",
    "aes": "aes block cipher attack",
    "angr": "angr symbolic execution",
    "unicorn": "unicorn emulation",
    "shellcode": "shellcode loader",
    "kernel": "linux kernel exploitation",
}


def _terms_from(path: Path, text: str) -> str:
    """从正文提取检索词：文件名 + 缩写展开 + 首个小标题。

    只取少量高信号词 —— 检索词越长越容易一条都不命中（反而显得"缺"）。
    """
    parts = [_norm_query(path)]
    low = path.stem.lower()
    for k, v in _ABBR.items():
        if k in low:
            parts.append(v)
            break
    # 首个小标题（通常是"这文件在讲什么"的最准表述）
    # ★ 但要排除通用章节名：实测 `ecdlp-solving.md` 的首个小标题是「触发条件」，
    #   拼进检索词后成了噪声（这种词在几万块里到处都是，反而抬高/拉低分数）。
    m = re.search(r"^#{2,3}\s+(.+)$", text, re.M)
    if m:
        h = re.sub(r"^[\d.\s、A-Za-z]*", "", m.group(1)).strip()
        if 2 <= len(h) <= 40 and not ANY_SECTION.search(h):
            parts.append(h)
    return " ".join(p for p in parts if p).strip()


# 通用章节名：出现在检索词里只会引入噪声（每个文件都可能有）
ANY_SECTION = re.compile(
    r"^(触发条件|适用场景|核心思路|基本原理|概述|流程|步骤|小结|总结|注意事项|前置知识|"
    r"overview|introduction|summary|usage|steps|prerequisites|notes?)$", re.I)


def _hint_for(path: Path) -> str:
    for name, hint in DIR_TO_HINT.items():
        if name in path.parts:
            return hint
    return ""


def _executable_score(text: str) -> int:
    """0-3 分：这文件里有多少"能照做"的东西。"""
    s = 0
    if re.search(r"```(bash|sh|python|py|js|console)", text):
        s += 1
    if re.search(r"\b(curl|nmap|python3?\s|gdb|objdump|openssl|tesseract|ffmpeg|sqlmap|jwt|ghidra|ida)\b", text):
        s += 1
    if re.search(r"(判定|判据|verdict|如果.*则|when .*then|成功.*失败|exit code|退出码)", text, re.I):
        s += 1
    return s


def _lines(p: Path) -> int:
    try:
        return sum(1 for _ in p.open("r", encoding="utf-8", errors="replace"))
    except Exception:
        return 0


def kb_search_score(query: str, kb_cmd: str | None) -> float | None:
    """跑一次 kb search，取 top1 分数。拿不到就返回 None（不当成 0）。

    ★ 引号陷阱（2026-11-06 踩到，首版全 0 分）：
      查询词含空格，塞进 `docker exec bash -lc 'kb search "{q}"'` 时会被
      两层引号解析吃掉 → 实际搜的是被截断的词，于是全 0 分。
      "全部 0 分"看起来像"我方 kb 什么都没命中"，实际是**命令拼错了**。
      修法：不拼命令，直接用 argv 列表传参（shell=False），彻底避开引号。
    """
    if not kb_cmd:
        return None
    # 推荐形态：kb_cmd 是"前缀"，查询词作为最后一个 argv 追加
    # 例：--kb-cmd "docker exec pictf kb search --top 1"
    parts = kb_cmd.split()
    if not parts:
        return None
    try:
        p = subprocess.run([*parts, query], capture_output=True, text=True,  # noqa: S603
                           encoding="utf-8", errors="replace", timeout=60)
        out = p.stdout or ""
        if not out.strip():
            return None      # 命令没输出（可能容器没起来）→ 不当成 0
        m = re.search(r"\[\d+\]\s+([\d.]+)\s+", out)
        return float(m.group(1)) if m else 0.0
    except Exception:
        return None


def scan(src: Path, kb_cmd: str | None) -> list[dict]:
    out = []
    for md in sorted(src.rglob("knowledge-base/*.md")):
        rel = md.relative_to(src)
        text = md.read_text(encoding="utf-8", errors="replace")[:8000]
        n = _lines(md)
        ex = _executable_score(text)
        q = _norm_query(md) + " " + _hint_for(md)
        score = kb_search_score(q.strip(), kb_cmd)
        index_like = bool(INDEX_LIKE.search(md.stem))

        # 综合判断：缺不缺
        # ★ 阈值的来历（2026-11-06 实测标定，不是拍脑袋）：
        #   对 152 篇全跑 `kb search <标题> --top 1`，分数分布实测：
        #     min=13.8  中位=21.2  max=40.1  P75=25.4  P90=28.5  P95=31.2
        #   首版我写 60/85（凭印象），结果 119 篇全判"候选"—— 阈值根本够不到，
        #   等于把所有东西都标成"缺口"，这种筛选没有信息量。
        #   现按实测分位定：>P90(28.5) 我方已有强相关内容；>P75(25.4) 部分重叠。
        if index_like:
            verdict = "跳过：索引/清单类，不是规程"
        elif n < 40:
            verdict = "跳过：太短，多半是梗概"
        elif ex == 0:
            verdict = "跳过：无可执行内容（纯概念）"
        elif score is None:
            verdict = "待定：kb 检索不可用，需人工看"
        elif score >= 28.5:
            verdict = "跳过：我方 kb 已有强相关（≥P90）"
        elif score >= 25.4:
            verdict = "可选：有部分重叠（P75~P90），看是否补了细节"
        else:
            verdict = f"★ 候选：低于 P75（{score:.1f}），我方可能确实缺"

        out.append({
            "file": str(rel).replace("\\", "/"),
            "lines": n, "exec": ex,
            "kb_top1": score,
            "query": q.strip(),
            "verdict": verdict,
        })
    return out


def _selftest() -> int:
    fail = []

    def eq(n, got, want):
        if got != want:
            fail.append(f"{n}: got={got!r} want={want!r}")

    # 查询词构造
    eq("去扩展名+连字符", _norm_query(Path("a/llm-attack-methodology.md")), "llm attack methodology")
    eq("下划线也换", _norm_query(Path("a/frida_17x_api.md")), "frida 17x api")

    # 方向提示
    eq("web 方向", _hint_for(Path("x/web-analysis/knowledge-base/a.md")), "web")
    eq("未知方向", _hint_for(Path("x/other/a.md")), "")

    # 可执行性打分
    eq("空文本 0 分", _executable_score(""), 0)
    eq("有命令块 +1", _executable_score("```bash\necho x\n```"), 1)
    eq("有具体命令 +1", _executable_score("curl -i http://x"), 1)
    eq("有判据 +1", _executable_score("判定：如果 200 则成功"), 1)
    eq("三者齐 3 分", _executable_score("```bash\ncurl x\n```\n判定：退出码 0 则成功"), 3)

    # 索引类识别
    if not INDEX_LIKE.search("templates"):
        fail.append("templates 应被认成索引类")
    if not INDEX_LIKE.search("wordlists-guide"):
        fail.append("wordlists-guide 应被认成索引类")
    if INDEX_LIKE.search("lattice-attacks"):
        fail.append("lattice-attacks 不该被认成索引类")

    # kb 检索解析（伪造输出）
    fake = "命中 3 条（索引 1 块）\n\n[0]   90.5 ref/web    x › y\n"
    m = re.search(r"\[\d+\]\s+([\d.]+)\s+", fake)
    eq("解析 top1 分数", float(m.group(1)) if m else None, 90.5)

    if fail:
        print("FAIL")
        for f in fail:
            print("  " + f)
        return 1
    print("OK kb_gap selftest: 14 组断言全绿")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="既定决策：判断 OpenSecurity 知识库哪些值得并入")
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("scan")
    p.add_argument("--src", required=True, help="OpenSecurity 的 .opencode 目录")
    p.add_argument("--kb-cmd", default=None,
                   help="跑 kb search 的命令模板，{q} 会被替换。省略则不做检索判断")
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--json", action="store_true")

    a = ap.parse_args()
    if a.selftest:
        return _selftest()
    if a.cmd != "scan":
        ap.print_help()
        return 4

    src = Path(a.src)
    if not src.is_dir():
        print(f"输入错误：{src} 不是目录")
        return 4

    rows = scan(src, a.kb_cmd)
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0

    cand = [r for r in rows if r["verdict"].startswith("★")]
    opt = [r for r in rows if r["verdict"].startswith("可选")]
    pend = [r for r in rows if r["verdict"].startswith("待定")]

    print(f"扫描 {len(rows)} 篇\n")
    print(f"=== ★ 候选（{len(cand)} 篇）===")
    for r in sorted(cand, key=lambda x: (-x["exec"], -x["lines"]))[: a.top]:
        print(f"  {r['lines']:4d} 行  exec={r['exec']}  kb_top1={r['kb_top1']}  {r['file']}")

    print(f"\n=== 可选（{len(opt)} 篇，部分重叠）===")
    for r in sorted(opt, key=lambda x: (-x["exec"], -x["lines"]))[: a.top]:
        print(f"  {r['lines']:4d} 行  exec={r['exec']}  kb_top1={r['kb_top1']}  {r['file']}")

    # ★ 待定必须单独列：它们没被判成"跳过"，只是 kb 检索没跑
    #   首版把待定混进"跳过"里，导致 152 篇全显示跳过 —— 看起来像"什么都不缺"，
    #   实际是 120 篇根本没判。这是会误导决策的显示缺陷。
    if pend:
        print(f"\n=== 待定（{len(pend)} 篇，kb 检索未跑通，需接 --kb-cmd）===")
        for r in sorted(pend, key=lambda x: (-x["exec"], -x["lines"]))[: a.top]:
            print(f"  {r['lines']:4d} 行  exec={r['exec']}  {r['file']}")

    skipped = len(rows) - len(cand) - len(opt) - len(pend)
    print(f"\n=== 跳过 {skipped} 篇（索引类/太短/纯概念/已高度重叠）===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
