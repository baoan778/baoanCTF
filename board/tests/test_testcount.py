#!/usr/bin/env python3
"""test_testcount.py —— 守「测试数量的单一可写点」（4b 闸门的防假红装置）。

由来（2026-11-07 实录）
----------------------
本轮加 5 条测试后 `selfcheck` 报红：「文档写 61 条、现场是 66 条」。
闸门**抓得对**（它抓到过真撒谎），但落地方式错：数字散在两处（自检命令注释
与回归现场行），每加一个文件都要人手改，改一处漏一处 —— 闸门从「防撒谎」
退化成「制造假红」。

修法：数字只有**一个可写点**，由 `tools/testcount.py` 机器算。
本测试守四件事：
  ① 一致时 rc=0；② 不一致时 rc=1 且**指出修法**；
  ③ `--sync` 能把**两处锚点**一次改对（漏一处等于没改）；
  ④ 找不到锚点时**报错不静默**（宁可红，不可假绿）。

全程在临时目录副本上跑，**不碰真 docs/**。

    python3 board/tests/test_testcount.py
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys as _sys
import tempfile

try:
    for _s in (_sys.stdout, _sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = pathlib.Path(__file__).resolve().parents[2]
TOOL = LAB / "tools" / "testcount.py"
# ★ 2026-11-26：锚点文件从 docs/状态快照.md 改为 README.md（状态快照不再随开源分发）。
DOC = LAB / "README.md"

checks: list = []


def ok(name, cond, extra=""):
    checks.append((name, bool(cond), str(extra)))


def _report():
    bad = [c for c in checks if not c[1]]
    for name, good, extra in checks:
        mark = "✓" if good else "✗"
        print(f"  {mark} {name}" + (f"   {extra[:240]}" if extra and not good else ""))
    print(f"\n通过 {len(checks) - len(bad)} / 失败 {len(bad)}")
    if bad:
        print("失败项：" + "、".join(c[0] for c in bad))
        return 1
    print("✓ 测试数量单一可写点（testcount）：全部通过")
    return 0


def run(root: pathlib.Path, *args: str):
    return subprocess.run(  # noqa: S603
        [_sys.executable, str(root / "tools" / "testcount.py"), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        cwd=str(root),
    )


def fixture(root: pathlib.Path, n_tests: int, doc_n: int | None):
    """造一个最小仓库：tools/testcount.py + README.md + N 个测试文件。"""
    (root / "tools").mkdir(parents=True, exist_ok=True)
    # ★ 每次 fixture 都要**清空**测试目录：否则上一轮的 test_x08.py 还在，
    #   live 计数会带着旧文件（本测试第一版就这么踩了，[json] live=9 应为 3）。
    tdir = root / "board" / "tests"
    if tdir.is_dir():
        for old in tdir.glob("test_*.py"):
            old.unlink()
    tdir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(TOOL, root / "tools" / "testcount.py")
    for i in range(n_tests):
        (root / "board" / "tests" / f"test_x{i:02d}.py").write_text("# stub\n", encoding="utf-8")
    if doc_n is None:
        body = "# README\n\n> 这里没有任何计数锚点\n"
    else:
        body = ("# README\n\n| 回归测试 | **" + str(doc_n) + " 条**（CI 双平台全绿） |\n")
    (root / "README.md").write_text(body, encoding="utf-8")


def main() -> int:
    ok("tools/testcount.py 存在", TOOL.is_file())
    ok("README.md 存在", DOC.is_file())
    if not TOOL.is_file():
        return _report()

    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)

        # ① 一致 → rc=0
        fixture(root, 7, 7)
        p = run(root, "--check")
        ok("[一致] rc=0", p.returncode == 0, p.stdout + p.stderr)
        ok("[一致] 输出说得清", "一致" in p.stdout, p.stdout)

        # ② 不一致 → rc=1 且给修法
        fixture(root, 9, 7)
        p = run(root, "--check")
        ok("[不一致] rc=1", p.returncode == 1, str(p.returncode))
        ok("[不一致] 指出修法", "--sync" in p.stdout, p.stdout)
        ok("[不一致] 报出两个数", "7" in p.stdout and "9" in p.stdout, p.stdout)

        # ③ --sync 一次改对**两处**
        p = run(root, "--sync")
        ok("[sync] rc=0", p.returncode == 0, p.stdout + p.stderr)
        body = (root / "README.md").read_text(encoding="utf-8")
        ok("[sync] README 锚点已改", "**9 条**" in body, body[:200])
        ok("[sync] 旧的 7 不再出现", "**7 条**" not in body, body[:200])
        p = run(root, "--check")
        ok("[sync 后] check 转绿", p.returncode == 0, p.stdout)

        # ④ 无锚点 → 不静默成功
        fixture(root, 5, None)
        p = run(root, "--check")
        ok("[无锚点] check 给 WARN 不假绿", "WARN" in p.stdout, p.stdout)
        p2 = run(root, "--sync")
        ok("[无锚点] sync 拒绝并 rc=2（不瞎写）", p2.returncode == 2, p2.stdout + str(p2.returncode))

        # ⑥ --json 形态
        fixture(root, 3, 3)
        p = run(root, "--json")
        try:
            j = json.loads(p.stdout)
        except Exception:
            j = {}
        ok("[json] live 正确", j.get("live") == 3, str(j))
        ok("[json] doc 正确", j.get("doc") == 3, str(j))
        ok("[json] ok=true", j.get("ok") is True, str(j))

    return _report()


if __name__ == "__main__":
    raise SystemExit(main())
