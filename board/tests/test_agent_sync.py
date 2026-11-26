#!/usr/bin/env python3
"""pi-agent-sync 回归：同步链路不许再「静默跳过」。

    python3 board/tests/test_agent_sync.py

背景（2026-11-02 实测）：`tools/pi-agent-sync.sh` 的 `SRC` 写死 `<repo>/.pi/config`，
**这个目录从来不存在** —— 于是 `[ -d "$SRC" ] || exit 0` 静默跳过，
`roles/` 和 `settings.json` 一直没同步过，而脚本什么都不报。
同一脚本的 `LIVE` 写死 `/root/pi-lab-agent`，非 root 用户同样不存在 → 同样静默跳过。

这份测试锁住修复后的行为：
  A. 真源自动探测到 <repo>/agent-template（存在的那个）
  B. 运行侧自动探测到 ~/.pi-ctf/agent（非 root 也能用）
  C. ★ 找不到源/靶时**必须喊出来**（stderr 有 SKIP），不许静默 exit 0
  D. ★ 显式指定了不存在的路径 → **rc=2 报错**，不许悄悄回退默认值
  E. 正常跑 rc=0，且输出里打印了实际的源和靶
  F. 探到的路径必须真实存在（防止又写死一个不存在的目录）
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "tools" / "pi-agent-sync.sh"


def _find_bash():
    """定位一个行为一致的 bash。

    ★ 2026-11-06 修：Windows 上直接 subprocess.run(["bash", ...]) 会被 PATH 里
    排第一的 shim bash 接走 —— 实测那个 shim 不传环境变量给子进程
    （$PI_LAB_AGENT_SRC 在子 shell 里恒为空），于是「显式指定路径」三条断言全假红。
    优先用 Git Bash（真的 POSIX bash），再回退 PATH 上的 bash。
    """
    cands = [
        r"C:\\Program Files\\Git\\bin\\bash.exe",
        r"C:\\Program Files\\Git\\usr\\bin\\bash.exe",
        r"C:\\Program Files (x86)\\Git\\bin\\bash.exe",
    ]
    for c in cands:
        if Path(c).is_file():
            return c
    return "bash"


BASH = _find_bash()
passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail and not cond else ""))


def run(env_extra=None, home=None):
    """跑一次同步脚本。

    ★ 2026-11-06 修：原实现传 str(SCRIPT)（Windows 绝对路径），在 Windows 宿主 +
    POSIX bash 组合下两种绝对路径写法都失败（实测 rc=127）：
      反斜杠形式 → 反斜杠被 bash 当转义符吃掉，路径变成一坨拼在一起的字符串
      as_posix 形式 → bash 不认 "D:" 盘符，报 No such file
    只有「cwd=仓库根 + 相对路径」能过（bash 自己按 /mnt/d 或 /d 挂载解析）。
    这不是脚本的缺陷 —— 容器/WSL 里绝对路径一直正常 —— 而是测试没考虑宿主差异。
    """
    env = dict(os.environ)
    env.pop("PI_LAB_AGENT_SRC", None)
    env.pop("PI_LAB_AGENT_DIR", None)
    if env_extra:
        env.update(env_extra)
    if home:
        env["HOME"] = home
    rel = SCRIPT.relative_to(ROOT).as_posix()
    p = subprocess.run([BASH, rel], capture_output=True, env=env, cwd=str(ROOT), timeout=180)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def _host_path(bash_path, bash_rc, bash_out):
    """把脚本自己打印的路径翻译成宿主上真的能 stat 的路径。

    ★ 2026-11-06 修：Windows 宿主上 Git Bash 报出来的路径是 /mnt/d/... 或 /d/...，
    而脚本是在 Git Bash 自己的根下跑的 —— 宿主 Python 根本 stat 不到 /mnt/d/baoan。
    不能因为「查不到」就判失败，得按 bash 报的那套根换算回来。
    容器 / WSL 里，bash 报的路径本来就与 Python 视角一致，原样返回即可。
    """
    if os.name != "nt":
        return Path(bash_path)
    p = str(bash_path)
    probes = []
    for m in re.finditer(r"/mnt/([a-zA-Z])/(.*)$", p):
        probes.append(Path(m.group(1).upper() + ":/" + m.group(2)))
    for m in re.finditer(r"^/([a-zA-Z])/(.*)$", p):
        probes.append(Path(m.group(1).upper() + ":/" + m.group(2)))
    probes.append(Path(p))
    for cand in probes:
        if cand.exists():
            return cand
    return probes[0]


def main() -> int:
    if not SCRIPT.exists():
        print(f"SKIP 找不到 {SCRIPT}")
        return 77

    # ★ 2026-11-06 修：在 Windows 宿主上跑时，运行侧目录只存在于容器/WSL 里，
    #   探测链必然全落空（Git Bash 报 /c/Users/.../.pi-ctf/agent 不存在）。
    #   这不是脚本缺陷，也不该判红 —— 按 _pkgpaths 的约定显式 SKIP，selfcheck
    #   会单独统计跳过，不混进「全绿」。容器内（LIVE 真实存在）会走正常路径。
    import shutil as _sh
    if _sh.which("rsync") is None:
        print()
        print("SKIP 本机没有 rsync（同步链路无法端到端验证；容器内跑才有意义）")
        return 77

    print("1) 正常跑：自动探测源和靶")
    rc, out, err = run()
    live_missing = "找不到运行侧 agent 目录" in (out + err)
    if live_missing:
        print("     （运行侧目录不在本机，自动探测链落空；此环境下跳过「打印源/靶」断言）")
        check("rc=0", rc == 0, f"rc={rc} :: {err[:200]}")
        check("★★ 落空时必须喊 SKIP（不许静默）", "SKIP" in (out + err), (out + err)[:300])
        m_src = m_live = None
    else:
        check("rc=0", rc == 0, f"rc={rc} :: {err[:200]}")
        m_src = re.search(r"\[sync\] 源: (.+)", out)
        m_live = re.search(r"\[sync\] 靶: (.+)", out)
        check("★ 打印了源路径", bool(m_src), out[:300])
        check("★ 打印了靶路径", bool(m_live), out[:300])

    print("2) ★ 探到的路径必须真实存在（防再次写死空目录）")
    if m_src:
        src = _host_path(m_src.group(1).strip(), rc, out)
        check("★★ 源目录真实存在", src.is_dir(), f"{src} 不存在")
        check("★ 源是 agent-template（不是那个不存在的 .pi/config）",
              src.name == "agent-template", f"实际 {src}")
        check("★ 源里有 roles/", (src / "roles").is_dir(), "roles 不在")
        check("★ 源里有 settings.json", (src / "settings.json").is_file(), "settings.json 不在")
    if m_live:
        live = _host_path(m_live.group(1).strip(), rc, out)
        check("★★ 靶目录真实存在", live.is_dir(), f"{live} 不存在")
        check("★ 靶不是 /root/pi-lab-agent（非 root 不该选中它）",
              str(live) != "/root/pi-lab-agent", f"实际 {live}")

    print("3) ★★ 核心：找不到靶时必须喊出来，不许静默 exit 0")
    # 用一个空的 HOME，且不给 PI_LAB_AGENT_DIR → 探测链全落空
    import tempfile
    empty_home = tempfile.mkdtemp(prefix="nohome-")
    try:
        rc2, out2, err2 = run({"PI_LAB_AGENT_DIR": ""}, home=empty_home)
        # PI_LAB_AGENT_DIR="" 会被当成空串跳过；空 HOME 让 ~/.pi-ctf/agent 也不存在
        # /root/pi-lab-agent 若存在则仍会命中 —— 那种情况下测不了这条，如实 SKIP
        if "靶:" in out2:
            print("     （/root/pi-lab-agent 存在，此环境无法构造『全落空』场景，跳过该断言）")
        else:
            check("★★ 有 SKIP 提示（不再静默）", "SKIP" in (out2 + err2), (out2 + err2)[:300])
            check("★ rc=0（跳过不是错误）", rc2 == 0, f"rc={rc2}")
    finally:
        import shutil
        shutil.rmtree(empty_home, ignore_errors=True)

    print("4) ★★ 显式指定不存在的路径 → rc=2 报错，不许回退默认值")
    rc3, out3, err3 = run({"PI_LAB_AGENT_SRC": "/nonexistent/abc"})
    check("★★ rc=2", rc3 == 2, f"rc={rc3}")
    check("★ stderr 说明原因", "不存在" in err3 or "ERROR" in err3, err3[:200])
    check("★ 没有真的跑同步", "靶:" not in out3, out3[:200])

    rc4, out4, err4 = run({"PI_LAB_AGENT_DIR": "/nonexistent/xyz"})
    check("★★ 靶路径无效也 rc=2", rc4 == 2, f"rc={rc4}")
    check("★ 同样没跑同步", "源:" not in out4 or "ERROR" in err4, (out4 + err4)[:200])

    print("5) 幂等：连跑两次第二次应报『已一致』")
    rc5, out5, _ = run()
    check("rc=0", rc5 == 0, f"rc={rc5}")
    check("★ 第二次无变更（已一致或无输出）",
          "已一致" in out5 or "仓库→真身" not in out5, out5[:300])

    print("6) 同步清单里的关键项在两边都对得上")
    if m_src is None or m_live is None:
        print("     （运行侧不在本机，无法比对两侧）")
    if m_src and m_live:
        src = _host_path(m_src.group(1).strip(), rc, out)
        live = _host_path(m_live.group(1).strip(), rc, out)
        for item in ("roles", "settings.json", "extensions"):
            s_ok = (src / item).exists()
            l_ok = (live / item).exists()
            check(f"★ {item} 两边都在", s_ok and l_ok,
                  f"src={s_ok} live={l_ok}")

    print()
    if failed:
        print(f"通过 {len(passed)} / 失败 {len(failed)}")
        print("失败项：" + "、".join(failed[:10]))
        return 1
    print(f"通过 {len(passed)} / 失败 0")
    print("✓ pi-agent-sync（同步链路）：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
