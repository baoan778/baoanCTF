#!/usr/bin/env python3
"""内存闸门回归测试（2026-10-01 WSL E_UNEXPECTED 事故的护栏）。

事故链条：比赛编排「12 条 lane 想开就开」→ 8 个并发 git 在 `/mnt/d` 上各吃 ~900MB
（合计 5.7GB）→ 8GB WSL VM global OOM → Ubuntu 发行版卡死成 `Wsl/Service/E_UNEXPECTED`
→ pi 跑一半没了，且诊断命令自己也在坏掉的那一环上。

所以两处都要判据化：
  1. `radar.py` —— 检查点看得见（内存行 + 低内存禁止开新 lane + 重 git 同屏提交告警）
  2. `slot.sh`  —— 真正的闸门（拿名额前拒，退出码 3；不许和「等不到名额」的 1 混用）

保险丝（全部临时目录，不碰真产物）：
  * `PI_LAB_MEMINFO_PATH` / `PI_LAB_PROC_ROOT` —— 隔离内存与进程读数
  * `PI_LAB_SLOT_DIR` —— 隔离名额锁，绝不碰真的 `work/tsecbench-run/slots`

    python3 board/tests/test_mem_gate.py
"""
from __future__ import annotations
import sys

import importlib.util
import os
import pathlib
import shutil
import subprocess
import tempfile

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
#   自包含（自己 import sys），不依赖本文件是否已 import sys 或用别名。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = pathlib.Path(__file__).resolve().parents[2]
RADAR = LAB / "work" / "tsecbench-run" / "radar.py"
SLOT = LAB / "work" / "tsecbench-run" / "slot.sh"
REAL_SLOTS = LAB / "work" / "tsecbench-run" / "slots"
PAYLOAD_SLOT = LAB / "work" / "hosted-agent" / "ctx" / "payload" / "tsecbench-run" / "slot.sh"

MEM_OK = ("MemTotal:       10485760 kB\n"
          "MemAvailable:    8388608 kB\n"
          "SwapTotal:      16777216 kB\n"
          "SwapFree:       16777216 kB\n")
MEM_LOW = ("MemTotal:       10485760 kB\n"
           "MemAvailable:     512000 kB\n"
           "SwapTotal:      16777216 kB\n"
           "SwapFree:         200000 kB\n")

checks: list = []


def check(name, cond, extra=""):
    checks.append((name, bool(cond), str(extra)))


def load_radar():
    spec = importlib.util.spec_from_file_location("radar_memgate", RADAR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sh_path(p) -> str:
    """把本机路径转成 shell 能认的形式。

    ★ 跨平台真 bug（2026-11-01）：slot.sh 由 bash 执行，而 Windows 侧的 bash 是 MSYS。
      MSYS 把 `PI_LAB_SLOT_DIR=C:\\Users\\...\\slots` 当成**相对路径**（C: 被当成盘符前缀
      拼到当前目录下），于是 mkdir 成功、owner 写到了别处，测试再从原路径读就 FileNotFound。
      → 传进 shell 的路径统一走本函数：POSIX 原样返回，Windows 转成 /c/Users/... 形式。
    """
    s = str(p)
    if os.name == "posix":
        return s
    # C:\Users\x → /c/Users/x （MSYS/Cygwin 风格）
    drive, rest = os.path.splitdrive(os.path.abspath(s))
    rest = rest.replace("\\", "/")
    if drive.endswith(":"):
        return "/" + drive[0].lower() + rest
    return rest


def _bash_isolates() -> bool:
    """这台 bash 收不收得到我们传的隔离变量？

    ★ 跨平台真 bug（2026-11-01，实测定性）：本机 `bash` 解析到 WSL 的 /usr/bin/bash
      （uname = WSL2 Linux）。Windows 进程给 subprocess 的 env **不会**自动映射进 WSL
      （WSLENV 未设时被隔断）——实测 env={"PI_LAB_SLOT_DIR":...} 到 bash 里是空串。
      后果不是「测试红」这么轻：slot.sh 的 ${PI_LAB_SLOT_DIR:-默认} 会回落到**真实**的
      work/tsecbench-run/slots，于是测试把名额写进真产物（已实测污染 slot1/slot2 并清理）。
      → 隔离不当场验证生效就绝不执行，直接 SKIP（77）。
    """
    probe_var = "PI_LAB_ISOLATION_PROBE"
    env = dict(os.environ)
    env[probe_var] = "ok-probe"
    r = subprocess.run(["bash", "-c", f'printf "%s" "${{{probe_var}:-}}"'],
                       capture_output=True, env=env, timeout=30)
    got = (r.stdout or b"").decode("utf-8", "replace").strip()
    return got == "ok-probe"


def run_slot(slot_dir, meminfo, *args, env_extra=None):
    env = dict(os.environ)
    env["PI_LAB_SLOT_DIR"] = str(slot_dir)
    env["PI_LAB_MEMINFO_PATH"] = str(meminfo)
    env.pop("PI_LAB_MEM_GATE", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(["bash", str(SLOT), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=60, env=env)


def real_slots_snapshot():
    if not REAL_SLOTS.exists():
        return {}
    return {p.name: (p / "owner").read_text(encoding="utf-8", errors="replace").strip()
            for p in sorted(REAL_SLOTS.iterdir()) if (p / "owner").exists()}


def main() -> int:
    # ★ 2026-10-30：radar.py 是**运维脚本**（work/tsecbench-run/，本机跑分平台用），
    #   发布包（浏览器工作台版）不包含它。缺产物时显式 SKIP（退出码 77），
    #   否则 236 行测试全部报 FileNotFoundError，看着像包坏了。
    if not RADAR.is_file():
        print(f"SKIP 发布包不含运维脚本 radar.py（{RADAR}）")
        return 77
    # ★ 隔离门（2026-11-01 实测）：本机 bash 是 WSL 的 /usr/bin/bash，Windows 进程给的
    #   env 穿不过去（实测为空）→ slot.sh 的 ${PI_LAB_SLOT_DIR:-默认} 会回落写**真实**
    #   work/tsecbench-run/slots（已实测污染 slot1/slot2 并清理）。隔离当场验证不通过就
    #   一步都不跑，直接 SKIP（77）——宁可不测，也不能改真产物。
    if not _bash_isolates():
        print("SKIP 本机 bash 收不到隔离变量（WSL env 隔断）——"
              "跑下去会把名额写进真实 work/tsecbench-run/slots")
        return 77
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="memgate-test-"))
    radar = load_radar()
    real_before = real_slots_snapshot()
    try:
        fake_mem = tmp / "meminfo"
        fake_mem.write_text(MEM_OK, encoding="utf-8")
        missing_mem = tmp / "no-such-meminfo"

        # ── A. radar：解析与曲线 ──────────────────────────────────────────────
        m = radar.meminfo(str(fake_mem))
        check("解析 MemAvailable（kB→MB）", m.get("MemAvailable") == 8192, m)
        check("解析 SwapFree（kB→MB）", m.get("SwapFree") == 16384, m)
        check("meminfo 不存在 → 返回 {}（不抛）", radar.meminfo(str(missing_mem)) == {})
        junk = tmp / "junk-meminfo"
        junk.write_text("MemAvailable: abc\n", encoding="utf-8")
        check("meminfo 坏内容 → 返回 {}（不抛）", radar.meminfo(str(junk)) == {})
        check("曲线 8192MB → 6 条", radar.lane_mem_cap(8192) == 6)
        check("曲线 4096MB → 6 条（边界含）", radar.lane_mem_cap(4096) == 6)
        check("曲线 3200MB → 4 条", radar.lane_mem_cap(3200) == 4)
        check("曲线 2500MB → 2 条", radar.lane_mem_cap(2500) == 2)
        check("曲线 1500MB → 1 条", radar.lane_mem_cap(1500) == 1)
        check("读不到内存时按「不知道」处理（6 条，不是 0）", radar.lane_mem_cap(None) == 6)
        check("开新 lane 阈值＝2048MB（复盘 §5 的数字）", radar.MEM_SPAWN_MIN_MB == 2048)

        # ── B. radar：重进程统计（假 /proc，不碰真进程表）─────────────────────
        fake_proc = tmp / "proc"
        heavy = fake_proc / "111"
        heavy.mkdir(parents=True)
        (heavy / "comm").write_text("git\n", encoding="utf-8")
        (heavy / "statm").write_text("900000 200000 0 0 0 0 0\n", encoding="utf-8")   # 200000 页 ≈ 781MB
        small = fake_proc / "222"
        small.mkdir()
        (small / "comm").write_text("git\n", encoding="utf-8")
        (small / "statm").write_text("100 10 0 0 0 0 0\n", encoding="utf-8")          # 40KB → 不算重
        other = fake_proc / "333"
        other.mkdir()
        (other / "comm").write_text("bash\n", encoding="utf-8")                        # 不在名单里
        (other / "statm").write_text("900000 300000 0 0 0 0 0\n", encoding="utf-8")
        hp = radar.heavy_procs(str(fake_proc))
        check("只数名单内且 ≥128MB 的进程", hp["by_name"] == {"git": 1}, hp)
        check("RSS 换算正确（≈781MB）", hp["heavy"] and 770 <= hp["heavy"][0][1] <= 790,
              hp["heavy"])
        check("proc root 不存在 → 空结果不抛",
              radar.heavy_procs(str(tmp / "no-such-proc")) == {"heavy": [], "by_name": {}})

        # ── C. radar：build_report 真的出告警（假 meminfo/proc 驱动）──────────
        ws = tmp / "ws"
        (ws / "logs").mkdir(parents=True)
        for i, name in enumerate(("laneA", "laneB")):
            (ws / "logs" / f"{name}.md").write_text(f"# lane {name[-1]} —— a-0{i + 1}\n正文\n", encoding="utf-8")
        os.environ["PI_LAB_MEMINFO_PATH"] = str(fake_mem)
        os.environ["PI_LAB_PROC_ROOT"] = str(fake_proc)
        try:
            lines, warns = radar.build_report(ws, 30, 15, {"remaining_min": 300})
            check("内存行进报告", any(l.startswith("内存") for l in lines), lines[:6])
            check("重进程行进报告", any(l.startswith("重进程") for l in lines), lines[:6])
            check("内存充足 → 无闸门告警", not any("内存闸门" in w for w in warns))
            check("单个重 git → 不报同屏提交", not any("同屏提交" in w for w in warns))

            fake_mem.write_text(MEM_LOW, encoding="utf-8")
            _, warns_low = radar.build_report(ws, 30, 15, {"remaining_min": 300})
            check("低内存 → 禁止开新 lane", any("禁止开新 lane" in w for w in warns_low), warns_low)
            check("低内存 → 点名关哪条（2 活跃 > 上限 1）",
                  any("内存闸门" in w and "lane" in w for w in warns_low), warns_low)
            check("swap 见底 → 告警", any("swap 只剩" in w for w in warns_low), warns_low)

            fake_mem.write_text(MEM_OK, encoding="utf-8")
            _, warns_ok = radar.build_report(ws, 30, 15, {"remaining_min": 300})
            check("内存恢复 → 闸门告警消失", not any("内存闸门" in w for w in warns_ok))

            for pid in ("444", "555"):
                (fake_proc / pid).mkdir()
                (fake_proc / pid / "comm").write_text("git\n", encoding="utf-8")
                (fake_proc / pid / "statm").write_text("900000 150000 0 0 0 0 0\n", encoding="utf-8")
            _, warns_git = radar.build_report(ws, 30, 15, {"remaining_min": 300})
            check("3 个重 git 同屏 → 告警别同时提交", any("同屏提交" in w for w in warns_git),
                  warns_git)

            os.environ["PI_LAB_MEMINFO_PATH"] = str(missing_mem)
            lines_nm, warns_nm = radar.build_report(ws, 30, 15, {"remaining_min": 300})
            check("读不到内存 → 报告里明说闸门失效",
                  any("读不到" in l for l in lines_nm), lines_nm[:6])
            check("读不到内存 → 不假装知道（无闸门告警）",
                  not any("内存闸门" in w for w in warns_nm))
            os.environ["PI_LAB_MEMINFO_PATH"] = str(fake_mem)
        finally:
            os.environ.pop("PI_LAB_MEMINFO_PATH", None)
            os.environ.pop("PI_LAB_PROC_ROOT", None)

        # ── D. slot.sh：真正的闸门（名额目录已被隔离）────────────────────────
        slot_dir = tmp / "slots"
        r = run_slot(slot_dir, fake_mem, "acquire", "T1", "3")
        check("内存充足 → 拿到 slot1", r.returncode == 0 and r.stdout.strip() == "slot1",
              f"rc={r.returncode} out={r.stdout!r} err={r.stderr[:120]}")
        check("名额写在隔离目录里",
              (slot_dir / "slot1" / "owner").read_text(encoding="utf-8", errors="replace").strip() == "T1")

        # ★ 回归（2026-10-06）：timeout=0 也必须**真的试一次**。
        #   原写法 `while [ $(date +%s) -lt $deadline ]` 在高负载下两次 date 之间就超时
        #   → 一次都没试就报 NO_SLOT（假「名额已满」）。自检并行 4 路时实测到，单独跑复现不了。
        zero_dir = tmp / "slots-zero"
        r = run_slot(zero_dir, fake_mem, "acquire", "T0", "0")
        check("timeout=0 也要真试一次（不空转报 NO_SLOT）",
              r.returncode == 0 and r.stdout.strip() == "slot1",
              f"rc={r.returncode} out={r.stdout!r} err={r.stderr[:120]}")

        fake_mem.write_text(MEM_LOW, encoding="utf-8")
        r = run_slot(slot_dir, fake_mem, "acquire", "T2", "1")
        check("低内存 → acquire 退出码 3（拒占名额）", r.returncode == 3,
              f"rc={r.returncode} err={r.stderr[:160]}")
        check("拒绝时给的是人话（LOW_MEM + 复盘出处）",
              "LOW_MEM" in r.stderr and "E_UNEXPECTED" in r.stderr, r.stderr[:160])
        check("低内存时不新增名额目录", not (slot_dir / "slot2").exists())

        r = run_slot(slot_dir, fake_mem, "mem")
        check("slot.sh mem → BLOCK + 退出码 3",
              r.returncode == 3 and "VERDICT=BLOCK" in r.stdout, r.stdout.strip())

        r = run_slot(slot_dir, fake_mem, "acquire", "T3", "1",
                     env_extra={"PI_LAB_MEM_GATE": "off"})
        check("PI_LAB_MEM_GATE=off 是应急逃生口",
              r.returncode == 0 and r.stdout.strip() == "slot2", f"rc={r.returncode}")

        fake_mem.write_text(MEM_OK, encoding="utf-8")
        r = run_slot(slot_dir, fake_mem, "mem")
        check("内存恢复 → VERDICT=OK", r.returncode == 0 and "VERDICT=OK" in r.stdout)

        r = run_slot(slot_dir, fake_mem, "acquire", "T4", "2")
        check("第三个名额仍能正常拿到", r.returncode == 0 and r.stdout.strip() == "slot3",
              f"rc={r.returncode} out={r.stdout!r}")
        r = run_slot(slot_dir, fake_mem, "acquire", "T5", "1")
        check("名额拿满 → 退出码 1（不与内存拒绝的 3 混用）", r.returncode == 1,
              f"rc={r.returncode}")

        # 读不到 meminfo = 不拦（拿不到数字就不假装知道是低内存）
        run_slot(slot_dir, fake_mem, "release", "T3")
        r = run_slot(slot_dir, missing_mem, "acquire", "T6", "1")
        check("读不到 meminfo 时不误拦", r.returncode == 0 and "LOW_MEM" not in r.stderr,
              f"rc={r.returncode} out={r.stdout!r} err={r.stderr[:120]}")

        r = run_slot(slot_dir, fake_mem, "release", "T1")
        check("release 语义没被改掉", r.returncode == 0, r.stderr[:120])

        # ── E. 不碰真产物 + 两个副本一致 ─────────────────────────────────────
        check("测试全程没碰真的 slots 目录", real_slots_snapshot() == real_before,
              f"{real_before} → {real_slots_snapshot()}")
        if PAYLOAD_SLOT.exists():
            check("payload 副本与源一致（含内存闸门）",
                  PAYLOAD_SLOT.read_text(encoding="utf-8") == SLOT.read_text(encoding="utf-8"),
                  "跑 work/hosted-agent/build-and-package.sh 重新打包")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for _, c, _ in checks if c)
    for name, c, extra in checks:
        print(f"  {'✓' if c else '✗'} {name}{('  ' + extra) if extra and not c else ''}")
    print(f"\n{passed}/{len(checks)} 断言通过")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
