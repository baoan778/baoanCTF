/**
 * pi-lab: reflect-paper —— 反思纸条（忙时通道，不打断工作）
 *
 * 为什么要有它
 * ------------
 * 长任务里最贵的错误是**在死路上闷头加速**。本仓 `skills/fgs-orchestrate` 写了纪律，
 * 但没有任何机制在跑偏时提醒 —— 纪律靠自觉，等于没有。
 *
 * 与 OpenSecurity 2.0 的关系（机制参考，代码不搬）
 * -----------------------------------------------
 * 它按「距上次盘点时长 × 工具调用密度」定期触发（默认 30 分钟）：
 *   忙时 → 附在命令输出之后（不打断）；空闲 → 发唤醒消息。
 * 本扩展实现**忙时通道**；空闲通道由同族的 `idle-resume` 负责。
 *
 * 三条设计纪律
 * ------------
 * 1. **不打断**：纸条挂在 `tool_result` 的 content 末尾 —— 模型下一次读工具输出时
 *    自然看见，不新起一轮、不抢主导权（与 `flag-watch` 同一手法，已实战验证）。
 * 2. **净活跃口径**：墙钟扣掉等人时长。一次权限询问挂 20 分钟不该算"跑了 20 分钟"，
 *    否则提醒全是噪声，多了就没人看。
 * 3. **有上限 + fail-open**：单场最多 8 次；模块加载失败/任何异常都静默跳过。
 *
 * 逃生门：`/reflect off` · `touch board/projects/<pid>/.no-reflect` · `PI_LAB_REFLECT=0`
 * 日志：log/reflect-paper.jsonl（每次提醒都留一条，赛后可复盘"提醒有没有用"）
 */

import * as fs from "node:fs";
import * as path from "node:path";
import { pathToFileURL } from "node:url";

function findLab() {
  const env = process.env.PI_LAB_ROOT;
  if (env && fs.existsSync(path.join(env, "tools", "reflectwatch.mjs"))) return env;
  let cur = process.cwd();
  for (let i = 0; i < 8; i++) {
    if (fs.existsSync(path.join(cur, "tools", "reflectwatch.mjs"))) return cur;
    const up = path.dirname(cur);
    if (up === cur) break;
    cur = up;
  }
  return env || process.cwd();
}

const LAB = findLab();
const MOD = path.join(LAB, "tools", "reflectwatch.mjs");
const LOG = path.join(LAB, "log", "reflect-paper.jsonl");
const BOARDS = path.join(LAB, "board", "projects");
const MAX_LOG = 4 * 1024 * 1024;

let mod = null;
let modTried = false;

async function loadMod() {
  if (mod || modTried) return mod;
  modTried = true;
  try {
    const m = await import(pathToFileURL(MOD).href);
    if (typeof m.decideReflect === "function") mod = m;
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
    } catch { /* 不存在 */ }
    fs.appendFileSync(LOG, JSON.stringify(rec) + "\n");
  } catch { /* 记不上不影响主流程 */ }
}

const PID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

export function pidFromCommand(cmd) {
  const s = String(cmd || "");
  if (!/\bfgs(\.py)?\b/.test(s)) return null;
  const m = s.match(/\bfgs(?:\.py)?\s+(?:kickoff|init|next|status|goal|snapshot)\s+([A-Za-z0-9][A-Za-z0-9._-]{0,63})/);
  return m && PID_RE.test(m[1]) ? m[1] : null;
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

export default function reflectPaper(pi) {
/** 零依赖逃生舱（recover-mode.ts 只写 globalThis 标记，两边无文件依赖） */
function recoverActive() {
  try {
    const g = (globalThis as any).__PI_LAB_RECOVER;
    return !!(g && typeof g.until === "number" && Date.now() < g.until);
  } catch { return false; }
}
  let pid = null;
  let off = process.env.PI_LAB_REFLECT === "0";
  let st = { nudges: 0, lastNudgeActiveMs: 0 };
  let wallStart = Date.now();
  let waitMs = 0;              // 等人累计（净活跃口径要用）
  let waitOpenAt = null;
  let toolCalls = 0;

  const skipFile = () => {
    try {
      return pid ? fs.existsSync(path.join(BOARDS, pid, ".no-reflect")) : false;
    } catch { return false; }
  };

  const netActiveNow = () => {
    const wall = Date.now() - wallStart;
    const w = waitMs + (waitOpenAt ? Date.now() - waitOpenAt : 0);
    return Math.max(0, wall - w);
  };

  /**
   * 把「净活跃 + 调用数 + 提醒计数」发布到 globalThis，供**空闲通道**（idle-resume）
   * 用同一份状态判「反思是否到期」。
   *
   * 为什么走 globalThis 而不是 import：扩展之间没有相对 import 的先例（每个扩展都是
   * 独立加载的 .ts），跨文件 import 会让「一个扩展加载失败拖垮另一个」。
   * 这与 recover-mode 用 globalThis 传标记是同一套约定：**只传数据，不传行为**。
   * 判据逻辑仍只有一份 —— tools/reflectwatch.mjs 的 decideReflect。
   */
  const publishState = () => {
    try {
      (globalThis as any).__PI_LAB_REFLECT = {
        activeMs: netActiveNow(),
        toolCalls,
        nudges: st.nudges,
        lastNudgeActiveMs: st.lastNudgeActiveMs,
        pid,
        at: Date.now(),
      };
    } catch { /* 发布失败不影响忙时通道 */ }
  };

  // 等人时长：权限询问/UI 弹窗期间不算活跃
  pi.on("ui_prompt_start", () => { waitOpenAt = Date.now(); });
  pi.on("ui_prompt_end", () => {
    if (waitOpenAt) { waitMs += Date.now() - waitOpenAt; waitOpenAt = null; }
  });

  pi.on("tool_call", (event) => {
    try {
      const cmd = String(event?.input?.command ?? "");
      const p = pidFromCommand(cmd);
      if (p) { pid = p; }
    } catch { /* 放行 */ }
  });

  // 主钩子：把纸条追加到工具输出末尾
  pi.on("tool_result", async (event, ctx) => {
    try {
      if (off || skipFile()) return;
      if (recoverActive()) return;
      toolCalls++;
      const m = await loadMod();
      if (!m) return;

      const active = netActiveNow();
      const verdict = m.decideReflect({
        activeMs: active, toolCalls,
        nudges: st.nudges, lastNudgeActiveMs: st.lastNudgeActiveMs,
      }, {});

      if (verdict.action !== "nudge") {
        publishState();          // 未到期也发布：空闲通道要读最新净活跃
        return;
      }

      st = m.bumpReflect({ ...st, activeMs: active });
      publishState();
      appendLog({
        ts: new Date().toISOString(), action: "nudge", pid,
        active_min: Math.round(active / 60000), tool_calls: toolCalls,
        nudges: st.nudges, why: verdict.why,
      });
      try { ctx?.ui?.notify(`📝 反思纸条（净活跃 ${Math.round(active / 60000)} 分钟）`, "info"); }
      catch { /* 无 UI */ }

      // 追加到 content 末尾：模型下一次读输出时自然看见，不新起一轮
      const base = Array.isArray(event?.content)
        ? event.content
        : [{ type: "text", text: contentToText(event?.content) }];
      return {
        content: [
          ...base,
          { type: "text", text: `\n\n${verdict.paper}` },
        ],
      };
    } catch {
      return;   // 任何异常都放行
    }
  });

  pi.registerCommand("reflect", {
    description: "反思纸条开关与状态：/reflect [on|off|status|now]",
    handler: async (args, ctx) => {
      const a = String(args || "").trim().toLowerCase();
      let line;
      if (a === "off") { off = true; line = "📝 反思纸条：已关闭"; }
      else if (a === "on") { off = false; line = "📝 反思纸条：已开启"; }
      else if (a === "now") {
        // 手动要一张，便于赛前演练
        const m = await loadMod();
        const paper = m ? m.buildPaper(Math.round(netActiveNow() / 60000), toolCalls) : "（模块未加载）";
        line = `📝 手动纸条：\n${paper}`;
      } else {
        line = `📝 反思纸条：${off ? "已关闭" : "已开启"}`
             + `｜净活跃 ${Math.round(netActiveNow() / 60000)} 分钟`
             + `｜工具调用 ${toolCalls} 次`
             + `｜已提醒 ${st.nudges} 次`
             + (pid ? `｜项目 ${pid}` : "｜未认到项目");
      }
      publishState();
      appendLog({ ts: new Date().toISOString(), action: a || "status", pid, line });
      try { ctx?.ui?.notify(line, "info"); } catch { /* 无 UI */ }
      console.log(line);
    },
  });
}
