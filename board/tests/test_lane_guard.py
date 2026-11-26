#!/usr/bin/env python3
"""lane_guard.py 回归测试 —— 治「长尾 lane 白烧 token / 卡死 / 不落盘」的看门狗。

背景（2026-10-01 血账）：一条 lane 跑 45 分钟 / 31.8 万 token，**从不落盘**，被超时杀掉后零产物；
另一条 69 轮 / 54 万 token 还在原地枚举。当时没有任何工具能提前发现 —— 只能等它自己超时。
本测试守住三件事：判据正确、不碰真产物、坏输入不崩。

    python3 board/tests/test_lane_guard.py
"""
from __future__ import annotations
import sys

import importlib.util
import json
import os
import pathlib
import shutil
import tempfile
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

LAB = pathlib.Path(__file__).resolve().parents[2]
GUARD = LAB / "tools" / "lane_guard.py"


def load():
    spec = importlib.util.spec_from_file_location("lane_guard_mod", GUARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_run(runs: pathlib.Path, name: str, state: str, age_min: float, idle_min: float,
             pid: int = 0) -> pathlib.Path:
    d = runs / name
    d.mkdir(parents=True, exist_ok=True)
    now_ms = int(time.time() * 1000)
    (d / "status.json").write_text(json.dumps({
        "state": state,
        "startedAt": now_ms - int(age_min * 60_000),
        "lastActivityAt": now_ms - int(idle_min * 60_000),
        "deadlineAt": now_ms + 10 * 60_000,
        "pid": pid,
    }), encoding="utf-8")
    log = d / "output-0.log"
    log.write_text("x" * 2048, encoding="utf-8")
    t = time.time() - idle_min * 60
    os.utime(log, (t, t))
    return d


def main() -> int:
    checks: list = []

    def check(name, cond, extra=""):
        checks.append((name, bool(cond), str(extra)))

    m = load()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="laneguard-test-"))
    runs = tmp / "runs"
    real_runs = pathlib.Path("/tmp/pi-subagents-uid-0/async-subagent-runs")
    real_before = sorted(p.name for p in real_runs.iterdir()) if real_runs.is_dir() else []

    try:
        ws = tmp / "ws"
        (ws / "notes").mkdir(parents=True)
        (ws / "notes" / "fresh.md").write_text("progress", encoding="utf-8")

        make_run(runs, "aaaaaaaa-0000", "running", 5, 0.2)      # 正常
        make_run(runs, "bbbbbbbb-0000", "running", 5, 9)        # STUCK（日志静默 9 分钟）
        make_run(runs, "cccccccc-0000", "running", 70, 0.2)     # OVER_BOX（超 40 分钟）
        make_run(runs, "dddddddd-0000", "completed", 5, 0.2)    # 已完成 → 不产生 flag
        make_run(runs, "eeeeeeee-0000", "running", 5, 0.2, pid=999_999)   # ORPHAN（pid 不存在）

        rows = m.scan(runs, 4.0, 40.0, 5.0, ws)
        by = {r["run"][:8]: r for r in rows}
        check("扫描出不重复的 run", len(rows) == 5, [r["run"] for r in rows])
        check("运行中 lane 计数正确", sum(1 for r in rows if r["state"] == "running") == 4)
        check("正常 lane 无 flag", by["aaaaaaaa"]["flags"] == [], by["aaaaaaaa"]["flags"])
        check("卡死（日志静默）被点名", any(f.startswith("STUCK") for f in by["bbbbbbbb"]["flags"]),
              by["bbbbbbbb"]["flags"])
        check("超时间盒被点名", any(f.startswith("OVER_BOX") for f in by["cccccccc"]["flags"]),
              by["cccccccc"]["flags"])
        check("完成态不报 flag", by["dddddddd"]["flags"] == [], by["dddddddd"]["flags"])
        check("僵尸（pid 没了）被点名", "ORPHAN" in by["eeeeeeee"]["flags"], by["eeeeeeee"]["flags"])

        # NO_NOTES：工作区 notes/ 的写入早于 lane 启动 → 说明它一直没落盘
        ws_old = tmp / "ws_old"
        (ws_old / "notes").mkdir(parents=True)
        old = ws_old / "notes" / "old.md"
        old.write_text("old", encoding="utf-8")
        t_old = time.time() - 3600
        os.utime(old, (t_old, t_old))
        rows_old = m.scan(runs, 4.0, 40.0, 5.0, ws_old)
        check("★ 没落盘（NO_NOTES）被点名",
              any("NO_NOTES" in r["flags"] for r in rows_old if r["run"][:8] == "aaaaaaaa"),
              [r["flags"] for r in rows_old if r["run"][:8] == "aaaaaaaa"])
        now = time.time()
        old.write_text("progress now", encoding="utf-8")
        os.utime(old, (now, now))
        rows_after = m.scan(runs, 4.0, 40.0, 5.0, ws_old)
        check("落盘之后 NO_NOTES 消失",
              not any("NO_NOTES" in r["flags"] for r in rows_after if r["run"][:8] == "aaaaaaaa"))
        ws_empty = tmp / "ws_empty"
        (ws_empty / "notes").mkdir(parents=True)
        check("notes 目录为空也算没落盘",
              any("NO_NOTES" in r["flags"] for r in m.scan(runs, 4.0, 40.0, 5.0, ws_empty)
                  if r["run"][:8] == "bbbbbbbb"))

        # 阈值可调（论文级判据：曲线是写死的，不容临场改口径）
        check("阈值可调：40 分钟盒放宽到 90 分钟 → OVER_BOX 消失",
              not any("OVER_BOX" in r["flags"] for r in m.scan(runs, 4.0, 90.0, 5.0, ws)
                      if r["run"][:8] == "cccccccc"))
        check("阈值可调：stuck 阈值放宽到 20 分钟 → STUCK 消失",
              not any("STUCK" in r["flags"] for r in m.scan(runs, 20.0, 40.0, 5.0, ws)
                      if r["run"][:8] == "bbbbbbbb"))

        # 坏输入 / 边界
        check("runs 目录不存在 → 空结果不抛", m.scan(tmp / "nope", 4, 40, 5, ws) == [])
        bad = runs / "ffffffff-0000"
        bad.mkdir(parents=True, exist_ok=True)
        (bad / "status.json").write_text("{ 这不是 JSON", encoding="utf-8")
        rows_bad = m.scan(runs, 4.0, 40.0, 5.0, ws)
        check("坏 status.json 被跳过（不抛异常）",
              "ffffffff" not in {r["run"][:8] for r in rows_bad})

        # ★ 不许碰真产物：真实 run 目录不能被本测试改动
        real_after = sorted(p.name for p in real_runs.iterdir()) if real_runs.is_dir() else []
        check("测试没有碰真实 subagent run 目录", real_after == real_before,
              f"{len(real_before)} → {len(real_after)}")

        # --check 语义：有 flag 时退出码 1
        rc = m.main(["--runs-dir", str(runs), "--workspace", str(ws), "--check",
                     "--stuck-min", "4", "--timebox-min", "40"])
        check("--check 有告警时退出码 1", rc == 1, rc)
        clean = tmp / "clean_runs"
        make_run(clean, "11111111-0000", "running", 5, 0.2)
        ws_fresh = tmp / "ws_fresh"
        (ws_fresh / "notes").mkdir(parents=True)
        f = ws_fresh / "notes" / "n.md"
        f.write_text("ok", encoding="utf-8")
        os.utime(f, (time.time(), time.time()))
        rc2 = m.main(["--runs-dir", str(clean), "--workspace", str(ws_fresh), "--check"])
        check("--check 全绿时退出码 0", rc2 == 0, rc2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for _, c, _ in checks if c)
    for name, c, extra in checks:
        print(f"  {'✓' if c else '✗'} {name}" + (f"  {extra}" if extra and not c else ""))
    print(f"\n{passed}/{len(checks)} 断言通过")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
