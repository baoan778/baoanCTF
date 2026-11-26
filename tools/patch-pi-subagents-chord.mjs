#!/usr/bin/env node
/**
 * patch-pi-subagents-chord.mjs —— 修 pi-subagents 的 chord 门禁误判（幂等）。
 *
 * 缺陷（2026-11-14 实测，容器内复现）：
 *   pi-subagents 用「版本号正则 ^0\.(\d+)\.\d+$ 且 <0.85」来判断宿主是不是 pre-chord。
 *   本机 pi 是 **1.5.0** —— 正则不匹配（只认 0.x）→ stableVersion=null → isPreChord=false
 *   → 强制要求 @earendil-works/chord；而 pi 1.5.0 的 dependencies 里压根没有这个包。
 *   结果：subagent 工具全线不可用（实测 agent 只能 --no-lanes 单线跑，速度直接砍半）。
 *
 * 修法：看宿主 manifest **有没有声明** chord，而不是猜版本。声明了才要求。
 * 幂等：已打过补丁则直接退出 0；找不到锚点则退出 1（不静默）。
 *
 * 用法：node tools/patch-pi-subagents-chord.mjs [--check]
 */
import fs from "node:fs";
import path from "node:path";

const AGENT = process.env.PI_CODING_AGENT_DIR || path.join(process.env.HOME || "", ".pi-ctf", "agent");
const CANDIDATES = [
  path.join(AGENT, "npm/node_modules/pi-subagents/src/runs/background/runner-aliases.js"),
  path.join(AGENT, "npm/node_modules/pi-subagents/dist/runs/background/runner-aliases.js"),
];
const MARK = "patch-2026-11-14";

const OLD = `    const stableVersion = typeof hostManifest?.version === "string" ? /^0\\.(\\d+)\\.\\d+$/.exec(hostManifest.version) : null;
    const isPreChord = stableVersion !== null && Number(stableVersion[1]) < 85;
    const required = [...HOST_PEER_ALIASES, ...(isPreChord ? [] : CHORD_PEER_ALIASES)];`;

const NEW = `    // ★ patch-2026-11-14：原来只按版本号判断（0.x < 0.85 才不要 chord）。
    //   本机 pi 是 1.5.0，正则 ^0\\.(\\d+)\\.\\d+$ 匹配不到 → 被当成“必须有 chord”，
    //   而 pi 1.5.0 的 dependencies 里根本没有 @earendil-works/chord → subagent 全线不可用。
    //   改为**看 host manifest 到底声明没声明**：声明了才要求，没声明就别拦。
    const hostDeps = { ...(hostManifest?.dependencies || {}), ...(hostManifest?.peerDependencies || {}), ...(hostManifest?.optionalDependencies || {}) };
    const hostDeclaresChord = Object.keys(hostDeps).some((k) => k === "@earendil-works/chord");
    const stableVersion = typeof hostManifest?.version === "string" ? /^0\\.(\\d+)\\.\\d+$/.exec(hostManifest.version) : null;
    const isPreChord = !hostDeclaresChord || (stableVersion !== null && Number(stableVersion[1]) < 85);
    const required = [...HOST_PEER_ALIASES, ...(isPreChord ? [] : CHORD_PEER_ALIASES)];`;

const checkOnly = process.argv.includes("--check");

function findFile() {
  for (const p of CANDIDATES) if (fs.existsSync(p)) return p;
  return null;
}

const file = findFile();
if (!file) {
  console.error("[chord-patch] 找不到 runner-aliases.js（pi-subagents 没装？）");
  process.exit(1);
}

const src = fs.readFileSync(file, "utf8");
if (src.includes(MARK)) {
  console.log("[chord-patch] 已打过补丁，跳过：" + file);
  process.exit(0);
}
if (!src.includes(OLD)) {
  console.error("[chord-patch] 锚点不匹配（pi-subagents 版本变了？）—— 未改动：" + file);
  process.exit(1);
}

if (checkOnly) {
  console.log("[chord-patch] 需要补丁（未改）：" + file);
  process.exit(1);
}

fs.copyFileSync(file, file + ".bak-chord");
fs.writeFileSync(file, src.replace(OLD, NEW), "utf8");
console.log("[chord-patch] 已修：" + file);
console.log("[chord-patch] 备份：" + file + ".bak-chord");