#!/usr/bin/env python3
"""调度不卡死 / 不空转：**Step 租约**与**reason 终止性**（Cairn 对齐）。

跑法：
    python3 board/tests/test_lease.py

为什么要有这个文件
------------------
对着 Cairn 源码（`runtime/heartbeat.py` 的 `HeartbeatLease`、
`dispatch.example.yaml` 的 `interval`/`max_intents`）审计 pi-lab 时，
发现两个**静默故障**，都是「不报错但项目废掉」那一类：

 1. **僵尸 claim 把项目死锁在 wait**（对应 Cairn 的 intent heartbeat + 到期释放）
    `step-claim` 只写 `claimed_by`，没有任何到期概念。Worker 一死（子代理崩了 /
    会话丢了 / 超时被砍），那个 step 永远停在 claimed → `decide()` 永远返回 wait
    → **整个项目停住，零报错**。
 2. **reason 空手会无限自激**（对应 Cairn `validate_reason_payload` 里
    「open_intents 为空时 intents 不许为空」的硬 raise）
    `decide()` 的最后一个分支原先无条件 `return "reason"`，而 checkpoint 只记
    facts/hints/open_intents —— 空手一轮后三者都没变，下一次 next 又给 reason，
    再空手，再 reason… 每轮都在烧钱，图却一动不动。

这个文件把这四条钉住：租约到期要**被看见**（doctor/next）、要**能回收**（reclaim）、
要**能续期**（heartbeat）、reason 空手要**停下来等人**（none），
而注入 Hint / 产出新 Fact / 新建 Step 后要**重新跑起来**。

测试**不碰真产物**：一律 `PI_LAB_BOARD_DIR` 指向 `tempfile.mkdtemp()`。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
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
    tmp = Path(tempfile.mkdtemp(prefix="pilab-lease-"))
    PID = "lease"
    # TTL 设成 5 秒：测试要真等一次过期，但不能真等 30 分钟。
    # 为什么不是 2 秒（2026-09-29 踩）：4 路并行负载下，「心跳之后到 next 之间」
    # 很容易被吃掘 2 秒 → 本该 wait 的被判成 reclaim（偶发红）。现在：
    #   * 活着/续租这类断言用 5s 窗口 + 只调一次 next（不再双双紧调）；
    #   * 过期这类断言不等死睡，而是**轮询到真过期**（状态驱动，不靠定时）。
    # ★ 2026-11-12 修隔离：补 PI_LAB_WORK_DIR。`fgs snapshot --ref` / 产物扫描走
    #   `_work_dir()`，不设就会落到**真实 work/<pid>**，和别的测试及上一轮残留撞车。
    env = dict(os.environ, PI_LAB_BOARD_DIR=str(tmp / "projects"),
               PI_LAB_WORK_DIR=str(tmp / "work"), PI_FGS_STEP_TTL="5")

    def fgs(*args, ok=(0,)):
        p = subprocess.run([sys.executable, str(FGS), *args],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=120)
        return p.returncode, p.stdout, p.stderr

    def next_out():
        rc, out, err = fgs("next", PID, "--as", "tester", "--force")
        return out + err

    def wait_until_expired(max_wait=30):
        """轮询到 claim 真过期（不靠死睡：并行负载下固定 sleep 会被吃掉）。"""
        t0 = time.time()
        while time.time() - t0 < max_wait:
            if "reclaim" in next_out():
                return True
            time.sleep(1)
        return False

    def graph():
        return json.loads((tmp / "projects" / PID / "graph.json").read_text(encoding="utf-8"))

    def step(sid="s001"):
        return graph()["nodes"][sid]

    try:
        print("1) 租约：活着 → wait；过期 → reclaim（不再静默死锁）")
        rc, _, err = fgs("init", PID, "--kind", "ctf", "--goal", "验证租约")
        check("init 成功", rc == 0, err[:200])
        fgs("step-add", PID, "试一下 A")
        fgs("step-claim", PID, "s001", "--by", "w1")
        check("刚 claim 时 next = wait", "wait" in next_out(), next_out()[:200])
        check("刚 claim 时状态是 claimed", step()["status"] == "claimed")
        expired = wait_until_expired()
        out = next_out()
        check("过期后 next = reclaim", expired and "reclaim" in out, out[:300])
        check("指路到 step-reclaim", "step-reclaim" in out, out[:300])
        check("解释为什么（僵尸会死锁）", "死锁" in out, out[:300])
        check("verbose 里有 TTL 数字", "TTL" in out, out[:300])

        print("2) 到期要被看见：doctor 提醒（只读）")
        rc, out, err = fgs("doctor", PID, ok=(0, 1))
        both = out + err
        check("doctor 提到「过期」", "过期" in both, both[:300])
        check("doctor 只提醒不判死（退出码 0，没有硬问题）", rc == 0, f"rc={rc}")

        print("3) 续租：heartbeat 之后回到 wait")
        rc, out, err = fgs("step-heartbeat", PID, "s001", "--by", "w1")
        check("heartbeat 成功", rc == 0, err[:200])
        check("报出续租前的年龄", "s / TTL" in out or "原已" in out, out[:200])
        out2 = next_out()                                  # 只调一次：并行时窗口很贵
        check("续租后 next = wait（不再是 reclaim）",
              "wait" in out2 and "reclaim" not in out2, out2[:200])
        rc, out, err = fgs("step-heartbeat", PID, "s001", "--by", "别人")
        check("别人不能替你续租（退出码 2）", rc == 2, f"rc={rc} {err[:120]}")

        print("4) 回收：僵尸 claim 被放回 open，并且留痕")
        t0 = time.time()
        while time.time() - t0 < 30:                       # 轮询到真过期再回收
            rc, out, err = fgs("step-reclaim", PID)
            if rc == 0 and "过期" in out:
                break
            time.sleep(1)
        check("reclaim 成功", rc == 0, err[:200])
        check("报出回收了哪个 step 与原因", "s001" in out and "过期" in out, out[:300])
        check("状态回到 open 且 claimed_by 清空",
              step()["status"] == "open" and not step().get("claimed_by"))
        check("next 重新可派（explore）", "explore" in next_out(), next_out()[:200])
        log = (tmp / "projects" / PID / "log.jsonl").read_text(encoding="utf-8")
        check("log.jsonl 记了 step.reclaim", "step.reclaim" in log)
        check("log 里留下了原属者", '"was": "w1"' in log or "'was': 'w1'" in log, log[-300:])
        rc, out, err = fgs("step-reclaim", PID)
        check("没有僵尸时不乱动（退出码 0 + 明确说没有）", rc == 0 and "没有僵尸" in out, out[:200])

        print("5) reason 终止性：空手之后不许再问一遍")
        fgs("step-release", PID, "s001")          # 回到「无 open 无 claimed」
        g = graph()
        del g["nodes"]["s001"]                     # 干净点：这条 step 不参与本组
        for e in list(g["edges"]):
            if e["from"] == "s001":
                g["edges"].remove(e)
        (tmp / "projects" / PID / "graph.json").write_text(
            json.dumps(g, ensure_ascii=False, indent=2), encoding="utf-8")
        fgs("fact-add", PID, "附件是 ELF64", "--confidence", "verified")
        fgs("checkpoint", PID, "--bootstrapped")
        check("① bootstrap 收尾后 → reason（水位清零，不能吃掉这一次）",
              "reason" in next_out(), next_out()[:200])
        fgs("checkpoint", PID)
        out = next_out()
        check("② reason 空手后 → none（不再自激）", "none" in out, out[:300])
        check("② 明确要求人出手（hint）", "hint" in out, out[:300])
        check("② 解释原因（图没变）", "图没变" in out, out[:300])
        check("③ 连着来第二次也是 none（彻底不空转）", "none" in next_out(), next_out()[:200])

        print("6) 注入人的判断 / 新 Fact / 新 Step 后必须重新跑起来")
        fgs("hint", PID, "出题人说 flag 在环境变量里", "--from", "operator")
        check("④ 注入 Hint → reason（新态势）", "reason" in next_out(), next_out()[:200])
        fgs("checkpoint", PID)
        fgs("fact-add", PID, "env 里看到 FLAG=xxx", "--confidence", "verified")
        check("⑤ 出新 Fact → reason（新态势）", "reason" in next_out(), next_out()[:200])
        fgs("checkpoint", PID)
        check("⑥ 又空手 → none", "none" in next_out(), next_out()[:200])
        fgs("step-add", PID, "读 /proc/1/environ 找 FLAG")
        check("⑦ 新建 Step → explore（回到干活）", "explore" in next_out(), next_out()[:200])

        print("7) 优先级：有 open Step 时先探索；claim 住且过期后才报 reclaim（--json 可分支）")
        check("有 open Step → explore（explore 优先于 reclaim）",
              "explore" in next_out(), next_out()[:200])
        fgs("step-claim", PID, "s001", "--by", "w9")
        check("claim 之后未到期 → wait", "wait" in next_out(), next_out()[:200])
        wait_until_expired()                 # 轮询到真过期（TTL=5s，别死睡 2.5s）
        rc, out, _ = fgs("next", PID, "--as", "tester", "--force", "--json")
        j = json.loads(out.strip().splitlines()[-1])
        check("json.task = reclaim", j.get("task") == "reclaim", out[:200])
        check("json.target 指向僵尸 step", j.get("target") == "s001", out[:200])
        check("json.why 能直接读", "过期" in (j.get("why") or ""), out[:200])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ 租约 + reason 终止性：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
