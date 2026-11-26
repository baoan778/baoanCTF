#!/usr/bin/env python3
"""
kimi-token —— 打印 Kimi（Moonshot 开放平台）API key

为什么要有它（2026-10-12 现场踩到的坑）：
  `models.json` 的 `kimi` provider 曾经**硬编码**了一把 API key。
  那把 key 属于一个低等级账号：
      org-42617853b53e4359810a9bd0505c2a31  <ak-fd8edouatm6111hmbsw1>
      → max RPM = 3      （几乎不可用）
      → TPD 1500000 用完 → 429 "request reached organization TPD rate limit"
  于是会话跑到一半直接 429，pi「Retry failed after 3 attempts」硬失败 ——
  TPD 是**日额度**，重试多少次都没用，只能换 key 或换通道。
  而 `.env` 里那把 KIMI_API_KEY 是**另一个账号**（余额正常、HTTP 200）。
  → 所以把 provider 的 apiKey 改成 `!kimi-token`，key 只在 .env 里存一份，
    换 key 不用改 models.json，也不会把密钥提交进仓库。

key 从哪读（按优先级）：
  1) 环境变量 $KIMI_API_KEY                     （run-pi.sh 用 `set -a` + `. .env` 载入）
  2) <agentDir>/kimi-token 文件                 （agentDir = $PI_CODING_AGENT_DIR 或 .pi/agent）
  3) <PI_LAB_ROOT>/.env 里的 KIMI_API_KEY=…    （环境没载入时的兜底；PI_LAB_ROOT 默认由本脚本位置推导）

用法（给 pi 的 models.json 用，规则是**只往 stdout 吐值**）：
  kimi-token             # 打印 key（末尾换行）
  kimi-token --status    # 人看的诊断：来源 / 打码 key / 所属 org / /v1/models 是否 200
  kimi-token --check     # 只校验不打印；有 key 退出码 0，没有 1
"""
from __future__ import annotations

import json
import os
import re
import sys
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
LAB_ENV = os.path.join(LAB, ".env")
MODELS_URL = "https://api.moonshot.cn/v1/models"
MAX_TOKEN_FILE = 8192  # 任何文件读取限长（工程纪律）


def agent_dir() -> str:
    d = os.environ.get("PI_CODING_AGENT_DIR") or os.path.join(LAB, ".pi", "agent")
    if os.path.islink(d):
        d = os.path.realpath(d)
    return d


def read_capped(path: str) -> str:
    """只认普通文件 + 限长；FIFO/设备文件/超大文件一律拒绝。"""
    if not os.path.isfile(path) or os.path.islink(path):
        return ""
    try:
        if os.path.getsize(path) > MAX_TOKEN_FILE:
            return ""
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(MAX_TOKEN_FILE)
    except OSError:
        return ""


def first_token_line(text: str) -> str:
    """取令牌文件里的第一行非空、非注释内容（允许文件里带 # 说明）。"""
    for line in text.splitlines():
        s = line.strip().strip("'\"")
        if s and not s.startswith("#"):
            return s
    return ""


def from_env_file() -> str:
    m = re.search(r"(?m)^\s*(?:export\s+)?KIMI_API_KEY\s*=\s*[\"']?([^\"'\s#]+)",
                  read_capped(LAB_ENV))
    return m.group(1) if m else ""


def resolve() -> tuple[str, str]:
    v = (os.environ.get("KIMI_API_KEY") or "").strip()
    if v:
        return v, "env KIMI_API_KEY"
    p = os.path.join(agent_dir(), "kimi-token")
    v = first_token_line(read_capped(p))
    if v:
        return v, p
    v = from_env_file()
    if v:
        return v, LAB_ENV
    return "", ""


def mask(k: str) -> str:
    return f"{k[:7]}…{k[-4:]} (len={len(k)})" if len(k) > 12 else "(过短)"


def org_of(key: str, timeout: float = 20.0) -> str:
    """拿 org id：GET /v1/models 的响应头/正文都不给，只能用一次极小的探测。"""
    body = json.dumps({"model": "kimi-k3", "max_tokens": 1, "temperature": 1,
                       "messages": [{"role": "user", "content": "."}]}).encode()
    req = urllib.request.Request(
        "https://api.moonshot.cn/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return f"HTTP {r.status} ✔ key 可用"
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", "replace")[:200]
        m = re.search(r"(org-[0-9a-f]+)", txt)
        return f"HTTP {e.code} {('· ' + m.group(1)) if m else ''} {txt[:80]}"
    except OSError as e:
        return f"网络错误：{e}"


def main() -> int:
    args = sys.argv[1:]
    key, src = resolve()
    if "--status" in args:
        print(f"来源    : {src or '(没找到)'}")
        print(f"key     : {mask(key)}" if key else "key     : (空)")
        if key:
            print(f"探测    : {org_of(key)}")
            print("提示    : 429 + 'organization TPD rate limit' = 该账号当日额度用完，"
                  "重试无用；换 key（.env 的 KIMI_API_KEY）或换通道（/ms go、/ms cyber）")
        else:
            print("提示    : 在 .env 写 KIMI_API_KEY=sk-… 后重试")
        return 0 if key else 1
    if "--check" in args:
        return 0 if key else 1
    if not key:
        print(f"kimi-token: 没有 KIMI_API_KEY；在 {LAB_ENV} 写一行 "
              "KIMI_API_KEY=sk-…（或用 $KIMI_API_KEY）", file=sys.stderr)
        return 1
    sys.stdout.write(key + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
