#!/usr/bin/env python3
"""apiharvest.py —— 前端 asset → 接口清单（静态层，一键 harvest）。

为什么（历史决策 #30，api-recon 方法论第 ① 层）：
  web 题的前端面就是接口枚举的第一桶金。人工在几百 KB 的 bundle 里翻端点，
  既慢又常漏（压缩后字符串被拼接、模板字面量带变量、axios 方法藏在配置里）。
  这里把「拉 asset → 抠字符串 → 归类 → 去重」做成一步，输出端点清单当下一步
  （hook 录 outbound / 参数逆向）的靶子。

它做什么（纯字符串解析，不执行 JS，不发业务请求）：
  1. 收资产：--dir 扫目录里的 .js/.html，--url 抓 HTML 再抓同源 <script src>，或直接给文件
  2. 抠端点：fetch / axios / XHR.open 的 URL + router 的 path/url 配置 + 引号包裹的路径式字符串
  3. 归类：api（/api、/vN、/rest）/ route（前端路由）/ path（其它）/ static（图片字体样式）
  4. 归一：模板字面量里的变量 → {param}，query 拆成参数字段，同一路径多方法合并
  5. 闸门：单个 asset 超过 --big（默认 500KB）打警告 —— 主 entry 不许整读，改分段/按 URL 抓

用法：
    py tools/apiharvest.py --dir work/<pid>
    py tools/apiharvest.py --dir work/<pid> --json > work/<pid>/api.json
    py tools/apiharvest.py --url http://target/ --save work/<pid>
    py tools/apiharvest.py work/<pid>/app.js
    py tools/apiharvest.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MAX_INPUT = 8 * 1024 * 1024
BIG_ASSET = 500 * 1024
ASSET_EXT = (".js", ".mjs", ".html", ".htm")
STATIC_EXT = (".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff",
              ".woff2", ".ttf", ".eot", ".map", ".mp4", ".webp")

_TMPL = re.compile(r"\$\{[^}]{0,60}\}")
_Q = r"""['"\x60]"""
_METHOD_CALL = re.compile(
    r"\b(?:axios|api|request|http|client)\.(get|post|put|delete|patch)\s*\(\s*"
    + _Q + r"([^'\"\x60]{1,200})" + _Q,
    re.I)
_FETCH = re.compile(r"\bfetch\s*\(\s*" + _Q + r"([^'\"\x60]{1,200})" + _Q)
_XHR = re.compile(
    r"\.open\s*\(\s*" + _Q + r"([A-Za-z]{3,7})" + _Q + r"\s*,\s*" + _Q
    + r"([^'\"\x60]{1,200})" + _Q)
_ROUTE_CFG = re.compile(
    r"\b(?:path|url|endpoint|action)\s*:\s*" + _Q + r"(/[^'\"\x60]{0,200})" + _Q)
_STR = re.compile(_Q + r"(/[A-Za-z0-9_\-./{}$:?=&%+*~#]{1,180})" + _Q)
_SCRIPT_SRC = re.compile(r"<script[^>]*\bsrc\s*=\s*" + _Q + r"([^'\"\x60]+)" + _Q, re.I)


def read_capped(path, limit=MAX_INPUT):
    """只认普通文件 + 限长（仓库硬规矩：拒符号链接 / FIFO / 超大）"""
    if os.path.islink(path):
        raise ValueError("拒绝符号链接: " + str(path))
    if not os.path.isfile(path):
        raise ValueError("不是普通文件: " + str(path))
    size = os.path.getsize(path)
    if size > limit:
        raise ValueError("文件过大 " + str(size) + "B > " + str(limit) + "B: " + str(path))
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read(limit)


def _var_name(expr):
    tail = expr.strip().split(".")[-1]
    tail = re.sub(r"[^A-Za-z0-9_]", "", tail)
    return tail or "x"


def norm_path(raw):
    """返回 (归一后的路径, 参数字段)：模板变量 → {param}，query → 参数名，去尾部标点。"""
    p = (raw or "").strip()
    params = []
    for var in _TMPL.findall(p):
        name = _var_name(var[2:-1])
        if name not in params:
            params.append(name)
    p = _TMPL.sub(lambda m: "{" + _var_name(m.group()[2:-1]) + "}", p)
    p = p.rstrip(".,;:)]}")
    if "?" in p:
        base, qs = p.split("?", 1)
        for part in qs.split("&"):
            k = part.split("=")[0].strip()
            if k and not k.startswith("{") and k not in params:
                params.append(k)
        p = base
    return p, params


def classify(path):
    low = path.lower()
    if any(low.endswith(e) for e in STATIC_EXT):
        return "static"
    if re.search(r"/(api|rest|graphql|rpc)(/|$)", low) or re.search(r"/v[0-9]+(/|$)", low):
        return "api"
    return "path"


def harvest(text):
    """文本 → {path: {methods, kinds, params}}，外加原始命中条数。"""
    found = {}
    hits = [0]

    def add(raw, method=None, kind_hint=None):
        if not raw:
            return
        p, params = norm_path(raw)
        if not p.startswith("/") or p == "/" or len(p) > 200:
            return
        kind = kind_hint or classify(p)
        rec = found.setdefault(p, {"methods": set(), "kinds": set(), "params": set()})
        rec["kinds"].add(kind)
        if method:
            rec["methods"].add(method.upper())
        for x in params:
            rec["params"].add(x)
        hits[0] += 1

    for m in _XHR.finditer(text):
        add(m.group(2), m.group(1))
    for m in _METHOD_CALL.finditer(text):
        add(m.group(2), m.group(1))
    for m in _FETCH.finditer(text):
        add(m.group(1))
    for m in _ROUTE_CFG.finditer(text):
        add(m.group(1), None, "route")
    for m in _STR.finditer(text):
        add(m.group(1))
    return found, hits[0]


def merge_into(acc, found):
    for p, rec in found.items():
        cur = acc.setdefault(p, {"methods": set(), "kinds": set(), "params": set()})
        cur["methods"] |= rec["methods"]
        cur["kinds"] |= rec["kinds"]
        cur["params"] |= rec["params"]
    return acc


def collect_dir(root):
    root = pathlib.Path(root)
    if not root.is_dir():
        raise ValueError("不是目录: " + str(root))
    return [p for p in sorted(root.rglob("*"))
            if p.is_file() and not p.is_symlink() and p.suffix.lower() in ASSET_EXT]


def fetch_once(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "apiharvest/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(MAX_INPUT)
        enc = r.headers.get_content_charset() or "utf-8"
        final = r.geturl()
    return final, raw.decode(enc, "replace")


def assets_from_html(base, html):
    from urllib.parse import urljoin, urlsplit
    basehost = urlsplit(base).netloc
    out = []
    for m in _SCRIPT_SRC.finditer(html):
        u = urljoin(base, m.group(1).strip())
        host = urlsplit(u).netloc
        if host and host != basehost:
            continue
        out.append(u)
    return out


def analyze(texts, big=BIG_ASSET):
    acc, warnings, per = {}, [], []
    total = 0
    for name, text in texts:
        n = len(text)
        total += n
        if n > big:
            warnings.append("%s 有 %d B > %d B：主 entry 不许整读（闸门），改 --dir 分段或只抓重点 chunk"
                            % (name, n, big))
        found, hits = harvest(text)
        merge_into(acc, found)
        per.append({"asset": name, "bytes": n, "raw_hits": hits, "endpoints": len(found)})
    order = {"api": 0, "route": 1, "path": 2, "static": 3}
    rows = []
    for p in sorted(acc):
        rec = acc[p]
        kinds = sorted(rec["kinds"], key=lambda k: order.get(k, 9))
        rows.append({"path": p, "kind": kinds[0], "kinds": kinds,
                     "methods": sorted(rec["methods"]), "params": sorted(rec["params"])})
    rows.sort(key=lambda r: (order.get(r["kind"], 9), r["path"]))
    return {"assets": per, "endpoints": rows, "warnings": warnings, "bytes": total}


def render(rep):
    L = ["== apiharvest 接口清单 ==  资产 %d 份 / %d B，端点 %d 条"
         % (len(rep["assets"]), rep["bytes"], len(rep["endpoints"])), ""]
    for w in rep["warnings"]:
        L.append("  ! " + w)
    if rep["warnings"]:
        L.append("")
    for r in rep["endpoints"]:
        meth = ",".join(r["methods"]) if r["methods"] else "-"
        params = ("  参数: " + ", ".join(r["params"])) if r["params"] else ""
        L.append("  [" + r["kind"].ljust(6) + "] " + meth.ljust(14) + r["path"] + params)
    if not rep["endpoints"]:
        L.append("  （没抠到端点：bundle 可能被压缩成拼接字符串，改走运行时 hook 录 outbound）")
    L.append("")
    L.append("  下一步（门禁顺序）：① 静态清单 → ② 运行时 hook 录 outbound → ③ 参数 diff → ④ 才允许字典")
    return "\n".join(L)


def selftest():
    errs = []
    tmpl = "\"/api/item/" + "$" + "{itemId}/detail\""
    js = (
        "var a=fetch(\"/api/user/list\");"
        "axios.post(\"/api/user/\" + uid + \"/role\", {});"
        "axios.get(\"/api/config?verbose=1\");"
        "xhr.open(\"PUT\", \"/api/note/7\");"
        "var r=new VueRouter({routes:[{path:\"/admin/audit\",component:X},{path:\"/login\"}]});"
        "var s=\"/static/logo.png\";"
        "var t=" + tmpl + ";"
    )
    rep = analyze([("app.js", js)])
    paths = [r["path"] for r in rep["endpoints"]]
    for want in ("/api/user/list", "/api/note/7", "/api/config", "/admin/audit", "/login", "/api/item/{itemId}/detail"):
        if want not in paths:
            errs.append("没抠到 " + want + "（得到 " + ", ".join(paths[:12]) + "）")
    by = {r["path"]: r for r in rep["endpoints"]}
    if by.get("/api/note/7", {}).get("methods") != ["PUT"]:
        errs.append("XHR 方法没归到 /api/note/7：" + str(by.get("/api/note/7")))
    if by.get("/api/user/list", {}).get("kind") != "api":
        errs.append("/api/user/list 应归 api：" + str(by.get("/api/user/list")))
    if by.get("/admin/audit", {}).get("kind") != "route":
        errs.append("router 的 path 应归 route：" + str(by.get("/admin/audit")))
    if "verbose" not in by.get("/api/config", {}).get("params", []):
        errs.append("query 参数没拆出来：" + str(by.get("/api/config")))
    if "itemId" not in by.get("/api/item/{itemId}/detail", {}).get("params", []):
        errs.append("模板变量没记成参数：" + str(by.get("/api/item/{itemId}/detail")))
    if by.get("/static/logo.png", {}).get("kind") != "static":
        errs.append("静态资源应归 static")
    big = analyze([("main.js", "x" * 10)], big=2)
    if not big["warnings"] or "闸门" not in big["warnings"][0]:
        errs.append("超 --big 应警告（闸门）")
    r2 = render(rep)
    if "门禁顺序" not in r2 or "接口清单" not in r2:
        errs.append("渲染缺抬头或门禁提示")
    html = "<html><script src=\"/static/app.js\"></script><script src=\"https://cdn.x/lib.js\"></script></html>"
    srcs = assets_from_html("http://t.local/", html)
    if srcs != ["http://t.local/static/app.js"]:
        errs.append("同源过滤不对：" + str(srcs))
    if errs:
        for e in errs:
            print("  FAIL " + e)
        return 1
    print("apiharvest selftest: 9 项全过")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="前端 asset → 接口清单（静态 harvest）")
    ap.add_argument("files", nargs="*", help="asset 文件（.js/.html）")
    ap.add_argument("--dir", help="目录，扫里面的 .js/.html")
    ap.add_argument("--url", help="抓一次 HTML，再抓同源 <script src>")
    ap.add_argument("--save", help="--url 模式下把抓到的内容存到这个目录")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--big", type=int, default=BIG_ASSET, help="单 asset 警告阈值（默认 512000）")
    ap.add_argument("--selftest", action="store_true", help="离线自检")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()

    texts = []
    try:
        for f in a.files:
            texts.append((os.path.basename(f), read_capped(f)))
        if a.dir:
            for p in collect_dir(a.dir):
                texts.append((str(p), read_capped(str(p))))
        if a.url:
            final, html = fetch_once(a.url)
            texts.append((final, html))
            for u in assets_from_html(final, html):
                try:
                    fu, js = fetch_once(u)
                except (urllib.error.URLError, OSError) as e:
                    print("[fetch] 跳过 " + u + "：" + str(e), file=sys.stderr)
                    continue
                texts.append((fu, js))
                if a.save:
                    out = pathlib.Path(a.save)
                    out.mkdir(parents=True, exist_ok=True)
                    name = fu.rstrip("/").split("/")[-1] or "asset.js"
                    (out / name).write_text(js, encoding="utf-8", errors="replace")
    except ValueError as e:
        print("✗ " + str(e), file=sys.stderr)
        return 2
    except (urllib.error.URLError, OSError) as e:
        print("✗ 抓取失败：" + str(e), file=sys.stderr)
        return 2

    if not texts:
        print("✗ 没有输入：给文件、--dir 或 --url", file=sys.stderr)
        return 2
    rep = analyze(texts, big=a.big)
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(render(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
