#!/usr/bin/env python3
"""test_recover_gate_live.py —— X-1 **运行期**证据：闸门真的会放行。

为什么还要一条运行期测试
------------------------
静态守卫只能证明"代码里读了标记"，证明不了"读了之后真的不拦"。
本测试把 lane-watch 装进假 pi，造一个**本该被拦**的场景（kickoff 后一路都没派），
分别在「逃生舱激活 / 未激活」两种条件下跑同一个 tool_call，看会不会 block：
  · 未激活 → block=true（证明这道闸门本来是活的，不是本来就拦不住）
  · 激活   → 不 block（证明逃生舱真的打通了）
两条必须同时成立 —— 否则要么闸门是死的，要么逃生舱是假的。

    python3 board/tests/test_recover_gate_live.py
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys as _sys
import tempfile

_sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _pkgpaths import find_node, require_ext  # noqa: E402

try:
    for _s in (_sys.stdout, _sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = pathlib.Path(__file__).resolve().parents[2]
LANE = require_ext("lane-watch.ts")
RECOVER = require_ext("recover-mode.ts")
LANEWATCH_MOD = LAB / "tools" / "lanewatch.mjs"
NODE = find_node()

checks: list = []


def ok(name, cond, extra=""):
    checks.append((name, bool(cond), str(extra)))


def _report():
    bad = [c for c in checks if not c[1]]
    for name, good, extra in checks:
        mark = "✓" if good else "✗"
        print(f"  {mark} {name}" + (f"   {extra[:300]}" if extra and not good else ""))
    print(f"\n通过 {len(checks) - len(bad)} / 失败 {len(bad)}")
    if bad:
        print("失败项：" + "、".join(c[0] for c in bad))
        return 1
    print("✓ 逃生舱运行期（X-1 · 闸门真的放行）：全部通过")
    return 0


DRIVER = """
const laneMod = await import(process.env.LANE_URL);
const recMod = await import(process.env.RECOVER_URL);
const recoverOn = process.env.RECOVER_ON === "1";

// 1) 先让逃生舱按真实路径激活（走 input 钩子，不手动写 globalThis）
const recHandlers = {};
const recPi = { on: (n, fn) => { (recHandlers[n] || (recHandlers[n] = [])).push(fn); }, registerCommand: () => {} };
recMod.default(recPi);
if (recoverOn) {
  for (const fn of (recHandlers["input"] || [])) {
    await fn({ type: "input", text: ">>>RECOVER-MODE<<< 继续", source: "interactive" }, {});
  }
}

// 2) lane-watch：kickoff 之后连续跑很多次工具调用，一路都不派
const handlers = {};
const pi = {
  on: (n, fn) => { (handlers[n] || (handlers[n] = [])).push(fn); },
  registerCommand: () => {},
};
laneMod.default(pi);
const blocks = [];
const tcHandlers = handlers["tool_call"] || [];
for (let i = 0; i < 15; i++) {
  for (const fn of tcHandlers) {
    const r = await fn({ toolName: "bash", input: { command: i === 0 ? ("fgs kickoff " + process.env.PID) : "echo hi" } }, { ui: { notify: () => {} } });
    if (r && r.block) blocks.push({ i, reason: String(r.reason || "").slice(0, 80) });
  }
}
console.log(JSON.stringify({ recoverActive: recMod.recoverActive(), blocked: blocks.length }));
"""


def main() -> int:
    ok("lane-watch.ts 存在", LANE.is_file())
    ok("recover-mode.ts 存在", RECOVER.is_file())
    ok("tools/lanewatch.mjs 存在", LANEWATCH_MOD.is_file())
    ok("node 可用", bool(NODE))
    if not (LANE.is_file() and RECOVER.is_file() and LANEWATCH_MOD.is_file() and NODE):
        return _report()

    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / "tools").mkdir(parents=True, exist_ok=True)
        (root / "log").mkdir(parents=True, exist_ok=True)
        (root / "board" / "projects" / "rt").mkdir(parents=True, exist_ok=True)
        shutil.copy2(LANEWATCH_MOD, root / "tools" / "lanewatch.mjs")

        drv = root / "drv.mjs"
        drv.write_text(DRIVER, encoding="utf-8")

        def run_case(recover_on: bool):
            env = {**os.environ,
                   "PI_LAB_ROOT": str(root),
                   "LANE_URL": LANE.as_uri(),
                   "RECOVER_URL": RECOVER.as_uri(),
                   "RECOVER_ON": "1" if recover_on else "0",
                   "PID": "rt"}
            p = subprocess.run([NODE, "--experimental-strip-types", "--no-warnings", str(drv)],  # noqa: S603
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=180, cwd=str(root), env=env)
            ok(f"[recover={recover_on}] 脚本成功", p.returncode == 0,
               (p.stdout + p.stderr)[-400:])
            if p.returncode != 0:
                return {}
            try:
                return json.loads(p.stdout.strip().splitlines()[-1])
            except Exception as e:
                ok(f"[recover={recover_on}] 输出可解析", False, str(e))
                return {}

        off = run_case(False)
        ok("[未激活] 闸门是活的（真的会 block）", (off.get("blocked") or 0) > 0,
           json.dumps(off, ensure_ascii=False))
        ok("[未激活] 标记为 false", off.get("recoverActive") is False, str(off.get("recoverActive")))

        on = run_case(True)
        ok("[激活] 标记为 true", on.get("recoverActive") is True, str(on.get("recoverActive")))
        ok("[激活] 闸门一次都没拦（逃生舱打通）", (on.get("blocked") or 0) == 0,
           json.dumps(on, ensure_ascii=False))

    return _report()


if __name__ == "__main__":
    raise SystemExit(main())
