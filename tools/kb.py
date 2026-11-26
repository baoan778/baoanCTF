#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb.py —— pi-lab 本地知识库（离线，零外部依赖）：**参考资料 + 手法库 + PoC 库一个入口**。

为什么要有它（2026-10-12）
--------------------------
本机已经有三坨好东西，但**没有检索面**，只能靠人 `grep` + 猜目录：
  · `skills/*/references/` 185 篇（题型打法笔记）
  · `tools/knowledge/`     194 篇（NOPTrace 应急手册 / RiftX 手法与 payload）
  · `docs/writeups/`        跨题手法库 + 历年 WP
  · `tools/ctfpoc/`        **1.1 万条离线 PoC**（已有 `poc` 命令，但它是独立入口）
`AGENTS.md` 自己写着「只读当前路线那几篇，别整目录吞」——**没有检索就只能靠猜哪几篇**。
参考物是《CTF Agent 调优》那篇的做法（Crawl4AI + Qdrant + 每类一个 collection），
但**去掉了向量库**：我们要查的是「怎么打」的方法论与 payload，**词法检索够用**，
而且离线、零 API key、零外网（向量方案要 embedding key，且 CTFusion 2026 实测「检索=污染通道」——
所以本库**只收方法论/reference/PoC，不收答案**，检索结果一律带来源路径与行号，当证据用）。

三种东西，一个接口（对应那篇里的 crawl / search / list_sources 三件套）
----------------------------------------------------------------------
  入库：`kb build`（增量：只重算改动过的文件）
  检索：`kb search "泛微 e-cology" --cat web --top 5`
  取全文：`kb read <path> --lines 120-180` ／ PoC 原文：`kb poc "nacos" --show 2`

设计取向（每条都有理由，别"顺手优化"掉）
----------------------------------------
  · **索引放快盘**（默认 `$HOME/pi-lab-kb`，`PI_LAB_KB_DIR` 可改）：仓库在 `/mnt/d` 是 9p/drvfs，
    小文件读 ~5MB/s，索引若放仓里每次检索都要从慢盘读；索引是**派生物**，不进 git。
    **不写死 `/root`**：非 root 用户（发布包/WSL 普通账号）读不到 `/root`，会整库不可用。
  · **分块 ≤ ~1800 字符**：那篇的 chunk_size 建议 500 token；本仓纪律是「大输出不许整读」，
    所以返回的是**片段 + 行号指针**，不是整个文件。
  · **中文走 bigram**：中文文档占多数，单字召回太脏（"的/是"），bigram + 英文词权重够用。
  · **CVE/CNVD 走精确通道**：`CVE-2021-44228` 这种要么整条命中要么不算，别拆成词。
  · **PoC 不复制**：`poc` 命令与 `tools/ctfpoc/poc-index.json` 是唯一真相，
    这里只做进索引 + 统一检索 + 原文跳转；**改 PoC 去改源头**。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 仓库根：$PI_LAB_ROOT → 从本文件位置推（tools/ 一级深），不写死绝对路径
LAB = Path(os.environ.get("PI_LAB_ROOT") or Path(__file__).resolve().parent.parent)


def _default_kb_dir() -> Path:
    """索引目录默认值：**$HOME/pi-lab-kb**（快盘），不是写死的 `/root/pi-lab-kb`。

    ★ 2026-11-02 修（一类问题，不是补丁）：原来写死 `/root/pi-lab-kb`，只有 root 能读。
      非 root 用户（发布包 / WSL 普通账号，实测 `breakingbad`）一跑 `kb` 就
      `PermissionError: [Errno 13] /root/pi-lab-kb/index.jsonl` —— 而且**每条**子命令都炸，
      知识库整个不可用。`sudo` 也不是解法：索引是派生物，重建/检索都不该要提权。

    为什么 `$HOME` 是**泛化而不是新增特例**：
      · root 的 $HOME 就是 `/root` → 容器里解析结果与旧行为**逐字节相同**，零漂移；
      · 非 root 用户落到自己的 `$HOME`，天然可写，不需要 sudo，也不需要 export 任何变量；
      · 语义仍然是「放快盘上的派生目录」——/root 与 /home 在标准 Linux 里同为 ext4。

    优先级：`$PI_LAB_KB_DIR`（显式覆盖，测试与特殊布局用）→ `$HOME/pi-lab-kb`。
    $HOME 也取不到时（极端 cron/su 环境）才回落到 `/root`，保持旧行为。
    """
    env = os.environ.get("PI_LAB_KB_DIR")
    if env:
        return Path(env)
    home = os.environ.get("HOME") or os.path.expanduser("~")
    if home and home != "~":
        return Path(home) / "pi-lab-kb"
    return Path("/root/pi-lab-kb")


KB_DIR = _default_kb_dir()
INDEX = KB_DIR / "index.jsonl"
META = KB_DIR / "meta.json"
POC_INDEX = LAB / "tools" / "ctfpoc" / "poc-index.json"
POC_ROOT = LAB / "tools" / "ctfpoc" / "pocs"

CHUNK_CHARS = 1800          # 单块上限（≈500 token）
MIN_CHUNK_CHARS = 40        # 小于这个的块**并入邻块**（不是丢弃）
MAX_FILE_BYTES = 3 * 1024 * 1024
DEFAULT_TOP = 5
EXCERPT_CHARS = 400
# 分块/打分逻辑的版本号：**改了切块或 token 规则就必须 +1**，否则增量重建会因为
# mtime/size 没变而全量复用旧块（2026-10-12 自己踩过一次：改了 SKILL.md 的标题基，
# 重建却报「0 个文件重建」）
CHUNKER_VERSION = 3

# ── 分类：题型目录名 → 我们检索时用的 cat ─────────────────────────────
SKILL_CAT = {
    "ctf-web": "web", "ctf-pwn": "pwn", "ctf-reverse": "reverse", "ctf-crypto": "crypto",
    "ctf-misc": "misc", "ctf-forensics": "forensics", "ctf-osint": "osint",
    "ctf-malware": "malware", "ctf-ai-ml": "ai-ml", "ctf-binary": "binary",
    "ctf-kit": "kit", "ir-response": "ir", "src-recon": "src", "awdp": "awdp",
    "fgs-orchestrate": "meta", "wp-writer": "meta",
}
# RiftX 那批 skill 名的前缀 → cat（不知道的落 notes，宁可粗也不要错标）
RIFTX_PREFIX_CAT = [
    ("exploit-", "web"), ("recon-", "src"), ("api-", "web"), ("security-", "kit"),
    ("post-exploit", "src"), ("network", "src"), ("cloud", "src"), ("container", "src"),
]

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-\.]{1,}|[0-9][0-9a-zA-Z\.\-]{1,}|[\u3400-\u9fff]")
ID_RE = re.compile(r"\b(CVE-\d{4}-\d{3,7}|CNVD-\d{4}-\d{4,6})\b", re.I)
HEAD_RE = re.compile(r"^(#{1,4})\s+(.*)$")


# ────────────────────────── 分词与打分 ──────────────────────────

def terms_of(text: str) -> list[str]:
    """英文/数字按词，中文加 bigram（单字噪声太大；纯中文短查询也能命中）。"""
    raw = TOKEN_RE.findall(text or "")
    out: list[str] = []
    cjk: list[str] = []
    for t in raw:
        low = t.casefold()
        if "\u3400" <= low <= "\u9fff":
            cjk.append(low)
            out.append(low)
        else:
            out.append(low)
    for i in range(len(cjk) - 1):
        out.append(cjk[i] + cjk[i + 1])
    return out


def ids_of(text: str) -> set[str]:
    return {m.upper() for m in ID_RE.findall(text or "")}


def make_doc(kind: str, cat: str, path: str, title: str, body: str,
             lines: tuple[int, int], extra: dict | None = None) -> dict:
    """一条索引记录：token 频次（分字段加权）+ 指针 + 展示用摘要。"""
    title_t = Counter(terms_of(title))
    tag_t = Counter(terms_of(" ".join((extra or {}).get("tags", []) if isinstance((extra or {}).get("tags"), list)
                                    else [(extra or {}).get("tags", "")])))
    body_t = Counter(terms_of(body))
    merged = Counter()
    for counter, weight in ((title_t, 3), (tag_t, 2), (body_t, 1)):
        for t, n in counter.items():
            merged[t] += n * weight
    doc = {
        "kind": kind, "cat": cat, "path": path, "title": title,
        "lines": list(lines), "len": sum(merged.values()) or 1,
        "tf": dict(merged), "ids": sorted(ids_of(title + " " + body)),
        "head": re.sub(r"\s+", " ", body.strip())[:EXCERPT_CHARS],
    }
    if extra:
        doc.update({k: v for k, v in extra.items() if v not in (None, "", [], {})})
    return doc


def bm25(query_terms: Counter, doc: dict, df: dict, n_docs: int, avg_len: float,
         k1: float = 1.2, b: float = 0.75) -> float:
    score = 0.0
    for term, qn in query_terms.items():
        f = doc["tf"].get(term)
        if not f:
            continue
        idf = math.log(1 + (n_docs - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5))
        score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * doc["len"] / avg_len)) * (1 + math.log(qn))
    return score


def excerpt_at(body: str, query: str, width: int = EXCERPT_CHARS) -> str:
    """给命中位置附近的片段（中文按字找，英文大小写不敏感）。"""
    flat = re.sub(r"[ \t]+", " ", body.strip())
    if len(flat) <= width:
        return flat
    pos = flat.casefold().find(query.casefold()) if query else -1
    if pos < 0:
        return flat[:width] + " …"
    start = max(0, pos - width // 3)
    return ("…" if start else "") + flat[start:start + width] + ("…" if start + width < len(flat) else "")


# ────────────────────────── 入库 ──────────────────────────

def _cat_for_skill(name: str) -> str:
    return SKILL_CAT.get(name, "meta")


def _cat_for_riftx(name: str) -> str:
    for pref, cat in RIFTX_PREFIX_CAT:
        if name.startswith(pref):
            return cat
    return "notes"


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(LAB))
    except ValueError:
        return str(p)


def chunk_markdown(path: Path, kind: str, cat: str) -> list[dict]:
    # lint:ok 这里的 path 只来自 cmd_build 内部遍历（KB_SRC/knowledge 目录），不是用户输入；
    #          下面的 MAX_FILE_BYTES 截断就是本规则要的「限长」。
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > MAX_FILE_BYTES:
        text = text[:MAX_FILE_BYTES]
    lines = text.splitlines()
    # 标题基：SKILL.md 的 path.stem 是 "SKILL"（对检索毫无信息） → 用目录名（ctf-pwn 等）
    base = path.parent.name if path.name == "SKILL.md" else path.stem
    stack: list[tuple[int, str]] = [(1, base)]
    raw: list[tuple[str, str, int, int]] = []
    buf: list[str] = []
    start = 1

    def flush(end_line: int) -> None:
        nonlocal buf, start
        body = "\n".join(buf).strip()
        buf = []
        if not body:
            return
        raw.append((" › ".join(t for _, t in stack) or base, body, start, end_line))

    for i, line in enumerate(lines, 1):
        m = HEAD_RE.match(line)
        if m:
            flush(i - 1)
            level = len(m.group(1))
            while stack and stack[-1][0] >= level and len(stack) > 1:
                stack.pop()
            stack.append((level, m.group(2).strip()))
            buf = [line]
            start = i
            continue
        buf.append(line)
        if sum(len(x) + 1 for x in buf) > CHUNK_CHARS:
            flush(i)
            start = i + 1
    flush(len(lines))

    # ★ 小块**合并而不是丢弃**：早先直接丢掉 <MIN_CHUNK_CHARS 的块，结果标题/前言这类短块
    #   （中文尤其短）被静默吞掉 —— 极端情况整份文件变成 0 块，「索引里根本没有这个文件」
    #   却没人发现（2026-10-12 回归测试抓到：fixture 的 SKILL.md 整份消失）。
    merged: list[tuple[str, str, int, int]] = []
    for title, body, lo, hi in raw:
        if merged and len(merged[-1][1]) < MIN_CHUNK_CHARS:
            ptitle, pbody, plo, _phi = merged.pop()
            merged.append((ptitle, pbody + "\n" + body, plo, hi))
        else:
            merged.append((title, body, lo, hi))
    if len(merged) >= 2 and len(merged[0][1]) < MIN_CHUNK_CHARS:
        t0, b0, l0, _h0 = merged.pop(0)
        t1, b1, _l1, h1 = merged.pop(0)
        merged.insert(0, (t1, b0 + "\n" + b1, l0, h1))

    docs: list[dict] = []
    for title, body, lo, hi in merged:
        if len(body) < MIN_CHUNK_CHARS:      # 整个文件都短 → 丢（不值得进索引）
            continue
        docs.append(make_doc(kind, cat, _rel(path), title, body, (lo, hi)))
    return docs


def collect_sources() -> list[tuple[Path, str, str]]:
    out: list[tuple[Path, str, str]] = []
    # ⚠ SKILL.md 与 references/ **分开遍历**：早先写成「先 glob SKILL.md 再取它的 references/」，
    # 结果**没有 SKILL.md 的目录下的 references 会被静默丢掉**（2026-10-12 回归测试抓到的）。
    for skill_md in sorted((LAB / "skills").glob("*/SKILL.md")):
        out.append((skill_md, "skill", _cat_for_skill(skill_md.parent.name)))
    for ref in sorted((LAB / "skills").glob("*/references/*.md")):
        cat = _cat_for_skill(ref.parent.parent.name)
        out.append((ref, "ref", cat))
    for md in sorted((LAB / "tools" / "knowledge").rglob("*.md")):
        if md.name == "INDEX.md":
            continue
        # ctf-wp 是**独立来源**（kind=ctfwp），别在这里再收一遍，否则同一篇进两次索引
        # （2026-10-12 踩过两次：先是 cn-wp-2025，改名成 ctf-wp 后又忘了同步这里）
        if "ctf-wp" in md.parts:
            continue
        parts = md.relative_to(LAB / "tools" / "knowledge").parts
        if parts[0] == "noptrace":
            cat = "ir"
        elif parts[0] == "riftx" and len(parts) > 2:
            cat = _cat_for_riftx(parts[2])
        else:
            cat = "notes"
        out.append((md, "notes", cat))
    for md in sorted((LAB / "docs" / "writeups").rglob("*.md")):
        out.append((md, "writeup", _cat_for_skill("wp-writer") if md.name == "手法库.md" else "wp"))
    # 我们自己的 md 手册（2026-10-12 加）：竞赛模式 / 能力边界 / 工程纪律 / 并行工作 / WP流程 …
    # ⚠ 排除 `状态快照.md`：它是**每次收尾都改**的续接文档（churn 高、内容是仓库运维不是解题知识），
    #   放进来只会让 `kb stale` 天天报红。
    for md in sorted((LAB / "docs").glob("*.md")):
        if md.name in {"状态快照.md", "架构设计.md", "桌面交付物总览-给dsh.md"}:
            continue
        out.append((md, "doc", "meta"))
    # WP 仓库（`tools/knowledge/ctf-wp/`，2026-10-12 把 2025+ 抓取页与 2026 研读笔记合到一块）：
    # 结构 `<年>/<方向>/x.md`（方向 = web/pwn/crypto/reverse/misc/collections/review）→ cat 取方向。
    # **`raw/` 不收**（6.3MB 抓下来的原始材料，会稀释检索）；根目录的 INDEX/PROVENANCE 也不收。
    for md in sorted((LAB / "tools" / "knowledge" / "ctf-wp").rglob("*.md")):
        rel = md.relative_to(LAB / "tools" / "knowledge" / "ctf-wp")
        if "raw" in rel.parts or len(rel.parts) < 3:   # <年>/<方向>/<文件>
            continue
        out.append((md, "ctfwp", rel.parts[1]))
    return out


def load_pocs() -> list[dict]:
    if not POC_INDEX.exists():
        return []
    data = json.loads(POC_INDEX.read_text(encoding="utf-8"))
    docs = []
    for it in data.get("items", []):
        ids = list(it.get("cve") or []) + list(it.get("cnvd") or [])
        txt = " ".join(str(x) for x in (
            it.get("name"), it.get("id"), it.get("tags"), it.get("poc_class"), it.get("poc_type"),
            it.get("method"), it.get("path"), it.get("expression"), " ".join(map(str, ids)),
            it.get("lib"), it.get("file"),
        ) if x)
        docs.append(make_doc(
            "poc", "poc", str(it.get("file") or ""), str(it.get("name") or it.get("id") or "?"), txt, (0, 0),
            extra={
                "lib": it.get("lib"), "ids": sorted(set(ids)) or None,
                "usable": bool(it.get("expression")) or bool(it.get("path")),
                "expr": bool(it.get("expression")), "severity": it.get("severity"),
                "poc_path": it.get("path"), "file": it.get("file"), "poc_id": it.get("id"),
            },
        ))
    return docs


def cmd_build(a) -> int:
    t0 = time.time()
    KB_DIR.mkdir(parents=True, exist_ok=True)
    sources = collect_sources()
    # 源清单指纹：来源集合、kind、cat 任一变化 → 全量重建。
    # 为什么必须有它：增量是按 path 缓存块的，**同一路径换了 kind 时会复用旧记录**
    #（2026-10-12 踩过：cn-wp-2025 被我同时收进 notes 和 cnwp，去掉重复来源后
    #  重建却报「0 个文件重建」，索引里仍是旧的双份）。
    src_map = hashlib.sha1("\n".join(f"{_rel(p)}|{k}|{c}" for p, k, c in sources).encode()).hexdigest()
    old = {}
    old_ver = 0
    prev_map = None
    if META.exists() and not a.force:
        try:
            _m = json.loads(META.read_text(encoding="utf-8"))
            old = (_m.get("files") or {})
            old_ver = int(_m.get("chunker_version") or 0)
            prev_map = _m.get("source_map")
        except Exception:
            old = {}
    if old_ver != CHUNKER_VERSION:
        print(f"· 分块规则变了（chunker v{old_ver} → v{CHUNKER_VERSION}）→ 全量重建")
        old = {}
    elif prev_map != src_map:
        print("· 来源清单变了（新增/删除来源，或改了 kind/cat）→ 全量重建")
        old = {}
    docs: list[dict] = []
    files: dict[str, list] = {}
    reused = built = 0
    cached: dict[str, list[dict]] = {}
    if old and INDEX.exists():
        for line in INDEX.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            cached.setdefault(d["path"], []).append(d)
    if not INDEX.exists():
        old = {}                       # 有 meta 没索引 = 半成品 → 全量
    for path, kind, cat in sources:
        try:
            st = path.stat()
        except OSError:
            continue
        key = _rel(path)
        sig = [int(st.st_mtime), st.st_size]
        # ★ 只要签名没变就复用（**即使该文件产出 0 块**）——
        #   早先还要求 `key in cached`，结果那些"短到不够建块"的文件**每次都被判成改动**，
        #   增量重建永远不是 0（2026-10-12 回归测试抓到）。chunker_version 已负责"规则变了"那种情况。
        if old.get(key) == sig:
            # ★ 复用时要**按 (kind, cat) 过滤**：同一路径换了来源分类时，
            #   旧记录（kind/cat 不同）绝不能一起塞回去 —— 否则索引里会留下
            #   「同一篇进两份」的脏记录（2026-10-12 踩：cn-wp-2025 既是 notes 又是 cnwp）
            reuse = [d for d in cached.get(key, []) if d["kind"] == kind and d["cat"] == cat]
            if reuse:
                docs.extend(reuse)
                files[key] = sig
                reused += 1
                continue
        docs.extend(chunk_markdown(path, kind, cat))
        files[key] = sig
        built += 1
    pocs = load_pocs()
    docs.extend(pocs)
    with INDEX.open("w", encoding="utf-8") as fh:
        for d in docs:
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    df = Counter()
    for d in docs:
        for t in d["tf"]:
            df[t] += 1
    by_kind = Counter(d["kind"] for d in docs)
    by_cat = Counter(d["cat"] for d in docs)
    META.write_text(json.dumps({
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "chunker_version": CHUNKER_VERSION,
        "source_map": src_map,
        "elapsed_s": round(time.time() - t0, 1),
        "docs": len(docs), "by_kind": dict(by_kind), "by_cat": dict(by_cat),
        "df": dict(df.most_common(40000)), "n_docs": len(docs),
        "avg_len": sum(d["len"] for d in docs) / max(1, len(docs)),
        "files": files, "files_built": built, "files_reused": reused,
    }, ensure_ascii=False), encoding="utf-8")  # ★ 2026-11-07：必须显式 utf-8。Windows 上
    # Path.write_text 默认 locale 编码（中文系统=GBK），df 里只要有一个非 GBK 字符
    # （实测 '²' U+00B2）就 UnicodeEncodeError —— 索引建到 99% 才炸，前面白跑。
    print(f"✅ 索引 {len(docs)} 块（{built} 个文件重建 / {reused} 个复用）→ {INDEX}")
    print(f"   分类：{dict(by_cat.most_common())}")
    print(f"   来源：{dict(by_kind.most_common())}  用时 {round(time.time()-t0,1)}s")
    return 0


def load_index() -> tuple[list[dict], dict]:
    if not (INDEX.exists() and META.exists()):
        sys.exit("kb: 还没有索引 —— 先跑 `kb build`")
    meta = json.loads(META.read_text(encoding="utf-8"))
    docs = []
    for line in INDEX.read_text(encoding="utf-8").splitlines():
        if line.strip():
            docs.append(json.loads(line))
    return docs, meta


# ────────────────────────── 检索 ──────────────────────────

def search(docs: list[dict], meta: dict, query: str, cat: str | None, kinds: set[str] | None,
           top: int, sort_poc: bool = True, path_like: str | None = None) -> list[dict]:
    qterms = Counter(terms_of(query))
    qids = ids_of(query)
    df, n_docs, avg_len = meta["df"], meta["n_docs"], meta["avg_len"]
    hits = []
    for d in docs:
        if kinds and d["kind"] not in kinds:
            continue
        if cat and d["cat"] != cat:
            continue
        if path_like and path_like not in d["path"]:
            continue
        if qids:
            # 查询里有 CVE/CNVD：编号必须命中（标题或 ids 字段），否则这条不算
            if not (qids & set(d.get("ids") or []) or any(i in d["title"].upper() for i in qids)):
                continue
        s = bm25(qterms, d, df, n_docs, avg_len)
        if not s:
            continue
        if query.casefold() in d["title"].casefold():
            s += 6.0                                  # 标题整串命中，权重最高
        if d["kind"] == "poc" and (sort_poc and d.get("expr")):
            s += 1.5                                  # 带判定式的 PoC 优先（能直接照抄）
        hits.append((s, d))
    hits.sort(key=lambda x: -x[0])
    return hits[:top]


def cmd_search(a) -> int:
    docs, meta = load_index()
    kinds = set(a.kind.split(",")) if a.kind else None
    hits = search(docs, meta, a.query, a.cat, kinds, a.top, not a.no_poc_bias, a.path)
    if a.json:
        print(json.dumps([{"score": round(s, 3), **d} for s, d in hits], ensure_ascii=False, indent=1))
        return 0
    if not hits:
        print(f"无命中：「{a.query}」")
        print("提示：分类过滤用 --cat（web/pwn/reverse/crypto/misc/forensics/osint/malware/ai-ml/ir/src/poc…）；"
              "换英文别名（泛微→weaver、致远→seeyon、通达→tongda）；PoC 优先 `poc \"<产品>\"`。")
        return 0
    print(f"命中 {len(hits)} 条（索引 {meta['docs']} 块 / 建于 {meta['built_at']}）\n")
    for i, (s, d) in enumerate(hits):
        loc = f"{d['path']}:{d['lines'][0]}-{d['lines'][1]}" if d["lines"][1] else d["path"]
        tag = f"{d['kind']}/{d['cat']}"
        if d["kind"] == "poc":
            tag += "/可照抄" if d.get("expr") else ("/有请求" if d.get("usable") else "/仅名单")
        print(f"[{i}] {s:6.1f} {tag:24s} {d['title'][:60]}")
        print(f"      {loc}")
        if d["kind"] == "poc":
            if d.get("poc_path"):
                print(f"      path: {d['poc_path'][:110]}")
            if d.get("expr"):
                print(f"      expr: {str(d['expr'])[:110]}")
            if d.get("ids"):
                print(f"      ids : {', '.join(d['ids'])[:100]}")
        else:
            print(f"      {excerpt_at(d.get('head', ''), a.query, a.excerpt)}")
        print()
    print("取全文：`kb read <path> --lines A-B`；PoC 原文：`kb poc \"<关键词>\" --show 2`")
    return 0


def cmd_read(a) -> int:
    docs, meta = load_index()
    target = a.target
    outs: list[tuple[str, tuple[int, int] | None]] = []
    if target.isdigit():
        best = search(docs, meta, a.query or "", None, None, int(target) + 1)
        if len(best) <= int(target):
            sys.exit(f"kb read: 编号 {target} 越界（先 `kb search`，编号要配 --query）")
        d = best[int(target)][1]
        outs.append((d["path"], tuple(d["lines"]) if d["lines"][1] else None))
    else:
        outs.append((target, None))
    for rel, span in outs:
        # 索引里的 file 字段写成相对仓库根的 `pocs/xxx`（2026-10 的布局），而 PoC 原文
        # 实际收在 tools/ctfpoc/pocs/xxx —— 两种写法都要能走通。
        # ★ 2026-11-18 修：原来这里是
        #     if rel.startswith(str(POC_ROOT)) or rel.startswith("tools/ctfpoc"):
        #         p = POC_ROOT / rel
        #   —— 相对路径走到这一支会拼成 tools/ctfpoc/pocs/tools/ctfpoc/pocs/xxx（永不存在），
        #   else 支的兜底又是 POC_ROOT / rel = tools/ctfpoc/pocs/pocs/xxx（同样不存在）。
        #   当时根 pocs/ 还在，`LAB / rel` 恰好命中，这两个错被完全盖住。
        #   2026-11-18 删掉根 pocs/ 的重复目录后它们才暴露，所以这里一并改对：
        #   · 绝对路径 → 原样用
        #   · `pocs/<x>`  → LAB/pocs/<x>，不存在再试 POC_ROOT/<x>
        #   · `tools/ctfpoc/...` → LAB/rel
        #   · 其余 → LAB/rel
        _rp = rel.replace("\\", "/")
        _p = Path(rel)
        _tried: list[Path] = []
        if _p.is_absolute():
            p = _p
            _tried.append(p)
        elif _rp.startswith("pocs/"):
            p = LAB / _rp
            _tried.append(p)
            if not p.exists():
                _alt = POC_ROOT / _rp[len("pocs/"):]
                _tried.append(_alt)
                if _alt.exists():
                    p = _alt
        else:
            p = LAB / _rp
            _tried.append(p)
        if not p.exists():
            # 索引里 478 条是**死链**（原文从来没进过库，只有名字与标签），这里如实报出试过哪些
            print("!! 不存在：" + "  |  也试过：".join(str(x) for x in _tried))
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        if a.lines:
            m = re.match(r"^(\d+)\s*-\s*(\d+)$", a.lines.strip())
            if not m:
                sys.exit("kb read: --lines 形如 120-180")
            lo, hi = int(m.group(1)), int(m.group(2))
            lines = lines[max(0, lo - 1):hi]
        body = "\n".join(lines)
        if len(body) > a.chars:
            body = body[:a.chars] + f"\n…（截断，共 {len(body)} 字符；--chars 可调）"
        print(f"# {_rel(p)}" + (f"  [{a.lines}]" if a.lines else ""))
        print(body)
    return 0


def cmd_poc(a) -> int:
    """PoC 检索一律转发给 `poc`（它的索引与排序是唯一真相，别在这里造第二套）。"""
    import subprocess
    cmd = [str(LAB / "tools" / "bin" / "poc")] + a.rest
    # ★ 2026-11-12 补 timeout：poc 自己有索引/排序逻辑，异常时会挂住；
    #   原来没超时 = kb 整体跟着永久阻塞（容器里 pkill/ps 挂死是同一类病）。
    try:
        # ★ 2026-11-13 补 encoding：text=True 却不定编码，会按 locale 解（Windows cp936），
        #   输出里有非 GBK 字节时直接 UnicodeDecodeError 把 kb 打死。
        #   本仓已有先例（test_cli_smoke 注释里记过同一个坑），这里漏了。
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
    except subprocess.TimeoutExpired:
        sys.stderr.write("✗ poc 超过 120s 未返回（已中止，不让 kb 一起挂）\n")
        return 124
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    return r.returncode


def cmd_stats(a) -> int:
    docs, meta = load_index()
    print(f"索引：{meta['docs']} 块 · 建于 {meta['built_at']}（{meta['elapsed_s']}s）· {KB_DIR}")
    print(f"  文件：{len(meta['files'])} 个（上次重建 {meta['files_built']} 个 / 复用 {meta['files_reused']} 个）")
    print(f"  来源：{json.dumps(meta['by_kind'], ensure_ascii=False)}")
    print(f"  分类：{json.dumps(meta['by_cat'], ensure_ascii=False)}")
    if a.verbose:
        per = defaultdict(int)
        for d in docs:
            per[d["path"]] += 1
        for p, n in sorted(per.items(), key=lambda kv: -kv[1])[:20]:
            print(f"    {n:4d}  {p}")
    return 0


def cmd_stale(a) -> int:
    """索引是否落后于源文件（selfcheck 用）：exit 1 = 有改动没重建。"""
    if not META.exists():
        print("kb: 没有索引（跑 `kb build`）")
        return 1
    meta = json.loads(META.read_text(encoding="utf-8"))
    old = meta.get("files") or {}
    stale, missing = [], []
    for path, _kind, _cat in collect_sources():
        key = _rel(path)
        try:
            st = path.stat()
        except OSError:
            missing.append(key)
            continue
        if old.get(key) != [int(st.st_mtime), st.st_size]:
            stale.append(key)
    for key in old:
        if not (LAB / key).exists() and key not in {_rel(p) for p, _k, _c in collect_sources()}:
            missing.append(key)
    if stale or missing:
        print(f"索引落后：{len(stale)} 个文件改过 / {len(missing)} 个消失（跑 `kb build`）")
        for p in (stale + missing)[:8]:
            print(f"  · {p}")
        return 1
    print(f"索引最新（{meta['docs']} 块，建于 {meta['built_at']}）")
    return 0


# ────────────────────── 单篇 Markdown 规范化入库（S12）──────────────────────
# 为什么需要：本库有 ≈2.49 万块索引，但**没有「从一篇 WP/笔记快速规范入库」的规则** ——
# 现在全靠人手写 title/description，写出来的形态五花八门，检索时标题对不上。
# 抄 sapper `kb.py:177-209` 的三条解析规则（简单、可预测、不需要 frontmatter）：
#   ① 首个 `^#\s+` 行 → title（没有就用文件名，下划线/连字符转空格）
#   ② 紧随其后的首个非空行若以 `>` 开头 → description（引述块当摘要）
#   ③ 其余全部 → detail body
# 注意这是**解析规则**，不是"再写一套索引"：产出的条目仍走既有 make_doc/索引格式，
# 落盘成一个标准 .md 文件后由 `kb build` 正常吸收。

# ★ 仓库纪律（AGENTS.md §三）：收外部路径的参数必须走有界读 ——
#   只认普通文件 + 限长。否则 /dev/zero、FIFO、超大文件会把内存吃光，
#   整台发行版连 pi 一起 SIGKILL。这里不做 `Path(用户路径).read_text()`。
def read_capped(path: Path, limit: int) -> str:
    """只读**普通文件**且**最多 limit 字节**；不满足就抛，让调用方报清楚。"""
    p = Path(path)
    st = p.stat()                     # 不存在/无权限在这里就抛，交给调用方
    import stat as _stat
    if not _stat.S_ISREG(st.st_mode):
        raise ValueError(f"不是普通文件（mode={oct(st.st_mode)}）")
    with p.open("rb") as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"超过上限 {limit} 字节（只读前 {limit} 字节不入库，避免截断产生假内容）")
    return data.decode("utf-8", errors="replace")


_HEAD1_RE = re.compile(r"^#\s+(.+)$")


def parse_markdown_entry(text: str, fallback_title: str = "") -> dict:
    """把一篇 markdown 解析成 {title, description, detail, body_lines}。

    与 sapper 的差异（有意为之）：
      · 只认 **一级** `# ` 作标题：`## ` 是正文章节，当标题会把结构压平
      · title/description 都做单行化（多行标题会破 markdown 结构）
      · 返回 detail_start 行号，方便调用方保留原文其余部分
    """
    lines = text.splitlines()
    title = ""
    body_start = 0
    for i, line in enumerate(lines):
        m = _HEAD1_RE.match(line.strip())
        if m:
            title = _one_line(m.group(1))
            body_start = i + 1
            break
    if not title:
        title = _one_line(fallback_title.replace("-", " ").replace("_", " "))

    description = ""
    desc_line = -1
    for i, line in enumerate(lines[body_start:], body_start):
        s = line.strip()
        if not s:
            continue
        if s.startswith(">"):
            description = _one_line(s.lstrip("> "))
            desc_line = i
        break

    # ★ detail 必须从摘要行**之后**开始。
    #   踩过：原来从 body_start 起截，而 body_start 之后第一行正是那句 `> 摘要`，
    #   于是落盘时「摘要」出现两次（一次当 description，一次当正文开头）。
    detail_start = desc_line + 1 if desc_line >= 0 else body_start
    detail = "\n".join(lines[detail_start:]).strip()
    return {"title": title, "description": description,
            "detail": detail, "detail_start": detail_start}


def _one_line(s: str) -> str:
    """压成单行（标题/摘要里出现换行会把 markdown 结构撑破）。"""
    return " ".join(str(s or "").split())


def cmd_import_md(a) -> int:
    """把一篇 markdown 规范化入库（解析规则见 parse_markdown_entry）。

    默认只**预览**解析结果（--dry-run 语义），加 --write 才落盘 ——
    入库会动知识库内容，先看清楚再写。
    """
    src = Path(a.path)
    if not src.is_file():
        print(f"✗ 不是文件：{src}")
        return 2
    # 统一走有界读：知识库入库不许把 /dev/zero 之类读进来撑爆内存
    try:
        raw = read_capped(src, 2 * 1024 * 1024)
    except Exception as e:
        print(f"✗ 读不到 {src}（{e.__class__.__name__}: {e}）")
        return 2

    parsed = parse_markdown_entry(raw, fallback_title=src.stem)
    if not parsed["title"]:
        print(f"✗ 解析不出标题（文件里没有 `# ` 且文件名为空）：{src}")
        return 2

    print(f"源文件：{src}")
    print(f"  title       : {parsed['title']}")
    print(f"  description : {parsed['description'] or '（无 —— 首行不是 > 引述块）'}")
    print(f"  detail      : {len(parsed['detail'])} 字符 / "
          f"{len(parsed['detail'].splitlines())} 行")
    if parsed["detail"]:
        first = parsed["detail"].splitlines()[0][:80]
        print(f"  正文首行    : {first}")

    if not a.write:
        print()
        print("（预览模式。确认无误加 --write 落盘）")
        return 0

    # 落成规范形态：单行 title + 可选引述摘要 + 正文
    out_lines = [f"# {parsed['title']}", ""]
    if parsed["description"]:
        out_lines += [f"> {parsed['description']}", ""]
    if parsed["detail"]:
        out_lines.append(parsed["detail"])
    out_text = "\n".join(out_lines).rstrip() + "\n"

    dst = Path(a.out) if a.out else src.with_suffix(".kb.md")
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(out_text, encoding="utf-8")
    except OSError as e:
        print(f"✗ 写不了 {dst}（{e.__class__.__name__}: {e}）")
        return 2
    print()
    print(f"✓ 已写入 {dst}")
    print("  下一步：把它放进知识库源目录后跑 `kb build`（增量，几秒）")
    return 0


# ────────────────────────── 自检 ──────────────────────────

def cmd_selftest() -> int:
    ok = True

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        print(f"  {'✓' if cond else '✗'} {name}{(' — ' + detail) if detail and not cond else ''}")
        ok &= bool(cond)

    print("== kb --selftest ==")
    # 1. 分词：英文词 / 中文 bigram / 中文单字
    t = terms_of("泛微 e-cology RCE")
    check("分词含英文词 e-cology", "e-cology" in t, str(t[:8]))
    check("分词含中文 bigram 泛微", "泛微" in t)
    check("分词含连续中文 bigram（命令执行→命令/令执/执行）", "令执" in terms_of("命令执行"))
    # 2. CVE 精确通道
    check("CVE 提取", ids_of("see CVE-2021-44228 here") == {"CVE-2021-44228"})
    check("CNVD 提取", ids_of("CNVD-2021-10543") == {"CNVD-2021-10543"})
    # 3. 打分/排序：标题命中 > 正文命中；中文可召回
    docs = [
        make_doc("ref", "web", "a.md", "泛微 e-cology 前台 RCE", "正文讲请求骨架与判定式", (1, 9)),
        make_doc("ref", "pwn", "b.md", "堆利用入门", "顺便提到泛微 e-cology 一次", (1, 9)),
        make_doc("ref", "crypto", "c.md", "RSA 小公钥指数", "e=3 立方根攻击", (1, 9)),
    ]
    df = Counter()
    for d in docs:
        for term in d["tf"]:
            df[term] += 1
    meta = {"df": dict(df), "n_docs": len(docs), "avg_len": sum(d["len"] for d in docs) / len(docs)}
    hits = search(docs, meta, "泛微 e-cology RCE", None, None, 3)
    check("中文查询能召回", bool(hits), "无命中")
    check("标题命中排第一", hits and hits[0][1]["path"] == "a.md", str([(round(s, 2), d['path']) for s, d in hits]))
    # 4. CVE 查询只回编号命中的
    pocs = [make_doc("poc", "poc", "pocs/x/1.yaml", "Log4Shell", "CVE-2021-44228 jndi ldap",
                     (0, 0), extra={"ids": ["CVE-2021-44228"], "expr": "contains(\"jndi:\")"}),
            make_doc("poc", "poc", "pocs/x/2.yaml", "别的洞", "无关", (0, 0), extra={"ids": ["CVE-2019-0001"]})]
    m2df = Counter()
    for d in pocs:
        for term in d["tf"]:
            m2df[term] += 1
    m2 = {"df": dict(m2df), "n_docs": 2, "avg_len": 20.0}
    h2 = search(pocs, m2, "CVE-2021-44228", None, None, 5)
    check("CVE 精确通道只回命中那条", len(h2) == 1 and h2[0][1]["title"] == "Log4Shell", str(len(h2)))
    # 5. 分类过滤 + 有界摘要
    h3 = search(docs, meta, "利用", "pwn", None, 5)
    check("--cat 过滤生效", all(d["cat"] == "pwn" for _s, d in h3))
    long_body = "甲" * 3000
    ex = excerpt_at(long_body, "甲", EXCERPT_CHARS)
    check(f"摘要被截到 ≤{EXCERPT_CHARS + 20} 字符", len(ex) <= EXCERPT_CHARS + 20, str(len(ex)))
    # 6. 真实索引（若已建）
    if META.exists():
        d, m = load_index()
        if len(d) >= 100:      # 索引太小（回归测试用的 fixture）不能拿来说事 → 跳过而不是 FAIL
            check("真实索引可加载", True, f"{len(d)} 块")
            h = search(d, m, "堆 溢出", None, None, 3)
            check("真实索引：中文查询有命中", bool(h))
        else:
            print(f"  · 索引只有 {len(d)} 块（fixture？），跳过真实数据检查")
    else:
        print("  · 真实索引未建（先 `kb build`），跳过真实数据检查")
    print("== selftest", "PASS" if ok else "FAIL", "==")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(prog="kb", description="pi-lab 本地知识库（参考资料 + 手法库 + PoC 一个入口）")
    ap.add_argument("--selftest", action="store_true", help="自检（不联网、不依赖索引）")
    sub = ap.add_subparsers(dest="cmd")

    b = sub.add_parser("build", help="建/更新索引（默认增量：只重算改动过的文件）")
    b.add_argument("--force", action="store_true", help="全量重建")
    b.set_defaults(fn=cmd_build)

    s = sub.add_parser("search", help="检索（默认返回 5 条：片段 + 行号指针）")
    s.add_argument("query")
    s.add_argument("--path", default=None,
                   help="路径子串过滤，如 2026/review 或 2025/pwn（一个仓库里按年/批分桶时用它）")
    s.add_argument("--cat", help="限定分类：web/pwn/reverse/crypto/misc/forensics/osint/malware/ai-ml/ir/src/poc…")
    s.add_argument("--kind", help="限定来源：ref,skill,notes,writeup,poc（逗号分隔）")
    s.add_argument("--top", type=int, default=DEFAULT_TOP)
    s.add_argument("--excerpt", type=int, default=EXCERPT_CHARS)
    s.add_argument("--json", action="store_true")
    s.add_argument("--no-poc-bias", action="store_true", help="不要给「带判定式的 PoC」加分")
    s.set_defaults(fn=cmd_search)

    r = sub.add_parser("read", help="按路径或检索编号取全文（有界输出）")
    r.add_argument("target", help="路径，或 `kb search` 里的编号（配 --query）")
    r.add_argument("--query", default="", help="编号对应的检索词")
    r.add_argument("--lines", help="行区间，如 120-180")
    r.add_argument("--chars", type=int, default=8000)
    r.set_defaults(fn=cmd_read)

    p = sub.add_parser("poc", help="转发给 `poc`（PoC 库唯一真相）")
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(fn=cmd_poc)

    st = sub.add_parser("stats", help="索引概况")
    st.add_argument("--verbose", action="store_true")
    st.set_defaults(fn=cmd_stats)

    sl = sub.add_parser("stale", help="索引是否落后于源文件（exit 1 = 需要重建）")
    sl.set_defaults(fn=cmd_stale)

    im = sub.add_parser("import-markdown",
                        help="[S12] 把一篇 WP/笔记规范化入库（首 # 作标题、其后 > 行作摘要、余下为正文）")
    im.add_argument("path", help="源 .md 文件")
    im.add_argument("--write", action="store_true",
                    help="真的落盘（默认只预览解析结果 —— 入库会动知识库，先看清楚）")
    im.add_argument("--out", default=None,
                    help="输出路径（默认与源同名加 .kb.md 后缀）")
    im.set_defaults(fn=cmd_import_md)

    a = ap.parse_args()
    if a.selftest:
        return cmd_selftest()
    if not a.cmd:
        ap.print_help()
        return 2
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
