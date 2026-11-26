#!/usr/bin/env python3
"""tsec vpn 子命令回归：**任何环境都不许崩**。

跑法：
    python3 board/tests/test_tsec_vpn.py

为什么要有这个文件（2026-11-04 用户报障）
----------------------------------------
症状：「tsec vpn 命令群在容器内**必定崩溃**」。

两条互相独立的必崩路径，都跟 openvpn 本身无关：
  1. `cmd_vpn` 开头无条件 `if not shutil.which("openvpn"): die(...)` ——
     容器里没有 openvpn，于是**连 `tsec vpn status` / `down` 都跑不了**。
     一个「查状态」的命令要求 openvpn 存在，这个前提本身是错的。
  2. up 路径先用 `_sudo mkdir` 建 ~/.tsec（属主变 root），
     下一行却用普通用户身份 open(...,"wb") 往里写 → PermissionError traceback。
  3. 附带：openvpn 以 --daemon 起时错误写进 --log 文件、stderr 为空，
     原实现只贴 stderr → 用户看到一句空白的「openvpn 启动失败：」。

判据（本文件守的就是这三条）：
  · 任何子命令在任何环境下，stderr 都不许出现 Traceback
  · status / down 在「没装 openvpn」的环境里必须正常返回
  · up 失败时必须有**非空**的可执行指引

★ 测试不碰真 VPN：全程用不存在的 ovpn / 临时 HOME，绝不连任何远端。
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

LAB = Path(__file__).resolve().parents[2]
TSEC = LAB / "tools" / "tsecbench.py"

try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

passed, failed = [], []


def check(name, cond, detail=""):
    if cond:
        passed.append(name)
        print(f"  ✓ {name}")
    else:
        failed.append(name)
        print(f"  ✗ {name}   {detail}")


def run(args, home, extra_env=None):
    """跑一条 tsec 子命令。HOME 隔离 —— 绝不往真 ~/.tsec 里写东西。"""
    env = dict(os.environ)
    env["HOME"] = home
    env["TSEC_VPN_DIR"] = os.path.join(home, ".tsec")
    env["PYTHONIOENCODING"] = "utf-8"
    # ★ 2026-11-14：允许测试注入假 /proc，验证「只认本实例隧道」的归属判定，
    #   同时也不会碰真实进程（这条正是当年杀穿生产隧道的那条路径）。
    for k, v in (extra_env or {}).items():
        env[k] = v
    p = subprocess.run([sys.executable, str(TSEC), *args],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=120)
    return p.returncode, (p.stdout or "") + (p.stderr or ""), (p.stderr or "")


def main():
    print("1) status 在「没有 openvpn」的环境里也必须能跑")
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = run(["vpn", "status"], home)
        check("status 不崩（无 Traceback）", "Traceback" not in err, err[-300:])
        check("status 给出了环境结论", "环境" in out and "openvpn" in out, out[-300:])
        check("status 的返回码是布尔语义（0/1）", rc in (0, 1), f"rc={rc}")
        # ★ 2026-11-14：原断言只认「起隧道缺 / 没在跑」两种**故障**态。
        #   但本测试真正要守的是「结论明确、不许沉默」，而不是「必须坏」。
        #   本地跑分 run 47736 实测：容器里 openvpn 已装、隧道已在跑，
        #   status 输出「✅ openvpn 进程 pid=… / 隧道接口 tun0」——那是**健康**，
        #   却被这条断言判红（把「跑起来了」当成失败）。
        check("缺什么/有没有都给出明确结论",
              ("起隧道缺" in out) or ("没在跑" in out) or ("隧道接口" in out),
              out[-300:])

    print("2) down 在「没有 openvpn」的环境里也必须能跑")
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = run(["vpn", "down"], home)
        check("down 不崩（无 Traceback）", "Traceback" not in err, err[-300:])
        check("down 给了一句人话", ("已停" in out) or ("没在跑" in out), out[-200:])
        check("down 返回 0", rc == 0, f"rc={rc}")

    print("3) up 失败时必须是「可执行的指引」，不是空白错误")
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = run(["vpn", "up", "--file", "/nonexistent/nope.ovpn"], home)
        check("up 不崩（无 Traceback）", "Traceback" not in err, err[-300:])
        check("up 非零退出", rc != 0, f"rc={rc}")
        # 不管是「缺 openvpn」还是「ovpn 文件不存在」，都必须说清楚下一步
        check("错误信息非空且可执行",
              any(k in out for k in ("openvpn", "ovpn 文件不存在", "起不了隧道")),
              out[-400:])

    print("4) vpn 子命令解析：不给子命令默认走 status")
    with tempfile.TemporaryDirectory() as home:
        rc, out, err = run(["vpn"], home)
        check("无参不崩", "Traceback" not in err, err[-300:])
        check("无参给出 status 面板", "环境" in out, out[-300:])

    print("5) 日志超过 16KB 后 status 仍必须可用（2026-11-14 实测回归）")
    with tempfile.TemporaryDirectory() as home:
        tsec_dir = Path(home) / ".tsec"
        tsec_dir.mkdir(parents=True, exist_ok=True)
        log = tsec_dir / "vpn.log"
        filler = "".join(f"line {i} padding padding padding\n" for i in range(900))
        log.write_text(filler + "TAIL-MARKER-OK\n", encoding="utf-8")
        rc, out, err = run(["vpn", "status"], home)
        check("超长日志下 status 不崩", "Traceback" not in err, err[-300:])
        check("超长日志下仍给出尾部内容", "TAIL-MARKER-OK" in out, out[-300:])

    print("6) 【致命】跑测试不许杀掉别的实例正在用的隧道（2026-11-14 三次血案）")
    with tempfile.TemporaryDirectory() as home:
        # 造一个「别人的」openvpn：comm=openvpn，但 cmdline 指向另一个配置目录
        fake = Path(home) / "proc"
        (fake / "900").mkdir(parents=True)
        (fake / "900" / "comm").write_text("openvpn\n", encoding="utf-8")
        (fake / "900" / "stat").write_text(
            "900 (openvpn) S 1 900 900 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 0 0 0\n", encoding="utf-8")
        (fake / "900" / "cmdline").write_bytes(
            b"openvpn\x00--config\x00/root/tsec/task.ovpn\x00")
        rc, out, err = run(["vpn", "down"], home,
                           extra_env={"PI_LAB_PROC_ROOT": str(fake)})
        check("down 不把别的实例的隧道当自己的", "没在跑" in out, out[-300:])
        check("down 不因此崩", "Traceback" not in err, err[-300:])

    print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ tsec vpn 回归：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
