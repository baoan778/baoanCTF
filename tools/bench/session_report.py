#!/usr/bin/env python3
"""session_report.py —— 单题/全量会话体检：时间到底花在哪、卡在哪一步。

为什么需要它（2026-11-19 实战）：
  · 平台只给「对/错」，ledger 只有「跑了多少秒」——**没有任何东西告诉你为什么超时**。
  · 实测 27 道题各在一次失败上烧掉 ~190s（正好撞预算上限），但不知道是 ffuf 耗死、
    在重复同一个 payload、还是在跟一个还没起来的容器较劲。
  · 会话 JSONL（work/solve/<code>/*.jsonl）里其实全都有：assistant 消息带 durationMs
    （模型思考+生成耗时）与 usage，toolResult 里是命令与输出。拆开就能归因。

用法：
  python3 tools/bench/session_report.py xben-082-24          # 单题
  python3 tools/bench/session_report.py xben-082-24 --tools  # 逐条工具调用与耗时
  python3 tools/bench/session_report.py --all --top 15       # 全局：最贵的题
  python3 tools/bench/session_report.py --agg                # 全局：时间去向聚合
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOLVE = ROOT / "work" / "solve"


def load(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return rows


def pick(code: str) -> Path | None:
    d = SOLVE / code
    if not d.is_dir():
        return None
    fs = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_size, reverse=True)
    return fs[0] if fs else None


def _ts(r: dict) -> float | None:
    """时间戳兼容三种形态（pi 会话 JSONL 里三种都出现过）。

    · ISO 字符串（"2026-11-19T06:28:05.251Z"，会话头/消息行用它）
    · 毫秒整数（assistant.message.timestamp = 1795069686523）
    · 秒浮点
    早期版本只认数字 → 全部墙钟算成 0，聚合页显示 "模型生成 385780%"，被这一步卡住过。
    """
    v = r.get("timestamp")
    if v is None:
        m = r.get("message") or {}
        v = m.get("timestamp") if isinstance(m, dict) else None
    if isinstance(v, (int, float)):
        return float(v) / 1000.0 if v > 1e11 else float(v)
    if isinstance(v, str) and v:
        s = v.strip()
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            return datetime.fromisoformat(s).timestamp()
        except Exception:
            return None
    return None


def _content_text(m: dict) -> str:
    c = m.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        out = []
        for b in c:
            if isinstance(b, dict):
                if b.get("text"):
                    out.append(str(b["text"]))
                elif b.get("type") == "toolCall":
                    out.append(f"[tool {b.get('name')}] {json.dumps(b.get('arguments'), ensure_ascii=False)[:400]}")
        return " ".join(out)
    return ""


def parse_session(path: Path) -> dict:
    rows = load(path)
    model = None
    thinking = None
    events = []          # (ts, kind, text, meta)
    for r in rows:
        t = r.get("type")
        if t == "model_change":
            model = r.get("modelId")
        elif t == "thinking_level_change":
            thinking = r.get("thinkingLevel")
        elif t == "message":
            m = r.get("message") or {}
            role = m.get("role")
            ts = _ts(r)
            if role == "assistant":
                calls = [b for b in (m.get("content") or []) if isinstance(b, dict) and b.get("type") == "toolCall"]
                text = _content_text({"content": [b for b in (m.get("content") or []) if isinstance(b, dict) and b.get("type") == "text"]})
                for c in calls:
                    events.append({
                        "ts": ts, "kind": "tool", "name": c.get("name"),
                        "cmd": json.dumps(c.get("arguments"), ensure_ascii=False),
                        "gen_ms": m.get("durationMs"), "usage": m.get("usage"),
                        "text": text,
                    })
                if not calls:
                    events.append({"ts": ts, "kind": "assistant_text", "text": text[:400],
                                   "gen_ms": m.get("durationMs"), "usage": m.get("usage")})
            elif role == "toolResult":
                events.append({"ts": ts, "kind": "toolResult", "name": m.get("toolName"),
                               "text": _content_text(m)[:2000]})
    # 工具耗时 = 该 toolCall 到对应 toolResult 的间隔
    for i, e in enumerate(events):
        if e["kind"] != "tool":
            continue
        for j in range(i + 1, min(i + 4, len(events))):
            if events[j]["kind"] == "toolResult":
                if e.get("ts") and events[j].get("ts"):
                    e["tool_ms"] = round((events[j]["ts"] - e["ts"]) * 1000)
                e["result"] = events[j]["text"]
                break
    return {"model": model, "thinking": thinking, "events": events}


def analyze(code: str) -> dict:
    p = pick(code)
    if not p:
        return {"code": code, "error": "no session jsonl"}
    s = parse_session(p)
    ev = s["events"]
    if not ev:
        return {"code": code, "error": "no tool events"}
    t0 = next((e["ts"] for e in ev if e.get("ts")), None)
    tN = next((e["ts"] for e in reversed(ev) if e.get("ts")), None)
    wall = (tN - t0) if (t0 and tN) else 0.0

    tools = [e for e in ev if e["kind"] == "tool"]
    gen_ms = sum((e.get("gen_ms") or 0) for e in ev if e["kind"] in ("tool", "assistant_text"))
    tool_ms = sum((e.get("tool_ms") or 0) for e in tools)

    names: dict[str, int] = {}
    for t in tools:
        names[t["name"]] = names.get(t["name"], 0) + 1

    slow = sorted([t for t in tools if t.get("tool_ms")], key=lambda x: -x["tool_ms"])[:6]
    return {
        "code": code, "file": str(p), "model": s["model"], "thinking": s["thinking"],
        "wall_sec": round(wall, 1), "turns": len(tools),
        "gen_sec": round(gen_ms / 1000, 1), "tool_sec": round(tool_ms / 1000, 1),
        "tools": dict(sorted(names.items(), key=lambda x: -x[1])),
        "slow": [{"sec": round(x["tool_ms"] / 1000, 1), "cmd": (x.get("cmd") or "")[:220],
                  "out": (x.get("result") or "")[:160]} for x in slow],
        "last": [(t.get("cmd") or "")[:200] for t in tools[-3:]],
        "found_flag": any("FOUND_FLAG=" in (e.get("text") or "") for e in ev),
        "tools_detail": [{"name": t["name"], "ms": t.get("tool_ms"), "cmd": (t.get("cmd") or "")[:180]} for t in tools],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("code", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--agg", action="store_true")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--tools", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    codes = sorted(d.name for d in SOLVE.iterdir() if d.is_dir() and d.name.startswith("xben-")) if (a.all or a.agg) else []
    if a.agg:
        res = [analyze(c) for c in codes]
        res = [r for r in res if "error" not in r]
        tw = sum(r["wall_sec"] for r in res)
        tg = sum(r["gen_sec"] for r in res)
        tt = sum(r["tool_sec"] for r in res)
        print(f"会话 {len(res)} 个 · 墙钟合计 {tw:.0f}s · 模型生成 {tg:.0f}s ({tg/max(tw,1)*100:.0f}%) · 工具执行 {tt:.0f}s ({tt/max(tw,1)*100:.0f}%)")
        agg: dict[str, float] = {}
        cnt: dict[str, int] = {}
        for r in res:
            for t in r["tools_detail"]:
                agg[t["name"]] = agg.get(t["name"], 0) + (t["ms"] or 0) / 1000
                cnt[t["name"]] = cnt.get(t["name"], 0) + 1
        print("\n工具耗时排行（占总工具时长）：")
        for k, v in sorted(agg.items(), key=lambda x: -x[1])[:12]:
            print(f"  {k:<14} {v:>7.0f}s  {cnt[k]:>4} 次  平均 {v/max(cnt[k],1):>6.2f}s")
        print("\n最贵的题：")
        for r in sorted(res, key=lambda x: -x["wall_sec"])[: a.top]:
            print(f"  {r['code']:<14} {r['wall_sec']:>6.0f}s  轮{r['turns']:<3} 生成{r['gen_sec']:>5.0f}s 工具{r['tool_sec']:>5.0f}s "
                  f"{'FLAG' if r['found_flag'] else '     '} {list(r['tools'].items())[:3]}")
        return 0

    if a.all:
        res = [analyze(c) for c in codes]
        res = [r for r in res if "error" not in r]
        for r in sorted(res, key=lambda x: -x["wall_sec"])[: a.top]:
            print(f"{r['code']:<14} {r['wall_sec']:>6.0f}s 轮{r['turns']:<3} {'FLAG' if r['found_flag'] else ''}")
        return 0

    if not a.code:
        ap.print_help()
        return 2
    r = analyze(a.code)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    if "error" in r:
        print(f"{a.code}: {r['error']}")
        return 1
    print(f"== {r['code']} ==  模型 {r['model']} / think={r['thinking']}")
    print(f"墙钟 {r['wall_sec']}s · 轮次 {r['turns']} · 模型生成 {r['gen_sec']}s · 工具执行 {r['tool_sec']}s")
    print("工具分布：", ", ".join(f"{k}×{v}" for k, v in r["tools"].items()))
    print("最贵调用：")
    for s in r["slow"]:
        print(f"  {s['sec']:>6.1f}s  {s['cmd'][:140]}")
        if s["out"]:
            print(f"           → {s['out'][:120]}")
    print("最后动作：")
    for t in r["last"]:
        print("  " + t[:150])
    if a.tools:
        print("逐条：")
        for t in r["tools_detail"]:
            print(f"  {(t['ms'] or 0)/1000:>6.1f}s {t['name']:<10} {t['cmd'][:120]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
