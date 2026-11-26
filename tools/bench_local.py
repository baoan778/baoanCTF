#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bench_local.py —— 本地评测台：不依赖 TSecBench 平台 token 也能量方差/做对照。

## 为什么要有它（2026-11-07）

docs/状态快照.md 0.5 的 A/B 两项长期标着「阻塞」：

  A. 先量方差 —— 「从没做过同题 N 次，给不出置信区间」。
     官方数据：同 agent 同模型同题库，两遍差 12 题（11.5%）
     → 任何「改完变好/变差」都不可读。
  B. 提示词编排对照表（臂 C）—— 阻塞在 tsec run-status = 401 Token 无效或已过期。

**关键判断错了**：A/B 的根本阻塞不是平台 token，而是**没有本地跑题台**。
token 只影响「用平台题库 + 平台判分」；而「同一道题跑 N 次看结果稳不稳」
完全可以在本地做 —— 只要题目和判分标准在我们自己手里。

## 两臂（B 项用的就是这个）

  arm=skills    : pi -p --skill <skills目录>（带技能）
  arm=noskills  : pi -p --no-skills（不带技能）

两者**只差这一个变量**，其余（模型、思考档、题面、判分）完全一致 —— 这才叫对照。

## 指标（C3 口径）

  pass@1    = 总通过数 / 总运行数（最直观，但方差最高）
  union@N   = N 轮里至少过 1 次的题数 / 总题数（衡量「有能力」）
  reliable  = N 轮全过的题数 / 总题数（衡量「稳定」）
  variance_gap = union@N - pass@1（差越大 → 单轮结果越不可信）

## 题目从哪来

  ★ 2026-11-17：仓库里的 `bench/` 题库已整体删除（题面与答案早在 11-11 清空），要跑就得自建：
  建 `bench/challenges/<id>/{task.md,verify.sh}` 即可，本工具不依赖任何别的目录。

  bench/challenges/<id>/
    task.md     题干（喂给 agent 的唯一输入）
    verify.sh   判分：在题目录里跑，**退出码 0 = 通过**（唯一判据，不猜）
    answer/     （可选）参考解

★ 判分口径：退出码 0 就是通过。不解析输出、不看模型自述 ——
  模型说「我做完了」不算，脚本退 0 才算。这是「平台回执才算确认」的本地等价物。
"""
from __future__ import annotations

import tempfile
import shutil
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

LAB = Path(__file__).resolve().parents[1]

# ★ 2026-11-23：起 bash 一律用解析出来的完整路径（原委见 tools/bashfind.py）。
#   裸名 "bash" 在本机命中 C:/Windows/System32/bash.exe（起不来的 WSL 存根），
#   判分因此恒假红 —— verify.sh 明明 exit 0，却报「判分不过」。
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from bashfind import BASH
except ImportError:                     # 发布包没带 bashfind → 退回裸名
    BASH = "bash"


def _find_pi() -> str:
    """定位 pi 可执行体。

    * 不能直接写 "pi"：本机 pi 在 npm 全局目录里，只有交互 shell 才在 PATH 上；
      subprocess 走进程 PATH，实测拿不到 -> 第一次端到端跑出 0.0s + 找不到 pi。
    三种口径依次试：
      1) 环境变量 PI_LAB_PI_BIN（测试/特殊布局用）
      2) PATH 上的 pi
      3) 仓库包装 tools/bin/pi（它自己 source labenv.sh）
    """
    env = os.environ.get("PI_LAB_PI_BIN")
    if env and Path(env).exists():
        return env
    w = shutil.which("pi")
    if w:
        return w
    local = LAB / "tools" / "bin" / "pi"
    if local.is_file():
        return str(local)
    return "pi"


PI_BIN = _find_pi()
BENCH = LAB / "bench"
CHAL = BENCH / "challenges"
RESULTS = BENCH / "results"


# ─────────────────────────── 题目发现 ───────────────────────────

def list_challenges() -> list[dict]:
    """扫 bench/challenges/*，返回 [{id, dir, has_answer}]，按 id 排序。"""
    out = []
    if not CHAL.is_dir():
        return out
    for d in sorted(CHAL.iterdir()):
        if not d.is_dir():
            continue
        task, verify = d / "task.md", d / "verify.sh"
        if not (task.is_file() and verify.is_file()):
            continue
        out.append({"id": d.name, "dir": d,
                    "has_answer": (d / "answer").is_dir()})
    return out


# ─────────────────────────── 判分 ───────────────────────────

def judge(chal: dict, seconds: int = 300) -> tuple[bool, str]:
    """跑 verify.sh，退出码 0 = 通过。返回 (ok, 证据摘要)。"""
    # ★ 必须用相对路径 + cwd，不能传绝对路径：
    #   Windows 上 str(Path) 是 D:\baoan\bench\x\verify.sh，bash 会把 \b \x
    #   当转义吃掉 → 报 "D:baoanbenchxverify.sh: No such file or directory"。
    #   实测踩到：判分恒假红，而人是照着这条造题的。
    verify = chal["dir"] / "verify.sh"
    try:
        r = subprocess.run(
            [BASH, "./verify.sh"],
            cwd=str(chal["dir"]),
            capture_output=True, text=True, timeout=seconds,
            encoding="utf-8", errors="replace",
            env={**os.environ, "PI_LAB_BENCH_ID": chal["id"]},
        )
    except subprocess.TimeoutExpired:
        return False, f"判分超时（{seconds}s）"
    ev = ((r.stdout or "") + (r.stderr or "")).strip()
    return r.returncode == 0, ev[-600:]


# ─────────────────────────── 跑一轮 ───────────────────────────

def build_cmd(chal: dict, arm: str, model: str) -> list[str]:
    """组装 pi 无头命令。两臂只差 --skill / --no-skills。"""
    tp = chal["dir"] / "task.md"
    task = tp.read_text(encoding="utf-8", errors="replace") if tp.is_file() else ""
    cmd = [PI_BIN, "-p", "--no-session", "--mode", "json", "--model", model]
    if arm == "skills":
        cmd += ["--skill", str(LAB / "skills")]
    elif arm == "noskills":
        cmd += ["--no-skills"]
    else:
        raise SystemExit(f"未知 arm：{arm}（只认 skills / noskills）")
    cmd += [task]
    return cmd


def run_once(chal: dict, arm: str, model: str, timeout_s: int) -> dict:
    """跑一题一轮：agent 干活 → 判分 → 记录。

    ★★ 2026-11-10 修两个致命缺陷（此前所有历史跑分作废）：
    ① **agent 在题目目录里跑 → answer.txt 就在旁边，直接读答案就通过**。
       现在改为：把题复刻到临时沙箱（只带 task.md + 附件），answer.txt 不带。
    ② **pi 拿不到 API key 时 rc!=0，但判分仍看 answer.txt 存在与否 → 全绿**。
       现在改为：agent 非 0 退出 = 该轮直接判失败（不跑判分），并记错误原因。
    """
    t0 = time.time()
    cmd = build_cmd(chal, arm, model)

    # ① 隔离沙箱：只复制题目材料，**排除 answer.txt / answer/ / 已存在的产物**
    sandbox = Path(tempfile.mkdtemp(prefix="bench-"))
    try:
        for f in sorted(chal["dir"].iterdir()):
            if f.name in ("answer.txt", "answer", "solution.md"):
                continue
            if f.is_file():
                shutil.copy2(f, sandbox / f.name)
            elif f.is_dir() and f.name not in ("answer",):
                shutil.copytree(f, sandbox / f.name, dirs_exist_ok=True)
        # 判分脚本与题目分开：verify.sh 留在 sandbox 但答案文件不在
    except Exception as e:
        shutil.rmtree(sandbox, ignore_errors=True)
        return {"ok": False, "err": "建沙箱失败：%s" % e, "seconds": 0.0}

    try:
        r = subprocess.run(cmd, cwd=str(sandbox), capture_output=True,
                           text=True, timeout=timeout_s,
                           encoding="utf-8", errors="replace")
        rc, out = r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        rc, out = 124, "agent 超时"
    except FileNotFoundError:
        shutil.rmtree(sandbox, ignore_errors=True)
        return {"ok": False, "err": "找不到 pi（先确认 PATH）", "seconds": 0.0}

    # ② agent 非 0 = 没干活（缺 key / 崩了），不能算通过
    if rc != 0:
        shutil.rmtree(sandbox, ignore_errors=True)
        reason = "agent 异常退出 rc=%d" % rc
        if "No API key" in out or "API key not found" in out:
            reason = "pi 没有 API key（本次跑分无效）"
        return {"ok": False, "agent_rc": rc, "err": reason,
                "agent_tail": out[-400:], "seconds": round(time.time() - t0, 1)}

    # 判分：在沙箱里跑（答案只能由 agent 自己写出）
    ok_sb, ev = judge({"dir": sandbox, "id": chal["id"]})
    shutil.rmtree(sandbox, ignore_errors=True)
    return {"ok": ok_sb, "agent_rc": rc, "verify_ev": ev,
            "agent_tail": out[-400:], "seconds": round(time.time() - t0, 1)}


def metrics(rows: list[dict], n_chal: int, rounds: int) -> dict:
    """从逐轮结果算 pass@1 / union@N / reliable / 方差差。"""
    per: dict[str, list[bool]] = {}
    for r in rows:
        per.setdefault(r["id"], []).append(bool(r["ok"]))
    total = sum(len(v) for v in per.values())
    passed = sum(sum(1 for x in v if x) for v in per.values())
    union = sum(1 for v in per.values() if any(v))
    reliable = sum(1 for v in per.values() if v and all(v))
    p1_raw = passed / total if total else 0.0
    uN_raw = union / n_chal if n_chal else 0.0
    return {
        "runs": total,
        "pass@1": round(p1_raw, 4),
        "union@N": round(uN_raw, 4),
        "reliable": round(reliable / n_chal, 4) if n_chal else 0.0,
        # ★ 用**未取整**的值相减再取整：先各自 round 再减会出现两次取整误差
        #   （实测 0.6667-0.4444=0.2223 而真值 0.2222），审数会以为算错了。
        "variance_gap": round(uN_raw - p1_raw, 4),
        "per_challenge": {k: v for k, v in sorted(per.items())},
    }


# ─────────────────────────── 造题（自检用） ───────────────────────────

def _judge_case(root: Path, name: str, body: str, timeout: int = 300):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "task.md").write_text("t", encoding="utf-8", newline="\n")
    # ★ newline="\n" 不能省：Windows 上 pathlib 默认写 CRLF，脚本就变成
    #   "exit 0\r" → bash 报 "numeric argument required"，判分恒假红。
    #   实测踩到：自检里「退出码 0 → True」这条直接失败，而人是照着它造题的。
    (d / "verify.sh").write_text("#!/bin/bash\n" + body + "\n",
                                 encoding="utf-8", newline="\n")
    return judge({"id": name, "dir": d}, seconds=timeout)


# ─────────────────────────── 子命令 ───────────────────────────

def cmd_list() -> int:
    chals = list_challenges()
    if not chals:
        print(f"bench/challenges/ 里没有可用题目（要 task.md + verify.sh）→ {CHAL}")
        return 1
    print(f"共 {len(chals)} 题 → {CHAL}")
    for c in chals:
        has = "有参考解" if c["has_answer"] else "无参考解"
        print("  %-24s %s" % (c["id"], has))
    return 0


def cmd_run(arm, rounds, model, timeout_s, only):
    chals = list_challenges()
    if only:
        chals = [c for c in chals if c["id"] in only]
    if not chals:
        print("没有题目可跑 → python3 tools/bench_local.py list")
        return 1
    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    rows = []
    print("arm=%s rounds=%d model=%s 题数=%d" % (arm, rounds, model, len(chals)))
    for rd in range(1, rounds + 1):
        for c in chals:
            res = run_once(c, arm, model, timeout_s)
            mark = "✓" if res.get("ok") else "✗"
            print("  [%d/%d] %-20s %s %ss" % (rd, rounds, c["id"], mark,
                                              res.get("seconds", 0)))
            rows.append({"id": c["id"], "round": rd, **res})
    m = metrics(rows, len(chals), rounds)
    out = {"arm": arm, "rounds": rounds, "model": model,
           "at": stamp, "metrics": m, "rows": rows}
    f = RESULTS / ("%s-%s.json" % (stamp, arm))
    f.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("")
    print("pass@1=%s  union@N=%s  reliable=%s  方差差=%s"
          % (m["pass@1"], m["union@N"], m["reliable"], m["variance_gap"]))
    print("→ %s" % f)
    return 0


def cmd_compare():
    if not RESULTS.is_dir():
        print("还没跑过 → python3 tools/bench_local.py run --arm skills --rounds 3")
        return 1
    latest = {}
    for f in sorted(RESULTS.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        latest[d.get("arm", "?")] = f
    if not latest:
        print("results/ 里没有可解析的结果")
        return 1
    print("%-10s %8s %9s %9s %8s" % ("arm", "pass@1", "union@N", "reliable", "方差差"))
    print("-" * 50)
    for arm in sorted(latest):
        d = json.loads(latest[arm].read_text(encoding="utf-8"))
        m = d["metrics"]
        print("%-10s %8s %9s %9s %8s"
              % (arm, m["pass@1"], m["union@N"], m["reliable"], m["variance_gap"]))
    if len(latest) == 2:
        print("")
        print("★ 两臂只差 --skill/--no-skills，其余完全一致才叫对照。")
        print("★ 方差差 >0 说明单轮结果不可信 —— 这正是 A 项要量的东西。")
    return 0


def cmd_selftest() -> int:
    """不花模型额度：用假 runner 验证指标算法 + 判分链路 + 两臂只差一个变量。"""
    print("── bench_local 自检（不调用模型）──")
    fails = [0]

    def ck(name, cond):
        print("  %s %s" % ("✓" if cond else "✗", name))
        if not cond:
            fails[0] += 1

    # ① 指标算法：构造已知输入，手算期望值
    rows = [
        {"id": "a", "ok": True}, {"id": "a", "ok": True}, {"id": "a", "ok": True},
        {"id": "b", "ok": False}, {"id": "b", "ok": False}, {"id": "b", "ok": True},
        {"id": "c", "ok": False}, {"id": "c", "ok": False}, {"id": "c", "ok": False},
    ]
    m = metrics(rows, 3, 3)
    ck("runs 计数 = 9", m["runs"] == 9)
    ck("pass@1 = 4/9", m["pass@1"] == round(4 / 9, 4))
    ck("union@3 = 2/3（b 过过一次）", m["union@N"] == round(2 / 3, 4))
    ck("reliable = 1/3（只有 a 全过）", m["reliable"] == round(1 / 3, 4))
    ck("方差差 = union - pass@1", m["variance_gap"] == round(2 / 3 - 4 / 9, 4))

    # ② 边界：全过 / 全不过
    m2 = metrics([{"id": "x", "ok": True}] * 3, 1, 3)
    ck("全过 → 三项 1.0 且方差差 0",
       m2["pass@1"] == 1.0 and m2["union@N"] == 1.0
       and m2["reliable"] == 1.0 and m2["variance_gap"] == 0.0)
    m3 = metrics([{"id": "x", "ok": False}] * 3, 1, 3)
    ck("全不过 → pass@1 与 union 都 0",
       m3["pass@1"] == 0.0 and m3["union@N"] == 0.0)

    # ③ 判分链路：必过 / 必不过 / 超时
    # ★ 不能用 BENCH/_selftest：题库目录可能是只读挂载（历史上容器里 bench/ 就是 :ro；
    #   2026-11-17 起仓库里的 bench/ 已删，但自建题库仍可能只读），
    #   自检要造临时题 → OSError [Errno 30] Read-only file system。
    #   用系统临时目录：宿主/容器都能写，且不污染题库。
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="bench-selftest-"))
    shutil.rmtree(tmp, ignore_errors=True)
    ck("判分：退出码 0 → True", _judge_case(tmp, "pass", "exit 0")[0])
    ck("判分：退出码 1 → False", not _judge_case(tmp, "fail", "exit 1")[0])
    ck("判分：超时 → False（不挂死）",
       not _judge_case(tmp, "slow", "sleep 5", timeout=1)[0])

    # ④ 两臂只差一个变量（防「改了别的还叫对照」）
    _judge_case(tmp, "armcheck", "exit 0")
    fake = {"id": "armcheck", "dir": tmp / "armcheck"}
    a = build_cmd(fake, "skills", "m")
    b = build_cmd(fake, "noskills", "m")
    diff = [x for x in a if x not in b] + [x for x in b if x not in a]
    ck("两臂差异只在 --skill/--no-skills",
       set(diff) <= {"--skill", str(LAB / "skills"), "--no-skills"})

    shutil.rmtree(tmp, ignore_errors=True)
    print("")
    if fails[0]:
        print("❌ %d 项失败" % fails[0])
        return 1
    print("✅ 全部通过")
    return 0


def main(argv) -> int:
    ap = argparse.ArgumentParser(description="本地评测台（不依赖平台 token）")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("list", help="列出本地题")
    p1 = sub.add_parser("run", help="跑 N 轮")
    p1.add_argument("--arm", default="skills", choices=["skills", "noskills"])
    p1.add_argument("--rounds", type=int, default=3)
    p1.add_argument("--model", default="deepseek/deepseek-flash")
    p1.add_argument("--timeout", type=int, default=900)
    p1.add_argument("--only", nargs="*", default=None)
    sub.add_parser("compare", help="两臂并排 + 方差")
    sub.add_parser("selftest", help="自检（不调用模型）")
    a = ap.parse_args(argv)

    if a.cmd == "selftest":
        return cmd_selftest()
    if a.cmd == "list":
        return cmd_list()
    if a.cmd == "run":
        return cmd_run(a.arm, a.rounds, a.model, a.timeout, a.only)
    if a.cmd == "compare":
        return cmd_compare()
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
