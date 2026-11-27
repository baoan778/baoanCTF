/**
 * pi-lab: 思考时间 + 生成速度时钟（think-clock）
 * ==================================================================
 * 输入框（编辑器）的**两条横线**各管一件事：
 *
 *   ┌─ ⠸ Working… ───────────────────────────────────────────┐   ← 第一条横线
 *   │ > 你在这里输入……                                        │      pi 原生 Working 转圈
 *   └─ ⏱ 思考 1:23 · ⚡ 42 t/s ── 170 轮 · ↑ 162k ─┘   ← 第二条横线（默认 compact）
 *                                                                        （本插件画的会话仪表盘）
 *
 * 样式：`/time style compact|full|off`（落 <agentDir>/think-clock.json）
 *   compact（默认，2026-10-10 用户要求精简）：⏱ 思考 + ⚡ 速度 | 轮数 + ↑ token
 *   full   （老版全量）：思考 · 工具 · 速度 | 轮 · token · 墙 · 北京
 *   off    ：不画这条线（Working 嵌框保留）
 *
 * 工作线（第一条横线）右侧：MCP 指示器 `🔌N`（N = 启用的 server 数）。
 *   适配器原本那行「🔌 MCP: 1 server enabled」已用 <agentDir>/mcp-adapter.json
 *   的 settings.mcpFooterStatus="off" 关掉（2026-10-10 用户要求）。
 *
 * ⚠️ **第一条横线为什么以前是空的（踩过的坑，别改回去）**
 * pi 的 `CustomEditor` 有 `embedWorkingStatus` 选项，只有它为 `true` 时，
 * `isWorkingStatusEditor()` 才认这个编辑器，pi 才会把 `⠸ Working…` **嵌进顶部边框**；
 * 否则 Working 会退化成一个**独立行**（挂在编辑器上方）。
 * 本插件用 `setEditorComponent` 替换了编辑器，早期只在 super() 里传了 `paddingX: 0`
 * → `embedWorkingStatus` 默认 false → **Working 就被挤出边框了**。
 * 修法：super() 里显式传 `embedWorkingStatus: true`（见 installClockEditor）。
 *
 * 第二条横线的字段可增减：改 `FOOTER_LAYOUTS`（左/右两段各列字段名）。
 *
 * ── 口径说明（重要，别改错）──────────────────────────────────────
 * 实测事件时间线（本机 opencode-go provider）：
 *
 *   turn_start 18.830s          ← LLM 请求从这里开始
 *   message_start(assistant) 21.143s   ← 中间 2.3s 是网络+prefill+思考
 *   message_end(assistant)   22.397s   ← 流式只有 1.25s
 *   tool_call → tool_result             ← 工具在这个 turn 内部
 *   turn_end                 22.427s
 *
 * 由此两个结论：
 *   1) **不能**用 message_start→message_end 当模型时间 —— 只量到 200ms，
 *      真正的延迟在 turn_start→message_start 那一段。正确口径是：
 *          模型时间 = Σ(turn 时长) − 工具时间
 *   2) **不能**用流式区间当解码速度 —— 实测出现「203ms 流完 62 token」= 305 tok/s，
 *      这是 provider 把流缓冲后一次性灌进来的假象。所以用**有效速度**：
 *          有效 tok/s = output tokens ÷ 模型时间
 *      它包含 prefill/网络，也就是你实际等掉的时间 —— 诚实且稳定。
 *
 * ── 四个时钟 ─────────────────────────────────────────────────────
 *   model  模型时间（= Σturn − 工具）
 *   tool   工具执行（tool_call → tool_result 的**并集**，并行不重复计）
 *   wait   等你输入（ui_prompt_start → ui_prompt_end）
 *   other  墙钟 − 上面三个
 *
 * ── 存盘模型 ──────────────────────────────────────────────────────
 * **按会话存原始值，总数派生**（不存冗余的 total 计数器）。三个好处：
 *   1. `persist()` 幂等 —— 随时写不会算重
 *   2. `/reload` 与 `--resume` 命中同一条会话记录 → **接着上次算，不清零**
 *   3. 每 30s 自动落一次盘 → 崩溃/被强杀/直接关窗口，最多丢最后 30s
 *
 * 踩过的坑：旧版在 session_shutdown 时才把值累加进 `total`，而
 * `/reload` **不触发** session_shutdown → 重载前的累积直接丢，新实例还会用同一个
 * session key 把旧记录覆盖成 0。现在改成派生总数 + 续算，这两个问题都不存在了。
 *
 * ── 命令 ─────────────────────────────────────────────────────────
 *   /time           本次会话逐轮明细 + 累计
 *   /time top       工具耗时排行
 *   /time style S   底部横线样式：compact（默认）/ full / off
 *   /time reset     清掉累计
 *
 * 落盘：<PI_CODING_AGENT_DIR>/time-stats.json（跨会话累计）
 */

import { CustomEditor } from "@earendil-works/pi-coding-agent";
import { truncateToWidth, visibleWidth } from "@earendil-works/pi-tui";
import * as fs from "node:fs";
import * as path from "node:path";

const STATUS_KEY = "think-clock";
const TICK_MS = 1000;
const FLUSH_EVERY_TICKS = 30;   // 每 30s 落一次盘 —— 崩溃/被强杀不丢数据
const MAX_TURNS_KEPT = 60;   // 会话内逐轮明细保留条数

// ── 边框排版：左/右两段 + 中间 ─ 填满；宽度不够先砍右再砍左 ────────────
// 中文字符是宽字符，一律走 visibleWidth/truncateToWidth，别自己数 length
function fitBorder(left, right, width, border, fill = border) {
  if (width <= 0) return "";
  if (width === 1) return border("─");

  let l = left, r = right;
  const FIXED = 2, MIN_GAP = 3;

  while (FIXED + visibleWidth(l) + visibleWidth(r) + MIN_GAP > width && visibleWidth(r) > 0) {
    r = truncateToWidth(r, Math.max(0, visibleWidth(r) - 1), "");
  }
  while (FIXED + visibleWidth(l) + visibleWidth(r) + MIN_GAP > width && visibleWidth(l) > 0) {
    l = truncateToWidth(l, Math.max(0, visibleWidth(l) - 1), "");
  }
  const gap = Math.max(0, width - FIXED - visibleWidth(l) - visibleWidth(r));
  return `${border("─")}${l}${fill("─".repeat(gap))}${r}${border("─")}`;
}

// ── 底部横线样式表（左/右两段各列字段名）───────────────────────────
// 2026-10-10 用户：默认那条太长了 → 默认改 compact；full 保留老版；off 彻底不画。
export const FOOTER_LAYOUTS = {
  compact: { left: ["think", "tps"], right: ["turns", "tokens"] },
  full: {
    left: ["think", "tool", "tps"],
    right: ["turns", "tokens", "wall", "beijing"],
  },
  off: { left: [], right: [] },
};

export function layoutFor(style) {
  return FOOTER_LAYOUTS[style] || FOOTER_LAYOUTS.compact;
}

/** 把字段拼成左右两段纯文本（不带上色）——渲染与单测共用同一套逻辑 */
export function composeFooter(style, fields, sep = " · ") {
  const L = layoutFor(style);
  const j = (keys) => keys.map((k) => fields[k] || "").filter(Boolean).join(sep);
  return { left: j(L.left), right: j(L.right) };
}

/** 在已画好的横线右侧贴一个小标记（宽度安全，超 ANSI 安全）；贴不下就原样返回 */
export function spliceRight(line, chipText, width) {
  const cw = visibleWidth(chipText);
  if (!chipText || width <= cw + 8) return line;
  const head = truncateToWidth(line, width - cw - 1, "");
  const headW = visibleWidth(head);
  return head + " ".repeat(Math.max(1, width - headW - cw)) + chipText;
}

// ── MCP 服务器计数（工作线右侧那个小标记用）───────────────────────
// 读的是 pi-mcp-adapter 同一批配置文件：项目 .mcp.json / 项目 .pi/mcp.json /
// <agentDir>/mcp.json / 用户级标准位置。同名后读的覆盖先读的。
// 只数「没被 disabled 的」；一张表都没有 → null（不显示标记）。
// 10s 缓存：这函数在渲染路径上每帧都会被调，绝不能每帧读盘。
const MCP_CACHE = { at: 0, n: null };
export function mcpEnabledCount(agentDir) {
  const now = Date.now();
  if (now - MCP_CACHE.at < 10_000) return MCP_CACHE.n;
  const home = process.env.HOME || "/root";
  const paths = [
    path.join(process.cwd(), ".mcp.json"),
    path.join(process.cwd(), ".pi", "mcp.json"),
    path.join(agentDir, "mcp.json"),
    path.join(home, ".config", "mcp", "mcp.json"),
    path.join(home, ".agents", "mcp.json"),
  ];
  const servers = new Map();
  for (const p of paths) {
    try {
      const st = fs.statSync(p);
      if (!st.isFile() || st.size > 512 * 1024) continue;
      const j = JSON.parse(fs.readFileSync(p, "utf8"));
      const m = j && typeof j === "object" ? j.mcpServers || j["mcp-servers"] : null;
      if (m && typeof m === "object") for (const [k, v] of Object.entries(m)) servers.set(k, v);
    } catch { /* 不存在 / 坏了 → 跳过 */ }
  }
  const n = servers.size ? [...servers.values()].filter((v) => !(v && v.disabled === true)).length : null;
  MCP_CACHE.at = now;
  MCP_CACHE.n = n;
  return n;
}

export default function (pi) {
  const agentDir =
    process.env.PI_CODING_AGENT_DIR || path.join(process.cwd(), ".pi", "agent");
  const statsFile = path.join(agentDir, "time-stats.json");
  const styleFile = path.join(agentDir, "think-clock.json");

  // ── 底部横线样式（默认 compact）─────────────────────────────────
  const loadStyle = () => {
    try {
      const j = JSON.parse(fs.readFileSync(styleFile, "utf8"));
      if (j && FOOTER_LAYOUTS[j.style]) return j.style;
    } catch { /* 没配过 → 默认 */ }
    return "compact";
  };
  const saveStyle = (s) => {
    try {
      fs.mkdirSync(agentDir, { recursive: true });
      fs.writeFileSync(styleFile, JSON.stringify({ style: s }, null, 2));
    } catch { /* 写不了就只在内存里切 */ }
  };
  let footerStyle = loadStyle();

  // ── 会话内状态 ────────────────────────────────────────────────────
  let S = null;
  let timer = null;
  let lastCtx = null;
  let activeTui = null;
  let editorInstalled = false;
  let tickCount = 0;

  // 当前 turn 的开放状态
  let turnOpenAt = null;         // turn_start 时刻
  let turnToolIv = [];           // 本 turn 的工具区间（算本 turn 的模型时间）
  let pendingOutput = 0;         // 本 turn assistant 的 output tokens

  // 全局开放状态
  let waitOpenAt = null;
  const toolOpen = new Map();    // toolCallId -> { at, name }
  let toolIntervals = [];        // 全部已完成的工具区间

  function blankSession(sessionFile) {
    return {
      startedAt: Date.now(), firstPromptAt: null, sessionFile: sessionFile || null,
      turnMs: 0,          // Σ turn 时长（= 模型 + 工具）
      toolMs: 0,          // 工具并集（全局）
      waitMs: 0,          // 等人输入
      turns: 0, toolCalls: 0,
      outTokens: 0,       // 累计输出 token
      toolMsByName: {},   // 本会话工具耗时（按工具名）—— 存在会话里，总数派生
      turnLog: [],        // 逐轮明细
      lastTps: null,      // 最近一轮的有效 tok/s
      lastTokens: 0,
      resumed: false,     // 是否从已有记录续算（/reload 或 --resume）
    };
  }

  // ── 时长 ──────────────────────────────────────────────────────────
  const wallMs = () => (S ? Date.now() - S.startedAt : 0);

  /** 区间并集：并行工具调用累加会得出 > 墙钟的荒唐数字 */
  function unionMs(intervals) {
    if (!intervals.length) return 0;
    const xs = intervals.slice().sort((a, b) => a.s - b.s);
    let total = 0, cs = xs[0].s, ce = xs[0].e;
    for (let i = 1; i < xs.length; i++) {
      if (xs[i].s <= ce) ce = Math.max(ce, xs[i].e);
      else { total += ce - cs; cs = xs[i].s; ce = xs[i].e; }
    }
    return total + (ce - cs);
  }

  function toolMsLive() {
    const now = Date.now();
    const ivs = toolIntervals.slice();
    for (const [, o] of toolOpen) ivs.push({ s: o.at, e: now });
    return unionMs(ivs);
  }

  /** Σturn（含正在跑的这个 turn） */
  function turnMsLive() {
    return (S ? S.turnMs : 0) + (turnOpenAt ? Date.now() - turnOpenAt : 0);
  }

  /** ★ 模型时间 = Σturn − 工具（见文件头口径说明） */
  function modelMsLive() {
    return Math.max(0, turnMsLive() - toolMsLive());
  }

  const waitMsLive = () => (S ? S.waitMs + (waitOpenAt ? Date.now() - waitOpenAt : 0) : 0);

  /** 有效性检查：会话平均 tok/s */
  function avgTps() {
    if (!S || !S.outTokens) return null;
    const m = modelMsLive();
    return m > 0 ? S.outTokens / (m / 1000) : null;
  }

  function fmt(ms) {
    if (!ms || ms < 0) ms = 0;
    const s = Math.floor(ms / 1000), h = Math.floor(s / 3600),
          m = Math.floor((s % 3600) / 60), ss = s % 60;
    const p = (n) => String(n).padStart(2, "0");
    return h > 0 ? `${h}:${p(m)}:${p(ss)}` : `${m}:${p(ss)}`;
  }

  function human(ms) {
    if (!ms || ms < 0) ms = 0;
    const s = Math.floor(ms / 1000), h = Math.floor(s / 3600),
          m = Math.floor((s % 3600) / 60), ss = s % 60;
    const parts = [];
    if (h) parts.push(`${h}h`);
    if (h || m) parts.push(`${m}m`);
    parts.push(`${ss}s`);
    return parts.join(" ");
  }

  const n1 = (x) => (x == null ? "—" : x >= 100 ? String(Math.round(x)) : x.toFixed(1));

  /** token 数人性化：<1000 原样，1.2k / 12.4k / 1.05M */
  const tok = (n) => {
    n = Number(n) || 0;
    if (n < 1000) return String(n);
    if (n < 10000) return (n / 1000).toFixed(1).replace(/\.0$/, "") + "k";
    if (n < 1000000) return Math.round(n / 1000) + "k";
    return (n / 1000000).toFixed(2) + "M";
  };

  /**
   * 北京时间（UTC+8）。
   * **显式加 8h 再用 getUTC\* 读**，不依赖机器时区 —— 换到别的 TZ 也不会算错，
   * 也不用担心夏令时（中国不用）。
   */
  function bjTime(withSeconds = true) {
    const d = new Date(Date.now() + 8 * 3600 * 1000);
    const p = (n) => String(n).padStart(2, "0");
    const hm = `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
    return withSeconds ? `${hm}:${p(d.getUTCSeconds())}` : hm;
  }

  function bar(part, whole, width = 16) {
    if (!whole) return "░".repeat(width);
    const n = Math.max(0, Math.min(width, Math.round((part / whole) * width)));
    return "█".repeat(n) + "░".repeat(width - n);
  }

  function tick() {
    if (!S) return;
    // 周期性落盘：崩溃 / 被强杀 / 直接关窗口，最多丢最后 FLUSH_EVERY_TICKS 秒
    if (++tickCount % FLUSH_EVERY_TICKS === 0) persist();
    if (editorInstalled && activeTui) {
      try { activeTui.requestRender(); } catch { /* TUI 没了 */ }
      return;
    }
    if (lastCtx?.hasUI) {           // 非 TUI 兜底
      try {
        lastCtx.ui.setStatus(STATUS_KEY,
          `⏱ 思考 ${fmt(modelMsLive())} · 工具 ${fmt(toolMsLive())} · ⚡ ${n1(avgTps())} tok/s`);
      } catch { /* 忽略 */ }
    }
  }

  // ── 跨会话累计 ────────────────────────────────────────────────────
  // 模型：**按会话存原始值，总数派生**。好处：
  //   1. persist() 幂等 —— 随时写不会算重
  //   2. /reload 或 --resume 能接着上次继续算，而不是清零
  //      （踩过：旧版在 commit 时就累加到 total，重载后新实例用同一 key 覆盖，
  //        而且 reload 不走 session_shutdown，旧数据直接丢）
  //   3. 崩溃/被强杀最多丢最后 30s
  const ZERO = () => ({ turnMs: 0, toolMs: 0, waitMs: 0, wallMs: 0, turns: 0, toolCalls: 0, outTokens: 0, sessions: 0 });
  const T_FIELDS = ["turnMs", "toolMs", "waitMs", "wallMs", "turns", "toolCalls", "outTokens"];

  function blankStats() {
    return { retired: ZERO(), sessions: {} };
  }

  function sumSessions(sessions) {
    const s = ZERO();
    for (const x of Object.values(sessions || {})) {
      for (const f of T_FIELDS) s[f] += x[f] || 0;
    }
    s.sessions = Object.keys(sessions || {}).length;
    return s;
  }

  /**
   * 迁移旧模型（v1）。
   *
   * 旧版把累计直接写在顶层 `total` 里（每次 shutdown 累加），而 session 记录**没有
   * per-tool 明细**。新模型是「总数 = 会话之和 + retired」，所以：
   *
   *   retired 应补的 = 旧 total − 现有会话之和   ← 多出来的就是**已被裁掉的历史**
   *
   * 关键：按**差额**补，而不是「retired 不存在才搬」。
   * 踩过：旧版写过一次 retired:{} 之后，`!j.retired` 就不成立，迁移直接跳过，
   * 紧接着又被清理函数删掉 total —— 幸好那次没裁剪过所以无损，但不能靠运气。
   * 而且旧 total 里可能含着已被裁剪的会话，直接整体搬进 retired 会双重计数。
   */
  function migrateLegacy(j) {
    if (!j.total) return false;
    const sum = sumSessions(j.sessions);
    const r = j.retired || {};
    for (const f of T_FIELDS) {
      r[f] = (r[f] || 0) + Math.max(0, (j.total[f] || 0) - (sum[f] || 0));
    }
    r.sessions = (r.sessions || 0) + Math.max(0, (j.total.sessions || 0) - sum.sessions);

    // 工具的 per-session 明细旧版根本没存，无法拆分 →
    // 只有当会话里完全没有任何工具明细时，才把旧的全局表整体搬进 retired（否则会计重）
    const hasSessionToolData = Object.values(j.sessions || {})
      .some((s) => s.toolMsByName && Object.keys(s.toolMsByName).length);
    if (j.toolMsByName && !hasSessionToolData) {
      r.toolMsByName = { ...(r.toolMsByName || {}), ...j.toolMsByName };
    }

    j.retired = r;
    delete j.total;
    delete j.toolMsByName;
    return true;
  }

  function loadStats() {
    try {
      const j = JSON.parse(fs.readFileSync(statsFile, "utf8"));
      if (j && typeof j === "object" && j.sessions) {
        migrateLegacy(j);
        j.retired ??= ZERO();
        return j;
      }
    } catch { /* 首次运行或文件坏了，重建 */ }
    return blankStats();
  }
  let stats = loadStats();

  /** 总数 = 所有会话之和 + 被裁掉的历史（派生，不存冗余计数） */
  function computeTotal() {
    const t = ZERO();
    const add = (s) => {
      t.turnMs += s.turnMs || 0; t.toolMs += s.toolMs || 0;
      t.waitMs += s.waitMs || 0; t.wallMs += s.wallMs || 0;
      t.turns += s.turns || 0; t.toolCalls += s.toolCalls || 0;
      t.outTokens += s.outTokens || 0;
    };
    let n = 0;
    for (const s of Object.values(stats.sessions)) { add(s); n++; }
    add(stats.retired);
    t.sessions = n + (stats.retired.sessions || 0);
    return t;
  }

  /** 所有会话的工具耗时汇总（同样派生） */
  function computeToolMsByName() {
    const m = {};
    const merge = (src) => { for (const [k, v] of Object.entries(src || {})) m[k] = (m[k] || 0) + v; };
    for (const s of Object.values(stats.sessions)) merge(s.toolMsByName);
    merge(stats.retired.toolMsByName);
    return m;
  }

  /// 旧模型遗留的顶层键由 migrateLegacy 负责清掉，这里不再需要额外的清理函数

  function sessionRecord() {
    return {
      startedAt: S.startedAt, turnMs: S.turnMs, toolMs: S.toolMs,
      waitMs: S.waitMs, wallMs: wallMs(), turns: S.turns,
      toolCalls: S.toolCalls, outTokens: S.outTokens,
      toolMsByName: { ...S.toolMsByName },
    };
  }

  /** 裁剪：超出的最老会话把值**倒进 retired**，不能直接丢（否则总数会变小） */
  function prune() {
    const keys = Object.keys(stats.sessions);
    if (keys.length <= 200) return;
    keys.sort((a, b) => (stats.sessions[a].startedAt || 0) - (stats.sessions[b].startedAt || 0));
    for (const k of keys.slice(0, keys.length - 200)) {
      const s = stats.sessions[k];
      stats.retired = stats.retired || ZERO();
      for (const f of T_FIELDS) {
        stats.retired[f] = (stats.retired[f] || 0) + (s[f] || 0);
      }
      stats.retired.sessions = (stats.retired.sessions || 0) + 1;
      stats.retired.toolMsByName = stats.retired.toolMsByName || {};
      for (const [n, v] of Object.entries(s.toolMsByName || {})) {
        stats.retired.toolMsByName[n] = (stats.retired.toolMsByName[n] || 0) + v;
      }
      delete stats.sessions[k];
    }
  }

  /**
   * 原子写：写临时文件再 rename。
   * rename 在同一文件系统上是原子的 —— 读者永远看不到半截 JSON。
   * 踩过：之前直接 writeFileSync，一旦写到一半被打断，下次 loadStats 解析失败就
   * 回退成空统计，**整份历史归零**。
   */
  function writeAtomic(obj) {
    const tmp = `${statsFile}.tmp.${process.pid}`;
    fs.writeFileSync(tmp, JSON.stringify(obj, null, 2), "utf8");
    fs.renameSync(tmp, statsFile);
  }

  /** 临时文件残留清理（进程被强杀时可能留下） */
  function cleanupTmp() {
    try {
      const dir = path.dirname(statsFile), base = path.basename(statsFile);
      for (const f of fs.readdirSync(dir)) {
        if (f.startsWith(base + ".tmp.")) {
          try { fs.unlinkSync(path.join(dir, f)); } catch {}
        }
      }
    } catch {}
  }

  /**
   * 落盘。
   *
   * ★ 关键：多个 pi 窗口（或孤儿进程）共用同一个 stats 文件，所以**不能**把内存里
   * 的整份副本直接写回去 —— 后写的会把别人刚写的条目整个覆盖掉。
   * 实测后果：另一个人窗口的 flush 把本条目的重置/新条目全冲掉，条目数在 3→1→3 之间跳。
   *
   * 做法：重新读盘 → **只更新/插入自己的会话条目**（别人的照收）→ 原子写回。
   * 总计时已经是从 sessions 派生的，所以拿盘上的 retired、把自己的条目换进去即可。
   */
  function persist() {
    if (!S) return;
    const key = S.sessionFile || `ephemeral-${S.startedAt}`;
    const mine = sessionRecord();

    let merged = false;
    try {
      const j = JSON.parse(fs.readFileSync(statsFile, "utf8"));
      if (j && typeof j === "object" && j.sessions) {
        migrateLegacy(j);
        j.retired ??= ZERO();
        stats.retired = j.retired;          // 别人写的历史照收
        const others = { ...j.sessions };
        delete others[key];                 // 自己的以内存为准
        stats.sessions = { ...others, [key]: mine };
        merged = true;
      }
    } catch { /* 文件不在/写坏了 —— 走下面的兜底 */ }
    if (!merged) stats.sessions[key] = mine;

    prune();
    try { writeAtomic(stats); } catch { /* 写不了就算了，计时器不能拖死主流程 */ }
  }

  // ── 报告 ──────────────────────────────────────────────────────────
  function sessionReport() {
    const wall = wallMs();
    const model = modelMsLive(), tool = toolMsLive(), wait = waitMsLive();
    const other = Math.max(0, wall - model - tool - wait);
    const out = [`⏱  本次会话　${new Date(S.startedAt).toLocaleString()}`, ""];
    for (const [label, ms] of [["总思考（模型）", model], ["工具执行", tool], ["等你输入", wait], ["框架开销", other]]) {
      const pct = wall ? Math.round((ms / wall) * 100) : 0;
      out.push(`  ${label.padEnd(14)} ${human(ms).padStart(11)}  ${bar(ms, wall)} ${String(pct).padStart(3)}%`);
    }
    out.push(`  ${"─".repeat(14)} ${"─".repeat(16)}`);
    out.push(`  ${"墙钟总计".padEnd(13)} ${human(wall).padStart(11)}  · ${S.turns} 轮 · ${S.toolCalls} 次工具调用`);
    out.push(`  ${"输出 token".padEnd(13)} ${String(S.outTokens).padStart(11)}  · 平均 ${n1(avgTps())} tok/s`);

    // 逐轮明细 —— 直接回答"每个对话多少 token/秒"
    if (S.turnLog.length) {
      const recent = S.turnLog.slice(-12);
      out.push("");
      out.push(`  🔢 逐轮（最近 ${recent.length} 轮）`);
      out.push(`     ${"#".padStart(3)} ${"输出".padStart(7)} ${"模型".padStart(9)} ${"工具".padStart(8)} ${"tok/s".padStart(8)}`);
      for (const t of recent) {
        out.push(`     ${String(t.n).padStart(3)} ${String(t.tokens).padStart(7)} ${human(t.modelMs).padStart(9)} ${human(t.toolMs).padStart(8)} ${n1(t.tps).padStart(8)}`);
      }
    }
    return out.join("\n");
  }

  function totalReport() {
    const t = computeTotal();
    const wall = t.wallMs || 1;
    const model = Math.max(0, t.turnMs - t.toolMs);
    const tps = model > 0 ? t.outTokens / (model / 1000) : null;
    const pc = (v) => String(Math.round((v / wall) * 100)).padStart(3);
    return [
      `📊 累计（${t.sessions} 个会话）`, "",
      `  总思考（模型） ${human(model).padStart(11)}  ${bar(model, wall)} ${pc(model)}%`,
      `  工具执行       ${human(t.toolMs).padStart(11)}  ${bar(t.toolMs, wall)} ${pc(t.toolMs)}%`,
      `  等你输入       ${human(t.waitMs).padStart(11)}  ${bar(t.waitMs, wall)} ${pc(t.waitMs)}%`,
      `  ─────────────  ${"─".repeat(16)}`,
      `  墙钟总计       ${human(t.wallMs).padStart(11)}  · ${t.turns} 轮 · ${t.toolCalls} 次工具调用`,
      `  输出 token     ${String(t.outTokens).padStart(11)}  · 平均 ${n1(tps)} tok/s`,
    ].join("\n");
  }

  function topReport() {
    const agg = computeToolMsByName();
    const src = Object.keys(agg).length ? agg : (S?.toolMsByName || {});
    const xs = Object.entries(src).sort((a, b) => b[1] - a[1]).slice(0, 15);
    if (!xs.length) return "还没有工具耗时记录。";
    const max = xs[0][1] || 1;
    return [`🔧 工具耗时排行（累计）`, "",
      ...xs.map(([n, ms]) => `  ${n.padEnd(22)} ${human(ms).padStart(11)}  ${bar(ms, max, 14)}`)].join("\n");
  }

  // ── 事件接线 ──────────────────────────────────────────────────────
  pi.on("session_start", (event, ctx) => {
    lastCtx = ctx;
    if (timer) { clearInterval(timer); timer = null; }
    if (S) persist();                    // 切会话：先把上一个存下（幂等，不会算重）
    cleanupTmp();                        // 清掉被强杀留下的 .tmp

    const sessFile = ctx.sessionManager?.getSessionFile?.() ?? null;
    S = blankSession(sessFile);

    // ★ /reload 与 --resume 会命中同一条会话记录 → 接着算，**不要清零**
    //   （旧版在这里清空，导致 reload 后墙钟/思考时间从 0 重来，旧值还被覆盖掉）
    const prev = sessFile ? stats.sessions[sessFile] : null;
    if (prev) {
      S.startedAt   = prev.startedAt || S.startedAt;
      S.turnMs      = prev.turnMs || 0;
      S.toolMs      = prev.toolMs || 0;
      S.waitMs      = prev.waitMs || 0;
      S.turns       = prev.turns || 0;
      S.toolCalls   = prev.toolCalls || 0;
      S.outTokens   = prev.outTokens || 0;
      S.toolMsByName = { ...(prev.toolMsByName || {}) };
      S.resumed = true;
    }
    toolIntervals = [];
    toolOpen.clear();
    turnToolIv = [];
    turnOpenAt = null;
    pendingOutput = 0;
    waitOpenAt = null;

    // ★ 时钟装到编辑器下边框左下角
    editorInstalled = false;
    if (ctx.mode === "tui") {
      try {
        ctx.ui.setEditorComponent((tui, theme, keybindings) => {
          class ClockEditor extends CustomEditor {
            constructor(t, th, kb) {
              // ★ embedWorkingStatus: true 是**必需的**：
              //   不传 -> isWorkingStatusEditor() 返回 false -> pi 把 `⠸ Working…`
              //   从顶部边框挤出去，变成编辑器上方一个独立行。
              //   （详见文件头「第一条横线为什么以前是空的」）
              super(t, th, kb, { paddingX: 0, embedWorkingStatus: true });
              activeTui = t;
            }
            render(width) {
              const lines = super.render(width);              if (lines.length < 2) return lines;
              // ⚠️ 工厂给的 theme 是 EditorTheme（只有 borderColor 等），**没有 fg()**。
              // 上色必须用 ExtensionContext 的 theme（ctx.ui.theme）。
              // 拿不到就退化成无色纯文本 —— 渲染路径绝对不能抛异常，否则整个 TUI 崩掉。
              const paint = (name, s) => {
                const thm = lastCtx?.ui?.theme;
                if (thm && typeof thm.fg === "function") {
                  try { return thm.fg(name, s); } catch { /* 下面兜底 */ }
                }
                return s;
              };

              // 第一条横线（顶部边框）不在这里画 —— 由 CustomEditor.renderTopBorder
              // 负责，因为上面传了 embedWorkingStatus: true，pi 会把 ⠸ Working… 嵌进去。
              // 这里只改**第二条横线**（底部边框）：会话仪表盘。

              // 字段表 —— 想增减就改下面的 BOTTOM_LEFT / BOTTOM_RIGHT
              // 字段表（纯文本）；想增减就改 FOOTER_LAYOUTS 或这里，颜色由 COLOR 决定
              const plain = {
                think: `⏱ 思考 ${fmt(modelMsLive())}`,
                tool: `工具 ${fmt(toolMsLive())}`,
                tps: `⚡ ${footerStyle === "compact" ? String(Math.round(S?.lastTps ?? avgTps())) : n1(S?.lastTps ?? avgTps())} t/s`,
                turns: `${S?.turns ?? 0} 轮`,
                tokens: `↑ ${tok(S?.outTokens)}`,
                tools: `${S?.toolCalls ?? 0} 工具`,
                wait: `等 ${fmt(waitMsLive())}`,
                wall: `墙 ${fmt(wallMs())}`,
                beijing: `北京 ${bjTime()}`,
              };
              const COLOR = {
                think: "accent", tps: "accent", beijing: "accent",
                tool: "muted", turns: "muted", tokens: "muted", tools: "muted", wait: "muted",
                wall: "dim",
              };
              const painted = {};
              for (const k of Object.keys(plain)) painted[k] = paint(COLOR[k] || "muted", plain[k]);

              const { left, right } = composeFooter(footerStyle, painted);
              if (footerStyle === "off") return lines;   // 不画：留 pi 原生边框
              lines[lines.length - 1] = fitBorder(left, right, width, (s) => this.borderColor(s));
              return lines;
            }

            // ── 第一条横线（工作线）右侧：MCP 指示器 ──────────────────
            // 2026-10-10 用户：适配器原来那行「🔌 MCP: 1 server enabled」太长且占位，
            // 移到这里，且只留「🔌N」（N = 启用的 server 数；配置里 disabled 的不算）。
            // 旧的适配器那一行已在 .pi/agent/mcp-adapter.json 设 mcpFooterStatus=off 关掉。
            renderTopBorder(width, hiddenLineCount) {
              const line = super.renderTopBorder(width, hiddenLineCount);
              try {
                const n = mcpEnabledCount(agentDir);
                if (n === null || width < 24) return line;
                const chipText = `🔌${n}`;
                const thm = lastCtx?.ui?.theme;
                const chip = thm && typeof thm.fg === "function" ? thm.fg("muted", chipText) : chipText;
                return spliceRight(line, chip, width);
              } catch {
                return line;   // 渲染路径绝不能抛：出错就原样返回
              }
            }
          }
          return new ClockEditor(tui, theme, keybindings);
        });
        editorInstalled = true;
      } catch {
        editorInstalled = false;    // 换不了就退回 footer 状态
      }
    }

    timer = setInterval(tick, TICK_MS);
    if (typeof timer.unref === "function") timer.unref();
    tick();
  });

  pi.on("before_agent_start", (_e, ctx) => {
    lastCtx = ctx;
    if (S && !S.firstPromptAt) S.firstPromptAt = Date.now();
  });

  // 一个 turn = 一次 LLM 调用 + 它的工具调用；模型时间 = turn 时长 − 工具
  pi.on("turn_start", () => {
    turnOpenAt = Date.now();
    turnToolIv = [];
    pendingOutput = 0;
  });

  // 只要 assistant 的 output token 数（口径：completion tokens）
  pi.on("message_end", (e) => {
    if (e?.message?.role !== "assistant") return;
    const u = e.message.usage;
    pendingOutput += Number(u?.output ?? 0) || 0;
  });

  pi.on("turn_end", () => {
    if (!S || !turnOpenAt) return;
    const dur = Date.now() - turnOpenAt;
    const tTool = unionMs(turnToolIv);
    const modelForTurn = Math.max(0, dur - tTool);
    S.turnMs += dur;
    S.turns += 1;
    S.outTokens += pendingOutput;
    const tps = modelForTurn > 0 && pendingOutput > 0
      ? pendingOutput / (modelForTurn / 1000) : null;
    S.lastTps = tps;
    S.lastTokens = pendingOutput;
    S.turnLog.push({ n: S.turns, tokens: pendingOutput, modelMs: modelForTurn, toolMs: tTool, tps });
    if (S.turnLog.length > MAX_TURNS_KEPT) S.turnLog.shift();
    turnOpenAt = null;
    turnToolIv = [];
    pendingOutput = 0;
    tick();
  });

  // 工具计时：tool_call 在真正执行前、tool_result 在执行完成后（字段有文档保证）
  pi.on("tool_call", (e) => {
    const id = e?.toolCallId ?? `t${Date.now()}${Math.random()}`;
    toolOpen.set(id, { at: Date.now(), name: e?.toolName || "unknown" });
  });

  pi.on("tool_result", (e) => {
    const id = e?.toolCallId;
    const o = id ? toolOpen.get(id) : null;
    if (o) {
      const dur = Date.now() - o.at;
      toolOpen.delete(id);
      const iv = { s: o.at, e: Date.now() };
      toolIntervals.push(iv);
      turnToolIv.push(iv);
      if (S) {
        S.toolCalls += 1;
        S.toolMsByName[o.name] = (S.toolMsByName[o.name] || 0) + dur;
      }
    }
    tick();
  });

  // 等人输入的时间单独算，不该记进"思考时间"
  pi.on("ui_prompt_start", () => { waitOpenAt = Date.now(); tick(); });
  pi.on("ui_prompt_end", () => {
    if (waitOpenAt && S) S.waitMs += Date.now() - waitOpenAt;
    waitOpenAt = null;
    tick();
  });

  pi.on("agent_settled", () => {
    if (S) S.toolMs = unionMs(toolIntervals);   // 落一个稳定的快照值
    tick();
  });

  pi.on("session_shutdown", (_e, ctx) => {
    if (timer) { clearInterval(timer); timer = null; }
    if (S) S.toolMs = unionMs(toolIntervals);
    persist();
    if (editorInstalled) {
      try { ctx?.ui?.setEditorComponent(undefined); } catch { /* 忽略 */ }
    }
    try { ctx?.ui?.setStatus(STATUS_KEY, undefined); } catch { /* 忽略 */ }
    editorInstalled = false;
    activeTui = null;
    S = null;
  });

  // ── 命令 ──────────────────────────────────────────────────────────
  pi.registerCommand("time", {
    description: "思考时间与生成速度报告：/time | /time top | /time style compact|full|off | /time reset",
    handler: async (args, ctx) => {
      lastCtx = ctx;
      const sub = (args || "").trim().toLowerCase();
      if (sub === "reset") {
        stats = blankStats();
        try { writeAtomic(stats); } catch {}
        ctx.ui.notify("累计统计已清零（当前会话不受影响）", "info");
        return;
      }
      if (sub === "top") { ctx.ui.notify(topReport(), "info"); return; }
      if (sub.startsWith("style")) {
        const want = sub.replace(/^style\s*/, "").trim();
        if (!FOOTER_LAYOUTS[want]) {
          ctx.ui.notify(`当前样式：${footerStyle}　用法：/time style compact|full|off`, "info");
          return;
        }
        footerStyle = want;
        saveStyle(want);
        ctx.ui.notify(`底部横线样式 → ${want}（已落盘，下次会话沿用）`, "info");
        return;
      }
      if (!S) { ctx.ui.notify("本会话还没开始计时。", "info"); return; }
      ctx.ui.notify(sessionReport(), "info");
      ctx.ui.notify(totalReport(), "info");
    },
  });
}
