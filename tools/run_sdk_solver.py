#!/usr/bin/env python3
"""run_sdk_solver.py —— 用 tsec-benchmark SDK 驱动 baoan 智能体自动解题。

设计要点（对应 SDK_API.md 的五步流程）：
  list_challenges → start_challenge → call_your_agent → submit_flag → close_challenge

为什么不用 hosted-entrypoint.sh 那套 pi -p 循环：
  · 那套靠提示词让 agent 自己 tsec list / submit，判据软、易空转；
  · SDK 是结构化 API，flag 提交有回执（correct/awarded），闭环可审计；
  · 这里把「解题」交给 pi 子进程，「提交」由本脚本统一做，避免 agent 编 flag。

用法：
  python3 tools/run_sdk_solver.py                 # 解所有未通关的题
  python3 tools/run_sdk_solver.py --code xben-001-24   # 只解一道
  python3 tools/run_sdk_solver.py --dry-run       # 只列题，不起 agent
  python3 tools/run_sdk_solver.py --budget 600    # 每题 agent 时间盒（秒）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "log"
FLAG_RE = re.compile(r"[A-Za-z0-9_]{2,20}\{[^}\n]{4,200}\}")


def log(msg: str) -> None:
    print(f"[sdk-solver] {msg}", flush=True)


def extract_flags(text: str) -> list[str]:
    """从 agent 输出里抽 flag 候选（去重保序）。真伪仍由平台判定。"""
    seen, out = set(), []
    for m in FLAG_RE.finditer(text or ""):
        f = m.group(0)
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def build_prompt(code: str, addrs: list[str], flag_count: int) -> str:
    addr = ", ".join(addrs) or "(平台未给地址)"
    return (
        f"你是本机 CTF agent，正在打 TSecBench 的 {code} 题。\n"
        f"靶场地址（经 VPN 直连）：{addr}\n"
        f"这道题有 {flag_count} 个 flag。\n\n"
        "硬性要求：\n"
        "1) 先 fgs kickoff 建黑板，再按 /opt/pi-ctf/AGENTS.md 流程解题；\n"
        "2) 每拿到一个 flag，**立刻原样打印一行** `FOUND_FLAG=<flag>`（我要用它提交）；\n"
        "3) 不要自己 tsec submit（提交统一由外层脚本做），也不要猜/编 flag；\n"
        f"4) 时间盒：{os.environ.get('SOLVER_AGENT_BUDGET', '600')} 秒内拿不到就收尾并说明卡在哪。\n"
    )


def run_agent(prompt: str, budget: int, code: str) -> tuple[str, int]:
    """起一个 pi 子进程解题，返回 (stdout+stderr, rc)。超时即杀。"""
    cmd = ["pi", "-p", prompt, "--provider", "deepseek", "--model", "deepseek-flash"]
    log(f"{code}: 起 agent（预算 {budget}s）")
    try:
        cp = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=budget)
        return (cp.stdout or "") + (cp.stderr or ""), cp.returncode
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") + (e.stderr or "")
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        return out, 124
    except FileNotFoundError:
        return "找不到 pi 命令", 127


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", help="只解这一道")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--budget", type=int, default=int(os.environ.get("SOLVER_AGENT_BUDGET", "600")))
    ap.add_argument("--max-challenges", type=int, default=0, help="本轮最多解几道（0=全部）")
    args = ap.parse_args()

    os.environ["SOLVER_AGENT_BUDGET"] = str(args.budget)
    LOG_DIR.mkdir(exist_ok=True)
    runlog = LOG_DIR / f"sdk-solver-{time.strftime('%Y%m%d-%H%M%S')}.log"

    try:
        from tsec_benchmark import (
            TSecBenchmark, VpnCheckError, DuplicateSubmit, InvalidState,
            ChallengeNotFound, TSecError,
        )
    except ImportError as e:
        log(f"缺 SDK：{e} —— 用 /opt/pi-ctf/tools/venv/bin/python 跑本脚本")
        return 2

    base = os.environ.get("BENCHMARK_BASE_URL") or os.environ.get("TSEC_BASE_URL") or ""
    token = os.environ.get("BENCHMARK_TOKEN") or os.environ.get("TSEC_TOKEN") or ""
    if not base or not token:
        # 兜底读 tsec 配置，避免每次都要 export
        for cand in (Path.home() / ".pi-ctf/agent/tsecbench.json",
                     ROOT / ".pi/tsecbench.json"):
            if cand.is_file():
                try:
                    cfg = json.loads(cand.read_text(encoding="utf-8"))
                    base = base or cfg.get("base_url", "")
                    token = token or cfg.get("token", "")
                except Exception:
                    pass
                if base and token:
                    break
    if not base or not token:
        log("缺 BENCHMARK_BASE_URL / BENCHMARK_TOKEN")
        return 2

    solved, attempted = 0, 0
    try:
        with TSecBenchmark(base_url=base, token=token) as client:
            chs = client.list_challenges()
            todo = [c for c in chs if not c.is_completed]
            if args.code:
                todo = [c for c in todo if c.unique_code == args.code]
            if args.max_challenges:
                todo = todo[: args.max_challenges]
            log(f"待解 {len(todo)} / 共 {len(chs)} 题（日志 {runlog}）")
            for ch in todo:
                log(f"{ch.unique_code}  {ch.difficulty}  {ch.correct_flag_count}/{ch.flag_count}")
            if args.dry_run or not todo:
                return 0

            for ch in todo:
                attempted += 1
                try:
                    started = client.start_challenge(ch.unique_code)
                except InvalidState as e:
                    log(f"{ch.unique_code}: 起不了容器（{e.message}）—— 可能活跃实例已满，跳过")
                    continue
                except TSecError as e:
                    log(f"{ch.unique_code}: 起容器失败 {type(e).__name__}: {e}")
                    continue
                addrs = list(started.container_addr or [])
                log(f"{ch.unique_code} → {addrs}")
                try:
                    prompt = build_prompt(ch.unique_code, addrs, ch.flag_count)
                    out, rc = run_agent(prompt, args.budget, ch.unique_code)
                    with runlog.open("a", encoding="utf-8") as fh:
                        fh.write(f"\n===== {ch.unique_code} rc={rc} =====\n{out}\n")
                    # ★ R2-C：传题号，过滤「题号的 sha256」这类公开参考值
                    cands = extract_flags(out, ch.unique_code)
                    log(f"{ch.unique_code}: agent rc={rc}，抽出 {len(cands)} 个 flag 候选")
                    got = 0
                    for flag in cands:
                        try:
                            res = client.submit_flag(ch.unique_code, flag)
                            if res.correct:
                                got += 1
                                log(f"  ✅ {flag}  awarded={res.awarded} "
                                    f"进度 {res.correct_flag_count}/{res.total_flag_count}")
                            else:
                                log(f"  ❌ {flag}（平台判错）")
                        except DuplicateSubmit:
                            log(f"  ↺ {flag} 已提交过（幂等跳过）")
                        except ChallengeNotFound:
                            log("  题目不存在，停止本题")
                            break
                        except TSecError as e:
                            log(f"  提交异常 {type(e).__name__}: {e}")
                    if got:
                        solved += 1
                finally:
                    try:
                        client.close_challenge(ch.unique_code)
                    except TSecError as e:
                        log(f"  关闭容器失败（不影响已得分的 flag）：{e}")
    except VpnCheckError as e:
        log(f"VPN 检测未通过：{e} —— 先 tsec vpn up")
        return 3
    except TSecError as e:
        log(f"平台错误 {type(e).__name__}: {e}")
        return 1

    log(f"完成：尝试 {attempted} 题，有产出 {solved} 题。日志 {runlog}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
