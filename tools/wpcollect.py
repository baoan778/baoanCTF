#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wpcollect.py —— WP 素材收集器：把一个项目的"已发生的事实"整理成 WP 草稿骨架

为什么要它：写 WP（skills/wp-writer）最费时的不是写，是**翻材料** —— 黑板图、work/<pid>/ 里的
证据文件、flag 候选、原始输出散在五六个地方。这个脚本只做"搬运 + 排版"，
不做结论（结论是人的事）：把能机器化的部分填进 work/<pid>/WP-draft.md，留空的地方明确标 `(待补)`。

用法：
    python3 tools/wpcollect.py <pid>            # 生成 work/<pid>/WP-draft.md
    python3 tools/wpcollect.py <pid> --stdout   # 只打印，不写文件

纪律：只读 + 有上限（不整读大文件、不读 >2MB 的图/日志）；不写任何结论性文字。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
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

# 仓库根：$PI_LAB_ROOT → 从本文件位置推（tools/ 一级深），不写死绝对路径
LAB = Path(os.environ.get("PI_LAB_ROOT") or Path(__file__).resolve().parent.parent)
BOARD = LAB / "board" / "projects"
FLAGLOG = LAB / "log" / "flag-watch.jsonl"
INBOX = LAB / "work" / "inbox"
MAX_LIST = 200          # 目录列表上限
MAX_JSON = 2 * 1024 * 1024


def read_json(p: Path, cap: int = MAX_JSON):
    try:
        if not p.is_file() or p.stat().st_size > cap:
            return None
        # lint:ok 上一行就是护栏（普通文件 + 尺寸上限 cap），比 read_capped 更严。
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def board_summary(pid: str) -> dict:
    g = read_json(BOARD / pid / "graph.json") or {}
    nodes = g.get("nodes", {}) or {}
    goals = [n for n in nodes.values() if n.get("type") == "goal"]
    facts = [n for n in nodes.values() if n.get("type") == "fact"]
    steps = [n for n in nodes.values() if n.get("type") == "step"]
    ver = [f for f in facts if f.get("status") == "verified"]
    inf = [f for f in facts if f.get("status") != "verified"]
    return {
        "goal": (goals[0].get("text") if goals else ""),
        "kind": g.get("kind") or "ctf",
        "solved": bool(g.get("solved")),
        "confirmed": bool(g.get("confirmed")),
        "created": g.get("created_at") or "",
        "updated": g.get("updated_at") or "",
        "verified": [f.get("text", "") for f in ver],
        "unverified": [f.get("text", "") for f in inf],
        "steps": [s.get("text", "") for s in steps],
        "edges": g.get("edges", []) or [],
    }


def intake_card(pid: str) -> dict:
    """录题台卡片（有就用，能拿到题面/靶场/附件）"""
    if not INBOX.is_dir():
        return {}
    best = {}
    for f in sorted(INBOX.glob("*.json")):
        d = read_json(f, 512 * 1024) or {}
        if d.get("pid") == pid or f.stem.endswith(pid):
            best = d
    return best


def tree_of(root: Path) -> list[str]:
    out = []
    if not root.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", "__pycache__")]
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            try:
                out.append(f"{p.relative_to(root)}\t{p.stat().st_size}")
            except Exception:
                continue
            if len(out) >= MAX_LIST:
                out.append("…(已达上限)")
                return out
    return out


def flags_for_window(t0: str, t1: str) -> list[str]:
    if not FLAGLOG.is_file():
        return []
    hits, seen = [], set()
    try:
        with FLAGLOG.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.startswith("{"):
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                ts = r.get("ts", "")
                if t0 and ts and ts < t0:
                    continue
                if t1 and ts and ts > t1:
                    continue
                for h in r.get("hits", []) or []:
                    fl = h.get("flag")
                    if fl and fl not in seen:
                        seen.add(fl)
                        hits.append(f"{fl}  ({h.get('kind','?')}/{h.get('confidence','?')}, {ts}, tool={r.get('tool','?')})")
    except Exception:
        pass
    return hits


def build(pid: str) -> str:
    b = board_summary(pid)
    card = intake_card(pid)
    work = LAB / "work" / pid
    t0, t1 = b.get("created", ""), b.get("updated", "")
    L: list[str] = []
    add = L.append

    add(f"# {pid} — WP 草稿（自动生成的骨架，结论请自己写）")
    add("")
    add(f"> 生成时间：{datetime.now().isoformat(timespec='seconds')}　来源：`tools/wpcollect.py`")
    add(f"> 黑板状态：solved={b['solved']} · confirmed={b['confirmed']} · 类型={b['kind']}")
    add("")
    add("## 1. 题目")
    add("")
    add(f"- 目标：{b['goal'] or '(待补 —— 黑板没写 goal)'}")
    if card.get("target"):
        add(f"- 靶场/目标：`{card['target']}`")
    if card.get("kind"):
        add(f"- 题型：{card['kind']}")
    if card.get("flagfmt"):
        add(f"- flag 格式：{card['flagfmt']}")
    if card.get("attachments"):
        add("- 附件：")
        for a in card["attachments"]:
            add(f"  - `{a}`")
    add("- 题面：")
    add("```")
    add((card.get("desc") or "(待补 —— 从平台/题面粘贴原文)").strip())
    add("```")
    add("")
    add("## 2. 环境与准备")
    add("")
    add("- 附件/镜像来源：(待补)")
    add("- 工具链：`py`（pwntools/z3/…）、`poc`、`fgs` 等（见 tools/TOOLS.md）")
    add("")
    add("## 3. 关键步骤（时间线）")
    add("")
    if b["steps"]:
        for i, s in enumerate(b["steps"], 1):
            add(f"{i}. {s}")
    else:
        add("(待补 —— 可按黑板 step 节点或 delivery 顺序补)")
    add("")
    add("## 4. 已坐实的事实（verified）")
    add("")
    if b["verified"]:
        for f in b["verified"]:
            add(f"- ✅ {f}")
    else:
        add("(黑板里还没有 verified 事实 —— 说明结论还没上板)")
    add("")
    add("## 5. 假设/未验证")
    add("")
    if b["unverified"]:
        for f in b["unverified"]:
            add(f"- ⚠ {f}")
    else:
        add("(无)")
    add("")
    add("## 6. flag 与验证")
    add("")
    fl = flags_for_window(t0, t1)
    if fl:
        add("窗口内 flag-watch 命中（**需逐字确认**，见 AGENTS §四）：")
        for f in fl[:20]:
            add(f"- `{f}`")
    else:
        add("(窗口内无 flag-watch 命中 —— 若已拿到 flag，手工贴在这里并附原始输出)")
    add("")
    add("- 平台回执：(待补 —— `fgs confirm` 只认平台回显)")
    add("")
    add("## 7. 证据清单")
    add("")
    add(f"留档目录：`work/{pid}/`")
    add("")
    add("```")
    for line in tree_of(work):
        add(line)
    add("```")
    add("")
    add("## 8. 复现命令")
    add("")
    add("(待补 —— 把关键命令按顺序贴进来；WP 的价值一半在这里)")
    add("")
    add("## 9. 运输/踩坑")
    add("")
    add("(待补)")
    add("")
    return "\n".join(L)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    only_stdout = "--stdout" in sys.argv
    if not args:
        print(__doc__)
        return 2
    pid = args[0]
    if not (LAB / "work" / pid).is_dir() and not (BOARD / pid).is_dir():
        print(f"✗ 找不到 {pid}（work/{pid} 与 board/projects/{pid} 都不存在）")
        return 1
    text = build(pid)
    if only_stdout:
        print(text)
        return 0
    out = LAB / "work" / pid / "WP-draft.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"✓ 已生成 {out}（{len(text)} 字符）")
    print(f"  下一步：按 skills/wp-writer/SKILL.md 把 `(待补)` 填成结论，产出 docs/writeups/WPs/{pid}_WP.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
