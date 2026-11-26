#!/usr/bin/env python3
"""flag-watch 回归测试 —— 治「flag 已经出现在输出里，却被当普通文本划过去」。

背景：`tools/knowledge/flag-formats.md` 自己写着这是 agent 最常见的丢分方式，
但本仓以前只有纪律（AGENTS.md §四）没有机器。2026-10-06 补上：
  · `tools/flagwatch.mjs`（纯函数，零依赖，node 直接跑）
  · `.pi/agent/extensions/flag-watch.ts`（tool_result 钩子：命中就插警告进结果 + notify + 留痕）

本测试守住四件事（全离线；日志写到临时文件，**不碰真产物**）：
  ① flagwatch.mjs 的 --selftest 全绿（正例/反例/变形/去重/性能护栏）；
  ② 扩展真的能加载，且**命中时**把警告插进了 tool_result 内容；
  ③ 去重（同一 flag 同 session 只插一次）、负例不改内容、日志写进 PI_LAB_FLAGWATCH_LOG；
  ④ 静态守卫：fail-open（全 try/catch）、日志有轮转上限、不起进程、只挂 tool_result。

    python3 board/tests/test_flagwatch.py
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile

import sys as _sys
_sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _pkgpaths import ext_path, require_ext, agent_setting, skip, find_node  # noqa: E402

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
#   自包含（自己 import sys），不依赖本文件是否已 import sys 或用别名。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = pathlib.Path(__file__).resolve().parents[2]
MOD = LAB / "tools" / "flagwatch.mjs"
EXT = require_ext("flag-watch.ts")
# ★ 跨平台 file URL：直接拼 "file://" + 裸路径在 Windows 上会产出
#   file://D:\... （反斜杠被当转义）→ node 报 ERR_INVALID_URL。
#   Path.as_uri() 在两边都合法：Windows→file:///D:/...，POSIX→file:///mnt/...。
EXT_URI = require_ext("flag-watch.ts").as_uri()
NODE = find_node()


def node_run(js: str, env_extra=None, timeout=180):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return _run_esm(js, env, timeout)



def _run_esm(js: str, env: dict, timeout: int):
    """跑一段 ESM：**写临时文件再跑，不用 -e**。

    ★ 2026-11-10（T4 定位的真因）：原来用 -e 传内联脚本。Windows 上命令行超过阈值
      会被**静默截断** —— node 退出码 0、stdout 空、stderr 空，脚本根本没执行。
      表现就是"18 条断言里 7 条全 ✗"，看起来像扩展坏了，实际是**测试驱动方式坏了**。
      实测：短脚本(-e) 正常；2000+ 字符的 -e 静默不执行；同样内容写成文件跑完全正常。
      改用临时文件：跨平台一致，且不受命令行长度限制。
    """
    with tempfile.TemporaryDirectory() as _td:
        _p = os.path.join(_td, "_probe.mjs")
        pathlib.Path(_p).write_text(js, encoding="utf-8")
        return subprocess.run(  # noqa: S603
            [NODE, "--experimental-strip-types", _p],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=env)

def main() -> int:
    checks: list = []

    def ok(name, cond, extra=""):
        checks.append((name, bool(cond), str(extra)))

    ok("node 可用", bool(NODE), "本机没有 node → 扩展与测试都跑不了")
    ok("flagwatch.mjs 存在", MOD.is_file())
    ok("flag-watch.ts 存在", EXT.is_file())
    if not NODE:
        return _report(checks)

    # ① mjs 自检
    p = subprocess.run([NODE, str(MOD), "--selftest"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)  # noqa: S603
    ok("flagwatch.mjs --selftest 全绿（rc=0）",
       p.returncode == 0 and "OK" in p.stdout, (p.stdout + p.stderr)[-200:])

    # ②③ 用假 pi 直接驱动真钩子（日志隔离到临时文件）
    with tempfile.TemporaryDirectory() as td:
        log = os.path.join(td, "fw.jsonl")
        js = f"""
const ext = (await import("{EXT_URI}")).default;
const handlers = {{}};
const notifies = [];
const pi = {{ on: (name, fn) => {{ handlers[name] = fn; }}, exec: () => {{ throw new Error("不该起进程"); }} }};
const ctx = {{ ui: {{ notify: (t, l) => notifies.push([t, l]) }} }};
ext(pi);
if (!handlers.tool_result) {{ console.error("没有注册 tool_result"); process.exit(9); }}

const run = (content, tool="bash") => handlers.tool_result(
  {{ toolName: tool, toolCallId: "t1", content }}, ctx);

// 命中：数组形态（event.content 是 parts 数组）
const r1 = await run([{{ type: "text", text: "x " + "FLAG{{auto_detect_1}}" }}]);
const r1text = (r1?.content || []).map(p => p.text || "").join("\\n");
console.log(JSON.stringify({{
  hit_modified: !!r1 && r1text.includes("flag-watch") && r1text.includes("FLAG{{auto_detect_1}}"),
  hit_notify: notifies.length > 0,
  array_kept_original: r1text.includes("x "),
}}));

// 去重：同一个 flag 第二次不再插
const r2 = await run([{{ type: "text", text: "again FLAG{{auto_detect_1}}" }}]);
console.log(JSON.stringify({{ deduped: r2 === undefined }}));

// 负例：没有 flag 形状 → 不改内容
const r3 = await run([{{ type: "text", text: "no flags here, just else{{x}} and ${{VAR}}" }}]);
console.log(JSON.stringify({{ neg_untouched: r3 === undefined }}));

// 字符串形态的 content
const r4 = await run("FLAG{{string_shape_2}}");
console.log(JSON.stringify({{ str_modified: typeof r4?.content === "string" && r4.content.includes("flag-watch") }}));
"""
        env = {"PI_LAB_FLAGWATCH_LOG": log}
        r = node_run(js, env_extra=env)
        out = r.stdout
        got = {}
        for line in out.strip().splitlines():
            try:
                got.update(json.loads(line))
            except Exception:
                pass
        ok("命中时警告插进了 tool_result（数组形态）", got.get("hit_modified"),
           (r.stdout + r.stderr)[-300:])
        ok("命中时 notify 出声", got.get("hit_notify"), (r.stdout + r.stderr)[-300:])
        ok("原始内容没被吃掉", got.get("array_kept_original"), out[-200:])
        ok("同 session 同 flag 只报一次（去重）", got.get("deduped"), out[-200:])
        ok("没有 flag 形状 → 原样放行", got.get("neg_untouched"), out[-200:])
        ok("字符串形态 content 也处理", got.get("str_modified"), out[-200:])
        # 日志：写到指定文件、是 JSONL、含 flag
        ok("留痕写进 PI_LAB_FLAGWATCH_LOG", os.path.isfile(log))
        if os.path.isfile(log):
            lines = [l for l in pathlib.Path(log).read_text(encoding="utf-8").splitlines() if l.strip()]
            ok("日志是合法 JSONL", all(_is_json(l) for l in lines), lines[:1])
            ok("日志含命中 flag（可复核）",
               any("auto_detect_1" in l for l in lines), lines[:2])

    # ④ 静态守卫
    src = EXT.read_text(encoding="utf-8")
    ok("fail-open：核心路径包在 try/catch 里",
       src.count("try {") >= 4 and "} catch" in src)
    ok("加载失败就静默禁用（mod=null 放行）", "fail-open" in src)
    ok("日志有轮转上限（防涨爆）", "MAX_LOG" in src and "renameSync" in src)
    ok("不起进程（无 child_process / spawn / exec）",
       not re.search(r"child_process|spawn\(|execFile", src))
    ok("只挂 tool_result 钩子", 'pi.on("tool_result"' in src and src.count("pi.on(") == 1)
    ok("不直接判真伪（交给 fgs verify / confirm）",
       "fgs verify" in src and "fgs confirm" in src)
    ok("同一 session 去重用了 Set", "new Set()" in src)

    return _report(checks)


def _is_json(s: str) -> bool:
    try:
        json.loads(s)
        return True
    except Exception:
        return False


def _report(checks) -> int:
    passed = sum(1 for _, c, _ in checks if c)
    for name, c, extra in checks:
        print(f"  {'✓' if c else '✗'} {name}" + (f"  {extra}" if extra and not c else ""))
    print(f"\n{passed}/{len(checks)} 断言通过")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
