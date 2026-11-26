#!/usr/bin/env python3
"""probe.py —— WSL 侧探活/指纹（替代跑不通的 Windows 侧 httpx.exe）。

为什么要它（2026-10-04 实测）：
  `tools/bin/httpx` → `gobin/httpx.exe`（Windows 侧 Go 程序）在本机 **DNS 直接失败**：
      [DBG] Failed 'https://www.pku.edu.cn': cause="no address found for host"
  同一条 URL 用 WSL 侧的 `curl` / `py urllib` 是 **200**（走 127.0.0.1:7897 + Clash fake-ip）。
  → 于是「侦察主链」在 WSL 侧自建：限速、可跑、输出能直接喂给 `fgs`/`poc`。

设计原则（与 authzdiff 一致）：
  · 默认 **GET 只读**；`--head` 可切 HEAD。不做任何写操作。
  · 默认 **1 rps**，单目标并发 1（教育网/脆弱站点红线：`skills/src-recon/references/对抗-别把IP打没.md`）
  · 输出 JSONL（每行一条），方便后续 `jq`/脚本；证据可回放。

用法：
    py tools/probe.py https://a.edu.cn https://b.edu.cn
    py tools/probe.py -l hosts.txt --qps 1 --out work/edu/alive.jsonl --tech
    py tools/probe.py --selftest
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
#   自包含（自己 import sys），不依赖本文件是否已 import sys 或用别名。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 指纹表：特征 → (技术名, 对应的 poc 查询词)
SIGS = [
    ("rememberMe=", "Shiro", "shiro"),
    ("X-Application-Context", "Spring Boot", "spring"),
    ("Whitelabel Error Page", "Spring Boot", "spring"),
    ("/actuator", "Spring Boot Actuator", "spring"),
    ("Apache Tomcat", "Tomcat", "tomcat"),
    ("nginx", "nginx", ""),
    ("Microsoft-IIS", "IIS", ""),
    ("weaver", "泛微 e-cology", "泛微"),
    ("ecology", "泛微 e-cology", "泛微"),
    ("/seeyon/", "致远 A8", "致远"),
    ("ispirit", "通达 OA", "通达"),
    ("MYOA", "通达 OA", "通达"),
    ("jwglxt", "正方教务", "正方"),
    ("jsxsd", "教务系统(青果/正方系)", "教务"),
    ("cas/login", "统一认证 CAS", "CAS"),
    ("统一身份认证", "统一认证", "CAS"),
    ("/opac/", "图书馆 OPAC", "汇文"),
    ("phpMyAdmin", "phpMyAdmin", ""),
    ("宝塔", "宝塔面板", "宝塔"),
    ("RuoYi", "若依", "若依"),
    ("yonyou", "用友", "用友"),
    ("u8/", "用友 U8", "用友"),
    ("金智", "金智教育", ""),
    ("wui/index", "泛微 wui", "泛微"),
]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """★ 默认**不跟随跳转**（2026-10-04 实盘教训，同一晚两次同源误判）：
    跟随 302 会让「状态码/页面大小/技术栈」全部变成**跳转终点**的，导致
      · `www.cslg.edu.cn` 被打上「致远A8+正方教务」（标签来自它 302 到的 `www.szut.edu.cn`）
      · `debot/dzrgjy/exw` 被当成「所有路径回同一个 59KB 大页」（那是落到 CAS 登录页的尺寸）
    所以默认只看本体；要看终点显式加 `--follow`。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def fetch(url: str, timeout: float = 15, method: str = "GET", max_bytes: int = 200_000,
          proxy: str | None = None, follow: bool = False) -> dict:
    """单次请求 → 结果 dict（不抛异常；错误记在 err 字段）。"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE          # 只做踩点，不为证书链失败丢掉整站信息
    handlers = [urllib.request.HTTPSHandler(context=ctx)]
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    out = {"url": url, "status": None, "len": 0, "title": "", "server": "",
           "tech": [], "poc_hints": [], "final_url": url, "redirect_to": "", "err": ""}
    if not follow:
        handlers.append(_NoRedirect())
    op = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": UA}, method=method)
    t0 = time.time()
    out = {"url": url, "status": None, "len": 0, "title": "", "server": "",
           "tech": [], "poc_hints": [], "final_url": url, "err": ""}
    try:
        with op.open(req, timeout=timeout) as r:
            body = r.read(max_bytes)
            out["status"] = r.status
            out["len"] = len(body)
            out["final_url"] = r.geturl()
            hdr = {k.lower(): v for k, v in r.headers.items()}
            out["server"] = hdr.get("server", "")
            if 300 <= r.status < 400:
                out["redirect_to"] = hdr.get("location", "")
                return out
            blob = body.decode("utf-8", "replace")
            m = re.search(r"<title[^>]*>(.*?)</title>", blob, re.S | re.I)
            out["title"] = re.sub(r"\s+", " ", m.group(1)).strip()[:120] if m else ""
            hay = body[:60000].decode("utf-8", "replace") + " " + " ".join(f"{k}:{v}" for k, v in hdr.items())
            # ★ 剔除 href/src/action 属性值：**外链到别的系统不是本体的技术栈证据**
            #   实测误报（2026-10-04）：www.cslg.edu.cn 首页超链到 _reused_ 的
            #   zyoa.seeyon/index.jsp 与 jwxt/jwglxt → 被打上「致远A8+正方教务」标签，
            #   实际本体只是 302 跳转器 / 静态站。
            hay = re.sub(r'(?:href|src|action|data-src)\s*=\s*["\'][^"\']*["\']', " ", hay)
            for needle, tech, poc in SIGS:
                if needle.lower() in hay.lower():
                    if tech not in out["tech"]:
                        out["tech"].append(tech)
                    if poc and poc not in out["poc_hints"]:
                        out["poc_hints"].append(poc)
    except urllib.error.HTTPError as e:
        out["status"] = e.code
        out["err"] = f"HTTP {e.code}"
        try:
            out["server"] = e.headers.get("Server", "")
            if 300 <= e.code < 400:
                out["redirect_to"] = e.headers.get("Location", "")
        except Exception:
            pass
    except Exception as e:
        out["err"] = f"{type(e).__name__}: {e}"[:160]
    out["ms"] = int((time.time() - t0) * 1000)
    out["unreachable"] = looks_unreachable(out)
    return out


def fetch_with_fallback(url: str, **kw) -> dict:
    """先按给定 scheme 试，失败（连不上/SSL 错）就换另一 scheme 再试一次。
    EDU 大量站点只有 http，或 https 证书链不通 —— 一次失败就放弃会丢一大半资产。"""
    r = fetch(url, **kw)
    if r["status"] is not None:
        return r
    alt = url.replace("https://", "http://", 1) if url.startswith("https://") else \
        url.replace("http://", "https://", 1)
    r2 = fetch(alt, **kw)
    if r2["status"] is not None:
        r2["fallback_from"] = url
        return r2
    r["err"] = f"{r['err']} | 回退 {alt} 也失败: {r2['err']}"[:200]
    return r
    """★ 环境陷阱（2026-10-04 实测，两个子代理都踩到）：
    经本机 Clash（127.0.0.1:7897）访问**不可达或不存在**的目标，会统一回
    `502 / Content-Length: 0`（body 为空）或 TLS `unexpected EOF` ——
    **与 NXDOMAIN 不可区分**（拿随机域名试探，响应逐字节相同）。
    所以这类结果**既不能当存活、也不能当“后端挂了”**，只能记“未判定”。"""
    if r.get("status") == 502 and r.get("len", 0) == 0:
        return True
    if r.get("status") is None and "EOF" in str(r.get("err", "")):
        return True
    return False


def looks_unreachable(r: dict) -> bool:
    """★ 环境陷阱（2026-10-04 实测，两个子代理都踩到）：
    经本机 Clash（127.0.0.1:7897）访问**不可达或不存在**的目标，会统一回
    `502 / Content-Length: 0`（body 为空）或 TLS `unexpected EOF` ——
    **与 NXDOMAIN 不可区分**（拿随机域名试探，响应逐字节相同）。
    所以这类结果**既不能当存活、也不能当“后端挂了”**，只能记“未判定”。"""
    if r.get("status") == 502 and r.get("len", 0) == 0:
        return True
    if r.get("status") is None and "EOF" in str(r.get("err", "")):
        return True
    return False


def normalize(u: str) -> str:
    u = u.strip()
    if "," in u:                     # 兼容 hostsearch 的 "host,IP" 格式
        u = u.split(",", 1)[0]
    if not re.match(r"^https?://", u):
        return "https://" + u.strip("/")
    return u


def selftest() -> int:
    checks = []

    def ok(n, c, extra=""):
        checks.append((n, bool(c), extra))

    ok("normalize：裸域名补 https", normalize("a.edu.cn") == "https://a.edu.cn")
    ok("normalize：host,IP 格式只取 host", normalize("old.x.edu.cn,10.28.208.3") == "https://old.x.edu.cn")
    ok("normalize：保留下划线端口路径", normalize("http://x:8080/a") == "http://x:8080/a")
    # 指纹：用伪造的 HTML 走正则/匹配逻辑（不联网）
    blob = "<html><head><title> 统一身份认证平台 </title></head><body>nginx jwglxt</body></html>"
    hits = [t for n, t, _ in SIGS if n.lower() in blob.lower()]
    ok("指纹：标题/正文命中统一认证+正方", "统一认证" in hits and "正方教务" in hits, hits)
    ok("指纹：Shiro 从 cookie 名命中", any(t == "Shiro" for n, t, _ in SIGS if n in "rememberMe=x"))
    ok("指纹：poc 提示去重后有序", [p for n, _, p in SIGS if p][:1] == ["shiro"])
    # ★ 外链不算本体技术栈（实测误报来源：cslg 首页超链到 seeyon/jwglxt）
    linked = '<html><body><a href="http://zyoa.x.edu.cn/seeyon/index.jsp">oa</a>' \
             '<img src="/jwglxt/img.png"></body></html>'
    stripped = re.sub(r'(?:href|src|action|data-src)\s*=\s*["\'][^"\']*["\']', " ", linked)
    hit_stripped = [t for n, t, _ in SIGS if n.lower() in stripped.lower()]
    ok("外链/资源引用不算本体指纹（致远/正方不应命中）",
       "致远 A8" not in hit_stripped and "正方教务" not in hit_stripped, hit_stripped)
    ok("但正文里的真特征仍能命中",
       "致远 A8" in [t for n, t, _ in SIGS if n.lower() in "<body>/seeyon/index.jsp</body>".lower()])
    # fetch 的错误路径不应抛
    import tempfile  # noqa: F401
    r = fetch("http://127.0.0.1:9/", timeout=1, proxy=None)
    ok("连不上时返回 err 不抛异常", r["status"] is None and r["err"], r["err"])
    ok("结果结构完整", set(r) >= {"url", "status", "len", "server", "tech", "poc_hints", "err"})
    ok("502/0B 判定为“未判定（代理不可达）”",
       looks_unreachable({"status": 502, "len": 0, "err": ""}) is True)
    ok("真实 200 不误判", looks_unreachable({"status": 200, "len": 100, "err": ""}) is False)
    ok("TLS EOF 也归“未判定”",
       looks_unreachable({"status": None, "len": 0, "err": "URLError: SSL: UNEXPECTED_EOF"}) is True)

    passed = sum(1 for _, c, _ in checks if c)
    for n, c, e in checks:
        print(f"  {'✓' if c else '✗'} {n}{('  ' + str(e)) if e and not c else ''}")
    print(f"\n{passed}/{len(checks)} 断言通过")
    return 0 if passed == len(checks) else 1


def main(argv) -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("urls", nargs="*")
    ap.add_argument("-l", "--list", help="URL 清单文件（每行一个，支持 # 注释）")
    ap.add_argument("--qps", type=float, default=1.0, help="每秒请求数（默认 1，别调高）")
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--head", action="store_true", help="用 HEAD（更快，但拿不到标题/正文指纹）")
    ap.add_argument("--tech", action="store_true", help="打印命中的技术栈与 poc 查询提示")
    ap.add_argument("--out", help="JSONL 落盘路径")
    ap.add_argument("--proxy", default=None, help="默认从环境变量取（Clash 7897）；--proxy '' 强制直连")
    ap.add_argument("--follow", action="store_true", help="跟随跳转（默认**不跟随** —— 跟随会让状态/大小/指纹变成终点的）")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    targets = [normalize(u) for u in args.urls]
    if args.list:
        p = pathlib.Path(args.list)
        if not p.is_file() or p.stat().st_size > 8 * 1024 * 1024:
            print(f"[x] 清单必须是普通文件且 <8MB：{p}", file=sys.stderr)
            return 2
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                targets.append(normalize(line))
    if not targets:
        ap.print_help()
        return 2

    proxy = args.proxy
    if proxy is None:
        import os
        proxy = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY") or ""
        if "127.0.0.1" in proxy or "localhost" in proxy:
            pass  # 本机 Clash，可用
        proxy = proxy or None

    fh = open(args.out, "a", encoding="utf-8") if args.out else None
    interval = 1.0 / max(args.qps, 0.05)
    t_prev = 0.0
    for u in targets:
        wait = interval - (time.time() - t_prev)
        if wait > 0:
            time.sleep(wait)
        t_prev = time.time()
        r = fetch_with_fallback(u, timeout=args.timeout,
                                method="HEAD" if args.head else "GET", proxy=proxy,
                                follow=args.follow)
        if fh:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            fh.flush()
        status = r["status"] if r["status"] is not None else "ERR"
        line = f"{str(status):>4}  {r['len']:>7}B  {r['ms']:>5}ms  {u}"
        if r["title"]:
            line += f"  「{r['title'][:40]}」"
        if r["server"]:
            line += f"  [{r['server'][:24]}]"
        if r["err"] and r["status"] is None:
            line += f"  ⚠ {r['err'][:60]}"
        if r.get("unreachable"):
            line += "  ⚠ 502/0B：代理不可达，**不能算存活也不等于站点挂了**（Clash 对 NXDOMAIN 同样回 502）"
        print(line)
        if r.get("redirect_to"):
            line += f"  → {r['redirect_to'][:60]}"
        if args.tech and (r["tech"] or r["poc_hints"]):
            print(f"        tech: {', '.join(r['tech']) or '-'}"
                  + ("   → poc: " + " / ".join(f'poc "{h}"' for h in r["poc_hints"])
                     if r["poc_hints"] else ""))
    if fh:
        fh.close()
        print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
