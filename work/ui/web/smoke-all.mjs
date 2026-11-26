import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";

const html = readFileSync("dist/index.html", "utf8");
const m = /src="\/assets\/([^"]+\.js)"/.exec(html);
if (!m) { console.error("FAIL: 无入口"); process.exit(1); }
const js = readFileSync("dist/assets/" + m[1], "utf8");

// ★ 2026-11-05 修：侧栏按钮的 textContent 是「图标字符 + 标签」（如 "◈对话"），
//   原来用 === 精确比对，10 个页签全部匹配不上 → 冒烟永远 BAD=10（假红）。
//   改成按 data-tab 定位，标签名只用于显示。
const labels = [
  ["chat", "对话"], ["board", "黑板"], ["roles", "智能体"], ["skills", "技能"], ["files", "文件"],
  ["models", "模型"], ["sessions", "会话"], ["subagents", "子代理"], ["inspect", "记录"], ["system", "系统"],
];
let bad = 0;
for (const [tabId, lab] of labels) {
  const dom = new JSDOM("<!doctype html><html><body><div id=\"root\"></div></body></html>", {
    url: "http://127.0.0.1:30142/", runScripts: "outside-only", pretendToBeVisual: true,
  });
  const w = dom.window;
  const errs = [];
  w.addEventListener("error", (e) => errs.push(String(e && e.message)));
  // ★ 2026-11-18：口令闸门已删除，首屏不再打 /api/auth —— 这里只是把所有请求兜成成功。
  w.fetch = async () => ({ ok: true, json: async () => ({}) });
  w.EventSource = class {
    constructor() {
      this.onmessage = null;
      setTimeout(() => { if (this.onmessage) this.onmessage({ data: JSON.stringify({ type: "ui:hello", activeRuntimeId: "rt-x" }) }); }, 0);
    }
    close() {}
  };
  try { w.eval(js); } catch (e) { console.error("FAIL 执行: " + (e && e.message)); process.exit(1); }
  await new Promise((r) => setTimeout(r, 80));
  const btns = Array.from(w.document.querySelectorAll("button.tab"));
  const target = btns.find((b) => b.getAttribute("data-tab") === tabId) || btns.find((b) => (b.textContent || "").includes(lab));
  if (!target) { console.error("FAIL 找不到页签 " + lab); bad++; w.close(); continue; }
  target.click();
  await new Promise((r) => setTimeout(r, 120));
  const root = w.document.getElementById("root");
  const out = root ? root.innerHTML : "";
  if (errs.length) { console.error("FAIL " + lab + ": " + errs.join(" | ")); bad++; }
  else if (out.length < 100) { console.error("FAIL " + lab + ": 渲染过空 " + out.length); bad++; }
  else console.log("OK " + lab + " (" + out.length + " 字节)");
  w.close();
}
if (bad) { console.error("BAD=" + bad); process.exit(1); }
console.log("ALL_TABS_OK");
// 定时器（OpsBar 5s 轮询、SSE 重连）会挂住事件循环 —— 显式退出，别让 CI 等 5 分钟
process.exit(0);