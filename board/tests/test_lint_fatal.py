#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ruff 致命级门禁（回归锁）：board/*.py 与 tools/*.py 不许有 E9/F63/F7/F82。

为什么需要这个测试（2026-11-17）：
    tools/bin/selfcheck 里早就写了 ruff 致命级检查，但它**从来没真跑过** ——
    tools/venv 是 WSL 里建的 Linux venv，Windows 侧 venv/bin/python 是断链，
    于是永远走「ruff 不可用 → 退回 lint_zero」分支。等真接上 Ruff 才发现 5 处 F821：
      tools/sdkshard.py       : import json 缺失（last_attempt_hint/extract_facts 必 NameError）
      tools/sdkshard.py       : sessdir 在赋值前被用（历史事实注入从来没生效）
      tools/parallel_solver.py: _scores 引用局部变量 chs（ROI 排序静默失效）
    这类「被 except 吞掉的未定义名」不会让任何测试变红，只会让功能安静地不工作 ——
    所以必须有一条**会红的**机器判据。

判据：ruff check board/*.py tools/*.py --select E9,F63,F7,F82 → All checks passed。
没有 ruff 就 rc=77（发布包形态跳过，与其它测试同一约定）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT
FILES = sorted([str(p) for p in (LAB / "board").glob("*.py")] +
               [str(p) for p in (LAB / "tools").glob("*.py")])
FAILED = []


def check(cond, msg):
    if cond:
        print("  PASS " + msg)
    else:
        print("  FAIL " + msg)
        FAILED.append(msg)


def _exists(p) -> bool:
    """★ 2026-11-19：tools/venv/bin/python 是 WSL 里建的 Linux 符号链接，Windows 侧是断链；
    对断链 Path.exists() 在 Windows 上抛 OSError [WinError 1920]，
    会让「宿主上跑测试」崩掉，看起来像产品坏了。当成不存在。"""
    try:
        return p.exists()
    except OSError:
        return False


def find_ruff() -> str | None:
    """优先 venv，其次 PATH —— 与 tools/bin/selfcheck 同一顺序。"""
    venv_py = LAB / "tools" / "venv" / "bin" / "python"
    if _exists(venv_py) and os.access(str(venv_py), os.X_OK):
        r = subprocess.run([str(venv_py), "-m", "ruff", "--version"],
                           capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            return str(venv_py) + " -m ruff"
    if shutil.which("ruff"):
        return "ruff"
    return None


def main() -> int:
    print("== ruff 致命级门禁 ==")
    ru = find_ruff()
    if ru is None:
        print("SKIP 找不到 ruff（发布包形态）")
        return 77
    cmd = ru.split() + ["check"] + FILES + ["--select", "E9,F63,F7,F82",
                                            "--output-format", "concise"]
    r = subprocess.run(cmd, cwd=str(LAB), capture_output=True, text=True, timeout=600)
    out = (r.stdout or "") + (r.stderr or "")
    check("All checks passed" in out,
          "board/*.py tools/*.py 无 E9/F63/F7/F82 级问题（ruff=%s）" % ru)
    if "All checks passed" not in out:
        print("---- ruff 输出（前 20 行）----")
        print("\n".join(out.strip().splitlines()[:20]))
    # 反向对照：这条判据真的能红吗？拿一个含未定义名的临时文件试给 ruff。
    tmp = LAB / "work" / "ruff_selftest.py"
    tmp.parent.mkdir(exist_ok=True)
    tmp.write_text("def f():\n    return undefined_name_xyz\n", encoding="utf-8")
    try:
        cmd2 = ru.split() + ["check", str(tmp), "--select", "F821",
                             "--output-format", "concise"]
        r2 = subprocess.run(cmd2, cwd=str(LAB), capture_output=True, text=True, timeout=120)
        check("F821" in (r2.stdout or ""),
              "反向对照：故意写的未定义名必须被 ruff 抓到 F821")
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass

    print("== %d passed, %d failed ==" % (2 - len(FAILED), len(FAILED)))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
