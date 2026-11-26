#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_solve_sigterm.py — R4-A 停机落盘（SIGTERM 时别把本轮结果带走）

为什么（R4-01 §2.6）：2026-11-20 17:11 执行 supervise --stop 后，6 个轮段
（$0.7945 / 4139s / 全场成本 15.5%）在 shard 日志里一行记录都没有；其中
101 与 093 其实已经解出。根因是写收尾行的代码全在 solve_one 正常路径上，
SIGTERM 默认动作是立即终止进程，finally 都不执行。

本测试分两层：
  A. 静态接线：信号处理器必须能杀到「当前这轮 pi」（_CUR["proc"] 有赋值）、
     main 里真的装了处理器、处理器里用的是不 wait 的 _signal_kill、
     循环里有停机检查（停了就不再起下一题）。
  B. 运行时：起一个假 pi（打印 FOUND_FLAG 后长睡），1.5s 后给自己发 SIGTERM；
     断言 solve_one 在 15s 内返回 1、收尾日志行是 rc=143、flag 被提交（且日志行带题号）、
     且假 pi 已被杀（不等 budget 30s）。非 POSIX 宿主跳过 B（rc=77 的既有约定）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = Path(os.path.dirname(os.path.dirname(HERE)))
SDK = ROOT / "tools" / "sdkshard.py"

_passed, _failed = 0, 0


def ck(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  ✗ {name}" + (f"   [{detail}]" if detail else ""))


CHILD = r'''
import importlib.util, json, os, signal, sys, threading, time, types
from pathlib import Path

SDK = sys.argv[1]
TMP = Path(sys.argv[2])
TOOLS = str(Path(SDK).parent)
sys.path.insert(0, TOOLS)

spec = importlib.util.spec_from_file_location("sdkshard_ut", SDK)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

m.ROOT = TMP
m._SHARD_LOG = TMP / "log" / "shard-test.log"
m._SHARD_LOG.parent.mkdir(parents=True, exist_ok=True)

# 账本隔离：绝不写进真实 work/solve/ledger.json
import solve_ledger
solve_ledger.LEDGER = TMP / "work" / "solve" / "ledger.json"

# 假 pi：先打印 flag（逐字）再长睡 —— 模拟「已拿到结果但还没收尾」的那一秒。
fake_dir = TMP / "bin"
fake_dir.mkdir(parents=True, exist_ok=True)
pi = fake_dir / "pi"
pi.write_text("#!/bin/bash\n"
              "echo FOUND_FLAG=flag{11111111-2222-3333-4444-555555555555}\n"
              "sleep 300\n", encoding="utf-8")
pi.chmod(0o755)
os.environ["PATH"] = str(fake_dir) + os.pathsep + os.environ.get("PATH", "")

m.wait_ready = lambda *a, **k: (True, 0.0)
m.write_solver_system = lambda *a, **k: None

class Client:
    def __init__(self):
        self.subs = []
    def start_challenge(self, code):
        return types.SimpleNamespace(container_addr=["127.0.0.1:9"])
    def submit_flag(self, code, flag):
        self.subs.append(flag)
        return types.SimpleNamespace(correct=True, awarded=200,
                                     correct_flag_count=1, total_flag_count=1)
    def close_challenge(self, code):
        pass

c = Client()
m._install_stop_handlers()
threading.Timer(1.5, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
t0 = time.time()
got = m.solve_one(c, "xben-999-24", 30, model="deepseek-flash")
elapsed = time.time() - t0
print(json.dumps({"got": got, "subs": c.subs, "elapsed": round(elapsed, 2)}))
'''


def main():
    if not SDK.exists():
        print("SKIP  tools/sdkshard.py 不在发布包里")
        return 77
    # ★ 2026-11-25：B 段要真跑 sdkshard.solve_one，它内部
    #   `from tsec_benchmark import ...` —— 外部 pip 包，不是本仓库的。
    #   没装就 SKIP（77），不当失败：这是「本机缺依赖」，不是「代码坏了」。
    try:
        import tsec_benchmark  # noqa: F401
    except ImportError:
        print("SKIP  需要 tsec-benchmark 包（pip install tsec-benchmark）")
        return 77
    src = SDK.read_text(encoding="utf-8")

    print("A) 静态接线（信号处理器杀得到 pi、main 真的装了）")
    ck("A1 当前 pi 句柄有赋值", '_CUR["proc"] = proc' in src)
    ck("A2 main 安装信号处理器", "_install_stop_handlers()" in src)
    ck("A3 处理器用不 wait 的 _signal_kill",
       "def _signal_kill(" in src and "_signal_kill(_CUR[\"proc\"])" in src)
    ck("A4 不再引用未定义的 _StopRequested", "_StopRequested" not in src)
    ck("A5 取题循环有停机检查", "if _stop_pending():" in src)
    ck("A6 超预算/停机 rc 记 143", "_RC_STOPPED = 143" in src and "rc = _RC_STOPPED" in src)

    if os.name != "posix":
        print("B) 运行时（非 POSIX 宿主跳过）")
        print(f"\nSKIP  {_passed} passed, {_failed} failed")
        return 77

    print("B) 运行时：SIGTERM → 15s 内落盘并返回")
    tmp = Path(tempfile.mkdtemp(prefix="sigterm-"))
    try:
        env = dict(os.environ)
        env["PI_LAB_SIGDUMP_GRACE"] = "8"
        p = subprocess.run([sys.executable, "-c", CHILD, str(SDK), str(tmp)],
                           capture_output=True, text=True, timeout=45, env=env,
                           cwd=str(ROOT))
        out = (p.stdout or "") + (p.stderr or "")
        line = ""
        for ln in (p.stdout or "").splitlines():
            if ln.strip().startswith("{"):
                line = ln.strip()
        data = {}
        try:
            data = json.loads(line)
        except Exception:
            pass
        ck("B1 solve_one 返回 1（flag 被抓到并提交）", data.get("got") == 1, out[-500:])
        ck("B2 提交的是逐字 flag",
           data.get("subs") == ["flag{11111111-2222-3333-4444-555555555555}"], data.get("subs"))
        ck("B3 停机后不等 budget（elapsed < 15s）",
           isinstance(data.get("elapsed"), (int, float)) and data["elapsed"] < 15,
           data.get("elapsed"))
        logf = tmp / "log" / "shard-test.log"
        txt = logf.read_text(encoding="utf-8") if logf.exists() else ""
        ck("B4 收尾日志行 rc=143", "rc=143" in txt, txt[-300:])
        ck("B5 日志里保留 flag 原文", "flag{11111111-2222-3333-4444-555555555555}" in txt)
        # ★ R5-G：提交结果必须能判定“这一题得没得分” —— 三个 shard 同写一个日志文件，
        #   不带题号就无法归因（实测 049/051 两段交错，无法判断 049 是否得分）。
        ck("B6 提交行带题号（可归因）",
           "✅ xben-999-24 flag{" in txt, txt[-400:])
        ck("B7 有 got=N/M 汇总行",
           "xben-999-24: 提交结果 got=1/1" in txt, txt[-400:])
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'OK' if _failed == 0 else 'FAIL'}  {_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
