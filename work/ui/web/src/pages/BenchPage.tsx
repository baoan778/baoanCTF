import { useState } from "react";
import { api } from "../api";
import { usePoll } from "../lib/poll";

/** 跑分实况（2026-11-19 新增）。
 *
 * 为什么要有这一页：之前 10 个页签全是「手工对话驱动 pi」的工具，而限时赛真正在跑的是
 * parallel_solver → sdkshard → pi 子进程这条链路 —— 它一个字都不进 UI。结果就是
 * 「界面很空、看不见工作状态」，只能在终端 tail 日志。
 *
 * 数据来自 /api/bench（tools/bench/live.py 聚合：平台题单 + ledger.json + shard 日志）。
 * ★ 渲染纪律（全仓一致）：拿不到就写「—」「?」并给 title 说明，**绝不把失败渲染成 0**。
 *   平台抖 409 是常态，整页空白比显示「不知道」更糟。
 */

type ByDiff = { done?: number; n?: number; score?: number; total?: number; spent?: number };
type Left = { code: string; diff: string; score: number; attempts: number; spent: number; budget: number; rejected: number; roi: number | null };
type Snapshot = {
  ts?: number;
  net?: boolean;
  platform_error?: string;
  error?: string;
  stale_ms?: number;
  /** live = 本次真从平台拉到；cache = 平台不可用但有本地快照；none = 连快照都没有（真空） */
  platform_source?: "live" | "cache" | "none";
  platform_age_sec?: number;
  score?: number;
  total?: number;
  done?: number;
  count?: number;
  by_difficulty?: Record<string, ByDiff>;
  remaining?: Left[];
  containers?: string[];
  procs?: {
    shards?: { pid: string; etime: string }[];
    controller?: string | null;
    /** 守护进程（supervise.py）：解题链路挂掉时负责重启，它不在 = 出事后没人拉起 */
    supervisor?: { pid: number; etime: number } | null;
    agents?: number;
    /** 解题 pi 子进程的 pid（已排除 UI 那个常驻 pi） */
    agent_pids?: number[];
  };
  work?: { last_started?: { code?: string; addrs?: string; last?: string } };
  recent_flags?: { code?: string | null; flag?: string; awarded?: number }[];
};

const DIFF_CN: Record<string, string> = { easy: "简单", medium: "中等", hard: "困难" };
const DIFF_ORDER = ["easy", "medium", "hard"];

function fmtAge(sec: number) {
  if (sec < 90) return sec + " 秒前";
  if (sec < 5400) return Math.round(sec / 60) + " 分钟前";
  return (Math.round(sec / 360) / 10) + " 小时前";
}

function pct(a?: number, b?: number) {
  if (!b) return null;
  return Math.round(((a || 0) / b) * 1000) / 10;
}

export default function BenchPage() {
  const [s, setS] = useState<Snapshot | null>(null);
  const [err, setErr] = useState("");
  const [tick, setTick] = useState(0);

  // 3 秒轮询与后端缓存同频；后端自己有 3s 缓存，这里再密也没用。
  usePoll(
    () => api.bench() as Promise<Snapshot>,
    3000,
    (d) => { setS(d); setErr(""); setTick((n) => n + 1); },
    { onError: (e) => setErr(e instanceof Error ? e.message : String(e)) },
  );

  const bd = s?.by_difficulty || {};
  const left = s?.remaining || [];
  const p = pct(s?.score, s?.total);
  const shards = s?.procs?.shards || [];
  const cur = s?.work?.last_started;
  const fresh = s?.stale_ms ? Math.round(s.stale_ms / 1000) : null;
  // 平台拿不到但本地有快照时的「这是多久前的数」提示（后端 platform_source=cache）
  const staleAge = s?.platform_source === "cache" && s?.platform_age_sec
    ? fmtAge(s.platform_age_sec)
    : null;

  return (
    <div className="page">
      {err ? <div className="err">{err}</div> : null}
      {s?.platform_error ? (
        <div className={staleAge ? "warn" : "err"}
             title="平台 API 这次没拉到（409/超时是常态）；下面数字来自本地快照，仍可照常判断进度">
          平台题单暂不可用：{s.platform_error}
          {staleAge ? ` · 下面显示的是 ${staleAge}的本地快照（不是实时）` : ""}
          {fresh !== null ? `（本地快照 ${fresh}s 前）` : ""}
        </div>
      ) : null}

      {/* ① 总分条：抢分时第一眼要看的就是它 */}
      <div className="card" style={{ marginBottom: 14 }}>
        <h3>
          总分
          {staleAge ? <span className="pill warn" title="平台不可用，这是本地快照的数">快照 {staleAge}</span> : null}
          <span className="n">
            {s ? `${s.score ?? "—"} / ${s.total ?? "—"} 分 · 完成 ${s.done ?? "—"}/${s.count ?? "—"}` : "加载中…"}
          </span>
        </h3>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{ flex: 1, height: 10, background: "var(--panel-3)", borderRadius: 999, overflow: "hidden" }}>
            <div style={{ width: `${p ?? 0}%`, height: "100%", background: "var(--accent)", transition: "width .4s" }} />
          </div>
          <strong className="mono" style={{ fontSize: 18 }}>{p === null ? "—" : p + "%"}</strong>
        </div>
      </div>

      {/* ② 分难度：主战场在哪一档，决定下一刀往哪砍 */}
      <div className="card" style={{ marginBottom: 14 }}>
        <h3>分难度<span className="n"> · 已用时是该档通关题的累计秒数（ledger 口径）</span></h3>
        <div className="list">
          {DIFF_ORDER.map((d) => {
            const v = bd[d];
            if (!v) return null;
            const dp = pct(v.score, v.total);
            return (
              <div key={d} className="node" style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <span className="pill">{DIFF_CN[d] || d}</span>
                <span className="mono" style={{ minWidth: 130 }}>
                  {v.done ?? 0}/{v.n ?? 0} 题 · {v.score ?? 0}/{v.total ?? 0} 分
                </span>
                <div style={{ flex: 1, height: 6, background: "var(--panel-3)", borderRadius: 999, overflow: "hidden" }}>
                  <div style={{ width: `${dp ?? 0}%`, height: "100%", background: d === "hard" ? "var(--bad)" : d === "medium" ? "var(--gold)" : "var(--accent)" }} />
                </div>
                <span className="muted mono">{dp === null ? "—" : dp + "%"}</span>
                <span className="muted mono" title="该档已通关题累计耗时">{v.spent ? `${v.spent}s` : "—"}</span>
              </div>
            );
          })}
          {!Object.keys(bd).length ? <div className="empty">暂无（平台题单没拿到）</div> : null}
        </div>
      </div>

      {/* ③ 谁在跑：进程 / 容器 / 当前题 —— 这一段是「工作状态」的核心 */}
      <div className="card" style={{ marginBottom: 14 }}>
        <h3>谁在跑<span className="n"> · 每 {3}s 刷新（第 {tick} 次）</span></h3>
        <div className="row wrap" style={{ gap: 18, marginBottom: 10 }}>
          <span className="row">
            <span className={"dot " + (shards.length ? "ok" : "bad")} />
            <span>shard {shards.length} 路</span>
            <span className="muted mono">{shards.map((x) => `#${x.pid} ${x.etime}`).join(" · ") || "没有在跑的解题进程"}</span>
          </span>
          <span className="row">
            <span className={"dot " + ((s?.containers || []).length ? "ok" : "")} />
            <span>容器 {(s?.containers || []).length}</span>
            <span className="muted mono">{(s?.containers || []).join(" ") || "—"}</span>
          </span>
          <span className="row">
            <span className={"dot " + ((s?.procs?.agents || 0) > 0 ? "ok" : "")} />
            <span>pi 子进程 {s?.procs?.agents ?? "—"}</span>
            <span className="muted mono">
              {(s?.procs?.agent_pids || []).map((x) => "#" + x).join(" ") || "—"}
            </span>
          </span>
          <span className="row">
            {/* 守护节点：它不在 = 链路挂了以后没人自动拉起（优化 #7 那课） */}
            <span className={"dot " + (s?.procs?.supervisor ? "ok" : "bad")} />
            <span>守护</span>
            <span className="muted mono">
              {s?.procs?.supervisor ? `#${s.procs.supervisor.pid} ${s.procs.supervisor.etime}s` : "未运行"}
            </span>
          </span>
        </div>
        <div className="node">
          <div className="muted">当前/最近启动的题</div>
          <div className="mono">{cur?.code || "—"}{cur?.addrs ? <span className="muted"> {cur.addrs}</span> : null}</div>
          {cur?.last ? <div className="muted ell" title={cur.last}>{cur.last}</div> : null}
        </div>
      </div>

      {/* ④ 最近出旗：抢分的正反馈，也是「策略有效吗」的证据 */}
      <div className="card" style={{ marginBottom: 14 }}>
        <h3>最近出旗<span className="n"> · 平台回执 awarded 为准</span></h3>
        <div className="list">
          {(s?.recent_flags || []).map((f, i) => (
            <div key={i} className="node" style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <span className="pill ok">+{f.awarded ?? "?"}</span>
              <span className="mono" style={{ minWidth: 110 }}>{f.code || "?"}</span>
              <span className="mono ell" title={f.flag}>{f.flag}</span>
            </div>
          ))}
          {!(s?.recent_flags || []).length ? <div className="empty">还没有回执</div> : null}
        </div>
      </div>

      {/* ⑤ 待解队列：直接照抄就是下一刀（ROI 已排序） */}
      <div className="card">
        <h3>
          待解 {left.length}
          <span className="n"> · 按难度/尝试次数排；预算 = 下一轮该给的秒数</span>
        </h3>
        <div className="list" style={{ maxHeight: 420, overflow: "auto" }}>
          {left.map((r) => (
            <div key={r.code} className="node" style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <span className={"pill " + (r.diff === "hard" ? "bad" : r.diff === "medium" ? "warn" : "ok")}>{DIFF_CN[r.diff] || r.diff}</span>
              <span className="mono" style={{ minWidth: 110 }}>{r.code}</span>
              <span className="muted mono" style={{ minWidth: 56 }}>{r.score} 分</span>
              <span className="muted mono" style={{ minWidth: 84 }} title="已尝试次数 / 累计耗时">
                试 {r.attempts} · {r.spent}s
              </span>
              <span className="muted mono" style={{ minWidth: 66 }} title="下一轮预算">预算 {r.budget}s</span>
              {r.rejected ? <span className="pill bad" title="有候选中过平台判错">被拒 {r.rejected}</span> : null}
              <span className="spacer" />
              <span className="muted mono" title="分值 ÷ 累计耗时">{r.roi === null ? "未跑" : "ROI " + r.roi}</span>
            </div>
          ))}
          {!left.length ? <div className="empty">{s?.net ? "全部通关，或平台题单暂不可用" : "平台不可用，队列未知"}</div> : null}
        </div>
      </div>
    </div>
  );
}
