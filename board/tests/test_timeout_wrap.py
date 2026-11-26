#!/usr/bin/env python3
"""T8 · 统一超时包装回归（2026-11-11）。

验三件事：
  1. 超时被转成 CallTimeout（不是裸 TimeoutExpired，也不是静默吞掉）
  2. CallTimeout 不继承 Fail（超时 ≠ 结论不成立）
  3. 默认预算按类区分 + 环境变量可覆盖 + 下限保护（0/负数不会变无限等）
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = Path(__file__).resolve().parents[2]
FGS = LAB / "board" / "fgs.py"
PY = sys.executable or "python3"

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    mark = "\u2713 " if cond else "\u2717 "
    print("  " + mark + name + (("   " + str(detail)[:240]) if detail and not cond else ""))


def report():
    print("")
    print("\u901a\u8fc7 %d / \u5931\u8d25 %d" % (len(passed), len(failed)))
    if failed:
        print("failed: " + "\u3001".join(failed))
        return 1
    print("\u2713 T8 \u7edf\u4e00\u8d85\u65f6\u5305\u88c5\uff1a\u5168\u90e8\u901a\u8fc7")
    return 0


PROBE = """
import json, os, sys, time
sys.path.insert(0, %r)
import fgs
out = {}

# 1) 默认预算按类区分
out["t_tsec"] = fgs.timeout_for("tsec")
out["t_lock"] = fgs.timeout_for("lock")
out["t_shell"] = fgs.timeout_for("shell")
out["t_unknown_kind"] = fgs.timeout_for("no-such-kind")

# 2) 环境变量可覆盖
os.environ["PI_FGS_TIMEOUT_SHELL"] = "7"
out["t_env"] = fgs.timeout_for("shell")
os.environ["PI_FGS_TIMEOUT_SHELL"] = "not-a-number"
out["t_env_bad"] = fgs.timeout_for("shell")
os.environ["PI_FGS_TIMEOUT_SHELL"] = "0"
out["t_env_zero"] = fgs.timeout_for("shell")
os.environ["PI_FGS_TIMEOUT_SHELL"] = "-5"
out["t_env_neg"] = fgs.timeout_for("shell")
del os.environ["PI_FGS_TIMEOUT_SHELL"]

# 3) 超时 -> CallTimeout
t0 = time.time()
try:
    fgs.run_capped([sys.executable, "-c", "import time; time.sleep(5)"], timeout=1)
    out["timeout_raised"] = None
except fgs.CallTimeout as e:
    out["timeout_raised"] = "CallTimeout"
    out["timeout_what"] = e.what
    out["timeout_seconds"] = e.seconds
except Exception as e:
    out["timeout_raised"] = type(e).__name__
out["elapsed"] = round(time.time() - t0, 1)

# 4) 非零退出不抛（与 subprocess.run 语义一致）
cp = fgs.run_capped([sys.executable, "-c", "import sys; sys.exit(3)"], timeout=30)
out["rc_nonzero"] = cp.returncode

# 5) 正常命令拿到 stdout
cp = fgs.run_capped([sys.executable, "-c", "print(42)"], timeout=30)
out["stdout"] = (cp.stdout or "").strip()

# 6) CallTimeout 不是 Fail
out["is_fail_subclass"] = issubclass(fgs.CallTimeout, fgs.Fail)
out["is_exception"] = issubclass(fgs.CallTimeout, Exception)

print(json.dumps(out))
""" % str(LAB / "board")


def main():
    check("board/fgs.py exists", FGS.is_file())
    if not FGS.is_file():
        return report()
    src = FGS.read_text(encoding="utf-8", errors="replace")

    print("A) 符号在位")
    for sym in ("class CallTimeout", "def timeout_for", "def run_capped",
                "DEFAULT_TIMEOUTS", "TIMEOUT_FLOOR"):
        check("has " + sym, sym in src)

    print("B) 行为（调真实现）")
    p = subprocess.run([PY, "-c", PROBE], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    if "Traceback" in (p.stderr or ""):
        check("probe no crash", False, (p.stderr or "")[-400:])
        return report()
    try:
        d = json.loads((p.stdout or "").strip().splitlines()[-1])
    except Exception as e:
        check("probe output parses", False, str(e))
        return report()

    check("tsec 默认预算 = 60", d["t_tsec"] == 60, d["t_tsec"])
    check("lock 默认预算 = 30", d["t_lock"] == 30.0, d["t_lock"])
    check("shell 默认预算 = 120", d["t_shell"] == 120, d["t_shell"])
    check("未知类别回落到 shell 预算", d["t_unknown_kind"] == 120, d["t_unknown_kind"])
    check("环境变量可覆盖预算", d["t_env"] == 7, d["t_env"])
    check("环境变量写坏 → 回落默认（不崩）", d["t_env_bad"] == 120, d["t_env_bad"])
    check("环境变量=0 → 抬到下限 1（不会变无限等）", d["t_env_zero"] == 1, d["t_env_zero"])
    check("环境变量=负 → 抬到下限 1", d["t_env_neg"] == 1, d["t_env_neg"])

    check("★ 超时抛 CallTimeout（不是裸 TimeoutExpired）",
          d["timeout_raised"] == "CallTimeout", d["timeout_raised"])
    check("CallTimeout 带 what 与 seconds",
          bool(d["timeout_what"]) and d["timeout_seconds"] == 1,
          str(d.get("timeout_what")) + " / " + str(d.get("timeout_seconds")))
    check("真的按预算截断（5s 的活 1s 内返回）", d["elapsed"] <= 3.0, d["elapsed"])

    check("非零退出不抛（rc 透传）", d["rc_nonzero"] == 3, d["rc_nonzero"])
    check("正常命令拿到 stdout", d["stdout"] == "42", d["stdout"])

    check("★ CallTimeout 不继承 Fail（超时≠结论不成立）",
          d["is_fail_subclass"] is False, d["is_fail_subclass"])
    check("CallTimeout 是 Exception", d["is_exception"] is True)

    print("C) 接线完成 + 不破坏既有语义")
    # ★ 2026-11-18：从"包装已存在"升级为"**所有调用点都走包装**"。
    #   之前这里断言 >= 2（允许裸调用），等于给"接一半"发了通行证；现在收紧到
    #   只剩 run_capped 内部那一处。再冒出裸 subprocess.run 立刻报红。
    #   只数**代码行**：注释里提到这段字样是说明不是调用（注释自伤过一次）。
    code_src = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    check("★ 代码里只剩 1 处 subprocess.run（即 run_capped 内部）",
          code_src.count("subprocess.run(") == 1, code_src.count("subprocess.run("))
    check("★ 两处 tsec 调用都走 run_capped(kind=\"tsec\")",
          src.count('run_capped(_tsec_argv()') == 2, src.count('run_capped(_tsec_argv()'))
    check("两处都还在捕 CallTimeout（超时→rc=-1/err，不向上抛）",
          src.count("except CallTimeout as e:") == 2, src.count("except CallTimeout as e:"))
    check("T8 独立于 Lock 的超时（不接管锁）",
          "timeout=30.0" in src)

    return report()


if __name__ == "__main__":
    sys.exit(main())
