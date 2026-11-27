#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""watchdog.py — 该轮 该缺陷：守护进程死了要有人管

## 它解决什么

实测事故（某轮复盘 3.1b）：supervisor.log 在 14:13:20 之后 **118.5 分钟零 tick**，
孤儿跑完到 15:45，之后 **全场 27.1 分钟零活动**，靠人肉 16:11:47 重启。

根因不是 supervise 逻辑写错，而是**没有任何东西检查 supervise 自己还活着**。
supervise 只监控子进程（sdkshard / parallel_solver），它一旦被 OOM / 误杀 /
容器重启打死，就没有第二个进程知道该拉起它。
## 判据（两条同时成立才算死，避免误杀）

1. 心跳文件 `.supervisor-heartbeat` 超过 HEARTBEAT_STALE_S（默认 3 tick = 90s）没更新；
2. 用 procscan 确认**真的没有** supervise 进程在跑（防止心跳写盘失败但进程活着）。

为什么必须两条：心跳可能因磁盘/权限写不进去，单看文件会误判成死亡并重复拉起。
为什么用 3 tick 而不是 1 tick：单跳卡在平台慢查询上不该被当成死亡。

## 它**不**做什么（刻意的边界）

- 不杀任何进程（拉起前只确认没有 supervise 在跑）；
- 不碰 parallel_solver / shard / pi（那是 supervise 的职责）；
- 有停止标记 `.supervisor-stop` 时**什么都不做**（尊重停机）。

## 用法

    python3 tools/watchdog.py --once        # 检查一次（给人看 / CI）
    python3 tools/watchdog.py               # 常驻，每 30s 检查
    python3 tools/watchdog.py --dry-run     # 只报告不拉起
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

SOLVE = ROOT / "work" / "solve"
HEARTBEAT = SOLVE / ".supervisor-heartbeat"
STOP_MARK = SOLVE / ".supervisor-stop"
LOG = ROOT / "log" / "watchdog.log"


def _stale_s() -> float:
    """心跳多久算过期。默认 90s（=3 tick），可用 PI_SUP_STALE_S 覆盖。"""
    try:
        v = float(os.environ.get("PI_SUP_STALE_S", "") or 0)
        return v if v > 0 else 90.0
    except (TypeError, ValueError):
        return 90.0


def log(msg: str) -> None:
    line = "%s %s" % (time.strftime("%F %T"), msg)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    print(line, flush=True)


def heartbeat_age() -> float:
    """心跳距今秒数；不存在返回 inf。"""
    try:
        raw = HEARTBEAT.read_text(encoding="utf-8").split()
        return max(0.0, time.time() - float(raw[0]))
    except (OSError, ValueError, IndexError):
        return float("inf")


def supervisor_pids() -> list:
    """真的在跑的 supervise 进程（用 procscan 的语义识别，不按子串匹配）。"""
    try:
        import procscan  # noqa: PLC0415
        return procscan.python_script_pids("supervise.py")
    except Exception:  # noqa: BLE001
        return []


def check(dry_run: bool = False, spawn: bool = True) -> str:
    """返回动作名：stopped / alive / dead-and-restarted / dead-no-spawn / no-heartbeat。"""
    if STOP_MARK.is_file():
        return "stopped"          # 尊重停机，绝不擅自拉起

    age = heartbeat_age()
    stale = _stale_s()
    pids = supervisor_pids()

    if pids and age <= stale:
        return "alive"

    if pids and age > stale:
        # 进程在但心跳停了 = tick 卡死（不是死亡）。不杀（可能是长任务），只告警。
        log("⚠ 心跳已 %.0fs 未更新但 supervise 仍在跑（pid=%s）→ 判定卡顿，不重启" % (age, pids))
        return "alive"

    # 到这里：没有 supervise 进程
    # ★ 修正（自测发现）：心跳新鲜但没有 supervise 进程，可能是
    #   a) supervisor 刚退出（心跳还没来得及过期），或
    #   b) 心跳是别的实例留下的。这两种都**不该**立刻判死。
    #   判据：心跳必须在**阈值内且确实没有进程**时才进入判定；新鲜 = 视为正常（给人观察窗口）。
    if pids == [] and age <= stale:
        # 心跳很新却没进程：极可能是刚退出/刚重启的过渡态，给一个 tick 的观察窗口
        return "grace"
    if age == float("inf"):
        log("心跳文件不存在且无 supervise 进程 → 视为从未启动，%s" %
            ("跳过（dry-run）" if dry_run else "拉起"))
        return "no-heartbeat" if dry_run else ("dead-no-spawn" if not spawn or dry_run else _spawn())

    log("心跳已 %.0fs 未更新（阈值 %.0fs）且无 supervise 进程 → 判定守护已死%s" %
        (age, stale, "（dry-run 不拉起）" if dry_run else "，拉起"))
    if dry_run:
        return "dead-no-spawn"
    return _spawn()


def _spawn() -> str:
    """在后台拉起 supervise.py（与 solver-autostart.sh 同一命令形态）。"""
    py = sys.executable or "python3"
    script = str(ROOT / "tools" / "supervise.py")
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with (LOG.parent / "supervisor.log").open("a", encoding="utf-8") as out:
            # start_new_session：脱离本进程组，watchdog 自己被杀也不带走它
            subprocess.Popen([py, script], cwd=str(ROOT), stdout=out,
                             stderr=subprocess.STDOUT, start_new_session=True)
        log("已拉起 supervise.py（心跳应在 %ds 内恢复）" % int(_stale_s()))
        return "dead-and-restarted"
    except OSError as exc:
        log("拉起失败：%s" % exc)
        return "dead-no-spawn"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--interval", type=int, default=30)
    a = ap.parse_args()

    if a.once or a.dry_run:
        action = check(dry_run=a.dry_run)
        print("watchdog: %s（心跳 %.0fs，阈值 %.0fs，supervise pids=%s）" %
              (action, heartbeat_age(), _stale_s(), supervisor_pids()))
        return 0

    log("watchdog 启动 pid=%d interval=%ds stale=%.0fs" %
        (os.getpid(), a.interval, _stale_s()))
    while True:
        act = check()
        if act.startswith("dead-and"):
            log("动作 = %s" % act)
        time.sleep(max(10, a.interval))


if __name__ == "__main__":
    sys.exit(main())