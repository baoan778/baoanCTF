#!/usr/bin/env python3
"""procscan.py —— 唯一的「进程识别」实现。

## 为什么要有这个文件

同一条判据「某脚本在不在跑」在仓库里有三份不同实现，而且**三份都是错的**，
错法还不一样（全部为 2026-11-20 实测）：

1. tools/supervise.py 原 `procs(pattern)`：`ps -eo pid,args` 再 `if pattern in args`。
   实测诱饵 `bash -c 'tail -F log/shard.log sdkshard.py; sleep 15'` **会被判成 shard 活着**
   → 它决定「要不要重启 shard」，误判成活着就**永远不重启**（比漏判更糟）。
2. tools/solver-autostart.sh 原 `pgrep -f`：连执行这条命令的 shell 自己都算（已修，改 argv）。
3. tools/bench/live.py 的 `procs()`：同样子串匹配 → ①同一类误报；
   **②更严重：它按 `/dist/bundle/cli.js` 找 pi 子进程，而 pi 启动后会把 argv 重写成
   `["pi"]`（Node 的 process.title 行为），`ps` 里根本没有 cli.js** → 实况页的
   「pi 子进程」永远是 0/—，用户看起来就像「没在干活」。

## 判据（都基于 /proc，不解析 ps 文本）

· python 脚本：`argv[0]` 的 basename 以 `python` 开头，**且** `argv[1]` 的 basename == 目标脚本。
  只认真正被解释器执行的那个脚本；命令行里「提到」不算。
· pi 子进程：`/proc/<pid>/comm` == `pi`（进程名），**且**祖先链里能碰到一个 python 脚本
  （默认 sdkshard.py）。必须看祖先链 —— 容器里还有一个常驻的 UI pi（父进程是
  `node work/ui/server.mjs`），它跟解题无关，不能算进「谁在跑」。
"""
from __future__ import annotations

import os

PI_COMM = "pi"


def _root() -> str:
    """procfs 根。**只为可测性**：测试用假 /proc 造出「D 态 + 有 cmdline」的组合，
    才能验证状态守卫真的在拦（真 D 态没法按需制造，僵尸的 cmdline 又本来就是空）。
    生产路径不设这两个环境变量，行为与写死 /proc 完全一致。"""
    return os.environ.get("PI_LAB_PROC_ROOT") or "/proc"


# ★★ 2026-11-20 实测到的致命坑：读 **D 状态**进程的 /proc/<pid>/cmdline 会**永久阻塞**。
#   容器里出现过一批 D 态残留（wchan=__access_remote_vm / __vma_start_write /
#   proc_map_files_readdir，起不来的 ps/pgrep/find），从容器启动那一秒就挂在那里。
#   于是：
#     python3 tools/procscan.py      → 超时 124（本文件最早版本就是这么挂的）
#     /api/bench                     → {"error":"live.py 超时（15s）"}，实况页看起来「空」
#     tools/supervise.py 巡检         → 卡死，守护等于不存在
#   而 **/proc/<pid>/stat 永远不会阻塞**（它只读 task 结构体）—— 先看 state，
#   跳过 Z(zombie)/D(不可中断睡眠)/X(已死) 的 pid，再决定要不要读 cmdline。
#   代价：一个真在跑 python 脚本的进程不可能长期处于 D 态（D 态意味着它在等 IO、
#   根本执行不了），所以跳过它不会漏判。
_UNREADABLE_STATES = ("Z", "D", "X", "x")


def _read_state(pid: int) -> str:
    """进程状态字母（R/S/D/Z/…）。读 /proc/<pid>/stat，**不会阻塞**。"""
    try:
        with open(os.path.join(_root(), str(pid), "stat"), "r", encoding="utf-8", errors="replace") as fh:
            data = fh.read()
    except OSError:
        return ""
    try:
        return data[data.rindex(")") + 1:].split()[0]
    except (ValueError, IndexError):
        return ""


def _read_argv(pid: int) -> list[str]:
    # 先看状态：D/Z 态的 cmdline 读下去会挂死（见上面那段实测）。
    if _read_state(pid) in _UNREADABLE_STATES:
        return []
    try:
        with open(os.path.join(_root(), str(pid), "cmdline"), "rb") as fh:
            raw = fh.read()
    except OSError:
        return []
    return [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]


def _read_comm(pid: int) -> str:
    try:
        with open(os.path.join(_root(), str(pid), "comm"), "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _read_ppid(pid: int) -> int:
    try:
        with open(os.path.join(_root(), str(pid), "stat"), "r", encoding="utf-8", errors="replace") as fh:
            data = fh.read()
    except OSError:
        return 0
    # comm 里可能带空格/括号，从最后一个 ')' 之后切才安全
    try:
        rest = data[data.rindex(")") + 1:].split()
        return int(rest[1])
    except (ValueError, IndexError):
        return 0


def _read_etime(pid: int) -> int:
    """进程已运行秒数（近似：uptime - starttime/HZ）。取不到返回 0。"""
    try:
        with open(os.path.join(_root(), str(pid), "stat"), "r", encoding="utf-8", errors="replace") as fh:
            data = fh.read()
        start = int(data[data.rindex(")") + 1:].split()[19])
        with open(os.path.join(_root(), "uptime"), "r", encoding="utf-8") as fh:
            up = float(fh.read().split()[0])
        hz = os.sysconf("SC_CLK_TCK") or 100
        return max(0, int(up - start / hz))
    except Exception:  # noqa: BLE001
        return 0


def pids() -> list[int]:
    try:
        return [int(d) for d in os.listdir(_root()) if d.isdigit()]
    except OSError:
        return []


def python_script_pids(script_basename: str) -> list[int]:
    """真正由 python 解释器执行的、argv[1] basename 等于目标脚本的进程。"""
    out = []
    for pid in pids():
        argv = _read_argv(pid)
        if len(argv) < 2:
            continue
        if not os.path.basename(argv[0]).startswith("python"):
            continue
        if os.path.basename(argv[1]) != script_basename:
            continue
        out.append(pid)
    return out


def _ancestors(pid: int, limit: int = 12) -> list[int]:
    chain, cur = [], pid
    for _ in range(limit):
        cur = _read_ppid(cur)
        if cur <= 1 or cur in chain:
            break
        chain.append(cur)
    return chain


def pi_agents(anchor_script: str = "sdkshard.py") -> list[dict]:
    """解题用的 pi 子进程（排除 UI 那个常驻 pi）。

    判据：comm == 'pi' 且祖先链里有一个 python 进程的 argv[1] 是 anchor_script。
    """
    anchors = set(python_script_pids(anchor_script))
    out = []
    for pid in pids():
        if _read_comm(pid) != PI_COMM:
            continue
        anc = _ancestors(pid)
        if not (set(anc) & anchors):
            continue
        out.append({"pid": pid, "etime": _read_etime(pid), "pid_chain": anc[:3]})
    return out


def snapshot(anchor_script: str = "sdkshard.py") -> dict:
    """一次扫完，供 UI/守护共用。"""
    shards = python_script_pids("sdkshard.py")
    ctrl = python_script_pids("parallel_solver.py")
    sup = python_script_pids("supervise.py")
    return {
        "shards": [{"pid": p, "etime": _read_etime(p)} for p in shards],
        "controller": ({"pid": ctrl[0], "etime": _read_etime(ctrl[0])} if ctrl else None),
        "supervisor": ({"pid": sup[0], "etime": _read_etime(sup[0])} if sup else None),
        "agents": len(pi_agents(anchor_script)),
        "agent_pids": [a["pid"] for a in pi_agents(anchor_script)],
    }


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--script":
        _hits = python_script_pids(sys.argv[2])
        print(" ".join(str(p) for p in _hits))
        # ★ 退出码就是判据（shell 里直接 `if python3 procscan.py --script x; then`）。
        #   早一版无条件 exit 0，结果 solver-autostart.sh 每次都打「supervise.py 已在运行」
        #   —— 和它当年踩的 pgrep -f 是**同一个后果**：以为活着，永远不拉起。
        #   空结果必须 exit 1。
        raise SystemExit(0 if _hits else 1)
    print(json.dumps(snapshot(), ensure_ascii=False, indent=2))
