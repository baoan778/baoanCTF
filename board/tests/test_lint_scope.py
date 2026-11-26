#!/usr/bin/env python3
"""lint_zero 的「匹配范围」回归锁 —— 治「门禁自己制造噪声」。

跑法：    python3 board/tests/test_lint_scope.py

为什么要有它
------------
2026-11-18 跑 `python3 tools/lint_zero.py`：**190 条 WARN，几乎全是误报**。
两类最刺眼：

① subprocess 规则只按**属性名**匹配
       fn = getattr(n.func, 'attr', getattr(n.func, 'id', ''))
       if fn in ('run', 'call', 'check_output'):
   于是**任何叫 run/call 的本地函数**都被当成 subprocess.run。
   铁证：tools/pcapmap.py 全文件只有 1 处真 subprocess.run（带 timeout=timeout），
   被判「无 timeout」的另外 8 处全是调它自己的 run(cmd, timeout) 包装器；
   board/handover.py 的 run(*args, timeout=600) 同理。→ 11 条假警。

② read_text 规则靠**手写变量名白名单**豁免内部路径（DOC/text/src/...），
   名单外的内部变量（gp/cand/lib/qfile/gf/leg/...）全被报。实际被判的 36 条里绝大多数
   读的是黑板自己的 PROJECTS.glob("*/graph.json") 和仓库内的手法库 —— 不是外部输入。

这类误报的代价不是"看着烦"：真问题（winbridge 的 wslpath、IDA、Ghidra 三处真没 timeout）
被埋在 190 行里，谁也不会去看。门禁一旦开始狼来了，下一次真问题就没人信。

本测试把两类判据都钉死，**改 lint_zero 时先跑它**：
  · 本地 run()/call() 包装器 → 不许报
  · 真 subprocess.run / sp.run / from subprocess import run → 必须报
  · 外部路径（argparse 属性 / 函数形参）read_text → 必须报
  · 内部生成物（模块级常量、glob 结果）read_text → 不许报
  · `lint:ok 理由` 显式豁免 → 必须生效（且只罩到空行为止）
  · 「控制台 utf-8 兜底」样板 except: pass → 不许报
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
LINT = ROOT / "tools" / "lint_zero.py"
passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail and not cond else ""))


def run_lint(files: list[Path]) -> tuple[int, str]:
    r = subprocess.run([sys.executable, str(LINT), *[str(f) for f in files]],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=180, cwd=str(ROOT))
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def warns(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if ln.startswith("WARN")]


def main() -> int:
    print("== lint_zero 匹配范围 ==")
    if not LINT.exists():
        print("SKIP 找不到 tools/lint_zero.py（发布包形态）")
        return 77

    td = Path(tempfile.mkdtemp(prefix="lzscope-"))
    try:
        # ── ① 本地包装器不该被当成 subprocess ────────────────────────────
        local_wrap = td / "local_wrap.py"
        local_wrap.write_text(
            'import subprocess\n'
            '\n'
            'def run(*args, cwd=None, timeout=600):\n'
            '    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True,\n'
            '                       encoding="utf-8", timeout=timeout)\n'
            '    return p.returncode\n'
            '\n'
            'def call(x):\n'
            '    return run("git", "log", "-1", cwd=x)\n'
            '\n'
            'def go():\n'
            '    return call(1) + run("git", "status")\n',
            encoding="utf-8")
        rc, out = run_lint([local_wrap])
        w = warns(out)
        check("本地 run()/call() 包装器不报 subprocess", not [x for x in w if "subprocess." in x],
              "；".join(w)[:200])
        check("本地包装器用例 rc=0", rc == 0, f"rc={rc} {out[-200:]}")

        # ── ② 真 subprocess（三种 import 形态）必须报 ────────────────────
        real_sp = td / "real_sp.py"
        real_sp.write_text(
            'import subprocess\n'
            'import subprocess as sp\n'
            'from subprocess import run as srun\n'
            '\n'
            'def go():\n'
            '    subprocess.run(["ls"], capture_output=True, text=True, encoding="utf-8")\n'
            '    sp.call(["ls"], capture_output=True)\n'
            '    srun(["ls"], capture_output=True, encoding="utf-8")\n'
            '    subprocess.run(["ls"], capture_output=True, text=True, timeout=5)\n',
            encoding="utf-8")
        rc, out = run_lint([real_sp])
        w = warns(out)
        check("subprocess.run 无 timeout 报出",
              any("subprocess.run 无 timeout" in x for x in w), "；".join(w)[:300])
        check("别名 sp.call 无 timeout 报出",
              any("subprocess.call 无 timeout" in x for x in w), "；".join(w)[:300])
        check("from subprocess import run 无 timeout 报出",
              any("subprocess.run 无 timeout" in x for x in w), "；".join(w)[:300])
        enc = [x for x in w if "无 encoding" in x]
        check("text=True 带 encoding 不报 / 不带 encoding 要报",
              len(enc) == 1 and ":9:" in enc[0], "；".join(w)[:300])

        # ── ③ read_text：外部来源要报，内部来源不报 ──────────────────────
        rt = td / "rt.py"
        rt.write_text(
            'import argparse\n'
            'from pathlib import Path\n'
            '\n'
            'PROJECTS = Path("work")\n'
            '\n'
            'def walk_all():\n'
            '    for gp in sorted(PROJECTS.glob("*/graph.json")):\n'
            '        _ = gp.read_text(encoding="utf-8")\n'
            '\n'
            'def from_param(p):\n'
            '    return p.read_text(encoding="utf-8")\n'
            '\n'
            'def from_args(a):\n'
            '    return a.op_file.read_text(encoding="utf-8")\n',
            encoding="utf-8")
        rc, out = run_lint([rt])
        w = [x for x in warns(out) if "read_text" in x]
        check("glob 出来的内部路径 read_text 不报",
              not any(":9:" in x or "/9:" in x for x in w), "；".join(w)[:300])
        check("函数形参 read_text 报",
              any("read_text() 直读外部路径 p" in x for x in w), "；".join(w)[:300])
        check("argparse 属性 read_text 报",
              any("a.op_file" in x for x in w), "；".join(w)[:300])

        # ── ④ lint:ok 显式豁免 ──────────────────────────────────────────
        waived = td / "waived.py"
        waived.write_text(
            'from pathlib import Path\n'
            '\n'
            'def load(p):\n'
            '    # lint:ok 这里 p 来自内部清单，且下面这行就是限长判据\n'
            '    if p.stat().st_size > 1024:\n'
            '        return ""\n'
            '    return p.read_text(encoding="utf-8")\n'
            '\n'
            'def other(p):\n'
            '    return p.read_text(encoding="utf-8")\n',
            encoding="utf-8")
        rc, out = run_lint([waived])
        w = [x for x in warns(out) if "read_text" in x]
        check("lint:ok 豁免生效（跨一到两行护栏）", len(w) == 1, "；".join(w)[:300])
        check("lint:ok 不会罩住下一个函数",
              any("read_text() 直读外部路径 p" in x for x in w), "；".join(w)[:300])

        # ── ⑤ 控制台 utf-8 兜底样板不报 ─────────────────────────────────
        hdr = td / "hdr.py"
        hdr.write_text(
            'import sys\n'
            '\n'
            'try:\n'
            '    for _s in (sys.stdout, sys.stderr):\n'
            '        _s.reconfigure(encoding="utf-8", errors="replace")\n'
            'except Exception:\n'
            '    pass\n',
            encoding="utf-8")
        rc, out = run_lint([hdr])
        check("utf-8 兜底样板 except:pass 不报",
              not [x for x in warns(out) if "except 里只有 pass" in x], out[-200:])

        # ── ⑥ 真问题仍然会红：裸 except + 语法错误 ──────────────────────
        bad = td / "bad.py"
        bad.write_text('def f():\n    try:\n        pass\n    except:\n        pass\n',
                       encoding="utf-8")
        rc, out = run_lint([bad])
        check("裸 except 判 FAIL", "裸 except" in out and rc == 1, f"rc={rc}")

        syn = td / "syn.py"
        syn.write_text("def f(:\n", encoding="utf-8")
        rc, out = run_lint([syn])
        check("语法错误判 FAIL", "语法错误" in out and rc == 1, f"rc={rc}")

        # ── ⑦ 全仓现状：FAIL 必须为 0（WARN 不拦） ──────────────────────
        scan_files = sorted(str(p) for p in (ROOT / "board").glob("*.py")) + \
                     sorted(str(p) for p in (ROOT / "tools").glob("*.py"))
        rc, out = run_lint([Path(f) for f in scan_files])
        check("全仓 lint_zero FAIL=0", rc == 0, f"rc={rc} {out[-400:]}")
        check("全仓覆盖到 board/*.py 与 tools/*.py", len(scan_files) > 40, f"只扫了 {len(scan_files)}")
    finally:
        shutil.rmtree(td, ignore_errors=True)

    print(f"\n{'=' * 40}\n通过 {len(passed)} / 失败 {len(failed)}")
    return len(failed)


if __name__ == "__main__":
    sys.exit(main())
