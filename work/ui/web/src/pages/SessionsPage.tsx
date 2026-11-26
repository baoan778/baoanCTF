import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { usePoll } from "../lib/poll";

type Sess = { path?: string; fileName?: string; id?: string; title?: string; mtimeMs?: number; size?: number; messages?: number; cwd?: string; createdAt?: number };
type Rt = { runtimeId: string; sessionPath?: string; pid?: number; busy?: boolean; idleMs?: number };

function fmt(ms?: number): string {
  if (!ms) return "-";
  const d = new Date(ms);
  const p = (x: number) => String(x).padStart(2, "0");
  return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
}

export default function SessionsPage() {
  const [list, setList] = useState<Sess[]>([]);
  const [rts, setRts] = useState<Rt[]>([]);
  const [active, setActive] = useState("");
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [tree, setTree] = useState<unknown>(null);
  const [treeErr, setTreeErr] = useState("");
  const [forkAt, setForkAt] = useState("");
  const [busy, setBusy] = useState(false);

  // #22：两路 payload 打成一个签名；没变化就不 setState，免得选中的行/滚动位置被顶掉。
  const fetchAll = useCallback(async () => {
    const s = (await api.sessions()) as { sessions?: Sess[]; error?: string };
    if (s.error) throw new Error(s.error);
    const r = (await api.runtimes()) as { active?: string; runtimes?: Rt[] };
    return { sessions: s.sessions || [], active: r.active || "", runtimes: r.runtimes || [] };
  }, []);

  const applyAll = useCallback(
    (d: { sessions: Sess[]; active: string; runtimes: Rt[] }) => {
      setList(d.sessions);
      setActive(d.active);
      setRts(d.runtimes);
      setErr("");
    },
    [],
  );

  const load = useCallback(async () => {
    try {
      applyAll(await fetchAll());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, [fetchAll, applyAll]);

  usePoll(fetchAll, 8000, applyAll, {
    onError: (e) => setErr(e instanceof Error ? e.message : String(e)),
  });

  const loadTree = useCallback(async () => {
    setTreeErr("");
    try {
      const d = (await api.tree()) as { error?: string };
      if (d.error) throw new Error(d.error);
      setTree(d);
    } catch (e) {
      setTree(null);
      setTreeErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  async function doFork() {
    try {
      const r = (await api.fork({ entryId: forkAt.trim() })) as { error?: string };
      if (r.error) throw new Error(r.error);
      setNote("已在 " + forkAt.trim() + " 处分叉");
      setForkAt("");
      void load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function open(s: Sess) {
    const p = s.path || "";
    if (!p) return;
    try {
      const r = (await api.runtimeOpen(p)) as { error?: string };
      if (r.error) throw new Error(r.error);
      setNote("已打开：" + p.split("/").pop());
      void load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function rename(s: Sess) {
    const p = s.path || "";
    const n = window.prompt("新名称", s.title || "");
    if (!n || !p) return;
    try {
      const r = (await api.sessionsRename(p, n)) as { error?: string };
      if (r.error) throw new Error(r.error);
      void load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function del(s: Sess) {
    const p = s.path || "";
    if (!p || !confirm("删除会话（移进 .trash）？")) return;
    try {
      const r = (await api.sessionsDelete(p)) as { error?: string };
      if (r.error) throw new Error(r.error);
      void load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  /** 一键清空全部会话记录（2026-11-14）。
   *
   * 两道确认：先 confirm 一次，再要求输入大写 CLEAR 才放行 ——
   * 这是不可逆程度很高的操作（虽然服务端移到 .trash 可捞回，用户未必知道）。
   * 服务端还会校验 body.confirm === "clear-all"，前端两道只是防误触。 */
  async function clearAll() {
    if (!list.length) { setNote("没有会话可清"); return; }
    if (!confirm(`确定清空全部 ${list.length} 条会话记录？
（服务端会移到 .trash，仍可手动找回）`)) return;
    const typed = prompt("请输入 CLEAR 确认清空（区分大小写）：");
    if (typed !== "CLEAR") { setNote("已取消（未输入 CLEAR）"); return; }
    setBusy(true);
    try {
      const r = (await api.sessionsClearAll()) as { error?: string; moved?: number; failed?: number; total?: number };
      if (r.error) throw new Error(r.error);
      setNote(`已清空 ${r.moved ?? 0} 条${r.failed ? `，${r.failed} 条失败` : ""}`);
      void load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  /** 导出会话 HTML。
   *  服务端 /api/export 只下发**已经存在**的 /opt/pi-ctf/pi-session-*.html（它不代跑 pi），
   *  所以这里分两条路：已经生成过 → 直接下载；没生成过 → 把生成命令原样给你。
   *  绝不在文件不存在时假装点了下载——那只会让人以为导出成功、实际拿到 400。 */
  async function exportSession(s: Sess) {
    const p = s.path || "";
    const stem = (s.id || (s.fileName || "session")).replace(/\.jsonl$/, "").replace(/[^\w.-]/g, "_");
    const out = "pi-session-" + stem + ".html";
    setErr("");
    try {
      const r = await fetch(api.exportUrl(out), { method: "HEAD" });
      if (r.ok) {
        const a = document.createElement("a");
        a.href = api.exportUrl(out);
        a.download = out;
        a.click();
        setNote("已下载 " + out);
        return;
      }
    } catch { /* 落到下面的提示分支 */ }
    setNote("还没生成过 " + out + " —— 在容器里跑一次：cd /opt/pi-ctf && pi --export " + (p || "<会话路径>") + " " + out + "（之后再点导出就能直接下载）");
  }

  async function newSession() {
    try {
      await api.runtimeNew();
      setNote("已新建会话");
      void load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function closeRt(id: string) {
    try {
      await api.runtimeClose(id);
      void load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="page">
      <div className="grid">
        <div className="col">
          <div className="card">
            <h3>运行中的会话（{rts.length}）</h3>
            <div className="list">
              {rts.map((r) => (
                <div key={r.runtimeId} className={"item" + (r.runtimeId === active ? " on" : "")}>
                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <span className={"dot " + (r.busy ? "" : "ok")} />
                    <span className="mono" style={{ fontSize: 11 }}>{r.runtimeId}</span>
                    <span className="spacer" />
                    <button onClick={() => void closeRt(r.runtimeId)}>关闭</button>
                  </div>
                  <div className="muted">pid {r.pid ?? "-"} · {r.busy ? "忙" : "空闲"}</div>
                </div>
              ))}
            </div>
            <button style={{ marginTop: 8 }} onClick={() => void newSession()}>新建会话</button>
          </div>
          <div className="card" style={{ flex: 1, overflow: "auto" }}>
            <h3>会话树（/api/tree）</h3>
            {treeErr ? <div className="empty">{treeErr}</div> : null}
            <pre className="pre" style={{ maxHeight: 260 }}>{tree ? JSON.stringify(tree, null, 2) : "（无活动会话）"}</pre>
            <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
              <input style={{ flex: 1 }} placeholder="entryId（在该条目处分叉）" value={forkAt} onChange={(e) => setForkAt(e.target.value)} />
              <button onClick={() => void doFork()} disabled={!forkAt.trim()}>分叉</button>
            </div>
            <button style={{ marginTop: 8 }} onClick={() => void loadTree()}>刷新树</button>
          </div>
        </div>
        <div className="col">
          {err ? <div className="err">{err}</div> : null}
          {note ? <div className="muted">{note}</div> : null}
          <div className="card" style={{ flex: 1, overflow: "auto" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
              <h3 style={{ margin: 0 }}>历史会话（{list.length}）</h3>
              <span className="spacer" />
              <button
                className="danger"
                disabled={busy || !list.length}
                onClick={() => void clearAll()}
                title="清空全部会话记录（会移到 .trash，可找回）"
              >
                {busy ? "清空中…" : "一键清空"}
              </button>
            </div>
            <div className="list">
              {list.length === 0 ? <div className="empty">还没有历史会话</div> : null}
              {list.map((s, i) => {
                const p = s.path || "";
                return (
                  <div key={p || i} className="item">
                    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <strong style={{ fontSize: 13 }}>{s.title || (s.fileName || "").slice(0, 40) || "（未命名）"}</strong>
                      <span className="spacer" />
                      <button onClick={() => void open(s)}>打开</button>
                      <button onClick={() => void exportSession(s)} title="下载导出的 HTML（没生成过会给命令）">导出</button>
                      <button onClick={() => void rename(s)}>改名</button>
                      <button onClick={() => void del(s)}>删</button>
                    </div>
                    <div className="muted">{fmt(s.mtimeMs || s.createdAt)}{s.messages ? " · " + s.messages + " 条消息" : ""}</div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}