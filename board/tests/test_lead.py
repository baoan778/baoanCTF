#!/usr/bin/env python3
"""回归测试：调度者身份 / 首灯检查（ADR 0034 → pi-lab 映射）

跑法：
    python3 board/tests/test_lead.py

守的是什么：**多窗口双 dispatcher**。CyberPenda ADR 0034 记的实盘事故 ——
orchestrator 死了、spawn 消息没送达、孤儿会话看到一堆工作目录就自封第二个 orchestrator，
两条调度线并行，预算失效，最后 100 分钟零产出。

pi-lab 的映射只借两件事：`lead.json` 单 epoch 心跳 + 首灯身份检查。
  T1 无主 → next 自动接管（单窗口零摩擦）
  T2 自己人 → 续期心跳（不换 owner）
  T3 别人当值 → next **停手** exit 2（不派活、不给任务）
  T4 同上但 --json（脚本分支要能读到 wait）
  T5 --force 才能强行接管
  T6 心跳过期 → 别的会话可自动接管（死了的 lead 不该占着位置）
  T7 release → 回到无主，别人可接管
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
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

ROOT = Path(__file__).resolve().parents[1]
FGS = ROOT / "fgs.py"
PID = "_test_lead"

# ★ 隔离：临时项目别建到真 board/projects。
# 2026-09-29 踩过：6 个测试文件都在真目录里建项目，4 路并行时把 test_handover 的
# 计数打歪 → 偶发红，还被误当成 drvfs 负载抖动记进文档。
TMPDIR = Path(tempfile.mkdtemp(prefix="pilab-lead-"))
PDIR = TMPDIR / "projects" / PID
ENV = dict(os.environ, PI_LAB_BOARD_DIR=str(TMPDIR / "projects"))

passed, failed = [], []


def run(*args, env=None):
    p = subprocess.run([sys.executable, str(FGS), *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env={**ENV, **(env or {})})
    return p.returncode, p.stdout, p.stderr


def lead():
    f = PDIR / "lead.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail and not cond else ""))


def main():
    shutil.rmtree(TMPDIR, ignore_errors=True)
    rc, _, err = run("init", PID, "--kind", "ctf", "--goal", "leadership 回归测试")
    assert rc == 0, err

    print("T1 无主 → 自动接管（单窗口零摩擦）")
    rc, out, err = run("next", PID, "--as", "win-A")
    check("exit=0", rc == 0, f"rc={rc} {err}")
    check("banner 写了接管", "接管调度" in out, out)
    check("lead.json owner = win-A", (lead() or {}).get("owner") == "win-A")
    check("首灯 note 标了 auto", "auto" in ((lead() or {}).get("note") or ""))

    print("T2 自己人 → 续期，不换 owner、不重开始")
    t0 = (lead() or {}).get("epoch_at")
    started = (lead() or {}).get("started_at")
    rc, out, _ = run("next", PID, "--as", "win-A")
    check("exit=0", rc == 0)
    check("owner 没变", (lead() or {}).get("owner") == "win-A")
    check("started_at 没重置", (lead() or {}).get("started_at") == started)

    print("T3 别人当值 → 停手 exit 2")
    rc, out, err = run("next", PID, "--as", "win-B")
    check("exit=2", rc == 2, f"rc={rc}")
    check("没有输出任务", "下一个任务" not in out, out)
    check("提示了怎么接管", "fgs lead" in (out + err), out + err)
    check("owner 仍归 A", (lead() or {}).get("owner") == "win-A")

    print("T4 --json 分支能读到 wait")
    rc, out, _ = run("next", PID, "--as", "win-B", "--json")
    check("exit=2", rc == 2, f"rc={rc}")
    try:
        j = json.loads(out)
        check("json.task = wait", j.get("task") == "wait", out)
        check("json.lead = other", j.get("lead") == "other", out)
    except json.JSONDecodeError:
        check("输出是合法 JSON", False, out)

    print("T5 --force 才能强行接管")
    rc, out, _ = run("next", PID, "--as", "win-B", "--force")
    check("exit=0", rc == 0, f"rc={rc}")
    check("owner 换成 B", (lead() or {}).get("owner") == "win-B")

    print("T6 心跳过期 → 别人可自动接管（死了的 lead 不该占位）")
    stale = {"owner": "win-B", "started_at": "2020-01-01T00:00:00+00:00",
             "epoch_at": (datetime.now(timezone.utc) - timedelta(seconds=9999)).isoformat(timespec="seconds")}
    (PDIR / "lead.json").write_text(json.dumps(stale), encoding="utf-8")
    rc, out, _ = run("next", PID, "--as", "win-C")
    check("exit=0（自动接管）", rc == 0, f"rc={rc}")
    check("banner 说明原主已过期", "过期" in out, out)
    check("owner 换成 C", (lead() or {}).get("owner") == "win-C")

    print("T7 release → 回到无主")
    rc, out, _ = run("lead", PID, "--by", "win-C", "--release")
    check("exit=0", rc == 0, f"rc={rc}")
    rc, _, _ = run("next", PID, "--as", "win-D")
    check("别人可接管", (lead() or {}).get("owner") == "win-D")

    print("T8 只有持有者能释放")
    rc, out, err = run("lead", PID, "--by", "win-E", "--release")
    check("非持有者 release → exit 2", rc == 2, f"rc={rc}")
    check("owner 仍是 D", (lead() or {}).get("owner") == "win-D")

    print("T9 人读状态")
    rc, out, _ = run("lead", PID, "--by", "win-D")
    check("exit=0", rc == 0)
    check("能看出是自己", "续期" in out, out)

    shutil.rmtree(TMPDIR, ignore_errors=True)
    print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ 调度者身份 / 首灯检查：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
