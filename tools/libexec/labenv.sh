#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# labenv.sh —— 所有 tools/bin/* 包装脚本共用的「路径自解析」库
#
# 为什么要有它：发布包会被放在任意目录（WSL /mnt/d/baoan、容器 /opt/pi-ctf、或别的路径）。
# 以前每个包装里写死 上游发行方的固定绝对路径，一换目录就「找不到解释器 / 找不到实现」，
# 而且报错看不出根因。
#
# 用法（在包装脚本里）：
#   SELF="$0"; . "$(cd "$(dirname "$0")/../libexec" && pwd)/labenv.sh"
# 之后可用变量：
#   T      <仓库根>/tools         （工具目录）
#   ROOT   <仓库根>               （含 board/ skills/ kb/）
#   PY     python 解释器（优先 venv，可用 PI_LAB_PYTHON 覆盖）
#   PY3    系统 python3（只用标准库的工具用这个）
#
# 跟软链最多 10 跳；命令不在 PATH 里也能靠 $0 找到自己。
# ─────────────────────────────────────────────────────────────────────────────

# 解析「我自己」的真实路径（$0 可能是相对名、可能是软链）
_self="$0"
case "$_self" in
  */*) : ;;
  *) _self="$(command -v -- "$_self" 2>/dev/null || echo "$_self")" ;;
esac
_i=0
while [ -L "$_self" ] && [ "$_i" -lt 10 ]; do
  _l="$(readlink "$_self")"
  case "$_l" in
    /*) _self="$_l" ;;
    *)  _self="$(dirname "$_self")/$_l" ;;
  esac
  _i=$((_i + 1))
done
T="$(cd "$(dirname "$_self")/.." && pwd)"        # tools/
ROOT="$(cd "$T/.." && pwd)"                      # 仓库根

# python：PI_LAB_PYTHON 覆盖 → venv → 系统 python3
# ★ 2026-11-06 血账：`tools/venv` 是**挂载卷**，但它的 `bin/python` 丢了
#   （pyvenv.cfg 指向构建机 `/home/breakingbad/...`，容器内不存在）。
#   于是这里退化成系统 python3 → **venv 里装好的 PIL/dncil 全用不上** →
#   `arch-diagram*.py` 与 `revkit.py` 在容器内 `ModuleNotFoundError`，
#   selfcheck 报「存在但跑不了」。这不是工具坏了，是解释器选错了。
#   修法：解释器缺失时，用系统 python3 **并把 venv 的 site-packages 加进 PYTHONPATH** ——
#   依赖还在卷里，只是找不到入口。比重建 venv 稳（重建要联网、要 10+ 分钟）。
PY="${PI_LAB_PYTHON:-}"
# ★ 2026-11-07：**平台优先**。宿主机（Windows/Git Bash）上，
#   tools/venv 是 WSL 里建的 **Linux venv**（bin/python3.12 指向
#   /home/breakingbad/...，在 Windows 上就是个死链接）。原来的顺序会先碰它、
#   碰不上再退到系统 python（anaconda 3.14，没有 pwn）→ selfcheck 报「py 缺库」。
#   现在 windows 原生 venv 放：tools/venv-win（Scripts/python.exe，Python 3.12 + pwn/z3/…），
#   在 Windows 上**优先**用它。容器/里 Linux 路径不存在，自然跳过。
_win_venv_py="$T/venv-win/Scripts/python.exe"
# ★ 2026-11-11 修：原来**无条件**优先 venv-win，只要该目录存在就用它。
#   一旦宿主目录混进容器（.dockerignore 漏排时的后果），exec 一个 Windows 路径
#   就是 "cannot execute: required file not found"，而且报错指向 kb/py 而非根因。
#   正确判据是**平台**，不是「文件在不在」：只在 MINGW/MSYS/CYGWIN 上选它。
case "$(uname -s 2>/dev/null)" in
  MINGW*|MSYS*|CYGWIN*) _win_ok=1 ;;
  *)                    _win_ok=0 ;;
esac
if [ -z "$PY" ] || [ ! -x "$PY" ]; then
  if [ "$_win_ok" = "1" ] && [ -x "$_win_venv_py" ]; then
    PY="$_win_venv_py"
  elif [ -x "$T/venv/bin/python" ]; then
    PY="$T/venv/bin/python"
  else
    PY="$(command -v python3 || command -v python || true)"
    # 退化路径：把 venv 的 site-packages 借给系统 python
    # ★ 但只在**本平台能加载**时才借（2026-11-06 踩到）：
    #   tools/venv 是 Linux 里建的，PIL 带 `_imaging.cpython-312-*.so`；
    #   Windows 加载不了 .so → 借过来就是 `ImportError: cannot import name '_imaging'`。
    #   「为了修容器，反而弄坏宿主机」——所以先看有没有本平台可加载的扩展。
    _pp_ok=0
    for _sp in "$T"/venv/lib/python*/site-packages; do
      [ -d "$_sp" ] || continue
      case "$(uname -s 2>/dev/null)" in
        MINGW*|MSYS*|CYGWIN*) [ -n "$(ls "$_sp"/*.pyd 2>/dev/null)" ] && _pp_ok=1 ;;
        *)                    [ -n "$(ls "$_sp"/*.so 2>/dev/null)"  ] && _pp_ok=1 ;;
      esac
    done
    if [ "$_pp_ok" = "1" ]; then
      for _sp in "$T"/venv/lib/python*/site-packages; do
        [ -d "$_sp" ] && PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$_sp"
      done
      if [ -n "${PYTHONPATH:-}" ]; then export PYTHONPATH; fi
      # ★ 2026-11-07：借来的 site-packages 必须回填到**解释器**身上，不能只留在
      #   PYTHONPATH 环境变量里。原因：PYTHONPATH 只活在本进程树，而调用方常按
      #   `py -c "import sys;print(sys.executable)"` 拿到解释器路径后**另起**一个
      #   subprocess（不经本包装）→ 那个进程没有 PYTHONPATH → 又是裸 python3。
      #   踩中现场：board/tests/test_replay_env.py 拿到 /usr/bin/python3 后
      #   直接跑 import pwn → 假红，而 `py -c "import pwn"` 明明绿的。
      #   写一个 stable 的 .pth（幂等）：解释器自带 site-packages 里放一行路径。
      for _sp in "$T"/venv/lib/python*/site-packages; do
        [ -d "$_sp" ] || continue
        _site="$("$PY" -c 'import sysconfig;print(sysconfig.get_paths()["purelib"])' 2>/dev/null)"
        [ -d "$_site" ] || continue
        printf '%s\n' "$_sp" > "$_site/zz-pi-lab-venv.pth" 2>/dev/null || true
      done
    fi
  fi
fi
# ★ 2026-11-19：python3 在 PATH 里但**跑不起来**也不能算数（实测：anaconda3\\python.exe
#   缺 Lib/encodings，一律 "Failed to import encodings module"）。判据是能跑，不是能找。
_pick_py3() {
  for _c in python3 python; do
    _p="$(command -v "$_c" 2>/dev/null)" || continue
    [ -n "$_p" ] || continue
    if "$_p" -c 'pass' >/dev/null 2>&1; then printf '%s' "$_p"; return 0; fi
  done
  return 1
}
PY3="$(_pick_py3 || true)"
# PY 同样要事后校验：上面按路径挑中的解释器可能已经跑不起来。
if [ -n "$PY" ] && ! "$PY" -c 'pass' >/dev/null 2>&1; then PY=""; fi
if [ -z "$PY" ]; then
  if [ "$_win_ok" = "1" ] && [ -x "$_win_venv_py" ] && "$_win_venv_py" -c 'pass' >/dev/null 2>&1; then
    PY="$_win_venv_py"
  else
    PY="${PY3:-}"
  fi
fi
# 只用标准库的工具（tsec/kb/…）也走同一个可用解释器。
if [ -z "$PY3" ] && [ -n "$PY" ]; then PY3="$PY"; fi

# ★ 2026-11-17：Python 在本机默认按 Windows 控制台代码页（cp936）编码 stdout —— 任何 CLI
#   打印中文/「⚠」都可能在客户机上抛 UnicodeEncodeError，或把 GBK 字节写进管道让调用方
#   解码成乱码（实测：llmrec 崩、lint_zero 的汇总行在 selfcheck 里是乱码）。
#   本仓的约定是每个 CLI 自己 reconfigure（见 tools/apiharvest.py），但那是**每写一个新
#   工具就得记得一次**的约定 —— 已经漂了 17 个。这里再兜一层：给整棵进程树钉死 UTF-8。
#   用 ${VAR:-} 写法，调用方显式设了就不覆盖（测试要用别的编码时仍可覆盖）。
export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"

lab_die() { printf "%s: %s\n" "$(basename "$_self")" "$*" >&2; exit 127; }

# ── Windows 侧原生工具（gdb / nc / socat）──────────────────────────────────
# ★ 2026-11-06：宿主机（Windows）缺 gdb/nc/socat，selfcheck 报「pwn 缺必需」。
#   winget 装 WinLibs 走 GitHub 直连会 `InternetOpenUrl() failed 0x80072efd`
#   （本仓已知：GitHub 直连不通），所以改为经 ghproxy 下载后落在 tools/win/
#   （该目录已进 .gitignore：不进 git、不进镜像）。
#   这里把它们加进 PATH，让 `command -v gdb/nc/socat` 能认出来。
#   ★ 2026-11-12 修（实测踩到）：原来只判「目录在不在」就**无条件前置** $T/win，
#     于是容器里（Linux 自带 /usr/bin/gdb）也被 Windows 版盖掉 →
#     `pggdb` 报 `tools/win/mingw64/bin/gdb.exe: cannot execute: required file not found`。
#     判据应该是**平台**，不是目录存在：只在 MINGW/MSYS/CYGWIN（宿主 Git-Bash）上加。
#     这和上面 venv-win 的判据是同一个道理，别再写反了。
case "$(uname -s 2>/dev/null)" in
MINGW*|MSYS*|CYGWIN*)
  if [ -d "$T/win" ]; then
    case ":$PATH:" in
      *":$T/win:"*) : ;;
      *) PATH="$T/win:$PATH" ;;
    esac
    export PATH
  fi
  ;;
esac
# ★★ 2026-11-13 修：venv/bin 从没进过 PATH —— 技能里写的命令照抄就失败
#
# 实测：venv 里有 80 个可直接用的入口（checksec / ROPgadget / ropper / z3 /
# RsaCtfTool / pwn / cyclic / shellcraft / asm / disasm / hex / unhex / elfpatch /
# factordb / volshell / readelf.py / sage-* …），但 tools/bin/ 里没有任何一个包装，
# 本文件此前也没把它加进 PATH。
#
# 后果（真踩到）：skills/ 里 checksec 出现 22 处、z3 78 处、one_gadget 20 处、
# ROPgadget 12 处 —— agent 照技能里的字面命令敲，一律 command not found。
# 这属于「文档与装备对不上」，比缺工具更坑：它让人以为工具坏了。
#
# 只加 venv/bin 不加 venv-win/Scripts：后者只在 Git-Bash 下有意义，
# 容器里该拿的是镜像 venv（见上面 tools/venv 的具名卷说明）。
# ★★ 2026-11-17 修（实测踩到）：上面这段只判「目录在不在」就无条件前置 $T/venv/bin ——
#   可它是 **WSL 里建的 Linux venv**，Windows 侧 bin/python3 是断掉的符号链接。
#   前置之后 `python3` 先命中那个断链 → exec 失败 rc=126。
#   实测血账：selfcheck 全量模式第 4 段把 120 个测试全判失败（rc=126、log 0 字节），
#   而同样的测试在没 source 本文件的手工 shell 里全绿 —— 追了两轮才找到这里。
#   判据必须是**能不能跑**，不是目录存在与否（和上面 venv-win / tools/win 两处同源教训）。
#   容器里 venv/bin/python 可跑 → 行为不变；Windows 侧跳过，改前置 venv-win/Scripts。
if [ -x "$T/venv/bin/python" ] && "$T/venv/bin/python" -c 'pass' >/dev/null 2>&1; then
  case ":$PATH:" in
    *":$T/venv/bin:"*) : ;;
    *) PATH="$T/venv/bin:$PATH"; export PATH ;;
  esac
elif [ -x "$T/venv-win/Scripts" ] || [ -x "$T/venv-win/Scripts/python.exe" ]; then
  # Windows 原生 venv（Scripts/python.exe，Python 3.12 + pwn/z3/ROPgadget/asm 等）。
  # 只在宿主机有意义；容器里没有这个目录，自然走上面那条。
  case ":$PATH:" in
    *":$T/venv-win/Scripts:"*) : ;;
    *) PATH="$T/venv-win/Scripts:$PATH"; export PATH ;;
  esac
fi

