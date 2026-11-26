import { useEffect, useState } from "react";
import { api } from "../api";
import { usePoll } from "../lib/poll";
import { publishStatus, useSharedStatus } from "../lib/statusStore";

type Status = {
  ok?: boolean;
  childPid?: number | null;
  restarts?: number;
  clients?: number;
  root?: string;
  activeRuntimeId?: string;
  runtimeCount?: number;
  runtimes?: { runtimeId: string; busy?: boolean; session?: string | null }[];
};

type Defaults = {
  piVersion?: string;
  cwd?: string;
  defaultModel?: string;
  defaultProvider?: string;
  defaultThinkingLevel?: string;
  sessionsRoot?: string;
};

type Proj = { pid?: string; goal?: string };

// ★ 与服务端 FGS_ALLOW 一一对应（runFgs 会再校验一次；这里只是把能点的列出来，
//   两边不同步时服务端会明确回「不在白名单」，不会静默失败）。
const FGS_CMDS: { cmd: string; label: string; hint: string }[] = [
  { cmd: "next", label: "下一步", hint: "机器判断现在该干哪一类动作" },
  { cmd: "workers", label: "子代理", hint: "在跑 / 悬空的子代理台账" },
  { cmd: "cost", label: "成本", hint: "已花预算与坐实密度" },
  { cmd: "gate", label: "门禁", hint: "当前是否满足交题条件" },
  { cmd: "coverage", label: "覆盖", hint: "覆盖面统计" },
  { cmd: "deadline", label: "剩余时间", hint: "只读 show，不含设置" },
  { cmd: "doctor", label: "体检", hint: "黑板自检" },
  // 2026-11-11 补：本轮新增的两个只读命令，此前界面上调不到
  { cmd: "pending", label: "待提交", hint: "跨所有黑板：拿到 flag 还没交的（最贵的亏）" },
  { cmd: "blindspot", label: "盲区", hint: "死路缺三要素 / 未验证前提 / 覆盖空洞" },
];

export default function SystemPage() {
  const [st, setSt] = useState<Status | null>(null);
  const [df, setDf] = useState<Defaults | null>(null);
  const [tools, setTools] = useState<string[]>([]);
  // #43 本次会话的工具调用总次数（/api/tools 顺带返回，取不到就是 0）
  const [callsTotal, setCallsTotal] = useState(0);
  const [flags, setFlags] = useState<{ total?: number; error?: string; unique?: unknown[] } | null>(null);
  const [err, setErr] = useState("");

  // ★ X-5 就绪计数可对账：summary 给 X/Y + 缺项名单（点得开），drill 打开某一类的逐条明细。
  //   判据（总数=Σ分类、N=0 不许假装能点）全在 tools/readiness.mjs，这里只渲染。
  type ReadyCat = { key: string; label: string; total: number; ready: number; ratio: number };
  type ReadySum = {
    total: number; ready: number; ratio: number; available: boolean; complete: boolean;
    categories: ReadyCat[];
    missing: { category: string; name: string; why: string }[];
    one_line?: string;
  };
  const [ready, setReady] = useState<ReadySum | null>(null);
  const [readyDrill, setReadyDrill] = useState<{ key: string; label: string; items: { name: string; ok: boolean; why: string }[] } | null>(null);

  // ── 运维动作（2026-11-05 新增：停服务 / 跑 fgs 真正做成按钮）─────────────
  const [busyStop, setBusyStop] = useState(false);
  const [stopOut, setStopOut] = useState<{ ok: boolean; text: string } | null>(null);

  const [projs, setProjs] = useState<Proj[]>([]);
  const [fgsPid, setFgsPid] = useState("");
  const [fgsCmd, setFgsCmd] = useState("next");
  const [busyFgs, setBusyFgs] = useState(false);
  const [fgsOut, setFgsOut] = useState<{ cmd: string; ok: boolean; text: string } | null>(null);

  // #22：status 不再自己拉 —— App 每 5s 拉一次 /api/status 并 publish 到 statusStore，
  // 这里订阅同一份（原来两个页面各拉一次同一个接口）。其余四路打成一个签名去重。
  const shared = useSharedStatus<Status>();
  useEffect(() => {
    if (shared) setSt(shared);
  }, [shared]);

  const fetchAll = async () => {
    const df = (await api.defaults()) as Defaults;
    const tj = (await api.tools()) as { capabilities?: string[]; callsTotal?: number };
    const tools = tj.capabilities || [];
    // #43 数不出来就是 0，界面上不许显示 NaN 或假装有数
    const callsTotal = typeof tj.callsTotal === "number" ? tj.callsTotal : 0;
    const flags = (await api.flags()) as { total?: number; error?: string; unique?: unknown[] };
    const ready = (await api.readiness()) as ReadySum;
    return { df, tools, callsTotal, flags, ready };
  };
  const applyAll = (d: { df: Defaults; tools: string[]; callsTotal: number; flags: { total?: number; error?: string; unique?: unknown[] }; ready: ReadySum }) => {
    setDf(d.df);
    setTools(d.tools);
    setCallsTotal(d.callsTotal);
    setFlags(d.flags);
    setReady(d.ready);
    setErr("");
  };
  const load = async () => {
    try {
      // 主动刷新时仍要一份最新的 status（轮询还没到 / 刚重启完进程）
      const s = (await api.status()) as Status;
      setSt(s);
      publishStatus(s); // 同一份状态回灌给全站，免得 App 再拉一次
      applyAll(await fetchAll());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    // 项目列表只在挂载时取一次（pid 候选不该每 5 秒抖一次，会把用户正在选的项顶掉）
    void api.board()
      .then((d) => {
        const ps = ((d as { projects?: Proj[] }).projects || []) as Proj[];
        setProjs(ps);
        if (ps.length && !fgsPid) setFgsPid(String(ps[0].pid || ""));
      })
      .catch(() => setProjs([]));
    void load(); // 挂载时主动取一轮（App 的第一轮 status 可能还没回来）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // #22：5s 一轮，四路 payload 签名没变就不 setState
  usePoll(fetchAll, 5000, applyAll, {
    onError: (e) => setErr(e instanceof Error ? e.message : String(e)),
  });

  /**
   * 停服务 —— 先确认，再发。
   * ★ 为什么必须确认：这一下有现场代价。停掉的是容器里正在跑的 pi 进程，
   *   在途的工具调用会一起断（不是刷新页面那种无痛操作）。
   * ★ 回什么就显示什么：不给结论盖章，把服务端的原始 JSON 摊在下面。
   */
  async function doStop() {
    if (!confirm("停掉容器里的 pi 服务？\n\n正在跑的工具调用会一起中断；停完可以用「启动服务」再拉起来，但上一轮进度不保证能续。")) return;
    setBusyStop(true);
    setStopOut(null);
    try {
      const r = (await api.stop()) as Record<string, unknown>;
      const ok = (r as { ok?: boolean }).ok !== false;
      setStopOut({ ok, text: JSON.stringify(r, null, 2) });
    } catch (e) {
      setStopOut({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusyStop(false);
      void load();
    }
  }

  async function doStart() {
    if (!confirm("启动 / 重启容器里的 pi 服务？\n\n如果它已经在跑，这一步会按当前配置重启它（会打断在途任务）。")) return;
    setBusyStop(true);
    setStopOut(null);
    try {
      const r = (await api.start({})) as Record<string, unknown>;
      const ok = (r as { ok?: boolean }).ok !== false;
      setStopOut({ ok, text: JSON.stringify(r, null, 2) });
    } catch (e) {
      setStopOut({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusyStop(false);
      void load();
    }
  }

  /**
   * 跑 fgs —— 只读子命令，白名单在服务端（FGS_ALLOW），这里拼不出任意 shell。
   * 仍加确认：这不是「危险」而是「有成本」—— 每次都要起一个子进程，
   * 而且人看到输出会据此改变下一步动作，点错了会误导判断。
   */
  async function doFgs() {
    const cmd = fgsCmd;
    const pid = fgsPid.trim();
    // ★ pending 是跨项目命令（扫所有黑板），不需要 pid；其余命令必须选项目。
    //   必须与服务端 FGS_GLOBAL 保持一致，否则前端拦了服务端根本不要求的东西。
    const isGlobal = cmd === "pending";
    if (!isGlobal && !pid) {
      setFgsOut({ cmd, ok: false, text: "先选一个项目（pid 不能为空）" });
      return;
    }
    if (!confirm("要跑 fgs " + cmd + " " + pid + " 吗？\n\n只读子命令，不会改黑板；但输出会被你当成下一步的依据。")) return;
    setBusyFgs(true);
    setFgsOut(null);
    try {
      const r = (await api.fgs({ cmd, pid })) as {
        ok?: boolean; stdout?: string; stderr?: string; error?: string; timeout?: boolean; code?: number;
      };
      // ★ 三态必须分清：成功 / 失败 / 超时。把失败显示成空输出 = 骗人。
      const ok = r.ok === true && !r.error;
      const parts: string[] = [];
      if (r.error) parts.push("错误：" + r.error);
      if (r.timeout) parts.push("（超时 30 秒，已 kill）");
      if (r.code !== undefined) parts.push("退出码：" + r.code);
      if (r.stdout) parts.push("stdout:\n" + r.stdout.trimEnd());
      if (r.stderr) parts.push("stderr:\n" + r.stderr.trimEnd());
      if (!parts.length) parts.push("（没有输出）");
      setFgsOut({ cmd: "fgs " + cmd + " " + pid, ok, text: parts.join("\n\n") });
    } catch (e) {
      setFgsOut({ cmd: "fgs " + cmd + " " + pid, ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusyFgs(false);
    }
  }

  const online = !!st?.ok;

  return (
    <div className="page">
      {err ? <div className="err">{err}</div> : null}
      {/* X-5：就绪计数可对账 —— 数字点得开，缺项指名道姓，N=0 时不给"能点"的假象 */}
      <div className="card">
        <h3>环境就绪（可对账）</h3>
        {!ready ? (
          <div className="muted">未取到就绪数据（未取到 ≠ 全绿，先看上面的错误）</div>
        ) : (
          <>
            <div className="kv">
              <dt>总览</dt>
              <dd className={"mono " + (ready.complete ? "ok" : "")}>
                {ready.ready}/{ready.total}
                {ready.total ? "（" + Math.round(ready.ratio * 100) + "%）" : "（无检查项）"}
                {ready.one_line ? " · " + ready.one_line : ""}
              </dd>
            </div>
            <table className="tbl">
              <thead><tr><th>类别</th><th>就绪</th><th>明细</th></tr></thead>
              <tbody>
                {(ready.categories || []).map((c) => (
                  <tr key={c.key}>
                    <td>{c.label}</td>
                    <td className="mono">{c.ready}/{c.total}</td>
                    <td>
                      <button
                        className="btn sm"
                        onClick={() => {
                          void api.readinessDrill(c.key)
                            .then((d) => setReadyDrill(d as typeof readyDrill))
                            .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
                        }}
                      >点开</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {(ready.missing || []).length ? (
              <div className="warn">
                缺 {" "}
                {(ready.missing || []).map((m) => (
                  <span key={m.category + ":" + m.name} className="chip">
                    {m.name}{m.why ? "（" + m.why + "）" : ""}
                  </span>
                ))}
              </div>
            ) : (
              <div className="muted">全部就绪</div>
            )}
            {readyDrill ? (
              <div className="drill">
                <div className="muted">{readyDrill.label} 明细（{(readyDrill.items || []).length} 项）</div>
                <ul>
                  {(readyDrill.items || []).map((it) => (
                    <li key={it.name}>
                      {it.ok ? "✓" : "✗"} <span className="mono">{it.name}</span>
                      {it.ok ? "" : it.why ? " — " + it.why : ""}
                    </li>
                  ))}
                </ul>
                <button className="btn sm" onClick={() => setReadyDrill(null)}>收起</button>
              </div>
            ) : null}
          </>
        )}
      </div>

      <div className="card">
        <h3>运行状态</h3>
        <dl className="kv">
          <dt>pi 进程</dt><dd>{online ? "在线 pid " + st.childPid : "离线"}</dd>
          <dt>重启次数</dt><dd>{st?.restarts ?? "-"}</dd>
          <dt>SSE 客户端</dt><dd>{st?.clients ?? "-"}</dd>
          <dt>会话数</dt><dd>{st?.runtimeCount ?? "-"}</dd>
          <dt>活动会话</dt><dd className="mono">{st?.activeRuntimeId || "-"}</dd>
          <dt>工作根</dt><dd className="mono">{st?.root || "-"}</dd>
        </dl>
      </div>

      {/* 运维动作：改现场的两个开关 */}
      <div className="card" style={{ marginTop: 14 }}>
        <h3>服务控制</h3>
        <div className="row wrap">
          <button className="danger" onClick={() => void doStop()} disabled={busyStop || !online}>
            {busyStop ? "处理中…" : "停服务"}
          </button>
          <button onClick={() => void doStart()} disabled={busyStop}>
            {busyStop ? "处理中…" : online ? "重启服务" : "启动服务"}
          </button>
          <span className="muted">
            {online ? "当前在线；停服务会中断在途任务" : "当前离线；点「启动服务」拉起"}
          </span>
        </div>
        {stopOut ? (
          <div className="cmdout">
            <span className={stopOut.ok ? "" : "bad"}>{stopOut.ok ? "服务端回执：" : "服务端报错："}</span>
            {"\n" + stopOut.text}
          </div>
        ) : null}
      </div>

      {/* 问机器：只读 fgs 子命令 */}
      <div className="card" style={{ marginTop: 14 }}>
        <h3>问机器（fgs 只读子命令）</h3>
        <div className="row wrap">
          <select value={fgsPid} onChange={(e) => setFgsPid(e.target.value)}>
            {projs.length === 0 ? <option value="">（无项目）</option> : null}
            {projs.map((p) => (
              <option key={p.pid} value={p.pid}>{p.pid}</option>
            ))}
          </select>
          <select value={fgsCmd} onChange={(e) => setFgsCmd(e.target.value)}>
            {FGS_CMDS.map((c) => (
              <option key={c.cmd} value={c.cmd}>{c.label}（{c.cmd}）</option>
            ))}
          </select>
          <button onClick={() => void doFgs()} disabled={busyFgs || !fgsPid.trim()}>
            {busyFgs ? "运行中…" : "运行"}
          </button>
          <span className="muted">{FGS_CMDS.find((c) => c.cmd === fgsCmd)?.hint}</span>
        </div>
        <div className="muted" style={{ marginTop: 6 }}>
          白名单：{FGS_CMDS.map((c) => c.cmd).join(" / ")} —— 只读，改不了黑板。
        </div>
        {fgsOut ? (
          <div className="cmdout">
            <span className={fgsOut.ok ? "" : "bad"}>
              {fgsOut.cmd} · {fgsOut.ok ? "成功" : "失败"}
            </span>
            {"\n" + fgsOut.text}
          </div>
        ) : null}
      </div>

      <div className="card" style={{ marginTop: 14 }}>
        <h3>pi 配置</h3>
        <dl className="kv">
          <dt>版本</dt><dd>{df?.piVersion || "-"}</dd>
          <dt>模型</dt><dd>{df ? df.defaultProvider + " / " + df.defaultModel : "-"}</dd>
          <dt>思考级别</dt><dd>{df?.defaultThinkingLevel || "-"}</dd>
          <dt>cwd</dt><dd className="mono">{df?.cwd || "-"}</dd>
          <dt>会话目录</dt><dd className="mono">{df?.sessionsRoot || "-"}</dd>
        </dl>
      </div>
      <div className="card" style={{ marginTop: 14 }}>
        <h3>能力标签（{tools.length}）</h3>
        <div className="muted mono">{tools.join("  ") || "（无）"}</div>
        {/* #43 累计调用：会话 JSONL 里数出来的，界面重启也不归零；数不出来如实显示 0 */}
        <div className="muted" style={{ marginTop: 6 }}>
          本次会话工具调用 {callsTotal} 次（按会话 JSONL 的 toolResult 统计）
        </div>
      </div>
      <div className="card" style={{ marginTop: 14 }}>
        <h3>flag 记录</h3>
        {flags?.error ? <div className="muted">{flags.error}</div> : null}
        <dl className="kv">
          <dt>总数</dt><dd>{flags?.total ?? 0}</dd>
          <dt>去重</dt><dd>{(flags?.unique || []).length}</dd>
        </dl>
      </div>
    </div>
  );
}
