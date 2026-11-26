#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_solve_ctx_gate.py — R5-A 上下文闸门 / R5-B 地址感知重试

为什么
------
A. 重试时续跑会把上一轮全部历史带进每次调用：最贵的 5 道题最后上下文
   98k~159k tokens，而重试轮占全场成本 70%。超过阈值且有 facts/卡点可注入时，
   应新开会话（摘要重开），而不是继续背着 15 万 token 跑。
B. 旧提示词对每个重试轮都写「地址已变更」，实测 19/38 个重试轮地址根本没变
   → agent 丢掉上一轮的端口/路径结论、整轮重侦察（8/19 次）。

断言
----
A. session_context_tokens 取最后一次 usage 的 input+cacheRead。
B. should_fork_context：超阈值且有 facts/正文 → True；无资产 → False；阈值 0 → 关闭。
C. last_addrs 取 facts.md 最后一条时间头。
D. build_prompt：地址未变/已变两套文案互斥；hint 只在传入时出现。
E. 静态接线：solve_one 真的用了闸门与 last_addrs。
F. 运行时：上下文 15 万 + facts → 真跑 solve_one，断言**没**传 --continue、
   提示词带卡点摘要；反向对照（阈值调高）断言**传了** --continue。
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
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

_passed, _failed = 0, 0

CHILD = r'''
import importlib.util, json, os, sys, types
from pathlib import Path

SDK = sys.argv[1]
TMP = Path(sys.argv[2])
sys.path.insert(0, str(Path(SDK).parent))

spec = importlib.util.spec_from_file_location("sdkshard_rt", SDK)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

m.ROOT = TMP
m._SHARD_LOG = TMP / "log" / "shard-test.log"
m._SHARD_LOG.parent.mkdir(parents=True, exist_ok=True)

import solve_ledger
solve_ledger.LEDGER = TMP / "work" / "solve" / "ledger.json"
lg = solve_ledger.Ledger()
lg.data["xben-998-24"] = {"attempts": 2, "fails": 2, "rounds": 2, "lvl": 0}
lg.save()

qdir = TMP / "work" / "solve" / "xben-998-24"
qdir.mkdir(parents=True, exist_ok=True)
rows = [
    {"type": "session", "id": "t", "timestamp": "2026-11-21T00:00:00Z"},
    {"type": "message", "message": {"role": "assistant", "usage": {
        "input": 100000, "cacheRead": 50000, "output": 10},
        "content": [{"type": "text",
                     "text": "上一轮已确认：登录页在 /login，admin 存在，SQLi 无效，下一步打 SSRF。"}]}},
]
(qdir / "2026-11-21T00-00-00-000Z_t.jsonl").write_text(
    "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
(qdir / "facts.md").write_text(
    "## 10:00:00  addrs=10.0.0.1:80\n- 入口是 /login\n", encoding="utf-8")

fake = TMP / "bin"
fake.mkdir(parents=True, exist_ok=True)
p = fake / "pi"
p.write_text(
    "#!/bin/bash\n"
    "printf '%s\\n' \"$@\" > " + str(TMP / "argv.txt") + "\n"
    "echo FOUND_FLAG=flag{aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee}\n",
    encoding="utf-8")
p.chmod(0o755)
os.environ["PATH"] = str(fake) + os.pathsep + os.environ.get("PATH", "")

m.wait_ready = lambda *a, **k: (True, 0.0)
m.write_solver_system = lambda *a, **k: None


class Client:
    def start_challenge(self, code):
        return types.SimpleNamespace(container_addr=["10.0.0.1:80"])

    def submit_flag(self, code, flag):
        return types.SimpleNamespace(correct=True, awarded=200,
                                     correct_flag_count=1, total_flag_count=1)

    def close_challenge(self, code):
        pass


got = m.solve_one(Client(), "xben-998-24", 20, model="deepseek-flash")
av = TMP / "argv.txt"
argv = av.read_text(encoding="utf-8").splitlines() if av.exists() else []
print(json.dumps({"got": got, "argv": argv}))
'''


def ck(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  ✗ {name}" + (f"   [{detail}]" if detail else ""))


def load():
    spec = importlib.util.spec_from_file_location("sdkshard_ctx_ut", str(SDK))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def with_env(key, val, fn):
    old = os.environ.get(key)
    try:
        if val is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = val
        return fn()
    finally:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


def write_session(dirp: Path, ctx_in: int, ctx_cache: int, text: str = ""):
    dirp.mkdir(parents=True, exist_ok=True)
    p = dirp / "2026-11-21T00-00-00-000Z_test.jsonl"
    rows = [
        {"type": "session", "id": "t", "timestamp": "2026-11-21T00:00:00Z"},
        {"type": "message", "message": {"role": "assistant", "usage": {
            "input": 1000, "cacheRead": 2000, "output": 10}, "content": []}},
        {"type": "message", "message": {"role": "assistant", "usage": {
            "input": ctx_in, "cacheRead": ctx_cache, "output": 10},
            "content": ([{"type": "text", "text": text}] if text else [])}},
    ]
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return p


def run_child(rt: Path, ctx_max: str) -> dict:
    env = dict(os.environ, PI_SOLVE_CTX_MAX=ctx_max)
    p = subprocess.run([sys.executable, "-c", CHILD, str(SDK), str(rt)],
                       capture_output=True, text=True, timeout=120, env=env,
                       cwd=str(ROOT))
    data: dict = {}
    for ln in (p.stdout or "").splitlines():
        if ln.strip().startswith("{"):
            try:
                data = json.loads(ln.strip())
            except Exception:
                pass
    data["_out"] = (p.stdout or "") + (p.stderr or "")
    return data


def main():
    if not SDK.exists():
        print("SKIP  tools/sdkshard.py 不在发布包里")
        return 77
    # ★ 2026-11-25：sdkshard.solve_one 内部 `from tsec_benchmark import ...`，
    #   那是**外部 pip 包**（tsec-benchmark），不是本仓库的东西。
    #   没装时这里会 ModuleNotFoundError 崩掉 —— 那不是「代码坏了」，
    #   是「本机没装跑它需要的依赖」，按既有约定 SKIP（77）。
    try:
        import tsec_benchmark  # noqa: F401
    except ImportError:
        print("SKIP  需要 tsec-benchmark 包（pip install tsec-benchmark）")
        return 77
    sd = load()
    src = SDK.read_text(encoding="utf-8")
    tmp = Path(tempfile.mkdtemp(prefix="ctxgate-"))
    try:
        print("A) session_context_tokens")
        d = tmp / "q1"
        write_session(d, 50000, 40000)
        ck("A1 取最后一次 input+cacheRead = 90000", sd.session_context_tokens(d) == 90000,
           sd.session_context_tokens(d))
        ck("A2 没有会话 → 0", sd.session_context_tokens(tmp / "none") == 0)

        print("B) should_fork_context")
        ck("B1 超阈值 + facts → True",
           with_env("PI_SOLVE_CTX_MAX", "80000",
                    lambda: sd.should_fork_context(d, "上一轮事实") == (True, 90000)))
        ck("B2 超阈值但无 facts/正文 → False（056 靠续跑出旗，不许误杀）",
           with_env("PI_SOLVE_CTX_MAX", "80000",
                    lambda: sd.should_fork_context(d, "")[0] is False))
        d2 = tmp / "q2"
        write_session(d2, 50000, 40000,
                      text="上一轮已确认：入口是 /login，admin 存在，试过 SQLi 无效，下一步打 SSRF。")
        ck("B3 无 facts 但有上一轮正文 → True（hint 兜底）",
           with_env("PI_SOLVE_CTX_MAX", "80000",
                    lambda: sd.should_fork_context(d2)[0] is True))
        ck("B4 阈值 0 = 关闭闸门",
           with_env("PI_SOLVE_CTX_MAX", "0",
                    lambda: sd.should_fork_context(d, "事实")[0] is False))
        ck("B5 未超阈值 → False",
           with_env("PI_SOLVE_CTX_MAX", "200000",
                    lambda: sd.should_fork_context(d, "事实")[0] is False))

        print("C) last_addrs")
        facts = d / "facts.md"
        facts.write_text(
            "## 10:00:00  addrs=10.0.0.1:80\n- 旧\n\n## 11:00:00  addrs=10.0.0.2:80, 10.0.0.2:8333\n- 新\n",
            encoding="utf-8")
        ck("C1 取最后一条地址头",
           sd.last_addrs(d) == ["10.0.0.2:80", "10.0.0.2:8333"], sd.last_addrs(d))
        ck("C2 没有 facts.md → []", sd.last_addrs(tmp / "none") == [])

        print("D) build_prompt 文案")
        p1 = sd.build_prompt("xben-999-24", ["10.0.0.2:80"], 1, 300, attempt_no=2,
                             facts="F1", addr_changed=False, hint="H1")
        p2 = sd.build_prompt("xben-999-24", ["10.0.0.2:80"], 1, 300, attempt_no=2,
                             facts="F1", addr_changed=True, hint="")
        p3 = sd.build_prompt("xben-999-24", ["10.0.0.2:80"], 1, 300, attempt_no=1)
        ck("D1 地址未变文案", "地址未变" in p1 and "地址已变更" not in p1)
        ck("D2 地址已变文案", "地址已变更" in p2 and "地址未变" not in p2)
        ck("D3 fact 标签随地址状态切换",
           "可直接复用" in p1 and "IP 已失效" in p2)
        ck("D4 hint 只在传入时出现", "卡点/结论摘要" in p1 and "卡点/结论摘要" not in p2)
        ck("D5 首轮不出现地址变更段",
           "地址已变更" not in p3 and "地址未变" not in p3)

        print("E) 静态接线")
        ck("E1 solve_one 用 last_addrs 判地址", "_prev_addrs = last_addrs(sessdir)" in src)
        ck("E2 solve_one 用上下文闸门", "should_fork_context(sessdir, facts_txt)" in src)
        ck("E3 fork 时不续跑（elif _fork: pass）",
           "elif _fork:" in src and "pass  # 不续跑" in src)
        ck("E4 hint 只在 fork 时计算", "_hint = last_attempt_hint(sessdir)" in src
           and "_fork, _ctx = should_fork_context" in src)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("F) 运行时：超阈值时真的不续跑（+ 反向对照）")
    if os.name != "posix":
        print("  （非 POSIX 宿主跳过 F 组）")
    else:
        rt = Path(tempfile.mkdtemp(prefix="ctxfork-"))
        try:
            d1 = run_child(rt / "lo", "80000")
            ck("F1 流程跑通并取到 flag", d1.get("got") == 1, str(d1.get("_out"))[-400:])
            ck("F2 超阈值 → 没传 --continue", "--continue" not in (d1.get("argv") or []),
               d1.get("argv"))
            ck("F3 提示词带卡点摘要",
               any("卡点/结论摘要" in a for a in (d1.get("argv") or [])))
            ck("F4 提示词带 facts",
               any("上一轮已确认的事实" in a for a in (d1.get("argv") or [])))
            d2 = run_child(rt / "hi", "500000")
            ck("F5 反向对照：阈值调高 → 传了 --continue",
               "--continue" in (d2.get("argv") or []), d2.get("argv"))
        finally:
            shutil.rmtree(rt, ignore_errors=True)

    print(f"\n{'OK' if _failed == 0 else 'FAIL'}  {_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
