import { useEffect, useMemo, useRef, useState } from "react";
import { api, subscribe, nextId, type ChatItem, type Ev } from "../api";
import { onLocate, takeLocate } from "../lib/locate";
import NextMoveCard from "../components/NextMoveCard";
import ToolCard from "../components/ToolCard";
import { sendModeHint, setSendMode, shouldSubmitOnKey, useSendMode, type SendMode } from "../lib/chat-send-mode";

type Msg = { type?: string; role?: string; content?: unknown };

function textOf(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((c) => (c && typeof c === "object" && (c as { type?: string }).type === "text"
      ? String((c as { text?: string }).text ?? "")
      : ""))
    .join("");
}

export default function ChatPage() {
  const [items, setItems] = useState<ChatItem[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [rtId, setRtId] = useState<string>("");
  const [err, setErr] = useState("");
  // #41 定位回跳：聚焦哪张工具卡（由审证页「定位调用」经 lib/locate 带过来）
  const [focusId, setFocusId] = useState("");
  // #27 运行中换模型：curModel 是 "provider|modelId"，下拉直接取 /api/models 的扁平列表
  const [curModel, setCurModel] = useState("");
  const [modelChoices, setModelChoices] = useState<{ key: string; label: string }[]>([]);
  // itemsRef 让定位 effect 读到**当前**列表而不是闭包里的旧快照
  const itemsRef = useRef<ChatItem[]>([]);
  itemsRef.current = items;
  const boxRef = useRef<HTMLDivElement | null>(null);
  const curRef = useRef<string>(""); // 正在流式的 assistant 气泡 id
  // #18：贴底判定（±60px 内算贴底）。用户往上翻历史时不许把他拽回底部
  const atBottomRef = useRef(true);
  // #26：发送键位（默认 Ctrl+Enter；可切 Enter，输入法组字时回车永远是选词）
  const mode = useSendMode();
  // #35 旁路提问：想问一句小事（这串是不是 base64 套 gzip）不该把整段输出再喂一遍主线。
  // 回答只作为本地 note 气泡留在这一屏：不写会话文件、不进黑板、不占主线上下文。
  const [btwOpen, setBtwOpen] = useState(false);
  const [btwQ, setBtwQ] = useState("");
  const [btwBusy, setBtwBusy] = useState(false);
  async function askBtw() {
    const q = btwQ.trim();
    if (!q || btwBusy) return;
    setBtwBusy(true);
    setBtwQ("");
    setItems((p) => [...p, { kind: "note", id: nextId("btw-q"), text: "旁路提问（不进主线）：" + q }]);
    try {
      const r = (await api.btw({ question: q })) as { ok?: boolean; answer?: string; error?: string; ms?: number; note?: string };
      const head = "旁路回答 · " + (r.note || "不写会话") + (typeof r.ms === "number" ? " · " + r.ms + "ms" : "");
      const body = r.ok ? r.answer || "（空回答）" : r.error || "旁路提问失败";
      setItems((p) => [...p, { kind: "note", id: nextId("btw-a"), text: head + "\n" + body }]);
    } catch (e) {
      setItems((p) => [...p, { kind: "note", id: nextId("btw-e"), text: "旁路提问失败：" + String(e) }]);
    } finally {
      setBtwBusy(false);
    }
  }
  // #43 累计调用次数：同一工具在本次会话的这张列表里已出现几次。
  // 为什么不用服务端的会话 JSONL 聚合：这里数的是**屏幕上这张列表**，卡片和角标永远一致；
  // 服务端聚合（/api/skills）用来回答「技能这次用上没」，两处口径各自自洽。
  const callCounts = useMemo(() => {
    const m: Record<string, number> = {};
    for (const x of items) if (x.kind === "tool" && x.name) m[x.name] = (m[x.name] || 0) + 1;
    return m;
  }, [items]);

  // 事件→界面。规则来自实测的 pi 1.5 事件流（见 README）：
  //   message_start(assistant) → message_update{text_delta}* → message_end → turn_end
  // 其它类型（含高频的 extension_ui_request/think-clock）一律忽略，不进列表。
  useEffect(() => {
    const off = subscribe((e: Ev) => {
      const rt = String(e.__rt ?? "");
      if (e.type === "ui:hello") {
        const id = String(e.activeRuntimeId ?? "");
        if (id) setRtId(id);
        return;
      }
      // 只认当前会话的事件：别的 runtime 的流不能混进这条时间线
      if (rt && rtId && rt !== rtId) return;

      if (e.type === "message_start") {
        const m = e.message as Msg | undefined;
        if (!m || m.role !== "assistant") return;
        const id = nextId("a");
        curRef.current = id;
        setItems((prev) => [...prev, { kind: "assistant", text: "", id, streaming: true }]);
        return;
      }
      if (e.type === "message_update") {
        const evt = e.assistantMessageEvent as { type?: string; delta?: string } | undefined;
        if (!evt || evt.type !== "text_delta" || !evt.delta) return;
        const id = curRef.current;
        setItems((prev) =>
          prev.map((it) =>
            it.kind === "assistant" && it.id === id
              ? { ...it, text: it.text + String(evt.delta) }
              : it,
          ),
        );
        return;
      }
      // ★ 该轮修复：工具事件按 toolCallId 配对，start 建卡（跑），end 补输出与耗时（完/错）。
      //   服务端已瘦身（result 只留尾部 4KB），这里不再二次裁剪，只做配对。
      if (e.type === "tool_execution_start") {
        const id = String(e.toolCallId || "");
        if (!id) return;
        setItems((prev) => [
          ...prev,
          {
            kind: "tool", id: "t-" + id, name: String(e.toolName || "tool"),
            detail: "", toolId: id, args: String(e.args || ""), out: "", status: "running",
          },
        ]);
        return;
      }
      if (e.type === "tool_execution_end") {
        const id = String(e.toolCallId || "");
        if (!id) return;
        setItems((prev) =>
          prev.map((it) =>
            it.kind === "tool" && it.toolId === id
              ? {
                  ...it,
                  // ★ 命令与输出必须分开存：之前两者共用一个 detail 字段，
                  //   end 一到就把 args 覆盖成 result，折叠态那行「命令」直接变成输出，
                  //   复现链又断了（截图 v4 抓到的就是这个）。
                  out: String(e.result || ""),
                  status: e.isError ? "error" : "ok",
                  ms: typeof e.durationMs === "number" ? e.durationMs : it.ms,
                }
              : it,
          ),
        );
        return;
      }
      if (e.type === "message_end") {
        const m = e.message as Msg | undefined;
        if (!m || m.role !== "assistant") return;
        const id = curRef.current;
        const full = textOf(m.content);
        setItems((prev) =>
          prev.map((it) =>
            it.kind === "assistant" && it.id === id
              ? { ...it, text: full || it.text, streaming: false }
              : it,
          ),
        );
        return;
      }
      if (e.type === "turn_end") {
        setBusy(false);
        curRef.current = "";
      }
    });
    return off;
  }, []);

  // rtId 变了要重建闭包里的判断，用 ref 兜一层
  const rtRef = useRef("");
  rtRef.current = rtId;

  // #18 自动滚动三规则（照 ARTEX sessions-tab.tsx:1445-1490 的判定）
  //   ① 用户没贴底就不自动滚 —— 人手往上翻时每来一条流式增量都被拽回底部，等于没法读历史
  //   ② 贴底时新消息 / 流式增量正常跟随
  //   ③ 内容被撑高（工具卡展开、长文本、图片加载）也走同一条判定，不新增第二个「无脑贴底」入口
  // 触顶拉上一页 / 翻页保位那两条这里没有对应物：对话流不分页，items 全量在内存里。
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const onScroll = () => {
      atBottomRef.current = el.scrollTop + el.clientHeight >= el.scrollHeight - 60;
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    onScroll(); // 初次进来就算一次，别默认「贴底」是拍脑袋的 true
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  // 贴底时跟随。requestAnimationFrame 是为了等 React 把新节点排版完再量 scrollHeight，
  // 否则量到的是旧高度，每轮都差一截（永远差一条消息的高度）。
  useEffect(() => {
    const el = boxRef.current;
    if (!el || !atBottomRef.current) return;
    const h = requestAnimationFrame(() => {
      if (atBottomRef.current) el.scrollTop = el.scrollHeight;
    });
    return () => cancelAnimationFrame(h);
  }, [items]);

  // 容器尺寸不变、内容变高（展开工具卡 / 图片加载 / 流式增量）时兜底。
  // 这里不能用 ResizeObserver 观察 .stream 自己：它是 flex 固定高度，内容变高不改变它自身盒子，
  // 观察不到；改用 MutationObserver 看内容有没有新增/改写，判定仍然先问「贴底吗」。
  useEffect(() => {
    const el = boxRef.current;
    if (!el || typeof MutationObserver === "undefined") return;
    const mo = new MutationObserver(() => {
      if (atBottomRef.current) el.scrollTop = el.scrollHeight;
    });
    mo.observe(el, { childList: true, subtree: true, characterData: true });
    return () => mo.disconnect();
  }, []);

  // #41 定位回跳：审证页点「定位调用」→ 展开并滚到含这段文本的最后一张工具卡。
  // 倒着找的理由：同一段输出常被多条命令重复打印，最近的那条才是「刚打出来的」。
  useEffect(() => {
    const run = () => {
      const text = takeLocate();
      if (!text) return;
      const list = itemsRef.current;
      for (let i = list.length - 1; i >= 0; i--) {
        const it = list[i];
        if (it.kind === "tool" && (it.out || "").includes(text)) {
          // 人主动离开底部去看历史，就别让贴底跟随立刻把人拽回去
          atBottomRef.current = false;
          setFocusId(it.toolId || it.id);
          return;
        }
      }
      // 找不到必须说出来：工具输出只留尾部 4KB，命中的那条可能已经被截断
      setItems((p) => [...p, { kind: "note", text: "没找到包含该内容的工具输出（输出只留尾部 4KB，可能已被截断）", id: nextId("n") }]);
    };
    // 挂载时也跑一次：跨页跳转时事件在本页挂载之前就发完了，pending 才是真正的载体
    const off = onLocate(run);
    run();
    return off;
  }, []);

  // #27：模型下拉的数据只在挂载时拉一次 —— get_available_models 是 RPC（几十秒超时），
  // 放进轮询会把对话页拖卡；切换后 pi 自己的状态由切换回执回读，不必刷新这个列表。
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const d = (await api.models()) as {
          providers?: { id: string; name?: string; configured?: boolean; models?: { id: string; name?: string }[] }[];
          current?: { provider: string; id: string } | null;
        };
        const list: { key: string; label: string }[] = [];
        for (const p of d.providers || []) {
          if (p.configured === false) continue;
          for (const m of p.models || []) {
            list.push({ key: p.id + "|" + m.id, label: (p.name || p.id) + " / " + (m.name || m.id) });
          }
        }
        if (!alive) return;
        setModelChoices(list);
        if (d.current) setCurModel(d.current.provider + "|" + d.current.id);
      } catch { /* 模型接口挂了不该让对话页开不了：下拉留空即可 */ }
    })();
    return () => { alive = false; };
  }, []);

  /** #27 换模型：失败要回滚下拉并明说原因，不能让人以为已经切过去了 */
  async function switchModel(v: string) {
    if (!v) return;
    const cut = v.indexOf("|");
    const provider = v.slice(0, cut);
    const modelId = v.slice(cut + 1);
    const prev = curModel;
    setCurModel(v);
    try {
      const r = (await api.modelsActive({ provider, modelId })) as { ok?: boolean; note?: string; error?: string };
      if (!r.ok) throw new Error(r.error || "切换失败");
      setItems((p) => [...p, { kind: "note", text: r.note || "已切到 " + provider + "/" + modelId + "，从下一轮 LLM 调用生效", id: nextId("n") }]);
    } catch (e) {
      setCurModel(prev);
      setItems((p) => [...p, { kind: "note", text: "切换模型失败：" + (e instanceof Error ? e.message : String(e)) + "（仍用原来的模型）", id: nextId("n") }]);
    }
  }

  async function send() {
    const t = text.trim();
    if (!t || busy) return;
    setErr("");
    setText("");
    // 自己发出去的消息一定跟到底部（这不是「别人插话把你拽走」那种情况）
    atBottomRef.current = true;
    setItems((p) => [...p, { kind: "user", text: t, id: nextId("u") }]);
    setBusy(true);
    // ★ 2026-11-05 修：原来只有 turn_end 才把 busy 放回 false。
    //   提交失败（比如 401/403/网络断）走的是 catch 分支，而 catch 里没有 setBusy(false) —— 
    //   于是「运行中…」永远不结束、「中止」永远灰着，用户只能刷新页面。
    //   实测症状：发送后端拒绝时，面板上发送按钮卡在"运行中…"、中止按钮 disabled=true。
    let ok = false;
    try {
      const r = (await api.command({ type: "prompt", message: t, runtimeId: rtRef.current })) as {
        success?: boolean;
        error?: string;
        runtimeId?: string;
      };
      if (r.runtimeId) setRtId(r.runtimeId);
      if (!r.success) throw new Error(r.error || "发送失败");
      ok = true;
    } catch (e) {
      setBusy(false);
      setErr(e instanceof Error ? e.message : String(e));
      setItems((p) => [...p, { kind: "note", text: "发送失败：" + String(e), id: nextId("n") }]);
    } finally {
      // 只兜「提交就失败」这一种：真跑起来了要靠 turn_end 收尾（那里才是任务结束）
      if (!ok) setBusy(false);
    }
  }

  /**
   * 中止：**先确认再发**。
   * ★ 2026-11-05：原来点了就发 —— 赛时旁边的「发送」按钮只隔 8px，手滑一下，
   *   正在跑的那条工具调用就没了（不只是白丢一次，重跑还得重新付 token）。
   *   代价是多一下就绪成本，收益是「手滑不会掉进度」。
   */
  async function stop() {
    if (!confirm("中止正在跑的这条任务？\n\n已产出的工具输出会保留；重跑要重新花预算。")) return;
    try {
      await api.command({ type: "abort", runtimeId: rtRef.current });
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  const empty = useMemo(() => items.length === 0, [items]);

  return (
    <div className="page">
      <div className="chat">
        <div className="stream" ref={boxRef}>
          {/* ★ 2026-11-05：原来这里只有一句「还没有对话」，一屏 1000px 全是空白。
              空白态的根子不是缺装饰，是**没有决策可做** —— 所以换成「下一刀」卡：
              黑板当前可动的三刀 + 证据强度，点一下直接填进输入框。
              抢分时最贵的是从零想「现在该干嘛」的那段时间。 */}
          {empty ? (
            <div className="emptychat">
              <NextMoveCard onPick={(t) => setText(t)} />
              <div className="empty">也可以直接在下面输入指令，任务交给容器里的 pi 执行。</div>
            </div>
          ) : null}
          {items.map((it) => {
            if (it.kind === "note") {
              return (
                <div key={it.id} className="msg note">
                  {it.text}
                </div>
              );
            }
            // 工具不再是灰字气泡：改成可折叠工具卡（默认收起，只露命令 + 状态 + 耗时）
            if (it.kind === "tool") {
              return (
                <ToolCard
                  key={it.id}
                  it={{ id: it.toolId || it.id, name: it.name, args: it.args, output: it.out, status: it.status, ms: it.ms }}
                  focused={focusId !== "" && (it.toolId || it.id) === focusId}
                  calls={it.name ? callCounts[it.name] || 0 : 0}
                />
              );
            }
            const label = it.kind === "user" ? "你" : "pi";
            return (
              <div key={it.id} className={"msg " + it.kind}>
                <div className="role">
                  {label}
                  {it.kind === "assistant" && it.streaming ? " · 输出中" : ""}
                </div>
                <div className="body">{it.text}</div>
              </div>
            );
          })}
        </div>
        {err ? <div className="err">{err}</div> : null}
        <div className="composer">
          {/* #35 旁路提问的输入条：默认收起，用完即走 —— 它不是第二条主线，别占常驻位置 */}
          {btwOpen ? (
            <div className="btwbar" style={{ display: "flex", gap: 8, marginBottom: 8 }}>
              <input
                style={{ flex: 1 }}
                placeholder="旁路问一句（独立进程 · 无工具 · 不写会话与黑板）"
                value={btwQ}
                onChange={(e) => setBtwQ(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key !== "Enter" || e.nativeEvent.isComposing) return;
                  e.preventDefault();
                  void askBtw();
                }}
              />
              <button className="mini" onClick={() => void askBtw()} disabled={btwBusy || !btwQ.trim()}>
                {btwBusy ? "问…" : "问"}
              </button>
            </div>
          ) : null}
          <textarea
            placeholder={"输入指令（" + sendModeHint(mode) + "）"}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              // #26：先放行输入法组字（组字时回车是选词），再按模式判要不要发送
              if (!shouldSubmitOnKey(e, mode)) return;
              e.preventDefault();
              void send();
            }}
          />
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <button onClick={() => void send()} disabled={busy || !text.trim()}>
              {busy ? "运行中…" : "发送"}
            </button>
            <button onClick={() => void stop()} disabled={!busy}>
              中止
            </button>
            <button
              onClick={() => setBtwOpen((v) => !v)}
              title="旁路提问（/btw）：独立进程、无工具、不进当前会话与黑板；超时 120s，最多同时 2 路"
            >
              /btw
            </button>
            <select
              value={curModel}
              disabled={modelChoices.length === 0}
              title="运行中换模型：只对下一轮 LLM 调用生效，当前这轮与已有上下文都不受影响"
              onChange={(e) => void switchModel(e.target.value)}
            >
              {modelChoices.length === 0 ? <option value="">模型列表不可用</option> : null}
              {curModel && !modelChoices.some((c) => c.key === curModel) ? <option value={curModel}>{curModel.replace("|", " / ")}（当前）</option> : null}
              {modelChoices.map((c) => (
                <option key={c.key} value={c.key}>{c.label}</option>
              ))}
            </select>
            <select
              value={mode}
              title="发送键位（切到 Enter 后：输入法组字时回车仍是选词，不会误发）"
              onChange={(e) => setSendMode(e.target.value as SendMode)}
            >
              <option value="ctrl-enter">Ctrl+Enter</option>
              <option value="enter">Enter</option>
            </select>
          </div>
        </div>
      </div>
    </div>
  );
}