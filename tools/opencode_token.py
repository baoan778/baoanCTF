#!/usr/bin/env python3
"""
opencode-token —— 打印 OpenCode console 的 access token / org id（自动刷新）

背景（都是实测出来的，别改成"看起来更合理"的样子）：
  * `opencode console login` 用设备码流程登录，凭据落在
      ~/.local/share/opencode/opencode.db  (SQLite)
      account.access_token / refresh_token / token_expiry
      account_state.active_org_id
    这个 token 是 `st_` 开头的会话令牌，默认约 30 天有效。
  * 它**不能**打 /zen/go/v1（那是给 OPENCODE_API_KEY 用的），
    但**能**打 console 的 config 接口：
      GET https://opencode.ai/console/api/config
          Authorization: Bearer <token>
          x-org-id: <org>
    返回的 provider.opencode-go 里写着真正的推理端点和所需头：
      api    = https://opencode.ai/inference/go/openai/v1
      headers= { "x-opencode-org-id": "<org>" }
      env    = ["OPENCODE_CONSOLE_TOKEN"]
  也就是：console token + x-opencode-org-id 走 inference 端点；缺 org 头会 403。

用法（给 pi 的 models.json 用，只往 stdout 吐值）：
  opencode-token            # 打印 access token
  opencode-token --org      # 打印 active org id
  opencode-token --status   # 打印诊断信息（人看的）
  opencode-token --refresh  # 强制刷新后打印 token
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request

DB = os.path.expanduser("~/.local/share/opencode/opencode.db")
CLIENT_ID = "opencode-cli"
DEFAULT_SERVER = "https://opencode.ai/console"
REFRESH_MARGIN_S = 300  # 剩余不足 5 分钟就当过期


def fail(msg: str) -> "None":
    print(f"opencode-token: {msg}", file=sys.stderr)
    sys.exit(1)


def db_path() -> str:
    p = os.environ.get("OPENCODE_DB") or DB
    if not os.path.exists(p):
        fail(
            f"找不到 {p}\n"
            "  先跑：npm i -g opencode-ai && opencode console login"
        )
    return p


def read_state() -> dict:
    con = sqlite3.connect(db_path())
    try:
        row = con.execute(
            "select email, url, access_token, refresh_token, token_expiry from account "
            "order by time_updated desc limit 1"
        ).fetchone()
        if not row:
            fail("opencode.db 里没有 account —— 先跑 `opencode console login`")
        org_row = con.execute("select active_org_id from account_state limit 1").fetchone()
        return {
            "email": row[0],
            "server": (row[1] or DEFAULT_SERVER).rstrip("/"),
            "access": row[2],
            "refresh": row[3],
            "expiry": row[4],
            "org": org_row[0] if org_row else None,
        }
    finally:
        con.close()


def expiry_seconds(exp) -> float | None:
    """opencode 存的是毫秒时间戳；兼容秒/ISO 字符串。"""
    if exp in (None, ""):
        return None
    try:
        if isinstance(exp, str) and not exp.isdigit():
            return None
        n = int(exp)
    except (TypeError, ValueError):
        return None
    if n > 10_000_000_000:  # 毫秒
        n /= 1000.0
    return n


def refresh_token(state: dict) -> dict:
    """用 refresh_token 换新 token，并写回数据库。"""
    url = f"{state['server']}/auth/device/token"
    body = json.dumps(
        {
            "grant_type": "refresh_token",
            "refresh_token": state["refresh"],
            "client_id": CLIENT_ID,
        }
    ).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as e:
        fail(f"刷新失败 HTTP {e.code}: {e.read()[:200].decode('utf-8', 'replace')}")
    except Exception as e:  # 网络问题就沿用旧 token，让调用方自己撞 401
        print(f"opencode-token: 刷新异常({e})，沿用现有 token", file=sys.stderr)
        return state

    access = payload.get("access_token")
    if not access:
        fail(f"刷新响应里没有 access_token: {json.dumps(payload)[:200]}")
    new_refresh = payload.get("refresh_token") or state["refresh"]
    expires_in = int(payload.get("expires_in") or 0)
    new_expiry = int((time.time() + (expires_in or 30 * 86400)) * 1000)

    con = sqlite3.connect(db_path())
    try:
        con.execute(
            "update account set access_token=?, refresh_token=?, token_expiry=?, "
            "time_updated=? where access_token=?",
            (access, new_refresh, new_expiry, int(time.time() * 1000), state["access"]),
        )
        con.commit()
    finally:
        con.close()
    state.update(access=access, refresh=new_refresh, expiry=new_expiry)
    return state


def main() -> None:
    args = sys.argv[1:]
    state = read_state()

    if "--status" in args:
        exp = expiry_seconds(state["expiry"])
        left = (exp - time.time()) / 3600 if exp else None
        print(f"账号      : {state['email']}")
        print(f"server    : {state['server']}")
        print(f"org       : {state['org']}")
        print(f"token     : {state['access'][:6]}…(len={len(state['access'] or '')})")
        print(f"剩余有效期: {left:.1f} 小时" if left is not None else "剩余有效期: 未知")
        return

    if "--org" in args:
        if not state["org"]:
            fail("没有 active_org_id —— 跑 `opencode console login` 或 `opencode console switch`")
        print(state["org"])
        return

    force = "--refresh" in args
    exp = expiry_seconds(state["expiry"])
    if force or (exp is not None and exp - time.time() < REFRESH_MARGIN_S):
        state = refresh_token(state)
    print(state["access"])


if __name__ == "__main__":
    main()
