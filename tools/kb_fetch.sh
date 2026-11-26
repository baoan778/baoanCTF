#!/usr/bin/env bash
# 按需取外部知识基线 —— **不预存内容**（progressive disclosure：加载不相关的东西会掉分）
#
#   bash tools/kb_fetch.sh list              # 看有哪些源
#   bash tools/kb_fetch.sh get howtohunt     # 取到 stdout（管道给 grep/less）
#   bash tools/kb_fetch.sh save howtohunt    # 落盘到 tools/knowledge/external/
#
# 为什么是脚本而不是把内容抄进仓库：
#   ① 这些源合计 MB 级，全量入库＝臃肿；② 它们会更新，抄进来立刻过时；
#   ③ 仓库已有结论「一次塞多张不相关的技能卡会掉分」。
set -uo pipefail
PX="${KB_PROXY:-http://127.0.0.1:7897}"
BASE="https://raw.githubusercontent.com"
# 名:owner/repo/branch/path    （2026-10-03 第七轮采集，全部走代理可达）
declare -A SRC=(
  [howtohunt]="KathanP19/HowToHunt/master/README.md"                       # 各类漏洞测试方法论 + test case
  [bugbounty-all]="daffainfo/AllAboutBugBounty/master/README.md"           # bypasses + payloads 大全
  [bounty-reports]="reddelexc/hackerone-reports/master/README.md"          # ★ HackerOne 公开报告 = oracle 源头
  [search-engines]="edoardottt/awesome-hacker-search-engines/main/README.md" # 被动收集入口全集
  [llm4pentest]="simon-p-j-r/LLM4Pentest/main/README.md"                   # 论文 + 项目清单
  [offsec-ai]="Yeti-791/Awesome-Offensive-AI-Agentic-Landscape/main/README.md" # 53项目+11模型+116论文
  [tob-skills]="trailofbits/skills/main/README.md"                         # ★ 专业安全公司的窄技能 + Trophy Case
  [awesome-pentest]="enaqx/awesome-pentest/master/README.md"
  [race-cheatsheet]="blacklanternsecurity/red-run/main/skills/web/race-condition/SKILL.md"  # ★ 竞态全流程
  [bypass-403]="yaklang/hack-skills/main/skills/401-403-bypass-techniques/SKILL.md"        # ★ 403绕过完整矩阵
  # ★ yaklang/hack-skills：103 个漏洞类 skill 的体系化库 —— 任意一类都能现取
  #   列出全部：bash tools/kb_fetch.sh hackskills        （见下方 hackskills 分支）
  #   取某一类：KB_SKILL=request-smuggling bash tools/kb_fetch.sh get hackskill
)
case "${1:-list}" in
  list) printf '%s\n' "${!SRC[@]}" | sort ;;
  hackskills)  # 列出 103 个 skill 名
        curl -sL -x "$PX" "https://api.github.com/repos/yaklang/hack-skills/git/trees/main?recursive=1" \
          | python3 -c "import sys,json;print('\n'.join(sorted({p.split('/')[1] for p in (t['path'] for t in json.load(sys.stdin).get('tree',[])) if p.startswith('skills/') and p.count('/')>=2})))" ;;
  get)  [ -n "${2:-}" ] || { echo "用法: kb_fetch.sh get <名|hackskill>"; exit 2; }
        if [ "$2" = "hackskill" ]; then
          [ -n "${KB_SKILL:-}" ] || { echo "用法: KB_SKILL=<skill名> kb_fetch.sh get hackskill（名字先 hackskills）"; exit 2; }
          curl -sL -x "$PX" "$BASE/yaklang/hack-skills/main/skills/$KB_SKILL/SKILL.md"; exit 0
        fi
        u="${SRC[$2]:-}"; [ -n "$u" ] || { echo "未知源: $2（先 list）"; exit 2; }
        curl -sL -x "$PX" "$BASE/$u" ;;
  save) [ -n "${2:-}" ] || { echo "用法: kb_fetch.sh save <名>"; exit 2; }
        u="${SRC[$2]:-}"; [ -n "$u" ] || { echo "未知源: $2（先 list）"; exit 2; }
        d="$(dirname "$0")/knowledge/external"; mkdir -p "$d"
        curl -sL -x "$PX" "$BASE/$u" -o "$d/$2.md" -w "$d/$2.md  %{http_code}  %{size_download}B\n" ;;
  *) echo "用法: kb_fetch.sh {list|get <名>|save <名>}" ;;
esac
