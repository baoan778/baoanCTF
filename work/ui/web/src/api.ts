// 与 work/ui/server.mjs 的唯一出口。所有路径都在 service.mjs 里实现，
// 不再有 ZeroA 那层 Wails shim（那层已随前端源码一起丢失）。

export type Json = Record<string, unknown>;

async function get<T = Json>(p: string): Promise<T> {
  const r = await fetch(p, { headers: { accept: "application/json" } });
  if (!r.ok) throw new Error(r.status + " " + r.statusText + " @ " + p);
  return (await r.json()) as T;
}

async function post<T = Json>(p: string, body: unknown): Promise<T> {
  const r = await fetch(p, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(r.status + " " + r.statusText + " @ " + p);
  return (await r.json()) as T;
}

export const api = {
  status: () => get("/api/status"),
  runtimes: () => get("/api/runtimes"),
  sessions: () => get("/api/sessions"),
  sessionHistory: (p: string) => get("/api/session-history?path=" + encodeURIComponent(p)),
  skills: () => get("/api/skills"),
  tools: () => get("/api/tools"),
  // #35 旁路提问：独立进程、无工具、不进当前会话与黑板（回答只回给这一屏）
  btw: (body: Json) => post("/api/btw", body),
  defaults: () => get("/api/defaults"),
  board: () => get("/api/board"),
  boardDetail: (pid: string) => get("/api/board/" + encodeURIComponent(pid)),
  boardLane: (pid: string, p: string) =>
    get("/api/board/" + encodeURIComponent(pid) + "/lane?path=" + encodeURIComponent(p)),
  boardWp: (pid: string) => get("/api/board/" + encodeURIComponent(pid) + "/wp"),
  flags: () => get("/api/flags"),
  // 跑分实况（2026-11-19）：平台题单 + ledger + shard 日志聚合。
  // 后端 3s 缓存；拿不到时返回上一份快照并带 stale_ms（页面据此显示「N 秒前」）。
  bench: () => get("/api/bench"),
  // 就绪计数可对账（X-5）：summary 给 X/Y + 缺项名单；drill 给某一类的逐条明细。
  // 判据在 tools/readiness.mjs（纯函数有 28 组自检），前端只负责「能点开」。
  readiness: () => get("/api/readiness"),
  readinessDrill: (key: string) => get("/api/readiness?drill=" + encodeURIComponent(key)),
  // 作战台聚合：剩余时间 / 成本画像 / 下一步 —— 三路各自可能为 null，界面必须能显示「不知道」
  ops: (pid?: string) => get("/api/ops" + (pid ? "?pid=" + encodeURIComponent(pid) : "")),
  // flag 三态账本：accepted/pending/refuted + 平台原始回执
  flagsLedger: (pid?: string) => get("/api/flags/ledger" + (pid ? "?pid=" + encodeURIComponent(pid) : "")),
  // 一键清空「记录」：扫描命中 + flag 账本（服务端要求 confirm）
  flagsClearAll: () => post("/api/flags/clear-all", { confirm: "clear-all" }),
  // 「下一刀」候选：黑板上当前可动的三刀 + 证据强度
  moves: (pid?: string) => get("/api/moves" + (pid ? "?pid=" + encodeURIComponent(pid) : "")),
  kb: (q: string, top = 3) => get("/api/kb?q=" + encodeURIComponent(q) + "&top=" + top),
  subagents: () => get("/api/subagents"),
  subagentPolicy: () => get("/api/subagent-policy"),
  models: () => get("/api/models"),
  roles: () => get("/api/roles"),
  fsList: (p: string) => get("/api/fs/list?path=" + encodeURIComponent(p)),
  fsRead: (p: string) => get("/api/fs/read?path=" + encodeURIComponent(p)),
  fsStat: (p: string) => get("/api/fs/stat?path=" + encodeURIComponent(p)),
  fsDirs: (p: string) => get("/api/fs/dirs?path=" + encodeURIComponent(p)),
  fsSearch: (q: string, p: string) =>
    get("/api/fs/search?q=" + encodeURIComponent(q) + "&path=" + encodeURIComponent(p)),
  fsRaw: (p: string) => "/api/fs/raw?path=" + encodeURIComponent(p),

  rolesGet: (id: string) => get("/api/roles/get?id=" + encodeURIComponent(id)),
  rolesResolve: (id: string) => get("/api/roles/resolve?id=" + encodeURIComponent(id)),
  rolesSave: (role: Json, prompt: string) => post("/api/roles/save", { role, prompt }),
  rolesDelete: (id: string) => post("/api/roles/delete", { id }),
  rolesActive: (id: string) => post("/api/roles/active", { id }),
  rolesSession: (path: string, roleId: string) => post("/api/roles/session", { path, roleId }),
  rolesExport: (roleIds: string[], path: string) => post("/api/roles/export", { roleIds, path }),
  rolesImport: (source: string) => post("/api/roles/import", { source }),
  rolesReset: (overwrite = false) => post("/api/roles/reset", { overwrite }),

  modelsKeys: () => get("/api/models/keys"),
  modelsCatalog: () => get("/api/models/catalog"),
  modelsProvider: (body: Json) => post("/api/models/provider", body),
  modelsProviderDelete: (provider: string) => post("/api/models/provider/delete", { provider }),
  modelsTest: (provider: string, apiKey: string) => post("/api/models/test", { provider, apiKey }),
  // #27 运行中换模型：只调 set_model RPC（不重启进程，上下文不丢），从下一轮调用生效
  modelsActive: (body: Json) => post("/api/models/active", body),

  tree: () => get("/api/tree"),
  subagent: (runId: string) => get("/api/subagent/" + encodeURIComponent(runId)),
  subagentSay: (runId: string, text: string, mode: "steer" | "follow_up" | "auto") =>
    post("/api/subagent/" + encodeURIComponent(runId) + "/message", { text, mode }),

  exportUrl: (p: string) => "/api/export?path=" + encodeURIComponent(p),
  boardWpRawUrl: (pid: string) => "/api/board/" + encodeURIComponent(pid) + "/wp/raw",

  sessionsRename: (path: string, name: string) => post("/api/sessions/rename", { path, name }),
  sessionsDelete: (path: string) => post("/api/sessions/delete", { path }),
  // 一键清空：服务端要求 confirm="clear-all" 才执行（破坏性操作双保险）
  sessionsClearAll: () => post("/api/sessions/clear-all", { confirm: "clear-all" }),
  runtimeOpen: (sessionPath: string) => post("/api/runtime/open", { sessionPath }),
  runtimeNew: () => post("/api/runtime/new", {}),
  runtimeClose: (runtimeId: string) => post("/api/runtime/close", { runtimeId }),
  fork: (body: Json) => post("/api/fork", body),
  upload: (body: Json) => post("/api/upload", body),
  fgs: (body: Json) => post("/api/fgs", body),
  start: (body: Json) => post("/api/start", body),
  stop: () => post("/api/stop", {}),
  command: (cmd: Json) => post("/api/command", cmd),
};

// ── SSE：唯一的事件通道 ────────────────────────────────────────────────
// server.mjs 的 /api/events 会先灌 ui:hello，再按 id 补偿回放最近的一段，然后持续推送。
// #17 起每帧带 SSE id（断线重连浏览器自动发 Last-Event-ID，服务端据此从断点补发），
// 这里再把最后 id 存进 sessionStorage：页面刷新是**新实例**，头就没了，只能靠 ?since= 续接。
// 断线由 EventSource 自己重连；重连后必须用 hello.activeRuntimeId 对齐当前会话，
// 否则前端会拿旧 runtimeId 过滤事件 → 界面永远停在"正在想"（server.mjs 注释里踩过）。
export type Ev = { type: string; [k: string]: unknown };

const SSE_SINCE_KEY = "pictf.sseLastId";

export function subscribe(onEvent: (e: Ev) => void): () => void {
  let since = "";
  try { since = sessionStorage.getItem(SSE_SINCE_KEY) || ""; } catch { /* 隐私模式禁 storage：退化成只回最近 120 条 */ }
  const es = new EventSource("/api/events" + (since ? "?since=" + encodeURIComponent(since) : ""));
  es.onmessage = (m) => {
    if (m.lastEventId) {
      try { sessionStorage.setItem(SSE_SINCE_KEY, m.lastEventId); } catch { /* 同上 */ }
    }
    try { onEvent(JSON.parse(m.data) as Ev); } catch { /* 容忍坏帧 */ }
  };
  return () => es.close();
}

export type ChatItem =
  | { kind: "user"; text: string; id: string }
  | { kind: "assistant"; text: string; id: string; streaming: boolean }
  | {
      kind: "tool"; name: string; detail: string; id: string;
      toolId?: string; args?: string; out?: string;
      status?: "running" | "ok" | "error"; ms?: number;
    }
  | { kind: "note"; text: string; id: string };

let seq = 0;
export function nextId(prefix: string): string {
  seq += 1;
  return prefix + "-" + seq;
}