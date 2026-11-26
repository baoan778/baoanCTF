#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""handover.py —— 生成「交接包」（把当前状态打成一个能带走的目录）

为什么要有它
------------
之前交接包是**手工 cp** 出来的。2026-09-28 那一份刚生成就过期了：它写着
「七条测试 / 588 条断言」「跑第 0 节自检」，而这两处当天就变了（8 条 / 619 条 + `selfcheck`）。
**手写的状态摘要一定会漂**，所以这里的原则是：

    README 里的每一个事实都必须**从现场抓**（git / 文件系统 / fgs / selfcheck），
    唯一允许照抄的是**人写的那一段待办**（§0.5），而且原样搬运、不改一字。

包内容（默认输出到桌面）
------------------------
    README.md          ← 自动生成的状态摘要 + 包内容清单
    状态快照.md / 架构设计.md         ← docs 的正本副本
    手法库.md / INDEX.md              ← docs/writeups
    work/<pid>/                       ← 每块黑板的留档（大二进制/构建产物自动剔除）
    SHA256SUMS.txt                    ← 完整性清单（`sha256sum -c` 验）

用法
----
    handover                      # 生成到桌面 pi-lab-交接-<日期时间>/
    handover --out /tmp/x         # 指定目录
    handover --no-check           # 跳过 selfcheck（不抓自检结论）
    handover --with-tests         # 连回归测试一起跑（约 60s，默认只跑 --quick）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
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


LAB = Path(__file__).resolve().parents[1]
BOARD = Path(os.environ.get("PI_LAB_BOARD_DIR") or (LAB / "board" / "projects"))
DOCS = LAB / "docs"
WRITEUPS = Path(os.environ.get("PI_LAB_WRITEUPS_DIR") or (DOCS / "writeups"))
SELFCHECK = LAB / "tools" / "bin" / "selfcheck"
DEFAULT_DESKTOP = Path("/mnt/c/Users/幺零零/Desktop")

# 留档里这些不进包：二进制 / 构建产物 / 超大文件（它们不是"交接信息"）
SKIP_SUFFIX = {
    ".jar", ".class", ".exe", ".dll", ".so", ".zip", ".rar", ".7z",
    ".iso", ".dmp", ".raw", ".vmem", ".pyc", ".o", ".a", ".deb", ".rpm",
}
SKIP_DIRS = {"__pycache__", "build", "target", "out", ".gradle", ".git"}
MAX_FILE_BYTES = 2 * 1024 * 1024


def run(*args, cwd=None, timeout=600):
    p = subprocess.run(args, cwd=str(cwd or LAB), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, p.stdout, p.stderr


# ── 现场抓状态 ────────────────────────────────────────────────────────────────
def git_facts() -> dict:
    # ★ 2026-10-30：发布包（买家手里的目录）通常没有 .git → 这里必须**如实写「无 git」**，
    #   不能编一个哈希、也不能写 (无提交) 让人误以为是空仓库。
    #   test_handover.py 会检查这个措辞（缺 git 时要求 README 出现「无 git」）。
    if not (LAB / ".git").exists():
        return {"head": "无 git（未纳入版本控制）", "dirty": -1, "tracked": 0}
    rc, out, _ = run("git", "log", "-1", "--format=%h %cs")
    head = out.strip() if rc == 0 else "(无提交)"
    rc, out, _ = run("git", "status", "--porcelain")
    dirty = len([l for l in out.splitlines() if l.strip()]) if rc == 0 else -1
    rc, out, _ = run("git", "ls-files")
    tracked = len(out.splitlines()) if rc == 0 else 0
    return {"head": head, "dirty": dirty, "tracked": tracked}


def skill_facts() -> dict:
    skills = sorted(p.name for p in (LAB / "skills").glob("*/") if (p / "SKILL.md").exists())
    link = LAB / ".pi" / "skills"
    return {
        "count": len(skills),
        "names": skills,
        "link_ok": link.is_symlink() and bool(list(link.glob("*/SKILL.md"))) if link.is_symlink() else False,
        "link_target": os.readlink(link) if link.is_symlink() else None,
    }


def test_facts() -> dict:
    files = sorted((LAB / "board" / "tests").glob("test_*.py"))
    return {"count": len(files), "names": [f.name for f in files]}


def check_facts(with_tests: bool, skip: bool) -> dict:
    if skip or not SELFCHECK.exists():
        return {"ran": False, "verdict": "(未运行 —— `--no-check`)", "detail": ""}
    args = ["bash", str(SELFCHECK)] + ([] if with_tests else ["--quick"])
    try:
        rc, out, err = run(*args, timeout=900)
    except subprocess.TimeoutExpired:
        return {"ran": True, "verdict": "✗ 自检超时", "detail": ""}
    both = out + err
    verdict = next((l.strip() for l in both.splitlines() if "结论:" in l), f"(退出码 {rc})")
    fails = [l.strip() for l in both.splitlines() if "✗" in l and "结论:" not in l]
    return {"ran": True, "verdict": verdict, "detail": "; ".join(fails[:4]), "rc": rc}


def project_facts() -> list[dict]:
    rows = []
    for g in sorted(BOARD.glob("*/graph.json")):
        try:
            d = json.loads(g.read_text(encoding="utf-8"))
        except Exception:
            continue
        nodes = d.get("nodes") or {}
        goal = next(
            (v.get("text", "") for v in nodes.values() if v.get("type") == "goal"),
            "",
        )
        confirmed = d.get("confirmed") or {}
        solved = d.get("solved") or {}
        if confirmed:
            status = "已人工确认 ✅"
        elif solved:
            status = "solved 待确认 ⏳"
        else:
            status = "未解决"
        rows.append(
            {
                "pid": d.get("project_id", g.parent.name),
                "kind": d.get("kind", "?"),
                "rev": d.get("revision", "?"),
                "status": status,
                "flag": confirmed.get("flag") or solved.get("desc") or "",
                "goal": re.sub(r"\s+", " ", goal)[:90],
                "updated": (d.get("updated_at") or "")[:16],
                "has_wp": (WRITEUPS / "WPs" / f"{g.parent.name}_WP.md").exists()
                or (LAB / "work" / g.parent.name / "WRITEUP.md").exists(),
            }
        )
    return rows


def todo_verbatim() -> str:
    """原样搬运 状态快照 里的 §0.5 待办 —— 它承载的是人的判断，不许我们改写。"""
    snap = DOCS / "状态快照.md"
    if not snap.exists():
        return "(状态快照.md 不存在)"
    lines = snap.read_text(encoding="utf-8").splitlines()
    out, started = [], False
    for line in lines:
        if line.startswith("## 0.5"):
            started = True
            continue
        if started:
            if line.strip() == "---" or line.startswith("## "):
                break
            out.append(line)
    return "\n".join(out).strip() or "(§0.5 待办 为空)"


# ── 生成 ──────────────────────────────────────────────────────────────────────
def _io_workers() -> int:
    """drvfs 上的并发度：延迟瓶颈 → 并发有收益；但**多会话共存**时要让路。

    2026-10-06 实测：两个会话同时在同一个仓库跑时，固定 16 路会互相抢盘，
    整体反而更慢（test_matrix_fuzz 从 64.8s 涨到 278s，而它没被改过）。
    策略：按 1 分钟负载伸缩 —— 负载低于核数时放开到 16，越高越保守，下限 4。
    """
    base = min(16, max(4, (os.cpu_count() or 4) * 4))
    try:
        load1 = os.getloadavg()[0]
    except (OSError, AttributeError):
        # AttributeError：Windows 没有 os.getloadavg（POSIX-only）。
        # 没有负载读数就退回固定上限，不影响正确性——这只是并发调参。
        return base
    ncpu = os.cpu_count() or 4
    if load1 <= ncpu:
        return base
    if load1 <= ncpu * 2:
        return max(4, base // 2)
    return 4


def copy_work(pid: str, dest_root: Path) -> tuple[int, list[str]]:
    """拷贝 work/<pid>/，剔除二进制与构建产物。返回 (文件数, 跳过的说明)。

    ★ 2026-10-06 提速：原来是串行逐文件 `copy2`，5837 个文件在 drvfs 上要 ~3 分钟。
      先串行只做「筛选」（要读的元数据本来就少），再**线程池并行拷贝** ——
      drvfs 是延迟瓶颈，并发收益接近线性。输出（文件数 / skipped 列表）仍然确定性。
    """
    src = LAB / "work" / pid
    if not src.is_dir():
        return 0, []
    todo: list[tuple[Path, Path]] = []
    skipped: list[str] = []
    for p in src.rglob("*"):
        rel = p.relative_to(src)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if p.is_dir():
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if p.suffix.lower() in SKIP_SUFFIX or (size > MAX_FILE_BYTES and p.suffix.lower() != ".md"):
            skipped.append(str(rel))
            continue
        todo.append((p, dest_root / "work" / pid / rel))

    def cp(pair: tuple[Path, Path]) -> None:
        s, d = pair
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)

    if todo:
        with ThreadPoolExecutor(max_workers=_io_workers()) as ex:
            list(ex.map(cp, todo))
    return len(todo), skipped


def write_readme(out: Path, ctx: dict) -> None:
    g, s, t, c, rows = ctx["git"], ctx["skills"], ctx["tests"], ctx["check"], ctx["projects"]
    lines = [
        "# pi-lab 交接包（自动生成）",
        "",
        f"> 生成时间 **{ctx['now']}** · 生成者 `tools/bin/handover`（`board/handover.py`）",
        "> **正本在 `D:\\AI\\pi-lab`**，本包只是快照副本；包内 README 的每个数字都是生成时从现场抓的。",
        "",
        "## 怎么用",
        "",
        "下次启动把这份 `状态快照.md` 发给我（或直接发这句）：",
        "",
        "```",
        "读 <仓库根>/AGENTS.md 和 docs/状态快照.md，先跑 `selfcheck`，",
        "然后告诉我 §0.5「待办」里那件事该怎么接着做。",
        "```",
        "",
        "## 生成时的状态（自动抓取）",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| git | `{g['head']}` · 跟踪 {g['tracked']} 文件 · " + ("工作区干净" if g["dirty"] == 0 else f"**{g['dirty']} 处未提交**") + " |",
        f"| 技能 | {s['count']} 个 · 软链 " + ("✓" if s["link_ok"] else "**✗ 断了！**") + (f"（{s['link_target']}）" if s["link_target"] else "") + " |",
        f"| 回归测试 | {t['count']} 条测试文件（{' '.join(n.replace('test_', '').replace('.py', '') for n in t['names'])}） |",
        f"| 自检 | {c['verdict']}" + (f" —— {c['detail']}" if c.get("detail") else "") + " |",
        f"| 黑板项目 | {len(rows)} 个 |",
        "",
        "## 黑板项目",
        "",
        "| 项目 | 类型 | 状态 | 结论 / flag | 目标（截断） |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        if r["flag"]:
            # ctf 项目的 solved.desc 就是 flag；研究型项目的 desc 是一句结论，别当 flag 排版
            flag = f"`{r['flag']}`" if r["kind"] == "ctf" else re.sub(r"\s+", " ", r["flag"])[:70]
        else:
            flag = "—"
        wp = " · 有 WP" if r["has_wp"] else ""
        lines.append(f"| `{r['pid']}` | {r['kind']} | {r['status']}{wp} | {flag} | {r['goal']} |")
    lines += [
        "",
        "## 待办（摘自 `状态快照.md` §0.5，**人写的，原样搬运**）",
        "",
        todo_verbatim(),
        "",
        "## 包内容",
        "",
        "| 文件 | 是什么 |",
        "|---|---|",
        "| `状态快照.md` | **接手先读这个**：进度 / 已知坑 / 会话备份路径 |",
        "| `架构设计.md` | 架构 + 五个参考项目的认知存档（含「采纳了什么、为什么」） |",
        "| `手法库.md` | 跨题可复用手法 |",
        "| `INDEX.md` | WP 索引 |",
        "| `work/<项目>/` | 各题留档（二进制与构建产物已剔除，见 `MANIFEST-skipped.txt`） |",
        "| `board/projects/<项目>/` | 黑板图形快照 + 审计流水（复制回 `D:\\AI\\pi-lab\\board\\projects\\` 即可 `fgs show <项目>`） |",
        "| `SHA256SUMS.txt` | 完整性清单：`sha256sum -c SHA256SUMS.txt` |",
        "",
        "## 没打进包的东西（别以为丢了）",
        "",
        "- 二进制/样本/构建产物（`*.jar *.exe *.zip ...` 或 >2MB）：它们是**解题输入**，不是交接信息；",
        "  正本仍在 `D:\\AI\\pi-lab\\work\\<项目>\\`，逐条列在 `MANIFEST-skipped.txt`。",
        "- 对话全文：在 WSL 侧 `~/.pi-lab-sessions/`，备份见 `archive/sessions-*`。",
        "- 工具链本体（`tools/venv`、`tools/gobin`、`tools/vendor`、PoC 索引约 800MB）：属于环境，",
        "  不属于状态；目标机上按 `README.md` / `tools/TOOLS.md` 重建。",
        "",
    ]
    (out / "README.md").write_text("\n".join(lines), encoding="utf-8")


def write_sha256(out: Path) -> int:
    """完整性清单。

    ★ 2026-10-06 提速：drvfs 上**每文件开销 ~60ms**（与字节数无关）——
      实测 5837 文件 / 112MB 的包，串行 sha256 要 ~3 分钟。
      改两处：① 分块流式（不再 read_bytes() 全量进内存，避免大文件 OOM）；
      ② 线程池并发（drvfs 是延迟瓶颈不是带宽瓶颈 → 并发收益接近线性）。
      `ex.map` 保持输入顺序，输出仍然确定性。
    """
    files = sorted(p for p in out.rglob("*") if p.is_file() and p.name != "SHA256SUMS.txt")

    def one(p: Path) -> str:
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return f"{h.hexdigest()}  {p.relative_to(out).as_posix()}"

    with ThreadPoolExecutor(max_workers=_io_workers()) as ex:
        rows = list(ex.map(one, files))
    (out / "SHA256SUMS.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 pi-lab 交接包")
    ap.add_argument("--out", default=None, help="输出目录（默认桌面 pi-lab-交接-<日期时间>）")
    ap.add_argument("--no-check", action="store_true", help="跳过 selfcheck")
    ap.add_argument("--with-tests", action="store_true", help="连回归测试一起跑")
    a = ap.parse_args()

    now = datetime.now(timezone(timedelta(hours=8)))
    out = Path(
        a.out
        or os.environ.get("PI_LAB_HANDOVER_DIR")
        or (DEFAULT_DESKTOP / f"pi-lab-交接-{now:%Y%m%d-%H%M}")
    )
    out.mkdir(parents=True, exist_ok=True)

    ctx = {
        "now": now.strftime("%Y-%m-%d %H:%M %z"),
        "git": git_facts(),
        "skills": skill_facts(),
        "tests": test_facts(),
        "check": check_facts(a.with_tests, a.no_check),
        "projects": project_facts(),
    }

    # 1) 文档
    for src, dst in [
        (DOCS / "状态快照.md", "状态快照.md"),
        (DOCS / "架构设计.md", "架构设计.md"),
        (WRITEUPS / "手法库.md", "手法库.md"),
        (WRITEUPS / "INDEX.md", "INDEX.md"),
    ]:
        if src.exists():
            shutil.copy2(src, out / dst)

    # 2) 留档：**所有** work/<dir>（不只看已解出的）—— 交接的是"干到哪了"，
    #    半成品恰恰最需要带走（上一份手打包含 twin-trees 就是因为这个）
    pids = {p.name for p in (LAB / "work").iterdir() if p.is_dir()}
    pids |= {r["pid"] for r in ctx["projects"]}
    skipped_all: list[str] = []
    copied = 0
    for pid in sorted(pids):
        n, sk = copy_work(pid, out)
        copied += n
        skipped_all += [f"{pid}/{s}" for s in sk]
    (out / "MANIFEST-skipped.txt").write_text(
        "以下文件因体积/类型未进包（正本在 D:\\AI\\pi-lab\\work\\）：\n"
        + "\n".join(skipped_all) + "\n",
        encoding="utf-8",
    )

    # 2b) 黑板图形快照：带上 graph.json + 审计流水，收件人可以直接 `fgs show`
    n_graph = 0
    for g in sorted(BOARD.glob("*/")):
        dst = out / "board" / "projects" / g.name
        dst.mkdir(parents=True, exist_ok=True)
        for name in ("graph.json", "log.jsonl", "outbox.jsonl", "deliveries.jsonl"):
            src = g / name
            if src.exists():
                shutil.copy2(src, dst / name)
                n_graph += 1

    # 3) README + 校验
    write_readme(out, ctx)
    n_hashed = write_sha256(out)

    print(f"✓ 交接包已生成：{out}")
    print(
        f"  黑白板 {len(ctx['projects'])} 个 · 留档 {copied} 文件 · 黑板流水 {n_graph} 份 · "
        f"剔除 {len(skipped_all)} · 校验 {n_hashed} 条"
    )
    print(f"  git {ctx['git']['head']} · 技能 {ctx['skills']['count']} · 测试 {ctx['tests']['count']} 条 · {ctx['check']['verdict']}")
    print("  自检：sha256sum -c SHA256SUMS.txt （在包目录里跑）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
