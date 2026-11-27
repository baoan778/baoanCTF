# -*- coding: utf-8 -*-
"""该轮 该轮修复：bench 指标的唯一出口 —— 统一口径。

为什么必须存在：历史报告同时出现过三个"总耗时"，且互不可比：
  A. 账本 total_sec 累加 = 22989 s  -> 那是「槽位·秒」的积分，不是时长
  B. 调用级耗时求和      = 25921 s  -> 含并发重叠，与 A 不可比
  C. 墙钟                = 11567 s  -> 唯一正确的"到底跑了多久"
再加一个坑：会话文件名里的时间戳是 UTC，而文件 mtime 是本地 +08:00，
直接相减会得出 11.21h / parallelism 0.57x 的错误结论（该轮 已踩过一次）。

本模块的规矩：
  1) 任何"时长"都标注口径名，不写裸数字；
  2) 成本一律 is_estimate=True，caveat 写明是本地费率表算的，不是账单；
  3) 分母只允许用 wall_clock_s。
"""
from __future__ import annotations

import datetime
import glob
import json
import os
import re
from typing import Any

LOCAL_TZ = datetime.timezone(datetime.timedelta(hours=8))
SESSION_TS_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})")

CAVEAT_COST = "local rate table estimate (usage.cost.totalFromProvider=false), NOT a provider bill"

# 一次会话的墙钟跨度上限（秒）。超过就认为 mtime 被"后续追加写"污染，
# 而不是这次会话真的跑了 2.3 天。实测踩到：试点跑往 11-20 的老会话里追加内容，
# mtime 跳到 11-22，11567s 的基线墙钟被算成 200373s（虚增 17 倍）。
# 取 6 小时：单题最长实测 2092s（xben-066），留足余量仍能捕获真实长跑。
MAX_SESSION_SPAN_S = 6 * 3600


def session_name_to_local(basename: str):
    """会话文件名里的时间戳是 UTC，转成本地 +08:00 再参与任何减法。

    这是本模块存在的第一个理由：不做这一步就会把 3.2h 算成 11.2h。
    """
    m = SESSION_TS_RE.search(os.path.basename(basename))
    if not m:
        return None
    y, mo, d, h, mi, s = (int(x) for x in m.groups())
    try:
        utc = datetime.datetime(y, mo, d, h, mi, s, tzinfo=datetime.timezone.utc)
    except ValueError:
        return None
    return utc.astimezone(LOCAL_TZ)


def _read_usage(path: str):
    """从单个会话 jsonl 汇总 (tokens, cost)。坏行跳过，不抛。"""
    tok = 0
    cost = 0.0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                msg = rec.get("message") if isinstance(rec, dict) else None
                if not isinstance(msg, dict):
                    continue
                u = msg.get("usage")
                if not isinstance(u, dict):
                    continue
                try:
                    tok += int(u.get("totalTokens", 0) or 0)
                except (TypeError, ValueError):
                    pass
                c = u.get("cost")
                if isinstance(c, dict):
                    try:
                        cost += float(c.get("total", 0) or 0)
                    except (TypeError, ValueError):
                        pass
    except OSError:
        return 0, 0.0
    return tok, round(cost, 6)


def usage_of_dir(sessdir: str) -> dict:
    """汇总**一个会话目录**（评测台/单题）的 tokens 与成本估算。

    与 collect() 的区别：collect 要的是"整场基线"（含墙钟/并行度），
    这里只回答"这一臂花了多少"。成本永远是估算，带上同一个 caveat。
    """
    tok = 0
    cost = 0.0
    files = sorted(glob.glob(os.path.join(sessdir, "*.jsonl")))
    for p in files:
        t, c = _read_usage(p)
        tok += t
        cost += c
    return {
        "sessions": len(files),
        "tokens": tok,
        "cost_est_usd": round(cost, 6),
        "cost_is_estimate": True,
        "cost_caveat": CAVEAT_COST,
    }


def collect(solve_dir: str, only=None) -> dict:
    """扫 solve_dir 下所有会话，算出带口径标注的指标包。

    only: 可选的**显式会话路径列表**。给了就只统计这些文件。
    为什么需要它（该轮）：基线数字（106 会话 / 11567s / 1.99x）是
    「2026-11-20 那次基准跑」的**历史事实**；一旦目录里多了任何一次新跑，
    对**活目录**断言这些数字必然失败。测试应当锁定历史时间窗，
    而不是逼着常数跟着目录漂移 —— 那是测试写错了，不是代码坏了。
    """
    if only is not None:
        sessions = sorted(only)
    else:
        sessions = sorted(glob.glob(os.path.join(solve_dir, "*", "*.jsonl")))
    if not sessions:
        return {"ok": False, "error": "no session jsonl found", "solve_dir": solve_dir}

    starts = []
    ends = []
    total_tokens = 0
    total_cost = 0.0
    by_session = []

    for path in sessions:
        st = session_name_to_local(path)
        try:
            mt = datetime.datetime.fromtimestamp(os.path.getmtime(path), LOCAL_TZ)
        except OSError:
            continue
        # ★ 该轮 修正（实测踩到）：mtime 会被"后续追加写"改掉。
        #   昨天试点跑往 11-20 的老会话里**追加**了内容，mtime 就跳到 11-22，
        #   于是 11567s 的墙钟被算成 200373s（虚增 17 倍）。
        #   墙钟的语义是"这次会话跑了多久"，上限只能到会话**开始后**的合理区间；
        #   mtime 早于起始时间（时钟回拨/异常）时才用 st 兜底。
        #   这里把 end 钳在 [st, mtime]，且当 mtime 超出 st 合理跨度过大时按 st 计
        #   —— 宁可少算一次跨度，也不让一次追加写把整场基线搞坏。
        if st is not None and mt < st:
            mt = st
        # 追加写会把 mtime 推到很晚；跨度超过 MAX_SPAN_S 视为非本次会话的写入（如昨天的试点跑往老文件尾部追加），
        # 此时 end 退化为 start，避免单文件虚增 17 倍墙钟。
        if st is not None and mt is not None and (mt - st).total_seconds() > MAX_SESSION_SPAN_S:
            mt = st
        if st is not None:
            starts.append(st)
        else:
            st = mt
        ends.append(mt)
        tok, cost = _read_usage(path)
        total_tokens += tok
        total_cost += cost
        by_session.append({
            "challenge": os.path.basename(os.path.dirname(path)),
            "session": os.path.basename(path),
            "start_local": st.isoformat(),
            "end_local": mt.isoformat(),
            "span_s": round(max(0.0, (mt - st).total_seconds()), 1),
            "tokens": tok,
            "cost_est_usd": cost,
        })

    first = min(starts) if starts else min(ends)
    last = max(ends)
    wall_clock_s = round(max(0.0, (last - first).total_seconds()), 1)

    slot_seconds = 0.0
    ledger_path = os.path.join(solve_dir, "ledger.json")
    ledger_note = None
    # ★★ 2026-11-23 实测修（活体：本轮跑 6 道题后 F4/F5/F6 三连红）：
    #   collect() 原来「会话按 only 过滤、槽位秒却读整份 ledger」—— 口径自相矛盾。
    #   后果：只要目录里多跑一道题，历史基线断言的槽位秒/并行度就跟着涨（实测
    #   22989→23386、1.99→2.02），而成本 F6 同一时间涨到 5.14（那是**真涨**，
    #   因为真跑了活会话）。无法区分「代码回退」与「多跑了几道题」= 基线失效。
    #   修法：给了 only（时间窗）就只累加**这些会话对应题号**的 ledger 条目；
    #   没给 only（活目录口径）保持老行为。
    _only_codes = None
    if only is not None:
        _only_codes = {os.path.basename(os.path.dirname(str(p))) for p in only}
    if os.path.isfile(ledger_path):
        try:
            with open(ledger_path, "r", encoding="utf-8") as fh:
                lg = json.load(fh)
            for code, rec in lg.items():
                if not isinstance(rec, dict):
                    continue
                if _only_codes is not None and code not in _only_codes:
                    continue
                try:
                    slot_seconds += float(rec.get("total_sec", 0) or 0)
                except (TypeError, ValueError):
                    pass
            ledger_note = ("ledger.json present; slot_seconds = sum(total_sec)"
                           + ("（仅限 only 里的题号）" if _only_codes is not None else ""))
        except (OSError, json.JSONDecodeError) as exc:
            ledger_note = "ledger.json unreadable: " + str(exc)
    else:
        ledger_note = "no ledger.json"

    parallelism = round(slot_seconds / wall_clock_s, 2) if wall_clock_s > 0 else None
    spans = sorted(s["span_s"] for s in by_session)
    median_span = spans[len(spans) // 2] if spans else None

    return {
        "ok": True,
        "solve_dir": solve_dir,
        "session_count": len(sessions),
        "wall_clock_s": wall_clock_s,
        "wall_clock_h": round(wall_clock_s / 3600.0, 2),
        "slot_seconds": round(slot_seconds, 1),
        "slot_seconds_note": ledger_note,
        "parallelism_x": parallelism,
        "median_session_span_s": median_span,
        "median_session_span_h": round(median_span / 3600.0, 2) if median_span else None,
        "first_session_local": first.isoformat(),
        "last_write_local": last.isoformat(),
        "total_tokens": total_tokens,
        "cost_est_usd": round(total_cost, 4),
        "cost_is_estimate": True,
        "cost_caveat": CAVEAT_COST,
        "denominator_rule": "use wall_clock_s ONLY for speed claims",
        "by_session": by_session,
    }


def render_text(m: dict) -> str:
    if not m.get("ok"):
        return "bench_metrics: " + str(m.get("error"))
    lines = [
        "bench_metrics (该轮 该轮修复, unified accounting)",
        "  sessions            : " + str(m["session_count"]),
        "  wall clock          : " + str(m["wall_clock_s"]) + " s = " + str(m["wall_clock_h"]) + " h   [CORRECT denominator]",
        "  slot seconds        : " + str(m["slot_seconds"]) + " s   (" + str(m["slot_seconds_note"]) + ")",
        "  parallelism         : " + str(m["parallelism_x"]) + "x",
        "  median session span : " + str(m["median_session_span_s"]) + " s = " + str(m["median_session_span_h"]) + " h",
        "  window              : " + str(m["first_session_local"]) + " -> " + str(m["last_write_local"]) + " (local +08:00)",
        "  tokens              : " + str(m["total_tokens"]),
        "  cost (ESTIMATE)     : " + "$" + str(m["cost_est_usd"]),
        "  cost caveat         : " + str(m["cost_caveat"]),
        "  rule                : " + str(m["denominator_rule"]),
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = list(argv or [])
    solve_dir = None
    as_json = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--json":
            as_json = True
        elif a in ("-d", "--dir"):
            i += 1
            if i < len(argv):
                solve_dir = argv[i]
        elif a in ("-h", "--help"):
            print("usage: python3 bench_metrics.py [-d SOLVE_DIR] [--json]")
            return 0
        i += 1
    if solve_dir is None:
        here = os.path.dirname(os.path.abspath(__file__))
        solve_dir = os.path.abspath(os.path.join(here, "..", "work", "solve"))
    m = collect(solve_dir)
    if as_json:
        print(json.dumps(m, ensure_ascii=False, indent=2))
    else:
        print(render_text(m))
    return 0 if m.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())