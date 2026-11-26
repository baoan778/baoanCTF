#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_external_jitter.py — 历史决策 外部抖动 != 自己失败

为什么（2026-11-17）：平台侧瞬时问题（容器名额满 / TSec 连接抖动 / pi 引擎崩溃重试耗尽 /
引擎不可用）原来都走 Ledger().record(code, False) -> attempts+1、fails+1、total_sec+=耗时。
后果是**选错题**级别的：好题被算成「跑过没出还很贵」→ roi_order 排到队尾；
pending_with_budget 还会给它抬预算、换 v4-pro 重跑。

现在分两类记：own_failure（涨 attempts/fails/total_sec）、external_jitter（只记 jitter）。

断言分组：A 账本口径 / B ROI / C sdkshard 分类 / D 功能（假 client） / E 文档。

    python3 board/tests/test_external_jitter.py
"""
import importlib.util
import os
import sys
import tempfile
import time as _real_time
import types
from pathlib import Path

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = Path(os.path.dirname(os.path.dirname(HERE)))
TOOLS = ROOT / "tools"
SDK = TOOLS / "sdkshard.py"
LEDGER_PY = TOOLS / "solve_ledger.py"
DOCS = ROOT / "docs" / "并行工作.md"

_passed, _failed = 0, 0


def ck(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print("  x " + name + (("   [" + detail + "]") if detail else ""))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeTime:
    """替身时钟：sleep 不真等，time() 跟着往前走（避免测试真的睡 30s）。"""

    def __init__(self):
        self.t = _real_time.time()

    def time(self):
        return self.t

    def strftime(self, *a):
        return _real_time.strftime(*a)

    def sleep(self, s):
        self.t += float(s)


class TSecError(Exception):
    def __init__(self, msg=""):
        super().__init__(msg)
        self.message = msg


class TSecConnectionError(TSecError):
    pass


class InvalidState(TSecError):
    pass


class DuplicateSubmit(TSecError):
    pass


class ChallengeNotFound(TSecError):
    pass


def install_fake_tsec():
    """sdkshard 在 solve_one 里 import tsec_benchmark；仓里没有这个模块（在容器里），
    所以测试自己注册一个假模块，才能在不联网的情况下驱动 solve_one。"""
    m = types.ModuleType("tsec_benchmark")
    m.TSecError = TSecError
    m.TSecConnectionError = TSecConnectionError
    m.InvalidState = InvalidState
    m.DuplicateSubmit = DuplicateSubmit
    m.ChallengeNotFound = ChallengeNotFound
    sys.modules["tsec_benchmark"] = m


def main():
    tmp = Path(tempfile.mkdtemp(prefix="jitter-"))
    # 账本与 ROOT 都指到临时目录：绝不碰真账本/真日志
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    sl = load("solve_ledger_ut", LEDGER_PY)
    sl.LEDGER = tmp / "ledger.json"
    # 注册成正式模块名：sdkshard 的 _bail_jitter 里是 from solve_ledger import Ledger，
    # 必须命中这个实例，否则会写真账本 work/solve/ledger.json。
    sys.modules["solve_ledger"] = sl

    # ---------- A 账本口径 ----------
    lg = sl.Ledger()
    for i in range(3):
        lg.record_jitter("T1", "容器名额满")
    e = lg.data["T1"]
    ck("A1 抖动不涨 attempts/fails", e["attempts"] == 0 and e["fails"] == 0, str(e))
    ck("A2 抖动次数与原因留档", e["jitter"] == 3 and "名额" in e["last_jitter"], str(e))
    ck("A3 抖动不当正常尝试（无 last 字段）", "last" not in e, str(e))
    ck("A4 抖动时间戳存在", bool(e.get("last_jitter_at")), str(e))
    ck("A5 record(reason=external_jitter) 也走抖动分支",
       (lambda: (lg.record("T2", False, reason=sl.JITTER),
                 lg.data["T2"]["attempts"] == 0 and lg.data["T2"]["jitter"] == 1)[1])(),
       str(lg.data.get("T2")))
    ck("A6 常量名稳定", sl.JITTER == "external_jitter", sl.JITTER)

    # 只抖动过的题：时间只进 jitter_sec
    lg.record_elapsed("T1", 120.0, jitter=True)
    lg.record_elapsed("T1", 60.0, jitter=True)
    e = lg.data["T1"]
    ck("A7 抖动耗时独立记账", e["jitter_sec"] == 180.0 and e["jitter_runs"] == 2, str(e))
    ck("A8 抖动不进 total_sec/runs", e.get("total_sec", 0) == 0 and e.get("runs", 0) == 0, str(e))
    ck("A9 avg_sec 只算自己的耗时", lg.avg_sec("T1") == 0.0, str(lg.avg_sec("T1")))
    info = lg.jitter_info("T1")
    ck("A10 jitter_info 字段齐全",
       info["jitter"] == 3 and info["jitter_sec"] == 180.0 and info["jitter_runs"] == 2,
       str(info))

    # 自己失败一次 + 抖动一次：runs 只算自己那次
    lg.record_elapsed("T3", 100.0)
    lg.record_elapsed("T3", 900.0, jitter=True)
    e3 = lg.data["T3"]
    ck("A11 runs 只算自己的耗时", e3["runs"] == 1 and e3["total_sec"] == 100.0, str(e3))
    ck("A12 avg_sec 不被抖动抬价", lg.avg_sec("T3") == 100.0, str(lg.avg_sec("T3")))

    # ---------- B ROI ----------
    lg.record("H1", False)
    lg.record("H1", False)
    lg.record_elapsed("H1", 300.0)
    lg.record_elapsed("H1", 300.0)
    order = lg.roi_order(["H1", "T1"], {"H1": 100, "T1": 100})
    ck("B1 只抖动过的题仍排在好题之前", order == ["T1", "H1"], str(order))
    ck("B2 抖动过 ≠ 冷题", "T1" in lg.split(["T1"])[0], str(lg.split(["T1"])))
    ck("B3 真连败仍是冷题（既有逻辑没被改坏）", "H1" in lg.split(["H1"])[1], str(lg.split(["H1"])))
    ck("B4 抖动不写 rejected", int(lg.data["T1"].get("rejected", 0)) == 0, str(lg.data["T1"]))

    # ---------- C sdkshard 分类 ----------
    src = SDK.read_text(encoding="utf-8")
    ck("C1 有 _bail_jitter 定义", "def _bail_jitter" in src)
    ck("C2 抖动退出点 >= 4 处", src.count("_bail_jitter(") >= 5, str(src.count("_bail_jitter(")))
    ck("C3 引擎不可用（rc 126/127）走抖动", "rc in (126, 127)" in src)
    ck("C4 引擎崩溃重试用尽走抖动", "重跑次数已用尽" in src)
    ck("C5 容器名额满走抖动", "仍拿不到容器名额" in src)
    ck("C6 连接抖动重试耗尽走抖动", "起容器连接抖动重试耗尽" in src)
    ck("C7 finally 不重复记耗时", "if not _jitter_exit:" in src)
    ck("C8 配额熔断没被抖动吞掉", "return -1" in src and "mark_quota_exhausted()" in src)

    # ---------- D 功能：假 client 驱动 solve_one ----------
    install_fake_tsec()
    sdk = load("sdkshard_ut", SDK)
    sdk.ROOT = tmp
    sdk.time = FakeTime()
    sdk.LOG = tmp / "shard.log"
    sdk._SHARD_LOG = tmp / "shard.log"   # 别往真 log/ 里塞测试噪声
    sl_real = sys.modules["solve_ledger"]
    sl_real.LEDGER = tmp / "ledger2.json"

    class ConnClient:
        def start_challenge(self, code):
            raise TSecConnectionError("connection reset by peer")

        def close_challenge(self, code):
            pass

    rc = sdk.solve_one(ConnClient(), "J1", 5)
    lg2 = sl_real.Ledger()
    e = lg2.data.get("J1") or {}
    ck("D1 连接抖动：rc=0 且不记失败", rc == 0 and e.get("fails", 0) == 0, str(e))
    ck("D2 连接抖动：attempts 不涨（下轮仍是第 1 次）", e.get("attempts", 0) == 0, str(e))
    ck("D3 连接抖动：jitter 记 1 次", e.get("jitter", 0) == 1, str(e))
    ck("D4 连接抖动：耗时进 jitter_sec", e.get("jitter_sec", 0) > 0 and e.get("total_sec", 0) == 0, str(e))

    class SlotClient:
        def start_challenge(self, code):
            raise InvalidState("max active containers reached")

        def close_challenge(self, code):
            pass

    rc = sdk.solve_one(SlotClient(), "J2", 5)
    e = sl_real.Ledger().data.get("J2") or {}
    ck("D5 容器名额满：记抖动不记失败", rc == 0 and e.get("jitter", 0) == 1 and e.get("fails", 0) == 0, str(e))

    # 引擎不可用：容器起得来，但 PATH 空 -> Popen 抛 FileNotFoundError -> rc=127
    class OkClient:
        def start_challenge(self, code):
            return types.SimpleNamespace(container_addr=["127.0.0.1:1"])

        def close_challenge(self, code):
            pass

    old_path = os.environ.get("PATH", "")
    try:
        os.environ["PATH"] = ""
        rc = sdk.solve_one(OkClient(), "J3", 5)
    finally:
        os.environ["PATH"] = old_path
    e = sl_real.Ledger().data.get("J3") or {}
    ck("D6 引擎不可用：记抖动", rc == 0 and e.get("jitter", 0) == 1, str(e))
    ck("D7 引擎不可用：不算一次尝试", e.get("attempts", 0) == 0, str(e))
    ck("D8 引擎不可用：不涨 fails", e.get("fails", 0) == 0, str(e))

    # ---------- E 文档 ----------
    doc = DOCS.read_text(encoding="utf-8")
    ck("E1 文档有抖动记账口径", "外部抖动 ≠ 自己失败" in doc)
    ck("E2 文档写明 jitter_sec 不进 ROI", "jitter_sec" in doc)
    ck("E3 文档写明哪些算抖动", "容器名额" in doc and "引擎崩溃" in doc)

    # 清掉临时树（账本 / 会话 / 日志都在里面）
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    print("  " + str(_passed) + " passed, " + str(_failed) + " failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
