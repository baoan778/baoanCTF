#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# sage 命名空间补全（passagemath 专用）
#
# 为什么需要：passagemath 是 SageMath 的「模块化发行版」，每个轮子只装自己那部分，
#   没有上游单体包的 sage/all.py 与 sage/all_cmdline.py。缺了这两个文件：
#     · `from sage.all import *`  → ModuleNotFoundError: No module named 'sage.all'
#     · `sage script.sage`         → sage-eval 内部 import sage.all 直接崩
#   而 sage 的全部日常用法（factor / discrete_log / GF / EllipticCurve / small_roots）
#   都在 sage.all 里。
#
# 做什么：扫 site-packages 里现成的 sage/all__sagemath_*.py（passagemath 每个模块
#   自带一个），拼成 all.py（= 全部命名空间），再让 all_cmdline.py 复用它。
#   纯幂等：已存在且内容一致就跳过。
#
# 用法：bash docker/sage-shim.sh [venv路径]     默认 /opt/pi-ctf/tools/venv
# ─────────────────────────────────────────────────────────────────────────────
set -eu

VENV="${1:-/opt/pi-ctf/tools/venv}"
PY="$VENV/bin/python"
[ -x "$PY" ] || { echo "✘ 找不到 $PY" >&2; exit 1; }

"$PY" - <<'PY'
import glob, os, sys, sysconfig

sp = sysconfig.get_paths()["purelib"]
sage_dir = os.path.join(sp, "sage")
mods = sorted(
    os.path.basename(p)[:-3]
    for p in glob.glob(os.path.join(sage_dir, "all__sagemath_*.py"))
)
if not mods:
    print("✘ site-packages 里没有 sage/all__sagemath_*.py —— passagemath 没装上？", file=sys.stderr)
    sys.exit(1)

body = (
    "# 由 docker/sage-shim.sh 生成，勿手改（重新生成即可）\n"
    "# passagemath 的模块化命名空间合并成上游 sage.all 的等价物\n"
    + "\n".join("from sage.%s import *" % m for m in mods)
    + "\n"
)
all_py = os.path.join(sage_dir, "all.py")
cmd_py = os.path.join(sage_dir, "all_cmdline.py")
cmd_body = body + "from sage.all import *\n"

def write_if_changed(path, content):
    old = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            old = fh.read()
    if old == content:
        return False
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return True

c1 = write_if_changed(all_py, body)
c2 = write_if_changed(cmd_py, cmd_body)
print("sage 命名空间：%d 个模块 → %s%s（all.py %s, all_cmdline.py %s）" % (
    len(mods), sage_dir,
    "" if (c1 or c2) else " [已是最新]",
    "写入" if c1 else "未变",
    "写入" if c2 else "未变",
))
PY

# 冒烟：真跑一次，别只信文件存在
"$PY" - <<'PY'
from sage.all import *
checks = [
    ("factor", lambda: factor(2**64 - 1)),
    ("discrete_log", lambda: discrete_log(Mod(2, 101), Mod(8, 101))),
    ("EllipticCurve", lambda: EllipticCurve(GF(17), [2, 3]).order()),
    ("mod.sqrt", lambda: mod(4, 7).sqrt()),
    ("matrix.LLL", lambda: matrix(ZZ, [[1, 0], [0, 1]]).LLL()),
]
bad = []
for name, fn in checks:
    try:
        fn()
    except Exception as e:
        bad.append("%s: %s" % (name, e))
if bad:
    print("✘ sage 冒烟失败：" + "; ".join(bad), file=sys.stderr) if False else None
    import sys as _s
    print("✘ sage 冒烟失败：" + "; ".join(bad), file=_s.stderr)
    _s.exit(1)
print("✓ sage 冒烟通过（%d 项）" % len(checks))
PY
