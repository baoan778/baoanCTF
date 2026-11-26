import { useEffect, useMemo, useState } from "react";
import { api } from "../api";

// #43 技能页要显示「本次会话调用了几次」：calls=0 就是「未命中」（技能在，但这次没用上）。
// 数字来自服务端会话 JSONL 的 toolResult 聚合，所以界面重启后不会归零。
type Skill = { id: string; name: string; description?: string; path?: string; calls?: number };

export default function SkillsPage() {
  const [list, setList] = useState<Skill[]>([]);
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<Skill | null>(null);
  const [err, setErr] = useState("");
  const [callsTotal, setCallsTotal] = useState(0);

  useEffect(() => {
    void api
      .skills()
      .then((d) => {
        const o = d as { skills?: Skill[]; callsTotal?: number };
        setList(o.skills || []);
        setCallsTotal(typeof o.callsTotal === "number" ? o.callsTotal : 0);
      })
      .catch((e) => setErr(String(e)));
  }, []);

  const shown = useMemo(() => {
    const k = q.trim().toLowerCase();
    if (!k) return list;
    return list.filter(
      (s) =>
        s.id.toLowerCase().includes(k) ||
        (s.description || "").toLowerCase().includes(k),
    );
  }, [list, q]);

  // 未命中清单：会话里出现过工具调用，但这个名字一次都没被调用过。
  // 只在真的有调用记录时标（callsTotal=0 说明会话里还没有工具调用，标「未调用」会误导）。
  const missed = useMemo(
    () => (callsTotal > 0 ? list.filter((s) => !s.calls).length : 0),
    [list, callsTotal],
  );

  return (
    <div className="page">
      <div className="grid">
        <div className="col">
          <div className="card">
            <h3>技能（{list.length}）</h3>
            <div className="muted" style={{ marginBottom: 8 }}>
              本次会话工具调用 {callsTotal} 次
              {missed > 0 ? " · 没被调用的技能 " + missed + " 个" : ""}
            </div>
            <input placeholder="搜索 id / 描述" value={q} onChange={(e) => setQ(e.target.value)} />
            <div className="list" style={{ marginTop: 10 }}>
              {shown.map((s) => (
                <button
                  key={s.id}
                  className={"item" + (sel?.id === s.id ? " on" : "")}
                  onClick={() => setSel(s)}
                >
                  <div>
                    {s.name || s.id}
                    {typeof s.calls === "number" && callsTotal > 0 ? (
                      <span className="muted mono" title="本次会话里这个技能的调用次数（会话 JSONL 统计）">
                        {s.calls > 0 ? " ×" + s.calls : " 未调用"}
                      </span>
                    ) : null}
                  </div>
                </button>
              ))}
            </div>
          </div>
        </div>
        <div className="col">
          {err ? <div className="err">{err}</div> : null}
          <div className="card" style={{ flex: 1, overflow: "auto" }}>
            {!sel ? (
              <div className="empty">左边选一个技能看说明</div>
            ) : (
              <>
                <h3>{sel.name || sel.id}</h3>
                <div className="muted mono">{sel.path || ""}</div>
                <div className="muted" style={{ marginTop: 6 }}>
                  本次会话调用 {sel.calls || 0} 次
                </div>
                <p className="body" style={{ marginTop: 10 }}>
                  {sel.description || "（没有描述）"}
                </p>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}