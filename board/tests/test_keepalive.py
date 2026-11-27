#!/usr/bin/env python3
"""回归测试：启动器保活层（崩溃自动重启 + --continue 续跑 + 冷却闸门）

跑法：
    python3 board/tests/test_keepalive.py

为什么要有它
------------
2026-09-29 起 WSL 会从外部把整台发行版拆掉（`init.scope: Stopping timed out. Killing.`
→ pi 吃 SIGKILL = 退出码 137）。那一晚的主会话就是**死在半截 toolCall 上**，
窗口只剩一句「请按任意键继续」，现场全丢（后来靠 `sesscan where` 才看出是悬空工具调用）。

保活层是照 2026 第二轮调研 §六 T1 落的：把「人自己收工」和「被外部打死」分开 ——
前者不拉起（0 / 129 / 130 / 1），后者退避后带 `--continue` 续跑（137 / 134 / 143…），
并且**同一 60s 窗口连崩 3 次就停下**，否则一个必崩的环境会被重启成死循环。

这个文件守七件事（用桩 `pi` 驱动，不碰真 pi、不动真 log/、不动真会话目录）：
  T1 崩一次 → 第二次调用带 --continue，最后由保活层把退出码收在 0
  T2 普通错误（rc=1，如参数/密钥错）不重启 —— 重试无用，别刷屏
  T3 连崩 3 次 → 冷却闸门停手（2 条 restart + 1 条 stop/cooldown）
  T4 人按 Ctrl+C（rc=130）不重启
  T5 调用方钉住会话（--session）时不叠加 --continue，且 restarts.jsonl 记 pinned
  T6 退避是递增的（1s → 2s），不是固定间隔
  T7 PI_LAB_KEEPALIVE=0 时退化成「只跑一次」，退出码原样透传

  T8（2026-11-02 补，实测坐实的静默故障）启动器**必须**导出 PI_LAB_ROOT 与
     PI_CODING_AGENT_DIR。缺 PI_CODING_AGENT_DIR → pi 落到默认 ~/.pi/agent，
     那目录没有 extensions/，**14 个自定义扩展一个都不加载**（实测 `pi config`
     的 "User (" 段直接为 0）；缺 PI_LAB_ROOT → 扩展靠 `<LAB>/tools/*.mjs`
     干活，import 全失败走 fail-open，**闸门静默变哑弹**。
     两者叠加 = 从桌面/家目录启动时「要拦截却什么都没拦，且一声不响」。
     本组用桩 pi 把真实收到的环境变量打出来，锁死这两条不回归。
"""
import json
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

ROOT = Path(__file__).resolve().parents[2]
# ★ 2026-10-30：run-pi.sh 是**终端启动器**，发布包（浏览器工作台版）不包含它
#   —— 包里的启动入口是 start.sh / 启动UI.bat。缺产物时显式 SKIP（退出码 77），
#   不能一声不响地红：那是"测试把包判死"，不是包有缺陷。
RUN_PI = ROOT / "run-pi.sh"

passed: list[str] = []
failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        passed.append(name)
    else:
        failed.append(name)
        print(f"  ✗ {name}  {detail[:400]}")


def _quota_probe(tmp: Path, tag: str, text: str) -> Path:
    """造一个「命中额度墙」的标记文件（内容含限流/余额特征）。"""
    p = tmp / f"{tag}-quota.txt"
    p.write_text(text + "\n", encoding="utf-8")
    return p


def _stale_event(tmp: Path, tag: str, minutes: int) -> Path:
    """造一个 N 分钟没动过的事件源（mtime 往前拨），用来触发卡死判据。"""
    import time as _t
    p = tmp / f"{tag}-event.jsonl"
    p.write_text('{"text":"working"}\n', encoding="utf-8")
    past = _t.time() - minutes * 60
    os.utime(p, (past, past))
    return p


def _slow_pi(tmp: Path, tag: str, seconds: int) -> Path:
    """桩 pi：**活够 N 秒再干净退出**。

    为什么 T10 必须用它而不是喂 137：卡死判的是「进程**还活着**但没产出」。
    用会崩的桩测卡死，测的是崩溃重启，根本不是同一条路径（第一版就这么写错，
    T10 红得有理）。这里让进程活着，才能证明「卡死只报警、不重启」。
    """
    p = tmp / f"{tag}-slowpi"
    p.write_text(f"#!/bin/bash\nsleep {seconds}\nexit 0\n", encoding="utf-8")
    p.chmod(0o755)
    return p


def make_stub(tmp: Path) -> Path:
    """桩 pi：把收到的参数追加进 STUB_CALLS，然后按 STUB_SEQ 依次吐退出码。"""
    stub = tmp / "pi-stub"
    stub.write_text(
        "#!/bin/bash\n"
        'printf \'%s\\n\' "$*" >> "$STUB_CALLS"\n'
        'n=$(wc -l < "$STUB_CALLS")\n'
        'IFS=\',\' read -r -a rcs <<< "$STUB_SEQ"\n'
        'idx=$((n - 1))\n'
        '[ "$idx" -ge "${#rcs[@]}" ] && idx=$(( ${#rcs[@]} - 1 ))\n'
        'echo "stub-pi: call $n rc=${rcs[$idx]}" >&2\n'
        'exit "${rcs[$idx]}"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return stub


def run_launcher(tmp: Path, seq: str, extra_args=(), extra_env=None, tag: str = "t"):
    """在隔离环境里跑一次启动器。返回 (rc, stdout+stderr, 每次调用参数列表, restarts.jsonl 行)"""
    work = tmp / tag
    logdir = work / "log"
    calls = work / "calls.txt"
    logdir.mkdir(parents=True, exist_ok=True)
    calls.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env.update({
        "PI_LAB_PI_BIN": str(make_stub(tmp)),
        "PI_LAB_LOG_DIR": str(logdir),
        "PI_LAB_SESSIONS": str(work / "sessions"),
        "PI_LAB_KEEPALIVE_DELAY": "0",          # 测试里不真等
        "STUB_CALLS": str(calls),
        "STUB_SEQ": seq,
    })
    env.pop("PI_LAB_KEEPALIVE", None)
    env.update(extra_env or {})
    p = subprocess.run(["bash", str(RUN_PI), *extra_args], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=env, timeout=120)
    out = (p.stdout or "") + (p.stderr or "")
    call_list = [ln.strip() for ln in calls.read_text(encoding="utf-8").splitlines() if ln.strip()]
    rlog = logdir / "pi-restarts.jsonl"
    restarts = []
    if rlog.exists():
        for ln in rlog.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                restarts.append(json.loads(ln))
    return p.returncode, out, call_list, restarts



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


def main() -> int:
    # ★ 2026-10-30：发布包（浏览器工作台版）不带 run-pi.sh（那是**终端**启动器，
    #   包里对应入口是 start.sh）。缺这个产物时显式 SKIP（退出码 77），
    #   由 selfcheck 单列成"跳过"，不当成失败 —— 否则买家一跑回归就是 22 条红。
    if not RUN_PI.is_file():
        print(f"SKIP 发布包不含终端启动器 run-pi.sh（{RUN_PI}）—— 保活层是终端模式的产物")
        return 77
    # ★ 保活层是本机 bash 启动器：桩 pi 是 `#!/bin/bash` 脚本，靠 exec 位 + $STUB_* 环境变量。
    #   Windows 没有 bash、没有 exec 位 → 这套用例构造不出来，显式 SKIP（77），不当失败。
    # ★ 保活层要直接**执行**一个 `#!/bin/bash` 桩脚本（靠 shebang + exec 位）。
    #   仅 shutil.which("bash") 不够：本机 bash 存在（@dsh-ops bash-win32-x64），
    #   但 Windows 内核不认 shebang，直接 Popen(桩) 会抛 WinError 193。
    #   → 实测一次「能不能跑 shebang 脚本」，不能就显式 SKIP（77），不当失败。
    if not _can_exec_shebang():
        print("SKIP 本平台不能直接执行 shebang 脚本（WinError 193：保活层是 POSIX 启动器的产物）")
        return 77
    tmp = Path(tempfile.mkdtemp(prefix="pilab-keepalive-"))
    try:
        # ── T1 崩一次 → 带 --continue 续跑 ──────────────────────────────────
        rc, out, calls, restarts = run_launcher(tmp, "137,0", tag="t1")
        check("T1 崩一次后共 2 次调用", len(calls) == 2, f"calls={calls}")
        check("T1 首次不带 --continue", calls and "--continue" not in calls[0], str(calls[:1]))
        check("T1 重启带 --continue", len(calls) > 1 and "--continue" in calls[1], str(calls))
        check("T1 restarts.jsonl 记 1 条 restart",
              len(restarts) == 1 and restarts[0]["action"] == "restart", str(restarts))
        check("T1 restart 记录原退出码 137",
              bool(restarts) and restarts[0]["rc"] == 137, str(restarts[:1]))
        check("T1 保活层把退出码收在最后一次（0）", rc == 0, f"rc={rc}")
        check("T1 日志里留下重启横幅（人能看到发生过什么）", "崩了" in out, out[-300:])

        # ── T2 普通错误不重启 ──────────────────────────────────────────────
        rc, out, calls, restarts = run_launcher(tmp, "1", tag="t2")
        check("T2 普通错误 rc=1 只跑 1 次", len(calls) == 1, f"calls={calls}")
        check("T2 不写 restart 记录", restarts == [], str(restarts))
        check("T2 退出码原样是 1", rc == 1, f"rc={rc}")

        # ── T3 冷却闸门：连崩 3 次停手 ─────────────────────────────────────
        rc, out, calls, restarts = run_launcher(
            tmp, "137", tag="t3", extra_env={"PI_LAB_KEEPALIVE_MAX": "3"})
        check("T3 连崩 3 次后停手（共 3 次调用）", len(calls) == 3, f"calls={len(calls)}")
        acts = [r["action"] for r in restarts]
        check("T3 记录顺序 restart,restart,stop", acts == ["restart", "restart", "stop"], str(acts))
        check("T3 stop 的原因是 cooldown",
              restarts and restarts[-1].get("reason") == "cooldown", str(restarts[-1:]))
        check("T3 冷却时明确告诉人怎么接着跑", "bash run-pi.sh --continue" in out, out[-400:])
        check("T3 退出码是崩溃码 137", rc == 137, f"rc={rc}")

        # ── T4 Ctrl+C 不重启 ──────────────────────────────────────────────
        rc, out, calls, restarts = run_launcher(tmp, "130", tag="t4")
        check("T4 rc=130（Ctrl+C）只跑 1 次", len(calls) == 1, f"calls={len(calls)}")
        check("T4 不写 restart 记录", restarts == [], str(restarts))
        check("T4 退出码原样是 130", rc == 130, f"rc={rc}")

        # ── T5 钉住会话：不叠加 --continue ────────────────────────────────
        rc, out, calls, restarts = run_launcher(
            tmp, "137,0", tag="t5", extra_args=("--session", "deadbeef"[:8]))
        check("T5 --session 被原样透传", len(calls) > 1 and "--session deadbeef" in calls[1], str(calls))
        check("T5 钉住会话时不叠加 --continue", len(calls) > 1 and "--continue" not in calls[1], str(calls))
        check("T5 restarts.jsonl 记 pinned=true",
              bool(restarts) and restarts[0].get("pinned") is True, str(restarts[:1]))

        # ── T6 退避递增 ───────────────────────────────────────────────────
        rc, out, calls, restarts = run_launcher(
            tmp, "137", tag="t6",
            extra_env={"PI_LAB_KEEPALIVE_MAX": "3", "PI_LAB_KEEPALIVE_DELAY": "1"})
        delays = [r.get("delay") for r in restarts if r["action"] == "restart"]
        check("T6 退避是 1s → 2s（不是固定间隔）", delays == [1, 2], str(delays))

        # ── T7 关掉保活 ───────────────────────────────────────────────────
        rc, out, calls, restarts = run_launcher(
            tmp, "137", tag="t7", extra_env={"PI_LAB_KEEPALIVE": "0"})
        check("T7 关闭保活只跑 1 次", len(calls) == 1, f"calls={len(calls)}")
        check("T7 关闭保活不写 restart 记录", restarts == [], str(restarts))
        check("T7 退出码仍原样透传（137）", rc == 137, f"rc={rc}")

        # ── T8 扩展生效的两个前置环境变量（2026-11-02 静默故障回归）───────────
        # 病根：run-pi.sh 曾既不导 PI_LAB_ROOT，也不导 PI_CODING_AGENT_DIR。
        #   · 缺 AGENT_DIR → pi 用默认 ~/.pi/agent（无 extensions/）→ 扩展 0 加载
        #   · 缺 LAB       → 扩展 import <LAB>/tools/*.mjs 全失败 → fail-open 哑弹
        # 这里从「非仓库目录」启动，确保不是靠 cwd 蒙对的。
        probe = tmp / "t8probe.sh"
        probe.write_text(
            "#!/bin/bash\n"
            'echo "LAB=$PI_LAB_ROOT"\n'
            'echo "AGENT=$PI_CODING_AGENT_DIR"\n'
            "exit 0\n",
            encoding="utf-8",
        )
        probe.chmod(0o755)
        probe_work = tmp / "t8"
        probe_work.mkdir(parents=True, exist_ok=True)
        env8 = dict(os.environ)
        env8.update({
            "PI_LAB_PI_BIN": str(probe),
            "PI_LAB_LOG_DIR": str(probe_work / "log"),
            "PI_LAB_SESSIONS": str(probe_work / "sessions"),
            "PI_LAB_KEEPALIVE": "0",
        })
        env8.pop("PI_LAB_ROOT", None)          # 模拟「从桌面/家目录启动」的真实处境
        env8.pop("PI_CODING_AGENT_DIR", None)
        p8 = subprocess.run(["bash", str(RUN_PI)], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", env=env8,
                            cwd=str(probe_work), timeout=120)
        out8 = (p8.stdout or "") + (p8.stderr or "")
        lab8 = next((ln.split("=", 1)[1] for ln in out8.splitlines() if ln.startswith("LAB=")), "")
        ag8 = next((ln.split("=", 1)[1] for ln in out8.splitlines() if ln.startswith("AGENT=")), "")
        want_root = str(RUN_PI.parent)
        check("T8 导出 PI_LAB_ROOT（缺了闸门变哑弹）", lab8.rstrip("/\\") == want_root,
              f"LAB={lab8!r} want={want_root!r}")
        check("T8 导出 PI_CODING_AGENT_DIR（缺了扩展 0 加载）",
              ag8.endswith(".pi-ctf/agent") or ag8.endswith(".pi-ctf\\agent"),
              f"AGENT={ag8!r}")
        check("T8 从非仓库 cwd 启动也不影响（不靠 cwd 蒙对）",
              lab8.rstrip("/\\") == want_root, f"cwd={probe_work}")

        # ── T9–T12 该轮修复：守护从 1 类扩到 3 类（2026-11-03）──────────────────
        # 三类：进程死（原有）/ 卡死不产出（只报警）/ 额度墙（停手）。
        # 优先级：额度墙 > 进程死 > 卡死。这几条错了的代价是**烧钱**（额度墙被当崩溃
        # 无限重启）或**杀掉还在深挖的会话**（卡死被当崩溃），所以逐条锁死。
        print("9) 该轮修复 三类守护")

        # T9 额度墙：崩溃退出 + 会话里有 429 → 必须停手，一次都不许重启
        rc9, out9, _c9, r9 = run_launcher(tmp, "137", tag="t9",
            extra_env={"PI_LAB_KEEPALIVE_MAX": "5",
                       "PI_LAB_KEEPALIVE_DELAY": "0",
                       "PI_LAB_QUOTA_FILE": str(_quota_probe(tmp, "t9", "HTTP 429 Too Many Requests"))})
        check("T9 额度墙停手（不自动拉起）", "疑似额度墙" in out9, out9[-300:])
        check("T9 ★ 额度墙一次都不重启（重启会继续烧钱）", r9 == [] or
              all(r.get("action") == "stop" for r in r9), f"restarts={r9}")

        # T10 卡死：进程**还活着**但事件源 20 分钟没动 → 只报警，不重启不 kill。
        # ★ 桩必须是「活一段时间后干净退出」的，不能用 137（那测的是崩溃重启，另一条路径）。
        rc10, out10, _c10, r10 = run_launcher(tmp, "", tag="t10",
            extra_env={"PI_LAB_PI_BIN": str(_slow_pi(tmp, "t10", 12)),
                       "PI_LAB_STALL_MIN": "10", "PI_LAB_STALL_POLL": "1",
                       "PI_LAB_EVENT_FILE": str(_stale_event(tmp, "t10", 20))})
        check("T10 卡死报警（进程活着但没产出）", "疑似卡死" in out10, out10[-400:])
        check("T10 ★ 卡死不重启（do NOTHING 取舍）", r10 == [], f"restarts={r10}")
        check("T10 报警里写明「不重启也不 kill」", "不重启也不 kill" in out10, out10[-500:])

        # T11 优先级：额度 + 崩溃同时命中 → 走额度墙，不走崩溃重启
        rc11, out11, _c11, r11 = run_launcher(tmp, "137", tag="t11",
            extra_env={"PI_LAB_KEEPALIVE_MAX": "5", "PI_LAB_KEEPALIVE_DELAY": "0",
                       "PI_LAB_QUOTA_FILE": str(_quota_probe(tmp, "t11", "insufficient balance"))})
        check("T11 ★ 优先级：额度墙压过崩溃", "疑似额度墙" in out11 and
              all(r.get("action") == "stop" for r in r11), f"restarts={r11} {out11[-200:]}")

        # T12 不误伤：没有额度特征时，崩溃仍按原样重启
        rc12, out12, _c12, r12 = run_launcher(tmp, "137", tag="t12",
            extra_env={"PI_LAB_KEEPALIVE_MAX": "2", "PI_LAB_KEEPALIVE_DELAY": "0",
                       "PI_LAB_QUOTA_FILE": str(tmp / "t12-no-quota")})
        check("T12 无额度特征时不误判额度墙", "疑似额度墙" not in out12, out12[-300:])
        check("T12 崩溃仍正常重启（原行为不变）",
              any(r.get("action") == "restart" for r in r12), f"restarts={r12}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ 启动器保活层（续跑 / 冷却 / 收工判据 / 会话钉住）全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
