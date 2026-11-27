import { useState } from "react";
import { usePoll } from "../lib/poll";
import { api } from "../api";

/**
 * 「下一刀」卡
 *
 * 为什么要有这一屏：对话页空白态之所以难看，根子不是缺装饰，是**没有决策可做**——
 * 一屏空白 + 一个输入框，等于让人自己从零想「现在该干嘛」。抢分时最贵的就是这段空转。
 *
 * 数据全部来自服务端已有的机器产物（fgs next / graph.json / flag 账本），
 * 这张卡**不猜、不编、不排序玄学**：strength 是服务端按证据种类给的固定值，
 * 每条都带 source，点开能看到它凭什么排在这。
 */

type Move = {
  id: string;
  kind: string;
  title: string;
  why?: string;
  strength?: number;
  source?: string;
};

type Payload = {
  pid?: string | null;
  moves?: Move[];
  context?: { next?: string | null; facts?: number; verified?: number; unsent?: number; counts?: Record<string, number> | null };
  error?: string;
};

const STARS = (n: number) => "●".repeat(Math.max(1, Math.min(3, n))) + "○".repeat(Math.max(0, 3 - Math.max(1, Math.min(3, n))));

type KbHit = { title?: string; cat?: string; path?: string; line?: number | null; score?: number | null };

export default function NextMoveCard({ onPick }: { onPick?: (text: string) => void }) {
  const [data, setData] = useState<Payload | null>(null);
  const [err, setErr] = useState("");
  const [open, setOpen] = useState<string>("");
  // 该轮修复：展开一条时去知识库找同题经验的指针（只查一次，缓存在组件内；
  // 服务端另有 10 分钟缓存，所以重复点不同条也不重复跑进程）
  const [kb, setKb] = useState<Record<string, KbHit[] | "loading" | "none">>({});

  async function lookKb(m: Move) {
    if (kb[m.id] && kb[m.id] !== "none") return;
    setKb((p) => ({ ...p, [m.id]: "loading" }));
    try {
      const q = (m.title || "").slice(0, 80).trim();
      if (!q) { setKb((p) => ({ ...p, [m.id]: "none" })); return; }
      const d = (await api.kb(q, 3)) as { hits?: KbHit[] };
      const hits = d.hits || [];
      setKb((p) => ({ ...p, [m.id]: hits.length ? hits : "none" }));
    } catch {
      setKb((p) => ({ ...p, [m.id]: "none" }));
    }
  }

  // #22：10s 一轮，payload 签名没变就不 setData —— 这张卡每轮无条件重渲染会把
  // 已展开的那一条（open/kb 缓存）连同滚动位置一起顶掉。
  usePoll(
    () => api.moves() as Promise<Payload>,
    10000,
    (d) => {
      setData(d);
      setErr("");
    },
    { onError: (e) => setErr(e instanceof Error ? e.message : String(e)) },
  );

  const moves = data?.moves || [];
  const ctx = data?.context || {};

  return (
    <div className="movecard">
      <div className="mh">
        <span className="mt">下一刀</span>
        {data?.pid ? <span className="mp">黑板 {data.pid}</span> : null}
        {ctx.facts != null ? (
          <span className="ms">
            事实 {ctx.verified ?? 0}/{ctx.facts} 已核验
            {ctx.unsent ? " · 待提交 flag " + ctx.unsent : ""}
          </span>
        ) : null}
      </div>

      {err ? <div className="err">{err}</div> : null}
      {!err && data?.error && moves.length === 0 ? <div className="mempty">{data.error}</div> : null}

      {moves.length > 0 ? (
        <ol className="mlist">
          {moves.map((m) => (
            <li key={m.id} className={"mrow" + (open === m.id ? " open" : "")}>
              <button
                className="mbtn"
                onClick={() => {
                  const next = open === m.id ? "" : m.id;
                  setOpen(next);
                  if (next) void lookKb(m);
                }}
              >
                <span className="mkind">{m.kind}</span>
                <span className="mtitle">{m.title}</span>
                <span className="mstr" title="证据强度（服务端按证据种类给，不可手调）">{STARS(m.strength || 1)}</span>
              </button>
              {open === m.id ? (
                <div className="mwhy">
                  {m.why ? <div>{m.why}</div> : null}
                  {kb[m.id] === "loading" ? <div className="kbhits"><span className="hint">在知识库里找同类经验…</span></div> : null}
                  {Array.isArray(kb[m.id]) ? (
                    <div className="kbhits">
                      <div className="kbtitle">知识库命中 · {kb[m.id].length} 条</div>
                      {(kb[m.id] as KbHit[]).map((h, i) => (
                        <div key={i} className="kbrow">
                          <span className="kbcat">{h.cat || "?"}</span>
                          <span className="kbt">{h.title || ""}</span>
                          <code className="kbpath">{(h.path || "") + (h.line != null ? ":" + h.line : "")}</code>
                        </div>
                      ))}
                    </div>
                  ) : null}
                  {kb[m.id] === "none" ? <div className="kbhits"><span className="hint">知识库没有明显相关的条目</span></div> : null}
                  <div className="muted">
                    来源 <code>{m.source || "?"}</code>
                    {onPick ? (
                      <>
                        {" · "}
                        <a
                          href="#"
                          onClick={(e) => { e.preventDefault(); onPick(m.title); }}
                        >
                          填进输入框
                        </a>
                      </>
                    ) : null}
                  </div>
                </div>
              ) : null}
            </li>
          ))}
        </ol>
      ) : null}

      {moves.length === 0 && !data?.error ? <div className="mempty">正在读黑板…</div> : null}
    </div>
  );
}
