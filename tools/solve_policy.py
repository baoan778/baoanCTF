#!/usr/bin/env python3
"""solve_policy.py —— 解题策略的**唯一事实来源**（优化 13）。

## 为什么要有这个文件

预算/模型这两条策略在仓库里被抄了三份，而且**已经漂移了**（2026-11-20 实测）：

| 位置 | DIFF_BUDGET | cap | 用途 |
|---|---|---|---|
| tools/parallel_solver.py | 150/300/480 | hard 960，其他 640 | **真正下发给 shard 的预算** |
| tools/bench/live.py      | 120/180/300 | hard 600，其他 420 | 实况页「待解队列」每题的预算列 |
| tools/bench/todo.py      | 120/180/300 | hard 600，其他 420 | 命令行版的待办清单 |

后果：优化 #10 把预算从 120/180/300 抬到 150/300/480 之后，
**实况页和 todo 仍在按旧数显示**——用户看到的「这题下一轮给多少秒」是错的，
而且错的方向恰好是「比实际更小」，看起来像预算还没调过。

## 口径说明（为什么是这三个数）

数据来自 run 50607 的**干净轮 only**（n=85，5943s / 75 旗；污染轮 88 次
10880s 只出 2 旗，必须剔除，见 optimizations/04）。截断模拟：
  easy   38 旗/1306s：cap90→36、cap120→37、cap150→37、cap180→38
  medium 33 旗/3431s：cap120→29、cap150→30、cap180→31、cap300→32、cap420→33
  hard    4 旗/1206s：cap150→1、cap180→2、cap240→3、cap300→3、cap480→4
原 120/180/300 只保住 37+31+3=71 旗；150/300/480 保住 73/75。

## 递增规则

第 0 次尝试给基线；之后每次 ×1.5，最多 3 步（150→225→337→506），
上限 = 基线 ×2（hard 960 / 其他 640）。**上限必须跟着基线走**，
否则改基线等于没改（早期 cap 写死 600/420，把 480 的 hard 基线直接卡死）。
"""
from __future__ import annotations

import os  # noqa: PLC0415  # ★ 2026-11-23：模块级常量（ZERO_STREAK_STOP）要用，
#           不能只在函数里局部 import —— 我第一版就是这么写的，直接 NameError 崩在导入期。

DIFF_BUDGET = {"easy": 150, "medium": 300, "hard": 480}
DEFAULT_BUDGET = 150          # 难度字段缺失时的基线
RETRY_FACTOR = 1.5
RETRY_STEPS = 3               # 最多递增 3 步

# ★ 2026-11-21 R4-C「第 4 轮起硬封顶」：连续重试的题到第 4 轮一律给 180s。
#   为什么要有这道顶（138 run 回放，见 历史复盘 R4-02）：k>=4 的 6 个轮
#   （092 k=4/5、066 k=4、081 k=4/5、056 k=4）**全部零产出**，却各拿 596~929s，
#   合计 4400s；而 k>=4 唯一命中的 056 k=5 只用 21s。180s 已是它耗时的 8.5 倍。
#   为什么不是「第 4 轮直接不跑」：那会丢 056 的 300 分（回放 P4 实测）。
#   与 R3-A 的关系：R3-A 让 lvl 回落 = 降加速度，这里降的是天花板，两者不冲突。
RETRY_HARD_CAP_K = 4          # 从第几轮（1 基）起进入封顶档
RETRY_HARD_CAP_SEC = 180      # 封顶秒数


def cap_for(diff: str) -> int:
    """单题预算上限 = 基线 ×2（够 3 步递增用）。"""
    return 960 if (diff or "").lower() == "hard" else 640


def base_for(diff: str) -> int:
    return DIFF_BUDGET.get((diff or "").lower(), DEFAULT_BUDGET)


def retry_cap(tries: int) -> int | None:
    """第 tries 次尝试（0 基）是否已进「重试封顶」档；是则返回秒数上限，否则 None。

    ★ R4-C：只看次数，不看难度、不看 lvl —— 这是**最后一道**天花板，
      任何更聪明的判据都不许把它顶开（否则越难越贵的老毛病会从别处漏回来）。
    """
    return RETRY_HARD_CAP_SEC if int(tries) >= RETRY_HARD_CAP_K - 1 else None


def budget_for(diff: str, tries: int, lvl: int | None = None) -> int:
    """第 tries 次尝试该给多少秒（tries=0 为首次）。

    ★ 2026-11-20 R3-A「进展感知预算」：多给一个 lvl（**连续有进展的轮数**）。
      · lvl 缺省（None）= 老行为（按尝试次数递增）—— bench/live.py、bench/todo.py
        和 _verify_all.py 的既有断言不动，避免三份副本再次漂移；
      · 给了 lvl = 按「上一轮有没有进展」算出来的档位走（见 solve_ledger.note_round）。

    为什么要换判据（138 run 回放，见 历史复盘 R3-02）：
      「按尝试次数递增」把**超时轮**也算成一次值得加时的尝试，于是越啃不动越贵
      （056 第 2/3/4 轮 640s 全超时，共烧 1985s 零产出）。改成「上一轮有进展才升档」
      后，同一批 run 得分一分不丢（26900），墙钟 24281s → 23261s。
      「有进展」= rc==0 或 候选>0 或 落盘事实>0（三者取或，见 sdkshard.solve_one）。
    """
    base = base_for(diff)
    if lvl is not None:
        # 第 0 轮（首次）永远 base；档位由账本给（第 1 轮固定 1 档 = ×1.5）
        if tries <= 0:
            return base
        b = min(int(base * (RETRY_FACTOR ** min(max(int(lvl), 0), RETRY_STEPS))),
                cap_for(diff))
    elif tries <= 0:
        return base
    else:
        b = min(int(base * (RETRY_FACTOR ** min(int(tries), RETRY_STEPS))), cap_for(diff))
    # ★ R4-C：重试到第 4 轮起，一切算法给出的值都被 180s 压住（详见 RETRY_HARD_CAP_K 注释）。
    _cap = retry_cap(tries)
    return min(b, _cap) if _cap is not None else b


# ★ 模型档位：连败 2 次以上换更强模型（实测剩余的都是「同一个 flash 反复啃不动」
#   的硬骨头，换模型比加时间更有效）。只在没有 A/B 数据前沿用这条经验规则。
PRO_MODEL = "deepseek-v4-pro"
FLASH_MODEL = "deepseek-flash"
PRO_AFTER_TRIES = 2


def asset_for(prev_rc: int | None, prev_cands: int = 0,
              prev_facts: int = 0) -> bool:
    """上一轮是否给下一轮留下了**可继承的资产**（R4-B 的 pro 门）。

    ★ 2026-11-21 R4-B：原来只按尝试次数升 pro，于是「上一轮超时、零候选、零事实」
      的题照样拿最贵的模型重跑。138 run 实测 14 个 pro 段里有 2 段是这种零资产轮
      （081 k=5：$0.0820 / 693s / 0 分）。

    判据选 **rc==0 或 候选>0**（A2），不把 facts 单独算进去：
      · 把 facts 也算进去只多省 $0.0820（1.6%），且「落 1~2 条事实」几乎每轮都成立
        → 门形同虚设；
      · 只看 rc==0（A1）会砍掉「超时但落了候选」的 056 k=5（那轮 22s 命中 300 分）。
      · 14 段逐段回放：A2 砍掉的 2 段零产出，4 个命中段全保留。
    prev_rc 为 None（没跑过 / 无记录）= 视为有资产，保持老行为。
    """
    if prev_rc is None:
        return True
    return int(prev_rc) == 0 or int(prev_cands) > 0


# ═══════════════════════════════════════════════════════════════════════════
# D4（2026-11-23，操作者拍板）：单题早停 = **连续 N 轮零资产**
#
# 为什么选这一条而不是「按 ROI 硬停」：
#   ROI 是**跨题**排序用的（先做哪道），拿它当**单题**止损会把「贵但快出旗」的题
#   一起砍掉。而 056 是「第 4 轮末 $0.4487、第 5 轮才出旗」的实证反例 ——
#   任何「看累计钱/看累计时间」的硬停都会误杀它。
#   「连续 N 轮零资产」只看**最近 N 轮有没有留下东西**，与历史花费无关，
#   所以 056 那种「每轮都有进展」的题永远不触发。
#
# 与 R7-B（stop_by_cost）的关系：两者是**或**关系，不是替代。
#   · stop_by_cost：钱花超了**且**上一轮零资产 → 停（看钱）
#   · stop_by_zero：连续 N 轮零资产 → 停（看连续性，不看钱）
#   实测场景差别：一道 $0.10 的题连啃 6 轮零资产，stop_by_cost 永不触发
#   （没花到 $0.45），但它同样在烧槽位 —— 这才是 D4 要治的。
#
# N 取 3 的依据（138 run 回放，同 R4-C 封顶那一批）：
#   k>=4 的 6 个轮**全部零产出**，却各拿 596~929s，合计 4400s；
#   而 k>=4 唯一命中（056 k=5）那一轮是「有资产」的，不在这条判据的射程内。
#   取 3 而不是 2：给「先超时、第 2 轮才出线索」的题留一次机会
#   （实测 21 道题是"先超时后成功"，其中次轮出旗 12 道）。
# ═══════════════════════════════════════════════════════════════════════════
ZERO_STREAK_STOP = int(os.environ.get("PI_SOLVE_ZERO_STREAK", "3"))

# ★★ 2026-11-23 实测修正（回放抓到 D4 会误杀 056，必须记）：
#   我第一版按 asset_for（rc==0 或 候选>0）判"资产"，回放历史日志发现它会让
#   xben-056 在第 3 轮被停 —— 而 056 正是**第 5 轮 22 秒出旗拿 300 分**的那道题。
#   根因：056 的 5 轮走 --continue **共用同一个会话**（实测 1 个 jsonl、5 次 user 输入、
#   每轮新增 69/41/34/35 条消息），它的资产是**累积在上下文里的侦察与试错结论**，
#   而不是 facts.md（facts 只有 1 条）也不是 rc/候选（4 轮全 124）。
#   ⇒ "零资产"必须把"上下文还在长"算进去，否则判据治的是"真没进展"，却误伤了
#     "进展全在对话里"这一类题 —— 而后者恰恰是续跑机制存在的理由。
#   判据：本轮结束时会话文件比本轮开始时增长 >= ZERO_STREAK_MIN_GROWTH 字节 = 有资产。
ZERO_STREAK_MIN_GROWTH = int(os.environ.get("PI_SOLVE_ZERO_GROWTH", "2000"))


def stop_by_zero_rounds(zero_streak: int) -> bool:
    """连续 zero_streak 轮零资产 → 放弃这一题（不再起新一轮）。

    zero_streak —— 连续「上一轮什么都没留下」的轮数（账本 zero_streak 字段）
    · 0 / 1 / 2 → False（还有机会）
    · >= N    → True
    · N <= 0  → 关闭本判据（False），用于 A/B 对照
    """
    try:
        n = int(ZERO_STREAK_STOP)
    except (TypeError, ValueError):
        n = 3
    if n <= 0:
        return False
    try:
        return int(zero_streak or 0) >= n
    except (TypeError, ValueError):
        return False


def model_for(tries: int, asset: bool = True) -> str:
    """第 tries 次尝试用哪个模型。

    ★ R4-B：新增 asset（上一轮有没有资产，见 asset_for）。默认 True = 老行为，
      这样 bench/live.py、bench/todo.py、_verify_all.py 等既有调用点不用改，
      也不会出现「三份副本再次漂移」（R3-A 的教训）。
    """
    if int(tries) < PRO_AFTER_TRIES:
        return FLASH_MODEL
    return PRO_MODEL if asset else FLASH_MODEL


# ═══════════════════════════════════════════════════════════════════════════
# R7-A（2026-11-21）思考档位分档
#
# 实测依据（138 run，按会话轮序切分；见 历史复盘 R7-01）：
#   · 思维链 token 占**输出**的 64.5%（935,932 / 1,452,042），折 ≈$1.66 = 总成本 32%；
#   · 按轮序看，reasoning/out 从首轮 56% 一路涨到第 5 轮 85%：
#       #1 56% / #2 67% / #3 82% / #4 77% / #5 85%
#   · 第 3 轮及以后只有 16 轮，却吃掉 $2.989（58.4% 成本）。
#
# 为什么**不**全场降档：80/99 已解题是**首轮**解出的（平均 71s），
# 首轮降档等于拿"已经很快的那条路"去冒险换小钱（实测只省 5~8%）。
# 所以只对"已经证明没进展的重试轮"降档 —— 它与 R4-B（无资产不升 pro）、
# R4-C（第 4 轮起 180s 封顶）是同一条策略线的第三件：**没进展就别烧贵的**。
#
# 实测收益（系数按 medium≈high 的 0.5 计，保守；对 $5.1195 的分布）：
#   零资产重试轮降 medium → 省 $0.644（12.6%）  ← ★ 已被 R11-02 实测否证，见下
#   若系数取 0.25 → 省 $0.966（18.9%）
#   ⚠ 系数是**估算**（没有同题 A/B），所以默认值保守，且可一键关掉对照。
# ═══════════════════════════════════════════════════════════════════════════
# ★ R11（2026-11-22 操作者要求）：思考档位改为**可自定义**。
#   三个档位都能用环境变量改，不必改代码：
#     PI_THINK_FIRST        首轮档位           默认 high
#     PI_THINK_RETRY_ASSET  重试且有资产       默认 high
#     PI_THINK_RETRY_COLD   重试且零资产       默认 medium
#   合法值即 THINK_VALUES；非法值**忽略并回落默认**（绝不把错值传给 pi）。
#   为什么要有默认值兜底：环境变量写错（拼错/空串）时，宁可跑默认档，
#   也不能让 `--thinking ""` 变成 pi 的启动错误 —— 那会把整轮变成白跑。
def _think_env(name: str, default: str) -> str:
    """读一个思考档环境变量；非法值静默回落默认（不抛）。"""
    import os  # noqa: PLC0415
    v = (os.environ.get(name) or "").strip().lower()
    return v if v in THINK_VALUES else default


THINK_VALUES = ("off", "minimal", "low", "medium", "high", "xhigh", "max")
THINK_FIRST = _think_env("PI_THINK_FIRST", "high")            # 首轮（80/99 首轮解出，默认不动）
THINK_RETRY_ASSET = _think_env("PI_THINK_RETRY_ASSET", "high")  # 重试有资产（056/088 靠这种轮出旗）
THINK_RETRY_COLD = _think_env("PI_THINK_RETRY_COLD", "high")    # ★ R11 A/B 实测后改为 high：
#   降 medium 在 48 次/臂 的受控实验里 **既没变快（CI 跨 0）又更贵 11.1%**，长尾也更差，
#   详见 历史复盘 R11-02-AB-思考档.md。操作者 2026-11-22 明确要求：默认维持 high 或极高。


def thinking_for(tries: int, asset: bool = True) -> str:
    """第 tries 次尝试（0 基）用哪个思考档位。

    与 model_for / budget_for 用**同一个** asset 判据（solve_policy.asset_for），
    三件策略必须同源，否则会出现"用便宜模型 + 200s 封顶 + 高档思考"这种自相矛盾的组合。
    """
    if int(tries) <= 0:
        return THINK_FIRST
    return THINK_RETRY_ASSET if asset else THINK_RETRY_COLD


# ═══════════════════════════════════════════════════════════════════════════
# R7-B（2026-11-21）单题成本上限（唯一带"止损"性质的策略）
#
# 实测依据：104 题里 4 道烧掉 $2.555（49.9%）——
#   092 $0.824（5 轮未解）/ 066 $0.753（4 轮未解）/ 088 $0.502（3 轮已解）/ 056 $0.476（5 轮已解）
#
# 回放（真实逐轮成本，见 历史复盘 R7-01）：
#   cap=$0.30 → 省 15.7%，丢 1 题（056，$0.476/5 轮）
#   cap=$0.40 → 省 11.5%，丢 1 题（056）
#   cap=$0.45 → 省 11.0%，**丢 0 题**   ← 采用
#   cap=$0.50 → 省 4.5%，丢 0 题
#
# 为什么是"花超了**且本轮零资产**"才停，而不是纯看钱：
#   056 的累计花费在第 4 轮结束时是 $0.4487，正好卡在 0.45 之下 —— 纯看钱它会活到第 5 轮
#   并出旗（$0.4757）。判据加"零资产"后，有进展的轮永远不会因为"钱花多了"被砍，
#   只在"上一轮又什么都没留下"时才止损。
# ═══════════════════════════════════════════════════════════════════════════
COST_CAP_USD = 0.45
COST_CAP_MIN_USD = 0.20      # 分数为 0/缺失时的兜底下限（不给"$0 就停"这种空转）


def cost_cap(score: int | float | None = None) -> float:
    """单题累计成本上限。分值越高允许花越多（500 分题上限 0.5）。

    ★ 刻意做成"随分值线性、但有下限"：纯固定值会让 500 分题和高分难题被同等对待；
      纯按分值比例又会在低分题上过于吝啬（实测无此必要）。
    """
    try:
        s = float(score or 0)
    except (TypeError, ValueError):
        s = 0.0
    if s <= 0:
        return COST_CAP_USD
    return max(COST_CAP_USD, s * 0.001)


def stop_by_cost(spent: float, score: int | float | None = None,
                 last_round_asset: bool = True) -> bool:
    """是否该**放弃这一题**（不再起新一轮）。

    spent          —— 该题累计已花美元（账本 total_cost）
    last_round_asset —— 上一轮是否留下资产（solve_policy.asset_for）
    """
    try:
        s = float(spent or 0)
    except (TypeError, ValueError):
        s = 0.0
    if s <= 0:
        return False
    if last_round_asset:          # 有进展 → 永远不因成本砍（056 就靠这条活到出旗）
        return False
    return s >= cost_cap(score)


if __name__ == "__main__":
    import json
    print(json.dumps({
        "thinking": [thinking_for(t, True) for t in range(4)],
        "thinking_cold": [thinking_for(t, False) for t in range(4)],
        "cost_cap": {str(s): cost_cap(s) for s in (0, 200, 300, 500)},
        "diff_budget": DIFF_BUDGET,
        "cap": {d: cap_for(d) for d in DIFF_BUDGET},
        "sample": {d: [budget_for(d, t) for t in range(5)] for d in DIFF_BUDGET},
        "model": [model_for(t) for t in range(4)],
    }, ensure_ascii=False, indent=2))