#!/usr/bin/env python3
"""接力块 + `brief --ref` 回归测试 —— 治「负面结论不共享」与「简报正文付两遍上下文」。

背景（本仓 0xGame 2026 复盘 · 失误 #9）
--------------------------------------
「负面结论不共享：id=34 前两条 lane 都在『抽矩阵→解码』，**第二条不知道第一条已经试过**」。
`DEAD_RX` 那时候已经有了，缺的是把「已达成原语 / 已证死路 / 下一步动作」三节
**顶到简报最前面**（移植自 hxbai 的会话接力块，见 `board/fgs.py::_relay_block`）。

本测试守五件事：
  1. 三节都在，且**在正文最前**（不是埋在末尾等读者自己找）
  2. ① 只装**可复用原语**（shell/凭据/oracle…），**不装**已证伪的事实
  3. ② 装的是已证伪路线；一条都没有时要明说「不是试过不行，是没找到入口」
  4. `--ref` / `--write`：**stdout 只有一个路径**（正文不进主会话上下文）
  5. 空板 / 无 work 目录不许崩

    python3 board/tests/test_relay_brief.py
"""
from __future__ import annotations

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
FGS = LAB / "board" / "fgs.py"
PID = "relaydemo"

checks: list[tuple[str, bool, str]] = []


def ok(name: str, cond: bool, extra: str = "") -> None:
    checks.append((name, bool(cond), extra))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="fgs-relay-"))
    board = tmp / "board"
    work = tmp / "work"
    (work / PID / "lanes").mkdir(parents=True)
    env = dict(os.environ, PI_LAB_BOARD_DIR=str(board), PI_LAB_WORK_DIR=str(work))

    def fgs(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
        return subprocess.run([PY, str(FGS), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                              env=env, timeout=timeout)

    try:
        # ★ 2026-11-21（R6-D）：**setup 的每一步都要断言成功**。
        #   原实现这四行不查 rc，于是任何一步失败（典型：容器被并发 docker exec 抢 CPU 时
        #   fgs 的 15s 黑板写锁超时）都会让下游断言"两条死路触发换攻击面"莫名其妙地红 ——
        #   实测全量回归里就出现过一次 16/27，单跑 5~6 次全绿，无法归因。
        #   现在把 setup 失败单独报出来，红的时候一眼看出是"哪条 fact 没写进去"。
        for _args in (
            ("init", PID, "--goal", "接力块测试：拿到 flag"),
            # ① 原语：拿到 shell 回显
            ("fact-add", PID, "已拿到 shell 回显：uid=33(www-data)", "--confidence", "verified"),
            # ② 死路两条（措辞命中 DEAD_RX）
            ("fact-add", PID, "试过 SQLi /login，未发现注入（无回显）", "--confidence", "verified"),
            ("fact-add", PID, "试过 LFI /index.php?page=，无差异", "--confidence", "verified"),
        ):
            _p = fgs(*_args)
            ok(f"setup: {' '.join(_args[:2])} 成功",
               _p.returncode == 0 and "Traceback" not in (_p.stderr or ""),
               (f"rc={_p.returncode} {( _p.stderr or '')[-160:]}"))
        # 可复用产物
        (work / PID / "lanes" / "exp.py").write_text("print('poc')\n", encoding="utf-8")
        fgs("step-add", PID, "用 www-data 读 /var/www/html/config.php")

        # ── brief --step ──
        p = fgs("brief", PID, "--step", "s001")
        out = p.stdout
        ok("brief --step 不崩", p.returncode == 0 and "Traceback" not in p.stderr, p.stderr[-200:])

        i_relay = out.find("★ 接力块")
        i_step = out.find("## 这一步")
        ok("接力块存在", i_relay >= 0, out[:200])
        ok("接力块在正文最前（早于「## 这一步」）", 0 <= i_relay < i_step, f"relay@{i_relay} step@{i_step}")
        for sect in ("① 已达成原语", "② 已证死路", "③ 下一步动作"):
            ok(f"含「{sect}」", sect in out)

        ok("① 装进了 shell 原语", "uid=33(www-data)" in out)
        ok("① 列出了工作区产物 exp.py", "lanes/exp.py" in out, out[out.find("①"):][:400])
        ok("② 装进了两条死路", "未发现注入" in out and "无差异" in out)
        ok("③ 指向 open step s001", "认领 `s001`" in out)
        ok("两条死路触发「换攻击面」提醒", "换攻击面" in out)

        # ① 不许装已证伪的事实：在「①」与「②」之间的片段里不应出现死路原文
        seg = out[i_relay:out.find("② 已证死路", i_relay)]
        ok("① 里没有混入已证伪结论", "未发现注入" not in seg and "无差异" not in seg, seg[:200])

        # ── brief --restart ──
        p2 = fgs("brief", PID, "--restart")
        ok("--restart 也有接力块", "★ 接力块" in p2.stdout)
        ok("--restart 保留原「已证伪的路线」细节节", "已证伪的路线" in p2.stdout)
        ok("--restart 仍有重启纪律", "重启纪律" in p2.stdout)

        # ── --ref：stdout 只许有一个路径（本测试的核心契约） ──
        p3 = fgs("brief", PID, "--step", "s001", "--ref")
        lines = [l for l in p3.stdout.splitlines() if l.strip()]
        ok("--ref 的 stdout 只有一行", len(lines) == 1, f"{len(lines)} 行：{lines[:3]}")
        ok("--ref 打的是路径", lines and lines[0].endswith("_brief-step.txt"), lines[:1])
        ref = Path(lines[0]) if lines else None
        ok("--ref 真的落盘了", bool(ref and ref.exists()))
        if ref and ref.exists():
            body = ref.read_text(encoding="utf-8")
            ok("落盘正文含接力块", "★ 接力块" in body)
            ok("落盘正文含未解题面", "uid=33(www-data)" in body)
            ok("落盘头部写了「不要把正文贴进 prompt」", "不要" in body.splitlines()[1],
               body.splitlines()[1][:120] if len(body.splitlines()) > 1 else "")
        ok("--ref 时正文**没有**出现在 stdout", "★ 接力块" not in p3.stdout)

        # ── --write 自定义路径 ──
        custom = tmp / "mybrief.md"
        p4 = fgs("brief", PID, "--restart", "--write", str(custom))
        ok("--write 用自定义路径", str(custom) in p4.stdout and custom.exists(), p4.stdout[:200])
        ok("--write 同样不把正文打到 stdout", "★ 接力块" not in p4.stdout)

        # ── 边界：空板 ──
        fgs("init", "emptyboard", "--goal", "空板")
        p5 = fgs("brief", "emptyboard", "--restart")
        ok("空板不崩", p5.returncode == 0 and "Traceback" not in p5.stderr, p5.stderr[-200:])
        ok("空板明说「不是试过不行，是没找到入口」", "没找到入口" in p5.stdout or "换攻击面" in p5.stdout,
           p5.stdout[:300])

        # ── 边界：work 目录不存在 ──
        env2 = dict(os.environ, PI_LAB_BOARD_DIR=str(board),
                    PI_LAB_WORK_DIR=str(tmp / "nonexistent-work"))
        p6 = subprocess.run([PY, str(FGS), "brief", PID, "--restart"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env2, timeout=60)
        ok("work 目录不存在不崩", p6.returncode == 0 and "Traceback" not in p6.stderr,
           p6.stderr[-200:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for _, c, _ in checks if c)
    for name, c, extra in checks:
        if not c:
            print(f"  ✗ {name}" + (f"   [{extra}]" if extra else ""))
    print(f"\n{passed}/{len(checks)} 断言通过"
          + ("（只列失败项）" if passed == len(checks) else ""))
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
