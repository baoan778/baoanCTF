import { useEffect, useState } from "react";
import ChatPage from "./pages/ChatPage";
import BoardPage from "./pages/BoardPage";
import RolesPage from "./pages/RolesPage";
import SkillsPage from "./pages/SkillsPage";
import FilesPage from "./pages/FilesPage";
import ModelsPage from "./pages/ModelsPage";
import SessionsPage from "./pages/SessionsPage";
import SubagentsPage from "./pages/SubagentsPage";
import InspectPage from "./pages/InspectPage";
import BenchPage from "./pages/BenchPage";
import SystemPage from "./pages/SystemPage";
import { api } from "./api";
import { usePoll } from "./lib/poll";
import { publishStatus } from "./lib/statusStore";
import { LOCATE_EVENT } from "./lib/locate";
import OpsBar from "./components/OpsBar";

type Tab =
  | "chat" | "bench" | "board" | "roles" | "skills"
  | "files" | "models" | "sessions" | "subagents" | "inspect" | "system";

// 图标用字符而不是 emoji：emoji 在不同系统渲染差异大，侧栏会跳字。
// 图标用内联 SVG：字符图标（◈▦◎）在不同字体下宽度不一，侧栏会「跳字」，
// 而且没法上色。SVG 统一 16x16 viewBox，随 currentColor 变色。
const I = {
  chat: "M4 4h16v12H7l-3 3V4z",
  board: "M3 4h18v4H3zM3 11h8v9H3zM13 11h8v9h-8z",
  inspect: "M4 6h16M4 12h16M4 18h10",
  subagents: "M6 3v6a3 3 0 003 3h6a3 3 0 013 3v6M6 21v-6a3 3 0 013-3",
  files: "M4 4h7l2 3h7v13H4z",
  roles: "M12 3a4 4 0 100 8 4 4 0 000-8zM5 21v-2a5 5 0 015-5h4a5 5 0 015 5v2",
  skills: "M12 3l2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z",
  models: "M4 7l8-4 8 4v10l-8 4-8-4z M4 7l8 4 8-4 M12 11v10",
  sessions: "M12 7v5l3 3M12 21a9 9 0 110-18 9 9 0 010 18z",
  system: "M12 15a3 3 0 100-6 3 3 0 000 6zM19 12l2-1-2-1m-9 7l-1 2 1-2m-4-4l-2-1 2-1",
  bench: "M4 20V10M10 20V4M16 20v-7M22 20H2",
};

function Ico({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

// 分组：作战（赛中高频）/ 资产（赛前配）/ 系统（排障）。
// 10 个平铺等于每次都要扫一遍；分组后三秒能找到要的那个。
type Group = "作战" | "资产" | "系统";

const TABS: { id: Tab; label: string; ico: keyof typeof I; desc: string; group: Group }[] = [
  { id: "bench",     label: "实况",   ico: "bench",     desc: "跑分实况：进度 / 在跑哪题 / 待解队列", group: "作战" },
  { id: "chat",      label: "对话",   ico: "chat",      desc: "和容器里的 pi 交互，事件走 SSE", group: "作战" },
  { id: "board",     label: "黑板",   ico: "board",     desc: "FGS 黑板：节点 / 边 / 已核验事实", group: "作战" },
  { id: "inspect",   label: "记录",   ico: "inspect",   desc: "战果扫描：出现过哪些 flag", group: "作战" },
  { id: "subagents", label: "子代理", ico: "subagents", desc: "并行子代理的实时状态", group: "作战" },
  { id: "files",     label: "文件",   ico: "files",     desc: "浏览 / 搜索 / 上传 / 预览", group: "资产" },
  { id: "roles",     label: "智能体", ico: "roles",     desc: "工作区智能体档案与提示词", group: "资产" },
  { id: "skills",    label: "技能",   ico: "skills",    desc: "已装载的 CTF 技能库", group: "资产" },
  { id: "models",    label: "模型",   ico: "models",    desc: "供应商、密钥与模型目录", group: "系统" },
  { id: "sessions",  label: "会话",   ico: "sessions",  desc: "历史会话、运行实例与会话树", group: "系统" },
  { id: "system",    label: "系统",   ico: "system",    desc: "运行时状态、工具链与默认值", group: "系统" },
];

// ── 赛时 / 复盘 双模式（D-W1，2026-11-05 定案）─────────────────────────────
// 结论：**赛时只留「对话 / 黑板 / 记录」三个页签**，其余折叠进「更多」。
//
// 为什么是这三个（不是别的组合）：
//   · 对话 = 下指令的唯一入口，没有它什么都干不了；
//   · 黑板 = 唯一能看「做过什么、什么已证伪、还剩哪几条路」的地方（抗上下文压缩靠它）；
//   · 记录 = flag 三态账本所在，直接回答「哪个 flag 算分」。
// 被折叠的七个（智能体/技能/文件/模型/会话/子代理/系统）都是**赛前准备或赛后排障**用的：
// 技能库与智能体档案开赛前就配好了；进程、会话树、默认值这些是出问题才查的。
// 赛中把它们摆在侧栏 = 每次瞄一眼都在做选择，而选择是有成本的（这是 U3 那条推断的用法）。
//
// ⚠ 关键实现约束：被折叠的页签**仍然渲染在 DOM 里**（塞进 <details>，默认收起）。
//   如果直接从 DOM 里摘掉，smoke-all / smoke-live 按 data-tab 找不到按钮 → 假红；
//   更重要的是「赛时想临时看一眼系统页」不该逼人改代码或改设置。
//   折叠 = 视觉上只剩三个，能力上一个没少。
// 2026-11-05 定案修正：赛时页签从 3 个扩到 5 个 —— 补上「子代理」和「文件」。
//   子代理：赛时要盯并发跑没跑起来；
//   文件：赛时要拖附件/看落地的题包，而上传入口只在文件页。
// 剩下 5 个（智能体/技能/模型/会话/系统）才是真正的赛前配置与排障页。
// 2026-11-19：加「实况」并放进赛时核心组 —— 之前 5 个页签里**没有一个**显示
// 跑分链路（parallel_solver→sdkshard→pi）的状态，用户只能 terminal tail 日志。
// 实况页是唯一能回答「现在跑到哪、谁在跑、下一刀砍哪」的地方，必须赛中可见。
const CORE_TABS: Tab[] = ["bench", "chat", "board", "inspect", "subagents", "files"];
type Mode = "match" | "review";


export default function App() {
  const [tab, setTab] = useState<Tab>("chat");
  const [ready, setReady] = useState<boolean | null>(null);
  const [ver, setVer] = useState<string>("");
  // 默认赛时（这是作战台，默认就该是打仗的样子）；选择记在 localStorage 里
  // 口令闸门 2026-11-18 已整条删除（服务端 /api/auth 与 token.txt 一并没了），
  // 界面不再有任何「先验口令」分支 —— 边界只剩同源校验 + 端口绑 loopback。
  const [mode, setMode] = useState<Mode>(() => {
    try { return (localStorage.getItem("pictf.mode") as Mode) || "match"; } catch { return "match"; }
  });
  const isCore = (id: Tab) => CORE_TABS.includes(id);
  const pickMode = (m: Mode) => {
    setMode(m);
    try { localStorage.setItem("pictf.mode", m); } catch { /* 无痕模式兜底 */ }
    // 切回赛时时如果正停在非核心页，把页签拉回对话 —— 免得「看不见的页还开着」
    if (m === "match" && !isCore(tab)) setTab("chat");
  };

  // 顶部那个绿点必须是真的在查，不是写死 true。
  // #22：轮询去重（payload 没变就不 setState），并且全站只拉这一路 /api/status ——
  //      拉到的整份状态 publish 到 statusStore，SystemPage 从那里订阅（原来两个页面各拉一次）。
  usePoll(
    () => api.status() as Promise<{ ok?: boolean }>,
    5000,
    (s) => {
      publishStatus(s);
      setReady(!!s.ok);
    },
    { onError: () => setReady(false) },
  );

  useEffect(() => {
    void api
      .defaults()
      .then((d) => setVer(String((d as { piVersion?: string }).piVersion || "")))
      .catch(() => setVer(""));
  }, []);

  // 键盘快捷键：10 个页签靠鼠标点太慢，赛时切页是高频动作。
  //   Cmd/Ctrl + 1..9  → 切到第 N 个（赛时可见的）页签
  //   Cmd/Ctrl + \     → 赛时/复盘 切换
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      if (!mod) return;
      if (e.key === "\\") {
        e.preventDefault();
        setMode((m) => (m === "match" ? "review" : "match"));
        return;
      }
      const n = parseInt(e.key, 10);
      if (!Number.isFinite(n) || n < 1 || n > 9) return;
      const visible = TABS.filter((t) => mode === "review" || isCore(t.id));
      const target = visible[n - 1];
      if (target) { e.preventDefault(); setTab(target.id); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mode]);

  // #41 定位回跳：别的页（审证页）点了「定位调用」→ 切到对话页，由 ChatPage 自己取 pending 焦点。
  // 这里只负责切页：定位动作本身必须留在 ChatPage，因为 tab 切过去之前它还没挂载。
  useEffect(() => {
    const go = () => setTab("chat");
    window.addEventListener(LOCATE_EVENT, go);
    return () => window.removeEventListener(LOCATE_EVENT, go);
  }, []);

  const page = () => {
    switch (tab) {
      case "bench": return <BenchPage />;
      case "chat": return <ChatPage />;
      case "board": return <BoardPage />;
      case "roles": return <RolesPage />;
      case "skills": return <SkillsPage />;
      case "files": return <FilesPage />;
      case "models": return <ModelsPage />;
      case "sessions": return <SessionsPage />;
      case "subagents": return <SubagentsPage />;
      case "inspect": return <InspectPage />;
      case "system": return <SystemPage />;
      default: return null;
    }
  };

  const cur = TABS.find((t) => t.id === tab);

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <img src="/pictf-256.png" alt="" />
          <div>
            PiCTF
            <div className="sub">CTF 作战台</div>
          </div>
        </div>
        <nav className="nav">
          {/* 赛时：只显示核心组（作战 + 资产里的文件），按 group 分块；
              复盘：三组全展开。分组标题让 10 个页签不再是一锅粥。 */}
          {(["作战", "资产", "系统"] as Group[]).map((g) => {
            const items = TABS.filter((t) => t.group === g && (mode === "review" || isCore(t.id)));
            if (!items.length) return null;
            return (
              <div key={g}>
                <div className="group">{g}</div>
                {items.map((t) => (
                  <button
                    key={t.id}
                    className={"tab" + (tab === t.id ? " on" : "")}
                    onClick={() => setTab(t.id)}
                    title={t.desc}
                    data-tab={t.id}
                  >
                    <span className="ico"><Ico d={I[t.ico]} /></span>
                    {t.label}
                  </button>
                ))}
              </div>
            );
          })}

          {/* 赛时被折叠的核心外页签：用 details 而不是条件渲染 —— DOM 里始终在，
              冒烟脚本还能按 data-tab 找到它们，能力也随时可用。 */}
          {mode === "match" ? (
            <details className="more">
              <summary>{"更多 · 复盘用 " + TABS.filter((t) => !isCore(t.id)).length}</summary>
              {TABS.filter((t) => !isCore(t.id)).map((t) => (
                <button
                  key={t.id}
                  className={"tab" + (tab === t.id ? " on" : "")}
                  onClick={() => setTab(t.id)}
                  title={t.desc}
                  data-tab={t.id}
                >
                  <span className="ico"><Ico d={I[t.ico]} /></span>
                  {t.label}
                </button>
              ))}
            </details>
          ) : null}
        </nav>
        <div className="foot">
          {ver ? <span className="badge">pi {ver}</span> : null}
          <span className="badge">
            <span className={"dot " + (ready ? "ok" : ready === false ? "bad" : "")} />
            {ready === null ? "连接中" : ready ? "已就绪" : "未连接"}
          </span>
          <button
            className="modebtn"
            onClick={() => pickMode(mode === "match" ? "review" : "match")}
            title={mode === "match" ? "切到复盘：展开全部 10 个页签" : "切回赛时：只留 对话/黑板/记录/子代理/文件"}
          >
            {mode === "match" ? "赛时" : "复盘"}
          </button>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <span className="title">{cur?.label}</span>
          <span className="desc">{cur?.desc}</span>
          <span className="spacer" />
          {/* 快捷键提示：藏起来用户永远发现不了，直接印在顶栏 */}
          <span className="hint mono" title="Cmd/Ctrl + 数字 切换页签；Cmd/Ctrl + \ 切换赛时/复盘">
            ⌘1-9 切页签 · ⌘\ 切模式
          </span>
          <span className={"badge " + (mode === "match" ? "match" : "review")}>
            {mode === "match" ? "赛时" : "复盘"}
          </span>
        </header>
        {/* 作战条：常驻在所有页签之上 —— 时间/得分/下一刀 不该藏进某个页里（信条 P1/P3） */}
        <OpsBar />
        {page()}
      </div>
    </div>
  );
}
