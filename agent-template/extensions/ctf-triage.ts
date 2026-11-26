/**
 * pi-lab: ctf-triage —— 附件/目标「开箱摸形状」工具
 *
 * 为什么：AGENTS.md 写了两条血教训 —— ① 拿到 shell / 附件**第一条命令就一次性 dump**，
 * 别一轮一个文件（奶蛙题实测：5 分 09 秒里 ~2 分钟纯耗在回合数）；
 * ② 先摸形状再猜内容。这个工具把「摸形状」固化成**一次调用**：
 *   file 类型 → 按类型分流跑一组只读命令 → 回一张结构化卡 + **该起哪份 skill**。
 *
 * 它是**只读**的：只跑 file/md5/strings/binwalk 这类看一眼的命令，
 * 不做解包写盘（要解包我会自己再来一条，避免把目录搞乱）。
 *
 * 用法（我调用）：
 *   triage({ path: "<仓库根>/work/xxx/chal.zip" })
 *   triage({ path: "<目录>" })               # 目录：列结构 + 各文件类型分布
 *   triage({ path: "<URL 或 nc 地址>" })      # 目标：返回该走哪份 skill（不做实际请求）
 */

import * as fs from "node:fs";
import * as path from "node:path";
import { spawnSync } from "node:child_process";
import { Type } from "typebox";

const LAB = process.env.PI_LAB_ROOT || process.cwd();
const MAX_FILE_FOR_DEEP = 60 * 1024 * 1024;   // 超过 60MB 不做 binwalk/strings（太慢）
const OUT_CAP = 6000;                          // 单条命令输出上限（字符）

const SKILL = {
  zip: "skills/ctf-misc/SKILL.md（压缩包）", rar: "skills/ctf-misc/SKILL.md",
  "7z": "skills/ctf-misc/SKILL.md", gzip: "skills/ctf-misc/SKILL.md", tar: "skills/ctf-misc/SKILL.md",
  png: "skills/ctf-misc/SKILL.md（图片/隐写）", jpg: "skills/ctf-misc/SKILL.md", gif: "skills/ctf-misc/SKILL.md",
  bmp: "skills/ctf-misc/SKILL.md", pdf: "skills/ctf-misc/SKILL.md",
  pcap: "skills/ctf-forensics/SKILL.md（流量）", pcapng: "skills/ctf-forensics/SKILL.md",
  elf: "先 file/readelf → skills/ctf-pwn/SKILL.md 或 skills/ctf-reverse/SKILL.md",
  pe: "skills/ctf-reverse/SKILL.md（PE）/ 若带 nc 则 pwn",
  apk: "skills/ctf-reverse/SKILL.md + tools/wxapkg.py",
  text: "skills/ctf-kit/SKILL.md（先看内容再定题型）",
  other: "skills/ctf-kit/SKILL.md（兜底判题型）",
};

function cap(s, n = OUT_CAP) {
  s = String(s ?? "");
  return s.length > n ? s.slice(0, n) + `\n…（截断，原长 ${s.length}）` : s;
}

/** 命令是否装了（同一次调用里缓存，避免每个分支都跑一遍 which） */
const HAS = new Map();
export function hasCmd(cmd) {
  if (HAS.has(cmd)) return HAS.get(cmd);
  let ok = false;
  try {
    ok = spawnSync("bash", ["-c", `command -v ${cmd} >/dev/null 2>&1`], { timeout: 3000 }).status === 0;
  } catch { ok = false; }
  HAS.set(cmd, ok);
  return ok;
}

export function classify(ft) {
  const s = String(ft || "").toLowerCase();
  const has = (...kw) => kw.some((k) => s.includes(k));
  if (has("zip", "jar", "apk")) return s.includes("apk") ? "apk" : "zip";
  if (has("rar")) return "rar";
  if (has("7-zip", "7z")) return "7z";
  if (has("gzip", "bzip2", "xz", "compress")) return "gzip";
  if (has("tar archive")) return "tar";
  if (has("png")) return "png";
  if (has("jpeg", "jpg")) return "jpg";
  if (has("gif")) return "gif";
  if (has("bitmap", "bmp")) return "bmp";
  if (has("pdf")) return "pdf";
  if (has("pcapng") || has("pcap")) return has("pcapng") ? "pcapng" : "pcap";
  if (has("elf")) return "elf";
  if (has("pe32", "ms-dos executable", "ms windows")) return "pe";
  if (has("ascii text", "utf-8", "unicode text", "script", "json", "xml", "python", "shell")) return "text";
  return "other";
}

/** 按类型给出命令清单（都是只读的看一眼的命令） */
export function planFor(kind, file, size, hasBinwalk = true) {
  const cmds = [];
  const has = (c) => hasCmd(c);
  cmds.push(["file", [file]]);
  cmds.push(["md5sum", [file]]);
  if (kind === "zip" || kind === "rar" || kind === "7z" || kind === "gzip" || kind === "tar") {
    if (kind === "zip") cmds.push(["unzip", ["-l", file]]);
    else if (kind === "7z") cmds.push(["7z", ["l", file]]);
    else if (kind === "rar") cmds.push(["7z", ["l", file]]);
    else if (kind === "tar") cmds.push(["tar", ["-tvf", file]]);
    else cmds.push(["bash", ["-c", `gzip -l "${file}" 2>&1 | head -5`]]);
    if (hasBinwalk && size < MAX_FILE_FOR_DEEP) cmds.push(["binwalk", [file]]);
  } else if (kind === "png" || kind === "jpg" || kind === "gif" || kind === "bmp") {
    if (has("identify")) cmds.push(["identify", ["-verbose", file]]);
    if (has("exiftool")) cmds.push(["bash", ["-c", `exiftool "${file}" 2>/dev/null | head -40`]]);
    if (hasBinwalk) cmds.push(["binwalk", [file]]);
    if (kind === "png" && has("pngcheck")) cmds.push(["bash", ["-c", `pngcheck -vt "${file}" 2>&1 | head -30`]]);
  } else if (kind === "pdf") {
    cmds.push(["bash", ["-c", `pdfinfo "${file}" 2>/dev/null; pdftotext "${file}" - 2>/dev/null | head -40`]]);
  } else if (kind === "pcap" || kind === "pcapng") {
    cmds.push(["bash", ["-c", `capinfos "${file}" 2>/dev/null | head -30; tshark -r "${file}" -q -z io,phs 2>/dev/null | head -40`]]);
  } else if (kind === "elf") {
    cmds.push(["bash", ["-c", `readelf -h "${file}" | head -20; echo ---; readelf -lW "${file}" | grep -E "GNU_STACK|GNU_RELRO" ; echo ---; nm -D "${file}" 2>/dev/null | head -20`]]);
    cmds.push(["bash", ["-c", `strings -n 6 "${file}" | head -40`]]);
  } else if (kind === "pe") {
    cmds.push(["bash", ["-c", `strings -n 6 "${file}" | head -40`]]);
  } else {
    cmds.push(["bash", ["-c", `head -c 2000 "${file}" | cat -v | head -30`]]);
    if (hasBinwalk) cmds.push(["binwalk", [file]]);
  }
  return cmds;
}

export default function ctfTriage(pi) {
  pi.registerTool({
    name: "triage",
    label: "附件开箱",
    description:
      "对附件/目录/目标做一次「开箱摸形状」：文件类型、大小、哈希，按类型分流跑只读侦察命令（压缩包→列目录、图片→exif/binwalk、流量→capinfos/协议树、ELF→架构与保护、PE→字符串），并给出该起哪份 skill。只读、不解包写盘。",
    promptSnippet: "Triage an attachment/dir/target: type + hashes + type-specific recon + which skill to use",
    promptGuidelines: [
      "Use triage first when a challenge attachment or a suspicious file/dir arrives, instead of issuing several one-off file/strings/binwalk bash calls.",
      "Use triage with a URL or nc target only to get the recommended skill; it makes no network request.",
    ],
    parameters: Type.Object({
      path: Type.String({ description: "文件或目录路径；也接受 URL / nc 目标（只给建议，不请求）" }),
    }),
    async execute(_toolCallId, params, signal) {
      const target = String(params.path || "").trim();
      const lines = [];
      const run = async (cmd, args) => {
        try {
          const r = await pi.exec(cmd, args, { signal, timeout: 25000 });
          const out = cap(`${r.stdout || ""}${r.stderr || ""}`.trim());
          return { ok: r.code === 0, out };
        } catch (e) {
          return { ok: false, out: `(${cmd} 执行失败：${e && e.message})` };
        }
      };

      // 目标形态：URL / nc
      if (/^https?:\/\//i.test(target) || /^nc\s+/i.test(target)) {
        lines.push(`# 目标（未发请求）: ${target}`);
        lines.push(`· 建议起：${/^nc\s+/i.test(target) ? SKILL.elf : "skills/ctf-web/SKILL.md"}`);
        lines.push(`· 靶场类先落台账：/targets add <名字> ${target}`);
        return { content: [{ type: "text", text: lines.join("\n") }] };
      }

      let st;
      try {
        st = fs.statSync(target);
      } catch {
        return { content: [{ type: "text", text: `✗ 路径不存在：${target}` }] };
      }

      if (st.isDirectory()) {
        const r = await run("bash", ["-c", `cd "${target}" && find . -maxdepth 2 -printf '%y %10s  %p\\n' 2>/dev/null | head -60`]);
        const t = await run("bash", ["-c", `cd "${target}" && find . -maxdepth 3 -type f -exec file -b {} + 2>/dev/null | sort | uniq -c | sort -rn | head -15`]);
        lines.push(`# 目录开箱: ${target}`);
        lines.push("## 结构（前 60 项）\n" + r.out);
        lines.push("## 文件类型分布\n" + t.out);
        lines.push(`· 建议起：${SKILL.other}`);
        return { content: [{ type: "text", text: lines.join("\n") }] };
      }

      const size = st.size;
      const f = await run("file", [target]);
      const kind = classify(f.out);
      lines.push(`# 开箱: ${path.basename(target)}`);
      lines.push(`· 大小：${(size / 1048576).toFixed(2)} MB（${size} 字节）　类型判定：**${kind}**`);
      lines.push("```\n" + f.out.trim() + "\n```");

      const binwalkOk = (await run("bash", ["-c", "command -v binwalk >/dev/null && echo yes || echo no"])).out.includes("yes");
      const plan = planFor(kind, target, size, binwalkOk);
      for (const [cmd, args] of plan.slice(0, 8)) {
        const r = await run(cmd, args);
        lines.push(`## $ ${cmd} ${args.join(" ")}\n\`\`\`\n${r.out.trim() || "(无输出)"}\n\`\`\``);
      }
      if (size > MAX_FILE_FOR_DEEP) lines.push(`> ⚠ 文件 >60MB，已跳过 binwalk/strings 这类重活（要用就单独跑）`);
      lines.push(`· **建议起**：${SKILL[kind] || SKILL.other}`);
      lines.push(`· 解包/深入用：\`bash\`（本工具只读，不写盘）`);
      return { content: [{ type: "text", text: cap(lines.join("\n\n"), 20000) }] };
    },
  });
}
