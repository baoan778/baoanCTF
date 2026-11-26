#!/usr/bin/env python3
"""payload.py -- 离线 payload 库检索（PayloadsAllTheThings 快照，136K）

为什么要有（2026-11-14 评估结论）：
  剩余题反思判定全是 MULTI_METHOD —— 不缺思路，缺**具体 payload 变体**。
  容器能直连 raw.githubusercontent.com（实测 200 / 33KB / 12s），
  所以把常用类别抓成本地快照，检索不依赖网络、毫秒级。

用法:
  python3 tools/payload.py list
  python3 tools/payload.py search "<关键词>"      # 例: search sqlite bypass
  python3 tools/payload.py show <类别> [行数]      # 例: show ssti 60
"""
from __future__ import annotations

import pathlib
import re
import sys

DIR = pathlib.Path(__file__).resolve().parent / "payloads"


def cats():
    return sorted(p.stem for p in DIR.glob("*.md"))


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "list":
        for c in cats():
            p = DIR / (c + ".md")
            print(f"{c:10} {p.stat().st_size:>7} bytes  {p}")
        return 0
    if cmd == "search" and len(sys.argv) >= 3:
        kw = sys.argv[2]
        pat = re.compile(re.escape(kw), re.I)
        hits = 0
        for c in cats():
            f = DIR / (c + ".md")
            try:
                lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for i, ln in enumerate(lines):
                if pat.search(ln):
                    ctx = " | ".join(x.strip() for x in lines[i:i + 2] if x.strip())
                    print(f"[{c}:{i + 1}] {ctx[:220]}")
                    hits += 1
                    if hits >= 25:
                        print("...（还有更多，缩小关键词或直接 show 类别）")
                        return 0
        if not hits:
            print(f"未命中 {kw}；类别有: {', '.join(cats())}")
        return 0
    if cmd == "show" and len(sys.argv) >= 3:
        c = sys.argv[2]
        n = int(sys.argv[3]) if len(sys.argv) > 3 else 80
        f = DIR / (c + ".md")
        if not f.is_file():
            print(f"没有类别 {c}；可用: {', '.join(cats())}")
            return 1
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        print(chr(10).join(lines[:n]))
        print(f"...（共 {len(lines)} 行，用 search 精确找）")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())