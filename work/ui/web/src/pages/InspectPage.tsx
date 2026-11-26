import { useEffect, useState } from "react";
import { api } from "../api";
import { usePoll } from "../lib/poll";
import { requestLocate } from "../lib/locate";

type Row = {
  flag_id: string;
  flag?: string;
  state: string;
  tri: "verified" | "pending" | "refuted";
  at?: string;
  reply?: unknown;
  history?: { state: string; at?: string; by?: string; note?: string | null; correct?: boolean | null }[];
};

type Ledger = {
  pid?: string | null;
  flags?: Row[];
  counts?: { verified?: number; pending?: number; refuted?: number };
  error?: string;
};

const TRI_CN: Record<string, string> = { verified: "已核验", pending: "待核验", refuted: "证伪" };

export default function InspectPage() {
  const [led, setLed] = useState<Ledger | null>(null);
  const [flags, setFlags] = useState<{ entries?: { ts?: string; tool?: string; hits?: { flag?: string; kind?: string; confidence?: string }[] }[]; unique?: { flag?: string }[]; total?: number; error?: string } | null>(null);
  const [policy, setPolicy] = useState<Record<string, unknown> | null>(null);
  const [open, setOpen] = useState<string>("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  // #22：三路 payload 打成一个签名一起比对（任何一路变了才 setState）。
  // 清空记录之后仍要 load() 一次强制刷新，所以保留这个入口。
  const fetchAll = async () => {
    const led = (await api.flagsLedger()) as Ledger;
    const flags = (await api.flags()) as never;
    const pol = (await api.subagentPolicy()) as Record<string, unknown>;
    return { led, flags, pol };
  };
  const applyAll = (d: { led: Ledger; flags: never; pol: Record<string, unknown> }) => {
    setLed(d.led);
    setFlags(d.flags);
    setPolicy(d.pol);
    setErr("");
  };
  const load = async () => {
    try {
      applyAll(await fetchAll());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  usePoll(fetchAll, 8000, applyAll, {
    onError: (e) => setErr(e instanceof Error ? e.message : String(e)),
  });

  /** 一键清空「记录」（2026-11-14）。
   *
   * 清两份业务数据：扫描命中流水（log/flag-watch.jsonl）+ 所有黑板的 flag 账本。
   * **不动安全策略** —— 那是只读配置，不属于「记录」。
   * 服务端是改名归档（.bak-<时间戳>）而不是删除，复盘时还能翻。
   *
   * 双重确认与「会话清零」保持一致：confirm 一次 + 手打 CLEAR。 */
  async function clearRecords() {
    const total = (flags?.total ?? 0) + (led?.flags || []).length;
    if (!total) { setNote("没有记录可清"); return; }
    if (!confirm(`确定清空「记录」？
· 扫描命中 ${flags?.total ?? 0} 条
· flag 账本 ${(led?.flags || []).length} 条
（会改名归档为 .bak-时间戳，可找回）`)) return;
    const typed = prompt("请输入 CLEAR 确认清空（区分大小写）：");
    if (typed !== "CLEAR") { setNote("已取消（未输入 CLEAR）"); return; }
    setBusy(true);
    try {
      const r = (await api.flagsClearAll()) as { error?: string; cleared?: number; failed?: number };
      if (r.error) throw new Error(r.error);
      setNote(`已清空 ${r.cleared ?? 0} 份记录${r.failed ? `，${r.failed} 份失败` : ""}`);
      void load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const c = led?.counts || {};

  return (
    <div className="page">
      {err ? <div className="err">{err}</div> : null}

      {/* 页面级工具条：一键清空作用于「记录」整体（扫描命中 + flag 账本），
          不含下方只读的安全策略。 */}
      <div className="card" style={{ marginBottom: 14, display: "flex", alignItems: "center", gap: 10 }}>
        <strong style={{ fontSize: 13 }}>记录总览</strong>
        <span className="muted">
          账本 {(led?.flags || []).length} 条 · 扫描命中 {flags?.total ?? 0} 条
        </span>
        <span className="spacer" />
        {note ? <span className="muted">{note}</span> : null}
        <button
          className="danger"
          disabled={busy || ((led?.flags || []).length === 0 && (flags?.total ?? 0) === 0)}
          onClick={() => void clearRecords()}
          title="清空扫描命中与 flag 账本（改名归档为 .bak-时间戳，可找回）"
        >
          {busy ? "清空中…" : "一键清空记录"}
        </button>
      </div>

      {/* 三态账本：唯一能回答「这个 flag 到底算不算分」的地方。
          右侧那行计数不是装饰 —— 抢分时先看「已核验」有没有涨。 */}
      <div className="card">
        <h3>
          flag 账本
          <span className="n">
            {led?.pid ? " · " + led.pid : ""}
            {" · 已核验 "}{(c.verified ?? 0)}
            {" / 待核验 "}{(c.pending ?? 0)}
            {" / 证伪 "}{(c.refuted ?? 0)}
          </span>
        </h3>
        {led?.error ? <div className="empty">{led.error}</div> : null}
        <div className="list">
          {(led?.flags || []).map((f) => (
            <div key={f.flag_id} className={"node tri " + f.tri}>
              <div className="trihead">
                <span className={"tribadge " + f.tri}>{TRI_CN[f.tri] || f.tri}</span>
                <span className="mono ell">{f.flag || "(空)"}</span>
                <button className="mini" onClick={() => setOpen(open === f.flag_id ? "" : f.flag_id)}>
                  {open === f.flag_id ? "收起证据" : "展开证据"}
                </button>
                {f.flag ? (
                  // #41：账本里看到的 flag 往往想知道「是哪条命令打出来的」——按输出内容回跳对话页
                  <button className="mini" title="跳到对话页里打过这条 flag 的那条命令（按输出内容匹配）" onClick={() => requestLocate(f.flag || "")}>
                    定位调用
                  </button>
                ) : null}
              </div>
              <div className="muted">{f.at || ""} · 状态 {f.state}{f.reply ? " · 有平台回执" : " · 无回执"}</div>
              {open === f.flag_id ? (
                <div className="prov">
                  <div className="muted">平台原始回执（这是唯一算数的证据）</div>
                  <pre className="pre">{f.reply ? JSON.stringify(f.reply, null, 2) : "（没有回执：这条只是记了账，平台还没说对不对）"}</pre>
                  <div className="muted">账本事件流（append-only，从旧到新）</div>
                  <pre className="pre">{JSON.stringify(f.history || [], null, 2)}</pre>
                </div>
              ) : null}
            </div>
          ))}
          {(led?.flags || []).length === 0 && !led?.error ? <div className="empty">账本是空的：还没记过 flag</div> : null}
        </div>
      </div>

      <div className="card" style={{ marginTop: 14, maxHeight: 420, overflow: "auto" }}>
        <h3>
          扫描命中（{flags?.total ?? 0}）
          <span className="n"> · 只代表「输出里出现过」，不代表算分</span>
        </h3>
        {(flags?.entries || []).slice(-100).reverse().map((e, i) => (
          <div key={i} className="node">
            <div className="muted">{e.ts || ""} · 工具 {e.tool || "-"}</div>
            {(e.hits || []).map((h, j) => (
              <div key={j} className="mono">
                {h.flag || ""} <span className="muted">[{h.confidence || "?"}/{h.kind || "?"}]</span>
                {h.flag ? <button className="mini" onClick={() => requestLocate(h.flag || "")}>定位调用</button> : null}
              </div>
            ))}
          </div>
        ))}
        {(flags?.entries || []).length === 0 ? <div className="empty">还没有命中记录</div> : null}
      </div>

      <div className="card" style={{ marginTop: 14 }}>
        <h3>安全策略（只读）</h3>
        <pre className="pre">{policy ? JSON.stringify(policy, null, 2) : "（无）"}</pre>
      </div>
    </div>
  );
}
