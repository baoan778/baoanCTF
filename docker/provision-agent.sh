#!/usr/bin/env bash
# =============================================================================
# provision-agent.sh —— 用**环境变量**生成 pi 的模型配置（密钥不落盘）
#
# 为什么不在镜像里写死 models.json：
#   apiKey 一旦写进被 COPY 的配置文件，就会留在镜像层里（docker history 可查）。
#   这里改成「首次启动时由 env 生成」，并且 apiKey 只写 **$DEEPSEEK_API_KEY 占位符**——
#   pi 自己在请求时从环境变量展开（docs/models.md: "$NAME 或 ${NAME} 环境插值"）。
#   于是：镜像里没有明文密钥，容器 inspect 也看不到明文（值在宿主 .env，由 compose 注入）。
#
# 幂等：两个文件都已存在就什么都不做（保留用户在界面里改过的配置）。
# =============================================================================
set -euo pipefail

# ★ 2026-11-18 修复：下面 ⑤ 段（pi-subagents chord 补丁）引用了 $ROOT，
#   但本脚本从没定义过它；脚本头是 set -euo pipefail（:15），于是 line 163 直接
#   "ROOT: unbound variable" 退出 → entrypoint 判 provision 失败 → 容器重启循环。
#   旧镜像里那份 /usr/local/bin/provision-agent.sh 是 11-01 的版本（还没有这段），
#   所以直到 2026-11-18 重建镜像才引爆。这里补上定义（与 entrypoint.sh:13 同语义）。
ROOT="${PI_LAB_ROOT:-/opt/pi-ctf}"

AGENT="${PI_CODING_AGENT_DIR:-$HOME/.pi-ctf/agent}"
PROVIDER="${PI_PROVIDER:-deepseek}"
MODEL="${PI_MODEL:-deepseek-flash}"
BASE_URL="${DEEPSEEK_BASE_URL:-https://api.deepseek.com}"
MODELS="$AGENT/models.json"
SETTINGS="$AGENT/settings.json"

log() { printf '[provision] %s\n' "$*"; }

# ★ 2026-10-30 实测修：模板里的 settings.json 会随 cp -a 一起铺过来，
#   若按「文件存在就跳过」处理，defaultProvider/defaultModel 永远写不进去 ——
#   pi 会退回内置目录默认（实测跑到 deepseek-v4-pro，与要求的 flash 不一致）。
#   所以改成**按字段**判断：models.json 缺才写；settings.json 缺 defaultModel 才补。
MODELS_NEED=0; SETTINGS_NEED=0
[ -f "$MODELS" ] || MODELS_NEED=1
grep -q '"defaultModel"' "$SETTINGS" 2>/dev/null || SETTINGS_NEED=1
if [ "$MODELS_NEED" = 0 ] && [ "$SETTINGS_NEED" = 0 ]; then
  log "models.json 与 settings.json 已就绪（含 defaultModel），保持原样"
  # 注意：不能在这里 exit —— 下面还要保证 pi-web-access 扩展就位（老卷升级时会缺）
fi

# 密钥只做「有没有」的检查，绝不回显；占位符让 pi 在请求时展开
if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
  log "⚠ 环境里没有 DEEPSEEK_API_KEY —— 配置照写，但发消息会报「没有可用认证」"
fi

mkdir -p "$AGENT"

if [ "$MODELS_NEED" = 1 ]; then
  cat > "$MODELS" <<JSON
{
  "providers": {
    "$PROVIDER": {
      "baseUrl": "$BASE_URL",
      "api": "openai-completions",
      "apiKey": "\$DEEPSEEK_API_KEY",
      "models": [
        {
          "id": "$MODEL",
          "name": "DeepSeek V4.1 Flash",
          "reasoning": true,
          "thinkingLevelMap": { "minimal": null, "low": "low", "medium": null, "high": "high", "max": "max" },
          "input": ["text", "image"],
          "contextWindow": 1048576,
          "maxTokens": 393216,
          "compat": {
            "supportsStore": false,
            "supportsDeveloperRole": false,
            "maxTokensField": "max_tokens",
            "requiresReasoningContentOnAssistantMessages": true,
            "thinkingFormat": "deepseek",
            "supportsStrictMode": true
          }
        }
      ]
    }
  }
}
JSON
  chmod 600 "$MODELS"
  log "已生成 $MODELS（apiKey=\$DEEPSEEK_API_KEY 占位符，明文不落盘）"
fi

if [ "$SETTINGS_NEED" = 1 ]; then
  # 保留 agent-template 里已有的设置项（theme / packages / subagents / compaction），
  # 只补默认通道；用 python 合并，避免手写 JSON 掉字段。
  python3 - "$SETTINGS" "$PROVIDER" "$MODEL" <<'PY'
import json, os, sys
path, provider, model = sys.argv[1], sys.argv[2], sys.argv[3]
data = {}
if os.path.exists(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        data = {}
data.setdefault("theme", "dark")
# ★ 2026-11-23 实测修（见 agent-template/settings.json 的 _note）：
#   contextWindow=1M 配默认 reserveTokens=16384 → 阈值 98 万，实测峰值仅 17 万 → 永不压缩。
#   这里显式给 reserveTokens，让阈值≈16 万。用 setdefault 保留用户已有设置。
data.setdefault("compaction", {"enabled": True, "reserveTokens": 840000,
                               "keepRecentTokens": 20000})
data["defaultProvider"] = provider
data["defaultModel"] = model
with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
os.chmod(path, 0o600)
print(f"[provision] 已写 {path}（defaultProvider={provider} defaultModel={model}）")
PY
fi

# ── pi-web-access（联网搜索 web_search / 抓网页 fetch_content）────────
# 镜像里已预装到 /opt/pi-ctf/vendor/pi-agent-npm（见 Dockerfile ⑧·6）；这里做三件事：
#   ① 在 agent/npm/node_modules 建软链 —— 这既是 pi 的扩展探测路径，也让 pi 跳过联网安装
#   ② 把 "npm:pi-web-access@0.46.0" 合并进 settings.packages（pi 只有声明了才会加载）
#   ③ 只打印状态，绝不回显任何 key（零配置即可用：内置 Exa MCP）
ensure_web_access() {
  VEND="${PI_LAB_ROOT:-/opt/pi-ctf}/vendor/pi-agent-npm/node_modules"
  BAKED="$VEND/pi-web-access"
  DEST="$AGENT/npm/node_modules/pi-web-access"
  mkdir -p "$AGENT/npm/node_modules"
  # 不能整包软链：pi 用 <扩展路径> 做 require 起点，Node 找不到 <软链>/node_modules，
  # 而扁平依赖（p-limit/undici…）在 vendor/node_modules 下 → 报 Cannot find module 'p-limit'。
  # 正确做法：真实目录 + 目录内 node_modules 指向 vendor 扁平树（等价 npm 的安装布局）。
  pkgver() { python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("version",""))' "$1" 2>/dev/null || true; }
  if [ ! -d "$BAKED" ]; then
    log "⚠ 未找到预装的 pi-web-access：首次用到时 pi 会自行 npm 安装（需联网）"
  else
    WANT="$(pkgver "$BAKED/package.json")"
    GOT="$(pkgver "$DEST/package.json")"
    if [ -L "$DEST" ] || [ "$GOT" != "$WANT" ]; then
      rm -f "$DEST"
      rm -rf "$DEST"
      mkdir -p "$DEST"
      cp -a "$BAKED/." "$DEST/"
      ln -sfn "$VEND" "$DEST/node_modules"
      log "已就位 pi-web-access ${WANT:-?}（离线预装，依赖树内联，pi 不会再去 npm 下载）"
    else
      log "pi-web-access 已就位（${GOT:-?}）"
    fi
  fi
  python3 - "$SETTINGS" <<'PY'
import json, os, sys
path = sys.argv[1]
data = {}
if os.path.exists(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        data = {}
pkgs = data.get("packages")
if not isinstance(pkgs, list):
    pkgs = []
def is_web_access(x):
    return isinstance(x, str) and x.split("@")[0] in ("npm:pi-web-access", "pi-web-access")
if not any(is_web_access(x) for x in pkgs):
    pkgs.append("npm:pi-web-access@0.46.0")
    print("[provision] 已把 npm:pi-web-access@0.46.0 加入 settings.packages")
data["packages"] = pkgs
with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
os.chmod(path, 0o600)
PY
}
ensure_web_access

# ★ 2026-11-14：修 pi-subagents 的 chord 门禁误判（pi 1.x 被当 0.x 判断）。
#   不修的话 subagent 工具全线不可用，agent 只能单线跑，速度砍半。
#   幂等（已打过就跳过）；失败只告警，不阻塞启动。
if [ -f "$ROOT/tools/patch-pi-subagents-chord.mjs" ] && command -v node >/dev/null 2>&1; then
  if node "$ROOT/tools/patch-pi-subagents-chord.mjs" 2>&1 | sed "s/^/[provision] /"; then
    : # ok
  else
    log "⚠ pi-subagents chord 补丁未打上（subagent 可能不可用，不阻塞启动）"
  fi
fi

exit 0
