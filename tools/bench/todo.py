#!/usr/bin/env python3
"""bench/todo.py —— 命令行版「待解清单」（照抄就是下一刀的选题）。

★ 2026-11-20 重写。原版四个问题：
  1. token 与 base_url 硬编码在脚本里（旧任务凭据早已失效，却报不出原因）；
  2. 预算算法是第三份副本（120/180/300 + cap 600/420），与真正下发的 150/300/480 漂移；
  3. 路径写死 /opt/pi-ctf/work/solve/ledger.json，宿主上跑必 FileNotFoundError；
  4. 网络失败直接抛栈。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "bench"))

from solve_policy import budget_for, model_for  # noqa: E402

DIFF_ORDER = {"easy": 0, "medium": 1, "hard": 2}


def load_ledger() -> dict:
    fp = ROOT / "work" / "solve" / "ledger.json"
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def main() -> int:
    as_json = "--json" in sys.argv
    try:
        from live import platform  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        print("[todo] 载入 live.py 失败：" + str(e))
        return 2
    ok, data = platform(net=True, retries=2)
    if not ok:
        print("[todo] 平台题单不可用：" + str(data)[:160])
        print("        (409 = run 已终结; 超时 = VPN; 401 = token 不对。别当成『没有待解』)")
        return 1
    led = load_ledger()
    rows = []
    for i in data:
        if i.get("done"):
            continue
        code = i.get("code")
        L = led.get(code) or {}
        at = int(L.get("attempts", 0))
        sec = int(L.get("total_sec", 0))
        diff = (i.get("diff") or "").lower()
        score = int(i.get("score") or 0)
        rows.append({
            "diff": diff, "score": score, "code": code,
            "attempts": at, "budget": budget_for(diff, at), "model": model_for(at),
            "spent": sec, "rejected": int(L.get("rejected", 0)),
            "roi": round(score / sec, 2) if sec else None,
        })
    rows.sort(key=lambda r: (DIFF_ORDER.get(r["diff"], 9), r["attempts"], r["code"]))
    if as_json:
        print(json.dumps({"remaining": len(rows), "rows": rows,
                          "by_difficulty": dict(Counter(r["diff"] for r in rows))},
                         ensure_ascii=False, indent=2))
        return 0
    print("难度    分值   题号          尝  预算  累计秒 拒  ROI    档位")
    for r in rows:
        roi = "-" if r["roi"] is None else ("%.2f" % r["roi"])
        print("%-7s %5d %-12s %3d %5d %7d %3d %6s %s"
              % (r["diff"], r["score"], r["code"], r["attempts"], r["budget"],
                 r["spent"], r["rejected"], roi, r["model"]))
    print("")
    print("剩余按难度: " + str(dict(Counter(r["diff"] for r in rows))))
    print("没跑过的: " + str([r["code"] for r in rows if r["attempts"] == 0][:20]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
