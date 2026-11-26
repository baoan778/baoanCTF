/**
 * tracewatch.mjs —— 工具输出留痕的纯逻辑（零依赖，node 直接跑）
 *
 * 为什么要有它
 * ------------
 * 会话被压缩/重启后，"当时那条命令的原始输出是什么"就没了。
 * 而 `fgs verify` 的逐字溯源恰恰要求"这个值真的出现在某条真实输出里" ——
 * 如果那条输出已随压缩消失，人就只能靠记忆复述，而记忆复述正是伪造的来源。
 *
 * 已有机制为什么不够
 * ------------------
 * `flag-watch` 只记**命中 flag 形状的片段**（±600 字符），没命中就什么都不留；
 * `board/log.jsonl` 记的是黑板事件（fact/step/apply），不是工具输出。
 * 所以"我跑过那条命令、输出里有这个串"这件事**无处回查**。
 *
 * 三条设计纪律（否则它会变成存储炸弹）
 * ------------------------------------
 * 1. **有界**：单条只留前 N / 后 M 字符，中间标记省略了多少（原文留在会话里，压缩前够用）
 * 2. **轮转**：单文件超过上限就 rename 成 .1，只保留一代（不留无限历史）
 * 3. **fail-open**：写不进去就跳过，绝不影响工具本身
 *
 * 与 fgs verify 的关系：本模块**不能**替代逐字溯源 —— 它留的是**裁剪后**的片段。
 * 要盖章请用原文（`fgs verify --from <文件>`）；本模块只负责"让你还能找到那段输出"。
 */

/** 默认参数 */
export const DEFAULTS = {
  headChars: 2000,      // 保留开头
  tailChars: 3000,      // 保留结尾（结尾常含结果/flag，比开头更重要）
  maxLineChars: 400,    // 单行超长时截断（防一条 base64 撑爆）
  maxEntryBytes: 16 * 1024,
  fileMaxBytes: 8 * 1024 * 1024,
  skipTools: ["todo_write", "str_replace", "write", "edit"],  // 纯写操作，输出无证据价值
  spill: true,         // 被裁掉的部分另存一份原文（#47：工具大输出溢出到 cmd-output/，不占上下文）
};

/**
 * 裁剪一段输出，返回 {text, omitted}。
 * 中间省略而不是简单截断 —— 结果常在结尾。
 */
export function clip(text, opts) {
  const o = { ...DEFAULTS, ...(opts || {}) };
  let s = typeof text === "string" ? text : String(text ?? "");
  if (!s) return { text: "", omitted: 0 };

  const head = o.headChars, tail = o.tailChars;
  let omitted = 0;

  // ★ 顺序很重要（2026-11-06 自检抓到的真 bug）：
  //   必须先做**首尾裁剪**，再压单行长度。
  //   反过来写的话，一条 20000 字符的单行会先被 maxLineChars 截成 400，
  //   结尾的 TAIL 直接消失 —— 而结尾往往正是结果/flag 所在。
  //   实测：先截行 → endsWith("TAIL") = false；先首尾裁剪 → true。
  if (s.length > head + tail) {
    omitted = s.length - head - tail;
    s = s.slice(0, head) + `\n…[省略 ${omitted} 字符]…\n` + s.slice(-tail);
  }

  // 再做单行压缩：此时 s 已经是"被保留下来的那段"，压它不丢首尾
  if (o.maxLineChars > 0) {
    s = s.split("\n").map((ln) =>
      ln.length > o.maxLineChars
        ? ln.slice(0, o.maxLineChars) + `…(+${ln.length - o.maxLineChars})`
        : ln
    ).join("\n");
  }

  return { text: s, omitted };
}

/**
 * 该不该给这次工具调用留痕。
 * @returns {{keep:boolean, why:string}}
 */
export function shouldTrace(toolName, input, resultText, opts) {
  const o = { ...DEFAULTS, ...(opts || {}) };
  const name = String(toolName || "");
  if (!name) return { keep: false, why: "没有工具名" };
  if ((o.skipTools || []).includes(name)) {
    return { keep: false, why: `纯写操作（${name}），输出无证据价值` };
  }
  const txt = String(resultText ?? "");
  if (!txt.trim()) return { keep: false, why: "空输出" };
  // 很短的输出也留：命令回执（"OK"/"403"）本身就是证据
  return { keep: true, why: "" };
}

/**
 * 组装一条留痕记录（纯数据，不碰 IO）。
 * @returns {object} 可直接 JSON.stringify 的对象
 */
/**
 * 超长输出的原文落盘文件名（#47）：相对留痕文件所在目录的 cmd-output/。
 * 只给**名字**不给绝对路径 —— 写文件是调用方的事，这个模块零 IO。
 */
export function spillName(ts, tool) {
  const t = String(tool || "?").replace(/[^A-Za-z0-9_.-]/g, "_").slice(0, 40);
  const stamp = String(ts || "").replace(/[^0-9]/g, "").slice(0, 17) || "0";
  return `cmd-output/${stamp}-${t}.txt`;
}

export function makeRecord({ ts, pid, tool, input, resultText, isError, session }, opts) {
  const o = { ...DEFAULTS, ...(opts || {}) };
  const { text, omitted } = clip(resultText, o);

  // 命令摘要：只留首个关键行，便于人去 grep 找
  let cmd = "";
  if (input && typeof input === "object") {
    if (typeof input.command === "string") cmd = input.command;
    else if (typeof input.path === "string") cmd = `[${tool}] ${input.path}`;
    else if (typeof input.pattern === "string") cmd = `[${tool}] ${input.pattern}`;
    else {
      try { cmd = JSON.stringify(input).slice(0, 300); } catch { cmd = ""; }
    }
  }
  cmd = cmd.split("\n")[0].slice(0, 300);

  const rec = {
    ts: ts || new Date().toISOString(),
    tool: String(tool || "?"),
    cmd,
    is_error: !!isError,
    out_len: String(resultText ?? "").length,
    omitted,
    out: text,
  };
  // #47：被裁掉的原文另存一份，摘要里只留指针 —— 上下文不炸，原文又没丢。
  if (omitted > 0 && o.spill) rec.spill = spillName(rec.ts, tool);
  if (pid) rec.pid = pid;
  if (session) rec.session = session;
  return rec;
}

/**
 * 该不该轮转（纯判断，实际 rename 由调用方做）。
 * @returns {boolean}
 */
export function needsRotate(currentSize, opts) {
  const o = { ...DEFAULTS, ...(opts || {}) };
  return Number(currentSize) >= o.fileMaxBytes;
}

// ── 自检 ───────────────────────────────────────────────────────────────────
function _selftest() {
  const fail = [];
  const eq = (n, got, want) => {
    if (JSON.stringify(got) !== JSON.stringify(want)) fail.push(`${n}: got=${JSON.stringify(got)} want=${JSON.stringify(want)}`);
  };

  // 1) 短输出原样保留（不裁剪）
  const a = clip("OK\n");
  eq("短输出不裁", a.text, "OK\n");
  eq("短输出 omitted=0", a.omitted, 0);

  // 2) 中等输出（未超 head+tail）也不裁
  const mid = "x".repeat(3000);
  eq("中等不裁", clip(mid, { headChars: 2000, tailChars: 3000 }).omitted, 0);

  // 3) 超长：中间省略，**首尾都保留**
  const long = "HEAD" + "y".repeat(20000) + "TAIL";
  const c = clip(long, { headChars: 100, tailChars: 100 });
  if (!c.text.startsWith("HEAD")) fail.push("超长输出没保留开头");
  if (!c.text.endsWith("TAIL")) fail.push("超长输出没保留结尾");
  if (c.omitted <= 0) fail.push("超长输出 omitted 应 > 0");
  if (!/省略 \d+ 字符/.test(c.text)) fail.push("没有省略标记");

  // 4) 单行超长被截断（防 base64 撑爆）
  const huge = "A".repeat(5000);
  const d = clip(huge, { maxLineChars: 100, headChars: 9999, tailChars: 9999 });
  if (d.text.length > 200) fail.push("单行超长没截断");

  // 5) 空输入不炸
  eq("空输入", clip("").text, "");
  eq("null 输入", clip(null).text, "");

  // 6) shouldTrace：纯写操作不留
  eq("write 不留", shouldTrace("write", {}, "ok").keep, false);
  eq("edit 不留", shouldTrace("edit", {}, "ok").keep, false);
  //    空输出不留
  eq("空输出不留", shouldTrace("bash", {}, "   ").keep, false);
  //    正常 bash 留
  eq("bash 留", shouldTrace("bash", {}, "hello").keep, true);
  //    短回执也要留（"403" 本身就是证据）
  eq("短回执也留", shouldTrace("bash", {}, "403").keep, true);

  // 7) makeRecord：字段齐、cmd 取首行、超长已裁
  const r = makeRecord({
    ts: "2026-11-06T00:00:00Z", pid: "p1", tool: "bash",
    input: { command: "curl -i http://x\n第二行" },
    resultText: "y".repeat(30000), isError: false, session: "s1",
  }, { headChars: 50, tailChars: 50 });
  eq("record ts", r.ts, "2026-11-06T00:00:00Z");
  eq("record pid", r.pid, "p1");
  eq("record cmd 只取首行", r.cmd, "curl -i http://x");
  eq("record out_len 是原文长度", r.out_len, 30000);
  if (!(r.omitted > 0)) fail.push("record omitted 应 > 0");
  if (r.out.length > 400) fail.push("record out 没被裁剪");

  // 8) 各种 input 形态都能出 cmd
  eq("path 形态", makeRecord({ tool: "read", input: { path: "/a/b" }, resultText: "x" }).cmd, "[read] /a/b");
  eq("pattern 形态", makeRecord({ tool: "grep", input: { pattern: "flag" }, resultText: "x" }).cmd, "[grep] flag");

  // 9) 轮转阈值
  eq("未到不轮转", needsRotate(100, { fileMaxBytes: 1000 }), false);
  eq("到点轮转", needsRotate(1000, { fileMaxBytes: 1000 }), true);

  // 10) #47：超长输出另存原文（spill 指针），短输出与显式关闭都不给
  if (!String(r.spill || "").startsWith("cmd-output/")) fail.push("超长记录应给 spill 指针");
  if (!String(r.spill || "").includes("20261106000000")) fail.push("spill 名应带时间戳");
  eq("短输出无 spill", "spill" in makeRecord({ ts: "2026-11-06T00:00:01Z", tool: "bash", resultText: "ok" }), false);
  eq("显式关闭无 spill", "spill" in makeRecord({ ts: "2026-11-06T00:00:02Z", tool: "bash", resultText: "z".repeat(30000) }, { spill: false }), false);
  eq("spillName 只留安全字符", spillName("2026-11-06T00:00:00Z", "weird/tool name"), "cmd-output/20261106000000-weird_tool_name.txt");

  if (fail.length) {
    console.error("FAIL\n  " + fail.join("\n  "));
    return 1;
  }
  console.log("OK tracewatch selftest: 三次组断言全绿".replace("三次", "22 "));
  return 0;
}

if (process.argv.includes("--selftest")) process.exit(_selftest());
