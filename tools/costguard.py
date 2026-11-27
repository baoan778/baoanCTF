#!/usr/bin/env python3
"""costguard.py —— 跑前余额闸（R1-O2）。

血账（2026-11-20 实测）
----------------------
整轮实战 138 次 run、$5.120 / 2366 次请求，余额从 3.97 CNY 烧到 **1.12 CNY**，
而**调度器全程不知道余额**：
  · 唯一和钱有关的熔断是 `work/solve/.quota_exhausted`，它只在真吃到 402
    「Insufficient Balance」**之后**才写（tools/sdkshard.py:108）；
  · 本轮真正触发 402 只有 1 次（xben-030 rc=1 @ 16:58），那时余额已经见底；
  · 停机后 3 个孤儿 pi 又烧掉 $0.515 —— 没有任何一处问过"还够不够钱"。

本模块只做一件事：**开新一轮之前问一次余额**。
  · 低于阈值 → 拒绝起新一轮（不是杀正在跑的），打印充值/提阈值的方法；
  · 查不到（无 key / 网络不通 / 接口改版）→ **放行**（fail-open）。
    宁可在没余额时跑一轮白烧几分钱，也不能因为一次网络抖动把整场比赛停掉。

阈值：`SOLVER_MIN_BALANCE_CNY`，默认 2.0 元（约合 2~5 道 medium 的量级）。

用法：
  python3 tools/costguard.py            # 人读
  python3 tools/costguard.py --json     # 机器读（退出码：0 够 / 3 不够 / 0 查不到）
  python3 tools/costguard.py --quiet    # 只给退出码（脚本里用）

可测性：`PI_LAB_BALANCE_FIXTURE` 指向一个 JSON 文件时**不联网**，直接读它 ——
        测试才能覆盖"余额不足 / 余额充足 / 接口挂掉"三种情况而不真的打网络。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"

DEFAULT_MIN_CNY = 2.0
TIMEOUT = 8


def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    if v:
        return v
    # ★ 容器里 DEEPSEEK_API_KEY 由 docker compose env_file 注入；宿主/CI 里可能只在 .env。
    #   这里读 .env 只是**兜底**，密钥不落任何日志（只用于拼 header）。
    try:
        for line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return default


def api_key() -> str:
    return _env("DEEPSEEK_API_KEY")


def base_url() -> str:
    # /user/balance 挂在**根**上，不是 /v1 下（实测 /v1/user/balance 会 404）
    u = _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    if u.endswith("/v1"):
        u = u[:-3]
    return u


def threshold() -> float:
    try:
        return float(os.environ.get("SOLVER_MIN_BALANCE_CNY", DEFAULT_MIN_CNY))
    except (TypeError, ValueError):
        return DEFAULT_MIN_CNY


def fetch() -> dict:
    """查一次余额。返回 {ok, available, total, currency, error}。

    ok=False 表示**查不到**（不是"没钱"）—— 调用方一律 fail-open。
    """
    fixture = os.environ.get("PI_LAB_BALANCE_FIXTURE")
    if fixture:
        try:
            data = json.loads(Path(fixture).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return {"ok": False, "error": f"fixture 读不了：{e}"}
    else:
        key = api_key()
        if not key:
            return {"ok": False, "error": "没有 DEEPSEEK_API_KEY"}
        req = urllib.request.Request(
            base_url() + "/user/balance",
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310
                data = json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    infos = data.get("balance_infos") or []
    if not infos:
        return {"ok": False, "error": "响应里没有 balance_infos"}
    # 优先人民币；没有就取第一条（账户可能是 USD 账户）
    pick = next((i for i in infos if (i.get("currency") or "").upper() == "CNY"), infos[0])
    try:
        total = float(pick.get("total_balance") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"total_balance 不是数字：{pick.get('total_balance')!r}"}
    return {"ok": True, "available": bool(data.get("is_available", True)),
            "total": total, "currency": pick.get("currency") or "?",
            "granted": pick.get("granted_balance"), "topped_up": pick.get("topped_up_balance")}


def snapshot_path() -> Path:
    return os.environ.get("PI_LAB_BALANCE_FILE") and Path(os.environ["PI_LAB_BALANCE_FILE"]) \
        or (ROOT / "work" / "solve" / ".balance.json")


def save(res: dict) -> None:
    """把这次判定落盘，供实况页（tools/bench/live.py cost_gate）读取。

    ★ 实况页有 15s 预算，不能再打一次远程接口；所以"查"发生在调度侧，
      "显示"只读这个文件。写失败静默 —— 显示不到余额不是致命问题。
    """
    try:
        p = snapshot_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({**res, "ts": int(__import__("time").time() * 1000)},
                                ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


# DeepSeek 官方端点。余额接口问的是**这个账户**的钱；
# 只要实际跑的 base_url 不是它，官方余额就与本次花费无关。
OFFICIAL_BASE = "api.deepseek.com"


def _base_url() -> str:
    """当前**实际调用**的 LLM 端点（决定「官方余额是否代表本次花费」）。

    ★ 2026-11-24 实测缺陷（该轮的同类但更隐蔽）：容器里 PI_PROVIDER=deepseek、
      DEEPSEEK_API_KEY 也在，一切看起来都是官方 —— 但
      DEEPSEEK_BASE_URL=https://api.dafeiyu.win/v1，是个**自建 OpenAI 兼容端点**。
      于是 costguard 去问 DeepSeek 官方账户（1.12 CNY，低于阈值 2.0）→ allow=false
      → 整个解题链路被闸门拦住；而真正花钱的那条通道根本没被问过。
      上一版只按 provider **名字**判断（PI_SOLVE_PROVIDER 等），名字是 deepseek
      就 enforce —— 恰好被这个「名字没变、只换了 base_url」的配置骗过去；
      而且全仓没有任何地方设置过那几个变量，等于判据从来没生效过。
      → 改为按**端点**判断：名字随便叫，看它连的是不是官方。
    """
    for k in ("DEEPSEEK_BASE_URL", "PI_BASE_URL", "OPENAI_BASE_URL",
              "PI_SOLVE_BASE_URL", "SOLVE_BASE_URL"):
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    try:
        for line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("DEEPSEEK_BASE_URL="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _active_provider() -> str:
    """当前**实际跑题**用的 provider（与余额闸查询的账户可能不是同一个）。

    ★ 该轮（2026-11-22）实测缺陷：costguard 查的是 DEEPSEEK_API_KEY 指向的
      DeepSeek 官方账户，而解题链路可能走的是自定义 OpenAI 兼容端点（aaa）。
      官方账户欠费 is_available=false 时，会把**根本不花那笔钱**的 run 拦死 ——
      实测 104 题基线一次都没跑起来，报「账户不可用」，而 aaa 端点完全可用。
    """
    for k in ("PI_SOLVE_PROVIDER", "SOLVE_PROVIDER", "PI_EVALBENCH_PROVIDER"):
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    return "deepseek"


def _guard_scope() -> str:
    """余额闸该不该管这次 run。

    返回 "enforce" 表示按余额拦；"skip" 表示**端点不是官方，官方余额不代表本次花费**。

    判据按**端点**（base_url），不按 provider 名字 —— 名字是 deepseek 但
    base_url 指向自建兼容端点时，后者才是真的（2026-11-24 实测，见 _base_url）。
    base_url 查不到时退回按名字判（保守：维持历史行为）。
    """
    base = _base_url().lower()
    if base:
        # 官方才 enforce；任何非官方端点都 skip
        if OFFICIAL_BASE in base:
            return "enforce"
        return "skip"
    prov = _active_provider().lower()
    if prov in ("", "deepseek", "deepseek-official"):
        return "enforce"
    # 逃生门：需要强制按官方余额拦时设 SOLVER_BALANCE_ALWAYS=1
    if (os.environ.get("SOLVER_BALANCE_ALWAYS") or "").strip() == "1":
        return "enforce"
    return "skip"


def check() -> dict:
    """fetch + 判定。allow=True 表示**可以起新一轮**（含查不到的 fail-open）。"""
    info = fetch()
    th = threshold()
    # ★ 该轮：换过 provider 时，官方余额与本次花费无关 → 不拦，但**如实记录**。
    if _guard_scope() == "skip":
        base = _base_url()
        who = ("base_url=" + base) if base else ("provider=" + _active_provider())
        res = {**info, "threshold": th, "allow": True,
               "reason": who + " 不是 DeepSeek 官方端点 → 官方余额不代表本次花费，不拦（如实记录）",
               "provider": _active_provider(), "base_url": base, "scope": "skip"}
        save(res)
        return res
    if not info.get("ok"):
        # ★ 2026-11-24：把「为什么查不到」说清楚，别让人对着 error 字段猜。
        #   实测自建兼容端点（如 api.dafeiyu.win）**不提供 /user/balance**，
        #   返回的是 HTML/404，于是解析失败 → ok=false。这是**设计内的正常路径**，
        #   不是故障；但原文案只写「查不到余额」，看日志的人会以为接口挂了。
        _why = str(info.get("error") or "")
        _hint = ("（该端点不提供余额接口，属正常）"
                 if _why.lower().startswith(("jsondecodeerror", "http", "urlerror", "httperror"))
                 else "")
        res = {**info, "threshold": th, "allow": True,
               "reason": "查不到余额 → 放行（fail-open）" + _hint,
               "error_hint": _hint or None}
    elif not info.get("available"):
        res = {**info, "threshold": th, "allow": False,
               "reason": "账户不可用（is_available=false）"}
    elif info["total"] < th:
        res = {**info, "threshold": th, "allow": False,
               "reason": f"余额 {info['total']:.2f} {info['currency']} < 阈值 {th:.2f}"}
    else:
        res = {**info, "threshold": th, "allow": True,
               "reason": f"余额 {info['total']:.2f} {info['currency']} ≥ 阈值 {th:.2f}"}
    save(res)
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    r = check()
    if a.json:
        print(json.dumps(r, ensure_ascii=False))
    elif not a.quiet:
        if r.get("ok"):
            print(f"[costguard] {r['reason']}  →  {'放行' if r['allow'] else '停止起新一轮'}")
        else:
            print(f"[costguard] {r.get('error')} → 放行（fail-open）")
        if not r["allow"]:
            print(f"[costguard] 充值后自动恢复；或调阈值：SOLVER_MIN_BALANCE_CNY=0.5")
    # 退出码：0=放行 / 3=余额不足 / 0=查不到（fail-open，不能当失败）
    return 0 if r["allow"] else 3


if __name__ == "__main__":
    sys.exit(main())