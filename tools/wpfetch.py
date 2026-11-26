#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wpfetch.py —— 把外部 WP 页面抓成本地 Markdown（离线可查，喂给知识库 `kb`）

为什么要它：知识库 `kb` 只索引**本机文件**。外部 WP（博客 / GitHub 归档）得先落到盘上、
并且**留得下来源、抓取时间、发布日期**，否则半年后没人知道某段结论是从哪扒的。
所以本脚本只做三件事：抓 → 转成 Markdown（带来源头）→ 记账（`_fetch-log.jsonl` + `INDEX.md`）。

纪律（照 `tools/knowledge/` 既有的 PROVENANCE 约定）
  · **只抓公开页面，只存本机离线查阅**；不外发、不再分发；来源与许可写在每页头部和 `PROVENANCE.md`
  · **日期红线**：`--min-year`（默认 2025）—— 页面上找得到发布日期且早于它 → **拒收**
    （找不到日期就收，但头部写 `published: unknown`，并进 INDEX 的“日期未确认”一栏，不假装确定）
  · **有界**：单页超过 `--max-chars` 截断（默认 400k）；不抓二进制/附件
  · **幂等**：同一 URL 已抓过（记在 `_fetch-log.jsonl`）→ 跳过，除非 `--force`

用法
    wpfetch --list tools/knowledge/cn-wp-2025/sources.json          # 按清单抓
    wpfetch --list ... --only web                                   # 只抓某个方向
    wpfetch --list ... --dry-run                                    # 只打印将抓什么
    wpfetch --selftest                                              # 转换器自检（不联网）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 仓库根：$PI_LAB_ROOT → 从本文件位置推（tools/ 一级深），不写死绝对路径
LAB = Path(os.environ.get("PI_LAB_ROOT") or Path(__file__).resolve().parent.parent)
ROOT = LAB / "tools" / "knowledge" / "ctf-wp"
LOG = ROOT / "_fetch-log.jsonl"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"])}


# ────────────────────────── 抓 ──────────────────────────

def fetch(url: str, timeout: int = 40) -> tuple[int, bytes]:
    """curl 抓（本机 curl 有代理/UA 习惯，也便于看到真实 http code）；只取正文。"""
    cmd = ["curl", "-sSL", "--compressed", "-A", UA, "-m", str(timeout),
           "-w", "\n%{http_code}", url]
    # ★ 2026-11-12 补 timeout：curl 自带 -m 但那是**传输层**超时，
    #   进程本身仍可能不退出（DNS/代理握手卡住）。外层再兜一层。
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout + 15)
    except subprocess.TimeoutExpired:
        return 0, b""
    body = r.stdout
    if not body:
        return 0, b""
    idx = body.rfind(b"\n")
    try:
        code = int(body[idx + 1:].strip() or 0)
    except ValueError:
        code = 0
    return code, body[:idx]


# ────────────────────────── 转 Markdown ──────────────────────────

def html_to_md(html: bytes, url: str) -> tuple[str, str, str]:
    """返回 (markdown 正文, 标题, 检测到的发布日期)。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html.decode("utf-8", "replace"), "lxml")
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer",
                     "aside", "form", "iframe", "svg"]):
        tag.decompose()

    title = ""
    for sel in (("meta", {"property": "og:title"}), ("meta", {"name": "og:title"})):
        el = soup.find(*sel)
        if el and el.get("content"):
            title = el["content"].strip()
            break
    if not title and soup.title:
        title = soup.title.get_text(strip=True)

    # 发布日期：多来源试一次，找不到就 unknown（不猜）
    published = ""
    cands = []
    for prop in ("article:published_time", "og:published_time", "itemprop"):
        el = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        if el and el.get("content"):
            cands.append(el["content"])
    for t in soup.find_all(["time"]):
        if t.get("datetime"):
            cands.append(t["datetime"])
        elif t.get_text(strip=True):
            cands.append(t.get_text(strip=True))
    blob = " ".join(cands[:6])
    m = re.search(r"(20\d\d)[-/年\.](\d{1,2})([-/月\.](\d{1,2}))?", blob)
    if m:
        published = f"{m.group(1)}-{int(m.group(2)):02d}" + (f"-{int(m.group(4)):02d}" if m.group(4) else "")
    else:
        m2 = re.search(r"([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(20\d\d)", blob)
        if m2 and m2.group(1).casefold()[:3] in {k[:3] for k in MONTHS}:
            mon = [v for k, v in MONTHS.items() if k[:3] == m2.group(1).casefold()[:3]][0]
            published = f"{m2.group(3)}-{mon:02d}-{int(m2.group(2)):02d}"

    # 正文：优先 article/main，退到最大文本块
    node = soup.find("article") or soup.find("main") or soup.body or soup
    parts: list[str] = []

    def emit(text: str) -> None:
        t = re.sub(r"[ \t\xa0]+", " ", text.replace("\u200b", ""))
        if t.strip():
            parts.append(t.strip())

    for el in node.find_all(["h1", "h2", "h3", "h4", "p", "li", "pre", "blockquote", "td", "th"]):
        if el.name == "pre":
            code = el.get_text("\n", strip=False)
            lang = ""
            cls = " ".join(el.get("class") or [])
            m = re.search(r"language-([\w+-]+)", cls)
            if m:
                lang = m.group(1)
            emit(f"\n```{lang}\n{code.strip()}\n```\n")
        elif el.name.startswith("h"):
            emit("#" * int(el.name[1]) + " " + el.get_text(" ", strip=True))
        elif el.name in ("td", "th"):
            emit("| " + el.get_text(" ", strip=True) + " |")
        else:
            emit(el.get_text(" ", strip=True))
    md = "\n\n".join(parts)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md, title or url, published



# ────────────────────────── 发现（sitemap / RSS）──────────────────────────

def discover(base: str, min_year: int = 2025,
             keywords: tuple[str, ...] = ("writeup", "write-up", "wp", "ctf", "题解", "复现", "复盘")) -> list[str]:
    """从 sitemap.xml / RSS 里捞出 2025+ 且像 WP 的链接 —— 比在搜索引擎里猜 URL 靠谱。"""
    base = base.rstrip("/")
    urls: list[str] = []
    for path in ("/sitemap.xml", "/index.xml", "/atom.xml", "/feed"):
        code, body = fetch(base + path, timeout=25)
        if code != 200 or not body:
            continue
        text = body.decode("utf-8", "replace")
        if not re.search(r"<(urlset|rss|feed)", text, re.I):
            continue
        raw = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text, re.I) + \
              re.findall(r"<link[^>]*href=[\"']([^\"']+)[\"']", text, re.I)
        for loc in raw:
            u = loc.strip()
            if not u:
                continue
            if not u.startswith("http"):
                u = base + ("" if u.startswith("/") else "/") + u
            # 年份不在这里硬卡：很多博文 URL 里没有年份，硬卡会**一条都捞不到**
            # （2026-10-12 实测：8 个站点全 0 条就是这个原因）→ 年份红线放到 fetch 阶段按「发布日期」判。
            low = u.casefold()
            if any(k in low for k in keywords) or re.search(r"/(20(2[5-9]|3\d))/", u):
                urls.append(u)
        if urls:
            print(f"   （来源：{path}，{len(urls)} 条）")
            break
    else:
        print("   （没有 sitemap.xml / index.xml / atom.xml / feed 可用）")
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def cmd_discover(a) -> int:
    for base in a.discover:
        got = discover(base, a.min_year)
        print(f"## {base} → {len(got)} 条候选")
        for u in got:
            print("   ", u)
    return 0

# ────────────────────────── 落盘 ──────────────────────────

def slugify(text: str, max_len: int = 70) -> str:
    s = re.sub(r"https?://", "", text)
    s = re.sub(r"[^\w\u4e00-\u9fff\-]+", "-", s, flags=re.UNICODE)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:max_len] or hashlib.md5(text.encode()).hexdigest()[:10]


def load_log() -> dict:
    done = {}
    if LOG.exists():
        for line in LOG.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                done[rec.get("url", "")] = rec
    return done


def cmd_fetch(a) -> int:
    src = Path(a.list)
    if not src.exists():
        sys.exit(f"wpfetch: 清单不存在：{src}")
    data = json.loads(src.read_text(encoding="utf-8"))
    items = data.get("sources", data)
    if a.only:
        items = [it for it in items if it.get("dir") == a.only]
    done = load_log()
    ROOT.mkdir(parents=True, exist_ok=True)
    ok = skip = bad = stale = 0
    for it in items:
        url, direction = it["url"], it.get("dir", "misc")
        if a.dry_run:
            print(f"[dry] {direction:9s} {it.get('event',''):22s} {url}")
            continue
        if url in done and not a.force:
            print(f"[skip] 已抓过：{url}")
            skip += 1
            continue
        code, body = fetch(url)
        if code != 200 or len(body) < 200:
            print(f"[fail] {code} {url}")
            bad += 1
            continue
        if len(body) > a.max_chars:
            body = body[:a.max_chars]
        try:
            head_low = body[:4000].decode("utf-8", "replace").casefold()
            if "<html" not in head_low and "<!doctype" not in head_low:
                # 裸 Markdown（GitHub raw 那类）：直接用，标题取第一个 # 标题
                md = body.decode("utf-8", "replace")
                m = re.search(r"^#\s+(.+)$", md, re.M)
                title = (m.group(1).strip() if m else url)[:200]
                published = (re.search(r"(20\d\d)-\d\d-\d\d", url) or [""])[0] if False else ""
            else:
                md, title, published = html_to_md(body, url)
        except Exception as e:  # noqa: BLE001
            print(f"[fail] 解析失败 {url}: {e}")
            bad += 1
            continue
        if len(md) < 400:
            print(f"[fail] 正文太短（{len(md)} 字符，可能是 JS 渲染页）：{url}")
            bad += 1
            continue
        if published:
            year = int(published[:4])
            if year < a.min_year:
                print(f"[stale] 拒收（{published} < {a.min_year}）：{url}")
                stale += 1
                continue
        # 年份分桶：显式 year > 识别到的发布日期 > 事件名里的 20xx > 默认
        year = str(it.get("year") or (published[:4] if published else "") or
                   (re.search(r"(20\d\d)", it.get("event", "")).group(1)
                    if re.search(r"(20\d\d)", it.get("event", "")) else a.default_year))
        outdir = ROOT / year / direction
        outdir.mkdir(parents=True, exist_ok=True)
        event = it.get("event", "unknown")
        name = f"{event}-{slugify(title, 50)}.md"
        path = outdir / name
        head = (f"<!-- 来源：{url} ｜ 抓取：{time.strftime('%Y-%m-%d %H:%M')} ｜ 发布：{published or 'unknown'} -->\n"
                f"# {title}\n\n"
                f"> 赛事：{event} ｜ 方向：{direction} ｜ 原文：{url}\n"
                f"> 发布日期：{published or '**未能从页面识别（unknown）**'} ｜ 抓取日期：{time.strftime('%Y-%m-%d')}\n"
                f"> 用途：本机离线查阅（`kb search --kind ctfwp`）。**版权归原作者**，不外发、不再分发。\n\n")
        path.write_text(head + md + "\n", encoding="utf-8")
        rec = {"url": url, "event": event, "dir": direction, "year": year, "title": title,
               "published": published or "unknown", "fetched_at": time.strftime("%Y-%m-%d %H:%M"),
               "chars": len(md), "file": str(path.relative_to(LAB))}
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        done[url] = rec
        print(f"[ok]   {direction:9s} {len(md):7d} 字符  {path.relative_to(ROOT)}")
        ok += 1
        time.sleep(a.sleep)
    if not a.dry_run:
        write_index()
        print(f"\n抓取完成：新增 {ok} · 跳过 {skip} · 拒收(≤{a.min_year-1}) {stale} · 失败 {bad}")
        print(f"索引目录：{ROOT}")
    return 0


def write_index() -> None:
    recs = list(load_log().values())
    recs.sort(key=lambda r: (r.get("year", "n/a"), r.get("dir", ""), r.get("event", ""), r.get("url", "")))
    bydir: dict[str, int] = {}
    for r in recs:
        key = f"{r.get('year','n/a')}/{r.get('dir','?')}"
        bydir[key] = bydir.get(key, 0) + 1
    lines = ["# CTF WP 仓库 · INDEX（按年份分桶）", "",
             f"> 共 **{len(recs)}** 篇 ｜ 生成 {time.strftime('%Y-%m-%d %H:%M')} ｜ 抓取脚本 `tools/wpfetch.py`",
             "> **用途**：本机离线查阅（`kb search \"<词>\" --kind ctfwp`）。来源与发布日期见每篇头部；",
             "> **版权归原作者**，不外发、不再分发。日期一栏 `unknown` = 页面里没识别到发布日期（没猜）。", "",
             "| 年份/方向 | 篇数 |", "|---|---|"]
    for d, n in sorted(bydir.items()):
        lines.append(f"| {d} | {n} |")
    lines += ["", "| 年 | 方向 | 赛事 | 标题 | 发布 | 原文 |", "|---|---|---|---|---|---|"]
    for r in recs:
        lines.append(f"| {r.get('year','')} | {r.get('dir','')} | {r.get('event','')} | {r.get('title','')[:60]} "
                     f"| {r.get('published','')} | {r.get('url','')} |")
    lines += ["", f"> 日期未确认的有 **{sum(1 for r in recs if r.get('published') == 'unknown')}** 篇。", ""]
    (ROOT / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")



def cmd_from_page(a) -> int:
    """从**列表页/分类页**里抽候选文章链接（很多站点没有可用 sitemap，但列表页里全是链接）。"""
    from urllib.parse import urljoin, urlparse
    for page in a.from_page:
        code, body = fetch(page, timeout=35)
        if code != 200 or not body:
            print(f"## {page} → 抓取失败 {code}")
            continue
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(body.decode("utf-8", "replace"), "lxml")
        host = urlparse(page).netloc
        seen, out = set(), []
        for a_el in soup.find_all("a", href=True):
            u = urljoin(page, a_el["href"])
            if urlparse(u).netloc != host or u.rstrip("/") == page.rstrip("/"):
                continue
            low = u.casefold()
            if any(k in low for k in ("/posts/", "/archives/", "/20", "/write", "/ctf", "/p/", "/article")):
                if u not in seen:
                    seen.add(u)
                    out.append((u, a_el.get_text(" ", strip=True)[:60]))
        print(f"## {page} → {len(out)} 条候选")
        for u, t in out[: a.limit]:
            print(f"   {u}\n      {t}")
    return 0

def cmd_selftest() -> int:
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(f"  {'✓' if cond else '✗'} {name}{(' — ' + str(detail)) if detail and not cond else ''}")
        ok &= bool(cond)

    print("== wpfetch --selftest ==")
    html = b"""<html><head><title>t</title>
      <meta property="article:published_time" content="2025-06-23T10:00:00Z"></head>
      <body><nav>NAVSHOULDVANISH</nav><article><h1>Title</h1>
      <p>para one</p><pre class="language-python">print(1)</pre>
      <ul><li>item</li></ul><script>var x=1;</script></article>
      <footer>FOOTERSHOULDVANISH</footer></body></html>"""
    md, title, published = html_to_md(html, "http://x")
    check("标题取自 <title>", title == "t", title)
    check("发布日期识别到 2025-06-23", published == "2025-06-23", published)
    check("正文含段落/代码块/列表", "para one" in md and "```python" in md and "item" in md)
    check("nav/script/footer 被剔除", "NAVSHOULDVANISH" not in md and "var x" not in md and "FOOTERSHOULDVANISH" not in md)
    check("slugify 处理中文与 URL", slugify("2025强网杯 Writeup https://a.com/b?c=1") .startswith("2025强网杯-Writeup"))
    # 日期红线：2024 的页面别被当 2025 收进来
    md2, _t2, p2 = html_to_md(b"<html><body><article><p>x</p><time>2024-11-05</time></article></body></html>", "http://y")
    check("旧日期能识别（用于拒收）", p2.startswith("2024-11"), p2)
    print("== selftest", "PASS" if ok else "FAIL", "==")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(prog="wpfetch", description="外部 WP → 本地 Markdown（带来源/日期头）+ 记账")
    ap.add_argument("--list", help="来源清单 JSON（含 sources: [{url,event,dir}]）")
    ap.add_argument("--only", help="只抓某个方向（web/pwn/crypto/...）")
    ap.add_argument("--min-year", type=int, default=2025, help="日期红线：识别到更早的发布日期就拒收（默认 2025）")
    ap.add_argument("--max-chars", type=int, default=400_000, help="单页上限（默认 400k 字符）")
    ap.add_argument("--sleep", type=float, default=0.6, help="每篇间隔秒（别打疼人家站点）")
    ap.add_argument("--default-year", default="2025",
                    help="清单里没写 year、页面也识别不到发布日期时，落到哪一年（默认 2025）")
    ap.add_argument("--force", action="store_true", help="重抓已抓过的 URL")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--index-only", action="store_true", help="只按记账重写 INDEX.md")
    ap.add_argument("--discover", nargs="+", metavar="BASE_URL",
                    help="从站点的 sitemap.xml/RSS 里捞 2025+ 且像 WP 的链接（只打印候选，不写盘）")
    ap.add_argument("--from-page", nargs="+", metavar="LISTING_URL",
                    help="从列表页/分类页抽候选文章链接（只打印）")
    ap.add_argument("--limit", type=int, default=40, help="--from-page 每条最多打印多少")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return cmd_selftest()
    if a.discover:
        return cmd_discover(a)
    if a.from_page:
        return cmd_from_page(a)
    if a.index_only:
        write_index()
        print(f"INDEX.md 已重写（{len(load_log())} 篇）")
        return 0
    if not a.list:
        ap.print_help()
        return 2
    return cmd_fetch(a)


if __name__ == "__main__":
    sys.exit(main())
