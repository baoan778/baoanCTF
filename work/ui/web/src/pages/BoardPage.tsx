import { useEffect, useState } from "react";
import { api } from "../api";

// 列表页摘要：本轮新能力（server.mjs capsBrief 派生，拿不到就是 null）
type CapsBrief = {
  difficulty: string | null;
  deadUnverified: number;
  undeclaredOrigin: number;
  simulateConflict: number;
  dimsNever: string[];
} | null;

// 详情页全量（server.mjs newCapabilities 派生）
type CapsFull = {
  difficulty: string | null;
  difficultyKnown: boolean;
  probe: { deadUnverified: { id: string; missing: string[]; claim: string }[]; deadComplete: number; total: number };
  origin: { undeclared: { id: string; claim: string }[]; declared: number; total: number };
  simulate: { conflicts: { id: string; markers: string[]; claim: string }[]; total: number };
  dimensions: { counts: Record<string, number>; never: string[] };
} | null;

type Proj = {
  pid: string; kind?: string; revision?: number; updated_at?: string;
  goal?: string; nodes?: number; edges?: number; verified?: number;
  counts?: Record<string, number>;
  caps?: CapsBrief;
};
type Node = { id: string; type?: string; text?: string; status?: string; confidence?: string; created_at?: string };
type LogRow = { id?: string; op?: string; at?: string; actor?: string; text?: string; flag_id?: string; correct?: boolean };
type Detail = {
  pid?: string;
  graph?: { nodes?: Record<string, Node>; edges?: unknown[]; solved?: unknown };
  log?: LogRow[];
  wp?: { name?: string } | null;
  outbox?: { pending?: number };
  lanes?: { name?: string; closed?: boolean }[];
  caps?: CapsFull;
  error?: string;
};

/**
 * 黑板页（三栏信息架构，W-P1-3）
 *
 * 旧版问题：左窄右空 + 状态靠人认 —— 节点列表把 type/status/confidence 糊成一行灰字，
 * 一眼看不出哪条是结论、哪条还没盖章；中间栏把概况/节点/日志竖着堆，滚动一拉就丢上下文。
 *
 * 现在按「先看结论 -> 再看行动 -> 最后翻日志」切三栏：
 *   左 300：题目清单（每题一个状态点 + 已核验/全部）
 *   中 1fr：事实流，按三态分槽（已核验 / 待盖章 / 其它）
 *   右 320：概况 + 操作日志（最近 40，时间只留时分秒）
 * 三栏各自滚动，互不把对方顶走。
 */
const TRI: Record<string, string> = { verified: "ok", inferred: "warn", unverified: "warn" };

function NodeRow({ n }: { n: Node }) {
  const tri = TRI[n.status || ""] || "";
  return (
    <div className={"node ks " + tri}>
      <div className="ks-head">
        <span className={"ks-type " + (n.type || "")}>{n.type || "?"}</span>
        <span className="ks-id">{n.id}</span>
        <span className={"ks-state " + tri}>{n.status || "-"}</span>
      </div>
      <div className="ks-text">{n.text || ""}</div>
    </div>
  );
}

export default function BoardPage() {
  const [projects, setProjects] = useState<Proj[]>([]);
  const [sel, setSel] = useState<string>("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    void api.board()
      .then((d) => {
        const ps = ((d as { projects?: Proj[] }).projects || []) as Proj[];
        setProjects(ps);
        if (ps.length && !sel) setSel(ps[0].pid);
      })
      .catch((e) => setErr(String(e)));
  }, []);

  useEffect(() => {
    if (!sel) return;
    setDetail(null);
    void api.boardDetail(sel).then((d) => setDetail(d as Detail)).catch((e) => setErr(String(e)));
  }, [sel]);

  const nodes = Object.values(detail?.graph?.nodes || {}) as Node[];
  const edges = (detail?.graph?.edges || []) as unknown[];
  const facts = nodes.filter((n) => n.type === "fact");
  const verified = facts.filter((n) => n.status === "verified");
  const pending = facts.filter((n) => n.status !== "verified");
  const goals = nodes.filter((n) => n.type === "goal");
  const others = nodes.filter((n) => n.type !== "fact" && n.type !== "goal");

  return (
    <div className="page">
      <div className="grid k3">
        <div className="col">
          <div className="card fit">
            <h3>题目<span className="n"> · {projects.length}</span></h3>
            <div className="list">
              {projects.length === 0 ? <div className="empty">黑板还是空的</div> : null}
              {projects.map((p) => (
                <button key={p.pid} className={"item" + (sel === p.pid ? " on" : "")} onClick={() => setSel(p.pid)}>
                  <div className="it-line">
                    <span className={"dot " + ((p.verified ?? 0) > 0 ? "ok" : "")} />
                    <span className="it-name">{p.pid}</span>
                    {/* T16：开局定级。未定级显式标出来 —— 那是「没做」不是「简单」 */}
                    <span className="it-n" title="开局定级 (fgs diff)">
                      {p.caps?.difficulty || "未定级"}
                    </span>
                    <span className="it-n">{p.verified ?? 0}/{p.nodes ?? 0}</span>
                  </div>
                  <div className="muted ells">{p.goal || "（无目标）"}</div>
                  {/* 新能力告警：有盲区/未标来源/推演矛盾时点出来，没有就不占位 */}
                  {p.caps && (p.caps.deadUnverified > 0 || p.caps.undeclaredOrigin > 0 || p.caps.simulateConflict > 0) ? (
                    <div className="muted ells" style={{ color: "var(--warn, #b8860b)" }}>
                      {[
                        p.caps.deadUnverified > 0 ? `死路缺要素 ${p.caps.deadUnverified}` : "",
                        p.caps.undeclaredOrigin > 0 ? `来源未标 ${p.caps.undeclaredOrigin}` : "",
                        p.caps.simulateConflict > 0 ? `推演矛盾 ${p.caps.simulateConflict}` : "",
                      ].filter(Boolean).join(" · ")}
                    </div>
                  ) : null}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="col">
          {err ? <div className="err">{err}</div> : null}
          {!detail ? (
            <div className="card empty">{sel ? "加载中…" : "左边选一道题"}</div>
          ) : (
            <div className="card fit">
              <h3>
                事实流
                <span className="n">{" · 已核验 "}{verified.length}{" / 待盖章 "}{pending.length}{" · 边 "}{edges.length}</span>
              </h3>
              <div className="list">
                {goals.length > 0 ? (
                  <>
                    <div className="group">目标</div>
                    {goals.map((n) => <NodeRow key={n.id} n={n} />)}
                  </>
                ) : null}
                {verified.length > 0 ? (
                  <>
                    <div className="group ok">{"已核验 · 算结论（"}{verified.length}{"）"}</div>
                    {verified.map((n) => <NodeRow key={n.id} n={n} />)}
                  </>
                ) : null}
                {pending.length > 0 ? (
                  <>
                    <div className="group warn">{"待盖章 · 补一条逐字溯源就能用（"}{pending.length}{"）"}</div>
                    {pending.map((n) => <NodeRow key={n.id} n={n} />)}
                  </>
                ) : null}
                {others.length > 0 ? (
                  <>
                    <div className="group">其它</div>
                    {others.map((n) => <NodeRow key={n.id} n={n} />)}
                  </>
                ) : null}
                {/* 空白态是【决策】不是【等待】：demo 板上只有一条目标、没有事实——
                    此时最该说的是下一步做什么，而不是留一屏空气。
                    这一段只在真的没有 fact 时出现，有了事实立刻消失。 */}
                {facts.length === 0 ? (
                  <div className="empty kbhint">
                    <div>还没有可算结论的事实。</div>
                    <div className="hint">
                      下一步：跑一条命令拿到结果，再 <code>fgs verify {sel || "&lt;pid&gt;"} &lt;fact&gt; --from &lt;原始输出&gt;</code> 逐字溯源。
                      只有 verified 能进结论。
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
          )}
        </div>

        <div className="col">
          <div className="card">
            <h3>概况</h3>
            <dl className="kv">
              <dt>pid</dt><dd className="mono">{detail?.pid || sel || "—"}</dd>
              <dt>节点 / 边</dt><dd>{nodes.length} / {edges.length}</dd>
              <dt>在跑</dt><dd>{(detail?.lanes || []).filter((l) => !l.closed).length} 路</dd>
              <dt>待投递</dt><dd>{detail?.outbox?.pending ?? 0}</dd>
              <dt>WP</dt><dd>{detail?.wp?.name || "未写"}</dd>
              <dt>开局定级</dt>
              <dd>{detail?.caps?.difficulty || <span className="muted">未定级（fgs diff 可设）</span>}</dd>
            </dl>
          </div>

          {/* ★ 本轮新能力（2026-11-11）。此前 fgs 有、UI 没有 = 赛时等于没做。
              纪律：拿不到就显示「—」，不用 0 假装有数。 */}
          <div className="card fit">
            <h3>审计<span className="n"> · 盲区 / 来源 / 推演 / 六维</span></h3>
            {!detail?.caps ? (
              <div className="empty">这块板读不到派生信息</div>
            ) : (
              <dl className="kv">
                <dt>死路缺要素</dt>
                <dd>
                  {detail.caps.probe.total === 0 ? <span className="muted">无死路结论</span> : (
                    <span style={{ color: detail.caps.probe.deadUnverified.length ? "var(--warn, #b8860b)" : undefined }}>
                      {detail.caps.probe.deadUnverified.length > 0
                        ? `${detail.caps.probe.deadUnverified.length} 条未写全三要素`
                        : `${detail.caps.probe.deadComplete} 条全写全`}
                    </span>
                  )}
                </dd>
                <dt>来源未标</dt>
                <dd>
                  {detail.caps.origin.total === 0 ? <span className="muted">无 verified 结论</span> : (
                    <span style={{ color: detail.caps.origin.undeclared.length ? "var(--warn, #b8860b)" : undefined }}>
                      {detail.caps.origin.undeclared.length > 0
                        ? `${detail.caps.origin.undeclared.length}/${detail.caps.origin.total} 条未标 local/remote`
                        : `${detail.caps.origin.declared} 条全标了`}
                    </span>
                  )}
                </dd>
                <dt>推演矛盾</dt>
                <dd>
                  {detail.caps.simulate.total === 0
                    ? <span className="muted">无</span>
                    : <span style={{ color: "var(--warn, #b8860b)" }}>{detail.caps.simulate.total} 条自述推演却未标 local</span>}
                </dd>
                <dt>六维未出现</dt>
                <dd>
                  {detail.caps.dimensions.never.length === 0
                    ? <span className="muted">六个维度都有痕迹</span>
                    : <span style={{ color: "var(--warn, #b8860b)" }}>{detail.caps.dimensions.never.join("、")}</span>}
                </dd>
              </dl>
            )}
            {detail?.caps && (detail.caps.probe.deadUnverified.length > 0 || detail.caps.origin.undeclared.length > 0 || detail.caps.simulate.conflicts.length > 0) ? (
              <div className="hint" style={{ marginTop: 8 }}>
                {detail.caps.probe.deadUnverified.length > 0 ? (
                  <div>死路未写全：{detail.caps.probe.deadUnverified.slice(0, 3).map((r) => `${r.id}(缺${r.missing.join("/")})`).join("，")}</div>
                ) : null}
                {detail.caps.origin.undeclared.length > 0 ? (
                  <div>来源未标：{detail.caps.origin.undeclared.slice(0, 3).map((r) => r.id).join("，")} · <code>fgs fact-add ... --origin local|remote</code></div>
                ) : null}
                {detail.caps.simulate.conflicts.length > 0 ? (
                  <div>推演矛盾：{detail.caps.simulate.conflicts.slice(0, 2).map((r) => `${r.id}(${r.markers.join("/")})`).join("，")}</div>
                ) : null}
              </div>
            ) : null}
          </div>
          <div className="card fit">
            <h3>操作日志<span className="n"> · 最近 40</span></h3>
            <div className="loglist">
              {(detail?.log || []).slice(-40).reverse().map((l, i) => (
                <div key={i} className="logrow">
                  <span className="mono at">{(l.at || "").slice(11, 19)}</span>
                  <span className="op">{l.op || "?"}</span>
                  <span className="txt ell">{l.text || l.flag_id || ""}</span>
                </div>
              ))}
              {(detail?.log || []).length === 0 ? <div className="empty">还没有日志</div> : null}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
