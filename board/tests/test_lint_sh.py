#!/usr/bin/env python3
"""lint_sh 的回归锁 —— 治「容器起不来」这类只藏在 shell 里的死法。

跑法：    python3 board/tests/test_lint_sh.py

为什么要有它
-----------
2026-11-18 重建镜像后，容器无限重启（Restarting(1)），日志只有一行：
    /usr/local/bin/provision-agent.sh: line 163: ROOT: unbound variable
根因：脚本头写着 `set -euo pipefail`，2026-11-14 加的补丁段引用了 $ROOT，
但**整个文件从未定义 ROOT**。旧镜像里那份还是 11-01 的旧版，所以这个 bug
在源码里躺了几天都没被发现 —— 直到镜像重建把它烤进去。

ruff 只扫 Python，管不到 shell；本机没有 shellcheck（pip 拉不动）。
所以 tools/lint_sh.py 自己做了两类检查，本测试把两类判据都钉死：

  · bash -n 语法错误                      → 必须 FAIL
  · set -u 下引用了从未赋值的 $NAME     → 必须 FAIL（正是那次事故的形态）
  · 有赋值 / ${NAME:-} 兜底 / 引号 heredoc 里 / for 循环变量 / 算术上下文
                                          → 不许报（误报会把真问题埋掉）
  · 全仓现状                              → FAIL 必须为 0
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ★ Windows 控制台默认 cp936(GBK)，直接 print 中文会抛 UnicodeEncodeError。
try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
LINT = ROOT / "tools" / "lint_sh.py"
passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail and not cond else ""))


def run_lint(paths):
    r = subprocess.run([sys.executable, str(LINT), *[str(p) for p in paths]],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=180, cwd=str(ROOT))
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def write(td, name, body):
    # ★ newline="\n" 是必须的：Windows 上 write_text 默认把 \n 转成 \r\n，
    #   而 bash 认不出行尾带 \r 的 then/fi/heredoc 定界符 —— 探针会以
    #   "syntax error: unexpected end of file" 假失败，把人骗去查检查器。
    #   仓库里的真脚本都是 LF（git 归一化），所以探针也必须 LF。
    p = td / name
    p.write_text(body, encoding="utf-8", newline="\n")
    return p


SHEBANG = "#!/bin/bash\n"


def main():
    print("== lint_sh：shell 的语法 + set -u 未定义变量 ==")
    if not LINT.exists():
        print("SKIP 找不到 tools/lint_sh.py（发布包形态）")
        return 77

    td = Path(tempfile.mkdtemp(prefix="lsh-"))
    try:
        # ── ① 事故复现：set -u 下引用从未定义的 $ROOT → 必须 FAIL ──────
        prov = write(td, "prov.sh", SHEBANG +
                     "set -euo pipefail\n"
                     "AGENT=${PI_AGENT_DIR:-/home/ctf}\n"
                     "echo '[provision] ok'\n"
                     "if [ -f \"$ROOT/tools/x.mjs\" ]; then\n"
                     "  node \"$ROOT/tools/x.mjs\"\n"
                     "fi\n")
        rc, out = run_lint([prov])
        check("set -u 下引用未定义的 $ROOT 判 FAIL", rc == 1 and "$ROOT" in out, f"rc={rc} {out[-200:]}")
        # 行号必须精确指向引用那一行（prov.sh 第 5 行 = 第一个 "$ROOT"）。
        check("报出行号", "L5 $ROOT" in out, out[-200:])

        # ── ② 有赋值就不再报（同一个脚本补一行即可） ─────────────────────
        fixed = write(td, "fixed.sh", SHEBANG +
                      "set -euo pipefail\n"
                      "ROOT=\"${PI_LAB_ROOT:-/opt/pi-ctf}\"\n"
                      "echo \"$ROOT/tools/x.mjs\"\n")
        rc, out = run_lint([fixed])
        check("先赋值再引用不报", rc == 0, f"rc={rc} {out[-200:]}")

        # ── ③ 显式兜底的几种写法都不许报（否则误报会把真问题埋掉） ────────
        safe = write(td, "safe.sh", SHEBANG +
                     "set -euo pipefail\n"
                     "echo \"${ROOT:-/opt/x}/a\"\n"
                     "echo \"${MAYBE:+set}\"\n"
                     "for ((i = 0; i < 3; i++)); do echo \"$i\"; done\n"
                     "read -r -d '' HDR <<'PI_X' || true\n"
                     "$SELF=\"$0\"\n"
                     "PI_X\n"
                     "cat > f <<EOF\n"
                     "escaped \\$HOME stays literal\n"
                     "EOF\n"
                     "echo \"$HDR\"\n")
        rc, out = run_lint([safe])
        check("${X:-}/${X:+}/循环变量/引号heredoc/反斜杠$ 都不报", rc == 0,
              f"rc={rc} {out[-300:]}")

        # ── ④ 未加引号的 heredoc 会展开 → 里面的 $NAME 仍要查 ────────
        unq = write(td, "unquoted.sh", SHEBANG +
                    "set -euo pipefail\n"
                    "cat > f <<EOF2\n"
                    "\"$NOT_DEFINED_ANYWHERE\"\n"
                    "EOF2\n")
        rc, out = run_lint([unq])
        check("未加引号的 heredoc 里照样查未定义变量", rc == 1 and "NOT_DEFINED_ANYWHERE" in out,
              f"rc={rc} {out[-200:]}")

        # ── ⑤ source 别的文件 → 变量可能来自那边，整份跳过 ───────────────
        src = write(td, "sourced.sh", SHEBANG +
                    "set -euo pipefail\n"
                    ". \"${LIB}/libexec/labenv.sh\"\n"
                    "echo \"$FROM_LABENV/x\"\n")
        rc, out = run_lint([src])
        check("source 了别的文件 → 跳过 unbound 检查", rc == 0, f"rc={rc} {out[-200:]}")

        # ── ⑥ bash -n 能抓到的语法错必须 FAIL ────────────────────────────
        syn = write(td, "syn.sh", SHEBANG + "if [ 1 = 1 ]; then\n  echo hi\n")
        rc, out = run_lint([syn])
        check("语法错误判 FAIL", rc == 1, f"rc={rc} {out[-200:]}")

        # ── ⑦ 全仓现状：FAIL 必须为 0（仓库自己的脚本不许带病） ────────────
        rc, out = run_lint([])
        check("全仓 shell 体检 FAIL=0", rc == 0, f"rc={rc} {out[-300:]}")
        # 两种收尾形态都要认：全绿 "RESULT: N shell scripts OK"、
        # 有问题 "RESULT: F/N scripts have problems"。
        m = re.search(r"RESULT: (\d+)(?:/(\d+))?", out)
        covered = int(m.group(2) or m.group(1)) if m else 0
        check("覆盖到 100+ 个脚本", covered > 100, f"covered={covered} {out[-160:]}")
    finally:
        shutil.rmtree(td, ignore_errors=True)

    print(f"\n{'=' * 40}\n通过 {len(passed)} / 失败 {len(failed)}")
    return len(failed)


if __name__ == "__main__":
    sys.exit(main())
