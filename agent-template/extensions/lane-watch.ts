/**
 * pi-lab: lane-watch —— 派活闸门（**在途中**拦，不是事后判）
 *
 * 为什么需要它（2026-10-12 实测血账）
 * ----------------------------------
 * 会话 `01a12e9b`（奶蛙服务器）：`fgs kickoff` 打印了**可直接粘的两路派活命令**，
 * 然后 agent 用它做了 **15 次工具调用、0 次 subagent** —— 一个人把题做完了。
 * 以前只有两处兜底，都不够：① kickoff 的**文字**提醒（模型会划过去，上面就是反例）；
 * ② `fgs solved` 的**事后**闸门（题都做完了才拒，救不回来）。
 *
 * 它做什么：挂在 `tool_call` 上 —— kickoff 之后若已自己走了 > GRACE 次工具调用
 * 却一次 `subagent` 都没派，**直接拦下这一条工具调用**，把 `fgs kickoff` 当时打的
 * 那两路命令原样回放给模型（自包含、可直接照做）。
 *
 * 三条纪律
 * --------
 * · **fail-open**：硬拦满 MAX_BLOCKS 次就放行（继续软提醒）—— 子代理坏掉不能锁死整轮。
 * · **只认自己的靶子**：没跑过 `fgs kickoff` 的会话一律不碰（写代码/查资料/运维都无关）。
 * · **放行派活准备**：`fgs brief/next/step-add/…`、`lane_gate`、`sesscan` 不算「自己做题」。
 *
 * 解除通道：`/lanes off` · `touch board/projects/<pid>/.no-lanes` · `PI_LAB_LANES_GATE=0`。
 *
 * 纯 fs + 纯正则，不起进程；全程 try/catch —— 宁可自己静默失效，也绝不拖累 pi 跑工具。
 */

import * as fs from "node:fs";
import * as path from "node:path";
import { pathToFileURL } from "node:url";

/**
 * ★ 2026-11-02 修（实测坐实）：LAB 解析改成**多候选 + 存在性校验**，不再裸信 cwd。
 *
 * 病根：原来是 `const LAB = process.env.PI_LAB_ROOT || process.cwd()`。
 *   `process.cwd()` 是**用户启动 pi 时所在目录**，不是仓库根 —— 从桌面 / 家目录 /
 *   任意非仓库路径启动时，LAB 落到错地方 → `MOD` 指向不存在的 lanewatch.mjs →
 *   `loadMod()` 的 catch 把 mod 置 null → **每条 tool_call 直接 return，闸门静默变哑弹**。
 *   这是「要拦截却没拦截」的真因，而且它一声不响：台账里连一条 arm 都没有。
 *   实测同一份代码：cwd=仓库根 → 拦 3 次；cwd=C:\Users\Administrator → 0 次。
 *
 * 修法（三层，先验证「这个 LAB 真的是仓库」再采用）：
 *   ① `PI_LAB_ROOT` 环境变量（启动器已导，优先）
 *   ② 从 cwd **逐级向上**找 `tools/lanewatch.mjs` —— 支持在仓库任意子目录里启动
 *   ③ 都不行就退回 cwd（保持原行为，仍是 fail-open，不抛错）
 * 判据不是「路径长得像」，而是**`tools/lanewatch.mjs` 真的存在** —— 这样假候选会被跳过，
 * 而不是让闸门以为自己是好的、实际静默失效。
 */
function resolveLab(): string {
  const cands: string[] = [];
  const env = process.env.PI_LAB_ROOT;
  if (env) cands.push(env);
  // 从 cwd 向上逐级找（限制 12 层，防极端路径下走太久）
  try {
    let d = process.cwd();
    for (let i = 0; i < 12; i++) {
      cands.push(d);
      const up = path.dirname(d);
      if (up === d) break;
      d = up;
    }
  } catch {
    /* cwd 拿不到就算了 */
  }
  for (const c of cands) {
    try {
      if (fs.existsSync(path.join(c, "tools", "lanewatch.mjs"))) return c;
    } catch {
      /* 单个候选读不动就跳过 */
    }
  }
  return process.env.PI_LAB_ROOT || process.cwd(); // 全都不成立：保持 fail-open
}

const LAB = resolveLab();
const MOD = path.join(LAB, "tools", "lanewatch.mjs");
const LOG = process.env.PI_LAB_LANEWATCH_LOG || path.join(LAB, "log", "lane-watch.jsonl");
const MAX_LOG = 2 * 1024 * 1024; // 2MB 后轮转成 .1

let mod = null;
let modTried = false;

async function loadMod() {
  if (mod || modTried) return mod;
  modTried = true;
  try {
    const m = await import(pathToFileURL(MOD).href);
    if (typeof m.decide === "function") mod = m;
  } catch {
    mod = null; // fail-open：闸门静默失效，工具照跑
  }
  return mod;
}

function contentToText(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  const parts = [];
  for (const p of content) {
    if (typeof p === "string") parts.push(p);
    else if (p && typeof p === "object" && typeof p.text === "string") parts.push(p.text);
  }
  return parts.join("\n");
}

function appendLog(rec) {
  try {
    fs.mkdirSync(path.dirname(LOG), { recursive: true });
    try {
      if (fs.statSync(LOG).size > MAX_LOG) fs.renameSync(LOG, `${LOG}.1`);
    } catch { /* 文件不存在 */ }
    fs.appendFileSync(LOG, JSON.stringify(rec) + "\n");
  } catch { /* 记不上不影响拦截 */ }
}

export default function laneWatch(pi) {
/** 零依赖逃生舱（recover-mode.ts 只写 globalThis 标记，两边无文件依赖） */
function recoverActive() {
  try {
    const g = (globalThis as any).__PI_LAB_RECOVER;
    return !!(g && typeof g.until === "number" && Date.now() < g.until);
  } catch { return false; }
}
  let state = { pid: null, sinceKickoff: 0, subagentCalls: 0, blocks: 0, done: false, warned: 0 };
  let laneText = "";
  let softWarned = false;
  /**
   * 刚发了一次 kickoff、但命令行里的 pid 是 shell 替换符（`$(ls board/projects/ | tail -1)`）
   * —— 这种只能从**输出**里认 pid（`lane-watch` 2026-10-13 修：以前会把 `$P`/`$(ls` 当成 pid 装弹）。
   */
  let pendingKickoff = false;
  const isKickoffCmd = (s) => /(^|[;&|\s])fgs\s+kickoff\b/.test(s);
  let off = process.env.PI_LAB_LANES_GATE === "0";

  const skipFile = () => {
    try {
      return state.pid
        ? fs.existsSync(path.join(LAB, "board", "projects", state.pid, ".no-lanes"))
        : false;
    } catch { return false; }
  };

  pi.on("tool_call", async (event, ctx) => {
    try {
      if (recoverActive()) return;
      const m = await loadMod();
      if (!m || off) return;
      const name = event?.toolName ?? "?";
      const cmd = String(event?.input?.command ?? "");

      // ① 新题开场：kickoff 一出现就装弹（并清掉上一题的残留）
      const pid = m.parseKickoff(cmd);
      if (pid) {
        pendingKickoff = false;
        state = m.newState();
        state.pid = pid;
        laneText = "";
        softWarned = false;
        appendLog({ ts: new Date().toISOString(), action: "arm", pid, tool: name });
        return;
      }
      // ①b 是 kickoff 但 pid 是 `$(…)`/反引号 —— 先记住，等输出回来再认（parseKickoffOutput）
      if (isKickoffCmd(cmd)) {
        pendingKickoff = true;
        appendLog({ ts: new Date().toISOString(), action: "arm-deferred", pid: "(shell-substitution)", tool: name });
        return;
      }

      // ② 人写的逃生门
      if (state.pid && skipFile()) {
        if (!state.done) {
          state.done = true;
          appendLog({ ts: new Date().toISOString(), action: "skip-file", pid: state.pid });
        }
        return;
      }
      if (!state.pid || state.done) return;

      const r = m.decide(state, name, event?.input);
      state = r.state;
      if (r.action === "block") {
        appendLog({
          ts: new Date().toISOString(), action: "block", pid: state.pid, tool: name,
          blocks: state.blocks, since_kickoff: state.sinceKickoff,
        });
        try { ctx?.ui?.notify(`⛔ 派活闸门：${state.pid} 还没派任何一路`, "warning"); } catch { /* 无 UI */ }
        return { block: true, reason: m.buildReason(state, laneText) };
      }
    } catch {
      return; // 任何异常都放行
    }
  });

  pi.on("tool_result", async (event) => {
    try {
      const m = await loadMod();
      if (!m) return;
      const text = contentToText(event?.content);

      // 抄下 kickoff 打的那两路命令（拦截时原样回放，模型不用去翻历史）
      if (!laneText && text.includes("第 1 路") && text.includes("subagent(")) {
        const cap = m.captureLanes(text);
        if (cap) laneText = cap;
      }

      // 配合 ①b：命令行里 pid 认不出来时，从 kickoff 的输出横幅里补认
      if (pendingKickoff) {
        pendingKickoff = false;
        const outPid = m.parseKickoffOutput(text);
        if (outPid) {
          state = m.newState();
          state.pid = outPid;
          softWarned = false;
          appendLog({ ts: new Date().toISOString(), action: "arm-from-output", pid: outPid });
        }
      }

      if (off || !state.pid || state.done) return;
      // fail-open 之后的软提醒：每轮只贴一次，别训练出「狼来了」
      if (state.warned > 0 && !softWarned && text.length > 20) {
        softWarned = true;
        const line = m.warnLine(state);
        appendLog({ ts: new Date().toISOString(), action: "soft-warn", pid: state.pid });
        if (Array.isArray(event?.content)) {
          return { content: [...event.content, { type: "text", text: line }] };
        }
        if (typeof event?.content === "string") return { content: `${event.content}${line}` };
      }
    } catch {
      return;
    }
  });

  pi.registerCommand("lanes", {
    description: "派活闸门：/lanes 看状态 · /lanes off|on 开关（kickoff 后 0 路会被硬拦）",
    handler: async (args, ctx) => {
      const a = String(args || "").trim().toLowerCase();
      if (a === "off" || a === "on") {
        off = a === "off";
        ctx?.ui?.notify(`派活闸门 → ${off ? "关（本会话）" : "开"}`, "info");
        return;
      }
      const st = state.pid
        ? `已武装：pid=${state.pid} · 自己走了 ${state.sinceKickoff} 次 · 派活 ${state.subagentCalls} 次`
          + ` · 已拦 ${state.blocks} 次 · ${state.done ? "已解除" : "**未解除**"}`
        : "未武装（本会话没跑过 fgs kickoff）";
      ctx?.ui?.notify(`⛔ 派活闸门 ${off ? "（已关）" : "（开）"}｜${st}`, "info");
    },
  });
}
