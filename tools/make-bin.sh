#!/bin/bash
# 生成 tools/bin/ —— 把各工具统一挂成可直接调用的命令
# 重新生成：bash <仓库根>/tools/make-bin.sh
set -euo pipefail

T="$(cd "$(dirname "$0")" && pwd)"     # <仓库根>/tools
B="$T/bin"
PY="$T/venv/bin/python"

# ★ 会被**原样**写进每个生成的包装（见下方 heredoc 里的 $HDR）—— must 用引号包住：
#     T="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
#   所以这里整段用单引号，避免生成器自己先展开一遍。
#   为什么：包装装到买家机器上时，绝对路径必须由**运行时**算出来。
# ★ 2026-11-07：原来这里硬算 PY="$T/venv/bin/python" —— 容器里那是个**悬空软链**
#   （指向构建机 /home/breakingbad/...），于是每个生成的入口都 `No such file or directory`，
#   而 hand/py 却能跑（它 source labenv.sh）。现在统一走 labenv.sh：悬空时自动退化到
#   系统 python3 并借 venv 的 site-packages，与 py 命令行为一致。
read -r -d '' HDR <<'PI_BIN_SELF_LOCATE' || true
SELF="$0"; . "$(cd "$(dirname "$0")/../libexec" && pwd)/labenv.sh"
PI_BIN_SELF_LOCATE

# ⚠️ 2026-09-30 事故：以前这里直接 `rm -rf`，而 bin/ 里除了本脚本生成的入口，
# 还有 9 个人手写的（py/fgs/selfcheck/sesscan/quiet/sweep/handover/winrm/opencode-token）——
# 一跑就把它们全删了（靠 `git checkout -- tools/bin` 才救回来）。
# 现在人手写的都挪到 tools/hand/，这里是它的**唯一来源**，bin/ 用软链挂出去，
# 末尾还有一道断言护栏：少了任何一个就报错退出。
# ★ 2026-10-11 拆雷：重建前先把现有入口**整体暂存**。
#   血账：本脚本的生成列表（hand/self_tools/py_tools）**会漏**新加的工具
#   （lane_gate / pcapmap / pi-bench / skilllint / libcsearch / pggdb / rsactf /
#     mswitch / nccsec-token / jsteg / outguess / bkcrack …），
#   而下面这行 `rm -rf` 会把它们**静默删掉** —— 这只是「半套环境」，不报错。
#   现在：先 stash，重建后把**本脚本没生成过**的入口原样放回；末尾再断言一次。
PI_BIN_STASH="$(mktemp -d)"
if [ -d "$B" ]; then cp -a "$B"/. "$PI_BIN_STASH"/ 2>/dev/null || true; fi
rm -rf "$B"; mkdir -p "$B"

# ── 1. gobin/*.exe → 软链（WSL interop 直接可执行）────────────────
for f in "$T"/gobin/*.exe; do
  [ -e "$f" ] || continue
  n="$(basename "$f" .exe)"
  ln -sf "../gobin/$(basename "$f")" "$B/$n"
done

# ── 2. ctfpoc/poc.py → poc ────────────────────────────────────────
cat > "$B/poc" <<EOF
#!/bin/bash
$HDR
exec "\$PY" "\$T/ctfpoc/poc.py" "\$@"
EOF

# ── 3. vendor/* Python 工具 → 包装脚本（cwd 必须落在工具目录）────
# 格式: 命令名|工具目录|入口文件[|额外 PYTHONPATH 目录]
#   第 4 段给「入口还需要 repo 内 shim 包」的工具用（★ 2026-11-18：jwt_tool 依赖
#   tools/vendor/{ratelimit,Cryptodome,termcolor} 三个自研 shim —— 容器里装不上的
#   那套）。这个补丁原先只存在于 tools/bin/jwt_tool，一跑 make-bin 就被生成器覆盖，
#   属于「人肉修好了、生成器不知道」的典型；现在写进生成器本身）。
py_tools=(
  "cmseek|CMSeeK|cmseek.py"
  "dumpall|Dumpall|dumpall.py"
  "githack|GitHack|GitHack.py"
  "jsfinder|JSFinder|JSFinder.py"
  "pocbomber|POC-bomber|pocbomber.py"
  "ssrfmap|SSRFmap|ssrfmap.py"
  "svnexploit|SvnExploit|SvnExploit.py"
  "xsstrike|XSStrike|xsstrike.py"
  "dirsearch|dirsearch-0.4.3|dirsearch.py"
  "graphw00f|graphw00f|main.py"
  "hash-identifier|hash-identifier|hash-identifier.py"
  "jwt_tool|jwt_tool|jwt_tool.py|\$T/vendor"
  "pyinstxtractor|pyinstxtractor|pyinstxtractor.py"
  "rsatool|rsatool|rsatool.py"
  "tplmap|tplmap|tplmap.py"
)
# nosqlmap 是 Python2 源码（print 语句），本机只有 py3 —— 不挂命令，需要时用容器跑
# see: vendor/NoSQLMap/*.py 语法为 py2
py_tools+=(
  "wafw00f|wafw00f|wafw00f/bin/wafw00f"
)
for row in "${py_tools[@]}"; do
  IFS='|' read -r name dir entry extra <<<"$row"
  [ -f "$T/vendor/$dir/$entry" ] || { echo "  ⚠️  跳过 $name（缺 $dir/$entry）"; continue; }
  # extra 里写的是**运行时**才展开的字面量（如 \$T/vendor），由包装脚本自己展开
  _extra=""
  [ -n "${extra:-}" ] && _extra=":$extra"
  cat > "$B/$name" <<EOF
#!/bin/bash
$HDR
cd "\$T/vendor/$dir" || exit 1
export PYTHONPATH="\$T/vendor/$dir$_extra:\${PYTHONPATH:-}"
exec "\$PY" "\$T/vendor/$dir/$entry" "\$@"
EOF
done

# ── 4. 非 Python 入口 ─────────────────────────────────────────────
# XXEinjector (ruby)
cat > "$B/xxeinjector" <<EOF
#!/bin/bash
$HDR
cd "\$T/vendor/XXEinjector" || exit 1
exec ruby XXEinjector.rb "\$@"
EOF
# dvcs-ripper (perl)
for s in git svn hg bzr cvs; do
  cat > "$B/rip-$s" <<EOF
#!/bin/bash
$HDR
cd "\$T/vendor/dvcs-ripper" || exit 1
exec perl rip-$s.pl "\$@"
EOF
done

# ── 5. Windows 侧工具（走 WSL interop）───────────────────────────
# IDA Pro 8.3 / Ghidra 11.4 只有 Windows 版，装在 D:\AI\... ；
# WSL interop 能直接执行 .exe（gobin 那批就是这么挂的）。
# winbridge.py 负责找路径 + wslpath 转换 + 文本模式调用（不弹 GUI）。
for n in ida ghidra; do
  cat > "$B/$n" <<EOF
#!/bin/bash
$HDR
exec "\$PY" "\$T/winbridge.py" $n "\$@"
EOF
done

# ── 5b. 自研工具（tools/*.py / tools/flagwatch.mjs）→ 包装脚本 ──────
# ⚠️ 2026-10-10 事故：这批入口以前是**手工**放进 bin/ 的，本脚本第四版的 rm -rf
# 一次就吃掉 20 个命令（probe/ctfsurf/flagwatch/winbridge…），靠 'git checkout -- tools/bin' 才救回。教训：只要 bin/ 里还有“本脚本不知道的入口”，重建就是定时炸弹。
# 所以现在把它们全部**纳入生成**，并进末尾护栏（少一个就 exit 1）。
self_tools=(
  "arch-diagram|arch-diagram.py"
  "arch-diagram-simple|arch-diagram-simple.py"
  "authzdiff|authzdiff.py"
  "bench_local|bench_local.py"
  "browser|browser.py"
  "costguard|costguard.py"
  "ctfsurf|ctfsurf.py"
  "doccmd_check|doccmd_check.py"
  "edu_index|edu_index.py"
  "eduassets|eduassets.py"
  "gen-agent-bridge|gen-agent-bridge.py"
  "handoff-card|handoff_card.py"
  "bench_metrics|bench_metrics.py"
  "watchdog|watchdog.py"
  "imgintel|imgintel.py"
  "intake|intake.py"
  "kb|kb.py"
  "lint_sh|lint_sh.py"
  "lane_gate|lane_gate.py"
  "lane_guard|lane_guard.py"
  "md2docx|md2docx.py"
  "osint_probe|osint_probe.py"
  "pocrun|pocrun.py"
  "probe|probe.py"
  "pyenv-check|pyenv_check.py"
  "pyenv_check|pyenv_check.py"
  "revkit|revkit.py"
  "toolreg|toolreg.py"
  "tsecbench|tsecbench.py"
  "winbridge|winbridge.py"
  "wsbridge|wsbridge.py"
  "wpcollect|wpcollect.py"
  "wpfetch|wpfetch.py"
  "quiet|quiet.py"
  "mswitch|mswitch.py"
  "kimi-token|kimi_token.py"
  "nccsec-token|nccsec_token.py"
  "opencode-token|opencode_token.py"
  "thinkcheck|thinkcheck.py"
  "wxapkg|wxapkg.py"
)
for row in "${self_tools[@]}"; do
  IFS='|' read -r name entry <<<"$row"
  [ -f "$T/$entry" ] || { echo "✗ 自研工具 $name 的入口不见了：$T/$entry"; exit 1; }
  cat > "$B/$name" <<EOF
#!/bin/bash
$HDR
exec "\$PY" "\$T/$entry" "\$@"
EOF
done
# flagwatch 是 node 的 .mjs（零依赖）—— 用系统 node，不占 venv
cat > "$B/flagwatch" <<EOF
#!/bin/bash
$HDR
exec node "\$T/flagwatch.mjs" "\$@"
EOF

# ── 6. 人手写的入口：tools/hand/ → bin/ ──────────────────────────
# 为什么要分开：这一批不是"生成物"，内容是人维护的；放进 bin/ 会被上面的 rm -rf 吃掉。
# 软链还是拷贝由 git core.symlinks 决定（见下面的分流注释）：只留 hand/ 一份正本。
# ★ 2026-11-27：清单改为**从目录自动发现**，不再手写。
#   实测缺陷：硬编码的清单漏了 pggdb 与 rsactf，跑一次本脚本就把这两个入口
#   静默删掉（重建后 112 → 110），而末尾护栏只校验清单里的名字，看不见漏的那些。
#   手写清单 + 只校验自身 = 永远发现不了自己的遗漏；改为扫目录，缺谁都不可能。
HAND_TOOLS="$(cd "$T/hand" && ls -1 | grep -vx '.hand-tools' | LC_ALL=C sort | tr '\n' ' ')"
# ★ 2026-11-18：本机 git core.symlinks=false —— MSYS 的 `ln -s` **确实**能建真软链，
#   但 git 把它们存成 120000 对象，这种仓库在没开开发者模式的 Windows 上 checkout 出来
#   是一堆「内容是 ../hand/xxx 的普通文件」→ 入口全废，且极难归因。
#   所以按 git 配置分流：能信软链就软链（Linux/容器，改 hand 立即生效），
#   不能信就落成**副本**（Windows，改完必须重跑本脚本 —— 由 selfcheck §5 的
#   byte-equal 检查盯着漂移）。两条路都只留 hand/ 一个正本。
# ★ 2026-11-24：把这份清单**落盘**成 tools/hand/.hand-tools（进版本控制）。
#   为什么：selfcheck 要做「bin 与 hand 正本一致」的检查，也需要同一份清单，
#   而它原本**各自硬编码一份 HAND_TOOLS** —— 三份副本，改了这里忘了那里就静默漂
#   （历史真发生过：加了工具但 selfcheck 不查它，byte-equal 检查形同虚设）。
#   现在只留一个来源：tools/hand/ 目录本身；本文件是它落盘后的快照，
#   selfcheck 读这个快照，不自己维护列表。
{
  printf '# 人手写入口清单 —— 由 tools/make-bin.sh 扫 tools/hand/ 生成，别手改。\n'
  printf '# 唯一来源：tools/hand/ 目录里有哪些文件。\n'
  for n in $HAND_TOOLS; do printf '%s\n' "$n"; done
} > "$T/hand/.hand-tools"

GIT_SYMLINKS="$(git -C "$T/.." config --get core.symlinks 2>/dev/null || true)"
for n in $HAND_TOOLS; do
  [ -f "$T/hand/$n" ] || { echo "✗ tools/hand/$n 不见了（人手写的入口，去 git 里找）"; exit 1; }
  if [ "$GIT_SYMLINKS" = "false" ]; then cp -f "$T/hand/$n" "$B/$n"; else ln -sf "../hand/$n" "$B/$n"; fi
done

chmod +x "$B"/* "$T"/hand/* 2>/dev/null || true

# 护栏：人写的入口必须都在（少了就报错，绝不再静默出半套环境）
missing=""
for n in $HAND_TOOLS; do [ -e "$B/$n" ] || missing="$missing $n"; done
# ★ 2026-11-24：这里原本把上面三行**又写了一遍**（复制粘贴残留）。删掉重复份。
#   missing 累加是幂等的（同一个名字最多进两次，判断只看非空），
#   所以重复不影响正确性 —— 但它让人以为有两道检查，看不出真结构。
for row in "${self_tools[@]}"; do
  IFS='|' read -r n _ <<<"$row"
  [ -e "$B/$n" ] || missing="$missing $n"
done
[ -e "$B/flagwatch" ] || missing="$missing flagwatch"
if [ -n "$missing" ]; then
  echo "✗ 生成后缺少人手写的入口：$missing —— 别用这个 bin/，先修 make-bin.sh"
  exit 1
fi

# ★ 把「本脚本不认识、但重建前存在」的入口原样放回（拆雷，见上面的注释）
RESTORED=""
if [ -d "$PI_BIN_STASH" ]; then
  for f in "$PI_BIN_STASH"/* "$PI_BIN_STASH"/.[!.]*; do
    # ★ 用 -L || -e：软链的**目标**可能暂时不存在（gobin 里的二进制），
    #   但链接本身要保住。只用 [ -e ] 会把它们当空气跳过 —— 那就是静默半套环境。
    { [ -L "$f" ] || [ -e "$f" ]; } || continue
    n="$(basename "$f")"
    if [ ! -e "$B/$n" ]; then cp -a "$f" "$B/$n"; RESTORED="$RESTORED $n"; fi
  done
  rm -rf "$PI_BIN_STASH"
fi
[ -n "$RESTORED" ] && echo "↩︎ 放回本脚本未生成的入口：$RESTORED"
# ★ 末尾断言（原封神的一行）：2026-10-12 修 —— 原行是 `if [ -n "$PI_BIN_LOST" ]; then :; fi`，
#   而 PI_BIN_LOST **全脚本从未定义**：在 `set -u` 下脚本必死在最后一行，
#   于是「✅ 生成」与清单**从来没打印过**、退出码也是 1（bin/ 已经重建好了，所以之前没人发现）。
#   现在换成真断言：数量 + 关键入口，缺了就报红退出。
BIN_N=$(ls "$B" | wc -l)
if [ "$BIN_N" -lt 60 ]; then
  echo "✗ bin/ 只剩 $BIN_N 个入口 —— 重建像半套环境，别用！" >&2
  echo "  抢救：git checkout -- tools/bin，再查上面哪一步跳过了" >&2
  exit 1
fi
for n in py fgs selfcheck sesscan revkit pwnkit; do
  [ -e "$B/$n" ] || { echo "✗ 关键入口 $n 不在 bin/ —— 重建不完整" >&2; exit 1; }
done
echo "✅ 生成 $B/ —— $(ls "$B" | wc -l) 个命令"
ls "$B" | pr -6 -t -w 100
