#!/usr/bin/env python3
"""多旗题下 `fgs next` 不给 `none` 的遗留缺陷回归（2026-11-03 修）。

    python3 board/tests/test_next_termination.py

## 缺陷是什么（实测复现，不是猜）

多旗题的真实形态：**拿到了 1 面旗、但题还没打完**（平台上是"部分得分"）。
这种项目在黑板上的样子是：
  · 图上有 verified Fact（第一面旗的证据）
  · `solved` 仍然是 None（因为还没解完）
  · 跑过 `fgs checkpoint`（reason 水位已记）
  · 没有 open / claimed 的 Step

旧 `decide()` 的最后一段是：

    if ck.get("open_intents") and not open_s:
        return "reason", ...
    if not g.get("bootstrapped"):          # ← 无条件，短路了下面的终止性门禁
        return "bootstrap", ...
    return ("none", ...)                   # ← 永远到不了

于是 `fgs next` **连跑 N 次恒返 `bootstrap`**：既不返回 `none`，又在把同一件活
反复派出去（"多派任务"）。根因是把 `bootstrapped`（一次性初始化标记）
当成了图状态 —— 能走到这个分支说明图上早有探索产物，再派 bootstrap 是重复劳动。

## 本测试锁住什么

  A. ★ 多旗题（拿 1 面 / 未 solved / ck 已记 / 无 open step）→ `next` 必须给 `none`
  B. ★ 连跑 4 次都是 `none`（不许第一轮 none、第二轮又 bootstrap）
  C. `bootstrap` 仍只在**真初始态**出现：无 fact 无 step 且没 bootstrapped
  D. 已有 fact 但从未 checkpoint → `reason`（第一次总要人读图）
  E. 打过 `checkpoint --bootstrapped` 的老路径不变：先 reason（水位清零）、空手后 none
  F. 新 Fact / 新 Hint 仍能把项目从 `none` 拽回 `reason`（终止性不许把项目焊死）
  G. `bootstrapped` 标记仍出现在 none 的提示里（标记没被删，只是不再改变调度结论）

为什么必须单独一个文件：这套判据的分支顺序很容易在后续重构里被再次打乱，
而它错了的代价是**静默空转**（没人会盯着 next 输出数它给了几次 bootstrap）。
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
PID = "next_term"
passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail and not cond else ""))


def fgs(lab, *args):
    env = dict(os.environ)
    env["PI_LAB_BOARD_DIR"] = lab
    # ★ 2026-11-11 修（与 test_cli_smoke 2026-11-07 同一个坑）：原来只隔离了 BOARD，
    #   没隔离 WORK → fgs 里 worker_dir() 会退回**真 work/<pid>/**，并行跑时和
    #   test_matrix_fuzz 等共用同一批 pid 的文件互相踩，表现为 E/F 段整片假红
    #   （单跑 15/0、批量跑 9/6）。两处都隔离才算真隔离。
    env["PI_LAB_WORK_DIR"] = str(Path(lab).parent / "work")
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PI_SESSION_ID", None)          # 别让真会话 id 干扰 lead 判定
    env["PI_LAB_OWNER"] = "test-owner"
    p = subprocess.run([sys.executable, str(FGS), *args],
                       capture_output=True, env=env, timeout=180)
    return (p.returncode,
            p.stdout.decode("utf-8", "replace"),
            p.stderr.decode("utf-8", "replace"))


def task_of(lab):
    """取 next 的 task 字段（--json 走程序，别去正则中文）。"""
    rc, out, err = fgs(lab, "next", PID, "--json")
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line).get("task"), out
            except json.JSONDecodeError:
                continue
    return None, out + err


def fresh(lab):
    fgs(lab, "init", PID, "--kind", "ctf")
    fgs(lab, "goal", PID, "多旗题：共 6 面")


def add_verified_fact(lab, text="面1 flag{a}"):
    fgs(lab, "fact-add", PID, text, "--confidence", "verified")


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="fgs-next-term-")
    lab = str(Path(tmp, "projects"))
    Path(lab).mkdir(parents=True, exist_ok=True)

    print("0) 真初始态：没目标 → goal；有目标无探索产物 → bootstrap")
    fresh(lab)
    # fresh 已建 goal，所以此刻是 bootstrap
    t, raw = task_of(lab)
    check("C. 有 goal、无 fact 无 step → bootstrap", t == "bootstrap", f"实际 {t} :: {raw[:200]}")
    # 空项目：只 init、不建 goal → 必须先把目标定下来
    lab_empty = str(Path(tmp, "p2", "projects"))
    Path(lab_empty).mkdir(parents=True, exist_ok=True)
    fgs(lab_empty, "init", PID, "--kind", "ctf")
    t_empty, raw_empty = task_of(lab_empty)
    check("C. 空项目（连 goal 都没有）→ goal", t_empty == "goal",
          f"实际 {t_empty} :: {raw_empty[:200]}")

    print("1) ★ 多旗题：拿了 1 面旗、未 solved、ck 已记、无 open step")
    fresh(lab)
    fgs(lab, "step-add", PID, "面1")
    fgs(lab, "step-claim", PID, "s001", "--by", "w1")
    fgs(lab, "step-conclude", PID, "s001", "拿到面1")
    add_verified_fact(lab)
    fgs(lab, "checkpoint", PID)                 # ck 已记：图与水位一致
    t, raw = task_of(lab)
    check("A. ★ 必须给 none（旧实现在此恒给 bootstrap）", t == "none", f"实际 {t} :: {raw[:250]}")
    check("A. 理由说明「图没变」并在要求人出手",
          "图没变" in raw and "hint" in raw, raw[:300])
    check("G. bootstrapped 标记仍在提示里（没被删掉）",
          "bootstrapped" in raw, raw[:300])

    print("2) ★ 连跑 4 次都是 none（不许自激反复派活）")
    seq = [task_of(lab)[0] for _ in range(4)]
    check("B. ★ 连续 4 次全 none", all(x == "none" for x in seq), f"实际 {seq}")

    print("3) 已有 fact 但从未 checkpoint → reason（第一次总要人读图）")
    lab2 = str(Path(tmp, "p3", "projects"))
    Path(lab2).mkdir(parents=True, exist_ok=True)
    fgs(lab2, "init", PID, "--kind", "ctf")
    fgs(lab2, "goal", PID, "多旗")
    add_verified_fact(lab2)
    t2, raw2 = task_of(lab2)
    check("D. 有 fact、ck 未记 → reason", t2 == "reason", f"实际 {t2} :: {raw2[:200]}")

    print("4) 打过 --bootstrapped 的老路径不变：先 reason，空手后 none")
    lab3 = str(Path(tmp, "p4", "projects"))
    Path(lab3).mkdir(parents=True, exist_ok=True)
    fgs(lab3, "init", PID, "--kind", "ctf")
    fgs(lab3, "goal", PID, "多旗")
    add_verified_fact(lab3)
    fgs(lab3, "checkpoint", PID, "--bootstrapped")
    t3, _ = task_of(lab3)
    check("E. bootstrap 收尾后 → reason（水位清零，不吃掉这一次）", t3 == "reason", f"实际 {t3}")
    fgs(lab3, "checkpoint", PID)
    t3b, _ = task_of(lab3)
    check("E. 空手后 → none", t3b == "none", f"实际 {t3b}")

    print("5) 终止性不许把项目焊死：新 Fact / 新 Hint 要能拽回 reason")
    fgs(lab3, "fact-add", PID, "面2 flag{b}", "--confidence", "verified")
    t4, _ = task_of(lab3)
    check("F. 新 Fact → reason", t4 == "reason", f"实际 {t4}")
    fgs(lab3, "checkpoint", PID)
    check("F. 记完水位又回 none", task_of(lab3)[0] == "none", "")
    fgs(lab3, "hint", PID, "出题人说 flag 在环境变量里", "--from", "operator")
    t5, _ = task_of(lab3)
    check("F. 新 Hint → reason", t5 == "reason", f"实际 {t5}")
    fgs(lab3, "checkpoint", PID)
    fgs(lab3, "step-add", PID, "读 /proc/1/environ")
    t6, _ = task_of(lab3)
    check("F. 新 open Step → explore（回到干活）", t6 == "explore", f"实际 {t6}")

    print("6) 不许多派：none 之后图没变，next 不许再给 bootstrap/explore")
    counts = [task_of(lab)[0] for _ in range(3)]
    check("不出现 bootstrap（多派的主要形态）", "bootstrap" not in counts, f"实际 {counts}")
    check("不出现 explore（没有 open step 就没得派）", "explore" not in counts, f"实际 {counts}")

    total, bad = len(passed) + len(failed), len(failed)
    print(f"\n通过 {len(passed)} / 失败 {bad}")
    if bad:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ next 终止性 / 多旗题不给 none 的遗留缺陷：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
