#!/usr/bin/env python3
"""「判据自己会不会误诊 / 漏诊」—— 今天新增的门禁与 doctor 检查逐个对拉。

跑法：
    python3 board/tests/test_doctor_checks.py

为什么要有这个文件
------------------
今天加了一堆**门禁**（solved 必须挂图上、deepen 不许当结论、写类要有无害证据、
授权范围 fail-closed、租约、meta 枚举……）。门禁比功能更容易出两种错，而且都不报错：

  * **漏诊**：该拦的没拦 → 假的结论进了图，后面全错
  * **误诊**：不该拦的拦了 → 人开始习惯性 --force，门禁等于不存在

`test_solved_gate` / `test_lease` / `test_scope` / `test_swarm` 各自测「能拦住」；
这个文件专门补另一半：**正例不许误报**（拿真项目里出现过的形态当样本），
外加几个「配置写错 / 缺前置条件」的退化路径。

测试不碰真产物：`PI_LAB_BOARD_DIR` / `PI_LAB_WORK_DIR` 指向临时目录。
"""
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
    tmp = Path(tempfile.mkdtemp(prefix="pilab-doctor-"))
    PID = "dc"
    env = dict(os.environ, PI_LAB_BOARD_DIR=str(tmp / "projects"),
               PI_LAB_WORK_DIR=str(tmp / "work"))

    def fgs(*args, ok=(0,), extra_env=None):
        e = dict(env, **(extra_env or {}))
        p = subprocess.run([sys.executable, str(FGS), *args],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=e, timeout=120)
        return p.returncode, p.stdout, p.stderr

    def graph():
        return json.loads((tmp / "projects" / PID / "graph.json").read_text(encoding="utf-8"))

    def rewrite(g):
        (tmp / "projects" / PID / "graph.json").write_text(
            json.dumps(g, ensure_ascii=False, indent=2), encoding="utf-8")

    def doctor():
        """doctor 的告警都打在 stdout，退出码 1 = 有硬问题。"""
        return fgs("doctor", PID, ok=(0, 1))

    try:
        fgs("init", PID, "--kind", "pentest", "--goal", "判据对拉")
        fgs("scope", PID, "--set", "a.edu.cn")

        print("1) 退化路径：配置写错不许把 CLI 搞崩")
        rc, out, err = fgs("--help", extra_env={"PI_FGS_STEP_TTL": "30m"})
        check("PI_FGS_STEP_TTL 非整数时仍能跑（rc=0）", rc == 0, f"rc={rc} {err[:200]}")
        check("并且明确提醒回退默认", "回退默认" in err, err[:200])
        rc, out, err = fgs("--help", extra_env={"PI_FGS_STEP_TTL": "0"})
        check("TTL=0 是合法值（立刻视为过期）", rc == 0, f"rc={rc}")

        print("2) 没有 active goal 时，报错要指向真原因（不是「孤点」）")
        fgs("fact-add", PID, "flag{D}", "--confidence", "verified")
        g = graph(); g["nodes"]["g001"]["status"] = "archived"; rewrite(g)
        rc, out, err = fgs("solved", PID, "--fact", "f001", "--desc", "flag{D}", "--no-lanes", "测试：单命令流程", ok=(0, 3))
        check("退出码 3", rc == 3, f"rc={rc}")
        check("说的是「没有 active goal」而不是孤点", "没有 active goal" in out + err, (out + err)[:300])
        check("给的是立目标 / 恢复 goal 的修法", "fgs goal" in out + err, (out + err)[:300])
        g = graph(); g["nodes"]["g001"]["status"] = "active"; rewrite(g)

        print("3) 挂上 satisfies 之后正例不许误报")
        rc, _, err = fgs("relate", PID, "--type", "satisfies", "--from", "f001", "--to", "g001")
        check("relate 成功", rc == 0, err[:200])
        rc, out, err = fgs("solved", PID, "--fact", "f001", "--desc", "flag{D}", "--no-lanes", "测试：单命令流程")
        check("solved 成功", rc == 0, err[:200])
        rc, out, err = doctor()
        check("doctor 干净（没有误报）", rc == 0 and "✗" not in out, out[-400:])

        print("4) meta 枚举：拼错要在**写入时**被拒，不是等 doctor")
        # IR 与 SRC 两套 verdict 词表都必须能用（踩过：只放 SRC 三裁决会把 IR 的 pass 拒掉）
        for flag, good in [("--verdict", "accepted"), ("--verdict", "pass"),
                           ("--severity", "high"), ("--proof-kind", "sentinel")]:
            rc, out, err = fgs("fact-add", PID, f"好 {flag}={good}",
                               "--confidence", "verified", flag, good)
            check(f"{flag} {good} 接受", rc == 0, (out + err)[:200])
        jump = fgs("--help")[1]
        for flag in ("--severity", "--proof-kind"):
            check(f"fact-add 暴露了 {flag}", flag in subprocess.run(
                [sys.executable, str(FGS), "fact-add", "--help"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", env=env).stdout)

        print("5) 写类结论缺无害证据 → 提醒；带 sentinel → 不提醒；只读越权 → 不该被要求")
        fgs("fact-add", PID, "未授权删除他人数据（返回成功文案）", "--confidence", "verified",
            "--action", "verify", "--verdict", "accepted")
        rc, out, err = doctor()
        check("缺 proof_kind 被提醒", "缺无害证据" in out, out[-500:])
        def last_fact():
            return [n["id"] for n in graph()["nodes"].values() if n["type"] == "fact"][-1]

        def warned_ids(out):
            """从 doctor 输出里抽出被报「缺无害证据」的 fact id。"""
            ids = set()
            for ln in out.splitlines():
                if "缺无害证据" in ln:
                    ids.add(ln.split(":")[0].strip().lstrip("⚠✗· ").strip())
            return ids

        fgs("fact-add", PID, "未授权删除他人数据（自建哨兵增改删闭环）", "--confidence", "verified",
            "--action", "verify", "--verdict", "accepted", "--proof-kind", "sentinel")
        sentinel_id = last_fact()
        rc, out, err = doctor()
        check("带 proof_kind=sentinel 的那条不被提醒", sentinel_id not in warned_ids(out),
              f"{sentinel_id} in {warned_ids(out)}")
        fgs("fact-add", PID, "未授权读取他人订单（只读越权，无写操作）", "--confidence", "verified",
            "--action", "verify", "--verdict", "accepted")
        readonly_id = last_fact()
        rc, out, err = doctor()
        check("只读越权**不**被要求 proof_kind（防误报）", readonly_id not in warned_ids(out),
              f"{readonly_id} in {warned_ids(out)}")
        check("而无害证据缺失的那条确实被提醒（证明确实会报）",
              any(i in warned_ids(out) for i in ("f006",)), f"warned={warned_ids(out)}")

        print("6) deepen 不许当结论；但作为线索存在时只提醒")
        fgs("fact-add", PID, "线索：疑似 IDOR，未打穿", "--confidence", "verified",
            "--action", "verify", "--verdict", "deepen")
        rc, out, err = doctor()
        check("verdict=deepen 被提醒（不该当结论用）", "deepen" in out, out[-500:])
        g = graph()
        deepen_id = [i for i, n in g["nodes"].items()
                     if (n.get("meta") or {}).get("verdict") == "deepen"][0]
        g["solved"] = {"by": deepen_id, "desc": "硬凑的", "at": "2026-09-29T00:00:00+00:00"}
        rewrite(g)
        rc, out, err = doctor()
        check("deepen 当 solved 依据 → 判死（rc=1）", rc == 1, f"rc={rc}")
        check("说清「打回/忽略的结论不能当已解决」", "不能充当已解决" in out or "打回" in out, out[-400:])
        g = graph(); g.pop("solved"); rewrite(g)

        print("7) 授权范围：未声明提醒、越界判死、界内放行")
        fgs("step-add", PID, "对界内目标试越权", "--target", "a.edu.cn")
        rc, out, err = doctor()
        check("界内 target 不报", "越界" not in out, out[-400:])
        g = graph()
        for n in g["nodes"].values():
            if n.get("target"):
                n["target"] = "evil.com"
        rewrite(g)
        rc, out, err = doctor()
        check("越界 target 判死（rc=1）", rc == 1, f"rc={rc}")
        check("报出越界主机名", "evil.com" in out, out[-400:])
        g = graph()                                  # 恢复现场：后面的组不该被这次伪造连坐
        for n in g["nodes"].values():
            if n.get("target") == "evil.com":
                n["target"] = "a.edu.cn"
        rewrite(g)
        rc, out, err = doctor()
        check("改回界内后 doctor 恢复干净", rc == 0 and "✗" not in out, out[-300:])

        print("8) 僵尸 claim 只是提醒（不判死），且给了回收命令")
        fgs("step-add", PID, "会变成僵尸的步")
        sid = [n["id"] for n in graph()["nodes"].values()
               if n["type"] == "step" and "僵尸" in n["text"]][0]
        fgs("step-claim", PID, sid, "--by", "w1")
        rc, out, err = doctor()
        check("未过期时不提醒", "过期" not in out, out[-300:])
        rc, out, err = fgs("doctor", PID, extra_env={"PI_FGS_STEP_TTL": "0"}, ok=(0, 1))
        check("TTL=0 时提醒过期", "过期" in out + err, (out + err)[-300:])
        check("仍不判死（rc=0）", rc == 0, f"rc={rc}")
        check("给回收命令", "step-reclaim" in out + err, (out + err)[-300:])

        print("9) Worker 路径也受范围管（apply 时拦，不留越界步）")
        fgs("publish", PID, "--by", "w1",
            "--op", json.dumps({"op": "step", "text": "打界外", "target": "evil.com"}))
        rc, out, err = fgs("apply", PID, ok=(0, 3))
        check("apply 拒绝（退出码 3）", rc == 3, f"rc={rc}")
        check("说清 Worker 无权扩大范围", "无权扩大范围" in out + err, (out + err)[:300])
        check("图里没有多出越界步",
              not any(n.get("target") == "evil.com" for n in graph()["nodes"].values()))
        # 被拒条目按设计会一直留在 outbox（挡住后续 apply）→ 先走显式撤回通道
        seq = json.loads((tmp / "projects" / PID / "outbox.jsonl").read_text(
            encoding="utf-8").splitlines()[0])["_seq"]
        rc, _, err = fgs("outbox", PID, "--withdraw", seq, "--reason", "界外样本，撤回")
        check("撤回被拒条目", rc == 0, err[:200])
        fgs("publish", PID, "--by", "w1",
            "--op", json.dumps({"op": "step", "text": "打界内",
                                "target": "https://a.edu.cn:8443/x"}))
        rc, out, err = fgs("apply", PID)
        check("界内 step 合入成功", rc == 0, (out + err)[:200])
        tgts = [n.get("target") for n in graph()["nodes"].values() if n.get("target")]
        check("target 被归一化保存（不再默默丢掉）", "a.edu.cn" in tgts, str(tgts)[:200])

        print("10) 未声明范围时 Worker 的 target 也照存（不误拦）")
        fgs("scope", PID, "--clear")
        fgs("publish", PID, "--by", "w1",
            "--op", json.dumps({"op": "step", "text": "无范围声明时的步", "target": "whatever.com"}))
        rc, out, err = fgs("apply", PID)
        check("未声明范围 → 放行", rc == 0, err[:200])
        tgts = [n.get("target") for n in graph()["nodes"].values() if n.get("target")]
        check("target 仍被保存", "whatever.com" in tgts, str(tgts)[:200])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ 门禁与 doctor 判据（误诊 + 漏诊）：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
