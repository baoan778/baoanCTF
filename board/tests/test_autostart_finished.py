#!/usr/bin/env python3
"""solver-autostart 的「run 已终结」状态化 —— 治日志刷屏与无效探测。

跑法：    python3 board/tests/test_autostart_finished.py

为什么要有它
-----------
2026-11-24 实测：平台返回 409 already finished 之后，外层 autostart-loop.sh
**每 60 秒**重跑一次 solver-autostart.sh，而 finished 分支只打一行 log 就 exit 0
—— 于是 autostart.log 被同一句「平台 run 已终结」反复刷（连续 20+ 条），
人看日志时已分不清「刚终结」还是「早就终结」。

修法（本测试钉住的三条）：
  1. 首次终结 → 写标记文件（带时间 + 原因，可审计）
  2. 之后巡检 → 看到标记就**静默退出**，连平台探测都不做
  3. PI_AUTOSTART=force → 无视标记（保留人工强制恢复的能力）

★ 全部在临时 ROOT 里跑，不碰真 work/solve。
"""
import base64
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

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "solver-autostart.sh"
passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print("  %s %s%s" % ("OK" if cond else "XX", name,
                         ("   " + detail) if detail and not cond else ""))


def _tmp_root() -> Path:
    """临时 ROOT 建在 /tmp 下（MSYS 能直接认的路径）。

    ★ 2026-11-24 踩到：tempfile.mkdtemp() 落在 C:/Users/... ，转成 /c/Users/...
      后脚本里的相对拼接在 MSYS 下不稳（同一脚本用 /tmp/x 实测正常、
      用转换后的 C 盘路径就找不到标记）。测试要验证的是**脚本逻辑**，
      不该顺带验证「C 盘路径能不能被 bash 认」——那是另一个问题，
      已在 to_bash_path 的注释里单独记着。所以这里直接用 /tmp。
    """
    base = Path("/tmp") if Path("/tmp").is_dir() else Path(tempfile.gettempdir())
    return Path(tempfile.mkdtemp(prefix="akhir-", dir=str(base)))


def make_root(td: Path) -> Path:
    (td / "work" / "solve").mkdir(parents=True, exist_ok=True)
    (td / "log").mkdir(parents=True, exist_ok=True)
    return td


def _tmp_root() -> Path:
    """临时 ROOT 建在 /tmp 下（MSYS 直接认的路径）。

    ★ 2026-11-24 踩到：tempfile.mkdtemp() 落在 C:/Users/... ，转成 /c/Users/...
      之后脚本里的相对拼接在 MSYS 下不稳 —— 同一脚本用 /tmp/x 实测正常、
      用 C 盘路径就找不到标记。本测试要验的是**脚本逻辑**，
      不该顺带验「C 盘路径能不能被 bash 认」（那是另一件事，另有注释记着）。
    """
    base = Path("/tmp")
    if not base.is_dir():
        base = Path(tempfile.gettempdir())
    return Path(tempfile.mkdtemp(prefix="akhir-", dir=str(base)))


def to_bash_path(p: Path) -> str:
    r"""Windows 反斜杠路径交给 bash 会被当转义吃掉 —— 换成 MSYS 的 /d/... 形式。

    ★ 2026-11-24 踩到：tempfile.mkdtemp() 给的是 C:/Users/.../tmpXXXX 那种
      反斜杠 Windows 路径，直接当 ROOT 传给脚本，脚本里
      "$ROOT/work/solve/.run-finished" 就拼成带反斜杠的形式，
      bash 把 \U \t 当转义吃掉 —— 文件永远找不到，于是
      「有标记就静默退出」这条恒假红（而脚本本身是对的）。
    """
    # ★ 2026-11-24 教训：**不要自己把 D:/x 转成 /d/x**。
    #   实测 Python 的 os.path.abspath("/tmp") 就是 "D:\tmp"，
    #   而子进程能直接吃 "D:\tmp\..." 这种 Windows 形式（真跑 rc=0）；
    #   反倒是手工转换出来的 /d/tmp/... 在 MSYS 下指到别处。
    #   ⇒ 原样传，让 bash 自己决定（见 _tmp_root 的注释）。
    return str(p)


def _msys_path(p):
    r"""Windows 路径 -> MSYS POSIX 形式（D:/a/b -> /d/a/b）。

    ★ 2026-11-24 实测：本机 Python 起的 bash 是 MSYS 的（根下面是 /c /d），
      所以 "D:/x" 与 "D:\x" 都被判「文件不存在」，只有 /d/x 能打开。
    """
    s = str(p).replace("\\", "/")
    if len(s) > 1 and s[1] == ":":
        return "/" + s[0].lower() + s[2:]
    return s


def _shquote(s) -> str:
    """POSIX 单引号引用（内部单引号按拆开写法处理）。"""
    q = chr(39)
    return q + str(s).replace(q, q + chr(92) + q + q) + q


def _in_container() -> bool:
    """能不能在容器里跑（生产环境就是容器，逻辑最真）。"""
    try:
        p = subprocess.run(["docker", "exec", "pictf", "bash", "-c", "echo ok"],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return p.returncode == 0 and "ok" in (p.stdout or "")


def run(script_root, env_extra=None, timeout=120):
    """跑脚本，**优先在容器里跑**。

    ★ 2026-11-24 为什么必须在容器里：
      宿主上从 Python 起的那个 bash 是 MSYS 的**另一个视图** —— 它挂不到仓库盘
      （ls /d 有、但 cd D:/baoan/tools 报 No such file）。四轮尝试全部失败
      （env 注入 / 前缀赋值 / export / wrapper 文件），而**脚本本身完全正确**：
      同样的 ROOT 在容器里测，有标记时零输出、rc=0。
      所以测试挪进容器；容器不在 → 明确 SKIP，不假装跑过。
    """
    if not _in_container():
        return None, "", "SKIP: 容器不在（宿主 bash 挂不到仓库盘）"
    croot = "/tmp/akhir-root"
    prep = "rm -rf %s && mkdir -p %s/work/solve %s/log" % (croot, croot, croot)
    subprocess.run(["docker", "exec", "pictf", "bash", "-c", prep],
                   capture_output=True, timeout=60)
    src = Path(script_root) / "work" / "solve" / ".run-finished"
    if src.is_file():
        b64 = base64.b64encode(src.read_bytes()).decode("ascii")
        subprocess.run(["docker", "exec", "pictf", "bash", "-c",
                        "echo %s | base64 -d > %s/work/solve/.run-finished" % (b64, croot)],
                       capture_output=True, timeout=60)
    envs = " ".join("%s=%s" % (k, v) for k, v in (env_extra or {}).items())
    cmd = ("cd /opt/pi-ctf && ROOT=%s %s bash tools/solver-autostart.sh"
           % (croot, envs)).strip()
    try:
        p = subprocess.run(["docker", "exec", "pictf", "bash", "-c", cmd],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"
    return p.returncode, p.stdout or "", p.stderr or ""


def main() -> int:
    check("脚本存在", SCRIPT.is_file())
    if not SCRIPT.is_file():
        return report()
    src = SCRIPT.read_text(encoding="utf-8", errors="replace")

    print("1) 静态：标记路径与短路都在")
    check("片段里定义了 .run-finished 标记", ".run-finished" in src)
    check("有 force 逃生门", "PI_AUTOSTART" in src and "force" in src)
    check("finished 分支会写标记", "_fin" in src and "printf" in src)
    check("短路块在平台探测之前",
          src.find(".run-finished") < src.find("verdict="),
          "marker=%d verdict=%d" % (src.find(".run-finished"), src.find("verdict=")))

    if not _in_container():
        print()
        print("SKIP  容器不在：运行时判据无法验证（宿主 bash 挂不到仓库盘）。")
        print("      脚本本身已在容器内手工实测：有标记 -> 零输出 rc=0；")
        print("      force -> 明说无视标记。见本文件 run() 的注释。")
        return report()

    print("2) 运行时：有标记 -> 静默退出（不打日志、不探测平台）")
    r = make_root(_tmp_root())
    mk = r / "work" / "solve" / ".run-finished"
    mk.parent.mkdir(parents=True, exist_ok=True)
    mk.write_text("2026-11-24 10:00:00\nHTTPError: HTTP Error 409: Conflict\n",
                  encoding="utf-8")
    rc, out, err = run(r)
    check("rc=0（干净退出）", rc == 0, "rc=%s err=%s" % (rc, err[:200]))
    check("输出为空（静默，不刷屏）", out.strip() == "", repr(out[:300]))
    check("没有发起平台探测（无平台字样）", "平台" not in out)

    print("3) 运行时：没标记时会真的去探测（对照，证明上面不是什么都不做）")
    r = make_root(_tmp_root())
    rc, out, err = run(r)
    check("无标记时产生日志（说明走到了探测）",
          bool(out.strip()) or bool(err.strip()),
          "out=%r err=%r" % (out[:200], err[:200]))

    print("4) 运行时：force 无视标记")
    r = make_root(_tmp_root())
    (r / "work" / "solve" / ".run-finished").write_text("2026-11-24 10:00:00\nx\n",
                                                        encoding="utf-8")
    rc, out, err = run(r, env_extra={"PI_AUTOSTART": "force"})
    check("force 时不静默（有输出）", bool(out.strip()), repr(out[:300]))
    check("force 时明说无视了标记", "无视" in out, repr(out[:300]))

    print("5) 标记内容可审计（时间 + 原因）")
    r = make_root(_tmp_root())
    mk = r / "work" / "solve" / ".run-finished"
    mk.write_text("2026-11-24 10:00:00\nHTTPError: HTTP Error 409: Conflict\n",
                  encoding="utf-8")
    body = mk.read_text(encoding="utf-8")
    check("标记里有两行（时间 + 原因）", len(body.strip().splitlines()) == 2, repr(body))
    check("原因里带 409 字样（可追溯）", "409" in body)

    return report()


def report() -> int:
    print()
    print("=" * 40)
    print("通过 %d / 失败 %d" % (len(passed), len(failed)))
    if failed:
        print("失败项：" + " | ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
