#!/usr/bin/env python3
"""supervise.py —— 解题链路守护进程（P0：parallel_solver 是单次前台进程，挂了就永久停摆）。

为什么必须有它（2026-11-19 实测事故）：
  · 13:45 起的 parallel_solver(pid 1697) + 3 个 sdkshard 在 14:34 容器重启里被全杀；
  · 之后**没有任何机制把它们拉起来**，直到我 14:45 手工重启 —— 空转约 10 分钟；
  · 根因是架构性的：UI 服务是容器 PID1（docker/entrypoint.sh 第 80 行 exec node server.mjs），
    parallel_solver 只是我 nohup 起的一个孤儿进程，谁都不会去管它。

本脚本的职责（只做这一件事，宁简单勿复杂）：
  每 30s 看一次：
    1) 到达 SOLVER_DEADLINE（或 run 已结束）→ 退出，不再拉起；
    2) 平台还有未通关题，但**没有活的 sdkshard 进程** → 拉起 parallel_solver；
    3) parallel_solver 活着但 shard 全死 → 杀掉总控再拉（它的子进程管理不可靠）；
    4) 一切都好 → 什么都不做（打印一行心跳，便于 grep）。

为什么看「sdkshard 活没活」而不是「parallel_solver 活没活」：
  总控进程可能活着但子进程全死了（subprocess.Popen 之后只 communicate 一次，
  某个 shard 崩掉时它不会重试），这时仅看总控会误判「一切正常」。

用法：
  python3 tools/supervise.py                 # 前台跑（调试）
  setsid nohup python3 tools/supervise.py >> log/supervisor.log 2>&1 < /dev/null &
  python3 tools/supervise.py --once          # 只检查一次（CI/人工排查）
  python3 tools/supervise.py --stop          # 停机：写标记 + 杀链路 + 收孤儿 pi
  python3 tools/supervise.py --stop --keep-children   # 只写标记，不动进程
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "log"

# ★ 某轮优化：进程识别统一走 tools/procscan.py（原来这里是第三份重复实现，
#   且用 'pattern in args' 子串匹配 —— 实测 `bash -c 'tail -F ... sdkshard.py'` 会被
#   误判成 shard 活着，而这条判据正是「要不要重启」，误判成活着就永远不重启）。
sys.path.insert(0, str(Path(__file__).resolve().parent))
import procscan  # noqa: E402

STATE = ROOT / "work" / "solve" / ".supervisor.json"
STOP_MARK = ROOT / "work" / "solve" / ".supervisor-stop"
TICK = 30

# ═══════════════════════════════════════════════════════════════════════════
# ★★ 该轮 该缺陷：守护自己死了没人知道（真实事故 某轮复盘 §3.1b）
#
#   实测：supervisor.log 14:13:20 之后 **118.5 分钟零 tick**，
#   孤儿跑完到 15:45，之后 **全场 27.1 分钟零活动**，靠人肉 16:11:47 重启。
#
#   为什么原来的守护抓不到：它只检查"子进程在不在"，
#   **没有任何东西检查"守护自己还在不在跳"** —— 它一旦被 OOM/误杀/容器重启打死，
#   就没有第二个进程知道该拉起它。
#
#   修法（心跳 + 外部看门狗，两层）：
#     ① 本文件每跳一次就更新 HEARTBEAT 文件（内容=时间戳+pid）；
#     ② tools/watchdog.py 只做一件事：HEARTBEAT 超过阈值没更新 → 重启 supervise。
#   为什么用文件而不是信号：跨进程、跨容器重启都能读；不需要守护配合。
#   为什么阈值取 3×tick：单跳卡住（平台慢查询）不该被误判成死亡。
# ═══════════════════════════════════════════════════════════════════════════
HEARTBEAT = ROOT / "work" / "solve" / ".supervisor-heartbeat"
HEARTBEAT_STALE_S = 3 * TICK          # 超过这么久没更新 = 判定守护已死


def log(msg: str) -> None:
    print(f"[supervise {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def procs(pattern: str) -> list[int]:
    """找出**真的在跑**这个 python 脚本的进程（argv 语义，不是命令行子串）。

    ★★ 2026-11-20 某轮优化（实测抓到的误判）：原实现是
        `if pattern in args and "supervise.py" not in args`
    也就是「命令行里出现 sdkshard.py 就算」。用 tools/_an_procs2.py 造诱饵实测，
    下面这行会**被误判成 shard 活着**：

        bash -c 'tail -F log/shard.log sdkshard.py 2>/dev/null; sleep 15'   → 命中

    后果与该判据的用途正好相反 —— 它用来决定「要不要重启 shard」，
    误判成活着就**永远不重启**，比漏判更糟（漏判最多多重启一次）。
    真实场景里这种命令行不罕见：我用 bash 查日志时随手就会 tail 到 sdkshard.py，
    而守护每 30s 巡检一次，只要我那条命令活着它就不敢重启。

    判据改成与 solver-autostart.sh 里 running_py_script() 一致的语义：
      argv[0] 的 basename 以 python 开头，**且** argv[1] 的 basename 就是目标脚本。
    只认真正被解释器执行的那个脚本，命令行里"提到"不算。

    实现本身已抽到 tools/procscan.py（某轮优化 顺手统一了三份重复实现：
    这里、solver-autostart.sh、bench/live.py 都在判「谁在跑」但各写各的）。
    """
    return procscan.python_script_pids(pattern.rsplit("/", 1)[-1])


def pending_count() -> tuple[bool, int]:
    """(平台可达, 未通关数)。用 bench/live.py 的同一套 token 解析，避免两份逻辑漂移。"""
    try:
        sys.path.insert(0, str(ROOT / "tools" / "bench"))
        from live import platform  # noqa: PLC0415

        # ★ 签名是 platform(net=True, retries=3)，token 由 live._cfg() 自己在内部解析
        #   （env → 在跑的 shard 的 /proc/<pid>/environ → tsecbench.json）。
        #   早期版本写成 platform(base, token, ...) → TypeError: got multiple values for 'net'，
        #   结果每次都判「平台不可达」→ 守护永远不拉起（正好是最该拉起的场景）。
        ok, data = platform(net=True, retries=1)
        if not ok:
            return False, -1
        return True, sum(1 for c in data if not c.get("done"))
    except Exception as e:  # noqa: BLE001
        log(f"查平台失败（按未知处理，不重启）：{type(e).__name__}: {e}")
        return False, -1


def deadline_passed() -> bool:
    dl = float(os.environ.get("SOLVER_DEADLINE", "0") or "0")
    if dl and time.time() >= dl:
        return True
    # 平台 run 过期时间（写进 state 文件，由 --once 之外的手工流程更新；没有就忽略）
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
        exp = float(st.get("expires_at", 0) or 0)
        if exp and time.time() >= exp:
            return True
    except Exception:
        pass
    return False


def start_solver() -> None:
    LOG.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.setdefault("BENCHMARK_TOKEN", "")
    out = open(LOG / "parallel-supervisor.log", "a", encoding="utf-8")
    cmd = [sys.executable, str(ROOT / "tools" / "parallel_solver.py"),
           "--shards", os.environ.get("SUP_SHARDS", "3"),
           "--rounds", os.environ.get("SUP_ROUNDS", "40"),
           "--budget", os.environ.get("SUP_BUDGET", "300"),
           # ★ 2026-11-19 某轮优化：每轮题数保持 24。
           #   并行调度模拟（tools/_an_sim2.py，run 50607 干净轮真实时长）：
           #     每轮 12 + FIFO 2706s / LPT 2359s        每轮 24 + FIFO 2575s / LPT 2338s
           #     每轮 48 + FIFO 2512s / LPT 2330s        不限轮  + FIFO 2373s / LPT 2328s
           #   → **收益几乎全部来自 LPT（同轮大小下 -237s / -9.2%）**，
           #     轮大小只影响 LPT 能发挥多少：12→2359、24→2338、48→2330、不限→2328。
           #   曾试过把默认改成 12（以为「题少更好排」），实测反而差 21s —— 已回退。
           #   不改成「不限轮」的理由：一整轮跑完 104 题要 40 分钟，
           #   中途新解出的题不重新 list、代码改动不生效、事故恢复粒度也太粗；
           #   而它相对 24 只快 10s（0.4%），不值得。
           "--per-round", os.environ.get("SUP_PER_ROUND", "24")]
    # setsid + 独立进程组：守护自己被杀时不连带子进程
    p = subprocess.Popen(cmd, cwd=str(ROOT), env=env, stdout=out, stderr=subprocess.STDOUT,
                         start_new_session=True)
    log(f"拉起 parallel_solver pid={p.pid}（{' '.join(cmd[2:])}）")


def kill_tree(pid: int) -> None:
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except Exception:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass


def reap_orphans(include_live: bool = False) -> list[int]:
    """回收无头 pi（孤儿 / 全部）。返回被杀 pid 列表。

    ★★ 2026-11-20 该轮修复：
      停机时只 kill parallel_solver + sdkshard 是**不够**的 ——
      sdkshard 用 start_new_session=True 拉起 pi，pi 在独立进程组里，
      父亲一死立刻被 init 收养（ppid=1）继续烧 API。
      实测停机后 13 分钟 3 个孤儿 pi 又发了 37 次请求 / $0.515，全部零产出。
      include_live=True 时连「还有活父亲」的一起收：停机要一次清干净，
      否则永远在演「先死父、再留孤儿」。
    """
    try:
        import reap_orphan_agents as _roa  # noqa: PLC0415  (同目录，运行时再导入)
    except Exception:
        return []
    try:
        found = _roa.scan(orphans_only=not include_live)
    except Exception:
        return []
    if not found:
        return []
    killed = _roa.kill_pids([r["pid"] for r in found])
    if killed:
        log(f"回收无头 pi {len(killed)} 个：{killed}")
    return killed


def beat() -> None:
    """写一次心跳。硬要求：**任何 return 分支之前都要跳**。

    为什么放在 tick() 开头而不是每跳结束：tick 内部有多条 return（stopped/unknown/ok/
    restarted/started），写在末尾会漏掉一半。心跳的语义是「守护进程还活着并完成了这次巡检」，
    所以正确位置是**进入 tick 后立刻**更新 —— 这样即使本次巡检判定要退出（stopped），
    也留下了「我还活着并做出了判断」的记录。

    失败不抛：磁盘满/权限问题不该让守护本身崩掉。
    """
    try:
        HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT.write_text("%d %s" % (int(time.time()), time.strftime("%F %T")),
                             encoding="utf-8")
    except OSError:
        pass


def heartbeat_age() -> float:
    """心跳距今多少秒；文件不存在返回 inf（视为从没活过）。"""
    try:
        raw = HEARTBEAT.read_text(encoding="utf-8").split()
        return max(0.0, time.time() - float(raw[0]))
    except (OSError, ValueError, IndexError):
        return float("inf")


def tick(verbose: bool = False) -> str:
    """一次巡检，返回动作名（ok / started / restarted / stopped / idle / unknown）。"""
    beat()
    if STOP_MARK.is_file():
        return "stopped"
    if deadline_passed():
        log("到达止损时间 → 守护退出（不再拉起）")
        return "stopped"

    ok, left = pending_count()
    shards = procs("sdkshard.py")
    ctrl = procs("parallel_solver.py")

    if not ok:
        # 平台不可达：别乱杀（VPN 抖动时杀掉只会让题白跑），只在**没有 shard** 时才休眠
        if not shards and not ctrl:
            log("平台不可达且无解题进程 → 本轮不拉起（等下一跳）")
            return "unknown"
        if verbose:
            log(f"平台不可达，但在跑 shard={len(shards)} ctrl={len(ctrl)} → 保持")
        return "ok"

    if left == 0:
        log("全部通关 → 守护退出")
        return "stopped"

    if shards:
        # ★ 顺路收孤儿：shard 被 kill 时留下的 pi 会一直烧钱（ppid=1）。
        #   include_live=False —— 正常跑时**绝不**碰还有活父亲的无头 pi（那是正在干活的）。
        reap_orphans(include_live=False)
        if verbose:
            log(f"正常：未通关 {left} 题，shard {len(shards)} 路在跑")
        return "ok"

    # shard 全死
    if ctrl:
        log(f"总控 pid={ctrl} 活着但 shard 全死（未通关 {left} 题）→ 重启总控")
        for p in ctrl:
            kill_tree(p)
        time.sleep(2)
        start_solver()
        return "restarted"
    log(f"解题链路空转（未通关 {left} 题）→ 拉起")
    start_solver()
    return "started"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--keep-children", action="store_true",
                    help="配合 --stop：只写停止标记，不杀 parallel_solver/shard/pi")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--tick", type=int, default=TICK)
    a = ap.parse_args()

    if a.stop:
        STOP_MARK.parent.mkdir(parents=True, exist_ok=True)
        STOP_MARK.write_text(time.strftime("%F %T"), encoding="utf-8")
        log(f"已写停止标记 {STOP_MARK}（守护会在下一跳退出）")
        if a.keep_children:
            log("--keep-children：不杀子进程")
            return 0
        # ★★ 2026-11-20 该轮修复：写标记**不等于停机**。
        #   原来这里只写文件就返回，于是「按脚本名 kill 掉 sdkshard」之后
        #   那些 start_new_session 拉起的 pi 全变孤儿继续烧钱（实测 $0.515/13min）。
        #   现在按「总控 → shard → 无头 pi」的顺序真正收干净：
        #   注意不能只杀父 —— sdkshard 的 pi 在独立进程组里，父一死就成孤儿，
        #   所以要等父进程落地后再扫一遍（reap_orphans 兜底）。
        for name in ("parallel_solver.py", "sdkshard.py"):
            caught = procs(name)
            for pid in caught:
                kill_tree(pid)
            if caught:
                log(f"已停 {name} {len(caught)} 个：{caught}")
        time.sleep(1)                       # 给子进程一点时间落地，再扫孤儿
        killed = reap_orphans(include_live=True)
        log(f"停机完成：清掉无头 pi {len(killed)} 个（含活父亲）" if killed
            else "停机完成：无残留无头 pi")
        return 0

    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"pid": os.getpid(), "started": time.strftime("%F %T")},
                                ensure_ascii=False), encoding="utf-8")

    if a.once:
        log(f"单次巡检 → {tick(verbose=True)}")
        return 0

    log(f"守护启动 pid={os.getpid()} tick={a.tick}s")
    while True:
        action = tick(verbose=a.verbose)
        if action == "stopped":
            return 0
        time.sleep(max(5, a.tick))


if __name__ == "__main__":
    raise SystemExit(main())
