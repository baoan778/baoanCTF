#!/usr/bin/env python3
"""lane-watch 回归测试 —— 治「开了黑板、却一路子代理都不派」。

血账（2026-10-12 实测，不是推测）
----------------------------------
会话 `01a12e9b`（奶蛙服务器）：`fgs kickoff` 打印了**可直接粘的两路派活命令**，
然后 agent 用它做了 **15 次工具调用、0 次 subagent** —— 一个人把题做完了。
本仓以前只有两处兜底，都不够：① kickoff / AGENTS 的**文字**提醒（模型会划过去）；
② `fgs solved` 的**事后**闸门（题都做完了才拒）。
所以补**在途中**的机器闸门，与 flag-watch 同一套机制：

  · `tools/lanewatch.mjs`            —— 纯逻辑（零依赖，node 直接跑，--selftest）
  · `.pi/agent/extensions/lane-watch.ts` —— tool_call 钩子：kickoff 后 0 路 → 拦

本测试守住五件事（全离线；日志写临时文件，**不碰真产物**）：
  ① mjs --selftest 全绿；
  ② 钩子行为：kickoff 装弹 → GRACE 次放行 → 第 GRACE+1 次**拦**，理由含 pid + 可照做的 subagent 调用；
  ③ plumbing（`fgs brief/next/…`、`lane_gate`、`sesscan`）不算「自己做题」，超线也放行；
  ④ 派过 subagent 就永久解除；拦满 MAX_BLOCKS 后 **fail-open**（放行 + 软提醒），
     且没跑过 kickoff 的会话 30 次调用一次都不拦（写代码/运维不受影响）；
  ⑤ 静态守卫：全程 try/catch（fail-open）、不起进程、注册了 tool_call、逃生通道齐。

    python3 board/tests/test_lanewatch.py
"""
from __future__ import annotations

import json
import os
import pathlib
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
MOD = LAB / "tools" / "lanewatch.mjs"
EXT = require_ext("lane-watch.ts")
# ★ 跨平台 file URL：直接拼 "file://" + 裸路径在 Windows 上会产出
#   file://D:\... （反斜杠被当转义）→ node 报 ERR_INVALID_URL。
#   Path.as_uri() 在两边都合法：Windows→file:///D:/...，POSIX→file:///mnt/...。
EXT_URI = require_ext("lane-watch.ts").as_uri()
NODE = find_node()

checks: list = []


def ok(name, cond, extra=""):
    checks.append((name, bool(cond), str(extra)))


def _report():
    bad = [c for c in checks if not c[1]]
    for name, good, extra in checks:
        print(f"  {'✓' if good else '✗'} {name}" + (f"   {extra[:200]}" if extra and not good else ""))
    print(f"\n通过 {len(checks) - len(bad)} / 失败 {len(bad)}")
    if bad:
        print("失败项：" + "、".join(c[0] for c in bad))
        return 1
    print("✓ 派活闸门（lane-watch）：全部通过")
    return 0


def _run_esm(js: str, env: dict, timeout: int):
    """跑一段 ESM：**写临时文件再跑，不用 -e**。

    ★ 2026-11-10（T4 定位的真因）：原来用 `-e` 传内联脚本。Windows 上命令行
      超过阈值会被**静默截断** —— node 退出码 0、stdout 空、stderr 空，脚本根本没执行。
      表现就是"18 条断言里 7 条全 ✗"，看起来像扩展坏了，实际是**测试驱动方式坏了**。
      实测：短脚本(-e)正常；2000+ 字符的 -e 静默不执行；同样内容写成文件跑完全正常。
      改用临时文件：跨平台一致，且不受命令行长度限制。
    """
    with tempfile.TemporaryDirectory() as _td:
        _p = os.path.join(_td, "_probe.mjs")
        pathlib.Path(_p).write_text(js, encoding="utf-8")
        return subprocess.run(  # noqa: S603
            [NODE, "--experimental-strip-types", _p],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=env)


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
    ok("node 可用", bool(NODE), "本机没有 node → 扩展与测试都跑不了")
    ok("tools/lanewatch.mjs 存在", MOD.is_file())
    ok("lane-watch.ts 存在", EXT.is_file())
    if not NODE:
        return _report()

    p = subprocess.run([NODE, str(MOD), "--selftest"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)  # noqa: S603
    ok("lanewatch.mjs --selftest 全绿（rc=0）",
       p.returncode == 0 and "OK" in p.stdout, (p.stdout + p.stderr)[-300:])

    with tempfile.TemporaryDirectory() as td:
        log = os.path.join(td, "lw.jsonl")
        js = f"""
const ext = (await import("{EXT_URI}")).default;
const handlers = {{}};
const notifies = [];
const pi = {{
  on: (name, fn) => {{ handlers[name] = fn; }},
  registerCommand: (n) => {{ handlers["cmd:" + n] = true; }},
  exec: () => {{ throw new Error("不该起进程"); }},
}};
const ctx = {{ ui: {{ notify: (t, l) => notifies.push([t, l]) }} }};
ext(pi);
console.log(JSON.stringify({{
  has_tool_call: !!handlers.tool_call,
  has_tool_result: !!handlers.tool_result,
  has_command: !!handlers["cmd:lanes"],
}}));

const call = (name, input) => handlers.tool_call({{ toolName: name, toolCallId: "t", input }}, ctx);
const res  = (text) => handlers.tool_result({{ toolName: "bash", toolCallId: "t", content: [{{type:"text",text}}] }}, ctx);

const seq = [];
// ① 没跑 kickoff 的会话：不该有任何拦截
let blocked = 0;
for (let i = 0; i < 30; i++) {{ const r = await call("bash", {{command: "echo hi"}}); if (r?.block) blocked++; }}
seq.push(["no_kickoff_blocks", blocked]);

// ② kickoff 装弹 → GRACE 次放行 → 第 GRACE+1 次拦
await call("bash", {{ command: "cd $PI_LAB_ROOT && fgs kickoff lw-test --kind ctf --goal g" }});
await res("★ 第 1 轮：派 2 路**独立全题分析**\\n  ── 第 1 路 · 独立全题分析 A ──\\n  subagent({{ agent: \\"worker\\", task: \\"A\\" }})\\n★ 第 2 轮起");
let firstBlockAt = -1, reason = "";
for (let i = 1; i <= 8; i++) {{
  const r = await call("bash", {{ command: "curl http://x/" + i }});
  if (r?.block && firstBlockAt < 0) {{ firstBlockAt = i; reason = r.reason || ""; }}
}}
seq.push(["first_block_at", firstBlockAt]);
seq.push(["reason_has_pid", reason.includes("lw-test")]);
seq.push(["reason_has_call", reason.includes("subagent(")]);
seq.push(["reason_replays_lanes", reason.includes("第 1 路")]);
seq.push(["reason_has_escape", reason.includes("/lanes off")]);
seq.push(["notify_fired", notifies.length > 0]);

// ③ plumbing 在超线后仍放行
seq.push(["plumbing_brief", (await call("bash", {{command:"fgs brief lw-test --as-task"}})) === undefined]);
seq.push(["plumbing_lanegate", (await call("bash", {{command:"lane_gate bg 'x'"}})) === undefined]);
seq.push(["plumbing_sesscan", (await call("bash", {{command:"sesscan where lw-test"}})) === undefined]);

// ④ 派一路 → 永久解除
seq.push(["subagent_allowed", (await call("subagent", {{ task: "x" }})) === undefined]);
seq.push(["after_dispatch_ok", (await call("bash", {{command:"curl http://y/"}})) === undefined]);

// ⑤ 命令行里 pid 是 shell 替换符（`$(ls …)`）：不装弹 → 从 kickoff 输出补认 → 装到真 pid 上
//   （2026-10-13 修：以前会把 `$(ls` / `$P` 抄成 pid，拦截时回放的派活命令指向错项目）
await call("bash", {{ command: "cd $PI_LAB_ROOT && fgs kickoff $(ls board/projects/ | tail -1) --kind ctf --goal g" }});
seq.push(["deferred_no_arm", (await call("bash", {{command:"curl http://z/0"}})) === undefined]);
await res("══━ lw-out  (kind=ctf, rev=1) ═══\\n  ⬜ 未解决\\n  ★ 第 1 轮：派 2 路\\n  ── 第 1 路 · 独立全题分析 A ──\\n  subagent(agentA)");
let outBlockAt = -1, outReason = "";
for (let i = 1; i <= 8; i++) {{
  const r = await call("bash", {{ command: "curl http://z/" + i }});
  if (r?.block && outBlockAt < 0) {{ outBlockAt = i; outReason = r.reason || ""; }}
}}
seq.push(["out_arm_first_block_at", outBlockAt]);
seq.push(["out_arm_reason_pid", outReason.includes("lw-out")]);
seq.push(["out_arm_reason_no_junk", !outReason.includes("$(")]);
console.log(JSON.stringify(Object.fromEntries(seq)));
"""
        env = dict(os.environ)
        env["PI_LAB_LANEWATCH_LOG"] = log
        r = _run_esm(js, env, 180)
        got = {}
        for line in r.stdout.strip().splitlines():
            try:
                v = json.loads(line)
                if isinstance(v, dict):
                    got.update(v)
                elif isinstance(v, list):
                    got[v[0]] = v[1]
            except Exception:
                pass
        tail = (r.stdout + r.stderr)[-300:]
        ok("扩展注册了 tool_call / tool_result / /lanes 命令",
           got.get("has_tool_call") and got.get("has_tool_result") and got.get("has_command"), tail)
        ok("没跑 kickoff 的会话一次都不拦（30 次）", got.get("no_kickoff_blocks") == 0, tail)
        ok("kickoff 后第 7 次才拦（GRACE=6）", got.get("first_block_at") == 7, f"{got.get('first_block_at')} {tail}")
        ok("拦截理由含 pid", got.get("reason_has_pid"), tail)
        ok("拦截理由含可直接照做的 subagent 调用", got.get("reason_has_call"), tail)
        ok("拦截理由原样回放 kickoff 打的两路", got.get("reason_replays_lanes"), tail)
        ok("拦截理由写了逃生通道", got.get("reason_has_escape"), tail)
        ok("拦截时 notify 出声", got.get("notify_fired"), tail)
        ok("plumbing 放行：fgs brief", got.get("plumbing_brief"), tail)
        ok("plumbing 放行：lane_gate", got.get("plumbing_lanegate"), tail)
        ok("plumbing 放行：sesscan", got.get("plumbing_sesscan"), tail)
        ok("subagent 放行", got.get("subagent_allowed"), tail)
        ok("派过之后任意工具放行", got.get("after_dispatch_ok"), tail)
        # 2026-10-13 新增：shell 替换符 pid 的处理
        ok("kickoff 的 pid 是 $(…) 时不装弹（不误拦）", got.get("deferred_no_arm") is True, tail)
        ok("从 kickoff 输出补认 pid 后照常 GRACE+1 拦", got.get("out_arm_first_block_at") == 7, f"{got.get('out_arm_first_block_at')} {tail}")
        ok("补认后的理由指向真 pid（不含 $() 垃圾）",
           got.get("out_arm_reason_pid") and got.get("out_arm_reason_no_junk"), tail)

        ok("留痕写进 PI_LAB_LANEWATCH_LOG", os.path.isfile(log))
        if os.path.isfile(log):
            lines = [l for l in pathlib.Path(log).read_text(encoding="utf-8").splitlines() if l.strip()]
            ok("日志是合法 JSONL", all(l.startswith("{") for l in lines), lines[:1])
            ok("日志含 arm 与 block（可复核）",
               any('"arm"' in l for l in lines) and any('"block"' in l for l in lines),
               lines[:2])

    # ⑤ fail-open：拦满 3 次后放行 + 软提醒（单独一轮，避免污染上面的序列）
    with tempfile.TemporaryDirectory() as td:
        log2 = os.path.join(td, "lw2.jsonl")
        js = f"""
const ext = (await import("{EXT_URI}")).default;
const handlers = {{}};
const pi = {{ on: (n, f) => {{ handlers[n] = f; }}, registerCommand: () => {{}}, }};
const ctx = {{ ui: {{ notify: () => {{}} }} }};
ext(pi);
const call = (n, i) => handlers.tool_call({{ toolName: n, toolCallId: "t", input: i }}, ctx);
const res  = (t) => handlers.tool_result({{ toolName: "bash", toolCallId: "t", content: [{{type:"text",text:t}}] }}, ctx);
await call("bash", {{ command: "fgs kickoff lw2 --kind ctf" }});
let blocks = 0;
for (let i = 0; i < 12; i++) {{ const r = await call("bash", {{command:"curl z/" + i}}); if (r?.block) blocks++; }}
const soft = await res("some output " + "x".repeat(40));
const softText = Array.isArray(soft?.content) ? soft.content.map(p => p.text || "").join("") : "";
console.log(JSON.stringify({{ blocks: blocks, soft: softText.includes("派活闸门") }}));
"""
        env = dict(os.environ)
        env["PI_LAB_LANEWATCH_LOG"] = log2
        r = _run_esm(js, env, 180)
        got2 = {}
        for line in r.stdout.strip().splitlines():
            try:
                got2.update(json.loads(line))
            except Exception:
                pass
        ok("硬拦上限 = 3（之后 fail-open）", got2.get("blocks") == 3, str(got2) + (r.stdout + r.stderr)[-200:])
        ok("fail-open 后插软提醒进 tool_result", got2.get("soft"), str(got2))

    # 静态守卫
    src = EXT.read_text(encoding="utf-8")
    ok("全程 try/catch（fail-open：闸门自己坏了也不拖累 pi）",
       src.count("catch") >= 3, str(src.count("catch")))
    ok("不起进程（无 spawn/exec/child_process）",
       not any(k in src for k in ("child_process", "spawn(", "execSync")), "扩展必须纯 fs")
    ok("逃生通道齐（/lanes · .no-lanes · PI_LAB_LANES_GATE）",
       all(k in src for k in ("/lanes", ".no-lanes", "PI_LAB_LANES_GATE")))
    ok("不改工具参数（只 block 或放行）", "{ block: true" in src and "event.input." not in src)
    # 逻辑与扩展必须成对存在（缺一个 = 静默失效）
    ok("mjs 与 ts 成对（一缺就静默不生效）", MOD.is_file() and EXT.is_file())

    # ★ 2026-11-02 补：LAB 不许裸信 cwd。
    #   旧写法 `const LAB = process.env.PI_LAB_ROOT || process.cwd()` 把「仓库根」
    #   寄托在**用户启动 pi 时所在目录**上：从仓库子目录启动 → MOD 指向不存在的
    #   lanewatch.mjs → loadMod() catch → mod=null → 每条 tool_call 直接 return。
    #   闸门静默变哑弹，台账里连 arm 都没有（实测子目录启动 0 拦 / 仓库根 3 拦）。
    #   现在必须走 resolveLab()（多候选 + tools/lanewatch.mjs 存在性校验）。
    ok("LAB 走 resolveLab()（不裸信 cwd）",
       "resolveLab()" in src and "const LAB = process.env.PI_LAB_ROOT || process.cwd();" not in src,
       "发现退回裸 cwd 写法 → 子目录启动时闸门会静默失效")
    ok("resolveLab 校验 tools/lanewatch.mjs 真存在（假候选会被跳过）",
       'path.join(c, "tools", "lanewatch.mjs")' in src and "fs.existsSync" in src)
    ok("resolveLab 支持从 cwd 向上逐级查找（子目录启动也能命中）",
       "path.dirname(d)" in src and "for (let i = 0" in src)
    return _report()


if __name__ == "__main__":
    raise SystemExit(main())
