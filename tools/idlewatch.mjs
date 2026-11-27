/**
 * idlewatch.mjs —— 「空闲续推」的纯逻辑（零依赖，node 直接跑）
 *
 * 为什么要有它
 * ------------
 * 模型跑长任务最大的敌人是**停**：给出一段阶段性结论后就安静等人。
 * OpenSecurity 2.0 的对策是「随机完成标记 + 没标记就注入 resume」——
 * 但那个标记要 LLM **自己原样吐出来**，等于把「我完成了没」的判断权交给被考核方。
 *
 * 本仓的做法（2026-11-06 用户定案 既定决策）：**按黑板未闭合 Step 数判**。
 * 我们有编排层（board/graph.json），图状态是机器产物、模型改不了；
 * 拿它当判据，比让模型自述「我完成了」可靠一个量级。
 *
 * 三条设计纪律（与 lane-watch 同族）
 * ----------------------------------
 * 1. **fail-open**：读不到图 / 图坏了 → 一律 stop（不续推）。宁可少推，不可乱推。
 * 2. **只认已经开过板的会话**：没见过 `fgs kickoff` 的会话（写代码 / 查资料 / 运维）不碰。
 * 3. **有硬上限**：续推次数 / 连续无进展 双封顶，防止图状态卡死时无限烧钱。
 *
 * 纯函数，不读盘 —— 读盘由调用方（扩展）做，便于离线穷举测试。
 */

/** 默认参数（可按赛制覆盖） */
export const DEFAULTS = {
  maxResumes: 40,        // 单场最多续推次数（比 OpenSecurity 的 80 更保守：一次续推=一轮 token）
  stallLimit: 6,         // 连续 N 次续推后 unclosed 数没变 → 判定「推不动了」，停
  cooldownMs: 0,         // 续推最小间隔（0 = 不限制；限流场景可调大）
};

/**
 * 从 graph.json + outbox 行数算出「还有多少活没闭合」。
 *
 * @param {object} g              graph.json 解析结果
 * @param {number} pendingOutbox  未结算投递条数（outbox.jsonl 行数）
 * @returns {{solved:boolean, bootstrapped:boolean, goals:number, open:number,
 *            claimed:number, verified:number, pendingOutbox:number, unclosed:number}}
 */
export function snapshotFromGraph(g, pendingOutbox) {
  const nodes = (g && g.nodes && typeof g.nodes === "object") ? g.nodes : {};
  let open = 0, claimed = 0, goals = 0, verified = 0, done = 0, abandoned = 0;
  for (const n of Object.values(nodes)) {
    if (!n || typeof n !== "object") continue;
    if (n.type === "step") {
      const s = n.status;
      if (s === "open") open++;
      else if (s === "claimed") claimed++;
      else if (s === "done") done++;
      else if (s === "abandoned") abandoned++;
    } else if (n.type === "goal" && n.status === "active") {
      goals++;
    } else if (n.type === "fact" && n.confidence === "verified") {
      verified++;
    }
  }
  const po = Number(pendingOutbox) || 0;
  return {
    solved: !!(g && g.solved),
    bootstrapped: !!(g && g.bootstrapped),
    goals, open, claimed, verified, done, abandoned,
    pendingOutbox: po,
    unclosed: open + claimed + po,
  };
}

/**
 * 判断这次 settle 该不该把模型推回去。
 *
 * @param {object} snap     snapshotFromGraph 的返回值
 * @param {object} st       续推状态（由调用方维护，本函数只读）
 * @param {object} [opts]   DEFAULTS 的覆盖
 * @returns {{action:"continue"|"stop", why:string, msg?:string, kind:string}}
 *          kind 用于测试与日志归因：solved / aborted / no-goal-none / work / no-work /
 *          capped / stall / bad-graph / no-project
 */
export function decideIdle(snap, st, opts) {
  const o = { ...DEFAULTS, ...(opts || {}) };
  const s = st || {};

  // ── 停机条件（先判停，再判推；顺序错会导致"该停时还在推"）──
  if (s.aborted) {
    return { action: "stop", kind: "aborted", why: "用户按了 Esc（agent_settled.aborted=true）—— 人的意图优先，不复活" };
  }
  if (!snap || typeof snap !== "object") {
    return { action: "stop", kind: "bad-graph", why: "读不到黑板（图缺失/损坏）—— fail-open，不乱推" };
  }
  if (snap.solved) {
    return { action: "stop", kind: "solved", why: "图上已 solved —— 收工" };
  }
  if (Number(s.resumes || 0) >= o.maxResumes) {
    return { action: "stop", kind: "capped", why: `续推已达上限 ${o.maxResumes} 次（防无限烧钱）` };
  }
  if (Number(s.stalled || 0) >= o.stallLimit) {
    return { action: "stop", kind: "stall", why: `连续 ${o.stallLimit} 次续推 unclosed 数没下降 —— 推不动了，交人判断` };
  }
  if (o.cooldownMs > 0 && s.lastAt && (Date.now() - Number(s.lastAt)) < o.cooldownMs) {
    return { action: "stop", kind: "cooldown", why: `距上次续推不足 ${Math.round(o.cooldownMs / 1000)}s（冷却中）` };
  }

  // ── 还没有活可干的情形 ──
  if (snap.goals === 0) {
    return {
      action: "continue", kind: "work",
      why: "图上还没有 Goal",
      msg: "【续推 · 黑板判据】图上还没有 Goal，你还不能停。先 `fgs goal <pid> \"…\"` 立目标，再开工。",
    };
  }
  if (snap.unclosed === 0) {
    if (!snap.bootstrapped) {
      return {
        action: "continue", kind: "work",
        why: "还没 bootstrap（未做过初始整题尝试）",
        msg: "【续推 · 黑板判据】图上有 Goal 但还没 bootstrap。先做一次整题尝试（`fgs next` 会给 bootstrap），把产物落图。",
      };
    }
    return {
      action: "stop", kind: "no-work",
      why: "图上没有未闭合的 Step、没有在跑、没有未结算投递 —— 模型停下是合理的",
    };
  }

  // ── 有活：拼一条能直接照做的续推消息 ──
  const parts = [];
  if (snap.open) parts.push(`${snap.open} 个 open Step 可认领`);
  if (snap.claimed) parts.push(`${snap.claimed} 个 Step 在跑`);
  if (snap.pendingOutbox) parts.push(`${snap.pendingOutbox} 条投递未结算`);
  const detail = parts.length ? parts.join(" / ") : "图上有未闭合工作";

  const nextLine = snap.pendingOutbox
    ? "下一步：先 `fgs apply <pid>` 把投递结算掉（未结算时派新活＝两条结论赛跑）。"
    : snap.open
      ? "下一步：`fgs next <pid>` 会给可认领的 Step；认领后干活，产物 `publish` 到 outbox。"
      : "下一步：`fgs next <pid>` 看该回收还是该等；僵尸 Step 用 `fgs step-reclaim <pid>`。";

  return {
    action: "continue", kind: "work",
    why: `${detail}（已续推 ${Number(s.resumes || 0)} 次）`,
    msg: `【续推 · 黑板判据】你停下了，但黑板显示还有活没闭合：${detail}。\n`
       + `${nextLine}\n`
       + `注：本判断来自 board/graph.json 的机器状态，不是你自述的完成度；`
       + "真做完了就把结论落图（fgs solved 需要 verified Fact 挂图）。",
  };
}

/**
 * 一次续推后更新状态；同时维护「连续无进展」计数。
 * @param {object} st   旧状态
 * @param {object} snap 本次快照
 * @returns {object} 新状态
 */
export function bumpState(st, snap) {
  const s = { ...(st || {}) };
  s.resumes = Number(s.resumes || 0) + 1;
  s.lastAt = Date.now();
  const cur = Number(snap && snap.unclosed) || 0;
  const prev = s.lastUnclosed;
  if (prev !== undefined && prev !== null && cur >= Number(prev)) {
    s.stalled = Number(s.stalled || 0) + 1;
  } else {
    s.stalled = 0;
  }
  s.lastUnclosed = cur;
  return s;
}

// ── 自检（本仓纪律：新工具必须能 `--selftest`）──────────────────────────────
function _selftest() {
  const fail = [];
  const eq = (name, got, want) => {
    const ok = JSON.stringify(got) === JSON.stringify(want);
    if (!ok) fail.push(`${name}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
  };

  // 1) 图快照：各种节点类型/状态都要分别归类
  const g = {
    nodes: {
      g1: { type: "goal", status: "active" },
      g2: { type: "goal", status: "done" },
      s1: { type: "step", status: "open" },
      s2: { type: "step", status: "open" },
      s3: { type: "step", status: "claimed" },
      s4: { type: "step", status: "done" },
      s5: { type: "step", status: "abandoned" },
      f1: { type: "fact", confidence: "verified" },
      f2: { type: "fact", confidence: "inferred" },
    },
    solved: false, bootstrapped: true,
  };
  const snap = snapshotFromGraph(g, 2);
  eq("goals 只算 active", snap.goals, 1);
  eq("open", snap.open, 2);
  eq("claimed", snap.claimed, 1);
  eq("verified 只算 verified", snap.verified, 1);
  eq("pendingOutbox", snap.pendingOutbox, 2);
  eq("unclosed = open+claimed+outbox", snap.unclosed, 5);

  // 2) 空图不炸
  const empty = snapshotFromGraph(null, 0);
  eq("空图 goals", empty.goals, 0);
  eq("空图 unclosed", empty.unclosed, 0);

  // 3) 停机：solved
  eq("solved 停", decideIdle({ solved: true, goals: 1, unclosed: 3 }, {}).action, "stop");
  eq("solved kind", decideIdle({ solved: true }, {}).kind, "solved");

  // 4) 停机：用户 Esc
  const ab = decideIdle({ solved: false, goals: 1, unclosed: 3 }, { aborted: true });
  eq("aborted 停", ab.action, "stop");
  eq("aborted kind", ab.kind, "aborted");

  // 5) 停机：坏图
  eq("坏图停", decideIdle(null, {}).kind, "bad-graph");

  // 6) 停机：上限 / 停滞
  eq("上限停", decideIdle({ goals: 1, unclosed: 3 }, { resumes: 40 }).kind, "capped");
  eq("停滞停", decideIdle({ goals: 1, unclosed: 3 }, { stalled: 6 }).kind, "stall");

  // 7) 停机：图上真没活
  const nw = decideIdle({ goals: 1, unclosed: 0, bootstrapped: true }, {});
  eq("无活停", nw.action, "stop");
  eq("无活 kind", nw.kind, "no-work");

  // 8) 续推：有 open step
  const cw = decideIdle({ goals: 1, open: 2, claimed: 0, pendingOutbox: 0, unclosed: 2, bootstrapped: true }, {});
  eq("有活推", cw.action, "continue");
  eq("有活 kind", cw.kind, "work");
  if (!/2 个 open Step/.test(cw.msg || "")) fail.push("续推消息没带 open 数");
  if (!/fgs next/.test(cw.msg || "")) fail.push("续推消息没给下一步命令");

  // 9) 续推：投递未结算优先（不能边派活边结算）
  const po = decideIdle({ goals: 1, open: 1, pendingOutbox: 3, unclosed: 4, bootstrapped: true }, {});
  if (!/fgs apply/.test(po.msg || "")) fail.push("有未结算投递时应先让 apply");

  // 10) 续推：没 Goal / 没 bootstrap
  eq("没 Goal 推", decideIdle({ goals: 0, unclosed: 0, bootstrapped: false }, {}).kind, "work");
  const nb = decideIdle({ goals: 1, unclosed: 0, bootstrapped: false }, {});
  eq("没 bootstrap 推", nb.action, "continue");
  if (!/bootstrap/.test(nb.msg || "")) fail.push("没 bootstrap 时消息应提 bootstrap");

  // 11) 冷却
  const cd = decideIdle({ goals: 1, unclosed: 3 }, { lastAt: Date.now() }, { cooldownMs: 60000 });
  eq("冷却停", cd.kind, "cooldown");

  // 12) bumpState：进展清零 / 无进展累加
  let st = bumpState({}, { unclosed: 5 });
  eq("首次 resumes", st.resumes, 1);
  eq("首次 stalled", st.stalled, 0);
  st = bumpState(st, { unclosed: 5 });
  eq("同数 stalled+1", st.stalled, 1);
  st = bumpState(st, { unclosed: 3 });
  eq("下降 stalled 清零", st.stalled, 0);
  eq("resumes 累计", st.resumes, 3);

  // 13) 参数可覆盖
  eq("maxResumes 可覆盖", decideIdle({ goals: 1, unclosed: 1 }, { resumes: 5 }, { maxResumes: 5 }).kind, "capped");

  if (fail.length) {
    console.error("FAIL\n  " + fail.join("\n  "));
    return 1;
  }
  console.log("OK idlewatch selftest: 13 组断言全绿");
  return 0;
}

if (process.argv.includes("--selftest")) process.exit(_selftest());
