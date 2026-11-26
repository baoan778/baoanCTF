#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# pi 界面文案精简（幂等补丁）
#
# 为什么需要：pi 全屏模式（alt-screen）向上滚动后，底部那条「回到最新消息」
#   胶囊写死了英文长文案：
#       " ↓ Jump to latest message · Ctrl+End "
#   中文终端里又长又占宽度 —— 而 pi **没有配置项**能改它，扩展 API 也只管
#   状态栏/widget，管不到这条 scrollToEndIndicator。
#   本脚本把这段文案换成短标签（默认 "↓ 回到最新"），快捷键那半截仍由 pi
#   自己按 keybinding 渲染（Ctrl+End 照旧显示）。
#
# ★ 会被 npm 升级冲掉：`npm i -g @earendil-works/pi-coding-agent` 之后重跑。
#   selfcheck 里挂了闸门 —— 被冲掉会告警，不会静默失效。
#
# 定位方式（不靠英文原文，apply / revert 共用一套）：
#   扫 pi 的 dist，找 `${shortcut` 前紧跟着 `↓ ` 的那段 label 文案 ——
#   打包版里 ↓ 被转义成 \u2193，未打包版是真字符，两种写法都认。
#   （第一版按英文原文找文件 → 打完补丁就再也搜不到自己改过的 bundle，
#     --revert 会漏掉真正在跑的那份；这里改成按锚点选文件）
#
# 安全网（三层）：
#   ① 改前留一份原件备份 <file>.pi-ui-orig（--revert 优先用它还原）
#   ② 改完跑 node --check，语法不过立刻回滚（宁可长文案，也不能让 pi 起不来）
#   ③ 清 Node 编译缓存，避免「改了没生效」这种最难查的情况
#
# 用法:
#   bash tools/pi-ui-patch.sh                    # 打补丁（已打过 → 跳过）
#   bash tools/pi-ui-patch.sh --check            # 只检查不写盘（0=已生效）
#   bash tools/pi-ui-patch.sh --label "↓ 最新"    # 换文案
#   bash tools/pi-ui-patch.sh --revert           # 还原成上游英文
#
# 生效方式：**新开一个 pi** —— 界面代码在进程启动时加载（改完当前会话看不到）
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

MODE=apply
LABEL="回到最新"
PI_ROOT_ARG=""

while [ $# -gt 0 ]; do
  case "$1" in
    --check)   MODE=check ;;
    --revert)  MODE=revert ;;
    --root)    shift; PI_ROOT_ARG="${1:-}" ;;
    --root=*)  PI_ROOT_ARG="${1#*=}" ;;
    --label)   shift; LABEL="${1:-}" ;;
    --label=*) LABEL="${1#*=}" ;;
    -h|--help) sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数: $1（试 --help）" >&2; exit 1 ;;
  esac
  shift
done

[ -n "$LABEL" ] || { echo "✘ --label 不能为空" >&2; exit 1; }

# ── 找 pi 装在哪（跟着 PATH 里的 pi 走，不写死路径）────────────────────────────
# ★ 2026-11-03：支持 `--root` 显式指定包根。为什么需要它：
#   1.4.0 是 bun 独立二进制（`pi.exe`，没有 dist/），脚本按 `readlink -f` 向上两级
#   猜包根必然失败；1.5.0 换成 npm 布局后，PATH 里的 `pi` 又是个 **shell 转发脚本**
#   （`exec node .../dist/bundle/cli.js`），readlink 指向脚本自己、不是包根。
#   所以两条路都给：能猜就猜，猜不出时用 --root 直说，别让人卡在"猜不出包根"。
if [ -n "$PI_ROOT_ARG" ]; then
  PI_ROOT="$PI_ROOT_ARG"
  PI_REAL="${PI_ROOT}/dist/bundle/cli.js"
  PI_BIN="(由 --root 指定)"
else
  PI_BIN="$(command -v pi 2>/dev/null || true)"
  [ -n "$PI_BIN" ] || { echo "✘ 找不到 pi（先 sudo npm i -g @earendil-works/pi-coding-agent）" >&2; exit 1; }
  PI_REAL="$(readlink -f "$PI_BIN")"
  # npm 布局：pi 是 bin 软链 → <pkg>/dist/bundle/cli.js，包根是它的上三级
  case "$PI_REAL" in
    */dist/bundle/*|*/dist/cli.js) PI_ROOT="$(cd "$(dirname "$PI_REAL")/../.." && pwd)" ;;
    *) PI_ROOT="$(cd "$(dirname "$PI_REAL")/../.." && pwd)" ;;
  esac

  # ★ 2026-11-04 修（容器里 100% 误报）：pi 是 **shell 转发脚本**时（仓库 tools/bin/pi、
  #   npm 全局 bin 都长这样），readlink -f 指向脚本自己、上两级是 tools/ 或 bin/，
  #   永远没有 package.json → --check 退 1 → selfcheck 假报「补丁被冲掉」。
  #   证据：容器内 grep -o 回到最新 = 1（补丁其实在），--check 却说「猜不出包根」。
  #   兜底：从 wrapper 正文抠 dist/bundle/cli.js 路径，再把常见布局各试一遍。
  if [ ! -f "$PI_ROOT/package.json" ] && [ -f "$PI_REAL" ] \
     && head -c 2 "$PI_REAL" 2>/dev/null | grep -q '#!'; then
    _cands=""
    _abs="$(grep -oE '/[^"'"'"' ]*dist/bundle/cli\.js' "$PI_REAL" 2>/dev/null | head -1 || true)"
    _rel="$(grep -oE '\$\{?ROOT\}?/[^"'"'"' ]*dist/bundle/cli\.js' "$PI_REAL" 2>/dev/null | head -1 | sed -E 's/^\$\{?ROOT\}?\///' || true)"
    _base="$(cd "$(dirname "$PI_REAL")/../.." 2>/dev/null && pwd)"
    [ -n "$_rel" ]  && _cands="$_base/$_rel
$_cands"
    [ -n "$_abs" ]  && _cands="$_abs
$_cands"
    # ★ 2026-11-24：vendor/pi 已从仓库移除（开源瘦身 167MB），
    #   这里保留该候选只是为了兼容老版本包（自带 vendor/pi 的）；找不到就跳过。
    #   真正的来源是全局 npm 包（第 2 行）。
    _cands="$(dirname "$PI_REAL")/../lib/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js
$_base/vendor/pi/dist/bundle/cli.js
$_cands"
    while IFS= read -r _c; do
      [ -n "$_c" ] && [ -f "$_c" ] || continue
      _r="$(cd "$(dirname "$_c")/../.." 2>/dev/null && pwd)" || continue
      if [ -n "$_r" ] && [ -f "$_r/package.json" ] && [ -d "$_r/dist" ]; then PI_ROOT="$_r"; break; fi
    done <<EOF
$_cands
EOF
  fi
fi
  # ★ 2026-11-17：Windows 的 pi 是 **.cmd 批处理**（不是 #! shell），上面的 #! 兜底进不去。
  #   但那个 .cmd 正文里就写着 cli.js 的绝对路径（SET "_pi=D:\pi\...\dist\bundle\cli.js"），
  #   照它反推包根即可 —— 否则 --check 永远「猜不出包根」，selfcheck 假报「补丁被冲掉」。
  if [ ! -f "$PI_ROOT/package.json" ] && [ -f "$PI_REAL" ]; then
    _win="$(grep -oiE '[a-z]:[\\/][^"]*dist[\\/](bundle[\\/])?cli\.js' "$PI_REAL" 2>/dev/null | head -1 || true)"
    if [ -n "$_win" ]; then
      _p="$(printf '%s' "$_win" | sed 's#\\\\#/#g')"
      _d="$(printf '%s' "$_p" | cut -c1 | tr 'A-Z' 'a-z')"
      _rest="$(printf '%s' "$_p" | cut -c3-)"
      _posix="/$_d$_rest"
      if [ -f "$_posix" ]; then
        _r="$(cd "$(dirname "$_posix")/../.." 2>/dev/null && pwd)" || _r=""
        if [ -n "$_r" ] && [ -f "$_r/package.json" ]; then PI_ROOT="$_r"; fi
      fi
    fi
  fi

[ -f "$PI_ROOT/package.json" ] && [ -d "$PI_ROOT/dist" ] || {
  echo "✘ 猜不出 pi 包根目录（pi=$PI_REAL）" >&2
  echo "  若装的是 npm 布局，直接指定：$0 --root <包根>" >&2
  echo "  例：$0 --root /d/pi/pi-1.5.0/node_modules/@earendil-works/pi-coding-agent" >&2
  exit 1; }
PI_VER="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("version","?"))' "$PI_ROOT/package.json" 2>/dev/null || echo '?')"

echo "pi: $PI_REAL  (v$PI_VER)"
echo "包根: $PI_ROOT"

# 候选 = 所有 chunk + 未打包版 renderer（由 python 按锚点筛，这儿不预筛 →
# 预筛过一次就漏了：按英文原文筛，打完补丁的文件就再也进不了候选）
CAND=()
for f in "$PI_ROOT"/dist/bundle/chunks/*.js "$PI_ROOT/dist/modes/interactive/tui-renderer.js"; do
  [ -f "$f" ] && CAND+=("$f")
done
[ "${#CAND[@]}" -gt 0 ] || { echo "✘ dist/ 结构变了：找不到 chunk / renderer" >&2; exit 1; }

# ── 干活 ──────────────────────────────────────────────────────────────────────
OUT="$(python3 - "$MODE" "$LABEL" "${CAND[@]}" <<'PY'
import sys, re, os, subprocess

mode, label = sys.argv[1], sys.argv[2]
cands = sys.argv[3:]

BAK = ".pi-ui-orig"                    # 打补丁前的原件（--revert 优先用它还原）
LONG = "Jump to latest message"        # 上游英文原文
target = LONG if mode == "revert" else label
WANT_ESC, WANT_RAW = "\\u2193 " + target, "\u2193 " + target
# 锚点：`↓ <文案>${shortcut` —— [^`$] 把匹配限制在模板字符串内，不跨进别的代码
ESC_RE = re.compile(r'\\u2193 [^`$]{1,80}?(?=\$\{shortcut)')
RAW_RE = re.compile('\u2193 [^`$]{1,80}?(?=\\$\\{shortcut)')
MARK = "scrollToEndIndicator"          # 结构标记：确认这段真的是那条胶囊


def node_check(path):
    """语法闸门：改完必须还能被 node 解析（否则 pi 启动即崩，比长文案严重得多）"""
    try:
        r = subprocess.run(["node", "--check", path], capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as e:
        return "node 不可用，未校验（%s）" % e
    return "" if r.returncode == 0 else (r.stderr.strip().splitlines() or ["?"])[0][:200]


changed, already, missing, restored, skipped = [], [], [], [], []
for f in cands:
    try:
        src = open(f, encoding="utf-8").read()
    except OSError as e:
        print("ERR 读不了 %s: %s" % (f, e)); sys.exit(1)

    if not (ESC_RE.search(src) or RAW_RE.search(src)):
        skipped.append(f); continue
    if MARK not in src:                        # 有锚点但不在那条胶囊里 → 别乱改
        skipped.append(f); continue

    # ——— 还原：优先用「打补丁前的原件备份」，比反着做文本替换可靠 ———
    if mode == "revert" and os.path.exists(f + BAK):
        bak = open(f + BAK, encoding="utf-8").read()
        if bak == src:
            already.append(f); continue
        if not os.access(f, os.W_OK):
            print("ERR 只读: %s" % f); sys.exit(1)
        open(f, "w", encoding="utf-8").write(bak)
        changed.append(f); restored.append(f); continue

    new = ESC_RE.sub(lambda _m: WANT_ESC, src)
    new = RAW_RE.sub(lambda _m: WANT_RAW, new)
    if new == src:
        already.append(f); continue
    if mode == "check":
        changed.append(f)               # 检查模式：不写盘，只记「还没生效」
        continue

    if not os.access(f, os.W_OK):
        print("ERR 只读: %s" % f); sys.exit(1)
    # 备份只在「当前是上游原件」时写 —— 反复换文案也不会把补丁当原件存下来
    if (not os.path.exists(f + BAK)) or (LONG in src):
        open(f + BAK, "w", encoding="utf-8").write(src)
    open(f, "w", encoding="utf-8").write(new)
    bad = node_check(f)
    if bad:
        open(f, "w", encoding="utf-8").write(src)       # 回滚，绝不留下语法坏的文件
        print("ERR node --check 失败已回滚: %s -> %s" % (f, bad)); sys.exit(1)
    changed.append(f)

for f in changed:  print("CHANGED " + f)
for f in already:  print("ALREADY " + f)
for f in restored: print("RESTORED " + f)
for f in missing:  print("MISSING " + f)
print("SKIPPED %d（扫了 %d 个文件，无锚点）" % (len(skipped), len(cands)))
print("STATE " + ("changed" if changed else ("already" if already else "none")))
PY
)"
RC=$?
echo "$OUT" | sed 's/^CHANGED /  ✎ 改过: /; s/^RESTORED /  ↩ 从原件还原: /; s/^ALREADY /  · 已是目标文案: /; s/^MISSING /  ? 找不到锚点: /; s/^SKIPPED /  · 跳过: /'
[ "$RC" -ne 0 ] && { echo "✘ 改写失败" >&2; exit 1; }
STATE="$(printf '%s\n' "$OUT" | sed -n 's/^STATE //p')"

case "$MODE$STATE" in
  checkchanged)
    echo "✘ 补丁未生效（文案还是别的）→ 跑: bash tools/pi-ui-patch.sh"
    exit 1 ;;
  checkalready) echo "✓ 补丁在，无需处理"; exit 0 ;;
  applychanged|revertchanged)
    CC="${TMPDIR:-/tmp}/node-compile-cache"
    if [ -d "$CC" ]; then rm -rf "$CC" 2>/dev/null && echo "  · 已清 Node 编译缓存（$CC）"; fi
    echo "✓ 已写入。**新开一个 pi 才看得到**（界面代码在启动时加载）"
    exit 0 ;;
  applyalready|revertalready) echo "· 已是目标文案，跳过"; exit 0 ;;
  *)
    echo "✘ pi 源码里找不到这条胶囊的锚点 —— 上游改了渲染方式" >&2
    echo "  ↳ 手工找: grep -rn 'scrollToEndIndicator' $PI_ROOT/dist | head" >&2
    exit 2 ;;
esac
