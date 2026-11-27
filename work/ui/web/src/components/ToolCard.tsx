import { useEffect, useRef, useState } from "react";
import HttpBlock from "./HttpBlock";

/**
 * 工具事件卡
 *
 * 为什么要有：AGENTS.md 的 flag 纪律写着 ——「值拆自二进制/内存的，把**取到它的那条命令
 * 与原始输出**一起给，方便别人复现」。之前对话页只渲染 text_delta，工具调用整条被丢掉，
 * 「这条 flag 是哪条命令打出来的」在界面上根本没有痕迹 —— 复现链断在第一环。
 *
 * 设计取舍：
 *  · 默认**折叠**，只露一行「命令 + 状态 + 耗时」。赛中屏幕是稀缺资源，展开是主动行为。
 *  · 折叠行显示**人类可读的命令**（从 args JSON 里取 command/cmd/path 那一个字段），
 *    而不是把整个 {"command":...,"timeout":10} 糊上去 —— 后者一行读不完，等于没显示。
 *  · 输出只保留尾部 4KB（服务端已截断）：真正要复现的是最后那段。
 */

export type ToolItem = {
  id: string;
  name: string;
  args?: string;
  output?: string;
  status?: "running" | "ok" | "error";
  ms?: number;
};

/** args 是 JSON 字符串（pi 传的是对象）：优先取最常见的几个键，取不到才回落到原文 */
function cmdOf(args?: string): string {
  if (!args) return "";
  const t = args.trim();
  if (!t.startsWith("{")) return t;
  try {
    const o = JSON.parse(t) as Record<string, unknown>;
    for (const k of ["command", "cmd", "path", "file_path", "pattern", "query", "url"]) {
      const v = o[k];
      if (typeof v === "string" && v) return v;
    }
    const first = Object.values(o).find((v) => typeof v === "string");
    return typeof first === "string" ? first : t;
  } catch {
    return salvage(t);
  }
}

/**
 * #23 被截断的 args 兜底。
 * 为什么会有截断：折叠行摘要由后端截到约 200 字符，JSON 尾巴必然被切断，JSON.parse 必失败。
 * ARTEX transcript.tsx:158-179 的做法是不放弃 —— 用正则在残缺文本里把 command 抠出来，
 * 并且容忍缺失的收尾引号。之前这里直接回落整段 JSON 原文，折叠行一行读不完 = 等于没显示。
 */
const CMD_RE = /"command"\s*:\s*"((?:\\.|[^"\\])*)/;

/** 单趟反转义：链式 replace 会把 \\n（转义过的反斜杠 + n）拆错，这里一次扫完 */
function unescapeJson(s: string): string {
  return s.replace(/\\(u[0-9a-fA-F]{4}|.)/g, (_m, c: string) => {
    if (c[0] === "u") return String.fromCharCode(parseInt(c.slice(1), 16));
    switch (c) {
      case "n": return "\n";
      case "t": return "\t";
      case "r": return "\r";
      case "b": return "\b";
      case "f": return "\f";
      case "v": return "\v";
      case "0": return "\0";
      default: return c; // \" \\ \/ 等按原字符
    }
  });
}

function salvage(t: string): string {
  const m = CMD_RE.exec(t);
  if (m && m[1]) return unescapeJson(m[1]).trim();
  return t;
}

/** 展开时给完整参数：JSON 美化，一行一个键；被截断时至少把 command 抠出来 */
function prettyArgs(args?: string): string {
  if (!args) return "";
  const t = args.trim();
  if (!t.startsWith("{")) return t;
  try { return JSON.stringify(JSON.parse(t), null, 2); } catch { return salvage(t); }
}

// #43 累计调用：同一工具在本次会话里已经调过几次（由对话页按卡片计数传入，不额外发请求）
export default function ToolCard({ it, focused, calls }: { it: ToolItem; focused?: boolean; calls?: number }) {
  // #41 定位回跳：从审证页点「定位调用」跳过来时，这张卡默认就展开（目标就是它的输出），
  // 并把自己滚进视野 —— 否则人还得在几十张卡里再找一遍。
  const [open, setOpen] = useState(!!focused);
  const boxRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!focused) return;
    const el = boxRef.current;
    if (!el) return;
    // block:"nearest" —— 已经在视野里就一点都不动，别把人正在看的那屏顶走
    el.scrollIntoView({ block: "nearest" });
  }, [focused]);
  const st = it.status || "running";
  const cmd = cmdOf(it.args);
  const full = prettyArgs(it.args);
  return (
    <div ref={boxRef} className={"toolcard " + st + (focused ? " focus" : "")}>
      <button
        className="tc-head"
        onClick={() => setOpen((v) => !v)}
        title={open ? "收起" : "展开完整命令与输出"}
      >
        <span className="tc-caret">{open ? "▾" : "▸"}</span>
        <span className="tc-name">{it.name}</span>
        <span className="tc-cmd mono">{cmd || "（无参数）"}</span>
        {/* #43 累计调用次数：只在真的重复调过时显示，第一次不占位置 */}
        {typeof calls === "number" && calls > 1 ? (
          <span className="tc-ms mono" title="本次会话里这个工具已调用 N 次">
            ×{calls}
          </span>
        ) : null}
        {typeof it.ms === "number" ? <span className="tc-ms mono">{it.ms}ms</span> : null}
        <span className={"tc-state " + st}>{st === "running" ? "跑" : st === "ok" ? "完" : "错"}</span>
      </button>
      {open ? (
        <div className="tc-body">
          {full ? (
            <>
              <div className="tc-label">参数</div>
              <pre className="tc-pre">{full}</pre>
            </>
          ) : null}
          {it.output ? (
            <>
              <div className="tc-label">输出（尾部 4KB）</div>
              {/* #42 报文高亮 + 有界预览：裸 pre 读不出状态码，几十 KB 一起渲染还会卡 */}
              <HttpBlock text={it.output} />
            </>
          ) : null}
          {!full && !it.output ? <div className="hint">没有留下参数或输出</div> : null}
        </div>
      ) : null}
    </div>
  );
}
