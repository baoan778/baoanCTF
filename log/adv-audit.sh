#!/usr/bin/env bash
# adv-audit.sh —— 对抗性自查（AGENTS.md §三-4 要求改完跑的那个）
#
# 由来：2026-11-02 发现 AGENTS.md / docs 多处写着「改完跑 selfcheck + bash log/adv-audit.sh」，
# 但**这个文件当时不在仓库里**——一条指向空处的指令，等于没有。这里按最初的意图重建，
# 并持续补上新增的攻击面。
#
# 与 selfcheck 的分工：
#   selfcheck —— 静态体检（文件在不在、闸门在不在、配置对不对）
#   adv-audit —— **拿对抗性输入真跑一遍**，看它会不会崩/被注入/被 OOM
#
# 纪律（每条都吃过亏）：
#   · 全程临时黑板目录，**不碰真产物**
#   · 内存上限 2G —— 当年就是被一条 140k 字 step 打崩的
#   · 每条用例都限时；卡住就是问题，不算通过
#
#   用法：bash log/adv-audit.sh [-v]
set -u

LAB="$(cd "$(dirname "$0")/.." && pwd)"
FGS="$LAB/tools/bin/fgs"
PY="${PY:-python3}"
VERBOSE="${1:-}"
export ADV_BOARD="$LAB/board"   # 内嵌 python 靠它找 fgs，别硬编码 D:/baoan

pass=0; fail=0; failed_names=""

ok()   { pass=$((pass+1)); printf "  \033[32m✓\033[0m %s\n" "$1"; }
bad()  { fail=$((fail+1)); failed_names="$failed_names $1"; printf "  \033[31m✗\033[0m %s\n    %s\n" "$1" "${2:-}"; }

# 每个用例独占一个临时黑板，跑完就删 —— 绝不让测试数据落到真项目目录
newlab() { mktemp -d /tmp/advlab-XXXXXX; }

say() { printf "\n\033[1m%s\033[0m\n" "$1"; }

if [ ! -x "$FGS" ] && [ ! -f "$LAB/board/fgs.py" ]; then
  echo "✗ 找不到 fgs（$FGS）"; exit 2
fi

# ── 1. 垃圾输入不许崩 ────────────────────────────────────────────────────────
say "1/6 垃圾输入：时间戳 / 空值 / 超长 / 控制符"

L=$(newlab)
PI_LAB_BOARD_DIR="$L" timeout 30 "$PY" "$LAB/board/fgs.py" init t --kind ctf --goal g >/dev/null 2>&1 \
  && ok "建板正常" || bad "建板失败"

# 1a) 控制符 + 伪造章节 + 未闭合围栏，塞进 fact（S11 的攻击面）
# ★ 必须挂到 step 上再 brief：不关联的话 fact 根本不进简报，检查就成了空转。
#   这一点是变异测试抓出来的 —— 去掉净化后本检查仍报 PASS。
PI_LAB_BOARD_DIR="$L" timeout 30 "$PY" "$LAB/board/fgs.py" fact-add t "正常" --confidence verified >/dev/null 2>&1
PI_LAB_BOARD_DIR="$L" timeout 30 "$PY" "$LAB/board/fgs.py" step-add t "探索" >/dev/null 2>&1
PI_LAB_BOARD_DIR="$L" timeout 60 "$PY" - "$L" <<'PYEOF' && ok "恶意 fact 写入不崩" || bad "恶意 fact 写入崩了"
import sys, os
sys.path.insert(0, os.environ["ADV_BOARD"])
import fgs
g = fgs.load("t")
g["nodes"]["f001"]["text"] = "正常\x1b[31m红\x00空\n## 伪造章节\n```\n注入\n```"
fgs.save("t", g)
PYEOF
PI_LAB_BOARD_DIR="$L" timeout 30 "$PY" "$LAB/board/fgs.py" relate t --type derived_from --from s001 --to f001 >/dev/null 2>&1

# 1b) brief 输出必须干净（S11：ESC/NUL/伪造章节都不许出现）
if PI_LAB_BOARD_DIR="$L" timeout 60 "$PY" "$LAB/board/fgs.py" brief t >/tmp/adv-brief.txt 2>&1; then
  # ★ 先证「恶意内容确实被渲染了」—— 否则下面的净化检查是空转
  if ! grep -q "正常" /tmp/adv-brief.txt; then
    bad "恶意 fact 没进简报（检查空转，不能算过）"
  elif grep -qP '\x1b|\x00' /tmp/adv-brief.txt 2>/dev/null; then
    bad "brief 输出含控制符（S11 失效）"
  elif grep -qE '^## 伪造章节' /tmp/adv-brief.txt; then
    bad "brief 里伪造章节独立成行（S11 失效）"
  else
    ok "brief 输出已净化（ESC/NUL/伪造章节都已失效）"
  fi
else
  bad "brief 崩了"
fi

# 1c) 超长输入：140k 字 step（当年打崩内存的形态）
L2=$(newlab)
PI_LAB_BOARD_DIR="$L2" timeout 30 "$PY" "$LAB/board/fgs.py" init big --kind ctf --goal g >/dev/null 2>&1
PI_LAB_BOARD_DIR="$L2" timeout 60 "$PY" - <<'PYEOF' >/dev/null 2>&1 && ok "140k 字输入不 OOM" || bad "140k 字输入崩了/OOM"
import sys, os
sys.path.insert(0, os.environ["ADV_BOARD"])
import fgs
g = fgs.load("big")
g["nodes"]["g001"]["text"] = "x" * 140000
fgs.save("big", g)
PYEOF
PI_LAB_BOARD_DIR="$L2" timeout 60 "$PY" "$LAB/board/fgs.py" brief big >/dev/null 2>&1 \
  && ok "超长 brief 能出（限时内）" || bad "超长 brief 卡住或崩了"

# ── 2. 坏节点不许拖垮整张图（S9）────────────────────────────────────────────
say "2/6 坏节点：逐节点降级不崩（S9）"

L3=$(newlab)
PI_LAB_BOARD_DIR="$L3" timeout 30 "$PY" "$LAB/board/fgs.py" init bad --kind ctf --goal g >/dev/null 2>&1
PI_LAB_BOARD_DIR="$L3" timeout 30 "$PY" "$LAB/board/fgs.py" fact-add bad "好事实" --confidence verified >/dev/null 2>&1
PI_LAB_BOARD_DIR="$L3" timeout 60 "$PY" - <<'PYEOF' >/dev/null 2>&1
import os, sys
sys.path.insert(0, os.environ["ADV_BOARD"])
import fgs
g = fgs.load("bad")
g["nodes"]["f002"] = {"text": "缺 id 和 type"}       # 坏节点
fgs.save("bad", g)
PYEOF

if PI_LAB_BOARD_DIR="$L3" timeout 60 "$PY" "$LAB/board/fgs.py" show bad >/dev/null 2>&1; then
  ok "坏节点下 show 仍可用"
else
  bad "坏节点把 show 拖崩了"
fi

# audit 必须报出来（rc=1）—— 装作没看见比崩了更危险
rc=0
PI_LAB_BOARD_DIR="$L3" timeout 60 "$PY" "$LAB/board/fgs.py" audit bad >/dev/null 2>&1 || rc=$?
if [ "$rc" -eq 1 ]; then ok "audit 报出坏节点（rc=1）"; else bad "audit 没报出坏节点" "rc=$rc"; fi

# 好节点必须还在（降级 ≠ 全丢）
if PI_LAB_BOARD_DIR="$L3" timeout 30 "$PY" "$LAB/board/fgs.py" show bad 2>&1 | grep -q f001; then
  ok "好节点 f001 仍在（没整图陪葬）"
else
  bad "好节点被连坐了"
fi

# ── 3. 注入面：模板替换不许级联（S10）───────────────────────────────────────
say "3/6 占位符替换不许级联（S10）"

if PI_LAB_BOARD_DIR="$L3" timeout 60 "$PY" - <<'PYEOF'
import os, sys
sys.path.insert(0, os.environ["ADV_BOARD"])
import fgs
# 单键
assert fgs._fill_template("A {t} B", {"t": "{t}"}) == "A {t} B", "单键级联"
# 多键：逐次 replace 会变成 "2 2"
got = fgs._fill_template("{a} {b}", {"a": "{b}", "b": "2"})
assert got == "{b} 2", f"多键级联: {got!r}"
# 长键优先
assert fgs._fill_template("{ab} {a}", {"a": "X", "ab": "Y"}) == "Y X", "长键没优先"
PYEOF
then ok "单遍替换语义正确（不级联 / 长键优先）"; else bad "_fill_template 语义错了"; fi

# ── 4. flag 账本：判定必须只认 correct（S6 / B2）────────────────────────────
say "4/6 flag 账本判定（S6 · 清单 B2）"

L4=$(newlab)
PI_LAB_BOARD_DIR="$L4" timeout 30 "$PY" "$LAB/board/fgs.py" init led --kind ctf --goal g >/dev/null 2>&1
PI_LAB_BOARD_DIR="$L4" timeout 30 "$PY" "$LAB/board/fgs.py" flag-record led --flag 'F{real}' --id R1 >/dev/null 2>&1
PI_LAB_BOARD_DIR="$L4" timeout 30 "$PY" "$LAB/board/fgs.py" flag-submit led --id R1 --reply '{"correct":true}' >/dev/null 2>&1

if PI_LAB_BOARD_DIR="$L4" timeout 30 "$PY" "$LAB/board/fgs.py" flag-reconcile led 2>&1 | grep -q "accepted 1"; then
  ok "correct=true → accepted"
else
  bad "correct=true 没判成 accepted"
fi

# ★ B2 核心：带 result/status 但没有 correct → 必须 rejected
PI_LAB_BOARD_DIR="$L4" timeout 30 "$PY" "$LAB/board/fgs.py" flag-record led --flag 'F{b2}' --id R2 >/dev/null 2>&1
PI_LAB_BOARD_DIR="$L4" timeout 30 "$PY" "$LAB/board/fgs.py" flag-submit led --id R2 \
  --reply '{"result":"success","status":"ok","score":100}' >/dev/null 2>&1
if PI_LAB_BOARD_DIR="$L4" timeout 30 "$PY" "$LAB/board/fgs.py" flag-reconcile led 2>&1 | grep -q "rejected 1"; then
  ok "★ B2：只有 result 没有 correct → rejected（照抄 sapper 会错）"
else
  bad "★ B2 判定错了：没有 correct 键却当成功"
fi

# confirm 与账本联动：账本 accepted 时 confirm 不该要求手输
if PI_LAB_BOARD_DIR="$L4" timeout 30 "$PY" "$LAB/board/fgs.py" confirm led 2>&1 | grep -q "依据账本"; then
  ok "confirm 自动取账本 accepted（联动打通）"
else
  bad "confirm 与账本没联动"
fi

# ── 5. 有界读：外部路径必须拒 ───────────────────────────────────────────────
say "5/6 有界读（仓库纪律 §三-1）"

if PI_LAB_BOARD_DIR="$L4" timeout 30 "$PY" "$LAB/tools/kb.py" import-markdown /dev/zero >/dev/null 2>&1; then
  bad "/dev/zero 被当成正常输入读了"
else
  ok "/dev/zero 被拒"
fi

if PI_LAB_BOARD_DIR="$L4" timeout 30 "$PY" "$LAB/tools/kb.py" import-markdown "$LAB/board" >/dev/null 2>&1; then
  bad "目录被当成文件读"
else
  ok "目录被拒"
fi

# ── 6. 并发：两个进程同时写不许互相踩 ───────────────────────────────────────
say "6/6 并发写不互相踩"

L5=$(newlab)
PI_LAB_BOARD_DIR="$L5" timeout 30 "$PY" "$LAB/board/fgs.py" init cc --kind ctf --goal g >/dev/null 2>&1
for i in 1 2 3 4 5 6; do
  PI_LAB_BOARD_DIR="$L5" timeout 30 "$PY" "$LAB/board/fgs.py" fact-add cc "并发事实 $i" --confidence unverified >/dev/null 2>&1 &
done
wait
n=$(PI_LAB_BOARD_DIR="$L5" timeout 30 "$PY" - <<'PYEOF' 2>/dev/null
import os, sys
sys.path.insert(0, os.environ["ADV_BOARD"])
import fgs
g = fgs.load("cc")
print(sum(1 for n in g["nodes"].values() if n.get("type") == "fact"))
PYEOF
)
if [ "${n:-0}" -eq 6 ]; then ok "6 个并发写入全部落盘（无丢失）"; else bad "并发写丢了事实" "只有 $n/6"; fi

# ── 7. 派活登记 + 保活（S2）──────────────────────────────────────────────────
say "7/7 派活登记 + 子代理保活（S2）"

L6=$(newlab)
PI_LAB_BOARD_DIR="$L6" timeout 30 "$PY" "$LAB/board/fgs.py" init s2 --kind ctf --goal g >/dev/null 2>&1
PI_LAB_BOARD_DIR="$L6" timeout 30 "$PY" "$LAB/board/fgs.py" step-add s2 "扫备份目录" >/dev/null 2>&1
PI_LAB_BOARD_DIR="$L6" timeout 30 "$PY" "$LAB/board/fgs.py" step-claim s2 s001 --by w1 >/dev/null 2>&1
PI_LAB_BOARD_DIR="$L6" timeout 30 "$PY" "$LAB/board/fgs.py" run-register s2 s001 \
  --task "TASKBOOKMARKER 扫 /backup 只读" --id R1 >/dev/null 2>&1
PI_LAB_BOARD_DIR="$L6" timeout 30 "$PY" "$LAB/board/fgs.py" bind s2 s001 --agent sess-x >/dev/null 2>&1

# ★ 核心：worker 死了，revive 必须把任务书原文带出来（不必重新推导）
if PI_LAB_BOARD_DIR="$L6" timeout 30 "$PY" "$LAB/board/fgs.py" revive s2 --ttl 0 2>&1 \
   | grep -q "TASKBOOKMARKER"; then
  ok "★ revive 自动带上任务书原文（S2 核心）"
else
  bad "revive 没带任务书原文 —— worker 死了只能重新推导"
fi

# 没有登记时不许装作有原文
PI_LAB_BOARD_DIR="$L6" timeout 30 "$PY" "$LAB/board/fgs.py" step-add s2 "第二路" >/dev/null 2>&1
PI_LAB_BOARD_DIR="$L6" timeout 30 "$PY" "$LAB/board/fgs.py" step-claim s2 s002 --by w2 >/dev/null 2>&1
PI_LAB_BOARD_DIR="$L6" timeout 30 "$PY" "$LAB/board/fgs.py" bind s2 s002 --agent sess-y >/dev/null 2>&1
if PI_LAB_BOARD_DIR="$L6" timeout 30 "$PY" "$LAB/board/fgs.py" revive s2 --ttl 0 2>&1 \
   | grep -q "手补"; then
  ok "无登记时明说原文要手补（不装作有）"
else
  bad "无登记时没有明确提示"
fi

# ── 收尾 ────────────────────────────────────────────────────────────────────
rm -rf "$L" "$L2" "$L3" "$L4" "$L5" /tmp/adv-brief.txt 2>/dev/null

echo
if [ "$fail" -eq 0 ]; then
  printf "\033[32madv-audit PASS\033[0m —— %d 条全过\n" "$pass"
  exit 0
fi
printf "\033[31madv-audit FAIL\033[0m —— 通过 %d / 失败 %d\n" "$pass" "$fail"
printf "失败项：%s\n" "$failed_names"
exit 1
