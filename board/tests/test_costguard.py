#!/usr/bin/env python3
"""余额闸（costguard.py）回归测试 —— 治「调度器全程不知道余额」。

血账（2026-11-20 实测）
----------------------
整轮实战 138 次 run / $5.120，余额 3.97 → **1.12 CNY**；唯一和钱有关的熔断是
work/solve/.quota_exhausted，它只在真吃到 402 之后才写（tools/sdkshard.py:108）；
停机后 3 个孤儿 pi 又烧掉 $0.515。全程**没有任何一处问过「还够不够钱」**。

  · tools/costguard.py       —— fetch() 查余额 → check() 判定 → save() 落盘
  · tools/parallel_solver.py:215-227 —— 起新一轮前调用它

本测试守住六件事（**全离线**：一律走 PI_LAB_BALANCE_FIXTURE，绝不打真接口）：
  ① 余额不足 → allow=False / CLI exit 3；余额充足 → allow=True / exit 0；
  ② 查不到（无 key / 接口挂 / 响应改版 / 数字不是数字）→ **fail-open 放行**；
  ③ 阈值可配（SOLVER_MIN_BALANCE_CNY），边界值等于阈值算够；
  ④ 账户 is_available=false 单独拦（余额够也不让跑）；
  ⑤ base_url 砍 /v1（实测 /v1/user/balance 404）；USD 账户取首条；
  ⑥ 判定结果落盘（work/solve/.balance.json，供实况页读）+ 已接进调度器。

    python3 board/tests/test_costguard.py
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = pathlib.Path(__file__).resolve().parents[2]
MOD = LAB / "tools" / "costguard.py"

checks: list = []


def ok(name, cond, extra=""):
    checks.append((name, bool(cond), str(extra)))


def _report():
    bad = [c for c in checks if not c[1]]
    for name, good, extra in checks:
        print(f"  {'OK ' if good else 'ERR'} {name}" + (f"   {extra[:200]}" if extra and not good else ""))
    print("\n通过 %d / 失败 %d" % (len(checks) - len(bad), len(bad)))
    if bad:
        print("失败项：" + "、".join(c[0] for c in bad))
        return 1
    print("OK 余额闸（costguard）：全部通过")
    return 0


def _fixture(td, name, obj):
    p = pathlib.Path(td) / name
    p.write_text(json.dumps(obj, ensure_ascii=False) if not isinstance(obj, str) else obj, encoding="utf-8")
    return str(p)


def _run_cli(fixture, *args, env_extra=None):
    """跑 CLI。返回 (rc, stdout, stderr)。"""
    env = dict(os.environ)
    env["PI_LAB_BALANCE_FIXTURE"] = fixture
    env.pop("SOLVER_MIN_BALANCE_CNY", None)
    # ★ 2026-11-24：必须**显式钉住端点**，否则结果取决于跑测试那台机器的 .env。
    #   实测踩到：宿主 .env 里 DEEPSEEK_BASE_URL=https://api.dafeiyu.win/v1（自建端点），
    #   而新判据按端点决定拦不拦 → 子进程按真实配置判 skip，
    #   本文件里「余额不足必须拦」的 8 条断言全红。**这是测试隔离缺陷，不是逻辑回归**：
    #   这些断言测的是「官方账户余额不足」，就得把端点钉成官方。
    #   想测非官方端点不拦，用 env_extra 显式覆盖（见 main() 里的端点专项）。
    env.setdefault("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    env.update(env_extra or {})
    env["PYTHONIOENCODING"] = "utf-8"  # 不设的话子进程走 cp936，管道里中文全糊
    p = subprocess.run([sys.executable, str(MOD), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=90, env=env)
    return p.returncode, p.stdout, p.stderr


def _load():
    sys.path.insert(0, str(MOD.parent))
    import importlib
    import costguard
    return importlib.reload(costguard)


def main():
    ok("tools/costguard.py 存在", MOD.is_file())
    if not MOD.is_file():
        return _report()

    cg = _load()
    ok("默认阈值 2.0 元", cg.DEFAULT_MIN_CNY == 2.0, cg.DEFAULT_MIN_CNY)
    ok("超时 <= 10s（不能把调度器拖死）", cg.TIMEOUT <= 10, cg.TIMEOUT)

    with tempfile.TemporaryDirectory() as td:
        snap = os.path.join(td, ".balance.json")
        os.environ["PI_LAB_BALANCE_FILE"] = snap

        # ---- ① 余额不足 / 充足 ----
        low = _fixture(td, "low.json", {
            "is_available": True,
            "balance_infos": [{"currency": "CNY", "total_balance": "1.12",
                               "granted_balance": "0.00", "topped_up_balance": "1.12"}],
        })
        rc, out, err = _run_cli(low, "--json")
        ok("余额 1.12 < 2.0 → exit=3", rc == 3, "rc=%s %s" % (rc, err[-200:]))
        try:
            j = json.loads(out.strip())
        except Exception:
            j = {}
        ok("--json 可解析且 allow=False", j.get("allow") is False, out[:200])
        ok("理由含具体数字与阈值", "1.12" in j.get("reason", "") and "2.00" in j.get("reason", ""),
           j.get("reason"))
        snapj = {}
        try:
            snapj = json.loads(pathlib.Path(snap).read_text(encoding="utf-8"))
        except Exception:
            pass
        ok("落盘快照有 allow 与 ts", snapj.get("allow") is False and "ts" in snapj, str(snapj)[:200])

        rich = _fixture(td, "rich.json", {
            "is_available": True,
            "balance_infos": [{"currency": "CNY", "total_balance": "9.99"}],
        })
        rc, out, _ = _run_cli(rich, "--json")
        ok("余额 9.99 >= 2.0 → exit=0", rc == 0, "rc=%s" % rc)
        ok("--json allow=True", json.loads(out.strip()).get("allow") is True, out[:150])

        # 边界：等于阈值算够
        edge = _fixture(td, "edge.json", {
            "is_available": True,
            "balance_infos": [{"currency": "CNY", "total_balance": "2.00"}],
        })
        rc, _, _ = _run_cli(edge)
        ok("余额恰好等于阈值 → 放行", rc == 0, "rc=%s" % rc)

        # ---- ⑥b 阈值可配 ----
        rc, _, _ = _run_cli(low, "--quiet", env_extra={"SOLVER_MIN_BALANCE_CNY": "0.5"})
        ok("调低阈值到 0.5 后 1.12 放行", rc == 0, "rc=%s" % rc)
        rc, _, _ = _run_cli(rich, "--quiet", env_extra={"SOLVER_MIN_BALANCE_CNY": "99"})
        ok("调高阈值到 99 后 9.99 被拦", rc == 3, "rc=%s" % rc)
        rc, _, _ = _run_cli(low, "--quiet", env_extra={"SOLVER_MIN_BALANCE_CNY": "abc"})
        ok("阈值写错（abc）→ 退回默认 2.0", rc == 3, "rc=%s" % rc)

        # ---- ⑤b USD 账户取首条 ----
        usd = _fixture(td, "usd.json", {
            "is_available": True,
            "balance_infos": [{"currency": "USD", "total_balance": "100.0"}],
        })
        rc, out, _ = _run_cli(usd, "--json")
        j = json.loads(out.strip())
        ok("无 CNY 时取首条（USD 账户）", j.get("currency") == "USD" and rc == 0, out[:150])

        # ---- ④ is_available=false 单独拦 ----
        off = _fixture(td, "off.json", {
            "is_available": False,
            "balance_infos": [{"currency": "CNY", "total_balance": "999.0"}],
        })
        rc, out, _ = _run_cli(off, "--json")
        ok("账户不可用（余额充足也拦）", rc == 3 and "is_available" in out, out[:200])

        # ---- ② 查不到 → fail-open 放行 ----
        broken = _fixture(td, "broken.json", "{ 这不是 json")
        rc, out, err = _run_cli(broken, "--json")
        ok("fixture 坏了 → 放行且 exit=0（fail-open）", rc == 0, "rc=%s %s" % (rc, err[-150:]))
        bj = {}
        try:
            bj = json.loads(out.strip())
        except Exception:
            pass
        ok("fixture 坏了 → json 里 ok=false 且 allow=true",
           bj.get("ok") is False and bj.get("allow") is True, out[:200])

        nob = _fixture(td, "nob.json", {"is_available": True, "balance_infos": []})
        rc, out, _ = _run_cli(nob, "--json")
        ok("响应里没有 balance_infos → 放行", rc == 0 and json.loads(out.strip()).get("allow") is True,
           out[:200])

        nan = _fixture(td, "nan.json", {"is_available": True,
                                       "balance_infos": [{"currency": "CNY", "total_balance": "很多钱"}]})
        rc, out, _ = _run_cli(nan, "--json")
        ok("total_balance 不是数字 → 放行", rc == 0 and json.loads(out.strip()).get("allow") is True,
           out[:200])

        # 无 key 也不打网络（in-process，避开 .env 兜底）
        old_env = os.environ.pop("DEEPSEEK_API_KEY", None)
        old_file = cg.ENV_FILE
        old_fix = os.environ.pop("PI_LAB_BALANCE_FIXTURE", None)
        try:
            cg.ENV_FILE = pathlib.Path(td) / "_no_such_env"
            info = cg.fetch()
            ok("无 key → fetch 返回 ok=False（不是抛异常）", info.get("ok") is False, str(info)[:200])
            ok("无 key → check() 仍放行（fail-open）", cg.check().get("allow") is True)
        finally:
            cg.ENV_FILE = old_file
            if old_env is not None:
                os.environ["DEEPSEEK_API_KEY"] = old_env
            if old_fix is not None:
                os.environ["PI_LAB_BALANCE_FIXTURE"] = old_fix

        # ---- ⑤ base_url 砍 /v1 ----
        old_base = os.environ.get("DEEPSEEK_BASE_URL")
        try:
            os.environ["DEEPSEEK_BASE_URL"] = "https://api.deepseek.com/v1"
            ok("base_url 砍掉 /v1（/v1/user/balance 实测 404）", cg.base_url() == "https://api.deepseek.com",
               cg.base_url())
            os.environ["DEEPSEEK_BASE_URL"] = "https://api.deepseek.com/v1/"
            ok("base_url 容忍尾部斜杠", cg.base_url() == "https://api.deepseek.com", cg.base_url())
        finally:
            if old_base is None:
                os.environ.pop("DEEPSEEK_BASE_URL", None)
            else:
                os.environ["DEEPSEEK_BASE_URL"] = old_base

        os.environ.pop("PI_LAB_BALANCE_FILE", None)
        ok("快照默认落 work/solve/.balance.json",
           str(cg.snapshot_path()).replace("\\", "/").endswith("work/solve/.balance.json"),
           cg.snapshot_path())

        # ---- ⑥b 人类可读输出给了补救办法 ----
        rc, out, _ = _run_cli(low)
        ok("人读模式说明怎么恢复（充值 / 调阈值）", "充值" in out and "SOLVER_MIN_BALANCE_CNY" in out, out[:300])
        rc, out, _ = _run_cli(low, "--quiet")
        ok("--quiet 不打印", out.strip() == "", out[:100])

    # ---- ⑥d 端点决定拦不拦（2026-11-24 新增）----
    # 血账：容器里 PI_PROVIDER=deepseek、DEEPSEEK_API_KEY 也在，看着全是官方，
    # 但 DEEPSEEK_BASE_URL=https://api.dafeiyu.win/v1 是自建兼容端点。
    # 旧判据只看 provider 名字 → enforce → 官方余额 1.12 < 2.0 把整条链拦死，
    # 而真正花钱的通道根本没被问过。新判据按**端点**判。
    with tempfile.TemporaryDirectory() as td2:
        lowfix = _fixture(td2, "low.json", {
            "is_available": True,
            "balance_infos": [{"currency": "CNY", "total_balance": "1.12"}],
        })
        # 官方端点 + 余额不足 → 必须拦（历史行为不许坏）
        rc, out, _ = _run_cli(lowfix, "--json",
                              env_extra={"DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1"})
        ok("官方端点 + 余额不足 → 拦（rc=3）", rc == 3, "rc=%s out=%s" % (rc, out[:200]))
        # 自建端点 + 同一个余额 → 必须放行（这才是不烧那笔钱）
        rc, out, _ = _run_cli(lowfix, "--json",
                              env_extra={"DEEPSEEK_BASE_URL": "https://api.dafeiyu.win/v1"})
        ok("自建端点 + 余额不足 → 放行（rc=0）", rc == 0, "rc=%s out=%s" % (rc, out[:200]))
        try:
            got = json.loads(out)
        except Exception:
            got = {}
        ok("放行时如实记下 base_url（不是静默跳过）",
           "dafeiyu" in json.dumps(got, ensure_ascii=False), str(got)[:200])
        ok("放行理由说明「官方余额不代表本次花费」",
           "不代表本次花费" in (got.get("reason") or ""), str(got.get("reason"))[:200])
        ok("放行时 scope=skip", got.get("scope") == "skip", str(got.get("scope")))
        # 逃生门：SOLVER_BALANCE_ALWAYS=1 时即使自建端点也按官方余额拦
        rc, out, _ = _run_cli(lowfix, "--json",
                              env_extra={"DEEPSEEK_BASE_URL": "https://api.dafeiyu.win/v1"})
        ok("默认不设 ALWAYS 时不强制拦", rc == 0)

    # ---- ⑥c 已接进调度器（静态守卫）----
    ps = (LAB / "tools" / "parallel_solver.py")
    ok("tools/parallel_solver.py 存在", ps.is_file())
    if ps.is_file():
        src = ps.read_text(encoding="utf-8", errors="replace")
        ok("调度器引用了 costguard", "costguard" in src)
        ok("调度器在起新一轮前判定（allow / 余额）", "allow" in src or "balance" in src)
        ok("调度器有旁路开关（PI_LAB_SKIP_COSTGUARD）", "PI_LAB_SKIP_COSTGUARD" in src)

    return _report()


if __name__ == "__main__":
    sys.exit(main())
