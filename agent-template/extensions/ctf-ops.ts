/**
 * pi-lab: ctf-ops —— 运营面小工具（靶场台账 / worker 面板 / 线索请求 / 上下文守卫 / WP 收集）
 *
 * 一个文件装 5 件小事，都是"打题过程中反复要用"的：
 *   /targets                    靶场台账：录题台收集的目标 + 手工登记，一表看完（免得我回头问你 nc 地址）
 *   /targets add <名> <目标>     手工登记一行
 *   /lanes                      在跑的 worker / 活跃黑板一览（现在要敲 fgs workers）
 *   /hint [一句话]               OSINT 最弱项（33%）专用：生成"需要你给一句线索"的请求（含剪贴板）
 *   /wp <pid>                   WP 素材收集：调 tools/wpcollect.py 生成草稿
 *   上下文守卫                   上下文到 90% 时提醒一次 /compact（治"压缩前才发现"）
 */

import * as fs from "node:fs";
import * as path from "node:path";
import { spawnSync } from "node:child_process";

const LAB = process.env.PI_LAB_ROOT || process.cwd();
const INBOX = path.join(LAB, "work", "inbox");
const BOARD = path.join(LAB, "board", "projects");
const TARGETS = path.join(LAB, "log", "ctf-targets.json");
const WPCOLLECT = path.join(LAB, "tools", "wpcollect.py");
const HINT_TEMPLATE = path.join(LAB, "prompts", "human-hint.md");

function readJSON(p, cap = 512 * 1024) {
  try {
    const st = fs.statSync(p);
    if (!st.isFile() || st.size > cap) return null;
    return JSON.parse(fs.readFileSync(p, "utf8"));
  } catch { return null; }
}

function saveTargets(list) {
  try {
    fs.mkdirSync(path.dirname(TARGETS), { recursive: true });
    fs.writeFileSync(TARGETS, JSON.stringify({ targets: list }, null, 2), "utf8");
    return true;
  } catch { return false; }
}

function winPath(p) {
  const m = /^\/mnt\/([a-z])\/(.*)$/.exec(p);
  return m ? `${m[1].toUpperCase()}:\\${m[2].replace(/\//g, "\\")}` : null;
}

function clipboard(text) {
  try {
    const tmp = path.join(LAB, "log", ".ops-clip.txt");
    fs.writeFileSync(tmp, text);
    const wp = winPath(tmp);
    if (!wp) return false;
    const r = spawnSync("powershell.exe", ["-NoProfile", "-Command", `Set-Clipboard -Value (Get-Content -LiteralPath '${wp}' -Raw)`], { timeout: 8000 });
    return !r.error && r.status === 0;
  } catch { return false; }
}

/** 台账 = 手工登记 + 录题台卡片（同一 name 时以手工为准） */
export function collectTargets() {
  const rows = [];
  const manual = readJSON(TARGETS)?.targets || [];
  for (const t of manual) rows.push({ ...t, src: "手工" });
  try {
    for (const f of fs.readdirSync(INBOX).filter((x) => x.endsWith(".json")).sort().reverse()) {
      const card = readJSON(path.join(INBOX, f)) || {};
      if (!card.target) continue;
      const name = card.pid || card.title || f;
      if (rows.some((r) => r.name === name)) continue;
      rows.push({ name, target: card.target, kind: card.kind || "?", note: card.title || "", ts: card.ts || "", src: "录题台" });
    }
  } catch { /* 没 inbox */ }
  return rows;
}

export default function ctfOps(pi) {
  let ctxWarned = false;

  pi.registerCommand("targets", {
    description: "靶场台账：/targets 看全部 · /targets add <名> <目标> [备注]",
    handler: async (args, ctx) => {
      const a = String(args || "").trim();
      if (a.startsWith("add ")) {
        const rest = a.slice(4).trim();
        const m = rest.match(/^(\S+)\s+(\S+)(?:\s+(.*))?$/);
        if (!m) { ctx.ui.notify("用法：/targets add <名> <目标> [备注]", "warning"); return; }
        const list = readJSON(TARGETS)?.targets || [];
        const item = { name: m[1], target: m[2], note: m[3] || "", ts: new Date().toISOString() };
        const i = list.findIndex((x) => x.name === item.name);
        if (i >= 0) list[i] = item; else list.push(item);
        ctx.ui.notify(saveTargets(list) ? `已登记：${item.name} → ${item.target}` : "写入失败（看 log/）", saveTargets(list) ? "info" : "error");
        return;
      }
      const rows = collectTargets();
      const lines = [`╭─ 靶场台账（${rows.length} 条）`];
      if (!rows.length) lines.push("│ （空 —— 录题台投的卡片会自动进这里，或 /targets add 手工加）");
      for (const r of rows.slice(0, 25)) {
        lines.push(`│ ${String(r.name).slice(0, 22).padEnd(22)} ${String(r.target).slice(0, 44).padEnd(44)} ${r.kind} ${r.src}`);
      }
      lines.push("╰─ /targets add <名> <目标> 登记 · 台账文件 log/ctf-targets.json");
      ctx.ui.setWidget("ctf-targets", lines);
      ctx.ui.notify(`靶场台账 ${rows.length} 条`, "info");
    },
  });

  pi.registerCommand("lanes", {
    description: "在跑的 worker / 活跃黑板一览",
    handler: async (_args, ctx) => {
      const lines = ["╭─ worker / 黑板"];
      try {
        const r = spawnSync("bash", ["-c", `ps -eo pid,etimes,pcpu,args --sort=-etimes | grep -iE "pi-subagents|subagent" | grep -v grep | head -8`], { encoding: "utf8", timeout: 5000 });
        const procs = (r.stdout || "").trim();
        lines.push(procs ? `│ 子代理进程：\n${procs.split("\n").map((l) => "│   " + l.slice(0, 110)).join("\n")}` : "│ 子代理进程：无");
      } catch { lines.push("│ 子代理进程：查询失败"); }
      try {
        const boards = fs.readdirSync(BOARD).map((pid) => {
          const p = path.join(BOARD, pid);
          let mtime = 0;
          try { mtime = fs.statSync(p).mtimeMs; } catch { /* ignore */ }
          const g = readJSON(path.join(p, "graph.json"), 2 * 1024 * 1024) || {};
          const goal = Object.values(g.nodes || {}).find((n) => n && n.type === "goal");
          return { pid, mtime, goal: (goal && goal.text) || "", solved: !!g.solved };
        }).filter((b) => Date.now() - b.mtime < 12 * 3600e3).sort((a, b) => b.mtime - a.mtime);
        lines.push("│ 12h 内活跃黑板：");
        for (const b of boards.slice(0, 8)) {
          const ago = Math.round((Date.now() - b.mtime) / 60000);
          lines.push(`│   ${b.pid.padEnd(20)} ${b.solved ? "✅" : "🎯"} ${ago} 分钟前  ${String(b.goal).slice(0, 50)}`);
        }
        if (!boards.length) lines.push("│   （无）");
      } catch { lines.push("│ 黑板读取失败"); }
      lines.push("╰─ 详细监督：fgs workers <pid> · 拓扑快照：fgs snapshot <pid>");
      ctx.ui.setWidget("ctf-lanes", lines);
      ctx.ui.notify("worker/黑板面板已打开", "info");
    },
  });

  pi.registerCommand("hint", {
    description: "线索请求（OSINT 最弱项专用）：/hint [想问的一句话]",
    handler: async (args, ctx) => {
      const ask = String(args || "").trim();
      let tpl = "";
      try { tpl = fs.readFileSync(HINT_TEMPLATE, "utf8").trim(); } catch { tpl = ""; }
      const msg = [
        "【需要你给一句线索】",
        ask || "这张图/这段信息我判不出语义层：请用一句话告诉我「这是哪里/什么东西」（例如：某大学某楼 / 某市某路口 / 哪种设备的界面）。",
        tpl ? `\n── 模板参考（prompts/human-hint.md）──\n${tpl.slice(0, 800)}` : "",
      ].join("\n");
      const ok = clipboard(msg);
      ctx.ui.notify(ok ? "线索请求已生成并复制到剪贴板（直接粘给我即可）" : "线索请求已生成（剪贴板失败，见下方）", "info");
      pi.sendMessage({ customType: "ctf-hint", content: msg, display: true });
    },
  });

  pi.registerCommand("wp", {
    description: "WP 素材收集：/wp <pid> → 生成 work/<pid>/WP-draft.md",
    handler: async (args, ctx) => {
      const pid = String(args || "").trim();
      if (!pid) { ctx.ui.notify("用法：/wp <pid>", "warning"); return; }
      const r = spawnSync("python3", [WPCOLLECT, pid], { encoding: "utf8", timeout: 30000, cwd: LAB });
      const out = ((r.stdout || "") + (r.stderr || "")).trim();
      ctx.ui.notify(out.slice(0, 400) || "wpcollect 无输出", r.status === 0 ? "info" : "error");
    },
  });

  // 上下文守卫：到 90% 提醒一次
  pi.on("turn_end", async (_e, ctx) => {
    try {
      if (ctxWarned) return;
      const u = typeof ctx?.getContextUsage === "function" ? ctx.getContextUsage() : null;
      const limit = u && (u.limit || u.maxTokens || u.contextWindow);
      if (!u || !limit) return;
      if (u.tokens / limit >= 0.9) {
        ctxWarned = true;
        ctx.ui.notify(`🧠 上下文已 ${Math.round((u.tokens / limit) * 100)}% —— 建议 /compact 或收口这一轮（压缩前把结论写进黑板）`, "warning");
      }
    } catch { /* ignore */ }
  });
}
