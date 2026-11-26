#!/usr/bin/env python3
"""tool-trace 回归测试 —— 治「压缩后原始输出没了，只剩记忆复述」。

血账（2026-11-06）
------------------
`fgs verify` 的逐字溯源要求「这个值真的出现在某条真实输出里」。
但会话一旦压缩/重启，"那条输出"就消失了 —— 人只能凭记忆复述，
而**记忆复述正是编造值的来源**。
现有机制都不够：`flag-watch` 只记命中 flag 形状的片段；`log.jsonl` 只记黑板事件。

本测试守六件事（全离线；临时目录，不碰真产物）：
  ① tools/tracewatch.mjs --selftest 全绿；
  ② 裁剪顺序正确：**先首尾裁剪再压单行**（顺序反了会吃掉结尾的 TAIL —— 真踩过）；
  ③ 有界：超长输出既保留开头也保留结尾，中间有省略标记；
  ④ 纯写工具（write/edit）不留痕（省空间）；
  ⑤ 扩展静态守卫：挂 tool_result、追加不新建消息、fail-open、逃生门齐；
  ⑥ 端到端：跑一次钩子 → 落盘文件存在、JSON 可解析、含 out 字段。

    python3 board/tests/test_tooltrace.py
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
MOD = LAB / "tools" / "tracewatch.mjs"
EXT = require_ext("tool-trace.ts")

NODE = None
for cand in ("node", "node.exe"):
    try:
        if subprocess.run([cand, "--version"], capture_output=True, timeout=20).returncode == 0:  # noqa: S603
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
    print("✓ 工具输出留痕（tool-trace）：全部通过")
    return 0


def run_node_file(script: str, cwd: str, env: dict | None = None):
    """落临时 .mjs 再跑（`node -e` 传长脚本会崩；临时文件放系统 temp 不放 cwd）。"""
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as fh:
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
    ok("tools/tracewatch.mjs 存在", MOD.is_file())
    ok("tool-trace.ts 存在", EXT.is_file())
    if not (NODE and MOD.is_file() and EXT.is_file()):
        return _report()

    # ① 纯逻辑自检
    p = subprocess.run([NODE, str(MOD), "--selftest"], capture_output=True, text=True,  # noqa: S603
                       encoding="utf-8", errors="replace", timeout=120)
    ok("tracewatch.mjs --selftest 全绿（rc=0）",
       p.returncode == 0 and "OK" in p.stdout, (p.stdout + p.stderr)[-300:])

    # ⑤ 静态守卫
    src = EXT.read_text(encoding="utf-8", errors="replace")
    ok("挂 tool_result", 'pi.on("tool_result"' in src)
    ok("不新起一轮（不 sendUserMessage）", "sendUserMessage" not in src)
    ok("有轮转（只留一代）", ".jsonl.1" in src and "renameSync" in src)
    ok("有大小上限", "MAX_LOG_BYTES" in src)
    ok("逃生门：/trace 命令", 'registerCommand("trace"' in src)
    ok("逃生门：.no-trace 文件", ".no-trace" in src)
    ok("逃生门：环境变量", "PI_LAB_TOOLTRACE" in src)
    ok("fail-open 注释在", "fail-open" in src)
    ok("按天分文件", "toISOString().slice(0, 10)" in src)

    # ②③④ 精确验证纯函数（不依赖时钟）
    js = f"""
const m = await import("{MOD.as_uri()}");
const out = {{}};

// ② 裁剪顺序：超长单行必须**同时**保留开头与结尾
const long = "HEAD" + "y".repeat(20000) + "TAIL";
const c = m.clip(long, {{ headChars: 100, tailChars: 100, maxLineChars: 400 }});
out.keep_head = c.text.startsWith("HEAD");
out.keep_tail = c.text.endsWith("TAIL");
out.has_marker = /省略 \\d+ 字符/.test(c.text);
out.omitted_gt0 = c.omitted > 0;

// ③ 有界：裁剪后长度远小于原文
out.bounded = c.text.length < 1000;

// ④ 纯写工具不留
out.write_skip = m.shouldTrace("write", {{}}, "ok").keep === false;
out.edit_skip = m.shouldTrace("edit", {{}}, "ok").keep === false;
out.bash_keep = m.shouldTrace("bash", {{}}, "hello").keep === true;
out.empty_skip = m.shouldTrace("bash", {{}}, "   ").keep === false;

// record 字段
const r = m.makeRecord({{ ts: "T", pid: "p", tool: "bash",
                          input: {{ command: "curl x\\nline2" }},
                          resultText: "y".repeat(30000), isError: false }},
                       {{ headChars: 50, tailChars: 50, maxLineChars: 200 }});
out.rec_has_out = typeof r.out === "string" && r.out.length > 0;
out.rec_cmd_first_line = r.cmd === "curl x";
out.rec_out_len = r.out_len === 30000;

console.log(JSON.stringify(out));
"""
    pr = run_node_file(js, str(LAB))
    blob = (pr.stdout or "") + (pr.stderr or "")
    try:
        r = json.loads(pr.stdout.strip().splitlines()[-1]) if pr.stdout.strip() else {}
    except Exception:
        r = {}

    ok("② 裁剪保留开头", r.get("keep_head") is True, blob[-300:])
    ok("② 裁剪保留结尾（顺序正确）", r.get("keep_tail") is True, blob[-300:])
    ok("③ 中间有省略标记", r.get("has_marker") is True, blob[-300:])
    ok("③ omitted 计数 > 0", r.get("omitted_gt0") is True, blob[-300:])
    ok("③ 裁剪后有界（<1000 字符）", r.get("bounded") is True, blob[-300:])
    ok("④ write 不留痕", r.get("write_skip") is True, blob[-300:])
    ok("④ edit 不留痕", r.get("edit_skip") is True, blob[-300:])
    ok("④ bash 留痕", r.get("bash_keep") is True, blob[-300:])
    ok("④ 空输出不留", r.get("empty_skip") is True, blob[-300:])
    ok("record 含 out 字段", r.get("rec_has_out") is True, blob[-300:])
    ok("record cmd 只取首行", r.get("rec_cmd_first_line") is True, blob[-300:])
    ok("record out_len 是原文长度", r.get("rec_out_len") is True, blob[-300:])

    # ⑥ 端到端：真跑钩子，验证落盘
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        (root / "tools").mkdir(parents=True, exist_ok=True)
        (root / "log").mkdir(parents=True, exist_ok=True)
        (root / "tools" / "tracewatch.mjs").write_text(
            MOD.read_text(encoding="utf-8"), encoding="utf-8")

        js2 = f"""
const ext = (await import("{EXT.as_uri()}")).default;
const handlers = {{}};
const pi = {{ on: (n, f) => {{ (handlers[n] ||= []).push(f); }},
             registerCommand: (n) => {{ handlers["cmd:" + n] = true; }},
             exec: () => {{ throw new Error("不该起进程"); }} }};
ext(pi);
const out = {{ has_tool_result: !!handlers.tool_result,
              has_cmd: !!handlers["cmd:trace"] }};
// 先跑一条 fgs 命令认 pid，再喂一条工具结果
for (const h of (handlers.tool_call || []))
  await h({{ toolName: "bash", toolCallId: "t1",
             input: {{ command: "fgs kickoff demo --kind ctf --goal g" }} }}, {{}});
for (const h of (handlers.tool_result || []))
  await h({{ toolName: "bash", toolCallId: "t1",
             input: {{ command: "curl -i http://x" }},
             content: [{{ type: "text", text: "HTTP/1.1 200\\nflag: SUN{{abc123}}" }}],
             isError: false }}, {{}});
// 再喂一条 write（应被跳过）
for (const h of (handlers.tool_result || []))
  await h({{ toolName: "write", toolCallId: "t2",
             input: {{ path: "/tmp/a" }},
             content: [{{ type: "text", text: "written" }}], isError: false }}, {{}});
console.log(JSON.stringify(out));
"""
        env = {"PI_LAB_ROOT": td, "PI_LAB_TOOLTRACE": "1", "PATH": os.environ.get("PATH", "")}
        pr2 = run_node_file(js2, td, env)
        blob2 = (pr2.stdout or "") + (pr2.stderr or "")
        try:
            r2 = json.loads(pr2.stdout.strip().splitlines()[-1]) if pr2.stdout.strip() else {}
        except Exception:
            r2 = {}

        ok("⑥ 扩展注册 tool_result", r2.get("has_tool_result") is True, blob2[-400:])
        ok("⑥ 扩展注册 /trace", r2.get("has_cmd") is True, blob2[-400:])

        # 找出落盘文件
        trace_root = root / "log" / "tool-trace"
        files = list(trace_root.rglob("*.jsonl")) if trace_root.exists() else []
        ok("⑥ 落盘文件已生成", len(files) >= 1, f"trace_root={trace_root} files={files}")
        if files:
            lines = [l for l in files[0].read_text(encoding="utf-8").splitlines() if l.strip()]
            ok("⑥ 只写了 1 条（write 被跳过）", len(lines) == 1, f"实际 {len(lines)} 条")
            if lines:
                try:
                    rec = json.loads(lines[0])
                    ok("⑥ 落盘 JSON 可解析", True)
                    ok("⑥ 含 out 字段", "out" in rec, str(rec)[:200])
                    ok("⑥ out 里有原文关键内容", "SUN{abc123}" in str(rec.get("out")), str(rec.get("out"))[:200])
                    ok("⑥ 记了 pid", rec.get("pid") == "demo", str(rec.get("pid")))
                    ok("⑥ 记了 cmd", "curl" in str(rec.get("cmd")), str(rec.get("cmd")))
                except Exception as e:
                    ok("⑥ 落盘 JSON 可解析", False, str(e))

    return _report()


if __name__ == "__main__":
    raise SystemExit(main())
