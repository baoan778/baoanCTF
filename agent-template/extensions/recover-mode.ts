/**
 * pi-lab: recover-mode —— 零依赖逃生舱（X-1）
 *
 * 为什么要有它
 * ------------
 * 本仓有四道闸门（idle-resume / lane-watch / reflect / flag-watch）会**主动拦截**。
 * 它们各自都有逃生门（/idle off、.no-idle、环境变量），但那些逃生门的前提是
 * 「pi 还能收到我的命令」—— 一旦闸门自身有 BUG（判定条件写死成恒真、
 * 或者某个钩子把所有消息拦成空），就没有任何通道把话送进去了。
 *
 * OpenSecurity 2.0 的对应做法（文章 A-16）：消息开头加前缀 RECOVER-MODE，
 * 跳过插件全部拦截。它自陈这是「零依赖通道：只认这个字符串前缀，
 * 不碰控制台、配置等任何可能坏掉的依赖」。本扩展照此实现。
 *
 * 零依赖是什么意思（逐条可核对）
 * ------------------------------
 * 1. **不 import 任何本地模块**（不 import reflectwatch/idlewatch/board 任何东西）
 *    —— 只用 node 内置 fs / path。理由：任何本地模块都可能成为故障源。
 * 2. **不读配置决定是否生效**，只认一个关闭开关 PI_LAB_RECOVER=0。
 * 3. **不依赖其它扩展已加载**：本扩展只**写**一个 globalThis 标记，从不**读**别人的状态。
 * 4. **失败静默**：写标记失败也吞异常，绝不让消息卡住。
 *
 * 谁读这个标记
 * ------------
 * 四道闸门在拦截前调 recoverActive()（各扩展内一份 3 行实现，不做文件 import）。
 * 用 globalThis 而不是 import，是为了让「闸门」与「逃生舱」之间**没有文件依赖**：
 * 把本文件整个删掉，闸门照常工作，只是没了逃生通道。
 *
 * 为什么 input 钩子够用（时序论证）
 * --------------------------------
 * 所有闸门的拦截点（tool_call / agent_before_settle / tool_result）都在
 * **用户消息进来之后**才发生。input 是用户消息的第一个事件，所以标记一定先写上。
 *
 * 逃生门（自指，必须自己能关）
 *   · 环境变量 PI_LAB_RECOVER=0 —— 连逃生舱本身也关掉（演练用）
 *
 * 日志：log/recover-mode.jsonl（每次激活留一条，赛后可查「当时是不是真被拦死了」）
 */

import * as fs from "node:fs";
import * as path from "node:path";

/** 触发前缀（与 OpenSecurity 2.0 一致，方便对照） */
export const RECOVER_PREFIX = ">>>RECOVER-MODE<<<";

/** 激活有效期：一次激活覆盖后续 N 分钟内的拦截判定（够走完一轮修复） */
export const RECOVER_TTL_MS = 10 * 60 * 1000;

/** 找仓库根：只按**文件存在**判据，不做任何 import */
export function findLab(): string {
  const env = process.env.PI_LAB_ROOT;
  const probe = (d: string) => {
    try { return fs.existsSync(path.join(d, "board", "fgs.py")); } catch { return false; }
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
const LOG = process.env.PI_LAB_RECOVER_LOG || path.join(LAB, "log", "recover-mode.jsonl");
const MAX_LOG = 1024 * 1024;

export function appendLog(rec: Record<string, unknown>, logPath = LOG) {
  try {
    fs.mkdirSync(path.dirname(logPath), { recursive: true });
    try {
      if (fs.statSync(logPath).size > MAX_LOG) fs.renameSync(logPath, logPath + ".1");
    } catch { /* 文件不存在 */ }
    fs.appendFileSync(logPath, JSON.stringify(rec) + "\n");
  } catch { /* 记不上不影响逃生 */ }
}

/** 剥离前缀后的正文（保留原消息其余内容，不做任何改写） */
export function stripPrefix(text: unknown): { active: boolean; body: string } {
  const s = String(text ?? "");
  const t = s.replace(/^[\s\u3000]+/, "");
  if (!t.startsWith(RECOVER_PREFIX)) return { active: false, body: s };
  const body = t.slice(RECOVER_PREFIX.length).replace(/^[\s\u3000]+/, "");
  return { active: true, body };
}

/** 标记是否仍然有效（各闸门调用） */
export function recoverActive(now = Date.now()): boolean {
  try {
    const g = (globalThis as any).__PI_LAB_RECOVER;
    if (!g || typeof g.until !== "number") return false;
    return now < g.until;
  } catch {
    return false;
  }
}

export default function recoverMode(pi: any) {
  const hardOff = process.env.PI_LAB_RECOVER === "0";
  let activations = 0;

  pi.on("input", (event: any, ctx: any) => {
    try {
      if (hardOff) return { action: "continue" as const };
      const r = stripPrefix(event?.text);
      if (!r.active) return { action: "continue" as const };

      const until = Date.now() + RECOVER_TTL_MS;
      try {
        (globalThis as any).__PI_LAB_RECOVER = { until, at: Date.now() };
      } catch { /* 写不上也不拦消息 */ }
      activations++;
      appendLog({
        ts: new Date().toISOString(),
        action: "activate",
        until: new Date(until).toISOString(),
        ttl_min: Math.round(RECOVER_TTL_MS / 60000),
        n: activations,
        body_len: r.body.length,
      });
      try {
        ctx?.ui?.notify("🚑 RECOVER-MODE 已激活：本仓四道闸门放行（10 分钟）", "warning");
      } catch { /* 无 UI */ }

      // 关键：把前缀**剥掉**再送进去 —— 模型看到的应是原指令，
      // 而不是一句它不认识的控制标记（否则它会去解释这个标记）。
      return { action: "transform" as const, text: r.body };
    } catch {
      return { action: "continue" as const };
    }
  });

  pi.registerCommand("recover", {
    description: "/recover [status] —— 零依赖逃生舱状态",
    handler: async (_args: string, ctx: any) => {
      const on = !hardOff && recoverActive();
      const g = (globalThis as any).__PI_LAB_RECOVER;
      const left = g && typeof g.until === "number"
        ? Math.max(0, Math.round((g.until - Date.now()) / 60000))
        : 0;
      const line = "🚑 逃生舱：" + (hardOff
        ? "已硬关闭（PI_LAB_RECOVER=0）"
        : on ? "激活中，还剩 " + left + " 分钟" : "待命（未激活）")
        + "｜本次会话已激活 " + activations + " 次"
        + "｜用法：消息开头加 " + RECOVER_PREFIX;
      appendLog({ ts: new Date().toISOString(), action: "status", on, activations });
      try { ctx?.ui?.notify(line, "info"); } catch { /* 无 UI */ }
      console.log(line);
    },
  });
}
