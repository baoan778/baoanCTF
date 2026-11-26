import { useMemo, useState } from "react";

/**
 * #42 报文高亮 + 有界预览 + 按需加载（ARTEX 的「报文要能读」纪律）。
 *
 * 为什么要有：工具卡的输出是**裸 pre** —— curl 的响应头和 body 糊成一片灰字，
 * 一条 nmap/httpx 动辄几十 KB 一起塞进 DOM，页面直接卡；人眼要花十几秒才能找到状态码。
 * 报文的三个信息点（状态行 / 头 / body 格式）本来是有结构的，结构化了才一眼可判。
 *
 * 三个边界：
 *  · 有界预览：首屏最多渲染 MAX_HEAD 字符，剩下的按需加载（渲染几十万字符会卡死页面）；
 *  · 格式探测失败就退回原 pre（高亮不许把原文改坏，这是只读视图）；
 *  · 只在「像 HTTP 报文」时才当报文渲染，普通日志不乱加颜色（避免误读）。
 */
const MAX_HEAD = 4000;

const STATUS_RE = /^(HTTP\/\d(?:\.\d)?)\s+(\d{3})\s*(.*)$/;
const REQ_RE = /^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(\S+)\s+(HTTP\/\d(?:\.\d)?)$/;
const HEADER_RE = /^([A-Za-z0-9!#$%&'*+.^_`|~-]+):\s?(.*)$/;

export type BodyKind = "http" | "json" | "text";

/** 只看前 8KB 判格式：几十 KB 的输出没必要为了探测全量扫一遍 */
export function detectBodyFormat(s: string): BodyKind {
  const head = s.slice(0, 8192);
  const first = head.split("\n", 1)[0].replace(/\r$/, "");
  if (STATUS_RE.test(first) || REQ_RE.test(first)) return "http";
  const t = head.trim();
  if ((t.startsWith("{") && t.endsWith("}")) || (t.startsWith("[") && t.endsWith("]"))) return "json";
  return "text";
}

/** 报文拆成「头 + 空行 + body」；没有空行时整体当头（否则会把 body 首行误判成头） */
function splitHttp(s: string): { head: string[]; body: string } {
  const i = s.indexOf("\n\n");
  const j = s.indexOf("\r\n\r\n");
  const cut = j >= 0 && (i < 0 || j < i) ? j : i;
  if (cut < 0) return { head: s.split("\n"), body: "" };
  return { head: s.slice(0, cut).split("\n"), body: s.slice(cut + (cut === j ? 4 : 2)) };
}

const JSON_TOKEN = /("(?:\\.|[^"\\])*"\s*:)|("(?:\\.|[^"\\])*")|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|\b(true|false|null)\b/g;

/** JSON 词法着色：键 / 字符串 / 数字 / 字面量 分开上色（只做前端渲染，不改原文） */
function jsonNodes(s: string) {
  const out: React.ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  JSON_TOKEN.lastIndex = 0;
  while ((m = JSON_TOKEN.exec(s))) {
    if (m.index > last) out.push(s.slice(last, m.index));
    const cls = m[1] ? "hb-key" : m[2] ? "hb-str" : m[3] ? "hb-num" : "hb-lit";
    out.push(<span key={"t" + k++} className={cls}>{m[0]}</span>);
    last = m.index + m[0].length;
  }
  if (last < s.length) out.push(s.slice(last));
  return out;
}

function statusClass(code: string): string {
  const n = Number(code);
  if (n >= 200 && n < 400) return "hb-status hb-ok";
  if (n >= 400) return "hb-status hb-bad";
  return "hb-status";
}

export default function HttpBlock({ text }: { text: string }) {
  const [all, setAll] = useState(false);
  const kind = useMemo(() => detectBodyFormat(text), [text]);
  const shown = all ? text : text.slice(0, MAX_HEAD);
  const hidden = text.length - shown.length;

  const body = useMemo(() => {
    if (kind !== "http") return null;
    return splitHttp(shown);
  }, [kind, shown]);

  return (
    <div className="hb">
      {kind === "http" && body ? (
        <>
          <pre className="tc-pre hb-pre">
            {body.head.map((ln, i) => {
              const st = STATUS_RE.exec(ln);
              if (st) {
                return (
                  <div key={"h" + i}>
                    <span className="hb-proto">{st[1]}</span>{" "}
                    <span className={statusClass(st[2])}>{st[2]}</span>
                    {st[3] ? <span className="hb-reason"> {st[3]}</span> : null}
                  </div>
                );
              }
              const hd = HEADER_RE.exec(ln);
              if (hd) {
                return (
                  <div key={"h" + i}>
                    <span className="hb-hname">{hd[1]}</span>
                    <span className="hb-colon">: </span>
                    <span className="hb-hval">{hd[2]}</span>
                  </div>
                );
              }
              return <div key={"h" + i}>{ln}</div>;
            })}
          </pre>
          {body.body ? (
            <>
              <div className="tc-label">body</div>
              <pre className="tc-pre hb-pre">{detectBodyFormat(body.body) === "json" ? jsonNodes(body.body) : body.body}</pre>
            </>
          ) : null}
        </>
      ) : kind === "json" ? (
        <pre className="tc-pre hb-pre">{jsonNodes(shown)}</pre>
      ) : (
        <pre className="tc-pre">{shown}</pre>
      )}
      {hidden > 0 ? (
        <div className="row hb-more">
          <span className="muted">还有 {hidden} 字符没渲染</span>
          <button className="mini" onClick={() => setAll(true)}>加载全部</button>
          <span className="muted">（长输出整段渲染会让页面卡，按需再开）</span>
        </div>
      ) : null}
    </div>
  );
}