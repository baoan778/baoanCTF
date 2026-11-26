# SoL-Pi 本地补丁（vendored，别当上游原样）

上游：`NVlabs/SoL-Pi` @ `06b3ee3`（2026-10-12），MIT，见 `LICENSE`。
本目录是**上游源码 + 下方补丁**，随 PiCTF 分发。升级上游时：重新 vendor 后把补丁重放一遍，
再跑 `dev/build/selftest.sh` 里的 `sol-pi` 那组断言。

---

## pictf-1（必须，勿丢）：`then_run` 必须按 `isError` 判成功/失败

**文件**：`extensions/action-fusion/then-run.ts`（`runFusedMutation()` 里，约 114 行）

**为什么**：pi 0.85.1 起，`bash` 工具的**非零退出不再抛异常**，而是 resolve 成
`{ content: [...], isError: true }`（见 pi 的 `dist/core/tools/bash.js`：`if (exitCode !== 0)` 直接 return）。
上游只用 `try/catch` 判失败，所以在 pi 1.1.0 上：

> **验证命令失败（比如 `exit 7`）会被标成 `[then_run:succeeded]`。**

这正好是「改文件 → 顺手跑验证」这条链上最不能出错的地方（假绿会让 agent 以为 exp 跑通了）。

```ts
const bashResult = await bash.execute(`${toolCallId}:then_run`, thenRun, signal, undefined, ctx);
// pi 1.1.0: 非零退出不再抛异常，而是 resolve 成 isError:true 的结果 → 必须显式判 isError
if ((bashResult as { isError?: boolean }).isError) {
    throw new Error(resultText(bashResult));
}
const output = resultText(bashResult);
```

**怎么算生效**：`dev/build/selftest.sh` 的「sol-pi」组会断言本行存在，并**先造病**（把它删掉 → 断言必须变红）。

---

## 已知但**未改**：类型层与 pi 1.1.0 的差异（不影响运行期）

上游是照 pi 0.85.1 写的；在 1.1.0 的 `.d.ts` 下 `npx tsc --noEmit` 有 25 处
`TS2345: ExtensionContext 不能赋给 ExtensionToolContext`（本目录 src 里 1 处、上游 tests 里 24 处）。
pi 用 **jiti 转译**加载扩展（`dist/core/extensions/loader.js`），**不做类型检查**，所以运行期无影响，
实测（见下）四机制全部正常注册。改它只会扩大与上游的 diff，故保留原样。

## 实测矩阵（2026-10-13，本机）

| 项 | 结果 |
|---|---|
| 上游 `scripts/check-pi-compat.mjs`（对 pi 1.1.0） | PASS |
| 上游 vitest 全套（对 pi 1.1.0） | 194/195 → **打上 pictf-1 后 195/195** |
| 买家包形态加载（ROOT 下 0 个 node_modules，扩展为目录） | PASS：真会话能跑，注册出 `obs_recall`(ObservationPack) 与 `then_run`(Action Fusion) |
| 引擎安装（`engine/server.mjs` installAgentPack） | PASS：首次启动把本目录铺到 `<数据>\agent\extensions\sol-pi\` |

## 分发边界（给发行方）

- 买家包**不**带 `node_modules`：本扩展的 `typebox` / `@earendil-works/*` 由 pi 自己的
  jiti 别名解析（护在 pi 内部），无需随包第三方依赖。
- 四个机制**默认按 `engine/agent-pack/sol-pi.json` 走**：只开 Action Fusion + ObservationPack
  （纯本地、不额外调模型、不打断 run）；Evidence-Preserving Reducer 与 Online Context Compact
  默认关（前者会把日志发给另一个模型；后者会 abort + 自动续跑）。
