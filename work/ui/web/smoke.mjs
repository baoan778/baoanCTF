// 无浏览器环境下的"能不能挂上去"冒烟：跑在容器里，依赖容器内的 jsdom。
// 用法：docker exec -w /opt/pi-ctf/work/ui/web pictf node smoke.mjs
// 它挡的是最低级也最常见的失败：白屏（运行时报错 / 入口 404 / 没渲染）。
import { JSDOM } from "jsdom";
import { readFileSync } from "node:fs";

const html = readFileSync("dist/index.html", "utf8");
const m = /src="\/assets\/([^"]+\.js)"/.exec(html);
const jsFile = m ? m[1] : "";
if (!jsFile) {
  console.error("SMOKE_FAIL: index.html 没有 JS 入口");
  process.exit(1);
}
const js = readFileSync("dist/assets/" + jsFile, "utf8");
const dom = new JSDOM("<!doctype html><html><body><div id=\"root\"></div></body></html>", {
  url: "http://127.0.0.1:30142/",
  runScripts: "outside-only",
  pretendToBeVisual: true,
});
const window = dom.window;
const errors = [];
window.addEventListener("error", (e) => errors.push(String(e && e.message)));
// 打桩：不打真网络
window.fetch = async () => ({ ok: true, json: async () => ({}) });
window.EventSource = class {
  constructor() {
    this.onmessage = null;
    setTimeout(() => {
      if (this.onmessage) this.onmessage({ data: JSON.stringify({ type: "ui:hello", activeRuntimeId: "rt-x" }) });
    }, 0);
  }
  close() {}
};

try {
  window.eval(js);
} catch (e) {
  console.error("SMOKE_FAIL: 执行报错 " + e.message);
  process.exit(1);
}
await new Promise((r) => setTimeout(r, 300));
const root = window.document.getElementById("root");
const out = root ? root.innerHTML : "";
if (out.indexOf("PiCTF") < 0) {
  console.error("SMOKE_FAIL: 没渲染出 PiCTF（root 长度=" + out.length + "）");
  process.exit(1);
}
if (errors.length) {
  console.error("SMOKE_FAIL: 运行时报错 " + errors.join(" | "));
  process.exit(1);
}
console.log("SMOKE_OK 渲染字节=" + out.length + " 页签=" + (out.match(/class="tab/g) || []).length);
// ★ 2026-11-07 修：jsdom 会留活句柄（timer/EventSource 桩），不显式退出则进程挂住，
//   只能靠外层 timeout 杀掉 —— 表现为「冒烟永远跑不完」，极容易误判成页面挂了。
//   本仓纪律：冒烟脚本必须自己 process.exit（smoke-all / smoke-live 都有，唯独这份漏了）。
process.exit(0);