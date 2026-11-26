#!/usr/bin/env python3
"""lane 看门狗（仓库级通用工具，2026-10-01 从竞赛工作区提升上来） —— 把「长尾 lane 白烧 token / 不落盘 / 卡死」变成机器可判。

为什么需要（2026-10-01 0xGame 实盘血账，全部是编排者自己的失误）：
  · id=34 第一条 lane：**45 分钟 / 31.8 万 token，全程不落盘 → 超时后零产物**（白烧）；
  · id=14 第一条 lane：**69 轮 / 54 万 token** 仍在枚举 BV 号，我 steer 两次都没被消费；
  · 多条 lane「看着不动」其实是卡在 4 分钟的长命令/交互式 nc 上（`nc` 永远不退）；
  · 我不会被"跑完了"通知叫醒之前，**根本不知道 lane 是否在动 / 是否烧过头**。

判据（全部来自 run 目录的 status.json + 日志 mtime，不猜）：
  STUCK       running 且日志 > `--stuck-min` 分钟没写 → 可能卡在长命令/交互
  OVER_BOX    运行时长 > `--timebox-min` 分钟 → 该收尾（双阶段：让它只写结论）
  NEAR_DL     距 deadline < `--near-deadline-min` 分钟 → 立刻催它收尾，否则产物会丢
  NO_NOTES    running 且工作区 `notes/` 自该 lane 启动后没有任何写入 → 没落盘（超时就白干）
  ORPHAN      state=running 但 pid 已不存在 → 僵尸/残留

用法（任何多 lane 场景都能用：比赛/多目标/swarm）：
    python3 tools/lane_guard.py                 # 看一次（人读）
    python3 lane_guard.py --check         # 有任何 flag 就退出码 1（挂进检查点）
    python3 lane_guard.py --stuck-min 4 --timebox-min 40
    python3 lane_guard.py --runs-dir /tmp/pi-subagents-uid-0/async-subagent-runs
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
#   自包含（自己 import sys），不依赖本文件是否已 import sys 或用别名。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_RUNS = "/tmp/pi-subagents-uid-0/async-subagent-runs"


def pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    except Exception:
        return True
    return True


def notes_mtime(ws: pathlib.Path):
    d = ws / "notes"
    if not d.is_dir():
        return None
    newest = 0.0
    try:
        for p in d.glob("*.md"):
            newest = max(newest, p.stat().st_mtime)
    except OSError:
        return None
    return newest or None


def scan(runs_dir: pathlib.Path, stuck_min: float, timebox_min: float,
         near_dl_min: float, ws: pathlib.Path) -> list:
    now = time.time()
    rows = []
    if not runs_dir.is_dir():
        return rows
    for d in sorted(runs_dir.iterdir(), key=lambda p: -p.stat().st_mtime):
        sp = d / "status.json"
        if not sp.exists():
            continue
        try:
            st = json.loads(sp.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        state = st.get("state", "?")
        started = (st.get("startedAt") or 0) / 1000.0
        last_act = (st.get("lastActivityAt") or st.get("lastUpdate") or 0) / 1000.0
        deadline = (st.get("deadlineAt") or 0) / 1000.0
        pid = st.get("pid") or 0
        logs = sorted(d.glob("output-*.log"))
        log_m = max((p.stat().st_mtime for p in logs), default=0.0)
        log_sz = sum(p.stat().st_size for p in logs)
        idle_min = (now - max(log_m, last_act)) / 60.0 if (log_m or last_act) else None
        age_min = (now - started) / 60.0 if started else None
        flags = []
        if state in ("running", "active"):
            if idle_min is not None and idle_min > stuck_min:
                flags.append(f"STUCK({idle_min:.0f}m)")
            if age_min is not None and age_min > timebox_min:
                flags.append(f"OVER_BOX({age_min:.0f}m)")
            if deadline and (deadline - now) / 60.0 < near_dl_min:
                flags.append(f"NEAR_DL({(deadline - now) / 60.0:.0f}m)")
            nm = notes_mtime(ws)
            # notes 为空 或 最新写入早于本 lane 启动 → 说明这条 lane 一直没落盘（超时就白干）
            if nm is None or nm < started:
                flags.append("NO_NOTES")
            if pid and not pid_alive(int(pid)):
                flags.append("ORPHAN")
        rows.append({
            "run": d.name, "state": state, "age_min": age_min, "idle_min": idle_min,
            "log_mb": log_sz / 1e6, "flags": flags, "dir": str(d),
        })
    return rows


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-dir", default=os.environ.get("PI_LAB_LANE_RUNS", DEFAULT_RUNS))
    ap.add_argument("--workspace", default=str(pathlib.Path.cwd()))
    ap.add_argument("--stuck-min", type=float, default=4.0)
    ap.add_argument("--timebox-min", type=float, default=40.0)
    ap.add_argument("--near-deadline-min", type=float, default=5.0)
    ap.add_argument("--check", action="store_true", help="有 flag 就退出码 1")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    rows = scan(pathlib.Path(args.runs_dir), args.stuck_min, args.timebox_min,
                args.near_deadline_min, pathlib.Path(args.workspace))
    live = [r for r in rows if r["state"] in ("running", "active")]
    print("=== lane 看门狗 ===")
    print(f"运行中 {len(live)} 条 / 共 {len(rows)} 个 run（判据：stuck>{args.stuck_min:g}m · "
          f"timebox>{args.timebox_min:g}m · deadline<{args.near_deadline_min:g}m）")
    for r in live:
        age = f"{r['age_min']:.0f}m" if r["age_min"] is not None else "?"
        idle = f"{r['idle_min']:.1f}m" if r["idle_min"] is not None else "?"
        tag = ("  ⚠ " + " ".join(r["flags"])) if r["flags"] else ""
        print(f"  {r['run'][:8]}  {age:>5} 龄  日志静默 {idle:>6}  {r['log_mb']:.2f}MB{tag}")
    flagged = [r for r in rows if r["flags"]]
    if flagged:
        print(f"\n⚠ {len(flagged)} 条需要处置（照抄命令即可）:")
        for r in flagged:
            print(f"  · {r['run'][:8]} [{' '.join(r['flags'])}]")
            if any(f.startswith(("STUCK", "NO_NOTES")) for f in r["flags"]):
                print(f"      steer 收尾: subagent({{action:'steer', id:'{r['run'][:8]}', "
                      f"mode:'follow_up', message:'立刻把已确认结论写进 notes/，并回一行卡在哪；不要再开新路线'}})")
            if any(f.startswith(("OVER_BOX", "NEAR_DL")) for f in r["flags"]):
                print(f"      停: subagent({{action:'stop', id:'{r['run'][:8]}}}"
                      f"   # 或让它只写结论（双阶段收尾）")
            if "ORPHAN" in " ".join(r["flags"]):
                print("      残留进程已不在 → 可直接 fgs step-reclaim / 忽略该 run")
    else:
        print("\n✓ 无告警：没有卡死/超时/不落盘的 lane")
    return 1 if (flagged and args.check) else 0


def _selftest() -> int:
    import shutil
    import tempfile
    checks = []

    def ok(n, c, extra=""):
        checks.append((n, bool(c), extra))

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="laneguard-"))
    runs = tmp / "runs"
    ws = tmp / "ws"
    (ws / "notes").mkdir(parents=True)
    now_ms = int(time.time() * 1000)

    def mk(name, state, age_min, idle_min, pid=0, notes_age_min=0.0, notes=False):
        d = runs / name
        d.mkdir(parents=True)
        started = now_ms - int(age_min * 60000)
        (d / "status.json").write_text(json.dumps({
            "state": state, "startedAt": started, "lastActivityAt": now_ms - int(idle_min * 60000),
            "deadlineAt": now_ms + 600000, "pid": pid}), encoding="utf-8")
        f = d / "output-0.log"
        f.write_text("x" * 1000, encoding="utf-8")
        t = (now_ms - int(idle_min * 60000)) / 1000.0
        os.utime(f, (t, t))
        if notes:
            nf = ws / "notes" / f"{name[:8]}.md"
            nf.write_text("progress", encoding="utf-8")
            nt = time.time() - notes_age_min * 60
            os.utime(nf, (nt, nt))

    try:
        mk("aaaaaaaa-0000", "running", 5, 0.2, notes=True)                    # 正常（有落盘）
        mk("bbbbbbbb-0000", "running", 5, 9, notes=True)                      # STUCK
        mk("cccccccc-0000", "running", 70, 0.2, notes=True)                   # OVER_BOX
        mk("dddddddd-0000", "completed", 5, 0.2, notes=True)                  # 已完成 → 不算
        mk("ffffffff-0000", "running", 30, 0.2, notes=True, notes_age_min=45)  # 落盘早于启动 → NO_NOTES
        rows = scan(runs, 4.0, 40.0, 5.0, ws)
        by = {r["run"][:8]: r for r in rows}
        ok("识别运行中 lane 数（4 条 running）", sum(1 for r in rows if r["state"] == "running") == 4)
        ok("卡死被点名", any(f.startswith("STUCK") for f in by["bbbbbbbb"]["flags"]), by["bbbbbbbb"]["flags"])
        ok("超时间盒被点名", any(f.startswith("OVER_BOX") for f in by["cccccccc"]["flags"]), by["cccccccc"]["flags"])
        ok("正常 lane 无 flag", by["aaaaaaaa"]["flags"] == [], by["aaaaaaaa"]["flags"])
        ok("完成态不产生 flag", by["dddddddd"]["flags"] == [])
        # NO_NOTES 是「工作区级」判据（取 notes/ 最新写入）→ 用**隔离工作区**测
        ws_iso = tmp / "ws_iso"
        (ws_iso / "notes").mkdir(parents=True)
        nf = ws_iso / "notes" / "old.md"
        nf.write_text("old", encoding="utf-8")
        t_old = time.time() - 60 * 60
        os.utime(nf, (t_old, t_old))
        rows_iso = scan(runs, 4.0, 40.0, 5.0, ws_iso)
        ok("NO_NOTES：notes 早于启动 → 点名",
           any("NO_NOTES" in r["flags"] for r in rows_iso if r["run"][:8] == "ffffffff"),
           [r["flags"] for r in rows_iso if r["run"][:8] == "ffffffff"])
        # 落盘后 NO_NOTES 应消失
        nt = time.time()
        nf.write_text("progress now", encoding="utf-8"); os.utime(nf, (nt, nt))
        rows2 = scan(runs, 4.0, 40.0, 5.0, ws_iso)
        ok("落盘后 NO_NOTES 消失",
           not any("NO_NOTES" in r["flags"] for r in rows2 if r["run"][:8] == "ffffffff"))
        # 空 notes 目录也算没落盘
        ws_empty = tmp / "ws_empty"
        (ws_empty / "notes").mkdir(parents=True)
        rows3e = scan(runs, 4.0, 40.0, 5.0, ws_empty)
        ok("notes 目录为空 → 也算 NO_NOTES",
           any("NO_NOTES" in r["flags"] for r in rows3e if r["run"][:8] == "aaaaaaaa"))
        mk("eeeeeeee-0000", "running", 5, 0.2, pid=999999, notes=True)
        rows3 = scan(runs, 4.0, 40.0, 5.0, ws)
        ok("pid 不存在 → ORPHAN", any("ORPHAN" in r["flags"] for r in rows3 if r["run"][:8] == "eeeeeeee"))
        ok("坏 status.json 不抛", scan(runs / "nope", 4, 40, 5, ws) == [])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for _, c, _ in checks if c)
    for n, c, e in checks:
        print(f"  {'✓' if c else '✗'} {n}{('  ' + str(e)) if e and not c else ''}")
    print(f"\n{passed}/{len(checks)} 断言通过")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
