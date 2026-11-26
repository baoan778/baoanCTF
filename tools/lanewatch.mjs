/**
 * lanewatch.mjs —— 「派活闸门」的纯逻辑（零依赖，node 直接跑）
 *
 * 为什么要有它（2026-10-12 血账，实测不是推测）
 * ----------------------------------------------
 * 会话 `01a12e9b`（奶蛙服务器）：`fgs kickoff` 打印了**可直接粘的两路派活命令**，
 * 然后 agent 用它做了 **15 次工具调用、0 次 subagent** —— 一个人把题做完了。
 * 本仓以前对这个只有两处兜底，都不够：
 *   ① kickoff / AGENTS 的**文字**提醒 —— 模型会划过去（上面就是反例）；
 *   ② `fgs solved` 的**事后**闸门 —— 太晚，题都做完了才拒，救不回来。
 * 缺的是**在途中**的机器闸门：摸了几步还不派人 → 直接拦下一条工具调用。
 *
 * 三条设计纪律
 * ------------
 * 1. **fail-open**：拦满 MAX_BLOCKS 次就放行（继续软提醒）。子代理基础设施坏掉时
 *    不能把整轮卡死 —— 宁可少拦，不可把人的活锁住。
 * 2. **只认自己的靶子**：没跑过 `fgs kickoff` 的会话（写代码 / 查资料 / SRE）一律不碰。
 * 3. **放行「派活准备工作」**：`fgs brief/next/step-add/...`、`lane_gate`、`sesscan`
 *    不算「自己做题」—— 那正是为了派人。
 */

export const GRACE = 6;        // kickoff 后先允许自己做 6 次工具调用（够看现场、够写任务书）
export const MAX_BLOCKS = 3;   // 最多硬拦 3 次；之后放行但继续警告

// 派活的准备工作（放行）
const PLUMBING = /\bfgs\s+(brief|next|step-add|step-claim|step-conclude|step-heartbeat|step-release|step-reclaim|workers|bind|snapshot|show|kickoff|plan|swarm|aid|steer|cost|gate|checkpoint|coverage|doctor|outbox|apply|log|lead|goal|scope|fact-add|hint|relate)\b/;
const HELPERS = /\b(lane_gate|lane_guard|sesscan|handover)\b/;

/**
 * 合法的黑板项目名（与 board 的 pid 口径一致：字母数字开头，只含字母数字 . _ -）。
 * ★ 2026-10-13 加：台账里真的出现过 `$P` / `$(ls` / `===` / `====',` 这些垃圾 pid ——
 *   命令行写成 `fgs kickoff $(ls board/projects/ | tail -1) …` 时，正则只能抓到 shell 替换符，
 *   于是装弹装到了**不存在的项目**上：拦截时回放的派活命令会指向错项目。
 *   现在认不出来就**不装弹**，改由 `parseKickoffOutput` 从 kickoff 的输出里认 pid。
 */
const PID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

/** `fgs kickoff <pid> …` → pid；不是 kickoff / 没有 pid / `--help` / pid 不合法 一律 null */
/**
 * 在 idx 之前是否处于未闭合的引号里（决定这段 `fgs kickoff` 是真命令还是一段被引用的文本）。
 * 不追求完整 shell 解析：只要能把 grep/echo 里**引用别人的命令**这种常见误报挡掉。
 */
function inQuote(s, idx) {
  let q = null;
  for (let i = 0; i < idx; i++) {
    const c = s[i];
    if (q) { if (c === q) q = null; }
    else if (c === '"' || c === "'") q = c;
  }
  return q !== null;
}

export function parseKickoff(command) {
  if (typeof command !== "string" || !command) return null;
  const re = /\bfgs\s+kickoff\s+([^\s;&|]+)/g;
  let m;
  while ((m = re.exec(command)) !== null) {
    // ★ 2026-10-13 修（现场血的教训）：**引号里的不算命令**；引号只在两端成对时才算包着 pid。
    //   以前无条件剥引号，于是引用别人的命令也会装弹：
    //     grep -l "fgs kickoff lw2" *.jsonl  → 抓到 `lw2"`，剥尾引号成 `lw2` → 装到不存在的项目
    //   （本会话真被自己这条 grep 误装弹，7 次调用后被闸门拦住）。
    if (inQuote(command, m.index)) continue;
    const raw = m[1];
    const quoted = (raw.startsWith("\"") && raw.endsWith("\"")) || (raw.startsWith("'") && raw.endsWith("'"));
    const pid = quoted ? raw.slice(1, -1) : raw;
    if (!pid || pid.startsWith("-")) continue;
    if (!PID_RE.test(pid)) continue;   // `$(…)` / 反引号 / 只带半边引号：不装弹
    return pid;
  }
  return null;
}

/**
 * 从 `fgs kickoff` 的**输出**里认 pid（形如 `══━ nrk-web  (kind=ctf, rev=1) ═══`）。
 * 存在的意义：命令行里 pid 是 shell 替换符时（`$(ls board/projects/ | tail -1)`）只能靠输出认。
 * 调用方只在「刚跑过 kickoff」时用它，别拿 `fgs show` 的输出乱认。
 */
export function parseKickoffOutput(text) {
  if (typeof text !== "string" || !text) return null;
  const m = text.match(/══━\s+([A-Za-z0-9][A-Za-z0-9._-]{0,127})\s+\(kind=/);
  return m ? m[1] : null;
}

export function newState() {
  return { pid: null, sinceKickoff: 0, subagentCalls: 0, blocks: 0, done: false, warned: 0 };
}

/** 这次调用是不是「派活的准备工作」 */
export function isLanePlumbing(toolName, input) {
  if (toolName !== "bash" && toolName !== "powershell") return false;
  const cmd = String((input && (input.command || input.cmd)) || "");
  if (!cmd) return false;
  return PLUMBING.test(cmd) || HELPERS.test(cmd);
}

/**
 * 唯一决策函数。返回 `{action: "allow"|"block", state, reason?, warn?}`
 * —— 纯函数（state 进、新 state 出），所以能被测试逐条钉死。
 */
export function decide(state, toolName, input) {
  const st = { ...state };
  if (st.done) return { action: "allow", state: st };
  // 派了活就永久解除（本轮目标达成）
  if (toolName === "subagent") {
    st.subagentCalls += 1;
    st.done = true;
    return { action: "allow", state: st };
  }
  if (!st.pid) return { action: "allow", state: st };                      // 没 kickoff：不关我事
  if (isLanePlumbing(toolName, input)) return { action: "allow", state: st }; // 准备派活：放行且不计数
  st.sinceKickoff += 1;
  if (st.sinceKickoff <= GRACE) return { action: "allow", state: st };
  if (st.blocks < MAX_BLOCKS) {
    st.blocks += 1;
    return { action: "block", state: st };
  }
  st.warned += 1;                                                          // fail-open：放行 + 软提醒
  return { action: "allow", state: st, warn: true };
}

/** 从 kickoff 的输出里抄出「第 1 路 / 第 2 路」那两段，用于事后原样回放 */
export function captureLanes(text) {
  if (typeof text !== "string") return "";
  const i = text.indexOf("── 第 1 路");
  if (i < 0) return "";
  const j = text.indexOf("★ 第 2 轮", i);
  const seg = text.slice(i, j > i ? j : i + 2200);
  return seg.trim().slice(0, 2200);
}

/** 拦截理由（会作为 `block.reason` 回给模型 —— 必须自包含、可直接照做） */
export function buildReason(state, laneText) {
  const pid = state.pid || "<pid>";
  const head = `⛔ 派活闸门（本会话第 ${state.blocks} 次拦截）：题 ${pid} 已经自己走了 `
    + `${state.sinceKickoff} 次工具调用，**还没派任何一路**。`;
  const rule = "规矩（AGENTS §六·七）：kickoff 之后第一轮 = **并发 1~2 路独立分析整题**"
    + " + 主会话自己走最重那条。不许把活全甩出去，**也不许一个人全做完** —— "
    + "难题里多样性是靠不同视角撞同一道题拿到的，不是靠多敲几次命令。";
  const body = (laneText && laneText.includes("subagent("))
    ? `把下面这两条直接发出去（pid 用 ${pid}）：\n${laneText}`
    : `现在就派（下面两条原样可用，只换 pid）：\n`
      + `  subagent({ agent: "ctf-route", context: "fresh", async: true, timeoutMs: 1500000,\n`
      + `             task: "独立分析 ${pid} 整道题：摸形状→找判定点→给解题假设与下一步，不写完整 exp；`
      + `只写文件到 work/${pid}/lanes/lane1.md，不碰黑板；结论走 fgs publish；最后回一行回执 + 产出路径" })\n`
      + `  subagent({ agent: "ctf-route", context: "fresh", async: true, timeoutMs: 1500000,\n`
      + `             task: "独立分析 ${pid} 整道题（**不看 A 的结论**）：同上，写 work/${pid}/lanes/lane2.md" })`;
  const esc = `解除：\`/lanes off\` · 或 \`touch board/projects/${pid}/.no-lanes\`（写清理由）`
    + ` · 硬拦满 ${MAX_BLOCKS} 次后自动放行（只软提醒）`;
  return [head, rule, body, esc].join("\n");
}

/** fail-open 阶段的软提醒（贴在 tool_result 尾巴上） */
export function warnLine(state) {
  return `\n\n⛔ 派活闸门（软提醒）：题 ${state.pid} 仍是 **0 路**（已自己走 ${state.sinceKickoff} 次工具调用）。`
    + ` 派一路再继续：subagent({ agent: "ctf-route", context: "fresh", async: true, task: "独立分析 `
    + `${state.pid} 整道题 … 写 work/${state.pid}/lanes/lane1.md" })`
    + ` ｜解除：/lanes off\n`;
}

// ── 自检（本仓纪律：新工具必须能 `--selftest`）────────────────────────────────
function _selftest() {
  const fails = [];
  const eq = (name, got, want) => {
    const a = JSON.stringify(got), b = JSON.stringify(want);
    if (a !== b) fails.push(`${name}: got ${a} want ${b}`);
  };

  eq("parseKickoff 正常", parseKickoff(`cd $PI_LAB_ROOT && fgs kickoff nrk-web --kind ctf`), "nrk-web");
  eq("parseKickoff 带引号", parseKickoff(`fgs kickoff "mp-1" --kind ctf`), "mp-1");
  // 只带半边引号（= 引用别人的命令）：不算真 kickoff
  eq("parseKickoff 不认 grep 里引用的 kickoff", parseKickoff(`grep -l "fgs kickoff lw2" *.jsonl`), null);
  eq("parseKickoff 不认单边引号", parseKickoff(`echo 'fgs kickoff abc`), null);
  eq("parseKickoff --help 不算", parseKickoff("fgs kickoff --help"), null);
  eq("parseKickoff 非 kickoff", parseKickoff("fgs brief p --as-task"), null);
  // 2026-10-13 修的那批（台账真实出现过）
  eq("parseKickoff 不认 shell 替换 $()", parseKickoff("fgs kickoff $(ls board/projects/ | tail -1) --kind ctf"), null);
  eq("parseKickoff 不认 $P", parseKickoff("fgs kickoff $P --kind ctf"), null);
  eq("parseKickoff 不认 反引号", parseKickoff("fgs kickoff `pwd` --kind ctf"), null);
  eq("parseKickoff 不认 ===", parseKickoff("fgs kickoff === --kind ctf"), null);
  eq("parseKickoff 带路径式 pid 不认", parseKickoff("fgs kickoff board/projects/x --kind ctf"), null);
  // 从输出里补认
  eq("parseKickoffOutput 认 kickoff 横幅", parseKickoffOutput("══━ nrk-web  (kind=ctf, rev=2) ═══\n  ⬜ 未解决"), "nrk-web");
  eq("parseKickoffOutput 没横幅就不认", parseKickoffOutput("随便一段输出"), null);
  eq("plumbing: fgs brief", isLanePlumbing("bash", { command: "fgs brief p --as-task" }), true);
  eq("plumbing: lane_gate", isLanePlumbing("bash", { command: "lane_gate bg 'x'" }), true);
  eq("plumbing: 普通命令不算", isLanePlumbing("bash", { command: "curl http://t/" }), false);
  eq("plumbing: read 不算", isLanePlumbing("read", { command: "" }), false);

  // 没 kickoff：永远放行
  let st = newState();
  for (let i = 0; i < 30; i++) {
    const r = decide(st, "bash", { command: "curl x" });
    st = r.state;
    if (r.action !== "allow") { fails.push("未 kickoff 却拦了"); break; }
  }

  // kickoff 后：GRACE 次放行，第 GRACE+1 次拦
  st = { ...newState(), pid: "p1" };
  let firstBlock = -1;
  for (let i = 1; i <= GRACE + 1; i++) {
    const r = decide(st, "bash", { command: "curl x" });
    st = r.state;
    if (r.action === "block" && firstBlock < 0) firstBlock = i;
  }
  eq("第 GRACE+1 次才拦", firstBlock, GRACE + 1);

  // plumbing 不计数、也不被拦
  st = { ...newState(), pid: "p1", sinceKickoff: GRACE + 5 };
  eq("plumbing 在超线后仍放行", decide(st, "bash", { command: "fgs next p1" }).action, "allow");

  // 派了 subagent → 永久解除
  st = { ...newState(), pid: "p1", sinceKickoff: 99 };
  const r0 = decide(st, "subagent", {});
  eq("subagent 放行", r0.action, "allow");
  eq("派过就 done", r0.state.done, true);
  eq("done 之后任意工具放行", decide(r0.state, "bash", { command: "curl x" }).action, "allow");

  // 拦满 MAX_BLOCKS → 之后 fail-open（放行 + warn）
  st = { ...newState(), pid: "p1", sinceKickoff: GRACE };
  for (let i = 0; i < MAX_BLOCKS; i++) {
    const r = decide(st, "bash", { command: "curl x" });
    st = r.state;
    if (r.action !== "block") fails.push(`第 ${i + 1} 次该拦却放行`);
  }
  const rw = decide(st, "bash", { command: "curl x" });
  eq("拦满后 fail-open 放行", rw.action, "allow");
  eq("fail-open 带 warn", rw.warn, true);

  // 理由必须自包含（含 pid + 可直接照做的 subagent 调用）
  const reason = buildReason({ ...newState(), pid: "p1", blocks: 1, sinceKickoff: 7 }, "");
  for (const need of ["p1", "subagent(", "ctf-route", "AGENTS"]) {
    if (!reason.includes(need)) fails.push(`理由缺 ${need}`);
  }
  const reason2 = buildReason({ ...newState(), pid: "p1", blocks: 2, sinceKickoff: 8 }, captureLanes(SAMPLE));
  if (!reason2.includes("第 1 路")) fails.push("有 laneText 时应原样回放");

  // 抄 lane 命令
  const cap = captureLanes(SAMPLE);
  if (!cap.includes("subagent(") || cap.length > 2200) fails.push("captureLanes 结果不对");

  if (fails.length) {
    console.error("✗ lanewatch 自检失败：\n  - " + fails.join("\n  - "));
    return 1;
  }
  console.log(`OK lanewatch 自检通过（GRACE=${GRACE} / MAX_BLOCKS=${MAX_BLOCKS}，`
    + `${GRACE + 1} 次才拦、plumbing 放行、代理后解除、拦满 fail-open）`);
  return 0;
}

// 假 kickoff 输出（只用于自检：形状与 `fgs kickoff` 打的那段一致）
export const SAMPLE = `
★ 第 1 轮：派 2 路**独立全题分析**（不切分、不互相参考）
  ── 第 1 路 · 独立全题分析 A ──
  subagent({ agent: "worker", context: "fresh", async: true, timeoutMs: 1500000,
             task: "独立分析 target 整道题…" })
  ── 第 2 路 · 独立全题分析 B ──
  subagent({ agent: "worker", context: "fresh", async: true, timeoutMs: 1500000,
             task: "独立分析 target 整道题（**不看 A 的结论**）…" })
  派完后：
    fgs step-add nrk-web "<第1路目标>"
★ 第 2 轮起：**单一、可验证、彼此不重复**的工作项
`;

if (process.argv.includes("--selftest")) process.exit(_selftest());
