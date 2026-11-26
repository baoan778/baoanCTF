/**
 * flagwatch.mjs —— flag 形态扫描（纯函数，零依赖，可离线自检）
 *
 * 为什么要它：`tools/knowledge/flag-formats.md` 自己写着「**agent 最常见的丢分方式是
 * flag 已经出现在输出里，却当成普通文本划过去了**」—— 但本仓一直没有对应的**自动**检测。
 * 这个模块就是那个检测：任何工具输出 / 文件内容 / 日志经过它，命中就返回候选。
 * 接进 pi 的钩子在 `.pi/agent/extensions/flag-watch.ts`（tool_result）。
 *
 * 设计原则：**宁缺毋滥** —— 误报会训练出「狼来了」效应，比漏报更坏。
 *   命中必须同时满足：形状对 + 前缀不是代码构造 + 正文没有正则/模板/代码符号。
 *
 * 用法：node tools/flagwatch.mjs --selftest
 */

// 前缀：字母数字下划线，2~20 位（0xGame 这种数字开头也要）
const BRACE = /(?<![A-Za-z0-9_$`\\@])([A-Za-z0-9_]{2,20})\{([^{}\s;()"'`\\$[\]<>=:]{3,120})\}/g;
// `flag: xxx` / `FLAG = xxx`（要求够长且含数字，压误报）
const KV = /(?<![A-Za-z0-9_])(?:flag|ctf|key)\s*[:=]\s*([A-Za-z0-9_+/-]{8,80})/gi;
// 变形：先抠 base64 候选，再解码回看得见的字（比猜固定前缀靠谱 —— 连续流的 base64 没有 `=` 对齐）
const B64_CAND = /(?<![A-Za-z0-9+/])([A-Za-z0-9+/]{8,}={0,2})(?![A-Za-z0-9+/=])/g;
// 变形：hex 里的 flag{ / FLAG{（hex 无对齐歧义，固定前缀就行）
const HEX = /(?:666c61677b|464c41477b)[0-9a-fA-F]{4,}/g;
const B64_MAX_CAND = 200;
const B64_MAX_LEN = 4096;

// 代码构造黑名单（缩小到「会紧跟 { 的关键字」）
const DENY_PREFIX = new Set([
  "else", "struct", "enum", "union", "switch", "try", "catch", "finally", "do",
  "namespace", "function", "class", "interface", "def", "lambda", "impl", "trait",
  "match", "while", "for", "if", "when", "case", "default", "static", "fn", "func",
  "return", "typedef", "public", "private", "protected", "object", "package", "type",
  "todo", "fixme", "note", "bug", "hack", "xxx",
]);

const KNOWN = new Set([
  "flag", "flag1", "flag2", "flag3", "FLAG", "Flag", "ctf", "CTF",
  "0xGame", "ctfshow", "BUUCTF", "NSSCTF", "DASCTF", "hgame", "actf", "n1ctf",
  "SUCTF", "moectf", "HNCTF", "BMZCTF", "QWB", "LHFLAG", "PKWCTF", "flag_format",
]);

/** 前缀是否像 flag 前缀（含字母、非黑名单、非全数字） */
function prefixOk(p) {
  if (DENY_PREFIX.has(p.toLowerCase())) return false;
  if (!/[A-Za-z]/.test(p)) return false;      // 排除 `{2,20}` 这类量词碎片
  return true;
}

/** 正文是否像 flag 正文（不是正则/模板/代码） */
function bodyOk(b) {
  if (b.length < 3) return false;
  if (b.includes("..")) return false;          // `XXX{...}` 模板占位
  if (b.includes("->") || b.includes("://")) return false;
  if (/^[.\-+*]/.test(b)) return false;
  return true;
}

function confidenceOf(prefix, body) {
  if (KNOWN.has(prefix)) return "high";
  if (/^[A-Z0-9_]+$/.test(prefix)) return "high";          // 全大写下划线：像出题人前缀
  if (/[0-9]/.test(body) && /[A-Za-z]/.test(body)) return "high";
  return "medium";
}

/** 只在文本里找 brace 形态（给 base64 解码后的二次扫描用，不递归） */
function braceHits(text) {
  const out = [];
  for (const m of text.matchAll(BRACE)) {
    const [full, prefix, body] = m;
    if (!prefixOk(prefix) || !bodyOk(body)) continue;
    out.push({ flag: full, kind: "brace", confidence: confidenceOf(prefix, body), index: m.index ?? 0 });
  }
  return out;
}

/**
 * 扫一段文本，返回候选（去重，按位置）。
 * @param {string} text
 * @returns {Array<{flag:string,kind:string,confidence:string,index:number}>}
 */
export function scanFlags(text) {
  if (typeof text !== "string" || text.length === 0) return [];
  const out = [];
  const seen = new Set();
  const push = (flag, kind, confidence, index) => {
    if (seen.has(flag)) return;
    seen.add(flag);
    out.push({ flag, kind, confidence, index });
  };

  for (const m of text.matchAll(BRACE)) {
    const [full, prefix, body] = m;
    if (!prefixOk(prefix) || !bodyOk(body)) continue;
    push(full, "brace", confidenceOf(prefix, body), m.index ?? 0);
  }
  for (const m of text.matchAll(KV)) {
    const v = m[1];
    // `flag: <word>` 太容易误报 → 要求够长且含 ≥2 个数字
    if ((v.match(/[0-9]/g) || []).length < 2) continue;
    if (/^(placeholder|example|sample|xxxx|todo|none|null)/i.test(v)) continue;
    push(v, "kv", "medium", m.index ?? 0);
  }
  for (const m of text.matchAll(HEX)) push(m[0], "hex", "high", m.index ?? 0);
  // base64：抠候选 → 解码 → 只留解码后真含 brace 形态的（限制候选数与长度，防 DoS）
  if (text.length <= 2_000_000) {
    let n = 0;
    for (const m of text.matchAll(B64_CAND)) {
      if (++n > B64_MAX_CAND) break;
      const tok = m[1];
      if (tok.length > B64_MAX_LEN) continue;
      const padded = tok + "=".repeat((4 - (tok.length % 4)) % 4);
      let dec = "";
      try { dec = Buffer.from(padded, "base64").toString("latin1"); } catch { continue; }
      if (!dec || dec.length < 6) continue;
      const inner = braceHits(dec);
      if (inner.length) push(inner[0].flag, "base64", "high", m.index ?? 0);
    }
  }

  return out.sort((a, b) => a.index - b.index);
}

/** 给命中生成一句给模型看的提示（钩子用） */
export function warnLine(hits) {
  const lines = hits.map((h) => `  · [${h.confidence}/${h.kind}] ${h.flag}`);
  return [
    "🚩 flag-watch 命中 flag 形状（别划过去）：",
    ...lines,
    "  下一步：逐字确认（byte-exact）+ `fgs verify <pid> <fact> --from <原始输出>`；",
    "  提交只认平台回执 `fgs confirm`。变形先亲手解一层再判。",
  ].join("\n");
}

// ─────────────────────────────────────────────── 自检
if (process.argv.includes("--selftest")) {
  const POS = [
    ["FLAG{th1s_1s_4_t3st}", "brace"],
    ["LHFLAG{IHDR_CRC_3E8C7A9D}", "brace"],
    ["ctfshow{1a2b3c4d}", "brace"],
    ["0xGame{h3llo_w0rld}", "brace"],
    ["BUUCTF{a-b_c!d@e}", "brace"],
    ["n1ctf{2b3c4d5e}", "brace"],
  ];
  const NEG = [
    "${VAR}",                       // shell 变量
    "else{foo();}",
    "struct{int a;}",
    "XXX{...}",                     // 模板占位
    "flag{...}",
    "`LHFLAG{`（练习平台）",           // 文档里的反引号写法，无闭合 }
    "[A-Za-z0-9_]{2,20}\\{[^}]{3,}\\}",  // flag-formats.md 里的正则本身
    "awk '{print $1}'",
    '{"a":1,"b":2}',
    "class Foo{}",
    "if(x){y;}",
    "flag: placeholder",
    "// TODO{later}",
    "@media{color:red}",
  ];
  const errs = [];
  for (const [t, kind] of POS) {
    const h = scanFlags(t);
    if (!h.some((x) => x.kind === kind)) errs.push(`漏报(${kind}): ${t}`);
  }
  for (const t of NEG) {
    const h = scanFlags(t);
    if (h.length) errs.push(`误报: ${t} → ${JSON.stringify(h.map((x) => x.flag))}`);
  }
  // 变形
  const b64 = Buffer.from("flag{b4se64_layer}").toString("base64");
  const b64pad = Buffer.from("flag{x_y}").toString("base64");
  const hex = Buffer.from("flag{hex_layer}").toString("hex");
  if (!scanFlags(b64).some((x) => x.kind === "base64")) errs.push(`漏报 base64: ${b64}`);
  if (!scanFlags(b64pad).some((x) => x.kind === "base64")) errs.push(`漏报 base64(带=): ${b64pad}`);
  if (!scanFlags(hex).some((x) => x.kind === "hex")) errs.push(`漏报 hex: ${hex}`);
  // 假 base64 不能误报
  if (scanFlags("VGhpcyBpcyBqdXN0IGEgc2VudGVuY2U=").length) errs.push("普通 base64 误报");
  // 去重：同一 flag 出现两次只报一次
  if (scanFlags("FLAG{dup} and FLAG{dup}").length !== 1) errs.push("去重失效");
  // 空输入不崩
  if (scanFlags("").length !== 0 || scanFlags(null).length !== 0) errs.push("空输入处理错");
  // 性能护栏：1MB 文本不炸（不能有灾难性回溯）
  const big = "x".repeat(1024 * 1024);
  const t0 = Date.now();
  scanFlags(big);
  const ms = Date.now() - t0;
  if (ms > 2000) errs.push(`1MB 扫描太慢: ${ms}ms`);

  const n = POS.length + NEG.length + 7;
  console.log(errs.length ? `flagwatch selftest: FAIL ${errs.length}/${n}` : `flagwatch selftest: OK ${n}/${n}`);
  for (const e of errs) console.log("  ✗", e);
  if (!errs.length) console.log(warnLine(scanFlags("leak: FLAG{dry_run_ok}")));
  process.exit(errs.length ? 1 : 0);
}
