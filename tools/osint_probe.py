#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""osint_probe.py —— OSINT 图源/档案源的**连通性探针**（把「本机不可达」变成可复核判据）

## 为什么要有它（2026-11-07，决策：做 D 项）

docs/状态快照.md 0.5 的 D 项写着：

  「OSINT/图寻 1/3（33%）全表最弱；imgintel 只固化了元数据/OCR/坐标，
    语义识别仍需人给一句话。沉淀一页：Bing 图搜可用 / Wayback 本机不可达」。

**两处过期了**（2026-11-07 实测）：
  - Wayback **现在可达**，且 /wayback/available API 真返回快照（不用人工点网页）
  - Bing 图搜返回里能直接捞到 murl（原图直链），可程序化提取

所以「本机不可达」不该写成结论 —— 网络会变。**能复核的结论才留得下来**：
本工具把每个源跑一遍，给 状态码 / 是否真拿到内容 / 证据片段，
并支持 --json 落盘，隔一段时间重跑就能看出谁坏了、谁恢复了。

## 用法

    python3 tools/osint_probe.py                # 全跑一遍
    python3 tools/osint_probe.py --json x.json  # 落盘（趋势可追）
    python3 tools/osint_probe.py --only bing wayback
    python3 tools/osint_probe.py --selftest     # 不联网，验提取逻辑

## 判据（不猜）

  可达   = HTTP 2xx/3xx **且** 关键内容特征命中（不是只看状态码）
  半通   = 状态码 ok 但内容特征没命中（多半被风控/跳转/需要登录）
  不可达 = 连不上 / 4xx / 5xx
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def fetch(url: str, timeout: int = 20) -> tuple[int, str]:
    """GET 一个 URL，返回 (status, body)。出错返回 (0, 错误文本)，不抛。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return 0, str(e)[:200]


# ── 每个源：url + 内容特征判断 + 证据提取 ──

def probe_bing() -> dict:
    """Bing 图搜：能拿到 murl（原图直链）才算真可用。"""
    st, body = fetch("https://www.bing.com/images/search?q=ctf")
    urls = re.findall(r'murl&quot;:&quot;([^&]+)&quot;', body)
    if not urls:
        urls = re.findall(r'"murl":"([^"]+)"', body)
    return {"name": "Bing 图搜", "status": st,
            "ok": st in (200, 301, 302) and bool(urls),
            "evidence": "murl=%d 例 %s" % (len(urls), urls[:1])}


def probe_wayback() -> dict:
    """Wayback availability API：能返回 archived_snapshots.closest 才算真可用。"""
    u = "https://archive.org/wayback/available?url=example.com"
    st, body = fetch(u)
    ok = st == 200
    ev = ""
    if ok:
        try:
            d = json.loads(body)
            c = (d.get("archived_snapshots") or {}).get("closest") or {}
            ok = bool(c.get("available"))
            ev = "closest=%s" % (c.get("timestamp", "?"))
        except Exception as e:
            ok, ev = False, "JSON 解析失败：%s" % str(e)[:80]
    return {"name": "Wayback 快照 API", "status": st, "ok": ok, "evidence": ev}


def probe_yandex() -> dict:
    st, body = fetch("https://yandex.com/images/")
    return {"name": "Yandex 图搜", "status": st,
            "ok": st in (200, 302) and len(body) > 1000,
            "evidence": "body=%dB" % len(body)}


def probe_saucenao() -> dict:
    st, body = fetch("https://saucenao.com/")
    return {"name": "SauceNAO（二次元图搜）", "status": st,
            "ok": st == 200 and "saucenao" in body.lower(),
            "evidence": "body=%dB" % len(body)}


def probe_tineye() -> dict:
    st, body = fetch("https://tineye.com/")
    return {"name": "TinEye（反向图搜）", "status": st,
            "ok": st == 200 and len(body) > 1000,
            "evidence": "body=%dB" % len(body)}


PROBES = {
    "bing": probe_bing,
    "wayback": probe_wayback,
    "yandex": probe_yandex,
    "saucenao": probe_saucenao,
    "tineye": probe_tineye,
}


def verdict(r: dict) -> str:
    if r["ok"]:
        return "可达"
    if r["status"] and r["status"] < 400:
        return "半通"
    return "不可达"


def cmd_run(only: list[str] | None, json_out: str, timeout: int) -> int:
    names = only or list(PROBES)
    rows = []
    print("── OSINT 源连通性（判据：状态码 + 内容特征，不只看状态码）──")
    for n in names:
        fn = PROBES.get(n)
        if not fn:
            print("  跳过未知源：%s" % n)
            continue
        try:
            r = fn()
        except Exception as e:
            r = {"name": n, "status": 0, "ok": False, "evidence": str(e)[:120]}
        r["verdict"] = verdict(r)
        rows.append(r)
        print("  %-24s %-6s HTTP=%-4s %s"
              % (r["name"], r["verdict"], r["status"], r["evidence"][:60]))
    n_ok = sum(1 for r in rows if r["ok"])
    print("")
    print("%d/%d 可达" % (n_ok, len(rows)))
    if json_out:
        import pathlib
        pathlib.Path(json_out).write_text(
            json.dumps({"rows": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print("→ %s" % json_out)
    return 0 if n_ok else 1


def cmd_selftest() -> int:
    """不联网：验证 murl 提取 + verdict 分级没有写反。"""
    print("── osint_probe 自检（不联网）──")
    fails = [0]

    def ck(name, cond):
        print("  %s %s" % ("✓" if cond else "✗", name))
        if not cond:
            fails[0] += 1

    # murl 提取：三种真实出现过的形态都要捞到
    cases = [
        ('a murl&quot;:&quot;https://x/1.jpg&quot; b', 1),
        ('"murl":"https://x/2.jpg"', 1),
        ("no link here", 0),
    ]
    for body, want in cases:
        got = re.findall(r'murl&quot;:&quot;([^&]+)&quot;', body)
        if not got:
            got = re.findall(r'"murl":"([^"]+)"', body)
        ck("murl 提取（期望 %d 例）" % want, len(got) == want)

    # verdict 不能写反
    ck("ok=True → 可达", verdict({"ok": True, "status": 200}) == "可达")
    ck("ok=False + 2xx → 半通", verdict({"ok": False, "status": 200}) == "半通")
    ck("ok=False + 连不上 → 不可达", verdict({"ok": False, "status": 0}) == "不可达")
    ck("ok=False + 404 → 不可达", verdict({"ok": False, "status": 404}) == "不可达")

    print("")
    if fails[0]:
        print("❌ %d 项失败" % fails[0])
        return 1
    print("✅ 全部通过")
    return 0


def main(argv) -> int:
    ap = argparse.ArgumentParser(description="OSINT 图源/档案源连通性探针")
    ap.add_argument("--only", nargs="*", default=None, help="只跑这些源")
    ap.add_argument("--json", default="", help="结果落盘路径")
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return cmd_selftest()
    return cmd_run(a.only, a.json, a.timeout)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
