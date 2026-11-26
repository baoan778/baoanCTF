import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";

const BASE = "http://127.0.0.1:30142";
const html = readFileSync("dist/index.html", "utf8");
const mm = /src="\/assets\/([^"]+\.js)"/.exec(html);
if (!mm) { console.error("FAIL 无入口"); process.exit(1); }
const js = readFileSync("dist/assets/" + mm[1], "utf8");

// 认 data-tab 钩子而不是按钮文案：文案会改（这次加了图标），钩子才代表契约。
const tabs = ["chat", "board", "roles", "skills", "files", "models", "sessions", "subagents", "inspect", "system"];
// ★ 2026-11-18：口令闸门已删除，冒烟不再需要读 token 文件、也不再带 Authorization 头。
const LABEL = { chat: "对话", board: "黑板", roles: "智能体", skills: "技能", files: "文件", models: "模型", sessions: "会话", subagents: "子代理", inspect: "记录", system: "系统" };
let bad = 0;
for (const lab of tabs) {
  const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {
    url: BASE + "/", runScripts: "outside-only", pretendToBeVisual: true,
  });
  const w = dom.window;
  const errs = [];
  w.addEventListener("error", (e) => errs.push(String(e && e.message)));
  // 真打后端。2026-11-18 起不再需要口令头（闸门已删）。
  w.fetch = ((u, o) => {
    const url = String(u).startsWith("http") ? String(u) : BASE + String(u);
    return fetch(url, { ...(o || {}) });
  });
  w.EventSource = class {
    constructor() {
      this.onmessage = null;
      setTimeout(() => { if (this.onmessage) this.onmessage({ data: JSON.stringify({ type: "ui:hello", activeRuntimeId: "rt-new-1" }) }); }, 0);
    }
    close() {}
  };
  try { w.eval(js); } catch (e) { console.error("FAIL 执行 " + (e && e.message)); process.exit(1); }
  await new Promise((r) => setTimeout(r, 400));
  const btns = Array.from(w.document.querySelectorAll("button.tab"));
  const target = btns.find((b) => b.getAttribute("data-tab") === lab);
  if (!target) { console.error("FAIL 找不到 " + lab); bad++; w.close(); continue; }
  target.click();
  await new Promise((r) => setTimeout(r, 900));
  const root = w.document.getElementById("root");
  const out = root ? root.innerHTML : "";
  const hasErrBox = /class="err"/.test(out);
  const errTxt = (out.match(/class="err">([^<]{0,120})/) || [])[1] || "";
  if (errs.length) { console.error("FAIL " + lab + " 运行时: " + errs.join(" | ")); bad++; }
  else if (hasErrBox) { console.error("FAIL " + lab + " 页面报错框: " + errTxt); bad++; }
  else if (out.length < 150) { console.error("FAIL " + lab + " 渲染过空 " + out.length); bad++; }
  else console.log("OK " + (LABEL[lab] || lab) + " (" + out.length + "B)" + (out.includes("加载中") ? " [含加载中]" : ""));
  w.close();
}
if (bad) { console.error("BAD=" + bad); process.exit(1); }
console.log("LIVE_TABS_OK");
process.exit(0);