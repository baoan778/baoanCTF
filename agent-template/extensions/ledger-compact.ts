/**
 * pi-lab: ledger-compact —— 压缩台账穿越（X-2）
 *
 * 治什么
 * ------
 * 上下文压缩由**总结器重写**，重写会洗掉限定词：
 *   "注入点在 search（仅 GET，未测 POST）" → "注入点在 search"
 * 之后所有推理都建立在变宽的前提上。本扩展让 `work/<pid>/ledger.md`
 * **原样穿越**：压缩发生时标记"下次必注入"，此后按节拍重注入。
 *
 * 挂哪个钩子（为什么不是 session_before_compact 直接改 summary）
 * ----------------------------------------------------------
 * pi 的 `session_before_compact` 只能 `{cancel}` 或**整体替换** compaction 结果
 * （`SessionBeforeCompactResult.compaction` 需要我们自己跑一次 summarization，
 * 代价高且有失败风险）。而 `context` 在**每次 LLM 调用前**触发，结果语义是
 * **替换** `messages` —— 我们把台账作为尾随一条消息附上即可，
 * 既不动压缩逻辑，也不承担总结失败的风险。
 * ★ 关键：必须返回 `[...event.messages, injected]`（替换语义，漏了原消息就冲掉原问题）。
 *
 * 三条纪律（与 reflect-paper / idle-resume 同族）
 * --------------------------------------------
 * 1. **fail-open**：读不到台账 / 结构不对 / 模块加载失败 → 一律不注入。
 * 2. **有界**：注入按 token 预算逐行截断（默认 4000），截断处写全文路径。
 * 3. **不刷屏**：压缩后必注入一次；平时按 `PI_LAB_LEDGER_EVERY`（默认 10 次 LLM 调用）
 *    节拍注入一次。空台账（只有占位符）永不注入。
 *
 * 逃生门：`/ledger off` · `touch work/<pid>/.no-ledger` · `PI_LAB_LEDGER=0`
 * 日志：log/ledger-compact.jsonl
 */

import * as fs from "node:fs";
import * as path from "node:path";
import { pathToFileURL } from "node:url";

function findLab() {
  const env = process.env.PI_LAB_ROOT;
  const probe = (d) => {
    try { return fs.existsSync(path.join(d, "tools", "ledger.mjs")); } catch { return false; }
  };
  if (env && probe(env)) return env;
  let cur = process.cwd();
  for (let i = 0; i < 10; i++) {
    if (probe(cur)) return cur;
    const up = path.dirname(cur);
    if (up === cur) break;
    cur = up;
  }
  return env || process.cwd();
}

const LAB = findLab();
const MOD = path.join(LAB, "tools", "ledger.mjs");
const LOG = process.env.PI_LAB_LEDGER_LOG || path.join(LAB, "log", "ledger-compact.jsonl");
const BOARDS = path.join(LAB, "board", "projects");
const WORK = path.join(LAB, "work");
const MAX_LOG = 1024 * 1024;
const PID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

let mod = null;
let modTried = false;
async function loadMod() {
  if (mod || modTried) return mod;
  modTried = true;
  try {
    const m = await import(pathToFileURL(MOD).href);
    if (typeof m.renderForInject === "function") mod = m;
  } catch { mod = null; }
  return mod;
}

function appendLog(rec) {
  try {
    fs.mkdirSync(path.dirname(LOG), { recursive: true });
    try { if (fs.statSync(LOG).size > MAX_LOG) fs.renameSync(LOG, LOG + ".1"); } catch { /* 无文件 */ }
    fs.appendFileSync(LOG, JSON.stringify(rec) + "\n");
  } catch { /* 记不上不影响注入 */ }
}

function pidFromCommand(cmd) {
  const s = String(cmd || "");
  if (!/\bfgs(\.py)?\b/.test(s)) return null;
  let m = s.match(/\bfgs(?:\.py)?\s+(?:kickoff|init|next|status|goal|ledger)\s+([A-Za-z0-9][A-Za-z0-9._-]{0,63})/);
  if (m && PID_RE.test(m[1])) return m[1];
  m = s.match(/--project[=\s]+([A-Za-z0-9][A-Za-z0-9._-]{0,63})/);
  if (m && PID_RE.test(m[1])) return m[1];
  return null;
}

/** 会话没认到 pid 时兜底：work/ 下最近改过 ledger.md 的那个目录 */
function latestLedgerPid() {
  try {
    let best = null;
    let bestAt = 0;
    for (const n of fs.readdirSync(WORK)) {
      if (!PID_RE.test(n) || n.startsWith("_")) continue;
      const f = path.join(WORK, n, "ledger.md");
      try {
        const st = fs.statSync(f);
        if (st.mtimeMs > bestAt) { bestAt = st.mtimeMs; best = n; }
      } catch { /* 这个项目没台账 */ }
    }
    return best;
  } catch { return null; }
}

export default function ledgerCompact(pi) {
  let pid = null;
  let off = process.env.PI_LAB_LEDGER === "0";
  let callsSince = 0;
  let pendingCompact = false;
  let injected = 0;
  const every = Math.max(1, Number(process.env.PI_LAB_LEDGER_EVERY || 10));

  const skipFile = () => {
    try { return pid ? fs.existsSync(path.join(WORK, pid, ".no-ledger")) : false; } catch { return false; }
  };

  pi.on("tool_call", (event) => {
    try {
      const p = pidFromCommand(event?.input?.command);
      if (p) pid = p;
    } catch { /* 放行 */ }
  });

  // 压缩发生 → 标记下次 LLM 调用必注入（这是"穿越"的关键点）
  pi.on("session_before_compact", (event) => {
    try {
      if (off) return;
      pendingCompact = true;
      appendLog({
        ts: new Date().toISOString(), action: "compact-before", pid,
        reason: event?.reason, tokens_before: event?.preparation?.tokensBefore,
      });
    } catch { /* 放行 */ }
  });

  pi.on("session_compact", (event) => {
    try {
      appendLog({ ts: new Date().toISOString(), action: "compact-done", pid, reason: event?.reason });
    } catch { /* 放行 */ }
  });

  // 主钩子：每次 LLM 调用前，按"压缩后必注 + 节拍"决定是否附台账
  pi.on("context", async (event, ctx) => {
    try {
      callsSince++;
      if (off || skipFile()) return;
      const due = pendingCompact || callsSince >= every;
      if (!due) return;

      const m = await loadMod();
      if (!m) return;

      const usePid = pid || latestLedgerPid();
      if (!usePid) return;
      pid = usePid;

      const f = path.join(WORK, usePid, "ledger.md");
      let raw = "";
      try { raw = fs.readFileSync(f, "utf8"); } catch { return; }

      const block = m.renderForInject(raw, { path: f, maxTokens: 4000 });
      if (!block) {
        // 空台账 / 结构不对 → 不注入，但清掉"压缩后必注"标记，避免每轮都试
        if (pendingCompact) {
          pendingCompact = false;
          appendLog({ ts: new Date().toISOString(), action: "skip-empty", pid });
        }
        return;
      }

      pendingCompact = false;
      callsSince = 0;
      injected++;
      appendLog({
        ts: new Date().toISOString(), action: "inject", pid, n: injected,
        bytes: block.length, tokens: m.estimateTokens(block),
      });
      try { ctx?.ui?.notify("📒 分析台账已附上（压缩穿越）", "info"); } catch { /* 无 UI */ }

      // ★ 替换语义：必须带上原 messages，否则会冲掉本轮问题
      return {
        messages: [
          ...event.messages,
          {
            role: "user",
            content: [{ type: "text", text: block }],
            timestamp: Date.now(),
          },
        ],
      };
    } catch {
      return;   // 任何异常都放行（fail-open）
    }
  });

  pi.registerCommand("ledger", {
    description: "/ledger [on|off|status|now] —— 分析台账穿越开关与状态",
    handler: async (args, ctx) => {
      const a = String(args || "").trim().toLowerCase();
      let line;
      if (a === "off") { off = true; line = "📒 台账穿越：已关闭"; }
      else if (a === "on") { off = false; line = "📒 台账穿越：已开启"; }
      else if (a === "now") { pendingCompact = true; line = "📒 下次 LLM 调用将强制注入台账"; }
      else {
        const usePid = pid || latestLedgerPid();
        const f = usePid ? path.join(WORK, usePid, "ledger.md") : null;
        const has = f ? fs.existsSync(f) : false;
        line = "📒 台账穿越：" + (off ? "已关闭" : "已开启")
          + "｜项目 " + (usePid || "未认到")
          + "｜台账 " + (has ? "存在" : "缺（用 `fgs ledger <pid> --init` 建）")
          + "｜已注入 " + injected + " 次"
          + "｜节拍 " + every + " 次调用";
      }
      appendLog({ ts: new Date().toISOString(), action: a || "status", pid, line });
      try { ctx?.ui?.notify(line, "info"); } catch { /* 无 UI */ }
      console.log(line);
    },
  });
}
