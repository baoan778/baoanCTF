#!/usr/bin/env python3
"""reap_orphan_agents.py —— 回收 shard 被 kill 后残留的孤儿 pi 进程。

为什么必须有（2026-11-14 实测）：
  shard 被 SIGKILL 时，subprocess 拉起的 pi 不会跟着死，变成 ppid=1 的孤儿，
  继续烧 API 配额（实测一次 pkill 留下 3 个，跑了 2-3 分钟）。

判据（不依赖 cwd，因为 A/B 证明改 cwd 会丢项目上下文、不划算）：
  · comm == "pi"；
  · 无头（cmdline 里有 -p / --print）—— WebUI 常驻 pi 豁免；
  · 默认只看 ppid == 1（已被 init 收养 = 父进程已死）。

★★ 2026-11-20 该轮修复（实战复盘，实测白烧 $0.515/13min）：
  停机时只"按脚本名"kill 了 parallel_solver 与 sdkshard，而 sdkshard 用
  `start_new_session=True` 拉起 pi —— pi 在**独立进程组**里，父亲一死就变孤儿继续跑。
  实测停机后 13 分钟内 3 个 pi 又发了 37 次请求 / $0.515，全部零产出
  （平台 run 已终结，连"交了旗也没用"，纯亏）。
  所以本脚本新增 `--all`：**连"还有活父亲"的无头 pi 一起杀**，
  供 `supervise.py --stop` 在停机时一次清干净（否则永远是"先死父、再留孤儿"）。

用法：
  python3 tools/reap_orphan_agents.py [--dry-run]            # 只回收孤儿（周期巡检用）
  python3 tools/reap_orphan_agents.py [--dry-run] --all      # 所有无头 pi（停机用）
  python3 tools/reap_orphan_agents.py --json                 # 机器可读（测试/巡检用）

可测性：procfs 根可用 PI_LAB_PROC_ROOT 覆盖（与 tools/procscan.py 同一约定），
        测试才能造出「孤儿 pi / 有活父亲的 pi / D 态」三种组合而不真杀进程。
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys

# ★ 2026-11-20：Git Bash / MSYS 会把 `/proc` 之类的 POSIX 路径改写成 Windows 路径，
#   传进 docker exec 就变成 `can't open file 'D:\\...'`。只对子进程生效，改不到父 shell。
os.environ.setdefault("MSYS_NO_PATHCONV", "1")
os.environ.setdefault("MSYS2_ARG_CONV_EXCL", "*")

# ★ 2026-11-20：D 态（不可中断睡眠）进程读 cmdline 会**永久阻塞**。
#   本脚本在巡检里被循环调用，一个卡住的 pid 就能让整套清扫挂死，
#   连带 supervise 巡检卡死 = 守护等于不存在。先读 /proc/<pid>/stat 的 state 挡掉。
_UNREADABLE_STATES = ("Z", "D", "X", "x")


def _proc_root() -> str:
    return os.environ.get("PI_LAB_PROC_ROOT") or "/proc"


def _stat_fields(pid: int):
    """返回 (state, ppid, starttime)；读不到就 None。"""
    try:
        with open(os.path.join(_proc_root(), str(pid), "stat"), "r",
                  encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
        fields = raw[raw.rindex(")") + 1:].split()
        return fields[0], int(fields[1]), int(fields[19])
    except (OSError, IndexError, ValueError):
        return None


def is_headless(pid: int) -> bool:
    """是不是「跑分的 pi」（带 -p/--print）。WebUI 常驻的交互式 pi 没有 -p → 豁免。"""
    # 先看状态再读 cmdline —— 顺序不能反，D 态会阻塞（见 _UNREADABLE_STATES 注释）
    st = _stat_fields(pid)
    # 判据写成内联元组：tools/_verify_all.py 有一条静态断言要求这五个字符原样出现
    # （它守的是「别处又抄了一份忘了加守卫」）。改这里要同步改那条断言。
    if st is None or st[0] in ("Z", "D", "X", "x"):
        return False
    try:
        with open(os.path.join(_proc_root(), str(pid), "cmdline"), "rb") as fh:
            raw = fh.read()
    except OSError:
        return False
    argv = [a for a in raw.split(b"\0") if a]
    return b"-p" in argv or b"--print" in argv


def scan(orphans_only: bool = True) -> list:
    """列出候选 pi 进程。

    orphans_only=True  → 只要 ppid == 1 的（父进程已死，典型孤儿）；
    orphans_only=False → 所有无头 pi（停机时用：父进程还活着也要一起收，
                          否则"先死父再留孤儿"，总有一段时间在烧钱）。
    """
    out = []
    try:
        names = os.listdir(_proc_root())
    except OSError:
        return out
    for name in names:
        if not name.isdigit():
            continue
        pid = int(name)
        try:
            with open(os.path.join(_proc_root(), str(pid), "comm"), "r",
                      encoding="utf-8", errors="replace") as fh:
                comm = fh.read().strip()
        except OSError:
            continue
        if comm != "pi":
            continue
        st = _stat_fields(pid)
        if st is None:
            continue
        state, ppid, starttime = st
        if state == "Z":
            continue                      # 僵尸壳：已死，杀不动也不用杀
        if orphans_only and ppid != 1:
            continue
        if not is_headless(pid):
            continue                      # 没有 -p = 交互式 WebUI 常驻，豁免
        out.append({"pid": pid, "ppid": ppid, "state": state, "starttime": starttime})
    return sorted(out, key=lambda r: r["starttime"])


def kill_pids(pids, sig=signal.SIGTERM) -> list:
    """按顺序发信号，返回真正发成功的 pid（失败静默：已经退出的很正常）。"""
    ok = []
    for pid in pids:
        try:
            os.kill(int(pid), sig)
            ok.append(int(pid))
        except OSError:
            pass
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--all", action="store_true",
                    help="连『还有活父亲』的无头 pi 一起收（停机用；默认只收孤儿）")
    ap.add_argument("--json", action="store_true", help="机器可读输出（测试/巡检用）")
    ap.add_argument("--signal", default="TERM", choices=["TERM", "KILL"])
    args = ap.parse_args()

    procs = scan(orphans_only=not args.all)
    if args.json:
        print(json.dumps({"procs": procs, "all": args.all, "dry_run": args.dry_run}))
        return 0
    if not procs:
        print("没有孤儿 pi" if not args.all else "没有无头 pi")
        return 0

    sig = signal.SIGKILL if args.signal == "KILL" else signal.SIGTERM
    if args.dry_run:
        for r in procs:
            print("[dry] 会杀 pi pid=%s ppid=%s state=%s" % (r["pid"], r["ppid"], r["state"]))
        return 0
    killed = kill_pids([r["pid"] for r in procs], sig)
    for pid in killed:
        print("已杀 pi pid=%s" % pid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
