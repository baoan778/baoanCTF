#!/usr/bin/env python3
"""handoff_card.py -- 会话交接卡（<=60 行，机器生成，不给人手改）

为什么要有它
-----------
开新对话最贵的一步是恢复上下文。本仓现在两条路都太重：
  - docs/状态快照.md：672 行约 15KB，一开头整读就等于先烧掉一次预算；
  - handover：打整套交接包（文档 + 留档 + 黑板 + 校验），适合交付，不适合每天开工。
而接手真正要看的只有五件事：项目在哪、机器核对过的状态、人写的下一步、
该读哪些文件、有哪些坑。本工具只渲染这五段，硬上限 60 行。

口径（不许发明，全部可复核）
--------------------------
1. 只输出机器能核对的事实：git 分支/提交/未提交改动、解题账本、余额闸门、
   常驻提示词预算。缺数据就写「缺」并说明原因，不猜。
2. 人写的判断只有一个来源：docs/状态快照.md 第 0.5 节「待办」-- 原样搬运，
   不改写（与 board/handover.py 的 todo_verbatim() 同一口径）。
3. 只读：除 --out 指定的那一个文件外不写任何东西；不碰黑板、不涨 revision。
4. 有界：读盘一律限长，超了报「太大」而不是吃内存（AGENTS.md 第三节）。
5. 不冒充自检结论：本卡不跑测试，自检要单独跑 selfcheck。

用法
----
    handoff-card                          打到 stdout
    handoff-card --out docs/交接卡.md     落盘给下一个会话读
    handoff-card --json                   机器可读
    handoff-card --check [路径]          卡里的提交号 != HEAD 时 rc=3
    handoff-card --advise --session X     按 5 条触发判据给「该换会话了」的建议
    handoff-card --max-lines 60           硬上限

退出码：0 正常 / 2 用法错 / 3 --check 发现过期
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CARD_MAX_LINES = 60
READ_CAP = 2 * 1024 * 1024
AGENTS_BUDGET = 18500
SNAP = "docs/状态快照.md"
TODO_MAX = 12

# 触发判据（--advise）。数值都是**实测基线**，改之前先看 docs/交接卡-模板.md。
TRIG_TURNS = 30                 # 用户轮次
TRIG_TOKENS = 2_000_000         # 累计 input+cache，约等于 60k 上下文的 33 倍
TRIG_REPEAT = 5                 # 同一工具同一入参重复次数（原地打转）
TRIG_COST = 1.5                 # 单题/单会话美元
TRIG_IDLE_HOURS = 6             # 距最后一条消息的间隔（缓存过期）


# ------------------------------ 读盘（都有界） ------------------------------

def lab_root() -> Path:
    env = os.environ.get("PI_LAB_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[1]


def read_text(path, what="文件") -> str:
    p = Path(path)
    try:
        if p.is_symlink() or not p.is_file():
            return ""
        if p.stat().st_size > READ_CAP:
            print("[handoff-card] 跳过 %s：超过 %dB 上限" % (what, READ_CAP), file=sys.stderr)
            return ""
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(READ_CAP)
    except OSError:
        return ""


def read_json(path, what="JSON"):
    raw = read_text(path, what)
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        print("[handoff-card] %s 解析失败：%s" % (what, path), file=sys.stderr)
        return None


def git(root, *args):
    cmd = ["git", "-C", str(root), "-c", "core.quotepath=false", *args]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=20,
                           encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return p.returncode, (p.stdout or "")


def git_info(root) -> dict:
    rc, _ = git(root, "rev-parse", "--git-dir")
    if rc != 0:
        return {"is_repo": False}
    info = {"is_repo": True}
    for key, args in (("branch", ("rev-parse", "--abbrev-ref", "HEAD")),
                      ("sha", ("rev-parse", "--short", "HEAD")),
                      ("subject", ("log", "-1", "--pretty=%s")),
                      ("toplevel", ("rev-parse", "--show-toplevel"))):
        _, out = git(root, *args)
        info[key] = out.strip()
    _, st = git(root, "status", "--porcelain")
    lines = [ln for ln in st.splitlines() if ln.strip()]
    info["dirty_count"] = len(lines)
    info["dirty_paths"] = [ln[3:] for ln in lines[:40]]
    return info


DATE_RX = re.compile(r"20\d\d-\d\d-\d\d")


def _split_blocks(rows):
    """把第 0.5 节切成条目块（每块以 「> ★ ...」 或 「### 」开头）。

    为什么不能只取前 N 行：实测第 0.5 节有 128 行、分成 3 块，只截前 12 行
    等于把一块**从中间切断**，读的人看到半句就断，比不给更糟。
    """
    starts = [i for i, ln in enumerate(rows)
              if ln.startswith("> ★") or ln.startswith("### ")]
    if not starts:
        return [rows] if rows else []
    # ★ 2026-11-23 修：小节标题后面通常空一行，rows[0] 是空串。
    #   原先无条件把 0 插进 starts，于是「空行」自己变成第 0 块、正文为空 ——
    #   实测就是这个 bug 让整段待办变成「（待办为空）」，比不给更误导。
    #   只有开头的**实质内容**（非空行）才值得单独成块。
    if starts[0] != 0 and any(ln.strip() for ln in rows[:starts[0]]):
        starts.insert(0, 0)
    blocks = []
    for n, s in enumerate(starts):
        e = starts[n + 1] if n + 1 < len(starts) else len(rows)
        blocks.append(rows[s:e])
    return blocks


def _block_dates(blk):
    ds = DATE_RX.findall("\n".join(blk))
    return (ds[0] if ds else ""), (max(ds) if ds else "")


def todo_fingerprint(root) -> str:
    """§0.5 的指纹（sha256 前 12 位）。--check 比的是它，不是 HEAD 提交号。

    ★ 为什么不能比 HEAD：卡里记提交号的话，**每次 commit 之后卡立刻算过期** ——
      刚生成、刚提交就假红，这条检查很快就会被人关掉。而卡真正会过期的原因是
      「人改了待办」，所以指纹只算 §0.5 那一段正文（去掉首尾空白）。
    """
    body, _note = snapshot_todo(root, with_note=False)
    if not body:
        return ""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]


def snapshot_todo(root, with_note=True):
    """搬第 0.5 节**第一块**（原样，不改写人的话），并如实报告它有多旧。

    ★ 为什么按文档顺序而不是按日期排序：实测三块的**块头日期**是
      10-11 夜 / 10-11 晚 / 10-10 晚（= 新到旧），而正文里常夹着后来的
      「★ 2026-11-18 校正」批注 —— 若按「块内最大日期」排序，会把块头最老、
      正文被追加最多的那块排到第一，**挑错**。所以顺序信文件，时效只做提醒。
    """
    p = Path(root) / SNAP
    if not p.is_file():
        return "", ""
    out, started = [], False
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i > 200000:
                    break
                if line.startswith("## 0.5"):
                    started = True
                    continue
                if started:
                    if line.strip() == "---" or line.startswith("## "):
                        break
                    out.append(line.rstrip("\n"))
    except OSError:
        return "", ""
    if not "\n".join(out).strip():
        return "", ""

    blocks = _split_blocks(out)
    body = "\n".join(blocks[0]).strip("\n")
    first_date, last_date = _block_dates(blocks[0])

    note = []
    if len(blocks) > 1:
        note.append("（第 0.5 节共 %d 块，卡里只放**第一块**；其余 %d 块看 docs/状态快照.md）"
                    % (len(blocks), len(blocks) - 1))
    if last_date:
        try:
            age = (datetime.now() - datetime.strptime(last_date, "%Y-%m-%d")).days
        except ValueError:
            age = -1
        if age >= 3:
            note.append("（这块最新只提到 %s（%d 天前）-- 先确认它还作数，别照着旧计划干）"
                        % (last_date, age))
    elif first_date:
        note.append("（这块写的日期是 %s）" % first_date)
    return body, ("\n".join(note) if with_note else "")


def solve_state(root) -> dict:
    base = Path(root) / "work" / "solve"
    st = {}
    bal = read_json(base / ".balance.json", "余额闸门")
    if isinstance(bal, dict):
        st["balance"] = {"total": bal.get("total"), "currency": bal.get("currency"),
                         "threshold": bal.get("threshold"), "allow": bal.get("allow"),
                         "reason": bal.get("reason"), "ts": bal.get("ts")}
    led = read_json(base / "ledger.json", "解题账本")
    if isinstance(led, dict) and led:
        def _sum(k):
            return sum((v.get(k) or 0) for v in led.values() if isinstance(v, dict))
        st["ledger"] = {"challenges": len(led), "runs": _sum("runs"),
                        "rejected": _sum("rejected"),
                        "cost": round(_sum("total_cost"), 3),
                        "seconds": int(_sum("total_sec"))}
    cache = read_json(base / "_platform_cache.json", "平台进度缓存")
    if isinstance(cache, dict) and isinstance(cache.get("items"), list):
        items = cache["items"]
        done = sum(1 for i in items if isinstance(i, dict) and i.get("done"))
        ts = cache.get("ts")
        when = (datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
                if isinstance(ts, (int, float)) else "缺")
        st["platform"] = {"done": done, "total": len(items), "cached_at": when}
    return st


def agents_budget(root) -> dict:
    try:
        size = (Path(root) / "AGENTS.md").stat().st_size
    except OSError:
        return {}
    return {"bytes": size, "budget": AGENTS_BUDGET, "left": AGENTS_BUDGET - size}


# ------------------------------ 渲染 ------------------------------

def _fmt_ts(ts) -> str:
    if isinstance(ts, (int, float)):
        secs = ts / 1000.0 if ts > 1e11 else ts
        return datetime.fromtimestamp(secs).strftime("%m-%d %H:%M")
    return "缺"


def _group_dirty(paths) -> str:
    seen = {}
    for p in paths:
        top = p.split("/")[0] if "/" in p else p
        seen[top] = seen.get(top, 0) + 1
    return "、".join("%s×%d" % (k, v) for k, v in list(seen.items())[:5]) or "无"


def build(root) -> dict:
    g = git_info(root)
    todo, note = snapshot_todo(root)
    return {"root": str(root),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "git": g, "todo": todo, "todo_note": note,
            "todo_fingerprint": todo_fingerprint(root),
            "solve": solve_state(root), "agents": agents_budget(root)}


def render(card, max_lines=CARD_MAX_LINES) -> str:
    g = card["git"]
    out = []
    out.append("# 交接卡（机器生成，别手改；要改改 docs/状态快照.md 第 0.5 节）")
    out.append("")
    out.append("> 生成 %s · 新对话只读本卡，别整读状态快照" % card["generated_at"])
    out.append("> 重生成：handoff-card --out docs/交接卡.md")
    fp = card.get("todo_fingerprint") or "缺"
    out.append("> 待办指纹：todo:%s（人改了 §0.5 才需重生成；不用每次 commit 都重跑）" % fp)
    out.append("")
    out.append("## 1. 项目")
    out.append("- 路径：%s" % card["root"])
    if g.get("is_repo"):
        head = "%s @ %s %s" % (g.get("branch") or "缺", g.get("sha") or "缺",
                               g.get("subject") or "")
        out.append("- 分支/提交：%s" % head.rstrip())
        dc = g.get("dirty_count", 0)
        if dc:
            out.append("- 未提交：%d 处（%s）→ 先 git diff 再动手"
                       % (dc, _group_dirty(g.get("dirty_paths") or [])))
        else:
            out.append("- 未提交：无（干净）")
        top = g.get("toplevel") or ""
        if top and Path(top).resolve() != Path(card["root"]).resolve():
            out.append("- 注意：这是工作树，不是主仓（主仓：%s）" % top)
    else:
        out.append("- git：不是仓库 / git 不在（跳过分支信息）")
    out.append("")
    out.append("## 2. 机器核对过的状态（不是自评）")
    sv = card["solve"]
    if sv.get("balance"):
        b = sv["balance"]
        verdict = "放行" if b.get("allow") else "**拦**"
        out.append("- 余额闸门：%s %s（阈值 %s）→ %s；%s · 测于 %s"
                   % (b.get("total"), b.get("currency") or "", b.get("threshold"),
                      verdict, b.get("reason") or "", _fmt_ts(b.get("ts"))))
    else:
        out.append("- 余额闸门：缺 work/solve/.balance.json（costguard 没跑过）")
    led = sv.get("ledger")
    if led:
        out.append("- 解题账本：%d 题 / %d 跑 / 被拒 %d / $%s / %ds"
                   % (led["challenges"], led["runs"], led["rejected"],
                      led["cost"], led["seconds"]))
    pf = sv.get("platform")
    if pf:
        out.append("- 平台进度：%d/%d 通关（缓存于 %s，可能已过时，以平台回执为准）"
                   % (pf["done"], pf["total"], pf["cached_at"]))
    ag = card["agents"]
    if ag:
        warn = " 快满了" if ag["left"] < 500 else ""
        out.append("- AGENTS.md 常驻：%d B / 预算 %d（剩 %d）%s"
                   % (ag["bytes"], ag["budget"], ag["left"], warn))
    out.append("")
    out.append("## 3. 下一步（状态快照第 0.5 节原文，不改写）")
    if card["todo"]:
        out.extend(card["todo"].splitlines())
        if card.get("todo_note"):
            out.extend(card["todo_note"].splitlines())
    else:
        out.append("（待办为空 -- 上一轮已收口，或快照没写）")
    out.append("")
    out.append("## 4. 新对话只读这些")
    out.append("- 本卡（docs/交接卡.md）")
    out.append("- docs/状态快照.md：只读第 0.5 节与第 4 节（已知坑），其余不用读")
    out.append("- 具体轮次背景：见交接卡里的「轮次摘要」小节")
    out.append("")
    out.append("## 5. 坑（由机器状态推出来的，不是照抄清单）")
    pits = []
    if g.get("is_repo") and g.get("dirty_count"):
        pits.append("- 有 %d 处未提交：先看清是什么，别在他人改动上直接覆盖"
                    % g["dirty_count"])
    if sv.get("balance") and not sv["balance"].get("allow"):
        pits.append("- 余额闸门是拦着的：起新一轮前先充值或确认走的是哪个端点（R5-06 #59）")
    if ag and ag["left"] < 500:
        pits.append("- AGENTS.md 只剩 %d B：新规则请进 docs/，别塞常驻区" % ag["left"])
    if not pits:
        pits.append("- 无自动发现的坑；人工坑在 docs/状态快照.md 第 4 节")
    out.extend(pits)
    out.append("")
    if len(out) > max_lines:
        dropped = len(out) - max_lines
        out = out[:max_lines]
        out.append("（超 %d 行上限，截掉 %d 行；完整字段用 --json）" % (max_lines, dropped))
    return "\n".join(out).rstrip() + "\n"


# ------------------------------ 触发判据（--advise） ------------------------------

def scan_session(path) -> dict:
    """流式扫一份会话 JSONL（不整读、不建大对象），只取判据要的计数。"""
    p = Path(path)
    info = {"path": str(p), "user_turns": 0, "tokens_in": 0, "tokens_out": 0,
            "last_ts": None, "repeat_max": 0, "repeat_sample": "", "lines": 0,
            "error": ""}
    if not p.is_file():
        info["error"] = "会话文件不存在"
        return info
    if p.stat().st_size > 200 * 1024 * 1024:
        info["error"] = "会话文件超过 200MB，拒绝扫（用 sesscan）"
        return info
    seen = {}
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                info["lines"] += 1
                if info["lines"] > 200000:
                    info["error"] = "行数超上限，只扫了前 20 万行"
                    break
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(ev, dict):
                    continue
                ts = ev.get("timestamp") or ev.get("ts")
                if isinstance(ts, (int, float)):
                    info["last_ts"] = ts
                elif isinstance(ts, str) and len(ts) >= 10:
                    info["last_ts"] = ts
                role = ev.get("role") or (ev.get("message") or {}).get("role")
                if role == "user":
                    info["user_turns"] += 1
                usage = ev.get("usage") or (ev.get("message") or {}).get("usage") or {}
                if isinstance(usage, dict):
                    for k in ("input", "input_tokens", "prompt_tokens"):
                        v = usage.get(k)
                        if isinstance(v, (int, float)):
                            info["tokens_in"] += int(v)
                            break
                    for k in ("output", "output_tokens", "completion_tokens"):
                        v = usage.get(k)
                        if isinstance(v, (int, float)):
                            info["tokens_out"] += int(v)
                            break
                    for k in ("cache_read", "cacheRead", "cached_tokens"):
                        v = usage.get(k)
                        if isinstance(v, (int, float)):
                            info["tokens_in"] += int(v)
                            break
                for blk in (ev.get("tool_calls") or []):
                    try:
                        key = "%s|%s" % (blk.get("name"), json.dumps(
                            blk.get("arguments") or blk.get("input") or {},
                            ensure_ascii=False, sort_keys=True)[:400])
                    except (TypeError, ValueError):
                        continue
                    seen[key] = seen.get(key, 0) + 1
                    if seen[key] > info["repeat_max"]:
                        info["repeat_max"] = seen[key]
                        info["repeat_sample"] = key.split("|")[0]
    except OSError as exc:
        info["error"] = "读不了：%s" % exc
    return info


def advise(root, session=None, now=None) -> dict:
    """5 条触发判据 + 每条的证据。判据本身不替人做决定，只给「为什么」。"""
    now = now or datetime.now()
    hits = []
    sv = solve_state(root)
    led = sv.get("ledger") or {}
    sess = scan_session(session) if session else {}

    turns = sess.get("user_turns", 0)
    if turns >= TRIG_TURNS:
        hits.append({"id": "T1-轮次", "why": "对话已 %d 轮（阈值 %d）" % (turns, TRIG_TURNS),
                     "action": "写交接卡 → 开新对话"})

    tin = sess.get("tokens_in", 0) + sess.get("tokens_out", 0)
    if tin >= TRIG_TOKENS:
        hits.append({"id": "T2-上下文", "why": "累计 token %d（阈值 %d）"
                     % (tin, TRIG_TOKENS),
                     "action": "写交接卡 → 开新对话"})

    rep = sess.get("repeat_max", 0)
    if rep >= TRIG_REPEAT:
        hits.append({"id": "T3-原地打转",
                     "why": "同一工具同一入参重复 %d 次（%s）" % (rep, sess.get("repeat_sample")),
                     "action": "先记「已证伪路线」再开新对话，别带着它硬试"})

    cost = led.get("cost") or 0
    if cost >= TRIG_COST:
        hits.append({"id": "T4-花费", "why": "账本累计 $%s（阈值 $%s）" % (cost, TRIG_COST),
                     "action": "看 fgs cost 的坐实密度，低产出的题换掉"})

    last = sess.get("last_ts")
    if last:
        try:
            dt = (datetime.fromtimestamp(last / 1000.0) if isinstance(last, (int, float))
                  and last > 1e11 else
                  datetime.fromtimestamp(last) if isinstance(last, (int, float)) else
                  datetime.fromisoformat(str(last).replace("Z", "+00:00")).replace(tzinfo=None))
            gap = (now - dt).total_seconds() / 3600.0
            if gap >= TRIG_IDLE_HOURS:
                hits.append({"id": "T5-隔了很久",
                             "why": "距最后一条消息 %.1f 小时（缓存多已过期，续跑按全价重读）" % gap,
                             "action": "开新对话 + 交接卡，别在原对话里回一句「继续」"})
        except (ValueError, OSError, OverflowError):
            pass

    return {"session": sess.get("path", ""), "hits": hits,
            "verdict": "建议开新对话" if hits else "可以继续",
            "evidence": {"user_turns": turns, "tokens": tin,
                         "repeat_max": rep, "ledger_cost": cost}}


def render_advice(adv) -> str:
    out = ["# 开新对话判据（%s）" % adv["verdict"], ""]
    if not adv["hits"]:
        out.append("（无触发：轮次 %d / token %d / 重复 %d / 账本 $%s）"
                   % (adv["evidence"]["user_turns"], adv["evidence"]["tokens"],
                      adv["evidence"]["repeat_max"], adv["evidence"]["ledger_cost"]))
        return "\n".join(out) + "\n"
    for h in adv["hits"]:
        out.append("- [%s] %s → %s" % (h["id"], h["why"], h["action"]))
    out.append("")
    out.append("提醒格式（照抄给人）：建议开新对话：<上面第一条原因>。交接卡已更新到 docs/交接卡.md")
    return "\n".join(out) + "\n"


# ------------------------------ CLI ------------------------------

def parse_args(argv):
    opt = {"out": None, "json": False, "check": None, "advise": False,
           "session": None, "max_lines": CARD_MAX_LINES, "root": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            print(__doc__)
            raise SystemExit(0)
        elif a == "--json":
            opt["json"] = True
        elif a in ("--out", "--session", "--root", "--max-lines"):
            i += 1
            if i >= len(argv):
                raise SystemExit("%s 需要一个值" % a)
            if a == "--out":
                opt["out"] = argv[i]
            elif a == "--session":
                opt["session"] = argv[i]
            elif a == "--root":
                opt["root"] = argv[i]
            else:
                try:
                    opt["max_lines"] = int(argv[i])
                except ValueError:
                    raise SystemExit("--max-lines 需要整数")
        elif a == "--check":
            opt["check"] = "docs/交接卡.md"
            if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                i += 1
                opt["check"] = argv[i]
        elif a == "--advise":
            opt["advise"] = True
        else:
            raise SystemExit("不认识的参数：%s（--help 看用法）" % a)
        i += 1
    return opt


def cmd_check(path: Path, card) -> int:
    g = card["git"]
    if not g.get("is_repo"):
        print("[handoff-card] 不是 git 仓库，--check 无从比对", file=sys.stderr)
        return 3
    raw = read_text(path, "交接卡")
    if not raw.strip():
        print("[handoff-card] 没有 %s -- 先 handoff-card --out %s" % (path, path))
        return 3
    cur = todo_fingerprint(Path(g.get("toplevel") or card["root"]))
    m = re.search(r"待办指纹：todo:([0-9a-f]{6,64})", raw)
    if not m:
        # 老格式的卡（只记了提交号）：不假装能判，直接要求重生成
        print("[handoff-card] %s 是旧格式（没有待办指纹）-- 重跑 handoff-card --out %s"
              % (path, path))
        return 3
    old = m.group(1)
    if cur and old != cur:
        print("[handoff-card] 卡已过期：卡的待办指纹 %s / 现场 %s（有人改了 §0.5）"
              " -- 重跑 handoff-card --out %s" % (old, cur, path))
        return 3
    print("[handoff-card] 卡是最新的（待办指纹 %s，与现场一致；此后 commit 不影响）" % cur)
    return 0


def main(argv) -> int:
    try:
        opt = parse_args(argv)
    except SystemExit as exc:
        msg = str(exc)
        if msg and msg != "0":
            print(msg, file=sys.stderr)
        return 0 if msg in ("0", "") else 2
    root = Path(opt["root"]).resolve() if opt["root"] else lab_root()

    if opt["advise"]:
        adv = advise(root, opt["session"])
        if opt["json"]:
            print(json.dumps(adv, ensure_ascii=False, indent=2))
        else:
            sys.stdout.write(render_advice(adv))
        return 0

    card = build(root)
    if opt["check"]:
        p = Path(opt["check"])
        return cmd_check(p if p.is_absolute() else root / p, card)
    if opt["json"]:
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return 0
    text = render(card, opt["max_lines"])
    if opt["out"]:
        p = Path(opt["out"])
        if not p.is_absolute():
            p = root / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
        print("[handoff-card] 已写入 %s（%d 行）" % (p, len(text.splitlines())))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
