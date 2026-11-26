#!/usr/bin/env python3
"""bashfind 的回归锁 —— 治「工具自己起不来，却把锅甩给脚本」。

跑法：    python3 board/tests/test_bashfind.py

为什么要有它
-----------
2026-11-23 一次环境事故连环打红两条链：
  · tools/lint_sh.py    把**267 个** shell 脚本全判成语法错误；
  · tools/bench_local.py 判分恒假红（verify.sh 明明 exit 0）。
根因不在被检对象，而在**起 bash 的方式**：
Windows 的 CreateProcess 把 System32 排在 PATH 之前，
本机 C:/Windows/System32/bash.exe 是起不来的 WSL 存根 → 裸名 "bash" 恒失败，
而 shell 里敲 bash 走 PATH 所以一切正常 —— 极难归因。

本测试把三层判据钉死：
  1. BASH 必须是**绝对路径**且真能执行（不是裸名、不是那个存根）
  2. is_access_denied 必须认出 UTF-16 的「拒绝访问」帧（起不来 ≠ 脚本语法错）
  3. lint_sh / bench_local 必须真的用 BASH（静态断言，防有人改回裸名）
"""
import os
import subprocess
import sys
from pathlib import Path

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print("  %s %s%s" % ("OK" if cond else "XX", name,
                         ("   " + detail) if detail and not cond else ""))


def main() -> int:
    sys.path.insert(0, str(TOOLS))
    import bashfind

    print("1) BASH 解析：绝对路径 + 真能执行")
    b = bashfind.BASH
    check("BASH 不是裸名（给了路径）", os.path.dirname(b) != "",
          "BASH=%r" % b)
    if os.path.dirname(b):
        check("BASH 指向真实存在的文件", os.path.isfile(b), b)
        r = subprocess.run([b, "-c", "echo PI_OK"], capture_output=True, timeout=30)
        check("BASH 真能 -c 执行", r.returncode == 0 and b"PI_OK" in (r.stdout or b""),
              "rc=%d err=%r" % (r.returncode, (r.stderr or b"")[:80]))
        check("BASH 不是 System32 那个 WSL 存根",
              "system32" not in b.lower().replace("\\", "/"),
              b)

    print("2) 探针口径：--version 能跑不算数，必须 -c 真执行")
    # 这是我自己第一次写错的地方：--version 在坏 shim 上也能返回 0。
    src = (TOOLS / "bashfind.py").read_text(encoding="utf-8", errors="replace")
    check("_works 用 -c 探而不是 --version", '-c' in src and 'PI_LAB_BASH_OK' in src)
    check("_works 里没有拿 --version 当判据",
          '[path, "--version"]' not in src and "[path, '--version']" not in src)

    print("3) is_access_denied：认出「起不来」的两种编码")
    check("UTF-16 的拒绝访问被认出",
          bashfind.is_access_denied("拒绝访问。".encode("utf-16-le")))
    check("英文 Access is denied 被认出",
          bashfind.is_access_denied(b"Access is denied."))
    check("普通语法错**不**被误认",
          not bashfind.is_access_denied(b"line 3: syntax error near unexpected token"))
    check("空 stderr 不误报", not bashfind.is_access_denied(b""))
    check("空 None 不误报", not bashfind.is_access_denied(None))

    print("4) 接线：lint_sh / bench_local 必须真的用 BASH（防改回裸名）")
    lint = (TOOLS / "lint_sh.py").read_text(encoding="utf-8", errors="replace")
    check("lint_sh 从 bashfind 取 BASH", "from bashfind import" in lint)
    check("lint_sh 的 syntax_ok 用 [BASH,", "[BASH, \"-n\"" in lint)
    check("lint_sh 里没有裸名 bash 起子进程",
          'subprocess.run(["bash"' not in lint and "subprocess.run(['bash'" not in lint)
    bench = (TOOLS / "bench_local.py").read_text(encoding="utf-8", errors="replace")
    check("bench_local 从 bashfind 取 BASH", "from bashfind import BASH" in bench)
    check("bench_local 判分用 [BASH,", "[BASH, \"./verify.sh\"]" in bench)
    check("bench_local 里没有裸名 bash 判分",
          '["bash", "./verify.sh"]' not in bench)
    check("两边都有 ImportError 兜底（发布包不带 bashfind 时不崩）",
          "except ImportError" in lint and "except ImportError" in bench)

    print("5) 端到端：lint_sh 对一个**好**脚本给绿、对**坏**脚本给红")
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="pilab-bashfind-"))
    good = tmp / "good.sh"
    good.write_text("#!/bin/bash\necho ok\n", encoding="utf-8", newline="\n")
    bad = tmp / "bad.sh"
    bad.write_text("#!/bin/bash\nif [ 1 ; then\n", encoding="utf-8", newline="\n")
    p = subprocess.run([sys.executable, str(TOOLS / "lint_sh.py"),
                        str(good), str(bad)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=180, cwd=str(ROOT))
    out = (p.stdout or "") + (p.stderr or "")
    # ★ 按**文件名**判定，别按「第几个 FAIL」切 —— 第一次写成
    #   out.split("bad.sh")[0]，而实际输出里 bad.sh 出现在 FAIL 行**之中**，
    #   切片把好脚本那半也带上了坏脚本的 FAIL，于是假红（自己踩的，记在这）。
    fail_lines = [ln for ln in out.splitlines() if ln.strip().startswith("FAIL")]
    check("好脚本不进 FAIL 行",
          not any("good.sh" in ln for ln in fail_lines),
          "\n".join(fail_lines)[:200])
    check("坏脚本被点名为 syntax error",
          any("bad.sh" in ln and "syntax" in ln.lower() for ln in fail_lines),
          out[:300])

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("=" * 40)
    print("通过 %d / 失败 %d" % (len(passed), len(failed)))
    if failed:
        print("失败项：" + " | ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
