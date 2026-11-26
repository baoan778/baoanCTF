#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_quota_strict.py — 历史决策 额度错误「极严格判定」

为什么（2026-11-17）：熔断器误判比不熔断更贵——把一次 429 限流当成余额耗尽，整场就停了；
反过来，题面正文里出现「Insufficient Balance」「402」这类字样（记账题、支付类题很常见）
被正则扫到，也会整场停机。ARTEX server/task_llm.go isQuotaExhaustedError 只认明确
quota/credits/billing/payment 措辞，429/auth/网络/5xx 一律不按额度耗尽旋转。

断言分组：A 四分类 / B 只扫供应商错误行 / C 冷却秒数 / D 静态接线 / E 文档。

    python3 board/tests/test_quota_strict.py
"""
import importlib.util
import os
import re
import sys
import tempfile
from pathlib import Path

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = Path(os.path.dirname(os.path.dirname(HERE)))
SDK = ROOT / "tools" / "sdkshard.py"
DOCS = ROOT / "docs" / "并行工作.md"

_passed, _failed = 0, 0


def ck(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print("  x " + name + ("   [" + str(detail)[:300] + "]" if detail else ""))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    tmp = Path(tempfile.mkdtemp(prefix="quota-"))
    sdk = load("sdkshard_quota", SDK)
    sdk.ROOT = tmp
    sdk.QUOTA_FLAG = tmp / ".quota_exhausted"
    sdk.LOG = tmp / "shard.log"
    sdk._SHARD_LOG = tmp / "shard.log"

    print("A) 四分类")
    ck("A1 Insufficient Balance = 欠费", sdk.quota_verdict("Error code: 402 - {\"error\": {\"message\": \"Insufficient Balance\"}}") == "exhausted")
    ck("A2 insufficient_quota = 欠费", sdk.quota_verdict("insufficient_quota: you exceeded your current quota") == "exhausted")
    ck("A3 余额不足中文 = 欠费", sdk.quota_verdict("API 返回：账户余额不足") == "exhausted")
    ck("A4 429 = 限流不是欠费", sdk.quota_verdict("Error code: 429 - rate limit exceeded") == "throttled")
    ck("A5 rate_limit 措辞也算限流", sdk.quota_verdict("upstream: too many requests") == "throttled")
    ck("A6 401 = auth", sdk.quota_verdict("Error code: 401 - {\"error\":{\"message\":\"Invalid API key\"}}") == "auth")
    ck("A7 5xx = none", sdk.quota_verdict("Error code: 503 - upstream unavailable") == "none")
    ck("A8 HTTP 402 单凭状态码不熔断", sdk.quota_verdict("Error code: HTTP 402 payment optional") == "none")
    ck("A9 空串 = none", sdk.quota_verdict("") == "none")
    ck("A10 网络错误 = none", sdk.quota_verdict("dial tcp 1.2.3.4:443: connect: connection refused") == "none")

    print("B) 只扫供应商错误行")
    body = "\n".join([
        "本题是一道支付系统中的记账题，题目描述如下：",
        "当订单返回 Insufficient Balance 时，用户需要充值；HTTP 402 Payment Required 是页面标题。",
        "请分析 /api/pay 的 402 分支，flag 藏在错误描述里。",
    ])
    ck("B1 题面正文里的字样不算供应商错误", sdk.quota_action(body) == "none", sdk._api_error_text(body)[:200])
    err = body + "\n" + "Error code: 402 - {\"error\": {\"message\": \"Insufficient Balance\", \"type\": \"insufficient_balance_error\"}}"
    ck("B2 真的错误行仍判欠费", sdk.quota_action(err) == "exhausted", sdk._api_error_text(err)[-200:])
    ck("B3 错误行过滤只留 marked 行", "记账题" not in sdk._api_error_text(err), sdk._api_error_text(err)[:200])
    ck("B4 429 行判限流", sdk.quota_action(body + "\nError code: 429 - rate limit") == "throttled")
    ck("B5 401 行判 auth", sdk.quota_action(body + "\nError code: 401 - invalid api key") == "auth")
    ck("B6 有界（最多 4 KB）", len(sdk._api_error_text("\n".join(["Error code: 500 x" * 50] * 100))) <= 4096)

    print("C) 冷却秒数")
    ck("C1 默认 20", sdk.throttle_seconds() == 20, sdk.throttle_seconds())
    os.environ["PI_SOLVE_THROTTLE_SEC"] = "0"
    ck("C2 显式 0 = 不睡（不是没设）", sdk.throttle_seconds() == 0, sdk.throttle_seconds())
    os.environ["PI_SOLVE_THROTTLE_SEC"] = "-5"
    ck("C3 负数按 0", sdk.throttle_seconds() == 0, sdk.throttle_seconds())
    os.environ["PI_SOLVE_THROTTLE_SEC"] = "abc"
    ck("C4 非法回落默认", sdk.throttle_seconds() == 20, sdk.throttle_seconds())
    os.environ.pop("PI_SOLVE_THROTTLE_SEC", None)

    print("D) 静态接线")
    src = SDK.read_text(encoding="utf-8")
    ck("D1 QUOTA_RE 不再含裸 HTTP 402", not re.search(r'QUOTA_RE = re\.compile\([^)]*HTTP 402', src))
    ck("D2 有 _api_error_text", "def _api_error_text" in src)
    ck("D3 有 quota_action 且走过滤", "def quota_action" in src and "_api_error_text(out)" in src)
    ck("D4 只有 exhausted 才写熔断标记", src.index('if _qa == "exhausted":') < src.index("mark_quota_exhausted()", src.index('if _qa == "exhausted":')))
    ck("D5 限流分支不换 key、不熔断", "不换 key、不熔断" in src)
    ck("D6 限流重试用尽记抖动", "平台限流重试用尽" in src and "_bail_jitter" in src)
    ck("D7 auth 分支明说不熔断", "不按额度耗尽熔断" in src)
    ck("D8 冷却用 PI_SOLVE_THROTTLE_SEC", "PI_SOLVE_THROTTLE_SEC" in src)

    print("E) 文档")
    doc = DOCS.read_text(encoding="utf-8")
    ck("E1 §4.6j 存在", "4.6j 额度错误极严格判定" in doc)
    ck("E2 写明 429≠欠费", "429 ≠ 欠费" in doc or "429 是限流不是欠费" in doc)
    ck("E3 写明只扫供应商错误行", "_api_error_text" in doc)
    ck("E4 写明四分类", "exhausted" in doc and "throttled" in doc and "auth" in doc and "none" in doc)

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    print("")
    print(("OK" if _failed == 0 else "FAIL") + "  " + str(_passed) + " passed, " + str(_failed) + " failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
