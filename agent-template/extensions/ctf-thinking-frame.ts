/**
 * pi-lab: ctf-thinking-frame —— 给「思考块」加边框
 *
 * 由来（2026-10-10 用户）：思考块原来是**一整块黑底**，在透明终端里完全不合群；
 * 把背景改透明之后，又少了"这是思考"的视觉边界 → 现在用**左侧竖线 + 上下横线**框出来。
 *
 * 做法：pi 的 `registerMarkdownTransformer` 能拿到 messageType 与**精确可用宽度**，
 * 我们只对 `assistant-thinking` 下手，把它整段包进 Markdown 引用块（引用块自带 `│ ` 左框），
 * 再在首尾各加一条横线做"盒感"。
 *
 * 为什么不去手工 wrap + 右侧封边：那会破坏思考里的 ``` 代码围栏（被我加的 `│ ` 前缀拆散，
 * 代码高亮全没）。引用块是 Markdown 原生语义，**里面照旧能嵌代码块**，宽度交给渲染器算 —— 稳。
 *
 * 注意：hook 是**显示层**的，会话与模型上下文里的原文一字不改。
 * 开关：PI_THINK_FRAME=0 关掉。
 */

import * as fs from "node:fs";
import * as path from "node:path";

const LAB = process.env.PI_LAB_ROOT || process.cwd();
const LOG = path.join(LAB, "log", "thinking-frame.log");

function log(line) {
  try {
    fs.mkdirSync(path.dirname(LOG), { recursive: true });
    fs.appendFileSync(LOG, `${new Date().toISOString()} ${line}\n`);
  } catch { /* ignore */ }
}

/** 纯函数：给思考 markdown 套框（便于单测） */
export function frameThinking(markdown, availableWidth) {
  const src = String(markdown ?? "");
  if (!src.trim()) return src;
  const W = Math.max(20, Number(availableWidth) || 80) - 2;   // 引用块吃掉 2 列（"│ "）
  const label = "💭 思考";
  // "─── 💭 思考 " 的可见宽度：3 + 1 + 2(emoji) + 1 + 4(中文) + 1 = 12
  const ruleLen = Math.max(4, W - 12);
  const top = `${"─".repeat(3)} ${label} ${"─".repeat(ruleLen)}`;
  const bottom = "─".repeat(Math.max(8, W));

  const body = src
    .split("\n")
    .map((l) => (l.trim() ? `> ${l}` : ">"))
    .join("\n");

  return `> ${top}\n>\n${body}\n>\n> ${bottom}`;
}

export default function ctfThinkingFrame(pi) {
  if (process.env.PI_THINK_FRAME === "0") return;
  try {
    if (typeof pi.registerMarkdownTransformer !== "function") {
      log("skip: 这个 pi 版本没有 registerMarkdownTransformer");
      return;
    }
    pi.registerMarkdownTransformer((markdown, { messageType, availableWidth }) => {
      if (messageType !== "assistant-thinking") return markdown;
      return frameThinking(markdown, availableWidth);
    });
    log("registered (thinking frame on)");
  } catch (e) {
    log(`register failed: ${e && e.message}`);
  }
}
