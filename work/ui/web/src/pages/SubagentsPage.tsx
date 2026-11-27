import { useEffect, useMemo, useState } from "react";
import { api, type Json } from "../api";
import { usePoll } from "../lib/poll";

// #33 分角色监督：一个子代理在跑的时候，用户最想一眼看到的是「哪一路在烧钱、哪一路没产出」。
// 后端 /api/subagents 早就把 role（status.json 的 agent / events.jsonl 的 agentName）、
// tokens、mission（挂在哪条线上）都给了，这里只是把它们**分组成可扫的块**，而不是一堆平铺行。
type Mission = { missionId?: string; summary?: string; status?: string; ownerSessionId?: string } | null;
type Item = {
  runId?: string;
  id?: string;
  name?: string;
  status?: string;
  state?: string;
  model?: string;
  startedAt?: number;
  outcome?: string;
  task?: string;
  agentName?: string;
  agent?: string;
  tokens?: number | null;
  mission?: Mission;
  lastEvent?: { type?: string } | null;
};

type Group = { key: string; items: Item[]; running: number; tokens: number; mission?: Mission };

/** tokens 的显示口径：只在有数时给数，别用 0 冒充「没花」—— 老 run 的 status.json 没有这个字段 */
function fmtTokens(n?: number | null): string {
  if (typeof n !== "number" || !Number.isFinite(n) || n <= 0) return "-";
  return n < 1000 ? String(n) : (n / 1000).toFixed(1) + "k";
}

function groupOf(list: Item[], by: "role" | "mission"): Group[] {
  const map = new Map<string, Group>();
  for (const x of list) {
    const key = by === "role" ? x.agentName || "(未标角色)" : x.mission?.missionId || "(无线路)";
    let g = map.get(key);
    if (!g) {
      g = { key, items: [], running: 0, tokens: 0, mission: by === "mission" ? x.mission || null : undefined };
      map.set(key, g);
    }
    g.items.push(x);
    if ((x.state || x.status || "") === "running") g.running += 1;
    if (typeof x.tokens === "number" && Number.isFinite(x.tokens)) g.tokens += x.tokens;
  }
  // 在跑的多、token 高的排前面：扫视时先看烧钱的那一路
  return [...map.values()].sort((a, b) => b.running - a.running || b.tokens - a.tokens || a.key.localeCompare(b.key));
}

export default function SubagentsPage() {
  const [data, setData] = useState<{ live?: Item[]; history?: Item[]; missions?: Item[] } | null>(null);
  const [policy, setPolicy] = useState<Json | null>(null);
  const [err, setErr] = useState("");
  const [sel, setSel] = useState("");
  const [detail, setDetail] = useState<Json | null>(null);
  const [msg, setMsg] = useState("");
  const [note, setNote] = useState("");
  const [by, setBy] = useState<"role" | "mission">("role");

  // #22：6s 一轮。子代理列表/策略两路 payload 打成一个签名，没变化就不 setState ——
  // 否则每 6 秒重渲染一次列表，正在选中的行与详情滚动位置都会被顶掉。
  const fetchAll = async () => {
    const d = (await api.subagents()) as { live?: Item[]; history?: Item[]; missions?: Item[] };
    const pol = (await api.subagentPolicy()) as Json;
    return { d, pol };
  };
  const applyAll = (v: { d: { live?: Item[]; history?: Item[]; missions?: Item[] }; pol: Json }) => {
    setData(v.d);
    setPolicy(v.pol);
    setErr("");
  };

  const load = async () => {
    try {
      applyAll(await fetchAll());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  usePoll(fetchAll, 6000, applyAll, {
    onError: (e) => setErr(e instanceof Error ? e.message : String(e)),
  });

  useEffect(() => {
    if (!sel) { setDetail(null); return; }
    void api.subagent(sel).then((d) => setDetail(d as Json)).catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [sel]);

  async function say(mode: "steer" | "follow_up") {
    if (!sel || !msg.trim()) return;
    setNote("");
    try {
      const r = (await api.subagentSay(sel, msg.trim(), mode)) as { ok?: boolean; error?: string };
      if (r.error) throw new Error(r.error);
      setNote("已发送（" + mode + "）");
      setMsg("");
      void load(); // 发完立刻刷一次，不等下一轮 6s
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  const pol = (policy?.subagents || {}) as Record<string, unknown>;
  const scope = (pol.modelScope || {}) as { enforce?: boolean; allow?: string[] };
  const live = data?.live || [];
  const groups = useMemo(() => groupOf(live, by), [live, by]);
  const liveTokens = live.reduce((s, x) => s + (typeof x.tokens === "number" ? x.tokens : 0), 0);
  const runningCount = live.filter((x) => (x.state || x.status || "") === "running").length;

  return (
    <div className="page">
      <div className="grid">
        <div className="col">
          {err ? <div className="err">{err}</div> : null}
          <div className="card">
            <h3>运行中（{live.length}）</h3>
            {/* 结果维度优先：先给「在跑几路 / 几个角色 / 烧了多少 token」，再给明细 */}
            <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>
              在跑 {runningCount} 路 · {groups.length} {by === "role" ? "个角色" : "条线路"} · token 合计 {fmtTokens(liveTokens)}
            </div>
            <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
              <button className="mini" onClick={() => setBy("role")} disabled={by === "role"}>按角色</button>
              <button className="mini" onClick={() => setBy("mission")} disabled={by === "mission"}>按线路</button>
            </div>
            <div className="list">
              {live.length === 0 ? <div className="empty">当前没有在跑的子代理</div> : null}
              {groups.map((g) => (
                <div key={g.key} style={{ marginBottom: 10 }}>
                  <div className="muted" style={{ fontSize: 12, display: "flex", gap: 8, marginBottom: 4 }}>
                    <strong style={{ color: "var(--fg, inherit)" }}>{g.key}</strong>
                    <span>{g.items.length} 路 · 在跑 {g.running} · {fmtTokens(g.tokens)}</span>
                  </div>
                  {by === "mission" && g.mission?.summary ? (
                    <div className="muted" style={{ fontSize: 11, marginBottom: 4 }}>{String(g.mission.summary).slice(0, 90)}</div>
                  ) : null}
                  {g.items.map((x) => {
                    const id = x.runId || x.id || "";
                    return (
                      <button key={id} className={"item" + (sel === id ? " on" : "")} onClick={() => setSel(id)}>
                        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                          <span className={"dot " + ((x.state || x.status) === "running" ? "" : "ok")} />
                          <strong style={{ fontSize: 13 }}>{x.agentName || x.name || id}</strong>
                          <span className="muted" style={{ fontSize: 11 }}>{fmtTokens(x.tokens)}</span>
                        </div>
                        <div className="muted">{x.state || x.status || "-"} · {x.model || x.lastEvent?.type || "-"}</div>
                        {x.task ? <div className="muted" style={{ fontSize: 11 }}>{String(x.task).slice(0, 80)}</div> : null}
                      </button>
                    );
                  })}
                </div>
              ))}
            </div>
          </div>
          <div className="card" style={{ maxHeight: 240, overflow: "auto" }}>
            <h3>历史（{(data?.history || []).length}）</h3>
            {(data?.history || []).slice(0, 40).map((x, i) => {
              const id = x.runId || x.id || "";
              return (
                <button key={i} className="item" onClick={() => id && setSel(id)}>
                  <div style={{ fontSize: 12 }}>{x.agentName || x.agent || x.name || id}</div>
                  <div className="muted">{x.outcome || x.status || "-"}</div>
                </button>
              );
            })}
          </div>
        </div>
        <div className="col">
          {note ? <div className="muted">{note}</div> : null}
          <div className="card">
            <h3>策略</h3>
            <dl className="kv">
              <dt>配置</dt><dd className="mono">{String(policy?.path || "-")}</dd>
              <dt>默认</dt><dd>{String(pol.defaultProvider || "-")} / {String(pol.defaultModel || "-")}</dd>
              <dt>思考</dt><dd>{String(pol.defaultThinking || "-")}{pol.disableThinking ? "（禁用）" : ""}</dd>
              <dt>白名单</dt><dd className="mono">{scope.enforce ? "强制" : "不强制"} · {(scope.allow || []).join(", ") || "空"}</dd>
            </dl>
          </div>
          {sel ? (
            <div className="card" style={{ flex: 1, display: "flex", flexDirection: "column" }}>
              <h3>详情 · <span className="mono">{sel}</span></h3>
              <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
                <input style={{ flex: 1 }} placeholder="给这个子代理发指令…" value={msg} onChange={(e) => setMsg(e.target.value)} />
                <button onClick={() => void say("steer")} disabled={!msg.trim()}>插入</button>
                <button onClick={() => void say("follow_up")} disabled={!msg.trim()}>追加</button>
              </div>
              <pre className="pre" style={{ flex: 1, overflow: "auto" }}>{detail ? JSON.stringify(detail, null, 2) : "（加载中）"}</pre>
            </div>
          ) : (
            <div className="card empty">左边选一个子代理看详情 / 发指令</div>
          )}
        </div>
      </div>
    </div>
  );
}