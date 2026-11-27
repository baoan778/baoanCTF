#!/usr/bin/env python3
"""从黑板（FGS）生成应急响应报告 + 时间线。

取代原技能包的 `generate_report.py`（那个读它自己的 `current_session.json`）。
本脚本**直接读 pi-lab 黑板的 `graph.json`** —— 图是唯一真相，不另存一份会话状态。

用法
----
    python3 skills/ir-response/scripts/ir-report.py <project_id>
    python3 skills/ir-response/scripts/ir-report.py <project_id> --out reports/ir-report.md
    python3 skills/ir-response/scripts/ir-report.py <project_id> --board $PI_LAB_ROOT/board
    python3 skills/ir-response/scripts/ir-report.py <project_id> --ioc-out reports/iocs.csv   # .csv / .json / .md

产出
----
    <out>              报告（套 sapper 的 templates/report.md）
    <out 同目录>/timeline.md   时间线（套 sapper 的 templates/timeline.md）
    --ioc-out <path>   单独的 IOC 表（机器可读：CSV / JSON）—— 交出去能直接进封禁流程

设计原则（和黑板一致）
--------------------
- **不编时间**：事实正文里能解析出时间戳就用它；解析不出就用该 Fact 的 `created_at`，
  并在时间线里打 `*` 标记「这是记录时间，不是事件时间」。宁可不写，不猜。
- **不升级置信度**：`inferred` / `unverified` 的 Fact 单独列一节「待坐实」，不混进 IOC 表。
- IOC 表只用**正文里逐字出现**的串（正则抽），不推断、不补全。
- **IOC 必须机器可读**：同一条 IOC 被多条 Fact 提到时合并成一行（出处并列、首见/末见时间取极值），
  导出格式**只看后缀**，不认的后缀直接报错退 1（不静默写一份没人能用的文件）。
"""
from __future__ import annotations

import argparse
import os
import csv
import json
import re
import sys
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

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
REPORT_TPL = SKILL / "templates" / "report.md"
TIMELINE_TPL = SKILL / "templates" / "timeline.md"

# ── IOC 正则（只认逐字出现的串）───────────────────────────────────────────
RE_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
RE_DOMAIN = re.compile(r"\b(?:[a-z0-9-]+\.)+(?:com|net|org|cn|xyz|top|io|me|info|biz|ru|cc|pw|onion)\b", re.I)
RE_SHA256 = re.compile(r"\b[0-9a-f]{64}\b", re.I)
RE_MD5 = re.compile(r"\b[0-9a-f]{32}\b", re.I)
RE_URL = re.compile(r"\bhttps?://[^\s\"'<>）)]+")
RE_UPATH = re.compile(r"(?<![\w/])/(?:tmp|var|etc|usr|opt|home|root|dev/shm)/[^\s\"'<>:：，,（）()\[\]「」]+")
RE_WPATH = re.compile(r"\b[A-Za-z]:\\[^\s\"'<>:：，,（）()\[\]「」]+")
RE_TS = re.compile(r"(20\d{2}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?)")

# 不该当 IOC 的噪声
NOISE_IP = {"0.0.0.0", "127.0.0.1", "255.255.255.255", "1.1.1.1"}
NOISE_DOMAIN = {"example.com", "github.com", "localhost"}


def load_graph(board: Path, pid: str) -> dict:
    f = board / "projects" / pid / "graph.json"
    if not f.exists():
        sys.exit(f"✗ 找不到黑板图：{f}\n  （先 `fgs init {pid} …` 或检查 --board）")
    return json.loads(f.read_text(encoding="utf-8"))


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


def uniq(seq):
    seen, out = set(), []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


# 处置四段从哪来：Fact 的 meta.phase 命中这些词就自动进对应段
PHASE_MAP = {
    "contain": ("contain", "containment", "隔离", "遏制", "处置"),
    "eradicate": ("eradicate", "eradication", "清除", "根治", "根除"),
    "recover": ("recover", "recovery", "恢复", "复原"),
    "lessons": ("lesson", "lessons", "复盘", "改进", "教训"),
}

PLACEHOLDER = {
    "contain": "（按现场填写：先边界后主机；高风险动作走 HITL 闸门）",
    "eradicate": "（按现场填写：清持久化 / 换凭据 / 补漏洞，逐条写明验证方式）",
    "recover": "（按现场填写：从可信备份恢复；恢复前完成取证）",
    "lessons": "（按现场填写：哪个日志源最关键、哪个检测最早能发现）",
}


def norm_ts(raw: str, fallback: str = "") -> str:
    """把时间归一到 UTC 的 `YYYY-MM-DDTHH:MM:SS`；解析不出就用 fallback。

    为什么要归一：`meta.time` 是人写的，可能是 `2026-09-01T11:12:00+08:00`。
    旧版直接截前 19 个字符 → 偏移量被丢掉，而且时间线是**按字符串排序**的，
    一条 `+08:00` 一条 `Z` 就会排错位（差 8 小时）。技能里写了「统一到 UTC」，
    那就得让脚本真的做到，不能只在文档里提。
    """
    s = (raw or "").strip()
    if not s:
        return fallback
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s[:19] if s else fallback
    if dt.tzinfo is None:
        return dt.strftime("%Y-%m-%dT%H:%M:%S")          # 没写时区：原样保留，别自作主张当 UTC
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def extract_iocs(text: str):
    """从一段正文里抽逐字出现的 IOC。返回 [(类型, 值)]。"""
    hits = []
    for m in uniq(RE_URL.findall(text)):
        hits.append(("URL", m))
    for m in uniq(RE_SHA256.findall(text)):
        hits.append(("SHA256", m))
    for m in uniq(RE_MD5.findall(text)):
        if not RE_SHA256.search(m):          # 32 位也可能是 64 位的前缀，去重
            hits.append(("MD5", m))
    for m in uniq(RE_IP.findall(text)):
        if m not in NOISE_IP:
            hits.append(("IP", m))
    for m in uniq(RE_DOMAIN.findall(text)):
        if m.lower() not in NOISE_DOMAIN:
            hits.append(("域名", m))
    for m in uniq(RE_UPATH.findall(text)):
        hits.append(("路径(Linux)", m))
    for m in uniq(RE_WPATH.findall(text)):
        hits.append(("路径(Windows)", m))
    return hits


def bullet(items, empty="（无）"):
    items = [x for x in items if str(x).strip()]
    return "\n".join(f"- {x}" for x in items) if items else f"- {empty}"


def facts_by_confidence(g: dict):
    verified, pending = [], []
    for n in g["nodes"].values():
        if n["type"] != "fact":
            continue
        (verified if n.get("confidence") == "verified" else pending).append(n)
    verified.sort(key=lambda x: x["id"])
    pending.sort(key=lambda x: x["id"])
    return verified, pending


def build(g: dict):
    nodes = g["nodes"]
    goal = next((n for n in nodes.values() if n["type"] == "goal"), None)
    hints = [n for n in nodes.values() if n["type"] == "hint"]
    steps = [n for n in nodes.values() if n["type"] == "step"]
    verified, pending = facts_by_confidence(g)

    incident = (goal or {}).get("text") or g.get("project_id", "（未填写）")
    scope = "；".join(h["text"] for h in hints if "范围" in h["text"] or "scope" in h["text"].lower()) or "（见 Hint / 待补）"
    rules = "；".join(h["text"] for h in hints if "规则" in h["text"] or "rule" in h["text"].lower()) or \
            "先取证后处置 · 最小影响 · VBR · 高风险动作走 HITL"

    # ── IOC：**优先用结构化 meta.ioc**，正则只作兜底 ──
    #   纪律：不论哪条通路，值都必须是逐字出现过的 —— meta 是人填的，正则抠的也是原文。
    #   同一条 IOC 被多条 Fact 提到 → **合并成一条**（出处并列、首见/末见取极值），
    #   否则交出去的表里同个 IP 会出现五行，封禁的人还得自己合并。
    iocs: list[dict] = []
    index: dict[tuple, dict] = {}
    for n in verified:
        meta = n.get("meta") or {}
        pairs = []
        for it in meta.get("ioc") or []:
            kind, _, val = it.partition("=")
            pairs.append((kind.strip() or "IOC", val.strip()))
        if not pairs:
            pairs = extract_iocs(n["text"])
            structured = False
        else:
            structured = True
        when = norm_ts(meta.get("time") or "", (n.get("created_at") or "")[:19])
        for kind, val in pairs:
            if not val:
                continue
            # 去重键把「路径(Linux)/路径(Windows)」归一，否则同一路径会以两种类型各出现一次
            key = ("路径" if kind.startswith("路径") else kind, val.lower())
            rec = index.get(key)
            if rec is None:
                rec = {"type": kind, "value": val, "first_seen": when, "last_seen": when,
                       "facts": [], "phases": [], "tools": [], "targets": [],
                       "origin": "meta" if structured else "正文正则"}
                index[key] = rec
                iocs.append(rec)
            elif structured and rec["origin"] != "meta":
                rec["origin"] = "meta"          # 结构化证据优先：同一条被手填过就标 meta
            if n["id"] not in rec["facts"]:
                rec["facts"].append(n["id"])
            for field in ("phase", "tool", "target"):
                v = meta.get(field)
                if isinstance(v, str) and v.strip() and v.strip() not in rec[field + "s"]:
                    rec[field + "s"].append(v.strip())
            if when:
                if not rec["first_seen"] or when < rec["first_seen"]:
                    rec["first_seen"] = when
                if not rec["last_seen"] or when > rec["last_seen"]:
                    rec["last_seen"] = when

    ioc_rows = []
    for rec in iocs:
        span = rec["first_seen"] if rec["first_seen"] == rec["last_seen"] \
            else f"{rec['first_seen']} → {rec['last_seen']}"
        ioc_rows.append(f"| {rec['type']} | `{rec['value']}` | {','.join(rec['facts'])} | {span} | verified | {rec['origin']} |")

    # ── 时间线：meta.time > 正文时间戳 > created_at（后者打 * 标记"这是记录时间"）──
    tl = []
    for n in verified + pending:
        meta = n.get("meta") or {}
        if meta.get("time"):
            tl.append((norm_ts(meta["time"]), n["id"], n["type"], n["text"]))
            continue
        m = RE_TS.search(n["text"])
        if m:
            tl.append((norm_ts(m.group(1).replace(" ", "T")) or m.group(1).replace(" ", "T"),
                       n["id"], n["type"], n["text"]))
        else:
            tl.append((n.get("created_at", "")[:19] + "*", n["id"], n["type"], n["text"]))
    tl.sort(key=lambda x: x[0])

    # ── 处置四段：有 phase 的 Fact 自动归位，没写就留占位（不编）──
    phase_notes = {k: [] for k in PHASE_MAP}
    for n in verified:
        ph = str((n.get("meta") or {}).get("phase") or "").lower()
        if not ph:
            continue
        for key, words in PHASE_MAP.items():
            if any(w in ph for w in words):
                phase_notes[key].append(f"`{n['id']}` {n['text']}")

    # ── 答案/flag 形态 ──
    answers = [f"{n['id']}: {n['text']}" for n in verified
               if re.search(r"flag\{|[A-Za-z0-9_]{3,}FLAG\{|问题\d|答案[=：]", n["text"], re.I)]

    open_steps = [s for s in steps if s["status"] in ("open", "claimed")]
    done_steps = [s for s in steps if s["status"] == "done"]

    return {
        "incident": incident, "scope": scope, "rules": rules,
        "verified": verified, "pending": pending, "ioc_rows": ioc_rows, "iocs": iocs,
        "timeline": tl, "answers": answers, "phase_notes": phase_notes,
        "open_steps": open_steps, "done_steps": done_steps,
        "hints": hints, "solved": g.get("solved"), "pid": g.get("project_id"),
        "rev": g.get("revision"),
    }


def render_report(b: dict) -> str:
    tpl = REPORT_TPL.read_text(encoding="utf-8") if REPORT_TPL.exists() else "# 应急响应报告\n{{INCIDENT_NAME}}\n"
    v, p = b["verified"], b["pending"]

    rep = {
        "INCIDENT_NAME": b["incident"],
        "DATE": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "SEVERITY": "（待定级 —— 人工判断，不由脚本推断）",
        "MODE": "enterprise-ir",
        "TIMEZONE": "UTC（本地时间请按日志源时区自行换算）",
        "SCOPE": b["scope"],
        "RULES": b["rules"],
        "SOURCES": bullet([h["text"] for h in b["hints"] if "来源" in h["text"] or "告警" in h["text"]]),
        "STAKEHOLDERS": bullet([h["text"] for h in b["hints"] if "干系" in h["text"] or "负责人" in h["text"]]),
        "ASSETS": bullet([h["text"] for h in b["hints"] if "资产" in h["text"] or re.search(r"\d+\.\d+\.\d+\.\d+", h["text"])]),
        "SUMMARY_NOTES": bullet([f"`{n['id']}` {n['text']}" for n in v[-12:]]),
        "IOCS": ("| 类型 | 值 | 出处(Fact) | 时间 | 置信度 | 来源 |\n|---|---|---|---|---|---|\n" + "\n".join(b["ioc_rows"]))
                if b["ioc_rows"] else "-（未从 verified Fact 中抽出 IOC；先补事实，别猜）",
        "FLAGS": bullet(b["answers"], "（不适用）"),
        "EVIDENCE": bullet([f"`{n['id']}` {n.get('evidence') or n['text']}" for n in v]),
        "ARTIFACTS": bullet([f"`{n['id']}` {n.get('evidence')}" for n in v if n.get("evidence")]),
        "ACTIONS": bullet([f"`{n['id']}` [{n['status']}] {n['text']}" for n in b["done_steps"]]),
        "PHASES": bullet([f"`{n['id']}` [{m.get('phase')}·{m.get('action') or '-'}] {n['text']}"
                           for n in v for m in [n.get("meta") or {}] if m.get("phase")],
                          "（Fact 没带 --phase；补上后这里会自动按阶段分组）"),
        "CONTAINMENT": bullet(b["phase_notes"]["contain"], PLACEHOLDER["contain"]),
        "ERADICATION": bullet(b["phase_notes"]["eradicate"], PLACEHOLDER["eradicate"]),
        "RECOVERY": bullet(b["phase_notes"]["recover"], PLACEHOLDER["recover"]),
        "LESSONS": bullet(b["phase_notes"]["lessons"], PLACEHOLDER["lessons"]),
        "NEXT_STEPS": bullet([f"`{s['id']}` [{s['status']}] {s['text']}" for s in b["open_steps"]],
                             "（没有 open Step）"),
    }
    if p:
        rep["EVIDENCE"] += "\n\n### 待坐实（inferred / unverified —— 不能当结论）\n" + \
            bullet([f"`{n['id']}` [{n.get('confidence')}] {n['text']}" for n in p])
        rep["IOCS"] += "\n\n> ⚠️ 另有 %d 条 `inferred`/`unverified` Fact 未进入上表 —— 坐实后自行补入。" % len(p)

    for k, val in rep.items():
        tpl = tpl.replace("{{" + k + "}}", str(val))
    return tpl


def render_timeline(b: dict) -> str:
    tpl = TIMELINE_TPL.read_text(encoding="utf-8") if TIMELINE_TPL.exists() else "# 时间线\n{{INCIDENT_NAME}}\n{{TIMELINE}}"
    rows = []
    for ts, nid, ntype, text in b["timeline"]:
        mark = "⚠" if ts.endswith("*") else " "
        rows.append(f"{mark} {ts}  {ntype} `{nid}`  {text}")
    tpl = tpl.replace("{{INCIDENT_NAME}}", b["incident"])
    tpl = tpl.replace("{{TIMELINE}}", bullet(rows, "（图里还没有 Fact）"))
    return tpl


IOC_FIELDS = ["type", "value", "first_seen", "last_seen", "facts", "phases", "tools", "targets", "origin"]


def export_iocs(b: dict, path: Path) -> str:
    """导出机器可读的 IOC 表（交付物：报告给人看，这张表给设备吃）。

    判据：**只认后缀，不猜格式** —— 不认识的后缀直接报错退 1。
    宁可让人显式说要什么，也不要静默写一份没人能用的文件（fail-closed）。
    CSV 用 utf-8-sig：Windows 上 Excel 双击打开中文表头才不乱码。
    """
    rows = b["iocs"]
    suffix = path.suffix.lower()
    path.parent.mkdir(parents=True, exist_ok=True)
    if suffix == ".csv":
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(IOC_FIELDS)
            for r in rows:
                w.writerow([r["type"], r["value"], r["first_seen"], r["last_seen"],
                            ";".join(r["facts"]), ";".join(r["phases"]),
                            ";".join(r["tools"]), ";".join(r["targets"]), r["origin"]])
        return f"CSV {len(rows)} 条"
    if suffix == ".json":
        payload = {
            "project": b["pid"],
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "count": len(rows),
            "iocs": rows,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return f"JSON {len(rows)} 条"
    if suffix == ".md":
        path.write_text("| 类型 | 值 | 出处(Fact) | 时间 | 置信度 | 来源 |\n|---|---|---|---|---|---|\n"
                        + "\n".join(b["ioc_rows"]) + "\n", encoding="utf-8")
        return f"Markdown {len(rows)} 条"
    sys.exit(f"✗ 不认的 IOC 导出后缀：{suffix or '（没有扩展名）'} —— 只支持 .csv / .json / .md")


def main() -> int:
    ap = argparse.ArgumentParser(description="从 FGS 黑板生成应急响应报告 + 时间线")
    ap.add_argument("project", help="黑板 project_id（board/projects/<pid>/）")
    ap.add_argument("--board", default=os.environ.get("PI_LAB_ROOT") or Path(__file__).resolve().parents[3] / "board", type=str, help="board 目录")
    ap.add_argument("--out", default=None, help="报告输出路径（默认 reports/<pid>-ir-report.md）")
    ap.add_argument("--ioc-out", default=None,
                    help="单独导出 IOC 表：.csv（utf-8-sig，Excel 友好）/ .json / .md")
    a = ap.parse_args()

    g = load_graph(Path(a.board), a.project)
    b = build(g)
    out = Path(a.out) if a.out else Path(f"reports/{a.project}-ir-report.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_report(b), encoding="utf-8")
    tl = out.parent / "timeline.md"
    tl.write_text(render_timeline(b), encoding="utf-8")

    print(f"✓ 报告  ：{out}")
    print(f"✓ 时间线：{tl}")
    if a.ioc_out:
        ipath = Path(a.ioc_out)
        print(f"✓ IOC 表：{ipath}（{export_iocs(b, ipath)}）")
    print(f"  事实 {len(b['verified'])} verified / {len(b['pending'])} 待坐实 "
          f"· IOC {len(b['iocs'])} 条 · open step {len(b['open_steps'])} 个")
    if b["pending"]:
        print(f"  ⚠ 有 {len(b['pending'])} 条不是 verified，报告里已单列，未混入 IOC 表")
    return 0


if __name__ == "__main__":
    sys.exit(main())
