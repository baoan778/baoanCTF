#!/usr/bin/env python3
"""
nccsec-token —— 打印 NCC 国家网络空间安全云社区「大模型中心」的 API 令牌

背景（2026-10-10 现场摸到的）：
  * 模型叫 **Cyber-Model-1**（网安垂域，百万级上下文），
    推理端点 = https://ai.nccsec.cn/v1 （OpenAI 兼容，Bearer 认证）。
  * 令牌不是注册就给的：登录 nccsec.cn → 个人中心 → **大模型中心**
    → 「新建令牌」（`POST /api/personal/llm/key/create`）才拿得到。
    额度是「模型积分」，由每日登录免费发放 / 安全币 1:1 兑换。
  * `GET https://ai.nccsec.cn/v1/models` **不要 key 也能看模型列表**，
    所以体检时先打它；真正调用才需要 Bearer。

令牌从哪读（按优先级）：
  1) 环境变量 $NCCSEC_API_KEY        （run-pi.sh 会从 .env 载入）
  2) <agentDir>/nccsec-token 文件     （agentDir = $PI_CODING_AGENT_DIR 或 <仓库根>/.pi/agent）
  3) <仓库根>/.env 里的 NCCSEC_API_KEY 行（环境没载入时的兜底）

用法（给 pi 的 models.json 用，规则是只往 stdout 吐值）：
  nccsec-token             # 打印令牌（全程序列化，末尾换行）
  nccsec-token --status    # 人看的诊断：令牌来源 / 长度 / /v1/models 是否 200
  nccsec-token --check     # 只校验，不打印令牌；有令牌退出码 0，没有 1
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

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
DEFAULT_AGENT_DIR = os.path.join(LAB, ".pi", "agent")
LAB_ENV = os.path.join(LAB, ".env")
MODELS_URL = "https://ai.nccsec.cn/v1/models"
MAX_TOKEN_FILE = 8192  # 令牌文件不许超过 8K（工程纪律：任何文件读取限长）


def agent_dir() -> str:
    return os.environ.get("PI_CODING_AGENT_DIR") or DEFAULT_AGENT_DIR


def read_capped(path: str, limit: int = MAX_TOKEN_FILE) -> str | None:
    """只认普通文件，且限长 —— 不跟 /dev/zero、FIFO、巨型文件纠缠。"""
    try:
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(limit + 1)[:limit]
    except OSError:
        return None


def from_env_file() -> tuple[str | None, str]:
    """兜底：直接扫 .env（run-pi.sh 没载入时的场景，比如直接跑本脚本）。"""
    raw = read_capped(LAB_ENV, 64 * 1024)
    if not raw:
        return None, ""
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("NCCSEC_API_KEY="):
            val = line.split("=", 1)[1].strip().strip("'\"")
            if val:
                return val, LAB_ENV
    return None, ""


def load_token() -> tuple[str | None, str]:
    env = (os.environ.get("NCCSEC_API_KEY") or "").strip()
    if env:
        return env, "环境变量 $NCCSEC_API_KEY"

    token_file = os.path.join(agent_dir(), "nccsec-token")
    raw = read_capped(token_file)
    if raw:
        # 文件里可能贴着整段说明，取第一个像令牌的 token（或第一行非空非注释）
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.search(r"[A-Za-z0-9_\-]{16,}", line)
            if m:
                return m.group(0), token_file
    return from_env_file()


def probe(token: str, tries: int = 3) -> tuple[bool, str]:
    """拿令牌打 /v1/models；只报状态，不回显令牌。

    ⚠ 这个端点是**全局每秒限速**的（实测匿名/带令牌都可能吃 429
      `[GS] 全局每秒请求次数超限`）—— 429/5xx 一律重试，不算“令牌不行”。
    """
    last = "(没跑)"
    for i in range(tries):
        req = urllib.request.Request(MODELS_URL, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read(4096).decode("utf-8", "replace")
                return True, f"HTTP {resp.status} {body[:200]}"
        except urllib.error.HTTPError as e:
            detail = e.read(300).decode("utf-8", "replace") if e.fp else ""
            last = f"HTTP {e.code} {detail}"
            if e.code == 429 or 500 <= e.code < 600:
                time.sleep(2 * (i + 1))  # 限速/网关抖：退避重试，别把重试类当失败
                continue
            return False, last
        except Exception as e:  # noqa: BLE001 —— 网络层什么都可能抛
            last = f"{type(e).__name__}: {e}"
            time.sleep(2 * (i + 1))
    kind = "限速/网关" if "429" in last or "HTTP 5" in last else "网络"
    return False, f"{last}（{kind}，已重试 {tries} 次）"


def main(argv: list[str]) -> int:
    args = set(argv[1:])
    token, where = load_token()

    if "--check" in args:
        if token:
            return 0
        print("nccsec-token: 没有令牌 —— 见 nccsec-token --status", file=sys.stderr)
        return 1

    if "--status" in args:
        if not token:
            print("nccsec-token: ✘ 没有令牌")
            print("  去 nccsec.cn 登录 → 个人中心 → 大模型中心 → 新建令牌，然后任选一种：")
            print(f"    echo '<令牌>' > {os.path.join(agent_dir(), 'nccsec-token')}")
            print("    或把 NCCSEC_API_KEY=<令牌> 写进 <仓库根>/.env")
            return 1
        print(f"nccsec-token: 令牌来源 = {where}（长度 {len(token)}，前缀 {token[:4]}…）")
        try:
            with urllib.request.urlopen(MODELS_URL, timeout=20) as resp:
                listing = json.loads(resp.read(65536).decode("utf-8", "replace"))
            ids = [m.get("id") for m in listing.get("data", [])]
            print(f"  端点 {MODELS_URL} → 200，模型：{', '.join(ids) or '(空)'}")
        except Exception as e:  # noqa: BLE001
            print(f"  端点 {MODELS_URL} → 匿名探测失败（不一定是坏事，该端点有全局每秒限速）：{type(e).__name__}: {e}")
        ok, detail = probe(token)
        print(f"  带令牌自检 → {'✔ 可用' if ok else '✘ 不可用'}：{detail}")
        if not ok and ("429" in detail or "HTTP 5" in detail):
            print("  （限速/网关抖动不是令牌问题 —— 稍后重跑；真调用照样能成）")
            return 0
        return 0 if ok else 1

    if not token:
        print("nccsec-token: 没有令牌（nccsec-token --status 看怎么拿）", file=sys.stderr)
        return 1
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
