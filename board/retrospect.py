#!/usr/bin/env python3
"""手法库的「候选 → 审核 → 合并」闸门（对位 CyberStrikeAI 技能包的 retrospect_ir + apply_updates_ir）。

问题
----
`docs/writeups/手法库.md` 是**跨题复用**的唯一沉淀，但现在它是 wp-writer 代理**直接写**的 ——
没有中间态、没有审核、没有审计。写错一条就会污染后续所有题（本项目真踩过：
曾把 OCR 的 `psm=7` 写反成「糊的」，后来才发现是 psm=3 才对）。

做法（照它们那套，但落到我们的图上）
----
    黑板图  ──scan──►  候选（不合并）  ──merge(--reviewer 人)──►  手法库.md
                          │                    │
                          │                    └─ 审计：谁、什么时候、合了哪条
                          └─ 只有 verified 的 Fact 能进候选；与既有条目重复的直接跳过

用法
----
    python3 board/retrospect.py scan [--project PID]      # 扫图生成候选
    python3 board/retrospect.py --list                    # 列候选
    python3 board/retrospect.py --show <id>               # 看细节（含可贴的 markdown）
    python3 board/retrospect.py --merge <id> --reviewer 张三 [--generalize "…"] [--pitfall "…"]
    python3 board/retrospect.py --drop <id> --reviewer 张三 --reason "太具体，不可复用"

为什么 merge 必须带 --reviewer
---------------------------
和 `fgs confirm` 是同一条纪律：**对外产物不能建立在 agent 的自评上**。
手法库是给未来的自己（和同事）看的，写错一条的成本远高于多敲一个名字。

落盘
----
    docs/writeups/手法库.candidates.json    ← 候选（结构化，机器可读）
    docs/writeups/手法库.md                 ← 正本（只有 merge 才动）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import os

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
#   自包含（自己 import sys），不依赖本文件是否已 import sys 或用别名。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


ROOT = Path(__file__).resolve().parents[1]
BOARD = Path(os.environ.get("PI_LAB_BOARD_DIR") or (ROOT / "board" / "projects"))
# 允许覆盖：测试这条闸门时要在临时目录里跑，不能拿真手法库当试验品
WRITEUPS = Path(os.environ.get("PI_LAB_WRITEUPS_DIR") or (ROOT / "docs" / "writeups"))
CANDS = WRITEUPS / "手法库.candidates.json"
LIB = WRITEUPS / "手法库.md"
AUDIT = WRITEUPS / "手法库.audit.jsonl"
# ★ 死路正本（2026-10-12）：手法库管「该怎么做」，这本管「**别再走**」。
#   为什么单独一本：难题里最贵、别人最没有的资产就是「这条路走不通」——
#   埋在 WP 散文里搜不出来，混进手法库又把「照做就能成」的语义弄脏。
DEADLIB = WRITEUPS / "死路库.md"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_cands() -> list:
    if not CANDS.exists():
        return []
    try:
        return json.loads(CANDS.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def save_cands(items: list) -> None:
    CANDS.parent.mkdir(parents=True, exist_ok=True)
    CANDS.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def audit(rec: dict) -> None:
    with AUDIT.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": now(), **rec}, ensure_ascii=False) + "\n")


def norm(s: str) -> str:
    """归一化：用于跟既有条目/候选做重复判定。"""
    return re.sub(r"[\s\W_]+", "", (s or "").lower())


# ``` 围栏块（库开头的「记录格式」示例里也有 ###，不剥掉会被当成真条目 ——
# 2026-10-12 实测：`fgs brief` 里冒出一条 `✗ <走不通的路线>（一句话）`）
_FENCE_RX = re.compile(r"^```.*?^```", flags=re.M | re.S)


def strip_fences(text: str) -> str:
    return _FENCE_RX.sub("", text or "")


def lib_body_norm(lib: Path = LIB) -> str:
    """正本的归一化全文（用于判断候选是不是已经写过了）。"""
    if not lib.exists():
        return ""
    # lint:ok lib 默认是模块级常量 LIB（仓库内手法库），不是外部路径。
    return norm(lib.read_text(encoding="utf-8"))


# 不参与相似度判断的高频词（否则"两个中文词一样"就会误判成重复）
STOP = {
    "the", "and", "for", "with", "this", "that", "from", "into", "step", "fact",
    "true", "false", "none", "null", "http", "https", "www", "com", "org", "net",
    "文件", "目录", "路径", "命令", "输出", "结果", "结论", "证据", "来源", "做法",
    "触发", "条件", "通用", "踩坑", "验证", "确认", "检查", "发现", "使用", "通过",
    "已经", "可以", "需要", "一个", "这个", "那个", "我们", "他们", "以及", "然后",
}


def sig_tokens(*texts) -> set:
    """抽"像指纹一样"的 token：十六进制、路径、文件名、英文标识符、中文 2-gram。

    为什么不用精确子串：wp-writer 会把 Fact **改写**成步骤，原文一个字都对不上，
    但「IHDR / CRC / 0x7cfb886d」这些特征词一定重合 —— 所以按 token 重合度算。
    """
    s = " ".join(t for t in texts if t).lower()
    toks = set()
    toks |= set(re.findall(r"0x[0-9a-f]+", s))
    toks |= set(re.findall(r"/[\w./-]{3,}", s))
    toks |= set(re.findall(r"\b[\w-]+\.(?:php|phtml|png|jpg|gif|py|sh|bat|ps1|exe|dll|so|zip|rar|"
                           r"pcap|pcapng|evtx|log|txt|json|md|xml|yaml|yml|conf|ini|dat|bin)\b", s))
    for w in re.findall(r"\b[a-z][a-z0-9_]{3,}\b", s):
        toks.add(w)
    for w in re.findall(r"[\u4e00-\u9fff]{2,}", s):
        toks |= {w[i:i + 2] for i in range(len(w) - 1)}
    return {t for t in toks if len(t) >= 2 and t not in STOP}


def _weight(tok: str) -> float:
    """token 的"区分力"权重。

    踩过的坑：中文 bigram 噪声极大（"证据/做法/文件"谁都有），第一版等权算出来
    PNG 那条只有 34%，低于阈值 → **同一条手法被写进了正本两次**。
    现在按区分力加权：硬指纹 1.0 / 英文标识符 0.7 / 中文 bigram 0.2。
    """
    if tok.startswith("0x") or "/" in tok or "." in tok:
        return 1.0                       # 十六进制、路径、文件名 —— 最强指纹
    if re.fullmatch(r"[a-z][a-z0-9_]{3,}", tok):
        return 0.7                       # ihdr / tesseract / sqlmap 这类标识符
    return 0.2                           # 中文 bigram


def lib_entries(lib: Path = LIB) -> list:
    """把正本切成条目：[(标题, 正文)]，按 ### 切。"""
    if not lib.exists():
        return []
    # lint:ok 同上：lib 默认是仓库内常量 LIB。
    parts = re.split(r"^###\s+", strip_fences(lib.read_text(encoding="utf-8")), flags=re.M)[1:]
    out = []
    for part in parts:
        lines = part.splitlines()
        if lines:
            out.append((lines[0].strip(), "\n".join(lines)))
    return out


def lib_similarity(c: dict, lib: Path = LIB):
    """候选特征词与**每一条**正本条目的加权覆盖率。

    返回 [(覆盖率, 命中 token, 条目标题)]，按覆盖率降序 —— 只给一个总分没用，
    必须能说出「跟哪一条像」，人才好判断。
    """
    a = sig_tokens(c.get("trigger"), *(c.get("method") or []))
    if not a:
        return []
    wa = sum(_weight(x) for x in a)
    rows = []
    for title, body in lib_entries(lib):
        b = sig_tokens(title, body)
        hit = a & b
        wh = sum(_weight(x) for x in hit)
        rows.append((wh / wa if wa else 0.0, hit, title))
    rows.sort(key=lambda r: -r[0])
    return rows


def best_match(c: dict, lib: Path = LIB):
    rows = lib_similarity(c, lib)
    return rows[0] if rows else (0.0, set(), "")


# ★ fail-closed：只要跟任何一条条目有**可察觉**的重合，默认就拦下，要写必须 --force-dup。
#   理由（踩了三次才想明白）：自动分数永远调不准 —— 调高漏判、调低误拦，
#   而漏判的代价是往正本里灌重复条目（不可逆地变脏）。
#   人的判断是独立通道：多打一个 flag 很便宜，"库脏了"很贵。
DUP_BLOCK = 0.10
DUP_WARN = 0.04


def already_in_lib(c: dict, body: str | None = None, lib: Path = LIB) -> bool:
    """覆盖率够高就算"已在正本"。"""
    score, _, _ = best_match(c, lib)
    return score >= DUP_BLOCK


def lib_titles(lib: Path = LIB) -> list:
    """正本里已有的 ### 标题。"""
    if not lib.exists():
        return []
    return [m.group(1).strip() for m in re.finditer(r"^###\s+(.+)$",
              strip_fences(lib.read_text(encoding="utf-8")), re.M)]


# ── 死路通道（kind=deadend）─────────────────────────────────────────────────
# 与 `board/fgs.py` 的 DEAD_RX 同源（那边管本题简报，这边管跨题沉淀）；
# 这里多几个词（排除/不成立/不适用），因为 Worker 按《派活契约》第 6 条
# 以「排除：…」起头投死路事实。
DEAD_RX = re.compile(
    r"(未发现|没有|无回显|无时间差|证伪|不通|失败|不行|未命中|无差异|全负|零命中"
    r"|排除|不成立|走不通|死路|不可行|不适用)")
DEAD_MAX_PER_PROJECT = 20      # 一题最多抽 20 条死路进候选（防一道烂题把候选池淹了）


def lib_for(c: dict) -> Path:
    """候选进哪本正本：手法 → 手法库.md；死路 → 死路库.md。"""
    return DEADLIB if c.get("kind") == "deadend" else LIB


def scan_deadends(pids: list, cands: list, known: set, seen_keys: set) -> tuple[int, int]:
    """抽「死路」候选（kind=deadend）。

    为什么单独一遍：死路**不挂 step** —— Worker 按《派活契约》第 6 条直接
    `fgs publish` 一条「排除：…」事实，所以按 step 走的主循环抓不到它。
    取两个来源：① `verified` 且文本命中 DEAD_RX 的 Fact；② 由 `abandoned` step 产出的。
    """
    added = skipped = 0
    dead_titles = [norm(t) for t in lib_titles(DEADLIB)]
    for pid in pids:
        gf = BOARD / pid / "graph.json"
        if not gf.exists():
            continue
        try:
            g = json.loads(gf.read_text(encoding="utf-8"))
        except Exception:
            continue
        nodes, edges = g.get("nodes", {}), g.get("edges", [])
        abandoned = {s["id"] for s in nodes.values()
                     if s.get("type") == "step" and s.get("status") == "abandoned"}
        prod_of = {}
        for e in edges:
            if e.get("type") == "produced":
                prod_of.setdefault(e.get("to"), e.get("from"))
        n_here = 0
        for fid, f in sorted(nodes.items()):
            if f.get("type") != "fact" or f.get("confidence") != "verified":
                continue
            text = (f.get("text") or "").strip()
            if len(text) < 8 or not DEAD_RX.search(text):
                continue
            if n_here >= DEAD_MAX_PER_PROJECT:
                break
            dedupe = hashlib.sha256(norm(text).encode()).hexdigest()[:12]
            if dedupe in seen_keys or any(norm(text) == t for t in dead_titles):
                skipped += 1
                continue
            cid = "dead-" + hashlib.sha256(f"{pid}:{fid}".encode()).hexdigest()[:8]
            if cid in known:
                skipped += 1
                continue
            src_step = prod_of.get(fid)
            cands.append({
                "id": cid, "dedupe": dedupe, "status": "candidate", "kind": "deadend",
                "created_at": now(), "project": pid,
                # signal：abandoned step 产出的 = 明确放弃过的，比顺手记一句可信
                "signal": "abandoned" if src_step in abandoned else "prose",
                "trigger": text[:120],
                "method": [f"`{fid}` {text}"],
                "source": {"project": pid, "step": src_step or f"({fid} 直投)",
                           "facts": [fid]},
                "evidence": [f.get("provenance", {}).get("evidence")],
                "generalize": None, "pitfall": None,
                "reviewer": None, "reviewed_at": None,
            })
            seen_keys.add(dedupe)
            n_here += 1
            added += 1
    return added, skipped


def scan(project: str | None) -> int:
    """从图里抽候选：只看 **done 的 Step + 它 produced 的 verified Fact**。"""
    pids = [project] if project else sorted(p.name for p in BOARD.iterdir() if p.is_dir())
    existing_titles = [norm(t) for t in lib_titles()]
    cands = load_cands()
    known = {c["id"] for c in cands}
    seen_keys = {c["dedupe"] for c in cands}
    added = skipped = 0

    for pid in pids:
        gf = BOARD / pid / "graph.json"
        if not gf.exists():
            continue
        g = json.loads(gf.read_text(encoding="utf-8"))
        nodes, edges = g["nodes"], g.get("edges", [])

        # 兜底：这题压根没走 step→fact（直接 fact-add 记的结论）—— 那就按"整题"抽一条候选。
        # 不这么做的话，最好用的手法反而会被漏掉（本项目真发生：PNG IHDR 那条就是这么丢的）。
        if not any(e.get("type") == "produced" for e in edges):
            vfacts = [n for n in nodes.values()
                      if n["type"] == "fact" and n.get("confidence") == "verified"]
            goal = next((n for n in nodes.values() if n["type"] == "goal"), None)
            if vfacts and goal:
                trigger = goal["text"].strip()
                method = [f"`{f['id']}` {f['text']}" for f in vfacts]
                dedupe = hashlib.sha256(norm(trigger).encode()).hexdigest()[:12]
                cid = "cand-" + hashlib.sha256(f"{pid}:whole".encode()).hexdigest()[:8]
                if (dedupe not in seen_keys and cid not in known
                        and not any(norm(trigger) == t for t in existing_titles)):
                    cands.append({
                        "id": cid, "dedupe": dedupe, "status": "candidate",
                        "created_at": now(), "project": pid, "signal": "prose",
                        "trigger": trigger, "method": method,
                        "source": {"project": pid, "step": "(整题)",
                                   "facts": [f["id"] for f in vfacts]},
                        "evidence": [f.get("provenance", {}).get("evidence") for f in vfacts],
                        "generalize": None, "pitfall": None,
                        "reviewer": None, "reviewed_at": None,
                    })
                    seen_keys.add(dedupe)
                    added += 1
            continue

        for sid, s in sorted(nodes.items()):
            if s["type"] != "step" or s["status"] != "done":
                continue
            # 这个 step 产出的 fact（produced 边）
            fids = [e["to"] for e in edges
                    if e.get("type") == "produced" and e.get("from") == sid]
            facts = [nodes[f] for f in fids if f in nodes and nodes[f].get("confidence") == "verified"]
            if not facts:
                continue                       # 没有 verified 结论 → 不是可复用手法，是过程记录

            trigger = s["text"].strip()
            if len(trigger) < 6:
                continue
            method = [f"`{f['id']}` {f['text']}" for f in facts]
            dedupe = hashlib.sha256(norm(trigger).encode()).hexdigest()[:12]
            if dedupe in seen_keys or any(norm(trigger) == t for t in existing_titles):
                skipped += 1
                continue

            cid = "cand-" + hashlib.sha256(f"{pid}:{sid}".encode()).hexdigest()[:8]
            if cid in known:
                skipped += 1
                continue
            cands.append({
                "id": cid, "dedupe": dedupe, "status": "candidate",
                "created_at": now(), "project": pid,
                # signal：事实带结构化 meta（phase/tool/…）= 更像可复用手法；prose = 更像过程记录
                "signal": "meta" if any(f.get("meta") for f in facts) else "prose",
                "trigger": trigger,                 # 触发条件 ← Step 文本（"下一步试什么"）
                "method": method,                   # 做法 ← 产出的事实（可复现）
                "source": {"project": pid, "step": sid,
                           "facts": [f["id"] for f in facts]},
                "evidence": [f.get("provenance", {}).get("evidence") for f in facts],
                "generalize": None, "pitfall": None,
                "reviewer": None, "reviewed_at": None,
            })
            seen_keys.add(dedupe)
            added += 1

    dead_added, dead_skipped = scan_deadends(pids, cands, known, seen_keys)

    save_cands(cands)
    n_tech = len([c for c in cands if c["status"] == "candidate" and c.get("kind") != "deadend"])
    n_dead = len([c for c in cands if c["status"] == "candidate" and c.get("kind") == "deadend"])
    print(f"✓ scan 完成：新增候选手法 {added} 条 / 候选死路 {dead_added} 条，"
          f"跳过（重复/无结论）{skipped + dead_skipped} 条")
    print(f"  候选池：手法 {n_tech} 条 + 死路 {n_dead} 条待审")
    if added or dead_added:
        print("  下一步：`python3 board/retrospect.py --list` 看候选，"
              "审核后 `--merge <id> --reviewer <你的名字>`（死路自动入 `docs/writeups/死路库.md`）")
    return 0


def to_markdown(c: dict) -> str:
    """把候选渲染成可直接贴进正本的条目（按 kind 分两种模板）。"""
    ev = [e for e in (c.get("evidence") or []) if e]
    if c.get("kind") == "deadend":
        why = c.get("generalize") or "（审核时补：现象 + 判据 —— 凭什么说它走不通）"
        return "\n".join([
            f"### ✗ {c['trigger'][:60]}",
            "",
            f"**走不通的路线**：{c['trigger']}",
            "",
            f"**为什么（现象与判据）**：{why}",
            "",
            "**原始证据**：",
            *[f"- {m}" for m in c["method"]],
            "",
            f"**别再做**：{c.get('pitfall') or '（审核时补：什么条件下不用再试、该换哪个攻击面）'}",
            "",
            "**来源**：" + f"{c['source']['project']}（step {c['source']['step']} / facts "
            + ", ".join(c["source"]["facts"]) + "）",
            *(["", "**证据**：", *[f"- {x}" for x in ev]] if ev else []),
            "",
        ])
    gen = c.get("generalize") or "（审核时补：这条脱离本题后还能怎么用）"
    pit = c.get("pitfall") or "（审核时补：踩过什么）"
    return "\n".join([
        f"### {c['trigger'][:60]}",
        "",
        f"**触发条件**：{c['trigger']}",
        "",
        "**做法**：",
        *[f"{i+1}. {m}" for i, m in enumerate(c["method"])],
        "",
        f"**通用化**：{gen}",
        "",
        f"**踩坑**：{pit}",
        "",
        "**来源**：" + f"{c['source']['project']}（step {c['source']['step']} / facts "
        + ", ".join(c["source"]["facts"]) + "）",
        *([ "", "**证据**：", *[f"- {x}" for x in ev]] if ev else []),
        "",
    ])


def cmd_list() -> int:
    cands = [c for c in load_cands() if c["status"] == "candidate"]
    if not cands:
        print("候选池是空的。先 `python3 board/retrospect.py scan`")
        return 0
    # 「更像手法」的排前面：带结构化 meta 的 > 纯过程的。
    # signal 只是排序提示，**不代表已审核** —— 能不能进正本仍然由人决定。
    cands.sort(key=lambda c: (c.get("kind") == "deadend", c.get("signal") != "meta",
                              c["project"], c["id"]))
    body = lib_body_norm()
    n_dead = len([c for c in cands if c.get("kind") == "deadend"])
    print(f"══━ 待审候选（{len(cands)} 条：手法 {len(cands) - n_dead} / 死路 {n_dead}）═══")
    for c in cands:
        if c.get("kind") == "deadend":
            tag = "✗死路" if c.get("signal") == "abandoned" else "✗疑死路"
        else:
            tag = "◆手法" if c.get("signal") == "meta" else "·过程"
        score, hit, title = best_match(c, lib_for(c))
        dup = ""
        if score >= DUP_BLOCK:
            dup = f"  ⚠疑似已在正本({score:.0%} ↔ {title[:18]})"
        elif score >= DUP_WARN:
            dup = f"  ~弱重合({score:.0%})"
        print(f"  {tag} {c['id']}  [{c['project']}] {c['trigger'][:48]}{dup}")
        kind_txt = "产出 1 条已证伪结论" if c.get("kind") == "deadend" else f"产出 {len(c['method'])} 条 verified 事实"
        print(f"            {kind_txt} · {c['source']['step']}")
    print("\n审核：`--show <id>` 看全文 → `--merge <id> --reviewer <名字>` 或 `--drop <id> ...`")
    return 0


def cmd_show(cid: str) -> int:
    c = next((x for x in load_cands() if x["id"] == cid), None)
    if not c:
        print(f"✗ 没有候选 {cid}", file=sys.stderr)
        return 1
    print(f"══━ {c['id']}  ({c['status']}{' / 死路' if c.get('kind') == 'deadend' else ''}) ═══")
    rows = [r for r in lib_similarity(c, lib_for(c)) if r[0] >= DUP_WARN]
    if rows:
        print("⚠ 与正本已有条目的重合度（merge 会默认拦下）：")
        for sc, hit, title in rows[:3]:
            print(f"    {sc:5.1%}  「{title}」  命中：{'、'.join(sorted(hit)[:8])}")
        print()
    print(to_markdown(c))
    return 0


def cmd_merge(cid: str, reviewer: str, generalize, pitfall, force_dup=False) -> int:
    if not reviewer or not reviewer.strip():
        # argparse 已经拦了一次，这里是第二道
        print("✗ merge 必须带 --reviewer（谁审的）", file=sys.stderr)
        return 1
    cands = load_cands()
    c = next((x for x in cands if x["id"] == cid), None)
    if not c:
        print(f"✗ 没有候选 {cid}", file=sys.stderr)
        return 1
    if c["status"] != "candidate":
        print(f"✗ 候选 {cid} 已经是 {c['status']}，不重复合并", file=sys.stderr)
        return 1
    lib = lib_for(c)                     # 手法 → 手法库.md；死路 → 死路库.md
    is_dead = c.get("kind") == "deadend"
    # 门禁：不能与目标正本既有标题重名
    # （比的是 **写进正本的那个标题**：模板里截到 60 字，所以这里也截 60 —— 
    #   否则超过 60 字的候选会绕过重名守卫，同一句话反复写进库）
    if any(norm(c["trigger"][:60]) == norm(t) for t in lib_titles(lib)):
        print(f"✗ 正本已有同义条目：「{c['trigger'][:40]}」—— 先 --drop 或改标题", file=sys.stderr)
        return 1
    score, hit, title = best_match(c, lib)
    if score >= DUP_WARN and not force_dup:
        print(f"✗ 与正本已有条目重合，默认不写入（fail-closed）。", file=sys.stderr)
        print(f"  最像的一条：「{title}」覆盖率 {score:.0%}", file=sys.stderr)
        print(f"  命中的特征词：{'、'.join(sorted(hit)[:12])}", file=sys.stderr)
        print("  → 确实重复：`--drop <id> --reviewer … --reason …`", file=sys.stderr)
        print("  → 确实不同：`--force-dup`（会连同覆盖率一起记进审计）", file=sys.stderr)
        return 1
    if force_dup and score >= DUP_WARN:
        print(f"⚠ 与「{title}」有 {score:.0%} 重合 —— 已用 --force-dup 确认，本条连同覆盖率记入审计")

    c["generalize"] = generalize
    c["pitfall"] = pitfall
    c["status"] = "merged"
    c["reviewer"] = reviewer
    c["reviewed_at"] = now()

    lib.parent.mkdir(parents=True, exist_ok=True)
    with lib.open("a", encoding="utf-8") as f:
        f.write("\n" + to_markdown(c) + "\n")
    save_cands(cands)
    audit({"action": "merge", "kind": c.get("kind") or "technique", "lib": str(lib),
           "candidate": cid, "reviewer": reviewer,
           "lib_similarity": round(score, 3), "closest": title, "force_dup": bool(force_dup),
           "project": c["project"], "trigger": c["trigger"][:80],
           "generalize": generalize, "pitfall": pitfall})
    print(f"✓ 已合并进{'死路库' if is_dead else '手法库'}：{lib}")
    print(f"  条目：{c['trigger'][:60]}　审核：{reviewer}")
    if not generalize:
        want = "「现象 + 判据」是死路库的价值所在" if is_dead else "「脱离本题还能怎么用」是手法库的价值所在"
        print(f"  ⚠ 没写 --generalize —— {want}，建议补上")
    return 0


def cmd_drop(cid: str, reviewer: str, reason) -> int:
    cands = load_cands()
    c = next((x for x in cands if x["id"] == cid), None)
    if not c:
        print(f"✗ 没有候选 {cid}", file=sys.stderr)
        return 1
    c["status"] = "dropped"
    c["reviewer"] = reviewer
    c["reviewed_at"] = now()
    c["drop_reason"] = reason or "（未写原因）"
    save_cands(cands)
    audit({"action": "drop", "candidate": cid, "reviewer": reviewer, "reason": c["drop_reason"]})
    print(f"✓ 候选 {cid} 已弃用（弃用也是结论 —— 下次不会再冒出来）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="手法库候选→审核→合并闸门")
    ap.add_argument("command", nargs="?", default=None, choices=[None, "scan"], help="scan：扫图生成候选")
    ap.add_argument("--project", help="只扫某个项目（默认全部）")
    ap.add_argument("--list", action="store_true", help="列候选")
    ap.add_argument("--show", metavar="ID", help="看候选全文")
    ap.add_argument("--merge", metavar="ID", help="审核通过并合并进手法库")
    ap.add_argument("--drop", metavar="ID", help="弃用候选")
    ap.add_argument("--reviewer", help="审核人（merge/drop 必填）")
    ap.add_argument("--generalize", help="通用化：脱离本题后还能怎么用")
    ap.add_argument("--pitfall", help="踩坑记录")
    ap.add_argument("--reason", help="弃用原因")
    ap.add_argument("--force-dup", action="store_true",
                    help="覆盖率超阈值也要写（必须是人明确确认过）")
    a = ap.parse_args()

    if a.list:
        return cmd_list()
    if a.show:
        return cmd_show(a.show)
    if a.merge:
        if not a.reviewer:
            print("✗ --merge 必须带 --reviewer <你的名字>", file=sys.stderr)
            return 1
        return cmd_merge(a.merge, a.reviewer, a.generalize, a.pitfall,
                         force_dup=getattr(a, "force_dup", False))
    if a.drop:
        if not a.reviewer:
            print("✗ --drop 必须带 --reviewer <你的名字>", file=sys.stderr)
            return 1
        return cmd_drop(a.drop, a.reviewer, a.reason)
    if a.command == "scan":
        return scan(a.project)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
