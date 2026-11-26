#!/usr/bin/env python3
"""
mswitch —— pi-lab 模型切换（NCC Cyber-Model-1  ⟷  OpenCode Go 订阅的 DeepSeek）

两个通道（都在 `.pi/agent/models.json` 里配好了）：
  go     = opencode-go/deepseek-v4.1-flash   （$10/月订阅，认证走 opencode-token 现场取）
  cyber  = nccsec/cyber-model-1              （NCC 国家网安云社区大模型中心，Bearer 令牌）

它做两件事（各自独立，失败互不牵连）：
  1) 改 run-pi.sh 的默认通道：把 <仓库根>/.env 里的 PI_PROVIDER / PI_MODEL 换成目标
  2) 给**当前正在跑的 pi** 留一个标记文件 <agentDir>/switch-on-reload.json
     → 在 pi 里敲 `/reload`，扩展 `model-switch.ts` 读到就切过去（只生效一次，读完即删）
     也可以直接在 pi 里敲 `/ms` / `/cyber` / `/ocgo`，连 reload 都不用

用法：
  mswitch                  # 看当前：.env 默认通道 + 标记文件 + cyber 令牌可用性
  mswitch list             # 同上（显式）
  mswitch go               # 切到 OpenCode Go 的 DeepSeek
  mswitch cyber            # 切到 NCC Cyber-Model-1
  mswitch <provider>/<model>   # 任意通道
  mswitch --no-env cyber   # 只写标记（不改 .env 默认）
  mswitch --selftest       # 自检（临时目录，不碰真配置）

⚠ cyber 要先有令牌（nccsec.cn 个人中心→大模型中心→新建令牌）：
     echo '<令牌>' > <仓库根>/.pi/agent/nccsec-token
     或把 NCCSEC_API_KEY=<令牌> 写进 .env
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

def _lab_root() -> str:
    """仓库根：$PI_LAB_ROOT → 从本文件位置推（本文件在 tools/ 下一级），不写死任何绝对路径。

    ★ 2026-11-19：这批脚本从 tools/hand/<name> 搬到了 tools/<name>.py（原因见 make-bin.sh：
      宿主 Windows 的 exec 不解析 shebang，裸 Python 入口敲不出来）。位置少了一级，
      所以 dirname 从**三层**改成**两层** —— 不改的话算出来的 LAB 是 D:\，`.env` 与
      `.pi/agent` 全指向盘根，令牌一律读不到。
    """
    env = (os.environ.get("PI_LAB_ROOT") or "").strip()
    if env:
        return env
    here = os.path.realpath(__file__)
    return os.path.dirname(os.path.dirname(here))


LAB = _lab_root()
ENV_FILE = os.path.join(LAB, ".env")
AGENT_DIR = os.environ.get("PI_CODING_AGENT_DIR") or os.path.join(LAB, ".pi", "agent")
MARKER = "switch-on-reload.json"
TOKEN_CMD = os.path.join(LAB, "tools", "bin", "nccsec-token")

ALIASES = {
    "go": ("opencode-go", "deepseek-v4.1-flash"),
    "ocgo": ("opencode-go", "deepseek-v4.1-flash"),
    "cyber": ("nccsec", "cyber-model-1"),
    "ncc": ("nccsec", "cyber-model-1"),
    "nccsec": ("nccsec", "cyber-model-1"),
    # Claude 中转（ai98pro.xyz）：实测 opus-4-6 / sonnet-5 / haiku-4-5 能出字，
    # opus-5 / 5-5 / 4-8 上游账号池经常是空的（报 All available accounts exhausted）
    "opus": ("ai98", "claude-opus-4-6"),
    "claude": ("ai98", "claude-opus-4-6"),
    "opus5": ("ai98", "claude-opus-5-5"),
    "sonnet": ("ai98", "claude-sonnet-5"),
    "haiku": ("ai98", "claude-haiku-4-5"),
    # Kimi（Moonshot 官方）：四个模型都实测能出字（思考型，content 与 reasoning_content 都有）
    "kimi": ("kimi", "kimi-k3"),
    "kimi-code": ("kimi", "kimi-k2.7-code"),
    "kimi-fast": ("kimi", "kimi-k2.7-code-highspeed"),
}
CHANNELS = [
    ("go", "opencode-go", "deepseek-v4.1-flash", "$10/月订阅（opencode console 账号，免 key）"),
    ("cyber", "nccsec", "cyber-model-1", "NCC 网安垂域模型，积分计费（2.10/8.40 积分每 1M tok）"),
    ("opus", "ai98", "claude-opus-4-6", "Claude Opus 4.6（中转 ai98pro.xyz；实测可用）"),
    ("opus5", "ai98", "claude-opus-5-5", "Claude Opus 5.5（同中转；上游账号池常空，会报 exhausted）"),
    ("sonnet", "ai98", "claude-sonnet-5", "Claude Sonnet 5（同中转）"),
    ("haiku", "ai98", "claude-haiku-4-5", "Claude Haiku 4.5（同中转，便宜快）"),
    ("kimi", "kimi", "kimi-k3", "Kimi K3（Moonshot 官方 api.moonshot.cn，官方 key）"),
    ("kimi-code", "kimi", "kimi-k2.7-code", "Kimi K2.7 Code（同上，偏代码/长任务）"),
    ("kimi-fast", "kimi", "kimi-k2.7-code-highspeed", "Kimi K2.7 Code 高速版（同上）"),
]


def read_env_pair(env_file: str) -> tuple[str | None, str | None]:
    try:
        with open(env_file, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read(1 << 20)
    except OSError:
        return None, None
    prov = model = None
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith("PI_PROVIDER="):
            prov = s.split("=", 1)[1].strip()
        elif s.startswith("PI_MODEL="):
            model = s.split("=", 1)[1].strip()
    return prov, model


def write_env_pair(env_file: str, provider: str, model: str) -> str:
    """把 .env 里的 PI_PROVIDER/PI_MODEL 换成目标值；缺行就追加。备份只在首次改动时留。"""
    try:
        with open(env_file, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read(1 << 20)
    except OSError:
        return f"⚠ 读不到 {env_file}，跳过默认通道修改"

    backup = env_file + ".bak-mswitch"
    if not os.path.exists(backup):
        try:
            shutil.copy2(env_file, backup)
        except OSError:
            pass

    out, seen = [], {"PI_PROVIDER": False, "PI_MODEL": False}
    for line in raw.splitlines():
        s = line.lstrip()
        hit = None
        for key in ("PI_PROVIDER", "PI_MODEL"):
            if s.startswith(key + "="):
                hit = key
        if hit:
            seen[hit] = True
            out.append(f"{hit}=" + (provider if hit == "PI_PROVIDER" else model))
        else:
            out.append(line)
    tail = []
    if not seen["PI_PROVIDER"]:
        tail.append(f"PI_PROVIDER={provider}")
    if not seen["PI_MODEL"]:
        tail.append(f"PI_MODEL={model}")
    if tail:
        if out and out[-1].strip():
            out.append("")
        out.extend(tail)

    try:
        with open(env_file, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out) + "\n")
    except OSError as e:
        return f"⚠ 写 {env_file} 失败：{e}"
    return f"✔ 默认通道已写入 {env_file}：PI_PROVIDER={provider} PI_MODEL={model}（备份 {backup}）"


def write_marker(provider: str, model: str, agent_dir: str | None = None) -> str:
    d = agent_dir or AGENT_DIR
    try:
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, MARKER)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"provider": provider, "model": model}, fh)
        return f"✔ 已留切换标记 {path} —— 在 pi 里敲 /reload 即生效（读完自动删除）"
    except OSError as e:
        return f"⚠ 写标记文件失败：{e}"


def token_ready() -> str:
    if not os.path.exists(TOKEN_CMD):
        return "? 找不到 nccsec-token"
    try:
        r = subprocess.run([sys.executable, TOKEN_CMD, "--check"], capture_output=True, timeout=30)
    except Exception as e:  # noqa: BLE001
        return f"? 令牌自检异常：{e}"
    return "✔ 令牌就绪" if r.returncode == 0 else "✘ 还没有令牌（nccsec-token --status 看怎么拿）"


def cmd_list() -> int:
    prov, model = read_env_pair(ENV_FILE)
    print(f"默认通道（run-pi.sh 读 {ENV_FILE}）：{prov or '(未设，脚本默认 deepseek/deepseek-v4-flash)'}"
          f" / {model or '-'}")
    marker = os.path.join(AGENT_DIR, MARKER)
    if os.path.exists(marker):
        try:
            with open(marker, "r", encoding="utf-8") as fh:
                print(f"待生效标记：{fh.read(200).strip()}")
        except OSError:
            pass
    print("可用通道：")
    for alias, p, m, note in CHANNELS:
        extra = f"  [{token_ready()}]" if p == "nccsec" else ""
        print(f"  {alias:<6} {p}/{m}{extra}\n         {note}")
    print("用法： mswitch go | mswitch cyber | mswitch opus | mswitch <provider>/<model>")
    print("       会话内： /ms（在 go/cyber 之间来回切）、/cyber、/ocgo [模型id]")
    return 0


def selftest() -> int:
    fails = []
    with tempfile.TemporaryDirectory() as td:
        env = os.path.join(td, ".env")
        with open(env, "w") as fh:
            fh.write("# comment\nPI_API_KEY=stub\nPI_PROVIDER=opencode-go\nPI_MODEL=deepseek-v4.1-flash\n")
        msg = write_env_pair(env, "nccsec", "cyber-model-1")
        if "✔" not in msg:
            fails.append(f"write_env_pair: {msg}")
        if read_env_pair(env) != ("nccsec", "cyber-model-1"):
            fails.append("env 回读不一致")
        with open(env) as fh:
            body = fh.read()
        if body.count("PI_PROVIDER=") != 1 or body.count("PI_MODEL=") != 1:
            fails.append("env 出现重复行")
        if "# comment" not in body or "PI_API_KEY=stub" not in body:
            fails.append("env 其他行被破坏")
        if not os.path.exists(env + ".bak-mswitch"):
            fails.append("没留备份")

        # 缺 PI_MODEL 的文件 → 追加且不重复
        env2 = os.path.join(td, "no-model.env")
        with open(env2, "w") as fh:
            fh.write("PI_PROVIDER=opencode-go\n")
        write_env_pair(env2, "nccsec", "cyber-model-1")
        if read_env_pair(env2) != ("nccsec", "cyber-model-1"):
            fails.append("缺行追加失败")

        m = write_marker("nccsec", "cyber-model-1", agent_dir=td)
        if "✔" not in m:
            fails.append(f"write_marker: {m}")
        with open(os.path.join(td, MARKER)) as fh:
            if json.load(fh) != {"provider": "nccsec", "model": "cyber-model-1"}:
                fails.append("标记内容不对")

    if fails:
        print("mswitch --selftest: ✘")
        for f in fails:
            print("  -", f)
        return 1
    print("mswitch --selftest: ✔ 全部通过")
    return 0


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if a != "--no-env"]
    no_env = "--no-env" in argv[1:]

    if "--selftest" in args:
        return selftest()

    if not args or args[0] in ("list", "status", "-h", "--help"):
        if args and args[0] in ("-h", "--help"):
            print(__doc__.strip())
            return 0
        return cmd_list()

    want = args[0]
    if "/" in want:
        provider, _, model = want.partition("/")
    elif want in ALIASES:
        provider, model = ALIASES[want]
    else:
        print(f"mswitch: 不认识的通道 '{want}'（用 go / cyber / provider/model）", file=sys.stderr)
        return 2

    print(f"→ 目标通道：{provider}/{model}")
    if not no_env:
        print(write_env_pair(ENV_FILE, provider, model))
    print(write_marker(provider, model))
    if provider == "nccsec":
        state = token_ready()
        print(f"  令牌：{state}")
        if state.startswith("✘"):
            print("  （没令牌也能切，但 pi 会说『没有可用认证』；先建令牌再 /model）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
