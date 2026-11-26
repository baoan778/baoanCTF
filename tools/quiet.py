#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""quiet —— 对抗实战：别把 IP 打没（低速、封禁探测、出口切换、证据留痕）

来源动机（实战教训，见 skills/src-recon/references/对抗-别把IP打没.md）：
  * 防守方对 SYN 扫描限速 → 高速扫描只得到一堆假 filtered，**必须低速扫**
  * 打到某个量级就被封 IP / 被 WAF 拦 → 之后所有请求都是 403，白耗时间
  * 拿不到出口切换手段 = 被封了就"停手等"，等于退赛

用法
----
  quiet ip                                  # 当前出口 IP（直连 vs 代理各一次）
  quiet probe <url>                         # 封禁探测：连发小请求，判状态码/重置/超时
  quiet req <url> [--qps 1] [--data k=v] [-X POST] [--ua ...]   # 节流请求（含退避）
  quiet scan <host> [-p 22,80,443-1000]     # 低速端口扫描（实测参数，见下）
  quiet egress ls                           # 列出 Clash 节点与当前选中（需本地 Clash）
  quiet egress use <节点名|关键字>          # 换出口（切换 → 立刻回报新 IP）

出口切换：走 Clash RESTful API（默认 http://127.0.0.1:9090，secret 取 CLASH_SECRET）
          也可用 CLASH_API=http://127.0.0.1:9097 指定端口。

逐出证据：设 PI_QUIET_EVIDENCE_DIR=<dir> 时，每次请求/探测都追加一条带时间戳的 JSONL，
          格式 {ts, action, target, status, ms, bytes, note} —— 直接进证据包。

依赖：仅标准库（urllib）；nmap 由 scan 调用（缺失时退化为 python 自带 socket 扫描）。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import socket
import ssl
import subprocess
import sys
import time
import http.client as httpc   # ★ 别名：别被本文件里的 http() 函数遮蔽
import urllib.error
import urllib.parse      # ★ 显式导入：cmd_egress 里用了 urllib.parse.quote，
                         #   靠 urllib.request 的副作用带进来是隐式依赖，撞上就 AttributeError
import urllib.request
from datetime import datetime, timezone, timedelta

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
]
# 低位端口默认集：不扫全端口，先摸常见面（要全端口时显式 -p-）
COMMON_PORTS = ("21,22,80,81,443,445,873,1433,3000,3306,3389,5432,5985,6379,7001,8000,8080,8081,8443,8888,9000,9090,9200,11211,27017")

BLOCK_HINTS = {
    403: "403 = 被拒（可能 WAF 规则命中 / IP 已被拉黑）",
    429: "429 = 限流（请求太快，必须降速并退避）",
    418: "418 = 常见于蜜罐/WAF 的『我在逗你』响应",
    503: "503 = 服务不可用（压垮了 / 被策略拦截）",
}


def now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def evidence(action: str, target: str, **kw):
    d = os.environ.get("PI_QUIET_EVIDENCE_DIR")
    if not d:
        return
    try:
        os.makedirs(d, exist_ok=True)
        rec = {"ts": now_iso(), "action": action, "target": target, **kw}
        with open(os.path.join(d, f"quiet-{datetime.now():%Y%m%d}.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ── HTTP 基础 ────────────────────────────────────────────────────────────────
def http(url: str, method="GET", data=None, ua=None, timeout=15, proxy=None, headers=None, insecure=False):
    """返回 (status, ms, bytes, text, err)。proxy=None 表示**不走代理**（对抗场景通常直连目标）。"""
    ctx = ssl.create_default_context()
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    handlers = [urllib.request.HTTPSHandler(context=ctx)]         # ★ context 只能挂在 handler 上
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        handlers.append(urllib.request.ProxyHandler({}))          # 显式禁用环境代理
    op = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, method=method,
                                 data=data.encode() if isinstance(data, str) else data)
    req.add_header("User-Agent", ua or random.choice(UA_POOL))
    req.add_header("Accept", "*/*")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    t0 = time.time()
    try:
        with op.open(req, timeout=timeout) as r:
            body = r.read(65536)
            return r.status, int((time.time() - t0) * 1000), len(body), body.decode("utf-8", "replace"), None
    except urllib.error.HTTPError as e:
        body = b""
        try:
            body = e.read(65536)
        except Exception:
            pass
        return e.code, int((time.time() - t0) * 1000), len(body), body.decode("utf-8", "replace"), None
    except Exception as e:                                        # 连接重置/超时/DNS 失败
        return None, int((time.time() - t0) * 1000), 0, "", f"{type(e).__name__}: {e}"


# ── 出口 IP ──────────────────────────────────────────────────────────────────
IP_RE = re.compile(r"^\s*((?:\d{1,3}\.){3}\d{1,3}|[0-9a-fA-F:]{4,})\s*$")


def get_ip(proxy=None) -> str:
    """只看**纯文本**端点，并用正则校验真的是 IP —— ip.sb 那种会回 HTML，别当真。"""
    for u in ("https://ipv4.icanhazip.com", "https://api.ipify.org", "https://ifconfig.me/ip",
              "https://ipecho.net/plain", "https://api64.ipify.org"):
        st, _, _, txt, err = http(u, timeout=10, proxy=proxy, headers={"Accept": "text/plain"})
        body = (txt or "").strip()
        if st == 200 and IP_RE.match(body):
            return body
    return "<取不到（端点都被拦或走了代理？）>"


def cmd_ip(a):
    direct = get_ip(None)
    print(f"直连出口 IP : {direct}")
    env_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or os.environ.get("ALL_PROXY")
    if env_proxy:
        print(f"环境里有代理 : {env_proxy}（本工具默认 --noproxy 直连；要比对就 quiet ip --via-proxy）")
    if getattr(a, "via_proxy", False) or os.environ.get("CLASH_API"):
        px = os.environ.get("QUIET_PROXY") or env_proxy
        if px:
            print(f"经代理出口 IP: {get_ip(px)}  （{px}）")
    evidence("ip", "-", direct=direct)
    clash = clash_status()
    if clash:
        print(f"Clash 当前选中: {clash}")
    else:
        print("（未发现本地 Clash API —— 出口切换需要它或自有代理池：CLASH_API / CLASH_SECRET）")


# ── Clash 出口切换 ───────────────────────────────────────────────────────────
def clash_api() -> str | None:
    cands = [os.environ.get("CLASH_API"), "http://127.0.0.1:9090", "http://127.0.0.1:9097", "http://127.0.0.1:9091"]
    for base in cands:
        if not base:
            continue
        st, _, _, txt, err = http(base.rstrip("/") + "/version", timeout=3, proxy=None)
        if st == 200 and "version" in (txt or ""):
            return base.rstrip("/")
    return None


def clash_headers():
    s = os.environ.get("CLASH_SECRET") or os.environ.get("CLASH_TOKEN")
    return {"Authorization": f"Bearer {s}"} if s else {}


def clash_proxies():
    base = clash_api()
    if not base:
        return None, None
    st, _, _, txt, err = http(base + "/proxies", timeout=6, proxy=None, headers=clash_headers())
    if st != 200:
        return base, None
    try:
        return base, json.loads(txt).get("proxies", {})
    except Exception:
        return base, None


def clash_status():
    base, px = clash_proxies()
    if not px:
        return None
    sel = [f"{n}→{v.get('now')}" for n, v in px.items()
           if v.get("type") in ("Selector", "URLTest", "Fallback") and v.get("now")]
    return " | ".join(sel[:6]) if sel else None


def cmd_egress(a):
    base, px = clash_proxies()
    if not px:
        print("✗ 没找到 Clash API（试过 127.0.0.1:9090/9097/9091）→ 用 CLASH_API=http://127.0.0.1:<port> 指定")
        return 1
    groups = {n: v for n, v in px.items() if v.get("type") in ("Selector", "URLTest", "Fallback")}
    if a.act == "ls":
        for n, v in groups.items():
            print(f"[{n}] 当前={v.get('now')}")
            for m in v.get("all", [])[:40]:
                print(f"    - {m}{'  ←当前' if m == v.get('now') else ''}")
        return 0
    if a.act == "use":
        kw = a.name
        if not kw:
            print("✗ 用法: quiet egress use <节点名|关键字>"); return 1
        for n, v in groups.items():
            hits = [m for m in v.get("all", []) if kw == m or kw.lower() in m.lower()]
            if not hits:
                continue
            target = hits[0]
            st, _, _, txt, err = http(f"{base}/proxies/{urllib.parse.quote(n)}", method="PUT",
                                      data=json.dumps({"name": target}), proxy=None,
                                      headers={**clash_headers(), "Content-Type": "application/json"})
            if st in (200, 204):
                print(f"✓ [{n}] 已切到 {target}")
                time.sleep(1.5)
                print(f"  新出口 IP: {get_ip(os.environ.get('QUIET_PROXY') or 'http://127.0.0.1:7890')}")
                evidence("egress", n, node=target, status=st)
                return 0
            print(f"✗ 切换失败 status={st} {err or txt[:120]}")
            return 1
        print(f"✗ 没找到匹配 {kw!r} 的节点（quiet egress ls 看列表）")
        return 1
    return 1


def raw_get(ip: str, host_header: str, path: str = "/", port: int = 80, tls: bool = False,
            timeout: int = 12, ua: str | None = None):
    """IP 直连但自定义 Host 头 —— 用来区分『域名层拦截』与『IP 被封』。"""
    conn = None
    try:
        if tls:
            conn = httpc.HTTPSConnection(ip, port, timeout=timeout,
                                         context=ssl._create_unverified_context())
        else:
            conn = httpc.HTTPConnection(ip, port, timeout=timeout)
        t0 = time.time()
        conn.putrequest("GET", path, skip_host=True, skip_accept_encoding=True)
        conn.putheader("Host", host_header)
        conn.putheader("User-Agent", ua or random.choice(UA_POOL))
        conn.putheader("Accept", "*/*")
        conn.endheaders()
        r = conn.getresponse()
        body = r.read(65536)
        return r.status, int((time.time() - t0) * 1000), len(body), body.decode("utf-8", "replace")[:200], None
    except Exception as e:
        return None, 0, 0, "", f"{type(e).__name__}: {e}"
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


def cmd_trap(a):
    """判"像被封"，还是真的被封：
       A) 域名直访      B) IP 直连（默认 Host=IP）   C) IP 直连 + Host=域名
       结论：B/C 正常而 A 403 → **域名层拦截（ICP/CDN/WAF 按域名规则）**，不是 IP 被封 → 用 IP+Host 继续；
            三者都 403/429/重置 → **真在 IP 层被拦** → 停手冷却 + 换出口。"""
    dom, ip = a.domain, a.host
    path, tls = a.path, (a.port in (443, 8443))
    print(f"[*] trap 判定 {dom}  vs  IP {ip}:{a.port}{path}\n")
    st_a = http(f"http{'s' if tls else ''}://{dom}{path}" if a.port in (80, 443) else f"http{'s' if tls else ''}://{dom}:{a.port}{path}",
                timeout=a.timeout)
    st_b = raw_get(ip, ip, path, a.port, tls, a.timeout)
    st_c = raw_get(ip, dom, path, a.port, tls, a.timeout)
    for tag, (st, ms, n, body, err) in (("A 域名直访        ", st_a), ("B IP直连(Host=IP) ", st_b),
                                        ("C IP直连(Host=域名)", st_c)):
        print(f"  {tag} status={st} {ms}ms {n}B {err or ''}")
        evidence("trap", f"{dom}|{ip}", status=st, ms=ms, bytes=n, note=tag.strip())
    ok_b = st_b[0] in (200, 301, 302, 401, 404, 405)
    ok_c = st_c[0] in (200, 301, 302, 401, 404, 405)
    bad_a = st_a[0] in (403, 429, None)
    print()
    if bad_a and (ok_b or ok_c):
        print("→ 判定：**域名层拦截，不是封你 IP**（这正是长城杯里阿里云 ICP 拦 .com/.cn 的情形）")
        print("   继续办法：用 IP 直连 + `Host: 域名` 头访问（本工具 C 行就是）；vhost 爆破会失效，改用 IP 段/端口侧查")
        print("   ⚠ 别在这里换出口——换了也没用，白折腾")
    elif not ok_b and not ok_c:
        print("→ 判定：**疑似 IP 层被拦（真被限/封）**")
        print("   ① 立刻停手冷却 5–15 分钟（别继续请求） ② quiet egress use <另一节点> 换出口 ③ QPS 降到 ≤0.3 再来")
    else:
        print("→ 判定：没有明显拦截。继续时 quiet req --qps 1；扫端口用 quiet scan（低速）")
    return 0


# ── 封禁探测 ─────────────────────────────────────────────────────────────────
def cmd_probe(a):
    url = a.url
    print(f"[*] 封禁探测 {url}（连发 {a.times} 次，间隔 {a.interval}s）")
    verdict = []
    for i in range(a.times):
        st, ms, n, txt, err = http(url, timeout=a.timeout)
        hint = ""
        if err:
            hint = f"连接层异常（{err}）"
        elif st in BLOCK_HINTS:
            hint = BLOCK_HINTS[st]
        elif st in (401, 404, 405, 400):
            hint = f"{st} 正常（端点存在/需认证，不是封禁）"
        elif st and st >= 400:
            hint = f"{st} 需关注"
        print(f"  #{i+1} status={st} {ms}ms {n}B {hint}")
        evidence("probe", url, status=st, ms=ms, bytes=n, note=hint or err or "")
        verdict.append(("异常" if (err or (st in BLOCK_HINTS)) else ("可疑" if st is None else "正常")))
        time.sleep(a.interval)
    print()
    if "异常" in verdict:
        print("→ 判定：**疑似被封/被限速**。按顺序做：")
        print("   ① 立刻停手冷却（5–15 分钟，别继续打） ② quiet egress use <另一节点> 换出口")
        print("   ③ 换 UA/加 Referer、把 QPS 降到 ≤0.3 ④ 只剩 403 且换出口无效 → 目标可能按 URL/规则拦，不是按 IP")
    elif "可疑" in verdict:
        print("→ 判定：不确定（目标不稳/超时）。先 quiet ip 确认出口，再降速重试一次")
    else:
        print("→ 判定：当前正常。继续时保持 quiet req --qps 1 以下，别上扫描器")
    return 0


# ── 节流请求 ─────────────────────────────────────────────────────────────────
def cmd_req(a):
    headers = {}
    if a.header:
        for h in a.header:
            if ":" in h:
                k, v = h.split(":", 1)
                headers[k.strip()] = v.strip()
    delay = 1.0 / max(a.qps, 0.05)
    backoff = 1.0
    ok = 0
    for i in range(a.times):
        st, ms, n, txt, err = http(a.url, method=a.method.upper(), data=a.data,
                                   ua=a.ua, timeout=a.timeout, headers=headers)
        tag = ""
        if err:
            tag = f"ERR {err}"
        elif st == 429 or (st and 500 <= st < 600):
            sleep_for = min(backoff, a.max_backoff)
            tag = f"退避 {sleep_for:.1f}s（{st}）"
            time.sleep(sleep_for)
            backoff = min(backoff * 2, a.max_backoff)
        elif st in (403, 503):
            tag = "⚠ 可能是封禁/规则拦截 → 停手并 quiet egress use <节点>"
            backoff = min(backoff * 2, a.max_backoff)
        else:
            backoff = 1.0
            ok += 1
        print(f"  #{i+1} status={st} {ms}ms {n}B {tag}")
        if a.show and txt:
            print("      " + txt[:300].replace("\n", " "))
        evidence("req", a.url, status=st, ms=ms, bytes=n, note=tag or err or "")
        if i < a.times - 1:
            time.sleep(delay * (1 + random.random() * 0.3))       # 抖动，避免完美等间隔被识别
    print(f"\n完成：{a.times} 次（正常 {ok}）· 目标 QPS≈{a.qps}")
    return 0


# ── 低速扫描 ─────────────────────────────────────────────────────────────────
def cmd_scan(a):
    ports = a.ports or COMMON_PORTS
    print(f"[*] 低速扫描 {a.host} 端口={ports if ports != '-' else '全端口(1-65535)'}")
    env = dict(os.environ, PATH=os.environ.get("PATH", "") + ":/usr/bin:/bin:/usr/local/bin")
    nmap = subprocess.run(["bash", "-lc", "command -v nmap"], capture_output=True, text=True, env=env).stdout.strip()
    if nmap:
        # ★ 实测有效：-T1 + 限速，避免被 SYN 限速导致大量假 filtered
        cmd = ["nmap", "-Pn", "-sS" if os.geteuid() == 0 else "-sT",
               "-p", "-" if ports == "-" else ports,
               "-T1", "--min-rate", "80", "--max-retries", "5", "--open", a.host]
        if ports == "-":
            print("    （全端口低速扫，可能要十几分钟 —— 这正是防守方限速的代价，别用 -T4 硬冲）")
        print("    " + " ".join(cmd))
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=a.timeout)
        out = r.stdout
        print(out)
        evidence("scan", a.host, note="nmap " + " ".join(cmd[1:]), bytes=len(out))
        if "filtered" in out:
            print("→ 出现大量 filtered：**说明对端在限速/丢包**，继续保持 -T1；换 -sT 或加 --scan-delay 1s 再试")
        return r.returncode
    print("    （没有 nmap，退化为 python socket 扫描：只连常见端口，单线程 + 间隔）")
    opened = []
    for p in [int(x) for x in ports.replace(" ", "").split(",") if x.isdigit()]:
        s = socket.socket()
        s.settimeout(a.timeout)
        try:
            if s.connect_ex((a.host, p)) == 0:
                print(f"    open  {p}")
                opened.append(p)
        except Exception:
            pass
        finally:
            s.close()
        time.sleep(0.2)
    evidence("scan", a.host, note=f"socket {len(opened)} open", bytes=0)
    print(f"完成：open={opened}")
    return 0


def main():
    ap = argparse.ArgumentParser(prog="quiet", description="对抗实战：低速 / 封禁探测 / 出口切换 / 证据留痕")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ip", help="看当前出口 IP + Clash 选中节点")
    p.add_argument("--via-proxy", action="store_true")
    p.set_defaults(fn=cmd_ip)

    p = sub.add_parser("trap", help="判『域名层拦截』还是『IP 被封』（A 域名 / B IP / C IP+Host 对比）")
    p.add_argument("host", help="目标 IP"); p.add_argument("domain", help="绑定的域名")
    p.add_argument("--port", type=int, default=80); p.add_argument("--path", default="/")
    p.add_argument("--timeout", type=int, default=12)
    p.set_defaults(fn=cmd_trap)

    p = sub.add_parser("probe", help="封禁探测（连发小请求判 403/429/重置/超时）")
    p.add_argument("url"); p.add_argument("--times", type=int, default=3)
    p.add_argument("--interval", type=float, default=1.0); p.add_argument("--timeout", type=int, default=12)
    p.set_defaults(fn=cmd_probe)

    p = sub.add_parser("req", help="节流请求（QPS 限制 + 429/5xx 退避 + 抖动）")
    p.add_argument("url"); p.add_argument("--qps", type=float, default=1.0)
    p.add_argument("--times", type=int, default=1); p.add_argument("-X", "--method", default="GET")
    p.add_argument("--data", default=None); p.add_argument("--ua", default=None)
    p.add_argument("--header", action="append"); p.add_argument("--show", action="store_true")
    p.add_argument("--timeout", type=int, default=15); p.add_argument("--max-backoff", type=float, default=30.0)
    p.set_defaults(fn=cmd_req)

    p = sub.add_parser("scan", help="低速端口扫描（nmap -T1 --min-rate 80 或 socket 退化）")
    p.add_argument("host"); p.add_argument("-p", "--ports", default=None)
    p.add_argument("--timeout", type=int, default=1800)
    p.set_defaults(fn=cmd_scan)

    p = sub.add_parser("egress", help="Clash 出口切换（ls / use <节点>）")
    p.add_argument("act", choices=["ls", "use"]); p.add_argument("name", nargs="?")
    p.set_defaults(fn=cmd_egress)

    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
