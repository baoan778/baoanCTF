#!/usr/bin/env python3
"""基线断言的防复发守卫 —— 治「拿活目录当基线」。

跑法：    python3 board/tests/test_baseline_frozen.py

为什么要有它
-----------
2026-11-24 二次血账（docs/经验-工程纪律.md §4d）：

    test_bench_metrics 的 F4/F5 断言「槽位秒 / 并行度」，
    而这两个值读的是 work/solve/ledger.json —— **活的解题账本**。

    11-20 基准跑完   22989 / 2.0   → 绿
    11-23 跑几题验证 23386 / 2.02  → 红（放宽成区间）
    11-24 又跑 3 题  36306 / 3.14  → 再次红

第一次只「放宽区间」，病根（数据源会变）没动，于是必然再犯。

本测试把「不许改回活目录」钉死：
  1. test_bench_metrics 的 F4/F5 必须读 _baseline-*（冻结快照）
  2. 不许出现「用 SOLVE 算 slot/parallelism 再断言具体数值」的写法
  3. 冻结基线真存在时，能算出合理量级（反向验证判据本身可用）
"""
import json
import re
import sys
from pathlib import Path

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
BM_TEST = ROOT / "board" / "tests" / "test_bench_metrics.py"
passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print("  %s %s%s" % ("OK" if cond else "XX", name,
                         ("   " + detail) if detail and not cond else ""))


def main() -> int:
    check("test_bench_metrics.py 存在", BM_TEST.is_file())
    if not BM_TEST.is_file():
        return report()
    src = BM_TEST.read_text(encoding="utf-8", errors="replace")

    print("1) F4/F5 必须读冻结基线，不许读活目录")
    # 找到 F4/F5 所在区段（从 F4 的 ck 往前找 2000 字符）
    i_f4 = src.find("F4 ")
    check("找到了 F4 断言", i_f4 > 0, str(i_f4))
    seg = src[max(0, i_f4 - 2000): i_f4 + 300]
    check("F4/F5 区段引用了 _baseline-", "_baseline-" in seg,
          seg[-300:] if "_baseline-" not in seg else "")
    # 反向：这段里不许出现「用活目录的 slot_seconds 做区间断言」
    bad = re.search(r"20000\s*<=\s*_slot\s*<=", seg)
    check("没有「活目录 _slot 区间断言」残留", bad is None,
          bad.group(0) if bad else "")

    print("2) 静态：不许把活账本当基线（关键调用点检查）")
    # collect(SOLVE...) 本身合法（F1-F3 要它），但不许用它的 slot 做区间断言
    check("F1~F3 仍可用活目录（会话数/墙钟）",
          "SOLVE" in src and "session_count" in src)

    print("3) 反向：冻结基线存在时能量出合理量级")
    bases = sorted((ROOT / "work").glob("_baseline-*/ledger.json"), reverse=True)
    if not bases:
        print("  · 跳过（本机无 work/_baseline-*/ledger.json）")
    else:
        d = json.loads(bases[0].read_text(encoding="utf-8", errors="replace"))
        sec = sum((v.get("total_sec") or 0) for v in d.values() if isinstance(v, dict))
        check("冻结基线有 100+ 题", len(d) >= 100, str(len(d)))
        check("冻结基线槽位秒在 2 万~3 万", 20000 <= sec <= 30000, round(sec, 1))
        check("冻结基线不会被写（只读快照）",
              "baseline" in str(bases[0]).replace("\\", "/"),
              str(bases[0]))

    print("4) 文档：纪律已写进 CONTRIBUTING 与工程纪律")
    ct = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8", errors="replace")
    cd = (ROOT / "docs" / "经验-工程纪律.md").read_text(encoding="utf-8", errors="replace")
    check("CONTRIBUTING 有「基线断言必须绑冻结快照」", "冻结快照" in ct)
    check("工程纪律有 §4d", "4d." in cd and "冻结快照" in cd)
    check("文档点明了「放宽区间是治标」", "治标" in cd or "治标" in ct)

    return report()


def report() -> int:
    print()
    print("=" * 40)
    print("通过 %d / 失败 %d" % (len(passed), len(failed)))
    if failed:
        print("失败项：" + " | ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
