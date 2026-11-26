#!/usr/bin/env python3
"""kill-guard 回归测试 —— 治「按进程名批量杀 → 打死整条控制链 → 整场空转」。

血账（2026-11-20 15:47:03 实测，见 log/shard-20261120.log）
------------------------------------------------------------
xben-092-24 执行：
    for pid in $(ps -eo pid,comm | awk '$2=="python3"{print $1}'); do kill $pid; done
打死 1390 supervise / 1391 parallel_solver / 51009 sdkshard → **整场空转 26 分钟**。

  · tools/killguard.mjs                   —— 纯逻辑（零依赖，node 跑，--selftest）
  · agent-template/extensions/kill-guard.ts —— tool_call 钩子：判定命中 → block

本测试守住六件事（全离线；日志写临时文件，不碰真产物）：
  ① mjs --selftest 全绿（含 16 条该拒 / 17 条该放行）；
  ② 钩子行为：实战原命令 → block，理由含「为什么拦 + 4 条正确做法 + 逃生门」；
  ③ 放行侧不误伤：kill <pid> / kill $! / pkill -f '<唯一名>' / grep 里的 kill 字样；
  ④ 命令位置识别：echo/注释/heredoc 里写的 kill 不是命令，一律放行；
  ⑤ 逃生门：/killguard off、PI_LAB_KILL_GUARD=0、recover-mode 三选一都能解除；
  ⑥ 静态守卫：全程 try/catch（fail-open）、不起进程、注册了 tool_call、模块缺失不崩。

    python3 board/tests/test_kill_guard.py
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _pkgpaths import require_ext, find_node  # noqa: E402

LAB = pathlib.Path(__file__).resolve().parents[2]
MOD = LAB / "tools" / "killguard.mjs"
EXT = require_ext("kill-guard.ts")
EXT_URI = EXT.as_uri()
NODE = find_node()

# 实战里真实执行过的那一条（逐字取自 log/shard-20261120.log）
REAL_CMD = (
    'for pid in $(ps -eo pid,comm | awk \'$2=="python3"{print $1}\'); '
    'do kill $pid; done'
)

checks: list = []


def ok(name, cond, extra=""):
    checks.append((name, bool(cond), str(extra)))


def _report():
    bad = [c for c in checks if not c[1]]
    for name, good, extra in checks:
        print(f"  {'OK ' if good else 'ERR'} {name}" + (f"   {extra[:200]}" if extra and not good else ""))
    print(f"\n通过 {len(checks) - len(bad)} / 失败 {len(bad)}")
    if bad:
        print("失败项：" + "、".join(c[0] for c in bad))
        return 1
    print("OK kill 闸门（kill-guard）：全部通过")
    return 0


def _run_esm(js, env, timeout=180):
    """跑一段 ESM：**写临时文件再跑，不用 -e**（Windows 上超长 -e 会被静默截断）。"""
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "_probe.mjs")
        pathlib.Path(p).write_text(js, encoding="utf-8")
        return subprocess.run(  # noqa: S603
            [NODE, "--experimental-strip-types", p],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=env)


def main():
    ok("node 可用", bool(NODE), "本机没有 node → 扩展与测试都跑不了")
    ok("tools/killguard.mjs 存在", MOD.is_file())
    ok("agent-template/extensions/kill-guard.ts 存在", EXT.is_file())
    if not NODE:
        return _report()

    # ── ① 纯逻辑自检 ───────────────────────────────────────────────────────
    p = subprocess.run([NODE, str(MOD), "--selftest"], capture_output=True, text=True,  # noqa: S603
                       encoding="utf-8", errors="replace", timeout=120)
    ok("killguard.mjs --selftest 全绿（rc=0）",
       p.returncode == 0 and "OK" in p.stdout, (p.stdout + p.stderr)[-300:])

    # ── ①b CLI 契约：exit 0 放行 / exit 10 拒绝 / --json 可解析 ──────────────
    with tempfile.TemporaryDirectory() as td:
        f = os.path.join(td, "c.txt")
        pathlib.Path(f).write_text(REAL_CMD, encoding="utf-8")
        with open(f, "r", encoding="utf-8") as fh:
            r = subprocess.run([NODE, str(MOD), "check", "-", "--json"], stdin=fh,  # noqa: S603
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=120)
        ok("CLI 实战原命令 exit=10", r.returncode == 10, f"rc={r.returncode}")
        try:
            j = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:
            j = {}
        ok("CLI --json 可解析且 action=deny", j.get("action") == "deny", r.stdout[-200:])
        ok("CLI --json 带 rule", bool(j.get("rule")), str(j)[:200])

        pathlib.Path(f).write_text("kill 12345", encoding="utf-8")
        with open(f, "r", encoding="utf-8") as fh:
            r2 = subprocess.run([NODE, str(MOD), "check", "-", "--json"], stdin=fh,  # noqa: S603
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=120)
        ok("CLI 普通 kill exit=0", r2.returncode == 0, f"rc={r2.returncode}")

    # ── ②–⑤ 钩子行为 ──────────────────────────────────────────────────────
    with tempfile.TemporaryDirectory() as td:
        log = os.path.join(td, "kg.jsonl")
        js = (
            'const ext = (await import("' + EXT_URI + '")).default;\n'
            'const handlers = {};\n'
            'const notifies = [];\n'
            'const pi = {\n'
            '  on: (n, f) => { handlers[n] = f; },\n'
            '  registerCommand: (n, spec) => { handlers["cmd:" + n] = spec && spec.handler; },\n'
            '};\n'
            'const ctx = { ui: { notify: (t, l) => notifies.push([t, l]) } };\n'
            'ext(pi);\n'
            'const out = {};\n'
            'out.has_tool_call = !!handlers.tool_call;\n'
            'out.has_command = !!handlers["cmd:killguard"];\n'
            'const call = (name, input) => handlers.tool_call({ toolName: name, toolCallId: "t", input }, ctx);\n'
            'const REAL = ' + json.dumps(REAL_CMD) + ';\n'
            '\n'
            '// ② 实战原命令 → block\n'
            'const r1 = await call("bash", { command: REAL });\n'
            'out.real_blocked = !!(r1 && r1.block);\n'
            'const reason = (r1 && r1.reason) || "";\n'
            'out.reason_has_why = reason.includes("为什么拦");\n'
            'out.reason_has_fix = reason.includes("kill <pid>") && reason.includes("supervise.py --stop");\n'
            'out.reason_has_escape = reason.includes("/killguard off") && reason.includes("PI_LAB_KILL_GUARD=0");\n'
            'out.reason_has_incident = reason.includes("26 分钟");\n'
            'out.notify_fired = notifies.length > 0;\n'
            '\n'
            '// ③ 放行侧不误伤\n'
            'const allow = async (c) => { const r = await call("bash", { command: c }); return !(r && r.block); };\n'
            'out.allow_kill_pid = await allow("kill 12345");\n'
            'out.allow_kill_bang = await allow("python3 x.py & pid=$!; kill $pid");\n'
            'out.allow_pkill_unique = await allow("pkill -f myserver_8f3a");\n'
            'out.allow_grep = await allow("grep -n \\"kill\\" tools/killguard.mjs");\n'
            'out.allow_ps = await allow("ps aux | grep python | head");\n'
            '\n'
            '// ④ 命令位置识别：echo / 注释 / heredoc 里的 kill 不是命令\n'
            'out.allow_echo = await allow("echo \\"killall python3\\"");\n'
            'out.allow_comment = await allow("# killall python3 很危险");\n'
            'out.allow_heredoc = await allow("cat > /tmp/k.sh <<\'EOF\'\\nfor pid in $(ps -eo pid,comm); do kill $pid; done\\nEOF");\n'
            '\n'
            '// ③b 该拒的其它形状\n'
            'const deny = async (c) => { const r = await call("bash", { command: c }); return !!(r && r.block); };\n'
            'out.deny_killall = await deny("killall python3");\n'
            'out.deny_pkill_f = await deny("pkill -f \\"python3 -m http.server\\"");\n'
            'out.deny_xargs = await deny("ps -eo pid,comm | xargs -r kill -9");\n'
            'out.deny_kill0 = await deny("kill 0");\n'
            '\n'
            '// ③c R3-C：无锚点的脚本名 pattern（15:11 xben-082 的 pkill -f srv.py 曾被放行）\n'
            'out.deny_pkill_srv = await deny("pkill -f srv.py 2>/dev/null");\n'
            'out.deny_pkill_mjs = await deny("pkill -f probe.mjs");\n'
            'out.allow_pkill_path = await allow("pkill -f \\"/opt/pi-ctf/work/solve/xben-092-24/serve.py\\"");\n'
            'out.allow_pkill_suffix = await allow("pkill -f myserver_8f3a.py");\n'
            '\n'
            '// 非 bash 工具不碰（read/write 的 content 里出现 kill 不是命令）\n'
            'const rw = await call("write", { file_path: "/tmp/a.sh", content: REAL });\n'
            'out.write_not_blocked = !(rw && rw.block);\n'
            '\n'
            '// ⑤ 逃生门 A：PI_LAB_KILL_GUARD=0（模块级，由 env 决定；这里验 recover-mode 那条通道）\n'
            'globalThis.__PI_LAB_RECOVER = { until: Date.now() + 60000 };\n'
            'out.recover_allows = await allow(REAL);\n'
            'globalThis.__PI_LAB_RECOVER = { until: 0 };\n'
            'out.recover_expired_blocks = await deny(REAL);\n'
            '\n'
            '// ⑤b 逃生门 B：/killguard off\n'
            'await handlers["cmd:killguard"]("off", ctx);\n'
            'out.off_allows = await allow(REAL);\n'
            'await handlers["cmd:killguard"]("on", ctx);\n'
            'out.on_blocks = await deny(REAL);\n'
            '\n'
            'console.log(JSON.stringify(out));\n'
        )
        env = dict(os.environ)
        env["PI_LAB_KILLGUARD_LOG"] = log
        r = _run_esm(js, env)
        got = {}
        for line in r.stdout.strip().splitlines():
            try:
                v = json.loads(line)
                if isinstance(v, dict):
                    got.update(v)
            except Exception:
                pass
        if not got:
            ok("钩子探针跑出结果", False, (r.stdout + r.stderr)[-500:])
            return _report()

        expect = [
            ("注册了 tool_call 钩子", "has_tool_call", True),
            ("注册了 /killguard 命令", "has_command", True),
            ("实战原命令被拦（验收点 1）", "real_blocked", True),
            ("理由含「为什么拦」", "reason_has_why", True),
            ("理由含可直接照做的做法（验收点 4）", "reason_has_fix", True),
            ("理由含逃生门", "reason_has_escape", True),
            ("理由点名 26 分钟事故", "reason_has_incident", True),
            ("拦截时弹了通知", "notify_fired", True),
            ("放行 kill <pid>（验收点 3）", "allow_kill_pid", True),
            ("放行自有 pid 变量 kill $pid", "allow_kill_bang", True),
            ("放行 pkill -f '<唯一名>'", "allow_pkill_unique", True),
            ("放行 grep 'kill'", "allow_grep", True),
            ("放行普通 ps/grep", "allow_ps", True),
            ("放行 echo 字符串里的 kill", "allow_echo", True),
            ("放行注释里的 kill", "allow_comment", True),
            ("放行 heredoc 里写的 kill", "allow_heredoc", True),
            ("拦 killall python3（验收点 2）", "deny_killall", True),
            ("拦 pkill -f 通用名（验收点 2）", "deny_pkill_f", True),
            ("拦 ps | xargs kill", "deny_xargs", True),
            ("拦 kill 0", "deny_kill0", True),
            ("拦 pkill -f 无锚点脚本名（R3-C 实战缺口）", "deny_pkill_srv", True),
            ("拦 pkill -f 裸 mjs 名（R3-C）", "deny_pkill_mjs", True),
            ("放行 pkill -f 带路径锚点的脚本（R3-C 不误伤）", "allow_pkill_path", True),
            ("放行 pkill -f 带唯一后缀的脚本（R3-C 不误伤）", "allow_pkill_suffix", True),
            ("write 工具内容不误判", "write_not_blocked", True),
            ("recover-mode 放行", "recover_allows", True),
            ("recover 过期后恢复拦截", "recover_expired_blocks", True),
            ("/killguard off 放行", "off_allows", True),
            ("/killguard on 恢复拦截", "on_blocks", True),
        ]
        for label, key, want in expect:
            ok(label, got.get(key) == want, f"got={got.get(key)!r}")

        # 留痕：拦截要写 jsonl（事后能查「拦过几条、拦了什么」）
        try:
            recs = [json.loads(x) for x in pathlib.Path(log).read_text(encoding="utf-8").splitlines() if x.strip()]
        except Exception:
            recs = []
        ok("拦截留痕 log/kill-guard.jsonl", any(x.get("action") == "block" for x in recs),
           f"records={len(recs)}")
        ok("留痕里有 rule 与 cmd", any(x.get("rule") and x.get("cmd") for x in recs), str(recs)[:200])

    # ── ⑥ 静态守卫 ─────────────────────────────────────────────────────────
    src = EXT.read_text(encoding="utf-8", errors="replace")
    ok("扩展全程 try/catch（fail-open）", src.count("catch") >= 4, f"catch={src.count('catch')}")
    ok("扩展不起进程（无 child_process）", "child_process" not in src and "spawn(" not in src)
    ok("扩展有模块缺失兜底（loadMod 返回 null 就放行）", "mod = null" in src and "if (!m) return;" in src)
    ok("扩展有零依赖逃生舱 recoverActive", "recoverActive" in src and "__PI_LAB_RECOVER" in src)
    ok("扩展路径按模块存在性探测（resolveLab）", "resolveLab" in src and "killguard.mjs" in src)
    ok("只对 bash/shell/exec 生效", 'name !== "bash"' in src)

    # 模块侧：--selftest 里必须同时有「该拒」和「该放行」两组，防止有人删掉放行组
    msrc = MOD.read_text(encoding="utf-8", errors="replace")
    ok("mjs 自检含该拒组", "DENY_CASES" in msrc)
    ok("mjs 自检含放行组", "ALLOW_CASES" in msrc)
    ok("mjs 不引第三方依赖", "import " not in msrc.replace("import * as fs", "").replace("import.meta", ""))

    return _report()


if __name__ == "__main__":
    sys.exit(main())
