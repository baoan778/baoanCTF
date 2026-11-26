/**
 * reflectwatch.mjs —— 「反思纸条」的纯逻辑（零依赖，node 直接跑）
 *
 * 为什么要有它
 * ------------
 * 长任务里最贵的错误，是**在死路上闷头加速**。OpenSecurity 2.0 的反思机制按
 * 「距上次盘点时长 × 工具调用密度」定期触发（默认 30 分钟）：
 *   · 忙时 → 把一条提醒**附在命令输出之后**（不打断工作）
 *   · 空闲 → 发一条唤醒消息
 * 本仓已有 `skills/fgs-orchestrate` 的纪律，但**没有任何机制在跑偏时提醒**。
 *
 * 本模块只做"该不该提醒"的判断，不碰 IO（读盘/发消息由扩展做），便于离线穷举。
 *
 * 口径（刻意与「墙钟」区分）
 * --------------------------
 * **净活跃时长**才是正确口径：`墙钟 - 等人时长`。
 * 理由：一次权限询问挂 20 分钟，墙钟到点就提醒，但模型其实没跑 —— 那是噪声，
 *       提醒多了就没人看（与 flagq 告警同一个道理）。
 *
 * 三条纪律（与 idlewatch / lane-watch 同族）
 * ------------------------------------------
 * 1. **纯函数**：不读环境、不读盘；同样输入永远同样输出。
 * 2. **不打断**：本模块只产出"纸条文本"，何时贴由扩展决定（挂在 tool_result 后）。
 * 3. **有上限**：单场最多提醒 N 次，防止把"提醒"变成刷屏。
 */

/** 默认参数（可按赛制覆盖） */
export const DEFAULTS = {
  intervalMs: 30 * 60 * 1000,   // 净活跃满 30 分钟触发（OpenSecurity 默认值）
  minToolCalls: 20,             // 且至少这么多工具调用（防"想了 30 分钟没动"误报）
  maxNudges: 8,                 // 单场最多提醒次数
  minGapMs: 30 * 60 * 1000,     // 两次提醒最小间隔（净活跃口径）
};

/** 反思四分类 —— 与 skills/fgs-orchestrate 的口径一致，不另造词 */
export const REFLECT_CHOICES = [
  { key: "继续", hint: "证据在增加、方向没被证伪 → 沿当前攻击面再推一轮" },
  { key: "换向", hint: "同一假设已失败 ≥2 次 → 换**攻击面**（不是换 payload）" },
  { key: "放弃", hint: "已确认走不通 → 写成 Fact 标 abandoned，并写明 WHY" },
  { key: "深挖", hint: "有反常现象但没解释 → 它往往就是突破口，别划过去" },
];

/**
 * 该不该出纸条。
 *
 * @param {object} st   状态：{ activeMs, toolCalls, nudges, lastNudgeActiveMs }
 * @param {object} [opts] DEFAULTS 覆盖
 * @returns {{action:"nudge"|"skip", kind:string, why:string, paper?:string}}
 *          kind 用于日志归因：due / fresh / interval / not-enough-calls / capped / gap
 */
export function decideReflect(st, opts) {
  const o = { ...DEFAULTS, ...(opts || {}) };
  const s = st || {};
  const active = Number(s.activeMs) || 0;
  const calls = Number(s.toolCalls) || 0;
  const nudges = Number(s.nudges) || 0;

  if (active < o.intervalMs) {
    const left = Math.ceil((o.intervalMs - active) / 60000);
    return { action: "skip", kind: "interval", why: `净活跃还差 ${left} 分钟到 ${Math.round(o.intervalMs / 60000)} 分钟` };
  }
  if (nudges >= o.maxNudges) {
    return { action: "skip", kind: "capped", why: `已达提醒上限 ${o.maxNudges} 次` };
  }
  const last = Number(s.lastNudgeActiveMs);
  if (Number.isFinite(last) && last > 0 && (active - last) < o.minGapMs) {
    const left = Math.ceil((o.minGapMs - (active - last)) / 60000);
    return { action: "skip", kind: "gap", why: `距上次提醒还差 ${left} 分钟净活跃` };
  }
  if (calls < o.minToolCalls) {
    return {
      action: "skip", kind: "not-enough-calls",
      why: `净活跃 ${Math.round(active / 60000)} 分钟但只有 ${calls} 次工具调用 —— 多半在思考或等人，不打扰`,
    };
  }

  const mins = Math.round(active / 60000);
  return {
    action: "nudge", kind: "due",
    why: `净活跃 ${mins} 分钟 / ${calls} 次工具调用（第 ${nudges + 1} 次提醒）`,
    paper: buildPaper(mins, calls),
  };
}

/** 纸条正文：短、可扫、带四选一，**不写散文** */
export function buildPaper(mins, calls) {
  const lines = [
    `【反思纸条 · 净活跃 ${mins} 分钟 / ${calls} 次工具调用】`,
    "先落盘本轮实验结果，再做方向盘点（继续 / 换向 / 放弃 / 深挖）：",
  ];
  for (const c of REFLECT_CHOICES) lines.push(`  · ${c.key} —— ${c.hint}`);
  lines.push("判据不是「感觉」，是图上有没有新的 verified Fact：`fgs snapshot <pid>` 看一眼。");
  return lines.join("\n");
}

/**
 * 一次提醒后更新状态。
 * @param {object} st   旧状态
 * @returns {object} 新状态
 */
export function bumpReflect(st) {
  const s = { ...(st || {}) };
  s.nudges = Number(s.nudges || 0) + 1;
  s.lastNudgeActiveMs = Number(s.activeMs) || 0;
  return s;
}

/**
 * 净活跃时长 = 墙钟 - 等人时长（下限 0）。
 * 抽成函数是为了让"口径"这件事只有一个实现点。
 */
export function netActive(wallMs, waitMs) {
  return Math.max(0, (Number(wallMs) || 0) - (Number(waitMs) || 0));
}

// ── 自检（本仓纪律：新工具必须能 `--selftest`）──────────────────────────────
function _selftest() {
  const fail = [];
  const eq = (name, got, want) => {
    if (JSON.stringify(got) !== JSON.stringify(want)) {
      fail.push(`${name}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
    }
  };

  // 1) 净活跃口径：墙钟扣掉等人
  eq("netActive 扣等人", netActive(60 * 60000, 20 * 60000), 40 * 60000);
  eq("netActive 负值归零", netActive(1000, 5000), 0);
  eq("netActive 空输入", netActive(), 0);

  // 2) 未到间隔 → 不提醒
  const a = decideReflect({ activeMs: 10 * 60000, toolCalls: 99 });
  eq("未到间隔 skip", a.action, "skip");
  eq("未到间隔 kind", a.kind, "interval");

  // 3) 到间隔但工具调用太少 → 不打扰（这是"防噪声"的核心）
  const b = decideReflect({ activeMs: 40 * 60000, toolCalls: 3 });
  eq("调用太少 skip", b.action, "skip");
  eq("调用太少 kind", b.kind, "not-enough-calls");

  // 4) 到间隔且调用够 → 提醒，且纸条带四选一
  const c = decideReflect({ activeMs: 40 * 60000, toolCalls: 50 });
  eq("正常触发", c.action, "nudge");
  eq("正常触发 kind", c.kind, "due");
  for (const k of ["继续", "换向", "放弃", "深挖"]) {
    if (!(c.paper || "").includes(k)) fail.push(`纸条缺「${k}」`);
  }
  if (!/净活跃/.test(c.paper || "")) fail.push("纸条没写净活跃口径");
  if (!/fgs snapshot/.test(c.paper || "")) fail.push("纸条没给可执行的判据命令");

  // 5) 上限
  const d = decideReflect({ activeMs: 999 * 60000, toolCalls: 500, nudges: 8 });
  eq("上限 skip", d.kind, "capped");

  // 6) 间隔：刚提醒过不久 → 不再提醒
  const e = decideReflect({ activeMs: 40 * 60000, toolCalls: 50, nudges: 1, lastNudgeActiveMs: 39 * 60000 });
  eq("间隔内 skip", e.kind, "gap");

  // 7) 间隔已过 → 再提醒
  const f = decideReflect({ activeMs: 80 * 60000, toolCalls: 90, nudges: 1, lastNudgeActiveMs: 30 * 60000 });
  eq("间隔已过可再提醒", f.action, "nudge");

  // 8) 参数可覆盖
  eq("intervalMs 可覆盖",
    decideReflect({ activeMs: 5 * 60000, toolCalls: 99 }, { intervalMs: 5 * 60000 }).action, "nudge");
  eq("minToolCalls 可覆盖",
    decideReflect({ activeMs: 40 * 60000, toolCalls: 1 }, { minToolCalls: 1 }).action, "nudge");

  // 9) bumpReflect 记账
  let st = bumpReflect({ activeMs: 40 * 60000 });
  eq("bump nudges", st.nudges, 1);
  eq("bump 记 last", st.lastNudgeActiveMs, 40 * 60000);
  st = bumpReflect(st);
  eq("bump 累加", st.nudges, 2);

  // 10) 纯函数：同输入同输出（不读时间/环境）
  const x1 = decideReflect({ activeMs: 40 * 60000, toolCalls: 50 });
  const x2 = decideReflect({ activeMs: 40 * 60000, toolCalls: 50 });
  eq("纯函数可重复", x1.paper, x2.paper);

  // 11) 空状态不炸
  eq("空状态 skip", decideReflect().action, "skip");

  if (fail.length) {
    console.error("FAIL\n  " + fail.join("\n  "));
    return 1;
  }
  console.log("OK reflectwatch selftest: 11 组断言全绿");
  return 0;
}

if (process.argv.includes("--selftest")) process.exit(_selftest());
