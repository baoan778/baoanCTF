#!/usr/bin/env python3
"""模型切换（Cyber-Model-1 ⟷ OpenCode Go DeepSeek）回归测试。

对象（2026-10-10 新增，操作者要求「弄个模型切换功能」）：
  · `tools/bin/nccsec-token`  —— NCC 大模型中心的 Bearer 令牌（models.json 的 apiKey 靠它）
  · `tools/bin/mswitch`       —— shell 侧切换：改 .env 默认通道 + 留 /reload 标记
  · `.pi/agent/extensions/ocgo-switch.ts` —— 会话内 `/ms`、`/cyber`、`/ocgo` + marker 消费
  · `.pi/agent/models.json`   —— nccsec provider（baseUrl https://ai.nccsec.cn/v1）
  · `.pi/settings.json`       —— enabledModels（Ctrl+P 只在两条通道间循环）

守住的原则：
  ① 全离线：不发任何真实请求；令牌用假值，目录用临时目录（**不碰真产物**）；
  ② 无令牌时**必须明确失败**（rc=1 + 告诉你去哪拿），不许静默发空 key；
  ③ 切换命令双向可用，marker 文件读一次即删（不许每次开会话都改模型）；
  ④ 失败打开（fail-open）：切换失败只 notify，绝不拖垮 pi。

    python3 board/tests/test_model_switch.py
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
from _pkgpaths import agent_file, ext_path, require_ext, agent_setting, skip, find_node  # noqa: E402

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
TOKEN = LAB / "tools" / "bin" / "nccsec-token"
KIMI = LAB / "tools" / "bin" / "kimi-token"
MSWITCH = LAB / "tools" / "bin" / "mswitch"
EXT = require_ext("ocgo-switch.ts")
# ★ 发布包里运行态配置在 $PI_CODING_AGENT_DIR（容器 /home/ctf/.pi-ctf/agent），
#   开发仓在 <lab>/.pi/agent/；模板在 agent-template/。都可能不存在 → 下面按「存在才断言」处理。
MODELS = agent_file("models.json")
SETTINGS = agent_file("settings.json")
SKIPPED: list = []          # 断言「包内确实没有该产物」时记这里，不混进失败
# ★ 2026-11-02 修：WSL 侧 PATH 上**没有 node**，但包里带 vendor/node。
#   老写法只查 PATH → 找不到就判「node 可用」红，把「环境缺 node」
#   误报成「扩展跑不了」，整条测试 FAIL。和之前 selfcheck 的坑是同一个：
#   探测解释器要按 PATH → 包内 vendor 的顺序找。
NODE = find_node()

# 驱动真扩展的假 pi（断言写死在 harness 里；rc=0 才算过）
HARNESS = r"""
import fs from "node:fs";
import os from "node:os";
import path from "node:path";


const dir = fs.mkdtempSync(path.join(os.tmpdir(), "swtest-"));
process.env.PI_CODING_AGENT_DIR = dir;

const mod = await import(EXT_PATH);
const cmds = new Map();
const handlers = {};
const notes = [];
const ctx = { model: { provider: "opencode-go", id: "deepseek-v4.1-flash" },
              modelRegistry: { find: (p, id) => ({ provider: p, id }) },
              ui: { notify: (m) => notes.push(String(m)) } };
const pi = {
  registerCommand: (n, o) => cmds.set(n, o),
  on: (ev, fn) => { (handlers[ev] ||= []).push(fn); },
  setModel: async (m) => { ctx.model = { provider: m.provider, id: m.id }; pi.last = `${m.provider}/${m.id}`; return true; },
};
mod.default(pi);
const fails = [];
const expect = (c, w) => { if (!c) fails.push(w); };
const run = (name, args) => cmds.get(name).handler(args, ctx);

expect([...cmds.keys()].sort().join(",") === "cyber,ms,ocgo", `注册的命令=${[...cmds.keys()]}`);
await run("cyber", "");
expect(pi.last === "nccsec/cyber-model-1", `/cyber → ${pi.last}`);
await run("cyber", "nccsec/cyber-x");
expect(pi.last === "nccsec/cyber-x", `/cyber <id> → ${pi.last}`);
await run("ocgo", "");
expect(pi.last === "opencode-go/deepseek-v4.1-flash", `/ocgo → ${pi.last}`);
await run("ms", "cyber");
expect(pi.last === "nccsec/cyber-model-1", `/ms cyber → ${pi.last}`);
await run("ms", "");
expect(pi.last === "opencode-go/deepseek-v4.1-flash", `/ms 从 cyber 回切 → ${pi.last}`);
await run("ms", "");
expect(pi.last === "nccsec/cyber-model-1", `/ms 再切回 cyber → ${pi.last}`);
notes.length = 0;
await run("ms", "status");
expect(/当前 nccsec\/cyber-model-1/.test(notes.join("|")), `status 应报当前模型：${notes}`);
expect(/✘ 没令牌/.test(notes.join("|")), `status 应报没令牌：${notes}`);
notes.length = 0;
await run("ms", "bogus");
expect(/不认识的通道/.test(notes.join("|")), `未知通道要有提示：${notes}`);
// 认证失败：setModel 返回 false 时只 notify，不抛
pi.setModel = async () => false;
notes.length = 0;
await run("cyber", "");
expect(/没有可用的认证/.test(notes.join("|")), `无认证要有提示：${notes}`);
// marker：读一次即删
fs.writeFileSync(path.join(dir, "switch-on-reload.json"),
                 JSON.stringify({ provider: "nccsec", model: "cyber-model-1" }));
let last = null;
pi.setModel = async (m) => { last = `${m.provider}/${m.id}`; return true; };
await handlers.session_start[0]({ reason: "reload" }, ctx);
expect(last === "nccsec/cyber-model-1", `marker 未生效：${last}`);
expect(!fs.existsSync(path.join(dir, "switch-on-reload.json")), "marker 用完没删");
last = null;
await handlers.session_start[0]({ reason: "start" }, ctx);
expect(last === null, "marker 删了还会再切？");
fs.writeFileSync(path.join(dir, "nccsec-token"), "deadbeefdeadbeefdeadbeef\n");
notes.length = 0;
await run("ms", "status");
expect(/✔ 已配令牌/.test(notes.join("|")), `有令牌应报已配：${notes}`);

console.log(fails.length ? "FAIL\n- " + fails.join("\n- ") : "PASS");
process.exit(fails.length ? 1 : 0);
"""


def main() -> int:
    # ★ 本用例的核心是**执行** tools/bin 下的 `nccsec-token` / `mswitch` 等 shell 脚本
    #   （靠 shebang + exec 位）。Windows 内核不认 shebang，Popen 会抛 WinError 193，
    #   即便 shutil.which("bash") 存在也救不了 —— 显式 SKIP（77），不当失败。
    if os.name != "posix":
        print("SKIP 本平台非 POSIX（不能直接执行 shebang 脚本：WinError 193）")
        return 77
    checks: list = []

    def ok(name, cond, extra=""):
        checks.append((name, bool(cond), str(extra)))

    ok("node 可用", bool(NODE), "没有 node → 扩展与测试都跑不了")
    ok("nccsec-token 存在且可执行", TOKEN.is_file() and os.access(TOKEN, os.X_OK))
    ok("kimi-token 存在且可执行", KIMI.is_file() and os.access(KIMI, os.X_OK))
    ok("mswitch 存在且可执行", MSWITCH.is_file() and os.access(MSWITCH, os.X_OK))
    ok("ocgo-switch.ts 存在", EXT.is_file())

    # ---- models.json / settings.json 静态检查 ----
    #   发布包只带**模板**不带运行态：models.json 由 provision-agent.sh 在容器首启时生成，
    #   这里若拿不到就不硬判红（否则卖家跑回归必红一片），但要**明确打 SKIP 行**。
    if MODELS is None:
        SKIPPED.append("models.json 不在包内（运行态由 provision-agent.sh 生成）→ 跳过 provider 断言")
    try:
        if MODELS is None:
            raise FileNotFoundError("models.json 未找到（发布包形态）")
        cfg = json.loads(MODELS.read_text(encoding="utf-8"))
        if "nccsec" not in (cfg.get("providers") or {}):
            # 容器里这份是 provision-agent.sh 按 DEEPSEEK_* 环境变量生成的（只有 deepseek 一个
            # provider）——不是「配置坏了」，而是发布包形态不同 → 跳过 provider 断言。
            SKIPPED.append("models.json 由 provision 生成（仅含所配 provider，无 nccsec）→ 跳过 provider 断言")
            raise StopIteration
        p = cfg["providers"]["nccsec"]
        ok("models.json 有 nccsec provider", True)
        ok("nccsec baseUrl 指向 ai.nccsec.cn/v1",
           p.get("baseUrl") == "https://ai.nccsec.cn/v1", p.get("baseUrl"))
        ok("nccsec 模型 id = cyber-model-1",
           [m.get("id") for m in p.get("models", [])] == ["cyber-model-1"])
        ok("apiKey 走 nccsec-token（不是硬编码明文）",
           "nccsec-token" in str(p.get("apiKey", "")) and not re.search(r"sk-[A-Za-z0-9]{10,}", str(p)))
        # kimi：同一个坑的回归门（2026-10-12 硬编码低等级 key → TPD 429 硬失败）
        kp = cfg["providers"]["kimi"]
        ok("kimi.apiKey 走 kimi-token（不是硬编码明文）",
           "kimi-token" in str(kp.get("apiKey", "")) and not re.search(r"sk-[A-Za-z0-9]{10,}", str(kp)),
           kp.get("apiKey"))
        ok("kimi baseUrl 指向 api.moonshot.cn/v1",
           kp.get("baseUrl") == "https://api.moonshot.cn/v1", kp.get("baseUrl"))
    except (FileNotFoundError, StopIteration):
        pass                      # 上面已记 SKIPPED
    except Exception as e:  # noqa: BLE001
        ok("models.json 可解析且含 nccsec", False, e)

    if SETTINGS is not None:
        try:
            st = json.loads(SETTINGS.read_text(encoding="utf-8"))
            em = st.get("enabledModels") or []
            if em:                # 模板里没这项时不算失败（Ctrl+P 循环是可选配置）
                ok("settings.json 的 Ctrl+P 循环含两条通道",
                   "opencode-go/deepseek-v4.1-flash" in em and "nccsec/cyber-model-1" in em, em)
            else:
                SKIPPED.append("settings.json 无 enabledModels（发布包模板未启用 Ctrl+P 循环）→ 跳过")
        except Exception as e:  # noqa: BLE001
            ok("settings.json 可解析", False, e)
    else:
        SKIPPED.append("settings.json 不在包内 → 跳过 enabledModels 断言")

    # ---- mswitch 自检（临时目录）----
    r = subprocess.run([str(MSWITCH), "--selftest"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)  # noqa: S603
    ok("mswitch --selftest 全绿", r.returncode == 0 and "✔" in r.stdout, (r.stdout + r.stderr)[-200:])
    r = subprocess.run([str(MSWITCH), "list"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)  # noqa: S603
    ok("mswitch list 有用法/通道", "cyber" in r.stdout and "go" in r.stdout, r.stdout[-200:])

    # ---- nccsec-token：有假令牌就吐出来；没有就明确 rc=1（隔离环境，不碰真配置）----
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ)
        env.pop("NCCSEC_API_KEY", None)
        env["PI_CODING_AGENT_DIR"] = td          # 空的 agentDir → 不允许回退到真令牌
        r = subprocess.run([str(TOKEN), "--check"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, env=env)  # noqa: S603
        ok("无令牌时 --check 明确失败（rc=1）", r.returncode == 1, f"rc={r.returncode} {r.stdout}{r.stderr}")
        r = subprocess.run([str(TOKEN)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, env=env)  # noqa: S603
        ok("无令牌时不吐空 key（rc=1 且 stdout 干净）", r.returncode == 1 and not r.stdout.strip(), repr(r.stdout))
        env["NCCSEC_API_KEY"] = "FAKE-TOKEN-FOR-TEST"
        r = subprocess.run([str(TOKEN)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, env=env)  # noqa: S603
        ok("有令牌时只往 stdout 吐令牌", r.returncode == 0 and r.stdout.strip() == "FAKE-TOKEN-FOR-TEST", repr(r.stdout))
        (pathlib.Path(td) / "nccsec-token").write_text("# 注释\nFILE-TOKEN-1234567890abcdef\n", encoding="utf-8")
        env.pop("NCCSEC_API_KEY", None)
        r = subprocess.run([str(TOKEN)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, env=env)  # noqa: S603
        ok("能从 <agentDir>/nccsec-token 读令牌（跳过注释行）",
           r.stdout.strip() == "FILE-TOKEN-1234567890abcdef", repr(r.stdout))

    # ---- kimi-token：取值优先级 + 无 key 必须明确失败（全离线，不碰真 .env）----
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ)
        env.pop("KIMI_API_KEY", None)
        env["PI_CODING_AGENT_DIR"] = td
        env["PI_LAB_ROOT"] = td          # 兜底的 .env 也指向临时目录，绝不读真 key
        r = subprocess.run([str(KIMI), "--check"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, env=env)  # noqa: S603
        ok("kimi-token 无 key 时 --check 明确失败（rc=1）", r.returncode == 1, f"rc={r.returncode}")
        r = subprocess.run([str(KIMI)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, env=env)  # noqa: S603
        ok("kimi-token 无 key 时不吐空值（rc=1 + stdout 干净）",
           r.returncode == 1 and not r.stdout.strip(), repr(r.stdout))
        env["KIMI_API_KEY"] = "sk-FAKE-KEY-1234567890"
        r = subprocess.run([str(KIMI)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, env=env)  # noqa: S603
        ok("kimi-token 有环境变量时只往 stdout 吐 key",
           r.returncode == 0 and r.stdout.strip() == "sk-FAKE-KEY-1234567890", repr(r.stdout))
        env.pop("KIMI_API_KEY", None)
        (pathlib.Path(td) / "kimi-token").write_text("# 注释\nsk-IN-AGENTDIR-1234567890\n", encoding="utf-8")
        r = subprocess.run([str(KIMI)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, env=env)  # noqa: S603
        ok("kimi-token 能读 <agentDir>/kimi-token（跳过注释行）",
           r.stdout.strip() == "sk-IN-AGENTDIR-1234567890", repr(r.stdout))
        (pathlib.Path(td) / "kimi-token").unlink()
        (pathlib.Path(td) / ".env").write_text("PI_MODEL=x\nKIMI_API_KEY=sk-FROM-DOTENV-1234567890\n", encoding="utf-8")
        r = subprocess.run([str(KIMI)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, env=env)  # noqa: S603
        ok("kimi-token 能从 <PI_LAB_ROOT>/.env 兜底（环境未载入时）",
           r.stdout.strip() == "sk-FROM-DOTENV-1234567890", repr(r.stdout))

    # ---- 真扩展行为（假 pi 驱动）----
    if NODE:
        js = "const EXT_PATH = " + json.dumps(str(EXT)) + ";\n" + HARNESS
        r = subprocess.run(  # noqa: S603
            [NODE, "--experimental-strip-types", "--input-type=module", "-e", js],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
        )
        ok("扩展：/ms 双向 + /cyber + /ocgo + marker 读一次即删 + 无认证有提示",
           r.returncode == 0 and "PASS" in r.stdout, (r.stdout + r.stderr)[-300:])

    # ---- 静态守卫 ----
    src = EXT.read_text(encoding="utf-8")
    ok("用 pi.setModel 切模型", "pi.setModel(model)" in src)
    ok("marker 用完即删（unlinkSync）", "unlinkSync(markerFile)" in src)
    ok("fail-open：会话钩子包在 try/catch 里", src.count("try {") >= 2 and "} catch" in src)
    ok("注册了 aliases 表（go/cyber）", '"go"' in src and '"cyber"' in src and "nccsec" in src)

    return _report(checks)


def _report(checks) -> int:
    passed = sum(1 for _, c, _ in checks if c)
    for name, c, extra in checks:
        print(f"  {'✓' if c else '✗'} {name}" + (f"  {extra}" if extra and not c else ""))
    for s in SKIPPED:
        print(f"  ~ {s}")
    print(f"\n{passed}/{len(checks)} 断言通过" + (f"（另 {len(SKIPPED)} 条按发布包形态跳过）" if SKIPPED else ""))
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
