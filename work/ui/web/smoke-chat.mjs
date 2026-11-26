import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";

const BASE = "http://127.0.0.1:30142";
const html = readFileSync("dist/index.html", "utf8");
const js = readFileSync("dist/assets/" + /src="\/assets\/([^"]+\.js)"/.exec(html)[1], "utf8");

const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {
  url: BASE + "/", runScripts: "outside-only", pretendToBeVisual: true,
});
const w = dom.window;
const errs = [];
w.addEventListener("error", (e) => errs.push(String(e && e.message)));
w.fetch = ((u, o) => fetch(String(u).startsWith("http") ? String(u) : BASE + String(u), o));

// 自己实现一个 SSE 客户端（Node 这边可能没有全局 EventSource）
class SseShim {
  constructor(url) {
    this.onmessage = null; this.onerror = null; this.onopen = null;
    this._abort = new AbortController();
    this._run(url).catch(() => {});
  }
  async _run(url) {
    const res = await fetch(BASE + url, { signal: this._abort.signal, headers: { accept: "text/event-stream" } });
    if (this.onopen) this.onopen({});
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
        const lines = chunk.split("\n").filter((l) => l.startsWith("data:"));
        if (!lines.length) continue;
        const data = lines.map((l) => l.slice(5).trimStart()).join("\n");
        if (this.onmessage) this.onmessage({ data });
      }
    }
  }
  close() { this._abort.abort(); }
}
w.EventSource = SseShim;

w.eval(js);
await new Promise((r) => setTimeout(r, 1200));

const root = w.document.getElementById("root");
const ta = w.document.querySelector("textarea");
if (!ta) { console.error("FAIL 没有输入框"); console.log(root.innerHTML.slice(0, 500)); process.exit(1); }
const setter = Object.getOwnPropertyDescriptor(w.HTMLTextAreaElement.prototype, "value").set;
setter.call(ta, "只回复四个字：链路正常");
ta.dispatchEvent(new w.Event("input", { bubbles: true }));
await new Promise((r) => setTimeout(r, 150));
const sendBtn = Array.from(w.document.querySelectorAll("button")).find((b) => /发送|提交|发/.test(b.textContent) && !b.disabled);
if (!sendBtn) { console.error("FAIL 找不到可用的发送按钮"); process.exit(1); }
sendBtn.click();
console.log("已发送，等流式…");
await new Promise((r) => setTimeout(r, 14000));

const out = root.innerHTML;
if (errs.length) { console.error("FAIL 运行时: " + errs.join(" | ")); process.exit(1); }
if (!out.includes("链路正常")) { console.error("FAIL 用户消息没进时间线"); console.log(out.slice(0, 800)); process.exit(1); }
const asst = /class="msg assistant"[\s\S]{0,600}?class="body"[^>]*>([\s\S]{0,150}?)</.exec(out);
console.log("✓ 用户消息已渲染");
console.log("助手内容 =", asst ? JSON.stringify(asst[1].slice(0, 100)) : "（空/未到）");
console.log("DOM 长度 =", out.length);
console.log("CHAT_OK");
// SSE 是常开连接，不显式退出 Node 会一直挂着
process.exit(0);