/**
 * pi-lab: 会话内模型切换（Cyber-Model-1 ⟷ OpenCode Go 订阅的 DeepSeek）
 *
 * 为什么需要它：模型是会话启动时定的，agent 自己没有任何内置工具能改；
 * 唯一能改的是扩展里的 pi.setModel()。而扩展只在会话启动时加载，
 * 所以除了直接敲命令，还需要一条「写标记 → /reload → 自动切换」的旁路
 * （给 shell 侧的 `mswitch` 用）。
 *
 * 命令：
 *   /ms                    → 在 go 和 cyber 之间来回切（无参数时按当前模型反向）
 *   /ms status             → 看当前模型 / 两个通道 / cyber 令牌是否就绪
 *   /ms go | /ms cyber     → 切到指定通道
 *   /cyber [模型id]        → nccsec 通道（默认 cyber-model-1）
 *   /cyber opencode-go/glm-5.3 → 也接受 provider/model 写法
 *   /ocgo [模型id]         → opencode-go 通道（默认 deepseek-v4.1-flash，老写法保留）
 *
 * 自动（一次性）：shell 里 `mswitch cyber` 会写 <agentDir>/switch-on-reload.json
 *   {"provider":"nccsec","model":"cyber-model-1"}
 * 然后敲 `/reload`，本扩展读到它就切换、并把这个标记文件删掉（只生效一次）。
 *
 * 认证说明：
 *   · opencode-go 的认证来自 .pi/agent/models.json —— baseUrl 指向
 *     /inference/go/openai/v1，apiKey 用 tools/bin/opencode-token 现场取。
 *   · nccsec 的认证是 apiKey = tools/bin/nccsec-token（读 NCC 大模型中心建的令牌，
 *     来源：$NCCSEC_API_KEY 或 <agentDir>/nccsec-token 或 <仓库根>/.env）。
 * 没认证时 pi.setModel() 返回 false，命令会提示「没有可用的认证」而不是静默失败。
 */

import * as fs from "node:fs";
import * as path from "node:path";

/** 两个通道：别名 → [provider, 默认模型] */
const CHANNELS = {
  go: ["opencode-go", "deepseek-v4.1-flash"],
  cyber: ["nccsec", "cyber-model-1"],
  // Claude 中转（ai98pro.xyz）。opus=4.6 是实测能用的那个；opus5 上游常空。
  opus: ["ai98", "claude-opus-4-6"],
  opus5: ["ai98", "claude-opus-5-5"],
  sonnet: ["ai98", "claude-sonnet-5"],
  haiku: ["ai98", "claude-haiku-4-5"],
  // Kimi（Moonshot 官方）
  kimi: ["kimi", "kimi-k3"],
  "kimi-code": ["kimi", "kimi-k2.7-code"],
  "kimi-fast": ["kimi", "kimi-k2.7-code-highspeed"],
};
const FALLBACK = CHANNELS.go;
const TOKEN_FILE = "nccsec-token";

export default function (pi) {
  const agentDir =
    process.env.PI_CODING_AGENT_DIR || path.join(process.cwd(), ".pi", "agent");
  const markerFile = path.join(agentDir, "switch-on-reload.json");

  function cloudTokenPresent() {
    if (process.env.NCCSEC_API_KEY) return true;
    try {
      return fs.existsSync(path.join(agentDir, TOKEN_FILE));
    } catch {
      return false;
    }
  }

  async function switchTo(ctx, provider, modelId) {
    if (!provider || !modelId) {
      ctx.ui.notify("用法: /ms [go|cyber|provider/模型id]", "error");
      return false;
    }
    const model = ctx.modelRegistry.find(provider, modelId);
    if (!model) {
      ctx.ui.notify(
        `找不到模型 ${provider}/${modelId}（pi --list-models 可看全部；nccsec 只有 cyber-model-1）`,
        "error",
      );
      return false;
    }
    const ok = await pi.setModel(model);
    if (ok) {
      ctx.ui.notify(`已切换到 ${provider}/${modelId}`, "info");
    } else {
      const hint =
        provider === "nccsec"
          ? "nccsec 要令牌：nccsec.cn 个人中心→大模型中心→新建令牌 → echo '<令牌>' > .pi/agent/nccsec-token"
          : "检查 .pi/agent/models.json 里该 provider 的 apiKey";
      ctx.ui.notify(`${provider}/${modelId} 没有可用的认证 —— ${hint}`, "error");
    }
    return ok;
  }

  /** 把参数解析成 [provider, model]；args 为空时用 alias 的默认模型 */
  function resolve(args, alias) {
    const want = (args || "").trim();
    if (want) {
      const slash = want.indexOf("/");
      if (slash > 0) return [want.slice(0, slash), want.slice(slash + 1)];
      return [CHANNELS[alias][0], want];
    }
    return CHANNELS[alias];
  }

  pi.registerCommand("ms", {
    description: "模型切换：/ms 在 go 与 cyber 之间来回切，/ms cyber 直接切，/ms status 看状态",
    handler: async (args, ctx) => {
      const want = (args || "").trim();
      if (want === "status" || want === "list") {
        const cur = ctx.model ? `${ctx.model.provider}/${ctx.model.id}` : "(未知)";
        const token = cloudTokenPresent() ? "✔ 已配令牌" : "✘ 没令牌（cyber 会切不过去）";
        ctx.ui.notify(
          `当前 ${cur} ｜ go=${CHANNELS.go.join("/")} ｜ cyber=${CHANNELS.cyber.join("/")} ${token}`,
          "info",
        );
        return;
      }
      if (!want) {
        // 无参数 = 来回切：当前在 nccsec 就回 go，否则去 cyber
        const onCyber = ctx.model && ctx.model.provider === CHANNELS.cyber[0];
        const [p, m] = onCyber ? CHANNELS.go : CHANNELS.cyber;
        await switchTo(ctx, p, m);
        return;
      }
      if (CHANNELS[want]) {
        await switchTo(ctx, ...CHANNELS[want]);
        return;
      }
      const slash = want.indexOf("/");
      if (slash > 0) {
        await switchTo(ctx, want.slice(0, slash), want.slice(slash + 1));
        return;
      }
      ctx.ui.notify(`不认识的通道 '${want}'（go / cyber / provider/模型id）`, "error");
    },
  });

  pi.registerCommand("cyber", {
    description: "把当前会话切到 NCC 的 Cyber-Model-1（默认），可带模型 id 或 provider/模型id",
    handler: async (args, ctx) => {
      await switchTo(ctx, ...resolve(args, "cyber"));
    },
  });

  pi.registerCommand("ocgo", {
    description: "把当前会话切到 OpenCode Go 的模型（默认 deepseek-v4.1-flash）",
    handler: async (args, ctx) => {
      await switchTo(ctx, ...resolve(args, "go"));
    },
  });

  pi.on("session_start", async (event, ctx) => {
    // 只在重载/启动时看一次标记文件；用过就删，避免每次开会话都被改模型
    let raw;
    try {
      if (!fs.existsSync(markerFile)) return;
      raw = fs.readFileSync(markerFile, "utf8");
      fs.unlinkSync(markerFile);
    } catch {
      return;
    }
    try {
      const cfg = JSON.parse(raw) || {};
      await switchTo(
        ctx,
        cfg.provider || FALLBACK[0],
        cfg.model || FALLBACK[1],
      );
    } catch (err) {
      ctx.ui.notify(`自动切换失败: ${err}`, "error");
    }
  });
}
