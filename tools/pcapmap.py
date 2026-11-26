#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pcapmap —— pcap 一键画像（把"摸流量"从十几条命令变成一条）

为什么需要（2026-10-10 实测）：
  做 ez_ftp 时我先手敲了十几条 tshark 才拼出全貌（分层 → 会话 → 明文凭据 →
  数据对象 → 传输中断/续传 → 时序异常）。这种"每题都要重来一遍"的活该固化 ——
  少漏一面，比快几分钟更值钱（漏面是丢分主因）。

一条命令给七块面板：
  ① 基本画像（包数/时长/时间范围/哈希）
  ② 协议分层（谁在说话）
  ③ TCP 会话 Top（内外网标注、单向大流量）
  ④ **明文凭据面板**（FTP USER/PASS + 登录结果、HTTP 认证头）—— CTF 最常吃的第一口
  ⑤ **文件传输面板**（传了什么/多大/声明值 vs 实际抓到/是否中断/是否断点续传）—— ez_ftp 的设计点
  ⑥ 异常与隧道迹象（重传、RST、爆破计数、超长 DNS、ICMP 载荷、TLS 体积）
  ⑦ 下一步提示（按面板自动给）

用法：
  pcapmap <x.pcap> [--export DIR] [--json] [--top N]
  --export DIR   顺便把 FTP/HTTP/SMB 对象导出到该目录（tshark --export-objects）
  每个外部命令都有 timeout；输出按面板截断（Top N），不会把 pcap 内容整屏刷出来。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
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

MAX_CMD = 240          # 单条 tshark 上限（秒）
DEFAULT_TOP = 8


def run(cmd, timeout=MAX_CMD):
    """跑外部命令；任何失败都返回 (rc, '')，不抛异常（画像工具不该因一条命令崩掉）"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, p.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def tsh(fields, display_filter=None, pcap="", extra=None, timeout=MAX_CMD):
    cmd = ["tshark", "-r", pcap, "-T", "fields"]
    for f in fields:
        cmd += ["-e", f]
    if display_filter:
        cmd += ["-Y", display_filter]
    cmd += (extra or [])
    rc, out = run(cmd, timeout)
    if rc != 0:
        return []
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        rows.append(line.split("\t"))
    return rows


def is_private(ip):
    if not ip:
        return None
    if ip.startswith(("10.", "192.168.", "127.")):
        return True
    if re.match(r"^172\.(1[6-9]|2\d|3[01])\.", ip):
        return True
    return False


def panel_basic(pcap):
    rc, out = run(["capinfos", pcap], 120)
    want = ("Number of packets", "Capture duration", "Earliest packet time",
            "Latest packet time", "File size", "Data byte rate", "SHA256")
    info = {}
    for line in out.splitlines():
        k, _, v = line.partition(":")
        k = k.strip()
        if k in want:
            info[k] = v.strip()
    return info


def panel_protocols(pcap, top):
    rc, out = run(["tshark", "-r", pcap, "-q", "-z", "io,phs"], 180)
    rows = []
    for line in out.splitlines():
        m = re.match(r"^\s*([a-z0-9\-]+)\s+frames:(\d+)\s+bytes:(\d+)\s*$", line)
        if m:
            name, fr, by = m.group(1), int(m.group(2)), int(m.group(3))
            if name in ("frame", "raw", "ip", "tcp", "udp", "eth", "data", "data-text-lines"):
                continue
            rows.append((by, fr, name))
    rows.sort(reverse=True)
    return rows[:top]


def panel_convs(pcap, top):
    rc, out = run(["tshark", "-r", pcap, "-q", "-z", "conv,tcp"], 180)
    rows = []
    for line in out.splitlines():
        m = re.match(r"^(\S+)\s+<->\s+(\S+)\s+(\d+)\s+([\d.]+ \w+)\s+(\d+)\s+([\d.]+ \w+)\s+(\d+)\s+([\d.]+ \w+)\s+([\d.]+)", line)
        if m:
            a, b = m.group(1), m.group(2)
            clean = lambda x: x.replace(" bytes", "").replace(" byte", "").strip()
            b1, b2 = m.group(4), m.group(6)
            ft = int(m.group(7))
            rows.append({"a": a, "b": b, "bytes_ab": clean(b1), "bytes_ba": clean(b2),
                         "frames": ft, "start": m.group(9)})
    rows.sort(key=lambda r: r["frames"], reverse=True)
    return rows[:top]


def panel_creds(pcap):
    """明文凭据：CTF 流量题最常吃的第一口。FTP 把 PASS 与随后的 230/530 配对。"""
    out = {"ftp": [], "http_auth": []}
    rows = tsh(fields=["frame.number", "frame.time_relative", "ip.src",
                       "ftp.request.command", "ftp.request.arg",
                       "ftp.response.code", "ftp.response.arg"],
               display_filter="ftp", pcap=pcap, timeout=180)
    cur = {}
    for r in rows + [[""] * 7]:
        r = (r + [""] * 7)[:7]
        num, t, src, cmd, arg, code, carg = [x.strip() for x in r]
        if cmd in ("USER", "PASS"):
            cur[cmd] = {"arg": arg, "t": t, "src": src, "frame": num}
        elif code in ("230", "530") and cur:
            out["ftp"].append({
                "t": cur.get("PASS", {}).get("t") or cur.get("USER", {}).get("t") or t,
                "src": cur.get("USER", {}).get("src") or src,
                "user": cur.get("USER", {}).get("arg", ""),
                "pass": cur.get("PASS", {}).get("arg", ""),
                "result": code, "detail": carg,
            })
            cur = {}
    # HTTP：Authorization / Cookie 里的可疑串
    rows = tsh(fields=["frame.time_relative", "ip.src", "http.authorization", "http.cookie"],
               display_filter="http.authorization or http.cookie", pcap=pcap, timeout=120)
    for r in rows[:40]:
        r = (r + [""] * 4)[:4]
        if r[2] or r[3]:
            out["http_auth"].append({"t": r[0], "src": r[1], "authorization": r[2], "cookie": r[3][:120]})
    return out


def panel_transfers(pcap, export=None):
    """文件传输：谁传了什么、声明多大、**实际抓到多少**、有没有中断、有没有 REST 续传。

    关联方式（第一版在这里翻过车）：控制通道会报被动端口（229/227），
    数据通道用**该端口**（服务端侧）承载 —— 所以按 `tcp.srcport == 被动端口` 汇总字节，
    而不是按客户端 IP 汇总（那会把同一客户端的多次传输全算在一个文件上）。
    """
    rows = tsh(fields=["frame.time_relative", "ip.src", "ip.dst", "tcp.srcport", "tcp.dstport",
                       "ftp.request.command", "ftp.request.arg", "ftp.response.code", "ftp.response.arg"],
               display_filter="ftp", pcap=pcap, timeout=180)
    transfers, pending, rest_from, pasv = [], {}, {}, {}
    for r in rows:
        r = (r + [""] * 9)[:9]
        t, src, dst, sp, dp, cmd, arg, code, carg = [x.strip() for x in r]
        if cmd in ("SIZE", "RETR", "STOR", "APPE", "LIST", "NLST"):
            pending[cmd] = {"t": t, "arg": arg, "client": src}
        elif cmd == "REST":
            rest_from[src] = arg
        elif code == "213" and "SIZE" in pending:
            pending["SIZE"]["size"] = carg
        elif code in ("229", "227"):
            # ★ 顺序坑：EPSV/PASV 的响应在 RETR 之前到（SIZE → EPSV → 229 → RETR → 150），
            #   所以这里只能**按客户端存"最近一次被动端口"**，等 150 时再挂到传输上。
            m = re.search(r"\|{3}(\d+)\|", carg)
            if m:
                port = int(m.group(1))
            else:
                m2 = re.search(r"\d+,(\d+),(\d+)\)", carg)
                port = (int(m2.group(1)) * 256 + int(m2.group(2))) if m2 else None
            if port:
                pasv[src if src != "0.0.0.0" else dst] = port
                pasv[dst] = port          # 响应包的 src=服务端、dst=客户端 —— 两个都记，稳妥
        elif code == "150":
            op = next((k for k in ("RETR", "STOR", "APPE", "LIST", "NLST") if k in pending), "?")
            info = pending.get(op, {})
            item = {"t": t, "who": info.get("client", dst), "op": op,
                    "file": info.get("arg") or ("(目录列表)" if op in ("LIST", "NLST") else "?"),
                    "declared": pending.get("SIZE", {}).get("size", ""),
                    "passive_port": pasv.get(info.get("client", ""), ""),
                    "rest_from": rest_from.get(info.get("client", ""), ""),
                    "status": None, "status_src": src}
            transfers.append(item)
        elif code in ("226", "426", "426 "):
            for it in reversed(transfers):
                if it.get("status") is None and it.get("status_src") == src:
                    it["status"] = "完整(226)" if code == "226" else "**中断(426)**"
                    break
    # 实际抓到多少：按被动端口（服务端）汇总 tcp.len
    data = tsh(fields=["tcp.srcport", "tcp.dstport", "tcp.len"],
               display_filter="ftp-data", pcap=pcap, timeout=180)
    by_port = {}
    for r in data:
        r = (r + [""] * 3)[:3]
        sp, dp, ln = r[0], r[1], int(r[2] or 0)
        by_port[sp] = by_port.get(sp, 0) + ln        # 服务端→客户端（下载）
        by_port[dp] = by_port.get(dp, 0) + ln        # 客户端→服务端（上传）
    for it in transfers:
        it.setdefault("status", "未知")
        pp = str(it.get("passive_port", ""))
        it["captured_bytes"] = by_port.get(pp, 0) if pp else ""
    exported = []
    if export:
        os.makedirs(export, exist_ok=True)
        before = set(os.listdir(export))
        run(["tshark", "-r", pcap, "--export-objects", "ftp," + export], 300)
        exported = sorted(set(os.listdir(export)) - before)
    return transfers, exported


def panel_anomalies(pcap, top):
    out = {}
    # 爆破迹象：530 计数
    rc, o = run(["tshark", "-r", pcap, "-Y", "ftp.response.code==530", "-T", "fields", "-e", "frame.number"], 120)
    out["ftp_530_失败登录"] = len([x for x in o.splitlines() if x.strip()])
    rc, o = run(["tshark", "-r", pcap, "-Y", "ftp.response.code==230", "-T", "fields", "-e", "frame.number"], 120)
    out["ftp_230_成功登录"] = len([x for x in o.splitlines() if x.strip()])
    rc, o = run(["tshark", "-r", pcap, "-q", "-z", "expert,warn"], 180)
    out["expert_warn_top"] = [l.strip() for l in o.splitlines()[:top]] if o.strip() else []
    # 超长 DNS 名（隧道迹象）
    dns = tsh(fields=["dns.qry.name"], display_filter="dns.flags.response==0", pcap=pcap, timeout=120)
    names = [r[0] for r in dns if r and r[0]]
    out["dns_查询数"] = len(names)
    longest = sorted(names, key=len, reverse=True)[:3]
    out["dns_最长域名"] = [(n[:80], len(n)) for n in longest]
    # TLS 体积与 ICMP 载荷
    tls = tsh(fields=["tcp.len"], display_filter="tls", pcap=pcap, timeout=120)
    out["tls_字节"] = sum(int(r[0] or 0) for r in tls if r and r[0])
    icmp = tsh(fields=["data.len"], display_filter="icmp", pcap=pcap, timeout=120)
    out["icmp_包数"] = len(icmp)
    out["icmp_最大载荷"] = max([int(r[0] or 0) for r in icmp] or [0])
    return out


def hints(basic, protos, convs, creds, transfers, anom):
    h = []
    ok = [c for c in creds["ftp"] if c["result"] == "230"]
    bad = [c for c in creds["ftp"] if c["result"] == "530"]
    if bad:
        h.append(f"检测到 **{len(bad)} 次失败登录**（疑似口令喷洒/爆破）→ 先看是否最终有 230 成功；失败的凭据也是口令字典（ez_ftp 就靠它拿到 zip 口令）")
    if ok:
        u = ", ".join(f"{c['user']}/{c['pass']}" for c in ok[:3])
        h.append(f"**有效凭据**：{u} → 立刻用于同题其它加密对象（zip/Office/数据库）")
    broken = [t for t in transfers if "中断" in str(t.get("status", ""))]
    rest = [t for t in transfers if t.get("rest_from")]
    if broken:
        h.append(f"**有 {len(broken)} 个传输被中断（426）** → 抓到的字节可能不完整；若之后出现同文件重传，检查是否 `REST` 续传（两段拼起来才是完整文件）")
    if rest:
        h.append("**发现 REST 断点续传** → 该文件必须把两段按偏移拼起来（别只解第一段）")
    for t in transfers:
        d, c = t.get("declared"), t.get("captured_bytes")
        if d and c and str(d).isdigit() and int(d) != c:
            h.append(f"`{t['file']}` 声明 {d} 字节但抓到 {c} → 对比差值，看是否有第二段/是否被截断")
    if anom.get("tls_字节", 0) > 200000:
        h.append(f"TLS 体积较大（{anom['tls_字节']} B）→ 若题面暗示外泄，检查 TLS 时长/节奏（无密钥时只能看元数据）")
    if anom.get("dns_最长域名") and anom["dns_最长域名"][0][1] > 60:
        h.append("DNS 出现超长域名 → 排查 DNS 隧道（TXT/长标签/高熵）")
    if anom.get("icmp_最大载荷", 0) > 100:
        h.append(f"ICMP 载荷最大 {anom['icmp_最大载荷']} B → 排查 ICMP 隐写/隧道")
    if not h:
        h.append("未命中内置启发式 —— 看④凭据面板与⑤传输面板，必要时手查 follow stream")
    return h


def main():
    ap = argparse.ArgumentParser(description="pcap 一键画像（分层/会话/凭据/传输/异常/提示）")
    ap.add_argument("pcap")
    ap.add_argument("--export", default="", help="顺带把文件对象导出到该目录")
    ap.add_argument("--top", type=int, default=DEFAULT_TOP)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if not os.path.isfile(a.pcap):
        print("✗ 找不到 pcap:", a.pcap, file=sys.stderr)
        return 1
    if not shutil.which("tshark"):
        print("✗ 没有 tshark（先装 wireshark-common/tshark）", file=sys.stderr)
        return 1

    basic = panel_basic(a.pcap)
    protos = panel_protocols(a.pcap, a.top)
    convs = panel_convs(a.pcap, a.top)
    creds = panel_creds(a.pcap)
    transfers, exported = panel_transfers(a.pcap, a.export or None)
    anom = panel_anomalies(a.pcap, a.top)
    hs = hints(basic, protos, convs, creds, transfers, anom)

    res = {"basic": basic, "protocols": protos, "conversations": convs, "credentials": creds,
           "transfers": transfers, "exported": exported, "anomalies": anom, "hints": hs}
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    print("═" * 72)
    print("① 基本画像")
    for k in ("Number of packets", "Capture duration", "Earliest packet time", "Latest packet time", "File size"):
        if basic.get(k):
            print(f"   {k}: {basic[k]}")
    if basic.get("SHA256"):
        print(f"   SHA256: {basic['SHA256'][:32]}…")

    print("\n② 协议分层（按字节 Top %d）" % a.top)
    for by, fr, name in protos:
        print(f"   {name:<14} {fr:>6} 帧  {by:>10} B")

    print("\n③ TCP 会话 Top %d（★=含非内网端）" % a.top)
    for c in convs:
        ext = ""
        for side in (c["a"], c["b"]):
            ip = side.split(":")[0]
            if is_private(ip) is False:
                ext = " ★"
        print(f"   {c['a']:<28} <-> {c['b']:<28} {c['frames']:>5} 帧  {c['bytes_ab']:>10}B/{c['bytes_ba']:>10}B  起 {c['start']}{ext}")

    print("\n④ 明文凭据面板")
    if not creds["ftp"] and not creds["http_auth"]:
        print("   （未发现明文凭据：可能全在 TLS 里）")
    for c in creds["ftp"]:
        flag = "✓ 成功" if c["result"] == "230" else "✗ 失败"
        print(f"   [{c['t']:>8}s] {c['src']:<16} {c['user']}/{c['pass']:<18} {flag}")
    for c in creds["http_auth"]:
        print(f"   [{c['t']:>8}s] {c['src']:<16} Authorization={c['authorization'][:60]} Cookie={c['cookie'][:40]}")

    print("\n⑤ 文件传输面板")
    if not transfers:
        print("   （没有 FTP 数据对象）")
    for t in transfers:
        print(f"   [{t['t']:>8}s] {t['who']:<16} {t['op']} {t['file']:<28} 声明={t.get('declared','?'):<8} 抓到={t.get('captured_bytes','?'):<8} {t.get('status','')}"
              + (f"  REST from {t['rest_from']}" if t.get("rest_from") else ""))
    if exported:
        print("   导出对象: " + ", ".join(exported))

    print("\n⑥ 异常与隧道迹象")
    for k, v in anom.items():
        if k == "expert_warn_top" and v:
            print("   expert 告警（前几条）:")
            for line in v[:4]:
                print("     " + line[:110])
        elif k != "expert_warn_top":
            print(f"   {k}: {v}")

    print("\n⑦ 下一步提示（自动生成，不是结论）")
    for h in hs:
        print("   • " + h)
    print("═" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
