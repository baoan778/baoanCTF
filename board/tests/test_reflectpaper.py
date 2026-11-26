#!/usr/bin/env python3
"""reflect-paper 回归测试 —— 治「纪律靠自觉，等于没有」。

由来（2026-11-06，E-6）
----------------------
`skills/fgs-orchestrate` 写了"定期盘点方向"的纪律，但**没有任何机制在跑偏时提醒**。
OpenSecurity 2.0 用「距上次盘点时长 × 工具调用密度」触发（默认 30 分钟），
忙时把提醒**附在命令输出之后**（不打断），空闲才发唤醒消息。

本仓实现忙时通道（空闲通道由 idle-resume 负责）。

本测试守六件事（全离线；临时目录，不碰真产物）：
  ① tools/reflectwatch.mjs --selftest 全绿；
  ② 净活跃口径 = 墙钟 - 等人（**不是墙钟**，否则权限询问挂 20 分钟也算"跑了"）；
  ③ 触发条件双门槛：净活跃够 **且** 工具调用够（防"想了 30 分钟没动"误报）；
  ④ 纸条必须带四选一（继续/换向/放弃/深挖）与可执行判据命令；
  ⑤ 有上限（单场 ≤8 次）与最小间隔；
  ⑥ 扩展静态守卫：挂 tool_result、追加而非新建消息、fail-open、逃生门齐。

    python3 board/tests/test_reflectpaper.py
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import tempfile

import sys as _sys
_sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _pkgpaths import require_ext  # noqa: E402

try:
    import sys as _sm
    for _s in (_sm.stdout, _sm.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = pathlib.Path(__file__).resolve().parents[2]
MOD = LAB / "tools" / "reflectwatch.mjs"
EXT = require_ext("reflect-paper.ts")
NODE = None
for cand in ("node", "node.exe"):
    try:
        p = subprocess.run([cand, "--version"], capture_output=True, timeout=20)  # noqa: S603
        if p.returncode == 0:
            NODE = cand
            break
    except Exception:
        pass

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
    print("✓ 反思纸条（reflect-paper）：全部通过")
    return 0


def run_node_file(script: str, cwd: str, env: dict | None = None):
    """落临时 .mjs 再跑（本机 `node -e` 传长内联脚本会崩，血账见 test_idleresume）。

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
            [NODE, path], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=180, cwd=cwd, env=e,
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main() -> int:
    ok("node 可用", bool(NODE))
    ok("tools/reflectwatch.mjs 存在", MOD.is_file())
    ok("reflect-paper.ts 存在", EXT.is_file())
    if not NODE or not MOD.is_file() or not EXT.is_file():
        return _report()

    # ① 纯逻辑自检
    p = subprocess.run([NODE, str(MOD), "--selftest"], capture_output=True, text=True,  # noqa: S603
                       encoding="utf-8", errors="replace", timeout=120)
    ok("reflectwatch.mjs --selftest 全绿（rc=0）",
       p.returncode == 0 and "OK" in p.stdout, (p.stdout + p.stderr)[-300:])

    # ⑥ 静态守卫
    src = EXT.read_text(encoding="utf-8", errors="replace")
    ok("挂 tool_result（不新起一轮）", 'pi.on("tool_result"' in src)
    ok("返回 content（追加）而非 message", "return {\n        content:" in src.replace("\r\n", "\n")
       or "content: [" in src)
    ok("净活跃口径（扣等人）", "waitMs" in src and "netActiveNow" in src)
    ok("监听 ui_prompt_start/end", 'pi.on("ui_prompt_start"' in src and 'pi.on("ui_prompt_end"' in src)
    ok("逃生门：/reflect 命令", 'registerCommand("reflect"' in src)
    ok("逃生门：.no-reflect 文件", ".no-reflect" in src)
    ok("逃生门：环境变量", "PI_LAB_REFLECT" in src)
    ok("fail-open 注释在", "fail-open" in src)

    # ②③④⑤ 端到端
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / "tools").mkdir(parents=True, exist_ok=True)
        (root / "log").mkdir(parents=True, exist_ok=True)
        (root / "tools" / "reflectwatch.mjs").write_text(
            MOD.read_text(encoding="utf-8"), encoding="utf-8")

        js = f"""
const ext = (await import("{EXT.as_uri()}")).default;
const handlers = {{}};
const notifies = [];
const pi = {{
  on: (n, fn) => {{ (handlers[n] ||= []).push(fn); }},
  registerCommand: (n) => {{ handlers["cmd:" + n] = true; }},
  exec: () => {{ throw new Error("不该起进程"); }},
}};
const ctx = {{ ui: {{ notify: (t, l) => notifies.push([t, l]) }} }};
ext(pi);

const out = {{}};
out.has_tool_result = !!handlers.tool_result;
out.has_cmd = !!handlers["cmd:reflect"];
out.has_ui_hooks = !!handlers.ui_prompt_start && !!handlers.ui_prompt_end;

// 立刻跑一次工具结果：净活跃≈0 → 不该出纸条
const callResult = async () => {{
  const hs = handlers.tool_result || [];
  if (!hs.length) return null;
  return await hs[0]({{ toolName: "bash", toolCallId: "t",
                        content: [{{ type: "text", text: "ok" }}], isError: false }}, ctx);
}};
const r0 = await callResult();
out.early_no_paper = !(r0 && Array.isArray(r0.content)
  && r0.content.some(p => String(p.text || "").includes("反思纸条")));

console.log(JSON.stringify(out));
"""
        env = {"PI_LAB_ROOT": td, "PI_LAB_REFLECT": "1", "PATH": os.environ.get("PATH", "")}
        pr = run_node_file(js, td, env)
        blob = (pr.stdout or "") + (pr.stderr or "")
        try:
            res = json.loads(pr.stdout.strip().splitlines()[-1]) if pr.stdout.strip() else {}
        except Exception:
            res = {}

        ok("扩展注册 tool_result", res.get("has_tool_result") is True, blob[-400:])
        ok("扩展注册 /reflect", res.get("has_cmd") is True, blob[-400:])
        ok("扩展监听 UI 等待（净活跃口径）", res.get("has_ui_hooks") is True, blob[-400:])
        ok("净活跃不足时不出纸条", res.get("early_no_paper") is True, blob[-400:])

    # ②③④⑤ 用纯函数精确验证（不依赖时钟）
    js2 = f"""
const m = await import("{MOD.as_uri()}");
const out = {{}};
// 净活跃口径
out.net = [m.netActive(3600000, 1200000), m.netActive(1000, 5000)];
// 到间隔但工具少 → 不打扰
out.few_calls = m.decideReflect({{ activeMs: 40*60000, toolCalls: 3 }}).kind;
// 到间隔且够 → 出纸条，且带四选一
const d = m.decideReflect({{ activeMs: 40*60000, toolCalls: 50 }});
out.due_action = d.action;
out.paper_choices = ["继续","换向","放弃","深挖"].filter(k => (d.paper||"").includes(k));
out.paper_has_cmd = /fgs snapshot/.test(d.paper || "");
// 上限与间隔
out.capped = m.decideReflect({{ activeMs: 999*60000, toolCalls: 500, nudges: 8 }}).kind;
out.gap = m.decideReflect({{ activeMs: 40*60000, toolCalls: 50, nudges: 1,
                            lastNudgeActiveMs: 39*60000 }}).kind;
console.log(JSON.stringify(out));
"""
    pr2 = run_node_file(js2, str(LAB))
    try:
        r2 = json.loads(pr2.stdout.strip().splitlines()[-1]) if pr2.stdout.strip() else {}
    except Exception:
        r2 = {}
    bl2 = (pr2.stdout or "") + (pr2.stderr or "")

    ok("净活跃 = 墙钟 - 等人", r2.get("net") == [2400000, 0], bl2[-300:])
    ok("工具调用少 → 不打扰", r2.get("few_calls") == "not-enough-calls", bl2[-300:])
    ok("净活跃够 + 调用够 → 出纸条", r2.get("due_action") == "nudge", bl2[-300:])
    ok("纸条带四选一（继续/换向/放弃/深挖）",
       r2.get("paper_choices") == ["继续", "换向", "放弃", "深挖"], bl2[-300:])
    ok("纸条带可执行判据命令（fgs snapshot）", r2.get("paper_has_cmd") is True, bl2[-300:])
    ok("上限生效", r2.get("capped") == "capped", bl2[-300:])
    ok("最小间隔生效", r2.get("gap") == "gap", bl2[-300:])

    return _report()


if __name__ == "__main__":
    raise SystemExit(main())
