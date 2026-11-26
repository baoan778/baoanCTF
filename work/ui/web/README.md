# work/ui/web —— PiCTF 前端工程

> 2026-11-04 重建。原来的前端源码从未入库（仓库里只有一份 2026-10-30 的 `dist/` 快照），
> `docs/状态快照.md` 里那句“改完前端跑 `bash work/ui/build.sh`”当时**指向一个不存在的文件**。
> 现在源码在这里，构建链在这里，`dist/` 是可重建产物。

## 结构

```
web/
  index.html            # Vite 入口，title=PiCTF
  src/main.tsx          # 挂载
  src/App.tsx           # 顶栏 + 四个页签
  src/api.ts            # 全部 /api 调用 + SSE 订阅（唯一出口）
  src/pages/ChatPage.tsx     # 对话（prompt / abort / SSE 流式）
  src/pages/BoardPage.tsx    # 黑板（FGS 图、节点、操作日志）
  src/pages/RolesPage.tsx    # 智能体（增删改 / 启用 / 导入导出 / 编译预览）
  src/pages/SkillsPage.tsx   # 技能列表（可搜）
  src/pages/FilesPage.tsx    # 文件（浏览 / 读文本 / 预览图片 / 目录内搜索）
  src/pages/ModelsPage.tsx   # 模型（提供商增删 / 密钥 / 测试连通）
  src/pages/SessionsPage.tsx # 会话（历史 / 打开 / 改名 / 删除 / 运行中的 runtime）
  src/pages/SubagentsPage.tsx# 子代理（策略、运行中、历史、missions）
  src/pages/InspectPage.tsx  # 记录（flag 命中、安全策略）
  src/pages/SystemPage.tsx   # 运行状态 / pi 配置 / 能力标签
  smoke.mjs                  # 单页挂载冒烟
  smoke-all.mjs              # 逐个页签点一遍（10 页全过才算过）
  src/styles.css
  public/               # 静态资源（图标/字体/manifest）—— ★ 必须放这里，别手放 dist/
  dist/                 # 构建产物，server.mjs 直接提供
```

## 构建：一条命令 `uibuild`（★ 2026-11-18 改）

```bash
uibuild            # 重建 + 回填宿主 dist（要求容器在跑：src 是它的只读挂载）
uibuild --install  # 改了 package.json 时先强制 npm install
uibuild --check    # 只判断 dist 有没有落后于源码（selfcheck 用同一判据）
```

它做四件事：容器里 `tsc --noEmit` + `vite build --outDir /tmp/uibuild-dist` →
`docker cp` 回宿主暂存区 → 校验 `index.html` 引的 JS 真的存在且 >50KB →
**清空并替换** `work/ui/web/dist`（顺手清掉上一版残留的 hash 文件）。
构建失败或校验不过时**宿主 dist 一个字节都不动**。

★ 为什么不能像以前那样「容器里直接 build」：compose 现在把 `work/ui/web/dist`
**只读**挂进容器（宿主那份 == 容器跑的那份），`vite` 的 `emptyOutDir` 会在只读目录上失败。
同理，`src/` 现在也是只读 bind mount —— 宿主改完**容器里立刻可见**，不需要再 `docker cp` 同步
（老写法会把文件 cp 进只读挂载，报 `mounted volume is marked read-only`）。

★ **为什么要求容器在跑**：容器里的 `work/ui/web/src` 是宿主的**只读 bind mount**，
所以「在容器里 build」用的就是宿主当前源码。反过来，用镜像起个临时容器去构建是**有害的**：
镜像里的 src 是建镜像那一刻的快照（实测 2026-11-18 时还停在 11-05 的 `App.tsx`），
它会一本正经地构建出**老 bundle**（实测 `index-Dm7O1sLd.js`，带已删的口令闸门），
而所有自检查看起来都是绿的。这个坑真踩过，所以 `uibuild` 现在直接拒绝。

底层的 `work/ui/build.sh` 还在，现在支持 `--out <目录>` / `--install`，
镜像构建时（`docker/Dockerfile` ⑦·3）也用它：镜像内自己重建一份 dist，
不让「镜像里的前端是几个月前的」这种静默漂移留下来。

## ★ 静态资源必须放 public/（踩过的坑）

`vite.config.ts` 里 `emptyOutDir: true`，每次 build 都把 `dist/` **清空**。
把 `pictf.ico` / `fonts/*.woff2` / `manifest.webmanifest` 直接放在 `dist/` 里的后果：
**下一次 build 全部消失**，页面图标和字体静默变 404（页面不报错，只是难看）。

正确位置是 `web/public/`，Vite 会自动原样拷进 `dist/`。

## 后端契约（server.mjs，不用改）

后端 49 个 `/api` 路由**全部保留**，本工程已接 **49 条全覆盖**（10 个页签）。
关键几条：

| 接口 | 用途 |
|---|---|
| `GET /api/events` | SSE，唯一事件通道；首帧 `ui:hello` 带 `activeRuntimeId` |
| `POST /api/command` | `{type:"prompt", message, runtimeId}` / `{type:"abort"}` |
| `GET /api/board` · `/api/board/<pid>` | 黑板索引 / 单题图与日志 |
| `GET /api/status` · `/api/defaults` · `/api/tools` | 运行状态与配置 |

## pi 1.5 事件流（实测，别猜）

```
turn_start
  message_start {role:"user"|"assistant"|"system"}
  message_update {assistantMessageEvent:{type:"text_delta", delta:"…"}}   ← 流式正文
  message_end   {role, content:[{type:"text",text}]}
turn_end
```

- `extension_ui_request / think-clock` 这类是**高频状态帧**（空闲时也能一秒几十条），渲染层必须忽略，否则列表会被刷爆。
- 每条事件带 `__rt`；只消费 `__rt === activeRuntimeId` 的事件，否则别的会话的输出会混进当前时间线。
- SSE 重连后必须用 `ui:hello.activeRuntimeId` 重新对齐（server.mjs 注释里记着这个坑）。

## 与旧 dist 的关系

旧的 `index-GVQTnXRy.js` / `CodeView-*.js` / `index-BFAel4xH.css` 是 ZeroA 外壳的构建产物（源码已丢失），
`dist/` 里还留着是因为静态处理器会按需取用；新入口只引用新产物。确认无用后可整批删除。
## 作战条（OpsBar，2026-11-05）

顶栏下方常驻 40px 的作战条，四项：**剩余时间 / 平台得分 / 已核验事实 / 下一刀**。
数据来自新接口 `GET /api/ops`（服务端聚合 `fgs deadline` + `cost` + `next`，10 秒缓存）。

设计依据：`learning-notes/LEARNING_CHECKLIST.md` §19（W 系列已并入）→ 其 §1.2 信条 P1（时间是一等公民）/ P3（一屏一个决策）。

★ **纪律：三路数据各自独立失败，失败就显示「—」或原因，绝不渲染成 0。**
把默认值当事实渲染，就是这个项目犯过的错 —— `/api/defaults` 曾把 `piVersion` 写死成 `1.1.0`，
而容器与宿主实测都是 **1.5.0**（2026-11-05 修：改成真跑 `pi --version`，失败返回 `unknown`）。

## 冒烟脚本的三个坑（都踩过）

1. **页签按钮的 textContent 是「图标 + 标签」**（`◈对话`）。原来 `textContent === "对话"` 精确比对
   → 10 个页签永远匹配不上，冒烟 **假红 BAD=10**。现在按 `data-tab` 定位（这轮同时修了 smoke-all / smoke-live）。
2. **改了宿主脚本必须同步进容器**，否则你跑的是镜像里那份旧的（这轮 smoke-live 就因此在容器里假红）。
   `docker cp work/ui/web/smoke-live.mjs pictf:/opt/pi-ctf/work/ui/web/smoke-live.mjs`
3. **必须 `process.exit(0)`**：OpsBar 的 10 秒轮询 + SSE 重连会挂住事件循环，不显式退出 CI 会一直等。

依赖：`jsdom`（已进 devDependencies）。容器里若没有，`npm i -D jsdom` 一条即可。
