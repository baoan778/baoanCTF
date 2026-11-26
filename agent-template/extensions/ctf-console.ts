/**
 * pi-lab: ctf-console —— CTF 值班台（给操作者看的仪表盘）
 *
 * 为什么要它：CTF 是「多题并行 + 长时间跑」的活，操作者最难受的是**看不见状态**：
 *   · 现在在打哪几题？黑板走到哪一步了？（要翻长输出或敲 fgs snapshot）
 *   · 已经命中过哪些 flag 候选？哪些过了平台回执？（散在 log/flag-watch.jsonl）
 *   · 这轮烧了多久、上下文占了百分之几？
 * 这个扩展把上面三样压成**常驻一行状态** + **一块可随时展开的面板**。
 *
 * 用法：
 *   /ctf            展开/收起「值班台」面板（常驻编辑器上方，纯只读）
 *   /flags          列全部 flag 候选（含 /ctf 里放不下的）
 *   /flags copy <n> 把第 n 条候选放进 **Windows 剪贴板**（走 interop，直接粘到平台）
 *
 * 设计纪律（对齐 AGENTS.md 的工程要求）：
 *   · 只读：不写黑板、不改会话，唯一写入是剪切板用的临时文件（log/.ctf-console-clip.txt）
 *   · 所有文件读取都过 size 上限（readCapped），坏文件/巨大文件一律跳过
 *   · 全程 try/catch —— 宁可自己静默失效，也绝不拖累 pi 跑工具
 *   · 不 import pi 包（保持零依赖，方便用 node --experimental-strip-types 单测）
 */

import * as fs from "node:fs";
import * as path from "node:path";
import { spawnSync } from "node:child_process";

const LAB = process.env.PI_LAB_ROOT || process.cwd();
const BOARD_DIR = path.join(LAB, "board", "projects");
const FLAG_LOG = path.join(LAB, "log", "flag-watch.jsonl");
const FLAG_STATUS = path.join(LAB, "log", "flag-status.json");
const CLIP_TMP = path.join(LAB, "log", ".ctf-console-clip.txt");

const MAX_GRAPH = 2 * 1024 * 1024; // graph.json 上限 2MB
const MAX_FLAGLOG = 2 * 1024 * 1024; // flag-watch.jsonl 上限 2MB（超出只读尾部）
const MAX_BOARDS = 40;
const ACTIVE_HOURS = 72; // 多久没动就归到「归档」

// ── 小工具 ────────────────────────────────────────────────────────────────

/** 有上限的文件读取：非普通文件 / 超限 / 读失败 → null（对齐 read_capped 纪律） */
function readCapped(p, cap = MAX_GRAPH) {
  try {
    const st = fs.statSync(p);
    if (!st.isFile() || st.size > cap) return null;
    return fs.readFileSync(p, "utf8");
  } catch {
    return null;
  }
}

/** 读文件尾部 cap 字节（日志类用，防超大文件） */
function readTail(p, cap) {
  try {
    const st = fs.statSync(p);
    if (!st.isFile()) return null;
    if (st.size <= cap) return fs.readFileSync(p, "utf8");
    const fd = fs.openSync(p, "r");
    try {
      const buf = Buffer.alloc(cap);
      fs.readSync(fd, buf, 0, cap, st.size - cap);
      return buf.toString("utf8");
    } finally {
      fs.closeSync(fd);
    }
  } catch {
    return null;
  }
}

/** WSL 路径 → Windows 路径（给 powershell/clip 用），拿不到就 null */
function winPath(p) {
  const m = /^\/mnt\/([a-z])\/(.*)$/.exec(p);
  if (!m) return null;
  return `${m[1].toUpperCase()}:\\${m[2].replace(/\//g, "\\")}`;
}

function fmtAgo(ms) {
  if (!ms || ms < 0) return "?";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h${m % 60 ? (m % 60) + "m" : ""}`;
  return `${Math.floor(h / 24)}d`;
}

function fmtTs(iso) {
  try {
    const d = new Date(iso);
    const p = (n) => String(n).padStart(2, "0");
    return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  } catch {
    return "?";
  }
}

/** 中英混排的宽度（中文算 2） */
function wide(s) {
  let w = 0;
  for (const ch of s) w += /[\u1100-\uFFE6]/.test(ch) ? 2 : 1;
  return w;
}

function padTo(s, w) {
  const d = w - wide(s);
  return d > 0 ? s + " ".repeat(d) : s;
}

function clip(s, w) {
  if (wide(s) <= w) return s;
  let out = "";
  for (const ch of s) {
    if (wide(out + ch + "…") > w) break;
    out += ch;
  }
  return out + "…";
}

// ── 数据源（全部只读、全部有上限）────────────────────────────────────────

/** 读黑板：每题一行 {pid, goal, kind, solved, confirmed, facts, verified, updatedAt, mtimeMs} */
function loadBoards() {
  const out = [];
  let names = [];
  try {
    names = fs.readdirSync(BOARD_DIR).slice(0, MAX_BOARDS);
  } catch {
    return out;
  }
  for (const pid of names) {
    const p = path.join(BOARD_DIR, pid, "graph.json");
    if (!pid || pid.startsWith(".")) continue;
    let d = null;
    const txt = readCapped(p);
    if (txt !== null) {
      try {
        d = JSON.parse(txt);
      } catch {
        d = null;
      }
    }
    let mtimeMs = 0;
    try {
      mtimeMs = fs.statSync(path.join(BOARD_DIR, pid)).mtimeMs;
    } catch { /* ignore */ }

    const nodes = d && d.nodes ? Object.values(d.nodes) : [];
    const goalNode = nodes.find((n) => n && n.type === "goal" && n.status !== "abandoned");
    const facts = nodes.filter((n) => n && n.type === "fact");
    out.push({
      pid,
      goal: (goalNode && goalNode.text) || "(无目标节点)",
      kind: (d && d.kind) || "?",
      solved: !!(d && d.solved),
      confirmed: !!(d && d.confirmed),
      facts: facts.length,
      verified: facts.filter((f) => f.status === "verified").length,
      updatedAt: (d && d.updated_at) || "",
      mtimeMs,
    });
  }
  out.sort((a, b) => b.mtimeMs - a.mtimeMs);
  return out;
}

/** 读 flag 候选：去重、带最近时间与次数 */
function loadFlags() {
  const txt = readTail(FLAG_LOG, MAX_FLAGLOG);
  if (!txt) return [];
  const byFlag = new Map();
  for (const line of txt.split("\n")) {
    const s = line.trim();
    if (!s || s[0] !== "{") continue;
    let rec = null;
    try {
      rec = JSON.parse(s);
    } catch {
      continue;
    }
    for (const h of (rec && rec.hits) || []) {
      if (!h || !h.flag) continue;
      const cur = byFlag.get(h.flag) || { flag: h.flag, kind: h.kind || "?", n: 0, first: rec.ts, last: rec.ts, tool: rec.tool || "?" };
      cur.n += 1;
      cur.last = rec.ts || cur.last;
      cur.tool = rec.tool || cur.tool;
      byFlag.set(h.flag, cur);
    }
  }
  return [...byFlag.values()].sort((a, b) => String(b.last).localeCompare(String(a.last)));
}

/** 疑似自测/示例 flag（别当真，免得误导提交） */
function isSelfTest(flag) {
  return /dry[-_]?run|example|test|dummy|placeholder|sample|fake/i.test(flag);
}

/** 把候选与黑板对上：某题 graph.json 里逐字出现过 → 标出题号 */
function annotateWithBoards(flags, boards) {
  const texts = [];
  for (const b of boards.slice(0, MAX_BOARDS)) {
    const txt = readCapped(path.join(BOARD_DIR, b.pid, "graph.json"));
    if (txt) texts.push([b, txt]);
  }
  for (const f of flags) {
    for (const [b, txt] of texts) {
      if (txt.includes(f.flag)) {
        f.board = b.pid;
        f.boardSolved = b.solved;
        f.boardConfirmed = b.confirmed;
        break;
      }
    }
  }
  return flags;
}

/** 会话信息：耗时 / 上下文占用 */
function sessionInfo(ctx, startedMs) {
  const info = { elapsed: fmtAgo(Date.now() - startedMs), ctxPct: null, ctxText: "" };
  try {
    const u = ctx && typeof ctx.getContextUsage === "function" ? ctx.getContextUsage() : null;
    if (u && u.tokens) {
      const limit = u.limit || u.maxTokens || u.contextWindow || 0;
      info.ctxText = limit ? `${(u.tokens / 1000).toFixed(1)}k/${(limit / 1000).toFixed(0)}k` : `${(u.tokens / 1000).toFixed(1)}k`;
      if (limit) info.ctxPct = Math.round((u.tokens / limit) * 100);
    }
  } catch { /* ignore */ }
  return info;
}

// ── 渲染 ─────────────────────────────────────────────────────────────────

function buildCardLines(ctx, startedMs) {
  const boards = loadBoards();
  const flags = annotateWithBoards(loadFlags(), boards);
  const si = sessionInfo(ctx, startedMs);
  const now = Date.now();
  const active = boards.filter((b) => !b.solved && now - b.mtimeMs < ACTIVE_HOURS * 3600e3);
  const done = boards.filter((b) => b.solved || (now - b.mtimeMs >= ACTIVE_HOURS * 3600e3));
  const W = 96;
  const rule = (t) => {
    const head = t ? `─ ${t} ` : "";
    return head + "─".repeat(Math.max(0, W - wide(head) - 2));
  };
  const lines = [];
  lines.push(`╭─ CTF 值班台 ${rule("")}`.slice(0, W + 2));
  lines.push(`│ 会话 ${si.elapsed}${si.ctxPct !== null ? ` · 上下文 ${si.ctxPct}% (${si.ctxText})` : ""} · 黑板 ${boards.length} 张（在打 ${active.length} / 归档 ${done.length}）`);
  lines.push(`├${rule("在打")}`);
  if (!active.length) lines.push("│ （没有 72h 内活跃的题 —— 新题第一动作：fgs kickoff <pid> --kind ctf --goal …）");
  for (const b of active.slice(0, 8)) {
    const fc = flags.filter((f) => f.board === b.pid).length;
    lines.push(
      `│ 🎯 ${padTo(clip(b.pid, 22), 22)} 事实 ✔${b.verified}/${b.facts}  候选 ${fc}   ${fmtTs(b.updatedAt)}`
    );
    lines.push(`│    ${clip(b.goal.replace(/\s+/g, " "), W - 8)}`);
  }
  lines.push(`├${rule("已收官 / 归档")}`);
  for (const b of done.slice(0, 6)) {
    const mark = b.confirmed ? "✅ 已确认" : b.solved ? "☑️ 已解未确认" : "· 无结论";
    lines.push(`│ ${padTo(clip(b.pid, 22), 22)} ${padTo(mark, 12)} ${fmtTs(b.updatedAt)}`);
  }
  if (done.length > 6) lines.push(`│ … 另有 ${done.length - 6} 张（/ctf 面板已截断，看 board/projects/）`);
  lines.push(`├${rule("flag 候选（最近 8 条）")}`);
  if (!flags.length) lines.push("│ （还没有命中 —— flag-watch 扫到会立刻插进工具结果）");
  flags.slice(0, 8).forEach((f, i) => {
    const tag = f.boardConfirmed ? "✅已确认" : f.boardSolved ? "☑️已解" : isSelfTest(f.flag) ? "⚠️疑似自测" : "";
    const at = f.board ? `@${f.board}` : "";
    lines.push(`│ ${i + 1}. ${clip(f.flag, 52)} ${padTo(tag, 10)} ${at} ${fmtTs(f.last)}`);
  });
  lines.push(`╰─ 全部候选/复制：/flags ｜ /flags copy <n> → Windows 剪贴板`);
  return lines;
}

// buildStatusLine 已删（2026-10-10 操作者：常驻状态栏没用）

/** 写 Windows 剪贴板（powershell interop）；失败返回错误串 */
function copyToClipboard(text) {
  try {
    fs.writeFileSync(CLIP_TMP, text);
  } catch (e) {
    return `写临时文件失败：${e && e.message}`;
  }
  const wp = winPath(CLIP_TMP);
  if (!wp) return "拿不到 Windows 路径（LAB 不在 /mnt/<盘>/ 下？）→ 已把内容打在下面";
  const r = spawnSync(
    "powershell.exe",
    ["-NoProfile", "-Command", `Set-Clipboard -Value (Get-Content -LiteralPath '${wp}' -Raw)`],
    { timeout: 10000 }
  );
  if (r.error) return `powershell 调用失败：${r.error.message}`;
  if (r.status !== 0) return `powershell 退出码 ${r.status}`;
  return null;
}

// ── 扩展主体 ─────────────────────────────────────────────────────────────

/** 把 mark 状态并回候选（供列表显示）；文件很小，直接读 */
function loadFlagStatus() {
  try {
    const d = JSON.parse(fs.readFileSync(FLAG_STATUS, "utf8"));
    return d && typeof d === "object" ? d : {};
  } catch { return {}; }
}

/** 记录 flag 的回执状态（人确认后才算数 —— 机器不得自行认定） */
function markFlag(flag, status) {
  try {
    const d = loadFlagStatus();
    d[flag] = { status, at: new Date().toISOString() };
    fs.mkdirSync(path.dirname(FLAG_STATUS), { recursive: true });
    fs.writeFileSync(FLAG_STATUS, JSON.stringify(d, null, 2), "utf8");
    return true;
  } catch { return false; }
}

/** 在当前会话 JSONL 里逐字找这个字符串（流式读，有行数/字节上限） */
function locateInSession(ctx, needle) {
  const out = [];
  try {
    const file = ctx?.sessionManager?.getSessionFile?.();
    if (!file) return out;
    const st = fs.statSync(file);
    if (!st.isFile() || st.size > 128 * 1024 * 1024) return out;   // 超大会话不扫（防接口热点把 VM 读爆）
    const raw = fs.readFileSync(file, "utf8");
    const lines = raw.split("\n");
    for (let i = 0; i < lines.length && out.length < 12; i++) {
      const L = lines[i];
      if (!L.includes(needle)) continue;
      let type = "?", tool = "";
      try {
        const j = JSON.parse(L);
        type = j.type || j.role || "?";
        tool = j.toolName || j.name || j?.message?.toolName || "";
        if (j.message?.role) type = j.message.role;
      } catch { /* 非 JSON 行也算命中 */ }
      const idx = L.indexOf(needle);
      out.push({ line: i + 1, type, tool, snippet: L.slice(Math.max(0, idx - 40), idx + needle.length + 20) });
    }
  } catch { /* ignore */ }
  return out;
}

export default function ctfConsole(pi) {
  const startedMs = Date.now();
  let panelOn = false;

  // 2026-10-10 操作者：常驻状态栏与启动提示都去掉；用法靠 /ctf 与 /flags 两条命令

  pi.registerCommand("ctf", {
    description: "CTF 值班台：在打哪几题 / 黑板进度 / flag 候选 / 会话开销（再敲一次收起）",
    handler: async (_args, ctx) => {
      try {
        if (panelOn) {
          ctx.ui.setWidget("ctf-console", undefined);
          panelOn = false;
          ctx.ui.notify("值班台已收起", "info");
          return;
        }
        const lines = buildCardLines(ctx, startedMs);
        ctx.ui.setWidget("ctf-console", lines);
        panelOn = true;
        // 面板是 widget（只在 TUI 显示），同时把摘要打给模型/日志一份，方便事后复盘
        try {
          pi.appendEntry("ctf-console", { card: lines.join("\n"), at: new Date().toISOString() });
        } catch { /* ignore */ }
      } catch (e) {
        ctx.ui.notify(`值班台出错：${e && e.message}`, "error");
      }
    },
  });

  pi.registerCommand("flags", {
    description: "flag 候选表（/flags 看表 · copy <n> 进剪贴板 · src <n> 逐字溯源 · mark <n> submitted|confirmed）",
    getArgumentCompletions: (prefix) => {
      const items = ["copy", "src", "mark"].map((v) => ({ value: v, label: v }));
      return items.filter((i) => i.value.startsWith(prefix)).length ? items : null;
    },
    handler: async (args, ctx) => {
      try {
      const flags = annotateWithBoards(loadFlags(), loadBoards());
      const status = loadFlagStatus();
      for (const f of flags) {
        const s = status[f.flag];
        f.submitted = !!(s && (s.status === "submitted" || s.status === "confirmed"));
        f.confirmed = !!(s && s.status === "confirmed");
      }
      const a = String(args || "").trim();

        if (a === "") {
          // 落到下面默认分支（列表）
        } else if (a.startsWith("copy")) {
          const n = parseInt(a.replace(/^copy\s*/, ""), 10);
          if (!Number.isInteger(n) || n < 1 || n > flags.length) {
            ctx.ui.notify(`用法：/flags copy <序号 1..${flags.length}>`, "warning");
            return;
          }
          const f = flags[n - 1];
          const err = copyToClipboard(f.flag);
          if (err) ctx.ui.notify(`复制失败：${err}\n${f.flag}`, "error");
          else ctx.ui.notify(`已复制到 Windows 剪贴板：${f.flag}`, "info");
          return;
        } else if (a.startsWith("src")) {
          // 逐字溯源：在**当前会话文件**里找这个字符串，看它到底从哪条工具输出里来的
          const n = parseInt(a.replace(/^src\s*/, ""), 10);
          if (!Number.isInteger(n) || n < 1 || n > flags.length) {
            ctx.ui.notify(`用法：/flags src <序号 1..${flags.length}>`, "warning");
            return;
          }
          const flag = flags[n - 1].flag;
          const hits = locateInSession(ctx, flag);
          if (!hits.length) {
            ctx.ui.notify(`未在当前会话文件里找到 ${flag}（可能是别的会话/手动敲的）`, "warning");
            return;
          }
          const lines = [`╭─ 逐字溯源：${flag}`];
          for (const h of hits.slice(0, 6)) {
            lines.push(`│ 第 ${h.line} 行 · ${h.type}${h.tool ? ` · tool=${h.tool}` : ""}`);
            lines.push(`│   ${clip(h.snippet, 96)}`);
          }
          if (hits.length > 6) lines.push(`│ …另有 ${hits.length - 6} 处`);
          lines.push(`╰─ 下一步：\`fgs verify <pid> <fact> --from <原始输出>\`（逐字确认后才算证据）`);
          ctx.ui.setWidget("ctf-console", lines);
          panelOn = true;
          ctx.ui.notify(`找到 ${hits.length} 处（见面板）`, "info");
          return;
        } else if (a.startsWith("mark")) {
          const m = a.match(/^mark\s+(\d+)\s+(submitted|confirmed)$/);
          if (!m) {
            ctx.ui.notify(`用法：/flags mark <序号> submitted|confirmed`, "warning");
            return;
          }
          const n = parseInt(m[1], 10);
          if (!Number.isInteger(n) || n < 1 || n > flags.length) {
            ctx.ui.notify(`序号超范围（1..${flags.length}）`, "warning");
            return;
          }
          const flag = flags[n - 1].flag;
          const ok = markFlag(flag, m[2]);
          ctx.ui.notify(ok ? `已标记 ${flag} → ${m[2] === "confirmed" ? "✅ 平台已确认" : "📤 已提交"}` : "标记写入失败（看 log/）", ok ? "info" : "error");
          return;
        }

        const lines = [`╭─ flag 候选（${flags.length} 条，最近在上）`];
        if (!flags.length) lines.push("│ （空 —— flag-watch 命中会记 log/flag-watch.jsonl）");
        flags.forEach((f, i) => {
          const tag = f.boardConfirmed ? "✅已确认" : f.boardSolved ? "☑️已解" : f.confirmed ? "✅平台已确认" : f.submitted ? "📤已提交" : isSelfTest(f.flag) ? "⚠️疑似自测" : "";
          lines.push(`│ ${String(i + 1).padStart(2)}. ${clip(f.flag, 56)} ${padTo(tag, 10)} ${f.board ? "@" + f.board : ""} ${fmtTs(f.last)} ×${f.n}`);
        });
        lines.push("╰─ copy <n> 进剪贴板 · src <n> 逐字溯源 · mark <n> submitted|confirmed");
        ctx.ui.setWidget("ctf-console", lines);
        panelOn = true;
        ctx.ui.notify(`flag 候选 ${flags.length} 条（见面板；/flags copy N 复制）`, "info");
      } catch (e) {
        ctx.ui.notify(`flags 出错：${e && e.message}`, "error");
      }
    },
  });
}
