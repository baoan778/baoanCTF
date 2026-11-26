#!/bin/bash
# =============================================================================
# PiCTF WebUI 部署自检 / 复现脚本
#
# 用法（在 WSL 里，或在 Windows 上用 wsl 调用）：
#   bash verify-deploy.sh            # 快速自检（约 10 秒）
#   bash verify-deploy.sh --full     # 追加 39 条回归（约 2 到 4 分钟）
#   bash verify-deploy.sh --e2e      # 追加真实发一条消息（会消耗一次 API 调用）
#   bash verify-deploy.sh --full --e2e
#
# 快速自检 16 项（引擎/容器/镜像/HTTP/py 弹药/工具注册/kb/Go 五件套/联网扩展/pi 常驻/
# 模型/密钥/密钥不落盘/旧路径/运行期目录/挂载可写），--full 加 39 条回归，--e2e 加真实模型调用与联网搜索。
#
# 退出码：0 = 全部通过；1 = 有失败项。每项打印 PASS/FAIL/WARN 与原始证据（WARN 不计失败，但要点开看）。
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT" || exit 9

CT=pictf
# ★ 2026-11-13：镜像 tag 从 compose.yaml 现读，不再写死。
#   写死的代价：升版到 0.2.4 之后这条永远 FAIL；而 FAIL 里混着一条真故障时，
#   人就会开始忽略整片红灯（「假红比没检查更贵」）。
IMG="$(sed -n 's/^[[:space:]]*image:[[:space:]]*\([^[:space:]]*\).*/\1/p' "$ROOT/compose.yaml" 2>/dev/null | head -1 | tr -d '\r')"
IMG="${IMG:-pictf-webui:0.2.4}"
URL=http://127.0.0.1:30142/
FULL=0; E2E=0
for a in "$@"; do
  case "$a" in
    --full) FULL=1 ;;
    --e2e)  E2E=1 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "未知参数：$a"; exit 2 ;;
  esac
done

PASS=0; FAIL=0; WARN=0
ok()   { PASS=$((PASS+1)); printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  \033[31mFAIL\033[0m  %s\n' "$1"; }
warn() { WARN=$((WARN+1)); printf '  \033[33mWARN\033[0m  %s\n' "$1"; }
info() { printf '        %s\n' "$1"; }

echo "== PiCTF 部署自检 =="
echo "目录：$ROOT"
echo

# 1 Docker 引擎
if V="$(docker version --format 'client={{.Client.Version}} server={{.Server.Version}}' 2>&1)"; then
  ok "Docker 引擎可用 ($V)"
else
  bad "Docker 引擎不可用"; info "$(echo "$V" | head -2)"
fi

# 2 容器状态
ST="$(docker ps --filter "name=^${CT}$" --format '{{.Status}}' 2>/dev/null)"
if [ -n "$ST" ] && echo "$ST" | grep -q healthy; then
  ok "容器 $CT 运行中且健康 ($ST)"
else
  bad "容器 $CT 未就绪 (状态: ${ST:-无})"
fi

# 3 镜像
ISZ="$(docker images "$IMG" --format '{{.Size}}' 2>/dev/null | head -1)"
if [ -n "$ISZ" ]; then ok "镜像 $IMG 存在 ($ISZ)"; else bad "镜像 $IMG 不存在"; fi

# 4 HTTP
CODE="$(curl -s -o /dev/null -w '%{http_code}' "$URL" 2>/dev/null)"
if [ "$CODE" = "200" ]; then ok "工作台 HTTP 200 ($URL)"; else bad "工作台 HTTP=$CODE ($URL)"; fi

# 5 容器内 Python 弹药
OUT="$(docker exec "$CT" bash -lc 'py -c "import importlib.metadata as m, pwn, z3, Crypto, gmpy2; print(m.version(\"pwntools\"), z3.get_version_string())"' 2>&1 | tail -1)"
case "$OUT" in
  *Traceback*|*Error*) bad "py 弹药异常"; info "$OUT" ;;
  "")                  bad "py 命令无输出" ;;
  *)                   ok "py 弹药可用 (pwntools $OUT)" ;;
esac

# 6 工具登记
TR="$(docker exec "$CT" bash -lc 'cd /opt/pi-ctf && python3 tools/toolreg.py check 2>&1 | grep -o "可用 [0-9]* / [0-9]*" | head -1' 2>/dev/null | tr -d '\r')"
TRN="${TR#可用 }"; TRA="${TRN%% / *}"; TRB="${TRN##* / }"
if [ -z "$TR" ]; then
  bad "toolreg 无输出"
elif [ -n "$TRA" ] && [ "$TRA" = "$TRB" ]; then
  ok "工具登记 $TRA/$TRB 全可用"
else
  bad "工具登记不完整 ($TR)"
fi

# 7 知识库
KB="$(docker exec "$CT" bash -lc 'cd /opt/pi-ctf && tools/bin/kb search "nacos 未授权" --kind poc --top 1 2>&1 | head -1' 2>/dev/null | tr -d '\r')"
case "$KB" in
  *"命中"*) ok "知识库检索有命中 ($KB)" ;;
  *)        bad "知识库检索失败 ($KB)" ;;
esac

# 8 Go 系 Web 工具
GO="$(docker exec "$CT" bash -lc 'for t in ffuf httpx katana naabu nuclei; do command -v $t >/dev/null || echo "MISS:$t"; done; find /home/ctf/nuclei-templates -name "*.yaml" | wc -l' 2>/dev/null | tr -d '\r')"
TPL="$(echo "$GO" | tail -1)"; TPL="${TPL:-0}"
if echo "$GO" | grep -q MISS; then
  bad "Go 工具缺失 ($(echo "$GO" | tr '\n' ' '))"
elif [ "$TPL" -ge 1000 ] 2>/dev/null; then
  ok "Go 系工具齐备，nuclei 模板 $TPL 条"
else
  warn "Go 系工具齐备，但 nuclei 模板只有 $TPL 条（见 docs/交付-部署说明.md 的模板更新命令）"
fi

# 8.5 联网扩展装载（pi-web-access）。注意：**不能查 /api/tools** —— 那是容器内二进制工具清单
#     （7z/nmap/kb…），不含 agent 扩展工具，查它必然误判。真信号是「设置里声明了 + 依赖能解析 + pi 没被扩展拖死」。
WEB="$(docker exec -u ctf "$CT" bash -lc '
  A=/home/ctf/.pi-ctf/agent
  P="$A/npm/node_modules/pi-web-access"
  decl=$(pi list 2>/dev/null | grep -c "pi-web-access@" || true)
  [ -s "$P/dist/index.js" ] && pkg=ok || pkg=miss
  dep=$(node -e "const{createRequire}=require(\"module\");const r=createRequire(\"$P/dist/index.js\");try{r.resolve(\"p-limit\");r.resolve(\"undici\");console.log(\"ok\")}catch(e){console.log(\"miss:\"+e.code)}" 2>/dev/null)
  printf "decl=%s pkg=%s dep=%s" "$decl" "$pkg" "$dep"
' 2>/dev/null | tr -d '\r')"
case "$WEB" in
  "decl=1 pkg=ok dep=ok") ok "联网扩展已装载（pi-web-access 声明 + 包体 + 依赖可解析）" ;;
  *) bad "联网扩展不完整 ($WEB) —— 见 docs/交付-故障处置.md「pi 一起就退出」" ;;
esac

# 8.6 pi 常驻（扩展加载失败会让 pi 反复 exit 1，工作台 HTTP 200 但 agent 全废 —— 2026-11-01 真故障）
ALIVE="$(docker exec "$CT" bash -lc 'pgrep -x pi >/dev/null && echo yes || echo no' 2>/dev/null | tr -d '\r')"
DEAD="$(docker logs --since 15m "$CT" 2>&1 | grep -c "重启次数用尽" || true)"
if [ "$ALIVE" = "yes" ] && [ "${DEAD:-0}" -eq 0 ]; then
  ok "pi 常驻运行，近 15 分钟无「重启次数用尽」"
else
  bad "pi 未常驻 (alive=$ALIVE 重启用尽日志=${DEAD:-0}) —— docker logs $CT | tail -30"
  echo "    ↳ 若日志显示 pi 退出 code=0（不是崩溃、没有 stderr），优先查 runtime 钉住的会话文件是否已被删除/移入 .trash："
  echo "      docker exec $CT bash -lc \"curl -s http://127.0.0.1:30142/api/status | head -c 300\""
  echo "      含 activeRuntimeId 指向 sessions/<id>.jsonl；该文件不在就说明会话被清空 —— 工作台会自动改开新会话（server.mjs 已修），重启容器即可恢复。"
fi

# 9 模型配置（脱敏）
# ★ 2026-11-23：内嵌 Python 一律用**单引号 heredoc**，不再往双引号里套双引号。
#   实测（MSYS bash 5.3.20）：
#       X="$(py -c "  ... z=[k+('a') for k in []] ...  ")"
#   这种「内层双引号 + 方括号 + 圆括号 + 单引号」组合会被解析器截断，报
#       syntax error near unexpected token `('
#   而 Git Bash 5.3.15 与容器 bash 都不报 —— 同一份脚本在不同 bash 上判据不同。
#   heredoc 只有一个引号上下文，两边都一样，且不必再担心 $ 与反引号被展开。
MODEL="$(curl -s "${URL}api/models" 2>/dev/null | python3 - <<'PY' 2>/dev/null
import sys, json
try:
    d = json.load(sys.stdin)
    p = (d.get('providers') or [{}])[0]
    m = (p.get('models') or [{}])[0]
    print(p.get('id'), m.get('id'), p.get('baseUrl'))
except Exception:
    print('')
PY
)"
if [ -n "$MODEL" ]; then ok "模型已配置 (provider/model/baseUrl = $MODEL)"; else bad "模型配置读取失败"; fi

# 10 密钥 —— ★ 2026-11-13 重写。原来的判据是 /api/models/keys 里的 hasKey，
#    而**虚拟 provider（ctf/*）的 hasKey 永远是 False**（它本来就不需要密钥），
#    所以这条从落地起就永远 FAIL —— 真正的 401 就埋在整片假红里没人点开。
#    现在分两段：10a 配置层面（虚拟 provider 算可用），10b 真打一次上游（401 就是 401）。
KEY="$(curl -s "${URL}api/models/keys" 2>/dev/null | python3 - <<'PY' 2>/dev/null
import sys, json
try:
    d = json.load(sys.stdin).get('keys', {})

    def ok1(v):
        return bool(v.get('hasKey')) or bool(v.get('viaAuth')) or bool(v.get('virtual'))

    parts = [k + ('=ok' if ok1(v) else '=MISSING') for k, v in d.items()]
    print(','.join(parts) or 'none')
except Exception:
    print('')
PY
)"
if [ -z "$KEY" ] || [ "$KEY" = "none" ]; then
  bad "密钥状态读取失败（/api/models/keys 无输出）"
elif echo "$KEY" | grep -q "MISSING"; then
  bad "有 provider 没有密钥来源 ($KEY)"
else
  ok "各 provider 密钥来源齐备 ($KEY)"
fi

# 10b 真打一次上游：配置齐全 ≠ 密钥有效（2026-11-13 实测：上面全绿，实际回 401）
# ★ 探针必须用「模型实际生效的凭据」，不是 env 里那把：pi 解析优先 models.json 的
#   providers.<id>.apiKey、env 兜底（实测：改 models.json 后 env 仍是旧 key，pi -p 已回正文）。
#   只看 env 会把「已修好」误报成 401 —— 那又是一条假红。
PROBE="$(docker exec "$CT" python3 -c '
import json, os, urllib.request
key = ""
try:
    d = json.load(open("/home/ctf/.pi-ctf/agent/models.json"))
    k = ((d.get("providers") or {}).get("deepseek") or {}).get("apiKey") or ""
    if k and not k.startswith("$"):
        key = k.strip()
except Exception:
    pass
key = key or os.environ.get("DEEPSEEK_API_KEY", "")
if not key:
    print("no-key"); raise SystemExit
base = (os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
req = urllib.request.Request(base + "/models", headers={"Authorization": "Bearer " + key})
try:
    with urllib.request.urlopen(req, timeout=20) as r:
        print("http=%d" % r.status)
except Exception as e:
    code = getattr(e, "code", None)
    print("http=%s" % (code if code else "ERR"))
' 2>/dev/null | tr -d '\r')"
case "$PROBE" in
  "http=200") ok "上游密钥实测可用 (GET $PROBE)" ;;
  "no-key")   bad "容器内 DEEPSEEK_API_KEY 为空 —— 检查 .env 是否真被 compose 注入" ;;
  *)          bad "上游密钥实测失败 ($PROBE) —— 换 key，别信 hasKey" ;;
esac

# 11 密钥未落镜像/容器
L1="$(docker history "$IMG" --no-trunc 2>/dev/null | grep -cE 'sk-[0-9a-fA-F]{16,}' || true)"
# ★ 2026-11-13：容器内扫描要排除**凭据存储本身**。
#   WebUI 的「添加模型提供商」就是把 apiKey 写进 <agent>/models.json（产品设计如此），
#   auth.json 同理。这两处有 key 是正常状态，不算「明文密钥泄漏」。
#   真正要守的是：镜像层不能有（L1）、仓库树/其余运行目录不能有。
L2="$(docker exec "$CT" bash -lc 'grep -rlE "sk-[0-9a-fA-F]{16,}" /home/ctf/.pi-ctf /opt/pi-ctf 2>/dev/null | grep -vE "/(models|auth)\.json$" | wc -l' 2>/dev/null | tr -d '\r')"
if [ "${L1:-0}" -eq 0 ] && [ "${L2:-0}" -eq 0 ]; then
  ok "镜像层/容器内无明文密钥"
else
  bad "发现明文密钥（镜像层 $L1 处 / 容器内 $L2 个文件）"
fi

# 12 旧路径残留
OLD="$(docker exec "$CT" bash -lc 'grep -rl "/mnt/d/AI/pi-lab" /opt/pi-ctf/tools /opt/pi-ctf/skills /opt/pi-ctf/board /opt/pi-ctf/work /opt/pi-ctf/prompts /opt/pi-ctf/agent-template /opt/pi-ctf/docker 2>/dev/null | wc -l' 2>/dev/null | tr -d '\r')"
if [ "${OLD:-1}" -eq 0 ]; then ok "镜像内无旧路径 /mnt/d/AI/pi-lab"; else bad "镜像内仍有 $OLD 个文件含旧路径"; fi

# 13 运行期目录
DIRS="$(docker exec "$CT" bash -lc 'for d in /opt/pi-ctf/workspace /opt/pi-ctf/board/projects /home/ctf/.pi-ctf/agent /home/ctf/.pi-ctf/sessions; do [ -d "$d" ] || echo "MISS:$d"; done' 2>/dev/null | tr -d '\r')"
if [ -z "$DIRS" ]; then ok "运行期目录齐备（workspace / board.projects / agent / sessions）"; else bad "目录缺失：$DIRS"; fi

# 14 bind mount 可写性（Docker Desktop 会把挂在容器里的宿主目录重建成 root:root，
#    ctf 用户就写不进去 —— 2026-11-01 实战后审计发现的真故障：黑板建不了项目、题面落不了盘）
#   注意必须 -u ctf：镜像 USER 已是 root（入口负责降权），用默认 root 跑这个检查会假绿。
WR="$(docker exec -u ctf "$CT" bash -lc 'for d in /opt/pi-ctf/workspace /opt/pi-ctf/board/projects /opt/pi-ctf/work/intake /opt/pi-ctf/log; do
  f="$d/.wtest-$RANDOM"
  if ( : > "$f" ) 2>/dev/null; then rm -f "$f"; else echo "NOWRITE:$d"; fi
done' 2>/dev/null | tr -d '\r')"
if [ -z "$WR" ]; then
  ok "可写目录齐备（workspace / board.projects / work.intake / log，以 ctf 身份实测）"
else
  bad "不可写：$WR（入口的属主修正没生效，查 docker logs $CT | head -20）"
fi

# 15 可选：全部回归（条数从现场数，不写死 —— 写死过一次 39，现场早就是 80+ 了）
NCASE="$(docker exec "$CT" bash -lc 'ls /opt/pi-ctf/board/tests/test_*.py 2>/dev/null | wc -l' 2>/dev/null | tr -d '\r')"
NCASE="${NCASE:-0}"
if [ "$FULL" -eq 1 ]; then
  echo "  ... 正在跑 $NCASE 条回归（几分钟）"
  # 用唯一文件名 + stdin 写入容器（docker cp 会留下 600 root 文件导致 Permission denied）
  RUNNER="/tmp/reg-one-$BASHPID.sh"
  docker exec -i "$CT" bash -c "cat > $RUNNER && chmod 755 $RUNNER" <<'EOS'
#!/bin/bash
timeout 900 python3 "$1" > /tmp/reg-one.log 2>&1
echo "$1 $?"
EOS
  SUM="$(docker exec "$CT" bash -lc "cd /opt/pi-ctf && ls board/tests/test_*.py | xargs -P 6 -n1 bash $RUNNER" 2>/dev/null | tr -d '\r' | awk '{c[$2]++} END{printf "N=%d PASS=%d FAIL=%d SKIP=%d", NR, c["0"]+0, NR-(c["0"]+0)-(c["77"]+0), c["77"]+0}')"
  case "$SUM" in
    "N=$NCASE PASS=$NCASE FAIL=0 SKIP=0") ok "回归 $SUM（现场 $NCASE 条）" ;;
    *) bad "回归 $SUM（期望 N=$NCASE PASS=$NCASE FAIL=0 SKIP=0；条数与现场不符也会红，先看是不是没挂到新文件）" ;;
  esac
fi

# 16 可选：真实发消息（用唯一令牌，确认是本次新回复）
if [ "$E2E" -eq 1 ]; then
  TOKEN="selftest-$(date +%s)-$RANDOM"
  R="$(curl -s -X POST "${URL}api/command" -H 'Content-Type: application/json' \
        -d "{\"type\":\"prompt\",\"message\":\"本次自检令牌 ${TOKEN}，请只回复四个字：部署成功\"}" 2>/dev/null)"
  case "$R" in
    *'"success":true'*) ok "端到端发消息已受理（令牌 $TOKEN）" ;;
    *)                  bad "端到端发消息失败"; info "$(echo "$R" | head -c 200)" ;;
  esac
  FOUND=0
  for _i in 1 2 3 4 5 6 7 8 9 10; do
    sleep 5
    if docker exec "$CT" bash -lc "grep -rq '$TOKEN' /home/ctf/.pi-ctf/sessions/ 2>/dev/null"; then FOUND=1; break; fi
  done
  if [ "$FOUND" -eq 1 ]; then ok "模型已真实回复并落盘（令牌可检索）"; else bad "50 秒内会话里没找到本次令牌回复"; fi

  # 16.5 联网工具对模型可见（决定性判据）：新建一条一次性会话，看 pi 发给模型的 system 段里
  #      有没有 web_search / fetch_content。注意 **不能用现役会话验**：pi-web-access 对「扩展安装前
  #      建立的旧会话」保留它原来的工具集（tool-activation.ts 的 transcript 声明逻辑），旧会话里
  #      既没有 web 工具也没有 web_enable，属预期行为，换新会话即可。
  VIS="$(docker exec -u ctf "$CT" bash -lc 'cd /opt/pi-ctf && timeout 100 pi --mode json -p --no-session --model deepseek-flash "只回复两个字：在" 2>/dev/null | head -c 20000' 2>/dev/null | tr -d '\r')"
  case "$VIS" in
    *"- web_search:"*"- fetch_content:"*) ok "联网工具对模型可见（新会话 system 段含 web_search / fetch_content）" ;;
    *) warn "新会话里没看到联网工具声明（静态检查见 8.5；手动开一个新会话再问）" ;;
  esac
fi

echo
printf '== 结论：PASS=%d FAIL=%d WARN=%d ==\n' "$PASS" "$FAIL" "${WARN:-0}"
[ "$FAIL" -eq 0 ] && echo "部署成功，可直接使用 $URL" || echo "存在失败项，按上面 FAIL 行排查（详见 docs/交付-故障处置.md）"
[ "$FAIL" -eq 0 ] || exit 1
