#!/usr/bin/env python3
"""parallel_solver.py —— 多 shard 并行跑 SDK 解题（平台限 3 个活跃容器）。

为什么单独一个脚本（不往 run_sdk_solver.py 里塞）：
  · 单 shard 版本已验证能出分（xben-001-24 awarded=300）；
  · 平台硬限「同一时间最多 3 道题容器」，所以并发上限固定 3，多了会被
    InvalidState 挡回来；这里按 shard 编号切题单，各自独立进程，互不抢。
  · 每 shard 自己 list→start→solve→submit→close，失败只影响自己那道。

用法：
  python3 tools/parallel_solver.py --shards 3 --budget 300
  python3 tools/parallel_solver.py --shards 3 --rounds 5    # 连续跑 5 轮
  python3 tools/parallel_solver.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_cfg():
    base = os.environ.get("BENCHMARK_BASE_URL") or os.environ.get("TSEC_BASE_URL") or ""
    token = os.environ.get("BENCHMARK_TOKEN") or os.environ.get("TSEC_TOKEN") or ""
    for cand in (Path.home() / ".pi-ctf/agent/tsecbench.json", ROOT / ".pi/tsecbench.json"):
        if cand.is_file():
            try:
                cfg = json.loads(cand.read_text(encoding="utf-8"))
                base = base or cfg.get("base_url", "")
                token = token or cfg.get("token", "")
            except Exception:
                pass
            if base and token:
                break
    return base, token


_DIFF_ORDER = {"easy": 0, "medium": 1, "hard": 2}


def pending_codes(client) -> list[str]:
    """未通关题，**按难度升序**（easy → medium → hard）。

    ★ 2026-11-14 实测：hard 题单题吃满 150s 且常常失败（xben-010 被重试 4 次），
      它们排在前面会把 3 个名额全占住，让大量能得分的 easy/medium 干等。
      34 次尝试只完成 17 题（50% 效率），主因就是难度不排序。
      现在简单题先扫完，hard 留到最后统一啃。
    """
    chs = client.list_challenges()
    pend = [c for c in chs if not c.is_completed]
    # ★ 2026-11-14 止损：SOLVER_ALLOW 白名单（逗号分隔）——只跑指定题，其余沉底。
    #   用户决定：再给 084/066/088/101 两轮，其余 4 题（056/081/089/092）放弃。
    _allow = [x.strip() for x in os.environ.get("SOLVER_ALLOW", "").split(",") if x.strip()]
    if _allow:
        pend = [c for c in pend if c.unique_code in _allow]
    pend.sort(key=lambda c: (_DIFF_ORDER.get((c.difficulty or "").lower(), 9), c.unique_code))
    return [c.unique_code for c in pend]


# 按难度给预算。★ 2026-11-19 校准（data-driven，不再是拍脑袋）。
# ★ 2026-11-20 某轮优化：数值搬到 tools/solve_policy.py（唯一事实来源）。
#   原因：同一份策略被抄了 3 份且已漂移 —— 这里 150/300/480，而 bench/live.py 与
#   bench/todo.py 还停在 120/180/300（实况页显示的「下一轮预算」因此是错的）。
#   下面的 DIFF_BUDGET 保留为**别名**（有测试与外部脚本按名字读它）。
#   数据来自 run 50607 的 173 次真实运行，但**必须只用「干净轮」**——
#   173 次里有 88 次是收尾轮污染的残影（正文含 Tool bash not found / INFRA_BLOCKED），
#   那些轮 10880s 只出 2 面旗；污染率第2次起 77%/96%/91%，
#   拿含污染的样本算「续跑不划算」「第3次零出旗」全是假象（tools/_an_pol2.py 口径）。
#   干净轮 only（n=85，5943s / 75 旗）的截断模拟：
#     easy   38 旗/1306s：cap90→36、cap120→37、cap150→37、cap180→38 全保
#     medium 33 旗/3431s：cap120→29、cap150→30、cap180→31、cap240→31、cap300→32、cap420→33
#     hard    4 旗/1206s：cap150→1、cap180→2、cap240→3、cap300→3、cap480→4 全保
#   现行 120/180/300 只保住 37+31+3=71；改 150/300/480 保住 73/75。
#   ★ 为什么敢放宽：并发放不进第 4 个容器（平台限 3 活跃），单个 run 拉长
#     只是填进本来就空着的槽位；实测总墙钟对 cap 几乎不敏感（±3 min 内）。
#   ★ 但**先修污染再谈预算**：污染率不归零时任何预算调优都是在错误数据上做的。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from solve_policy import (  # noqa: E402
    DIFF_BUDGET as _POLICY_BUDGET,
    asset_for as _policy_asset,
    base_for as _policy_base,
    budget_for as _policy_budget,
    model_for as _policy_model,
    thinking_for as _policy_thinking,
    cost_cap as _policy_cap,
    stop_by_cost as _policy_stop,
    stop_by_zero_rounds as _policy_zero,   # ★ D4（2026-11-23）连续 N 轮零资产早停
)

DIFF_BUDGET = dict(_POLICY_BUDGET)


# ★ 2026-11-19 某轮优化：队列 LPT 排序（Longest Processing Time first）。
#   背景：parallel_solver 每轮用 p.communicate() 等**全部** shard 退出才起下一轮，
#   于是最快的那 1~2 路要在轮尾空等最慢的那路。实测 run 50607 六轮的轮尾跨度
#   467/586/962/2284/701/453s，**合计损失 5559s 槽位容量 = 3 槽总容量的 21.1%**
#   —— 相当于一整个 shard 全程白等。
#   修法：把**预期耗时最长的题排在队列最前**。3 路同时起跑就先啃硬骨头，
#   尾部只剩短题，尾部跨度趋近 0。共享队列（work_queue）保证谁先完谁继续拿。
#   ★ 只改**队列内顺序**，不改「哪些题入选」——ROI 选题结论依然有效
#     （入选仍由 solve_ledger.roi_order 决定，这里只是同一批题内的执行先后）。
_PRIOR_SEC = {"easy": 40.0, "medium": 90.0, "hard": 240.0}


def lpt_order(todo: list[str], ledger, diff_of: dict[str, str] | None = None) -> list[str]:
    """按预期耗时降序重排（LPT）。预期耗时 = 历史 avg_sec；没跑过用难度先验。"""
    diff_of = diff_of or {}

    def est(code: str) -> float:
        try:
            a = ledger.avg_sec(code) if ledger is not None else 0.0
        except Exception:  # noqa: BLE001
            a = 0.0
        if a > 0:
            return float(a)
        return _PRIOR_SEC.get((diff_of.get(code) or "").lower(), 90.0)

    return sorted(todo, key=lambda c: -est(c))


def pending_with_budget(client) -> list[str]:
    """返回 "code:budget" 列表（按难度排序，预算随难度）。"""
    chs = client.list_challenges()
    pend = [c for c in chs if not c.is_completed]
    pend.sort(key=lambda c: (_DIFF_ORDER.get((c.difficulty or "").lower(), 9), c.unique_code))
    # ★ 2026-11-14 实测：medium 里有一批「180s 超时但没拿到候选」的题
    #   （081/089/027/056/029/032 等）。它们不是没救，是第一次时间不够。
    #   策略：**重试时预算递增**（每失败一次 ×1.5，上限 420s），而不是原样再跑一遍。
    try:
        from solve_ledger import Ledger  # noqa: PLC0415
        _lg = Ledger()
    except Exception:  # noqa: BLE001
        _lg = None
    out = []
    for c in pend:
        tries = _lg.attempts(c.unique_code) if _lg else 0
        # ★ 2026-11-20 某轮优化：预算与档位全部问 tools/solve_policy.py。
        #   原来这段是「就地算」的第三份副本（另外两份在 bench/live.py、bench/todo.py），
        #   改基线时只改这一份，实况页显示的数字立刻与真实下发值不一致。
        # ★ R3-A（2026-11-20）进展感知预算：多传一个 lvl（连续有进展的轮数）。
        #   lvl 由 sdkshard 在每轮收尾用「rc==0 / 有候选 / 有事实」判定后写进账本，
        #   所以这里只在**账本里已经有轮次记录**时才传 —— 首次尝试（rounds==0）
        #   传 None 保持老口径，避免「还没跑过就先降档」。
        lvl = _lg.level(c.unique_code) if (_lg and _lg.rounds(c.unique_code) > 0) else None
        b = _policy_budget(c.difficulty or "", tries, lvl)
        # ★ R4-B（2026-11-21）：升 pro 加一道「上一轮有没有资产」的门。
        #   原来只看尝试次数 → 上一轮超时、零候选、零事实的题照样拿最贵的模型
        #   重跑（历史会话 实测 081 k=5：$0.0820 / 693s / 0 分）。
        #   没跑过（last_round 为 None）→ asset 保持 True，老行为不变。
        _lr = _lg.last_round(c.unique_code) if _lg else None
        _asset = _policy_asset(_lr.get("rc"), _lr.get("cands", 0), _lr.get("facts", 0)) \
            if _lr else True
        model = _policy_model(tries, _asset)
        # ★ R7-A（2026-11-21）：思考档位跟着同一套 asset 判据走。
        #   实测 reasoning 占输出 64.5%（≈$1.66=总成本 32%），且从首轮 56% 涨到第 5 轮 85%。
        #   但首轮解出占 80/99，降首轮档位是拿"已经很快的路"冒险换小钱 → 只降冷重试轮。
        try:
            think = _policy_thinking(tries, _asset)
        except Exception:  # noqa: BLE001
            think = ""
        out.append(f"{c.unique_code}:{b}:{model}:{think}" if think
                   else f"{c.unique_code}:{b}:{model}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shards", type=int, default=3)
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--budget", type=int, default=int(os.environ.get("SOLVER_AGENT_BUDGET", "300")))
    ap.add_argument("--dry-run", action="store_true")
    # 每轮限量：小值 → 每轮重新 list（已通关自动剔除）+ 代码改动一轮内生效
    ap.add_argument("--per-round", type=int, default=0)
    args = ap.parse_args()

    base, token = load_cfg()
    if not base or not token:
        print("[parallel] 缺 BENCHMARK_BASE_URL / BENCHMARK_TOKEN", flush=True)
        return 2

    try:
        from tsec_benchmark import TSecBenchmark, VpnCheckError
    except ImportError as e:
        print(f"[parallel] 缺 SDK：{e}", flush=True)
        return 2

    env = dict(os.environ)
    env["BENCHMARK_BASE_URL"] = base
    env["BENCHMARK_TOKEN"] = token
    env["SOLVER_AGENT_BUDGET"] = str(args.budget)

    # ★ 2026-11-14 修（实测死锁）：上一轮被 kill / 异常的 shard 会把题目容器留在
    #   available 状态，白占 3 个名额之一；新 shard 于是全卡在「已满」重试，
    #   进程都在、pi 一个没起。开跑前必须先清场。
    if not args.dry_run:
        try:
            import subprocess as _sp
            _sp.run([sys.executable, str(ROOT / "tools" / "reap_stale_containers.py")],
                    cwd=str(ROOT), timeout=120, check=False)
        except Exception as e:  # noqa: BLE001
            print(f"[parallel] 清场失败（继续跑）：{e}", flush=True)

    for rnd in range(1, args.rounds + 1):
        try:
            with TSecBenchmark(base_url=base, token=token) as client:
                todo = pending_codes(client)
                # ★ 2026-11-17 修（ruff F821 抓到）：下面 ROI 排序用的 _scores 引用 var chs，
                #   而 chs 是 pending_codes() 的局部变量 → NameError 被 except 吞掉，
                #   _scores **永远是空字典** = ROI 排序静默失效（按分值/耗时选题从没生效）。
                #   必须在 client 还活着时把题面分抓下来。
                try:
                    _chs_rnd = client.list_challenges()
                    _scores_rnd = {c.unique_code: int(c.total_score or 0) for c in _chs_rnd}
                    # ★ 2026-11-19 某轮优化：队列 LPT 排序要用难度先验（见下面 qfile.write_text）。
                    _diff_rnd = {c.unique_code: (c.difficulty or "").lower() for c in _chs_rnd}
                except Exception:  # noqa: BLE001
                    _scores_rnd = {}
                    _diff_rnd = {}
        except VpnCheckError as e:
            print(f"[parallel] VPN 未通：{e} —— 先 tsec vpn up", flush=True)
            return 3
        except Exception as e:
            print(f"[parallel] 列题失败 {type(e).__name__}: {e}", flush=True)
            return 1

        if not todo:
            print(f"[parallel] 第 {rnd} 轮：全部通关，收工", flush=True)
            return 0
        # ★★ 2026-11-20 R1-O2：跑前问一次余额。
        #   血账：整轮 历史会话 / $5.120，余额 3.97→1.12 CNY，而调度器全程不知道余额；
        #   唯一与钱有关的熔断 .quota_exhausted 只在**吃到 402 之后**才写。
        #   查不到余额一律放行（fail-open）：宁可白跑一轮，也不能因网络抖动停掉比赛。
        #   旁路：PI_LAB_SKIP_COSTGUARD=1 直接跳过（接口改版/离线演示时用，别当常态）。
        if os.environ.get("PI_LAB_SKIP_COSTGUARD", "").strip() in ("1", "true", "yes", "on"):
            _cg = {"allow": True, "ok": False, "reason": "PI_LAB_SKIP_COSTGUARD=1 → 跳过余额检查"}
        else:
            try:
                import costguard  # noqa: PLC0415
                _cg = costguard.check()
            except Exception as _e:  # noqa: BLE001
                _cg = {"allow": True, "reason": f"costguard 不可用：{_e}"}
        if not _cg.get("allow"):
            print(f"[parallel] 成本闸门拦下：{_cg.get('reason')}", flush=True)
            print("[parallel] 充值后自动恢复；或调低阈值 SOLVER_MIN_BALANCE_CNY=0.5", flush=True)
            return 4
        if _cg.get("ok"):
            print(f"[parallel] 成本闸门：{_cg.get('reason')}", flush=True)
        # ★ 2026-11-14：每轮限量。好处是**每轮重新 list 题库**（已通关的自动剔除），
        #   且代码改动只需等一轮就能生效，不用整批跑完才换。
        # ★ 2026-11-14 修（实测浪费）：失败题会一直排在最前（按 unique_code 序），
        #   3 个 shard 全被同一批啃不动的题占住，能得分的题排队干等。
        #   用解题账本把「连续失败 ≥ N 次」的题降到队尾（不删，留给低峰重试）。
        # ★ 2026-11-14 实测：xben-071 连吃 4×120s 全超时。冷题（连败≥2 或被拒>2）
        #   必须**只在热题耗尽后才跑**，否则它们会一直占着 3 个名额烧时间。
        cold_pool = []
        try:
            # tools/ 不是包（没有 __init__.py），直接加 sys.path 再 import
            _tp = str(ROOT / "tools")
            if _tp not in sys.path:
                sys.path.insert(0, _tp)
            from solve_ledger import Ledger  # noqa: PLC0415
            _lg = Ledger()
            hot_list, cold_pool = _lg.split(todo)
            # ★ R7-B（2026-11-21）单题成本上限：回放 104 题的真实逐轮成本，
            #   cap=$0.45 + 「上一轮零资产」双判据 → 省 11.0% 且**丢 0 题**。
            #   纯看钱会误杀 056（它在第 4 轮结束时花了 $0.4487 < 0.45，
            #   第 5 轮才出旗）；加「零资产」后，有进展的轮永远不因钱被砍。
            #   旁路：PI_SOLVE_COST_CAP=0 关掉；PI_SOLVE_COST_CAP=<usd> 改阈值。
            _cap_env = os.environ.get("PI_SOLVE_COST_CAP", "").strip()
            try:
                _cap = float(_cap_env) if _cap_env else _policy_cap()
            except ValueError:
                _cap = _policy_cap()
            # ★★ D4（2026-11-23，用户拍板「按连续 N 轮零资产停」）：
            #   与成本上限是**或**关系：cost 看钱，zero 看连续性。
            #   实测差别：一道 $0.10 的题连啃 6 轮零资产，stop_by_cost 永不触发
            #   （没花到 $0.45），但它照样在烧槽位 —— 这才是 D4 要治的。
            #   反例保护：056「每轮都有进展」→ zero_streak 恒 0，永不触发。
            #   旁路：PI_SOLVE_ZERO_STREAK=0 关掉本判据做 A/B（stop_by_zero_rounds 内判）。
            _dropped = []
            for c in list(todo):
                _reason = ""
                try:
                    _spent = _lg.total_cost(c)
                except Exception:  # noqa: BLE001
                    _spent = 0.0
                # ① D4：连续零资产轮数
                if _policy_zero(_lg.zero_streak(c)):
                    _reason = f"连续 {_lg.zero_streak(c)} 轮零资产"
                # ② 成本上限（老判据，保留）
                elif _cap > 0:
                    _lr2 = _lg.last_round(c)
                    _asset2 = _policy_asset(_lr2.get("rc"), _lr2.get("cands", 0),
                                            _lr2.get("facts", 0)) if _lr2 else True
                    if _policy_stop(_spent, _scores_rnd.get(c, 0), _asset2):
                        _reason = f"成本 ${_spent:.3f} 超 ${_cap:.2f} 且零资产"
                if _reason:
                    _dropped.append((c, _spent, _reason))
            if _dropped:
                todo = [c for c in todo if c not in {d[0] for d in _dropped}]
                for _q, _v, _why in _dropped[:8]:
                    print(f"[parallel] 早停 {_q}：{_why}", flush=True)
            # ★ 2026-11-14 用户要求：跳过没价值的题，优先挑有把握快速解出的。
            #   按 **分值 / 该题历史平均耗时** 排序；没跑过的题最优先（成本下限最低）。
            _scores = dict(_scores_rnd)
            # ★ 2026-11-14 该轮修复 修：原来冷/热两池各自排序，但冷池整体后置也不对 ——
            #   实测 084（ROI 0.71）排在 056（0.82）前面。改为**合并后统一按 ROI 排**，
            #   冷池只记标记（用于日志提示），不再单独成池。
            _retry = [c for c in todo if int((_lg.data.get(c) or {}).get("rejected", 0)) > 0]
            _rest = [c for c in todo if c not in _retry]
            todo = _lg.roi_order(_retry, _scores) + _lg.roi_order(_rest, _scores)
            hot_list = todo
            cold_pool = []
            todo = hot_list if hot_list else cold_pool
            if cold_pool:
                print(f"[parallel] 冷题 {len(cold_pool)} 道（连败/多次被拒）→ 本轮排队尾", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[parallel] 账本不可用（按原序跑）：{e}", flush=True)
        # ★ 止损：SOLVER_DEADLINE（epoch 秒）到了就停，不再起新 shard
        # ★ 2026-11-14：配额熔断标记存在就不再起新一轮（避免每秒撞 402）
        _qf = ROOT / "work" / "solve" / ".quota_exhausted"
        if _qf.is_file():
            _when = ""
            try:
                _when = _qf.read_text(encoding="utf-8", errors="ignore").strip()
            except OSError:
                pass
            print(f"[parallel] 配额熔断（标记时间 {_when}）→ 停止；充值后删掉 {_qf} 即可恢复", flush=True)
            break
        _dl = float(os.environ.get("SOLVER_DEADLINE", "0") or "0")
        if _dl and time.time() >= _dl:
            print("[parallel] 到达止损时间，停止起新一轮", flush=True)
            break
        if args.per_round:
            todo = todo[: args.per_round]
        # 热题不够一屏时，才把冷题补进来（避免它们长期饿死、又不让它们占主力时段）
        if cold_pool and len(todo) < (args.per_round or len(todo) + len(cold_pool)):
            need = (args.per_round or 0) - len(todo)
            if need > 0:
                todo += cold_pool[:need]
        print(f"[parallel] 第 {rnd} 轮：待解 {len(todo)} 题，起 {args.shards} 个 shard", flush=True)
        if args.dry_run:
            return 0

        # 按 shard 切题单：第 i 个 shard 拿所有 index % shards == i 的题
        # ★ 2026-11-14：改为**共享队列**。原来按 index%shards 静态切桶，
        #   先跑完的 shard 只能空等（实测 1/3 时间只有 2 个在干活）。
        #   现在把本轮题单写进队列，N 个 shard 各自 pop，谁快谁多干。
        qfile = ROOT / "work" / "solve" / "queue.txt"
        qfile.parent.mkdir(parents=True, exist_ok=True)
        try:
            from work_queue import WorkQueue  # noqa: PLC0415
            # 队列行格式 code:budget（预算按难度），shard 会解析；纯 code 也兼容
            try:
                with TSecBenchmark(base_url=base, token=token) as _c:
                    _wb = {ln.split(":")[0]: ln for ln in pending_with_budget(_c)}
            except Exception:  # noqa: BLE001
                _wb = {}
            # ★ 某轮优化：写队列前按 LPT 重排（见 lpt_order 定义处的实测数据）。
            try:
                from solve_ledger import Ledger as _L  # noqa: PLC0415
                _lgq = _L()
            except Exception:  # noqa: BLE001
                _lgq = None
            _q_order = lpt_order(todo, _lgq, _diff_rnd)
            # 静态切桶也按同一顺序切（队列不可用时的退路），否则退路会退回「编号序」
            todo = _q_order
            qfile.write_text("\n".join(_wb.get(c, c) for c in _q_order) + "\n", encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            print(f"[parallel] 队列写入失败（退回静态切桶）：{e}", flush=True)
            qfile = None
        buckets = [todo[i:: args.shards] for i in range(args.shards)]
        procs = []
        for i, bucket in enumerate(buckets):
            if not bucket:
                continue
            cmd = [sys.executable, str(ROOT / "tools" / "run_sdk_solver.py"),
                   "--budget", str(args.budget)]
            for code in bucket:
                cmd += ["--code", code]
            # run_sdk_solver 只支持单个 --code，这里改成逐题串行：一个 shard 顺序解自己的桶
            arg_codes = ["--codes", ",".join(bucket)]
            if qfile is not None:
                arg_codes = ["--queue", str(qfile)]
            p = subprocess.Popen([sys.executable, str(ROOT / "tools" / "sdkshard.py"),
                                  *arg_codes,
                                  "--budget", str(args.budget)],
                                 cwd=str(ROOT), env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True)
            procs.append((i, p, bucket))

        for i, p, bucket in procs:
            out, _ = p.communicate()
            print(f"----- shard{i} rc={p.returncode} ({len(bucket)} 题) -----", flush=True)
            print((out or "").strip()[-2000:], flush=True)

    with TSecBenchmark(base_url=base, token=token) as client:
        left = pending_codes(client)
    print(f"[parallel] 收尾：还剩 {len(left)} 题未通关", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
