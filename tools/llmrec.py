#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llmrec.py — LLM 调用级留痕（历史决策 #50 的 baoan 落法）

ARTEX 用 Recorder 装饰 llm.Provider，让 **HTTP transport** 捕获原始 wire 请求/响应体
（Recorder 自己看到的已经是 normalized 的），并用 WithWorker 把没有 session 的调用（拦截 judge）
单列计费。baoan 没有自有 LLM transport —— 模型调用发生在外部 pi 进程里，原始 wire 体在本机拿不到。
所以这里只做**拿得到的那一层**：把 pi 会话 JSONL 里的每条 assistant 调用摊成一行
（哪次调用 / 哪个 provider·model / 入出 token 与缓存 / 成本 / 这一轮带了多少字进、出了什么工具）。

诚实边界（写进 docs/并行工作.md §4.6u）：
  * 本工具不产出 wire 原始体，也不伪造（拿不到就是拿不到）；
  * usage 缺失的调用按「未知」计数，不当 0（与 #49 同一口径）；
  * 成本只在 provider 回传 cost 时才有值，其余是 null。

用法：
  llmrec --dir <会话目录>            # 汇总（调用数/入出 token/成本/按模型分组）
  llmrec --file <会话.jsonl> --json  # 逐次调用记录（JSON）
  llmrec --dir <目录> --out log/llmrec.jsonl   # 落盘，一条调用一行
  llmrec --dir <目录> --tail 20      # 只看最后 20 次调用
"""
import argparse
import json
import os
import sys
from pathlib import Path

# ★ 2026-11-17 修（selfcheck 全量模式抓到）：Windows 控制台默认 cp936，
#   下面的人读输出里有「…」「⚠」等字符 → print 直接 UnicodeEncodeError 崩，
#   重定向给测试时又变成 GBK 字节被 utf-8 解码成乱码（6 条断言假红）。
#   这是本仓所有 CLI 的统一约定（apiharvest / browser / ctfsurf 等同款），别省。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

USAGE_KEYS = ("input", "output", "cache_read", "cache_write")


def iter_objs(path):
    try:
        fh = open(path, "r", encoding="utf-8", errors="replace")
    except OSError:
        return
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue          # 半行 JSON（被 kill 时很常见）跳过，不炸整轮
            if isinstance(o, dict):
                yield o


def _text_chars(content):
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        n = 0
        for c in content:
            if isinstance(c, dict):
                if c.get("type") == "text":
                    n += len(c.get("text") or "")
                elif c.get("type") == "tool_result":
                    n += len(json.dumps(c, ensure_ascii=False))
            elif isinstance(c, str):
                n += len(c)
        return n
    return 0


def calls(session_path, label=""):
    """把一次会话摊成调用记录。返回 (meta, records)。"""
    meta = {"session": os.path.basename(session_path), "path": str(session_path), "label": label}
    recs = []
    pending_in = 0            # 自上次 assistant 之后累计的请求侧字符（近似「这一轮带了多少字」）
    model = provider = ""
    for o in iter_objs(session_path):
        t = o.get("type")
        if t == "model_change":
            provider = o.get("provider") or provider
            model = o.get("modelId") or model
            continue
        if t != "message":
            continue
        m = o.get("message") or {}
        role = m.get("role")
        if role == "assistant":
            tool_names = [c.get("name") for c in (m.get("content") or [])
                          if isinstance(c, dict) and c.get("type") == "tool_use"]
            u = m.get("usage") or {}
            recs.append({
                "n": len(recs) + 1,
                "session": meta["session"],
                "label": label or "main",
                "ts": o.get("timestamp") or m.get("timestamp"),
                "provider": m.get("provider") or provider,
                "model": m.get("model") or model,
                "api": m.get("api") or "",
                "usage": dict((k, u.get(k)) for k in USAGE_KEYS if k in u),
                "missing_usage": not bool(u),
                "cost": u.get("cost"),
                "in_chars": pending_in,
                "out_chars": _text_chars(m.get("content")),
                "tools": tool_names,
                "stop": m.get("stopReason") or m.get("stop_reason") or "",
            })
            pending_in = 0
        else:
            pending_in += _text_chars(m.get("content"))
    return meta, recs


def _g(rec, key):
    return (rec.get("usage") or {}).get(key)


def summarize(meta, recs):
    tot = dict((k, 0) for k in USAGE_KEYS)
    cost = 0.0
    have_cost = False
    missing = 0
    by_model = {}
    for r in recs:
        if r.get("missing_usage"):
            missing += 1
        for k in USAGE_KEYS:
            v = _g(r, k)
            if isinstance(v, (int, float)):
                tot[k] += int(v)
        c = r.get("cost")
        if isinstance(c, (int, float)):
            cost += float(c)
            have_cost = True
        key = r.get("model") or "?"
        b = by_model.setdefault(key, {"calls": 0, "input": 0, "output": 0, "cost": 0.0, "missing": 0})
        b["calls"] += 1
        b["input"] += int(_g(r, "input") or 0)
        b["output"] += int(_g(r, "output") or 0)
        if isinstance(c, (int, float)):
            b["cost"] += float(c)
        if r.get("missing_usage"):
            b["missing"] += 1
    return {"session": meta["session"], "calls": len(recs), "tokens": tot,
            "cost": (cost if have_cost else None), "missing_usage": missing,
            "by_model": by_model}


def _files(target):
    p = Path(target)
    if p.is_file():
        return [p]
    if p.is_dir():
        return sorted([x for x in p.iterdir() if x.suffix == ".jsonl" and x.is_file()])
    return []


def main(argv=None):
    ap = argparse.ArgumentParser(prog="llmrec", description="pi 会话 → 调用级留痕（#50）")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--dir", help="会话目录（只展开一层 .jsonl）")
    src.add_argument("--file", help="单个会话 JSONL")
    ap.add_argument("--out", help="把逐次调用写成 JSONL（一条调用一行）")
    ap.add_argument("--json", action="store_true", help="逐次调用打到 stdout（JSON 数组）")
    ap.add_argument("--tail", type=int, default=0, help="只看最后 N 次调用（0=全部）")
    ap.add_argument("--label", default="", help="给这批调用打标签（如 judge / lane-b），记录里单列")
    a = ap.parse_args(argv)

    files = _files(a.dir or a.file)
    if not files:
        sys.stderr.write("没有找到 .jsonl：%s\n" % (a.dir or a.file))
        return 2

    all_recs, sums = [], []
    for f in files:
        meta, recs = calls(f, label=a.label)
        if not recs:
            continue
        if a.tail and len(recs) > a.tail:
            recs = recs[-a.tail:]
            for i, r in enumerate(recs, 1):
                r["n"] = i
        all_recs.extend(recs)
        sums.append(summarize(meta, recs))

    if not all_recs:
        sys.stderr.write("这些 JSONL 里没有 assistant 调用记录（空会话？）\n")
        return 2

    if a.out:
        outp = Path(a.out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        tmp = outp.with_name(outp.name + ".staging")
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            for r in all_recs:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, outp)

    if a.json:
        sys.stdout.write(json.dumps(all_recs, ensure_ascii=False, indent=2) + "\n")
        return 0

    print("调用级留痕（transcript 层；wire 原始体本机拿不到，见 docs/并行工作.md §4.6u）")
    for s in sums:
        print("  %-60s 调用 %d" % (s["session"][:60], s["calls"]))
    print("")
    print("合计：%d 次调用 · 输入 %d / 输出 %d token · 缓存读 %d / 写 %d"
          % (len(all_recs), sum(s["tokens"]["input"] for s in sums),
             sum(s["tokens"]["output"] for s in sums),
             sum(s["tokens"]["cache_read"] for s in sums),
             sum(s["tokens"]["cache_write"] for s in sums)))
    tcost = sum(s["cost"] or 0.0 for s in sums)
    any_cost = any(s["cost"] is not None for s in sums)
    print("成本：%s" % (("%.4f" % tcost) if any_cost else "未知（provider 没回传 cost / 这些消息没有 usage）"))
    miss = sum(s["missing_usage"] for s in sums)
    if miss:
        print("⚠ %d 次调用没有 usage 字段（按未知计入，**不是真的 0**）" % miss)
    models = {}
    for s in sums:
        for k, v in s["by_model"].items():
            b = models.setdefault(k, {"calls": 0, "input": 0, "output": 0})
            b["calls"] += v["calls"]
            b["input"] += v["input"]
            b["output"] += v["output"]
    if models:
        print("")
        for k in sorted(models):
            b = models[k]
            print("  %-32s %4d 次 · 入 %8d / 出 %7d" % (k[:32], b["calls"], b["input"], b["output"]))
    if a.out:
        print("")
        print("逐次调用已落盘：%s（%d 行）" % (a.out, len(all_recs)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
