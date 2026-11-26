#!/usr/bin/env node
/**
 * readiness.mjs —— 「就绪计数可对账」的纯逻辑层（X-5，借 OS2.0 控制台的做法）
 *
 * 问题
 * ----
 * 本仓 UI 上散着若干 `ready/limit`、`ok/err` 计数，但：
 *   ① 总数是手写的（加一个工具就漂）；
 *   ② 数字点不开 —— 看见 "4/7" 却不知道缺的是哪 3 个；
 *   ③ N=0（一个都没就绪）时按钮照旧可点，点了没反应 = 最差的体验。
 *
 * OS2.0 控制台的做法（文章 A-10）：`X/Y = Σ(工具 + 包 + 镜像 + 模型 + 配置)`，
 * 悬浮可看每类明细；`N=0` 时禁用而非静默无操作。本模块把这套判据纯函数化。
 *
 * 三条纪律
 * --------
 * 1. **总数由分类算出**，不许外部传进来（传进来就会漂）；
 * 2. **缺项必须指名道姓**（report 里带 name + why），只给数字等于没给；
 * 3. **available 与 ratio 分开**：ratio 是展示，available 是"能不能点"，
 *    二者判据不同（有缺项仍可查看明细，但一个都没有就不该给按钮）。
 */

/**
 * 汇总多分类就绪状态。
 * @param {Array<{key,label,items:Array<{name,ok,why?}>}>} categories
 */
export function summarize(categories) {
  const cats = Array.isArray(categories) ? categories : [];
  let total = 0;
  let ready = 0;
  const out = [];
  const missing = [];
  for (const c of cats) {
    const items = Array.isArray(c && c.items) ? c.items : [];
    const r = items.filter((i) => i && i.ok).length;
    total += items.length;
    ready += r;
    for (const i of items) {
      if (!i || i.ok) continue;
      missing.push({ category: c.key, name: i.name, why: i.why || "" });
    }
    out.push({
      key: c.key,
      label: c.label || c.key,
      total: items.length,
      ready: r,
      ratio: items.length ? r / items.length : 0,
    });
  }
  const ratio = total ? ready / total : 0;
  // available 的语义：**有没有东西可操作**（ready>0）。这与「有没有明细可看」不同 ——
  // 前者决定按钮能不能点（N=0 时点下去只会"没反应"，是最差的体验），
  // 后者由 categories/missing 表达（永远可看，用于排查）。
  // 2026-11-07 第一版把 available 写成 total>0，被测试当场抓出语义错位。
  return {
    total,
    ready,
    ratio,
    hasItems: total > 0,
    available: total > 0 && ready > 0,
    complete: total > 0 && ready === total,
    categories: out,
    missing,
  };
}

/** 某一类的明细（点开那个数字时给什么）。找不到该类返回 null。 */
export function drill(categories, key) {
  const cats = Array.isArray(categories) ? categories : [];
  const c = cats.find((x) => x && x.key === key);
  if (!c) return null;
  const items = Array.isArray(c.items) ? c.items : [];
  return {
    key: c.key,
    label: c.label || c.key,
    total: items.length,
    ready: items.filter((i) => i && i.ok).length,
    items: items.map((i) => ({ name: i && i.name, ok: !!(i && i.ok), why: (i && i.why) || "" })),
  };
}

/** 一行摘要（UI 顶部那行 "4/5 就绪 · 缺 angr"） */
export function oneLine(summary) {
  const s = summary || {};
  if (!s.total) return "就绪 0/0（无检查项）";
  let line = "就绪 " + s.ready + "/" + s.total;
  const miss = s.missing || [];
  if (miss.length) {
    const names = miss.slice(0, 3).map((m) => m.name).join("、");
    line += " · 缺 " + names + (miss.length > 3 ? " 等 " + miss.length + " 项" : "");
  } else {
    line += " · 全部就绪";
  }
  return line;
}

// ── 自检（本仓纪律：新工具必须能 --selftest）────────────────────────────
function _selftest() {
  const fail = [];
  const eq = (n, got, want) => {
    if (JSON.stringify(got) !== JSON.stringify(want)) {
      fail.push(n + ": got " + JSON.stringify(got) + " want " + JSON.stringify(want));
    }
  };

  const cats = [
    { key: "a", label: "A", items: [{ name: "x", ok: true }, { name: "y", ok: true }] },
    { key: "b", label: "B", items: [{ name: "p", ok: true }, { name: "q", ok: false, why: "未装" }] },
  ];
  const s = summarize(cats);
  eq("总数 = 分类之和", s.total, 4);
  eq("就绪数", s.ready, 3);
  eq("ratio", s.ratio, 0.75);
  eq("available", s.available, true);
  eq("complete", s.complete, false);
  eq("缺项数", s.missing.length, 1);
  eq("缺项有名字", s.missing[0].name, "q");
  eq("缺项带类别", s.missing[0].category, "b");
  eq("缺项带原因", s.missing[0].why, "未装");

  const allGreen = summarize([{ key: "k", label: "K", items: [{ name: "z", ok: true }] }]);
  eq("全绿 complete", allGreen.complete, true);
  eq("全绿 ratio=1", allGreen.ratio, 1);
  eq("全绿无缺项", allGreen.missing.length, 0);

  const none = summarize([{ key: "k", label: "K", items: [{ name: "z", ok: false }] }]);
  eq("零就绪 ratio=0", none.ratio, 0);
  eq("零就绪 available=false（按钮不许假装能点）", none.available, false);
  eq("零就绪 hasItems=true（明细照样能看）", none.hasItems, true);

  const empty = summarize([]);
  eq("空分类 total=0", empty.total, 0);
  eq("空分类 ratio=0", empty.ratio, 0);
  eq("空分类 available=false", empty.available, false);
  eq("空分类不崩", typeof empty.categories, "object");

  eq("summarize(null) 不崩", summarize(null).total, 0);

  const d = drill(cats, "b");
  eq("drill 类别", d.key, "b");
  eq("drill 条目数", d.items.length, 2);
  eq("drill 就绪数", d.ready, 1);
  eq("drill 不存在的类 → null", drill(cats, "zz"), null);

  eq("oneLine 带缺项名", oneLine(s).includes("q"), true);
  eq("oneLine 全绿", oneLine(allGreen).includes("全部就绪"), true);
  eq("oneLine 空", oneLine(empty).includes("0/0"), true);

  if (fail.length) {
    for (const f of fail) console.error("FAIL " + f);
    console.error("readiness selftest: " + fail.length + " 组失败");
    return 1;
  }
  console.log("OK readiness selftest: 28 组断言全绿");
  return 0;
}

if (process.argv.includes("--selftest")) process.exit(_selftest());
