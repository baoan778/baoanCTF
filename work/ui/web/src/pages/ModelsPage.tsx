import { useEffect, useState } from "react";
import { api, type Json } from "../api";

type Model = { id: string; name?: string; contextWindow?: number | null; reasoning?: boolean; input?: string[] };
type Provider = {
  id: string;
  name?: string;
  baseUrl?: string;
  api?: string;
  configured?: boolean;
  hasKey?: boolean;
  keySource?: string | null;
  keyHint?: string;
  removable?: boolean;
  modelCount?: number;
  models?: Model[];
  /** 连通性记忆（gui-state.json 的 modelHealth）：只记真发过请求的结果，没记录 = 未测过 */
  health?: {
    fails?: number;
    last_error?: string;
    last_reason?: string;
    last_fail_at?: string | null;
    last_ok_at?: string | null;
    last_ms?: number | null;
  } | null;
};
type KeyInfo = { hasKey?: boolean; keySource?: string | null; keyHint?: string; usable?: boolean; virtual?: boolean };

export default function ModelsPage() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [keys, setKeys] = useState<Record<string, KeyInfo>>({});
  const [catalog, setCatalog] = useState<string[]>([]);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [form, setForm] = useState({
    mode: "catalog" as "catalog" | "custom",
    provider: "",
    apiKey: "",
    baseUrl: "",
    api: "",
    displayName: "",
    models: "",
  });
  const [testOut, setTestOut] = useState<Json | null>(null);

  const load = async () => {
    setErr("");
    try {
      const m = (await api.models()) as { providers?: Provider[]; error?: string };
      if (m.error) throw new Error(m.error);
      setProviders(m.providers || []);
      const k = (await api.modelsKeys()) as { keys?: Record<string, KeyInfo> };
      setKeys(k.keys || {});
      const c = (await api.modelsCatalog()) as { providers?: string[] };
      setCatalog(c.providers || []);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => { void load(); }, []);

  async function save() {
    setNote("");
    setErr("");
    try {
      const body: Json = {
        mode: form.mode,
        provider: form.provider.trim(),
        apiKey: form.apiKey,
        baseUrl: form.baseUrl,
        api: form.api,
        displayName: form.displayName,
      };
      if (form.mode === "custom") {
        body.models = form.models.split(/[\n,]/).map((s) => s.trim()).filter(Boolean);
      }
      const r = (await api.modelsProvider(body)) as { ok?: boolean; error?: string; existed?: boolean };
      if (r.error) throw new Error(r.error);
      setNote("已保存 " + form.provider + (r.existed ? "（覆盖）" : "（新增）"));
      void load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function test() {
    setTestOut(null);
    try {
      setTestOut((await api.modelsTest(form.provider.trim(), form.apiKey)) as Json);
    } catch (e) {
      setTestOut({ error: e instanceof Error ? e.message : String(e) });
    }
  }

  async function del(id: string) {
    if (!confirm("删除提供商 " + id + "？")) return;
    try {
      const r = (await api.modelsProviderDelete(id)) as { error?: string };
      if (r.error) throw new Error(r.error);
      setNote("已删除 " + id);
      void load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="page">
      <div className="grid">
        <div className="col">
          <div className="card" style={{ flex: 1, overflow: "auto" }}>
            <h3>提供商（{providers.length}）</h3>
            <div className="list">
              {providers.map((p) => {
                const k = keys[p.id] || {};
                const h = p.health || {};
                return (
                  <div key={p.id} className="item">
                    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <span
                        className={"dot " + ((h.fails || 0) > 0 ? "bad" : k.usable || p.configured ? "ok" : "")}
                        title={(h.fails || 0) > 0 ? "最近失败 " + h.fails + " 次：" + (h.last_error || "见测试回包") : undefined}
                      />
                      <strong>{p.name || p.id}</strong>
                      <span className="muted mono">{p.id}</span>
                      <span className="spacer" />
                      {p.removable ? <button onClick={() => void del(p.id)}>删</button> : null}
                    </div>
                    <div className="muted">
                      {(p.models || []).length} 个模型 · {k.virtual ? "虚拟（无需密钥）" : k.hasKey ? "有密钥" : "无密钥"}
                      {k.keyHint ? " · " + k.keyHint : ""}
                      {(h.fails || 0) > 0 ? " · ⚠ 最近失败 " + h.fails + " 次（" + (h.last_reason || "?") + "）" : ""}
                    </div>
                    {(h.fails || 0) > 0 ? (
                      <div className="muted" style={{ fontSize: 11 }}>{String(h.last_error || "").slice(0, 160)}</div>
                    ) : h.last_ok_at ? (
                      <div className="muted" style={{ fontSize: 11 }}>上次连通 {String(h.last_ok_at).slice(11, 19)} · {String(h.last_ms ?? "—")} ms</div>
                    ) : (
                      <div className="muted" style={{ fontSize: 11 }}>未测过 · 点下面「测试密钥」才产生记录</div>
                    )}
                    <div className="muted mono" style={{ fontSize: 11 }}>
                      {(p.models || []).slice(0, 6).map((m) => m.id).join(", ")}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
        <div className="col">
          {err ? <div className="err">{err}</div> : null}
          {note ? <div className="muted">{note}</div> : null}
          <div className="card">
            <h3>添加 / 修改</h3>
            <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
              <button className={form.mode === "catalog" ? "item on" : "item"} onClick={() => setForm({ ...form, mode: "catalog" })}>内置目录</button>
              <button className={form.mode === "custom" ? "item on" : "item"} onClick={() => setForm({ ...form, mode: "custom" })}>自定义</button>
            </div>
            {form.mode === "catalog" ? (
              <select
                style={{ width: "100%" }}
                value={form.provider}
                onChange={(e) => setForm({ ...form, provider: e.target.value })}
              >
                <option value="">选择提供商…</option>
                {catalog.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            ) : (
              <>
                <input placeholder="provider id（如 my-llm）" value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value })} />
                <input placeholder="API 地址 https://…" style={{ marginTop: 6 }} value={form.baseUrl} onChange={(e) => setForm({ ...form, baseUrl: e.target.value })} />
                <input placeholder="api 类型（可选，如 openai-completions）" style={{ marginTop: 6 }} value={form.api} onChange={(e) => setForm({ ...form, api: e.target.value })} />
                <textarea placeholder="模型 ID，一行一个" style={{ marginTop: 6, height: 70 }} value={form.models} onChange={(e) => setForm({ ...form, models: e.target.value })} />
              </>
            )}
            <input placeholder="显示名（可选）" style={{ marginTop: 6 }} value={form.displayName} onChange={(e) => setForm({ ...form, displayName: e.target.value })} />
            <input placeholder="API 密钥（留空 = 保持已存的）" type="password" style={{ marginTop: 6 }} value={form.apiKey} onChange={(e) => setForm({ ...form, apiKey: e.target.value })} />
            <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
              <button onClick={() => void save()} disabled={!form.provider.trim()}>保存</button>
              <button onClick={() => void test()} disabled={!form.provider.trim()}>测试密钥</button>
            </div>
            {testOut ? (
              <div style={{ marginTop: 8 }}>
                {testOut.ok ? (
                  <div style={{ padding: "6px 8px", border: "1px solid var(--ok, #3ddc97)", borderRadius: 6 }}>
                    ✓ 连通{typeof testOut.ms === "number" ? " · " + testOut.ms + " ms" : ""}
                    {testOut.model ? " · " + String(testOut.model) : ""}
                    <div className="muted mono" style={{ fontSize: 11 }}>{String(testOut.endpoint || "")}</div>
                    {testOut.reply ? <div className="muted">模型回：{String(testOut.reply)}</div> : null}
                  </div>
                ) : (
                  <div className="err" style={{ padding: "6px 8px" }}>
                    ✗ 失败{testOut.status ? " · HTTP " + String(testOut.status) : ""}
                    {testOut.reason ? " · " + String(testOut.reason) : ""}
                    <div>{String(testOut.error || "")}</div>
                    {testOut.endpoint ? <div className="muted mono" style={{ fontSize: 11 }}>{String(testOut.endpoint)}</div> : null}
                    {testOut.next ? <div style={{ marginTop: 4 }}>下一步：{String(testOut.next)}</div> : null}
                  </div>
                )}
                <details style={{ marginTop: 6 }}>
                  <summary className="muted" style={{ cursor: "pointer", fontSize: 11 }}>原始回包（排查用）</summary>
                  <pre className="pre">{JSON.stringify(testOut, null, 2)}</pre>
                </details>
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}