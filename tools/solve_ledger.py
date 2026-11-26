#!/usr/bin/env python3
"""solve_ledger.py —— 解题账本：记录每题尝试次数，避免反复啃同一道失败题。

为什么必须有（2026-11-14 实测）：
  每轮都重新 list 题库，失败题仍排在最前面（按 unique_code 序），
  于是 3 个 shard 全被 xben-002/003/010 这三道啃不动的题占住，
  其它能得分的题排队干等。实测 14/104 时，3 个活跃容器全是失败过的题。

规则：
  · 每题记录 attempts / last_status；
  · 连续失败 >= MAX_ATTEMPTS 的题，本轮排到最后（不删，留给低峰重试）；
  · 成功即清零。

★ 2026-11-17 历史决策 #21（外部抖动 ≠ 自己失败）：账本分两类记。
  · own_failure —— 题是真的没解出来：涨 attempts / fails，耗时才进 total_sec，
    于是 roi_order 的「跑过折扣」与 pending_with_budget 的「抬预算换模型」都作用在它身上。
  · external_jitter —— 平台侧瞬时问题（容器名额满 / TSec 连接抖动 / 引擎崩溃重试耗尽 /
    引擎不可用）：**不涨 attempts，不涨 fails**，时间单独进 jitter_sec。
    否则好题会被算成「跑过没出、还很贵」→ ROI 排到后面，或者被当成「啃不动的硬骨头」
    抬预算换更贵的模型 —— 这是**选错题**级别的错，不是记账美观问题。
"""
from __future__ import annotations
import json, os, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "work" / "solve" / "ledger.json"
MAX_ATTEMPTS = int(os.environ.get("SOLVER_MAX_ATTEMPTS", "2"))
# 提交失败最多重解几轮（超过即沉底）—— 实测 xben-071 连吃 4×120s 白烧
MAX_REJECTED_RETRY = int(os.environ.get("SOLVER_MAX_REJECTED_RETRY", "2"))
# 外部抖动的 reason 标签（record() 也接受它，转发给 record_jitter）
JITTER = "external_jitter"
# ★ D4 的"资产"判据之一：上下文增长多少字节算有进展（见 solve_policy 的长注释）
#   为什么 2000B：实测 056 每轮 +34~69 条消息（数万字节），而"什么都没干"的轮
#   只会写几行日志（< 1KB）。2000 是高信噪阈，两边都不会误判。
ZERO_GROWTH_MIN = int(os.environ.get("PI_SOLVE_ZERO_GROWTH", "2000"))


class Ledger:
    def __init__(self) -> None:
        self.data: dict[str, dict] = {}
        try:
            if LEDGER.is_file():
                self.data = json.loads(LEDGER.read_text(encoding="utf-8"))
        except Exception:
            self.data = {}

    def save(self) -> None:
        try:
            LEDGER.parent.mkdir(parents=True, exist_ok=True)
            LEDGER.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def attempts(self, code: str) -> int:
        return int((self.data.get(code) or {}).get("attempts", 0))

    # ── R3-A 进展感知预算（2026-11-20）──────────────────────────────────────
    # 为什么要新增这两个方法：旧策略「预算 = base × 1.5^尝试次数」把**超时轮**
    # 也算成一次「值得加时」的尝试，于是越啃不动的题越贵，而它们照样超时。
    # 138 run 回放（历史复盘 R3-02）：改成「上一轮有进展才升档」后，
    # 得分一分不丢（26900），墙钟 24281s → 23261s（省 1020s）。
    # 「有进展」= rc==0 或 候选>0 或 落盘事实>0 —— 三者取或，由 sdkshard.solve_one 判定后传进来。
    def level(self, code: str) -> int:
        """连续有进展的轮数（下一次尝试的预算档位）。0 = 回到基线。"""
        return int((self.data.get(code) or {}).get("lvl", 0))

    def rounds(self, code: str) -> int:
        """本题实际跑完的轮数（与 attempts 不同：抖动/崩溃重跑不涨 attempts）。"""
        return int((self.data.get(code) or {}).get("rounds", 0))

    def zero_streak(self, code: str) -> int:
        """连续「零资产」轮数（D4 早停判据；0 = 上一轮有资产或题没跑过）。"""
        return int((self.data.get(code) or {}).get("zero_streak", 0) or 0)

    def last_round(self, code: str) -> dict | None:
        """上一轮的三项产出（rc / 候选数 / 落盘事实数）；没跑过返回 None。

        ★ R4-B（2026-11-21）：pro 升档要问「上一轮留下了什么资产」，而不是只问
          「试过几次」。三项原始值由 sdkshard 在收尾时写进来（note_round 的 kwargs），
          这里只负责取，判据本身在 solve_policy.asset_for —— 避免又抄一份。
        """
        e = self.data.get(code) or {}
        if int(e.get("rounds", 0)) <= 0:
            return None
        return {
            "rc": e.get("last_rc"),
            "cands": int(e.get("last_cands", 0) or 0),
            "facts": int(e.get("last_facts", 0) or 0),
        }

    def note_round(self, code: str, progressed: bool, rc: int | None = None,
                   cands: int | None = None, facts: int | None = None,
                   tokens: int | None = None, cost: float | None = None,
                   ctx_growth: int | None = None) -> None:
        """跑完一轮后记账：有进展 → 档位 +1（最多 RETRY_STEPS 档，由 policy 封顶）；
        没进展 → **回落到基线 0**，下一轮不再越喂越贵。

        ★ 第 0 轮（本题第一枪）结束后固定给 1 档：第二枪按 ×1.5 起（与旧行为一致，
          因为「第一次超时」通常确实值得再给多一点时间 —— 回放里 21 道题是
          「先超时后成功」，第二枪就加时是对的）。
        ★ 之后只看**上一轮**有没有进展：056 连续 3 轮超时 → 档位回 0，
          不再拿 640s 去撞同一堵墙（回放里它第 5 枪 22s 就出旗了）。
        """
        e = self.data.setdefault(code, {"attempts": 0, "fails": 0, "rejected": 0})
        if int(e.get("rounds", 0)) <= 0:
            e["lvl"] = 1
        else:
            # 档位再高也没用：solve_policy.budget_for 只认 min(lvl, RETRY_STEPS=3)，
            # 这里就截在 3，账本里读出来是「真实用到的档位」，复盘时不会被 4/5 迷惑。
            e["lvl"] = min(int(e.get("lvl", 0)) + 1, 3) if progressed else 0
        e["rounds"] = int(e.get("rounds", 0)) + 1
        e["last_progress"] = "yes" if progressed else "no"
        # ★ D4（2026-11-23，操作者拍板「按连续 N 轮零资产停」）：
        #   记「连续零资产轮数」，供 solve_policy.stop_by_zero_rounds 判早停。
        #   与 lvl 的区别：lvl 是**预算档位**（有进展才升，回落到 0 后靠 lvl 不会停题），
        #   这里是**止损计数**（只涨不清零，除非真拿到资产）。
        #   ★ 判据「有资产」与 asset_for 同源：rc==0 或 候选>0 才算资产；
        #     facts 单独不算（否则"落 1~2 条事实"几乎每轮都成立，门形同虚设 —— R4-B 实测）。
        # ★★ 2026-11-23 实测修正：光看 rc/候选 会误杀 056（它 4 轮全 124、facts 只 1 条，
        #   但 5 轮共用会话累积上下文，第 5 轮 22 秒出旗）。所以把**上下文增长**也算资产。
        _has_asset = bool(rc == 0 or (cands or 0) > 0
                          or int(ctx_growth or 0) >= ZERO_GROWTH_MIN)
        e["zero_streak"] = 0 if _has_asset else int(e.get("zero_streak", 0) or 0) + 1
        # ★ R4-B：把这一轮的 rc / 候选数 / 事实数也存下来，供下轮 model_for 判资产。
        #   只存不判 —— 判据在 solve_policy.asset_for，一份。
        if rc is not None:
            e["last_rc"] = int(rc)
        if cands is not None:
            e["last_cands"] = int(cands)
        if facts is not None:
            e["last_facts"] = int(facts)
        # ★ R7-B（2026-11-21，R6-02 §1 B 项）：把这一轮**花了多少钱**也记下来。
        #   为什么必须先记账再谈优化：R5-01 §2 的成本四分是**事后从会话 jsonl 算的**，
        #   账本里只有耗时（total_sec），于是 ROI 排序只看"分值/秒"，
        #   「一题烧了 $0.8 还没出」在排序上和一题"30 秒没出"没有区别 ——
        #   降档、早停这类成本优化失去判据。
        #   只累计**真实跑过**的轮（jitter 轮不传 tokens/cost，见 record_elapsed 的同类处理）。
        if tokens is not None and int(tokens) > 0:
            e["total_tokens"] = int(e.get("total_tokens", 0) or 0) + int(tokens)
        if cost is not None and float(cost) > 0:
            e["total_cost"] = round(float(e.get("total_cost", 0) or 0) + float(cost), 6)
        self.save()

    def record(self, code: str, ok: bool, reason: str = "") -> None:
        """记录一次尝试。

        reason:
          ""               —— 正常（无候选 = 没解出来）
          "rejected"       —— 有候选但**平台判错**（提交失败，必须重解）
        ★ 2026-11-14 用户明确要求：提交失败的题一定要重新解（可能是超时/正则误伤/瞬时错误耽误了），
          所以 submit 失败要单独计数，且**不按普通失败降权**，下一轮优先重解。
        """
        if reason == JITTER:      # ★ #21：外部抖动不算一次尝试，见 record_jitter()
            self.record_jitter(code)
            return
        e = self.data.setdefault(code, {"attempts": 0, "fails": 0, "rejected": 0})
        e["attempts"] = int(e.get("attempts", 0)) + 1
        if ok:
            e["fails"] = 0
            e["rejected"] = 0
        elif reason == "rejected":
            e["rejected"] = int(e.get("rejected", 0)) + 1
            e["fails"] = 0          # 判错≠没解出来，不按普通失败降权，下一轮优先重解
        else:
            e["fails"] = int(e.get("fails", 0)) + 1
        e["last"] = time.strftime("%H:%M:%S")
        self.save()

    def record_jitter(self, code: str, why: str = "") -> None:
        """记一次**外部抖动**：容器名额满 / 平台连接抖动 / 引擎崩溃重试耗尽 / 引擎不可用。

        ★ 历史决策 #21：这类事件不是「这题解不出来」，所以
          · **不涨 attempts**（否则下一轮会被当成第 N 次尝试，强制换攻击面 + 续跑旧会话）；
          · **不涨 fails**（否则连续抖两次就被沉底，热题变冷题）；
          · 只记 jitter 次数、原因与时间戳（用于排查「到底是平台还是题」）。
        """
        e = self.data.setdefault(code, {"attempts": 0, "fails": 0, "rejected": 0})
        e["jitter"] = int(e.get("jitter", 0)) + 1
        if why:
            e["last_jitter"] = str(why)[:160]
        e["last_jitter_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.save()

    def jitter(self, code: str) -> int:
        return int((self.data.get(code) or {}).get("jitter", 0))

    def jitter_info(self, code: str) -> dict:
        """给日志/doctor 用：抖动次数、累计抖动耗时、最近原因。"""
        e = self.data.get(code) or {}
        return {
            "jitter": int(e.get("jitter", 0)),
            "jitter_sec": round(float(e.get("jitter_sec", 0) or 0), 1),
            "jitter_runs": int(e.get("jitter_runs", 0) or 0),
            "last_jitter": e.get("last_jitter", ""),
            "last_jitter_at": e.get("last_jitter_at", ""),
        }

    def rank(self, codes: list[str]) -> list[str]:
        """排序：**提交失败过的题优先**，其次普通题，最后才是连续失败过多的题。

        ★ 2026-11-14 用户要求「提交失败的题一定要重新解」—— 它们不是解不出来，
          而是提交环节被耽误（超时/正则误伤/瞬时错误），所以要排在最前面重解。
        """
        retry, hot, cold = [], [], []
        for c in codes:
            e = self.data.get(c) or {}
            rejected = int(e.get("rejected", 0))
            fails = int(e.get("fails", 0))
            if rejected and rejected <= MAX_REJECTED_RETRY:
                retry.append(c)          # 提交失败过 → 优先重解（有上限，防死循环）
            elif fails >= MAX_ATTEMPTS:
                cold.append(c)           # 连败 → 沉底，只在没有热题时才出
            else:
                hot.append(c)
        return retry + hot + cold

    def record_elapsed(self, code: str, seconds: float, jitter: bool = False) -> None:
        """累计该题花掉的墙钟时间（用于算「分值/平均耗时」的 ROI）。

        jitter=True 时时间只进 jitter_sec / jitter_runs —— **不进 total_sec / runs**。
        这样「被平台抖动烧掉的时间」不会抬高该题的 avg_sec（#21）：否则一题因为容器满了
        白等 300s，ROI 就被打到 0.3，被排到队尾，而它其实一次都没真正跑过。

        ★ 2026-11-14 用户要求：跳过没价值的题，优先挑有把握快速解出的。
          判据不能只看尝试次数 —— 一题试 2 次各 600s（1200s）和试 5 次各 30s（150s）
          成本差 8 倍。必须按**实际耗时**算边际收益。
        """
        if seconds <= 0:
            return
        e = self.data.setdefault(code, {"attempts": 0, "fails": 0, "rejected": 0})
        if jitter:
            e["jitter_sec"] = round(float(e.get("jitter_sec", 0) or 0) + seconds, 1)
            e["jitter_runs"] = int(e.get("jitter_runs", 0) or 0) + 1
        else:
            e["total_sec"] = round(float(e.get("total_sec", 0)) + seconds, 1)
            e["runs"] = int(e.get("runs", 0)) + 1
        self.save()

    def total_cost(self, code: str) -> float:
        """本题累计美元成本（只算真实跑过的轮；抖动轮不计）。无记录 → 0。"""
        return float((self.data.get(code) or {}).get("total_cost", 0) or 0)

    def avg_sec(self, code: str) -> float:
        """本题**自己**的平均耗时（秒）。外部抖动时间不计入（#21）。"""
        e = self.data.get(code) or {}
        runs = int(e.get("runs", 0))
        if runs <= 0:
            return 0.0
        return float(e.get("total_sec", 0)) / runs

    def roi_order(self, codes: list[str], scores: dict[str, int] | None = None) -> list[str]:
        """按 **分值 / 平均耗时** 降序（ROI 排序），没跑过的题给高优先级。

        · 没跑过 → 用 0 成本计入（视为最便宜，优先试）；
        · 跑过但一次没出 → 按历史平均耗时惩罚，越贵越靠后；
        · 分值来自平台 total_score（easy/medium/hard 不同）。

        ★ #21：「跑过」只看 runs（own_failure 的耗时才累加到 total_sec/runs）。
          只被平台抖动烧过时间的题 runs 仍是 0 → 仍按「没跑过」给最高优先级，
          不背「跑过没出」的折扣。抖动时间保留在 jitter_sec 里供排查。
        """
        scores = scores or {}
        def key(c: str) -> float:
            score = float(scores.get(c, 100))
            avg = self.avg_sec(c)
            if avg <= 0:
                return -1e9          # 未尝试：最优先（成本未知但下限最低）
            return -(score / max(avg, 1.0))
        return sorted(codes, key=key)

    def split(self, codes: list[str]) -> tuple[list[str], list[str]]:
        """返回 (热, 冷)。冷题只应在热题耗尽后才跑 —— 实测 xben-071 连吃 4×120s。"""
        ranked = self.rank(codes)
        hot, cold = [], []
        for c in ranked:
            e = self.data.get(c) or {}
            rejected = int(e.get("rejected", 0))
            fails = int(e.get("fails", 0))
            if fails >= MAX_ATTEMPTS or (rejected and rejected > MAX_REJECTED_RETRY):
                cold.append(c)
            else:
                hot.append(c)
        return hot, cold