/**
 * pi-lab: 启动自检 + `/selfcheck`
 *
 * 为什么需要这个扩展：
 *   技能是自动加载的 —— pi 扫 `.pi/skills`。而那个路径是**软链**（→ ../skills）。
 *   软链一旦断掉（删目录、重装、git 检出覆盖），pi 不会报任何错，只是**安静地一个技能
 *   都不加载** —— 于是我会凭印象裸干、不按题型走 skill。这是本机最贵的一类静默故障。
 *   插件崩溃记录（crashes.json）同理：崩过也没人看。
 *
 *   所以本扩展在 session_start 做三个**纯 fs** 检查（不起进程、不阻塞启动），
 *   只在真出问题时出声。完整检查（7 条回归测试 / 黑板不变量 / 密钥泄漏 / 工具链）
 *   走 `/selfcheck` 或命令行 `selfcheck`。
 *
 * 为什么写得这么保守：think-clock 踩过「插件崩掉整个 TUI」的坑
 *   （`setEditorComponent` 工厂给的 theme 不是 ctx.ui.theme）。
 *   本扩展只做「读文件 + notify + pi.exec」，全部 try/catch；
 *   宁可自检自己静默失败，也绝不拖累 pi 启动。
 */

import * as fs from "node:fs";
import * as path from "node:path";

const LAB = process.env.PI_LAB_ROOT || process.cwd();
const MIN_SKILLS = 15; // 12 题型 + 3 支撑；只报警少、不报警多

/**
 * ★ 2026-11-02 修（实测坐实）：**技能真正被加载的位置是 agent-dir/skills，不是 <LAB>/.pi/skills**。
 *
 * 病根：pi 的 skill 发现路径是 `<agent-dir>/skills/`（见 pi docs/configuration.md 资源表），
 * 而 agent-dir 默认 `~/.pi/agent`，本仓用 `PI_CODING_AGENT_DIR=~/.pi-ctf/agent` 指到真身，
 * 真身里 `skills -> /mnt/d/baoan/skills` 是软链。**`<LAB>/.pi/skills` 这条路径压根不存在**
 * —— 仓库里没有 `.pi` 目录（`ls -d /mnt/d/baoan/.pi` → No such file）。
 *
 * 后果：本扩展每次 session_start 都报 `⛔ 技能一个都没加载`（红字 error），
 * 而当时 16 个技能（≥MIN_SKILLS=15）**正常在场、正常加载**。一个永远亮的红灯
 * 等于没有红灯 —— 真正的断链反而会被当成"又是那条老误报"划过去。
 *
 * 修法：**按 pi 自己的口径解析**（agent-dir 优先），`<LAB>/.pi/skills` 只作兼容兜底；
 * 只有**两条都不成立**时才报错，且报错文案指出真正该修的那条路径。
 */
const AGENT_DIR =
  process.env.PI_CODING_AGENT_DIR || path.join(process.env.HOME || "", ".pi", "agent");
const SKILL_DIRS = [path.join(AGENT_DIR, "skills"), path.join(LAB, ".pi", "skills")];

/** 返回第一个真的能数出 skill 的目录（找不到返回 null）。 */
function resolveSkillDir(): string | null {
  for (const d of SKILL_DIRS) {
    if (countSkills(d) >= 0) return d;
  }
  return null;
}
const SYMLINK_HINT = `ln -sfn ${path.join(LAB, "skills")} ${path.join(AGENT_DIR, "skills")}`;

/** 数出 dir 下「真的是 skill」的目录数（必须有 SKILL.md）。失败返回 -1。 */
function countSkills(dir: string): number {
  try {
    return fs
      .readdirSync(dir, { withFileTypes: true })
      .filter(
        (d) => d.isDirectory() && fs.existsSync(path.join(dir, d.name, "SKILL.md")),
      ).length;
  } catch {
    return -1;
  }
}

/** crashes.json 的条数；文件不存在/读不动都算 0（= 没崩）。 */
function countCrashes(file: string): number {
  try {
    const raw = JSON.parse(fs.readFileSync(file, "utf8"));
    if (Array.isArray(raw)) return raw.length;
    return raw && typeof raw === "object" ? Object.keys(raw).length : 0;
  } catch {
    return 0;
  }
}

export default function labPreflight(pi) {
  // ── session_start：三个瞬时检查，只在有问题时 notify ──────────────────────
  pi.on("session_start", async (_event, ctx) => {
    try {
      const found = resolveSkillDir();
      if (found === null) {
        ctx.ui.notify(
          `⛔ 技能一个都没加载：${SKILL_DIRS[0]}（以及兼容路径 ${SKILL_DIRS[1]}）都不存在。修：\n${SYMLINK_HINT}`,
          "error",
        );
      } else {
        const n = countSkills(found);
        if (n < MIN_SKILLS) {
          ctx.ui.notify(
            `⚠ 只解析出 ${n} 个 skill（应 ≥${MIN_SKILLS}）—— 检查 ${found} 指向的 skills/ 是否完整`,
            "warning",
          );
        }
      }

      const crashed = countCrashes(path.join(AGENT_DIR, "crashes.json"));
      if (crashed > 0) {
        ctx.ui.notify(
          `⚠ 插件崩溃记录 ${crashed} 条 → 敲 /selfcheck 或看 .pi/agent/crashes.json`,
          "warning",
        );
      }
    } catch {
      /* 自检本身不许影响启动 */
    }
  });

  // ── /selfcheck：跑 tools/bin/selfcheck；默认 --quick（全套含回归测试约 50s）──
  pi.registerCommand("selfcheck", {
    description:
      "跑 tools/bin/selfcheck（默认 --quick；`/selfcheck full` 含 7 条回归测试）",
    handler: async (args, ctx) => {
      const full = /^(full|-f|all)$/i.test(String(args ?? "").trim());
      ctx.ui.notify(
        full
          ? "selfcheck: 全套（软链/插件/黑板 + 7 条回归测试，约 50s）…"
          : "selfcheck: --quick（软链/插件/黑板/仓库/密钥/工具链）…",
        "info",
      );
      try {
        const r = await pi.exec(
          path.join(LAB, "tools", "bin", "selfcheck"),
          full ? [] : ["--quick"],
          { timeout: full ? 600000 : 90000 },
        );
        const lines = `${r.stdout ?? ""}${r.stderr ?? ""}`.trimEnd().split("\n");
        const bad = lines
          .filter((l) => l.includes("✗") && !l.includes("结论:"))
          .slice(0, 4);
        const verdict = lines.filter((l) => l.includes("结论:")).slice(-1);
        const text = [...bad, ...verdict].join("\n").trim();
        ctx.ui.notify(
          text || `selfcheck 无输出（exit=${r.code}）`,
          (r.code ?? 1) === 0 ? "info" : "error",
        );
      } catch (err) {
        ctx.ui.notify(`selfcheck 跑不起来：${err}`, "error");
      }
    },
  });
}
