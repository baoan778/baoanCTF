/**
 * pi-lab: ctf-cost —— 会话开销看板 + 预算闸（治「52 元/flag → 284 元/flag」）
 *
 * 依据（docs/经验-工程纪律.md §2）：外部冠军整场 <50 元是健康线；而 TCH 实测
 * 前两天 52 元/flag、第三天 284 元/flag —— **边际递减非常陡**。
 * 所以真正要盯的不是"花了多少"，而是「**烧了多少 token / 多少轮，有没有换来新事实**」。
 *
 * 本扩展做三件事：
 *   1. 累计本会话 token/轮数（message_end 的 usage）
 *   2. `/cost` 一张卡：token、轮数、耗时、flag 命中、**黑板事实推进**、建议（继续/换题）
 *   3. 阈值提醒 +（可选）**硬闸**：`/cost gate on` 后，超预算再开 subagent 会被拦下
 *
 * 注意：opencode-go 是订阅制，token ≠ 直接计费；所以这里的"预算"是**注意力/时间预算**，
 * 不是钱。别把它说成花了多少钱。
 *
 * 数据分析：本会话 flag 命中读 log/flag-watch.jsonl；黑板推进读 board/projects/<pid>/graph.json
 * 的 verified 事实总数（每次 /cost 或阈值检查时重算，10s 缓存）。
 */

import * as fs from "node:fs";
import * as path from "node:path";

const LAB = process.env.PI_LAB_ROOT || process.cwd();
const BOARD = path.join(LAB, "board", "projects");
const FLAGLOG = path.join(LAB, "log", "flag-watch.jsonl");
const COSTLOG = path.join(LAB, "log", "cost.jsonl");

const WARN_OUT = Number(process.env.PI_COST_WARN_OUT || 150_000);   // 输出 token 预警线
const HARD_OUT = Number(process.env.PI_COST_HARD_OUT || 300_000);   // 硬闸线（默认不开）

function appendLog(rec) {
  try {
    fs.appendFileSync(COSTLOG, JSON.stringify(rec) + "\n");
  } catch { /* ignore */ }
}

function readCapped(p, cap = 1024 * 1024) {
  try {
    const st = fs.statSync(p);
    if (!st.isFile() || st.size > cap) return null;
    return fs.readFileSync(p, "utf8");
  } catch { return null; }
}

/** 全仓黑板的 verified 事实总数（进度指标） */
export function verifiedTotal() {
  let n = 0, boards = 0;
  try {
    for (const pid of fs.readdirSync(BOARD)) {
      const txt = readCapped(path.join(BOARD, pid, "graph.json"), 2 * 1024 * 1024);
      if (!txt) continue;
      try {
        const g = JSON.parse(txt);
        const facts = Object.values(g.nodes || {}).filter((x) => x && x.type === "fact");
        n += facts.filter((f) => f.status === "verified").length;
        boards++;
      } catch { /* 坏图跳过 */ }
    }
  } catch { /* 没黑板 */ }
  return { verified: n, boards };
}

/** 本会话（since 某个时间点）flag 命中条数 */
export function flagHitsSince(sinceMs) {
  const txt = readCapped(FLAGLOG, 2 * 1024 * 1024);
  if (!txt) return 0;
  let n = 0;
  for (const line of txt.split("\n")) {
    const s = line.trim();
    if (!s.startsWith("{")) continue;
    try {
      const r = JSON.parse(s);
      if (Date.parse(r.ts) >= sinceMs) n += (r.hits || []).length;
    } catch { /* ignore */ }
  }
  return n;
}

export function fmtTok(n) {
  n = Number(n) || 0;
  if (n < 1000) return String(n);
  if (n < 1e6) return (n / 1000).toFixed(1) + "k";
  return (n / 1e6).toFixed(2) + "M";
}

export default function ctfCost(pi) {
  const startedMs = Date.now();
  const st = { inTok: 0, outTok: 0, turns: 0, warnedMid: false, warnedHigh: false, gate: false };
  let lastVerified = null;
  let verifiedAtTurn = 0;

  pi.on("message_end", (e) => {
    try {
      if (e?.message?.role !== "assistant") return;
      const u = e.message.usage || {};
      st.outTok += Number(u.output || 0) || 0;
      st.inTok += Number(u.input || 0) || 0;
    } catch { /* ignore */ }
  });

  const check = (ctx) => {
    try {
      if (!st.warnedMid && st.outTok >= WARN_OUT) {
        st.warnedMid = true;
        ctx?.ui?.notify(`💸 本会话输出已 ${fmtTok(st.outTok)} token（预警线 ${fmtTok(WARN_OUT)}）—— 敲 /cost 看边际产出，考虑换题或收口`, "warning");
        appendLog({ ts: new Date().toISOString(), event: "warn", outTok: st.outTok, turns: st.turns });
      }
      if (!st.warnedHigh && st.outTok >= HARD_OUT) {
        st.warnedHigh = true;
        ctx?.ui?.notify(`💸💸 输出 ${fmtTok(st.outTok)} token（硬闸线 ${fmtTok(HARD_OUT)}）${st.gate ? "，新 subagent 已被拦（/cost gate off 解除）" : ""}`, "warning");
        appendLog({ ts: new Date().toISOString(), event: "hard-warn", outTok: st.outTok, turns: st.turns });
      }
    } catch { /* ignore */ }
  };

  pi.on("turn_end", (_e, ctx) => {
    st.turns++;
    const v = verifiedTotal();
    if (lastVerified === null || v.verified > lastVerified) {
      lastVerified = v.verified;
      verifiedAtTurn = st.turns;
    }
    check(ctx);
  });

  // 硬闸（默认关）：超预算时拦下新开的 subagent
  pi.on("tool_call", (event, _ctx) => {
    try {
      if (!st.gate) return;
      if (event?.toolName !== "subagent") return;
      if (st.outTok < HARD_OUT) return;
      return { block: true, reason: `成本闸：本会话输出 ${fmtTok(st.outTok)} token 已超 ${fmtTok(HARD_OUT)}。先 /cost 看边际产出，或 /cost gate off 解除后再开 lane。` };
    } catch { return; }
  });

  pi.registerCommand("cost", {
    description: "会话开销与边际产出看板：/cost · /cost gate on|off（超预算拦新 lane）",
    handler: async (args, ctx) => {
      const a = String(args || "").trim().toLowerCase();
      if (a === "gate on" || a === "gate off") {
        st.gate = a.endsWith("on");
        ctx.ui.notify(`成本硬闸 → ${st.gate ? "开" : "关"}（线：输出 ${fmtTok(HARD_OUT)} token）`, "info");
        return;
      }
      const v = verifiedTotal();
      const flags = flagHitsSince(startedMs);
      const mins = Math.round((Date.now() - startedMs) / 60000);
      const sinceProgress = st.turns - verifiedAtTurn;
      const lines = [
        `╭─ 会话开销（订阅制：这里是注意力/时间预算，不是账单）`,
        `│ 输出 ${fmtTok(st.outTok)} token · 输入 ${fmtTok(st.inTok)} · ${st.turns} 轮 · ${mins} 分钟`,
        `│ 黑板：${v.boards} 张 · verified 事实 ${v.verified} 条（上次推进在 ${sinceProgress} 轮前）`,
        `│ flag 命中（本会话）：${flags} 条`,
        `│ 阈值：预警 ${fmtTok(WARN_OUT)}${st.warnedMid ? "（已触发）" : ""} · 硬闸 ${fmtTok(HARD_OUT)}${st.gate ? "（开）" : "（关）"}`,
      ];
      if (sinceProgress >= 25) lines.push(`│ ⚠ ${sinceProgress} 轮没推进黑板 —— 按 AGENTS「一条路线多次没进展就换攻击面」`);
      else if (st.outTok > WARN_OUT && flags === 0) lines.push(`│ ⚠ 输出已 ${fmtTok(st.outTok)} 但本会话 0 flag —— 考虑换题/换路线`);
      else lines.push(`│ ✓ 边际产出正常`);
      lines.push(`╰─ /cost gate on 开启硬闸 · 详情看 log/flag-watch.jsonl`);
      ctx.ui.setWidget("ctf-cost", lines);
      ctx.ui.notify(`输出 ${fmtTok(st.outTok)} tok · ${st.turns} 轮 · verified ${v.verified} · flag ${flags}`, "info");
    },
  });
}
