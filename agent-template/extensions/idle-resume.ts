/**
 * pi-lab: idle-resume —— 空闲续推（防「模型给出阶段性结论后就安静等人」）
 *
 * 为什么要有它
 * ------------
 * 长任务最大的敌人是「停」。OpenSecurity 2.0 的做法是随机完成标记 + 没标记就注入 resume；
 * 但那个标记要 LLM **自己原样吐出来** —— 判断权在被考核方手上。
 *
 * 本仓的做法（2026-11-06 用户定案 既定决策）：**按黑板未闭合 Step 数判**。
 * board/graph.json 是机器产物、模型改不了；拿图状态当判据，
 * 比让模型自述「我完成了」可靠一个量级。
 *
 * 挂哪个钩子：`agent_before_settle`（pi 原生，返回 `{continue:true}` 会让 pi 再发一次请求），
 * 比"注入一条消息"更硬 —— 由运行时强制，不靠下一轮模型自己看见。
 *
 * 三条纪律（与 lane-watch / flag-watch 同族）
 * ------------------------------------------
 * 1. **fail-open**：读不到图 / 模块 import 失败 → 一律不续推。宁可少推，不可乱推。
 * 2. **只认开过板的会话**：没见过 `fgs kickoff`/`fgs init` 的会话（写代码 / 查资料 / 运维）不碰。
 * 3. **双封顶**：续推次数 + 连续无进展。图状态卡死时不能无限烧钱。
 *
 * 逃生门（三层，与其它闸门一致）：
 *   · `/idle off`                        本次会话关掉
 *   · `touch board/projects/<pid>/.no-idle`  按项目关掉
 *   · 环境变量 `PI_LAB_IDLE_RESUME=0`
 *
 * 日志：log/idle-resume.jsonl（每次续推/停止都留一条，便于赛后复盘）
 */

import * as fs from "node:fs";
import * as path from "node:path";
import { pathToFileURL } from "node:url";

/**
 * 找仓库根：优先 PI_LAB_ROOT，其次从 cwd 逐级向上找带 tools/idlewatch.mjs 的目录。
 * 判据是「文件真的存在」，不是「路径长得像」—— 假候选会被跳过。
 * （这段逻辑抄 lane-watch.ts 的 findLab，那边为同一个坑付过代价。）
 */
function findLab() {
  const env = process.env.PI_LAB_ROOT;
  if (env && fs.existsSync(path.join(env, "tools", "idlewatch.mjs"))) return env;
  let cur = process.cwd();
  for (let i = 0; i < 8; i++) {
    if (fs.existsSync(path.join(cur, "tools", "idlewatch.mjs"))) return cur;
    const up = path.dirname(cur);
    if (up === cur) break;
    cur = up;
  }
  return env || process.cwd();
}

const LAB = findLab();
const MOD = path.join(LAB, "tools", "idlewatch.mjs");
const LOG = path.join(LAB, "log", "idle-resume.jsonl");
const BOARDS = path.join(LAB, "board", "projects");
const MAX_LOG = 4 * 1024 * 1024;

let mod = null;
let modTried = false;

async function loadMod() {
  if (mod || modTried) return mod;
  modTried = true;
  try {
    const m = await import(pathToFileURL(MOD).href);
    if (typeof m.decideIdle === "function" && typeof m.snapshotFromGraph === "function") mod = m;
  } catch {
    mod = null; // fail-open
  }
  return mod;
}

function appendLog(rec) {
  try {
    fs.mkdirSync(path.dirname(LOG), { recursive: true });
    try {
      if (fs.statSync(LOG).size > MAX_LOG) fs.renameSync(LOG, `${LOG}.1`);
    } catch { /* 文件不存在 */ }
    fs.appendFileSync(LOG, JSON.stringify(rec) + "\n");
  } catch { /* 记不上不影响续推 */ }
}

/** 合法项目名（与 board 的 pid 口径一致） */
const PID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

/** 从一条命令行里认出 pid：`fgs kickoff <pid>` / `fgs init <pid>` / `fgs next <pid>` / `--project <pid>` */
export function pidFromCommand(cmd) {
  const s = String(cmd || "");
  if (!/\bfgs(\.py)?\b/.test(s)) return null;
  let m = s.match(/\bfgs(?:\.py)?\s+(?:kickoff|init|next|status|goal)\s+([A-Za-z0-9][A-Za-z0-9._-]{0,63})/);
  if (m && PID_RE.test(m[1])) return m[1];
  m = s.match(/--project[=\s]+([A-Za-z0-9][A-Za-z0-9._-]{0,63})/);
  if (m && PID_RE.test(m[1])) return m[1];
  return null;
}

/** 从 kickoff 的输出横幅里补认 pid（命令行写了 shell 替换符时） */
export function pidFromOutput(text) {
  const s = String(text || "");
  const m = s.match(/(?:项目|project)[：:\s]+([A-Za-z0-9][A-Za-z0-9._-]{0,63})/i)
    || s.match(/\bfgs\s+(?:kickoff|init)\s+([A-Za-z0-9][A-Za-z0-9._-]{0,63})/);
  return m && PID_RE.test(m[1]) ? m[1] : null;
}

/**
 * 读黑板：graph.json + outbox 未结算行数。
 * 任何异常都返回 null（fail-open：调用方据此不续推）。
 */
export function readBoard(pid) {
  if (!pid || !PID_RE.test(pid)) return null;
  let g;
  try {
    const raw = fs.readFileSync(path.join(BOARDS, pid, "graph.json"), "utf8");
    g = JSON.parse(raw);
  } catch {
    return null;
  }
  let pending = 0;
  try {
    const f = path.join(BOARDS, pid, "outbox.jsonl");
    const txt = fs.readFileSync(f, "utf8");
    for (const line of txt.split("\n")) if (line.trim()) pending++;
  } catch { /* 没有 outbox 文件 = 0 条 */ }
  return { graph: g, pendingOutbox: pending };
}

/** 列表里最新的项目（会话没认到 pid 时的兜底；多项目并行时不猜，直接返回 null） */
export function latestProject() {
  try {
    const names = fs.readdirSync(BOARDS)
      .filter((n) => PID_RE.test(n) && !n.startsWith("."))
      .filter((n) => fs.existsSync(path.join(BOARDS, n, "graph.json")));
    if (names.length === 0) return null;
    if (names.length === 1) return names[0];
    // 多个项目：只在「恰好一个最近 3 小时内活跃」时才认，避免并行多题时推错
    const now = Date.now();
    const fresh = names.filter((n) => {
      try { return now - fs.statSync(path.join(BOARDS, n, "graph.json")).mtimeMs < 3 * 3600 * 1000; }
      catch { return false; }
    });
    return fresh.length === 1 ? fresh[0] : null;
  } catch {
    return null;
  }
}

export default function idleResume(pi) {
/** 零依赖逃生舱（recover-mode.ts 只写 globalThis 标记，两边无文件依赖） */
function recoverActive() {
  try {
    const g = (globalThis as any).__PI_LAB_RECOVER;
    return !!(g && typeof g.until === "number" && Date.now() < g.until);
  } catch { return false; }
}
  let pid = null;
  let st = {};                 // { resumes, stalled, lastUnclosed, lastAt }
  let pendingPid = false;      // 命令行 pid 是 shell 替换符时，等输出补认
  let off = process.env.PI_LAB_IDLE_RESUME === "0";
  let resolving = false;       // 防止 before_settle 与 settled 重入

  const skipFile = () => {
    try {
      return pid ? fs.existsSync(path.join(BOARDS, pid, ".no-idle")) : false;
    } catch { return false; }
  };

  // ── 装弹：从工具调用里认 pid ──────────────────────────────────────────
  pi.on("tool_call", (event) => {
    try {
      const cmd = String(event?.input?.command ?? "");
      const p = pidFromCommand(cmd);
      if (p) {
        pid = p;
        pendingPid = false;
        st = {};
        appendLog({ ts: new Date().toISOString(), action: "arm", pid });
      } else if (/\bfgs(?:\.py)?\s+(kickoff|init)\b/.test(cmd)) {
        pendingPid = true;
      }
    } catch { /* 放行 */ }
  });

  pi.on("tool_result", (event) => {
    try {
      if (!pendingPid) return;
      const text = Array.isArray(event?.content)
        ? event.content.map((c) => (c && c.text) || "").join("\n")
        : String(event?.content ?? "");
      const p = pidFromOutput(text);
      if (p) {
        pendingPid = false;
        pid = p;
        st = {};
        appendLog({ ts: new Date().toISOString(), action: "arm-from-output", pid });
      }
    } catch { /* 放行 */ }
  });

  /**
   * 核心判断：该不该续推。返回 {continue} 或 undefined。
   * before_settle 只影响「这一轮要不要再发一次」，settled 只做记账与 UI 提示。
   */
  const evaluate = async (aborted) => {
    if (off || skipFile()) return null;
    if (recoverActive()) return null;
    const m = await loadMod();
    if (!m) return null;

    const usePid = pid || latestProject();
    if (!usePid) return null;              // 没开过板 → 不碰
    if (!pid) pid = usePid;

    const board = readBoard(pid);
    // readBoard 返回 null（图缺失/损坏）→ snap 传 null，让 decideIdle 出 bad-graph
    const snap = board ? m.snapshotFromGraph(board.graph, board.pendingOutbox) : null;

    // ── X-3（2026-11-07）：空闲反思优先于续推 ──────────────────────────
    // OpenSecurity 2.0 的反思有两条投递通道：忙时纸条（tool.execute.after 追加输出）
    // 与**空闲唤醒**（session.idle 时发消息），且唤醒**优先于 resume**。
    // 本仓此前只有忙时通道（reflect-paper），模型安静停下时**只有续推、没有盘点** ——
    // 于是在死路上被加速。这里补空闲通道：到期先发盘点，本轮不再续推。
    //
    // 判据来源单一：改用 reflect-paper 发布的 globalThis 状态（净活跃/调用数/提醒计数），
    // 判分仍走 tools/reflectwatch.mjs 的 decideReflect —— 不另造第二套口径。
    const reflectHit = await checkReflectDue();
    if (reflectHit) {
      appendLog({
        ts: new Date().toISOString(), action: "reflect-idle", pid,
        active_min: Math.round(reflectHit.activeMs / 60000),
        tool_calls: reflectHit.toolCalls, why: reflectHit.why,
      });
      appendLog({ ts: new Date().toISOString(), action: "resume-skipped",
                  pid, why: "空闲反思到期（优先于续推）" });
      return { msg: reflectHit.paper, why: reflectHit.why, kind: "reflect" };
    }

    const verdict = m.decideIdle(snap, { ...st, aborted: !!aborted });
    if (verdict.action !== "continue") {
      // 只在「有意义的停」上记账，避免 cooldown 刷屏
      if (verdict.kind !== "cooldown") {
        appendLog({
          ts: new Date().toISOString(), action: "stop", pid,
          kind: verdict.kind, why: verdict.why,
          unclosed: snap ? snap.unclosed : null, resumes: st.resumes || 0,
        });
      }
      return null;
    }

    st = m.bumpState(st, snap);
    appendLog({
      ts: new Date().toISOString(), action: "resume", pid,
      resumes: st.resumes, stalled: st.stalled,
      unclosed: snap.unclosed, open: snap.open, claimed: snap.claimed,
      pending_outbox: snap.pendingOutbox, why: verdict.why,
    });
    return { msg: verdict.msg, why: verdict.why, kind: verdict.kind };
  };

  // ── 主钩子：before_settle 返回 continue:true → pi 再发一次请求 ─────────
  pi.on("agent_before_settle", async (event, ctx) => {
    if (resolving) return;
    resolving = true;
    try {
      const r = await evaluate(event?.aborted);
      if (!r) return;
      const isReflect = r.kind === "reflect";
      try {
        ctx?.ui?.notify(isReflect
          ? `📝 空闲反思（优先于续推）：${r.why}`
          : `🔁 空闲续推：${r.why}`, "info");
      } catch { /* 无 UI */ }
      // 用 custom_message 把"为什么推你回去"写进会话，模型下一轮看得见
      return {
        continue: true,
        entries: [{
          type: "custom_message",
          customType: r.kind === "reflect" ? "reflect-idle" : "idle-resume",
          content: r.msg,
          display: true,
        }],
      };
    } catch {
      return undefined;   // 任何异常都放行（fail-open）
    } finally {
      resolving = false;
    }
  });

  // ── 记账钩子：settled 时把停止原因落日志（不改变行为）───────────────
  pi.on("agent_settled", async (event) => {
    if (off) return;
    try {
      if (event?.aborted) {
        appendLog({ ts: new Date().toISOString(), action: "stop", pid, kind: "aborted",
                    why: "用户中断（aborted=true）" });
      }
    } catch { /* 忽略 */ }
  });

  // ── 命令 ────────────────────────────────────────────────────────────
  pi.registerCommand("idle", {
    description: "空闲续推开关与状态：/idle [on|off|status]",
    handler: async (args, ctx) => {
      const a = String(args || "").trim().toLowerCase();
      if (a === "off") { off = true; appendLog({ ts: new Date().toISOString(), action: "off", pid }); }
      else if (a === "on") { off = false; appendLog({ ts: new Date().toISOString(), action: "on", pid }); }
      const usePid = pid || latestProject();
      let line = `🔁 空闲续推：${off ? "已关闭" : "已开启"}`;
      if (usePid) {
        const b = readBoard(usePid);
        if (b) {
          const s = snapshotFromGraphSafe(usePid, b);
          line += `\n   项目 ${usePid}：未闭合 ${s ? s.unclosed : "?"}`
               + `（open ${s ? s.open : "?"} / claimed ${s ? s.claimed : "?"}`
               + ` / 投递 ${s ? s.pendingOutbox : "?"}）`
               + `，已续推 ${st.resumes || 0} 次，连续无进展 ${st.stalled || 0}`;
        } else {
          line += `\n   项目 ${usePid}：读不到 graph.json`;
        }
      } else {
        line += "\n   尚未认到项目（跑一次 `fgs kickoff/init <pid>` 后生效）";
      }
      try { ctx?.ui?.notify(line, "info"); } catch { /* 无 UI */ }
      console.log(line);
    },
  });
}

/**
 * 读 reflect-paper 发布的净活跃状态，判「反思是否到期」。
 * fail-open：状态没发布 / 模块加载失败 / 已关闭 → 一律返回 null（走续推）。
 */
async function checkReflectDue() {
  try {
    if (process.env.PI_LAB_REFLECT === "0") return null;
    const pub = (globalThis as any).__PI_LAB_REFLECT;
    if (!pub || typeof pub.activeMs !== "number") return null;
    // 状态太旧（>2 分钟没更新）说明那条通道没在跑，不拿陈旧数据判
    if (typeof pub.at === "number" && Date.now() - pub.at > 2 * 60 * 1000) return null;
    const rm = await loadReflectMod();
    if (!rm) return null;
    const v = rm.decideReflect({
      activeMs: pub.activeMs, toolCalls: pub.toolCalls,
      nudges: pub.nudges, lastNudgeActiveMs: pub.lastNudgeActiveMs,
    }, {});
    if (!v || v.action !== "nudge") return null;
    // 打戳：bump 一次，避免同一窗口内反复唤醒（与忙时通道共用同一计数源）
    try {
      const next = rm.bumpReflect({ ...pub, activeMs: pub.activeMs });
      (globalThis as any).__PI_LAB_REFLECT = { ...pub, nudges: next.nudges,
        lastNudgeActiveMs: next.lastNudgeActiveMs, at: Date.now() };
    } catch { /* 打戳失败也不影响本轮唤醒 */ }
    return { paper: v.paper, why: v.why, activeMs: pub.activeMs, toolCalls: pub.toolCalls };
  } catch {
    return null;
  }
}

let rmod = null;
let rmodTried = false;
async function loadReflectMod() {
  if (rmod || rmodTried) return rmod;
  rmodTried = true;
  try {
    const p = path.join(LAB, "tools", "reflectwatch.mjs");
    const m = await import(pathToFileURL(p).href);
    if (typeof m.decideReflect === "function") rmod = m;
  } catch { rmod = null; }
  return rmod;
}

/** /idle status 用的小包装（mod 是异步加载的，这里同步取一次；拿不到就返回 null） */
function snapshotFromGraphSafe(pid, board) {
  try {
    if (!mod || !board) return null;
    return mod.snapshotFromGraph(board.graph, board.pendingOutbox);
  } catch { return null; }
}
