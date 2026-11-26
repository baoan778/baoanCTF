#!/usr/bin/env python3
"""reflect.FILTER_BLOCKED 的回归锁 —— 治「语义级不可解」这类判不出来的死法。

跑法：    python3 board/tests/test_reflect_filter.py

为什么要有它
-----------
2026-11-23 手工取证 xben-010：它的输入过滤器把全部 26 个字母 + 数字 + < >
都封了（逐字符探测实证），而任何 XSS payload 都需要字母。它跑了 6 轮、
提交 3 个不同候选全被平台判错。这是**题坏了**，不是模型不行 ——
但旧判据只认连接层故障，认不出来，于是 agent 继续在死路上烧时间。

这段实现原本只写了一半（算了 filter_hits 却从没用），本测试把它钉死：

  · FILTER_BLOCKED 是**独立判定**，不是 BROKEN_CHALLENGE 的子类
    （证据相反：BROKEN 是连不上，FILTER 是活着但拒输入）
  · 阈值是 3 次（一次试错 / 两次换写法 / 三次说明规则封死了空间）
  · **不许标废** —— 有些题故意把黑名单当考点，误判的代价是放弃可解题
  · 优先级高于 SAME_METHOD / MULTI_METHOD（面都换不动了，说「换面」是文不对题）
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
sys.path.insert(0, str(ROOT / "tools"))

import reflect  # noqa: E402

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print("  %s %s%s" % ("OK" if cond else "XX", name,
                         ("   " + detail) if detail and not cond else ""))


def _mk(outs):
    """把若干段工具输出喂进 analyze 的内部判据（不碰真实会话文件）。"""
    pat = re.compile(
        r"Sorry you can(?:not|'t) use|"
        r"invalid characters?|"
        r"blacklist|forbidden characters?|"
        r"blocked by.*(?:filter|waf)",
        re.I,
    )
    return sum(1 for t in outs if pat.search(t))


def main() -> int:
    print("1) 常量与判据")
    check("FILTER_HIT_MIN 存在且为 3", reflect.FILTER_HIT_MIN == 3,
          str(getattr(reflect, "FILTER_HIT_MIN", None)))

    deny = "Sorry you cannot use the character '<'"
    check("典型拒绝提示能命中", _mk([deny]) == 1)
    check("invalid characters 能命中", _mk(["invalid characters in payload"]) == 1)
    check("blacklist 能命中", _mk(["blocked: blacklist rule 7"]) == 1)
    check("普通 500 不命中", _mk(["HTTP/1.1 500 Internal Server Error"]) == 0)
    check("普通 200 不命中", _mk(["HTTP/1.1 200 OK\n<html>hi</html>"]) == 0)

    print("2) render：FILTER_BLOCKED 的文案边界")
    info = {"code": "p", "session": "s.jsonl", "commands": 40,
            "surfaces": {"xss": 12}, "missing_tool": [],
            "verdict": "FILTER_BLOCKED", "filter_hits": 9,
            "filter_lines": [deny]}
    out = reflect.render(info)
    check("有判定标题", "FILTER_BLOCKED" in out or "输入过滤器" in out)
    check("贴出被拒的原始行", deny[:20] in out)
    check("写明「应用活着」", "活着" in out)
    check("**不**说标废（误判代价是放弃可解题）",
          "标废" not in out.replace("不标废", ""))
    check("给了字符级探测这一步", "字符级探测" in out)
    check("给了换载体的方向", "载体" in out)
    check("明确要求先人工核", "人工核" in out)

    print("3) 优先级：FILTER_BLOCKED 压过 SAME_METHOD / MULTI_METHOD")
    src = (ROOT / "tools" / "reflect.py").read_text(encoding="utf-8", errors="replace")
    i_f = src.find('verdict = "FILTER_BLOCKED"')
    i_s = src.find('verdict = "SAME_METHOD"')
    i_m = src.find('verdict = "MULTI_METHOD"')
    check("三处都在", min(i_f, i_s, i_m) > 0)
    check("FILTER 在 SAME_METHOD 之前", 0 < i_f < i_s, "%d vs %d" % (i_f, i_s))
    check("FILTER 在 MULTI_METHOD 之前", 0 < i_f < i_m, "%d vs %d" % (i_f, i_m))

    print("4) 阈值边界：2 次不算，3 次才算")
    # 判据是 filter_hits >= FILTER_HIT_MIN
    check("2 次不达阈值", (2 >= reflect.FILTER_HIT_MIN) is False)
    check("3 次达阈值", (3 >= reflect.FILTER_HIT_MIN) is True)

    print("5) 反向：连接层故障仍优先判 BROKEN（不许被 FILTER 抢走）")
    # broken 的条件是 fatal>=2 且 alive==0；filter_blocked 必须显式排除 broken。
    # ★ 2026-11-24：判据改成多行表达式后，原先 "filter_blocked = filter_hits" 的
    #   整串匹配失效（那正是「测试绑死实现细节」的典型）。改成按**语义**断言：
    #   filter_blocked 的赋值块里要出现 tiny 判据、且要带 not broken。
    j = src.find("filter_blocked = (")
    block = src[j:j + 260] if j > 0 else ""
    check("filter_blocked 是多行表达式（可读）", j > 0, "未找到 filter_blocked = (")
    check("filter_blocked 显式排除 broken", "not broken" in block, block[:160])
    check("filter_blocked 要求极小输入判据", "tiny_hits" in block, block[:160])
    check("filter_blocked 在 if broken: 之前判定", 0 < j < src.find("if broken:"))

    print("7) 真实数据回放的教训：极小输入才算封死（2026-11-24）")
    # 全量回放 104 题后发现的误判：004/008/013 只封了 script/onerror/alert
    # 这几个**关键字**，换载体就能过 —— 那是正常考点，不该判 FILTER_BLOCKED。
    # 真封死的特征是「极小输入也被拒」：087 拒单字符 S 与 >，010 拒单引号。
    tiny = reflect._is_tiny_denial
    check("单字符 S 算极小", tiny("Sorry you can't use: 'S'") is True)
    check("单字符 > 算极小", tiny("Sorry you can't use: '>'") is True)
    check("单引号算极小", tiny("Sorry you can't use: '''") is True)
    check("script 不算（是黑名单关键字）", tiny("Sorry you can't use: 'script'") is False)
    check("onerror 不算", tiny("Sorry you can't use: 'onerror'") is False)
    check("alert 不算", tiny("Sorry you can't use: 'alert'") is False)
    check("无引号值时不误判", tiny("something unrelated") is False)
    check("空串不崩", tiny("") is False)
    check("FILTER_TINY_MIN 存在且为 2", reflect.FILTER_TINY_MIN == 2)
    check("判据要求 tiny_hits >= FILTER_TINY_MIN",
          "tiny_hits >= FILTER_TINY_MIN" in src)
    check("返回值带 filter_tiny（可审计）", '"filter_tiny"' in src)

    print("6) filter_hits 真的被用上了（治「算了不用」的半截实现）")
    check("filter_hits 出现在返回值里", '"filter_hits": filter_hits' in src)
    check("filter_lines 出现在返回值里", '"filter_lines"' in src)
    uses = src.count("filter_hits")
    check("filter_hits 至少出现 3 次（算/判/返回）", uses >= 3, "count=%d" % uses)

    print()
    print("=" * 40)
    print("通过 %d / 失败 %d" % (len(passed), len(failed)))
    if failed:
        print("失败项：" + " | ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
