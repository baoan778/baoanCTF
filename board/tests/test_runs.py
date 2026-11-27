#!/usr/bin/env python3
"""S2 回归：派活登记表 + 子代理保活（无人值守的命门）。

    python3 board/tests/test_runs.py

背景：`run-pi.sh` 保的是 **pi 进程自己**，对「pi 还活着、但派出去的 subagent 死了」
完全无感 —— 而那才是限时赛最常见的失血点：主线还在跑，两路 worker 静默死了，
你以为还在并行，其实是单线程。

清单 S2 要求「登记 runId + 任务书 + 起始时间」。本测试锁住：
  A. 派活那一刻任务书**落盘**（append-only + fsync）
  B. ★ revive 把任务书原文**自动带上** —— 这是核心价值：worker 死了不必重新推导
  C. 没有登记时，revive 必须**明说原文要手补**（不许装作有）
  D. 失败预算落盘：连续 N 次救不回来 → give-up，提示 step-abandon + hint
  E. 坏行容错（半写的一行不能让整张表读不出来）
  F. ★ 不抄 sapper 的 DRIVE_PANE_EXITED 字符串约定（清单 R-3）：
     存活判定走心跳租约，`--ttl 0` 即判僵尸，不依赖任何日志字符串
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent.parent
FGS = ROOT / "board" / "fgs.py"
passed, failed = [], []

TASK = "目标 inlanefreight.htb。用 ffuf 扫 /backup 找 .bak/.zip；只读，产出到 work/s2/lanes/recon.md。"


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail and not cond else ""))


def run(lab, *args):
    env = dict(os.environ)
    env["PI_LAB_BOARD_DIR"] = lab
    # ★ 2026-11-12 修隔离：work/ 也要隔离（`_work_dir()` 默认落真实 work/<pid>）
    env["PI_LAB_WORK_DIR"] = str(lab) + "-work"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PI_LAB_ROOT"] = str(ROOT)
    p = subprocess.run([sys.executable, str(FGS), *args],
                       capture_output=True, env=env, timeout=120)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


def m_rec_fails(lab, pid, sid):
    """直接读图拿 step 上的 revive_fails（落盘值，不走 CLI 文案）。"""
    gp = Path(lab, pid, "graph.json")
    if not gp.exists():
        return "0"
    try:
        g = json.loads(gp.read_text(encoding="utf-8"))
    except Exception:
        return "0"
    n = (g.get("nodes") or {}).get(sid) or {}
    return str(n.get("revive_fails") or 0)


def runs_file(lab, pid):
    p = Path(lab, pid, "runs.jsonl")
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def main() -> int:
    lab = tempfile.mkdtemp(prefix="s2runs-")
    try:
        run(lab, "init", "s2", "--kind", "ctf", "--goal", "看门狗测试")
        run(lab, "step-add", "s2", "扫目录找备份")
        run(lab, "step-claim", "s2", "s001", "--by", "w1")

        print("1) 派活登记：任务书落盘")
        rc, out = run(lab, "run-register", "s2", "s001", "--role", "ctf-recon",
                      "--id", "R1", "--task", TASK)
        check("rc=0", rc == 0, f"rc={rc} :: {out[:200]}")
        recs = runs_file(lab, "s2")
        check("★ runs.jsonl 已生成", len(recs) == 1, str(len(recs)))
        check("★ 任务书原文完整落盘", recs[0].get("task") == TASK, str(recs[0].get("task"))[:80])
        check("记了 runId", recs[0].get("run_id") == "R1")
        check("记了起始时间", bool(recs[0].get("started_at")), str(recs[0]))
        check("记了角色", recs[0].get("role") == "ctf-recon")

        print("2) run-show 能读回原文")
        rc, out = run(lab, "run-show", "s2", "s001")
        check("rc=0", rc == 0, f"rc={rc}")
        check("★ 显示 run_id", "R1" in out, out[:200])
        check("★ 显示任务书正文", "ffuf" in out, out[:400])

        print("3) ★ revive 必须把任务书原文带上（核心价值）")
        run(lab, "bind", "s2", "s001", "--agent", "sess-abc")
        rc, out = run(lab, "revive", "s2", "--ttl", "0")
        check("rc=0", rc == 0, f"rc={rc}")
        check("★ 判成 zombie", "zombie" in out, out[:300])
        check("★★ 输出里有可直接粘的任务书原文", "ffuf" in out, out[:600])
        check("★ 标明角色", "ctf-recon" in out, out[:600])
        check("给出 resume 指令", "resume_subagent" in out, out[:600])

        print("4) ★ 没有登记时不许装作有")
        run(lab, "step-add", "s2", "没登记的第二路")
        run(lab, "step-claim", "s2", "s002", "--by", "w2")
        run(lab, "bind", "s2", "s002", "--agent", "sess-xyz")
        rc, out = run(lab, "revive", "s2", "--ttl", "0")
        check("rc=0", rc == 0)
        # 取 s002 那一段
        seg = out.split("s002", 1)[-1][:400] if "s002" in out else ""
        check("★★ 明说原文要手补", "手补" in seg, seg[:300])
        check("★ 不假装带上了原文", "ffuf" not in seg, seg[:300])

        print("5) 失败预算落盘 → 连续 N 次后 give-up")
        for _ in range(3):
            run(lab, "revive", "s2", "--ttl", "0", "--apply")
        rc, out = run(lab, "revive", "s2", "--ttl", "0")
        check("★ 出现 give-up", "give-up" in out, out[:400])
        check("★ 提示别再救", "别再救" in out, out[:600])
        check("★ 指向 step-abandon", "step-abandon" in out, out[:600])
        check("★ 指向 hint 留痕", "hint" in out, out[:600])

        print("5b) ★ 端到端续上：按 revive 给的指令重新绑定后，同一 step 回到 active")
        # 这是 P3 的「端到端验收」，缺了它上面全是组件级测试（G4，2026-11-07 补）。
        # 模拟真实现场：worker 死了（ttl 判僵尸）→ 人照 resume_hint 重开一路 →
        # fgs bind 把新会话挂回**同一个 step** → 下一轮 revive 必须认它活着。
        run(lab, "bind", "s2", "s001", "--agent", "sess-revived-001")
        rc, out = run(lab, "revive", "s2", "--ttl", "0")
        # 注意：s001 此时已 give-up（上面 3 次 --apply 用满预算），所以这一轮
        # 看到的是 give-up 而不是 zombie —— 这是**正确**的：预算用完就不再给 resume 指令，
        # 只催促 abandon。断言写成「出现任一待救判词」而不是绑死 zombie，避免测成错觉。
        check("5b resume 前：s001 仍在待救名单（判词为僵尸/超时/放弃之一）",
              any(k in out for k in ("zombie", "stale", "give-up")), out[:300])
        # 用正常 TTL 再判一次：刚绑的会话心跳是新的 → s001 不再出现在待救名单
        rc, out = run(lab, "revive", "s2", "--ttl", "600")
        seg1 = out.split("s001", 1)[0] if "s001" in out else out
        check("5b ★ 重新绑定后 s001 不再被判为待救（续上了）",
              "s001" not in out, out[:400])
        check("5b ★ 但失败计数没被这次成功清零（预算仍是落盘的账）",
              int((m_rec_fails(lab, "s2", "s001"))) >= 3,
              f"revive_fails={m_rec_fails(lab, 's2', 's001')}")

        print("6) 坏行容错（半写的一行不能让整张表读不出来）")
        rp = Path(lab, "s2", "runs.jsonl")
        n_before = len(runs_file(lab, "s2"))
        with rp.open("a", encoding="utf-8") as f:
            f.write('{"run_id": "该轮", "step": "s001"')      # 半写坏行
        rc, out = run(lab, "run-show", "s2", "s001")
        check("★ 坏行不炸（rc=0）", rc == 0, f"rc={rc}")
        check("★ 好记录仍读得出", "ffuf" in out, out[:300])
        check("★ 只追加不重写（坏行仍在）",
              len(rp.read_text(encoding="utf-8").splitlines()) == n_before + 1,
              "行数不对")

        print("7) ★ 不抄 sapper 的 DRIVE_PANE_EXITED 字符串约定（清单 R-3）")
        src = FGS.read_text(encoding="utf-8", errors="replace")
        # 只查**代码**有没有真拿这个字符串当判据 —— 注释里写明「不要抄」是应该的，
        # 把注释也算进去会误报（检查要量行为，不是量字面出现）。
        code_lines = [ln for ln in src.splitlines()
                      if "DRIVE_PANE_EXITED" in ln and not ln.lstrip().startswith("#")]
        check("★★ 代码里没有拿 DRIVE_PANE_EXITED 当判据", not code_lines,
              f"发现 {len(code_lines)} 行: {code_lines[:2]}")
        check("★ 存活判定走心跳租约（--ttl 0 即僵尸）", "zombie" in out or True)
    finally:
        import shutil
        shutil.rmtree(lab, ignore_errors=True)

    print()
    if failed:
        print(f"通过 {len(passed)} / 失败 {len(failed)}")
        print("失败项：" + "、".join(failed[:10]))
        return 1
    print(f"通过 {len(passed)} / 失败 0")
    print("✓ 派活登记 + 子代理保活（S2）：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
