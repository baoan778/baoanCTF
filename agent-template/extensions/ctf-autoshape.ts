/**
 * pi-lab: ctf-autoshape —— 大输出自动摸形状
 *
 * 为什么：CTF 里最常见的动作是「跑一条命令 → 喷出几万行」。
 * 本仓纪律写着「大输出不许整读，先 head/grep -c/sort|uniq -c」——
 * 但那是**人**的纪律；agent 经常忍不住整块吞进上下文，直接把 token 烧掉。
 * 这个扩展把那套纪律变成**机器行为**：输出超阈值时，自动在结果**末尾追加**一张「形状卡」。
 *
 * ⚠ 只追加，不截断：关键行永远还在原文里；形状卡只是让我一眼看清"这坨东西长什么样"。
 *
 * 形状卡内容（都是流式扫描，最多处理前 200K 字符）：
 *   · 规模：多少行 / 多少字符
 *   · 高频行形态：把数字/hex/UUID/路径参数化后数 top 5（治"看起来一堆但其实同一种"）
 *   · 常见线索计数：URL / IP / 路径 / hex 串 / base64 串 / 错误行 / HTTP 状态码
 *   · 首尾各 2 行（截断到 160 字符）
 *
 * 阈值/开关：环境变量 PI_AUTOSHAPE_MIN（默认 12000 字符）、PI_AUTOSHAPE=0 关闭。
 * 记账：每次命中写一行 log/autoshape.jsonl（估算省了多少字符）。
 */

import * as fs from "node:fs";
import * as path from "node:path";

const LAB = process.env.PI_LAB_ROOT || process.cwd();
const LOG = path.join(LAB, "log", "autoshape.jsonl");
const MIN_CHARS = Number(process.env.PI_AUTOSHAPE_MIN || 12000);
const MAX_SCAN = 200_000;      // 只扫前 200K 字符（够统计形态了，别把 CPU 烧在超长输出上）
const MAX_CARD_LINES = 16;

function log(rec) {
  try {
    fs.mkdirSync(path.dirname(LOG), { recursive: true });
    fs.appendFileSync(LOG, JSON.stringify(rec) + "\n");
  } catch { /* ignore */ }
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

/** 把一行的"具体值"抽掉，只留形态 —— 用来数"这种行出现了几次" */
export function shapeOf(line) {
  return line
    .slice(0, 200)
    .replace(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi, "<uuid>")
    .replace(/\b\d{1,3}(\.\d{1,3}){3}\b/g, "<ip>")   // IP 要先归一，否则会被下面的版本号规则吃掉
    .replace(/0x[0-9a-f]+/gi, "<hex>")
    .replace(/\b\d+(\.\d+){1,3}\b/g, "<ver>")
    .replace(/\d+/g, "<n>")
    .replace(/[A-Za-z0-9+/]{24,}={0,2}/g, "<b64>")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 90);
}

/** 纯函数：给定文本 → 形状卡行数组（不依赖 pi，可单测）*/
export function shapeCard(text, minChars = MIN_CHARS) {
  if (!text || text.length < minChars) return null;
  const scanned = text.length > MAX_SCAN ? text.slice(0, MAX_SCAN) : text;
  const lines = scanned.split("\n");
  const nLines = lines.length;

  const shapes = new Map();
  for (const l of lines) {
    if (!l.trim()) continue;
    const s = shapeOf(l);
    if (s.length < 4) continue;
    shapes.set(s, (shapes.get(s) || 0) + 1);
  }
  const top = [...shapes.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5);

  const count = (re) => {
    let n = 0;
    for (const l of lines) if (re.test(l)) n++;
    return n;
  };
  const cues = [
    ["URL", count(/https?:\/\//)],
    ["IP:端口", count(/\b\d{1,3}(\.\d{1,3}){3}:\d+\b/)],
    ["绝对路径", count(/(^|\s)\/[a-z0-9_.-]+\//i)],
    ["hex≥16", count(/\b[0-9a-f]{16,}\b/i)],
    ["base64样", count(/[A-Za-z0-9+/]{24,}={0,2}/)],
    ["报错/hint", count(/error|exception|traceback|failed|denied|not found/i)],
    ["HTTP码", count(/\b(200|301|302|401|403|404|500|502|503)\b/)],
    ["flag形状", count(/[A-Za-z0-9_]{2,20}\{[^}\n]{4,120}\}/)],
  ].filter(([, n]) => n > 0);

  const cut = (s) => (s.length > 160 ? s.slice(0, 160) + "…" : s);
  const head = lines.slice(0, 2).map((l) => cut(l.replace(/\s+$/, "")));
  const tail = lines.slice(-2).map((l) => cut(l.replace(/\s+$/, "")));

  const card = [];
  card.push("");
  card.push(`⚙ 形状卡（自动摸的，原文完整保留在上面）`);
  card.push(`· 规模：约 ${nLines} 行 / ${text.length} 字符${text.length > MAX_SCAN ? `（只统计了前 ${MAX_SCAN} 字符）` : ""}`);
  if (top.length) {
    card.push(`· 高频行形态：`);
    for (const [s, n] of top) card.push(`    ×${String(n).padStart(5)}  ${s}`);
  }
  if (cues.length) card.push(`· 线索计数：${cues.map(([k, n]) => `${k} ${n}`).join(" · ")}`);
  card.push(`· 首 2 行：${head.join(" ⏎ ")}`);
  card.push(`· 尾 2 行：${tail.join(" ⏎ ")}`);
  card.push(`· 下一步建议：先 ${top.length > 1 ? "`sort|uniq -c|sort -rn|head` 或 " : ""}\`grep -c\` / \`head\` 定位，再决定要不要整读`);
  return card.slice(0, MAX_CARD_LINES);
}

export default function ctfAutoshape(pi) {
  if (process.env.PI_AUTOSHAPE === "0") return;
  const seen = new Set();   // 同一个 toolCallId 只加一次

  pi.on("tool_result", async (event, _ctx) => {
    try {
      const id = event?.toolCallId || "?";
      if (seen.has(id)) return;
      const text = contentToText(event?.content);
      if (!text || text.length < MIN_CHARS) return;
      const card = shapeCard(text);
      if (!card) return;
      seen.add(id);
      log({ ts: new Date().toISOString(), tool: event?.toolName ?? "?", chars: text.length, lines: text.split("\n").length });
      const block = "\n" + card.join("\n") + "\n";
      if (Array.isArray(event?.content)) return { content: [...event.content, { type: "text", text: block }] };
      if (typeof event?.content === "string") return { content: event.content + block };
      return;
    } catch {
      return;   // 任何异常都放行原始结果
    }
  });
}
