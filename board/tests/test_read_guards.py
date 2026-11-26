#!/usr/bin/env python3
"""读外部路径的护栏（`read_capped`）回归 —— 治的是「读爆 VM」这一级事故。

跑法：    python3 board/tests/test_read_guards.py

为什么要有它
------------
2026-09-29 一晚上三次 **VM 级事故**：`fgs verify ... --from <怪路径>` 直接把
内存吃到 6.5~7.2G，8G 的 WSL VM global OOM，`python3` 被 oom-kill，
**整台发行版连 pi 一起被 SIGKILL**（所以症状是"pi 用着一半突然没了"）。

凶手就是这句：`src = p.read_text(encoding="utf-8", errors="replace")` —— 外面给的路径没验类型、没限长。
喂 `/dev/zero` 是无限长，喂目录/FIFO 是行为未定义，喂 10G 的盘就是自寻 OOM。

对抗性自检（故意喂怪路径）本身是对的 —— 所以护栏必须落在工具里，且判据是
「**快速拒绝 + 说清原因**」，而不是"希望调用方别这么喂"。

本测试不碰真产物（`PI_LAB_BOARD_DIR` 指向临时目录），也不制造大文件
（用 `PI_FGS_MAX_READ_BYTES` 把上限调小来测「太大」这一支）。
"""
import os
import shutil
import subprocess
import sys
import tempfile
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

BOARD = Path(__file__).resolve().parents[1]
FGS = BOARD / "fgs.py"
passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail and not cond else ""))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="pilab-readguard-"))
    PID = "rg"
    base_env = dict(os.environ, PI_LAB_BOARD_DIR=str(tmp / "projects"))

    def fgs(*args, env=None, timeout=30):
        p = subprocess.run([sys.executable, str(FGS), *args],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=env or base_env, timeout=timeout)
        return p.returncode, p.stdout, p.stderr

    try:
        fgs("init", PID, "--kind", "ctf", "--goal", "x")
        fgs("fact-add", PID, "flag{Real_One}", "--confidence", "verified")

        print("1) 证据是目录 → 快速拒绝（不能去读它）")
        adir = tmp / "adir"
        adir.mkdir()
        rc, out, err = fgs("verify", PID, "f001", "--from", str(adir))
        check("退出码非 0", rc != 0, f"rc={rc}")
        check("说清「是目录，不是文件」", "目录" in out + err, (out + err)[:200])

        print("2) 证据是 /dev/zero → 快速拒绝（这是那晚 OOM 的形态）")
        if Path("/dev/zero").exists():
            rc, out, err = fgs("verify", PID, "f001", "--from", "/dev/zero", timeout=20)
            check("退出码非 0", rc != 0, f"rc={rc}")
            check("说清「不是普通文件」", "不是普通文件" in out + err, (out + err)[:200])
        else:
            check("本机无 /dev/zero（跳过）", True)

        print("3) 证据是 FIFO → 快速拒绝（读它会挂死）")
        fifo = tmp / "ev.fifo"
        if not hasattr(os, "mkfifo"):
            print("  SKIP 本平台无 os.mkfifo（Windows），FIFO 用例跳过")
        else:
            os.mkfifo(fifo)
            rc, out, err = fgs("verify", PID, "f001", "--from", str(fifo), timeout=20)
            check("退出码非 0", rc != 0, f"rc={rc}")
            check("说清「不是普通文件」", "不是普通文件" in out + err, (out + err)[:200])

        print("4) 证据超过上限 → 拒绝并给出调法（用 1KB 上限测）")
        big = tmp / "big.txt"
        big.write_text("flag{Real_One}\n" + "x" * 4000, encoding="utf-8")
        env_small = dict(base_env, PI_FGS_MAX_READ_BYTES="1024")
        rc, out, err = fgs("verify", PID, "f001", "--from", str(big), env=env_small)
        check("退出码非 0", rc != 0, f"rc={rc}")
        check("说清「太大」并给出上限", "太大" in out + err and "上限" in out + err, (out + err)[:200])
        check("给出调大的环境变量名", "PI_FGS_MAX_READ_BYTES" in out + err, (out + err)[:200])

        print("4b) 普通文件但内容是二进制 → 不崩（errors=replace 读进来，只是核验失败）")
        binfile = tmp / "blob.bin"
        binfile.write_bytes(bytes(range(256)) * 40)
        rc, out, err = fgs("verify", PID, "f001", "--from", str(binfile))
        check("二进制普通文件 → 优雅失败非 0（不 Traceback）",
              rc != 0 and "Traceback" not in out + err, (out + err)[:200])

        print("5) 没护栏时最该保住的：正常路径照样能盖章")
        ok_file = tmp / "real.txt"
        ok_file.write_text("shell> cat /flag\nflag{Real_One}\n", encoding="utf-8")
        rc, out, err = fgs("verify", PID, "f001", "--from", str(ok_file))
        check("小文件 → 退出码 0", rc == 0, (out + err)[:200])
        check("盖上 verified_by=verbatim", "verbatim" in out, out[:200])

        print("6) 管道输入仍然可用（且加了下限保护）")
        p = subprocess.run([sys.executable, str(FGS), "verify", PID, "f001", "--from", str(ok_file)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=base_env)
        p2 = subprocess.run([sys.executable, str(FGS), "verify", PID, "f001"],
                            input="flag{Real_One}\n", capture_output=True, text=True, encoding="utf-8", errors="replace", env=base_env)
        check("--text/stdin 路径没被改坏", p2.returncode == 0, (p2.stdout + p2.stderr)[:200])

        print("7) 同类入口一起上护栏：--op-file / --targets-file")
        rc, out, err = fgs("publish", PID, "--by", "tester", "--op-file", str(adir))
        check("publish --op-file 是目录 → 拒绝", rc != 0 and "目录" in out + err, (out + err)[:200])
        rc, out, err = fgs("swarm", PID, "--from", "f001", "--targets", "a.com",
                           "--targets-file", "/dev/zero", timeout=20)
        if Path("/dev/zero").exists():
            check("swarm --targets-file /dev/zero → 拒绝",
                  rc != 0 and ("不是普通文件" in out + err or "目录" in out + err), (out + err)[:200])
        else:
            # 本平台无 /dev/zero（Windows）→ 这条护栏形态构造不出来，显式跳过。
            print("  SKIP swarm --targets-file /dev/zero（本平台无 /dev/zero）")

        print("8) 拒绝必须是「快」的（那晚是 30 秒才 OOM）")
        import time
        if not Path("/dev/zero").exists():
            print("  SKIP /dev/zero 2 秒内被拒（本平台无 /dev/zero）")
        else:
            t0 = time.time()
            fgs("verify", PID, "f001", "--from", "/dev/zero", timeout=20)
            check(f"/dev/zero 在 2 秒内被拒（实测 {time.time() - t0:.2f}s）", time.time() - t0 < 2.0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ 读外部路径护栏：全部通过（/dev/zero / FIFO / 目录 / 超大 都被快速拒）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
