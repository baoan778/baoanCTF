/**
 * killguard.mjs —— 「按进程名批量杀」的闸门逻辑（零依赖，node 直接跑）
 *
 * 血账（2026-11-20 15:47:03，实测日志 log/shard-20261120.log）
 * -------------------------------------------------------------
 * xben-092-24 这条 shard 为了清掉自己留下的进程，执行了：
 *     for pid in $(ps -eo pid,comm | awk '$2=="python3"{print $1}'); do kill $pid; done
 * 结果把**整条控制链**一起打死：1390 supervise / 1391 parallel_solver / 51009 sdkshard，
 * 之后整场空转 26 分钟（没有 shard 在跑，也没人重新拉起）。
 * 根因不是模型"不听话"，而是**没有任何闸门拦过这条命令** —— 它看起来就是一次普通清理；
 * lane_gate 也不管（那条只管长任务/扫描/破坏系统目录）。
 *
 * 这条命令的三宗罪（决定了拦截规则，不是拍脑袋）：
 *   1. **按 comm 匹配**：comm 是进程名，python3 同时匹配 sdkshard / parallel_solver / supervise / 任何脚本；
 *   2. **集合是算出来的**：$(ps … | awk …) 里究竟有谁由当时的进程表决定，人看不见；
 *   3. **无回显**：kill $pid 不打印杀了谁，杀完才知道（日志里确实没有）。
 *
 * 设计取向（与 lane_gate 同一套：拒绝必须给改写模板，不只给禁令）
 * --------------------------------------------------------------
 * · **只拦「集合算出来的 / 按名字的」批量杀**，普通 kill <pid> 一律放行 ——
 *   误伤正常清理比漏报更糟：agent 清不掉进程会转入更野的做法。
 * · **异常一律放行**（扩展侧 try/catch）；但**判定命中时不按次数放行**（与 lane-watch 不同）：
 *   一条 kill 打死控制链没有 undo，而「这条命令被拒」并不锁死 agent（其它工具照跑）。
 *   逃生通道是显式的：/killguard off · PI_LAB_KILL_GUARD=0 · recover-mode。
 * · **只管命令位置**：grep 'kill'、echo "kill $pid"、写脚本的 heredoc 都不碰 ——
 *   否则等于训练模型绕路（绕一次就永远绕）。
 *
 * 用法：
 *   node tools/killguard.mjs check '<命令>'          # 人读；exit 0 放行 / 10 拒绝
 *   node tools/killguard.mjs check '<命令>' --json   # 机器读
 *   echo '<命令>' | node tools/killguard.mjs check -  # 从 stdin 读（避免 shell 转义打架）
 *   node tools/killguard.mjs --selftest
 */

import * as fs from "node:fs";

// 通用进程名：出现这些就说明「按名字杀」会连带打死解释器/控制链/编辑器
const GENERIC = new Set([
  // 解释器与运行时
  "python", "python2", "python3", "py", "pypy", "node", "nodejs", "npm", "npx", "deno", "bun",
  "bash", "sh", "zsh", "dash", "ksh", "fish", "ssh", "sshd", "sftp", "git", "curl", "wget",
  "java", "ruby", "perl", "php", "go", "rustc", "cargo",
  // 网络/中继（现场最常"清理"错的对象）
  "nc", "netcat", "ncat", "socat", "tmux", "screen",
  // 本仓控制链（就是 2026-11-20 被打死的那几个）
  "pi", "dsh", "docker", "dockerd", "containerd", "supervisord", "supervise",
  "sdkshard", "parallel_solver", "run_sdk_solver",
  // web/py 服务
  "uvicorn", "gunicorn", "flask", "django", "jupyter", "notebook",
  "chromium", "chrome", "google-chrome", "firefox", "xvfb", "xterm",
  "qemu", "qemu-system-x86_64", "gdb",
  // 文件名主干（main.py → main、http.server → http）
  "main", "app", "server", "serve", "http", "https", "api", "test", "tests", "tmp", "run",
]);

// 对 killall / pkill（无 -f）的判定：名字是通用名 ⇒ 拒
function isGenericToken(tok) {
  const x = String(tok || "").toLowerCase().replace(/\.exe$/, "");
  if (!x) return true;
  if (GENERIC.has(x)) return true;
  if (/^python[0-9.]*$/.test(x) || /^node[0-9.]*$/.test(x)) return true;
  const base = x.replace(/\.[a-z0-9]+$/, ""); // main.py → main / http.server → http
  return base !== x && GENERIC.has(base);
}

// ── 文本预处理 ──────────────────────────────────────────────────────────────

/**
 * 去掉 heredoc 的正文（cat > x.sh <<'EOF' … EOF 里写的 kill 不是要执行的）。
 * 只认「行首词等于分隔符」的结束行；只匹配 << 后跟字母/下划线开头的词，
 * 以免把 awk 的 << 位移当 heredoc。
 */
function stripHeredocs(s) {
  const lines = String(s).split("\n");
  const out = [];
  let term = null;
  for (const line of lines) {
    if (term) {
      if (line.replace(/^\t+/, "").trim() === term) { term = null; out.push(""); }
      else out.push("");
      continue;
    }
    const m = line.match(/<<-?\s*(['"]?)([A-Za-z_][A-Za-z0-9_]*)\1/);
    if (m) term = m[2];
    out.push(line);
  }
  return out.join("\n");
}

/** 把单引号串抹成空格（awk '$2=="python3"' 这类数据不算命令位置）。保留换行与长度。 */
function maskSingleQuoted(s) {
  return String(s).replace(/'[^']*'/g, (m) => m.replace(/[^\n]/g, " "));
}

/** 把双引号串也抹成空格（echo "kill \$pid" 是数据）。同样保留长度，好让下标与原文对齐。 */
function maskDoubleQuoted(s) {
  return String(s).replace(/"[^"]*"/g, (m) => m.replace(/[^\n]/g, " "));
}

/** 抽出 bash -c '…' / eval "…" 的载荷 —— 那是真会执行的代码，不能被上面的抹除吃掉。 */
function extractPayloads(s) {
  const out = [];
  let m;
  const reShell = /\b(?:bash|sh|zsh|dash|ksh)\s+(?:-[A-Za-z]+\s+)*--?c\s+(?:'([^']*)'|"([^"]*)")/g;
  while ((m = reShell.exec(s))) out.push(m[1] ?? m[2] ?? "");
  const reEval = /\beval\s+(?:'([^']*)'|"([^"]*)")/g;
  while ((m = reEval.exec(s))) out.push(m[1] ?? m[2] ?? "");
  return out;
}

/** 参数切词：认单/双引号，双引号里认反斜杠。 */
function tokenizeArgs(s) {
  const out = [];
  let cur = "";
  let q = null;
  const str = String(s);
  for (let i = 0; i < str.length; i++) {
    const c = str[i];
    if (q) {
      if (c === q) { q = null; continue; }
      if (q === '"' && c === "\\") { cur += str[++i] ?? ""; continue; }
      cur += c;
      continue;
    }
    if (c === "'" || c === '"') { q = c; continue; }
    if (/\s/.test(c)) { if (cur) { out.push(cur); cur = ""; } continue; }
    cur += c;
  }
  if (cur) out.push(cur);
  return out;
}

/** 从 start 取到本段结束（未加引号的 ; | & ) } 换行 > 处截断）。 */
function argsAfter(cmd, start) {
  let q = null;
  for (let i = start; i < cmd.length; i++) {
    const c = cmd[i];
    if (q) { if (c === q) q = null; continue; }
    if (c === "'" || c === '"') { q = c; continue; }
    if (c === ";" || c === "|" || c === "&" || c === ")" || c === "}" || c === "\n" || c === ">") {
      return cmd.slice(start, i);
    }
  }
  return cmd.slice(start);
}

// 命令位置：行首 / 分隔符之后 / do then else 之后 / xargs sudo exec … 之后
const CMDPOS = /(?:^|[;&|(){}\n]|\b(?:do|then|else|xargs|sudo|exec|command|time|nohup|eval|env|nice|ionice|stdbuf))\s*(?:-[A-Za-z0-9_=./-]+\s+)*$/;

// 收集器：真正会算出一批 pid 的命令
const PROC_SOURCE = /\b(?:ps|pgrep|pidof|awk|pstree|top)\b/;

const DENY = (rule, why, extra) => ({ action: "deny", rule, why, extra: extra || "" });
const ALLOW = (why) => ({ action: "allow", why: why || "" });


// ── 三类判定 ────────────────────────────────────────────────────────────────

/** kill 的信号前缀：最多剥一个信号，之后的 -1 必须当 pid 看（kill -- -1 = 杀所有）。 */
function stripSignals(toks) {
  let i = 0;
  let sawSig = false;
  while (i < toks.length) {
    const t = toks[i];
    if (t === "--") { i++; break; }
    if (t === "-s" || t === "-n" || t === "--signal") { i += 2; sawSig = true; continue; }
    if (!sawSig && /^-\d{1,2}$/.test(t)) { i++; sawSig = true; continue; }
    if (!sawSig && /^-[A-Za-z]+$/.test(t)) { i++; sawSig = true; continue; }
    break;
  }
  return toks.slice(i);
}

function classifyKill(argsRaw, line, viaXargs) {
  const raw = String(argsRaw).trim();
  if (!raw) return null; // 裸 kill 只打印用法
  const toks = tokenizeArgs(raw);
  if (toks.some((t) => t === "-l" || t === "-L")) return null; // 列信号名
  if (viaXargs) {
    return DENY("xargs-kill",
      "被杀的 pid 来自 xargs，也就是来自上一条命令算出的**集合** —— 会连带打死同名的控制链进程。");
  }
  if (/[$]\s*\(/.test(raw) || raw.includes(String.fromCharCode(96))) {
    return DENY("computed-kill",
      "pid 来自命令替换（$(…) / 反引号）：要杀谁由当时的进程表决定，人看不见也没法回头。");
  }
  const targets = stripSignals(toks);
  if (!targets.length) return null;
  for (const tg of targets) {
    if (tg === "0") {
      return DENY("kill-group-zero", "kill 0 = 杀**本进程组全部进程**，会把拉起它的父进程一起带走。");
    }
    if (/^-\d+$/.test(tg)) {
      return DENY("kill-negative", "kill " + tg + " = 杀**整个进程组**（-1 更是杀所有进程），不是杀单个 pid。");
    }
    if (tg.startsWith("%")) continue;                       // 作业控制 %1，范围只有自己的作业
    if (/^\d+$/.test(tg)) {
      if (Number(tg) === 1) {
        return DENY("kill-pid1", "pid 1 是容器入口（entrypoint/tini），杀它 = 直接关机，容器内一切归零。");
      }
      continue;
    }
    if (/^\$[!?$]$/.test(tg) || /^\$\{?[!?$]\}?$/.test(tg)) continue; // $! / $? / $$
    if (/^\$\{?[A-Za-z_][A-Za-z0-9_]*\}?$/.test(tg)) {
      if (PROC_SOURCE.test(line)) {
        return DENY("kill-var-from-proc-list",
          "kill 的 pid 是变量 " + tg + "，而这条命令行里在用 ps/pgrep/awk 列进程 —— "
          + "这就是 2026-11-20 打死 supervise/parallel_solver/sdkshard 的那条命令的形状。");
      }
      continue; // pid=$!; kill $pid 这类明确的自有进程，放行
    }
    if (/[*?]/.test(tg)) {
      return DENY("kill-glob", "kill 目标含通配符（" + tg + "），展开成谁由文件系统/进程表决定。");
    }
    // 其余（路径、名字）交给 shell 报错，不拦
  }
  return null;
}

function classifyKillall(argsRaw, viaXargs) {
  if (viaXargs) {
    return DENY("xargs-killall", "xargs 会把上一条命令算出的进程名喂给 killall，等于批量。");
  }
  const toks = tokenizeArgs(argsRaw).filter((t) => t !== "--" && !t.startsWith("-"));
  for (const n of toks) {
    if (isGenericToken(n)) {
      return DENY("killall-generic",
        "killall " + n + " 会杀掉**所有同名进程** —— 解释器/控制链也叫这个名字，名字上看不出来。");
    }
  }
  return null;
}

/** -f 的 pattern 里是否带了"唯一锚点"：端口 / 路径 / 长 hex / 带连字符的唯一名 */
function hasAnchor(p) {
  return /:\d{2,5}\b/.test(p)
    || p.includes("/")
    || /\b[A-Fa-f0-9]{8,}\b/.test(p)
    || /[A-Za-z][-_][A-Za-z0-9]{2,}/.test(p);
}

function classifyPkill(argsRaw, viaXargs) {
  if (viaXargs) {
    return DENY("xargs-pkill", "xargs 会把上一条命令算出的名字喂给 pkill，等于批量。");
  }
  const toks = tokenizeArgs(argsRaw);
  const OPTS_WITH_VALUE = new Set([
    "-u", "-U", "-P", "-t", "-g", "-s", "-G",
    "--uid", "--parent", "--terminal", "--pgroup", "--session", "--group", "--ns",
  ]);
  let full = false;
  let pattern = null;
  for (let i = 0; i < toks.length; i++) {
    const t = toks[i];
    if (t === "--") continue;
    if (t === "-f" || t === "--full") { full = true; continue; }
    if (OPTS_WITH_VALUE.has(t)) { i++; continue; }
    if (t.startsWith("-")) continue;
    if (pattern === null) pattern = t;
  }
  if (pattern === null) return null;
  if (pattern === "") return DENY("pkill-empty", "空 pattern = 匹配一切。");
  if (!full) {
    // 无 -f：pattern 是**进程名**，和 killall 同一条判据
    if (isGenericToken(pattern)) {
      return DENY("pkill-name-generic",
        "pkill " + pattern + " 按**进程名**匹配，会杀掉所有同名进程（含控制链）。");
    }
    return null;
  }
  // 有 -f：pattern 是整条命令行，需要确认它足够"唯一"
  if (pattern.length < 6) {
    return DENY("pkill-f-short", "pattern 太短（<6 字符），很可能匹配到无关进程。");
  }
  if (!/[A-Za-z0-9]/.test(pattern)) {
    return DENY("pkill-f-broad", "pattern 不含任何字母数字，等于匹配一切。");
  }
  if (hasAnchor(pattern)) return null;
  for (const tk of pattern.split(/[^A-Za-z0-9_.-]+/)) {
    if (!tk) continue;
    if (isGenericToken(tk)) {
      return DENY("pkill-f-generic",
        "pkill -f 的 pattern 里的 " + tk + " 是通用词，会匹配所有 python3/服务/脚本 —— "
        + "这正是「按名字批量杀」的形状。");
    }
  }
  // ★ R3-C（2026-11-20 回放实测）：`pkill -f srv.py` 原来被判**放行** ——
  //   "srv" 不在 GENERIC、pattern 又没有锚点（无 / 无端口 无长 hex 无唯一后缀），
  //   于是从最后的 return null 漏出去。可它在现场是**最危险的一类**：
  //   srv.py / serve.py / main.py 这种名字，任何一轮的临时服务都可能叫它，
  //   而挂掉之后 ps 上看不出是谁起的（15:11 xben-082 就是这么放的）。
  //   判据故意只认「脚本扩展名 + 无锚点」：有锚点（路径 / 端口 / 唯一后缀）已在上一步放行，
  //   所以 `/opt/pi-ctf/work/solve/xben-092-24/serve.py` 这类精确写法不受影响。
  if (/\.(?:py|sh|mjs|cjs|js|ts|pl|rb|php)\s*$/i.test(pattern.trim())) {
    return DENY("pkill-f-no-anchor",
      "pkill -f " + pattern + " 按**脚本名**模糊匹配，而这个名字没有唯一锚点 —— "
      + "任何一轮起的临时服务都可能叫它，杀错了从进程表上也看不出来是谁起的。");
  }
  return null;
}


// ── 主入口 ──────────────────────────────────────────────────────────────────

export const GUARD_OFF_ENV = "PI_LAB_KILL_GUARD";

export function check(cmd) {
  const raw = String(cmd == null ? "" : cmd);
  if (!raw.trim()) return ALLOW("空命令");
  try {
    if (typeof process !== "undefined" && process.env && process.env[GUARD_OFF_ENV] === "0") {
      return ALLOW("逃生门 PI_LAB_KILL_GUARD=0");
    }
  } catch { /* 读不到环境变量就照常判 */ }

  // ★ 两份文本**等长**（抹除只把字符换成空格，不增删、不动换行）：
  //   · scanCmd  —— 判定「这个 kill 是不是在命令位置」：字符串里的 kill 是数据，不算；
  //   · scanArgs —— 取参数：必须用**没抹过**的原文，否则 pkill -f '…' 的 pattern 会被抹空
  //                → 静默放行（假阴性比误伤更危险，2026-11-20 就是漏拦出来的 26 分钟空转）。
  const base = stripHeredocs(raw);
  const payloads = extractPayloads(raw).join("\n");
  const scanCmd = maskDoubleQuoted(maskSingleQuoted(base)) + "\n" + payloads;
  const scanArgs = base + "\n" + payloads;
  const RE = /\b(kill|pkill|killall)\b/g;
  let m;
  while ((m = RE.exec(scanCmd))) {
    const name = m[1];
    const idx = m.index;
    const before = scanCmd.slice(Math.max(0, idx - 120), idx);
    if (!CMDPOS.test(before)) continue;           // 不是命令位置：grep/echo/注释里的字
    const args = argsAfter(scanArgs, idx + name.length);
    const viaXargs = /\bxargs\b[^;|&\n]{0,100}$/.test(before);
    let r = null;
    if (name === "killall") r = classifyKillall(args, viaXargs);
    else if (name === "pkill") r = classifyPkill(args, viaXargs);
    else r = classifyKill(args, scanArgs, viaXargs);
    if (r && r.action === "deny") {
      return { ...r, cmd: raw.trim().slice(0, 400), matched: (name + args).trim().slice(0, 200) };
    }
  }
  return ALLOW("没有命中「批量/按名字杀进程」的形状");
}

export function buildReason(r) {
  return [
    "⛔ kill-guard 拦下了一条「批量 / 按名字杀进程」的命令（它不是普通 kill）。",
    "",
    "命中：" + (r.rule || "?") + "  ｜ 片段：" + (r.matched || ""),
    "为什么拦：" + (r.why || ""),
    "",
    "不是不许清理进程，是不许用**看不见的集合**清理：",
    "  · 2026-11-20 15:47:03 实战中，一条同形状的命令把 supervise / parallel_solver / sdkshard",
    "    一起打死（它们也是 python3），整场空转 26 分钟 —— 见 log/shard-20261120.log。",
    "",
    "正确做法（任选一条，都很短）：",
    "  1) 先看清再杀（推荐）：  ps -o pid,ppid,etime,cmd -p <pid>  然后  kill <pid>",
    "  2) 只杀自己刚起的：起的时候记 pid ——  python3 x.py & echo $! > /tmp/x.pid ；kill $(cat /tmp/x.pid)",
    "  3) 要停整条解题链路：用 supervise.py --stop（按进程组回收，不误伤无关进程）",
    "  4) 确实要按名字批量杀：先 pgrep -af '<唯一名字>' 看完整列表，确认没有",
    "     sdkshard / parallel_solver / supervise / pi 之后再逐个 kill。",
    "",
    "应急放行：/killguard off（本会话）· PI_LAB_KILL_GUARD=0（整容器）· recover-mode",
  ].join("\n");
}

// ── 自检 ────────────────────────────────────────────────────────────────────

const DENY_CASES = [
  ["实战原命令（按 comm 批量杀）", "for pid in $(ps -eo pid,comm | awk '$2==\"python3\"{print $1}'); do kill $pid; done", "kill-var-from-proc-list"],
  ["ps|awk|xargs kill", "ps -eo pid,comm | awk '$2==\"python3\"{print $1}' | xargs -r kill -9", "xargs-kill"],
  ["kill \$(pgrep)", "kill -9 $(pgrep -f python3)", "computed-kill"],
  ["killall python3", "killall python3", "killall-generic"],
  ["killall -9 python", "killall -9 python", "killall-generic"],
  ["killall node", "killall node", "killall-generic"],
  ["pkill python3", "pkill python3", "pkill-name-generic"],
  ["pkill -f http.server", "pkill -f http.server", "pkill-f-generic"],
  ["pkill -f python3 -m http.server", "pkill -f \"python3 -m http.server\"", "pkill-f-generic"],
  ["pkill -f main.py", "pkill -f main.py", "pkill-f-generic"],
  ["pkill -f .", "pkill -f .", "pkill-f-short"],
  ["kill 0", "kill 0", "kill-group-zero"],
  ["kill -- -1", "kill -- -1", "kill-negative"],
  ["kill -9 1", "kill -9 1", "kill-pid1"],
  ["pgrep|xargs pkill", "pgrep -f python | xargs pkill", "xargs-pkill"],
  ["bash -c 包裹", "bash -c \"killall python3\"", "killall-generic"],
  // ★ R3-C（2026-11-20 回放）：无锚点的脚本名 pattern —— 15:11 xben-082 现场就是这条放行的
  ["pkill -f 无锚点脚本名", "pkill -f srv.py", "pkill-f-no-anchor"],
  // serve.sh 的 "serve" 本身是通用词主干 → 先命中更具体的 pkill-f-generic（同样拒）
  ["pkill -f 通用脚本名", "pkill -f serve.sh", "pkill-f-generic"],
  ["pkill -f 裸 mjs", "pkill -f probe.mjs", "pkill-f-no-anchor"],
];

const ALLOW_CASES = [
  ["普通 kill", "kill 12345"],
  ["kill -9 <pid>", "kill -9 1234"],
  ["kill -TERM <pid>", "kill -TERM 4321"],
  ["kill 作业", "kill %1"],
  ["kill -0 探活", "kill -0 12345"],
  ["kill -l 列信号", "kill -l"],
  ["自有 pid 变量", "python3 x.py & pid=$!; kill $pid"],
  ["pkill 唯一名字", "pkill -f myserver_8f3a"],
  ["pkill 端口锚点", "pkill -f \"socat TCP-LISTEN:2222\""],
  ["pkill 路径锚点", "pkill -f \"/opt/pi-ctf/work/solve/xben-092-24/serve.py\""],
  ["killall 唯一名字", "killall my-helper-7"],
  ["grep 里的 kill 字样", "grep -n \"kill\" tools/killguard.mjs"],
  ["echo 字符串", "echo \"for pid in $(ps); do kill $pid; done\""],
  ["注释", "# 千万别 kill 0 / killall python3"],
  ["heredoc 写脚本", "cat > /tmp/k.sh <<'EOF'\nfor pid in $(ps -eo pid,comm); do kill $pid; done\nEOF"],
  ["ps 无 kill", "ps aux | grep python | head"],
  ["kill 自己写的 pid", "cat /tmp/x.pid | xargs -r kill"],
  // ★ R3-C：带锚点的脚本名仍要放行（精确路径 / 唯一后缀 / 端口）
  ["pkill 唯一后缀+脚本名", "pkill -f myserver_8f3a.py"],
  ["pkill 端口锚点+脚本", "pkill -f \"srv.py TCP-LISTEN:2222\""],
];

export function selftest() {
  const errs = [];
  let n = 0;
  for (const [name, cmd, rule] of DENY_CASES) {
    n++;
    const r = check(cmd);
    if (r.action !== "deny") errs.push("该拒却放行: " + name + " → " + cmd);
    else if (rule && r.rule !== rule) errs.push("规则不符: " + name + " → got " + r.rule + ", want " + rule);
  }
  for (const [name, cmd] of ALLOW_CASES) {
    n++;
    const r = check(cmd);
    if (r.action !== "allow") errs.push("误伤: " + name + " → " + cmd + "  (rule=" + r.rule + ")");
  }
  // 理由必须自包含：能照做 + 说明后果 + 有逃生门
  const reason = buildReason(check(DENY_CASES[0][1]));
  for (const need of ["kill <pid>", "supervise.py --stop", "/killguard off", "26 分钟"]) {
    n++;
    if (!reason.includes(need)) errs.push('理由缺 "' + need + '"');
  }
  // 空输入不崩
  n += 2;
  if (check("").action !== "allow") errs.push("空输入应放行");
  if (check(null).action !== "allow") errs.push("null 应放行");
  // 性能护栏：50KB 文本不炸
  n++;
  const big = ("ps aux | grep x; echo hello\n").repeat(2000);
  const t0 = Date.now();
  check(big);
  const ms = Date.now() - t0;
  if (ms > 2000) errs.push("50KB 太慢: " + ms + "ms");

  if (errs.length) {
    console.error("✗ killguard 自检 FAIL " + errs.length + "/" + n);
    for (const e of errs) console.error("  - " + e);
    return 1;
  }
  console.log("OK killguard 自检通过 " + n + "/" + n
    + "（" + DENY_CASES.length + " 拒 / " + ALLOW_CASES.length + " 放行 / 理由自包含 / 空输入 / 性能）");
  return 0;
}

// ── CLI ─────────────────────────────────────────────────────────────────────

if (process.argv.includes("--selftest") || process.argv[2] === "selftest") {
  process.exit(selftest());
} else if (process.argv[2] === "check") {
  let cmd = process.argv[3];
  if (cmd === "-" || cmd === undefined) {
    try { cmd = fs.readFileSync(0, "utf8"); } catch { cmd = ""; }
  }
  const r = check(cmd);
  if (process.argv.includes("--json")) {
    console.log(JSON.stringify({ action: r.action, rule: r.rule || null, why: r.why || null, matched: r.matched || null }));
  } else if (r.action === "deny") {
    console.log(buildReason(r));
  } else {
    console.log("[killguard] 放行：" + (r.why || ""));
  }
  process.exit(r.action === "deny" ? 10 : 0);
}

