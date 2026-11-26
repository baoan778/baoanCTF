#!/usr/bin/env node
/**
 * ledger.mjs —— 分析台账纯逻辑层（X-2 压缩穿越 + X-4 台账结构）
 *
 * 为什么要有它
 * ------------
 * 本仓 board/fgs.py 里已有一个 ledger，但那是**flag 申报台账**（谁在什么时候交了
 * 哪个 flag、平台回执是什么）。本文这个是完全不同的东西：**分析台账** ——
 * "这题目前确认了什么、依据哪一级证据、还有哪些**没测**"。
 *
 * 为什么必须有（OpenSecurity 2.0 的实测结论，文章 + 源码双证）
 * ------------------------------------------------------------
 * 上下文压缩由**总结器重写**，重写会**洗掉限定词**：
 *   "已验证：注入点在 search 参数（仅 GET，未测 POST）"
 * 压成
 *   "已验证注入点在 search 参数"  ← "未测 POST" 没了
 * 之后所有推理都建立在这个**变宽了的前提**上，越跑越偏。
 * 所以台账要**原样穿越**压缩，而不是让总结器去复述。
 *
 * 三条设计纪律
 * ------------
 * 1. **纯函数**：不读环境、不读盘（读盘由扩展做），同样输入永远同样输出。
 * 2. **有界**：注入按 token 预算逐行截断，截断处写明"全文见路径"，
 *    绝不把台账变成新的上下文炸弹。
 * 3. **fail-safe**：结构不合法时**不注入**（宁可这轮没台账，也不注入半截误导）。
 *
 * 台账结构（三段固定节名 —— 与 fgs 的申报台账刻意区分）
 *   ## 结论台账    每条 = 结论 + 证据等级 + 已验证范围 + 未验证的是什么
 *   ## 未测条件     还没试的维度（**这一段比结论更值钱**）
 *   ## 观测记录     原始观测（关键命令 + 关键输出片段）
 */

import * as path from "node:path";

/** 固定节名 —— 改这里必须同时改 board/tests/test_ledger.py 的断言 */
export const SECTIONS = {
  conclusions: "结论台账",
  untested: "未测条件",
  observations: "观测记录",
};

/** 证据等级四档（与 OS2.0 的 observed/inferred/assumption/unverified 对齐） */
export const EVIDENCE_LEVELS = ["observed", "inferred", "assumption", "unverified"];

/** 默认注入预算（token 估算口径：字符数 / 4，与 fgs 的 estimate_tokens 同源） */
export const DEFAULT_TOKEN_BUDGET = 4000;

/** 台账文件名 */
export const LEDGER_FILENAME = "ledger.md";

/** 占位行（视为"无内容"） */
const PLACEHOLDER = "- （暂无）";

export function estimateTokens(text) {
  const s = typeof text === "string" ? text : String(text ?? "");
  return Math.ceil(s.length / 4);
}

/** 台账文件位置：work/<pid>/ledger.md（与题目工作目录同层，压缩/换人后可查） */
export function ledgerPath(lab, pid) {
  return path.join(String(lab), "work", String(pid), LEDGER_FILENAME);
}

/** 三段模板（`fgs ledger <pid> --init` 用；人也能照抄） */
export function buildTemplate(pid, goal) {
  const p = pid || "<pid>";
  const g = goal ? String(goal) : "<一句话目标>";
  return [
    "# 分析台账 · " + p,
    "",
    "> 目标：" + g,
    "> 本文件**原样穿越上下文压缩**（不被总结器重写）。",
    "> 写结论时必须带限定词，尤其「已验证范围」—— 少一句限定，后面多跑半小时。",
    "",
    "## " + SECTIONS.conclusions,
    "> 每条：`结论` ｜ 证据等级 ｜ 已验证范围 ｜ 未验证的是什么",
    "> 证据等级：" + EVIDENCE_LEVELS.join(" / "),
    "",
    PLACEHOLDER,
    "",
    "## " + SECTIONS.untested,
    "> 还没试的维度。这一段决定下一次探索往哪走。",
    "",
    PLACEHOLDER,
    "",
    "## " + SECTIONS.observations,
    "> 原始观测：关键命令 + 关键输出片段（不是全文；全文在 log/tool-trace.jsonl）",
    "",
    PLACEHOLDER,
    "",
  ].join("\n");
}

/**
 * 解析台账 → {ok, sections, missing, lines, bytes}
 * ok=false 表示结构不对 → 调用方**不注入**（fail-safe）。
 */
export function parseLedger(text) {
  const src = typeof text === "string" ? text : "";
  const out = { ok: false, sections: {}, missing: [], lines: 0, bytes: src.length };
  if (!src.trim()) {
    out.missing = Object.values(SECTIONS);
    return out;
  }
  const lines = src.split(/\r?\n/);
  out.lines = lines.length;
  const heads = Object.values(SECTIONS);
  let cur = null;
  for (const ln of lines) {
    const m = ln.match(/^##\s+(.+?)\s*$/);
    if (m) {
      const name = m[1];
      cur = heads.includes(name) ? name : null;
      if (cur) out.sections[cur] = [];
      continue;
    }
    if (cur) out.sections[cur].push(ln);
  }
  out.missing = heads.filter((h) => !(h in out.sections));
  out.ok = out.missing.length === 0;
  for (const k of Object.keys(out.sections)) {
    out.sections[k] = out.sections[k].join("\n").trim();
  }
  return out;
}

/** 段落是否有实质内容（只有占位符 / 空 / 纯引用 → 视为空段） */
export function sectionHasContent(body) {
  const s = String(body || "")
    .split(/\r?\n/)
    .map((x) => x.trim())
    .filter((x) => x && !x.startsWith(">"))
    .filter((x) => !/^[-*]?\s*[（(]?暂无[）)]?$/.test(x));
  return s.length > 0;
}

/**
 * 按 token 预算逐行截断（只取完整行，保持 markdown 结构）。
 * 返回 {text, truncated, kept, total, tokens}
 */
export function capByTokens(text, maxTokens = DEFAULT_TOKEN_BUDGET) {
  const src = typeof text === "string" ? text : "";
  const budget = Number(maxTokens) > 0 ? Number(maxTokens) : DEFAULT_TOKEN_BUDGET;
  const lines = src.split(/\r?\n/);
  const kept = [];
  let used = 0;
  for (const ln of lines) {
    const t = estimateTokens(ln) + 1;
    if (used + t > budget) break;
    kept.push(ln);
    used += t;
  }
  let truncated = kept.length < lines.length;
  if (kept.length === 0) {
    kept.push(lines[0].slice(0, budget * 4) + "…");
    used = estimateTokens(kept[0]);
    truncated = true;
  }
  return { text: kept.join("\n"), truncated, kept: kept.length, total: lines.length, tokens: used };
}

/**
 * 生成注入块（压缩后塞回上下文用）。
 * 空台账 / 结构不对 → 返回 null（fail-safe：不注入误导内容）。
 */
export function renderForInject(text, opts) {
  const o = opts || {};
  const p = parseLedger(text);
  if (!p.ok) return null;
  const real = Object.keys(SECTIONS).filter((k) => sectionHasContent(p.sections[SECTIONS[k]]));
  if (real.length === 0) return null;
  const cap = capByTokens(text, o.maxTokens || DEFAULT_TOKEN_BUDGET);
  const full = o.path ? String(o.path) : "";
  const head = [
    "## 分析台账（压缩后原样穿越 · 未被总结器重写）",
    "> 这是压缩前落盘的**唯一带限定词**的记录。以下结论的「已验证范围」以本文件为准；",
    "> 若与你记忆中的摘要冲突，**以本文件为准**。",
  ];
  if (cap.truncated && full) {
    head.push("> （台账超预算已截断，完整版：" + full + "）");
  }
  return head.join("\n") + "\n\n" + cap.text;
}

// ── 自检（本仓纪律：新工具必须能 --selftest）────────────────────────────
function _selftest() {
  const fail = [];
  const eq = (name, got, want) => {
    if (JSON.stringify(got) !== JSON.stringify(want)) {
      fail.push(name + ": got " + JSON.stringify(got) + " want " + JSON.stringify(want));
    }
  };

  eq("estimateTokens 4 字符=1", estimateTokens("abcd"), 1);
  eq("estimateTokens 空", estimateTokens(""), 0);

  const tpl = buildTemplate("demo", "拿 flag");
  const pt = parseLedger(tpl);
  eq("模板解析 ok", pt.ok, true);
  eq("模板三段齐全", pt.missing, []);

  eq("缺段 ok=false", parseLedger("## 结论台账\n- x").ok, false);
  eq("空文本 ok=false", parseLedger("").ok, false);

  eq("占位符无实质内容", sectionHasContent("- （暂无）\n> 说明"), false);
  eq("真结论有内容", sectionHasContent("- 注入点=search ｜ observed ｜ 仅 GET"), true);

  eq("空台账不注入", renderForInject(tpl), null);

  const real = tpl.replace(PLACEHOLDER, "- 注入点=search ｜ observed ｜ 仅 GET，POST 未测");
  const inj = renderForInject(real, { path: "/x/ledger.md" });
  eq("有内容会注入", typeof inj === "string", true);
  eq("注入含穿越声明", !!(inj && inj.includes("原样穿越")), true);
  eq("注入含原限定词", !!(inj && inj.includes("POST 未测")), true);

  const big = real + "\n" + "x".repeat(8000);
  const cap = capByTokens(big, 100);
  eq("超预算会截断", cap.truncated, true);
  const cap2 = capByTokens("abc\ndef", 4000);
  eq("未超预算不截断", cap2.truncated, false);

  const inj2 = renderForInject(big, { path: "/w/demo/ledger.md", maxTokens: 100 });
  eq("截断注入带全文路径", !!(inj2 && inj2.includes("/w/demo/ledger.md")), true);

  eq("ledgerPath 口径", ledgerPath("/lab", "p1"), path.join("/lab", "work", "p1", "ledger.md"));

  if (fail.length) {
    for (const f of fail) console.error("FAIL " + f);
    console.error("ledger selftest: " + fail.length + " 组失败");
    return 1;
  }
  console.log("OK ledger selftest: 17 组断言全绿");
  return 0;
}

if (process.argv.includes("--selftest")) process.exit(_selftest());
