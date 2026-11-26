/**
 * PiCTF: flag-watch —— 工具输出里的 flag 形态自动报警
 *
 * 为什么需要它：AGENTS.md 的 flag 纪律写着 ——
 *   「**agent 最常见的丢分方式是 flag 已经出现在输出里，却当成普通文本划过去了**」。
 * 在那之前只有**纪律**（要求人盯），没有**机器**。这个扩展把纪律变成机器行为。
 *
 * 它做什么：在 `tool_result` 钩子里扫每次工具输出（bash/read/grep… 的文本），
 *   命中 → ① 把警告**插进结果内容**（模型一定看得到）② notify ③ 记 `log/flag-watch.jsonl` 留痕。
 *   同一 session 同一 flag 只报一次（防刷屏训练出「狼来了」）。
 *
 * 为什么不在这里直接判真伪：形状对 ≠ 是真 flag（可能是自己拼的 / 文档里的示例）。
 *   本扩展只负责「**别划过去**」；逐字溯源用 `fgs verify`，提交确认用 `fgs confirm`（平台回执才算），
 *   这两步由主会话按 AGENTS.md §4 执行 —— 本扩展不调用、也不判断。
 *
 * 纯正则 + 纯 fs，不起进程；全程 try/catch —— 宁可自己静默失效，也绝不拖累 pi 跑工具。
 */

import * as fs from "node:fs";
import * as path from "node:path";
import { createHash } from "node:crypto";
import { pathToFileURL } from "node:url";

const LAB = process.env.PICTF_ROOT || process.env.PI_LAB_ROOT || process.cwd();
const MOD = path.join(LAB, "tools", "flagwatch.mjs");
// 两个环境变量名都认：历史名字与测试/文档里用的名字（曾因只认一个导致留痕静默丢失）
const LOG =
  process.env.PICTF_FLAGWATCH_LOG ||
  process.env.PI_LAB_FLAGWATCH_LOG ||
  path.join(LAB, "log", "flag-watch.jsonl");
const MAX_LOG = 2 * 1024 * 1024; // 2MB 后轮转成 .1（防日志涨爆盘）

let mod = null;      // { scanFlags, warnLine }
let modTried = false;

async function loadMod() {
  if (mod || modTried) return mod;
  modTried = true;
  try {
    const m = await import(pathToFileURL(MOD).href);
    if (typeof m.scanFlags === "function") mod = m;
  } catch {
    mod = null; // fail-open：工具输出原样通过
  }
  return mod;
}

/** 把 tool_result 的 content 拼成纯文本（数组或字符串两种形态都认） */
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
  } catch { /* 记不上日志不影响报警 */ }
}

/** 会话标识：拿不到就用 "adhoc"（多路子代理共用一个目录也能用，靠 sha1 区分） */
function sessionTag(ctx) {
  try {
    const s = ctx?.session?.id || ctx?.sessionId || ctx?.id;
    if (typeof s === "string" && /^[\w.-]{1,64}$/.test(s)) return s;
  } catch { /* 无上下文 */ }
  return "adhoc";
}

/**
 * 把命中处的上下文片段落到 log/flag-ctx/<sid>/<sha1>.txt，返回 sha1 坐标。
 * 只取每条命中的 flag 字符串在文本里的位置，前后各 600 字 —— 够复现，又不把全量输出搬上盘。
 */
function spillContext(text, hits, sid) {
  const pieces = [];
  for (const h of hits) {
    const at = text.indexOf(h.flag);
    if (at < 0) continue;
    const from = Math.max(0, at - 600);
    const to = Math.min(text.length, at + h.flag.length + 600);
    pieces.push(`--- ${h.flag} @ ${at} ---\n${text.slice(from, to)}`);
  }
  if (!pieces.length) return null;
  const body = pieces.join("\n\n").slice(0, 200 * 1024);   // 硬上限 200KB
  const sha = createHash("sha1").update(body).digest("hex");
  const dir = path.join(path.dirname(LOG), "flag-ctx", sid);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, sha + ".txt"), body, "utf8");
  return sha;
}

export default function flagWatch(pi) {
  const seen = new Set();

  pi.on("tool_result", async (event, ctx) => {
    try {
      const m = await loadMod();
      if (!m) return;
      const text = contentToText(event?.content);
      if (!text || text.length < 8) return;

      const hits = m.scanFlags(text).filter((h) => !seen.has(h.flag));
      if (!hits.length) return;
      for (const h of hits) seen.add(h.flag);

      // ★ 2026-11-05：命中处的**原始输出片段**落盘，供 UI 展开证据（AGENTS.md §4：
      //   「把取到它的那条命令与原始输出一起给，方便复现」）。只落命中前后各 600 字，
      //   绝不落全量输出（可能几 MB，且 pwn 题里常有不可读字节）。
      //   坐标 (sid, sha1) 进日志，界面据此取回；落盘失败不影响报警。
      const sid = sessionTag(ctx);
      let ctxRef = null;
      try {
        ctxRef = spillContext(text, hits, sid);
      } catch { ctxRef = null; }

      appendLog({
        ts: new Date().toISOString(),
        tool: event?.toolName ?? "?",
        sid: ctxRef ? sid : null,
        ctx: ctxRef,
        hits: hits.map((h) => ({ flag: h.flag, kind: h.kind, confidence: h.confidence })),
      });

      const warn = m.warnLine(hits);
      try { ctx?.ui?.notify(`🚩 flag-watch: ${hits.map((h) => h.flag).join(" | ")}`, "warning"); } catch { /* 无 UI 上下文 */ }

      // 插进结果内容：模型一定会看到（这是本扩展存在的主要理由）
      if (Array.isArray(event?.content)) {
        return { content: [...event.content, { type: "text", text: `\n\n${warn}\n` }] };
      }
      if (typeof event?.content === "string") {
        return { content: `${event.content}\n\n${warn}\n` };
      }
      return;
    } catch {
      return; // 任何异常都放行原始结果
    }
  });
}
