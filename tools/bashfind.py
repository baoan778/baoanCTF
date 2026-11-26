#!/usr/bin/env python3
"""bashfind.py -- 找到一个**真能从 Python 里起来**的 bash（零第三方依赖）

为什么要有它（2026-11-23 宿主实测 + 归因，一次坑踩了三个地方）
------------------------------------------------------------
**现象**：Python 里 `subprocess.run(["bash", ...])` 恒失败（rc=1，stderr 是
UTF-16 的「拒绝访问。」），而**同一个 shell 里敲 bash 完全正常** —— 于是
lint_sh 把 267 个脚本全判成语法错误、bench_local 判分恒假红，看着像脚本坏了。

**真根因（不是 shim，是 Windows 的检索顺序）**：
  CreateProcess 找可执行文件时，**System32 排在 PATH 之前**。
  这台机器的 C:/Windows/System32/bash.exe 是 **WSL 的 bash 存根**，
  而 WSL 起不来 → 返回「拒绝访问。」。
  裸名 bash 正好命中它；写**完整路径**则一条都没失败过。
  在 shell 里敲 bash 走的是 PATH（Git Bash / MSYS 在前），所以看不出问题。

**实测判据**（全部本机跑过）：

    subprocess.run(["bash", "-c", "echo ok"])                        -> rc=1  拒绝访问
    subprocess.run(["C:/Program Files/Git/bin/bash.exe", "-c", ...]) -> rc=0  ok
    where bash 列表最后一项 C:/Windows/System32/bash.exe             -> rc=1  拒绝访问（同一个错）

⇒ 结论：**Python 里起 bash 一律给完整路径**。这就是本模块存在的理由。

受害面（都是「工具自己坏了却看着像目标/脚本的错」）：
  · tools/lint_sh.py    -> 把**每一个** shell 脚本判成语法错误（267/267 全红）
  · tools/bench_local.py-> 判分恒假红（verify.sh 明明 exit 0）
  · 未来任何 `subprocess.run(["bash", ...])`

所以：**凡是 Python 里要起 bash，一律走这里**，别写裸名。

用法
----
    from bashfind import BASH          # 模块级解析一次
    subprocess.run([BASH, "-n", name], ...)

环境变量 PI_LAB_BASH 可覆盖（CI / 容器 / 想测另一份 bash 时）。
"""
from __future__ import annotations

import os
import subprocess
import sys

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_WIN = os.name == "nt"


def _works(path: str) -> bool:
    """真的**按真实用法**跑一下才算 —— 文件存在、甚至 --version 能跑，都不算数。

    ★ 2026-11-23 实测：那个 shim 的 `--version` 返回 rc=0（能打印版本），
      但 `-c 'echo ok'` 返回 rc=1「拒绝访问。」。所以探针必须是 -c 真执行，
      用 --version 探会把坏的当好的 —— 这正是我第一次写错的地方。
    """
    if not path or not os.path.isfile(path):
        return False
    try:
        p = subprocess.run([path, "-c", "echo PI_LAB_BASH_OK"],
                           capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return p.returncode == 0 and b"PI_LAB_BASH_OK" in (p.stdout or b"")


def candidates():
    """按优先级给候选。先认环境变量，再认 which，最后是 Windows 上的 MSYS 原生位置。"""
    env = os.environ.get("PI_LAB_BASH")
    if env:
        yield env
    which = None
    try:
        from shutil import which as _which
        which = _which("bash")
    except Exception:
        which = None
    if which:
        yield which
    if _WIN:
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                     os.environ.get("ProgramW6432")):
            if not base:
                continue
            for sub in (("Git", "bin"), ("Git", "usr", "bin")):
                yield os.path.join(base, *sub, "bash.exe")
        for c in (r"C:\Program Files\Git\bin\bash.exe",
                  r"C:\Program Files\Git\usr\bin\bash.exe",
                  r"C:\Program Files (x86)\Git\bin\bash.exe"):
            yield c
        # WSL 的 bash（最后兜底；它不接受 Windows 路径，调用方要自己换算）
        yield r"C:\Windows\System32\bash.exe"
    else:
        for c in ("/bin/bash", "/usr/bin/bash", "/usr/local/bin/bash"):
            yield c


def find_bash(fallback: str = "bash") -> str:
    """返回第一个**实测能跑**的 bash 路径；全都不行才退回给定名字。

    ★ 取舍：优先用 PATH 上那个（本机是 MSYS 5.3.20），因为它是**更严格的解析器** ——
      实测它比 Git Bash 5.3.15 多拦住一种坏写法（内层双引号里同时出现
      [ ] ( ) ' 时会被截断）。用它做体检，等于用更严的尺子量：在这个 bash 下能过，
      换到 Git Bash / 容器 bash 也一定过。
      （MUST NOT 反过来择优 —— 先挑宽松的那个会让检查漏掉真问题。）
    """
    seen = set()
    for c in candidates():
        if not c or c in seen:
            continue
        seen.add(c)
        if _works(c):
            return c
    # 一个都不行：退回裸名，并由调用方兜住「起不来」的情况（skip 而不是判红）
    return fallback


BASH = find_bash()


def is_access_denied(err: bytes) -> bool:
    """起不来（拒绝访问）与「脚本自己语法错」是两件事 —— 前者必须 skip，不许判红。"""
    if not err:
        return False
    for enc in ("utf-16-le", "utf-8", "gbk"):
        try:
            msg = err.decode(enc, "ignore")
        except Exception:
            continue
        if "拒绝访问" in msg or "Access is denied" in msg or "access denied" in msg.lower():
            return True
    return False


if __name__ == "__main__":
    print("BASH =", BASH)
    print("candidates:")
    for c in candidates():
        print("  %-60s %s" % (c, "OK" if _works(c) else "-"))