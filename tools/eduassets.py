#!/usr/bin/env python3
"""eduassets.py —— 多源被动子域枚举（EDU 资产"先搜干净"这一步）。

为什么（用户定规矩：**先把该学校资产搜全，打不动再换下一个**）：
  单靠 hackertarget 只能拿到几十个 host（实测 sdut=50），漏得非常多 ——
  我们后来靠搜索引擎才补出 `bgxt/yjsxt/hqfw/cgzh` 这些**真正有系统**的资产。
  所以枚举必须多源合并：**证书透明日志 + 被动 DNS + 历史 URL + URLScan**。

数据源（全部被动、只读、不扫目标）：
  · certspotter   —— CT 日志，支持 Link 头分页（`after=<cursor>`）
  · hackertarget  —— 被动 DNS（host,IP）
  · wayback(CDX)  —— 历史 URL → 提取 hostname
  · urlscan.io    —— 扫描记录里的域名
  · otx           —— AlienVault 被动 DNS（限流时会自动跳过）

用法：
    py tools/eduassets.py --domain sdut.edu.cn --domain cslg.edu.cn --domain cust.edu.cn
    py tools/eduassets.py --domain x.edu.cn --out-dir work/edu/assets --max-pages 20
    py tools/eduassets.py --selftest
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

UA = "Mozilla/5.0 (X11; Linux x86_64) pi-lab-eduassets/1.0 (passive enumeration)"


def _get(url: str, timeout: int = 25) -> tuple:
    """返回 (status, text, headers)。不抛异常。"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return r.status, r.read(2_000_000).decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read(20000).decode("utf-8", "replace"), dict(e.headers or {})
        except Exception:
            return e.code, "", dict(e.headers or {})
    except Exception as e:
        return "ERR", repr(e)[:80], {}


# ────────────────────────── 各源的解析（纯函数，可离线测） ──────────────────────────

def hosts_from_certspotter(text: str) -> set:
    try:
        d = json.loads(text)
    except Exception:
        return set()
    out = set()
    if isinstance(d, list):
        for e in d:
            if isinstance(e, dict):
                for n in e.get("dns_names", []) or []:
                    out.add(str(n).strip().lower())
    return out


def hosts_from_hackertarget(text: str) -> set:
    out = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or "," not in line:
            continue
        h = line.split(",", 1)[0].strip().lower()
        if re.match(r"^[a-z0-9_.-]+\.[a-z]{2,}$", h):
            out.add(h)
    return out


def hosts_from_wayback(text: str) -> set:
    out = set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(?:https?://)?([^/:?#\s]+)", line)
        if m:
            h = m.group(1).lower()
            if "." in h:
                out.add(h)
    return out


def hosts_from_urlscan(text: str) -> set:
    try:
        d = json.loads(text)
    except Exception:
        return set()
    out = set()
    for r in d.get("results", []) or []:
        for k in ("page", "task"):
            v = r.get(k) if isinstance(r, dict) else None
            if isinstance(v, dict) and isinstance(v.get("domain"), str):
                out.add(v["domain"].lower())
    return out


def hosts_from_otx(text: str) -> set:
    try:
        d = json.loads(text)
    except Exception:
        return set()
    out = set()
    for r in (d.get("passive_dns") or []):
        h = (r.get("hostname") or "").lower()
        if h:
            out.add(h)
    return out


def keep(hosts: set, domain: str) -> set:
    """只留属于该域的（含裸域），剔除通配与明显垃圾。"""
    d = domain.lower().lstrip(".")
    out = set()
    for h in hosts:
        h = h.replace("*.", "").strip(".")
        if not h or h.startswith("-"):
            continue
        if h == d or h.endswith("." + d):
            out.add(h)
    return out


# ────────────────────────── 采集 ──────────────────────────

def certspotter(domain: str, max_pages: int, sleep_s: float, log) -> set:
    out, url, pages = set(), (
        f"https://api.certspotter.com/v1/issuances?domain={domain}"
        f"&include_subdomains=true&expand=dns_names"), 0
    while url and pages < max_pages:
        st, txt, hdr = _get(url)
        pages += 1
        if st != 200:
            log(f"    certspotter 第{pages}页 → {st}")
            break
        got = hosts_from_certspotter(txt)
        out |= got
        nxt = re.search(r'<([^>]+)>;\s*rel="next"', hdr.get("Link", "") or hdr.get("link", ""))
        url = nxt.group(1) if nxt else None
        log(f"    certspotter 第{pages}页 +{len(got)}（累计 {len(out)}）")
        time.sleep(sleep_s)
    return out


def hackertarget(domain: str, log) -> set:
    st, txt, _ = _get(f"https://api.hackertarget.com/hostsearch/?q={domain}")
    got = hosts_from_hackertarget(txt) if st == 200 else set()
    log(f"    hackertarget → {st}，{len(got)} 条")
    return got


def wayback(domain: str, limit: int, log) -> set:
    url = (f"http://web.archive.org/cdx/search/cdx?url=*.{domain}"
           f"&output=text&fl=original&collapse=urlkey&limit={limit}")
    st, txt, _ = _get(url, timeout=90)
    got = hosts_from_wayback(txt) if st == 200 else set()
    log(f"    wayback → {st}，{len(got)} 个 host")
    return got


def urlscan(domain: str, log) -> set:
    st, txt, _ = _get(f"https://urlscan.io/api/v1/search/?q=domain%3A{domain}&size=1000")
    got = hosts_from_urlscan(txt) if st == 200 else set()
    log(f"    urlscan → {st}，{len(got)} 条")
    return got


def otx(domain: str, log) -> set:
    st, txt, _ = _get(f"https://otx.alienvault.com/api/v1/indicators/domain/{domain}/passive_dns")
    got = hosts_from_otx(txt) if st == 200 else set()
    log(f"    otx → {st}，{len(got)} 条")
    return got


def enumerate_domain(domain: str, out_dir: pathlib.Path, max_pages: int, wayback_limit: int,
                     sleep_s: float, sources: list, log=print) -> dict:
    log(f"  ── {domain} ──")
    per, allh = {}, set()
    for name in sources:
        try:
            if name == "certspotter":
                got = certspotter(domain, max_pages, sleep_s, log)
            elif name == "hackertarget":
                got = hackertarget(domain, log)
            elif name == "wayback":
                got = wayback(domain, wayback_limit, log)
            elif name == "urlscan":
                got = urlscan(domain, log)
            elif name == "otx":
                got = otx(domain, log)
            else:
                continue
        except Exception as e:
            log(f"    {name} 异常 {type(e).__name__}")
            got = set()
        got = keep(got, domain)
        per[name] = sorted(got)
        allh |= got
        time.sleep(sleep_s)
    allh = keep(allh, domain)
    out_dir.mkdir(parents=True, exist_ok=True)
    # ★ 与已有结果取并集：分源多次跑不许覆盖之前跑出来的 host（实测踩过：只跑 wayback 把 56 个冲成 6 个）
    f = out_dir / f"{domain}.hosts.txt"
    if f.exists():
        prev = {ln.strip().lower() for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()}
        allh = keep(allh | prev, domain)
    f.write_text("\n".join(sorted(allh)) + "\n", encoding="utf-8")
    (out_dir / f"{domain}.sources.json").write_text(
        json.dumps({k: len(v) for k, v in per.items()} | {"merged": len(allh)},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"  → 合并去重 {len(allh)} 个 host（各源："
        + "，".join(f"{k}={len(v)}" for k, v in per.items()) + "）")
    return {"domain": domain, "merged": sorted(allh), "per_source": per}


def selftest() -> int:
    checks = []

    def ok(n, c, extra=""):
        checks.append((n, bool(c), extra))

    cs = json.dumps([{"dns_names": ["a.sdut.edu.cn", "*.sdut.edu.cn", "x.other.cn"]}])
    ok("certspotter：取 dns_names 并去通配", hosts_from_certspotter(cs) ==
       {"a.sdut.edu.cn", "*.sdut.edu.cn", "x.other.cn"})

    ht = "a.sdut.edu.cn,1.1.1.1\nb.sdut.edu.cn,2.2.2.2\ngarbage\n"
    ok("hackertarget：解析 host,IP", hosts_from_hackertarget(ht) == {"a.sdut.edu.cn", "b.sdut.edu.cn"})

    wb = "https://www.sdut.edu.cn/a\nhttp://sub.sdut.edu.cn:8080/b\n"
    ok("wayback：从 URL 提 host（去端口/路径）",
       hosts_from_wayback(wb) == {"www.sdut.edu.cn", "sub.sdut.edu.cn"}, hosts_from_wayback(wb))

    ok("keep：只留目标域（含裸域、去通配）",
       keep({"a.sdut.edu.cn", "*.sdut.edu.cn", "sdut.edu.cn", "evil.com"}, "sdut.edu.cn")
       == {"a.sdut.edu.cn", "sdut.edu.cn"}, keep({"a.sdut.edu.cn", "*.sdut.edu.cn", "sdut.edu.cn", "evil.com"}, "sdut.edu.cn"))

    ok("urlscan：从 results 提 domain",
       hosts_from_urlscan(json.dumps({"results": [{"page": {"domain": "a.sdut.edu.cn"}},
                                                   {"task": {"domain": "b.sdut.edu.cn"}}]}))
       == {"a.sdut.edu.cn", "b.sdut.edu.cn"})
    ok("otx：从 passive_dns 提 hostname",
       hosts_from_otx(json.dumps({"passive_dns": [{"hostname": "C.sdut.edu.cn"}]})) == {"c.sdut.edu.cn"})
    ok("坏 JSON 不抛", hosts_from_certspotter("<html>nope") == set() and hosts_from_otx("x") == set())

    import tempfile
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="eduassets-"))
    real_cs, real_ht = globals()["certspotter"], globals()["hackertarget"]
    globals()["certspotter"] = lambda *a, **k: {"a.sdut.edu.cn"}
    globals()["hackertarget"] = lambda *a, **k: {"b.sdut.edu.cn", "a.sdut.edu.cn"}
    r = enumerate_domain("sdut.edu.cn", tmp, 1, 10, 0.0, ["certspotter", "hackertarget"])
    ok("合并去重且落盘", r["merged"] == ["a.sdut.edu.cn", "b.sdut.edu.cn"]
       and (tmp / "sdut.edu.cn.hosts.txt").exists())
    ok("各源计数落盘", json.loads((tmp / "sdut.edu.cn.sources.json").read_text())["merged"] == 2)
    # 反例：第二次只跑一个源，不许把已有 host 冲掉（并集语义）
    globals()["certspotter"] = lambda *a, **k: {"c.sdut.edu.cn"}
    globals()["hackertarget"] = lambda *a, **k: set()
    r2 = enumerate_domain("sdut.edu.cn", tmp, 1, 10, 0.0, ["certspotter"])
    ok("★ 分源重跑是并集，不覆盖已有 host",
       set(r2["merged"]) == {"a.sdut.edu.cn", "b.sdut.edu.cn", "c.sdut.edu.cn"}, r2["merged"])
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    globals()["certspotter"], globals()["hackertarget"] = real_cs, real_ht

    passed = sum(1 for _, c, _ in checks if c)
    for n, c, e in checks:
        print(f"  {'✓' if c else '✗'} {n}" + (f"  {e}" if e and not c else ""))
    print(f"\n{passed}/{len(checks)} 断言通过")
    return 0 if passed == len(checks) else 1


def main(argv) -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--domain", action="append", default=[], help="目标域（可重复）")
    ap.add_argument("--out-dir", default="work/edu/assets")
    ap.add_argument("--max-pages", type=int, default=20, help="certspotter 最多翻几页（每页≤100）")
    ap.add_argument("--wayback-limit", type=int, default=20000)
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--sources", default="certspotter,hackertarget,wayback,urlscan,otx")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if not args.domain:
        ap.print_help()
        return 2

    srcs = [s.strip() for s in args.sources.split(",") if s.strip()]
    out = pathlib.Path(args.out_dir)
    summary = {}
    for d in args.domain:
        r = enumerate_domain(d, out, args.max_pages, args.wayback_limit, args.sleep, srcs)
        summary[d] = {"merged": len(r["merged"]), "per_source": {k: len(v) for k, v in r["per_source"].items()}}
    print("\n=== 汇总 ===")
    for d, s in summary.items():
        print(f"  {d}: {s['merged']} 个 host   " + "，".join(f"{k}={v}" for k, v in s["per_source"].items()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
