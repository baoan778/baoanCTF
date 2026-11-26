#!/usr/bin/env python3
"""EDUSRC 公开情报索引 —— 「打哪个学校、打哪个厂商」用数据说话，不靠猜。

为什么要它（2026-10-04，操作者要「挖几个新出的 edu 证书」）：
  · EDUSRC 的**漏洞详情页 / 单位页要登录**，但**两张公开表是全开的**：
      1. `/list/`            —— 已确认漏洞流水：时间 | 单位(匿名"某省某高校") | 等级 | 作者（约 2.4 万页）
      2. `/rank/company/`    —— 教育软件**开发商榜**：排名 | 开发商 | 修复率（17 页 ≈ 850 家）
  · 由此可得的**客观判据**：
      - 等级分布 = "现在什么级别能被确认"的基准（避免拿"低危被告知不算"的幻想去刷）
      - 产出节奏（每天确认多少条）= 竞争烈度，估自己的预期
      - 开发商榜高修复率 = **产品确实有洞且被确认过**，是「选哪个系统」的最好线索
  · 这比"随便挑一所学校碰运气"高一个量级——**先看数据，再选题**。

只读、限速、只访问平台公开页（站内 1 rps）。任何登录墙后的页面一律不碰。

用法：
    py tools/edu_index.py fetch                 # 拉最新流水 3 页 + 厂商榜全榜 → 缓存
    py tools/edu_index.py fetch --list-pages 10 --vendor-pages 17
    py tools/edu_index.py show                  # 等级分布 / 日均产出 / 时间跨度
    py tools/edu_index.py vendors --top 40      # 厂商榜 + 每条给出本地 PoC 查询命令
    py tools/edu_index.py selftest              # 离线（内嵌 fixture）验证解析，不发请求
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time
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

BASE = "https://src.sjtu.edu.cn"
UA = "Mozilla/5.0 (X11; Linux x86_64) pi-lab-edu-index/1.0 (+read-only public pages)"
OUT_DEFAULT = pathlib.Path("work/edu")
SEV_ORDER = ["严重", "高危", "中危", "低危", "无"]

# 厂商名 → 检索词：去掉地名/公司后缀，留下品牌词（否则拿整串去 poc 查命中为 0）
_GEO = ("北京", "上海", "广州", "深圳", "杭州", "长沙", "无锡", "四川", "浙江", "衡水",
        "南京", "武汉", "成都", "西安", "重庆", "天津", "苏州", "厦门", "福州", "合肥",
        "郑州", "济南", "青岛", "大连", "沈阳", "哈尔滨", "昆明", "贵阳", "南宁", "南昌",
        "中国", "国家", "湖南", "湖北", "河南", "河北", "山东", "山西", "陕西", "甘肃")
_SUFFIX = ("股份有限公司", "有限责任公司", "有限公司", "信息科技", "信息技术", "科技", "信息技术服务",
           "技术服务", "技术", "信息", "网络", "软件", "电子", "智能", "科技集团", "集团", "公司")


def brand(vendor: str) -> str:
    """从开发商全名抽品牌词（用于 poc 查询）：'浙江齐治科技股份有限公司' → '齐治'"""
    v = re.sub(r"[（(].*?[)）]", "", vendor).strip()
    changed = True
    while changed:                      # 地名/后缀可能叠加，反复剥
        changed = False
        for g in _GEO:
            if v.startswith(g) and len(v) - len(g) >= 2:
                v = v[len(g):]
                changed = True
                break
        for s in _SUFFIX:
            if v.endswith(s) and len(v) - len(s) >= 2:
                v = v[: -len(s)]
                changed = True
                break
    return re.sub(r"^[省市]", "", v)[:8] or vendor[:6]


# ────────────────────────── 解析（纯函数，可离线测） ──────────────────────────

def _cells(row_html: str) -> list:
    return [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip()
            for c in re.findall(r"<t[dh].*?</t[dh]>", row_html, flags=re.S)]


def parse_list(html: str) -> tuple:
    """解析 /list/ → (rows, total_pages)。rows: [{time, unit, severity, author}]"""
    body = re.sub(r"<script.*?</script>", "", html, flags=re.S)
    rows = []
    for r in re.findall(r"<tr.*?</tr>", body, flags=re.S):
        c = _cells(r)
        if len(c) < 4 or c[0] in ("时间", "") or not re.match(r"\d{4}-\d{2}-\d{2}", c[0]):
            continue
        rows.append({"time": c[0], "unit": c[1], "severity": c[2], "author": c[3]})
    pages = [int(m) for m in re.findall(r"\?page=(\d+)", html)] or [1]
    return rows, max(pages)


def parse_vendors(html: str) -> tuple:
    """解析 /rank/company/ → (rows, total_pages)。rows: [{rank, vendor, fix_rate}]"""
    body = re.sub(r"<script.*?</script>", "", html, flags=re.S)
    rows = []
    for r in re.findall(r"<tr.*?</tr>", body, flags=re.S):
        c = _cells(r)
        if len(c) < 3 or not c[0].isdigit():
            continue
        rows.append({"rank": int(c[0]), "vendor": c[1], "fix_rate": c[2]})
    pages = [int(m) for m in re.findall(r"\?page=(\d+)", html)] or [1]
    return rows, max(pages)


# ────────────────────────── 抓取（限速，公开页 only） ──────────────────────────

def get(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def fetch(kind: str, pages: int, out: pathlib.Path, sleep_s: float = 1.0) -> dict:
    # 合并式写入：分两次抓 list / vendors 时不会互相覆盖
    p = out / "edusrc-index.json"
    cache = {}
    if p.exists():
        try:
            cache = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    cache["fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    cache["base"] = BASE
    if kind in ("list", "all"):
        rows, total = [], 1
        for p in range(1, pages + 1):
            try:
                h = get(f"{BASE}/list/?page={p}")
            except Exception as e:
                print(f"  ⚠ list p{p} 取数失败：{type(e).__name__}", file=sys.stderr)
                break
            rs, total = parse_list(h)
            if not rs:
                break
            rows += rs
            if p < pages:
                time.sleep(sleep_s)
        cache["list"] = {"rows": rows, "total_pages": total}
    if kind in ("vendors", "all"):
        rows, total = [], 1
        for p in range(1, pages + 1):
            try:
                h = get(f"{BASE}/rank/company/?page={p}")
            except Exception as e:
                print(f"  ⚠ vendors p{p} 取数失败：{type(e).__name__}", file=sys.stderr)
                break
            rs, total = parse_vendors(h)
            if not rs:
                break
            rows += rs
            if p < pages:
                time.sleep(sleep_s)
        cache["vendors"] = {"rows": rows, "total_pages": total}
    out.mkdir(parents=True, exist_ok=True)
    (out / "edusrc-index.json").write_text(
        json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return cache


def load(out: pathlib.Path) -> dict:
    p = out / "edusrc-index.json"
    if not p.exists():
        print(f"还没有缓存 —— 先跑：py tools/edu_index.py fetch（看的是 {p}）", file=sys.stderr)
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


# ────────────────────────── 视图 ──────────────────────────

def show(cache: dict) -> int:
    d = cache.get("list")
    if not d or not d["rows"]:
        print("（没有 list 数据）")
        return 1
    rows = d["rows"]
    print(f"=== EDUSRC 已确认漏洞流水（本样本 {len(rows)} 条 = 公开只有**最新 1 页**；"
          f"分页链接到 {d['total_pages']} 页 ≈ {d['total_pages'] * 10} 条历史，翻页需登录）")
    dist = {}
    for r in rows:
        dist[r["severity"]] = dist.get(r["severity"], 0) + 1
    tot = sum(dist.values())
    print("等级分布：" + "  ".join(f"{s} {dist.get(s, 0)}（{dist.get(s, 0) * 100 // tot}%）"
                                   for s in SEV_ORDER if dist.get(s)))
    days = sorted({r["time"] for r in rows})
    if days:
        print(f"时间跨度：{days[0]} → {days[-1]}（{len(days)} 天）")
        per_day = {}
        for r in rows:
            per_day[r["time"]] = per_day.get(r["time"], 0) + 1
        avg = sum(per_day.values()) / len(per_day)
        print(f"确认节奏：平均 {avg:.1f} 条/天（平台口径，全行业）")
    authors = {}
    for r in rows:
        authors[r["author"]] = authors.get(r["author"], 0) + 1
    top = sorted(authors.items(), key=lambda x: -x[1])[:8]
    print("活跃提交者：" + "  ".join(f"{a}({n})" for a, n in top))
    return 0


def vendors(cache: dict, top: int) -> int:
    d = cache.get("vendors")
    if not d or not d["rows"]:
        print("（没有 vendors 数据）")
        return 1
    rows = d["rows"][:top]
    print(f"=== 教育软件开发商榜（前 {len(rows)} / 共抓到 {len(d['rows'])} 家；"
          f"分页链接到 {d['total_pages']} 页，实际数据到第 {max(1, (len(d['rows']) + 49) // 50)} 页——后面是空页）")
    for r in rows:
        key = brand(r["vendor"])
        print(f"  #{r['rank']:<4} {r['vendor'][:34]:<36} 修复率 {r['fix_rate']:<8} "
              f"→ 查 PoC: poc \"{key}\"")
    print("\n提示：厂商名 → 产品 → 本地 PoC（poc \"<厂商>\" --usable）→ 目标站点指纹匹配 → 验证")
    return 0


# ────────────────────────── selftest（离线） ──────────────────────────

LIST_FIXTURE = """
<table><tr><th>时间</th><th>标题</th><th>等级</th><th>作者</th></tr>
<tr><td>2026-10-04</td><td>安徽省某高校</td><td>中危</td><td>1ux4ry</td></tr>
<tr><td>2026-10-04</td><td>江苏省某高校</td><td>高危</td><td>Caption</td></tr>
<tr><td>2026-10-03</td><td>广东省某高校</td><td>低危</td><td>End</td></tr>
<tr><td colspan="4">没有数据</td></tr></table>
<a href="?page=1">1</a><a href="?page=23920">末页</a>
"""

VENDOR_FIXTURE = """
<table><tr><th>#</th><th>开发商</th><th>修复率</th></tr>
<tr><td>1</td><td>北京瑞格心灵（奥泰瑞格）科技有限公司</td><td>100%</td></tr>
<tr><td>101</td><td>杭州安恒信息技术股份有限公司</td><td>90.20%</td></tr>
</table>
<a href="?page=17">末页</a>
"""


def selftest() -> int:
    checks = []

    def ok(n, c, extra=""):
        checks.append((n, bool(c), extra))

    rows, total = parse_list(LIST_FIXTURE)
    ok("list 解析 3 行（表头/无日期行被跳过）", len(rows) == 3, rows)
    ok("list 总页数解析", total == 23920, total)
    ok("list 字段完整", rows[0] == {"time": "2026-10-04", "unit": "安徽省某高校",
                                    "severity": "中危", "author": "1ux4ry"}, rows[0])

    vr, vtotal = parse_vendors(VENDOR_FIXTURE)
    ok("vendor 解析 2 行", len(vr) == 2, vr)
    ok("vendor 修复率保留原文", vr[1]["fix_rate"] == "90.20%", vr[1])
    ok("vendor 总页数", vtotal == 17, vtotal)
    ok("无关 HTML 不误报", parse_list("<html><body>hi</body></html>")[0] == [])
    ok("品牌词：去地名+后缀", brand("浙江齐治科技股份有限公司") == "齐治", brand("浙江齐治科技股份有限公司"))
    ok("品牌词：保留品牌", brand("北京瑞格心灵（奥泰瑞格）科技有限公司") == "瑞格心灵",
       brand("北京瑞格心灵（奥泰瑞格）科技有限公司"))
    ok("品牌词：短名不误剥", brand("安恒") == "安恒", brand("安恒"))

    # show/vendors 在只有 fixture 数据时可用（缓存路径换到临时目录）
    import tempfile
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="eduindex-"))
    cache = {"fetched_at": "t", "list": {"rows": rows, "total_pages": total},
             "vendors": {"rows": vr, "total_pages": vtotal}}
    (tmp / "edusrc-index.json").write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    loaded = load(tmp)
    ok("load 能读回缓存", bool(loaded.get("list")))
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc_show = show(loaded)
        rc_vend = vendors(loaded, 5)
    out = buf.getvalue()
    ok("show 退出码 0", rc_show == 0)
    ok("show 打出等级分布", "中危 1" in out and "高危 1" in out, out[:200])
    ok("vendors 退出码 0", rc_vend == 0)
    ok("vendors 给出 poc 查询命令", 'poc "' in out)

    passed = sum(1 for _, c, _ in checks if c)
    for n, c, e in checks:
        print(f"  {'✓' if c else '✗'} {n}{('  ' + str(e)) if e and not c else ''}")
    print(f"\n{passed}/{len(checks)} 断言通过")
    return 0 if passed == len(checks) else 1


def main(argv) -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("cmd", nargs="?", default="show",
                    choices=["fetch", "show", "vendors", "selftest"])
    ap.add_argument("--list-pages", type=int, default=1,
                    help="抓 /list/ 多少页（★ 平台公开只给第 1 页=15 条，2 页起是空壳）")
    ap.add_argument("--vendor-pages", type=int, default=12,
                    help="/rank/company/ 多少页（1 页 50 家；实际到第 11 页，之后为空页）")
    ap.add_argument("--top", type=int, default=40, help="vendors 打印前几家")
    ap.add_argument("--out", default=str(OUT_DEFAULT), help="缓存目录")
    ap.add_argument("--sleep", type=float, default=1.0, help="页间间隔（秒），别把平台打出问题")
    args = ap.parse_args(argv)

    if args.cmd == "selftest":
        return selftest()
    out = pathlib.Path(args.out)
    if args.cmd == "fetch":
        fetch("list", args.list_pages, out, args.sleep)
        fetch("vendors", args.vendor_pages, out, args.sleep)
        c = load(out)
        print(f"已缓存 → {out / 'edusrc-index.json'}"
              f"（list {len(c.get('list', {}).get('rows', []))} 条 / "
              f"vendors {len(c.get('vendors', {}).get('rows', []))} 家）")
        return show(c)
    c = load(out)
    if not c:
        return 1
    return show(c) if args.cmd == "show" else vendors(c, args.top)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
