#!/usr/bin/env python3
"""孤儿 pi 回收的回归测试 —— 治「停机后无头 pi 继续烧钱」。

血账（2026-11-20 实测，不是推测）
----------------------------------
17:11:08 写到停止标记并 kill 掉 parallel_solver + 3 个 sdkshard，
**以为停机了**。实际 tools/sdkshard.py:988 用 `start_new_session=True` 拉起 pi，
pi 在独立进程组里，父亲一死立刻被 init 收养（ppid=1）继续跑：

  · xben-066 停机后又发 16 次请求 $0.181（17:11:31 → 17:24:24）
  · xben-092 停机后又发 18 次请求 $0.269（17:11:11 → 17:24:26）
  · xben-081 停机后又发  3 次请求 $0.065（17:15:48 → 17:19:54）
  合计 37 次 / $0.515，全部零产出（平台 run 已终结，交了旗也不算分）。

本测试守住四件事（全离线；用假 /proc，**不碰真进程**）：
  ① 默认只认「ppid==1 且无头」的 pi；
  ② `--all` 连「还有活父亲」的无头 pi 一起认（停机用）；
  ③ WebUI 常驻 pi（无 -p）与僵尸态永不入列，D 态不读 cmdline（不阻塞）；
  ④ 静态守卫：supervise --stop 必须真杀（不能只写标记）、巡检时必须用 orphans_only。

    python3 board/tests/test_reap_orphan.py
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _pkgpaths import skip  # noqa: E402

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = pathlib.Path(__file__).resolve().parents[2]
REAPER = LAB / "tools" / "reap_orphan_agents.py"
SUP = LAB / "tools" / "supervise.py"

checks: list = []


def ok(name, cond, extra=""):
    checks.append((name, bool(cond), str(extra)))


def _report():
    bad = [c for c in checks if not c[1]]
    for name, good, extra in checks:
        print(f"  {'✓' if good else '✗'} {name}" + (f"   {extra[:200]}" if extra and not good else ""))
    print(f"\n通过 {len(checks) - len(bad)} / 失败 {len(bad)}")
    if bad:
        print("失败项：" + "、".join(c[0] for c in bad))
        return 1
    print("✓ 孤儿回收（reap_orphan_agents）：全部通过")
    return 0


def _mk_proc(root: pathlib.Path, pid: int, comm: str, state: str, ppid: int,
             argv: list):
    """造一个假 /proc/<pid>/{comm,stat,cmdline}。"""
    d = root / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "comm").write_text(comm + "\n", encoding="utf-8")
    # stat: pid (comm) state ppid ... ; starttime 是 ')' 之后第 20 个字段
    rest = [state, str(ppid)] + ["0"] * 18
    (d / "stat").write_text(f"{pid} ({comm}) " + " ".join(rest) + "\n", encoding="utf-8")
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")


def _run(args, root: pathlib.Path):
    env = dict(os.environ)
    env["PI_LAB_PROC_ROOT"] = str(root)
    # Windows 下子进程 stdout 走 cp936，本文件按 utf-8 解 → 中文全变乱码，
    # 断言 "没有" 就永远不成立。强制子进程也用 utf-8。
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(  # noqa: S603
        [sys.executable, str(REAPER), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, env=env)


def main() -> int:
    ok("tools/reap_orphan_agents.py 存在", REAPER.is_file())
    ok("tools/supervise.py 存在", SUP.is_file())
    if not REAPER.is_file():
        return _report()

    if not pathlib.Path("/proc/self").exists() and os.name == "nt":
        # 只是"扫不到东西"的兜底 —— 本测试全程用假 /proc，不依赖真 procfs
        pass

    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td) / "proc"
        root.mkdir()
        # ① 孤儿无头 pi（父已死）
        _mk_proc(root, 101, "pi", "S", 1, ["pi", "-p", "solve x", "--session-dir", "/tmp/s"])
        _mk_proc(root, 102, "pi", "S", 1, ["pi", "-p", "solve y"])
        # ② 有活父亲的无头 pi（正常干活，巡检不能碰）
        _mk_proc(root, 201, "pi", "S", 900, ["pi", "-p", "solve z"])
        # ③ WebUI 常驻 pi（无 -p）—— 永不动
        _mk_proc(root, 301, "pi", "S", 900, ["pi", "--ui"])
        _mk_proc(root, 302, "pi", "S", 1, ["pi"])            # 孤儿但交互式 → 也不动
        # ④ 僵尸 pi（ppid=1）—— 已死，不该再杀
        _mk_proc(root, 401, "pi", "Z", 1, ["pi", "-p", "dead"])
        # ⑤ D 态 pi：cmdline 里故意写"能证明读过它"的内容，读不到就不该被判为无头
        _mk_proc(root, 501, "pi", "D", 1, ["pi", "-p", "blocked"])
        # ⑥ 非 pi 进程
        _mk_proc(root, 601, "python3", "S", 1, ["python3", "sdkshard.py"])

        r = _run(["--json"], root)
        got = {}
        try:
            got = json.loads(r.stdout.strip() or "{}")
        except Exception:
            got = {}
        pids = sorted(p["pid"] for p in got.get("procs", []))
        ok("默认只认 ppid==1 的无头 pi（101/102）", pids == [101, 102], f"{pids} rc={r.returncode} {r.stderr[-200:]}")

        r2 = _run(["--json", "--all"], root)
        got2 = {}
        try:
            got2 = json.loads(r2.stdout.strip() or "{}")
        except Exception:
            got2 = {}
        pids2 = sorted(p["pid"] for p in got2.get("procs", []))
        ok("--all 加上有活父亲的 201（停机要一次清干净）", pids2 == [101, 102, 201], f"{pids2}")

        ok("WebUI 常驻 pi（无 -p）永不入列", 301 not in pids2 and 302 not in pids2, f"{pids2}")
        ok("僵尸 pi 不入列", 401 not in pids2, f"{pids2}")
        ok("D 态 pi 不入列（不读 cmdline，不会阻塞）", 501 not in pids2, f"{pids2}")
        ok("非 pi 进程不入列", 601 not in pids2, f"{pids2}")

        # dry-run 不杀
        r3 = _run(["--dry-run"], root)
        ok("--dry-run 退出码 0", r3.returncode == 0, r3.stderr[-200:])
        ok("--dry-run 列出 2 个（不误杀）", r3.stdout.count("[dry]") == 2, r3.stdout[-300:])

        dr = _run(["--dry-run", "--all"], root)
        ok("--dry-run --all 列出 3 个", dr.stdout.count("[dry]") == 3, dr.stdout[-300:])

    # 空 /proc：不该炸
    with tempfile.TemporaryDirectory() as td2:
        empty = pathlib.Path(td2) / "proc"
        empty.mkdir()
        re_ = _run([], empty)
        ok("空 procfs 不炸且退出码 0", re_.returncode == 0 and "没有" in re_.stdout, re_.stdout[-200:])

    # ── ④ 静态守卫（这是本次 P0 的"不许退回去"清单）────────────────
    reaper_src = REAPER.read_text(encoding="utf-8", errors="replace")
    sup_src = SUP.read_text(encoding="utf-8", errors="replace") if SUP.is_file() else ""

    ok("reaper 读 cmdline 前先看状态", 'in ("Z", "D", "X", "x")' in reaper_src)
    ok("reaper 支持 procfs 根覆盖（可测性）", "PI_LAB_PROC_ROOT" in reaper_src)
    ok("reaper 认 -p/--print 判无头", 'b"-p" in argv' in reaper_src and 'b"--print" in argv' in reaper_src)

    ok("supervise --stop 真杀总控（不只是写标记）",
       "parallel_solver.py" in sup_src and "kill_tree(pid)" in sup_src, "")
    ok("supervise --stop 也停 shard", '"sdkshard.py"' in sup_src)
    ok("supervise --stop 收无头 pi（含活父亲）",
       "reap_orphans(include_live=True)" in sup_src)
    ok("supervise 巡检只收孤儿（不碰正在干活的 pi）",
       "reap_orphans(include_live=False)" in sup_src)
    ok("supervise 保留 --keep-children 逃生通道", "--keep-children" in sup_src)

    return _report()


if __name__ == "__main__":
    sys.exit(main())
