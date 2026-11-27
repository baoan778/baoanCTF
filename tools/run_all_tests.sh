#!/bin/bash
# 全量回归（全部 board/tests/test_*.py，当前 80 个，串行）—— 2026-11-11 审计时固化
# ★ 数量别再写死：以 `ls board/tests/test_*.py | wc -l` 为准（本行曾写 77，实为 80）。
#
# 为什么单独有这个脚本：
#   1. board/tests 里每个 test_*.py 都是独立进程、独立临时目录，
#      但并行跑仍会因「只隔离了 BOARD 没隔离 WORK」互相踩（已修 2 处，
#      不排除还有漏的）→ 串行是当前唯一可信口径。
#   2. 容器内 tools 目录部分是 ro 挂载，测试脚本必须用容器里的 python3
#      （tools/venv 在容器内，宿主没有）。
#
# 用法（容器内）：
#   docker exec pictf bash /opt/pi-ctf/tools/run_all_tests.sh
# 判据：
#   末行 SUMMARY pass=N fail=0 才算全绿；NON-OK 段列出所有非零退出。
cd /opt/pi-ctf 2>/dev/null || cd "$(dirname "$0")/.." || exit 1
export PATH="$PWD/tools/bin:$PATH"

# ★ 清 __pycache__（保留，代价 <0.1s，无害）。
#   ⚠ 但**不要**再把这条当成 test_matrix_fuzz flaky 的根因 —— 2026-11-12 实测**推翻了**：
#     · 未清缓存时单独串行跑 20 次：0 失败
#     · 清了缓存时全量回归：仍偶现（约 1/20）
#   即"清缓存"与"不再红"没有因果关系。flaky 真因**至今未确认**，
#   处置在 test_matrix_fuzz.py 的 run() 里（timeout + 留证重试）。
#   本条清理继续留着，理由是"防陈旧字节码"这个一般性好处，不是治 flaky。
find board tools -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
pass=0; fail=0; skip=0; flaky=0
# ★ 该轮（2026-11-21）：证据文件挪到独立目录 + 开跑前做一次「安全清理」。
#   血账（某轮复盘 §3）：清 /tmp 时用了 `rm -rf test*`，把**回归自身的证据**
#   （test_audit.tsv / test_full_*.log）一起删了 —— 结论还在，但逐文件审计没了。
#   修法①证据搬到 /tmp/_evidence/（不再与测试 mkdtemp 前缀撞车）；
#   修法②清理只删**已知测试前缀**且显式排除证据目录，删前先 ls 计数。
EVID="/tmp/_evidence"
mkdir -p "$EVID"
if [ "${PI_TEST_KEEP_TMP:-0}" != "1" ]; then
  # 只清已知的测试临时前缀（白名单式，不用宽通配）；证据目录不在其中。
  _before=$(ls /tmp 2>/dev/null | wc -l)
  find /tmp -maxdepth 1 -type d \( -name "pilab-*" -o -name "gcomp*" -o -name "roleg*" \
       -o -name "dupclaim*" -o -name "clgaps*" -o -name "test-*" -o -name "iso*" \
       -o -name "wrapup*" -o -name "apih_*" -o -name "steer-*" \) \
       -exec rm -rf {} + 2>/dev/null || true
  _after=$(ls /tmp 2>/dev/null | wc -l)
  echo "[cleanup] /tmp 测试残留：$_before -> $_after（证据目录 $EVID 未动）"
fi
: > "$EVID/test_audit.tsv"
run_one() {
  # 跑一个测试文件：**全文留证**到 $EVID/test_full_<name>[.tryN].log，回显 "rc<TAB>末三行"。
  # 为什么留全文：只留末三行永远查不出根因 —— 实测跨题调度只报
  # "init ht2 没有 Traceback"，没有 stderr 正文，无法定位。
  # ★ 2026-11-21 修：**两次尝试各留一份**。原来重跑会覆盖首跑日志，
  #   于是「首跑红、重跑红」时只剩重跑那份，首跑的证据（往往是唯一能看出来
  #   "第几个 relate 失败"的那份）被自己抹掉 —— 实测 test_cap_counts 就踩到。
  local out rc full suffix
  suffix="${2:-}"
  full="$EVID/test_full_$(basename "$1" .py)${suffix}.log"
  # ★ 2026-11-21：开 faulthandler —— 段错误（rc=-11）与解释器内部崩溃
  #   （如 re/_parser 的 AttributeError）在纯 Python 层看不到栈，只有 faulthandler
  #   才会把原生调用栈打到 stderr。代价：失败时多几十行输出（已在全文日志里，不进报告）。
  #   实测触发过：test_cli_smoke 的 flags-left rc=-11、test_nonce 的 re/_parser 崩，
  #   两次都是"重跑就绿、当时没栈"，只能靠这个下次抓。
  out=$(PYTHONFAULTHANDLER=1 timeout 240 python3 "$1" 2>&1)
  rc=$?
  printf '%s\n' "$out" > "$full" 2>/dev/null || true
  printf '%s\t%s' "$rc" "$(printf '%s' "$out" | tail -3 | tr '\n' ' ')"
}
for f in board/tests/test_*.py; do
  IFS=$'\t' read -r rc tailtxt <<<"$(run_one "$f")"
  tag=OK
  if [ "$rc" -eq 77 ]; then tag=SKIP
  elif [ "$rc" -ne 0 ]; then
    # ★ 2026-11-21（R5-D）：**只对失败项重跑一次**，把「确定性红」与「负载敏感红」分开。
    #   为什么要有这一步（实测）：全量串行跑时 test_gate_stats / test_lead /
    #   test_supervise / test_transcript_cost 偶发红，单跑 5~6 次全绿；
    #   它们都是**租约/TTL/超时**类判据，容器被并发 docker exec 抢 CPU 时结果会漂。
    #   注意：这一步**不豁免**任何真失败 —— 重跑仍红就照旧记 FAIL 并进 NON-OK 段，
    #   重跑转绿的单列 FLAKY 段（既不当成绿，也不混进红），逼人去看是不是判据太依赖时序。
    IFS=$'\t' read -r rc2 tailtxt2 <<<"$(run_one "$f" ".try2")"
    tailtxt="$tailtxt  (全文 $EVID/test_full_$(basename "$f" .py)*.log)"
    if [ "$rc2" -eq 0 ]; then
      tag=FLAKY; flaky=$((flaky+1))
      tailtxt="$tailtxt  || 重跑:$tailtxt2"
    else
      tag=FAIL; fail=$((fail+1))
      tailtxt="$tailtxt  || 重跑 rc=$rc2:$tailtxt2"
    fi
    rc=$rc2
  fi
  if [ "$tag" = OK ]; then pass=$((pass+1)); fi
  if [ "$tag" = SKIP ]; then skip=$((skip+1)); fi
  printf '%s\t%s\t%s\t%s\n' "$tag" "$rc" "$f" "$tailtxt" >> "$EVID"/test_audit.tsv
done
# ★ 2026-11-19 追加：agent 侧功能回归（不是 board 测试，是「这轮修的缺陷还会不会复发」）
#   覆盖 6 处已修缺陷：python3 看不见 venv 库 / ISE 被当题库故障 / 陈旧缺工具信号 /
#   py2 模块让人去装 / 证据行取错 / 攻击面计数失真 / 收尾轮污染会话 / facts 噪声 /
#   隔离必须让 pi 看不见。改前全部会红，改后全绿 —— 它就是这些修复的判据。
#   为什么放这里：这些文件在 tools/ 下，board/tests/test_*.py 的 glob 扫不到。
if [ -f tools/_verify_all.py ]; then
  vout=$(timeout 300 python3 tools/_verify_all.py 2>&1)
  vrc=$?
  #   为什么抓 ASCII 的 RESULT: 而不是中文的「结果：」——Windows 上 python3 的 stdout 是
  #   cp936，中文经管道变成乱码字节，正则匹配不到，报告里这一组永远是空白（实测）。
  vline=$(printf '%s' "$vout" | grep -E '^RESULT: ' | tail -1)
  if [ $vrc -eq 0 ]; then pass=$((pass+1)); tag=OK
  elif [ $vrc -eq 77 ]; then skip=$((skip+1)); tag=SKIP
  else fail=$((fail+1)); tag=FAIL; fi
  printf '%s\t%s\t%s\t%s\n' "$tag" "$vrc" "tools/_verify_all.py" "$vline" >> "$EVID"/test_audit.tsv
  echo "---- agent 功能回归 ----"
  printf '%s\n' "$vline"
fi

# ★ 该轮：崩溃观测（客观量，不靠判断）
if command -v dmesg >/dev/null 2>&1; then
  _segv=$(dmesg 2>/dev/null | grep -c "signal: 11" || true)
  echo "[crash-watch] dmesg 里 signal: 11 累计 $_segv 次（记录于 $EVID/crash_watch.log）"
  printf "%s\t%s\n" "$(date +%FT%T)" "${_segv:-0}" >> "$EVID/crash_watch.log" 2>/dev/null || true
fi

# 该轮 (2026-11-22): FLAKY 连续两轮出现 -> 升级为 FAIL。
#   为什么：连续五轮把偶发红记成"吃时序"，实际是 Python 解释器 SIGSEGV。
#   "重跑就绿"只说明这次没抽中，不说明没问题。
#   状态跨轮保留：$EVID/flaky_streak.tsv 每行 "<测试文件><TAB><连续次数>"。
STREAK="$EVID/flaky_streak.tsv"
touch "$STREAK"
_escalate=0
while IFS="	" read -r _tf _tn; do
  [ -n "$_tf" ] || continue
  if grep -P "^FLAKY	" "$EVID"/test_audit.tsv | grep -qF "$_tf"; then
    _cnt=$(( ${_tn:-0} + 1 ))
  else
    _cnt=0
  fi
  printf "%s	%s\n" "$_tf" "$_cnt" >> "$STREAK.new"
  if [ "$_cnt" -ge 2 ]; then
    sed -i "s|^FLAKY	0	$_tf	|FAIL	1	$_tf	[连续${_cnt}轮FLAKY→升级] |" "$EVID"/test_audit.tsv
    _escalate=$((_escalate+1))
  fi
done < "$STREAK"
mv "$STREAK.new" "$STREAK"
if [ "$_escalate" -gt 0 ]; then
  fail=$((fail+_escalate)); flaky=$((flaky-_escalate))
  echo "---- ESCALATED：$_escalate 个 FLAKY 连续两轮出现 -> 已升 FAIL ----"
fi

echo "==== SUMMARY pass=$pass fail=$fail skip=$skip flaky=$flaky ===="
echo "---- NON-OK（FAIL/SKIP/FLAKY）----"
grep -v -P '^OK\t' "$EVID"/test_audit.tsv || true
if [ "$flaky" -gt 0 ]; then
  echo "---- FLAKY 提示（首跑红、重跑绿：不是功能坏了，是判据吃时序）----"
  echo "   逐个看是不是租约/TTL/超时类断言；连续两轮出现在 FLAKY = 该把它改成注入时钟。"
fi