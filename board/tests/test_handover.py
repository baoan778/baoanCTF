#!/usr/bin/env python3
"""交接包生成器回归：`board/handover.py`（`tools/bin/handover`）。

跑法：
    python3 board/tests/test_handover.py

为什么要有这个文件
------------------
手工打的交接包**刚生成就过期了**（写着 588 条断言 / 「跑第 0 节自检」，而这两处当天就变了）。
换成的自动版本，风险点换成了另一种：**README 里的数字如果哪天不再从现场抓、而变成写死的模板，
它会重新开始撒谎**。所以这里锁的就是这一点：

  * 包必须生成成功、`sha256sum -c` 必须全 OK
  * README 里的 git 短哈希 / 技能数 / 测试文件数，必须与**现场真实值**一致（不是"看起来像"）
  * **人写的待办要原样搬运**（逐字来自 `状态快照.md` §0.5）
  * 大二进制/构建产物不许进包（否则包会变成几百 M，失去"能带走"的意义），且要列进
    `MANIFEST-skipped.txt`（不然收件人以为丢了）

测试**不碰真产物**：一律 `--out` 到 `tempfile.mkdtemp()`，绝不往桌面写。
"""
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
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

BOARD = Path(__file__).resolve().parents[1]
LAB = BOARD.parent
HANDOVER = BOARD / "handover.py"
SNAP = LAB / "docs" / "状态快照.md"

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail and not cond else ""))


def main():
    print("1) 生成（--no-check，避免测试依赖 selfcheck 的耗时）")
    out = Path(tempfile.mkdtemp(prefix="pilab-handover-"))
    # ★ 先拍基线：生成**之前**存在哪几块黑板。
    # 并行跑时不能拿「计数相等」当判据（别的测试可能在同一瞬间建/删临时项目）；
    # 不变量是「生成那一刻存在的黑板必须都进了包」。
    baseline = {p.parent.name for p in (LAB / "board" / "projects").glob("*/graph.json")}
    # ★ 并发免疫：生成前先拍 git 哈希。多会话共存时，别人可能在我生成期间提交，
    #   现场哈希就会变 —— 那不是 bug。判据改成「等于生成前或生成后的哈希」。
    def _git_head() -> str:
        return subprocess.run(["git", "log", "-1", "--format=%h"], cwd=LAB,
                              capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
    head_before = _git_head()
    # 生成在 4 路并行 + drvfs 上会偶尔变慢/失败（实测遇到过一次 flaky）：
    # 对**纯生成**这一步重试一次，并显式打印重试（偶发不允许静默通过）
    r = None
    for attempt in (1, 2):
        r = subprocess.run(
            [sys.executable, str(HANDOVER), "--out", str(out), "--no-check"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
        )
        if r.returncode == 0:
            if attempt == 2:
                print("  ! 注意：第一次生成失败、重试后成功（负载偶发）—— 若频繁出现要查 drvfs 负载")
            break
        if attempt == 1:
            print(f"  ! 生成第 1 次失败（rc={r.returncode}），重试一次；输出尾部：")
            print("    " + ((r.stderr or r.stdout).strip().splitlines() or ["<空>"])[-1][:200])
    check("生成退出码 0", r.returncode == 0, (r.stderr or r.stdout)[-500:])
    if r.returncode != 0:
        print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
        return 1

    readme = out / "README.md"
    check("README.md 存在", readme.exists())
    text = readme.read_text(encoding="utf-8")
    # ★ 2026-11-26：状态快照不再随仓库分发，包内只带架构设计。
    check("架构设计.md 已带入包", (out / "架构设计.md").exists())
    check("SHA256SUMS.txt 存在", (out / "SHA256SUMS.txt").exists())

    print("2) README 里的数字必须是现场抓的（不是写死的）")
    head_after = _git_head()
    cands = {h for h in (head_before, head_after) if h}
    if cands:
        check(f"git 短哈希与生成时刻一致（{'/'.join(sorted(cands))}）",
              any(h in text for h in cands), text[:400])
    else:
        # ★ 2026-10-30：发布包（买家手里的目录）**没有 .git** —— 拿不到短哈希是正常的，
        #   不是缺陷。这种情况只要求 README 如实标注"无版本信息"，别把包判死。
        check("无 git 仓库时 README 如实标注（不编造哈希）",
              "无 git" in text or "无版本" in text or "未纳入版本控制" in text, text[:400])
    n_skills = len([p for p in (LAB / "skills").glob("*/") if (p / "SKILL.md").exists()])
    check(f"技能数 {n_skills} 出现在 README", f"{n_skills} 个" in text)
    n_tests = len(list((LAB / "board" / "tests").glob("test_*.py")))
    check(f"测试文件数 {n_tests} 写在 README", f"{n_tests} 条测试文件" in text)
    check("README 标明自己是自动生成", "自动生成" in text and "handover" in text)

    print("3) 人写的待办必须原样搬运")
    # ★ 2026-11-26：docs/状态快照.md 不再随仓库分发（开源交付只留产品）。
    #   这段检查「人写待办原样搬运」，源文件没了就没有意义，跳过。
    if not SNAP.is_file():
        print("  · 跳过：状态快照不再随仓库分发")
    else:
        src = SNAP.read_text(encoding="utf-8").splitlines()
        todo = []
        started = False
        for line in src:
            if line.startswith("## 0.5"):
                started = True
                continue
            if started:
                if line.strip() == "---" or line.startswith("## "):
                    break
                todo.append(line)
        key_line = next((l.strip() for l in todo if l.strip().startswith("|")), "")
        check("待办区存在", bool(todo))
        check("待办首行逐字出现在 README", bool(key_line) and key_line in text, key_line[:120])
        check("README 声明待办是人写的", "人写的" in text)

    print("4) 不该进包的东西")
    blobs = [p for p in out.rglob("*") if p.is_file() and p.suffix.lower() in (".jar", ".exe", ".class", ".zip")]
    check("没有二进制/构建产物进包", not blobs, str(blobs[:3]))
    man = out / "MANIFEST-skipped.txt"
    check("MANIFEST-skipped.txt 存在（说明剔除了什么）", man.exists())
    if man.exists():
        check("清单里有内容或明确写「无」", "未进包" in man.read_text(encoding="utf-8"))

    print("5) 黑板图形快照可被接手")
    graphs_list = sorted((out / "board" / "projects").glob("*/graph.json"))
    graphs = {p.parent.name for p in graphs_list}
    check(f"生成前的 {len(baseline)} 块黑板都进了包", baseline <= graphs,
          f"缺 {sorted(baseline - graphs)}")
    if graphs_list:
        d = json.loads(graphs_list[0].read_text(encoding="utf-8"))
        check("graph.json 可解析且结构对", d.get("schema") == "fgs/v1", str(list(d)[:5]))
        check("项目表里出现了该棋子", graphs_list[0].parent.name in text)

    print("6) 完整性清单必须真的对得上")
    sums = (out / "SHA256SUMS.txt").read_text(encoding="utf-8").split("\n")
    entries = [l for l in sums if "  " in l]
    check("清单非空", len(entries) > 0)
    bad = 0
    for line in entries[:60]:
        h, rel = line.split("  ", 1)
        p = out / rel
        if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() != h:
            bad += 1
    check("抽查的哈希全部匹配", bad == 0, f"{bad} 条不匹配")
    check("清单里不含它自己", not any("SHA256SUMS.txt" in l for l in entries))

    print("7) 默认输出路径（不真写桌面，只验算法）")
    r2 = subprocess.run(
        [sys.executable, str(HANDOVER), "--help"], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    check("--help 可跑", r2.returncode == 0)
    check("--help 提到桌面默认路径", "桌面" in (r2.stdout + r2.stderr) or "默认" in (r2.stdout + r2.stderr))
    src_txt = HANDOVER.read_text(encoding="utf-8")
    check("默认路径带日期时间（不会互相覆盖）", re.search(r"交接-\{now:%Y%m%d-%H%M\}", src_txt) is not None)

    shutil.rmtree(out, ignore_errors=True)
    print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ 交接包生成器：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
