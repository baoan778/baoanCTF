#!/usr/bin/env python3
"""thinkcheck 回归：派活前的 thinkingLevel 天花板预检。

    python3 board/tests/test_thinkcheck.py

为什么测这个：`pi-subagents` 的 `applySubagentMaxThinking`（agents.js:1111-1115）
会**无条件**用 settings 的 `maxThinking` 覆盖每个 agent 的天花板，随后
`assertThinkingWithinCeiling` 发现 agent 自己的 `thinkingLevel` 超了就直接抛错。
所以「把 maxThinking 调低」这个看似安全的动作，会让 ctf-verify(max) 在**派活那一刻**
才炸 —— 改配置时毫无征兆。

thinkcheck 把这个事后爆炸提前成事前红灯。本测试锁住它的判据：
  A. 未超限 → rc=0（不能把好的判坏）
  B. 超限 → rc=1 且点名具体角色（判据要可执行，不能只说"有问题"）
  C. 未设天花板 → rc=0 且说明"跟随默认"
  D. 未知层级名 → 不误判（rc=0 + 提示人工确认）
  E. 读不到 settings → rc=2（配置问题要和判据失败区分开）
  F. 输出必须给出**可执行的修法**（两句具体建议），而不是只报错
"""
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

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# ★ 2026-11-19：从 tools/bin/thinkcheck（裸 Python 脚本）搬到 tools/thinkcheck.py。
#   原因见 tools/make-bin.sh 与 docs/状态快照.md：Windows 侧 exec 不解析脚本 shebang，
#   凡是非 MSYS 原生解释器都会退化成「用 shell 解释整个文件」→ 必须在 bin/ 里放 bash 包装。
#   所以正本现在在 tools/，bin/ 只是 exec "$PY" 的壳。
TK = os.path.join(ROOT, "tools", "thinkcheck.py")
passed, failed = [], []

ROLES = {
    "ctf-verify": {"thinkingLevel": "max"},
    "ctf-route": {"thinkingLevel": "high"},
    "ctf-recon": {"thinkingLevel": "medium"},
    "assistant": {},                      # 未设 thinkingLevel
}


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail and not cond else ""))


def run(agent_dir):
    env = dict(os.environ)
    env["PI_CODING_AGENT_DIR"] = agent_dir
    p = subprocess.run([sys.executable, TK], capture_output=True, env=env, timeout=60)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


def mk(root: Path, maxthinking, roles=ROLES):
    rd = Path(root, "roles")
    rd.mkdir(parents=True, exist_ok=True)
    for n, d in roles.items():
        Path(rd, f"{n}.json").write_text(json.dumps(d), encoding="utf-8")
    s = {} if maxthinking is ... else {"maxThinking": maxthinking}
    Path(root, "settings.json").write_text(json.dumps(s), encoding="utf-8")
    return str(root)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="thinkcheck-"))
    try:
        print("1) 天花板 max：全部在界内 → rc=0")
        rc, out = run(mk(tmp / "a", "max"))
        check("rc=0", rc == 0, f"rc={rc} :: {out[:200]}")
        check("说全部在界内", "都在天花板内" in out, out[:300])

        print("2) 天花板降到 medium → 必须 rc=1 并点名")
        rc, out = run(mk(tmp / "b", "medium"))
        check("★ rc=1", rc == 1, f"rc={rc}")
        check("★ 点名 ctf-verify", "ctf-verify" in out, out[:300])
        check("★ 点名 ctf-route", "ctf-route" in out)
        check("不冤枉 medium 角色", "ctf-recon: thinkingLevel" not in out, out[:400])
        check("★ 给出可执行修法 a)", "a)" in out and "maxThinking" in out, out[-400:])
        check("★ 给出可执行修法 b)", "b)" in out and "thinkingLevel" in out)
        check("说明不是降级是失败", "抛错" in out or "失败" in out)

        print("3) 未设天花板 → rc=0 且说明跟随默认")
        rc, out = run(mk(tmp / "c", ...))
        check("rc=0", rc == 0, f"rc={rc}")
        check("说明未被覆盖", "不会被覆盖" in out or "生效" in out, out[:300])

        print("4) 未知层级名 → 不误判（提示人工确认）")
        rc, out = run(mk(tmp / "d", "turbo"))
        check("rc=0（无法比较不当作失败）", rc == 0, f"rc={rc}")
        check("提示人工确认", "人工确认" in out, out[:300])

        print("5) 读不到 settings → rc=2（配置问题 vs 判据失败要分开）")
        empty = tmp / "e"
        Path(empty, "roles").mkdir(parents=True, exist_ok=True)
        rc, out = run(str(empty))
        check("★ rc=2", rc == 2, f"rc={rc}")
        check("指出读不到", "读不到" in out, out[:300])

        print("6) 真实运行侧配置必须绿（本次交付的现场判据）")
        real = os.environ.get("PI_CODING_AGENT_DIR") or os.path.expanduser("~/.pi-ctf/agent")
        if Path(real, "settings.json").exists():
            rc, out = run(real)
            check(f"实际 {real} 在天花板内", rc == 0, f"rc={rc} :: {out[:300]}")
        else:
            print(f"   ({real} 没有 settings.json —— 宿主机常态，真身在容器里)")

        # ★ 2026-11-19：兜底必须命中仓库 agent-template/，否则这条体检在宿主上等于没装
        #   （宿主 ~/.pi-ctf/agent 只有 skills/，真正带 settings.json 的是容器那份卷）。
        env2 = {k: v for k, v in os.environ.items() if k != "PI_CODING_AGENT_DIR"}
        p2 = subprocess.run([sys.executable, TK], capture_output=True, env=env2, timeout=60)
        out2 = (p2.stdout + p2.stderr).decode("utf-8", "replace")
        check("不设 PI_CODING_AGENT_DIR 也能跑（兜底到仓库 agent-template/）",
              p2.returncode == 0, f"rc={p2.returncode} :: {out2[:300]}")
        check("兜底路径确实读到了 settings.json", "读不到" not in out2, out2[:300])
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failed:
        print(f"通过 {len(passed)} / 失败 {len(failed)}")
        print("失败项：" + "、".join(failed[:10]))
        return 1
    print(f"通过 {len(passed)} / 失败 0")
    print("✓ thinkcheck 预检：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
