#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""flagq — flag 候选质量判分（T2 · 移植 hxbai engine/judge.py 的启发式内核）

为什么要有它
------------
`fgs.py` 里 `FLAG_SHAPE`（原 :2931）只判「形状对不对」，任何 `xxx{...}` 都能过。
但形状对 ≠ 是真 flag：诱饵串、低熵占位、代码片段、自然语言说明、扫射式同形串，
全都是「形状合规的错误答案」。一次错交的代价远高于漏交一次
（hxbai 原话：宁可漏、不可误判）。

设计红线（对齐 AGENTS.md §三 三条工程纪律）
------------------------------------------
1. **纯函数、零依赖**：不读盘、不联网、不读环境变量之外的东西；同样的输入永远同样输出。
2. **不改变既有判定**：本模块只产出「分数 + 理由」，**不自己决定 submit/skip**；
   是否拦由调用方（`fgs verified-flags` 等）决定，且默认只警告不拦。
3. **测试可穷举**：所有分支都能用构造串覆盖，不需要靶场。

与 hxbai 的差异（抄结构不抄数字）
--------------------------------
· hxbai 的 `FLAGISH_FLOOR=2.0` / `SPRAY_MIN=4` 是它自己赛制标定的，这里**保留数字但标注为默认值 + 可覆盖**。
· hxbai 的 `_KNOWN_PREFIXES` 是它玩的平台集合；我方前缀由调用方传入（`known_prefixes`），
  不写死——否则换个平台就全判低分。
· 我们额外把「为什么扣分」逐条返回（`reasons`），hxbai 只返回分数。
  理由：编排者要能看懂为什么被判死，否则会反复和机器较劲。
"""

from __future__ import annotations

import re

# ── 可调参数（默认值来自 hxbai engine/judge.py:25-26，非真理，按赛制重标）──
FLAGISH_FLOOR = 2.0     # 低于此分 = 基本可以确定不是真 flag
SPRAY_MIN = 4           # 同形状候选达到这个数 → 判定为「扫射」，全部降级

# 花括号内层提取：`前缀{内层}`
# ★ 2026-11-05 修：内层允许空格（原 [^}\s] 把含空格的内层整条跳过，
#   导致「flag{flag goes here please}」被判成「形状都不像」而不是「是自然语言」——
#   结论碰巧都是拒，但理由错了，人看不懂为什么被拒）
_INNER_OF = re.compile(r"([A-Za-z0-9_]{2,})\{([^}]+)\}")

# 诱饵式前缀：not/no/fake/dummy/decoy/... 后面只跟分隔符
_BAIT_AFFIX = re.compile(
    r"^(n|no|non|not|fake|faux|dummy|decoy|invalid|wrong|bad|"
    r"sample|example|test|honeypot|trap|bait|nota)[\W_]*$"
)
_BAIT_WORDS = frozenset((
    # ★ 2026-11-05 修：初版词表漏了 not/no/non/nota —— 正则 _BAIT_AFFIX 里有这些分支，
    #   但词表是另一套判据，剥壳后剩下的 "not" 认不出来 → notflag 逃过判定。
    #   两套判据必须同源，否则就是漏网的口子。
    "not", "no", "non", "nota", "n",
    "fake", "faux", "dummy", "decoy", "honeypot", "trap", "bait",
    "invalid", "example", "sample", "test", "nope", "garbage", "junk",
    "wrong", "bad", "placeholder", "redacted",
))

# 正文在「描述」占位符而不是「报告」拿到 flag（与 fgs.py 的 PLACEHOLDER_CTX_RX 同义）
_CTX_RX = re.compile(
    r"(占位|样例|示例|示范|不是真|非真实|假的|假\s*flag|别提交|不要提交|待填|待替换|"
    r"placeholder|example|ill?ustrat|dummy|redacted)", re.I)

_CJK_RX = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff]")

# shell 元字符出现在花括号内 = 大概率是代码片段而不是 flag
_SHELL_META = set("&?*|;<>$`'\" ")

# 十六进制 / UUID 形状
_HEXISH = re.compile(r"[0-9a-f-]{8,}", re.I)


def _class_key(token: str) -> str:
    """同形聚类键：只保留前缀 + 内层长度档，用来识别「扫射」。
    两个 `HTB{same_length_inner}` 形状的串会被归到同一类。"""
    m = _INNER_OF.search(token or "")
    if not m:
        return ""
    prefix = m.group(1).lower()
    n = len(m.group(2))
    return f"{prefix}:{n // 4}"


def decoy_prefix(token: str) -> bool:
    """前缀本身是个诱饵词（notflag / fake_flag / dummy）。

    ★ 判定三步（2026-11-05 修：初版漏判 notflag）：
      ① 整串前缀就是纯诱饵词（fake / dummy / n）
      ② 前缀里带诱饵词（fake_flag / trap-it）
      ③ 前缀去掉 flag/ctf 等核心词后，**剩下的核仍是诱饵词**
         —— notflag → 去 flag → "not" → 诱饵；not_a_flag → 去 flag → "not_a" → 首段 "not" → 诱饵
    """
    if not token:
        return False
    prefix = (token.split("{", 1)[0] or "").strip()
    low = prefix.lower()
    if not low:
        return False
    if _BAIT_AFFIX.match(low):
        return True
    parts = set(re.split(r"[\W_]+", low))
    if parts & _BAIT_WORDS:
        return True
    # ③ 剥掉核心词后再看剩下的段
    stripped = re.sub(r"(flag|ctf|htb)", " ", low)
    rest = set(re.split(r"[\W_]+", stripped)) - {""}
    return bool(rest & _BAIT_WORDS)


def natural_language_inner(token: str) -> bool:
    """内层是自然语言（中文 ≥4 字 / 英文 ≥3 个单词）→ 是说明文字，不是 flag。"""
    m = _INNER_OF.search(token or "")
    inner = (m.group(2) if m else "") or ""
    if not inner:
        return False
    if len(_CJK_RX.findall(inner)) >= 4:
        return True
    if " " in inner:
        words = sum(1 for t in inner.split() if t.isascii() and t.isalpha())
        return words >= 3
    return False


def ambiguous_inner(token: str) -> bool:
    """内层形如 some_words_like_this（≥3 段纯字母）→ 可疑，但不一定假。"""
    m = _INNER_OF.search(token or "")
    inner = (m.group(2) if m else "") or ""
    if not inner:
        return False
    if "-" in inner or "_" in inner:
        segs = [t for t in inner.replace("-", " ").replace("_", " ").split()]
        pure = sum(1 for t in segs if t.isascii() and t.isalpha())
        has_entropy = any(len(t) >= 4 and not t.isalpha() for t in segs)
        return pure >= 3 and not has_entropy
    return False


def flagishness(token: str, known_prefixes=None) -> float:
    """给一个候选打「像不像真 flag」的分。分数越高越可信。

    >>> flagishness("HTB{abc123def456}", {"htb"}) > FLAGISH_FLOOR
    True
    >>> flagishness("notflag{abc123}", {"htb"}) < FLAGISH_FLOOR
    True
    """
    m = _INNER_OF.search(token or "")
    if not m:
        return -99.0
    prefix, inner = m.group(1), m.group(2)
    low_prefix = prefix.lower()
    known = {p.lower() for p in (known_prefixes or ())}
    score = 0.0

    # 前缀可信度（+3 / 0）
    if low_prefix in known or "flag" in low_prefix or "ctf" in low_prefix:
        score += 3
    if any(c.isdigit() for c in inner):
        score += 1
    if _HEXISH.fullmatch(inner):
        score += 2
    if "_" in inner or "-" in inner:
        score += 0.5
    if " " in inner:
        score -= 0.5
    if decoy_prefix(token):
        score -= 5
    elif known and low_prefix not in known and "flag" not in low_prefix and "ctf" not in low_prefix:
        score -= 2.5
    if inner and not any(c.isalnum() for c in inner):
        score -= 5
    bad = _SHELL_META & set(inner)
    if bad:
        # ★ 2026-11-05 修：代码片段是强信号，原先 -=1.0*len 太轻（1 个元字符只扣 1，
        #   加上前缀 +3 与数字 +1 仍能压线通过）。改为「任何 shell 元字符一票否决级扣分」。
        score -= 5.0 * len(bad)
    if "_" in inner and "-" in inner:
        score -= 0.6
    return score


def explain(token: str, known_prefixes=None) -> list:
    """返回这条候选被扣分/加分的逐条理由（给人看，不是给机器看）。"""
    m = _INNER_OF.search(token or "")
    if not m:
        return ["形状都不像 flag（没匹配到 前缀{内层}）"]
    prefix, inner = m.group(1), m.group(2)
    low = prefix.lower()
    known = {p.lower() for p in (known_prefixes or ())}
    out = []
    if low in known or "flag" in low or "ctf" in low:
        out.append(f"+ 前缀 {prefix} 可信")
    elif known:
        out.append(f"- 前缀 {prefix} 不在已知集合 {sorted(known)} 里")
    if any(c.isdigit() for c in inner):
        out.append("+ 内层含数字")
    if _HEXISH.fullmatch(inner):
        out.append("+ 内层是十六进制/UUID 形状")
    if decoy_prefix(token):
        out.append(f"- 前缀含诱饵词（{prefix}）")
    if natural_language_inner(token):
        out.append("- 内层是自然语言（说明文字，不是 flag）")
    if ambiguous_inner(token):
        out.append("- 内层是多个纯字母分段（像描述而非随机串）")
    bad = _SHELL_META & set(inner)
    if bad:
        out.append(f"- 内层含 shell 元字符 {sorted(bad)}（像代码片段）")
    return out


def judge(candidates, known_prefixes=None, floor=None, spray_min=None) -> dict:
    """批量判分。**不修改输入，不决定提交**，只给结论。

    返回:
      kept       通过质量门的候选（保持原顺序）
      dropped    [(候选, 理由列表)]
      sprayed    {聚类键: 数量} —— 被判为扫射的组
      known_prefixes  本次使用的前缀集合（便于复现）
    """
    floor = FLAGISH_FLOOR if floor is None else floor
    spray_min = SPRAY_MIN if spray_min is None else spray_min
    cands = [c for c in (candidates or ()) if c]

    # 先按「同形聚类」统计，判定扫射
    clusters = {}
    for c in cands:
        clusters[_class_key(c)] = clusters.get(_class_key(c), 0) + 1
    sprayed = {k: v for k, v in clusters.items() if k and v >= spray_min}

    kept, dropped = [], []
    for c in cands:
        why = []
        if _CTX_RX.search(c):
            why.append("正文在描述占位/样例，不是报告自己拿到")
        if natural_language_inner(c):
            why.append("内层是自然语言")
        if decoy_prefix(c):
            why.append("前缀是诱饵词")
        score = flagishness(c, known_prefixes)
        if score < floor:
            why.append(f"可信度 {score:.1f} < 门槛 {floor}")
        key = _class_key(c)
        if key in sprayed:
            why.append(f"同形状候选 {sprayed[key]} 个（≥{spray_min}）= 扫射")
        if why:
            dropped.append((c, why))
        else:
            kept.append(c)

    return {"kept": kept, "dropped": dropped, "sprayed": sprayed,
            "known_prefixes": sorted({p.lower() for p in (known_prefixes or ())})}


if __name__ == "__main__":
    import sys
    import json as _json
    toks = [ln.strip() for ln in sys.stdin if ln.strip()]
    print(_json.dumps(judge(toks), ensure_ascii=False, indent=2))
