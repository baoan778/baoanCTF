import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import HttpBlock from "../components/HttpBlock";

type Entry = { name: string; path: string; isDir: boolean; size?: number; mtimeMs?: number };
type Uploaded = { path: string; name: string; size: number };

function fmtSize(n?: number): string {
  if (!n && n !== 0) return "";
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
  return (n / 1024 / 1024).toFixed(1) + " MB";
}
function fmtTime(ms?: number): string {
  if (!ms) return "";
  const d = new Date(ms);
  const p = (x: number) => String(x).padStart(2, "0");
  return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
}

const IMG = /\.(png|jpe?g|gif|webp|bmp|svg|ico)$/i;
const ROOT = "/opt/pi-ctf";

/** 单个文件读成 base64（服务端契约就是 {files:[{name,data}]}，20MB 上限）。 */
function readAsB64(f: File): Promise<string> {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => {
      const s = String(r.result || "");
      const i = s.indexOf(",");
      res(i >= 0 ? s.slice(i + 1) : "");
    };
    r.onerror = () => rej(new Error("读取 " + f.name + " 失败"));
    r.readAsDataURL(f);
  });
}

export default function FilesPage() {
  const [path, setPath] = useState(ROOT);
  const [input, setInput] = useState(ROOT);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [sel, setSel] = useState<string>("");
  const [text, setText] = useState<string>("");
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<string[]>([]);
  const [err, setErr] = useState("");
  const [upMsg, setUpMsg] = useState("");
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async (p: string) => {
    setErr("");
    try {
      const d = (await api.fsList(p)) as { path?: string; entries?: Entry[]; error?: string };
      if (d.error) throw new Error(d.error);
      setEntries(d.entries || []);
      setPath(d.path || p);
      setInput(d.path || p);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => { void load(ROOT); }, [load]);

  async function open(e: Entry) {
    if (e.isDir) { void load(e.path); setSel(""); setText(""); return; }
    setSel(e.path);
    setText("");
    if (IMG.test(e.name)) return; // 图片走 <img>，不读文本
    try {
      const d = (await api.fsRead(e.path)) as { text?: string; error?: string };
      if (d.error) throw new Error(d.error);
      setText(d.text || "");
    } catch (err) {
      setErr(err instanceof Error ? err.message : String(err));
    }
  }

  async function search() {
    const k = q.trim();
    if (!k) return;
    setErr("");
    try {
      const d = (await api.fsSearch(k, path)) as { files?: string[]; error?: string };
      if (d.error) throw new Error(d.error);
      setHits(d.files || []);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  /** 上传：服务端落盘到 work/intake/，一次最多 16 个。 */
  async function upload(list: FileList | null) {
    const files = Array.from(list || []);
    if (!files.length) return;
    setErr("");
    setUpMsg("");
    setBusy(true);
    try {
      const payload = await Promise.all(files.slice(0, 16).map(async (f) => ({ name: f.name, data: await readAsB64(f) })));
      const r = (await api.upload({ files: payload })) as { files?: Uploaded[]; error?: string };
      if (r.error) throw new Error(r.error);
      const ok = r.files || [];
      setUpMsg("已保存 " + ok.length + " 个 → " + (ok[0] ? ok[0].path.replace(/\/[^/]*$/, "") : "work/intake"));
      void load(path);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  const isImg = sel && IMG.test(sel);

  return (
    <div className="page">
      <div className="grid">
        <div className="col">
          <div className="card">
            <h3>路径</h3>
            <div className="row">
              <input value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") void load(input); }} />
              <button onClick={() => void load(input)}>打开</button>
            </div>
            <div className="row" style={{ marginTop: 8 }}>
              <input placeholder="搜文件名…" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") void search(); }} />
              <button onClick={() => void search()}>搜</button>
            </div>
            {hits.length ? (
              <div className="list" style={{ marginTop: 8, maxHeight: 170 }}>
                {hits.map((h) => (
                  <button key={h} className="item mono" onClick={() => { void load(h.replace(/\/[^/]*$/, "")); setSel(h); void api.fsRead(h).then((d) => setText(String((d as { text?: string }).text || ""))); }}>
                    {h}
                  </button>
                ))}
              </div>
            ) : null}
          </div>

          <div className="card">
            <h3>上传</h3>
            <div
              className={"drop" + (over ? " over" : "")}
              onDragOver={(e) => { e.preventDefault(); setOver(true); }}
              onDragLeave={() => setOver(false)}
              onDrop={(e) => { e.preventDefault(); setOver(false); void upload(e.dataTransfer.files); }}
              onClick={() => fileRef.current?.click()}
            >
              <span className="big">⤒</span>
              {busy ? "上传中…" : "拖文件到这里，或点我选文件"}
              <div className="hint" style={{ marginTop: 4 }}>落到 work/intake/，单次 ≤16 个 · 单个 ≤20MB</div>
            </div>
            <input ref={fileRef} type="file" multiple style={{ display: "none" }} onChange={(e) => void upload(e.target.files)} />
            {upMsg ? <div className="pill ok" style={{ marginTop: 8 }}>{upMsg}</div> : null}
          </div>

          <div className="card" style={{ flex: 1, overflow: "auto" }}>
            <h3>目录 <span className="n">({entries.length})</span></h3>
            <div className="list">
              <button className="item" onClick={() => void load(path.replace(/\/[^/]+$/, "") || "/")}>
                <span className="faint">.. 上一级</span>
              </button>
              {entries.map((e) => (
                <button key={e.path} className={"item" + (sel === e.path ? " on" : "")} onClick={() => void open(e)}>
                  <div>{e.isDir ? "▸ " : "· "}{e.name}</div>
                  <div className="faint">{e.isDir ? "目录" : fmtSize(e.size)} · {fmtTime(e.mtimeMs)}</div>
                </button>
              ))}
            </div>
          </div>
        </div>
        <div className="col">
          {err ? <div className="err">{err}</div> : null}
          <div className="card" style={{ flex: 1, overflow: "auto" }}>
            <h3>{sel || "选一个文件看内容"}</h3>
            {!sel ? (
              <div className="empty">左边点文件。图片直接预览，文本走 fs/read（限 1MB），二进制可下载。</div>
            ) : isImg ? (
              <img src={api.fsRaw(sel)} alt={sel} style={{ maxWidth: "100%", borderRadius: 8 }} />
            ) : (
              // #42：读到的文件常是 HTTP 报文 / JSON，裸 pre 看不出状态码，几十 KB 更会卡
              <HttpBlock text={text || "（空文件或读取中）"} />
            )}
            {sel ? (
              <div className="row" style={{ marginTop: 10 }}>
                <a href={api.fsRaw(sel)} download={sel.split("/").pop()}><button>下载原文件</button></a>
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
