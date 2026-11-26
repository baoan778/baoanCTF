// pi-lab UI 服务端 —— 一个进程两件事：
//   ① 用 RPC 模式驱动 pi（子进程），把 JSONL 事件转发给浏览器（SSE）
//   ② 只读地读黑板（board/projects/*/graph.json），给界面看
//
// 纪律（对齐 docs/经验-工程纪律.md）：
//   · 文件读取一律「先 stat 再限长」，超限直接拒绝（不 read_text 一个大文件进内存）
//   · JSONL 只用 LF 切分（不用 readline：U+2028/2029 会被它当行分隔）
//   · 磁盘读有 mtime 缓存 + 最小刷新间隔（/mnt/d 是 drvfs，小文件 5MB/s）
//
// 启动：node work/ui/server.mjs   （默认 127.0.0.1:30142）

import { spawn, spawnSync } from "node:child_process";
import { parseKickoff } from "../../tools/lanewatch.mjs";
import { createServer } from "node:http";
import { appendFile, mkdir, open, readFile, rename, stat, readdir, unlink, writeFile } from "node:fs/promises";
import { statSync, existsSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));

// 安全边界（2026-11-18 操作者定案：**口令闸门整条通路已删除**，不是关闭）
//
// 删掉的是「每次开界面先查口令再输」那一跳：开关键量、口令文件、两个鉴权端点、
// 前端口令框全没了，代码里不再有可以打开的开关 —— 少一个赛时故障点，也少一份
// 「以为开了其实没开」的错觉。
//
// 仍然生效的是**同源校验** sameOrigin()（见下面的 /api 写请求闸门）：零成本挡住
// 浏览器跨站写，不打扰任何人。外网进不来靠的是宿主端口映射绑 127.0.0.1:30142，
// 不是靠这里 —— 换到共享机/VPS 时**要换的是那条端口映射或前面加反代**，别再来加口令。
/**
 * 采集就绪分类（X-5：UI 的 X/Y 必须能算出来、能点开）。
 *
 * 只采**本机能机器判定**的四类；每类都给出条目级 {name, ok, why}，
 * 这样前端点开任一数字都能看到「缺的是哪几个、为什么缺」。
 * 刻意不在这里做"要不要拦住用户"的判断 —— 那是 readiness.mjs 的事。
 */
function readinessCategories() {
  const cats = [];

  // ① 外部工具（PATH 上真有）
  const toolNames = ["py", "poc", "kb", "fgs", "nmap", "ffuf", "jq"];
  cats.push({
    key: "tools", label: "外部工具",
    items: toolNames.map((n) => {
      const r = spawnSync("bash", ["-lc", "command -v " + n], { encoding: "utf8", timeout: 4000 });
      return { name: n, ok: r.status === 0 && !!(r.stdout || "").trim(), why: "PATH 上找不到" };
    }),
  });

  // ② Python 弹药（真 import，不是查文件）
  //   ★ 2026-11-07 实测修正：本仓的 CTF 库（pwn/z3/Crypto…）装在 py（venv/bin/python）里，
  //   系统 python3 没有。第一版探的是 python3 → 容器里 5 项全"缺"，
  //   把"环境齐备"误报成"环境残缺"。口径：优先 py，没有才回落 python3。
  const mods = ["pwn", "z3", "Crypto", "gmpy2", "sympy"];
  const whichPy = spawnSync("bash", ["-lc", "command -v py || command -v python3"],
    { encoding: "utf8", timeout: 6000 });
  const pyCmd = (whichPy.stdout || "").trim() || "python3";
  const pyProbe = spawnSync("bash", ["-lc",
    pyCmd + " -c 'import " + mods.join(",") + "' 2>&1"], { encoding: "utf8", timeout: 25000 });
  cats.push({
    key: "deps", label: "Python 弹药（" + pyCmd.replace(/^.*[\\/]/, "") + "）",
    items: mods.map((m) => ({
      name: m, ok: pyProbe.status === 0,
      why: "import 失败（检查 " + pyCmd + " 的 site-packages）",
    })),
  });

  // ③ 知识库索引（kb stats 能读 + 块数非零）
  const kb = spawnSync("bash", ["-lc", "kb stats 2>&1 | head -3"], { encoding: "utf8", timeout: 20000 });
  cats.push({
    key: "kb", label: "知识库索引",
    items: [{
      name: "kb 索引",
      ok: kb.status === 0 && /索引[：:]\s*\d+/.test(kb.stdout || ""),
      why: "跑 kb build（或检查 kb 目录是否挂载）",
    }],
  });

  // ④ 黑板与工作目录（fgs 能跑 + work/ 在）
  const fgs = spawnSync("bash", ["-lc", "fgs --help >/dev/null 2>&1"], { encoding: "utf8", timeout: 8000 });
  let workOk = false;
  try { workOk = statSync(path.join(ROOT, "work")).isDirectory(); } catch { workOk = false; }
  cats.push({
    key: "board", label: "黑板 / 工作目录",
    items: [
      { name: "fgs", ok: fgs.status === 0, why: "fgs 不在 PATH（tools/bin 没注入？）" },
      { name: "work/", ok: workOk, why: "work/ 不存在（留档目录）" },
    ],
  });

  return cats;
}

const ROOT = process.env.PI_LAB_ROOT || path.resolve(HERE, "../..");
const PORT = Number(process.env.UI_PORT || 30142);
const HOST = process.env.UI_HOST || "127.0.0.1";
const BOARD = path.join(ROOT, "board", "projects");
// ★ 会话目录兜底：容器里以非 root 运行（USER ctf），/root 不可写 → 写死 /root 会让
//   第一条消息就报 EACCES。改成 $HOME/.pi-ctf/sessions（entrypoint 会建好），
//   PI_SESSION_DIR 存在时仍以它为准（compose / start.sh 都设了）。
const FALLBACK_SESSION_DIR = process.env.PI_SESSION_DIR || path.join(homedir(), ".pi-ctf", "sessions");
let sessionDir = process.env.PI_SESSION_DIR || null;   // 真值从 get_state.sessionFile 学（pi 1.1 用项目级会话目录）
const sessionDirOf = () => sessionDir || FALLBACK_SESSION_DIR;
const DIST = path.join(HERE, "web", "dist");          // ZeroA 前端构建产物
// 早期自研单页 index.html 从未入库（/simple 曾因此 500）；现改为 301 到主线前端，常量一并删掉。
const MAX_BOARD_BYTES = 8 * 1024 * 1024; // 单个 graph.json 上限
const BOARD_MIN_REFRESH_MS = 3000;       // 黑板最短刷新间隔

const log = (...a) => console.log(new Date().toISOString().slice(11, 19), "[ui]", ...a);

// 项目根 .env（run-pi.sh 用 set -a 导出，这里等价地读进来）：
// 不读的话，本服务 spawn 的 pi 拿不到 AI98_API_KEY 这类 provider 凭据，模型会报「没有可用认证」。
async function loadDotEnv() {
  try {
    const raw = await readFile(path.join(ROOT, ".env"), "utf8");
    let n = 0;
    for (const line of raw.split("\n")) {
      const t = line.trim();
      if (!t || t.startsWith("#")) continue;
      const i = t.indexOf("=");
      if (i <= 0) continue;
      const k = t.slice(0, i).trim();
      let v = t.slice(i + 1).trim();
      if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) v = v.slice(1, -1);
      if (!(k in process.env)) { process.env[k] = v; n++; }
    }
    if (n) log(`已从 .env 载入 ${n} 个变量`);
  } catch { /* 没有 .env 就算了 */ }
}

// ---------------------------------------------------------------- 限长读文件
/** 限长读成 Buffer（给 /api/fs/raw 用；超过上限直接拒，不做截断以免坏文件） */
async function readFileCapped(file, maxBytes) {
  const st = await stat(file);
  if (!st.isFile()) throw new Error("不是普通文件");
  if (st.size > maxBytes) throw new Error(`文件过大（${st.size} > ${maxBytes}）`);
  return { buf: await readFile(file), size: st.size };
}
async function readCapped(file, maxBytes = MAX_BOARD_BYTES) {
  const st = await stat(file);
  if (!st.isFile()) throw new Error("not a regular file");
  if (st.size > maxBytes) throw new Error(`too large: ${st.size} > ${maxBytes}`);
  const buf = await readFile(file);
  return { text: buf.toString("utf8"), mtimeMs: st.mtimeMs, size: st.size };
}

// ---------------------------------------------------------------- 模型配置（设置 → 模型页）
/**
 * Pi 的 agent 配置目录，解析顺序与 pi 自己一致（getAgentDir）：
 *   PI_CODING_AGENT_DIR → 否则 ~/.pi/agent。
 * ★ 本服务通常由 run-pi 系脚本带着 PI_CODING_AGENT_DIR 启动（指向仓库 .pi/agent），
 *   所以设置页编辑的就是「正在跑的那些 pi 子进程读的那份配置」，不是另一个残留副本。
 */
const AGENT_DIR = process.env.PI_CODING_AGENT_DIR || path.join(homedir(), ".pi", "agent");
const MODELS_PATH = path.join(AGENT_DIR, "models.json");
/** pi 的登录凭据（/login 写入）；它是 models.json 之外的另一个密钥来源 */
const AUTH_PATH = path.join(AGENT_DIR, "auth.json");
const MODELS_BACKUP_DIR = path.join(AGENT_DIR, "backups");
/**
 * 项目设置（`.pi/settings.json`）：子智能体派发策略就住在这里的 `subagents` 段。
 * schema 以 pi-subagents 的 docs/configuration.md 为准，不是我们发明的。
 */
const SETTINGS_PATH = path.join(ROOT, ".pi", "settings.json");

/**
 * 扩展注册的**虚拟 provider**（模型对象的 `api === 'pi-virtual'`，见 extensions/ctf-models.ts）。
 *
 * 为什么要单独跟踪：虚拟模型不属于任何 provider，pi 那边“永远可用”，每个请求由扩展挑一个
 * 真实模型；它**没有密钥这个概念**。而 keyAvailability 只看 models.json / auth.json /
 * 环境变量 —— 于是 `ctf` 被误报成“未配置密钥”，界面上还会引导用户去填一个不存在的 key
 * （实测就发生过）。
 */
const virtualProviders = new Set();
let virtualProbeAt = 0;

/** 冷启动时补一次探测（/api/models 没被调过时缓存是空的）；失败就算了，只是少个标记 */
async function refreshVirtualProviders(rt) {
  if (!rt || Date.now() - virtualProbeAt < 5 * 60 * 1000) return;
  virtualProbeAt = Date.now();
  try {
    const rec = await rt.command({ type: "get_available_models" }, 8000);
    for (const m of rec?.data?.models || []) {
      if (m.api === "pi-virtual" && m.provider) virtualProviders.add(m.provider);
    }
  } catch { /* 拿不到就保持现状 */ }
}

/** 读 models.json（限长；不存在当空配置） */
async function readModelsConfig() {
  try {
    const { text } = await readCapped(MODELS_PATH, 1024 * 1024);
    const data = JSON.parse(text);
    if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("models.json 顶层不是对象");
    if (!data.providers || typeof data.providers !== "object" || Array.isArray(data.providers)) data.providers = {};
    return data;
  } catch (e) {
    if (e.code === "ENOENT") return { providers: {} };
    throw e;
  }
}

/**
 * 写 models.json：先留时间戳备份，再「临时文件 + rename」原子替换。
 * 串行化由 withModelsWrite 保证 —— 两个保存同时进来时，read-modify-write
 * 后写的那次会拿着读到的旧值覆盖前一次的结果（表现是「配置丢字段」）。
 */
let modelsWriteChain = Promise.resolve();
function withModelsWrite(fn) {
  const run = modelsWriteChain.then(fn, fn);   // 前一次失败也继续排队，互不连坐
  modelsWriteChain = run.then(() => {}, () => {});
  return run;
}
async function writeModelsConfig(data) {
  const next = JSON.stringify(data, null, 2) + "\n";
  await mkdir(MODELS_BACKUP_DIR, { recursive: true });
  try {
    const prev = await readCapped(MODELS_PATH, 1024 * 1024);
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    await writeFile(path.join(MODELS_BACKUP_DIR, `models.json-${stamp}.bak`), prev.text);
  } catch (e) { if (e.code !== "ENOENT") throw e; }
  const tmp = `${MODELS_PATH}.ui-tmp`;
  await writeFile(tmp, next);
  await rename(tmp, MODELS_PATH);
}

/** 读项目 settings.json（限长；不存在当空对象） */
async function readSettingsConfig() {
  try {
    const { text } = await readCapped(SETTINGS_PATH, 1024 * 1024);
    const data = JSON.parse(text);
    if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("settings.json 顶层不是对象");
    return data;
  } catch (e) {
    if (e.code === "ENOENT") return {};
    throw e;
  }
}

/**
 * 写 settings.json：时间戳备份 + 临时文件 rename 原子替换（与 models.json 同一套做法）。
 * 串行化是必需的：两个保存同时进来时，read-modify-write 后写的那次会拿着旧值覆盖
 * 前一次的结果（表现是「配置丢字段」）。
 */
let settingsWriteChain = Promise.resolve();
function withSettingsWrite(fn) {
  const run = settingsWriteChain.then(fn, fn);
  settingsWriteChain = run.then(() => {}, () => {});
  return run;
}
async function writeSettingsConfig(data) {
  const next = JSON.stringify(data, null, 2) + "\n";
  await mkdir(MODELS_BACKUP_DIR, { recursive: true });
  try {
    const prev = await readCapped(SETTINGS_PATH, 1024 * 1024);
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    await writeFile(path.join(MODELS_BACKUP_DIR, `settings.json-${stamp}.bak`), prev.text);
  } catch (e) { if (e.code !== "ENOENT") throw e; }
  const tmp = `${SETTINGS_PATH}.ui-tmp`;
  await writeFile(tmp, next);
  await rename(tmp, SETTINGS_PATH);
}

/**
 * models.json 里 apiKey 的三种写法（pi 的约定）：
 *   明文 key      → configured
 *   $VAR / ${VAR} → 看环境变量（仓库 .env 已由本服务载入 process.env，
 *                    与 pi 子进程看到的环境一致；命令 `!cmd` 取 token 的情况除外）
 *   !command      → 外部命令现场取（如 opencode-token）；不执行它，按可用算
 */
function apiKeyState(entry) {
  const raw = typeof entry?.apiKey === "string" ? entry.apiKey.trim() : "";
  if (!raw) return { state: "missing", source: null, env: null };
  if (raw.startsWith("!")) return { state: "configured", source: "command", env: null };
  if (raw.startsWith("$")) {
    const name = raw.slice(1).replace(/^\{/, "").split("}")[0].split(/[^A-Za-z0-9_]/)[0];
    if (name && process.env[name]) return { state: "configured", source: "env", env: name };
    return { state: "missing", source: "env", env: name || null };
  }
  return { state: "configured", source: "literal", env: null };
}

/**
 * 提供商对应的环境变量名（pi 的 providers.md 那张表）。
 * 为什么要列别名：`google` 的变量叫 GEMINI_API_KEY、`opencode-go` 叫 OPENCODE_API_KEY，
 * 光靠 id 大写推不出来；少了它们会把「env 里给了 key」误判成「没密钥」。
 */
const ENV_ALIAS = {
  google: "GEMINI_API_KEY",
  opencode: "OPENCODE_API_KEY",
  "opencode-go": "OPENCODE_API_KEY",
  "zai-coding-cn": "ZAI_CODING_CN_API_KEY",
  "ai-gateway": "AI_GATEWAY_API_KEY",
  nvidia: "NVIDIA_API_KEY",
  radius: "RADIUS_API_KEY",
  typesafe: "TYPESAFE_API_KEY",
};
function providerEnvNames(id) {
  const up = String(id || "").toUpperCase().replace(/[^A-Z0-9]+/g, "_");
  const names = new Set([`${up}_API_KEY`, `${up}_KEY`]);
  if (ENV_ALIAS[id]) names.add(ENV_ALIAS[id]);
  return [...names];
}

/**
 * 一个提供商到底有没有可用的凭据。
 *
 * pi 的解析顺序（docs/models.md）：`--api-key` → auth.json（/login 存的）
 * → models.json 的 apiKey → 提供商环境变量。
 * 只看 models.json 会把「登录过」或「env 里给了 key」的提供商显示成没密钥
 * —— 实测本机 deepseek 就是（凭据在 auth.json，models.json 里没有）。
 */
function keyAvailability(provider, entry, authCfg) {
  const st = apiKeyState(entry);
  const envName = providerEnvNames(provider).find((n) => process.env[n]) || null;
  const viaAuth = Boolean(authCfg && authCfg[provider]);
  const missingEnv = st.source === "env" && !st.configured ? st.env : null;
  return {
    hasKey: st.state === "configured",
    keySource: st.source,
    viaAuth,
    envKey: envName,
    usable: st.state === "configured" || viaAuth || Boolean(envName),
    keyHint: st.state === "configured"
      ? (st.source === "env" ? `环境变量 ${st.env}` : st.source === "command" ? "由外部命令现场取" : "已写入 models.json")
      : missingEnv ? `环境变量 ${missingEnv} 未设置`
        : viaAuth ? "已用 /login 登录（auth.json）"
          : envName ? `环境变量 ${envName}`
            : "没找到密钥：点「填密钥」，或在 pi 里 /login",
  };
}

/** pi 的内置提供商 id 索引（pi.dev 的公开目录）。缓存 24h；取不到退回手抄清单。 */
const CATALOG_CACHE = "/tmp/ui/pi-provider-catalog.json";
const CATALOG_FALLBACK = [
  "deepseek", "openai", "anthropic", "google", "xai", "groq", "mistral", "openrouter",
  "moonshotai", "moonshotai-cn", "kimi-coding", "zai", "minimax", "qwen-token-plan",
  "opencode", "opencode-go", "github-copilot", "together", "fireworks", "cerebras",
  "nvidia", "huggingface", "meta", "azure", "amazon-bedrock",
];
async function providerCatalog() {
  let cached = null;
  try { cached = JSON.parse(await readFile(CATALOG_CACHE, "utf8")); } catch { /* 没缓存就算 */ }
  if (cached?.ids?.length && Date.now() - (cached.at || 0) < 24 * 3600 * 1000) return cached;
  try {
    const res = await fetch("https://pi.dev/api/models/providers", {
      headers: { accept: "application/json" }, signal: AbortSignal.timeout(4000),
    });
    const ids = await res.json();
    if (Array.isArray(ids) && ids.length && ids.every(id => typeof id === "string")) {
      const fresh = { ids, at: Date.now(), source: "pi.dev" };
      await mkdir(path.dirname(CATALOG_CACHE), { recursive: true });
      await writeFile(CATALOG_CACHE, JSON.stringify(fresh));
      return fresh;
    }
  } catch { /* 离线/超时就用手里的 */ }
  if (cached?.ids?.length) return { ...cached, source: "cache" };
  return { ids: CATALOG_FALLBACK, at: 0, source: "fallback" };
}

// ---------------------------------------------------------------- 智能体（Roles）
// 与 ZeroA 同一套语义（前端就是它那套）：一个智能体 = <agentDir>/roles/<id>.json
// + prompts/<id>.md；因为 Pi 没有 set_system_prompt / set_tools / set_skills，
// 智能体的提示词与工具集只能进**启动参数**—— "切换智能体 = 用新参数重启该会话的进程"。
const ROLES_DIR = path.join(AGENT_DIR, "roles");
const ROLES_PROMPTS_DIR = path.join(ROLES_DIR, "prompts");
const ROLES_EXPORTS_DIR = path.join(AGENT_DIR, "exports");
// 出厂角色预设包：compose.yaml 只读挂进来（容器内 = work/ui/roles-pack 的同一份）。
// 「恢复默认」= 把这里的预设**补回** <agentDir>/roles（只增不改）。刻意不动已存在的同名角色：
// 用户手工调过（换模型/挪思考档/精简工具）的东西不能被一次点击悄悄覆盖。
const ROLES_PACK_DIR = process.env.PI_LAB_ROLES_PACK || path.join(ROOT, "work", "ui", "roles-pack");
const GUI_STATE_PATH = path.join(AGENT_DIR, "gui-state.json");
const ROLE_ID_RE = /^[a-z0-9_-]{1,32}$/;
const MAX_ROLE_PROMPT_BYTES = 24 * 1024;   // 与 ZeroA 一致
const ROLE_THINKING_LEVELS = ["off", "minimal", "low", "medium", "high", "xhigh", "max"];

/** 工作区键：与前端 stores/sessions.ts 的 workspaceKey 同口径（小写 + 正斜杠 + 去尾斜杠） */
const workspaceKeyOf = (p) => String(p || "").trim().replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();

/** 当前工作区：以活动会话的 cwd 为准（没有活动进程就用仓库根） */
const currentWorkspacePath = () => activeRuntime()?.opts?.cwd || ROOT;

/** 用户给的路径规范化：D:\x → /mnt/d/x；~/x → HOME；相对路径按仓库根（导入/导出用）
 *
 *  ★ 2026-11-12 安全加固：本函数接受**任意绝对路径**（导入/导出必须支持，用户要从
 *    桌面拖文件进来），所以不能简单限制在 ROOT 内。改为**敏感路径黑名单**：
 *    设备树 / 伪文件系统 / 凭证目录一律拒绝，其余放行。
 *    为什么必须拦：/dev/zero、/proc/self/mem 这类读下去会把进程拖死（同 board/fgs.py
 *    里 read_capped 的动机）；~/.ssh、.env 这类是凭证泄漏面。
 *    返回 "" 表示路径非法，调用方按「未提供」处理。 */
const _DENY_PREFIX = [
  "/dev/", "/proc/", "/sys/", "/run/", "/boot/",
  "/.dockerenv", "/var/run/",
];
const _DENY_SEGMENT = [
  "/.ssh/", "/.aws/", "/.gnupg/", "/.docker/", "/.kube/",
  "/.config/gh/", "/.netrc", "/.pgpass",
];
function _isDeniedPath(p) {
  const s = String(p || "");
  if (!s) return true;
  // 逐段规范化，防 /dev/../dev/zero 这类绕法
  const norm = path.posix.normalize(s.replace(/\\/g, "/"));
  if (norm.includes("\0")) return true;
  for (const pre of _DENY_PREFIX) if (norm === pre.slice(0, -1) || norm.startsWith(pre)) return true;
  for (const seg of _DENY_SEGMENT) if (norm === seg.slice(0, -1) || norm.includes(seg)) return true;
  // 凭证类文件名（.env / *.pem / *.key / id_rsa…）不给读写
  const base = path.posix.basename(norm);
  if (/^\.env($|\.)/.test(base)) return true;
  if (/\.(pem|key|p12|pfx|jks)$/i.test(base)) return true;
  if (/^id_(rsa|dsa|ecdsa|ed25519)$/.test(base)) return true;
  return false;
}
function userPathOf(raw) {
  const s = String(raw || "").trim();
  if (!s) return "";
  let out;
  const win = /^([A-Za-z]):[\\/](.*)$/.exec(s);
  if (win) out = `/mnt/${win[1].toLowerCase()}/${win[2].replace(/\\/g, "/")}`;
  else if (s === "~" || s.startsWith("~/")) out = path.join(homedir(), s.slice(1));
  else out = path.isAbsolute(s) ? path.normalize(s) : path.join(ROOT, s);
  if (_isDeniedPath(out)) {
    log(`path_denied raw=${s} resolved=${out}`);
    return "";
  }
  return out;
}

/** 智能体 id 规范化（挡路径穿越与大小写混乱） */
function roleIdOf(id) {
  const s = String(id || "").trim().toLowerCase();
  if (!ROLE_ID_RE.test(s)) throw new Error(`智能体 ID 只能用 1-32 位小写字母、数字、下划线和短横线：${JSON.stringify(id)}`);
  return s;
}

/** 提示词文件路径：只允许智能体目录内的相对路径（promptFile 来自磁盘，别让它变成任意文件读） */
function rolePromptPath(role) {
  const id = String(role?.id || "").trim().toLowerCase();
  const name = String(role?.promptFile || "").trim();
  if (!name) return ROLE_ID_RE.test(id) ? path.join(ROLES_PROMPTS_DIR, `${id}.md`) : "";
  if (path.isAbsolute(name)) { log(`role_prompt_absolute_rejected id=${id}`); return ""; }
  const clean = path.normalize(name).replace(/\\/g, "/");
  if (clean.startsWith("..")) { log(`role_prompt_escape_rejected id=${id}`); return ""; }
  return path.join(ROLES_DIR, clean.includes("/") ? clean : `prompts/${clean}`);
}

/** 读智能体的提示词（读不到就是空——提示词本来就是可选的） */
async function readRolePrompt(role) {
  const p = rolePromptPath(role);
  if (!p) return "";
  try { const { text } = await readCapped(p, MAX_ROLE_PROMPT_BYTES * 4); return text; } catch { return ""; }
}

/** 技能名 → 实际路径：绝对路径直接用，否则按仓库 skills/<id>/SKILL.md 解析 */
async function resolveSkillPath(name) {
  const raw = String(name ?? "").trim();
  if (!raw) return null;
  const asPath = path.isAbsolute(raw) || raw.startsWith("~") ? userPathOf(raw) : path.join(ROOT, "skills", raw, "SKILL.md");
  try { if ((await stat(asPath)).isFile()) return asPath; } catch { /* 再试试当目录 */ }
  try { const file = path.join(asPath, "SKILL.md"); if ((await stat(file)).isFile()) return file; } catch { /* 没有 */ }
  return null;
}

/**
 * 读全部智能体。与 ZeroA 同判：JSON 坏了、文件名与 id 不符都标 broken 并展示，
 * 而不是静默丢掉——"列表里少了一个智能体"比"标着配置损坏"难查得多。
 */
async function loadRoles() {
  let entries = [];
  try { entries = await readdir(ROLES_DIR, { withFileTypes: true }); } catch { return []; }
  const views = [];
  for (const entry of entries) {
    if (!entry.isFile() || !/\.json$/i.test(entry.name)) continue;
    const fileId = entry.name.replace(/\.json$/i, "").toLowerCase();
    const view = { id: fileId, name: fileId, prompt: "", broken: "" };
    let role = null;
    try {
      const { text } = await readCapped(path.join(ROLES_DIR, entry.name), 1024 * 1024);
      role = JSON.parse(text);
      if (!role || typeof role !== "object" || Array.isArray(role)) throw new Error("顶层不是 JSON 对象");
    } catch (e) {
      view.broken = `读不到配置：${e.message}`;
      views.push(view);
      continue;
    }
    Object.assign(view, role);
    view.id = String(role.id || fileId).trim().toLowerCase();
    view.name = String(role.name || "").trim() || view.id;
    if (!ROLE_ID_RE.test(view.id)) view.broken = `智能体 id 不合法：${JSON.stringify(role.id)}`;
    else if (view.id !== fileId) view.broken = `文件名 ${entry.name} 与配置里的 id ${view.id} 不一致（按文件名找不到、按 id 会改到别的文件），请修正后重新保存`;
    if (!view.broken) view.prompt = await readRolePrompt(view);
    views.push(view);
  }
  views.sort((a, b) => a.name.localeCompare(b.name));
  return views;
}

async function findRole(id) {
  const rid = roleIdOf(id);
  const views = await loadRoles();
  return views.find((v) => v.id === rid) || null;
}

/** 保存前校验：能在保存时挡住的，不要留给 pi 启动时炸 */
async function validateRoleBody(role) {
  const id = roleIdOf(role?.id);
  const name = String(role?.name || "").trim();
  if (!name) throw new Error("智能体名称不能为空");
  if ([...name].length > 60) throw new Error("智能体名称不能超过 60 个字符");
  const kind = String(role?.kind ?? "").trim().toLowerCase();
  if (kind && kind !== "role" && kind !== "agent") throw new Error(`未知的智能体类型 ${JSON.stringify(role.kind)}（可用：role/agent；留空 = 普通智能体）`);
  const level = String(role?.thinkingLevel ?? "").trim().toLowerCase();
  if (level && !ROLE_THINKING_LEVELS.includes(level)) throw new Error(`未知的思考级别 ${JSON.stringify(role.thinkingLevel)}（可用：${ROLE_THINKING_LEVELS.join("/")}）`);
  for (const key of ["tools", "excludeTools", "skills", "extensions", "promptTemplates", "scopedModels", "capabilities"]) {
    const v = role?.[key];
    if (v != null && !Array.isArray(v)) throw new Error(`${key} 必须是数组或留空`);
  }
  for (const tool of role.tools ?? []) if (!String(tool ?? "").trim()) throw new Error("工具白名单里有空项");
  const d = role?.delegation;
  if (d != null) {
    if (typeof d !== "object" || Array.isArray(d)) throw new Error("delegation 必须是对象");
    const oneOf = (value, allowed, label) => {
      const s = String(value ?? "").trim().toLowerCase();
      if (s && !allowed.includes(s)) throw new Error(`未知的${label} ${JSON.stringify(value)}（可用：${allowed.join("/")}）`);
    };
    oneOf(d.mode, ["background", "blocking"], "委派模式");
    oneOf(d.deliverOn, ["turn_end", "checkpoint"], "投递时机");
    oneOf(d.lifecycle, ["continuable", "one-shot"], "子智能体形态");
    for (const key of ["maxChildren", "maxParallel", "maxDurationSeconds", "idleTimeoutSeconds"]) {
      const n = d[key];
      if (n != null && (typeof n !== "number" || Number.isNaN(n) || n < 0)) throw new Error(`委派预算 ${key} 必须是 >= 0 的数字`);
    }
  }
  return id;
}

/** 保存智能体（定义 + 提示词）。提示词先落盘：中途失败不会留下"智能体在、提示词没了"的半成品 */
async function saveRole(role, prompt) {
  const id = await validateRoleBody(role);
  const promptText = String(prompt ?? "");
  const promptBytes = Buffer.byteLength(promptText, "utf8");
  if (promptBytes > MAX_ROLE_PROMPT_BYTES) throw new Error(`提示词太长（${promptBytes} 字节，上限 ${MAX_ROLE_PROMPT_BYTES}）`);
  // 技能预检：pi 只在启动时才发现技能不存在，那时表现为"智能体启动了但技能没加载"
  const missing = [];
  for (const skill of role.skills ?? []) if (!(await resolveSkillPath(skill))) missing.push(String(skill).trim());
  if (missing.length) {
    const candidates = (await listSkills()).map((s) => s.id).slice(0, 12);
    throw new Error(`找不到技能 ${missing.join("、")}${candidates.length ? `；当前可用的技能：${candidates.join("、")}` : ""}`);
  }
  // builtin 只在调用方明确给了才落盘：出厂预设里没有这个字段，写进去会让真身与
  // roles-pack/agent-template 逐字节比对永远有差异（漂移检查的信噪比就是这么丢的）。
  const next = { ...role, id, name: String(role.name).trim(), formatVersion: 1 };
  if (role?.builtin !== undefined) next.builtin = Boolean(role.builtin);
  if (!String(next.promptFile ?? "").trim()) next.promptFile = `prompts/${id}.md`;
  const promptPath = rolePromptPath(next);
  if (!promptPath) throw new Error("提示词路径不合法（只允许智能体目录内的相对路径）");
  await mkdir(ROLES_PROMPTS_DIR, { recursive: true });
  await writeFile(promptPath, promptText, "utf8");
  const tmp = path.join(ROLES_DIR, `.${id}.json.tmp`);
  await writeFile(tmp, JSON.stringify(next, null, 2) + "\n", "utf8");
  await rename(tmp, path.join(ROLES_DIR, `${id}.json`));
  return next;
}

/**
 * 恢复出厂角色：把 roles-pack 里的预设**补回** <agentDir>/roles。
 * 两种模式：
 *   · 默认（只增不改）—— 已存在的同名角色跳过，用户手工调过的不会被一次点击覆盖；
 *   · overwrite=true —— 同名角色用预设覆盖（界面上是带确认的第二档）。
 * 读不到预设包就如实报错：宁可点不动，也不要假装重置成功了。
 */
async function resetRolesFromPack({ overwrite = false } = {}) {
  let entries = [];
  try { entries = await readdir(ROLES_PACK_DIR, { withFileTypes: true }); } catch (e) {
    throw new Error(`读不到出厂角色包 ${ROLES_PACK_DIR}（${e.code || e.message}）`);
  }
  const restored = [];
  const skipped = [];
  const failed = [];
  for (const entry of entries) {
    if (!entry.isFile() || !/\.json$/i.test(entry.name)) continue;
    const fileId = entry.name.replace(/\.json$/i, "").toLowerCase();
    if (!ROLE_ID_RE.test(fileId)) { failed.push({ id: fileId, error: "预设文件名不是合法角色 id" }); continue; }
    try {
      const { text } = await readCapped(path.join(ROLES_PACK_DIR, entry.name), 1024 * 1024);
      const role = JSON.parse(text);
      if (!role || typeof role !== "object" || Array.isArray(role)) throw new Error("顶层不是 JSON 对象");
      if (!overwrite && (await findRole(fileId))) { skipped.push(fileId); continue; }
      let prompt = "";
      try { prompt = (await readCapped(path.join(ROLES_PACK_DIR, "prompts", `${fileId}.md`), MAX_ROLE_PROMPT_BYTES * 4)).text; } catch { /* 预设没有提示词就留空 */ }
      const saved = await saveRole({ ...role, id: fileId }, prompt);
      restored.push(saved.id);
    } catch (e) { failed.push({ id: fileId, error: e.message }); }
  }
  log(`roles_reset overwrite=${overwrite ? 1 : 0} restored=${restored.length} skipped=${skipped.length} failed=${failed.length}`);
  return { overwrite: Boolean(overwrite), restored, skipped, failed, packDir: ROLES_PACK_DIR };
}

/** 删智能体；顺便清掉 gui-state 里指向它的记忆（否则徽标显示一个不存在的智能体） */
async function deleteRole(id) {
  const rid = roleIdOf(id);
  const file = path.join(ROLES_DIR, `${rid}.json`);
  try { await stat(file); } catch { throw new Error(`智能体不存在：${rid}`); }
  await unlink(file);   // 先删元数据：删完列表里就没有了
  try { await unlink(path.join(ROLES_PROMPTS_DIR, `${rid}.md`)); } catch { /* 孤儿 .md 不影响使用 */ }
  await patchGuiState((state) => {
    for (const map of [state.roleBySession, state.lastRoleByWorkspace]) {
      for (const [key, value] of Object.entries(map)) if (value === rid) delete map[key];
    }
  });
}

/** gui-state.json：会话→智能体、工作区→智能体的记忆（与 ZeroA 同名同文件；只碰我们这两个键） */
async function readGuiState() {
  try {
    const { text } = await readCapped(GUI_STATE_PATH, 1024 * 1024);
    const data = JSON.parse(text);
    return data && typeof data === "object" && !Array.isArray(data) ? data : {};
  } catch { return {}; }
}
let guiStateChain = Promise.resolve();
function patchGuiState(mutate) {
  const run = guiStateChain.then(async () => {
    const state = await readGuiState();
    state.roleBySession = state.roleBySession && typeof state.roleBySession === "object" ? state.roleBySession : {};
    state.lastRoleByWorkspace = state.lastRoleByWorkspace && typeof state.lastRoleByWorkspace === "object" ? state.lastRoleByWorkspace : {};
    mutate(state);
    await mkdir(path.dirname(GUI_STATE_PATH), { recursive: true });
    const tmp = `${GUI_STATE_PATH}.tmp`;
    await writeFile(tmp, JSON.stringify(state, null, 2) + "\n", "utf8");
    await rename(tmp, GUI_STATE_PATH);
    return state;
  });
  guiStateChain = run.then(() => {}, () => {});
  return run;
}

// ---- 模型连通性健康（学习清单 #25）：只记真发过请求的结果；没记录就显示「—」，不冒充「正常」 ----
/** 测试失败时的「下一步」：先说清是什么病，再给一句可执行动作（与 board/schema.md 错误文案约定同一条纪律） */
function testFailNext(reason, status) {
  if (reason === "no-key") return "去 pi 的 /login 登录一次，或在上面「API 密钥」里粘贴后点保存";
  if (reason === "no-endpoint") return "pi 不认识这家的端点 → 切「自定义」把 API 地址填成 https://<host>/v1 再保存";
  if (reason === "opaque") return "这条的密钥由外部命令提供，界面不代跑 → 直接用 pi 发一句话验证";
  if (reason === "network") return "先确认本机能上外网 / 代理开着，再点一次测试；别改配置";
  if (status === 401 || status === 403) return "密钥无效或没权限（不是网络问题）：换对提供商的 key（Moonshot 与 kimi-coding 不通用），或重新登录";
  if (status === 404) return "端点上没有这个接口：多半 baseUrl/api 选错 → 「自定义」改回 https://<host>/v1";
  if (status === 429) return "被限流：等一分钟再点，或换同家另一个模型";
  if (status >= 500) return "对方服务端故障：不是你的配置问题，稍后重试";
  return "把 error 原文带上，对着 endpoint 逐字核对 baseUrl / api 类型";
}

async function noteModelFail(provider, reason, error, status) {
  if (!provider) return;
  try {
    await patchGuiState((state) => {
      const h = (state.modelHealth && typeof state.modelHealth === "object") ? state.modelHealth : {};
      const cur = (h[provider] && typeof h[provider] === "object") ? h[provider] : {};
      h[provider] = {
        fails: (Number(cur.fails) || 0) + 1,
        last_error: String(error || reason || "").slice(0, 300),
        last_reason: String(reason || (status ? "http-" + status : "")),
        last_fail_at: new Date().toISOString(),
        last_ok_at: cur.last_ok_at || null,
        last_ms: Number.isFinite(cur.last_ms) ? cur.last_ms : null,
      };
      state.modelHealth = h;
    });
  } catch { /* 健康记录写失败不该影响测试本身 */ }
}

async function noteModelOk(provider, ms) {
  if (!provider) return;
  try {
    await patchGuiState((state) => {
      const h = (state.modelHealth && typeof state.modelHealth === "object") ? state.modelHealth : {};
      const cur = (h[provider] && typeof h[provider] === "object") ? h[provider] : {};
      h[provider] = {
        fails: 0, last_error: "", last_reason: "",
        last_fail_at: cur.last_fail_at || null,
        last_ok_at: new Date().toISOString(),
        last_ms: Number.isFinite(ms) ? ms : null,
      };
      state.modelHealth = h;
    });
  } catch { /* 同上 */ }
}

// ---- 能力（capabilities）：智能体声明"要什么能力"，这里把它解析成本机实际有的工具 ----
// 对齐 ZeroA 的能力语义（只列确实存在的），探测方式取本机翻得动的：
// PATH 命令探测 + 扩展包探测（agentDir/npm/node_modules）。
// agentTool 是 pi 能**直接调用**的工具名（扩展提供的），会被并进智能体的工具白名单。
const CAPABILITY_CATALOG = [
  // 基础
  { id: "7zip", name: "7-Zip", capabilities: ["archive.extract"], commands: ["7z", "7za", "7zr"] },
  { id: "unzip", name: "unzip", capabilities: ["archive.extract"], commands: ["unzip"] },
  { id: "tar", name: "tar", capabilities: ["archive.extract"], commands: ["tar"] },
  { id: "bkcrack", name: "bkcrack", capabilities: ["archive.crack"], commands: ["bkcrack"] },
  { id: "bash", name: "bash", capabilities: ["shell.unix"], commands: ["bash", "sh"] },
  { id: "curl", name: "curl", capabilities: ["net.download"], commands: ["curl"] },
  { id: "wget", name: "wget", capabilities: ["net.download"], commands: ["wget"] },
  { id: "git", name: "git", capabilities: ["repo.clone", "filesystem.search"], commands: ["git"] },
  { id: "jq", name: "jq", capabilities: ["data.json"], commands: ["jq"] },
  { id: "node", name: "node", capabilities: ["script.run"], commands: ["node"] },
  { id: "python", name: "python", capabilities: ["script.run", "env.packages"], commands: ["python3", "py", "python"] },
  { id: "ripgrep", name: "ripgrep", capabilities: ["filesystem.search"], commands: ["rg"] },
  { id: "ffmpeg", name: "ffmpeg", capabilities: ["media.convert"], commands: ["ffmpeg"] },
  // 联网（扩展提供的 agent 工具；装了 pi-web-access 才有）
  { id: "web_search", name: "web_search（扩展）", capabilities: ["web.search"], extensions: ["pi-web-access"], agentTool: "web_search" },
  { id: "fetch_content", name: "fetch_content（扩展）", capabilities: ["web.fetch"], extensions: ["pi-web-access"], agentTool: "fetch_content" },
  // 逆向 / 二进制
  { id: "ghidra", name: "Ghidra", capabilities: ["reverse.static"], commands: ["ghidra"] },
  { id: "ida", name: "IDA Pro", capabilities: ["reverse.static"], commands: ["ida"] },
  { id: "pycdc", name: "pycdc / pycdas", capabilities: ["reverse.static", "binary.unpack"], commands: ["pycdc", "pycdas"] },
  { id: "pyinstxtractor", name: "pyinstxtractor", capabilities: ["binary.unpack"], commands: ["pyinstxtractor", "pyenv-check"] },
  { id: "wxapkg", name: "wxapkg（小程序解包）", capabilities: ["binary.unpack"], commands: ["wxapkg"] },
  // 利用 / 调试
  { id: "pggdb", name: "gdb + gef", capabilities: ["pwn.debug"], commands: ["pggdb"] },
  { id: "libcsearch", name: "libcsearch", capabilities: ["pwn.debug"], commands: ["libcsearch"] },
  { id: "py", name: "py（pwntools/z3/pycryptodome）", capabilities: ["pwn.debug", "crypto.tools"], commands: ["py"] },
  // 取证 / 杂项
  { id: "foremost", name: "foremost", capabilities: ["forensics.disk"], commands: ["foremost", "dumpall"] },
  { id: "volatility", name: "Volatility 3", capabilities: ["forensics.memory"], commands: ["vol", "volatility"] },
  { id: "pcapmap", name: "pcapmap", capabilities: ["forensics.pcap"], commands: ["pcapmap"] },
  { id: "f5", name: "F5 / jsteg / outguess", capabilities: ["stego.image"], commands: ["f5", "jsteg", "outguess"] },
  // Web / 扫描
  { id: "nuclei", name: "nuclei", capabilities: ["web.scan"], commands: ["nuclei"] },
  { id: "feroxbuster", name: "feroxbuster", capabilities: ["web.scan"], commands: ["feroxbuster", "dirsearch"] },
  { id: "httpx", name: "httpx / katana", capabilities: ["web.scan"], commands: ["httpx", "katana"] },
  { id: "wafw00f", name: "wafw00f", capabilities: ["web.scan"], commands: ["wafw00f", "graphw00f"] },
  { id: "xsstrike", name: "xsstrike / dalfox / tplmap / commix", capabilities: ["web.exploit"], commands: ["xsstrike", "dalfox", "tplmap", "commix", "ssrfmap", "jwt_tool"] },
  { id: "githack", name: "githack / svnexploit", capabilities: ["web.exploit"], commands: ["githack", "svnexploit"] },
  // 情报
  { id: "imgintel", name: "imgintel（图寻）", capabilities: ["osint.image"], commands: ["imgintel"] },
  { id: "gau", name: "gau / urlfinder", capabilities: ["osint.web"], commands: ["gau", "urlfinder"] },
  // ★ pi-lab 自带工具（2026-10-12 加）：不列在这里，智能体的 env.md 里就只会有 apt 装的通用工具，
  //   "本机有什么"没说全 —— 智能体会去猜命令名（实测就是这么撞出不存在的命令的）。
  //   只点名**我们自己的入口**；bash/curl/unzip 这类不必列（默认就在）。
  { id: "fgs", name: "fgs（黑板 Goal/Step/Fact）", capabilities: ["ctf.board"], commands: ["fgs"] },
  { id: "kb", name: "kb（本地知识库）", capabilities: ["ctf.kb"], commands: ["kb"] },
  { id: "poc", name: "poc / pocbomber（离线 PoC 1.1 万条）", capabilities: ["ctf.poc"], commands: ["poc", "pocbomber"] },
  { id: "revkit", name: "revkit（WASM / EVM / .NET 入口）", capabilities: ["reverse.static", "binary.unpack"], commands: ["revkit"] },
  { id: "rsactf", name: "rsactf（RsaCtfTool）", capabilities: ["crypto.tools"], commands: ["rsactf"] },
  { id: "binwalk", name: "binwalk", capabilities: ["binary.unpack", "forensics.disk"], commands: ["binwalk"] },
  { id: "apktool", name: "apktool", capabilities: ["binary.unpack"], commands: ["apktool"] },
  { id: "testdisk", name: "testdisk", capabilities: ["forensics.disk"], commands: ["testdisk"] },
  { id: "tcpflow", name: "tcpflow", capabilities: ["forensics.pcap"], commands: ["tcpflow"] },
  { id: "yara", name: "yara", capabilities: ["malware.scan"], commands: ["yara"] },
  { id: "toolreg", name: "toolreg（工具清单自检）", capabilities: ["env.tools"], commands: ["toolreg"] },
  { id: "sesscan", name: "sesscan（查会话 / 断点）", capabilities: ["ops.session"], commands: ["sesscan"] },
  { id: "lane_gate", name: "lane_gate（危险命令事前闸门）", capabilities: ["ops.safety"], commands: ["lane_gate", "lane_guard"] },
];
const CAPABILITY_LABELS = {
  "archive.extract": "解压 / 压缩",
  "archive.crack": "压缩包密码破解",
  "shell.unix": "Unix shell",
  "net.download": "下载",
  "repo.clone": "代码仓库",
  "data.json": "JSON 处理",
  "script.run": "脚本运行",
  "env.packages": "包管理",
  "filesystem.search": "文件检索",
  "media.convert": "音视频处理",
  "web.search": "联网搜索（工具）",
  "web.fetch": "网页抓取（工具）",
  "reverse.static": "逆向分析",
  "binary.unpack": "脱壳 / 解包",
  "pwn.debug": "调试 / 利用",
  "crypto.tools": "密码学计算",
  "forensics.disk": "磁盘取证",
  "forensics.memory": "内存取证",
  "forensics.pcap": "流量分析",
  "stego.image": "图片隐写",
  "web.scan": "Web 扫描 / 指纹",
  "web.exploit": "Web 漏洞利用",
  "osint.image": "图寻 / 图像情报",
  "osint.web": "网络情报",
  // ★ pi-lab 自家能力（与上面新增的工具对应）
  "ctf.board": "黑板編排（目标/步骤/事实）",
  "ctf.kb": "本地知识库检索",
  "ctf.poc": "离线 PoC / 指纹库",
  "malware.scan": "恶意样本规则匹配",
  "env.tools": "工具清单自检",
  "ops.session": "会话诊断",
  "ops.safety": "危险命令拦截",
};
const commandProbeCache = new Map();
function probeCommand(cmd) {
  if (commandProbeCache.has(cmd)) return commandProbeCache.get(cmd);
  let found = null;
  for (const dir of String(process.env.PATH || "").split(":")) {
    if (!dir || found) continue;
    for (const name of [cmd, `${cmd}.exe`, `${cmd}.cmd`, `${cmd}.bat`]) {
      try { if (statSync(path.join(dir, name)).isFile()) { found = path.join(dir, name); break; } } catch { /* 没有 */ }
    }
  }
  commandProbeCache.set(cmd, found);
  return found;
}

/** 扩展包探测：agentDir/npm/node_modules/<name>（pi-web-access 这类装的扩展） */
const extensionProbeCache = new Map();
// ---------------------------------------------------------------- 版本真值（不许再写死）
// ★ 2026-11-05：/api/defaults 原先把 piVersion 写死成 "1.1.0"，而容器与宿主实测都是 1.5.0
//   （`pi --version` → 1.5.0；npm latest 也是 1.5.0）。界面顶部那个徽标因此一直在说假话，
//   排障时会把「UI 说 1.1」误当成「底座没升级」——事实驱动第一条就是别让界面编数字。
//   现在真跑一次 `pi --version`，成功缓存 5 分钟；失败返回 "unknown"，绝不给假值。
const PI_VERSION_TTL_MS = 5 * 60 * 1000;
let piVersionCache = { value: null, at: 0 };
function piVersionReal() {
  const hit = piVersionCache.value;
  if (hit && Date.now() - piVersionCache.at < PI_VERSION_TTL_MS) return Promise.resolve(hit);
  return new Promise((resolve) => {
    let out = "";
    let done = false;
    const finish = (v) => {
      if (done) return;
      done = true;
      if (v && v !== "unknown") piVersionCache = { value: v, at: Date.now() };
      resolve(v || "unknown");
    };
    let child;
    try { child = spawn("pi", ["--version"], { stdio: ["ignore", "pipe", "ignore"] }); }
    catch { return finish("unknown"); }
    const timer = setTimeout(() => { try { child.kill("SIGKILL"); } catch { /* 已经死了 */ } finish("unknown"); }, 8000);
    child.stdout.on("data", (d) => { out += d.toString(); if (out.length > 2000) out = out.slice(0, 2000); });
    child.on("error", () => { clearTimeout(timer); finish("unknown"); });
    child.on("exit", () => {
      clearTimeout(timer);
      const m = /(\d+\.\d+\.\d+(?:[-+][\w.]+)?)/.exec(out);
      finish(m ? m[1] : "unknown");
    });
  });
}

function probeExtension(name) {
  if (extensionProbeCache.has(name)) return extensionProbeCache.get(name);
  const dir = path.join(AGENT_DIR, "npm", "node_modules", name);
  let found = null;
  try { if (statSync(dir).isDirectory()) found = dir; } catch { /* 没装 */ }
  extensionProbeCache.set(name, found);
  return found;
}

/** 一条目录项在本机的探测结果（命令优先，其次扩展包） */
function probeCatalogTool(tool) {
  for (const cmd of tool.commands ?? []) {
    const p = probeCommand(cmd);
    if (p) return { command: cmd, path: p };
  }
  for (const ext of tool.extensions ?? []) {
    const p = probeExtension(ext);
    if (p) return { command: ext, path: p };
  }
  return null;
}

/** 能力 → 本机工具解析（role.capabilities 的求值） */
function resolveCapabilities(capabilities) {
  const wanted = [...new Set((capabilities ?? []).map((c) => String(c || "").trim()).filter(Boolean))];
  const environment = { commands: {}, pythonPackages: [], applications: [], agentTools: [], missing: [] };
  const resolutions = [];
  for (const capability of wanted) {
    const available = [];
    const missing = [];
    for (const tool of CAPABILITY_CATALOG) {
      if (!tool.capabilities.includes(capability)) continue;
      const hit = probeCatalogTool(tool);
      if (hit) {
        available.push({ id: tool.id, name: tool.name, available: true, path: hit.path, agentTool: tool.agentTool });
        environment.commands[tool.name] = hit.path;
        if (tool.agentTool) environment.agentTools.push(tool.agentTool);
      } else {
        missing.push({ id: tool.id, name: tool.name, available: false });
      }
    }
    if (!available.length) environment.missing.push(capability);
    resolutions.push({ capability, available, missing });
  }
  return { resolutions, environment };
}

/** 能力目录 + 本机状态（智能体页「能力」页签用；也供将来的工具页） */
function capabilityCatalogPayload() {
  const capabilities = [...new Set(CAPABILITY_CATALOG.flatMap((t) => t.capabilities))].sort();
  const tools = CAPABILITY_CATALOG.map((tool) => {
    const hit = probeCatalogTool(tool);
    return { id: tool.id, name: tool.name, capabilities: tool.capabilities, available: !!hit, command: hit ? hit.command : "", path: hit ? hit.path : "", agentTool: tool.agentTool || "" };
  });
  return {
    capabilities,
    capLabels: Object.fromEntries(capabilities.map((c) => [c, CAPABILITY_LABELS[c] || c])),
    tools,
    available: tools.filter((t) => t.available).length,
    missing: tools.filter((t) => !t.available).length,
  };
}

/**
 * 智能体 → pi 启动参数（ZeroA roles.Compile 的 v1 子集）。
 * 只编译**本版真的能生效**的字段；其余（权限/循环/委派）保存但不假装生效，编出警告。
 */
async function compileRole(role) {
  const args = [];
  const warnings = [];
  const capabilities = Array.isArray(role.capabilities) ? role.capabilities : [];
  const effective = {
    tools: role.tools ?? null,
    excludeTools: [...(role.excludeTools ?? [])],
    skills: role.skills ?? null,
    model: role.model || "",
    thinking: String(role.thinkingLevel || "").trim().toLowerCase(),
    promptFiles: [],
  };
  // 提示词：默认追加（--append-system-prompt 吃文件路径）；replaceSystemPrompt 才整体替换
  const promptPath = rolePromptPath(role);
  if (promptPath) {
    try {
      if ((await stat(promptPath)).isFile()) {
        if (role.replaceSystemPrompt) {
          const { text } = await readCapped(promptPath, MAX_ROLE_PROMPT_BYTES * 4);
          if (text.trim()) args.push("--system-prompt", text);
        } else {
          args.push("--append-system-prompt", promptPath);
          effective.promptFiles.push(promptPath);
        }
      }
    } catch { /* 没有提示词文件 = 纯配置智能体，正常 */ }
  }
  // 能力 → 环境说明（只列本机真的有、且智能体声明了的能力对应的工具）+ 白名单补齐
  let resolutions = [];
  let environment = { commands: {}, pythonPackages: [], applications: [], agentTools: [], missing: [] };
  if (capabilities.length) {
    ({ resolutions, environment } = resolveCapabilities(capabilities));
    const lines = resolutions.filter((r) => r.available.length).map((r) => `- ${r.capability}：${r.available.map((t) => t.name).join("、")}`);
    if (lines.length || environment.missing.length) {
      const text = [
        "## 本机工具（按当前智能体声明的能力解析，只列确实存在的）",
        ...lines,
        environment.missing.length ? `\n声明了但本机没有对应工具的能力：${environment.missing.join("、")}` : "",
      ].filter(Boolean).join("\n");
      try {
        await mkdir(ROLES_PROMPTS_DIR, { recursive: true });
        const envPath = path.join(ROLES_PROMPTS_DIR, `${roleIdOf(role.id)}.env.md`);
        await writeFile(envPath, text + "\n", "utf8");
        args.push("--append-system-prompt", envPath);
        effective.promptFiles.push(envPath);
      } catch (e) { warnings.push(`环境说明写不进去：${e.message}`); }
    }
    for (const capability of environment.missing) warnings.push(`本机没有能提供能力 ${capability} 的工具（智能体仍可启动）`);
    // 能力解析出的 agent 工具并进白名单（仅当白名单是数组；null = 不限制，不需要补）
    if (Array.isArray(effective.tools)) {
      const merged = [...effective.tools];
      for (const resolution of resolutions) for (const tool of resolution.available) if (tool.agentTool && !merged.includes(tool.agentTool)) merged.push(tool.agentTool);
      effective.tools = merged;
    }
  }
  // 资源白名单：nil = 不干预；[] = 全禁；非空 = --no-xxx + 逐个点名
  if (role.skills != null) {
    args.push("--no-skills");
    const resolved = [];
    for (const skill of role.skills) {
      const p = await resolveSkillPath(skill);
      if (p) resolved.push(p); else warnings.push(`技能找不到，已跳过：${String(skill).trim()}`);
    }
    for (const p of resolved) args.push("--skill", p);
    effective.skills = resolved;
  }
  if (role.extensions != null) {
    args.push("--no-extensions");
    for (const item of role.extensions) { const v = String(item ?? "").trim(); if (v) args.push("--extension", v); }
  }
  if (role.promptTemplates != null) {
    args.push("--no-prompt-templates");
    for (const item of role.promptTemplates) { const v = String(item ?? "").trim(); if (v) args.push("--prompt-template", v); }
  }
  if (role.contextFiles === false) args.push("--no-context-files");
  if (Array.isArray(role.scopedModels) && role.scopedModels.length) args.push("--models", role.scopedModels.map((m) => String(m).trim()).filter(Boolean).join(","));
  // 工具白名单：null = 不干预（pi 默认四个工具）；[] = 全关；非空 = 只留列出的
  if (Array.isArray(effective.tools)) {
    if (effective.tools.length === 0) args.push("--no-tools");
    else args.push("--tools", effective.tools.map((t) => String(t).trim()).filter(Boolean).join(","));
  }
  if (effective.excludeTools.length) args.push("--exclude-tools", effective.excludeTools.map((t) => String(t).trim()).filter(Boolean).join(","));
  // 本版没接入的字段：明确说出来，别让用户以为配上了
  if (role.permissions && Object.keys(role.permissions).length) warnings.push("智能体权限策略本版未接入（保存但不生效）");
  if (role.loop) warnings.push("任务循环本版未接入（保存但不生效）");
  if (role.delegation?.enabled) warnings.push("委派配置本版只保存：子智能体由 pi-subagents（.pi/agents/*.md）管理");
  return { args, warnings, effective, capabilities, resolutions, environment };
}

/**
 * 会话该用哪个智能体：先看这条会话自己的记忆，再看工作区默认选中的智能体。
 */
async function roleIdForRuntime({ sessionPath = null, cwd = null } = {}) {
  const state = await readGuiState();
  if (sessionPath && state.roleBySession?.[sessionPath]) return state.roleBySession[sessionPath];
  return state.lastRoleByWorkspace?.[workspaceKeyOf(cwd || currentWorkspacePath())] || "";
}

/**
 * 智能体 id → 启动参数。编译失败一律退化成"没有智能体"并写日志：
 * 一个坏智能体不该让整个会话起不来。
 */
async function compiledRoleFor(roleId) {
  const raw = String(roleId || "").trim().toLowerCase();
  if (!raw) return { roleId: "", args: [], runtime: null, model: "", thinking: "" };
  try {
    const role = await findRole(raw);
    if (!role) { log(`role_missing id=${raw}，本次不带智能体启动`); return { roleId: "", args: [], runtime: null, model: "", thinking: "" }; }
    if (role.broken) throw new Error(role.broken);
    const compiled = await compileRole(role);
    for (const warning of compiled.warnings) log(`role_warn ${role.id}: ${warning}`);
    return {
      roleId: role.id,
      args: compiled.args,
      // 运行时配置：启动后用 RPC 对齐的部分（会话里记住的旧值可能与智能体声明不同）
      runtime: { autoCompaction: role.autoCompaction, thinking: compiled.effective.thinking, model: role.model || "" },
      model: role.model || "",
      thinking: String(role.thinkingLevel || "").trim().toLowerCase(),
    };
  } catch (e) {
    log(`role_compile_failed id=${raw}: ${e.message}`);
    return { roleId: "", args: [], runtime: null, model: "", thinking: "" };
  }
}

/** 导出智能体（自包含 JSON：定义 + 提示词都在里面） */
async function exportRoles(roleIds, requestedPath) {
  const wanted = new Set((roleIds ?? []).map((v) => String(v || "").trim().toLowerCase()).filter(Boolean));
  const views = await loadRoles();
  const entries = views
    .filter((v) => wanted.size === 0 || wanted.has(v.id))
    .map((v) => {
      const role = { ...v };
      delete role.prompt; delete role.broken;
      return { role, prompt: v.prompt || "" };
    });
  if (!entries.length) throw new Error("没有可导出的智能体");
  let target = userPathOf(requestedPath);
  if (!target) {
    const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    target = path.join(ROLES_EXPORTS_DIR, `roles-${stamp}.json`);
  } else if (!/\.json$/i.test(target)) target = `${target}.json`;
  const bundle = { format: "pi-lab-roles", version: 1, exportedAt: new Date().toISOString(), roles: entries };
  await mkdir(path.dirname(target), { recursive: true });
  await writeFile(target, JSON.stringify(bundle, null, 2) + "\n", "utf8");
  log(`roles_exported path=${target} count=${entries.length}`);
  return { path: target, count: entries.length, ids: entries.map((e) => e.role.id) };
}

/** 解析导入内容：导出包 / 单个智能体 / 智能体数组（与 ZeroA 的 ParseBundle 同口径） */
function parseRoleBundle(text, dir) {
  let data;
  try { data = JSON.parse(text); } catch (e) { throw new Error(`JSON 解析失败：${e.message}`); }
  const entries = [];
  if (data && Array.isArray(data.roles) && data.roles.length) {
    for (const item of data.roles) entries.push({ role: item?.role ?? item, prompt: String(item?.prompt ?? ""), promptDir: dir });
  } else if (Array.isArray(data) && data.length) {
    for (const role of data) entries.push({ role, prompt: "", promptDir: dir });
  } else if (data && typeof data === "object" && data.id !== undefined) {
    entries.push({ role: data, prompt: "", promptDir: dir });
  } else {
    throw new Error("不是智能体文件（既不是导出包，也不是智能体定义）");
  }
  return entries;
}

/** 导入智能体：文件或目录都行；同名覆盖 */
async function importRoles(source) {
  const src = userPathOf(source);
  if (!src) throw new Error("缺少导入路径");
  let st;
  try { st = await stat(src); } catch { throw new Error(`找不到：${src}`); }
  const entries = [];
  if (st.isDirectory()) {
    const files = (await readdir(src, { withFileTypes: true })).filter((e) => e.isFile() && /\.json$/i.test(e.name));
    if (!files.length) throw new Error("目录里没有智能体 JSON 文件");
    for (const f of files.slice(0, 100)) {
      const { text } = await readCapped(path.join(src, f.name), 2 * 1024 * 1024);
      entries.push(...parseRoleBundle(text, src));
    }
  } else {
    const { text } = await readCapped(src, 2 * 1024 * 1024);
    entries.push(...parseRoleBundle(text, path.dirname(src)));
  }
  const created = [];
  const overwritten = [];
  const failed = [];
  for (const entry of entries) {
    const id = String(entry.role?.id || "").trim().toLowerCase();
    try {
      let prompt = entry.prompt;
      if (!prompt && entry.promptDir && id) {
        try { prompt = await readCapped(path.join(entry.promptDir, "prompts", `${id}.md`), MAX_ROLE_PROMPT_BYTES * 4).then((r) => r.text); } catch { /* 没有提示词文件 */ }
      }
      const existed = id ? await findRole(id) : null;
      const saved = await saveRole({ ...entry.role, builtin: false }, prompt ?? "");
      (existed ? overwritten : created).push(saved.id);
    } catch (e) { failed.push({ id: id || "?", error: e.message }); }
  }
  log(`roles_imported created=${created.length} overwritten=${overwritten.length} failed=${failed.length}`);
  return { created, overwritten, failed };
}

// ---------------------------------------------------------------- pi RPC 子进程
// ★ 一个 runtime = 一个 pi 进程 = 一条会话（对齐 ZeroA 的"一会话一进程"）。
//   为什么必须这样：只有一个进程时，切会话 = 把正在跑的那条换掉，**上一轮的活会被打断**
//   （用户实测反馈）。一条会话一个进程，切过去只是换"跟哪个进程说话"，谁都不被打断。
class PiRpc {
  constructor({ runtimeId, sessionPath = null, cwd = null, model = null, thinking = null, roleId = "", roleArgs = [], roleRuntime = null } = {}) {
    this.child = null;
    this.buf = "";
    this.inflight = new Map();
    this.seq = 0;
    this.listeners = new Set();
    this.ready = false;
    this.restarts = 0;
    this.lastExit = null;
    this.intentionalStop = false;
    this.stopping = false;
    this.pendingTimer = null;
    this.startAfterExit = false;
    this.runtimeId = runtimeId || `rt-${Math.random().toString(16).slice(2, 10)}`;
    this.sessionPath = sessionPath;      // 空 = 新会话（pi 自己建文件）
    this.busy = false;                    // 正在跑回合（回收时要跳过）
    this.__nudgeLeft = undefined;         // #12 空转续跑剩余额度（agent_start 时按配置重置）
    this.lastUsed = Date.now();
    // roleId/roleArgs：智能体的启动参数（提示词 / 工具白名单 / 能力环境说明…），
    // 由 openRuntime / applyStart 编译好后存进来；start() 只负责把它们拼进命令行。
    this.opts = { cwd: cwd || ROOT, model, thinking, name: "ui", roleId, roleArgs, roleRuntime };
  }

  start() {
    if (this.child) return;
    // ★ 会话文件被删/移入 sessions/.trash 后，旧 runtime 仍钉着那条路径：
    //   pi --session <不存在的文件> 会立刻 exit 0（不是崩溃、不报错），UI 只会说「pi 未运行（等 20s 未起来）」，
    //   服务看着健康、实际无法对话。这里降级为新会话 —— 宁可开一条新会话，也不能让工作台变哑巴。
    if (this.sessionPath && !existsSync(this.sessionPath)) {
      log(`会话文件已不存在（${path.basename(this.sessionPath)}），改为新会话启动`);
      this.sessionPath = null;
    }
    if (this.pendingTimer) { clearTimeout(this.pendingTimer); this.pendingTimer = null; }   // 手动启动优先，取消待执行的重启
    const env = { ...process.env, PATH: `${ROOT}/tools/bin:${process.env.PATH || ""}` };
    // --session-dir 与 run-pi.sh 保持一致：终端与 UI 共用同一个会话目录，
    // 否则侧栏里看不到终端那边刚开的会话（两边各写各的，用户会以为"会话丢了"）。
    const sessDir = process.env.PI_UI_SESSION_DIR || FALLBACK_SESSION_DIR;
    const args = ["--mode", "rpc", "--session-dir", sessDir];
    // 指定会话：pi 直接挂到那条会话文件上（这是"一会话一进程"能成立的关键）
    if (this.sessionPath) args.push("--session", this.sessionPath);
    // 不传 --name：那会给每条新会话盖同一个名字，把"首条消息标题"顶掉
    if (this.opts.model) args.push("--model", this.opts.thinking ? `${this.opts.model}:${this.opts.thinking}` : this.opts.model);
    // 没有指定模型、但智能体定了思考级别时，单独传 --thinking（
    // 不加这句的话，"智能体只设 thinking"会被静默丢掉，pi 用默认级别跑）
    else if (this.opts.thinking) args.push("--thinking", this.opts.thinking);
    // 智能体参数：仅当编译产物存在时追加（顺序不影响 pi 的参数解析）
    if (Array.isArray(this.opts.roleArgs) && this.opts.roleArgs.length) args.push(...this.opts.roleArgs);
    const child = spawn("pi", args, { cwd: this.opts.cwd || ROOT, env, stdio: ["pipe", "pipe", "pipe"] });
    this.child = child;
    log(`pi 启动 pid=${child.pid} runtime=${this.runtimeId}${this.opts.roleId ? ` 智能体=${this.opts.roleId}` : ""}${this.sessionPath ? " 会话=" + path.basename(this.sessionPath) : " （新会话）"}`);

    child.stdout.on("data", (chunk) => {
      this.buf += chunk.toString("utf8");
      let i;
      // ★ 只在 LF 上切；容忍 CRLF；不把 U+2028/2029 当边界
      while ((i = this.buf.indexOf("\n")) >= 0) {
        let line = this.buf.slice(0, i);
        this.buf = this.buf.slice(i + 1);
        if (line.endsWith("\r")) line = line.slice(0, -1);
        if (!line.trim()) continue;
        let rec;
        try { rec = JSON.parse(line); } catch { this.emit({ type: "ui:parse_error", line: line.slice(0, 300) }); continue; }
        if (rec.type === "response") {
          /**
           * 智能体的运行时配置：等第一条 get_state 回来再下发——这是"进程肯定已经起来了"
           * 的最早可靠信号（发早了会拿到"请先启动 pi"）。只做一次；
           * applyStart 换参数时会把它重置。
           *
           * 为什么需要对齐而不是只靠启动参数：会话文件里记着上一次的模型/思考级别，
           * 恢复旧会话时它可能与智能体声明不一致（"智能体只生效了一半"就是这么来的）。
           * 这里发现不一致就用 RPC 改回来，让它一定收敛到智能体的声明值。
           */
          if (rec.command === "get_state" && this.opts.roleRuntime && !this.__roleRuntimeApplied) {
            this.__roleRuntimeApplied = true;
            const cfg = this.opts.roleRuntime;
            const state = rec.data || {};
            if (cfg.thinking && state.thinkingLevel && state.thinkingLevel !== cfg.thinking) {
              log(`role_runtime thinking ${state.thinkingLevel} → ${cfg.thinking}`);
              this.command({ type: "set_thinking_level", level: cfg.thinking }, 15000).catch(() => { /* 失败不影响会话 */ });
            }
            const wanted = String(cfg.model || "");
            if (wanted.includes("/") && state.model) {
              const [provider, ...rest] = wanted.split("/");
              const modelId = rest.join("/");
              if (state.model.provider !== provider || state.model.id !== modelId) {
                log(`role_runtime model ${state.model.provider}/${state.model.id} → ${wanted}`);
                this.command({ type: "set_model", provider, modelId }, 20000).catch(() => { /* 失败不影响会话 */ });
              }
            }
            const auto = cfg.autoCompaction;
            if (auto !== undefined && auto !== null) {
              this.command({ type: "set_auto_compaction", enabled: !!auto }, 15000).catch(() => { /* 失败不影响会话 */ });
            }
          }
          // 顺手学一下 pi 到底把会话存哪（1.1 是项目级目录：<cwd>/.pi/agent/sessions/--...--/）
          if (rec.command === "get_state" && typeof rec.data?.sessionFile === "string" && rec.data.sessionFile.includes("/")) {
            const dir = path.dirname(rec.data.sessionFile);
            if (dir !== sessionDir) { sessionDir = dir; log("会话目录 → " + dir); }
            // 新会话建出来后把 path → runtime 的映射补上，之后切回来才能复用同一个进程
            if (this.sessionPath !== rec.data.sessionFile) {
              this.sessionPath = rec.data.sessionFile;
              if (typeof bindSession === "function") bindSession(this.sessionPath, this);
            }
          }
          const cb = rec.id && this.inflight.get(rec.id);
          if (cb) { this.inflight.delete(rec.id); cb(rec); continue; }
        }
        if (rec.type === "agent_start") { this.busy = true; this.__nudgeLeft = emptyTurnNudgeLimit(); }
        else if (rec.type === "agent_settled") this.busy = false;
        this.noteEmptyTurn(rec);   // #12：判定必须在瘦身/转发之前（只有原始 turn_end 带 content 块与 toolResults）
        // 流式熔断：病态输出（空白洪流 / 巨型单条）在这一层拦下，不进转发也不进内存
        if (this.guardStream(rec)) continue;
        this.emit(rec);
      }
    });

    child.stderr.on("data", (d) => {
      const text = d.toString();
      // pi 的 stderr 是诊断，不是协议数据：只截断显示，不当事件
      this.emit({ type: "ui:stderr", text: text.slice(0, 600) });
    });

    child.on("exit", (code, signal) => {
      this.child = null;
      this.ready = false;
      this.stopping = false;
      this.lastExit = { code, signal, at: Date.now() };
      log(`pi 退出 code=${code} signal=${signal}`);
      for (const [, cb] of this.inflight) cb({ type: "response", success: false, error: `pi 进程退出 code=${code}` });
      this.inflight.clear();
      // 主动停的（前端 Stop / 换启动参数）：不发告警、也不自动拉起——下一步由前端说了算，
      // 否则会和它紧接着的 Start() 撞车（实测：撞车窗口里所有命令都是 502）
      if (this.intentionalStop) {
        this.intentionalStop = false;
        this.emit({ type: "ui:pi_stopped" });
        // 前端常见序列是 Stop() → Start() 连着发，中间隔不到一个事件循环：
        // 那时 applyStart 看到 child 还在，会以为不用起 —— 这里补一次。
        if (this.startAfterExit) { this.startAfterExit = false; this.restarts = 0; log("（Stop→Start 紧接：退出后立刻补启动）"); this.start(); }
        return;
      }
      this.emit({ type: "ui:pi_exit", code, signal });
      if (this.restarts < 3) {
        this.restarts++;
        const wait = 1000 * this.restarts;
        log(`自动重启（第 ${this.restarts} 次，${wait}ms 后）`);
        this.pendingTimer = setTimeout(() => { this.pendingTimer = null; this.start(); }, wait);
      } else {
        log("重启次数用尽，请检查 pi 是否可用");
      }
    });
  }

  // 每条事件带上"它属于哪个 runtime"：前端靠它把后台会话的输出归到那条会话的时间线
  // （ZeroA 的 Envelope.runtimeId 就是干这个的，前端早就有这套路由）
  //
  // ★ 转发前先瘦身（slimEvent）：pi 的流式 delta 带全量累积快照，不瘦身就是平方放大。
  /** #12 空转续跑：thinking-only 回合注入「直接执行下一步」，每轮总额度有限、工具回合后刷新。
   *
   * 三条边界（照 ARTEX 的口径，写成注释免得后来人改坏）：
   *   ① 额度是**每轮 agent 运行**的，agent_start 时重置；不是整场累计，也不是每回合各给一份。
   *   ② **工具回合后刷新**：真打了一次工具就不算空转，额度回满 —— 这样长任务不会被前面两次空转锁死。
   *   ③ 额度用完只提醒、不再注入：注入到第 N 次还是空转，说明问题不在「忘了动手」，再注入就是烧钱。
   */
  noteEmptyTurn(rec) {
    if (!rec || rec.type !== "turn_end") return;
    const limit = emptyTurnNudgeLimit();
    if (limit <= 0) return;
    const toolResults = Array.isArray(rec.toolResults) ? rec.toolResults.length : 0;
    if (!isEmptyTurn(rec)) {
      if (toolResults > 0) this.__nudgeLeft = limit;   // 边界②：真干了活 → 额度刷新
      return;
    }
    const left = Number.isFinite(this.__nudgeLeft) ? this.__nudgeLeft : limit;
    if (left <= 0) {
      this.emit({"type": "ui:stderr", "text": `空转续跑：这一回合又只有思考（无正文、无工具），本轮额度已用完（上限 ${limit} 次）——不再注入，交回给你判断这是不是死循环。`});
      return;
    }
    this.__nudgeLeft = left - 1;
    log(`空转续跑：thinking-only 回合 → 注入下一步指令（剩 ${this.__nudgeLeft}/${limit}）`);
    this.emit({"type": "ui:stderr", "text": `空转续跑：上一回合只有思考、没有正文与工具调用 → 已自动注入「直接执行下一步」（剩 ${this.__nudgeLeft}/${limit} 次）。不想被自动拉回就把 PI_SOLVE_EMPTY_TURN_NUDGES 设为负数。`});
    setImmediate(() => {
      this.command({"type": "prompt", "message": EMPTY_TURN_NUDGE}, 60000)
        .catch((e) => log("空转续跑注入失败：" + e.message));
    });
  }

  emit(rec) {
    const slim = slimEvent(rec);
    for (const fn of this.listeners) { try { fn({ ...slim, __rt: this.runtimeId }); } catch (e) { log("listener 出错", e.message); } }
  }

  /**
   * 流式熔断（Percho `stream-guard` 同款）：病态输出不许把界面/内存拖死。
   *
   * 两个累计量，只有"纯空白"与"同一条消息总量"两种判据（正常内容会清零 blank）：
   *   · 连续空白 delta 累计 > 8KB —— 纯 `\n` / 空格洪流永远不会变成有效内容
   *   · 同一条助手消息的 delta 累计 > 2MB —— 正常回答没有这个量级
   * 触发就 `abort` 这一回合，并**明说**是熔断干的（不静默停）；熔断后该消息剩下的 delta 一律丢。
   *
   * 为什么值得做：Percho 现场事故——模型输出病态空白 thinking，3 分钟写 12.7GB trace、
   * 无上限转发把渲染进程堆爆（PITFALLS 0.4.6）。我们同样是"转发模型流式事件的中间层"。
   */
  guardStream(rec) {
    const t = rec?.type;
    // 一条消息的生命周期结束就归零：判据是"单条消息/单次连续空白"，不是整场累计
    if (t === "message_start" || t === "message_end" || t === "agent_start" || t === "agent_settled" || t === "tool_execution_end") {
      this.__guard = t === "message_start" ? { blank: 0, total: 0, aborted: false } : null;
      return false;
    }
    if (t !== "message_update") return false;
    if (this.__guard?.aborted) return true;                       // 已熔断：剩下的 delta 直接丢弃
    const ame = rec.assistantMessageEvent;
    if (!ame || (ame.type !== "text_delta" && ame.type !== "thinking_delta")) return false;
    const delta = typeof ame.delta === "string" ? ame.delta : "";
    if (!delta) return false;
    const g = this.__guard || (this.__guard = { blank: 0, total: 0, aborted: false });
    g.total += delta.length;
    g.blank = delta.trim() ? 0 : g.blank + delta.length;
    const why = g.blank > BLANK_BURST_MAX
      ? `连续空白输出 ${g.blank}B`
      : g.total > MESSAGE_DELTA_MAX ? `单条消息流式输出 ${g.total}B` : "";
    if (!why) return false;
    g.aborted = true;
    log(`流式熔断：${why} —— 已 abort 这一回合`);
    this.command({ type: "abort" }, 15000).catch(() => { /* 进程没了就算了 */ });
    this.emit({ type: "ui:stderr", text: `流式熔断：${why}，已中止这一回合（防止把界面拖死）。上一回合内容保留。` });
    return true;
  }

  subscribe(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }

  /** 发命令，返回 promise（按 id 配对；命令处理是异步的，不能靠顺序） */
  async command(cmd, timeoutMs = 120000) {
    // pi 可能正在重启/停止的过渡中：等它安定下来再发，别把 502 掸回给前端
    const settle = async (attempt = 0) => {
      const deadline = Date.now() + 20000;
      while ((this.stopping || !this.child) && Date.now() < deadline) await new Promise((r) => setTimeout(r, 150));
      if (!this.child) throw new Error("pi 未运行（等 20s 未起来）");
      if (attempt > 0) await new Promise((r) => setTimeout(r, 300));   // 刚换完进程，给它一拍
    };
    await settle();
    const id = cmd.id || `ui-${++this.seq}`;
    const payload = { ...cmd, id };
    const send = () => new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.inflight.delete(id); reject(new Error(`命令超时: ${payload.type}`)); }, timeoutMs);
      this.inflight.set(id, (rec) => { clearTimeout(timer); resolve(rec); });
      this.child.stdin.write(JSON.stringify(payload) + "\n", (err) => {
        if (err) { clearTimeout(timer); this.inflight.delete(id); reject(err); }
      });
    });
    try { return { ...(await send()), runtimeId: this.runtimeId }; }
    catch (e) {
      // 写管道时刚碰上进程切换（EPIPE）：等一拍重试一次
      await settle(1);
      return { ...(await send()), runtimeId: this.runtimeId };
    }
  }

  stop(intentional = true) {
    if (this.pendingTimer) { clearTimeout(this.pendingTimer); this.pendingTimer = null; }
    if (this.child) { this.intentionalStop = intentional; this.stopping = true; try { this.child.stdin.end(); } catch {} }
  }

  /** 前端 Start(cwd, model, thinking, ...)：参数真的变了才重启（否则就是幂等） */
  /**
   * 用新参数重启（/api/start）。智能体也在这条路上：智能体决定提示词/工具/能力环境，
   * 只能进启动参数，所以"切智能体" = "带着新参数重启这个会话的进程"。
   * 编译智能体要读磁盘（提示词、技能路径、能力探测），所以这个函数是 async 的。
   */
  async applyStart(body = {}) {
    const normCwd = (p) => {
      if (!p) return null;
      const s = String(p).replace(/\\/g, "/");
      const win = /^([A-Za-z]):\//.exec(s);         // D:/x → /mnt/d/x（WSL interop 路径转换）
      return win ? `/mnt/${win[1].toLowerCase()}/${s.slice(3)}` : s;
    };
    const next = {
      cwd: normCwd(body.cwd) || this.opts.cwd || ROOT,
      model: body.model || null,
      thinking: body.thinkingLevel || body.thinking || null,
      name: body.name || this.opts.name,
      // 没传 roleId（= undefined）时沿用这个进程身上已有的智能体；传空串 = 显式去掉智能体
      roleId: body.roleId === undefined ? (this.opts.roleId || "") : String(body.roleId || "").trim().toLowerCase(),
      roleArgs: [],
      roleRuntime: null,
    };
    const compiled = await compiledRoleFor(next.roleId);
    next.roleId = compiled.roleId;
    next.roleArgs = compiled.args;
    next.roleRuntime = compiled.runtime;
    next.model = compiled.model || next.model;          // 智能体的模型/思考级别优先
    next.thinking = compiled.thinking || next.thinking;
    const realChange = next.cwd !== this.opts.cwd || next.model !== this.opts.model || next.thinking !== this.opts.thinking
      || next.roleId !== (this.opts.roleId || "")
      || JSON.stringify(next.roleArgs) !== JSON.stringify(this.opts.roleArgs || []);
    const prev = this.opts;
    this.opts = { ...next };
    this.__roleRuntimeApplied = false;                  // 新参数 → 运行时配置要重新下发
    if (realChange && this.child) { log("启动参数变了，重启 pi", JSON.stringify({ from: { model: prev.model, thinking: prev.thinking, role: prev.roleId }, to: { model: next.model, thinking: next.thinking, role: next.roleId } })); this.restarts = 0; this.stop(true); this.startAfterExit = true; }
    else if (this.stopping) { this.startAfterExit = true; }        // 退出事件还没到：标记退出后立刻起
    else if (!this.child) { this.restarts = 0; this.start(); }
    return realChange;
  }
}

await loadDotEnv();

// ---------------------------------------------------------------- runtime 池（一会话一进程）
const runtimes = new Map();        // runtimeId → PiRpc
const byPath = new Map();          // sessionPath → PiRpc
let activeId = null;
let runtimeSeq = 0;

/** 新会话建出文件后，把 path → runtime 的映射补上（在 PiRpc 里调） */
function bindSession(sessionPath, rt) {
  if (!sessionPath || !rt) return;
  byPath.set(sessionPath, rt);
  if (rt.runtimeId === activeId) rememberSession(sessionPath);   // 当前会话换了文件 → 记住新的
}

/**
 * 开一个会话（已开过就复用那个进程）。
 * 这是"切会话不打断"的关键：目标会话有自己的 pi 进程，切过去只是换"在跟谁说话"，
 * 正在跑的那条继续在自己进程里跑，事件也照旧流回来（前端按 runtimeId 归位）。
 *
 * 智能体：老会话用它自己记住的智能体（gui-state.json 的 roleBySession），
 * 新会话用当前工作区选中的智能体（lastRoleByWorkspace）——编译成 pi 启动参数。
 */
async function openRuntime({ sessionPath = null, cwd = null, model = null, thinking = null } = {}) {
  if (sessionPath && byPath.has(sessionPath)) {
    const rt = byPath.get(sessionPath);
    rt.lastUsed = Date.now();
    activeId = rt.runtimeId;
    rememberSession(sessionPath);
    broadcast({ type: "ui:active_runtime", runtimeId: rt.runtimeId });
    if (!rt.child) { rt.restarts = 0; rt.start(); }
    return rt;
  }
  let compiled = { roleId: "", args: [], runtime: null, model: "", thinking: "" };
  try {
    compiled = await compiledRoleFor(await roleIdForRuntime({ sessionPath, cwd }));
  } catch (e) { log(`role_lookup_failed: ${e.message}`); }
  const rt = new PiRpc({
    runtimeId: sessionPath ? `rt:${sessionPath}` : `rt-new-${++runtimeSeq}`,
    sessionPath, cwd,
    model: compiled.model || model,
    thinking: compiled.thinking || thinking,
    roleId: compiled.roleId,
    roleArgs: compiled.args,
    roleRuntime: compiled.runtime,
  });
  runtimes.set(rt.runtimeId, rt);
  if (sessionPath) { byPath.set(sessionPath, rt); rememberSession(sessionPath); }
  activeId = rt.runtimeId;
  broadcast({ type: "ui:active_runtime", runtimeId: rt.runtimeId });
  attach(rt);
  rt.start();
  // 新会话：起来后问一次 get_state，把 pi 建出来的会话文件绑到这条 runtime 上
  // （不绑的话：这条会话在 byPath 里查不到 → 下次点它又会另开一个进程）
  if (!sessionPath) {
    setTimeout(() => { rt.command({ type: "get_state" }, 30000).catch(() => { /* 起不来就算了，命中的是下次调用 */ }); }, 1500);
  }
  return rt;
}

const activeRuntime = () => (activeId ? runtimes.get(activeId) : null) || null;

/** 空闲回收：没在跑回合、且闲置超过 8 分钟的进程收掉（当前会话不杀；终端里那些不归我们管） */
const RUNTIME_IDLE_MS = 8 * 60 * 1000;   // 8 分钟（每个 pi 进程约 200MB，别攒）
const RUNTIME_MAX = 4;                   // 除超时外再兜一道「最多留几个」（见下）

/**
 * 真的把一条 runtime 收掉（超时回收与超量回收共用）。
 */
function reapRuntime(id, reason) {
  const rt = runtimes.get(id);
  if (!rt) return;
  log(`回收 runtime（${reason}）${id.slice(0, 40)}…`);
  rt.stop(true);
  runtimes.delete(id);
  if (rt.sessionPath) byPath.delete(rt.sessionPath);
}

/**
 * 两道闸：① 闲置超时；② 存活数上限（LRU 先杀）。
 *
 * 为什么要有 ②：只靠 8 分钟超时，"把侧栏会话挨个点一遍"就能轻松攒出十几个 pi 进程
 * —— 实测 9 个 = 1.4GB RSS（每个 155~176MB），在 8GB 的 WSL 里直接就是全机卡顿的来源。
 * 上限之内切会话仍然复用进程（不重新启动），超出才杀最久没用的。
 * 当前会话进程与正在跑回合的进程**永不**被杀。
 */
setInterval(() => {
  const now = Date.now();
  for (const [id, rt] of [...runtimes]) {
    if (id === activeId || rt.busy) continue;
    if (now - rt.lastUsed < RUNTIME_IDLE_MS) continue;
    reapRuntime(id, '空闲超时');
  }
  const idle = [...runtimes]
    .filter(([id, rt]) => id !== activeId && !rt.busy)
    .sort((a, b) => a[1].lastUsed - b[1].lastUsed);
  let over = runtimes.size - RUNTIME_MAX;
  for (const [id] of idle) {
    if (over <= 0) break;
    reapRuntime(id, `超出上限 ${RUNTIME_MAX}`);
    over -= 1;
  }
}, 60000).unref();

// 兼容层：老的接口（/api/status、/api/start、/api/stop、/api/command、/api/defaults）
// 把"当前会话进程"当 pi 用。
const pi = {
  get child() { return activeRuntime()?.child ?? null; },
  get restarts() { return activeRuntime()?.restarts ?? 0; },
  get lastExit() { return activeRuntime()?.lastExit ?? null; },
  command: (cmd, timeoutMs) => {
    const rt = activeRuntime();
    if (!rt) return Promise.resolve({ type: "response", command: cmd?.type, success: false, error: "没有活动会话进程" });
    return rt.command(cmd, timeoutMs);
  },
  applyStart: (body) => {
    const rt = activeRuntime();
    return rt ? rt.applyStart(body) : Promise.resolve(false);
  },
  stop: (intentional = true) => activeRuntime()?.stop(intentional),
  subscribe: (fn) => { runtimeSubs.add(fn); return () => runtimeSubs.delete(fn); },
};
const runtimeSubs = new Set();
function fanout(rec) { for (const fn of runtimeSubs) { try { fn(rec); } catch { /* 忽略 */ } } }

/** 每个 runtime 的事件都转给全局订阅者（SSE 广播） */
function attach(rt) {
  if (rt.__attached) return rt;
  rt.__attached = true;
  rt.subscribe((rec) => fanout(rec));
  return rt;
}
// 注意：第一条约会话进程**在 SSE 段之后**才开（见文件后面的 bootstrapRuntime()）——
// 提前开会用到还没初始化的 recent（TDZ），整个服务起不来（踩过）

// ---------------------------------------------------------------- 流式事件瘦身（Percho `event-slim` 同款）
/** 连续空白 delta 的容忍上限（超了就熔断，见 Runtime.guardStream） */
const BLANK_BURST_MAX = 8 * 1024;
/** 单条助手消息 delta 总量上限 */
const MESSAGE_DELTA_MAX = 2 * 1024 * 1024;

/** #12（2026-11-17，ARTEX server/engine.go:600-620 + llmretry.go 借鉴）：空转续跑的额度。
 *
 * 为什么：最常见的一种烧轮形态不是「报错」，而是「想完了不动手」—— 一回合只有思考、
 * 没有正文也没有任何工具调用。它看起来像正常回合（有产出、有 stopReason=end），于是没人管，
 * 下一轮又从零想一遍。注入一句「直接执行刚想好的下一步」通常就能把它拉回执行。
 *
 * 语义照 ARTEX：未设 / 0 = 默认 2；负数 = 关闭；>0 = 用该值。
 * 与「空响应重试」共用同一个键（那条走 SDK 层的无内容块原样重发，尚未在 baoan 落地；
 * 落地时直接调这个函数，别另起一个配置项 —— 用户心智是「没产出实质内容就再来一次」）。
 */
// #12-NUDGE-BEGIN
const EMPTY_TURN_NUDGE_DEFAULT = 2;
function emptyTurnNudgeLimit() {
  const raw = process.env.PI_SOLVE_EMPTY_TURN_NUDGES;
  if (raw === undefined || raw === null || String(raw).trim() === "") return EMPTY_TURN_NUDGE_DEFAULT;
  const n = Number.parseInt(String(raw).trim(), 10);
  if (!Number.isFinite(n)) return EMPTY_TURN_NUDGE_DEFAULT;
  if (n < 0) return 0;               // 负 = 关闭（显式关掉要比「设 0」明确，0 沿用默认）
  if (n === 0) return EMPTY_TURN_NUDGE_DEFAULT;
  return n;
}
const EMPTY_TURN_NUDGE = "【空转提醒】你上一回合只输出了思考，没有正文、也没有任何工具调用 —— 直接执行刚想好的下一步，不要重复思考。";
/** 这一回合是不是「空转」：有思考、但既没有正文也没有工具调用/工具结果。
 *  口径取 turn_end 的权威终态（message.content + toolResults），不靠流式增量猜 ——
 *  增量可能被熔断丢掉，终态不会。 */
function isEmptyTurn(rec) {
  if (!rec || rec.type !== "turn_end") return false;
  const msg = rec.message || {};
  const blocks = Array.isArray(msg.content) ? msg.content : [];
  const hasText = blocks.some((b) => b && b.type === "text" && String(b.text || "").trim() !== "");
  const hasToolCall = blocks.some((b) => b && (b.type === "toolCall" || b.type === "tool_use"));
  const toolResults = Array.isArray(rec.toolResults) ? rec.toolResults.length : 0;
  return !hasText && !hasToolCall && toolResults === 0;
}
// #12-NUDGE-END

/** usage 是不是全零（前端 applyUsage 对全零的做法是“直接忽略”，所以这类字段没有信息量） */
function isZeroUsage(u) {
  if (!u || typeof u !== "object") return true;
  return !(u.input || u.output || u.cacheRead || u.cacheWrite || u.totalTokens || u.cost?.total);
}

/**
 * 把要转发出去的记录瘦身。**只动"流式过程"事件，不动终态**。
 *
 * 为什么必须做：pi 的每条 `message_update` delta 都带**两份全量累积快照**
 * （`assistantMessageEvent.partial` + 顶层 `message`），流式期按**条数平方**放大：
 * 输出越长，每帧越大，总传输量 ~ 长度²。Percho 的现场事故（PITFALLS 0.4.6）：
 * 模型输出病态空白 thinking，3 分钟写了 12.7GB trace，无上限转发把渲染进程堆爆。
 * 我们这层（pi → server.mjs → SSE → 浏览器）同样是转发中间层，所以同样要"先瘦身再分发"。
 *
 * 白名单按**前端实际消费的字段**定（依据 web/src/rpc/router.ts 的 message_update 分支）：
 *   · text/thinking 的 start / delta / end：contentIndex + delta（end 还要 content）
 *   · toolcall_* 前端根本不消费（router 的 default 分支直接忽略，工具渲染走 tool_execution_*）
 *     → 连快照一起丢，只留一个类型名
 *   · 顶层 usage 得留：用量环与生成速度靠它（router 里 usageOf(event.usage)）
 * 终态不受影响：`message_start` / `message_end` 仍带权威全量消息（时间线最终内容取自它们）。
 */
function slimEvent(rec) {
  if (!rec || typeof rec !== "object") return rec;
  if (rec.type === "message_update") {
    const out = { type: "message_update" };
    // ★ 全零 usage 一律不带（实测：一次回答 469 帧 usage，其中 466 帧全零，占整条流 29% 字节）。
    //   前端 applyUsage 本来就忽略全零（runtime.ts 里写着“忽略全零只丢噪声”），所以这是纯白赚。
    if (rec.usage !== undefined && !isZeroUsage(rec.usage)) out.usage = rec.usage;
    const ame = rec.assistantMessageEvent;
    if (!ame || typeof ame !== "object") return out;
    const kind = ame.type;
    if (kind === "text_delta" || kind === "thinking_delta") {
      out.assistantMessageEvent = { type: kind, contentIndex: ame.contentIndex, delta: ame.delta };
    } else if (kind === "text_end" || kind === "thinking_end") {
      out.assistantMessageEvent = { type: kind, contentIndex: ame.contentIndex, content: ame.content };
    } else if (kind === "text_start" || kind === "thinking_start") {
      out.assistantMessageEvent = { type: kind, contentIndex: ame.contentIndex };
    } else {
      out.assistantMessageEvent = { type: typeof kind === "string" ? kind : "unknown" };
    }
    return out;
  }
  // ★ 2026-11-05（W-P1-2）：工具事件以前整条丢给前端，前端也不消费 —— 于是
  //   「这条 flag 是哪条命令打出来的」在界面上毫无痕迹，复现链断在第一环。
  //   现在按**配对所需的最小字段**瘦身后放行 start/end（update 仍然只留 touch，防大输出撑爆 SSE）：
  //     start: {toolCallId, toolName, args}          ← args 用来生成「命令」一行
  //     end:   {toolCallId, toolName, isError, durationMs, result(尾部 4KB)}
  //   result 尾部截断是硬要求：nmap/strings 的输出能有几十 MB，不截就是 Percho 那次 12.7GB 事故的翻版。
  if (rec.type === "tool_execution_start") {
    let args = "";
    try { args = typeof rec.args === "string" ? rec.args : JSON.stringify(rec.args ?? ""); } catch { args = ""; }
    return { type: "tool_execution_start", toolCallId: rec.toolCallId, toolName: rec.toolName, args: args.slice(0, 4000) };
  }
  if (rec.type === "tool_execution_end") {
    let out = "";
    try {
      const r = rec.result;
      const c = r && typeof r === "object" ? (r.content ?? r.output ?? r) : r;
      out = typeof c === "string" ? c : Array.isArray(c)
        ? c.map((x) => (typeof x === "string" ? x : x && typeof x.text === "string" ? x.text : "")).join("\n")
        : JSON.stringify(c ?? "");
    } catch { out = ""; }
    return {
      type: "tool_execution_end", toolCallId: rec.toolCallId, toolName: rec.toolName,
      isError: !!rec.isError, durationMs: rec.durationMs ?? null,
      result: out.length > 4096 ? "…（前略）\n" + out.slice(-4096) : out,
    };
  }
  if (rec.type === "tool_execution_update") {
    // 前端只拿它"碰一下"时间线（touchTool(toolCallId)），partialResult 没人读——
    // 而长时间命令的实时输出可能很大（实测就是 nmap/strings 那类），一律不进转发。
    return { type: "tool_execution_update", toolCallId: rec.toolCallId };
  }
  return rec;
}

// ---------------------------------------------------------------- SSE 广播
// ★ #17（2026-11-17，ARTEX server/server.go:2996-3072 借鉴）：断线自愈三件套
//   ① 每帧带自增 id（浏览器据此在重连时自动发 Last-Event-ID）
//   ② 握手时 Last-Event-ID **覆盖** ?since=（自动重连带的头才是「断在哪一条」的真相）
//   ③ 从环形缓冲按 id 补偿回放；被挤掉的那段必须自报（ui:gap），否则前端把「没收到」当「没发生」
const clients = new Set();
const recent = [];         // 给新连接的补偿回放（存 {id, rec}，按 id 过滤）
const RECENT_MAX = 300;    // 缓冲上限 = 单次补偿回放上限（超出部分只回最近这段并报 gap）
let eventSeq = 0;          // **只在这里 +1**：id 一旦重复或回退，客户端会静默丢帧

function broadcast(rec) {
  const id = ++eventSeq;
  recent.push({ id, rec });
  if (recent.length > RECENT_MAX) recent.splice(0, recent.length - RECENT_MAX);
  const line = `id: ${id}\ndata: ${JSON.stringify(rec)}\n\n`;
  for (const res of clients) { try { res.write(line); } catch {} }
}

// #17-REPLAY-BEGIN
// 纯逻辑：给定环形缓冲与 since，算出「该补发哪一段」。单独成段是为了能切出来直跑
// （board/tests/test_sse_replay.py）—— 这里最容易看着对、实际漏一条，而漏掉的
// 那条可能就是最后的空转帧，界面于是永远停在「正在想」。
function pickReplay(recent, since, limit) {
  if (typeof since !== "number" || !isFinite(since) || since < 0) {
    // 没带 since（首次连接 / 手动清过）：只回最近 120 条，够把当前状态铺出来
    return { items: recent.slice(-120), lost: 0, dropped: 0, truncated: false };
  }
  // since 比缓冲起点还早 → 中间那段已经被挤掉了，必须自报丢了多少
  const oldest = recent.length ? recent[0].id : 0;
  const lost = recent.length && since < oldest - 1 ? oldest - 1 - since : 0;
  let items = [];
  for (const e of recent) if (e.id > since) items.push(e);
  let dropped = 0;
  if (items.length > limit) { dropped = items.length - limit; items = items.slice(-limit); }
  return { items, lost, dropped, truncated: lost > 0 || dropped > 0 };
}
// #17-REPLAY-END
pi.subscribe(broadcast);

// 开第一条会话进程：优先回到"上次看的会话"（存在 work/ui/.last-session.json），
// 找不到/文件没了就开新会话 —— 服务重启后回到原来那条，不用手动点回去。
// ★ 2026-11-14 修 P2-9（实测）：原来落在 work/ui/ —— 而容器只挂载了 work/ui 下的
//   server.mjs 与 web/* 单文件，整个目录**不是**挂载点 → 容器一重建，指针就没了，
//   只能退到「最新会话」= 最新探活。改落到 agent 卷（容器外持久）。
const LAST_SESSION_FILE = process.env.PI_LAB_LAST_SESSION_FILE
  || path.join(process.env.PI_CODING_AGENT_DIR || path.join(HERE, ".agent-state"), "ui-last-session.json");
async function rememberSession(sessionPath) {
  if (!sessionPath) return;
  // ★ 必须确认**文件真的在**才记：pi 的 get_state 会先报一个"计划中的路径"，
  //   新会话要等第一条消息才落盘 —— 记了不存在的那条，重启时必然恢复失败（踩过）。
  try { await stat(sessionPath); } catch { return; }
  writeFile(LAST_SESSION_FILE, JSON.stringify({ sessionPath, at: Date.now() }), "utf8").catch(() => {});
}
// ★ 2026-11-14 修 P2-9（实测）：探活会话（"reply OK" 这类）会被当成「最新会话」，
//   UI 重启就恢复成一个只剩 reply OK 的空壳，操作者真正的会话反而接不上。
//   判据用**用户消息内容**，不用文件名/时间（探活随时可能再写一条）：
//   取每条会话的第一条 user 文本，若它短于 24 字且形如 OK/reply/ok/你好/在吗，判为探活。
function looksLikeProbeSession(fileLines) {
  const userTexts = [];
  for (const ln of fileLines) {
    let rec;
    try { rec = JSON.parse(ln); } catch { continue; }
    const msg = rec?.message;
    if (!msg || msg.role !== "user") continue;
    for (const part of Array.isArray(msg.content) ? msg.content : []) {
      if (part?.type === "text" && typeof part.text === "string") userTexts.push(part.text);
    }
    if (userTexts.length >= 3) break;
  }
  if (!userTexts.length) return true;                     // 一条用户消息都没有 = 空壳
  const first = userTexts[0].trim();
  if (first.length > 24) return false;
  return /^(ok|okay|reply(\s+(with\s+)?ok(\s+only)?)?|say\s+ok|ping|test|hi|hello|你好|在吗|测试)[\s!.,。！]*$/i.test(first);
}
async function readSessionLines(p, maxBytes = 256 * 1024) {
  try {
    const st = await stat(p);
    const fh = await open(p, "r");
    try {
      const start = Math.max(0, st.size - maxBytes);
      const buf = Buffer.alloc(st.size - start);
      await fh.read(buf, 0, buf.length, start);
      return buf.toString("utf8").split("\n").filter(Boolean);
    } finally { await fh.close(); }
  } catch { return []; }
}
async function newestSubstantiveSessionFile() {
  const dir = sessionDirOf();
  try {
    const files = (await readdir(dir)).filter((f) => f.endsWith(".jsonl"));
    const stats = [];
    for (const f of files) {
      const p = path.join(dir, f);
      try { stats.push([p, (await stat(p)).mtimeMs]); } catch { /* 跳过 */ }
    }
    stats.sort((a, b) => b[1] - a[1]);
    for (const [p] of stats.slice(0, 20)) {
      const lines = await readSessionLines(p);
      if (!looksLikeProbeSession(lines)) return p;
      log("跳过探活会话：" + path.basename(p));
    }
    return stats[0]?.[0] || null;   // 全是探活 → 至少给一条，别开空白
  } catch { return null; }
}

async function restoreOrNewRuntime() {
  let wanted = null;
  try { wanted = JSON.parse(await readFile(LAST_SESSION_FILE, "utf8"))?.sessionPath || null; } catch { /* 没有记录 */ }
  // ★ 2026-11-14：记录存在也要**再验一次内容** —— 老版本记下来的很可能就是探活会话，
  //   恢复它等于每次重启都对着一条 "reply OK" 的空壳。
  if (wanted) {
    try {
      await stat(wanted);
      const lines = await readSessionLines(wanted);
      if (looksLikeProbeSession(lines)) {
        log("上次记录是探活会话，跳过：" + path.basename(wanted));
      } else {
        log("恢复到上次的会话：" + path.basename(wanted));
        rememberSession(wanted);
        const rt = attach(await openRuntime({ sessionPath: wanted }));
        setTimeout(() => { rt.command({ type: "get_state" }, 30000).catch(() => {}); }, 1200);
        return rt;
      }
    } catch { log("上次那条记录的文件不在了，退回最新的一条会话"); }
  }
  // 兜底：拿会话目录里最新的那条（宁可"大概对"也别让用户重启后面对空白）
  const newest = await newestSubstantiveSessionFile();
  if (newest) {
    log("恢复到最近更新的会话：" + path.basename(newest));
    rememberSession(newest);
    const rt = attach(await openRuntime({ sessionPath: newest }));
    setTimeout(() => { rt.command({ type: "get_state" }, 30000).catch(() => {}); }, 1200);
    return rt;
  }
  log("没有可恢复的会话，开新会话");
  return attach(await openRuntime({ sessionPath: null }));
}
await restoreOrNewRuntime();

// ---------------------------------------------------------------- 黑板（带缓存）
const boardCache = new Map(); // pid -> {mtimeMs, size, data, fetchedAt}

function nodeCounts(nodes) {
  const byType = {}, byConf = {};
  for (const n of Object.values(nodes)) {
    byType[n.type] = (byType[n.type] || 0) + 1;
    if (n.type === "fact") byConf[n.confidence || "unverified"] = (byConf[n.confidence || "unverified"] || 0) + 1;
  }
  return { byType, byConf };
}

async function boardIndex() {
  let pids = [];
  try { pids = (await readdir(BOARD, { withFileTypes: true })).filter((d) => d.isDirectory()).map((d) => d.name); }
  catch { return { projects: [], error: "黑板目录读不到: " + BOARD }; }
  pids.sort();

  const out = [];
  for (const pid of pids) {
    const file = path.join(BOARD, pid, "graph.json");
    let st;
    try { st = await stat(file); } catch { continue; }
    const cached = boardCache.get(pid);
    if (!cached || cached.mtimeMs !== st.mtimeMs || cached.size !== st.size) {
      if (cached && Date.now() - cached.fetchedAt < BOARD_MIN_REFRESH_MS) {
        out.push(summary(pid, cached.data));
        continue;
      }
      try {
        const { text, mtimeMs, size } = await readCapped(file);
        const data = JSON.parse(text);
        boardCache.set(pid, { mtimeMs, size, data, fetchedAt: Date.now() });
        out.push(summary(pid, data));
      } catch (e) {
        out.push({ pid, error: e.message });
      }
    } else {
      out.push(summary(pid, cached.data));
    }
  }
  // 最近更新的排前面
  out.sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
  return { projects: out, ts: Date.now() };
}

// ---------------------------------------------------------------- 本轮 fgs 新能力的只读派生量
// 2026-11-11：boards 页此前只显示 counts/solved，本轮 7 项新能力里有 6 项界面上看不见 ——
// 赛时要么开终端敲命令，要么这些能力等于没做。这里补上，全部**从 graph.json 本地算**：
//   · 不 spawn fgs（boardIndex 会被轮询，起进程等于拿 CPU 换画质）
//   · 拿不到就返回 null，界面显示「不知道」—— 不用默认值假装有数

// 与 board/fgs.py 的 DEAD_RX 同源（死路判词）
const DEAD_RX = /(不行|无效|不通|失败|无回显|不可达|死路|排除|not\s+work|no\s+(?:way|effect)|dead\s+end|dead-end|blocked|ruled\s+out)/i;
// 与 board/fgs.py 的 PROBE_ELEMENTS 同源（探针三要素关键词，宽进）
const PROBE_KEYWORDS = {
  carry: ['cred','token','session','cookie','auth','jwt','凭据','身份','登录态','匿名','未登录','admin','root','user','账号','密码','key'],
  host_form: ['host','vhost','localhost','127.0.0.1','0.0.0.0','内网','反代','域名','容器名','端口','hosts','直连','ip','url','http://','https://'],
  submit_path: ['method','路径','direct','proxy','中介','包装','headers','body','query','params','get','post','put','patch','直提交','绕过','代理','param','payload','请求'],
};
// 与 board/fgs.py 的 SIMULATE_MARKERS 同源（替代环境推演自述）
const SIMULATE_MARKERS = ['模拟了','用脚本模拟','纸上推演','推演出','按文档应该','理论上应该','假设它','假设该','应该会','推测它','凭经验判断','没实测','未实测','没有真机','无法上真目标','本地复现'];
const DIMENSION_WORDS = {
  '对象': ['user','用户','admin','管理员','guest','匿名','其他账号','别人','另一台','另一个','other'],
  '时机': ['先','后','并发','同时','之前','之后','过期','重放','第二次','再提交','时序','race','竞争'],
  '状态': ['登录','未登录','已认证','无权限','禁用','锁定','空','满','异常','错误','崩溃','重启'],
  '顺序': ['顺序','调换','倒序','先删后加','先加后删','swap','顺序无关'],
  '通道': ['get','post','put','header','cookie','body','query','json','xml','multipart','websocket','grpc','通道'],
  '生命周期': ['创建','销毁','删除','替换','重置','过期','回收','create','destroy','replace','reset'],
};

/** 一条 fact 的探针三要素覆盖情况（宽进：命中任一关键词即算写了） */
function probeOf(n) {
  const meta = n.meta || {};
  const text = String(n.text || '').toLowerCase();
  const out = {};
  for (const [k, ws] of Object.entries(PROBE_KEYWORDS)) {
    out[k] = !!(String(meta[k] || '').trim()) || ws.some((w) => text.includes(w.toLowerCase()));
  }
  return out;
}

/** 证据来源：'local' / 'remote' / null（未声明） */
function originOf(n) {
  const v = String((n.meta || {}).origin || '').trim().toLowerCase();
  return (v === 'local' || v === 'remote') ? v : null;
}

/**
 * 从单块 graph 派生本轮新能力（纯函数，只读，不 spawn）。
 * 每项拿不到就返回 null / 空，由调用方决定怎么显示「不知道」。
 */
function newCapabilities(g) {
  const nodes = g.nodes || {};
  const facts = Object.entries(nodes).filter(([, n]) => n.type === 'fact');
  const verified = facts.filter(([, n]) => n.confidence === 'verified');

  // T9：死路缺探针三要素
  const deadUnverified = [];
  let deadComplete = 0;
  for (const [id, n] of facts) {
    if (n.confidence !== 'verified') continue;
    if (!DEAD_RX.test(String(n.text || ''))) continue;
    const pr = probeOf(n);
    const missing = Object.entries(pr).filter(([, v]) => !v).map(([k]) => ({ carry: '携带物', host_form: '宿主形态', submit_path: '提交路径' }[k]));
    if (missing.length) deadUnverified.push({ id, missing, claim: String(n.text || '').slice(0, 120) });
    else deadComplete += 1;
  }

  // T11：verified 但没标来源
  const undeclaredOrigin = [];
  let originDeclared = 0;
  for (const [id, n] of verified) {
    if (originOf(n)) originDeclared += 1;
    else undeclaredOrigin.push({ id, claim: String(n.text || '').slice(0, 110) });
  }

  // T14：自述推演却标了 remote / 未标（矛盾声明）
  const simulateConflict = [];
  for (const [id, n] of verified) {
    if (originOf(n) === 'local') continue;   // 如实标的，合规
    const txt = String(n.text || '');
    const hits = SIMULATE_MARKERS.filter((m) => txt.includes(m));
    if (hits.length) simulateConflict.push({ id, markers: hits.slice(0, 3), claim: txt.slice(0, 110) });
  }

  // T10：六维覆盖（哪些维度在图上从未出现）
  const blob = Object.values(nodes).map((n) => String(n.text || '') + ' ' + JSON.stringify(n.meta || {})).join(' ').toLowerCase();
  const dims = {};
  for (const [d, ws] of Object.entries(DIMENSION_WORDS)) dims[d] = ws.filter((w) => blob.includes(w.toLowerCase())).length;
  const dimsNever = Object.entries(dims).filter(([, c]) => c === 0).map(([d]) => d);

  // T16：开局定级
  const difficulty = String(g.difficulty || '').trim().toLowerCase() || null;

  return {
    difficulty,
    difficultyKnown: ['easy', 'medium', 'hard'].includes(difficulty),
    probe: { deadUnverified: deadUnverified.slice(0, 8), deadComplete, total: deadUnverified.length + deadComplete },
    origin: { undeclared: undeclaredOrigin.slice(0, 8), declared: originDeclared, total: undeclaredOrigin.length + originDeclared },
    simulate: { conflicts: simulateConflict.slice(0, 8), total: simulateConflict.length },
    dimensions: { counts: dims, never: dimsNever },
  };
}

function summary(pid, g) {
  const nodes = g.nodes || {};
  const { byType, byConf } = nodeCounts(nodes);
  const goal = Object.values(nodes).find((n) => n.type === "goal" && n.status !== "dropped");
  return {
    pid,
    kind: g.kind,
    revision: g.revision,
    updated_at: g.updated_at,
    created_at: g.created_at,
    goal: goal ? goal.text : null,
    goal_id: goal ? goal.id : null,
    counts: byType,
    facts: byConf,
    verified: byConf.verified || 0,
    solved: g.solved || null,
    confirmed: g.confirmed || null,
    bootstrapped: !!g.bootstrapped,
    nodes: Object.keys(nodes).length,
    edges: (g.edges || []).length,
    // ★ 本轮新能力（列表页要能一眼看出哪块板有盲区/没标来源/没定级）
    caps: capsBrief(g),
  };
}

/** 列表页用的摘要（详情页用 newCapabilities 全量） */
function capsBrief(g) {
  try {
    const c = newCapabilities(g);
    return {
      difficulty: c.difficulty,
      deadUnverified: c.probe.deadUnverified.length,
      undeclaredOrigin: c.origin.undeclared.length,
      simulateConflict: c.simulate.total,
      dimsNever: c.dimensions.never,
    };
  } catch { return null; }
}

// ---------------------------------------------------------------- 子代理留档与投递合并
/**
 * 读板前把 worker 的投递合进图（`fgs apply`）。
 *
 * 为什么必须有这一步：worker 只 `publish` 到 outbox，合入靠主会话手动 `fgs apply` ——
 * 会话一断 / 被压缩 / 换人，那些结论就永远躺在 outbox 里，**界面上看到的是一块空板**。
 * 现场实例（2026-10-13 实测）：`json-parser-ctfplus` 的 route-a 投了 3 条 verified
 * （死路证据 + DNS oracle + 版本指纹），两天没人 apply，graph.json 只剩一个目标节点。
 *
 * 安全边界：只跑 `apply`（它自带校验：不过的条目进 outbox-rejected，**不销毁**）；
 * 失败/超时只记日志，绝不让它挡住读板（读板是只读路径）。
 */
async function applyOutboxIfAny(pid) {
  const ob = path.join(BOARD, pid, "outbox.jsonl");
  let pending = 0;
  try {
    const st = await stat(ob);
    if (!st.size) return { pending: 0, applied: false };
    const text = await readFile(ob, "utf8");
    pending = text.split("\n").filter((l) => l.trim()).length;
  } catch { return { pending: 0, applied: false }; }
  if (!pending) return { pending: 0, applied: false };
  const env = { ...process.env, PATH: `${ROOT}/tools/bin:${process.env.PATH || ""}` };
  const r = await new Promise((resolve) => {
    const child = spawn("fgs", ["apply", pid], { cwd: ROOT, env, stdio: ["ignore", "pipe", "pipe"] });
    let out = "", err = "", done = false;
    const finish = (extra = {}) => {
      if (done) return; done = true;
      try { child.kill("SIGKILL"); } catch {}
      resolve({ out, err, ...extra });
    };
    const timer = setTimeout(() => finish({ timeout: true }), 20000);
    child.stdout.on("data", (d) => { out += d.toString(); });
    child.stderr.on("data", (d) => { err += d.toString(); });
    child.on("exit", (code) => { clearTimeout(timer); finish({ code }); });
    child.on("error", (e) => { clearTimeout(timer); finish({ code: -1, err: String(e.message) }); });
  });
  const applied = r.code === 0;
  log(`黑板 ${pid}：读板前合并 outbox（待合 ${pending}）→ ${applied ? "成功" : `未成功（${(r.err || "").slice(0, 120)}）`}`);
  return { pending, applied, output: (r.out || "").slice(0, 600) };
}

/**
 * 子代理留档：`work/<pid>/lanes/**` 的文件清单（路径 + 大小 + 首行摘要）。
 * 这是「断了还有方向」的**实物**：子智能体的探索过程与结论就算没投上板，也还在这些文件里，
 * 板上至少要把它们列出来（点得到、知道去哪翻）。
 */
async function collectLanes(pid) {
  const base = path.join(ROOT, "work", pid, "lanes");
  const out = [];
  const walk = async (dir, depth) => {
    if (depth > 2 || out.length >= 24) return;
    let entries = [];
    try { entries = await readdir(dir, { withFileTypes: true }); } catch { return; }
    for (const e of entries) {
      if (out.length >= 24) return;
      const full = path.join(dir, e.name);
      if (e.isDirectory()) { await walk(full, depth + 1); continue; }
      if (!/\.(md|txt|json|py|mjs|sh|log)$/i.test(e.name)) continue;
      let size = 0, snippet = "", mtimeMs = 0;
      try {
        const st = await stat(full);
        size = st.size; mtimeMs = st.mtimeMs;
        // 限长读：只看头 4KB，取第一行非空内容当摘要
        const fh = await open(full, "r");
        try {
          const buf = Buffer.alloc(Math.min(Number(size), 4096));
          const { bytesRead } = await fh.read(buf, 0, buf.length, 0);
          snippet = buf.subarray(0, bytesRead).toString("utf8").split("\n").find((l) => l.trim()) || "";
        } finally { await fh.close(); }
      } catch { continue; }
      out.push({ path: path.relative(ROOT, full), name: e.name, size, mtimeMs, snippet: snippet.trim().slice(0, 160) });
    }
  };
  await walk(base, 0);
  return out.sort((a, b) => b.mtimeMs - a.mtimeMs);
}

async function boardLaneText(pid, rel) {
  let relPath = String(rel || "").trim();
  if (!relPath) throw new Error("缺少 path");
  // 两种写法都收：① 仓库相对（work/<pid>/lanes/…）② 相对这道题的 lanes/（lane1.md）。
  // 不论哪种，**resolve 后**都必须真在 work/<pid>/lanes/ 里面（.. 穿越在这一步被挡住）。
  const underBase = path.resolve(ROOT, "work", pid, "lanes");
  const abs = [path.resolve(ROOT, relPath), path.resolve(underBase, relPath)]
    .find((c) => c === underBase || c.startsWith(underBase + path.sep));
  if (!abs) throw new Error("只允许看这道题的 lanes/ 下的文件");
  const cap = 64 * 1024;
  const st = await stat(abs);
  const fh = await open(abs, "r");
  let text = "";
  try {
    const n = Math.min(Number(st.size), cap);
    const buf = Buffer.alloc(n);
    const { bytesRead } = await fh.read(buf, 0, buf.length, 0);
    text = buf.subarray(0, bytesRead).toString("utf8");
  } finally { await fh.close(); }
  return { path: path.relative(ROOT, abs), size: st.size, truncated: st.size > cap, text };
}

async function boardDetail(pid) {
  if (!/^[\w.-]{1,64}$/.test(pid)) throw new Error("非法 project id");
  // ★ 先合 outbox（worker 的投递），再读图 —— 否则界面上看到的是「没有线索」的板
  const outbox = await applyOutboxIfAny(pid).catch(() => ({ pending: 0, applied: false }));
  const lanes = await collectLanes(pid).catch(() => []);
  const file = path.join(BOARD, pid, "graph.json");
  const { text, mtimeMs, size } = await readCapped(file);
  const g = JSON.parse(text);
  boardCache.set(pid, { mtimeMs, size, data: g, fetchedAt: Date.now() });
  // 最近日志（tail，限 40 行 / 64KB）
  let logLines = [];
  try {
    const lf = path.join(BOARD, pid, "log.jsonl");
    const st = await stat(lf);
    // ★ 只读尾部 64KB（file handle 定长读）：log.jsonl 会一直长，整读违背 drvfs 限长纪律。
    //   窗口可能从多字节字符/半行中间切开 → 丢掉第一个 \n 之前的半截行。
    const cap = Math.min(st.size, 65536);
    const fh = await open(lf, "r");
    let text = "";
    try {
      const buf = Buffer.alloc(Number(cap));
      const { bytesRead } = await fh.read(buf, 0, buf.length, Math.max(0, st.size - cap));
      text = buf.subarray(0, bytesRead).toString("utf8");
    } finally { await fh.close(); }
    if (st.size > cap) { const i = text.indexOf("\n"); if (i >= 0) text = text.slice(i + 1); }
    logLines = text.split("\n").filter(Boolean).slice(-40).map((l) => { try { return JSON.parse(l); } catch { return { raw: l.slice(0, 200) }; } });
  } catch {}
  // ★ 本轮新能力全量（详情页展示盲区/半场/定级/来源/推演/六维）
  let caps = null;
  try { caps = newCapabilities(g); } catch { caps = null; }
  return { pid, graph: g, log: logLines, mtimeMs, wp: await boardWpInfo(pid), outbox, lanes, caps };
}

// ---------------------------------------------------------------- WP（writeup）集成
// 约定路径 docs/writeups/WPs/<pid>_WP.md（见 AGENTS.md 的 WP 流程）。
// "自动存进对应题"就是这么做的：WP 一落盘，下一轮刷新黑板就能看到（无需手工登记）。
const WP_DIR = path.join(ROOT, "docs", "writeups", "WPs");

/** 找这道题的 WP：先试约定名，再前缀匹配（<pid>_WP_v2.md / 大小写差异都认）；找不到返回 null */
async function boardWpInfo(pid) {
  if (!/^[\w.-]{1,64}$/.test(pid)) return null;
  const candidates = [];
  for (const name of [`${pid}_WP.md`, `${pid}_wp.md`, `${pid}.md`]) candidates.push(path.join(WP_DIR, name));
  try {
    const lower = pid.toLowerCase();
    const entries = await readdir(WP_DIR, { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isFile() || !/\.md(own)?$/i.test(entry.name)) continue;
      if (entry.name.toLowerCase().startsWith(lower)) candidates.push(path.join(WP_DIR, entry.name));
    }
  } catch { /* 目录还不存在（还没写过 WP） */ }
  let best = null;
  for (const file of candidates) {
    try {
      const st = await stat(file);
      if (!st.isFile()) continue;
      if (!best || st.mtimeMs > best.mtimeMs) best = { path: file, name: path.basename(file), mtimeMs: st.mtimeMs, size: st.size };
    } catch { /* 试下一个候选 */ }
  }
  return best;
}

/** 读 WP 头部（限长 512KB：WP 是给人看的文档，不会大到哪去，但也不整读以防万一） */
async function readHead(file, maxBytes) {
  const fh = await open(file, "r");
  try {
    const buf = Buffer.alloc(maxBytes);
    const { bytesRead } = await fh.read(buf, 0, maxBytes, 0);
    return buf.subarray(0, bytesRead).toString("utf8");
  } finally { await fh.close(); }
}

async function boardWpText(pid) {
  const info = await boardWpInfo(pid);
  if (!info) return null;
  const text = await readHead(info.path, 512 * 1024);
  return { ...info, text, truncated: info.size > 512 * 1024 };
}

// ---------------------------------------------------------------- 会话小工具
/** 只读会话文件头 4KB 拿 cwd（切会话时前端要它来分组/高亮，别为这扫整个目录） */
async function sessionCwd(file) {
  try {
    const fh = await open(file, "r");
    try {
      const buf = Buffer.alloc(4096);
      const { bytesRead } = await fh.read(buf, 0, buf.length, 0);
      const first = buf.subarray(0, bytesRead).toString("utf8").split("\n")[0];
      const e = JSON.parse(first);
      return e?.cwd ? { cwd: e.cwd } : null;
    } finally { await fh.close(); }
  } catch { return null; }
}

// ---------------------------------------------------------------- 会话（左栏列表 + 历史）
// 会话文件格式：第 1 行 header（type/session/id/timestamp/cwd），其后每行一条 entry（message 等）
const sessionCache = new Map(); // file -> {mtimeMs, size, meta}

async function sessionMeta(file) {
  const st = await stat(file);
  const c = sessionCache.get(file);
  if (c && c.mtimeMs === st.mtimeMs && c.size === st.size) return c.meta;
  // ★ 头尾各读一段（都用 file handle 固定长度，**不是** readFile 整个文件 ——
  //   会话文件能长到 GB 级，整读会把内存打爆）：
  //   头部 → session header + 第一条用户消息（当标题）；
  //   尾部 → 最新的 session_info（**改名是追加在文件末尾的**，只扫头读不到）
  const fh = await open(file, "r");
  let head = "", tail = "";
  try {
    const headCap = Math.min(st.size, 524288);
    const hbuf = Buffer.alloc(Number(headCap));
    const hRead = (await fh.read(hbuf, 0, hbuf.length, 0)).bytesRead;
    head = hbuf.subarray(0, hRead).toString("utf8");
    const tailCap = Math.min(st.size, 65536);
    const tbuf = Buffer.alloc(Number(tailCap));
    const tRead = (await fh.read(tbuf, 0, tbuf.length, Math.max(0, st.size - tailCap))).bytesRead;
    tail = tbuf.subarray(0, tRead).toString("utf8");
  } finally { await fh.close(); }
  const lines = head.split("\n");
  let header = null, title = "", name = "", messages = 0;
  for (const line of lines) {
    if (!line.trim()) continue;
    let e; try { e = JSON.parse(line); } catch { continue; }
    if (e.type === "session") header = e;
    else if ((e.type === "session_info" || e.type === "session_name") && e.name) name = e.name;
    else if (e.type === "message" && e.message) {
      messages++;
      if (!title && e.message.role === "user") {
        const c0 = e.message.content;
        title = (typeof c0 === "string" ? c0 : (c0 || []).filter((b) => b?.type === "text").map((b) => b.text).join(" ")).slice(0, 80);
      }
    }
  }
  // 尾部：以最后一次 session_info 为准
  for (const line of tail.split("\n")) {
    if (!line.trim()) continue;
    let e; try { e = JSON.parse(line); } catch { continue; }
    if ((e.type === "session_info" || e.type === "session_name") && e.name) name = e.name;
  }
  const meta = {
    path: file, fileName: path.basename(file), id: header?.id || null, cwd: header?.cwd || null,
    createdAt: header?.timestamp || null, mtimeMs: st.mtimeMs, size: st.size,
    name: name || undefined,                                  // 用户起的名字（session_info）
    title: name || title || path.basename(file, ".jsonl"),     // 没有名字就用第一条用户消息
    messages,
  };
  sessionCache.set(file, { mtimeMs: st.mtimeMs, size: st.size, meta });
  return meta;
}

async function listSessions(limit = 200) {
  const dir = sessionDirOf();
  let files = [];
  try { files = (await readdir(dir)).filter((f) => f.endsWith(".jsonl")); } catch { return []; }
  const stats = [];
  for (const f of files) {
    try { const st = await stat(path.join(dir, f)); stats.push([path.join(dir, f), st.mtimeMs]); } catch {}
  }
  stats.sort((a, b) => b[1] - a[1]);
  const out = [];
  for (const [file] of stats.slice(0, limit)) {
    try { out.push(await sessionMeta(file)); } catch {}
  }
  return out;
}

const MAX_HISTORY_BYTES = 16 * 1024 * 1024;
async function sessionHistory(file, limit = 200) {
  if (!file.startsWith(sessionDirOf() + "/") && !file.startsWith("/root/") && !file.startsWith("/tmp/") && !file.startsWith(homedir() + "/") && !file.includes("/.pi/agent/sessions/")) throw new Error("会话路径不在允许范围");
  // ★ 会话文件能到 GB 级：整读不可行，超限直接报错又会让历史面板「静默打不开」。
  //   做法与 sessionMeta 同款：file handle 读两段 —— 头部 256KB 只为 model/thinking 兜底，
  //   尾部 16MB 窗口取消息（历史面板只要最后 N 条，它们本来就在文件末尾）。
  const st = await stat(file);
  const fh = await open(file, "r");
  let head = "", tail = "";
  try {
    const hbuf = Buffer.alloc(Number(Math.min(st.size, 262144)));
    const hRead = (await fh.read(hbuf, 0, hbuf.length, 0)).bytesRead;
    head = hbuf.subarray(0, hRead).toString("utf8");
    const tcap = Math.min(st.size, MAX_HISTORY_BYTES);
    const tbuf = Buffer.alloc(Number(tcap));
    const tRead = (await fh.read(tbuf, 0, tbuf.length, Math.max(0, st.size - tcap))).bytesRead;
    tail = tbuf.subarray(0, tRead).toString("utf8");
  } finally { await fh.close(); }
  if (st.size > MAX_HISTORY_BYTES) { const i = tail.indexOf("\n"); if (i >= 0) tail = tail.slice(i + 1); }
  const messages = [];
  let model = null, thinkingLevel = "";
  // messages 只从 tail 收：小文件 tail 就是全文（head 里的消息不重复收）；
  // 大文件时头部那些老消息远在 limit 之外，不收不丢东西。
  for (const text of [head, tail]) {
    for (const line of text.split("\n")) {
      if (!line.trim()) continue;
      let e; try { e = JSON.parse(line); } catch { continue; }
      if (e.type === "message" && e.message) { if (text === tail) messages.push(e.message); }
      else if (e.type === "model_change" && (e.provider || e.modelId)) model = { provider: e.provider, id: e.modelId };
      else if (e.type === "thinking_level_change" && e.level) thinkingLevel = e.level;
    }
  }
  return { messages: messages.slice(-limit), model, thinkingLevel, truncated: st.size > MAX_HISTORY_BYTES };
}

// ---------------------------------------------------------------- 会话写操作（重命名 / 删除）
// 格式对齐 pi 自己的 SessionManager.appendSessionInfo：追加一条 session_info（取最后一条为准）。
// 删除**不做 unlink**（借 pi-web 的能力但把风险降低）：移到 <sessionDir>/.trash/，可反悔；
// 真删留给操作者手动，接口层面只提供软删（不提供 hard 参数，免得被前端误调）。
function assertSessionPath(file) {
  // ★ 先 resolve 再判：直接 startsWith/includes 会被「/tmp/x/.pi/agent/sessions/../../etc/…」
  //   这种带 .. 的路径绕过去（子串确实在，但归一化后早就逃出沙箱了）——重命名/删除
  //   是对文件系统的写操作，必须按归一化后的真实路径判。
  const norm = path.resolve(file);
  const dir = sessionDirOf();
  if (!norm.startsWith(dir + "/") && !norm.includes("/.pi/agent/sessions/") && !norm.startsWith("/root/") && !norm.startsWith("/tmp/") && !norm.startsWith(homedir() + "/")) {
    throw new Error("会话路径不在允许范围");
  }
}

const MAX_TAIL = 65536;
async function lastEntryId(file) {
  const st = await stat(file);
  const fh = await open(file, "r");
  try {
    const cap = Math.min(st.size, MAX_TAIL);
    const buf = Buffer.alloc(Number(cap));
    const { bytesRead } = await fh.read(buf, 0, buf.length, Math.max(0, st.size - cap));
    const lines = buf.subarray(0, bytesRead).toString("utf8").split("\n").filter((l) => l.trim());
    for (let i = lines.length - 1; i >= 0; i--) {
      try { const e = JSON.parse(lines[i]); if (e.id) return e.id; } catch { /* 截断的最后一行很正常 */ }
    }
  } finally { await fh.close(); }
  return null;
}

async function renameSession(file, name) {
  assertSessionPath(file);
  const clean = String(name ?? "").replace(/[\r\n]+/g, " ").trim();
  if (!clean) throw new Error("名字不能为空");
  const parentId = await lastEntryId(file);
  const entry = { type: "session_info", id: Math.random().toString(16).slice(2, 10), parentId, timestamp: new Date().toISOString(), name: clean };
  await appendFile(file, JSON.stringify(entry) + "\n", "utf8");
  sessionCache.delete(file);
  return { ok: true, name: clean };
}

async function deleteSession(file) {
  assertSessionPath(file);
  await stat(file);
  const trash = path.join(sessionDirOf(), ".trash");
  await mkdir(trash, { recursive: true });
  const dest = path.join(trash, `${Date.now()}-${path.basename(file)}`);
  await rename(file, dest);
  sessionCache.delete(file);
  return { ok: true, movedTo: dest };
}

/**
 * 一键清空全部会话记录（2026-11-14 用户需求）。
 *
 * 语义与单条删除**完全一致**：移到 sessionDirOf()/.trash/ 而不是 unlink，
 * 误点还能从 .trash 捞回来 —— 清空是高频破坏性操作，不能真删。
 *
 * 为什么放在服务端做：前端逐条调 /api/sessions/delete 会有 N 次往返，
 * 几百条会话要几十秒，且中途失败会留下半清空状态。一次调用 + 逐个 rename，
 * 单个失败不影响其余，最后汇总返回。
 */
async function clearAllSessions() {
  const dir = sessionDirOf();
  let entries = [];
  try {
    entries = await readdir(dir, { withFileTypes: true });
  } catch (e) {
    if (e.code === "ENOENT") return { ok: true, moved: 0, failed: 0, total: 0 };
    throw e;
  }
  // 只处理文件，跳过 .trash 等目录；只认会话文件后缀，别误动别的
  const files = entries
    .filter((d) => d.isFile() && /\.(jsonl|json)$/i.test(d.name))
    .map((d) => path.join(dir, d.name));

  const trash = path.join(dir, ".trash");
  await mkdir(trash, { recursive: true });

  let moved = 0;
  const failed = [];
  const stamp = Date.now();
  for (const f of files) {
    try {
      const dest = path.join(trash, `${stamp}-${path.basename(f)}`);
      await rename(f, dest);
      sessionCache.delete(f);
      moved += 1;
    } catch (e) {
      failed.push({ file: path.basename(f), error: e.message });
    }
  }
  return { ok: true, moved, failed: failed.length, failures: failed.slice(0, 10), total: files.length, trash };
}

// ---------------------------------------------------------------- 附件上传（拖拽 / ＋号选文件）
// 浏览器拿不到文件的真实路径，但 pi 的附件机制要的就是路径 ——
// 所以由服务端把字节落盘，再把路径交给前端（前端把它当附件交给模型，模型用 read 去看）。
// 落点固定 work/intake/，免得到处丢文件。
const INTAKE = path.join(ROOT, "work", "intake");
const MAX_UPLOAD = 128 * 1024 * 1024;   // 单文件上限

async function saveUploads(items) {
  if (!Array.isArray(items) || !items.length) throw new Error("没有文件");
  await mkdir(INTAKE, { recursive: true });
  const saved = [];
  for (const it of items.slice(0, 16)) {
    const rawName = String(it?.name || "file");
    // 只取最后一段 + 去掉路径分隔符和控制字符，防目录穿越 / 怪名字
    const safe = rawName.split(/[/\\]/).pop().replace(/[\x00-\x1f]/g, "").slice(0, 120) || "file";
    const buf = Buffer.from(String(it?.data || ""), "base64");
    if (!buf.length) continue;
    if (buf.length > MAX_UPLOAD) throw new Error(`${safe} 超过 ${MAX_UPLOAD / 1048576}MB`);
    const dest = path.join(INTAKE, `${Date.now()}-${safe}`);
    await writeFile(dest, buf);
    saved.push({ path: dest, name: safe, size: buf.length });
  }
  if (!saved.length) throw new Error("没有可保存的文件");
  return saved;
}

// ---------------------------------------------------------------- 战果（flag-watch）
// log/flag-watch.jsonl 是机器扫每个工具输出的兜底：{ts, tool, hits:[{flag,kind,confidence}]}
// 这是 pi-lab 独有的——终端里只能 tail 看，做完界面就能一眼扫到「哪些 flag 已经出现过」。
const FLAG_LOG = path.join(ROOT, "log", "flag-watch.jsonl");

/**
 * 一键清空「记录」模块的两份业务数据（2026-11-14 用户需求）。
 *
 * 清什么：
 *   1. log/flag-watch.jsonl        —— 扫描命中流水（谁在什么输出里见过 flag）
 *   2. board/projects/<pid>/flag-ledger.jsonl —— flag 三态账本（这个 flag 算不算分）
 *
 * 不清什么：安全策略（subagentPolicy）是只读配置，不属于「记录」，界面也不给删。
 *
 * 为什么是改名不是删除：这两份都是**审计证据**，删了就没法复盘「当时为什么判它算分」。
 * 统一改名成 <原名>.bak-<时间戳>，想看随时能翻；这也让误点可以人工恢复。
 */
async function clearFlagRecords() {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const done = [];
  const failed = [];

  // 1) 扫描命中流水
  try {
    await stat(FLAG_LOG);
    const dest = `${FLAG_LOG}.bak-${stamp}`;
    await rename(FLAG_LOG, dest);
    flagCache = { mtimeMs: 0, size: 0, data: null };   // 缓存必须失效，否则界面还显示旧数据
    done.push({ what: "flag-watch", movedTo: dest });
  } catch (e) {
    if (e.code !== "ENOENT") failed.push({ what: "flag-watch", error: e.message });
  }

  // 2) 所有项目黑板下的 flag 账本（清空记录 = 所有题的账本一起清）
  try {
    const idx = await boardIndex();
    for (const pj of idx.projects || []) {
      const pid = pj.pid;
      if (!pid || !/^[\w.-]{1,64}$/.test(pid)) continue;
      const src = ledgerPath(pid);
      try {
        await stat(src);
        const dest = `${src}.bak-${stamp}`;
        await rename(src, dest);
        done.push({ what: `ledger:${pid}`, movedTo: dest });
      } catch (e) {
        if (e.code !== "ENOENT") failed.push({ what: `ledger:${pid}`, error: e.message });
      }
    }
  } catch (e) {
    failed.push({ what: "board-index", error: e.message });
  }

  return { ok: true, cleared: done.length, failed: failed.length, done, failures: failed.slice(0, 10), stamp };
}
let flagCache = { mtimeMs: 0, size: 0, data: null };

async function flagReport(limit = 300) {
  let st;
  try { st = await stat(FLAG_LOG); } catch { return { entries: [], unique: [], total: 0, error: "没有 log/flag-watch.jsonl" }; }
  if (flagCache.size === st.size && flagCache.mtimeMs === st.mtimeMs) return flagCache.data;

  // 只读尾部 1MB（这文件会一直长；历史条目从旧到新，只要最近的命中）
  const cap = Math.min(st.size, 1024 * 1024);
  const fh = await open(FLAG_LOG, "r");
  let text = "";
  try {
    const buf = Buffer.alloc(Number(cap));
    const { bytesRead } = await fh.read(buf, 0, buf.length, Math.max(0, st.size - cap));
    text = buf.subarray(0, bytesRead).toString("utf8");
  } finally { await fh.close(); }

  const entries = [];
  const byFlag = new Map();
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    let e; try { e = JSON.parse(line); } catch { continue; }
    if (!Array.isArray(e.hits) || !e.hits.length) continue;
    // ctx = 原始输出片段的取回坐标（session + sha1）；老记录没这个字段就为 null
    entries.push({ ts: e.ts, tool: e.tool || "?", sid: e.sid || null, ctx: e.ctx || null,
      hits: e.hits.map((h) => ({ flag: h.flag, kind: h.kind, confidence: h.confidence })) });
    for (const h of e.hits) {
      if (!h?.flag) continue;
      const prev = byFlag.get(h.flag);
      if (prev) { prev.count++; prev.lastTs = e.ts; if (prev.tools.length < 4 && !prev.tools.includes(e.tool)) prev.tools.push(e.tool); }
      else byFlag.set(h.flag, { flag: h.flag, kind: h.kind, confidence: h.confidence, count: 1, firstTs: e.ts, lastTs: e.ts, tools: [e.tool] });
      // 每个 flag 记住「最近一次能展开原始输出」的坐标，界面直接拿去取
      if (e.sid && e.ctx) {
        const u = byFlag.get(h.flag);
        if (u && u.lastTs === e.ts) { u.sid = e.sid; u.ctx = e.ctx; }
      }
    }
  }
  const unique = [...byFlag.values()].sort((a, b) => String(b.lastTs).localeCompare(String(a.lastTs)));
  const data = { entries: entries.slice(-limit).reverse(), unique, total: entries.length, logSize: st.size };
  flagCache = { mtimeMs: st.mtimeMs, size: st.size, data };
  return data;
}

// ---------------------------------------------------------------- flag 三态账本（W-P0-2）
// ★ 2026-11-05：flag-watch.jsonl 只回答「这东西在输出里出现过」，它**根本不知道平台收不收**。
//   抢分时最危险的不是没找到，而是「攒了一堆候选、不知道哪个真算分」——
//   所以这里读 fgs 的 append-only 账本（board/projects/<pid>/flag-ledger.jsonl），
//   把每个 flag 的真实状态摊开：accepted=已核验（有平台回执）／其余=待核验／rejected=证伪。
//   三态词表照抄 board/fgs.py 的 LEDGER_STATES（recorded/submitted/accepted/rejected/error），不另造一套。
const LEDGER_TRI = {
  recorded: "pending", submitted: "pending", error: "pending",
  accepted: "verified", rejected: "refuted",
};
const ledgerPath = (pid) => path.join(BOARD, pid, "flag-ledger.jsonl");

async function flagLedger(wantPid) {
  const idx = await boardIndex();
  const projects = idx.projects || [];
  const pid = wantPid && /^[\w.-]{1,64}$/.test(wantPid) ? wantPid : projects[0]?.pid || null;
  if (!pid) return { pid: null, flags: [], counts: { verified: 0, pending: 0, refuted: 0 }, error: "还没有任何项目黑板" };

  let text = "";
  try {
    const st = await stat(ledgerPath(pid));
    // 账本 append-only 会一直长；只读尾部 1MB，与 flagReport 同一口径（别整本吞）
    const cap = Math.min(st.size, 1024 * 1024);
    const fh = await open(ledgerPath(pid), "r");
    try {
      const buf = Buffer.alloc(Number(cap));
      const { bytesRead } = await fh.read(buf, 0, buf.length, Math.max(0, st.size - cap));
      text = buf.subarray(0, bytesRead).toString("utf8");
    } finally { await fh.close(); }
  } catch {
    return { pid, flags: [], counts: { verified: 0, pending: 0, refuted: 0 }, error: "该项目还没有 flag-ledger.jsonl（没记过账）" };
  }

  // 账本是 append-only 的事件流、不是终态表：同一个 flag_id 会有多行（recorded → accepted）。
  // 按出现顺序 fold，后者覆盖前者 —— 这才是「它现在到底算不算分」。
  const byId = new Map();
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    let r; try { r = JSON.parse(line); } catch { continue; }   // 半写的一行跳过，别让它毒死整本账
    const id = String(r.flag_id || "");
    if (!id) continue;
    const prev = byId.get(id) || { flag_id: id, flag: "", history: [] };
    if (r.flag) prev.flag = String(r.flag);
    prev.history.push({ state: r.state || "?", at: r.at || "", by: r.by || "",
                        note: r.note ?? null, correct: r.correct ?? null });
    prev.state = String(r.state || "?");
    prev.tri = LEDGER_TRI[prev.state] || "pending";
    prev.at = r.at || "";
    // 证据只放**平台原始回执**，不放我们的解读；null 就代表没有回执
    prev.reply = r.reply ?? null;
    byId.set(id, prev);
  }

  const flags = [...byId.values()].map((f) => ({
    flag_id: f.flag_id, flag: f.flag, state: f.state, tri: f.tri, at: f.at,
    reply: f.reply, history: f.history.slice(-6),
  }));
  // 已核验优先显示（抢分视角：先看能变现的）
  flags.sort((a, b) => (a.tri === "verified" ? -1 : 0) - (b.tri === "verified" ? -1 : 0));
  const counts = { verified: 0, pending: 0, refuted: 0 };
  for (const f of flags) counts[f.tri] = (counts[f.tri] || 0) + 1;
  return { pid, flags, counts };
}

// ---------------------------------------------------------------- 「下一刀」候选（W-P0-3）
// ★ 2026-11-05：对话页空白态难看的根子是「没有决策可做」。这里把黑板上**当前可动的三刀**
//   摊成数据：优先证据最强、变现最快。每条带 why 与证据强度，界面不猜、不编。
//   数据全部来自已有的机器产物（fgs next / graph.json / flag 账本），不新增任何写路径。
async function moves(wantPid) {
  const idx = await boardIndex();
  const projects = idx.projects || [];
  const pid = wantPid && /^[\w.-]{1,64}$/.test(wantPid) ? wantPid : projects[0]?.pid || null;
  if (!pid) return { pid: null, moves: [], error: "还没有任何项目黑板（先 fgs kickoff <pid>）" };

  let next = null;
  try { next = await fgsJson("next", pid); } catch (e) { next = { error: e.message }; }

  let graph = null;
  try { graph = JSON.parse(await readCapped(path.join(BOARD, pid, "graph.json"))); } catch {}
  const nodes = graph?.nodes ? Object.values(graph.nodes) : [];
  const facts = nodes.filter((n) => n.type === "fact");
  const unsealed = facts.filter((n) => n.status !== "verified").slice(0, 3);

  let ledger = { flags: [] };
  try { ledger = await flagLedger(pid); } catch {}
  const unsent = (ledger.flags || []).filter((f) => f.tri === "pending" && f.flag);

  const out = [];
  if (next && next.task && next.task !== "none") {
    out.push({
      id: "next", kind: "机器建议",
      title: String(next.task), why: String(next.why || next.reason || ""),
      strength: 3, source: "fgs next",
    });
  }
  for (const f of unsent.slice(0, 2)) {
    out.push({
      id: "flag:" + f.flag_id, kind: "待提交 flag",
      title: f.flag,
      why: "账本状态是「" + f.state + "」，还没有平台回执 —— 交一次才知道算不算分",
      strength: f.state === "recorded" ? 2 : 1, source: "flag-ledger.jsonl",
    });
  }
  for (const n of unsealed) {
    out.push({
      id: "fact:" + n.id, kind: "差一步盖章",
      title: String(n.text || "").slice(0, 120),
      why: "事实已在图上但状态是「" + (n.status || "unverified") + "」—— 补一条逐字溯源就成结论",
      strength: 2, source: "graph.json#" + n.id,
    });
  }
  out.sort((a, b) => b.strength - a.strength);

  return {
    pid,
    moves: out.slice(0, 3),
    context: { next: next?.task || null, facts: facts.length,
               verified: facts.filter((n) => n.status === "verified").length,
               unsent: unsent.length, counts: ledger.counts || null },
    error: out.length ? undefined : "黑板上暂时没有可动的下一刀 —— 先 fgs kickoff / 派一路 worker",
  };
}

// ---------------------------------------------------------------- 子智能体（pi-subagents）
// 数据面（都是实测摸出来的，别改错）：
//   · 正在跑的：/tmp/pi-subagents-uid-*/async-subagent-runs/<runId>/status.json（含 state/pid/sessionId/cwd）
//   · 生命周期：同目录 events.jsonl（subagent.run.started/completed/…）
//   · 子智能体自己的会话：<parent session 的 .jsonl 同名目录>/<uuid>/run-N/session.jsonl  ← "点进去看他在干嘛"
//   · 历史：<agentDir>/run-history.jsonl（agent/ts/status/duration，task 被脱敏成 hash）
//   · 任务记录：<agentDir>/missions/projects/*/*.json（含 ownerSessionId=挂在哪条主会话 → 树状关系）
const HOME = process.env.HOME || "/root";
const SUBAGENT_RUN_GLOB = path.join("/tmp");   // 逐个目录列，别用 shell 通配
const RUN_HISTORY = path.join(ROOT, ".pi", "agent", "run-history.jsonl");
const MISSIONS_DIR = path.join(ROOT, ".pi", "agent", "missions", "projects");

// ---------------------------------------------------------------- 子智能体收工自动落板
// ★ 2026-10-13：子智能体跑了半天，结论常常没投上板（实测：zip-revenge 那路 worker 跑了 15 分钟、
//   一条 fact 都没投；json-parser 那 3 条投了却两天没人 apply）——人一断线，整条探索就没了痕迹。
//   这里做**机器兜底**：run 一进终态，就把它的收尾陈词（subagent-log-<runId>.md，没有就 output-0.log 尾部）
//   作为一条 inferred fact 投进对应板子的 outbox，紧接着 apply 合进图。
//   pid 从哪儿认：status.json 的 parent session → 那条会话里最后一次 `fgs kickoff <pid>`
//   （复用 tools/lanewatch.mjs 的 parseKickoff，与派活闸门同一口径）。
const SWEEP_STATE = path.join(HERE, ".swept-runs.json");
const SWEPT_MAX = 500;
const SWEEP_MIN_INTERVAL_MS = 20000;
/** run 的终态；`paused` 不算（它可能被 resume，等真结束再落） */
const RUN_TERMINAL = new Set(["complete", "failed", "stopped", "cancelled", "error", "timeout"]);
let sweptRuns = null;
let lastSweepAt = 0;

async function loadSwept() {
  if (sweptRuns) return sweptRuns;
  try { sweptRuns = new Set(JSON.parse(await readFile(SWEEP_STATE, "utf8"))); } catch { sweptRuns = new Set(); }
  return sweptRuns;
}

async function saveSwept() {
  try { await writeFile(SWEEP_STATE, JSON.stringify([...(sweptRuns || [])].slice(-SWEPT_MAX)), "utf8"); } catch { /* 写不上不影响 */ }
}

/** 父会话里最后一次 `fgs kickoff <pid>` 的 pid（限长读尾部 512KB）
 *  ★ 只认**工具调用参数里**的 kickoff：会话文件里还塞着 AGENTS.md 正文，
 *    里面就有例子「fgs kickoff nrk-web --kind ctf」—— 照纯文本扫会把例子当命令，
 *    把子智能体的结论投到**错的项目**上去。所以逐行 JSON 解出来、只看 toolCall 的 command。
 */
async function findPidForSession(sessionPath) {
  if (!sessionPath || typeof sessionPath !== "string") return null;
  try {
    const st = await stat(sessionPath);
    const cap = Math.min(Number(st.size), 512 * 1024);
    const fh = await open(sessionPath, "r");
    let text = "";
    try {
      const buf = Buffer.alloc(Number(cap));
      const { bytesRead } = await fh.read(buf, 0, buf.length, Math.max(0, st.size - cap));
      text = buf.subarray(0, bytesRead).toString("utf8");
    } finally { await fh.close(); }
    const lines = text.split("\n");
    if (st.size > cap) lines.shift();          // 首行可能被限长切断
    let last = null;
    for (const line of lines) {
      if (!line.includes("kickoff")) continue;
      let rec;
      try { rec = JSON.parse(line); } catch { continue; }
      const content = rec?.message?.content;
      if (!Array.isArray(content)) continue;
      for (const part of content) {
        if (!part || part.type !== "toolCall") continue;
        let args = part.arguments;
        if (typeof args === "string") { try { args = JSON.parse(args); } catch { continue; } }
        const cmd = String(args?.command ?? "");
        if (!cmd.includes("fgs kickoff")) continue;
        // ★ 口径：用 lanewatch 的 parseKickoff（已做到**引号感知**：引号里引用别人的命令不算）。
        //   之前这里自己写了个「必须紧跟在命令段开头」的正则，把 `timeout 40 fgs kickoff uitest`、
        //   `nohup fgs kickoff …` 这类带包装器的合法调用全漏了（实测：自动落板直接不触发）。
        //   剩下的误报（比如 `echo fgs kickoff abc`）由后面的「板子必须存在」兵底。
        const p = parseKickoff(cmd);
        if (p) last = p;
      }
    }
    return last;
  } catch { return null; }
}

/** 读文件尾部（限长）—— 终态 run 的结论可能在日志末尾 */
async function readTail(file, cap) {
  try {
    const st = await stat(file);
    const n = Math.min(Number(st.size), cap);
    const fh = await open(file, "r");
    try {
      const buf = Buffer.alloc(n);
      const { bytesRead } = await fh.read(buf, 0, buf.length, Math.max(0, st.size - n));
      return buf.subarray(0, bytesRead).toString("utf8");
    } finally { await fh.close(); }
  } catch { return ""; }
}

/** 从子智能体的日志里挑出**拿来当 fact 的那段话**。
 *  为什么不能直接取尾巴：run 被叫停时日志停在半句话上（实测 f001 开头是「ck with the option: …」），
 *  这种 fact 给下一棒的是噪声。做法：按空行切段，从后往前找一段「不是进度条/不是裸命令」的，
 *  过滤掉 `[HH:MM:SS]`、`x.x % (…)`、`Attack on …`、`###` 这些机器进度行。
 */
function summarizeRunLog(text) {
  const NOISE = /^\s*(\[\d{1,2}:\d{2}:\d{2}\]|\d+(\.\d+)?\s*%\s*\(|Attack on\b|Z reduction\b|bkcrack\b|###|\$ |&&|cd )/;
  // ★ 先把 `\r` 归一成 `\n`：bkcrack 的进度是靠 `\r` 在同一行刷新出来的，
  //   不归一的话整段会变成“一行”，噪声行就滤不掉了（实测第一次就踩到）。
  const src = String(text || "").replace(/\r/g, "\n");
  const clean = (p) => p.split("\n").map((l) => l.trim()).filter((l) => l && !NOISE.test(l)).join(" ").trim();
  // 段落门槛放到 8 字：一句短结论（如“没命中，已排除”）也是有效信息，别被长度卡掉
  const paras = src.split(/\n\s*\n/).map(clean).filter((p) => p.length >= 8);
  if (paras.length) return paras.slice(-1)[0].slice(0, 600);
  // 没成型段落：洗干净所有行取最后一句；实在没有就退回空格压缩后的原文
  const lines = src.split("\n").map((l) => l.trim()).filter((l) => l && !NOISE.test(l));
  const last = lines.slice(-1)[0] || "";
  return (last || src.replace(/\s+/g, " ").trim()).slice(0, 600);
}

async function sweepFinishedRuns() {
  const now = Date.now();
  if (now - lastSweepAt < SWEEP_MIN_INTERVAL_MS) return { skipped: true };
  lastSweepAt = now;
  await loadSwept();
  let uids = [];
  try { uids = (await readdir("/tmp")).filter((d) => d.startsWith("pi-subagents-uid-")); } catch { return { swept: 0 }; }
  const done = [];
  for (const u of uids.slice(0, 4)) {
    const root = path.join("/tmp", u, "async-subagent-runs");
    let runs = [];
    try { runs = await readdir(root); } catch { continue; }
    for (const rid of runs.slice(0, 80)) {
      if (sweptRuns.has(rid)) continue;
      let j = null;
      try { j = JSON.parse((await readCapped(path.join(root, rid, "status.json"), 256 * 1024)).text); } catch { continue; }
      if (!RUN_TERMINAL.has(String(j.state || ""))) continue;
      const pid = await findPidForSession(j.sessionId);
      if (!pid) { sweptRuns.add(rid); continue; }
      // 板子必须真的存在（否则 append 会 ENOENT：实测踩到过 smoke-test / lw2 这种测试名）
      try { await stat(path.join(BOARD, pid)); } catch { sweptRuns.add(rid); continue; }
      // 结论：优先 subagent-log-<runId>.md（子智能体的收尾陈词，几百字节），否则 output-0.log 尾部
      let text = "";
      try { text = (await readCapped(path.join(root, rid, `subagent-log-${rid}.md`), 8192)).text.trim(); } catch { /* 没这份 */ }
      if (!text) text = (await readTail(path.join(root, rid, "output-0.log"), 4096)).trim();
      if (!text) { sweptRuns.add(rid); continue; }
      text = summarizeRunLog(text);
      if (!text) { sweptRuns.add(rid); continue; }
      const agent = j.agent || j.agentName || "subagent";
      const fact = {
        op: "fact",
        confidence: "inferred",
        text: `[子智能体 ${agent} ${rid.slice(0, 8)} · ${j.state}] ${text.replace(/\s+/g, " ").slice(0, 600)}`,
        _published_by: `run:${rid.slice(0, 8)}`,
        _published_at: new Date().toISOString(),
        _seq: Math.random().toString(16).slice(2, 10),
      };
      try {
        await appendFile(path.join(BOARD, pid, "outbox.jsonl"), JSON.stringify(fact) + "\n", "utf8");
        await applyOutboxIfAny(pid);
        sweptRuns.add(rid);
        log(`子智能体收工落板：${rid.slice(0, 8)} → ${pid}（${agent} · ${j.state}）`);
        done.push({ rid, pid });
      } catch (e) { log(`收工落板失败 ${rid.slice(0, 8)}: ${e.message}`); }
    }
  }
  await saveSwept();
  return { swept: done.length, done };
}

/** 扫 /tmp 下的 pi-subagents-uid-XXX/async-subagent-runs/RUNID/status.json（只读 status.json，几百字节） */
async function listLiveRuns() {
  const out = [];
  let uids = [];
  try { uids = (await readdir(SUBAGENT_RUN_GLOB)).filter((d) => d.startsWith("pi-subagents-uid-")); } catch { return out; }
  for (const u of uids.slice(0, 4)) {
    const root = path.join(SUBAGENT_RUN_GLOB, u, "async-subagent-runs");
    let runs = [];
    try { runs = await readdir(root); } catch { continue; }
    for (const rid of runs.slice(0, 60)) {
      const st = path.join(root, rid, "status.json");
      try {
        const { text } = await readCapped(st, 256 * 1024);
        const j = JSON.parse(text);
        // agent 名（ctf-wp / ctf-route…）：events.jsonl 里 subagent.step.started / session_info_changed
        // 才带，status.json 没有 —— 头部 16KB 扫一次就够（限长）
        let agentName = j.agent || j.agentName || null;
        if (!agentName) {
          try {
            const hf = path.join(root, rid, "events.jsonl");
            const hst = await stat(hf);
            const hcap = Math.min(hst.size, 16384);
            const hfh = await open(hf, "r");
            try {
              const hbuf = Buffer.alloc(Number(hcap));
              const { bytesRead } = await hfh.read(hbuf, 0, hbuf.length, 0);
              const m = /"agent"\s*:\s*"([\w.-]{2,40})"/.exec(hbuf.subarray(0, bytesRead).toString("utf8"));
              if (m) agentName = m[1];
            } finally { await hfh.close(); }
          } catch { /* 没有就算了 */ }
        }

        // 正在跑的顺手带一条"最近在干什么"（events.jsonl 尾部 8KB，限长）
        let lastEvent = null;
        if ((j.state || "") === "running") {
          try {
            const ef = path.join(root, rid, "events.jsonl");
            const est = await stat(ef);
            const cap = Math.min(est.size, 8192);
            const fh = await open(ef, "r");
            try {
              const buf = Buffer.alloc(Number(cap));
              const { bytesRead } = await fh.read(buf, 0, buf.length, Math.max(0, est.size - cap));
              const lines = buf.subarray(0, bytesRead).toString("utf8").split("\n").filter((l) => l.trim());
              for (let i = lines.length - 1; i >= 0; i--) {
                try { const e = JSON.parse(lines[i]); lastEvent = { type: e.type, ts: e.ts, status: e.status }; break; } catch { /* 截断行 */ }
              }
            } finally { await fh.close(); }
          } catch { /* 没有就别显示 */ }
        }
        out.push({
          runId: j.runId || rid, state: j.state || "unknown", mode: j.mode || null, lastEvent,
          agentName,
          pid: j.pid ?? null, cwd: j.cwd || null, sessionId: j.sessionId || null,
          startedAt: j.startedAt || null, lastActivityAt: j.lastActivityAt || null,
          completedAt: j.completedAt || null, timeoutMs: j.timeoutMs || null,
          agent: j.agent || j.agentName || null,
          tokens: j.totalTokens || null, steering: j.steering || null,
          runDir: path.join(root, rid), live: true,
        });
      } catch { /* 破损/半截的跳过 */ }
    }
  }
  out.sort((a, b) => (b.startedAt || 0) - (a.startedAt || 0));
  return out;
}

/** 历史：run-history.jsonl 尾部（限长；task 是脱敏 hash，别指望拿原话） */
let histCache = { mtimeMs: 0, size: 0, data: [] };
async function listRunHistory(limit = 60) {
  let st;
  try { st = await stat(RUN_HISTORY); } catch { return []; }
  if (histCache.size === st.size && histCache.mtimeMs === st.mtimeMs) return histCache.data.slice(0, limit);
  const cap = Math.min(st.size, 512 * 1024);
  const fh = await open(RUN_HISTORY, "r");
  let text = "";
  try {
    const buf = Buffer.alloc(Number(cap));
    const { bytesRead } = await fh.read(buf, 0, buf.length, Math.max(0, st.size - cap));
    text = buf.subarray(0, bytesRead).toString("utf8");
  } finally { await fh.close(); }
  const rows = [];
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    try { const e = JSON.parse(line); rows.push({ ts: e.ts ? e.ts * 1000 : null, agent: e.agent, status: e.status, outcome: e.outcome, durationMs: e.duration, taskHash: e.taskHash }); } catch { /* 截断行 */ }
  }
  rows.reverse();
  histCache = { mtimeMs: st.mtimeMs, size: st.size, data: rows };
  return rows.slice(0, limit);
}

/** mission 记录：拿 ownerSessionId（树状关系）+ 状态 + 产出路径 */
async function listMissions(limit = 40) {
  const out = [];
  let projects = [];
  try { projects = await readdir(MISSIONS_DIR); } catch { return out; }
  const cand = [];
  for (const pr of projects.slice(0, 20)) {
    const dir = path.join(MISSIONS_DIR, pr);
    let files = [];
    try { files = (await readdir(dir)).filter((f) => f.endsWith(".json")); } catch { continue; }
    for (const f of files.slice(0, 40)) {
      try { const st = await stat(path.join(dir, f)); cand.push([st.mtimeMs, path.join(dir, f)]); } catch {}
    }
  }
  cand.sort((a, b) => b[0] - a[0]);
  for (const [mt, f] of cand.slice(0, limit)) {
    try {
      const { text } = await readCapped(f, 512 * 1024);
      const j = JSON.parse(text);
      out.push({
        missionId: j.id, status: j.status, createdAt: j.createdAt, updatedAt: j.updatedAt,
        ownerSessionId: j.ownerSessionId || null, cwd: j.cwd || null, summary: j.summary || "",
        runs: (j.runs || []).map((r) => ({ runId: r.runId, mode: r.mode, status: r.status, startedAt: r.startedAt, completedAt: r.completedAt, asyncDir: r.asyncDir })),
        artifacts: j.artifacts || [], mtimeMs: mt,
      });
    } catch {}
  }
  return out;
}

/** 子智能体自己的会话文件：<parent.jsonl 去掉扩展名>/<uuid>/run-N/session.jsonl */
async function childTranscriptPath(parentSession, runId) {
  if (!parentSession) return null;
  const base = parentSession.replace(/\.jsonl$/, "");
  let uuids = [];
  try { uuids = await readdir(base, { withFileTypes: true }); } catch { return null; }
  const dirs = uuids.filter((d) => d.isDirectory()).map((d) => d.name);
  for (const u of dirs.slice(0, 20)) {
    let runs = [];
    try { runs = await readdir(path.join(base, u)); } catch { continue; }
    for (const rn of runs.filter((x) => /^run-\d+$/.test(x))) {
      const f = path.join(base, u, rn, "session.jsonl");
      try { await stat(f); return f; } catch {}
    }
  }
  return null;
}

// ---------------------------------------------------------------- 会话分支树 / fork
// 数据面：pi 的 RPC 现成给树
//   get_tree          → {tree:[{entry:{id,parentId,type,...}, children:[...]}]}
//   get_fork_messages → {messages:[{entryId, text}]}（可分支的点 = 用户消息）
//   fork(entryId)     → 从那条用户消息开一条新分支（当前分支往前岔出去）
// 我们只把树"剪"成界面要的两样东西：可分支的点 + 当前分支路径（都限长）。

const MAX_TREE_NODES = 2000;

function previewOf(entry) {
  if (!entry) return "";
  if (entry.type === "message") {
    const m = entry.message || {};
    const c = m.content;
    const text = typeof c === "string" ? c
      : Array.isArray(c) ? c.filter((b) => b?.type === "text").map((b) => b.text).join(" ") : "";
    return { role: m.role || "?", text: String(text).replace(/\s+/g, " ").slice(0, 160) };
  }
  if (entry.type === "model_change") return { role: "model", text: String(entry.modelId || "") };
  if (entry.type === "thinking_level_change") return { role: "think", text: String(entry.thinkingLevel || "") };
  if (entry.type === "session_info") return { role: "name", text: String(entry.name || "") };
  return { role: entry.type || "?", text: "" };
}

async function sessionTree(rt) {
  const [treeRes, forkRes] = await Promise.all([
    rt.command({ type: "get_tree" }, 60000),
    rt.command({ type: "get_fork_messages" }, 60000),
  ]);
  const roots = treeRes?.data?.tree || [];
  const forkText = new Map();
  for (const m of forkRes?.data?.messages || []) forkText.set(m.entryId, String(m.text || ""));

  // 展平成 id → {entry, children} 便于算"路径"和"分支数"
  const byId = new Map();
  const walk = (nodes) => {
    for (const n of nodes || []) {
      if (byId.size >= MAX_TREE_NODES) return;
      const e = n.entry || {};
      byId.set(e.id, { entry: e, children: (n.children || []).map((c) => c.entry?.id).filter(Boolean) });
      walk(n.children);
    }
  };
  walk(roots);

  // 当前分支路径：从"最深的那条"回溯（roots 已按文件顺序，取最后一个根的第 0 支到底）
  const path = [];
  let curId = roots.length ? (roots[roots.length - 1].entry?.id ?? null) : null;
  const seen = new Set();
  while (curId && byId.has(curId) && path.length < MAX_TREE_NODES) {
    if (seen.has(curId)) break;
    seen.add(curId);
    const node = byId.get(curId);
    const p = previewOf(node.entry);
    if (p.text || node.entry.type === "message") {
      path.push({ id: curId, type: node.entry.type, role: p.role, text: p.text, ts: node.entry.timestamp, childCount: node.children.length });
    }
    // 继续走"有孩子的那一支"（最后一个孩子 = 最新分支）
    const kids = node.children.filter((k) => byId.has(k));
    curId = kids.length ? kids[kids.length - 1] : null;
  }

  const forkPoints = [...forkText.entries()]
    .map(([entryId, text]) => ({
      entryId,
      text: text.replace(/\s+/g, " ").slice(0, 200),
      childCount: byId.get(entryId)?.children?.length ?? 0,
      onActivePath: path.some((p) => p.id === entryId),
      ts: byId.get(entryId)?.entry?.timestamp ?? null,
    }))
    .reverse();   // 新的在前

  return { counts: { entries: byId.size, forks: forkPoints.length }, path, forkPoints };
}

// ---------------------------------------------------------------- 给子智能体发消息（steer 信箱）
// 数据面：pi-subagents 的 runner 会**轮询** <runDir>/control/steer-requests/ 里的 json，
// 所以"发消息"就是往那个目录写一个合法请求文件（格式取自它的 control-channel.js）。
// 结束的 run 不能发（它的 steer-inbox-closed.json 会存在）——这点要如实告诉用户。
async function steerRun(runDir, text, mode = "steer") {
  const msg = String(text || "").trim();
  if (!msg) throw new Error("消息不能为空");
  if (Buffer.byteLength(msg, "utf8") > 128 * 1024) throw new Error("消息太长（>128KB）");
  // 信箱关了 = 这条 run 已经结束：不装样子，直接说清楚
  try { await stat(path.join(runDir, "control", "steer-inbox-closed.json")); throw new Error("这条 run 已经结束，收不了消息了（可以看它的对话，或重新派一个）"); } catch (e) { if (e.message.startsWith("这条 run")) throw e; }
  const dir = path.join(runDir, "control", "steer-requests");
  await mkdir(dir, { recursive: true });
  const ts = Date.now();
  const id = `pictf-${ts}-${Math.random().toString(16).slice(2, 8)}`;
  const payload = { type: "steer", id, ts, message: msg, mode };
  const name = `${String(ts).padStart(13, "0")}-${Buffer.from(id).toString("base64url")}.json`;
  const dest = path.join(dir, name);
  // 临时文件 + rename = 原子落盘（它的消费端按文件读，不能读到半截）
  const tmp = dest + ".tmp";
  await writeFile(tmp, JSON.stringify(payload, null, 2), "utf8");
  await rename(tmp, dest);
  return { ok: true, file: dest, id, mode };
}

// ---------------------------------------------------------------- 问机器（fgs 只读子命令）
// 白名单：只允许这几个只读命令，绝不把任意 shell 暴露给界面
// ★ deadline 只能以「show」形态出现：本函数拼的参数固定是 `<cmd> <pid> --json`，
//   没有 --at / --clear 的通道，所以把它放进白名单不会打开任何写路径。
// ★ 只读白名单。2026-11-11 补本轮三项（此前界面上根本调不到）：
//   pending   —— 跨题待提交 flag（最贵的一种亏：成本已付、分数没到账）
//   blindspot —— 盲区（死路缺要素 / 未验证前提 / 覆盖空洞）
//   ops       —— 不是 fgs 子命令，走 /api/ops，此处不列
// ★ 刻意**不含** diff：它是**写**操作（改 difficulty），不该出现在只读入口。
const FGS_ALLOW = new Set([
  "next", "workers", "cost", "gate", "doctor", "coverage", "deadline",
  "pending",     // [T1] 跨题待提交（支持 --json，已实测）
  "blindspot",   // [T9] 盲区（支持 --json，已实测）
]);
// 跨项目命令：不需要 pid（fgs pending 扫所有黑板）
const FGS_GLOBAL = new Set(["pending"]);

async function runFgs(cmd, pid) {
  if (!FGS_ALLOW.has(cmd)) throw new Error(`fgs ${cmd} 不在白名单`);
  const isGlobal = FGS_GLOBAL.has(cmd);
  if (!isGlobal && !/^[\w.-]{1,64}$/.test(pid)) throw new Error("非法 project id");
  const env = { ...process.env, PATH: `${ROOT}/tools/bin:${process.env.PATH || ""}` };
  return await new Promise((resolve) => {
    const argv = isGlobal ? [cmd, "--json"] : [cmd, pid, "--json"];
    const child = spawn("fgs", argv, { cwd: ROOT, env, stdio: ["ignore", "pipe", "pipe"] });
    let out = "", err = "", done = false;
    const finish = (extra = {}) => {
      if (done) return; done = true;
      try { child.kill("SIGKILL"); } catch {}
      resolve({ ok: true, cmd, pid, stdout: out.slice(0, 200000), stderr: err.slice(0, 8000), ...extra });
    };
    const timer = setTimeout(() => finish({ timeout: true }), 30000);
    child.stdout.on("data", (d) => { out += d.toString(); if (out.length > 300000) finish(); });
    child.stderr.on("data", (d) => { err += d.toString(); });
    child.on("exit", (code) => { clearTimeout(timer); finish({ code }); });
    child.on("error", (e) => { clearTimeout(timer); finish({ code: -1, stderr: err + String(e.message) }); });
  });
}

// ---------------------------------------------------------------- 作战台聚合（W-P0-1，只读）
// 设计依据（docs/学习清单-W系列-WebUI设计.md §1.2）：
//   信条 P1「时间是一等公民」、P3「一屏一个决策」。
// 纪律：
//   · **只调只读子命令**（deadline / cost / next），与 FGS_ALLOW 同源，绝不经过 shell；
//   · 每一路**独立 try**，一路失败只让那一路变 null —— 界面必须能显示「不知道」，
//     不许拿默认值假装有数（那正是 piVersion 写死 1.1.0 犯过的错）；
//   · 5 秒内存缓存：这是个会被轮询的接口，fgs 每次都要起进程。
// 10 秒：/api/ops 一次要起 3 个 fgs 进程（deadline/cost/next），轮询太密等于拿 CPU 换画质。
// 剩余时间按分钟看就够，10 秒的抖动不影响任何决策。
const OPS_TTL_MS = 10000;
const opsCache = new Map(); // pid -> {at, data}

// 跑分实况（/api/bench）：3 秒缓存。聚合在 tools/bench/live.py（改口径不用重启 UI），
// 这里只负责起进程 + 兜错，超时 15s（它自己要打平台 API，冷的时候 ~1s，抖的时候要留余量）。
const benchCache = { at: 0, data: null };
function runBenchLive(localOnly = false) {
  return new Promise((resolve) => {
    // ★ 用 bash -lc 起：容器里 python3 与 /opt/pi-ctf 的 PATH 都靠 login shell 才齐；
    //   cwd=ROOT 让 live.py 能按相对路径找到 work/solve 与 log。
    const args = [path.join(ROOT, "tools", "bench", "live.py")];
    if (localOnly) args.push("--no-net");
    const child = spawn("python3", args, { cwd: ROOT, env: process.env, stdio: ["ignore", "pipe", "pipe"] });
    let out = "", err = "", done = false;
    const finish = (fn, v) => { if (done) return; done = true; clearTimeout(t); try { child.kill("SIGKILL"); } catch {} fn(v); };
    const t = setTimeout(() => finish(resolve, { ok: false, error: "live.py 超时（15s）" }), 15000);
    child.stdout.on("data", (b) => { if (out.length < 4 * 1024 * 1024) out += b.toString("utf8"); });
    child.stderr.on("data", (b) => { if (err.length < 20000) err += b.toString("utf8"); });
    child.on("error", (e) => finish(resolve, { ok: false, error: "起不来 python3：" + e.message }));
    child.on("close", () => {
      const line = out.trim().split(String.fromCharCode(10)).filter(Boolean).pop() || "";
      if (!line) return finish(resolve, { ok: false, error: (err || "").trim().slice(-300) || "live.py 无输出" });
      try { finish(resolve, { ok: true, data: JSON.parse(line) }); }
      catch { finish(resolve, { ok: false, error: "live.py 输出不是 JSON：" + line.slice(0, 160) }); }
    });
  });
}

async function fgsJson(cmd, pid, timeoutMs = 25000) {
  if (!FGS_ALLOW.has(cmd)) throw new Error(`fgs ${cmd} 不在白名单`);
  if (!/^[\w.-]{1,64}$/.test(pid)) throw new Error("非法 project id");
  const env = { ...process.env, PATH: `${ROOT}/tools/bin:${process.env.PATH || ""}` };
  return await new Promise((resolve, reject) => {
    const child = spawn("fgs", [cmd, pid, "--json"], { cwd: ROOT, env, stdio: ["ignore", "pipe", "pipe"] });
    let out = "", err = "", done = false;
    const finish = (fn, v) => { if (done) return; done = true; try { child.kill("SIGKILL"); } catch {} fn(v); };
    const timer = setTimeout(() => finish(reject, new Error(`fgs ${cmd} 超时`)), timeoutMs);
    child.stdout.on("data", (d) => { out += d.toString(); if (out.length > 400000) finish(reject, new Error(`fgs ${cmd} 输出过大`)); });
    child.stderr.on("data", (d) => { err += d.toString(); if (err.length > 20000) err = err.slice(0, 20000); });
    child.on("error", (e) => { clearTimeout(timer); finish(reject, new Error(e.message)); });
    child.on("exit", (code) => {
      clearTimeout(timer);
      if (code !== 0) return finish(reject, new Error((err || "").trim().split("\n")[0] || `fgs ${cmd} 退出码 ${code}`));
      try { finish(resolve, JSON.parse(out)); } catch { finish(reject, new Error(`fgs ${cmd} 输出不是 JSON`)); }
    });
  });
}

/** 作战台快照：一次拿齐「还有多久 / 值多少钱 / 下一步打哪」三件事 */
// 知识库检索（W-P2-1）。赛中「这题该翻哪份资料」不该靠人回想。
// 冷启动实测 1.8s（首次），所以按 (词, top) 缓存 10 分钟；同词重复点不重复跑进程。
const kbCache = new Map();
async function kbSearch(q, top) {
  const key = q + "|" + top;
  const v = kbCache.get(key);
  if (v && Date.now() - v.at < 600_000) return { ...v.data, cached: true };
  let data;
  try {
    const txt = await runKb(q, top);
    data = { q, top, hits: txt };
  } catch (e) {
    data = { q, top, hits: [], error: String(e?.message || e).slice(0, 160) };
  }
  kbCache.set(key, { at: Date.now(), data });
  return data;
}
function runKb(q, top) {
  return new Promise((resolve, reject) => {
    const child = spawn("kb", ["search", q, "--json", "--top", String(top)], {
      cwd: ROOT, env: process.env, stdio: ["ignore", "pipe", "pipe"],
    });
    let out = "";
    let done = false;
    const finish = (fn, v) => { if (done) return; done = true; clearTimeout(t); try { child.kill("SIGKILL"); } catch {} fn(v); };
    child.stdout.on("data", (b) => { if (out.length < 512 * 1024) out += b.toString("utf8"); });
    child.stderr.on("data", () => {});
    child.on("error", (e) => finish(reject, e));
    child.on("close", () => {
      try {
        const j = JSON.parse(out);
        const arr = Array.isArray(j) ? j : [];
        // 只留界面要用的四样：标题 / 分类 / 文件:行号 / 分数（其余 tf 词表等一律不进前端）
        resolve(arr.slice(0, top).map((h) => ({
          title: String(h.title || h.path || ""),
          cat: String(h.cat || h.kind || ""),
          path: String(h.path || ""),
          line: Array.isArray(h.lines) ? h.lines[0] : (h.line ?? null),
          score: typeof h.score === "number" ? Math.round(h.score * 10) / 10 : null,
        })));
      } catch { finish(reject, new Error("kb 返回的不是 JSON")); }
    });
    const t = setTimeout(() => finish(reject, new Error("kb 超时（15s）")), 15_000);
  });
}

// 剩余可交次数（W-P1-1）。60s 缓存 + 12s 超时：这是**平台**依赖，
// 断网/无 token 时必须快速失败并如实说「不知道」，绝不阻塞作战条。
let flagsLeftCache = { at: 0, data: null };

/** 跑一条 fgs 子命令拿原始 stdout（不解析 JSON、允许非 JSON 错误行），带超时与输出上限 */
function fgsRaw(args, timeoutMs) {
  return new Promise((resolve, reject) => {
    const child = spawn("fgs", args, { cwd: ROOT, env: process.env, stdio: ["ignore", "pipe", "pipe"] });
    let out = "";
    let done = false;
    const finish = (fn, v) => { if (done) return; done = true; clearTimeout(timer); try { child.kill("SIGKILL"); } catch {} fn(v); };
    // 限长：平台返回的题单可能很大，只留前 256KB（我们只数 flags_left）
    child.stdout.on("data", (b) => { if (out.length < 256 * 1024) out += b.toString("utf8"); });
    child.stderr.on("data", () => { /* 失败原因看 stdout 的 ✗ 行 */ });
    child.on("error", (e) => finish(reject, e));
    child.on("close", () => finish(resolve, out));
    const timer = setTimeout(() => finish(reject, new Error("timeout")), timeoutMs);
  });
}
async function flagsLeftCached() {
  if (flagsLeftCache.data && Date.now() - flagsLeftCache.at < 60_000) return flagsLeftCache.data;
  let data;
  try {
    const out = await fgsRaw(["flags-left", "--json"], 12_000);
    const txt = String(out || "").trim();
    if (txt.startsWith("[") || txt.startsWith("{")) {
      const j = JSON.parse(txt);
      const arr = Array.isArray(j) ? j : Array.isArray(j?.tasks) ? j.tasks : [];
      const left = arr.reduce((s, t) => s + (Number(t?.flags_left ?? t?.left ?? 0) || 0), 0);
      data = { known: true, left, tasks: arr.length };
    } else {
      // 失败路径：fgs flags-left 用退出码 0 + 一行 ✗ 报错，必须按内容判断
      data = { known: false, reason: txt.split("\n")[0].slice(0, 120) || "没有输出" };
    }
  } catch (e) {
    const m = String(e?.message || e);
    data = { known: false, reason: m.includes("timeout") || m.includes("超时") ? "平台 12s 无响应" : m.slice(0, 120) };
  }
  flagsLeftCache = { at: Date.now(), data };
  return data;
}

async function opsSnapshot(wantPid) {
  const idx = await boardIndex();
  const projects = idx.projects || [];
  const pid = wantPid && /^[\w.-]{1,64}$/.test(wantPid) ? wantPid : projects[0]?.pid || null;
  const key = pid || "-";
  const hit = opsCache.get(key);
  if (hit && Date.now() - hit.at < OPS_TTL_MS) return hit.data;

  const one = async (cmd) => {
    if (!pid) return { ok: false, error: "还没有任何项目黑板（先 fgs kickoff <pid>）" };
    try { return { ok: true, data: await fgsJson(cmd, pid) }; }
    catch (e) { return { ok: false, error: e.message }; }
  };

  const [deadline, cost, next] = await Promise.all([one("deadline"), one("cost"), one("next")]);
  // ★ 2026-11-05：成本画像（每 flag 花多少）算了两轮，前端一直没显示 —— 显示层漏了。
  //   抢分时最该盯的就是「这一步花了多少钱、坐实了几条」；边际递减陡（52 元/flag → 284 元/flag）。
  //   顶层单独给一份，界面不必再钻 cost 的内部结构。
  const costImage = cost.ok ? {
    points: cost.data?.points ?? null,
    flags: cost.data?.flags ?? null,
    perFlag: cost.data?.per_flag ?? cost.data?.ppm_anchor ?? null,
    verifiedPerStep: cost.data?.verified_per_step ?? null,
    solved: cost.data?.solved ?? null,
    confirmed: cost.data?.confirmed ?? null,
  } : null;

  // 工作单元（W-P1-1）：派出去的每一路都该在作战台上看得见 —— 否则「跑了几路、谁在干活」
  // 只能靠去子代理页翻，赛中根本没那个时间。
  let units = null;
  try {
    const lanes = await fgsJson("workers", pid).catch(() => null);
    const sa = await listLiveRuns().catch(() => []);
    units = {
      lanes: Array.isArray(lanes?.lanes) ? lanes.lanes.filter((l) => !l?.closed).slice(0, 8)
        : Array.isArray(lanes) ? lanes.filter((l) => !l?.closed).slice(0, 8) : [],
      subagents: (Array.isArray(sa) ? sa : []).filter((r) => r.state === "running").slice(0, 8),
    };
  } catch (e) { units = { error: e.message }; }

  let flags = null;
  try { const f = await flagReport(50); flags = { total: f.total, unique: (f.unique || []).length, top: (f.unique || []).slice(0, 5) }; }
  catch (e) { flags = { error: e.message }; }

  // ★ W-P1-1：剩余可交次数。以前故意不接，理由是「没 token 就常驻红字」——
  //   但那是显示问题，不是数据问题。实测 fgs flags-left 失败时**退出码 0**、stdout 打一行 ✗，
  //   完全可以解析成「不知道」而不是「0」。显示「?（缺 token）」即可，既不撒谎也不吓人。
  const leftCount = await flagsLeftCached();

  const cur = projects.find((p) => p.pid === pid) || null;
  const data = {
    ts: Date.now(),
    pid,
    projects: projects.slice(0, 20).map((p) => ({
      pid: p.pid, goal: p.goal || null, updated_at: p.updated_at || null,
      nodes: p.nodes ?? null, verified: p.verified ?? null, solved: p.solved ?? null,
    })),
    board: cur ? { nodes: cur.nodes ?? null, verified: cur.verified ?? null, counts: cur.counts ?? null } : null,
    deadline, cost, costImage, next, units, flags, flagsLeft: leftCount,
    piVersion: await piVersionReal(),
  };
  opsCache.set(key, { at: Date.now(), data });
  return data;
}

// ---------------------------------------------------------------- 技能清单（看板/智能体页用）
async function listSkills() {
  const dir = path.join(ROOT, "skills");
  let names = [];
  try { names = (await readdir(dir, { withFileTypes: true })).filter((d) => d.isDirectory()).map((d) => d.name); } catch { return []; }
  const out = [];
  for (const n of names) {
    const f = path.join(dir, n, "SKILL.md");
    try {
      const st = await stat(f);
      if (!st.isFile()) continue;
      // 限长读头部 4KB（frontmatter 就在最前面；SKILL.md 虽小，纪律是任何读取都限长）
      const buf = await readHead(f, 4096);
      const fm = /^---\n([\s\S]*?)\n---/.exec(buf)?.[1] || "";
      const name = /^name:\s*(.+)$/m.exec(fm)?.[1]?.trim() || n;
      const desc = /^description:\s*(.+)$/m.exec(fm)?.[1]?.trim() || "";
      out.push({ id: n, name, description: desc.slice(0, 300), path: f, mtimeMs: st.mtimeMs });
    } catch {}
  }
  return out.sort((a, b) => a.id.localeCompare(b.id));
}

// ---------------------------------------------------------------- #43 调用次数（会话 JSONL 聚合）
/**
 * #43 累计调用次数：从当前会话 JSONL 里数 toolName → 次数。
 *
 * 为什么从会话文件数，而不是在 RPC 事件里自增：
 *  · RPC 事件流没有「累计」语义，服务一重启界面上的计数就归零，看着像没调用；
 *  · JSONL 是唯一可回溯的落盘记录（toolResult 消息带 toolName），重启后照样数得出来。
 * 为什么只读尾部 512KB：会话可能很长，整读违背 drvfs 限长纪律；尾部足够回答「这次会话在用什么工具」。
 * 「未命中调用」（技能被问到却没用上）依赖 pi 侧埋点，短期拿不到 → 清单里记成待验证，不在这里假装能算。
 */
async function sessionToolCounts(maxBytes = 512 * 1024) {
  const rt = activeRuntime();
  let p = (rt && rt.sessionPath) || "";
  if (!p) p = (await newestSubstantiveSessionFile()) || "";
  if (!p) return { calls: {}, total: 0, session: "", truncated: false };
  let truncated = false;
  try { truncated = (await stat(p)).size > maxBytes; } catch { /* 拿不到大小就按没截断 */ }
  const lines = await readSessionLines(p, maxBytes);
  const calls = {};
  const seen = new Set();   // toolCallId 去重：同一次调用可能同时有 toolCall 块与 toolResult 两条
  const bump = (name) => { if (typeof name === "string" && name) calls[name] = (calls[name] || 0) + 1; };
  for (const line of lines) {
    if (!line.startsWith("{")) continue;
    let o = null;
    try { o = JSON.parse(line); } catch { continue; }
    const m = (o && o.message) || {};
    const id0 = typeof m.toolCallId === "string" ? m.toolCallId : "";
    if (m.role === "toolResult" && typeof m.toolName === "string") {
      if (id0) { if (seen.has(id0)) continue; seen.add(id0); }
      bump(m.toolName);
      continue;
    }
    if (Array.isArray(m.content)) {
      for (const b of m.content) {
        if (!b || typeof b !== "object") continue;
        if (b.type !== "toolCall" && b.type !== "tool_use") continue;
        const id = typeof b.id === "string" ? b.id : (typeof b.toolCallId === "string" ? b.toolCallId : "");
        if (id) { if (seen.has(id)) continue; seen.add(id); }
        bump(b.name || b.toolName);
      }
    }
  }
  const total = Object.keys(calls).reduce((a, k) => a + calls[k], 0);
  return { calls, total, session: path.basename(p), truncated };
}


// ---------------------------------------------------------------- #35 旁路提问（/btw）
/**
 * #35 旁路提问：想问「这个头是不是 gzip 错位」不该把整段输出再喂一遍主线。
 *
 * 通道纪律（照 ARTEX sidequestion 的边界，只落我们真用得上的部分）：
 *  · 独立进程 + 独立临时会话目录：不写进当前会话、不进活动流、不碰黑板/任务图；
 *  · --no-tools：旁路没有工具执行路径，只能就它看到的这句话回答（防它顺手改仓库）；
 *  · 单次上限 120s，超时就杀，返回半截输出并明确标注「超时」；
 *  · 最多同时 2 路，多了直接 429 —— 旁路是省时间的，不是再开一条并行主线。
 * 没做：ARTEX 的 250ms 快照 ticker / heartbeat / run_id 防旧覆盖 —— 那是常驻快照库的需要，
 * 我们每次现起现杀，没有可被旧快照覆盖的状态。
 */
const BTW_TIMEOUT_MS = 120000;
const BTW_MAX_CONCURRENT = 2;
let btwRunning = 0;
function runSideQuestion(question, provider, model) {
  return new Promise((resolve) => {
    const dir = path.join(tmpdir(), "pictf-btw-" + process.pid + "-" + Date.now());
    const args = ["-p", question, "--no-tools", "--session-dir", dir];
    if (provider) args.push("--provider", provider);
    if (model) args.push("--model", model);
    const env = { ...process.env, PATH: ROOT + "/tools/bin:" + (process.env.PATH || "") };
    const t0 = Date.now();
    let out = "";
    let errOut = "";
    let done = false;
    let timedOut = false;
    let child = null;
    const finish = (res) => { if (done) return; done = true; clearTimeout(timer); resolve(res); };
    const timer = setTimeout(() => { timedOut = true; try { if (child) child.kill("SIGKILL"); } catch { /* 已经退了 */ } }, BTW_TIMEOUT_MS);
    try {
      child = spawn("pi", args, { cwd: ROOT, env, stdio: ["ignore", "pipe", "pipe"] });
    } catch (e) {
      return finish({ ok: false, answer: "", error: "起不来 pi：" + e.message, ms: 0, timedOut: false });
    }
    child.stdout.on("data", (c) => { out += c.toString("utf8"); if (out.length > 64 * 1024) out = out.slice(0, 64 * 1024); });
    child.stderr.on("data", (c) => { errOut += c.toString("utf8"); if (errOut.length > 8 * 1024) errOut = errOut.slice(-8 * 1024); });
    child.on("error", (e) => finish({ ok: false, answer: out.trim(), error: "pi 起不来：" + e.message, ms: Date.now() - t0, timedOut: false }));
    child.on("close", (code) => {
      const answer = out.trim();
      const ms = Date.now() - t0;
      if (timedOut) return finish({ ok: false, answer, error: "旁路问题超过 " + Math.round(BTW_TIMEOUT_MS / 1000) + "s 已被杀掉（已拿到的部分输出仍在下面）", ms, timedOut: true });
      if (code !== 0) return finish({ ok: false, answer, error: (errOut.trim() || "pi 退出码 " + code).slice(0, 500), ms, timedOut: false });
      if (!answer) return finish({ ok: false, answer: "", error: "pi 没回任何内容", ms, timedOut: false });
      return finish({ ok: true, answer, error: "", ms, timedOut: false });
    });
  });
}
// ---------------------------------------------------------------- 文件面板（只读、限长）
async function fsList(dir, limit = 500) {
  const entries = await readdir(dir, { withFileTypes: true });
  const out = [];
  for (const e of entries.slice(0, limit)) {
    let size = null, mtimeMs = null;
    try { const st = await stat(path.join(dir, e.name)); size = st.size; mtimeMs = st.mtimeMs; } catch {}
    out.push({ name: e.name, path: path.join(dir, e.name), isDir: e.isDirectory(), size, mtimeMs });
  }
  out.sort((a, b) => (b.isDir - a.isDir) || a.name.localeCompare(b.name));
  return { path: dir, entries: out };
}

// ---------------------------------------------------------------- HTTP
const MIME = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
  ".woff2": "font/woff2", ".json": "application/json; charset=utf-8", ".map": "application/json; charset=utf-8",
};

function json(res, obj, code = 200) {
  const body = JSON.stringify(obj);
  res.writeHead(code, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" });
  res.end(body);
}

/** 限长读请求体（2MB 上限） */
async function readJsonBody(req, max = 2 * 1024 * 1024) {
  const chunks = [];
  let total = 0;
  for await (const c of req) {
    total += c.length;
    if (total > max) throw new Error("payload 过大");
    chunks.push(c);
  }
  try { return JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}"); } catch { return {}; }
}

// ---------------------------------------------------------------- 同源校验（W-P0-5）
// ★ 2026-11-05：写接口原来对「谁发的」完全不设防。工作台绑回 127.0.0.1 之后，
//   仍有 CSRF 面 —— 你在浏览器里开着这个台子，随便一个恶意网页都能 POST 过来
//   （同源策略只挡「读响应」，不挡「发请求」）。上传与智能体保存是最值得先堵的两个。
//
// 只拦**浏览器跨站发起的写请求**，不拦命令行/脚本：
//   · Origin 存在但 host 与本次请求不一致 → 拒
//   · sec-fetch-site: cross-site → 拒
//   · 两个头都没有（curl / Node fetch / 服务端互调）→ 放行，因为这压根不构成 CSRF
//
// ⚠ 这不是鉴权。真正的鉴权要 token，属另一件事；这里只做「跨站伪造」这一层。
//   有没有真拦住也能自证：下面 bump 一个计数，/api/status 会把拦截次数报出来，
//   免得门禁「看起来在拦、其实一次没拦」（fgs doctor --deadweight 同一个道理）。
let crossSiteBlocked = 0;

// ★ 2026-11-05 晚补：原来只有 /api/upload 和 /api/roles/save 两处**逐条**调它，
//   25 个 POST 里 23 个裸奔 —— 任意网页（<form> 或 no-cors fetch 都能无预检发出）可以
//   让浏览器替你发 POST，而浏览器**会自动带上本机 cookie**。当时没 cookie 所以危害小，
//   但只要以后加了任何 cookie 鉴权，这条就是现成的 CSRF 通道。
//   改法：不再逐条记得调，改成在路由分发前**统一闸**一次（见 handleApi 入口），
//   这里保留原签名，逐条调用依然安全（幂等，重复调用不会双计数）。
function sameOrigin(req) {
  if (req.__originChecked) return true;   // 已统一闸过
  const selfHost = String(req.headers.host || "");
  const origin = req.headers.origin;
  if (origin) {
    let oh = "";
    try { oh = new URL(origin).host; } catch { crossSiteBlocked++; return false; }
    if (oh !== selfHost) { crossSiteBlocked++; return false; }
  }
  if (String(req.headers["sec-fetch-site"] || "") === "cross-site") { crossSiteBlocked++; return false; }
  return true;
}

// ---------------------------------------------------------------- 原始工具输出留痕（W-P0-2 补完）
// ★ 2026-11-05：三态徽标原来只能展开**账本回执**，账本里根本没有「取到它的那条命令与原始输出」。
//   AGENTS.md §4 写得很直白：「值拆自二进制/内存的，把取到它的那条命令与原始输出一起给，方便复现」。
//   所以 flag-watch 命中时顺手把上下文片段落在 log/flag-ctx/<session>/<sha1>.txt（只落片段，不落全量输出）。
//   这里只读地把它取回来，给「展开证据 → 原始输出」用。
const FLAG_CTX = path.join(ROOT, "log", "flag-ctx");

async function flagRaw(want) {
  const sid = String(want.sid || "");
  const sha = String(want.sha || "");
  if (!/^[\w.-]{1,64}$/.test(sid) || !/^[0-9a-f]{8,64}$/.test(sha)) return { error: "参数不合法" };
  const f = path.join(FLAG_CTX, sid, sha + ".txt");
  try {
    const st = await stat(f);
    if (st.size > 256 * 1024) {
      // 限长纪律：绝不把大文件整读进内存
      const fh = await open(f, "r");
      try {
        const buf = Buffer.alloc(256 * 1024);
        const { bytesRead } = await fh.read(buf, 0, buf.length, 0);
        return { text: buf.subarray(0, bytesRead).toString("utf8"), truncated: true, bytes: st.size };
      } finally { await fh.close(); }
    }
    return { text: await readFile(f, "utf8"), truncated: false, bytes: st.size };
  } catch {
    return { error: "这条命中没有留下原始输出（flag-watch 只落片段；旧记录可能没有）" };
  }
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const p = url.pathname;

  try {
    // 早期自研单页原本留在 /simple。2026-11-04：那个 index.html 从来没进过库（实测 404/500），
    // 与其抛 500 让人怀疑服务挂了，不如直接 301 到主线前端 —— 坏链接要跳，不要报错。
    // ── 同源闸门（2026-11-05 晚：从「逐条记得调」改成「统一闸一次」）──────
    // 覆盖面原来只有 2/25 条 POST；这里一刀切住所有 /api 写请求。
    // 只对写生效：GET 不改状态，而且浏览器 <img>/<link> 的 GET 本来就没 origin 头，卡了会误伤。
    if (p.startsWith("/api/") && req.method !== "GET" && req.method !== "HEAD" && req.method !== "OPTIONS") {
      if (!sameOrigin(req)) return json(res, { error: "跨站写请求被拒（同源校验）" }, 403);
      req.__originChecked = true;
    }

    if (p === "/simple" || p === "/simple/") {
      res.writeHead(301, { location: "/", "cache-control": "no-store" });
      return res.end();
    }

    // 打开一条已有会话（切会话走这里，不再用 pi 的 switch_session —— 那会打断会话）
    if (p === "/api/runtime/open" && req.method === "POST") {
      let body = {};
      try { body = await readJsonBody(req); } catch (e) { return json(res, { error: e.message }, 413); }
      const sessionPath = String(body.sessionPath || "");
      if (!sessionPath) return json(res, { error: "缺少 sessionPath" }, 400);
      try {
        const rt = await openRuntime({ sessionPath });
        const [state, messages] = await Promise.all([rt.command({ type: "get_state" }, 60000), rt.command({ type: "get_messages" }, 120000)]);
        let cwd;
        try { cwd = (await sessionCwd(sessionPath))?.cwd; } catch { /* 拿不到就让前端用现状 */ }
        return json(res, { runtimeId: rt.runtimeId, sessionPath, cwd, state: state?.data ? state : null, messages: messages?.data ? messages : null });
      } catch (e) {
        return json(res, { error: e.message }, 502);
      }
    }

    // 开一条新会话（新进程，pi 自己建文件）
    if (p === "/api/runtime/new" && req.method === "POST") {
      let body = {};
      try { body = await readJsonBody(req); } catch (e) { return json(res, { error: e.message }, 413); }
      try {
        const rt = await openRuntime({ sessionPath: null, cwd: body.cwd || null });
        const [state, messages] = await Promise.all([rt.command({ type: "get_state" }, 60000), rt.command({ type: "get_messages" }, 60000)]);
        return json(res, { runtimeId: rt.runtimeId, sessionPath: rt.sessionPath || null, state: state?.data ? state : null, messages: messages?.data ? messages : null });
      } catch (e) {
        return json(res, { error: e.message }, 502);
      }
    }

    // ── 子智能体：列表 / 详情 ──────────────────────────────────────────────
    if (p === "/api/subagents") {
      // ★ 顺手做一次「子智能体收工自动落板」（丢后台，不拖响应；内部自带 20s 限流）
      void sweepFinishedRuns().catch(() => { /* 失败不影响面板 */ });
      const [live, history, missions] = await Promise.all([listLiveRuns(), listRunHistory(60), listMissions(30)]);
      // mission 里的 runId → 摘要（含 ownerSessionId：这条 run 挂在哪条主会话下）
      const byRun = new Map();
      for (const m of missions) for (const r of m.runs) byRun.set(r.runId, { missionId: m.missionId, ownerSessionId: m.ownerSessionId, summary: m.summary, status: m.status, artifacts: m.artifacts });
      const liveIds = new Set(live.map((r) => r.runId));
      return json(res, {
        live: live.map((r) => ({ ...r, mission: byRun.get(r.runId) || null })),
        history: history.filter((h) => !liveIds.has(h.runId)),
        missions: missions.slice(0, 20).map((m) => ({ missionId: m.missionId, status: m.status, ownerSessionId: m.ownerSessionId, updatedAt: m.updatedAt, runCount: m.runs.length, summary: m.summary })),
        ts: Date.now(),
      });
    }

    // 注意顺序：**剩余部分不含斜杠**才算"详情"，否则 .../message、.../resume 会被当成 runId
    // （这坑踩过两次了：先排除 /message，结果 /resume 又中招）
    if (p.startsWith("/api/subagent/") && !p.slice("/api/subagent/".length).includes("/")) {
      const runId = decodeURIComponent(p.slice("/api/subagent/".length));
      if (!/^[\w-]{8,64}$/.test(runId)) return json(res, { error: "非法 runId" }, 400);
      const live = (await listLiveRuns()).find((r) => r.runId === runId) || null;
      const missions = await listMissions(60);
      const mission = missions.find((m) => m.runs.some((r) => r.runId === runId)) || null;
      let events = [];
      if (live?.runDir) {
        try {
          const f = path.join(live.runDir, "events.jsonl");
          const st = await stat(f);
          const cap = Math.min(st.size, 256 * 1024);
          const fh = await open(f, "r");
          try {
            const buf = Buffer.alloc(Number(cap));
            const { bytesRead } = await fh.read(buf, 0, buf.length, Math.max(0, st.size - cap));
            for (const line of buf.subarray(0, bytesRead).toString("utf8").split("\n")) {
              if (!line.trim()) continue;
              try { const e = JSON.parse(line); events.push({ type: e.type, ts: e.ts, status: e.status, durationMs: e.durationMs, message: e.message }); } catch {}
            }
          } finally { await fh.close(); }
        } catch {}
      }
      // 子智能体自己的对话（"点进去看他在干嘛"）
      const parent = live?.sessionId || mission?.ownerSessionId || null;
      const childFile = await childTranscriptPath(parent, runId);
      let child = null;
      if (childFile) {
        try {
          const msgs = await sessionHistory(childFile, 40);
          child = { path: childFile, messages: msgs.messages, model: msgs.model, thinkingLevel: msgs.thinkingLevel };
        } catch (e) { child = { path: childFile, error: e.message, messages: [] }; }
      }
      return json(res, { runId, live, mission, events: events.slice(-60), child, ts: Date.now() });
    }

    if (p.startsWith("/api/subagent/") && p.endsWith("/message") && req.method === "POST") {
      const runId = decodeURIComponent(p.slice("/api/subagent/".length, -"/message".length));
      if (!/^[\w-]{8,64}$/.test(runId)) return json(res, { error: "非法 runId" }, 400);
      const live = (await listLiveRuns()).find((r) => r.runId === runId);
      if (!live?.runDir) return json(res, { error: "找不到这条 run（可能已被清理）" }, 404);
      let body = {};
      try { body = await readJsonBody(req); } catch (e) { return json(res, { error: e.message }, 413); }
      const mode = ["steer", "follow_up", "auto"].includes(String(body.mode)) ? String(body.mode) : "steer";
      try { return json(res, await steerRun(live.runDir, body.text, mode)); }
      catch (e) { return json(res, { ok: false, error: e.message }, 400); }
    }

    if (p === "/api/tree") {
      const rt = activeRuntime();
      if (!rt) return json(res, { error: "没有活动会话进程" }, 200);
      try { return json(res, await sessionTree(rt)); }
      catch (e) { return json(res, { error: e.message }, 502); }
    }

    if (p === "/api/fork" && req.method === "POST") {
      const rt = activeRuntime();
      if (!rt) return json(res, { error: "没有活动会话进程" }, 200);
      let body = {};
      try { body = await readJsonBody(req); } catch (e) { return json(res, { error: e.message }, 413); }
      const entryId = String(body.entryId || "");
      if (!entryId) return json(res, { error: "缺少 entryId" }, 400);
      try {
        const rec = await rt.command({ type: "fork", entryId }, 60000);
        if (rec?.success === false) return json(res, { ok: false, error: rec.error || "分支失败" }, 200);
        return json(res, { ok: true, data: rec?.data ?? null });
      } catch (e) { return json(res, { ok: false, error: e.message }, 502); }
    }

    // 给"已结束"的子智能体续命：没有官方 slash 命令，只能让**它所属的主会话**调
    // subagent({action:"resume", id, message}) —— 会有一次模型往返，界面上要说清楚。
    if (p.startsWith("/api/subagent/") && p.endsWith("/resume") && req.method === "POST") {
      const runId = decodeURIComponent(p.slice("/api/subagent/".length, -"/resume".length));
      if (!/^[\w-]{8,64}$/.test(runId)) return json(res, { error: "非法 runId" }, 400);
      const live = (await listLiveRuns()).find((r) => r.runId === runId);
      const missions = await listMissions(60);
      const mission = missions.find((m) => m.runs.some((r) => r.runId === runId));
      const owner = live?.sessionId || mission?.ownerSessionId || null;
      if (!owner) return json(res, { ok: false, error: "找不到它的主会话，无法续命" }, 400);
      let body = {};
      try { body = await readJsonBody(req); } catch (e) { return json(res, { error: e.message }, 413); }
      const text = String(body.text || "").trim();
      try {
        const rt = byPath.get(owner) || await openRuntime({ sessionPath: owner });
        const msg = text
          ? `用 subagent({action:"resume", id:"${runId}", message:"${text.replace(/"/g, '\\"')}"}) 复活这条子智能体 run 并把消息交给它；做完只回一行结果。`
          : `用 subagent({action:"resume", id:"${runId}"}) 复活这条子智能体 run，让它继续把没做完的做完；做完只回一行结果。`;
        const rec = await rt.command({ type: "prompt", message: msg }, 60000);
        if (rec?.success === false) return json(res, { ok: false, error: rec.error }, 200);
        return json(res, { ok: true, dispatchedVia: owner, disposition: rec?.data?.disposition || null });
      } catch (e) { return json(res, { ok: false, error: e.message }, 502); }
    }

    if (p === "/api/runtime/close" && req.method === "POST") {
      let body = {};
      try { body = await readJsonBody(req); } catch (e) { return json(res, { error: e.message }, 413); }
      const rt = runtimes.get(String(body.runtimeId || ""));
      if (!rt) return json(res, { ok: false, error: "没这条 runtime（可能已经回收了）" }, 200);
      if (rt.busy) return json(res, { ok: false, error: "它正在跑一个回合，先中止再结束进程" }, 200);
      if (rt.runtimeId === activeId) return json(res, { ok: false, error: "这是当前正在看的会话，换一条再结束它" }, 200);
      log(`手动结束 runtime ${rt.runtimeId.slice(0, 40)}…`);
      rt.stop(true);
      runtimes.delete(rt.runtimeId);
      if (rt.sessionPath) byPath.delete(rt.sessionPath);
      return json(res, { ok: true });
    }

    // 导出会话 HTML：pi 的 export_html 只是**写了个文件**（浏览器里没法"另存为"），
    // 这里把它当附件发回去 —— 只允许读工作目录下的 pi-session-*.html，别的最多读 32MB。
    if (p === "/api/export") {
      const raw = url.searchParams.get("path") || "";
      const abs = path.resolve(path.isAbsolute(raw) ? raw : path.join(ROOT, raw));
      const base = path.basename(abs);
      // 前缀判断要带分隔符：startsWith(ROOT) 会把 <仓库根>-evil 也当仓库内
      if (!(abs === ROOT || abs.startsWith(ROOT + "/")) || !/^pi-session-[\w.-]+\.html$/.test(base)) {
        return json(res, { error: "只允许导出工作目录下的 pi-session-*.html" }, 400);
      }
      try {
        const { text } = await readCapped(abs, 32 * 1024 * 1024);
        res.writeHead(200, {
          "content-type": "text/html; charset=utf-8",
          "content-disposition": `attachment; filename="${base}"`,
          "cache-control": "no-store",
        });
        return res.end(text);
      } catch (e) { return json(res, { error: e.message }, 404); }
    }

    if (p === "/api/runtimes") {
      return json(res, {
        active: activeId,
        runtimes: [...runtimes.values()].map((rt) => ({
          runtimeId: rt.runtimeId, sessionPath: rt.sessionPath || null,
          pid: rt.child?.pid ?? null, busy: !!rt.busy, idleMs: Date.now() - rt.lastUsed,
        })),
      });
    }

    if (p === "/api/defaults") {
      const st = await pi.command({ type: "get_state" }, 15000).catch(() => null);
      const m = st?.data?.model;
      return json(res, {
        cwd: ROOT, exe: "pi", exeDir: path.dirname(process.execPath), sessionsRoot: sessionDirOf(),
        mode: "real", piPath: "pi", piKind: "pi", piVersion: await piVersionReal(), debug: false,
        lastWorkspace: ROOT, workspaces: [ROOT], defaultWorkspace: ROOT, dismissed: [],
        defaultModel: m?.id || null, defaultProvider: m?.provider || null,
        defaultThinkingLevel: st?.data?.thinkingLevel || null,
      });
    }

    if (p === "/api/sessions") return json(res, { sessions: await listSessions(Number(url.searchParams.get("limit") || 200)) });

    if (p === "/api/session-history") {
      const file = url.searchParams.get("path") || "";
      try { return json(res, await sessionHistory(file)); }
      catch (e) { return json(res, { error: e.message }, 400); }
    }

    if (p === "/api/sessions/rename" && req.method === "POST") {
      const body = await readJsonBody(req);
      try { return json(res, await renameSession(String(body.path || ""), body.name)); }
      catch (e) { return json(res, { error: e.message }, 400); }
    }

    if (p === "/api/sessions/delete" && req.method === "POST") {
      const body = await readJsonBody(req);
      try { return json(res, await deleteSession(String(body.path || ""))); }
      catch (e) { return json(res, { error: e.message }, 400); }
    }

    // 一键清空：破坏性操作，要求 body.confirm === "clear-all" 才执行，
    // 防止误触（前端会先弹确认框，但服务端也要自己把关）。
    if (p === "/api/sessions/clear-all" && req.method === "POST") {
      const body = await readJsonBody(req);
      if (String(body.confirm || "") !== "clear-all") {
        return json(res, { error: '需要 body.confirm="clear-all" 才能清空' }, 400);
      }
      try { return json(res, await clearAllSessions()); }
      catch (e) { return json(res, { error: e.message }, 500); }
    }

    if (p === "/api/upload" && req.method === "POST") {
      if (!sameOrigin(req)) return json(res, { error: "跨站写请求被拒（同源校验）" }, 403);
      const body = await readJsonBody(req, 200 * 1024 * 1024);
      try { return json(res, { files: await saveUploads(body.files) }); }
      catch (e) { return json(res, { error: e.message }, 400); }
    }

    if (p === "/api/flags") return json(res, await flagReport(Number(url.searchParams.get("limit") || 300)));

    // 作战台聚合（W-P0-1）：只读，三路各自独立失败，界面据 ok 显示「不知道」而不是 0
    if (p === "/api/ops") {
      try { return json(res, await opsSnapshot(url.searchParams.get("pid") || "")); }
      catch (e) { return json(res, { error: e.message }, 500); }
    }

    // 知识库检索（W-P2-1）：只读。?q=词&top=3
    if (p === "/api/kb") {
      const q = String(url.searchParams.get("q") || "").trim();
      const top = Math.min(10, Math.max(1, Number(url.searchParams.get("top") || 3) || 3));
      if (!q) return json(res, { error: "缺 q" }, 400);
      if (q.length > 120) return json(res, { error: "q 太长（≤120）" }, 400);
      try { return json(res, await kbSearch(q, top)); } catch (e) { return json(res, { error: String(e?.message || e) }, 500); }
    }

    // 原始工具输出片段（W-P0-2 补完）：只读，按 <session>/<sha1> 取回命中处上下文
    if (p === "/api/flags/raw") {
      try { return json(res, await flagRaw({ sid: url.searchParams.get("sid"), sha: url.searchParams.get("sha") })); }
      catch (e) { return json(res, { error: e.message }, 500); }
    }

    // flag 三态账本（W-P0-2）：只读。accepted/pending/refuted 三态 + 平台原始回执
    if (p === "/api/flags/ledger") {
      try { return json(res, await flagLedger(url.searchParams.get("pid") || "")); }
      catch (e) { return json(res, { error: e.message }, 500); }
    }

    // 一键清空「记录」：扫描命中 + flag 账本。要求 confirm 才执行（破坏性操作）。
    if (p === "/api/flags/clear-all" && req.method === "POST") {
      const body = await readJsonBody(req);
      if (String(body.confirm || "") !== "clear-all") {
        return json(res, { error: '需要 body.confirm="clear-all" 才能清空' }, 400);
      }
      try { return json(res, await clearFlagRecords()); }
      catch (e) { return json(res, { error: e.message }, 500); }
    }

    // 「下一刀」候选（W-P0-3）：只读。黑板上当前可动的三刀 + 证据强度
    if (p === "/api/moves") {
      try { return json(res, await moves(url.searchParams.get("pid") || "")); }
      catch (e) { return json(res, { error: e.message }, 500); }
    }

    if (p === "/api/fgs" && req.method === "POST") {
      const body = await readJsonBody(req);
      try { return json(res, await runFgs(String(body.cmd || ""), String(body.pid || ""))); }
      catch (e) { return json(res, { ok: false, error: e.message }, 400); }
    }

    if (p === "/api/skills") {
      // #43 技能页给「本次会话调用了几次」：calls=0 就是「未命中」（技能在，但这次没用上）
      const agg = await sessionToolCounts().catch(() => ({ calls: {}, total: 0, session: "", truncated: false }));
      const skills = (await listSkills()).map((s) => ({ ...s, calls: agg.calls[s.id] || 0 }));
      return json(res, { skills, callsTotal: agg.total, callsSession: agg.session, callsTruncated: agg.truncated });
    }

    // ── 文件面板/缩略图要用的几个真接口（pi-web 也是走 HTTP 预览，不塞大 dataUrl）──
    const MIME_BY_EXT = {
      ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
      ".webp": "image/webp", ".svg": "image/svg+xml", ".bmp": "image/bmp", ".ico": "image/x-icon",
      ".pdf": "application/pdf", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".mp4": "video/mp4",
      ".txt": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8", ".json": "application/json",
      ".zip": "application/zip", ".html": "text/html; charset=utf-8",
    };
    /** 允许预览的路径：工作目录内，或会话目录内（附件落在 work/intake，截图/证据在仓库里） */
    const allowRead = (file) => {
      const abs = path.isAbsolute(file) ? file : path.join(ROOT, file);
      // 允许：仓库、会话目录、以及 D 盘/root/tmp —— 素材（截图、附件、证据）实际就散在这几处；
      // 这是本地单人工作台，读得到才有用（写操作另有各接口自己的约束）。
      const okPrefix = [ROOT, sessionDirOf(), FALLBACK_SESSION_DIR, "/mnt/d", "/root", "/tmp"]
        .some((d) => abs === d || abs.startsWith(d + "/"));
      return okPrefix ? abs : null;
    };

    if (p === "/api/fs/raw") {
      const abs = allowRead(url.searchParams.get("path") || "");
      if (!abs) return json(res, { error: "路径不在允许范围" }, 400);
      try {
        const { buf, size } = await readFileCapped(abs, 32 * 1024 * 1024);
        res.writeHead(200, {
          "content-type": MIME_BY_EXT[path.extname(abs).toLowerCase()] || "application/octet-stream",
          "content-length": String(size), "cache-control": "no-store",
        });
        return res.end(buf);
      } catch (e) { return json(res, { error: e.message }, 404); }
    }

    if (p === "/api/fs/stat") {
      const abs = allowRead(url.searchParams.get("path") || "");
      if (!abs) return json(res, { error: "路径不在允许范围" }, 400);
      try {
        const st = await stat(abs);
        return json(res, { exists: true, isDir: st.isDirectory(), size: st.size, mtimeMs: st.mtimeMs });
      } catch { return json(res, { exists: false, isDir: false }); }
    }

    if (p === "/api/fs/search") {
      const q = (url.searchParams.get("q") || "").toLowerCase();
      const limit = Math.min(Number(url.searchParams.get("limit") || 40), 200);
      if (q.length < 2) return json(res, { files: [], note: "至少 2 个字符" });
      const hits = [];
      const skip = new Set(["node_modules", ".git", ".trash", "dist", "__pycache__"]);
      const walk = async (dir, depth) => {
        if (hits.length >= limit || depth > 6) return;
        let entries = [];
        try { entries = await readdir(dir, { withFileTypes: true }); } catch { return; }
        for (const e of entries.slice(0, 400)) {
          if (hits.length >= limit) return;
          if (skip.has(e.name)) continue;
          const full = path.join(dir, e.name);
          if (e.isDirectory()) await walk(full, depth + 1);
          else if (e.name.toLowerCase().includes(q)) hits.push(full);
        }
      };
      await walk(ROOT, 0);
      return json(res, { files: hits, truncated: hits.length >= limit });
    }

    // ── 模型目录 + 配置（设置里的「模型」页）──
    // 行 = pi 运行时「可用」的提供商（get_available_models）∪ models.json 里写过的提供商。
    // 只看运行时的话，密钥写错的提供商就从界面消失了（用户以为没保存上）。
    if (p === "/api/models") {
      try {
        const rt = activeRuntime();
        const rec = rt ? await rt.command({ type: "get_available_models" }, 40000) : null;
        const models = rec?.data?.models || [];
        const byProv = new Map();
        const ensure = (pid) => {
          if (!byProv.has(pid)) {
            byProv.set(pid, {
              id: pid, name: pid, baseUrl: "", api: "",
              configured: false, hasKey: false, keySource: null, keyHint: "",
              removable: false, configBaseUrl: "", configApi: "",
              modelCount: 0, models: [],
            });
          }
          return byProv.get(pid);
        };
        for (const m of models) {
          const row = ensure(m.provider || "?");
          if (m.api === "pi-virtual" && m.provider) virtualProviders.add(m.provider);
          if (!row.baseUrl && m.baseUrl) row.baseUrl = m.baseUrl;
          if (!row.api && m.api) row.api = m.api;
          row.models.push({
            id: m.id, name: m.name || m.id, contextWindow: m.contextWindow || null,
            reasoning: !!m.reasoning, cost: m.cost || null, input: m.input || ["text"],
          });
        }
        let configError = "";
        let cfgProviders = {};
        /** 登录凭据（auth.json）读一次，循环里不再重复读盘 */
        let authCfg = {};
        try { authCfg = JSON.parse(await readFile(AUTH_PATH, "utf8")) || {}; } catch { /* 没登录过就没有这份文件 */ }
        try {
          const cfg = await readModelsConfig();
          cfgProviders = cfg.providers || {};
          for (const [pid, entry] of Object.entries(cfgProviders)) {
            const row = ensure(pid);
            row.removable = true;
            if (typeof entry?.name === "string" && entry.name.trim()) row.name = entry.name.trim();
            row.configBaseUrl = typeof entry?.baseUrl === "string" ? entry.baseUrl : "";
            row.configApi = typeof entry?.api === "string" ? entry.api : "";
            if (row.configBaseUrl) row.baseUrl = row.configBaseUrl;
            if (row.configApi) row.api = row.configApi;
            if (!row.models.length && Array.isArray(entry?.models)) row.modelCount = entry.models.length;
          }
        } catch (e) { configError = e.message; }

        // 密钥状态统一在这里算（两遍之后），因为 pi 认识但 models.json 里没有的提供商
        // （kimi-coding 这类内置目录提供商）也要有状态——历史 bug 就是它们没状态、
        // 界面用 configured 代替，显示成绿点但其实一把密钥都没有。
        for (const row of byProv.values()) {
          // 虚拟 provider：没有密钥可言，别让它走到 keyAvailability 的“未配置密钥”分支
          if (virtualProviders.has(row.id) || row.api === "pi-virtual") {
            Object.assign(row, {
              hasKey: false, keySource: null, viaAuth: false, envKey: null,
              virtual: true, usable: true,
              keyHint: "虚拟模型：由扩展按你的配置挑一个真实模型，不需要密钥",
            });
            continue;
          }
          Object.assign(row, keyAvailability(row.id, cfgProviders[row.id] || null, authCfg));
        }
        const available = new Set(models.map(m => m.provider));
        for (const row of byProv.values()) {
          row.configured = available.has(row.id);
          if (!row.modelCount) row.modelCount = row.models.length;
        }
        // 连通性记忆：只回「确实记过」的；没有 = null，前端显示「—」，不冒充「正常」
        let health = {};
        try {
          const gs = await readGuiState();
          health = (gs.modelHealth && typeof gs.modelHealth === "object") ? gs.modelHealth : {};
        } catch { /* 没有记忆就没有 */ }
        for (const row of byProv.values()) row.health = health[row.id] || null;
        const st = rt ? await rt.command({ type: "get_state" }, 20000) : null;
        return json(res, {
          providers: [...byProv.values()].sort((a, b) => (Number(b.configured) - Number(a.configured)) || a.id.localeCompare(b.id)),
          current: st?.data?.model ? { provider: st.data.model.provider, id: st.data.model.id } : null,
          modelsPath: MODELS_PATH,
          agentDir: AGENT_DIR,
          configError: configError || undefined,
        });
      } catch (e) { return json(res, { error: e.message }, 502); }
    }

    /**
     * 只回「哪些提供商手里有凭据」，**不惊动 pi**（不跑 get_available_models）。
     * 用途：输入框的模型下拉打开那一刻就要标出「这家没密钥」。
     * 为什么不复用 /api/models：那个会 await pi 的 RPC（40s 超时），打开下拉会卡。
     * 不在这个表里的 provider（如 kimi-coding 这类只有 pi 认识的内置目录提供商）
     * = 三个凭据来源都没命中，前端按「没密钥」标——这正是我们要的。
     */
    if (p === "/api/models/keys") {
      try {
        // 虚拟 provider 也要报成“可用”，否则对话页的模型下拉会把 ctf/* 误标成「未配置密钥」
        await refreshVirtualProviders(activeRuntime());
        const cfg = await readModelsConfig();
        let authCfg = {};
        try { authCfg = JSON.parse(await readFile(AUTH_PATH, "utf8")) || {}; } catch { /* 没登录过就没有这份文件 */ }
        const providers = cfg.providers || {};
        const keys = {};
        for (const id of new Set([...Object.keys(providers), ...Object.keys(authCfg || {}), ...virtualProviders])) {
          if (virtualProviders.has(id)) {
            keys[id] = { hasKey: false, keySource: null, viaAuth: false, envKey: null, usable: true, virtual: true, keyHint: "虚拟模型，不需要密钥" };
            continue;
          }
          keys[id] = keyAvailability(id, providers[id] || null, authCfg);
        }
        return json(res, { keys });
      } catch (e) { return json(res, { error: e.message }, 400); }
    }

    // 内置提供商目录（「添加模型提供商」的下拉用）
    if (p === "/api/models/catalog") {
      try {
        const { ids, source } = await providerCatalog();
        return json(res, { providers: ids, source });
      } catch (e) { return json(res, { providers: CATALOG_FALLBACK, source: "fallback", error: e.message }); }
    }

    // 新增 / 更新一个提供商（写 models.json；写入前自动备份，新会话生效）
    /**
     * 运行中换模型（#27）：从**下一次 LLM 调用**生效，不重启进程、不丢上下文。
     *
     * 为什么必须走 set_model RPC 而不是 /api/start：applyStart 会换启动参数 → 重启 pi →
     * 当前会话进程里的上下文全部丢掉。赛中最痛的正是「跑着跑着 400 / 额度尽 / 模型开始胡说」，
     * 重开一题等于把已经收集的证据和推理一起扔掉；RPC 换模型则连当前这轮都能继续。
     */
    if (p === "/api/models/active" && req.method === "POST") {
      let body;
      try { body = await readJsonBody(req); } catch (e) { return json(res, { ok: false, error: e.message }, 413); }
      const provider = String(body?.provider || "").trim();
      const modelId = String(body?.modelId || body?.model || "").trim();
      if (!provider || !modelId) return json(res, { ok: false, error: "缺少 provider 或 modelId" }, 400);
      const target = body?.runtimeId ? runtimes.get(String(body.runtimeId)) : activeRuntime();
      if (!target || !target.child) {
        return json(res, { ok: false, error: "当前没有正在运行的会话进程：先发一条指令或点启动，再换模型" }, 409);
      }
      let before = null;
      try {
        const st = await target.command({ type: "get_state" }, 8000);
        if (st?.data?.model) before = { provider: st.data.model.provider, id: st.data.model.id };
      } catch { /* 拿不到现状不挡切换，只少一行回显 */ }
      let rec = null;
      try { rec = await target.command({ type: "set_model", provider, modelId }, 20000); }
      catch (e) { return json(res, { ok: false, error: `切换失败：${e.message}`, before }, 502); }
      if (rec && rec.success === false) {
        return json(res, { ok: false, error: rec.error || "pi 拒绝了这次切换", before }, 502);
      }
      let after = null;
      try {
        const st = await target.command({ type: "get_state" }, 8000);
        if (st?.data?.model) after = { provider: st.data.model.provider, id: st.data.model.id };
      } catch { /* 状态没问出来就按「已下发」回报，别谎称已验证 */ }
      const landed = !!(after && after.provider === provider && after.id === modelId);
      // 生效边界必须说清：人容易以为「当前这轮也变了」，实际 RPC 只影响下一次调用
      const note = `已切到 ${provider}/${modelId}，从下一轮 LLM 调用生效`
        + (after ? (landed ? "" : `（注意：pi 当前状态仍是 ${after.provider}/${after.id}，可能下一轮才收敛）`) : "（状态未回读，无法确认已收敛）");
      log(`运行中换模型 ${before ? before.provider + "/" + before.id : "?"} → ${provider}/${modelId}（landed=${landed}）`);
      return json(res, { ok: true, before, after, landed, note, runtimeId: target.runtimeId });
    }

    if (p === "/api/models/provider" && req.method === "POST") {
      try {
        const body = await readJsonBody(req);
        const provider = String(body.provider || "").trim();
        const mode = body.mode === "custom" ? "custom" : "catalog";
        if (!/^[a-z][a-z0-9-]*$/.test(provider)) {
          return json(res, { error: "提供商 ID 需以小写字母开头，只能用 a-z、0-9 和短横线" }, 400);
        }
        const apiKey = typeof body.apiKey === "string" ? body.apiKey.trim() : "";
        const baseUrl = typeof body.baseUrl === "string" ? body.baseUrl.trim() : "";
        const api = typeof body.api === "string" ? body.api.trim() : "";
        const displayName = typeof body.displayName === "string" ? body.displayName.trim() : "";
        const modelIds = Array.isArray(body.models)
          ? body.models.map(v => String(v ?? "").trim()).filter(Boolean)
          : [];
        if (baseUrl && !/^https?:\/\//i.test(baseUrl)) return json(res, { error: "API 地址需以 http(s):// 开头" }, 400);
        if (mode === "custom") {
          if (!baseUrl) return json(res, { error: "自定义模型 API 需要填写 API 地址" }, 400);
          if (!modelIds.length) return json(res, { error: "自定义模型 API 至少需要一个模型" }, 400);
          if (new Set(modelIds).size !== modelIds.length) return json(res, { error: "模型 ID 不能重复" }, 400);
        }
        const existed = await withModelsWrite(async () => {
          const cfg = await readModelsConfig();
          const exists = Object.prototype.hasOwnProperty.call(cfg.providers, provider);
          const entry = { ...(exists ? cfg.providers[provider] : null) };
          if (displayName) entry.name = displayName;
          if (baseUrl) entry.baseUrl = baseUrl;
          if (body.clearBaseUrl === true) delete entry.baseUrl;
          if (api) entry.api = api;
          if (body.clearApi === true) delete entry.api;
          if (apiKey) entry.apiKey = apiKey;                    // 密钥留空 = 保持已存的（写只写的）
          if (mode === "custom") {
            // 同名模型保留原有字段：名字/上下文窗口/价格是手工加过的，别被重建成只剩 id
            const prev = new Map((Array.isArray(entry.models) ? entry.models : [])
              .filter(m => m && typeof m === "object")
              .map(m => [String(m.id ?? ""), m]));
            entry.models = modelIds.map(id => ({ ...(prev.get(id) || {}), id }));
          }
          // 与 pi 的校验对齐（applyModelsJson）：只有 api/name 的空壳会让 pi 报配置错误
          const has = (k) => {
            const v = entry[k];
            if (v === undefined || v === null) return false;
            if (Array.isArray(v)) return v.length > 0;
            if (typeof v === "string") return v.length > 0;
            return true;
          };
          const meaningful = ["baseUrl", "headers", "compat", "modelOverrides", "models", "apiKey", "oauth", "authHeader"].some(has);
          if (!meaningful) throw new Error("至少要填一项（API 密钥，或地址 / 模型）");
          cfg.providers[provider] = entry;
          await writeModelsConfig(cfg);
          return exists;
        });
        return json(res, { ok: true, provider, existed, modelsPath: MODELS_PATH });
      } catch (e) { return json(res, { error: e.message }, 400); }
    }

    /**
     * 测一次提供商的密钥（填/保存时立刻验证）。
     *
     * 为什么必须做：kimi（Moonshot 开放平台，api.moonshot.cn）与 kimi-coding
     * （Kimi for Coding，api.kimi.com/coding）是**两套互不通用的密钥**，端点也不同；
     * 贴错时只有在发消息那一刻才收到一句 401 invalid_authentication_error，
     * 用户根本分不清是「密钥无效」还是「选错了提供商」。把探测提前到填写阶段，
     * 错误就能就地讲清楚（实测：Moonshot 的 sk- key 打 kimi-coding 必 401）。
     */
    if (p === "/api/models/test" && req.method === "POST") {
      try {
        const body = await readJsonBody(req);
        const provider = String(body.provider || "").trim();
        if (!provider) return json(res, { error: "缺少 provider" }, 400);
        const cfg = await readModelsConfig();
        const entry = cfg.providers?.[provider] || null;
        const raw = typeof entry?.apiKey === "string" ? entry.apiKey.trim() : "";
        // 表单里刚敲进去的值优先（用户还没保存就能先验一次）
        const typed = typeof body.apiKey === "string" ? body.apiKey.trim() : "";
        let key = typed || raw;
        if (!typed && raw.startsWith("$")) {
          const name = raw.slice(1).replace(/^\{/, "").split("}")[0].split(/[^A-Za-z0-9_]/)[0];
          key = (name && process.env[name]) || "";
          if (!key) return json(res, { ok: false, reason: "no-key", error: `环境变量 ${name} 没有值`, next: testFailNext("no-key") });
        } else if (!typed && raw.startsWith("!")) {
          return json(res, { ok: false, reason: "opaque", error: "该提供商的密钥由外部命令提供，界面不代跑", next: testFailNext("opaque") });
        }
        // models.json 里没有时还看两个 pi 认的来源：环境变量、auth.json（/login 存的）。
        // 不看的话，面板说 deepseek 凭据可用、点「测试」却说没密钥，自相矛盾。
        if (!key) {
          const envName = providerEnvNames(provider).find((n) => process.env[n]);
          if (envName) key = process.env[envName] || "";
        }
        if (!key) {
          try {
            const auth = JSON.parse(await readFile(AUTH_PATH, "utf8")) || {};
            const a = auth[provider];
            if (a && typeof a === "object" && typeof a.key === "string") key = a.key.trim();
          } catch { /* 没登录过就没有这份文件 */ }
        }
        if (!key) return json(res, { ok: false, reason: "no-key", error: "这条提供商还没填密钥", next: testFailNext("no-key") });

        // 端点 / 协议：models.json 优先；内置目录提供商（如 kimi-coding）的 baseUrl 只有 pi 知道
        let baseUrl = String(entry?.baseUrl || "");
        let api = String(entry?.api || "");
        let modelId = String(entry?.models?.[0]?.id || "");
        if (!baseUrl || !api || !modelId) {
          try {
            const rt = activeRuntime();
            const rec = rt ? await rt.command({ type: "get_available_models" }, 30000) : null;
            const m = (rec?.data?.models || []).find((x) => x.provider === provider);
            if (m) { baseUrl = baseUrl || m.baseUrl || ""; api = api || m.api || ""; modelId = modelId || m.id || ""; }
          } catch { /* 拿不到就问不出端点，下面统一报错 */ }
        }
        if (!baseUrl) return json(res, { ok: false, reason: "no-endpoint", error: "查不到这个提供商的 API 地址", next: testFailNext("no-endpoint") });

        const base = baseUrl.replace(/\/+$/, "");
        const anthropic = api === "anthropic-messages";
        const withV1 = (p) => (base.endsWith("/v1") ? `${base}${p}` : `${base}/v1${p}`);
        const url = anthropic ? withV1("/messages") : withV1("/models");
        const headers = anthropic
          ? { "content-type": "application/json", "anthropic-version": "2023-06-01", "x-api-key": key }
          : { authorization: `Bearer ${key}` };
        const init = anthropic
          ? { method: "POST", headers, body: JSON.stringify({ model: modelId || "k3", max_tokens: 1, messages: [{ role: "user", content: "hi" }] }) }
          : { method: "GET", headers };
        let r;
        let text = "";
        const t0 = Date.now();
        try {
          r = await fetch(url, { ...init, signal: AbortSignal.timeout(15000) });
          text = (await r.text()).slice(0, 400);
        } catch (e) {
          const ms = Date.now() - t0;
          await noteModelFail(provider, "network", e.message, 0);
          return json(res, { ok: false, reason: "network", endpoint: url, error: `请求失败：${e.message}`, ms, next: testFailNext("network", 0) });
        }
        const ms = Date.now() - t0;
        if (r.ok) {
          // 「看得见模型确实说了话」才算通：anthropic 那条 POST 带回了回复正文，取来展示
          let reply = "";
          if (anthropic) { try { const j = JSON.parse(text); reply = String(j?.content?.[0]?.text || "").slice(0, 200); } catch { /* 回包不是 JSON 就不展示正文 */ } }
          await noteModelOk(provider, ms);
          return json(res, { ok: true, status: r.status, endpoint: url, provider, model: modelId || undefined, ms, reply: reply || undefined });
        }
        let msg = text;
        try { const j = JSON.parse(text); msg = j?.error?.message || j?.message || text; } catch { /* 非 JSON 就原样带回去 */ }
        await noteModelFail(provider, "http-" + r.status, msg, r.status);
        return json(res, { ok: false, status: r.status, endpoint: url, provider, error: msg, ms, next: testFailNext("", r.status) });
      } catch (e) { return json(res, { error: e.message }, 400); }
    }

    /**
     * 子智能体派发策略（pi-subagents 的 `subagents` 段）
     *
     * 为什么值得单独一个页面：这段配置决定「派子智能体能用哪些模型」，而它写在
     * `.pi/settings.json` 里、界面上原本完全看不见——被拒时只看到一句
     * “模型策略只允许 …”，还得去翻 JSON 才知道白名单到底是什么（实测踩过）。
     * schema 以 pi-subagents 的 docs/configuration.md 为准：modelScope{enforce,allow}、
     * defaultModel / defaultProvider / defaultThinking / maxThinking；allow 支持
     * `provider/*` 通配，以及两个特殊值：`inherit`（跟主会话模型）、`scoped`（跟会话的模型作用域）。
     */
    if (p === "/api/subagent-policy") {
      if (req.method === "GET") {
        try {
          const s = await readSettingsConfig();
          const sub = (s.subagents && typeof s.subagents === "object" && !Array.isArray(s.subagents)) ? s.subagents : {};
          const scope = (sub.modelScope && typeof sub.modelScope === "object" && !Array.isArray(sub.modelScope)) ? sub.modelScope : {};
          return json(res, {
            path: SETTINGS_PATH,
            subagents: {
              defaultModel: typeof sub.defaultModel === "string" ? sub.defaultModel : "",
              defaultProvider: typeof sub.defaultProvider === "string" ? sub.defaultProvider : "",
              defaultThinking: typeof sub.defaultThinking === "string" ? sub.defaultThinking : "",
              maxThinking: typeof sub.maxThinking === "string" ? sub.maxThinking : "",
              disableThinking: sub.disableThinking === true,
              modelScope: {
                enforce: scope.enforce !== false,
                allow: Array.isArray(scope.allow) ? scope.allow.filter((v) => typeof v === "string") : [],
              },
            },
          });
        } catch (e) { return json(res, { error: e.message }, 400); }
      }
      if (req.method === "POST") {
        let body;
        try { body = await readJsonBody(req); } catch (e) { return json(res, { error: e.message }, 413); }
        try {
          // 白名单条目校验：只认 provider/* 、provider/model，以及两个特殊值
          let allow;
          if (body?.modelScope?.allow !== undefined) {
            if (!Array.isArray(body.modelScope.allow)) return json(res, { error: "modelScope.allow 必须是数组" }, 400);
            allow = [];
            for (const raw of body.modelScope.allow) {
              const item = String(raw ?? "").trim();
              if (!item) continue;
              const ok = /^[A-Za-z0-9._-]+\/(\*|[A-Za-z0-9._:-]+)$/.test(item) || item === "inherit" || item === "scoped";
              if (!ok) return json(res, { error: `白名单条目不合法：「${item}」——应为 provider/* 、provider/model 、inherit 或 scoped` }, 400);
              if (!allow.includes(item)) allow.push(item);
            }
          }
          const str = (v) => (typeof v === "string" ? v.trim() : undefined);
          const updated = await withSettingsWrite(async () => {
            const cfg = await readSettingsConfig();
            const sub = (cfg.subagents && typeof cfg.subagents === "object" && !Array.isArray(cfg.subagents)) ? cfg.subagents : {};
            const setOrDelete = (obj, k, v) => { if (v === undefined) return; if (v) obj[k] = v; else delete obj[k]; };
            setOrDelete(sub, "defaultModel", str(body.defaultModel));
            setOrDelete(sub, "defaultProvider", str(body.defaultProvider));
            setOrDelete(sub, "defaultThinking", str(body.defaultThinking));
            setOrDelete(sub, "maxThinking", str(body.maxThinking));
            if (body.disableThinking !== undefined) {
              if (body.disableThinking) sub.disableThinking = true; else delete sub.disableThinking;
            }
            const scope = (sub.modelScope && typeof sub.modelScope === "object" && !Array.isArray(sub.modelScope)) ? sub.modelScope : {};
            if (body?.modelScope?.enforce !== undefined) scope.enforce = !!body.modelScope.enforce;
            if (allow !== undefined) scope.allow = allow;
            if (Object.keys(scope).length) sub.modelScope = scope; else delete sub.modelScope;
            if (Object.keys(sub).length) cfg.subagents = sub; else delete cfg.subagents;
            await writeSettingsConfig(cfg);
            return cfg;
          });
          // 越界只警告不拦：按文档，显式传的模型越界启动即 abort，而 defaultModel 越界只是 warn
          const sub = updated.subagents || {};
          const allowList = Array.isArray(sub.modelScope?.allow) ? sub.modelScope.allow : [];
          const dm = String(sub.defaultModel || "");
          const covered = !dm || allowList.length === 0 || allowList.some((pat) => {
            if (pat === "inherit" || pat === "scoped") return true;
            const [prov, rest] = String(pat).split("/");
            const [dprov, drest] = dm.split("/");
            return prov === dprov && (rest === "*" || rest === drest);
          });
          return json(res, {
            ok: true, path: SETTINGS_PATH, subagents: sub,
            warning: covered ? undefined : `默认派发模型「${dm}」不在白名单里——显式指定它会启动即被拒`,
          });
        } catch (e) { return json(res, { error: e.message }, 400); }
      }
      return json(res, { error: "只支持 GET / POST" }, 405);
    }

    // 删除一个提供商（只删 models.json 里可编辑的那种；环境变量/内置目录提供的不给删）
    if (p === "/api/models/provider/delete" && req.method === "POST") {
      try {
        const body = await readJsonBody(req);
        const provider = String(body.provider || "").trim();
        if (!provider) return json(res, { error: "缺少 provider" }, 400);
        const removed = await withModelsWrite(async () => {
          const cfg = await readModelsConfig();
          if (!Object.prototype.hasOwnProperty.call(cfg.providers, provider)) return false;
          delete cfg.providers[provider];
          await writeModelsConfig(cfg);
          return true;
        });
        if (!removed) return json(res, { error: "这个提供商不在 models.json 里（可能来自环境变量或内置目录），这张页面删不了" }, 400);
        return json(res, { ok: true, provider, modelsPath: MODELS_PATH });
      } catch (e) { return json(res, { error: e.message }, 400); }
    }

    // ── 智能体（智能体页 / 输入框徽标）──
    // 存盘：<agentDir>/roles/<id>.json + prompts/<id>.md；选择与会话记忆：gui-state.json
    if (p === "/api/roles") {
      try {
        const roles = await loadRoles();
        const state = await readGuiState();
        const workspace = currentWorkspacePath();
        return json(res, {
          roles,
          roleByWorkspace: state.lastRoleByWorkspace || {},
          roleBySession: state.roleBySession || {},
          workspace,
          selected: (state.lastRoleByWorkspace || {})[workspaceKeyOf(workspace)] || "",
          processRole: activeRuntime()?.opts?.roleId || "",
        });
      } catch (e) { return json(res, { error: e.message }, 500); }
    }

    if (p === "/api/roles/get") {
      try {
        const role = await findRole(url.searchParams.get("id") || "");
        if (!role) return json(res, { error: `智能体不存在：${url.searchParams.get("id") || ""}` }, 404);
        return json(res, role);
      } catch (e) { return json(res, { error: e.message }, 400); }
    }

    if (p === "/api/roles/save" && req.method === "POST") {
      if (!sameOrigin(req)) return json(res, { error: "跨站写请求被拒（同源校验）" }, 403);
      try {
        const body = await readJsonBody(req);
        await saveRole(body.role ?? {}, body.prompt ?? "");
        return json(res, await findRole(body.role?.id));
      } catch (e) { return json(res, { error: e.message }, 400); }
    }

    if (p === "/api/roles/delete" && req.method === "POST") {
      try {
        const body = await readJsonBody(req);
        await deleteRole(body.id);
        return json(res, { ok: true });
      } catch (e) { return json(res, { error: e.message }, 400); }
    }

    // 恢复出厂智能体：把 roles-pack（compose 只读挂载）里的预设补回 <agentDir>/roles。
    // 只增不改 —— 已有的同名角色跳过，返回里如实分开报 restored / skipped / failed。
    if (p === "/api/roles/reset" && req.method === "POST") {
      if (!sameOrigin(req)) return json(res, { error: "跨站写请求被拒（同源校验）" }, 403);
      try {
        const body = await readJsonBody(req);
        return json(res, { ok: true, ...(await resetRolesFromPack({ overwrite: body?.overwrite === true })) });
      } catch (e) { return json(res, { error: e.message }, 400); }
    }

    // 把某个智能体设成"当前工作区选中的智能体"（空串 = 不使用智能体）
    if (p === "/api/roles/active" && req.method === "POST") {
      try {
        const body = await readJsonBody(req);
        const raw = String(body.id ?? "").trim().toLowerCase();
        if (raw && !(await findRole(raw))) return json(res, { error: `智能体不存在：${raw}` }, 404);
        const key = workspaceKeyOf(currentWorkspacePath());
        await patchGuiState((state) => {
          if (raw) state.lastRoleByWorkspace[key] = raw; else delete state.lastRoleByWorkspace[key];
        });
        return json(res, { ok: true, selected: raw, workspaceKey: key });
      } catch (e) { return json(res, { error: e.message }, 400); }
    }

    // 记住"这条会话用的是哪个智能体"（重启后也能按它恢复）
    if (p === "/api/roles/session" && req.method === "POST") {
      try {
        const body = await readJsonBody(req);
        const file = String(body.path || "").trim();
        if (!file) return json(res, { error: "缺少会话路径" }, 400);
        const raw = String(body.roleId ?? "").trim().toLowerCase();
        if (raw && !(await findRole(raw))) return json(res, { error: `智能体不存在：${raw}` }, 404);
        await patchGuiState((state) => {
          if (raw) state.roleBySession[file] = raw; else delete state.roleBySession[file];
        });
        return json(res, { ok: true });
      } catch (e) { return json(res, { error: e.message }, 400); }
    }

    if (p === "/api/roles/export" && req.method === "POST") {
      try {
        const body = await readJsonBody(req);
        return json(res, await exportRoles(body.roleIds ?? [], body.path ?? ""));
      } catch (e) { return json(res, { error: e.message }, 400); }
    }

    if (p === "/api/roles/import" && req.method === "POST") {
      try {
        const body = await readJsonBody(req);
        return json(res, await importRoles(body.source ?? ""));
      } catch (e) { return json(res, { error: e.message }, 400); }
    }

    // 编译预览（智能体页「能力」页签）：这台机器上实际会用什么
    if (p === "/api/roles/resolve") {
      try {
        const role = await findRole(url.searchParams.get("id") || "");
        if (!role) return json(res, { error: `智能体不存在：${url.searchParams.get("id") || ""}` }, 404);
        const compiled = await compileRole(role);
        return json(res, {
          roleId: role.id,
          // 实际拼出来的 pi 启动参数（调试"智能体到底带没带上"用；零敏感信息）
          args: compiled.args,
          capabilities: compiled.capabilities,
          resolutions: compiled.resolutions,
          environment: compiled.environment,
          warnings: compiled.warnings,
          effective: compiled.effective,
        });
      } catch (e) { return json(res, { error: e.message }, 400); }
    }

    // 工具 / 能力目录（智能体页「能力」页签用；也供将来的工具页）
    if (p === "/api/tools") {
      try {
        // #43 顺手带上本次会话的工具调用次数（前端角标用）；数不出来就是空表，不让整个目录页失败
        const agg = await sessionToolCounts().catch(() => ({ calls: {}, total: 0, session: "", truncated: false }));
        return json(res, { ...capabilityCatalogPayload(), calls: agg.calls, callsTotal: agg.total, callsSession: agg.session, callsTruncated: agg.truncated });
      } catch (e) { return json(res, { error: e.message }, 500); }
    }

    // #35 旁路提问：独立进程、无工具、超时即杀、不进主线。返回的 note 由前端原样显示，
    // 提示「这句话只在这一屏，不写会话也不写黑板」—— 免得下一个人以为它在 transcript 里。
    if (p === "/api/btw" && req.method === "POST") {
      const body = await readJsonBody(req);
      const question = String(body.question || "").trim();
      if (!question) return json(res, { ok: false, error: "缺少 question" }, 400);
      if (question.length > 4000) return json(res, { ok: false, error: "问题太长（上限 4000 字符）：旁路只适合一句话的疑问" }, 400);
      if (btwRunning >= BTW_MAX_CONCURRENT) return json(res, { ok: false, error: "已有 " + btwRunning + " 路旁路提问在跑（上限 " + BTW_MAX_CONCURRENT + "）：等一条回来再问" }, 429);
      const st = await pi.command({ type: "get_state" }, 8000).catch(() => null);
      const provider = String(body.provider || st?.data?.model?.provider || "");
      const model = String(body.modelId || st?.data?.model?.id || "");
      btwRunning += 1;
      try {
        const r = await runSideQuestion(question, provider, model);
        return json(res, {
          ...r,
          model: provider && model ? provider + "/" + model : "（当前模型）",
          note: "旁路提问：只在这一屏，不写当前会话、不进黑板、不占主线上下文",
        });
      } finally { btwRunning -= 1; }
    }

    if (p === "/api/fs/list") {
      const dir = url.searchParams.get("path") || ROOT;
      try { return json(res, await fsList(dir)); } catch (e) { return json(res, { error: e.message }, 400); }
    }

    if (p === "/api/fs/read") {
      // 与 /api/fs/raw 同一条边界：此前这里裸读任意路径（1MB 上限），是文件面唯一的缺口
      const abs = allowRead(url.searchParams.get("path") || "");
      if (!abs) return json(res, { error: "路径不在允许范围" }, 400);
      try { const { text, size } = await readCapped(abs, 1024 * 1024); return json(res, { text, size }); }
      catch (e) { return json(res, { error: e.message }, 400); }
    }

    if (p === "/api/start" && req.method === "POST") {
      const body = await readJsonBody(req);
      const restarted = await pi.applyStart(body);
      // 等 pi 真的起来再回，不然前端紧接着的 get_state 会拿到 502
      const deadline = Date.now() + 25000;
      while (!pi.child && Date.now() < deadline) await new Promise((r) => setTimeout(r, 200));
      return json(res, { ok: true, restarted, childPid: pi.child?.pid ?? null });
    }

    if (p === "/api/stop" && req.method === "POST") { pi.stop(true); return json(res, { ok: true }); }

    if (p === "/api/events") {
      res.writeHead(200, {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-store",
        connection: "keep-alive",
        // ★ #17：反代会把整条流攒在缓冲区里，浏览器连上却一个字节收不到 —— 这是唯一关掉它的开关
        "x-accel-buffering": "no",
      });
      if (typeof res.flushHeaders === "function") res.flushHeaders();
      // retry：给浏览器的自动重连一个明确节奏，别让它按默认值反复赌
      res.write("retry: 3000\n\n");
      // ★ 必须带上"当前活动 runtimeId"：SSE 重连（服务端重启/断网）后，
      //   前端如果还记着旧 id，事件会被它按 runtimeId 丢掉 → 界面永远停在"正在想"（实测踩过）
      //   ui:hello **不带 id**：它是握手元数据，不该推进客户端的 Last-Event-ID
      res.write(`data: ${JSON.stringify({ type: "ui:hello", piReady: !!pi.child, pid: pi.child?.pid, activeRuntimeId: activeId })}\n\n`);
      // ★ Last-Event-ID **覆盖** ?since：自动重连带的头才是"断在哪一条"的真相；
      //   ?since 只服务于页面刷新后的手动续接（前端把 lastEventId 存进 sessionStorage）
      const hdrSince = Number.parseInt(String(req.headers["last-event-id"] ?? ""), 10);
      const qSince = Number.parseInt(String(url.searchParams.get("since") ?? ""), 10);
      const since = Number.isFinite(hdrSince) ? hdrSince : (Number.isFinite(qSince) ? qSince : NaN);
      const plan = pickReplay(recent, since, RECENT_MAX);
      if (plan.truncated) {
        // 连缓冲都被挤掉的那段：自报丢了多少，界面才能说"中间少了几条"而不是假装连续
        res.write(`data: ${JSON.stringify({ type: "ui:gap", lost: plan.lost, dropped: plan.dropped })}\n\n`);
      }
      for (const e of plan.items) res.write(`id: ${e.id}\ndata: ${JSON.stringify(e.rec)}\n\n`);
      clients.add(res);
      const ka = setInterval(() => { try { res.write(": ka\n\n"); } catch {} }, 15000);
      req.on("close", () => { clearInterval(ka); clients.delete(res); });
      return;
    }

    if (p === "/api/board") { void sweepFinishedRuns().catch(() => {}); return json(res, await boardIndex()); }

    // 子智能体留档的**内容**（板页点开看）：只允许本道题 lanes/ 下的文件，限长 64KB
    // ★ 判据必须看**结尾**：`/api/board/lane-test-zip` 这种 pid 里含 "lane" 的会把 includes 骗过去（实测 400）
    if (p.endsWith("/lane") && p.startsWith("/api/board/")) {
      const m = /^\/api\/board\/([^/]+)\/lane$/.exec(p);
      if (!m) return json(res, { error: "路径不对" }, 400);
      const pid = decodeURIComponent(m[1]);
      if (!/^[\w.-]{1,64}$/.test(pid)) return json(res, { error: "非法 project id" }, 400);
      let q = {};
      try { q = Object.fromEntries(new URL(req.url, "http://x").searchParams); } catch { /* 忽略 */ }
      try { return json(res, { ok: true, ...(await boardLaneText(pid, q.path)) }); }
      catch (e) { return json(res, { ok: false, error: e.message }, 200); }
    }

    // WP：预览（JSON，含正文）与下载（原文件）。★ 必须排在 /api/board/<pid> 前面
    if (p.startsWith("/api/board/") && p.endsWith("/wp/raw")) {
      const pid = decodeURIComponent(p.slice("/api/board/".length, -"/wp/raw".length));
      if (!/^[\w.-]{1,64}$/.test(pid)) return json(res, { error: "非法 project id" }, 400);
      const data = await boardWpText(pid);
      if (!data) return json(res, { error: "还没写这道题的 WP" }, 404);
      res.writeHead(200, {
        "content-type": "text/markdown; charset=utf-8",
        "content-disposition": `attachment; filename="${pid}_WP.md"; filename*=UTF-8''${encodeURIComponent(data.name)}`,
        "cache-control": "no-store",
      });
      return res.end(Buffer.from(data.text, "utf8"));
    }
    if (p.startsWith("/api/board/") && p.endsWith("/wp")) {
      const pid = decodeURIComponent(p.slice("/api/board/".length, -"/wp".length));
      if (!/^[\w.-]{1,64}$/.test(pid)) return json(res, { error: "非法 project id" }, 400);
      const data = await boardWpText(pid);
      if (!data) return json(res, { pid, found: false }, 200);
      return json(res, { pid, found: true, ...data });
    }

    if (p.startsWith("/api/board/")) {
      const pid = decodeURIComponent(p.slice("/api/board/".length));
      return json(res, await boardDetail(pid));
    }

    // ★ 2026-11-13：白名单里有 /api/healthz（见上方 OPEN），但这里**从来没有这个路由** ——
    //   实测 GET /api/healthz → 404。要么补路由、要么从白名单删掉：补路由更合理，
    //   因为 compose healthcheck 与外部探活都可能按这个名字来。
    if (p === "/api/healthz") {
      return json(res, { ok: true, ts: Date.now(), pid: process.pid });
    }

    // ★ 2026-11-19：跑分实况 —— 界面第一次能回答「现在跑到哪、谁在跑、出了哪些旗」。
    //   为什么走 python 而不是在 node 里解析三段数据源：
    //     · 平台 API + ledger.json + shard 日志的合并口径会反复调，改 .py 不必重启 UI；
    //     · live.py 实测 0.5s（含平台请求），单次开销可接受，用 3 秒缓存扛住轮询。
    //   ★ 失败必须**分段降级**：平台抖动时界面仍要显示在跑的进程与最近的旗；
    //     整块 503 会让最需要看现场的时候刚好一片空白（这正是最初的诉求）。
    if (p === "/api/bench") {
      const now = Date.now();
      const fresh = benchCache.data && now - benchCache.at < 3000;
      if (fresh) return json(res, { ...benchCache.data, cached: true });
      const r = await runBenchLive(url.searchParams.has("local"));
      if (r.ok) {
        benchCache.at = now; benchCache.data = r.data;
        return json(res, r.data);
      }
      // 拿不到就用上一份快照，但**明确标注它过期了多久**（不许假装是新的）
      if (benchCache.data) {
        return json(res, { ...benchCache.data, stale_ms: now - benchCache.at, error: r.error });
      }
      return json(res, { ok: false, error: r.error }, 200);
    }

    if (p === "/api/status") {
      return json(res, {
        ok: !!activeRuntime()?.child, childPid: activeRuntime()?.child?.pid ?? null,
        restarts: pi.restarts, lastExit: pi.lastExit, clients: clients.size, root: ROOT,
        activeRuntimeId: activeId,
        // 门禁自证：同源拦截真的拦过几次（0 说明这条门一次没生效过，要复查判据）
        crossSiteBlocked,
        runtimeCount: runtimes.size,
        runtimes: [...runtimes.values()].map((rt) => ({ runtimeId: rt.runtimeId, busy: !!rt.busy, session: rt.sessionPath ? path.basename(rt.sessionPath) : null })),
      });
    }

    // ★ 2026-11-07（X-5）：就绪计数可对账 —— 返回「总数怎么算出来的」而不是一个裸数字。
    //   判据全在 tools/readiness.mjs（纯函数，有 28 组自检），这里只负责**采集**。
    //   前端点开任一类别 → /api/readiness?drill=<key> 拿明细条目。
    if (p === "/api/readiness") {
      let m = null;
      try {
        m = await import(pathToFileURL(path.join(ROOT, "tools", "readiness.mjs")).href);
      } catch { m = null; }
      if (!m || typeof m.summarize !== "function") {
        return json(res, { error: "tools/readiness.mjs 不可用（未挂载或语法错）" }, 503);
      }
      const cats = readinessCategories();
      const drillKey = url.searchParams.get("drill");
      if (drillKey) {
        const d = m.drill(cats, drillKey);
        return d ? json(res, d) : json(res, { error: "没有这个类别：" + drillKey }, 404);
      }
      const s = m.summarize(cats);
      return json(res, { ...s, one_line: m.oneLine(s) });
    }

    if (p === "/api/command" && req.method === "POST") {
      let cmd;
      try { cmd = await readJsonBody(req); } catch (e) { return json(res, { error: e.message }, 413); }
      if (!cmd || typeof cmd.type !== "string") return json(res, { error: "缺少 type 或 JSON 解析失败" }, 400);
      /**
       * 带了 runtimeId 就必须**精确命中**那个会话进程；不许静默落到“当前活动”那个。
       *
       * 为什么（实测症状）：那个进程通常属于**另一条会话**——命令照跑，但回来的事件
       * 带着它的 __rt，前端按「这不是我的会话」把事件攒起来不回放。用户看到的是
       * “发出去没反应”，刷新页面才从磁盘发现“它已经自己跑完了”。
       * 只有前端**没带** runtimeId 时才允许退化到活动进程（懒启动那一刻就是这种）。
       */
      let target = cmd.runtimeId ? runtimes.get(cmd.runtimeId) : activeRuntime();
      if (cmd.runtimeId && !target) {
        /**
         * 进程没了（退出 / 空闲回收 / 被 LRU 收掉）。会话型 runtime 的 id 就是
         * `rt:<会话文件>`，据此能把**同一个会话**重新开出来：新进程的 id 仍然是
         * `rt:<同一个文件>`，所以前端手里那个 runtimeId 依然对得上——事件会正常
         * 落到它的时间线上，用户不必刷新。这就是“发出去没反应”的正解。
         * `rt-new-N`（新会话还没落盘）没有路径可依，只能如实报错。
         */
        const sessionPath = cmd.runtimeId.startsWith("rt:") ? cmd.runtimeId.slice(3) : "";
        if (sessionPath.endsWith(".jsonl")) {
          try {
            target = await openRuntime({ sessionPath });
            log(`命令命中已退出的 runtime，就地重建：${cmd.type} → ${String(target.runtimeId).slice(0, 48)}…`);
          } catch (e) {
            return json(res, { type: "response", command: cmd.type, success: false,
              error: `这个会话的 pi 进程已退出，重建失败：${e.message}` }, 200);
          }
        } else {
          return json(res, { type: "response", command: cmd.type, success: false, staleRuntime: true,
            error: "这个会话的 pi 进程已经不在了；重新发一次或新开会话" }, 200);
        }
      }
      if (!target) return json(res, { type: "response", command: cmd.type, success: false, error: "没有活动会话进程" }, 200);
      try {
        const rec = await target.command(cmd);
        return json(res, rec);
      } catch (e) {
        return json(res, { error: e.message }, 502);
      }
    }

    // ── 目录浏览（只列目录，给「添加工作区」的选文件夹用）────────────────────
    // 为什么需要：左栏「选择文件夹」曾经下线，理由是「浏览器拿不到真实路径、没有原生目录选择器」。
    // 但 dsh 的桌面版有原生选择器（📁+ / Ctrl+O）—— 我们是**本机服务**，
    // 完全可以在服务端列目录，把等价的入口补回来。
    // 安全边界：**只列目录名，不读任何文件内容**；条数封顶；拒绝非目录路径。
    // （文件内容那道口子仍然走 allowRead，没放宽松。）
    if (p === "/api/fs/dirs") {
      const raw = url.searchParams.get("path") || ROOT;
      const abs = path.isAbsolute(raw) ? path.normalize(raw) : path.join(ROOT, raw);
      try {
        const st = await stat(abs);
        if (!st.isDirectory()) return json(res, { error: "不是文件夹" }, 400);
        const names = await readdir(abs, { withFileTypes: true });
        const dirs = [];
        for (const d of names) {
          if (dirs.length >= 500) break;
          // 跳过隐藏目录与 node_modules：列出来只会淹没真实目标
          if (d.name.startsWith(".") || d.name === "node_modules") continue;
          let isDir = d.isDirectory();
          if (!isDir && d.isSymbolicLink()) { try { isDir = (await stat(path.join(abs, d.name))).isDirectory(); } catch { isDir = false; } }
          if (isDir) dirs.push({ name: d.name, path: path.join(abs, d.name) });
        }
        dirs.sort((a, b) => a.name.localeCompare(b.name, "zh"));
        const parent = path.dirname(abs);
        return json(res, { path: abs, parent: parent === abs ? null : parent, dirs });
      } catch (e) {
        return json(res, { error: `列目录失败：${e.message}` }, 400);
      }
    }

    // ZeroA 前端（构建产物）：根路径 + SPA 兜底
    if (!p.startsWith("/api/")) {
      const rel = p === "/" ? "index.html" : decodeURIComponent(p.slice(1));
      const file = path.join(DIST, rel);
      // 带分隔符的前缀判断：startsWith(DIST) 会把 dist-evil 这类兄弟目录也放进来
      if (file === DIST || file.startsWith(DIST + path.sep)) {
        try {
          const buf = await readFile(file);
          res.writeHead(200, { "content-type": MIME[path.extname(file)] || "application/octet-stream", "cache-control": "no-store" });
          return res.end(buf);
        } catch {
          try {
            const html = await readFile(path.join(DIST, "index.html"));
            res.writeHead(200, { "content-type": MIME[".html"], "cache-control": "no-store" });
            return res.end(html);
          } catch {
            res.writeHead(200, { "content-type": MIME[".html"], "cache-control": "no-store" });
            return res.end("<h1>前端还没构建</h1><p>跑 <code>bash work/ui/build.sh</code> 后再刷新。<a href='/simple'>/simple</a> 是旧单页。</p>");
          }
        }
      }
    }

    json(res, { error: "not found" }, 404);
  } catch (e) {
    json(res, { error: e.message }, 500);
  }
});

server.listen(PORT, HOST, () => {
  log(`界面守护: http://${HOST}:${PORT}  (根目录 ${ROOT})`);
  log("同源校验生效（/api 写请求）；口令闸门已移除");
  log(`浏览器打开：http://127.0.0.1:${PORT}/  —— Windows 侧直接访问这个地址即可`);
});

process.on("SIGINT", () => { log("收到 SIGINT，关闭所有 pi"); for (const rt of runtimes.values()) rt.stop(true); server.close(() => process.exit(0)); setTimeout(() => process.exit(0), 1500); });
process.on("SIGTERM", () => { for (const rt of runtimes.values()) rt.stop(true); process.exit(0); });
