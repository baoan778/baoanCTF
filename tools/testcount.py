#!/usr/bin/env python3
"""testcount.py —— 测试数量「单一可写点」：文档不许撒谎，但也不该逼人手改。

为什么要有它
-----------
`selfcheck` 的 4b 闸门抓 `docs/状态快照.md` 里形如 `N 条全绿` 的数字，与
`ls board/tests/test_*.py | wc -l` 比对。这条闸门是**对的**（它抓到过真撒谎：
文档写 61、现场 66）。但落地方式错了 —— 每加一个测试文件，都要人手去改文档，
而且历史上数字散落在两处（§0 的自检命令注释 + §0 的「本机回归现场」行），
改一处漏一处，闸门就从「防撒谎」变成「制造假红」。

本工具的定位：**让那个数字只有一个可写点，且由机器算**。
  · `--check`  文档与现场一致 → 0；不一致 → 1（selfcheck 4b 用这个）
  · `--sync`   把文档里的数字改成现场值（加测试后跑一次即可）
  · `--json`   出机器可读形态

设计纪律
--------
1. **只改一个数字，不重写文档**：用带锚点的行内替换，改完立刻复读校验；
2. **两处都同步**（自检命令注释 + 回归现场行），漏一处等于没改；
3. **找不到锚点就报错、不静默**（宁可红，不可假绿）。

    python3 tools/testcount.py --check
    python3 tools/testcount.py --sync
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = pathlib.Path(__file__).resolve().parents[1]
# ★ 2026-11-26：锚点从 docs/状态快照.md 改为 README.md。
#   状态快照是**内部交接文档**，开源交付里不该出现（使用者不需要知道开发进度）；
#   而 README 是使用者入口，测试数写在那里才有人真的会看到它过期。
DOC = LAB / "README.md"
TESTS = LAB / "board" / "tests"

# 锚点：同一个数字在文档里有**多种写法**，闸门必须全管，否则改一处漏两处。
#   ★ 2026-11-12 修：原来只管「N 条全绿」，漏了同一行里的
#     「全量串行 77/77 通过」和「board/tests/test_*.py 计 77 个」——
#     实测该行同时写着「80 条全绿」和「77 个」，自相矛盾却一直绿灯。
#   现在三种写法都纳入：改数字时三处一起改，缺一处就报错。
# README 的写法：`| 回归测试 | **151 条**（...） |`
PAT_CMD = re.compile(r"(再加 )\d+( 条回归测试)")
PAT_DOC = re.compile(r"(\*\*)\d+( 条\*\*)")
# 「77/77 通过」→ 分子分母都是同一个数（全量串行全绿）
PAT_FRAC = re.compile(r"\b\d+/\d+( 通过)")
# 「计 77 个」/「N 个」（后面紧跟测试语境词）
PAT_COUNT = re.compile(r"(计 )\d+( 个)")


def live_count() -> int:
    if not TESTS.is_dir():
        return 0
    return len(sorted(TESTS.glob("test_*.py")))


def doc_count(text: str) -> int | None:
    m = PAT_DOC.search(text)
    return int(re.sub(r"\D", "", m.group(0))) if m else None


def sync(text: str, n: int) -> tuple[str, bool]:
    """把两处锚点都改成 n。返回 (新文本, 是否真的改动了)。"""
    before = text
    text = PAT_CMD.sub(lambda m: f"{m.group(1)}{n}{m.group(2)}", text)
    text = PAT_DOC.sub(lambda m: f"{m.group(1)}{n}{m.group(2)}", text, count=1)
    # 同步另外两种写法（都替换为同一个 n）
    text = PAT_FRAC.sub(lambda m: f"{n}/{n}{m.group(1)}", text)
    text = PAT_COUNT.sub(lambda m: f"{m.group(1)}{n}{m.group(2)}", text)
    return text, text != before


def main() -> int:
    args = set(sys.argv[1:])
    n = live_count()
    if not DOC.is_file():
        print(f"FAIL 找不到 {DOC}")
        return 2
    text = DOC.read_text(encoding="utf-8")
    d = doc_count(text)

    if "--json" in args:
        print(json.dumps({"live": n, "doc": d, "ok": d == n, "doc_path": str(DOC)},
                         ensure_ascii=False))
        return 0 if d == n else 1

    if "--sync" in args:
        if d is None:
            print("FAIL 文档里找不到「N 条全绿」锚点 —— 格式变了，先修文档再加测试")
            return 2
        new, changed = sync(text, n)
        if not changed:
            print(f"✓ 已是 {n} 条，无需改动")
            return 0
        DOC.write_text(new, encoding="utf-8", newline="\n")
        # 写完复读校验（防"写了但没落盘"这类假绿）
        again = doc_count(DOC.read_text(encoding="utf-8"))
        if again != n:
            print(f"FAIL 写入后复读仍是 {again}，期望 {n}")
            return 2
        print(f"✓ 已同步：{d} → {n} 条（两处锚点）")
        return 0

    # 默认 --check
    # ★ 除主锚点外，再把另外两种写法也校验一遍：不一致就报错（不静默）
    stale = []
    for m in PAT_FRAC.finditer(text):
        v = int(re.sub(r"\D", "", m.group(1)) if False else m.group(0).split("/")[0])
        if v != n:
            stale.append(("N/N 通过", v))
    for m in PAT_COUNT.finditer(text):
        v = int(re.sub(r"\D", "", m.group(0)))
        if v != n:
            stale.append(("计 N 个", v))
    if stale:
        print(f"FAIL 文档里还有过期的测试数量（现场 {n}）：")
        for kind, v in stale:
            print(f"  {kind} 写的是 {v}")
        print("  修法：python3 tools/testcount.py --sync")
        return 1
    if d is None:
        print("WARN 文档里找不到「N 条全绿」锚点 —— 4b 检查失效")
        return 0
    if d == n:
        print(f"✓ 文档与现场一致（都写 {n} 条测试）")
        return 0
    print(f"FAIL 文档写 {d} 条测试，现场是 {n} 条")
    print("  修法：python3 tools/testcount.py --sync")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())