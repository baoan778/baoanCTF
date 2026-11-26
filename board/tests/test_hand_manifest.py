#!/usr/bin/env python3
"""人手写入口清单的单一可写点 —— 治「三份 HAND_TOOLS 各改各的」。

跑法：    python3 board/tests/test_hand_manifest.py

为什么要有它
-----------
2026-11-24 清理时发现：同一份「人手写入口」清单在**三个地方各存了一份**：

    tools/make-bin.sh:189          HAND_TOOLS="py pi fgs ..."
    tools/hand/selfcheck:506       HAND_TOOLS="py pi fgs ..."
    tools/bin/selfcheck:506        HAND_TOOLS="py pi fgs ..."   ← 它俩是独立副本

加一个入口要改三处，而历史**真漏过**：加了工具但 selfcheck 不查它，
于是「bin 与 hand byte-equal」这条闸门对它形同虚设 —— 而所有检查仍然是绿的。
这正是 docs/经验-工程纪律.md §4c 说的「检查器自己站不稳」。

现在的结构：
  · 唯一可写点 = tools/make-bin.sh 的 HAND_TOOLS
  · 它落盘到 tools/hand/.hand-tools（进版本控制）
  · 两份 selfcheck 都**读文件**，不再硬编码

本测试把这三条钉死：
  1. 清单文件存在且非空
  2. 清单内容 == make-bin.sh 的 HAND_TOOLS（集合相等，顺序无关）
  3. 两份 selfcheck 里**没有**硬编码的 HAND_TOOLS=（防有人又写回去）
"""
import re
import sys
from pathlib import Path

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
MAKE_BIN = ROOT / "tools" / "make-bin.sh"
MANIFEST = ROOT / "tools" / "hand" / ".hand-tools"
SELFCHECKS = [ROOT / "tools" / "hand" / "selfcheck",
              ROOT / "tools" / "bin" / "selfcheck"]

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print("  %s %s%s" % ("OK" if cond else "XX", name,
                         ("   " + detail) if detail and not cond else ""))


def manifest_entries() -> list:
    if not MANIFEST.is_file():
        return []
    out = []
    for line in MANIFEST.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def make_bin_entries() -> list:
    src = MAKE_BIN.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'^HAND_TOOLS="([^"]*)"', src, re.M)
    return m.group(1).split() if m else []


def main() -> int:
    print("1) 清单文件存在且可读")
    check("tools/hand/.hand-tools 存在", MANIFEST.is_file())
    ents = manifest_entries()
    check("清单非空", len(ents) > 0, str(len(ents)))
    check("清单里有 pi（2026-11-24 新增）", "pi" in ents, ",".join(ents[:6]))

    print("2) 清单 == make-bin.sh 的 HAND_TOOLS（集合相等）")
    mb = make_bin_entries()
    check("make-bin.sh 里找到了 HAND_TOOLS=", len(mb) > 0, str(mb)[:80])
    check("两边集合相等", set(ents) == set(mb),
          "仅清单有:%s 仅脚本有:%s" % (set(ents) - set(mb), set(mb) - set(ents)))

    print("3) 两份 selfcheck 不再硬编码 HAND_TOOLS=")
    for sc in SELFCHECKS:
        if not sc.is_file():
            check("%s 存在" % sc.name, False, str(sc))
            continue
        src = sc.read_text(encoding="utf-8", errors="replace")
        # 允许出现在**注释**里（说明来历），但赋值语句必须**不是字面量列表**。
        # 2026-11-24 修：第一版判据写成「有 HAND_TOOLS="..." 就红」，
        #   把 `HAND_TOOLS="$(...)"` 这种**从清单读**的正确写法也判红了。
        #   判据必须区分「读文件」与「写字面量」。
        hard = None
        for m in re.finditer(r'^\s*HAND_TOOLS="([^"]*)"', src, re.M):
            val = m.group(1)
            if "$(" in val or "${" in val:
                continue          # 命令替换 / 变量展开 = 从清单读，合法
            if any(tok.isalpha() for tok in val.split()):
                hard = m
                break
        check("%s/%s 无硬编码字面量" % (sc.parent.name, sc.name), hard is None,
              hard.group(0)[:80] if hard else "")
        check("%s/%s 会读清单文件" % (sc.parent.name, sc.name),
              ".hand-tools" in src)

    print("4) 反向：清单少一个就必须能发现")
    # 用集合比较模拟「清单漏了 pi」
    fake = [e for e in ents if e != "pi"]
    check("漏掉 pi 时集合不相等（判据有效）", set(fake) != set(mb))

    print()
    print("=" * 40)
    print("通过 %d / 失败 %d" % (len(passed), len(failed)))
    if failed:
        print("失败项：" + " | ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
