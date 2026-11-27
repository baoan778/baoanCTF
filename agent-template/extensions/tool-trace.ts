/**
 * pi-lab: tool-trace —— 工具输出留痕（压缩/重启后仍可回查）
 *
 * 为什么要有它
 * ------------
 * 会话被压缩或重启后，"当时那条命令的原始输出是什么"就没了。
 * 而 `fgs verify` 的逐字溯源要求"这个值真的出现在某条真实输出里" ——
 * 输出一旦随压缩消失，人就只能靠**记忆复述**，而记忆复述正是编造值的来源。
 *
 * 已有机制为什么不够
 * ------------------
 * · `flag-watch` 只记**命中 flag 形状的片段**（±600 字符），没命中就什么都不留
 * · `board/projects/<pid>/log.jsonl` 记的是黑板事件（fact/step/apply），不是工具输出
 * → "我跑过那条命令、输出里有这个串"这件事**无处可查**
 *
 * 三条设计纪律（否则它会变成存储炸弹）
 * ------------------------------------
 * 1. **有界**：单条只留前 2000 / 后 3000 字符，中间标省略；单行超 400 字符压缩
 * 2. **轮转**：单文件超 8MB rename 成 .1，只留一代（不留无限历史）
 * 3. **fail-open**：写不进去就跳过，绝不影响工具本身
 *
 * ★ 边界：本扩展留的是**裁剪后**的片段，**不能**替代逐字溯源。
 *   要盖章请用原文（`fgs verify --from <文件>`）；这里只负责"让你还能找到那段输出"。
 *
 * 逃生门：`/trace off` · `touch board/projects/<pid>/.no-trace` · `PI_LAB_TOOLTRACE=0`
 */

import * as fs from "node:fs";
import * as path from "node:path";
import { pathToFileURL } from "node:url";

function findLab() {
  const env = process.env.PI_LAB_ROOT;
  if (env && fs.existsSync(path.join(env, "tools", "tracewatch.mjs"))) return env;
  let cur = process.cwd();
  for (let i = 0; i < 8; i++) {
    if (fs.existsSync(path.join(cur, "tools", "tracewatch.mjs"))) return cur;
    const up = path.dirname(cur);
    if (up === cur) break;
    cur = up;
  }
  return env || process.cwd();
}

const LAB = findLab();
const MOD = path.join(LAB, "tools", "tracewatch.mjs");
const BOARDS = path.join(LAB, "board", "projects");
const LOGDIR = path.join(LAB, "log", "tool-trace");
const MAX_LOG_BYTES = 8 * 1024 * 1024;
const MAX_SPILL_CHARS = 4 * 1000 * 1000;   // #47：原文落盘的硬上限（再大就别存）

let mod = null;
let modTried = false;

async function loadMod() {
  if (mod || modTried) return mod;
  modTried = true;
  try {
    const m = await import(pathToFileURL(MOD).href);
    if (typeof m.makeRecord === "function") mod = m;
  } catch {
    mod = null; // fail-open
  }
  return mod;
}

const PID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

export function pidFromCommand(cmd) {
  const s = String(cmd || "");
  if (!/\bfgs(\.py)?\b/.test(s)) return null;
  const m = s.match(/\bfgs(?:\.py)?\s+(?:kickoff|init|next|status|goal|snapshot|verify)\s+([A-Za-z0-9][A-Za-z0-9._-]{0,63})/);
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

export default function toolTrace(pi) {
  let pid = null;
  let off = process.env.PI_LAB_TOOLTRACE === "0";
  let written = 0;

  const skipFile = () => {
    try {
      return pid ? fs.existsSync(path.join(BOARDS, pid, ".no-trace")) : false;
    } catch { return false; }
  };

  /** 按天分文件：log/tool-trace/<pid或no-pid>/<YYYY-MM-DD>.jsonl */
  const fileFor = () => {
    const day = new Date().toISOString().slice(0, 10);
    const sub = pid ? pid : "_no-pid";
    return path.join(LOGDIR, sub, `${day}.jsonl`);
  };

  const rotateIfNeeded = (file) => {
    try {
      const st = fs.statSync(file);
      if (st.size >= MAX_LOG_BYTES) {
        fs.renameSync(file, `${file}.1`);   // 只留一代
      }
    } catch { /* 文件不存在，无需轮转 */ }
  };

  pi.on("tool_call", (event) => {
    try {
      const p = pidFromCommand(String(event?.input?.command ?? ""));
      if (p) pid = p;
    } catch { /* 放行 */ }
  });

  pi.on("tool_result", async (event) => {
    try {
      if (off || skipFile()) return;
      const m = await loadMod();
      if (!m) return;

      const tool = event?.toolName ?? "?";
      const text = contentToText(event?.content);
      const keep = m.shouldTrace(tool, event?.input, text, {});
      if (!keep.keep) return;

      const rec = m.makeRecord({
        ts: new Date().toISOString(),
        pid,
        tool,
        input: event?.input,
        resultText: text,
        isError: !!event?.isError,
        session: process.env.PI_SESSION_ID || null,
      }, {});

      // ★ 2026-11-17：被裁掉的正文另存一份到 <留痕目录>/cmd-output/，
      //   摘要里只留指针（rec.spill）。这样上下文不被大输出撑爆，原文又没丢 ——
      //   与 ARTEX 的 sessionWorkDir/cmd-output 同构。写不进去就删指针，绝不影响留痕。
      const file = fileFor();
      if (rec.spill) {
        try {
          if (text.length > MAX_SPILL_CHARS) throw new Error("too big");
          const full = path.join(path.dirname(file), rec.spill);
          fs.mkdirSync(path.dirname(full), { recursive: true });
          fs.writeFileSync(full, text, "utf8");
        } catch {
          delete rec.spill;
        }
      }
      fs.mkdirSync(path.dirname(file), { recursive: true });
      rotateIfNeeded(file);
      fs.appendFileSync(file, JSON.stringify(rec) + "\n", "utf8");
      written++;
    } catch {
      return;   // fail-open：留痕失败绝不影响工具
    }
  });

  pi.registerCommand("trace", {
    description: "工具输出留痕开关与状态：/trace [on|off|status]",
    handler: async (args, ctx) => {
      const a = String(args || "").trim().toLowerCase();
      let line;
      if (a === "off") { off = true; line = "📼 工具留痕：已关闭"; }
      else if (a === "on") { off = false; line = "📼 工具留痕：已开启"; }
      else {
        let files = 0, bytes = 0;
        try {
          const sub = path.join(LOGDIR, pid || "_no-pid");
          for (const f of fs.readdirSync(sub)) {
            if (!f.endsWith(".jsonl") && !f.endsWith(".jsonl.1")) continue;
            files++;
            try { bytes += fs.statSync(path.join(sub, f)).size; } catch { /* 忽略 */ }
          }
        } catch { /* 目录不存在 */ }
        line = `📼 工具留痕：${off ? "已关闭" : "已开启"}`
             + `｜本进程已写 ${written} 条`
             + `｜${pid || "_no-pid"} 目录 ${files} 个文件 / ${(bytes / 1024).toFixed(0)} KB`
             + `\n   位置 ${LOGDIR}/<pid>/<日期>.jsonl（单文件 8MB 轮转，只留一代）`
             + `\n   回查：grep -l "<关键词>" ${LOGDIR}/*/*.jsonl`;
      }
      try { ctx?.ui?.notify(line, "info"); } catch { /* 无 UI */ }
      console.log(line);
    },
  });
}
