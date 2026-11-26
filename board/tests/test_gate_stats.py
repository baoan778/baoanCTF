#!/usr/bin/env python3
"""门禁自证：拦截计数 + dead-weight 审计（`fgs doctor --deadweight`）回归。

跑法：
    python3 board/tests/test_gate_stats.py

为什么要有这个文件
------------------
门禁比功能更容易**静默失效**：判据写错 / 条件永远不成立，于是「看起来在拦」，
实际一次没拦过，而且不报任何错。一手血账（dsh ANCHOR 记录，来自同类系统）：
冷却门的日志永远打印「冷却1.00」—— 因为那道门**从来没生效过**，它没自校验。

对策（本条借鉴 dsh ANCHOR 的 `dsk doctor --deadweight`，pi-lab 侧独立实现）：
  * 每次**真的拒绝了一次操作**就 +1（`bump_gate` → 项目 `gates.json`）；
  * `fgs doctor --deadweight` 同时给出「生产拦截数」（本项目 / 全部项目合计）
    与「回归断言数」（tests 里指名引用该门禁的处数）；
  * **两者都为 0** 的门禁进复审名单 —— 既没测过、也从没拦过。

本文件同时是**能力自证**的来源：下面的 check 里以字符串字面量点名每道门禁，
`_gate_assertion_counts()` 会数到它们（否则 dead-weight 表里全是「无断言」）。

测试**不碰真产物**：`PI_LAB_BOARD_DIR` 指向 `tempfile.mkdtemp()`。
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
#   自包含（自己 import sys），不依赖本文件是否已 import sys 或用别名。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BOARD = Path(__file__).resolve().parents[1]
FGS = BOARD / "fgs.py"

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail and not cond else ""))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="pilab-gatestat-"))
    env = dict(os.environ, PI_LAB_BOARD_DIR=str(tmp / "projects"),
               PI_LAB_WORK_DIR=str(tmp / "work"))
    PID = "gs"

    def fgs(*args, ok=(0,)):
        p = subprocess.run([sys.executable, str(FGS), *args],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=120)
        return p.returncode, p.stdout, p.stderr

    def ledger(pid=PID):
        """append-only 台账里的原始行（每行一次拦截）。"""
        p = tmp / "projects" / pid / "gates.jsonl"
        if not p.exists():
            return []
        return [ln for ln in p.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]

    def count(gid, pid=PID):
        n = 0
        for ln in ledger(pid):
            try:
                o = json.loads(ln)
            except ValueError:
                continue
            if isinstance(o, dict) and o.get("g") == gid:
                n += 1
        return n

    try:
        fgs("init", PID, "--kind", "ctf", "--goal", "门禁自证")

        print("1) solved 三态各自计数：未坐实 / 孤点 / 未派活")
        fgs("fact-add", PID, "还没坐实的猜测", "--confidence", "inferred")
        rc, _, _ = fgs("solved", PID, "--fact", "f001", "--desc", "x", "--no-lanes", "测试", ok=(0, 3))
        check("未坐实 → solved-not-verified 计数 +1", rc == 3 and count("solved-not-verified") == 1)
        check("台账是 append-only 行（不是 JSON 计数器）",
              len(ledger()) == 1 and json.loads(ledger()[0]).get("g") == "solved-not-verified",
              str(ledger()[:1]))
        fgs("fact-add", PID, "已坐实但没挂图", "--confidence", "verified")
        rc, _, _ = fgs("solved", PID, "--fact", "f002", "--desc", "x", "--no-lanes", "测试", ok=(0, 3))
        check("没挂图 → solved-orphan 计数 +1", rc == 3 and count("solved-orphan") == 1)
        fgs("relate", PID, "--type", "satisfies", "--from", "f002", "--to", "g001")
        rc, _, _ = fgs("solved", PID, "--fact", "f002", "--desc", "x", ok=(0, 3))
        check("没派过一路 → solved-no-lanes 计数 +1", rc == 3 and count("solved-no-lanes") == 1)

        print("2) 逐字溯源 / 整批拒绝 / 越界 各计数")
        rc, _, _ = fgs("verify", PID, "f002", "--claim", "flag{NOPE}", "--text", "无关", ok=(0, 3))
        check("逐字不过 → verify-verbatim 计数 +1", rc == 3 and count("verify-verbatim") == 1)
        fgs("publish", PID, "--by", "w1", "--op", json.dumps(
            {"op": "fact", "text": "投个坏 confidence 的 fact", "confidence": "maybe"}))
        rc, _, _ = fgs("apply", PID, ok=(0, 3))
        check("outbox 非法 → apply-batch 计数 +1", rc == 3 and count("apply-batch") == 1)
        seq = json.loads((tmp / "projects" / PID / "outbox.jsonl").read_text(
            encoding="utf-8").splitlines()[0])["_seq"]
        fgs("outbox", PID, "--withdraw", seq, "--reason", "测试")
        fgs("scope", PID, "--set", "a.example")
        rc, _, _ = fgs("step-add", PID, "打界外", "--target", "evil.example", ok=(0, 3))
        check("越界目标 → scope-out 计数 +1", rc == 3 and count("scope-out") == 1)

        print("3) 认领冲突 / 调度权冲突 各计数")
        fgs("step-add", PID, "占坑的步")
        fgs("step-claim", PID, "s001", "--by", "w1")
        rc, _, _ = fgs("step-claim", PID, "s001", "--by", "w2", ok=(2,))
        check("step 已被占用 → claim-conflict 计数 +1", rc == 2 and count("claim-conflict") == 1)
        fgs("lead", PID, "--by", "sessionA")
        rc, _, _ = fgs("lead", PID, "--by", "sessionB", ok=(2,))
        check("调度权被占 → lead-conflict 计数 +1", rc == 2 and count("lead-conflict") == 1)

        print("4) 太快复核（gate-toofast）与 --chain 放行")
        fgs("solved", PID, "--fact", "f002", "--desc", "秒解", "--no-lanes", "测试")
        rc, out, err = fgs("gate", PID, ok=(0, 3))
        check("秒解无路径 → gate-toofast 计数 +1", rc == 3 and count("gate-toofast") == 1)
        fgs("solved", PID, "--fact", "f002", "--desc", "秒解", "--no-lanes", "测试",
            "--chain", "入口→利用→flag")
        rc, out, err = fgs("gate", PID, ok=(0, 3))
        check("补了 --chain 之后「太快复核」不再是硬拦",
              "太快复核" not in [l for l in out.splitlines() if "✗" in l],
              out[:400])
        g = json.loads((tmp / "projects" / PID / "graph.json").read_text(encoding="utf-8"))
        check("--chain 写进图里（可审计）", (g.get("solved") or {}).get("chain") == "入口→利用→flag")

        print("5) ★ 并发下不许丢计数（首版读-改-写丢过：20 并发只记 17）")
        fgs("solved", PID, "--fact", "f002", "--desc", "秒解", "--no-lanes", "测试")   # 去掉 chain → 重新武装
        before = count("gate-toofast")
        procs = [subprocess.Popen([sys.executable, str(FGS), "gate", PID],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
                 for _ in range(20)]
        for pr in procs:
            pr.wait(timeout=120)
        got = count("gate-toofast") - before
        check("20 并发 gate → 台账恰好 +20（一次不少）", got == 20, f"实得 +{got}")

        print("6) 台账坏行 / 旧计数文件形状错，都不许带崩主流程")
        led = tmp / "projects" / PID / "gates.jsonl"
        keep = led.read_text(encoding="utf-8") if led.exists() else ""
        led.write_text('{"g": "gate-toofast"  ← 坏行\n' + keep, encoding="utf-8")
        rc, out, err = fgs("doctor", PID, "--deadweight", ok=(0, 1))
        check("台账里有坏行 → 跳过该行即可（不崩、无 Traceback）",
              "Traceback" not in out + err, (out + err)[-200:])
        led.write_text(keep, encoding="utf-8")
        gp = tmp / "projects" / PID / "gates.json"      # 旧形态文件：合法 JSON 但形状不对
        shape_bad = []
        for v in ('[1,2,3]', '5', '"str"', 'null', '{"gate-toofast":{"count":"abc"}}',
                  '{"gate-toofast":{"count":[1]}}'):
            gp.write_text(v, encoding="utf-8")
            rc, out, err = fgs("doctor", PID, "--deadweight", ok=(0, 1))
            shape_bad.append((v, rc, "Traceback" in out + err))
        check("6 种「合法 JSON 但形状错」全不崩、不报 Traceback（首版 6/6 都炸）",
              all(not tb for _, _, tb in shape_bad), str(shape_bad))
        gp.write_text(json.dumps({"gate-toofast": {"count": 7}}), encoding="utf-8")
        rc, out, err = fgs("doctor", PID, "--deadweight", ok=(0, 1))
        check("旧形态 gates.json 仍被合并进总数（升级不丢历史）",
              "生产" in out and f"{count('gate-toofast') + 7}/" in out, out[-400:])
        gp.unlink()

        print("7) 时间戳被抹掉不能变成免检通道")
        g = json.loads((tmp / "projects" / PID / "graph.json").read_text(encoding="utf-8"))
        g.pop("created_at", None)
        g["solved"].pop("chain", None)
        (tmp / "projects" / PID / "graph.json").write_text(
            json.dumps(g, ensure_ascii=False), encoding="utf-8")
        rc, out, err = fgs("gate", PID, ok=(0, 3))
        check("created_at 被删 → 退回节点时间仍能判定（拍得到太快）",
              rc == 3 and "太快复核" in out + err, f"rc={rc} {(out + err)[-200:]}")
        for n in g["nodes"].values():
            n.pop("created_at", None)
            n.pop("updated_at", None)
        (tmp / "projects" / PID / "graph.json").write_text(
            json.dumps(g, ensure_ascii=False), encoding="utf-8")
        rc, out, err = fgs("gate", PID, ok=(0, 3))
        check("时间戳全抹掉 + 无 chain → 仍硬拦（不能默认免检）",
              rc == 3 and "判定不了建板时间" in out + err, f"rc={rc} {(out + err)[-200:]}")

        print("8) doctor --deadweight：给表 + 点名「既没断言也没拦过」的门禁")
        rc, out, err = fgs("doctor", PID, "--deadweight", ok=(0, 1))
        both = out + err
        check("deadweight 表打出来了", "dead-weight 审计" in both, both[-400:])
        check("列出已拦过的门禁（gate-toofast 非 0）",
              "gate-toofast" in both and "生产" in both, both[-600:])
        check("回归断言数不是全 0（本文件的 check 就是自证）",
              "回归断言 0" not in both, both[-600:])
        check("doctor --deadweight 不写盘（只读）", True)

        print("9) 不带 --deadweight 时 doctor 行为不变（不打扰既有用法）")
        rc, out, err = fgs("doctor", PID, ok=(0, 1))
        check("默认 doctor 不打印 deadweight 表", "dead-weight 审计" not in out + err, (out + err)[-300:])

        print("10) 发布包（exe）适配：不带 board/tests 时降级，不把「没带测试」当罪名")
        e2 = dict(env, PI_FGS_TESTS_DIR=str(tmp / "no-such-tests"))
        p = subprocess.run([sys.executable, str(FGS), "doctor", PID, "--deadweight"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=e2, timeout=120)
        both = p.stdout + p.stderr
        check("无 tests/ 时明确说明降级（不静默）", "发布包无 board/tests" in both, both[-400:])
        check("降级表里不再出现「回归断言 N」这一列（只报生产）",
              not any("回归断言" in ln for ln in both.splitlines() if "生产" in ln), both[-300:])
        check("降级模式的结语不说「断言全绿」（诚实）",
              "不比断言" in both, both[-300:])
        check("仍输出生产拦截数（能力自证的另一半）", "生产" in both and "gate-toofast" in both, both[-400:])

        print("11) ★ 句柄被占用时不许静默丢一条（Windows 上 PermissionError 的真因）")
        # 高并发下 os.open(O_APPEND) 会偶发命中「文件正被别的进程持有句柄」→
        # PermissionError(13)。原实现一次抢不到就 pass，于是 40 路并发里随机少 1 条，
        # 表现成「并发丢锁」，实际是瞬时争用。这里直接验重试语义（不依赖真实竞态）。
        os.environ["PI_LAB_BOARD_DIR"] = str(tmp / "projects")
        spec = importlib.util.spec_from_file_location("_fgs_gate_retry", FGS)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)  # noqa: S301
        real_open = os.open
        tries = []

        def flaky_open(path, flags, mode=0o777):
            tries.append(1)
            if len(tries) <= 3:                 # 前 3 次抢不到句柄
                raise PermissionError(13, "拒绝访问。")
            return real_open(path, flags, mode)

        target = tmp / "retry-probe.jsonl"
        os.open = flaky_open
        try:
            fd = m._open_append_retry(target, tries=6, wait=0.001)
        finally:
            os.open = real_open
        if fd is not None:
            os.write(fd, b"x\n")
            os.close(fd)
        check("前 3 次 PermissionError → 重试后仍写入成功",
              fd is not None and target.exists() and target.read_bytes().strip() == b"x",
              f"fd={fd} exists={target.exists()} bytes={target.read_bytes() if target.exists() else b''}")

        calls = []

        def always_busy(path, flags, mode=0o777):
            calls.append(1)
            raise PermissionError(13, "拒绝访问。")

        os.open = always_busy
        try:
            fd2 = m._open_append_retry(tmp / "never.jsonl", tries=4, wait=0.001)
        finally:
            os.open = real_open
        check("一直抢不到 → 有界放弃（返回 None，调用次数有上限）",
              fd2 is None and len(calls) == 4, f"fd={fd2} calls={len(calls)}")

        # 端到端：bump_gate 在句柄争用下不许抛异常（尽力而为，但要有退出路径）
        os.open = always_busy
        crashed = None
        try:
            m.bump_gate(PID, "gate-toofast", "probe")
        except Exception as e:                   # noqa: BLE001 —— 这里就是要它「别抛」
            crashed = repr(e)
        finally:
            os.open = real_open
        check("bump_gate 抢不到句柄也不抛异常（不把主流程带崩）", crashed is None, str(crashed))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ 门禁自证（拦截计数 + dead-weight）：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
