#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_autostart_loop.py — 守护进程的守护（心跳循环）

为什么
------
实测（§3.1b）：log/supervisor.log 在 14:13:20 → 16:11:47 之间
**118.5 分钟零 tick**（tick=30s）—— 守护自己死了，而 entrypoint 只在容器启动时
调**一次** solver-autostart.sh。孤儿 shard 跑完后无人重启，于是出现 27.1 分钟全场零活动。

判据（必须同时成立，缺一条就等于没修）
  A. 存在常驻循环脚本，且**循环**调 solver-autostart.sh（不是跑一次就退）
  B. 幂等性**不重复实现**：循环自己不判「已在跑」，全交给 solver-autostart.sh
  C. 单次调用有硬超时（防止平台探测卡住把循环挂死）
  D. entrypoint 真的起的是**循环**而不是旧的单次脚本（本条核心接线）
  E. 可关闭：PI_AUTOSTART_LOOP=off → 只跑一次（保留旧行为做对照）
  F. 运行时：起真循环脚本（autostart 用假脚本替身），断言它真的被调了 ≥2 次
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = Path(os.path.dirname(os.path.dirname(HERE)))
LOOP = ROOT / "tools" / "autostart-loop.sh"
AUTO = ROOT / "tools" / "solver-autostart.sh"
ENTRY = ROOT / "docker" / "entrypoint.sh"

AUTOSTART = "solver-autostart.sh"

_passed, _failed = 0, 0


def ck(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print("  x " + name + (("   [" + str(detail)[:300] + "]") if detail else ""))


def main():
    for p in (LOOP, AUTO, ENTRY):
        if not p.exists():
            print("SKIP  %s 不在发布包里" % p.name)
            return 77
    loop = LOOP.read_text(encoding="utf-8")
    # ★ 容器里 docker/entrypoint.sh 是**建镜像时的快照**（不在 bind mount 里），
    #   改宿主源码不会同步；快照比源码旧 → 跳过 D 组，避免"改了宿主却红在容器"的假红。
    #   宿主跑本测试时 ENTRY 就是真源码，D 组即真检查。
    entry = ""
    if ENTRY.exists() and not (Path("/opt/pi-ctf").is_dir()
                               and ENTRY.stat().st_mtime < LOOP.stat().st_mtime - 3600):
        entry = ENTRY.read_text(encoding="utf-8")

    print("A) 常驻循环")
    ck("A1 有 while 真循环", "while :" in loop)
    ck("A2 循环体里调 autostart", AUTOSTART in loop)
    ck("A3 循环体里有 sleep", "sleep" in loop and "INTERVAL" in loop)
    ck("A4 间隔下限保护（>=10s）", "-lt 10" in loop and "INTERVAL=10" in loop)

    print("B) 幂等不重复实现")
    ck("B1 循环里不判 supervise 是否在跑", "running_py_script" not in loop)
    ck("B2 循环里不判停止标记", "supervisor-stop" not in loop)
    ck("B3 循环里不探测平台", "from live" not in loop)
    ck("B4 明写幂等归 autostart 管", "幂等" in loop)

    print("C) 单次硬超时")
    ck("C1 timeout 包住 autostart", "timeout 90 bash" in loop)
    ck("C2 超时只记日志不退出循环", "-eq 124" in loop and "跳过" in loop)

    print("D) entrypoint 接线（本条核心）")
    if not entry:
        print("  （容器内 entrypoint 是镜像快照 → 跳过 D 组；宿主跑即真检查）")
    ck("D1 起的是 autostart-loop.sh", "autostart-loop.sh" in entry or not entry)
    ck("D2 不再直接后台起 autostart（旧行已删）",
       ('setsid nohup bash "$ROOT/tools/' + AUTOSTART + '"') not in entry or not entry)
    ck("D3 记录了为什么改（27.1 / 118.5 分钟）",
       ("27.1" in entry) or ("118.5" in entry) or not entry)

    print("E) 可关闭（保留旧行为对照）")
    ck("E1 支持 PI_AUTOSTART_LOOP", "PI_AUTOSTART_LOOP" in loop)
    ck("E2 off 分支只跑一次并 exit 0", '= "off"' in loop and "exit 0" in loop)

    print("F) 运行时：真跑循环，断言被调 >=2 次")
    if os.name != "posix":
        print("  （非 POSIX 宿主跳过 F 组）")
        print("\nSKIP  %d passed, %d failed" % (_passed, _failed))
        return 77
    tmp = Path(tempfile.mkdtemp(prefix="autoloop-"))
    try:
        (tmp / "log").mkdir(parents=True, exist_ok=True)
        tdir = tmp / "tools"
        tdir.mkdir(parents=True, exist_ok=True)
        cnt = tmp / "count.txt"
        stub = tdir / AUTOSTART
        stub.write_text("#!/bin/bash\necho x >> '" + str(cnt) + "'\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
        real_loop = tdir / "autostart-loop.sh"
        real_loop.write_text(loop, encoding="utf-8")
        real_loop.chmod(0o755)
        env = dict(os.environ, ROOT=str(tmp), AUTOSTART_INTERVAL="10")
        # 用 timeout 命令掐断（而不是 Popen+terminate）→ 被杀进程的 stdout 已刷出，
        # 断言"日志里能看到启动行"才有意义（terminate 时管道内容可能还没读）。
        p = subprocess.run(["timeout", "12", "bash", str(real_loop)], env=env,
                           capture_output=True, text=True, timeout=30)
        out = (p.stdout or "") + (p.stderr or "")
        n = len(cnt.read_text(encoding="utf-8").splitlines()) if cnt.exists() else 0
        ck("F1 12s 内被调 >=2 次（真的在循环）", n >= 2, "calls=%d" % n)
        ck("F2 日志里能看到启动行", "启动" in (out or ""), (out or "")[:200])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n%s  %d passed, %d failed" % ("OK" if _failed == 0 else "FAIL", _passed, _failed))
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
