#!/usr/bin/env python3
"""回归测试：写锁（并发不丢更新 + 架构不变量）

跑法：
    python3 board/tests/test_locking.py

为什么要有它
------------
实测踩过：`board/fgs.py` 里 14 处 `save()`，**只有 5 处套了 `Lock`** ——
`fact-add` / `hint` / `step-add` / `relate` / `apply` 全是裸的读-改-写。
两条写命令撞在同一秒，后写的那条会把先写的**整个覆盖掉**，而且**没有任何报错**。
（很现实：我自己就习惯一次发两条 bash 并行跑命令。）

这个文件守三件事：
  T1 并发写不丢更新（8 条同时发，8 条都要在）
  T2 **架构不变量**：任何含 `save(` 的函数必须含 `with Lock`（AST 静态检查，
     防的是"以后新增写函数忘了加锁"）
  T3 锁可重入（`init` 会在自己的锁里调 `_set_goal`；不可重入就自己锁死）
  T4 跑完不留残留 `.lock`
"""
import ast
import json
import shutil
import subprocess
import os
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

ROOT = Path(__file__).resolve().parents[1]
FGS = ROOT / "fgs.py"
PID = "_test_locking"

# ★ 隔离：临时项目绝不能建到真 board/projects 里。
# 踩过的坑（2026-09-29）：原来直接写 ROOT/projects/_test_locking，
# 4 路并行时被 `test_handover` 数进「真目录里有几块黑板」→ 它偶发变红，
# 还被当成 drvfs 负载抖动记进了状态快照。而本仓自己的规矩就写着“测试别碰真产物”。
TMPDIR = Path(tempfile.mkdtemp(prefix="pilab-locking-"))
PDIR = TMPDIR / "projects" / PID
ENV = dict(os.environ, PI_LAB_BOARD_DIR=str(TMPDIR / "projects"))

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail and not cond else ""))


def fgs(*args):
    return subprocess.run([sys.executable, str(FGS), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=ENV)


def facts():
    g = json.loads((PDIR / "graph.json").read_text(encoding="utf-8"))
    return sorted(v["text"] for v in g["nodes"].values() if v["type"] == "fact")


def main():
    shutil.rmtree(PDIR, ignore_errors=True)

    print("T1 并发写不丢更新（8 条同时发）")
    rc = fgs("init", PID, "--kind", "ctf", "--goal", "锁测试")
    check("init 成功（锁可重入、不死锁）", rc.returncode == 0, rc.stderr[:200])
    procs = [subprocess.Popen([sys.executable, str(FGS), "fact-add", PID, f"并发 {i}",
                               "--confidence", "verified"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=ENV)
             for i in range(8)]
    for p in procs:
        p.wait()
    got = facts()
    check("8 条全部落盘", len(got) == 8, f"只落了 {len(got)} 条：{got}")
    check("内容无重复", len(set(got)) == len(got), str(got))

    print("T2 架构不变量：含 save() 的函数必须套 Lock（AST 静态检查）")
    src = (ROOT / "fgs.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    naked = []
    for fn in [n for n in tree.body if isinstance(n, ast.FunctionDef)]:
        saves = [n for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "save"]
        if not saves:
            continue
        locked = any(isinstance(n, ast.With) and "Lock(" in ast.unparse(n.items[0].context_expr)
                     for n in ast.walk(fn))
        if not locked:
            naked.append(fn.name)
    check("没有裸写函数", not naked, f"裸写：{naked}")

    print("T3 锁可重入（同进程重复获取不死锁）")
    rc = fgs("goal", PID, "改目标")            # goal 内部也会走 _set_goal 的锁
    check("goal 在 15s 锁超时内完成", rc.returncode == 0, rc.stderr[:200])
    check("goal 生效", "改目标" in (PDIR / "graph.json").read_text(encoding="utf-8"))

    print("T4 跑完不留残留 .lock")
    check("无 .lock 目录", not (PDIR / ".lock").exists())
    check("无 .graph.*.tmp 半成品", not list(PDIR.glob(".graph.*.tmp")))
    check("无 .lead.*.tmp 半成品", not list(PDIR.glob(".lead.*.tmp")))

    print("T5 交叉进程并发（两个进程同时改同一项目，都要生效）")
    a = subprocess.Popen([sys.executable, str(FGS), "hint", PID, "并发 hint A"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=ENV)
    b = subprocess.Popen([sys.executable, str(FGS), "step-add", PID, "并发 step B"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=ENV)
    a.wait(); b.wait()
    g = json.loads((PDIR / "graph.json").read_text(encoding="utf-8"))
    texts = [v["text"] for v in g["nodes"].values()]
    check("hint 生效", any("并发 hint A" == t for t in texts), str(texts)[:200])
    check("step 生效", any("并发 step B" == t for t in texts), str(texts)[:200])

    shutil.rmtree(PDIR, ignore_errors=True)
    print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ 写锁：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
