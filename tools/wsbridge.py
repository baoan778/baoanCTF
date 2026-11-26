#!/usr/bin/env python3
"""wsbridge —— 把 wss:// 隧道端点接成本地 TCP 端口（标准库，无依赖）。

为什么需要它
------------
平台给的 pentest 入口（例：wss://ctf.seusus.com/api/traffic/<token>?port=1080，
标签 tcp(http)）**不是网页**，是「WebSocket 包着的裸 TCP」：
  * 浏览器地址栏敲 wss://  -> 打不开（浏览器只把 ws 当 API，不当地址）
  * 普通 HTTP GET          -> 400 "Connection header did not include 'upgrade'"
  * 只有带 Upgrade 的握手    -> 101，之后 WS 二进制帧 = 隧道里的原始字节
所以要么装 websocat/wstunnel，要么用这个脚本。

用法
----
    python3 tools/wsbridge.py --url "wss://.../api/traffic/<token>?port=1080" --listen 8899
    # 另开一个窗口，照常打本地端口：
    curl -v http://127.0.0.1:8899/
    nmap -sV -p 8899 127.0.0.1
    # Burp: 上游代理填 127.0.0.1:8899；浏览器直接开 http://127.0.0.1:8899/

    --listen 127.0.0.1:8899   （默认 127.0.0.1:8899，只监听本机）
    --verbose                 打印握手与字节数
    --insecure                不校验证书（默认就不校验，平台常是自签/多级）
"""
from __future__ import annotations

import argparse
import base64
import os
import socket
import ssl
import struct
import sys
import threading
import urllib.parse

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
#   自包含（自己 import sys），不依赖本文件是否已 import sys 或用别名。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BUF = 65536


def log(on: bool, *a) -> None:
    if on:
        print(*a, file=sys.stderr, flush=True)


def ws_connect(url: str, insecure: bool = True, timeout: float = 20.0):
    """连上 wss:// 并完成握手，返回 (socket, 已读到的多余字节)。"""
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("wss", "ws"):
        raise SystemExit("只支持 ws:// 或 wss://，收到: %s" % url)
    host = u.hostname
    port = u.port or (443 if u.scheme == "wss" else 80)
    path = u.path + (("?" + u.query) if u.query else "")

    raw = socket.create_connection((host, port), timeout=timeout)
    if u.scheme == "wss":
        ctx = ssl.create_default_context()
        if insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        s = ctx.wrap_socket(raw, server_hostname=host)
    else:
        s = raw

    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        "GET %s HTTP/1.1\r\n"
        "Host: %s\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: %s\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "Origin: %s://%s\r\n"
        "User-Agent: Mozilla/5.0\r\n\r\n"
    ) % (path, u.netloc, key, u.scheme.replace("ws", "http"), u.netloc)
    s.sendall(req.encode())

    buf = b""
    while b"\r\n\r\n" not in buf:
        c = s.recv(4096)
        if not c:
            raise SystemExit("握手时连接被关闭")
        buf += c
    head, _, rest = buf.partition(b"\r\n\r\n")
    status = head.split(b"\r\n")[0].decode(errors="replace")
    if " 101" not in status:
        raise SystemExit("握手失败: %s\n%s" % (status, head.decode(errors="replace")[:400]))
    return s, rest, status


def ws_send(sock: socket.socket, data: bytes, opcode: int = 0x2) -> None:
    """客户端发出的帧必须加掩码。"""
    if not data and opcode == 0x2:
        return
    n = len(data)
    if n < 126:
        hdr = bytes([0x80 | opcode, 0x80 | n])
    elif n < 65536:
        hdr = bytes([0x80 | opcode, 0x80 | 126]) + struct.pack("!H", n)
    else:
        hdr = bytes([0x80 | opcode, 0x80 | 127]) + struct.pack("!Q", n)
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    sock.sendall(hdr + mask + masked)


class FrameReader:
    """把 WS 帧流还原成字节流。"""

    def __init__(self, sock: socket.socket, initial: bytes = b""):
        self.sock = sock
        self.buf = initial

    def _need(self, n: int) -> bytes:
        while len(self.buf) < n:
            c = self.sock.recv(BUF)
            if not c:
                raise EOFError
            self.buf += c
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def read(self) -> bytes:
        """返回一段应用数据；返回 b'' 表示对端关闭。"""
        payload = b""
        while True:
            b1, b2 = self._need(2)
            op = b1 & 0x0F
            fin = b1 >> 7
            ln = b2 & 0x7F
            if ln == 126:
                ln = struct.unpack("!H", self._need(2))[0]
            elif ln == 127:
                ln = struct.unpack("!Q", self._need(8))[0]
            mask = self._need(4) if (b2 >> 7) else b""
            data = self._need(ln) if ln else b""
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if op == 0x8:                       # close
                return b""
            if op == 0x9:                       # ping -> pong
                ws_send(self.sock, data, opcode=0xA)
                continue
            if op == 0xA:                       # pong
                continue
            payload += data
            if fin:
                return payload


def pump_up(conn: socket.socket, ws: socket.socket, done: threading.Event, verbose: bool) -> None:
    sent = 0
    try:
        while not done.is_set():
            data = conn.recv(BUF)
            if not data:
                break
            ws_send(ws, data)
            sent += len(data)
    except OSError:
        pass
    finally:
        done.set()
        log(verbose, "[wsbridge] 上行结束，共 %d 字节" % sent)


def handle(conn: socket.socket, url: str, insecure: bool, verbose: bool) -> None:
    try:
        ws, leftover, status = ws_connect(url, insecure)
    except Exception as e:
        log(True, "[wsbridge] 连隧道失败: %r" % (e,))
        conn.close()
        return
    log(verbose, "[wsbridge] 隧道 %s" % status)
    done = threading.Event()
    t = threading.Thread(target=pump_up, args=(conn, ws, done, verbose), daemon=True)
    t.start()
    reader = FrameReader(ws, leftover)
    down = 0
    try:
        while not done.is_set():
            data = reader.read()
            if not data:
                break
            conn.sendall(data)
            down += len(data)
    except (OSError, EOFError):
        pass
    finally:
        done.set()
        log(verbose, "[wsbridge] 下行结束，共 %d 字节" % down)
        for s in (conn, ws):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass


def main() -> None:
    ap = argparse.ArgumentParser(description="把 wss:// 隧道端点接成本地 TCP 端口")
    ap.add_argument("--url", required=True, help="wss:// 或 ws:// 隧道地址（原样粘过来）")
    ap.add_argument("--listen", default="127.0.0.1:8899", help="本地监听地址，默认 127.0.0.1:8899")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--insecure", action="store_true", default=True)
    args = ap.parse_args()

    lhost, _, lport = args.listen.rpartition(":")
    lhost = lhost or "127.0.0.1"
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((lhost, int(lport)))
    srv.listen(16)
    print("[wsbridge] 本地 %s:%s  ->  %s" % (lhost, lport, args.url), flush=True)
    print("[wsbridge] 现在可以:  curl -v http://%s:%s/   (Ctrl-C 退出)" % (lhost, lport), flush=True)
    try:
        while True:
            conn, addr = srv.accept()
            log(args.verbose, "[wsbridge] 来自 %s:%s" % addr)
            threading.Thread(target=handle, args=(conn, args.url, args.insecure, args.verbose), daemon=True).start()
    except KeyboardInterrupt:
        pass
    finally:
        srv.close()


if __name__ == "__main__":
    main()
