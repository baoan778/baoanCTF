#!/usr/bin/env python3
"""idle-resume 回归测试 —— 治「模型给出阶段性结论后就安静等人」。

血账 / 由来（2026-11-06 用户定案 既定决策 / 既定决策）
------------------------------------------------
OpenSecurity 2.0 的无人值守靠「随机完成标记」：模型不吐标记就注入 resume。
**判断权在被考核方手上** —— 模型学会"假装没完成"或"假装完成"都能骗过它。

本仓的判据换成**黑板状态**（board/graph.json 的未闭合 Step 数）：
机器产物、模型改不了，比自述可靠一个量级。

本测试守六件事（全离线；临时目录，不碰真产物）：
  ① tools/idlewatch.mjs --selftest 全绿；
  ② 纯函数：判据/停机/上限/停滞/冷却 各分支正确；
  ③ 扩展静态守卫：注册了 agent_before_settle、全程 try/catch、fail-open、
     逃生门齐（/idle off、.no-idle、PI_LAB_IDLE_RESUME=0）；
  ④ 端到端：造一个带 open Step 的黑板 → 钩子返回 continue:true 且带消息；
  ⑤ 端到端：solved / aborted / 无未闭合 → 一律不续推（这四条是"不扰民"的底线）；
  ⑥ 端到端：没开过板的会话一次都不推。

    python3 board/tests/test_idleresume.py
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import tempfile

import sys as _sys
_sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _pkgpaths import require_ext, skip, find_node  # noqa: E402

try:
    import sys as _sm
    for _s in (_sm.stdout, _sm.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = pathlib.Path(__file__).resolve().parents[2]
MOD = LAB / "tools" / "idlewatch.mjs"
EXT = require_ext("idle-resume.ts")
EXT_URI = EXT.as_uri()
NODE = find_node()

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
    print("✓ 空闲续推（idle-resume）：全部通过")
    return 0


def run_node_file(script: str, cwd: str, env: dict | None = None):
    """把脚本写成临时 .mjs 再跑。

    ★ 不用 `node -e`：本机实测 `node -e` 传长内联脚本会直接崩（打印一堆
      multi-isolate 栈帧、rc 非 0），换成落文件执行 100% 稳定。
    ★ 临时文件放系统 temp，**不放 cwd** —— cwd 可能是仓库根（LAB），
      写进去会留 `_probe.mjs` 污染工作区（2026-11-06 实际踩到，差点被 git add 提交）。
      模块解析不受影响：脚本里的 import 用的都是绝对 file:// URI。
    """
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    e = dict(os.environ)
    if env:
        e.update(env)
    try:
        return subprocess.run(  # noqa: S603
            [NODE, path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180, cwd=cwd, env=e,
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# 造一个最小黑板（只写本测试关心的字段）
def mk_board(root: str, pid: str, nodes: dict, *, solved=False, bootstrapped=False, outbox=0):
    d = pathlib.Path(root) / "board" / "projects" / pid
    d.mkdir(parents=True, exist_ok=True)
    (d / "graph.json").write_text(json.dumps({
        "schema": "fgs/v1", "project_id": pid, "kind": "ctf", "revision": 1,
        "nodes": nodes, "edges": [], "solved": solved, "bootstrapped": bootstrapped,
    }, ensure_ascii=False), encoding="utf-8")
    if outbox:
        (d / "outbox.jsonl").write_text("\n".join('{"seq":%d}' % i for i in range(outbox)) + "\n",
                                        encoding="utf-8")
    return d


def main() -> int:
    ok("node 可用", bool(NODE), "本机没有 node → 扩展与测试都跑不了")
    ok("tools/idlewatch.mjs 存在", MOD.is_file())
    ok("idle-resume.ts 存在", EXT.is_file())
    if not NODE or not MOD.is_file() or not EXT.is_file():
        return _report()

    # ① 纯逻辑自检
    p = subprocess.run([NODE, str(MOD), "--selftest"], capture_output=True, text=True,  # noqa: S603
                       encoding="utf-8", errors="replace", timeout=120)
    ok("idlewatch.mjs --selftest 全绿（rc=0）",
       p.returncode == 0 and "OK" in p.stdout, (p.stdout + p.stderr)[-300:])

    # ③ 静态守卫（读源码，不跑）
    src = EXT.read_text(encoding="utf-8", errors="replace")
    ok("注册了 agent_before_settle", 'pi.on("agent_before_settle"' in src)
    ok("返回 continue（而非只发消息）", "continue: true" in src)
    ok("尊重 aborted（用户 Esc 不复活）", "aborted" in src)
    ok("逃生门：/idle 命令", 'registerCommand("idle"' in src)
    ok("逃生门：.no-idle 文件", ".no-idle" in src)
    ok("逃生门：环境变量", "PI_LAB_IDLE_RESUME" in src)
    ok("fail-open：判分模块加载失败不拦", "fail-open" in src)
    msrc = MOD.read_text(encoding="utf-8", errors="replace")
    ok("有双封顶常量（次数/停滞）", "maxResumes" in msrc and "stallLimit" in msrc,
       "常量在 idlewatch.mjs 的 DEFAULTS 里，不在扩展")

    # ②④⑤⑥ 端到端：造黑板 → 跑钩子
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / "tools").mkdir(parents=True, exist_ok=True)
        (root / "log").mkdir(parents=True, exist_ok=True)
        # 把纯逻辑模块拷进临时根，扩展按 PI_LAB_ROOT 找它
        (root / "tools" / "idlewatch.mjs").write_text(
            MOD.read_text(encoding="utf-8"), encoding="utf-8")

        # 项目 A：有 2 个 open Step → 应续推
        mk_board(td, "pA", {
            "g1": {"type": "goal", "status": "active"},
            "s1": {"type": "step", "status": "open"},
            "s2": {"type": "step", "status": "open"},
        }, bootstrapped=True)
        # 项目 B：solved → 应停
        mk_board(td, "pB", {"g1": {"type": "goal", "status": "active"},
                            "s1": {"type": "step", "status": "open"}}, solved=True, bootstrapped=True)
        # 项目 C：没有未闭合 → 应停
        mk_board(td, "pC", {"g1": {"type": "goal", "status": "active"},
                            "s1": {"type": "step", "status": "done"}}, bootstrapped=True)

        js = f"""
const ext = (await import("{EXT_URI}")).default;
const handlers = {{}};
const notifies = [];
const pi = {{
  on: (n, fn) => {{ (handlers[n] ||= []).push(fn); }},
  registerCommand: (n) => {{ handlers["cmd:" + n] = true; }},
  exec: () => {{ throw new Error("不该起进程"); }},
}};
const ctx = {{ ui: {{ notify: (t, l) => notifies.push([t, l]) }}, isIdle: () => true }};
ext(pi);

const out = {{}};
out.has_before_settle = !!handlers.agent_before_settle;
out.has_settled = !!handlers.agent_settled;
out.has_cmd = !!handlers["cmd:idle"];

const callTool = async (cmd) => {{
  for (const h of (handlers.tool_call || [])) await h({{ toolName: "bash", toolCallId: "t", input: {{ command: cmd }} }}, ctx);
}};
const settle = async (aborted=false) => {{
  const hs = handlers.agent_before_settle || [];
  if (!hs.length) return null;
  return await hs[0]({{ type: "agent_before_settle", aborted, entries: [], continue: false,
                       context: {{ canContinue: true }}, outcome: "completed" }}, ctx);
}};

// ⑥ 没开过板（未认到 pid，且 latestProject 会退回唯一项目）→ 这里先造"认到"的对照
await callTool("cd $PI_LAB_ROOT && fgs kickoff pA --kind ctf --goal g");
const rA = await settle(false);
out.pA_continue = !!(rA && rA.continue === true);
out.pA_has_msg = !!(rA && Array.isArray(rA.entries) && rA.entries.length
  && String(rA.entries[0].content || "").includes("黑板"));
out.pA_has_entry_type = !!(rA && rA.entries && rA.entries[0] && rA.entries[0].type === "custom_message");

// ⑤ aborted → 不续推
const rAb = await settle(true);
out.aborted_no_continue = !(rAb && rAb.continue === true);

// ⑤ solved → 不续推
await callTool("cd $PI_LAB_ROOT && fgs kickoff pB --kind ctf --goal g");
const rB = await settle(false);
out.solved_no_continue = !(rB && rB.continue === true);

// ⑤ 没有未闭合 → 不续推
await callTool("cd $PI_LAB_ROOT && fgs kickoff pC --kind ctf --goal g");
const rC = await settle(false);
out.nowork_no_continue = !(rC && rC.continue === true);

console.log(JSON.stringify(out));
"""
        env = {"PI_LAB_ROOT": td, "PI_LAB_IDLE_RESUME": "1", "PATH": os.environ.get("PATH", "")}
        pr = run_node_file(js, td, env)
        blob = (pr.stdout or "") + (pr.stderr or "")
        try:
            res = json.loads(pr.stdout.strip().splitlines()[-1]) if pr.stdout.strip() else {}
        except Exception:
            res = {}

        ok("扩展注册 agent_before_settle", res.get("has_before_settle") is True, blob[-300:])
        ok("扩展注册 agent_settled", res.get("has_settled") is True, blob[-300:])
        ok("扩展注册 /idle 命令", res.get("has_cmd") is True, blob[-300:])
        ok("有 open Step → 续推（continue:true）", res.get("pA_continue") is True, blob[-400:])
        ok("续推消息解释「黑板判据」", res.get("pA_has_msg") is True, blob[-400:])
        ok("续推条目是 custom_message", res.get("pA_has_entry_type") is True, blob[-400:])
        ok("aborted → 不续推", res.get("aborted_no_continue") is True, blob[-400:])
        ok("solved → 不续推", res.get("solved_no_continue") is True, blob[-400:])
        ok("无未闭合 → 不续推", res.get("nowork_no_continue") is True, blob[-400:])

        # 日志确实落了盘（可复盘）
        lg = pathlib.Path(td) / "log" / "idle-resume.jsonl"
        ok("日志 idle-resume.jsonl 已生成", lg.is_file())
        if lg.is_file():
            txt = lg.read_text(encoding="utf-8", errors="replace")
            ok("日志里记了 resume", '"action":"resume"' in txt or '"action": "resume"' in txt)
            ok("日志里记了 stop 原因", '"action":"stop"' in txt or '"action": "stop"' in txt)

    return _report()


if __name__ == "__main__":
    raise SystemExit(main())
