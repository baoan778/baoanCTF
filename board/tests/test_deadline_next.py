#!/usr/bin/env python3
"""deadline 进决策入口的回归测试 —— 治「唯一决策入口对剩余时间盲」。

血账（2026-11-06 实测，不是推测）
----------------------------------
`fgs deadline` 单源落盘、不漂移（R-1 那条做得对），但全仓只有 4 处消费者：
  cmd_ladder:1891（仅显示）/ cmd_slot:4519（有建议，但只在槽位视图）/
  cmd_deadline / cmd_finish_all（只记录）。
而 `fgs next` —— **唯一"下一步做什么"的决策入口** —— `grep deadline` **0 命中**。
限时赛第一变量是「剩余时间 × 可得分数」，决策入口看不见 = 收尾全靠人肉记账。

三条设计红线（本测试逐条守住）：
  ① **只出建议，不改调度**：decide() 返回值一字不动，档位只影响打印；
  ② **不重算 deadline**：只读 `_deadline_left(g)`，与 R-1 单源一致；
  ③ **没设 deadline = 无建议**：不假装有节奏感。

    python3 board/tests/test_deadline_next.py
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import tempfile

import sys as _sys
_sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

try:
    import sys as _sm
    for _s in (_sm.stdout, _sm.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = pathlib.Path(__file__).resolve().parents[2]
FGS = LAB / "board" / "fgs.py"
PY = _sys.executable or "python3"

checks: list = []


def ok(name, cond, extra=""):
    checks.append((name, bool(cond), str(extra)))


def _report():
    bad = [c for c in checks if not c[1]]
    for name, good, extra in checks:
        print(f"  {'✓' if good else '✗'} {name}" + (f"   {extra[:240]}" if extra and not good else ""))
    print(f"\n通过 {len(checks) - len(bad)} / 失败 {len(bad)}")
    if bad:
        print("失败项：" + "、".join(c[0] for c in bad))
        return 1
    print("✓ deadline 进 fgs next（只建议不改调度）：全部通过")
    return 0


def fgs(board: str, *args: str):
    env = dict(os.environ)
    env["PI_LAB_BOARD_DIR"] = board
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(  # noqa: S603
        [PY, str(FGS), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, cwd=str(LAB), env=env,
    )


def main() -> int:
    ok("board/fgs.py 存在", FGS.is_file())
    if not FGS.is_file():
        return _report()

    # ① 静态：三条红线的代码级证据
    src = FGS.read_text(encoding="utf-8", errors="replace")
    ok("有 deadline_phase 纯函数", "def deadline_phase(left)" in src)
    ok("四个档位都定义", all(k in src for k in ("overdue", "endgame", "converge", "normal")))
    ok("有 per-phase 建议表", "PHASE_ADVICE" in src)
    ok("cmd_next 读 deadline", "phase = deadline_phase(_deadline_left(g))" in src)
    ok("明确标注 display only", "display only" in src)
    ok("decide() 签名未变（不接受 deadline）", "def decide(g: dict):" in src)

    with tempfile.TemporaryDirectory() as td:
        p = fgs(td, "init", "dl")
        ok("init 成功", p.returncode == 0, (p.stdout + p.stderr)[-200:])
        fgs(td, "goal", "dl", "t")

        # ③ 没设 deadline：不该有任何档位行
        p = fgs(td, "next", "dl")
        out = (p.stdout or "") + (p.stderr or "")
        ok("未设 deadline：无档位行", "deadline 剩余" not in out, out[-300:])
        _p = fgs(td, "next", "dl", "--json")
        if not (_p.stdout or "").strip():
            # ★ 2026-11-12 修偶发 IndexError：原来直接 `.stdout.strip().splitlines()[-1]`，
            #   子进程没吐 JSON 时就是 IndexError: list index out of range，
            #   真实原因（stderr/rc）全被吃掉，表现为「随机红」。
            #   现在把现场打出来，并让断言失败而非崩栈。
            ok("next --json 有输出", False,
               f"rc={_p.returncode} stderr={(_p.stderr or '')[:300]}")
            return _report()
        try:
            js = json.loads(_p.stdout.strip().splitlines()[-1])
        except json.JSONDecodeError as e:
            ok("未设 deadline：json 可解析", False,
               f"{e} out={(_p.stdout or '')[-260:]}")
            return _report()
        ok("未设 deadline：json 无 deadline 字段", "deadline" not in js, str(js)[:200])

        # 四个档位（用相对/绝对时间构造，绝不改动既有调度结论）
        cases = [
            ("+2h", "normal", "常规推进"),
            ("+20m", "converge", "收敛"),
            ("+3m", "endgame", "残局"),
            ("2020-01-01T00:00:00+08:00", "overdue", "已过 deadline"),
        ]
        baseline_task = js.get("task")
        for spec, phase, label in cases:
            _dp = fgs(td, "deadline", "dl", "--at", spec)
            # 把 deadline 的返回码也断言上：它失败了后面全是连锁误报
            ok(f"{phase}：deadline 设置成功", _dp.returncode == 0,
               f"rc={_dp.returncode} {(_dp.stderr or '')[:200]}")
            p = fgs(td, "next", "dl")
            out = (p.stdout or "") + (p.stderr or "")
            # ★ 断言失败时把「设进去的绝对时间 + 剩余秒数 + 原始输出」一起留证，
            #   否则只看到一个 ✗ 无法判断是边界漂移还是真 bug。
            if label not in out:
                _dl = fgs(td, "deadline", "dl", "--json").stdout or ""
                detail = f"spec={spec} rc={p.returncode} deadline_json={_dl[:200]} out={out[-260:]}"
                ok(f"{phase}：档位名出现", False, detail)
                return _report()
            ok(f"{phase}：档位名出现", True, "")

            _jp = fgs(td, "next", "dl", "--json")
            if not (_jp.stdout or "").strip():
                ok(f"{phase}：next --json 有输出", False,
                   f"rc={_jp.returncode} stderr={(_jp.stderr or '')[:300]}")
                return _report()
            try:
                j2 = json.loads(_jp.stdout.strip().splitlines()[-1])
            except json.JSONDecodeError as e:
                ok(f"{phase}：next --json 可解析", False,
                   f"spec={spec} {e} out={(_jp.stdout or '')[-260:]}")
                return _report()
            ok(f"{phase}：json.deadline.phase 正确",
               (j2.get("deadline") or {}).get("phase") == phase, str(j2.get("deadline"))[:200])
            ok(f"{phase}：json 带 advice", bool(j2.get("advice")), str(j2)[:200])
            # ① 最硬的一条：调度结论不能被剩余时间改变
            ok(f"{phase}：调度结论未被 deadline 改变",
               j2.get("task") == baseline_task,
               f"baseline={baseline_task} now={j2.get('task')}")

        # 收敛/残局档的建议必须说清「别开新题 / 只收口」
        fgs(td, "deadline", "dl", "--at", "+20m")
        out = fgs(td, "next", "dl").stdout or ""
        ok("收敛档建议含「不要开新题」", "不要开新题" in out, out[-300:])
        fgs(td, "deadline", "dl", "--at", "+3m")
        out = fgs(td, "next", "dl").stdout or ""
        ok("残局档建议含 finish-all", "finish-all" in out, out[-300:])
        ok("残局档建议含 confirm", "confirm" in out, out[-300:])

        # ② 不重算：清除 deadline 后立刻回到「无建议」
        fgs(td, "deadline", "dl", "--clear")
        out = fgs(td, "next", "dl").stdout or ""
        ok("clear 后无档位行", "deadline 剩余" not in out, out[-300:])

    return _report()


if __name__ == "__main__":
    raise SystemExit(main())
