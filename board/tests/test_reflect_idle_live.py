#!/usr/bin/env python3
"""test_reflect_idle_live.py —— X-3 **运行期**证据（不只是静态字符串匹配）。

为什么还要一条运行期测试
------------------------
静态守卫只证明"代码里有这个词"，证明不了"到期真的发盘点、真的不续推"。
本测试起一个真 node，把 idle-resume 扩展装进假 pi，喂一个**到期**的净活跃状态，
看 `agent_before_settle` 到底返回了什么：
  · 到期 → entries[0].customType == "reflect-idle"（盘点，不是续推）
  · 未到期 → 走续推路径（customType == "idle-resume"）或不动（图未闭合时）
  · aborted=true → 一律不动（用户中断不复活）

    python3 board/tests/test_reflect_idle_live.py
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
IDLE = require_ext("idle-resume.ts")
IDLEWATCH = LAB / "tools" / "idlewatch.mjs"
REFLECTWATCH = LAB / "tools" / "reflectwatch.mjs"
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
    print("✓ 反思空闲通道（X-3 · 运行期）：全部通过")
    return 0


DRIVER = """
const extUrl = process.env.IDLE_URL;
const mode = process.env.CASE_MODE;
const mod = await import(extUrl);
const handlers = {};
const pi = {
  on: (n, fn) => { (handlers[n] || (handlers[n] = [])).push(fn); },
  registerCommand: () => {},
};
const ui = [];
const ctx = { ui: { notify: (m) => ui.push(m) } };
mod.default(pi);

// 装弹：先走一次 tool_call 认 pid
const tc = handlers["tool_call"] || [];
for (const fn of tc) await fn({ toolName: "bash", input: { command: "fgs kickoff " + process.env.PID } }, ctx);

// 喂净活跃状态（模拟 reflect-paper 的发布）
globalThis.__PI_LAB_REFLECT = {
  activeMs: mode === "due" ? 40 * 60 * 1000 : 5 * 60 * 1000,
  toolCalls: 99,
  nudges: 0,
  lastNudgeActiveMs: 0,
  pid: process.env.PID,
  at: Date.now(),
};

let out;
for (const fn of (handlers["agent_before_settle"] || [])) {
  out = await fn({ type: "agent_before_settle", aborted: mode === "aborted" }, ctx);
}
console.log(JSON.stringify({
  out: out || null,
  customType: out && out.entries && out.entries[0] ? out.entries[0].customType : null,
  continued: !!(out && out.continue),
  ui,
}));
"""


def main() -> int:
    ok("idle-resume.ts 存在", IDLE.is_file())
    ok("tools/idlewatch.mjs 存在", IDLEWATCH.is_file())
    ok("tools/reflectwatch.mjs 存在", REFLECTWATCH.is_file())
    ok("node 可用", bool(NODE))
    if not (IDLE.is_file() and IDLEWATCH.is_file() and REFLECTWATCH.is_file() and NODE):
        return _report()

    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / "tools").mkdir(parents=True, exist_ok=True)
        (root / "log").mkdir(parents=True, exist_ok=True)
        (root / "board" / "projects" / "rt").mkdir(parents=True, exist_ok=True)
        # 扩展 findLab() 靠 tools/idlewatch.mjs 认根，所以两个模块都要拷进去
        shutil.copy2(IDLEWATCH, root / "tools" / "idlewatch.mjs")
        shutil.copy2(REFLECTWATCH, root / "tools" / "reflectwatch.mjs")
        # 最小 graph.json：readBoard 只做 JSON.parse，但要有未闭合 step 才能走到续推分支
        (root / "board" / "projects" / "rt" / "graph.json").write_text(json.dumps({
            "project_id": "rt", "kind": "ctf", "revision": 1,
            "nodes": {"g001": {"id": "g001", "type": "goal", "text": "rt", "status": "open"},
                      "s001": {"id": "s001", "type": "step", "text": "探面", "status": "open"}},
            "edges": [], "solved": None,
        }, ensure_ascii=False), encoding="utf-8")

        drv = root / "drv.mjs"
        drv.write_text(DRIVER, encoding="utf-8")

        def run_case(mode: str):
            env = {**os.environ,
                   "PI_LAB_ROOT": str(root),
                   "IDLE_URL": IDLE.as_uri(),
                   "CASE_MODE": mode,
                   "PID": "rt"}
            p = subprocess.run([NODE, "--experimental-strip-types", "--no-warnings", str(drv)],  # noqa: S603
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=180, cwd=str(root), env=env)
            ok(f"[{mode}] 脚本成功", p.returncode == 0, (p.stdout + p.stderr)[-400:])
            if p.returncode != 0:
                return {}
            try:
                return json.loads(p.stdout.strip().splitlines()[-1])
            except Exception as e:
                ok(f"[{mode}] 输出可解析", False, str(e))
                return {}

        # ① 到期 → 发盘点，不续推
        r = run_case("due")
        ok("[due] 确实返回了东西（说明走到了判据）", bool(r.get("out")),
           json.dumps(r, ensure_ascii=False)[:400])
        ok("[due] customType = reflect-idle（是盘点不是续推）",
           r.get("customType") == "reflect-idle", str(r.get("customType")))
        ok("[due] 仍会 continue（把模型推回来做盘点）", r.get("continued") is True)
        ok("[due] UI 提示写成「空闲反思」",
           any("空闲反思" in u for u in (r.get("ui") or [])), str(r.get("ui")))

        # ② 未到期 → 不该出盘点（走续推或不动）
        r2 = run_case("fresh")
        ok("[fresh] 不出盘点", r2.get("customType") != "reflect-idle",
           str(r2.get("customType")))

        # ③ 用户中断 → 一律不动（这是既有红线）
        r3 = run_case("aborted")
        ok("[aborted] 不返回续推/盘点", not r3.get("continued"),
           json.dumps(r3, ensure_ascii=False)[:300])

    return _report()


if __name__ == "__main__":
    raise SystemExit(main())
