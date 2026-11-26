#!/usr/bin/env python3
"""从黑板检测「打转」并发一份纠偏提示（Advisor）。

取代原技能包的 `loop_detector.py`（那个读它自己的 `current_session.json`）。
本脚本读 pi-lab 黑板的 **graph.json + log.jsonl + deliveries.jsonl** ——
打转的证据本来就在图里：**重复的 Fact、反复 claim/release 的 Step、被反复拒绝的投递**。

用法
----
    python3 skills/ir-response/scripts/loop-check.py <project_id>
    python3 skills/ir-response/scripts/loop-check.py <project_id> --wall 25   # 多少分钟没新事实算停摆

退出码
----
    0  正常
    1  检测到打转 / 停摆 —— 按输出的纠偏提示换攻击面

为什么要有它
-----------
`AGENTS.md` 里那条纪律「一条路线连续多次没进展就换攻击面」是靠自觉执行的；
脚本把它变成可观测的信号：**图上出现了什么，就等于发生了什么**。
"""
from __future__ import annotations

import argparse
import os
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
#   自包含（自己 import sys），不依赖本文件是否已 import sys 或用别名。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def read_jsonl(f: Path) -> list:
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def norm(s: str) -> str:
    """归一化：去空白/标点，只留中文与单词，用于"是不是同一句话"的判断。"""
    return re.sub(r"[\s\W_]+", "", (s or "").lower())[:60]


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="黑板打转检测 + 纠偏提示")
    ap.add_argument("project")
    ap.add_argument("--board", default=os.environ.get("PI_LAB_ROOT") or Path(__file__).resolve().parents[3] / "board", type=str)
    ap.add_argument("--wall", type=int, default=25, help="多少分钟没有新 Fact 算停摆（默认 25）")
    ap.add_argument("--sim", type=float, default=0.9, help="Fact 文本相似度阈值（默认 0.9）")
    a = ap.parse_args()

    d = Path(a.board) / "projects" / a.project
    if not (d / "graph.json").exists():
        sys.exit(f"✗ 找不到 {d}/graph.json")
    g = json.loads((d / "graph.json").read_text(encoding="utf-8"))
    log = read_jsonl(d / "log.jsonl")
    deliveries = read_jsonl(d / "deliveries.jsonl")

    nodes = g["nodes"]
    facts = [n for n in nodes.values() if n["type"] == "fact"]
    steps = [n for n in nodes.values() if n["type"] == "step"]

    signals = []

    # ① 重复事实（长得几乎一样的 Fact 反复出现 = 在原地绕）
    keys = Counter(norm(n["text"]) for n in facts)
    dups = [(k, c) for k, c in keys.items() if c > 1 and k]
    if dups:
        signals.append(("重复 Fact", [f"{c} 次：{k[:40]}…" for k, c in dups[:5]]))

    # ② 同一 Step 反复 claim / release
    claims = Counter(e.get("id") or e.get("step") for e in log if e.get("op") == "step.claim")
    releases = Counter(e.get("id") or e.get("step") for e in log if e.get("op") == "step.release")
    churn = [(k, claims[k] + releases.get(k, 0)) for k in claims]
    churn = [(k, v) for k, v in churn if v >= 3]
    if churn:
        signals.append(("Step 反复认领/释放", [f"{k} 共 {v} 次" for k, v in churn[:5]]))

    # ③ 投递被反复拒（同指纹 3 次以上）
    rej = Counter(e["fp"] for e in deliveries if e.get("outcome") == "rejected")
    bad = [(k, c) for k, c in rej.items() if c >= 3]
    if bad:
        reasons = {e["fp"]: e.get("reason") for e in deliveries if e.get("reason")}
        signals.append(("同一条投递被反复拒绝",
                        [f"fp={k} × {c}：{(reasons.get(k) or '')[:60]}" for k, c in bad[:5]]))

    # ④ 停摆：最后一次有新 Fact 到现在多久
    last_ts = max([t for t in (parse_ts(n.get("created_at")) for n in facts) if t], default=None)
    idle_min = None
    if last_ts:
        idle_min = (datetime.now(timezone.utc) - last_ts).total_seconds() / 60
        if idle_min >= a.wall and not g.get("solved"):
            signals.append(("停摆", [f"已 {idle_min:.0f} 分钟没有新 Fact（阈值 {a.wall} 分钟）"]))

    # ⑤ 图上"已证伪"是否被记下来（dropped/abandoned 也是产出）
    abandoned = [s for s in steps if s["status"] == "abandoned"]
    open_steps = [s for s in steps if s["status"] == "open"]

    print(f"══━ {a.project} 打转检查（rev={g.get('revision')}）═══")
    print(f"  Fact {len(facts)} · Step {len(steps)}（open {len(open_steps)} / abandoned {len(abandoned)}）"
          f" · 投递 {len(deliveries)} 条")
    if idle_min is not None:
        print(f"  距最后一次新 Fact：{idle_min:.0f} 分钟")
    print()

    if not signals:
        print("✓ 没检测到打转信号。")
        return 0

    for name, items in signals:
        print(f"⚠ {name}")
        for it in items:
            print(f"    - {it}")
    print()

    # ── Advisor：强制重写下一步（照原技能包的纠偏思路）──
    print("── 纠偏提示（把下面整段交给「下一轮的我」）" + "─" * 26)
    print("你现在在一个项目上打转了。**不要再重试同类动作**，按下面重写 3-6 步 todo：")
    print()
    print("1. 先复述**已证伪的路线**（图上 abandoned 的 Step / confidence=unverified 的 Fact），")
    print("   明确哪些攻击面已经排除了 —— 排除也是结论。")
    print("2. 换**攻击面**而不是换 payload：换入口（Web→SSH→DB→邮件）、换协议层、换数据源")
    print("   （主机日志 ↔ 流量 ↔ 内存 ↔ 磁盘镜像）。")
    print("3. 每一步都必须写**判定式**：看到什么输出才算这一步成功？现在写的很多步骤没有判定式。")
    print("4. 每步给一个**时间盒**（例如 15 分钟），到点没结果就落一条 Fact 记下「试过、看到什么」。")
    print("5. 查现成资料再动手：`poc \"<特征词>\"`（本地 POC 库）、")
    print("   `tools/knowledge/INDEX.md`（离线方法论）、`skills/ir-response/references/`（SOP）。")
    print("6. 最后：把这一步的结论写成一条 `fgs publish … --confidence verified` 的 Fact，")
    print("   由主会话 `fgs apply` 合入 —— **没进图的结论等于没发生**。")
    print("─" * 68)
    return 1


if __name__ == "__main__":
    sys.exit(main())
