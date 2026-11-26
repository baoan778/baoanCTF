#!/usr/bin/env bash
# =============================================================================
# tunnel-proxy.py —— 给容器用的最小 HTTP CONNECT 隧道代理（A3 方案）
#
# 为什么需要它（2026-11-03 实测，A1/A2 已被证伪）：
#   A1（容器 --network host）：host 模式容器看到的是 Docker Desktop VM 的网络栈
#       实测 MAC de:5d:34:7e:db:4e / 网关 192.168.65.1，**不是** MyLinux 的
#       00:15:5d:93:0e:ed / 172.28.208.1 → 宿主起 VPN，容器照样不通。
#   A2（容器内加静态路由）：`ip route add 10.99.99.0/24 via 172.28.208.177`
#       内核报 `Nexthop has invalid gateway`；加 onlink 则 `No route to host`
#       —— 容器与 MyLinux 不在同一二层，无法作为下一跳。
#   A3（本文件）：容器**能**用 TCP 直连 MyLinux（实测 172.28.208.177:9098
#       收到 "I-AM-MYLINUX"）。所以在 MyLinux 上起一个 CONNECT 代理，
#       容器用 http_proxy 指过去 → 容器内一切 HTTP(S) 客户端自动走 VPN 路由。
#       实测：容器经此代理读到 10.99.99.99:9099 的 "HELLO-FROM-MYLINUX"。
#
# 为什么在 MyLinux 跑而不是容器里：VPN 归宿主（Q18=A）。VPN 起来后
#   MyLinux 有 tun0 + 到靶场的路由，代理只是把容器的连接受托给它。
#
# 用法（MyLinux 侧，需 root 或已起 VPN 的普通用户）：
#   python3 tools/tunnel-proxy.py --port 8888            # 前台
#   nohup python3 tools/tunnel-proxy.py --port 8888 &    # 后台
#
# 容器侧（compose.yaml 里加 environment）：
#   http_proxy:  http://<MyLinux eth0 IP>:8888
#   https_proxy: http://<MyLinux eth0 IP>:8888
#   no_proxy:    localhost,127.0.0.1,::1,.local
#
# 安全边界：只监听在本机/内网，不做转发白名单之外的事；仅支持 CONNECT
#   （HTTPS/telnet 隧道），不代理明文 HTTP 绝对 URL，避免变成开放代理。
# =============================================================================
import argparse
import select
import socket
import sys
import threading

BUFSIZE = 65536
IDLE_TIMEOUT = 120


def _pipe(a: socket.socket, b: socket.socket) -> None:
    """双向搬运，任一侧关闭即收尾。"""
    try:
        while True:
            r, _, _ = select.select([a, b], [], [], IDLE_TIMEOUT)
            if not r:
                return
            for s in r:
                data = s.recv(BUFSIZE)
                if not data:
                    return
                (b if s is a else a).sendall(data)
    except OSError:
        pass


def _handle(client: socket.socket, allowed_hosts: set[str] | None) -> None:
    upstream = None
    try:
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = client.recv(4096)
            if not chunk:
                return
            buf += chunk
            if len(buf) > 8192:
                client.sendall(b"HTTP/1.1 431 Request Header Fields Too Large\r\n\r\n")
                return

        line = buf.split(b"\r\n", 1)[0].decode("latin-1")
        parts = line.split()
        if len(parts) < 3:
            client.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            return
        method, target = parts[0].upper(), parts[1]

        # ── 明文 HTTP 代理（绝对 URI）：`GET http://host:port/path HTTP/1.1` ──
        #   ★ 2026-11-03：原来只支持 CONNECT，导致 `tsec healthcheck`
        #     （http://10.0.100.58 明文）被自己回 405 —— 那不是网络不通，
        #     是代理能力缺口。明文必须支持，否则 healthcheck 永远探不了。
        if method != "CONNECT":
            if not target.lower().startswith("http://"):
                client.sendall(
                    b"HTTP/1.1 400 Bad Request\r\n"
                    b"X-Reason: only http:// absolute-URI or CONNECT\r\n\r\n"
                )
                return
            rest = target[7:]
            slash = rest.find("/")
            authority = rest if slash < 0 else rest[:slash]
            path = "/" if slash < 0 else rest[slash:]
            if ":" in authority:
                h, p_s = authority.rsplit(":", 1)
                p_num = int(p_s)
            else:
                h, p_num = authority, 80
            if allowed_hosts is not None and h not in allowed_hosts:
                client.sendall(b"HTTP/1.1 403 Forbidden\r\n"
                               b"X-Reason: host not in --allow list\r\n\r\n")
                return
            lines = buf.split(b"\r\n")
            lines[0] = f"{method} {path} HTTP/1.1".encode("latin-1")
            has_host = any(ln.lower().startswith(b"host:") for ln in lines[1:])
            if not has_host:
                lines.insert(1, f"Host: {authority}".encode("latin-1"))
            upstream = socket.create_connection((h, p_num), timeout=15)
            upstream.sendall(b"\r\n".join(lines))
            _pipe(client, upstream)
            return

        # ── CONNECT 隧道（HTTPS）──
        if ":" not in target:
            client.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            return
        host, port_s = target.rsplit(":", 1)
        port = int(port_s)

        if allowed_hosts is not None and host not in allowed_hosts:
            client.sendall(
                b"HTTP/1.1 403 Forbidden\r\n"
                b"X-Reason: host not in --allow list\r\n\r\n"
            )
            return

        upstream = socket.create_connection((host, port), timeout=15)
        client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        _pipe(client, upstream)
    except Exception as exc:  # noqa: BLE001
        try:
            client.sendall(
                b"HTTP/1.1 502 Bad Gateway\r\n"
                b"X-Reason: " + str(exc).encode()[:120] + b"\r\n\r\n"
            )
        except OSError:
            pass
    finally:
        for s in (client, upstream):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="最小 HTTP CONNECT 隧道代理（A3）")
    ap.add_argument("--port", type=int, default=8888)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument(
        "--allow",
        default="",
        help="逗号分隔的主机白名单；留空 = 不限制（仅供内网使用）",
    )
    args = ap.parse_args(argv)

    allowed = {h.strip() for h in args.allow.split(",") if h.strip()} or None

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.bind, args.port))
    srv.listen(64)
    scope = "不限" if allowed is None else f"{len(allowed)} 个主机"
    print(f"[tunnel-proxy] 监听 {args.bind}:{args.port}（白名单：{scope}）", flush=True)

    try:
        while True:
            client, peer = srv.accept()
            t = threading.Thread(target=_handle, args=(client, allowed), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("\n[tunnel-proxy] 收到中断，退出", flush=True)
    finally:
        srv.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
