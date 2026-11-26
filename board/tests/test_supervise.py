#!/usr/bin/env python3
"""主会话监督循环：`bind` / `workers` / `steer`（2026-10-05 用户定的工作方式）。

跑法：
    python3 board/tests/test_supervise.py

为什么要有这个文件
------------------
用户定的闭环：**主会话 + 黑板 = 统一记忆池；worker 卡住时，主会话给它换攻击面**。
在补上 `bind` / `workers` / `steer` 之前，这个闭环缺三个零件：

 1. 「Step ↔ 子代理会话」映射只活在派活那条消息里 —— 上下文一压缩就丢，
    于是「卡住了该 steer 谁」没人答得上，只能重派一个 worker 从头再做；
 2. 没有视图回答「谁在跑 / 租约剩多少 / 该转向还是回收」—— 只能人肉翻产物文件；
 3. 转向没有依据 —— 不知道该换到哪个面，「换攻击面」只是口号。

这个文件把四条不变量钉住：

 * `bind` 只记地址簿，**不续租**（否则死 worker 会被伪装成活的，僵尸回收被骗过去）；
 * `workers` 的 verdict 三态（active/stale/zombie）与 TTL 数学正确，且带会话地址；
 * `steer` 产出「可直接喂 steer_subagent 的正文」，`--record` 让转向次数可见（防无限转向）；
 * claim/release/reclaim 都清掉旧会话绑定（不会把新 worker 指向已经死掉的会话）。

测试**不碰真产物**：一律 `PI_LAB_BOARD_DIR` 指向 `tempfile.mkdtemp()`。
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
    tmp = Path(tempfile.mkdtemp(prefix="pilab-supervise-"))
    PID = "sup"
    # ★ 2026-11-12 修隔离：原来只隔离了 BOARD_DIR，**没隔离 WORK_DIR** →
    #   \`fgs workers\` / \`fgs bind\` 会去读写真实的 \`work/sup/\`，
    #   和其他测试以及上一轮的残留撞在一起（实测全量回归第 2 轮红）。
    #   两个目录都必须指向临时目录，缺一个就等于没隔离。
    env = dict(os.environ,
               PI_LAB_BOARD_DIR=str(tmp / "projects"),
               PI_LAB_WORK_DIR=str(tmp / "work"))

    def fgs(*args, ok=(0,)):
        p = subprocess.run([sys.executable, str(FGS), *args],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=120)
        return p.returncode, p.stdout, p.stderr

    def graph():
        return json.loads((tmp / "projects" / PID / "graph.json").read_text(encoding="utf-8"))

    def workers_json(*extra):
        rc, out, err = fgs("workers", PID, "--json", *extra)
        return json.loads(out)

    try:
        print("1) 准备：两块 Step，w1 认领 s001")
        rc, _, err = fgs("init", PID, "--kind", "ctf", "--goal", "验证监督循环")
        check("init 成功", rc == 0, err[:200])
        fgs("step-add", PID, "试入口 A（注入）")
        fgs("step-add", PID, "试入口 B（目录爆破）")
        fgs("step-claim", PID, "s001", "--by", "w1")
        check("claim 写了 claimed_at", bool(graph()["nodes"]["s001"].get("claimed_at")))

        print("2) bind：地址簿进图，且**不续租**")
        ts_before = graph()["nodes"]["s001"]["updated_at"]
        rc, out, err = fgs("bind", PID, "s001", "--agent", "sess-abc", "--note", "w-brute")
        check("bind 成功", rc == 0, err[:200])
        check("bind 不碰 updated_at（不伪装心跳）",
              graph()["nodes"]["s001"]["updated_at"] == ts_before)
        row = workers_json()["workers"][0]
        check("workers --json 带出会话 id", row["agent_session"] == "sess-abc", str(row))
        check("刚 bind 是 active", row["verdict"] == "active", str(row))
        rc, out, _ = fgs("workers", PID)
        check("人读视图含会话与 worker 名", "sess-abc" in out and "w1" in out, out[:300])
        check("人读视图列出未认领面", "s002" in out, out[:300])

        print("3) steer：转向简报 + 计数可见（防无限转向）")
        rc, out, err = fgs("steer", PID, "s001", "--record")
        check("steer 退出码 0", rc == 0, err[:200])
        for frag in ("转向简报", "s001", "sess-abc", "steer_subagent", "open Step"):
            check(f"简报含「{frag}」", frag in out, out[:300])
        row = workers_json()["workers"][0]
        check("workers 显示转向 1 次", row["steers"] == 1, str(row))

        print("4) 陈旧/僵尸判定（TTL 数学）")
        rc, out, _ = fgs("workers", PID, "--ttl", "-1", "--stale-only")
        check("--ttl -1 判僵尸且建议回收",
              "💀" in out and "step-reclaim" in out, out[:300])
        row = workers_json("--ttl", "-1")["workers"][0]
        check("JSON verdict=zombie", row["verdict"] == "zombie", str(row))
        check("lease_left 不为负", row["lease_left_s"] >= 0, str(row))

        print("5) reclaim：回收 + 清掉旧会话绑定")
        rc, out, _ = fgs("step-reclaim", PID, "--ttl", "-1")
        check("reclaim 成功", rc == 0 and "s001" in out, out[:200])
        check("reclaim 后绑定被清", "agent_session" not in graph()["nodes"]["s001"])
        check("没有 claimed 了", workers_json()["workers"] == [])

        print("6) 状态冲突分支（rc=2，不崩）")
        rc, _, err = fgs("bind", PID, "s001", "--agent", "x", ok=(2,))
        check("bind 到非 claimed → rc=2", rc == 2, err[:200])
        fgs("step-claim", PID, "s002", "--by", "w2")
        fgs("step-conclude", PID, "s002", "收口：B 面已试完", "--confidence", "verified")
        rc, _, err = fgs("steer", PID, "s002", ok=(2,))
        check("steer 已收口的 Step → rc=2", rc == 2, err[:200])
        rc, _, err = fgs("steer", PID, "s099", ok=(2,))
        check("steer 不存在的 Step → rc=2", rc == 2, err[:200])

        print("7) next 的 wait 会主动指路")
        fgs("step-claim", PID, "s001", "--by", "w3")
        rc, out, err = fgs("next", PID, "--as", "tester", "--force")
        check("wait 时提示 fgs workers", "wait" in (out + err) and "fgs workers" in (out + err),
              (out + err)[:300])

        print("8) release 也清绑定（不留死地址）")
        fgs("bind", PID, "s001", "--agent", "sess-w3")
        fgs("step-release", PID, "s001")
        check("release 后绑定被清", "agent_session" not in graph()["nodes"]["s001"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ 监督循环（bind/workers/steer）：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
