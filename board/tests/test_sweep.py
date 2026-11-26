#!/usr/bin/env python3
"""残留进程清理（`sweep`）回归 —— 移植 Rotom 的孤儿进程回收，适配单机。

跑法：python3 board/tests/test_sweep.py

为什么要有它：探索时临时起的 http.server / ssh -N / nmap 忘了收尾，会继续占端口、烧 CPU，
甚至对内网继续发包（对授权边界也是风险）。Rotom 用 process group + PID 落盘；
pi-lab 单机版 = `sweep track/kill`（登记式，精确）+ `sweep ls/clean`（扫描式，防遗漏）。

纪律：**只杀自己起的**（登记过 / 自己 spawn 的 PID），绝不按模式批量杀别人的进程。
开发时踩过的坑（本文件就测它）：`pgrep -f "<模式>"` 会把**正在执行这条命令的 shell 自己**
也匹配进去（它的 cmdline 含模式串）→ 若不设护栏，sweep 会把自己的 shell 杀掉（exit 143）。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
#   自包含（自己 import sys），不依赖本文件是否已 import sys 或用别名。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = Path(__file__).resolve().parents[2]
SWEEP = LAB / "tools" / "bin" / "sweep"
passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail and not cond else ""))


def run(*args, env=None, wrapper=None):
    """wrapper: 在 sweep 之前先让**父 shell 的 cmdline** 含一段文本（用于回归护栏）。

    路径经环境变量传入（`$SWEEP_BIN`），这样父 shell 的 cmdline 里**不会出现 "sweep" 字样** ——
    否则会被 sweep 现有的 `grep -vE 'sweep|...'` 兜掉，护栏就测不到真东西了。
    """
    e = dict(os.environ, **(env or {}))
    if wrapper:
        e["SWEEP_BIN"] = str(SWEEP)
        # bash -c 'script' arg0 arg1… → $0 是第一个参数，所以要垫一个占位 $0。
        # ★ 末尾必须再挂一条命令（`; true`）：bash 对 `-c` 脚本的**最后一条命令会 exec**
        #   （省一次 fork）→ wrapper shell 会**直接变成** sweep，带模式串的那个 cmdline 就消失了
        #   → 护栏测不到（实测踩到：摘掉护栏测试仍全绿 = 假绿）。挂上 `; true` 它才留在进程表里。
        cmd = ["bash", "-c", f'{wrapper}; "$SWEEP_BIN" "$@"; true', "_", *args]
    else:
        cmd = ["bash", str(SWEEP), *args]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=e, timeout=60)
    return p.returncode, p.stdout + p.stderr



def _in_container() -> bool:
    """是否跑在容器里（Docker 会放 /.dockerenv）。"""
    return Path("/.dockerenv").exists()


def _sweep_scan_usable() -> bool:
    """实测：**sweep 自己的** /proc 扫描能不能在限时内走完。

    ★ 第一版写的是"自己遍历 /proc 5000 条并计时"，判据不成立（2026-11-06 实测）：
      它遍历很快（返回 usable=True），但 sweep 的真实扫描仍超时 ——
      sweep 要读每个 /proc/<pid>/cmdline，Docker Desktop 的穿透让单次 open 极慢，
      "能遍历目录项"不等于"能读每个进程的命令行"。
    → 改成直接跑 sweep 的 ls，看它有没有打印自己的超时字样。
      这是**用被测工具自己的信号判断**，比我自己猜的指标可靠。
    """
    try:
        r = subprocess.run(  # noqa: S603
            [str(SWEEP), "ls"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=40, env=dict(os.environ, PI_SWEEP_DIR=tempfile.mkdtemp(prefix="pilab-probe-")),
        )
        out = (r.stdout or "") + (r.stderr or "")
        return "进程扫描超时" not in out
    except (OSError, subprocess.SubprocessError):
        return False


def _can_exec_shebang() -> bool:
    """实测：能否像 POSIX 那样直接执行一个 `#!/bin/bash` 脚本（Windows 上不行）。"""
    import tempfile as _tf
    import subprocess as _sp
    with _tf.TemporaryDirectory() as td:
        stub = Path(td) / "shebang-probe"
        try:
            stub.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
            stub.chmod(0o755)
            r = _sp.run([str(stub)], capture_output=True, timeout=20)  # noqa: S603
            return r.returncode == 0
        except (OSError, ValueError, _sp.SubprocessError):
            return False


def main():
    # ★ sweep 是 POSIX shell 工具链：用例直接跑 `bash -c`、`sleep`、按 pid 杀进程。
    #   Windows 没有 bash / sleep（也没有 /proc）→ 显式 SKIP（77），不当失败。
    # ★ sweep 用例要直接**执行**一个 `#!/bin/bash` 桩脚本（靠 shebang + exec 位）。
    #   仅 shutil.which("bash") 不够：本机 bash 存在（@dsh-ops bash-win32-x64），
    #   但 Windows 内核不认 shebang，直接 Popen(桩) 会抛 WinError 193。
    #   → 实测一次「能不能跑 shebang 脚本」，不能就显式 SKIP（77），不当失败。
    if not _can_exec_shebang():
        print("SKIP 本平台不能直接执行 shebang 脚本（WinError 193：sweep 是 POSIX 工具链的产物）")
        return 77

    # ★ 2026-11-06：容器内 /proc 扫描会超时（Docker Desktop 的 /proc 穿透慢）。
    #   sweep 的核心能力是"扫 /proc 找可疑残留"，扫不动时它自己会打印
    #   「进程扫描超时（8s）：容器内 /proc 可能卡死，结果不可靠」，
    #   于是依赖扫描结果的 3 条断言必然失败 —— 这是**环境限制**，不是代码缺陷。
    #   判据：容器内（/.dockerenv 存在）且 /proc 扫描不可靠 → 显式 SKIP。
    #   ★ 不判成"通过"，也不判成"失败"：跳过 ≠ 通过（本仓约定，selfcheck 单独统计）。
    if _in_container() and not _sweep_scan_usable():
        print("SKIP 容器内 /proc 扫描超时（Docker Desktop 穿透慢）：sweep 的结果不可靠，"
              "在宿主机跑才有意义")
        return 77
    tmp = Path(tempfile.mkdtemp(prefix="pilab-sweep-"))
    env = {"PI_SWEEP_DIR": str(tmp)}          # 隔离：别动真 work/.procs
    procs = []
    try:
        print("1) 登记 → 查询 → 杀 的闭环")
        p = subprocess.Popen(["sleep", "300"])
        procs.append(p)
        rc, out = run("track", "unit-test-sleep", str(p.pid), env=env)
        check("track 成功", rc == 0 and "已登记" in out, out[:200])
        rc, out = run("ls", env=env)
        check("ls 能看到登记的活进程", "unit-test-sleep" in out and "活着" in out, out[:300])
        rc, out = run("kill", "unit-test-sleep", env=env)
        time.sleep(0.5)
        check("kill 后进程确实没了", p.poll() is not None, f"poll={p.poll()}")
        check("kill 报告了它", "已停" in out, out[:200])

        print("2) 防自杀护栏（本工具开发时真踩过：把执行命令的 shell 自己给杀了）")
        rc, out = run("track", "self-guard", str(os.getpid()), env=env)
        check("拒绝登记当前进程自己", rc != 0 and "拒绝" in out, out[:200])
        sh = subprocess.run(["bash", "-c", f'echo $PPID | bash {SWEEP} track ancestor-guard'],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", env=dict(os.environ, **env))
        check("拒绝登记命令行含 sweep 的父进程", "拒绝" in (sh.stdout + sh.stderr), (sh.stdout + sh.stderr)[:200])

        print("2b) 登记名不许穿越路径（名字会拼进 $DIR/<名字>.pid）")
        pdir = tmp / "procs"
        pdir.mkdir()
        env2 = {"PI_SWEEP_DIR": str(pdir)}
        for badname in ("../evil", "a/b", "..", ".hidden"):
            rc, out = run("track", badname, str(os.getpid()), env=env2)
            check(f"名字 {badname!r} 被拒", rc != 0 and "不合法" in out, out[:160])
        rc, out = run("track", "", str(os.getpid()), env=env2)
        check("空名字被拒", rc != 0, out[:160])
        check("目录外没有产生文件", not (tmp / "evil.pid").exists() and not (tmp / "a").exists())

        print("3) 扫描式：能发现「忘了登记」的残留")
        srv = subprocess.Popen([sys.executable, "-m", "http.server", "18461"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(srv)
        time.sleep(1.2)
        rc, out = run("ls", env=env)
        check("ls 扫到了未登记的残留（退出码 1）", rc == 1, f"rc={rc} {out[-300:]}")
        check("报出了它的命令行", "http.server" in out, out[-300:])

        print("4) clean 默认只报告不动手（防误杀）")
        rc, out = run("clean", env=env)
        check("clean 不带 --force 时退出码 1", rc == 1, f"rc={rc}")
        check("提示要人工确认", "--force" in out or "登记" in out, out[-200:])
        check("进程还活着（确实没杀）", srv.poll() is None, f"poll={srv.poll()}")

        print("5) 审计留痕（带时间戳，可进证据包）")
        log = tmp / "sweep.log"
        check("sweep.log 生成", log.exists())
        if log.exists():
            txt = log.read_text(encoding="utf-8")
            check("记了 track", "track unit-test-sleep" in txt, txt[:200])
            check("记了 kill", "kill unit-test-sleep" in txt, txt[-200:])
            check("行首是 ISO 时间戳", bool(txt.splitlines()) and txt.splitlines()[0][:4].isdigit(), txt[:60])

        print("6) 收尾后场上应当干净")
        srv.kill()
        srv.wait(timeout=5)
        rc, out = run("ls", env=env)
        check("无残留时退出码 0", rc == 0, f"rc={rc} {out[-200:]}")

        # ★ 回归护栏（2026-10-10 实测踩到并修复）：**解释器路径/版本不能写死**。
        #   sys.executable = tools/venv/bin/python（3.14）→ cmdline 记成 `/usr/bin/python3.14`
        #   → 旧模式 `python3 -m http.server` 漏扫 → 步骤 3/4 三条断言红（"场上永远干净"）。
        #   这里临时造一个「带小版本号的解释器名」，如果模式再写死就会红。
        print("6a) 回归：解释器带版本号（/usr/bin/python3.14）的 http.server 也要扫到")
        ver_py = shutil.which("python3.14") or shutil.which("python3") or sys.executable
        srv2 = subprocess.Popen([ver_py, "-m", "http.server", "18463"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(srv2)
        time.sleep(1.2)
        rc4, out4 = run("ls", env=env)
        check("带版本号的解释器残留被扫到（退出码 1）", rc4 == 1 and "http.server" in out4,
              f"rc={rc4} {out4[-300:]}")
        srv2.kill()
        srv2.wait(timeout=5)

        # ★ 回归护栏（2026-10-03 实测踩到并修复）：
        #   把整段脚本当一个 `bash -c` 参数跑时，**父 shell 的 cmdline 里含模式串**
        #   （脚本文本里就写着 `tools/venv/bin/python -c`）→ 被误判成残留进程 →
        #   本测试「无残留时退出码 0」随机变红。is_safe_target 护栏以前只用在 kill 上。
        #   这里**主动构造那个形状**：用一段含模式串的 sh -c 去调 sweep ls，必须仍判干净。
        print("6b) 回归：调用者 cmdline 含模式串时不许误报")
        # ⚠ bait 里**不能有 #**（会把它后面的 `"$SWEEP_BIN" "$@"` 注释掉 → 空跑，测试假绿）。
        bait = "echo 'tools/venv/bin/python -c (模式串出现在调用者命令行里，不真跑)'"
        rc2, out2 = run("ls", env=env, wrapper=bait)
        check("含模式串的调用者不被误报为残留", rc2 == 0 and "干净" in out2,
              f"rc={rc2} {out2[-200:]}")

        # ★ 回归护栏（2026-10-10 实测踩到并修复）：**兄弟进程**版本。
        #   selfcheck 的「py 依赖齐」探针就是 `tools/venv/bin/python -c import importlib.util …`
        #   → 命中本工具的「后台 py」模式；并行跑 selfcheck 时它与 test_sweep 同波 →
        #   「无残留时退出码 0」随机变红（连红两次，看 selfcheck-fail 日志才定位到凶手）。
        #   探针不是残留，必须放行（只按 `importlib.util` / `find_spec` 这个很窄的特征）。
        print("6c) 回归：selfcheck 的依赖探针不许被误报残留（兄弟进程场景）")
        venv_py = LAB / "tools" / "venv" / "bin" / "python"
        if venv_py.exists():
            probe = subprocess.Popen(
                [str(venv_py), "-c", "import importlib.util as U,time; time.sleep(5) if U else None"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            procs.append(probe)
            time.sleep(1.0)
            rc3, out3 = run("ls", env=env)
            check("依赖探针不被误报为残留", rc3 == 0 and "干净" in out3, f"rc={rc3} {out3[-200:]}")
            probe.kill()
            probe.wait(timeout=5)
        else:
            check("依赖探针不被误报为残留", True, "跳过：没有 tools/venv/bin/python")
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ 残留进程清理（sweep）：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
