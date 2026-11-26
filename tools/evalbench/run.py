#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run.py —— 本地评测台的执行器：用**与实战完全相同的 pi 参数**跑本地题并计时。

## 它解决什么问题

实战（TSecBench）结束后，「改了提示词/参数到底变快还是变慢」没有任何可测手段。
本工具把 `sdkshard.solve_one()` 的**参数拼装过程**复用过来（同一个 build_prompt、
同一个 solver_system、同一组 --no-context-files/--no-skills/--exclude-tools），
只把「平台给的容器地址」换成「本地起的挑战地址」，把「平台判分」换成
「预期 flag 是否逐字出现在输出或会话里」。

于是可以：

    python3 tools/evalbench/run.py --arm baseline --n 1
    PI_SOLVE_SLIM=0 python3 tools/evalbench/run.py --arm no-slim --n 1
    python3 tools/evalbench/run.py --report            # 汇总各臂的耗时/成功率

每次运行的明细落在 `log/evalbench/<arm>.jsonl`，可复算。

## 为什么要「同参数」而不是简化调用

优化 06/12 改的都是 pi 的**启动参数**。如果评测台自己拼一套简化参数，
它测出来的就不是线上实际行为 —— 会得出「改了没影响」的假结论。
所以这里直接 import sdkshard，用它的 build_prompt / solver_system / wrapup_args。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import challenges as CH  # noqa: E402
import sdkshard as SD  # noqa: E402
import bench_metrics as BM  # noqa: E402

OUT_DIR = ROOT / "log" / "evalbench"
SOLVE_DIR = ROOT / "work" / "evalsolve"
# ★ R10 P0-B（缺陷 50）：证据必须落在**不会被清理**的地方。
#   旧行为把结果写进 log/evalbench/，而 log/ 是清理目标且不在持久化范围 ——
#   实测本工作区与容器两侧都没有该目录，于是 README #14 的 A/B 无法复核。
#   ★ 目录为什么是 tools/evalbench/results（R10 实测 docker inspect）：
#     容器只 rw 共享 kb/log/tools/workspace/work/{intake,solve}；
#     历史复盘 与 work/ 根 都**没有挂载**，写进容器里的那些路径会随容器消失。
#     第一版写成 历史复盘 data/evalbench，跑完宿主确实看不到 —— 已实测踩过。
#     PI_EVALBENCH_RESULT_DIR 可覆盖（跨机/迁移用）。
RESULT_DIR = Path(os.environ.get("PI_EVALBENCH_RESULT_DIR",
                                  str(ROOT / "tools" / "evalbench" / "results")))


def _nosave() -> bool:
    """PI_EVALBENCH_NOSAVE=1 时不落盘（回滚用；默认落盘）。"""
    return os.environ.get("PI_EVALBENCH_NOSAVE", "0").strip() == "1"


def _spawn(args, cwd, env, budget, outpath):
    """起一个 pi 并把 stdout/stderr 落到 outpath；超时杀整个进程组并返回 124。"""
    with outpath.open("wb") as fh:
        proc = subprocess.Popen(args, cwd=cwd, stdout=fh,
                                stderr=subprocess.STDOUT, start_new_session=True, env=env)
        try:
            return proc.wait(timeout=budget)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except OSError:
                proc.kill()
            return 124


def _session_stats(sessdir: Path):
    """从会话 jsonl 算：轮数 / 工具次数 / 生成秒 / 工具秒（归属产生它的那一方）。"""
    files = sorted(sessdir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not files:
        return {"turns": 0, "tool_calls": 0, "gen_sec": 0.0, "tool_sec": 0.0, "end_sec": 0.0}
    recs = []
    for f in files:
        for ln in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            if rec.get("type") != "message":
                continue
            m = rec.get("message") or {}
            ts = rec.get("timestamp")
            if not ts:
                continue
            recs.append((ts, m))
    recs.sort(key=lambda x: x[0])

    def _ts(s):
        # 2026-11-20T07:32:11.123Z
        try:
            from datetime import datetime
            return datetime.strptime(s[:23], "%Y-%m-%dT%H:%M:%S.%f").timestamp()
        except Exception:
            try:
                from datetime import datetime
                return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").timestamp()
            except Exception:
                return 0.0

    turns = sum(1 for _, m in recs if m.get("role") == "assistant")
    tools = 0
    gen = tool = 0.0
    for i in range(1, len(recs)):
        t0, m0 = recs[i - 1]
        t1, _ = recs[i]
        gap = _ts(t1) - _ts(t0)
        if gap <= 0 or gap > 120:
            continue
        if m0.get("role") == "toolResult":
            gen += gap
        elif m0.get("role") == "assistant":
            _c = m0.get("content")
            has_call = any(isinstance(b, dict) and b.get("type") == "toolCall"
                           for b in (_c if isinstance(_c, list) else []))
            if has_call:
                tool += gap
    for _, m in recs:
        c = m.get("content")
        if isinstance(c, list):
            tools += sum(1 for b in c if isinstance(b, dict) and b.get("type") == "toolCall")
    span = (_ts(recs[-1][0]) - _ts(recs[0][0])) if len(recs) > 1 else 0.0
    return {"turns": turns, "tool_calls": tools, "gen_sec": round(gen, 1),
            "tool_sec": round(tool, 1), "end_sec": round(span, 1)}


def run_one(code: str, port: int, expect_flag: str, arm: str, model: str,
            budget: int, attempt_no: int = 1, extra_env=None, rep: int = 1) -> dict:
    # ★ 每次重复用**独立会话目录**：同目录复用会让 pi 的会话越攒越多，
    #   而 _session_stats 是整目录统计 → 「均生成秒」会随重复次数单调增长，
    #   第一版就踩了这个坑（第 1 遍 2.3s，第 3 遍 18.8s，看着像变慢了）。
    sessdir = SOLVE_DIR / arm / ("%s-r%d" % (code, rep))
    sessdir.mkdir(parents=True, exist_ok=True)
    scratch = SD.lane_scratch(sessdir)
    sysfile = SD.write_solver_system(sessdir, scratch)
    addrs = ["127.0.0.1:%d" % port]
    prompt = SD.build_prompt(code, addrs, 1, budget, attempt_no=attempt_no)

    # ★ R10 P0-C：provider 必须可切 —— 硬编码 deepseek 会在切换 provider 后
    #   跑出"认证失败"，而失败会被误读成"这臂更差"（评测台最危险的假结论）。
    _prov = os.environ.get("PI_EVALBENCH_PROVIDER", "deepseek").strip() or "deepseek"
    args = ["pi", "-p", prompt, "--provider", _prov, "--model", model,
            "--session-dir", str(sessdir)]
    # ★ thinking 档位（pi 默认 high）。生产代码 sdkshard 是否传 --thinking 由
    #   PI_SOLVE_THINKING 决定；这里照抄同一逻辑，A/B 才有意义。
    _th = os.environ.get("PI_SOLVE_THINKING", "").strip()
    if _th:
        args += ["--thinking", _th]
    if os.environ.get("PI_SOLVE_SLIM", "1") != "0":
        args += ["--no-context-files", "--no-skills"]
    _ex = SD.solver_exclude()
    if _ex:
        args += ["--exclude-tools", _ex]
    if sysfile is not None:
        args += ["--append-system-prompt", str(sysfile)]

    env = dict(os.environ)
    if scratch is not None:
        env[SD.SCRATCH_ENV] = str(scratch)
    if extra_env:
        env.update(extra_env)

    outpath = OUT_DIR / ("out-%s-%s-%d.log" % (arm, code, int(time.time())))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    rc = _spawn(args, cwd=str(ROOT), env=env, budget=budget, outpath=outpath)
    wall = round(time.time() - t0, 1)
    out = outpath.read_text(encoding="utf-8", errors="replace")
    # ★ 实测（2026-11-20，no-slim 臂 12 次里 3 次）：pi 会以 rc=-11（SIGSEGV）秒退，
    #   连一条会话都不写（turns=0）。sdkshard 的 crash_sig 分支就是为了这个 ——
    #   评测台若把它记成「没解出来」，就会得出「某臂更差」的假结论。
    #   这里与线上同策略：崩溃重跑一次，并把崩溃次数记进结果。
    crash = (rc < 0) or ("Fatal error in" in out) or ("RepresentationChangerError" in out)
    crashes = 0
    if crash:
        crashes = 1
        rc2 = _spawn(args, cwd=str(ROOT), env=env, budget=budget, outpath=outpath)
        if rc2 == 0 or rc2 == 124:
            rc = rc2
            out = outpath.read_text(encoding="utf-8", errors="replace")
    # 超时也要从会话里捞（stdout 会丢，session 不会）
    for sf in sorted(sessdir.glob("*.jsonl")):
        for ln in sf.read_text(encoding="utf-8", errors="replace").splitlines():
            if "FOUND_FLAG=" in ln:
                out += "\n" + ln
    found = SD.extract_flags(out)
    ok = expect_flag in found or expect_flag in out
    stats = _session_stats(sessdir)
    # ★ R10 P0-B：成本也进结果（否则"省了多少"永远只能靠事后拿会话重算）。
    try:
        _use = BM.usage_of_dir(str(sessdir))
    except Exception:  # noqa: BLE001
        _use = {"tokens": 0, "cost_est_usd": 0.0}
    stats["tokens"] = int(_use.get("tokens", 0) or 0)
    stats["cost_est_usd"] = float(_use.get("cost_est_usd", 0.0) or 0.0)
    return {
        "ts": int(t0), "arm": arm, "code": code, "model": model, "budget": budget,
        "rc": rc, "wall_sec": wall, "ok": bool(ok), "crashes": crashes,
        "flags_seen": found[:4], "expect": expect_flag,
        **stats,
    }


def _append(arm: str, row: dict):
    if _nosave():
        return
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / (arm + ".jsonl")
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_summary(arm: str, rows: list, meta: dict):
    """把一臂的结果写成 durability 摘要 + 索引（R10 P0-B）。

    与 _append 的区别：jsonl 是逐行流水（给机器重算），summary 是**给人看的结论**，
    并且落在 tools/evalbench/results/ —— 容器 rw 共享，宿主可直接复核。
    没有 summary 时，compare.py 就无从下手，这正是缺陷 50 的形态。
    """
    if _nosave():
        print("[evalbench] PI_EVALBENCH_NOSAVE=1 -> 不写 summary")
        return None
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import compare as CMP  # noqa: PLC0415
    d = CMP.aggregate(arm, rows)
    d.update(meta)
    d["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    p = RESULT_DIR / ("%s-%s-summary.json" % (arm, stamp))
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    idx = RESULT_DIR / "index.jsonl"
    with idx.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"arm": arm, "summary": p.name,
                             "n": d["n"], "ok": d["ok"],
                             "cost_est_usd": d["cost_est_usd"],
                             "finished_at": d["finished_at"]}, ensure_ascii=False) + "\n")
    print("[evalbench] summary -> %s" % p)
    return d


def report():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in sorted(OUT_DIR.glob("*.jsonl")):
        for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rows.append(json.loads(ln))
            except Exception:
                continue
    if not rows:
        print("[evalbench] 还没有任何运行记录（先跑一次 run.py）")
        return 1
    by = {}
    for r in rows:
        by.setdefault(r["arm"], []).append(r)
    print("%-14s %4s %6s %8s %8s %8s %8s" % ("arm", "n", "ok", "均墙钟", "均生成", "均工具", "均轮次"))
    for arm, rs in sorted(by.items()):
        n = len(rs)
        ok = sum(1 for r in rs if r["ok"])
        print("%-14s %4d %6d %8.1f %8.1f %8.1f %8.1f" % (
            arm, n, ok,
            sum(r["wall_sec"] for r in rs) / n,
            sum(r.get("gen_sec", 0) for r in rs) / n,
            sum(r.get("tool_sec", 0) for r in rs) / n,
            sum(r.get("turns", 0) for r in rs) / n))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", default="baseline", help="臂名（结果按臂分文件）")
    ap.add_argument("--n", type=int, default=1, help="每个题跑几次")
    ap.add_argument("--codes", default="", help="只跑指定 id（逗号分隔），默认全跑")
    ap.add_argument("--model", default=os.environ.get("PI_EVALBENCH_MODEL", "deepseek-flash"))
    ap.add_argument("--budget", type=int, default=150)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        return report()

    targets = [c for c in CH.CHALLENGES
               if not a.codes or c["id"] in [x.strip() for x in a.codes.split(",")]]
    if not targets:
        print("[evalbench] 没匹配到题目")
        return 2
    servers = CH.serve_all()
    bad = 0
    rows = []
    errors = []
    try:
        for c in targets:
            for i in range(a.n):
                # ★ R10 P0-B：单次失败**也必须落盘**。旧版若 run_one 抛异常，
                #   这一次就整体消失 —— "跑崩的那一臂"会被误读成"从没跑过"，
                #   进而得出「某臂更差」的假结论（与 R6-04 记 FLAKY 的教训同源）。
                try:
                    row = run_one(c["id"], c["port"], c["flag"], a.arm, a.model,
                                  a.budget, rep=i + 1)
                    row.setdefault("status", "ok" if row.get("ok") else "miss")
                except Exception as exc:  # noqa: BLE001
                    errors.append({"code": c["id"], "error": str(exc)[:300]})
                    row = {"arm": a.arm, "code": c["id"], "ok": False, "status": "failed",
                           "rc": -1, "wall_sec": 0.0, "turns": 0, "gen_sec": 0.0,
                           "tool_sec": 0.0, "crashes": 0, "tokens": 0,
                           "cost_est_usd": 0.0, "error": str(exc)[:300]}
                rows.append(row)
                _append(a.arm, row)
                bad += 0 if row["ok"] else 1
                print("[evalbench] %-18s %s 墙钟=%5.1fs 生成=%5.1fs 工具=%5.1fs 轮=%d rc=%d"
                      % (c["id"], "OK  " if row["ok"] else "MISS", row["wall_sec"],
                         row.get("gen_sec", 0), row.get("tool_sec", 0),
                         row.get("turns", 0), row["rc"]))
    finally:
        for s in servers:
            s.shutdown()
    # ★ R10 P0-B：一臂结束必须留下**可复核的** summary，否则等于没跑过。
    write_summary(a.arm, rows, {"model": a.model, "budget": a.budget,
                                "provider": os.environ.get("PI_EVALBENCH_PROVIDER", "deepseek"),
                                "codes": [c["id"] for c in targets],
                                "repeat": a.n, "errors": errors[:20]})
    print("[evalbench] 本臂完成：%d 题，成功 %d" % (len(targets) * a.n, len(targets) * a.n - bad))
    if errors:
        print("[evalbench] 异常 %d 次（已按 status=failed 落盘）：%s"
              % (len(errors), [e["code"] for e in errors][:6]))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())