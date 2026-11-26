#!/usr/bin/env python3
"""`brief --as-task` + `workers` 多维止损的回归测试。

守两件事
--------
**一、`--as-task`：主会话不必手写模板，也不必把简报正文贴进 prompt。**
   派活时主会话原先要手写五段 + 把简报正文粘进 `subagent({task})` —— 正文于是付
   两遍（主会话一遍、Worker 一遍）。`--as-task` 把正文落盘、只回一段**可直接粘的骨架**。
   骨架必须**自足**：五段 + 5 条硬条款（`INFRA_BLOCKED` / `lane_gate bg` / `已证死路`…）
   一个都不许少，否则 Worker 还是得靠猜。

**二、多维止损：单看超时拦不住「不超时、也不产出」那种烧法。**
   四维：`dry_streak`（连续几个 done Step 没带来新信息）/ `dry_attempts`（无产出尝试）/
   `unreachable_streak`（连续不可达）/ `banked`（已拿到部分 flag → 窗口放宽）。

   ⚠ 这里有个**必须守住的坑**：`step-conclude` 必产 fact，所以 dry_streak
   **不能**定义成「零产出」（那样恒为 0，等于没做）。本测试专门钉这条 ——
   用「重复事实」造 dry，用「新事实」打断 streak。

    python3 board/tests/test_brief_astask.py
"""
from __future__ import annotations

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

LAB = Path(__file__).resolve().parents[2]
PY = sys.executable
FGS = LAB / "board" / "fgs.py"
PID = "astaskdemo"

checks: list[tuple[str, bool, str]] = []


def ok(name: str, cond: bool, extra: str = "") -> None:
    checks.append((name, bool(cond), extra))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="fgs-astask-"))
    board = tmp / "board"
    work = tmp / "work"
    (work / PID / "lanes").mkdir(parents=True)
    env = dict(os.environ, PI_LAB_BOARD_DIR=str(board), PI_LAB_WORK_DIR=str(work))

    def fgs(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
        return subprocess.run([PY, str(FGS), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                              env=env, timeout=timeout)

    try:
        r = fgs("init", PID, "--goal", "as-task 与止损回归：拿到 flag")
        ok("init 成功", r.returncode == 0, r.stderr[:200])

        # ── 造图：3 个 done Step。第 1 个带**新**事实，后两个是**重复**事实（= dry）──
        fgs("step-add", PID, "第一路：试 SQLi")
        fgs("step-claim", PID, "s001", "--by", "w1")
        fgs("step-conclude", PID, "s001", "目标 /login 存在且返回 200",
            "--confidence", "verified")

        fgs("step-add", PID, "第二路：再试一次 SQLi")
        fgs("step-claim", PID, "s002", "--by", "w2")
        fgs("step-conclude", PID, "s002", "目标 /login 存在且返回 200",
            "--confidence", "verified")          # ← 与 s001 逐字相同 = 重复

        fgs("step-add", PID, "第三路：还是试 SQLi")
        fgs("step-claim", PID, "s003", "--by", "w3")
        fgs("step-conclude", PID, "s003", "目标 /login 存在且返回 200",
            "--confidence", "verified")          # ← 又一模一样

        # 一条不可达 + 一条已 bank 的 flag + 一个待认领步（供 brief 选）
        fgs("fact-add", PID, "确认 flag: flag{demo_astask_1234}",
            "--confidence", "verified")
        # ★ 不可达必须放**最后**：unreachable_streak 数的是「末尾连续」（放中间会被后面打断）
        fgs("fact-add", PID, "目标 10.0.0.9 不可达：connection refused",
            "--confidence", "verified")
        fgs("step-add", PID, "第四路：换攻击面打文件面")

        # ── 一、brief --as-task ────────────────────────────────────────────
        brief_path = work / PID / "_brief-step.txt"
        if brief_path.exists():
            brief_path.unlink()
        r = fgs("brief", PID, "--step", "s002", "--as-task")
        out = r.stdout
        ok("--as-task 退出码 0", r.returncode == 0, r.stderr[:300])
        ok("--as-task 隐含落盘（简报文件生成）", brief_path.exists())
        ok("stdout 打了简报路径", str(brief_path) in out, out[:200])

        body = brief_path.read_text(encoding="utf-8") if brief_path.exists() else ""
        ok("简报正文在文件里（含接力块）", "接力块" in body)
        ok("★ stdout **不含**简报正文（正文只进 Worker 上下文）",
           "# 任务简报 ·" not in out, out[:300])

        for frag, label in [
            ("## 任务书", "任务书标题"),
            ("### 1. 目标", "① 目标"),
            ("### 2. 边界", "② 边界"),
            ("### 3. 已知事实", "③ 已知事实"),
            ("### 4. 产出", "④ 产出"),
            ("### 5. 回执", "⑤ 回执"),
            ("INFRA_BLOCKED", "条款 0 可解性探针"),
            ("禁止先逛文件系统", "条款 0b 首调用连目标"),
            ("lane_gate bg", "条款 0c 长任务闸门"),
            ("tmux new-session -d", "条款 0c 改写模板"),
            ("已证死路", "条款 3b 死路优先回"),
            ("产出即时落盘", "条款 5 落盘"),
            ("样例串", "占位符提示（样例串不算 flag）"),
            ("Do not describe", "立即执行提示"),
        ]:
            ok(f"任务书含「{label}」", frag in out, out[-400:])
        ok("任务书引用的是路径而非正文", f"read {brief_path}" in out)
        ok("目标写进了任务书", "第二路：再试一次 SQLi" in out)

        # 没有可认领步时**不许**硬拼空骨架
        fgs("step-release", PID, "s004")
        r2 = fgs("brief", PID, "--step", "s001", "--as-task")
        ok("指定已 done 的 step 仍能出简报", r2.returncode == 0, r2.stderr[:200])

        # ── 一·B、--task-ref（记忆池完整形态：主会话只掏指针）─────────────────
        task_path = work / PID / "_task-step.txt"
        if task_path.exists():
            task_path.unlink()
        r3 = fgs("brief", PID, "--step", "s002", "--task-ref")
        out3 = r3.stdout
        ok("--task-ref 退出码 0", r3.returncode == 0, r3.stderr[:300])
        ok("★ --task-ref 落了任务书文件", task_path.exists())
        ok("★ 主会话只掏指针（< 1000 字符）", len(out3) < 1000,
           f"实际 {len(out3)} 字符")
        ok("指针里给了任务书路径", str(task_path) in out3)
        # 指针里只**点名**了条款标签，正文不该在（查正文句子，不查标签词）
        ok("★ 指针里**没有**硬条款正文（骨架在文件里）",
           "禁止先逛文件系统" not in out3 and "tmux new-session -d" not in out3,
           out3[:300])
        tf = task_path.read_text(encoding="utf-8") if task_path.exists() else ""
        ok("任务书文件里**有**硬条款全文", "INFRA_BLOCKED" in tf and "lane_gate bg" in tf)
        ok("任务书文件里有 5 段", all(f"### {i}." in tf for i in range(1, 6)))
        ok("任务书文件里含 3b 死路条款", "已证死路" in tf)

        # ── 二、workers 多维止损 ───────────────────────────────────────────
        rj = fgs("workers", PID, "--json")
        ok("workers --json 退出码 0", rj.returncode == 0, rj.stderr[:300])
        try:
            d = json.loads(rj.stdout)
        except json.JSONDecodeError as e:
            d = {}
            ok("workers --json 可解析", False, str(e))
        sl = d.get("stoploss", {})
        ok("json 里有 stoploss", bool(sl), rj.stdout[:200])
        ok("★ dry_streak = 2（重复事实算 dry，不靠「零产出」）",
           sl.get("dry_streak") == 2, f"dry_streak={sl.get('dry_streak')}")
        ok("dry_cutoff = 2", sl.get("dry_cutoff") == 2)
        ok("unreachable_streak >= 1", sl.get("unreachable_streak", 0) >= 1,
           f"unreachable={sl.get('unreachable_streak')}")
        ok("★ banked = 1（认出 flag 形状）", sl.get("banked") == 1,
           f"banked={sl.get('banked')}")
        ok("bank_window_x = 4", sl.get("bank_window_x") == 4)
        ok("banked_ids 非空", bool(sl.get("banked_ids")))

        rt = fgs("workers", PID)
        ok("人读模式含止损画像标题", "止损画像" in rt.stdout)
        ok("★ 人读模式报「停这条线」", "停这条线" in rt.stdout, rt.stdout[-400:])
        ok("★ 人读模式报窗口放宽", "窗口放宽" in rt.stdout)

        # ── 三、假阳性守卫（对抗性探针抓到的两个真 bug，锁死不许回退）────────
        # BUG-A：`banked` 曾把「样例串 CTF{placeholder} 不是真 flag」算成已 bank 一条
        #   flag → 误报「💰 窗口放宽 ×4」，会让编排者对着空手题过度投入。
        pfa = f"{PID}-fp"
        (work / pfa / "lanes").mkdir(parents=True, exist_ok=True)
        fgs("init", pfa, "--goal", "假阳性守卫")
        fgs("fact-add", pfa, "题面样例串 CTF{placeholder} 不是真 flag，别提交",
            "--confidence", "verified")
        sfa = json.loads(fgs("workers", pfa, "--json").stdout)["stoploss"]
        ok("★ BUG-A 守卫：占位符不算 banked", sfa["banked"] == 0,
           f"banked={sfa['banked']}（应是 0）")

        # BUG-B：`unreachable_streak` 曾把「判别式：…即认为目标不可达」这种**规则描述**
        #   算成一次真不可达 → 误触「🛑 停并退避」。
        pfb = f"{PID}-fp2"
        (work / pfb / "lanes").mkdir(parents=True, exist_ok=True)
        fgs("init", pfb, "--goal", "假阳性守卫2")
        fgs("fact-add", pfb, "判别式：curl 超时 5s 即认为目标不可达",
            "--confidence", "verified")
        sfb = json.loads(fgs("workers", pfb, "--json").stdout)["stoploss"]
        ok("★ BUG-B 守卫：规则描述不算不可达", sfb["unreachable_streak"] == 0,
           f"unreachable={sfb['unreachable_streak']}（应是 0）")

        # 反向：真值**必须**仍被认出来（不许矫枉过正）
        pfc = f"{PID}-fp3"
        (work / pfc / "lanes").mkdir(parents=True, exist_ok=True)
        fgs("init", pfc, "--goal", "反向验证")
        fgs("fact-add", pfc, "拿到 flag{realsecret2026abc}", "--confidence", "verified")
        fgs("fact-add", pfc, "目标 10.0.0.9 不可达：connection refused",
            "--confidence", "verified")
        sfc = json.loads(fgs("workers", pfc, "--json").stdout)["stoploss"]
        ok("★ 反向：真 flag 仍算 banked", sfc["banked"] == 1, f"banked={sfc['banked']}")
        ok("★ 反向：真不可达仍被数到", sfc["unreachable_streak"] == 1,
           f"unreachable={sfc['unreachable_streak']}")
        # 未坐实的猜测（带 hedge 词）不算事件
        pfd = f"{PID}-fp4"
        (work / pfd / "lanes").mkdir(parents=True, exist_ok=True)
        fgs("init", pfd, "--goal", "猜测守卫")
        fgs("fact-add", pfd, "目标可能不可达（还没复测）", "--confidence", "unverified")
        sfd = json.loads(fgs("workers", pfd, "--json").stdout)["stoploss"]
        ok("★ 反向：没把握的猜测不触发退避", sfd["unreachable_streak"] == 0,
           f"unreachable={sfd['unreachable_streak']}")

        # 新事实要能**打断** streak（否则 streak 只增不减 = 假警报）
        fgs("step-add", PID, "第五路：拿到新东西")
        fgs("step-claim", PID, "s005", "--by", "w5")
        fgs("step-conclude", PID, "s005", "全新事实：/api/v2/debug 暴露了 env",
            "--confidence", "verified")
        rj2 = fgs("workers", PID, "--json")
        d2 = json.loads(rj2.stdout)
        ok("★ 新事实把 dry_streak 打断为 0",
           d2["stoploss"]["dry_streak"] == 0, f"dry_streak={d2['stoploss']['dry_streak']}")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for _, c, _ in checks if c)
    for n, c, e in checks:
        print(f"  {'✓' if c else '✗'} {n}{('  ' + str(e)) if e and not c else ''}")
    print(f"\n{passed}/{len(checks)} 断言通过")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
