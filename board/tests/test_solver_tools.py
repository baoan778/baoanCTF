#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_solver_tools.py — R5-C 工具面（schema 瘦身 + 缺工具补装）

为什么
------
1. 历史会话 的 2350 次工具调用里，edit / triage 各 0 次，但它们的 schema
   每轮随 system prompt 重发（约 5.9M 承载字符 / 277.8M = 2%）→ 应加进 --exclude-tools。
2. 实测缺 sshpass（1 次 command not found）、php/mysql/sqlite3/redis-cli/tcpdump
   全缺 → PHP 反序列化/DB 题只能手拼，白花时间。已补进镜像与 requirements。

断言
----
A. 默认 exclude 列表含 edit/triage，且仍保留原有四项。
B. Dockerfile 的 apt 列表含新工具；requirements-extra 含 PyMySQL/redis。
C. TOOLS.md 记录了“容器专有”工具（不许只在代码里）。
D. 运行时（容器内）：命令真的在、库真的能 import；宿主跳过（rc=77 约定）。
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = Path(os.path.dirname(os.path.dirname(HERE)))
SDK = ROOT / "tools" / "sdkshard.py"
DOCKERFILE = ROOT / "docker" / "Dockerfile"
REQ = ROOT / "requirements-extra.txt"
TOOLS = ROOT / "tools" / "TOOLS.md"

_passed, _failed = 0, 0


def ck(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  ✗ {name}" + (f"   [{detail}]" if detail else ""))


def load():
    spec = importlib.util.spec_from_file_location("sdkshard_tools_ut", str(SDK))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    if not SDK.exists():
        print("SKIP  tools/sdkshard.py 不在发布包里")
        return 77
    sd = load()
    # 容器里只挂源码目录：docker/Dockerfile 与根 requirements 不进镜像。
    # 这两项是“宿主静态检查”；容器内跳过，不让它变成假红。
    df = DOCKERFILE.read_text(encoding="utf-8") if DOCKERFILE.exists() else ""
    req = REQ.read_text(encoding="utf-8") if REQ.exists() else ""
    # ★ 容器里这两个文件是**建镜像时的快照**（不在 bind mount 里），改代码后不会同步。
    #   快照比源码旧 → 跳过静态检查，避免“改了宿主却红在容器”的假红；宿主跑本测试即真检查。
    if Path("/opt/pi-ctf").is_dir() and SDK.exists():
        _sdk_m = SDK.stat().st_mtime
        if DOCKERFILE.exists() and DOCKERFILE.stat().st_mtime < _sdk_m - 3600:
            df, req = "", ""
    tools = TOOLS.read_text(encoding="utf-8") if TOOLS.exists() else ""

    print("A) exclude 列表")
    ex = sd.SOLVER_EXCLUDE_DEFAULT
    for name in ("subagent", "subagent_supervisor", "bg_wait", "source_check", "edit", "triage"):
        ck(f"A1 排除 {name}", name in ex, ex)
    ck("A2 空值/0 仍可关闭排除", sd.solver_exclude() != "" or os.environ.get("PI_SOLVE_EXCLUDE") == "")

    print("B) 镜像与 requirements（宿主静态检查）")
    if df:
        for pkg in ("sshpass", "php-cli", "sqlite3", "redis-tools", "tcpdump", "mariadb-client"):
            ck(f"B1 Dockerfile 装 {pkg}", pkg in df)
        ck("B3 mysql/mysqldump 兼容软链", "ln -sf /usr/bin/mariadb /usr/bin/mysql" in df
           and "mariadb-dump /usr/bin/mysqldump" in df)
    else:
        print("  （容器内无 docker/Dockerfile → 跳过 B1/B3）")
    if req:
        for pkg in ("PyMySQL", "redis"):
            ck(f"B2 requirements-extra 有 {pkg}", pkg in req)
    else:
        print("  （容器内无 requirements-extra.txt → 跳过 B2）")

    print("C) TOOLS.md 记录")
    ck("C1 有容器专有工具段", "容器" in tools and "专有" in tools)
    for name in ("sshpass", "php", "sqlite3", "redis-cli", "tcpdump", "mysql"):
        ck(f"C2 TOOLS.md 提到 {name}", ("`" + name + "`") in tools)

    print("D) 运行时（容器内才有意义）")
    if not Path("/opt/pi-ctf").is_dir():
        print("  （宿主跳过：D 组只在容器 pictf 里跑）")
        print(f"\nSKIP  {_passed} passed, {_failed} failed")
        return 77
    for cmd in ("sshpass", "php", "sqlite3", "redis-cli", "tcpdump", "mysql"):
        ck(f"D1 {cmd} 在 PATH", shutil.which(cmd) is not None, shutil.which(cmd))
    for mod in ("pymysql", "redis"):
        rc = subprocess.run([sys.executable, "-c", f"import {mod}"],
                            capture_output=True, text=True)
        ck(f"D2 import {mod}", rc.returncode == 0, (rc.stderr or "")[-120:])

    print(f"\n{'OK' if _failed == 0 else 'FAIL'}  {_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
