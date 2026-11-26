/**
 * ctf-models —— 能力槽位（capability slots）：让角色不再绑定某个私有 provider。
 *
 * 解决的问题（产品化）
 * ------------------
 * 原先把型号**硬编码**进角色 frontmatter：
 *   `model: opencode-go/deepseek-v4.1-flash` / `model: opencode-go/mimo-v2.6-pro`
 * 这只在**作者的机器**上成立 —— 买家没有那个 provider，角色一跑就失败，
 * 而且失败得很难懂（"model not found"，看不出是「你没配视觉模型」）。
 *
 * 做法：注册两个**虚拟模型**（pi 的官方机制，见 docs/virtual-models.md）。
 * 虚拟模型不属于任何 provider，**永远可用**；每次请求由 route() 挑一个真实模型。
 *
 *   ctf/text    文本槽位 —— 所有 CTF 角色的默认
 *   ctf/vision  视觉槽位 —— 只有 ctf-vision 用；**没配就自动找能看图的模型**
 *
 * 于是「谁的模型」变成**买家自己的配置**，而不是作者的私有通道。
 *
 * 配置（买家自己填，不填就自动挑）
 * --------------------------------
 * 查找顺序：
 *   1. `$PI_LAB_MODELS_FILE`（显式路径，最高优先）
 *   2. `$PI_CODING_AGENT_DIR/ctf-models.json`
 *   3. `<cwd>/.pi/agent/ctf-models.json`
 *
 * 内容：
 *   {
 *     "text":   "deepseek/deepseek-chat",     // 留空 → 自动
 *     "vision": "openai/gpt-5",               // 留空 → 自动（优先能看图的）
 *     "textThinking":   "max",
 *     "visionThinking": "max",
 *     "visionFallbackToText": true            // 找不到视觉模型时，退回文本模型并告警
 *   }
 *
 * 命令
 * ----
 *   /ctfmodels             看当前解析结果（配了什么 / 实际路由到谁 / 有没有认证）
 *   /ctfmodels auto        自动挑一套写进配置
 *   /ctfmodels text  <provider/model>
 *   /ctfmodels vision <provider/model>
 *   /ctfmodels list        列出所有可用模型（标出哪些能看图）
 */

import * as fs from "node:fs";
import * as path from "node:path";

const VIRTUAL_API = "pi-virtual";          // pi 内部对虚拟模型的 api 标记
const PROVIDER = "ctf";                    // 虚拟 provider id（无人占用 → 永远可用）
const TEXT_ID = "text";
const VISION_ID = "vision";

type Cfg = {
  text?: string;
  vision?: string;
  textThinking?: string;
  visionThinking?: string;
  visionFallbackToText?: boolean;
};

const THINKING = ["off", "minimal", "low", "medium", "high", "xhigh", "max"] as const;
type Level = (typeof THINKING)[number];

/** 优先当作视觉模型的家族关键词（只是一种排序偏好，不是白名单）。 */
const VISION_HINTS = [
  "gpt-5", "gpt-4o", "gpt-4.1", "claude-opus", "claude-sonnet", "claude-3", "claude-4",
  "gemini", "qwen-vl", "qwen2-vl", "qwen3-vl", "mimo", "glm-4v", "glm-4.5v", "internvl",
  "llava", "pixtral", "minicpm-v", "step-1v", "vision", "-vl",
];

function agentDir(): string {
  return process.env.PI_CODING_AGENT_DIR || path.join(process.env.HOME || ".", ".pi", "agent");
}

function cfgPaths(cwd: string): string[] {
  const out: string[] = [];
  if (process.env.PI_LAB_MODELS_FILE) out.push(process.env.PI_LAB_MODELS_FILE);
  out.push(path.join(agentDir(), "ctf-models.json"));
  out.push(path.join(cwd, ".pi", "agent", "ctf-models.json"));
  return out;
}

function readCfg(cwd: string): { cfg: Cfg; from?: string } {
  for (const p of cfgPaths(cwd)) {
    try {
      if (!fs.existsSync(p)) continue;
      const raw = JSON.parse(fs.readFileSync(p, "utf8"));
      return { cfg: (raw ?? {}) as Cfg, from: p };
    } catch {
      /* 坏配置不许把扩展弄崩：当没配，后面会提示 */
    }
  }
  return { cfg: {} };
}

function writeCfg(cwd: string, cfg: Cfg): string {
  // 写「已存在的那份」，一份都没有就写 agentDir 的
  const existing = cfgPaths(cwd).find((p) => fs.existsSync(p));
  const target = existing || path.join(agentDir(), "ctf-models.json");
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, JSON.stringify(cfg, null, 2) + "\n", "utf8");
  return target;
}

function isVirtual(m: any): boolean {
  return !!m && m.api === VIRTUAL_API;
}

/** 解析 `provider/model`；model id 里可能带 `/`，所以两种拆法都试。 */
function resolveRef(reg: any, ref: string): any | undefined {
  if (!ref) return undefined;
  const i = ref.indexOf("/");
  if (i > 0) {
    const direct = reg.find(ref.slice(0, i), ref.slice(i + 1));
    if (direct) return direct;
  }
  for (const m of reg.getAvailable?.() ?? []) {
    if (`${m.provider}/${m.id}` === ref) return m;
  }
  for (const m of reg.getAll?.() ?? []) {
    if (`${m.provider}/${m.id}` === ref) return m;
  }
  return undefined;
}

function usable(reg: any, m: any): boolean {
  if (!m || isVirtual(m)) return false;
  try {
    return !!reg.hasConfiguredAuth(m);
  } catch {
    return false;
  }
}

function visionScore(m: any): number {
  const ids = `${m.provider ?? ""}/${m.id ?? ""} ${m.name ?? ""}`.toLowerCase();
  let s = 0;
  for (let i = 0; i < VISION_HINTS.length; i++) {
    if (ids.includes(VISION_HINTS[i])) s += VISION_HINTS.length - i;
  }
  return s;
}

function pickText(reg: any, cfg: Cfg, ctx: any): any | undefined {
  if (cfg.text) {
    const m = resolveRef(reg, cfg.text);
    if (usable(reg, m)) return m;
  }
  // 当前会话已在用的真实模型 —— 最贴合用户意图
  const cur = ctx?.model;
  if (cur && !isVirtual(cur) && usable(reg, cur)) return cur;
  const avail = (reg.getAvailable?.() ?? []).filter((m: any) => !isVirtual(m));
  return avail[0];
}

function pickVision(reg: any, cfg: Cfg, ctx: any): { model?: any; why: string } {
  if (cfg.vision) {
    const m = resolveRef(reg, cfg.vision);
    if (usable(reg, m)) {
      const ok = (m.input ?? []).includes("image");
      return { model: m, why: ok ? "配置指定" : "配置指定（⚠ 该模型未声明支持图片输入）" };
    }
  }
  const avail = (reg.getAvailable?.() ?? []).filter((m: any) => !isVirtual(m));
  const img = avail.filter((m: any) => (m.input ?? []).includes("image"));
  if (img.length) {
    // ★ 先看「文本模型同 provider」——那条通道**已经在工作**，不容易踩地区/配额类的坑。
    //   （2026-10-11 实测血账：自动挑选中一个能看图的模型，但那条通道当前 403
    //    `unsupported_country_region_territory` —— 而用户自己的文本 provider 是好的。
    //    只看「谁声明支持图片」不看「谁的通道真的通」，就会把好用户推到坏通道上。）
    const text = pickText(reg, cfg, ctx);
    if (text?.provider) {
      const same = img.filter((m: any) => m.provider === text.provider);
      if (same.length) {
        same.sort((a: any, b: any) => visionScore(b) - visionScore(a));
        return { model: same[0], why: `自动挑选（与文本模型同 provider：${text.provider}）` };
      }
    }
    img.sort((a: any, b: any) => visionScore(b) - visionScore(a));
    return { model: img[0], why: "自动挑选（可用模型里能看图的）" };
  }
  if (cfg.visionFallbackToText !== false) {
    const t = pickText(reg, cfg, ctx);
    if (t) return { model: t, why: "⚠ 没有能看图的模型 → 退回文本模型（图片会被忽略）" };
  }
  return { why: "没有可用的视觉模型" };
}

export default function (pi: any) {
  const note = (msg: string) => {
    try {
      pi.sendMessage?.({ role: "system", content: msg });
    } catch {
      /* 没 UI 时静默 */
    }
  };

  const status = (ctx: any) => {
    const { cfg, from } = readCfg(ctx.cwd);
    const reg = ctx.modelRegistry;
    const lines: string[] = ["── 能力槽位（ctf-models）──"];
    lines.push(`配置文件：${from ?? "（没有 —— 用自动挑选）"}`);
    const t = pickText(reg, cfg, ctx);
    lines.push(`ctf/text   → ${t ? `${t.provider}/${t.id}` : "❌ 没有可用模型（先 /login 或配 models.json）"}`);
    const v = pickVision(reg, cfg, ctx);
    lines.push(`ctf/vision → ${v.model ? `${v.model.provider}/${v.model.id}` : "❌ 无"}（${v.why}）`);
    const avail = (reg.getAvailable?.() ?? []).filter((m: any) => !isVirtual(m));
    const img = avail.filter((m: any) => (m.input ?? []).includes("image"));
    lines.push(`可用模型 ${avail.length} 个，其中能看图 ${img.length} 个`);
    if (!img.length) {
      lines.push("⚠ 没有任何能看图的模型 → ctf-vision 角色会退化成文本模型。");
      lines.push("  想用视觉：/login 一个有视觉的 provider，或写 ctf-models.json 的 vision 字段。");
    }
    return lines.join("\n");
  };

  pi.registerCommand("ctfmodels", {
    description: "能力槽位：看/设 ctf-text 与 ctf-vision 实际路由到哪个模型",
    handler: async (args: string, ctx: any) => {
      const a = (args || "").trim();
      const { cfg } = readCfg(ctx.cwd);
      if (!a) return note(status(ctx));
      const [sub, ...rest] = a.split(/\s+/);
      const val = rest.join(" ").trim();
      if (sub === "auto") {
        const t = pickText(ctx.modelRegistry, cfg, ctx);
        const v = pickVision(ctx.modelRegistry, cfg, ctx);
        if (t) cfg.text = `${t.provider}/${t.id}`;
        if (v.model) cfg.vision = `${v.model.provider}/${v.model.id}`;
        const p = writeCfg(ctx.cwd, cfg);
        return note(`已写入 ${p}\n${status(ctx)}`);
      }
      if (sub === "text" || sub === "vision") {
        if (!val) return note(`用法：/ctfmodels ${sub} <provider/model>`);
        cfg[sub as "text" | "vision"] = val;
        const p = writeCfg(ctx.cwd, cfg);
        return note(`已写入 ${p}\n${status(ctx)}`);
      }
      if (sub === "list") {
        const avail = (ctx.modelRegistry.getAvailable?.() ?? []).filter((m: any) => !isVirtual(m));
        const rows = avail
          .map((m: any) => `  ${(m.input ?? []).includes("image") ? "👁 " : "   "}${m.provider}/${m.id}`)
          .sort();
        return note(`可用模型（👁=能看图）：\n${rows.join("\n") || "  （没有）"}`);
      }
      return note("用法：/ctfmodels [auto | text <p/m> | vision <p/m> | list]");
    },
  });

  for (const [id, name, kind] of [
    [TEXT_ID, "CTF 文本槽位（按你的配置解析）", "text"],
    [VISION_ID, "CTF 视觉槽位（按你的配置解析）", "vision"],
  ] as const) {
    pi.registerVirtualModel({
      provider: PROVIDER,
      id,
      name,
      thinkingLevels: [...THINKING],
      input: ["text", "image"],
      route: (req: any, ctx: any) => {
        const { cfg } = readCfg(ctx.cwd);
        const m = kind === "vision" ? pickVision(ctx.modelRegistry, cfg, ctx).model
                                    : pickText(ctx.modelRegistry, cfg, ctx);
        if (!m) {
          throw new Error(
            `能力槽位 ${PROVIDER}/${id} 解析不到可用模型：` +
            `先 \`/login\` 一个 provider，或跑 \`/ctfmodels auto\`。`,
          );
        }
        const want = kind === "vision" ? cfg.visionThinking : cfg.textThinking;
        const lvl: Level = (THINKING as readonly string[]).includes(want ?? "")
          ? (want as Level)
          : (req.thinkingLevel ?? "medium");
        return { model: m, thinkingLevel: lvl };
      },
    } as any);
  }
}
