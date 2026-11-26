/**
 * PiCTF: kill-guard —— 拦掉「按进程名批量杀」的机器闸门
 *
 * 为什么需要它（2026-11-20 15:47:03 实战血账，不是推测）
 * -------------------------------------------------------
 * xben-092-24 这条 shard 为了清理自己留下的进程，执行了：
 *     for pid in $(ps -eo pid,comm | awk '$2=="python3"{print $1}'); do kill $pid; done
 * 把它头顶的整条控制链一起打死：1390 supervise / 1391 parallel_solver / 51009 sdkshard，
 * 之后**整场空转 26 分钟**（没有 shard 在跑，也没人重新拉起），比赛后半程全部停摆。
 *
 * 根因不是模型"不听话"：那条命令在它看来就是一次普通清理，而且**没有任何闸门管它** ——
 * lane_gate 只管长任务/扫描/破坏系统目录，按名字 kill 不在其列。
 * 所以补**在途中**的机器闸门，与 lane-watch / flag-watch 同一套机制：
 *
 *   · tools/killguard.mjs            —— 纯逻辑（零依赖，node 直接跑，--selftest 40 条）
 *   · agent-template/extensions/kill-guard.ts —— tool_call 钩子：判定命中 → block
 *
 * 三条纪律（第 2 条与 lane-watch 不同，是刻意的）
 * ----------------------------------------------
 * 1. **只管命令位置**：grep 'kill' / echo "kill $pid" / 写脚本的 heredoc 一律不碰 ——
 *    错了会训练模型绕路，绕一次就永远绕。
 * 2. **判定命中就硬拦，不按次数放行**：一条 kill 打死控制链**没有 undo**；
 *    而"这条命令被拒"并不锁死 agent（其它工具照跑，理由里直接给了 4 条正确做法）。
 *    lane-watch 敢 fail-open 是因为它拦的是"还没派活"，代价只是慢一点。
 * 3. **异常一律放行**：模块加载失败 / 正则崩 / 读不到输入 → 静默放过，绝不拖累 pi 跑工具。
 *
 * 逃生通道（三道，都是为了"闸门自己有 bug 时不至于把整场锁死"）：
 *   · /killguard off         本会话关闭
 *   · PI_LAB_KILL_GUARD=0    整容器关闭（容器重启后恢复）
 *   · recover-mode           复用 pi 的零依赖逃生舱（globalThis.__PI_LAB_RECOVER）
 *
 * 纯 fs + 纯正则，不起进程；全程 try/catch。
 */

import * as fs from "node:fs";
import * as path from "node:path";
import { pathToFileURL } from "node:url";

/**
 * 找仓库根：候选按「**tools/killguard.mjs 真的存在**」验证，判据不是"路径长得像"。
 * （lane-watch 2026-11-02 的实测教训：裸信 process.cwd() 时，从非仓库目录启动会让闸门
 *  静默变哑弹 —— 台账里连一条记录都没有，比拦截错了更难发现。）
 */
function resolveLab(): string {
  const cands: string[] = [];
  try {
    const env = process.env.PI_LAB_ROOT;
    if (env) cands.push(env);
  } catch { /* 没有环境变量就算了 */ }
  try {
    let d = process.cwd();
    for (let i = 0; i < 12; i++) {
      cands.push(d);
      const up = path.dirname(d);
      if (up === d) break;
      d = up;
    }
  } catch { /* cwd 拿不到就只靠 env */ }
  for (const c of cands) {
    try {
      if (fs.existsSync(path.join(c, "tools", "killguard.mjs"))) return c;
    } catch { /* 单个候选读不动就跳过 */ }
  }
  try {
    return process.env.PI_LAB_ROOT || process.cwd();
  } catch {
    return ".";
  }
}

const LAB = resolveLab();
const MOD = path.join(LAB, "tools", "killguard.mjs");
const LOG =
  process.env.PI_LAB_KILLGUARD_LOG ||
  path.join(LAB, "log", "kill-guard.jsonl");
const MAX_LOG = 2 * 1024 * 1024; // 2MB 后轮转成 .1（防日志涨爆盘）

let mod: any = null;
let modTried = false;

async function loadMod() {
  if (mod || modTried) return mod;
  modTried = true;
  try {
    const m: any = await import(pathToFileURL(MOD).href);
    if (typeof m.check === "function") mod = m;
  } catch {
    mod = null; // fail-open：闸门静默失效，工具照跑
  }
  return mod;
}

function appendLog(rec: Record<string, unknown>) {
  try {
    fs.mkdirSync(path.dirname(LOG), { recursive: true });
    try {
      if (fs.statSync(LOG).size > MAX_LOG) fs.renameSync(LOG, LOG + ".1");
    } catch { /* 文件不存在 */ }
    fs.appendFileSync(LOG, JSON.stringify(rec) + "\n");
  } catch { /* 记不上不影响拦截 */ }
}

/** 零依赖逃生舱（recover-mode.ts 只写 globalThis 标记，两边无文件依赖） */
function recoverActive(): boolean {
  try {
    const g: any = (globalThis as any).__PI_LAB_RECOVER;
    return !!(g && typeof g.until === "number" && Date.now() < g.until);
  } catch { return false; }
}

/** 只关心会起进程的三个工具上携带的命令字段 */
function commandOf(name: string, input: any): string {
  if (!input || typeof input !== "object") return "";
  if (name !== "bash" && name !== "shell" && name !== "exec") return "";
  const c = input.command ?? input.cmd ?? input.script;
  return typeof c === "string" ? c : "";
}

export default function killGuard(pi: any) {
  let off = false;
  try { off = process.env.PI_LAB_KILL_GUARD === "0"; } catch { /* 读不到就是开 */ }

  pi.on("tool_call", async (event: any, ctx: any) => {
    try {
      if (off || recoverActive()) return;
      const name = event?.toolName ?? "?";
      const cmd = commandOf(name, event?.input);
      if (!cmd) return;
      const m: any = await loadMod();
      if (!m) return; // fail-open：模块加载失败
      const r = m.check(cmd);
      if (!r || r.action !== "deny") return;

      appendLog({
        ts: new Date().toISOString(),
        action: "block",
        rule: r.rule,
        tool: name,
        matched: String(r.matched || "").slice(0, 200),
        cmd: cmd.slice(0, 400),
      });
      try {
        ctx?.ui?.notify("⛔ kill-guard：拦下按名字批量杀进程（可能打死整条控制链）", "warning");
      } catch { /* 无 UI */ }
      return { block: true, reason: m.buildReason(r) };
    } catch {
      return; // 任何异常都放行
    }
  });

  try {
    pi.registerCommand("killguard", {
      description: "kill 闸门：/killguard 看状态 · /killguard off|on 开关（按名字批量杀会被拦）",
      handler: async (args: string, ctx: any) => {
        try {
          const a = String(args || "").trim().toLowerCase();
          if (a === "off" || a === "on") {
            off = a === "off";
            appendLog({ ts: new Date().toISOString(), action: off ? "off" : "on" });
            ctx?.ui?.notify("kill 闸门 → " + (off ? "关（本会话）" : "开"), "info");
            return;
          }
          let ok = false;
          try { ok = fs.existsSync(MOD); } catch { ok = false; }
          ctx?.ui?.notify(
            "⛔ kill 闸门 " + (off ? "（已关）" : "（开）")
            + "｜模块 " + (ok ? "就位" : "**缺失，fail-open**")
            + "｜拦：killall/pkill 通用名 · kill $(pgrep…) · ps|awk|xargs kill · kill 0/-1/1"
            + "｜放行：kill <pid> · kill \$! · pkill -f '<唯一名>'",
            "info");
        } catch { /* 命令不出结果不影响主流程 */ }
      },
    });
  } catch { /* 老版本 pi 没有 registerCommand */ }
}
