#!/usr/bin/env python3
"""lane_gate.py 回归测试 —— 治「长任务前台跑 / 管道吞输出 / 裸 & 中继被杀」的事前闸门。

与 test_lane_guard.py 的分工：
  · test_lane_guard → **事后**看门狗（NO_NOTES / STUCK / OVER_BOX / ORPHAN）
  · test_lane_gate  → **事前**闸门（本文件）：命令跑之前判，拒了必须给改写模板

本测试守四件事：
  1. 该拒的真拒，且**每条 deny 都得带能直接粘的改写模板**（给正路，不只给禁令）
  2. 该放的必须放 —— 误伤比漏报致命（会把 `tail -5 log` 这种好习惯一起禁掉）
  3. 读外部路径走限长护栏（`/dev/zero`、FIFO、超大文件不许读）
  4. CLI 契约：exit 0 放行 / exit 10 拒绝 / `--json` 可解析

    python3 board/tests/test_lane_gate.py
"""
from __future__ import annotations

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

LAB = Path(__file__).resolve().parents[2]
PY = sys.executable
GATE = LAB / "tools" / "lane_gate.py"

checks: list[tuple[str, bool, str]] = []


def ok(name: str, cond: bool, extra: str = "") -> None:
    checks.append((name, bool(cond), extra))


def run(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(GATE), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


# ── 1. 判定：该拒 ────────────────────────────────────────────────────────────
def test_deny() -> None:
    cases = [
        ("R1 爆破管道接 tail", "hydra -l root -P rockyou.txt ssh://10.0.0.1 | tail -20"),
        ("R2 前台 hydra", "hydra -l root -P rockyou.txt ssh://10.0.0.1"),
        ("R2 前台 masscan", "masscan -p1-65535 10.0.0.1 --rate 10000"),
        ("R2 长 timeout", "timeout 300 python3 brute.py"),
        ("R3 宽窗口 grep -o", "grep -o -E '.{0,80}flag.{0,80}' big.txt"),
        ("R4 裸 grep", "grep 'flag'"),
        ("R5 裸 socat 中继", "socat TCP-LISTEN:2222,reuseaddr,fork TCP:10.0.0.2:22"),
        ("R6 rm -rf /", "rm -rf /"),
        ("R7 /24 扫描", "nmap 10.0.0.0/24"),
    ]
    for name, cmd in cases:
        p = run("check", cmd)
        ok(f"{name} → exit 10", p.returncode == 10, f"rc={p.returncode}")
        ok(f"{name} → 带改写模板", "改写" in p.stdout and len(p.stdout) > 60, p.stdout[:120])
        ok(f"{name} → 说明理由", "拒绝" in p.stdout)


# ── 2. 判定：该放（防误伤） ──────────────────────────────────────────────────
def test_allow() -> None:
    cases = [
        ("只看日志", "tail -5 /tmp/x.log"),
        ("带限长的宽窗口 grep", "grep -o -m 20 -E '.{0,80}flag' big.txt"),
        ("窄窗口 grep", "grep -o -E '.{0,20}flag' a.txt"),
        ("有文件的 grep", "grep flag a.txt"),
        ("后台爆破", "tmux new-session -d -s h 'hydra -l r -P p.txt ssh://10.0.0.1'"),
        ("setsid 中继", "setsid nohup socat TCP-LISTEN:2222,reuseaddr,fork TCP:10.0.0.2:22"),
        ("短 timeout", "timeout 20 curl -s http://10.0.0.1/"),
        ("单机 nmap", "nmap -sT -p80,443 10.0.0.1"),
        ("--help", "hydra --help"),
        ("/30 小段", "nmap 10.0.0.0/30"),
        ("空命令", ""),
    ]
    for name, cmd in cases:
        p = run("check", cmd)
        ok(f"放行 {name}", p.returncode == 0, f"rc={p.returncode} {p.stdout[:100]}")


# ── 3. 单机 /32 不算「网段越界」（但前台 masscan 仍按 R2 拒） ────────────────
def test_cidr_boundary() -> None:
    p = run("check", "masscan 10.0.0.1/32 -p1-100", "--json")
    if p.returncode == 10:
        rule = json.loads(p.stdout)["rule"]
        ok("/32 不被判成 R7 网段越界", rule != "R7", f"rule={rule}")
    else:
        ok("/32 放行（也可接受）", True)
    # /25 起是允许的段
    p2 = run("check", "nmap -sT 10.0.0.0/28 -p80")
    ok("/28 不被判成 R7", (p2.returncode == 0)
       or (p2.returncode == 10 and json.loads(p2.stdout)["rule"] != "R7"))


# ── 4. --json 契约 ──────────────────────────────────────────────────────────
def test_json() -> None:
    p = run("check", "rm -rf /", "--json")
    try:
        d = json.loads(p.stdout)
    except Exception as e:      # noqa: BLE001
        ok("--json 可解析", False, str(e))
        return
    ok("--json 可解析", True)
    ok("--json 含 decision/rule/why/fix", all(k in d for k in ("decision", "rule", "why", "fix")))


# ── 5. R1 的改写模板必须**去掉** `| tail`（否则等于没改） ────────────────────
def test_r1_fix_strips_pipe() -> None:
    p = run("check", "hydra -l r -P p.txt ssh://1.2.3.4 | tail -20")
    ok("R1 改写里不再带 `| tail`", "| tail" not in p.stdout, p.stdout[:200])
    ok("R1 改写里给了 tmux 正路", "tmux new-session" in p.stdout)


# ── 6. 读取护栏：/dev/zero、FIFO、超大文件都要拒 ────────────────────────────
def test_read_guard() -> None:
    # ★ /dev/zero 只在 POSIX 存在；Windows 上 "audit /dev/zero" 会被规范化成
    #   \dev\zero 然后报「待审文件不存在」——那是「文件不存在」不是「拒绝读特殊文件」，
    #   断言会假红。显式跳过（跳过 ≠ 通过）。
    if os.path.exists("/dev/zero"):
        p = run("audit", "/dev/zero", timeout=30)
        ok("/dev/zero 被拒（不吃内存）", p.returncode != 0 and "不是普通文件" in (p.stdout + p.stderr),
           (p.stdout + p.stderr)[:120])
    else:
        print("  SKIP /dev/zero 被拒（本平台无 /dev/zero）")

    tmp = Path(tempfile.mkdtemp(prefix="lane-gate-t-"))
    try:
        fifo = tmp / "f.fifo"
        if not hasattr(os, "mkfifo"):
            print("  SKIP FIFO 被拒（本平台无 os.mkfifo）")
        else:
            os.mkfifo(fifo)
            p = run("audit", str(fifo), timeout=30)
            ok("FIFO 被拒", p.returncode != 0 and "不是普通文件" in (p.stdout + p.stderr))

        big = tmp / "big.txt"
        big.write_text("x" * 2048, encoding="utf-8")
        env = dict(os.environ, PI_LANE_GATE_MAX_READ_BYTES="512")
        p = subprocess.run([PY, str(GATE), "audit", str(big)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=30)
        ok("超限文件被拒（上限可配）", p.returncode != 0 and "太大" in (p.stdout + p.stderr),
           (p.stdout + p.stderr)[:120])

        empty = tmp / "empty.txt"
        empty.write_text("", encoding="utf-8")
        p = run("audit", str(empty))
        ok("空文件不崩", p.returncode == 0 and "没有命中" in p.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── 7. audit 只报行号+类别，不贴原文（防二次污染上下文） ────────────────────
def test_audit() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="lane-gate-a-"))
    try:
        f = tmp / "lane.log"
        f.write_text(
            "hydra -l a -P p ssh://1.2.3.4 | tail -5\n"
            "nmap 10.0.0.0/24\n"
            "tail -5 /tmp/x.log\n",
            encoding="utf-8",
        )
        p = run("audit", str(f))
        ok("audit 命中 2 处", "2 处命中" in p.stdout, p.stdout[:120])
        ok("audit 报行号", "L1" in p.stdout and "L2" in p.stdout)
        ok("audit 不贴违规原文", "ssh://1.2.3.4" not in p.stdout, p.stdout[:200])
        ok("audit 不误伤干净的 tail", "L3" not in p.stdout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── 8. bg 正路：真能后台跑 + 落盘 + 重复起不重启 ────────────────────────────
def test_bg() -> None:
    if not shutil.which("tmux"):
        ok("bg（无 tmux 时跳过）", True)
        return
    name = f"lanegate_test_{os.getpid()}"
    log = f"/tmp/{name}.log"
    try:
        # ★ 2026-11-02 修偶发红（实测 5 次跑 2 次红）：
        #   原来跑 `for i in 1 2 3; do echo tick $i; sleep 1; done`（约 3 秒自然结束），
        #   下面 sleep 2.5 秒后判「同名第二次不重启」—— 机器稍慢那 3 秒就跑完了，
        #   tmux 会话已消失，"已经在跑" 自然不成立。这不是被测行为错，是**测试自己的时序太窄**。
        #   改成早先 echo 一次 tick（满足"落盘断言"）然后**长时间不退出**（sleep 300），
        #   让「同名仍在跑」这件事与机器速度解耦。kill-session 在 finally 里照旧收掉。
        # ★ 2026-11-02 修偶发红（实测 5 次跑 2 次红）：
        #   原来跑 `for i in 1 2 3; do echo tick $i; sleep 1; done`（约 3 秒自然结束），
        #   下面 sleep 2.5 秒后判「同名第二次不重启」—— 机器稍慢那 3 秒就跑完了，
        #   tmux 会话已消失，"已经在跑" 自然不成立。这不是被测行为错，是**测试自己的时序太窄**。
        #   改成**每轮都 echo** 的长循环：既保证日志里有 tick（落盘断言），
        #   又保证判「同名仍在跑」时进程一定还活着 —— 两个断言都不再和机器速度赛跑。
        p = run("bg", name, "for i in $(seq 1 300); do echo tick $i; sleep 1; done")
        ok("bg 起成功并报路径", p.returncode == 0 and log in p.stdout, p.stdout[:200])
        import time
        time.sleep(2.0)
        ok("bg 真的把输出落盘了", Path(log).exists() and "tick" in Path(log).read_text(encoding="utf-8", errors="replace"),
           Path(log).read_text(encoding="utf-8", errors="replace")[:100] if Path(log).exists() else "(无日志)")
        p2 = run("bg", name, "echo other")
        ok("同名第二次不重启（就地续力）", "已经在跑" in p2.stdout, p2.stdout[:200])
    finally:
        subprocess.run(["tmux", "kill-session", "-t", name],
                       capture_output=True, timeout=20)
        if Path(log).exists():
            Path(log).unlink()


def main() -> int:
    for fn in (test_deny, test_allow, test_cidr_boundary, test_json, test_r1_fix_strips_pipe,
               test_read_guard, test_audit, test_bg):
        try:
            fn()
        except Exception as e:      # noqa: BLE001
            ok(f"{fn.__name__} 抛异常", False, f"{type(e).__name__}: {e}")

    passed = sum(1 for _, c, _ in checks if c)
    for name, c, extra in checks:
        if not c:
            print(f"  ✗ {name}" + (f"   [{extra}]" if extra else ""))
    print(f"\n{passed}/{len(checks)} 断言通过"
          + ("（只列失败项）" if passed == len(checks) else ""))
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
