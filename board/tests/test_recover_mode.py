#!/usr/bin/env python3
"""test_recover_mode.py —— X-1 零依赖逃生舱回归（治「闸门自身 BUG 时无旁路」）。

测什么（全离线，不起 pi、不碰真产物）
------------------------------------
  ① 逃生舱模块本体：前缀识别 / 去前缀后正文完整 / TTL / 激活状态；
  ② 与 OpenSecurity 2.0 的对照口径：前缀字符串一致（便于互查文档）；
  ③ 零依赖硬证据：源码里**不 import 任何本地模块**（只 node:fs / node:path）；
  ④ 三道阻断型闸门都**真的**读了标记（静态守卫）；
  ⑤ 逃生门自指：PI_LAB_RECOVER=0 能关掉逃生舱自己；
  ⑥ 前缀被剥掉（模型看到原指令，不是一句它不认识的控制标记）。

    python3 board/tests/test_recover_mode.py
"""
from __future__ import annotations

import json
import os
import pathlib
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
EXT = require_ext("recover-mode.ts")
NODE = find_node()

checks: list = []


def ok(name, cond, extra=""):
    checks.append((name, bool(cond), str(extra)))


def _report():
    bad = [c for c in checks if not c[1]]
    for name, good, extra in checks:
        mark = "✓" if good else "✗"
        print(f"  {mark} {name}" + (f"   {extra[:240]}" if extra and not good else ""))
    print(f"\n通过 {len(checks) - len(bad)} / 失败 {len(bad)}")
    if bad:
        print("失败项：" + "、".join(c[0] for c in bad))
        return 1
    print("✓ 零依赖逃生舱（X-1）：全部通过")
    return 0


# 驱动脚本写成独立 .mjs 文件（避免 -e 里花括号转义地狱）
DRIVER = """
const extUrl = process.env.EXT_URL;
const mod = await import(extUrl);
const handlers = {};
const notifies = [];
const pi = {
  on: (n, fn) => { (handlers[n] || (handlers[n] = [])).push(fn); },
  registerCommand: () => {},
};
const ctx = { ui: { notify: (m) => notifies.push(m) } };
mod.default(pi);
const inputHandlers = handlers["input"] || [];
const send = async (text) => {
  let out;
  for (const fn of inputHandlers) out = await fn({ type: "input", text, source: "interactive" }, ctx);
  return out;
};
const out = {};
out.plain = await send("帮我看看这道题");
out.activeAfterPlain = mod.recoverActive();
out.marked = await send(">>>RECOVER-MODE<<<  修一下插件");
out.activeAfterMarked = mod.recoverActive();
out.leadWs = await send("   >>>RECOVER-MODE<<< hello");
out.tight = await send(">>>RECOVER-MODE<<<x");
out.strip = mod.stripPrefix(">>>RECOVER-MODE<<<  abc");
out.stripNot = mod.stripPrefix("abc");
out.prefixConst = mod.RECOVER_PREFIX;
out.ttl = mod.RECOVER_TTL_MS;
out.notifyN = notifies.length;
out.nHandlers = inputHandlers.length;
console.log(JSON.stringify(out));
"""

DRIVER_OFF = """
const mod = await import(process.env.EXT_URL);
const handlers = {};
const pi = { on: (n, fn) => { (handlers[n] || (handlers[n] = [])).push(fn); }, registerCommand: () => {} };
mod.default(pi);
let out;
for (const fn of (handlers["input"] || [])) out = await fn({ type: "input", text: ">>>RECOVER-MODE<<< x" }, {});
console.log(JSON.stringify({ action: out && out.action, active: mod.recoverActive() }));
"""


def run_driver(js: str, root: pathlib.Path, env_extra: dict):
    drv = root / "driver.mjs"
    drv.write_text(js, encoding="utf-8")
    env = {**os.environ, "PI_LAB_ROOT": str(root), "EXT_URL": EXT.as_uri(), **env_extra}
    return subprocess.run(  # noqa: S603
        [NODE, "--experimental-strip-types", "--no-warnings", str(drv)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, cwd=str(root), env=env,
    )


def main() -> int:
    ok("recover-mode.ts 存在", EXT.is_file())
    if not EXT.is_file():
        return _report()
    ok("node 可用", bool(NODE))
    if not NODE:
        return _report()

    src = EXT.read_text(encoding="utf-8", errors="replace")

    # ③ 零依赖硬证据：只允许 node: 内置（顶部 import 区）
    imports = [ln.strip() for ln in src.splitlines() if ln.strip().startswith("import ")]
    bad_imports = [ln for ln in imports if "node:" not in ln]
    ok("零依赖：不 import 任何本地模块", not bad_imports, "; ".join(bad_imports))
    ok("只用 node:fs / node:path", any("node:fs" in x for x in imports)
       and any("node:path" in x for x in imports))
    ok("不读配置文件决定是否生效", ".ai_env" not in src and "settings.json" not in src)

    # ② 与 OS2.0 一致的前缀与逃生门
    ok("前缀与 OS2.0 一致", ">>>RECOVER-MODE<<<" in src)
    ok("有 TTL 常量", "RECOVER_TTL_MS" in src)
    ok("逃生门自指（PI_LAB_RECOVER=0）", "PI_LAB_RECOVER" in src)
    ok("挂 input 钩子（消息入口，最早）", 'pi.on("input"' in src)
    ok("返回 transform（剥前缀）", '"transform"' in src)
    ok("日志落 log/recover-mode.jsonl", "recover-mode.jsonl" in src)

    # ④ 三道阻断型闸门都读了标记
    for name in ("lane-watch.ts", "idle-resume.ts", "reflect-paper.ts"):
        g = require_ext(name).read_text(encoding="utf-8", errors="replace")
        ok(f"{name} 读 __PI_LAB_RECOVER", "__PI_LAB_RECOVER" in g)
        ok(f"{name} 有 recoverActive() 守卫", "recoverActive()" in g)

    # ① ⑥ 运行期
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / "board").mkdir(parents=True, exist_ok=True)
        (root / "board" / "fgs.py").write_text("# stub\n", encoding="utf-8")
        (root / "log").mkdir(parents=True, exist_ok=True)

        p = run_driver(DRIVER, root, {"PI_LAB_RECOVER_LOG": str(root / "log" / "r.jsonl")})
        ok("运行期脚本成功", p.returncode == 0, (p.stdout + p.stderr)[-400:])
        if p.returncode == 0:
            r = json.loads(p.stdout.strip().splitlines()[-1])
            ok("挂了 input 钩子", r.get("nHandlers") == 1)
            ok("无前缀 → continue", (r.get("plain") or {}).get("action") == "continue")
            ok("无前缀不激活", r.get("activeAfterPlain") is False)
            ok("有前缀 → transform", (r.get("marked") or {}).get("action") == "transform")
            ok("前缀被剥掉", (r.get("marked") or {}).get("text") == "修一下插件",
               repr((r.get("marked") or {}).get("text")))
            ok("激活后 recoverActive() = true", r.get("activeAfterMarked") is True)
            ok("前导空白也认", (r.get("leadWs") or {}).get("action") == "transform")
            ok("前缀后无空格也认", (r.get("tight") or {}).get("action") == "transform")
            ok("stripPrefix 正文正确", (r.get("strip") or {}).get("body") == "abc")
            ok("stripPrefix 不误伤无前缀文本", (r.get("stripNot") or {}).get("active") is False)
            ok("前缀常量对齐 OS2.0", r.get("prefixConst") == ">>>RECOVER-MODE<<<")
            ok("TTL = 10 分钟", r.get("ttl") == 600000)
            ok("激活时有 UI 提示", (r.get("notifyN") or 0) >= 1)

        p2 = run_driver(DRIVER_OFF, root, {"PI_LAB_RECOVER": "0",
                                          "PI_LAB_RECOVER_LOG": str(root / "log" / "r2.jsonl")})
        ok("硬关脚本成功", p2.returncode == 0, (p2.stdout + p2.stderr)[-300:])
        if p2.returncode == 0:
            r2 = json.loads(p2.stdout.strip().splitlines()[-1])
            ok("PI_LAB_RECOVER=0 → 不激活", r2.get("active") is False)
            ok("PI_LAB_RECOVER=0 → 消息原样放行", r2.get("action") == "continue")

    return _report()


if __name__ == "__main__":
    raise SystemExit(main())
