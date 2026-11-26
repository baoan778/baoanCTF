#!/usr/bin/env python3
"""flagq 接线回归测试 —— 治「46 个用例全绿，但生产零调用」。

血账（2026-11-06 实测）
----------------------
`board/flagq.py` 有 255 行、`test_flagquality.py` 46 个用例全绿，
但 `grep -rn "import flagq|from flagq" --include=*.py . | grep -v /tests/` **零命中**。
"测试全绿"和"已生效"是两件事 —— 这是最危险的假象。

操作者定案（D-6=A）：**只告警，不拦截**。
理由：`fgs verify` 的逐字溯源（byte-exact）已经是硬门禁；flagq 是启发式，
启发式误判不该否决人工判断，它只负责把"为什么不像真 flag"摆到人面前。

本测试守五件事（全离线；临时 BOARD_DIR，不碰真产物）：
  ① 接线点在（verify / flag-declare / flag-record 三处都调）；
  ② 真旗形 → 不告警；
  ③ 诱饵前缀 → 告警且**给出理由**；
  ④ 扫射：连记 4 条同形 → 后续那条被判扫射（这是首版接线的真 bug：单条调用永远判不出）；
  ⑤ **绝不拦截**：告警场景下退出码仍是 0，且 flag 照常入账。

    python3 board/tests/test_flagq_wiring.py
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
    print("✓ flagq 接线（只告警不拦截）：全部通过")
    return 0


def fgs(board: str, *args: str):
    env = dict(os.environ)
    env["PI_LAB_BOARD_DIR"] = board
    # ★ 2026-11-11 修（与 test_cli_smoke / test_next_termination / test_matrix_fuzz 同一个坑）：
    #   只隔离 BOARD 不够 —— fgs 的 _work_dir() 会退回真 work/<pid>/，
    #   扫射用例要连记 5 条同形 flag，批量跑时和别的测试共用同一批 pid 文件互相踩，
    #   表现为「扫射：第 5 条触发提示」那 3 条随机红（单跑 19/0）。
    env["PI_LAB_WORK_DIR"] = str(pathlib.Path(board).parent / "work")
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

    # ① 静态：三处调用点 + 关键不变式（不 raise、不改判定）
    src = FGS.read_text(encoding="utf-8", errors="replace")
    ok("接线点 ≥3 处", src.count("_flagq_warn(") >= 4,
       f"实际 {src.count('_flagq_warn(')}（1 个定义 + 3 个调用点）")
    ok("verify 路径已接", "_flagq_warn([claim], project=a.project)" in src)
    ok("flag-record 路径已接", "_flagq_warn([text], project=a.project)" in src)
    ok("flag-declare 路径已接", "_flagq_warn([value], project=a.project)" in src)
    ok("判分异常不抛（静默降级）", "except Exception:\n        return []" in src
       or "判分崩了 = 没有建议，绝不拦" in src)
    ok("文案明确「不阻断」", "不阻断" in src)
    ok("扫射需要全批背景（only 参数）", "only=cands" in src)

    with tempfile.TemporaryDirectory() as td:
        # ② 真旗形：不该有 flagq 提示
        p = fgs(td, "init", "w1")
        ok("init 成功", p.returncode == 0, (p.stdout + p.stderr)[-200:])
        fgs(td, "goal", "w1", "t")
        ev = pathlib.Path(td) / "ev.txt"
        ev.write_text("leak: HTB{abc123def456}\n", encoding="utf-8")
        p = fgs(td, "flag-declare", "w1", "--value", "HTB{abc123def456}", "--source", str(ev))
        out = (p.stdout or "") + (p.stderr or "")
        ok("真旗形：退出码 0", p.returncode == 0, out[-300:])
        ok("真旗形：无 flagq 提示", "flagq 质量提示" not in out, out[-300:])

        # ③ 诱饵：该有提示 + 理由
        p2 = fgs(td, "init", "w2")
        fgs(td, "goal", "w2", "t")
        ev2 = pathlib.Path(td) / "ev2.txt"
        ev2.write_text("leak: notflag{deadbeef1234}\n", encoding="utf-8")
        p = fgs(td, "flag-declare", "w2", "--value", "notflag{deadbeef1234}", "--source", str(ev2))
        out = (p.stdout or "") + (p.stderr or "")
        ok("诱饵：触发 flagq 提示", "flagq 质量提示" in out, out[-400:])
        ok("诱饵：给出理由（前缀/可信度）",
           ("诱饵" in out) or ("可信度" in out), out[-400:])
        # ⑤ 绝不拦截：诱饵也照常成功
        ok("诱饵：仍然不拦截（rc=0）", p.returncode == 0, out[-300:])
        # ★ PI_LAB_BOARD_DIR 的值就是 projects 目录本身（不是仓库根），
        #   见 fgs.py: PROJECTS = Path(os.environ.get("PI_LAB_BOARD_DIR") or (ROOT/"projects"))
        #   首版写成 <td>/board/projects/... 是错的（测试自己找错文件，不是功能问题）。
        led = pathlib.Path(td) / "w2" / "flag-ledger.jsonl"
        ok("诱饵：仍照常入账", led.is_file() and "notflag" in led.read_text(encoding="utf-8"),
           str(led))

        # ④ 扫射：先入账 4 条同形，第 5 条应被判扫射
        fgs(td, "init", "w3")
        fgs(td, "goal", "w3", "t")
        for i in range(1, 5):
            fgs(td, "flag-record", "w3", "--flag", f"HTB{{aaa{i}}}")
        p = fgs(td, "flag-record", "w3", "--flag", "HTB{bbb9}")
        out = (p.stdout or "") + (p.stderr or "")
        ok("扫射：第 5 条触发提示", "flagq 质量提示" in out, out[-400:])
        ok("扫射：理由里点出「扫射」", "扫射" in out, out[-400:])
        ok("扫射：仍然不拦截（rc=0）", p.returncode == 0, out[-300:])

        # 只报本次候选，不重打历史
        ok("扫射：不重打历史 flag", out.count("⚠ '") <= 2, out[-400:])

    return _report()


if __name__ == "__main__":
    raise SystemExit(main())
