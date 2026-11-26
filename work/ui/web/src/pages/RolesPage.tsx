import { useEffect, useState } from "react";
import { api, type Json } from "../api";

type Role = {
  id: string;
  name?: string;
  prompt?: string;
  model?: string;
  thinkingLevel?: string;
  description?: string;
  broken?: boolean;
  kind?: string;
  tools?: string[];
  capabilities?: string[];
  [k: string]: unknown;
};

export default function RolesPage() {
  const [roles, setRoles] = useState<Role[]>([]);
  const [selected, setSelected] = useState("");
  const [cur, setCur] = useState<Role | null>(null);
  const [prompt, setPrompt] = useState("");
  const [resolved, setResolved] = useState<Json | null>(null);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [fresh, setFresh] = useState({ id: "", name: "" });

  const load = async () => {
    try {
      const d = (await api.roles()) as { roles?: Role[]; selected?: string; error?: string };
      if (d.error) throw new Error(d.error);
      setRoles(d.roles || []);
      if (d.selected) setSelected(d.selected);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => { void load(); }, []);

  useEffect(() => {
    if (!selected) { setCur(null); setPrompt(""); setResolved(null); return; }
    void api
      .rolesGet(selected)
      .then((r) => {
        const role = r as Role;
        setCur(role);
        setPrompt(String(role.prompt || ""));
      })
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
    void api.rolesResolve(selected).then((r) => setResolved(r as Json)).catch(() => setResolved(null));
  }, [selected]);

  async function save() {
    if (!cur) return;
    setErr("");
    setNote("");
    try {
      await api.rolesSave(cur, prompt);
      setNote("已保存 " + cur.id);
      void load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function create() {
    const id = fresh.id.trim().toLowerCase();
    if (!id) return;
    try {
      await api.rolesSave({ id, name: fresh.name.trim() || id, prompt: "" } as Json, "");
      setFresh({ id: "", name: "" });
      setSelected(id);
      void load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function del() {
    if (!cur) return;
    if (!confirm("删除智能体 " + cur.id + "？")) return;
    try {
      await api.rolesDelete(cur.id);
      setSelected("");
      void load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function activate() {
    if (!cur) return;
    try {
      await api.rolesActive(cur.id);
      setNote("已设为当前工作区智能体：" + cur.id);
      void load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function doImport() {
    const src = promptTo("导入：填写 zip/目录路径");
    if (!src) return;
    try {
      const r = await api.rolesImport(src);
      setNote("导入结果：" + JSON.stringify(r));
      void load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function doReset(overwrite: boolean) {
    const msg = overwrite
      ? "用出厂预设**覆盖**同名智能体？\n\n你在界面上改过的工具/思考档/提示词会被冲掉（角色定义以 roles-pack 为准）。"
      : "把出厂预设里**缺失**的智能体补回来？\n\n已存在的同名智能体保持不动。";
    if (!confirm(msg)) return;
    setErr("");
    setNote("");
    try {
      const r = (await api.rolesReset(overwrite)) as { restored?: string[]; skipped?: string[]; failed?: { id: string; error: string }[] };
      const parts = [`${overwrite ? "覆盖/写入" : "补回"} ${(r.restored || []).length} 个`];
      if (r.skipped?.length) parts.push(`跳过 ${r.skipped.length} 个`);
      if (r.failed?.length) parts.push(`失败 ${r.failed.length} 个：` + r.failed.map((f) => f.id + "(" + f.error + ")").join("、"));
      setNote("恢复出厂：" + parts.join("，") + ((r.restored || []).length ? "｜" + (r.restored || []).join("、") : ""));
      void load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function doExport() {
    if (!cur) return;
    const p = promptTo("导出到哪个路径（zip）");
    if (!p) return;
    try {
      const r = await api.rolesExport([cur.id], p);
      setNote("导出结果：" + JSON.stringify(r));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div className="page">
      <div className="grid">
        <div className="col">
          <div className="card">
            <h3>智能体（{roles.length}）</h3>
            <div className="list">
              {roles.map((r) => (
                <button key={r.id} className={"item" + (selected === r.id ? " on" : "")} onClick={() => setSelected(r.id)}>
                  <div>{r.name || r.id}{r.broken ? " ⚠" : ""}</div>
                  <div className="muted mono">{r.id}{r.kind ? " · " + r.kind : ""}</div>
                </button>
              ))}
            </div>
          </div>
          <div className="card">
            <h3>新建</h3>
            <input placeholder="id（小写字母/数字/短横线）" value={fresh.id} onChange={(e) => setFresh({ ...fresh, id: e.target.value })} />
            <input placeholder="显示名" style={{ marginTop: 6 }} value={fresh.name} onChange={(e) => setFresh({ ...fresh, name: e.target.value })} />
            <button style={{ marginTop: 8 }} onClick={() => void create()}>创建</button>
            <div style={{ marginTop: 10 }} className="muted">导入/导出走服务端文件路径</div>
            <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
              <button onClick={() => void doImport()}>导入</button>
              <button onClick={() => void doExport()} disabled={!cur}>导出当前</button>
            </div>
            <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
              <button onClick={() => void doReset(false)} title="把内置预设里缺失的角色补回来，已存在的同名智能体不覆盖">恢复出厂（补缺）</button>
              <button onClick={() => void doReset(true)} title="用出厂预设覆盖同名智能体（会冲掉界面上的改动）">覆盖为出厂</button>
            </div>
          </div>
        </div>
        <div className="col">
          {err ? <div className="err">{err}</div> : null}
          {note ? <div className="muted">{note}</div> : null}
          {!cur ? (
            <div className="card empty">左边选一个智能体</div>
          ) : (
            <>
              <div className="card">
                <h3>{cur.name || cur.id}</h3>
                <div style={{ display: "flex", gap: 8 }}>
                  <button onClick={() => void save()}>保存提示词</button>
                  <button onClick={() => void activate()}>设为当前</button>
                  <button onClick={() => void del()}>删除</button>
                </div>
                <dl className="kv" style={{ marginTop: 10 }}>
                  <dt>模型</dt><dd>{String(cur.model || "-")}</dd>
                  <dt>思考</dt><dd>{String(cur.thinkingLevel || "-")}</dd>
                </dl>
              </div>
              <div className="card" style={{ flex: 1, display: "flex", flexDirection: "column" }}>
                <h3>系统提示词</h3>
                <textarea style={{ flex: 1, minHeight: 260, fontFamily: "var(--mono)", fontSize: 12 }} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
              </div>
              <div className="card" style={{ maxHeight: 220, overflow: "auto" }}>
                <h3>编译预览（实机会带上的参数与能力）</h3>
                <pre className="pre">{resolved ? JSON.stringify(resolved, null, 2) : "（无）"}</pre>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function promptTo(msg: string): string {
  return window.prompt(msg) || "";
}