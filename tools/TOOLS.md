# pi-lab 工具清单（唯一事实来源）

> 本文件由实测生成，**技能/提示词里写工具必须以此为准**。
> 改工具后请重跑 `bash tools/make-bin.sh` 并更新本文件。
> 最后核对：2026-09-30

`run-pi.sh` 已把 `tools/bin` 接进 PATH，所以下面这些**直接敲命令名就行**。

> ★ **`tools/bin/` 里不许放裸非 bash 脚本**（2026-11-19 血账）。宿主 Windows 的 MSYS/Git-Bash
> **exec 层不解析脚本 shebang**：凡是非 MSYS 原生（原生 PE）的解释器，一律退化成「用 shell 解释整个文件」
> → `line 26: quiet: command not found` / `syntax error near unexpected token ('('`。判据是「解释器是不是
> MSYS 原生」，与语言无关：`#!/usr/bin/env bash` 过，`#!/usr/bin/env python3` 与 `#!/usr/bin/env node` 都不过。
> 所以 Python 实现一律放 `tools/<name>.py`，由 `tools/make-bin.sh` 的 `self_tools` 生成 `#!/bin/bash` +
> `exec "$PY" "$T/<name>.py" "$@"` 的壳进 `tools/bin/`；真手写的 bash 入口才放 `tools/hand/`。
> `selfcheck` 与 `board/tests/test_bin_exec.py`（27 断言，含反向探针）都守这条。

---

## 一、pi-lab 自带工具（`tools/bin/`）

> **数量类事实不写死**（写死必漂）：本机到底有哪些、哪些真的在，现场算 —— `toolreg check`。
> 分工：**本文件 = 人读的「怎么用」（唯一事实来源）；`toolreg` = 机器可读的「在不在 / 在哪」**。
> 做题时现装了新工具 → `toolreg record <id> <实测路径>` 写回，下次不用再找（借 ZeroA 的 tool-catalog 思路）。

### 侦察 / HTTP / 爬虫
| 命令 | 干什么 |
|---|---|
| `httpx` | ⚠ **本机跑不通**：软链到 `gobin/httpx.exe`（Windows 侧 Go 程序），实测 `no address found for host` **且退出码 0**（安静地无结果）→ 改用 `probe` |
| `probe` | ★ **WSL 侧探活/指纹**（自研 `tools/probe.py`）：状态码/标题/Server/技术栈 + 自动给 `poc` 查询提示；默认 1 rps、只读 GET/HEAD、JSONL 落盘 |
| **`ctfsurf`** | ★ **前端残迹 → 攻击面推演**（自研 `tools/ctfsurf.py`）：从 HTML/CSS/JS 抠**悬空 CSS 类**（定义未渲染 = 未露出功能的指纹）/ 表单 action 词汇表 / 题面中文动词 → 给出 P0·P1·P9 攻击面清单 + **反爆破闸门**（小应用先穷举词汇表，别开字典）。`ctfsurf --dir work/<pid>` / `--url <u> --save` / `--selftest`。解读见 `skills/ctf-web/references/frontend-residue.md` |
| **`apiharvest`** | ★ **前端 asset → 接口清单**（自研 `tools/apiharvest.py`）：fetch/axios/XHR `open` 的 URL + router `path/url` 配置 + 引号包裹的路径串 → 端点表（api/route/path/static 分类、模板变量归一成 `{param}`、query 拆参数字段、多方法合并）。`apiharvest --dir work/<pid>` / `--url <u> --save` / `--json` / `--selftest`；单 asset > `--big`（默认 500KB）打**闸门警告**（主 entry 不许整读）。四层打法见 `skills/ctf-web/references/api-recon.md` |
| **`llmrec`** | **LLM 调用级留痕**（自研 `tools/llmrec.py`）：把 pi 会话 JSONL 摊成「一条调用一行」——`n/session/label/ts/provider/model/api/usage{input,output,cache_read,cache_write}/missing_usage/cost/in_chars/out_chars/tools/stop`。`llmrec --dir <会话目录>` / `--file <x.jsonl>` / `--out log/llmrec.jsonl` / `--json` / `--tail N` / `--label judge`。**只做 transcript 层**：wire 原始体本机拿不到，不伪造（见 `docs/并行工作.md` §4.6u）；缺 usage 计 `missing_usage` 并明说「不是真的 0」 |
| **`flagwatch`** | ★ **flag 形态扫描**（自研 `tools/flagwatch.mjs`，零依赖）：从任意文本抠 `XXX{...}` / `flag: xxx` / base64 / hex 变形候选，**宁缺毋滥**（代码构造/模板/正则自身都不误报 —— 本仓 `flag-formats.md`/`AGENTS.md` 扫出 0 命中）。`flagwatch --selftest`。★ 已接成扩展 `.pi/agent/extensions/flag-watch.ts`（`tool_result` 钩子：命中就把警告**插进工具输出** + notify + 记 `log/flag-watch.jsonl`）—— 治「flag 已出现在输出里却被划过去」 |
| `nuclei` | 模板化漏洞扫描（主力） |
| `katana` | 爬虫，抓 URL/表单/JS 端点 |
| `gau` | 从 Wayback/CommonCrawl 拉历史 URL |
| `urlfinder` | ⚠ **本机没有 urlfinder**（2026-11-03 实测）→ 从 JS 抠接口用 `jsfinder`（有包装）或 `grep -oE '"/[a-zA-Z0-9_/.-]+"'` |
| `subjack` | ⚠ **本机没有 subjack**（2026-11-03 实测）→ 子域接管手工核 CNAME + 各家接管指纹表（`poc` 可查） |
| `enscan` | ⚠ **本机没有 enscan**（2026-11-03 实测）→ 资产收集用 `subfinder`/`amass`/`assetfinder`（Go bin 里都有） |
| `naabu` | 端口扫描（Go，快） |
| `masscan` | 端口扫描（大网段、需 root） |

### 目录 / 内容爆破
| 命令 | 干什么 |
|---|---|
| `dirsearch` | 经典目录扫描（自带字典、支持 `--random-agent`） |
| `feroxbuster` | 递归内容发现（快） |
| `hydra` | 口令爆破（ftp/ssh/http 等） |

### Web 漏洞利用
| 命令 | 干什么 |
|---|---|
| `commix` | ⚠ **本机没有 commix**（2026-11-03 实测：Git-Bash 与 WSL 两侧均 `command -v` 不到，Go bin 里也没有）→ 命令注入手工构造 `;`/`$()`/反引号/`%0a` 变形，或 `poc` 查现成骨架 |
| `dalfox` | ⚠ **本机没有 dalfox**（2026-11-03 实测）→ XSS 用 `xsstrike`（有包装）或手工/`poc` |
| `xsstrike` | XSS 扫描/绕过 |
| `ssrfmap` | SSRF 利用 |
| `tplmap` | SSTI 利用（含 RCE） |
| `jwt_tool` | JWT 测试（alg:none / 弱密钥 / kid） |
| `pocbomber` | POC 批量验证（本地 POC 库） |
| `poc` | 组件/CVE → 本地 POC 索引（`poc "<组件>"`、`poc --cve CVE-xxxx-xxxx`） |
| `xxeinjector` | XXE 利用（Ruby） |

### 指纹 / 组件识别
| 命令 | 干什么 |
|---|---|
| `cmseek` | CMS 识别 + 版本 + 深扫 |
| `wafw00f` | WAF 识别 |
| `graphw00f` | GraphQL 端点识别 |
| `hashid` / `hash-identifier` | 哈希类型识别 |

### 源码 / 版本库泄露
| 命令 | 干什么 |
|---|---|
| `githack` | `.git` 泄露 → 还原源码 |
| `svnexploit` | `.svn` 泄露利用 |
| `rip-git` `rip-svn` `rip-hg` `rip-bzr` `rip-cvs` | dvcs-ripper，各类版本库泄露拖取 |
| `dumpall` | 源码泄露综合利用 |
| `jsfinder` | 从 JS 抓敏感信息/接口 |

### 取证 / 逆向 / 密码
| 命令 | 干什么 |
|---|---|
| `pwnkit` | **pwn 常备**（★ 2026-10-01 新增）：`reach` 连通性 / `info` 保护与打法 / `fmt probe\|leak\|write` format string 三件套 |
| `foremost` | 文件雕复（carving） |
| `imgintel` | 图片情报流水线：EXIF + 分块 OCR(带置信度过滤) + 线索筛选 + WGS84/GCJ02/BD09/**BD09MC** 换算 + 反查链接，落 `notes/osint-*.md` |
| `lane_gate` | **lane 事前闸门**（`tools/lane_gate.py`，CLI `tools/bin/lane_gate`）：`lane_gate check '<命令>'` 判 + 给改写模板；`lane_gate bg '<命令>'` 生成 tmux 后台命令。拦 R1 长任务管道接 tail / R2 前台爆破大扫描 / R3 宽窗口 `grep -o` / R4 裸 `grep` 等 stdin / R5 裸 `&` 起中继 / R6 不可逆破坏 / R7 网段扫描 |
| `lane_guard` | **lane 看门狗**：没落盘(NO_NOTES)/卡死(STUCK)/超时间盒(OVER_BOX)/僵尸(ORPHAN)，`--check` 有告警退 1 |
| `vol` | 内存取证 —— **Volatility 3**（venv 里的 `volatility3`，wrapper `tools/bin/vol`）。★2026-11-04：本行原来写「Volatility 2.6」且命令名写 `volatility`，两个都错 → selfcheck 的 forensics 项一直在误报「可选缺」。实际 `vol --version` = 3.x |
| `pycdc` | .pyc 反编译 |
| `pycdas` | .pyc 反汇编 |
| `pyinstxtractor` | PyInstaller 打包程序解包 |
| `rsatool` | RSA 参数计算（已知 p/q/e/d 推其余） |

### 运行时
| 命令 | 干什么 |
|---|---|
| `py` | **pi-lab 专用 python**，已装 CTF 库（见第三节）。凡是要 `pwn`/`z3`/`Crypto` 的脚本，用 `py xxx.py`，别用系统 `python3` |
| **`toolreg`** | ★ **工具清单自登记 + 自检测**（★ 2026-10-12 新增；借 ZeroA `tool-catalog` 的问题陈述）：`toolreg check [--record]` 逐条真检测（**记录路径 → PATH 含 .exe → Python import → Node 模块**），`--record` 把新发现的路径写回 `tools/tool-registry.json`；`toolreg add <id> --commands a,b --direction web --note "…"` 登记新工具；`toolreg record <id> <实测路径>` 写回；`toolreg missing` 只列缺的（**有缺失退出码 1**，可当门禁）。⚠ 默认**不执行任何程序**；要版本号得显式 `--version`（白名单参数 + 5s 超时 + 截断 —— 清单是不可信输入）。发布包/Windows 友好（纯标准库、自解析路径、UTF-8 兜底） |

### 会话 / 留档（★ 2026-09-29 新增，治 “读爆 VM”）
| 命令 | 干什么 |
|---|---|
| `sesscan list [N]` | 最近 N 个 pi 会话：时间 / 大小 / 条目数 / 末条类型 / 首问 |
| `sesscan find <词> [--in user\|assistant\|all\|tool]` | 跨会话搜关键词（流式，命中给行号+片段） |
| `sesscan show <会话> [--head/--tail/--line N]` | 看条目；每条截断，不会把 576KB 单行打上屏 |
| **`sesscan where <会话>`** | ★ **这个会话停在哪**：最后事件 / 最后用户消息 / 最后工具调用 / **悬空工具调用**（会话是在工具执行中被切断的）+ 条目类型分布 |
| `sesscan stats` | 会话目录总账（数量 / 总大小 / 最大 5 个） |
| `sesscan --selftest` | 自检（临时目录，不碰真会话） |

### 录题台 / 值班台（★ 2026-10-10 新增）
| 命令 | 干什么 |
|---|---|
| `intake` | ★ **CTF 录题台（浏览器版）**（自研 `tools/intake.py`，纯标准库）：浏览器填表（题面/类型/附件/靶场）→ 落 `work/inbox/<pid>.json` + 附件 → pi 侧扩展 `ctf-inbox` 每 4s 扫一次，把结构化题面当**用户消息**投给 agent（正忙则 `followUp` 排队）。Windows 双击 `open-intake.bat` 即用（支持拖文件上传）；`intake --once card.json` 可脚本投递；`intake --port N` 换端口。⚠ 超出 24h 的卡片不自动发（须 `/inbox fire <n>`）。**平时用 F2 的 TUI 版即可，这个是备用/大表单** |
| `/inbox`（pi 命令） | 收件箱面板：排队/已发/过期待确认；`/inbox fire <n>` 手动发射；`/inbox on\|off` 自动发射开关 |
| `/ctf`（pi 命令） | ★ **值班台**（按需打开）：在打哪几题 / 黑板事实进度 / flag 候选 / 会话耗时与上下文占用（读 `board/projects/*/graph.json` + `log/flag-watch.jsonl`）。注：常驻状态栏已于 2026-10-10 按操作者要求取消 |
| **`F2` / `/intake`** | ★ **窗口内录题台**（`.pi/agent/extensions/ctf-intake-tui.ts`）：TUI 弹表单（题名/类型/靶场·目标/flag格式/附件路径/**题目内容**）→ `Ctrl+Y`（或直接**终端粘贴**）灌题面 → `Ctrl+S` 提交 → 卡片落盘 + **直接当用户消息发给 agent**。不离开终端 |
| `triage`（工具） | ★ **附件开箱摸形状**（`ctf-triage.ts`）：一次调用给出 类型/哈希/大小 + 按类型分流的只读侦察（zip→列目录・图片→exif/binwalk・流量→capinfos/协议树・ELF→架构与保护）+ **该起哪份 skill**；只读，不解包写盘 |
| `wpcollect` | ★ **WP 素材收集器**（`tools/wpcollect.py`）：`wpcollect <pid>` → 把黑板图/附件清单/flag 窗口/证据目录排成 `work/<pid>/WP-draft.md` 骨架，`(待补)` 处等结论。也可 `/wp <pid>` |
| `/flags`（pi 命令） | flag 候选全表；**`/flags copy <n>` 直接写进 Windows 剪贴板**（powershell interop）→ 粘到平台就能交；`/flags src <n>` 逐字溯源（在会话文件里找它到底从哪来）；`/flags mark <n> submitted\|confirmed` 记回执状态 |
| `/cost`（pi 命令） | 会话开销与边际产出：输出/输入 token、轮数、黑板 verified 总数、多少轮没推进、flag 命中；`/cost gate on` 超预算拦新 lane |
| `/targets`（pi 命令） | 靶场台账（录题台卡片自动入账 + 手工 `/targets add`），免得反复问 nc 地址 |
| `/lanes`（pi 命令） | 在跑的 worker + 12h 内活跃黑板一览 |
| `/hint`（pi 命令） | OSINT（最弱项 33%）线索请求模板，一键生成并进剪贴板 |
| `autoshape`（扩展） | **大输出自动摸形状**：输出 >12K 字符时在结果末尾**追加**形状卡（行数/高频行形态/线索计数/首尾行），原文不截断。阈值 `PI_AUTOSHAPE_MIN`，关闭 `PI_AUTOSHAPE=0` |

> 实现：`.pi/agent/extensions/ctf-console.ts`（值班台）+ `ctf-inbox.ts`（收件箱）；均只读 + 全程 try/catch，坏文件超大文件都会跳过。

### pi 界面（★ 2026-10-10 新增）
| 命令 | 干什么 |
|---|---|
| `pi-bench [--mins] [--ext <名>] [--runs N]` | ★ **量扩展/插件让我慢多少、贵多少**：每个配置起一次性 `pi --no-session`（不落会话），读它的 `usage.input+cacheRead`（=每轮真提示词）与自己算出的墙钟，拆成 **启动→出话(含TTFT) / 模型 / 收尾** 三段 —— 避免把模型抖动算到扩展头上。用它得出的结论：**我手写的 12 个 .ts 共 +226 tok、启动 +0s、每次工具调用 0.05~0.4ms（可忽略）；npm 插件包才是大头** |
| `bash tools/pi-ui-patch.sh` | ★ **界面文案精简**（幂等补丁）：全屏模式向上滚动时底部那条胶囊写死了英文长文案 `↓ Jump to latest message · Ctrl+End` —— pi **没有配置项**能改它，扩展 API 也只管状态栏，够不着这条 `scrollToEndIndicator`，所以只能改它 dist 里的文案。本脚本换成短标签（默认 `↓ 回到最新`，`--label "↓ 最新"` 可换），快捷键仍由 pi 按 keybinding 渲染。`--check` 只查不写（退出码 0=已生效）；`--revert` 还原英文。三层安全网：留 `.pi-ui-orig` 原件备份 · 改完 `node --check`（不过即回滚）· 清 Node 编译缓存。**⚠ `npm i -g` 升级 pi 会冲掉 → 重跑；`selfcheck` 有闸门盯着。改完要新开一个 pi 才看得到** |
| `powershell -File tools/fix-font-maple.ps1` | 修 Windows 侧「**找不到以下字体：Maple Mono NF CN**」。病根（2026-10-10 坐实）：`HKCU\...\CurrentVersion\Fonts` 里那四条写的是**裸文件名**，而 Windows 只认**全路径**（同目录能用的 UbuntuMono 写的就是全路径）→ GDI/DirectWrite 都枚举不到。脚本改成全路径 + `AddFontResourceW` + 广播 `WM_FONTCHANGE`；体检用 `check-font-maple.ps1`。改完**重启 Windows Terminal** |
| `uibuild [--check\|--install]` | ★ **重建 webUI 前端产物（2026-11-18 新增，唯一正确入口）**：compose 把 `work/ui/web/dist` **只读**挂进容器后，容器里不能再直接 `vite build` 写 dist。`uibuild` 做「容器内 `tsc --noEmit` + `vite build --outDir /tmp/uibuild-dist` → `docker cp` 回宿主暂存区 → 校验 index.html 引的 JS 存在且 >50KB → 清空换掉 `work/ui/web/dist`」；任何一步失败**宿主 dist 一个字节不动**。`--check` 只判「dist 有没有落后于源码」（与 selfcheck 同一判据，落后退 1）；`--install` 强制先 `npm install`（改了 package.json 用）。**要求容器在跑**：容器里的 `work/ui/web/src` 是宿主的只读 bind mount，用它构建才等于「用当前源码构建」；用镜像起临时容器会构建出**镜像快照里的旧前端**（实测踩过：产出带已删口令闸门的老 bundle 且自检全绿）—— 这条路径已被直接拒掉 |

### 配置目录 / 换盘（★ 2026-10-11 新增：治「UI 切会话 10+ 秒」）
| 命令 | 干什么 |
|---|---|
| `bash tools/pi-agent-sync.sh` | ★ **同步两份 pi 配置**：仓库 `agent-template/`（git 跟踪的真源；`.pi/config/` 是旧约定，早已不存在）↔ `$HOME/.pi-ctf/agent`（pi 实际读的 ext4 真身）。规则**分两类**（2026-11-14 校正）：`settings.json`/`sol-pi.json`/`mcp-adapter.json`/`agents/` 是**单向下发**（仓库真源，按内容 `--checksum` 比对，真身更新且内容不同时告警并存 `.live-conflict`）；`roles/`/`prompts/`/`themes/`/`extensions/` 是**双向 mtime 新的赢**。`run-pi.sh` / `start.sh` 启动时会自动跑（2026-11-14 才真正接线；此前两处只有注释没有调用）。**为什么要两份**：pi 每次启动对 agent 目录做 ~2000 次文件访问（`auth.json` 被 stat **1106 次**、npm 包目录 608 次、`models-store.json` 216 次），`/mnt/d` 是 9p(drvfs) 每次 ~4ms → 冷启动/切会话 **11~29s**；放 ext4 上同样操作 **<1s**（实测同负载 25.5s → 10.3s，空闲时 11~13s → 1~2.5s）。⚠ 三个坑：① 路径上**只要有一层 `/mnt/d`**（软链、bind mount 都算）就照样按 drvfs 计价；② 只把 `npm` 软链去 ext4 治不了本（还是 2.4ms/次）；③ `PI_CODING_AGENT_DIR` 必须指**真身路径**，不要指望 `.pi/agent` 软链。 |
| 布局 | 仓库：`agent-template/{agents,roles,prompts,extensions,themes}`（`extensions-extra/` 2026-11-19 已删：它是 `extensions/` 的旧快照、独有文件数 0） + `settings.json`/`sol-pi.json`/`models-store.json`（git 跟踪 96 个文件；`.pi/config/` 旧写法从未存在）· `.pi/agent` = **指向 `/root/pi-lab-agent` 的软链**（只为兼容写死老绝对路径的文件，如 `missions/index/*.json`；已被 gitignore）· 运行数据（auth/sessions/missions/npm/bin…）都在 ext4 真身里 |

### Kimi 通道（★ 2026-10-11 新增：Moonshot 官方）

**用**：`mswitch kimi` → pi 里 `/reload`；或会话内 `/ms kimi`。别名：`kimi`(K3) / `kimi-code`(K2.7 Code) / `kimi-fast`(K2.7 Code 高速版)。

| 项 | 值 |
|---|---|
| provider | `kimi`（配在 `.pi/agent/models.json`，gitignore 过） |
| baseUrl | `https://api.moonshot.cn/v1`（OpenAI 兼容） |
| key | `.env` 的 `KIMI_API_KEY`（`apiKey` 写 `!kimi-token` 引用，见下） |
| 模型 | `kimi-k3` / `kimi-k2.7-code` / `kimi-k2.7-code-highspeed` / `kimi-k2.6`（4 个，全部实测出字） |
| 上下文 | 256K（**思考型**：返回里 `content` 与 `reasoning_content` 都有；max_tokens 给太小会被思考吃光、看起来"空回复"） |

**key 从哪来（★ 2026-10-12 修）**：`models.json` 的 `apiKey` = `!$PI_LAB_ROOT/tools/bin/kimi-token`，
不再硬编码；取值顺序 `$KIMI_API_KEY` → `<agentDir>/kimi-token` → `.env` 里的 `KIMI_API_KEY=` 行。
体检：`kimi-token --status`（打码 key + 真打一次探测，把 `org-…` 一并回显）。

> ⚠ **2026-10-12 现场踩的坑**：`models.json` 里原本硬编码了一把 **Tier0（免费档）** 的 key
> （`org-42617853…<ak-fd8edouatm6111hmbsw1>`，**max RPM 3 / TPD 1.5M/天**）。
> 当天额度跑满后会话直接 `429 … reached organization TPD rate limit, current: 1598542, limit: 1500000`
> → pi 报 `Retry failed after 3 attempts` 硬失败。**TPD 是日额度，重试没有任何用**：
> 换 key（`.env` 的 `KIMI_API_KEY` 是另一个健康账号）或换通道（`/ms go`、`/ms cyber`）。
> 看到 `organization TPD rate limit` 时**先 `kimi-token --status`**，别对着会话反复重试。
>
> **额度档位（官方 `platform.kimi.com/docs/pricing/limits`，按*累计充值*分档）**：
> Tier0 ¥0 → 并发1 / RPM 3 / TPM 500K / **TPD 1.5M**；Tier1 ¥50 → 并发15 / RPM 100 / TPM 2M / **TPD 不限**；
> Tier2 ¥100 → 40/100/3M/不限；Tier3 ¥500 → 50/200/3M/不限。
> → **只要不是 Tier0，就没有 TPD 天花板**；报告里出现的 `TPD limit: 1500000` + `max RPM: 3` 就是 Tier0 的指纹。
> ⚠ 官方明写：**限速按「用户/账号」计，不按 key 计** —— 同一账号换 key 没用，得换账号（两把 key 余额不同 = 两个账号）。
> ⚠ 代金券**不计入**累计充值，所以档位看真实现金充值。
>
> ⚠ `kimi-coding/k3`（`https://api.kimi.com/coding`）**当前不可用**：它是 Kimi For Coding 订阅端点，
> 实测拿 Moonshot 开放平台的 key 打它一律 **401**（两把 key 都 401）。要用得单独开订阅换 key；
> 不想踩就在 `.pi/settings.json` 的 `enabledModels` 里去掉它。

**它和 ai98 中转的关键区别**：Kimi 是官方 key，**在仓库里带 AGENTS.md + 16 个技能跑也不会被内容审计拦**（实测 `pi --print --model kimi/kimi-k3` 直接出结果）。

### Claude 中转通道（★ 2026-10-11 新增：ai98pro.xyz）

**用**：`mswitch opus` → pi 里敲 `/reload`；或直接在会话里 `/ms opus`。别名：`opus`(Opus 4.6) / `opus5`(Opus 5.5) / `sonnet`(Sonnet 5) / `haiku`(Haiku 4.5)。

- provider 名 `ai98`，配在 `.pi/agent/models.json`（gitignore 过）；key 放 `.env` 的 `AI98_API_KEY`（`run-pi.sh` 用 `set -a` 导出，`apiKey` 写 `$AI98_API_KEY` 引用，不落明文）。
- **⚠ 两个实测坑**：
  1. **内容审计**：这个中转带审计。它会拦 **AGENTS.md 与技能描述**（实测：仓库内 4/4 被拦，提示 `内容审计命中风险规则`；`-ns -nc` 两个都关就过 —— 只关一个还是拦）。→ **它只适合非安全语境**（写文档、通用编码、在干净目录跑）；想在 pi-lab 里做 CTF 还得用 go / cyber。
  2. **容量抖**：上游是共享账号池，会随机回 `All available accounts exhausted`（一分钟级的抖动，重试即可）；`claude-opus-5/5-5/4-8` 比 `4-6` 更容易空。
- 自测：`cd /tmp && pi --print --model ai98/claude-opus-4-6 "回复OK"`（干净目录，6/6 通过）。

### 模型切换（★ 2026-10-10 新增：NCC Cyber-Model-1 ⟷ OpenCode Go 订阅的 DeepSeek）

两个通道，谁也不用改代码，切就完事：

| 通道 | provider/model | 认证 | 价格 |
|---|---|---|---|
| **go** | `opencode-go/deepseek-v4.1-flash` | opencode console 账号（`tools/bin/opencode-token` 现场取，免 key） | $10/月订阅 |
| **cyber** | `nccsec/cyber-model-1` | NCC 大模型中心令牌（`tools/bin/nccsec-token` 读） | **2.10 / 8.40 / 0.42 / 0.00 模型积分每 1M tok**（输入/输出/缓存读/缓存写；1 安全币 = 1 积分，每日登录免费发放 + 安全币兑换） |

| 命令 | 干什么 |
|---|---|
| `mswitch` | 看当前：`.env` 默认通道 + 待生效标记 + cyber 令牌是否就绪 |
| `mswitch cyber` \| `mswitch go` | **切通道**：① 改 `.env` 的 `PI_PROVIDER/PI_MODEL`（下次启动生效；首次改动留 `.bak-mswitch` 备份） ② 留 `.pi/agent/switch-on-reload.json` → 在 pi 里敲 **`/reload`** 当场生效（读完即删，只生效一次） |
| `mswitch <provider>/<model>` | 任意通道；`--no-env` 只留标记不动默认；`--selftest` 自检（临时目录） |
| **`/ms`**（pi 命令） | ★ **会话内一键来回切**：当前在 cyber 就回 go，反之亦然；`/ms cyber` `/ms go` 指定；`/ms status` 看当前模型+令牌状态 |
| `/cyber` \| `/ocgo`（pi 命令） | 直接切到某通道，可带模型 id 或 `provider/模型id` |
| `nccsec-token [--status]` | 取/看 NCC 令牌（`.pi/agent/models.json` 的 apiKey 就是 `!nccsec-token`）；`--status` 会打 `/v1/models` 自检并说明去哪拿令牌 |
| `Ctrl+P` / `shift+ctrl+p` | pi **原生**循环（`.pi/settings.json` 的 `enabledModels` 已把它限成上面这两条）；`Ctrl+L` 开模型选择器，`/model nccsec/cyber-model-1` 也行 |

> **拿 cyber 令牌**（没令牌 `/ms` 会提示「没有可用的认证」，不会静默失败）：nccsec.cn 登录 → 个人中心 → **大模型中心** → 「新建令牌」；然后任选一种：
> `echo '<令牌>' > ${PI_CODING_AGENT_DIR:-~/.pi-ctf/agent}/nccsec-token` 或把 `NCCSEC_API_KEY=<令牌>` 写进 `.env`（环境变量优先）。
> 匿名 `GET https://ai.nccsec.cn/v1/models` **不要 key** 就能列出 `cyber-model-1`，拿来确认端点活着。[^cyber]
>
> ⚠ `models.json` 里的 `cost` 数字是 **模型积分**（按平台标价填的），pi 会按 $ 显示 —— 当**相对量**看，别当美元。
> 回归测试：`python3 board/tests/test_model_switch.py`（20 条断言，全离线；扩展用假 pi 驱动）。
>
> **2026-10-10 实测（真的跑过）**：令牌已落在 `.pi/agent/nccsec-token`（已在 `.gitignore`，与 `.env.bak-mswitch` 一起防泄漏）；`nccsec-token --status` → `✔ 可用`（HTTP 200）；
> `pi --provider nccsec --model cyber-model-1 -p "…"` 真出话；**marker 路径也验过**：不传 `--provider/--model` 启动，会话真的跑在 `nccsec/cyber-model-1`，marker 读完即删。
> ⚠ `/v1/models` 有**全局每秒限速**，会回 `429 [GS] 全局每秒请求次数超限` —— 属**重试类**，`nccsec-token` 已内置退避重试（429/5xx 重试 3 次），别把它当「令牌不行」。
> ⚠ 上游回显的 `model` 是 `MiniMax-M3.1-Flash-Preview`、`message.name` 是 `MiniMax AI` —— 即这条通道后端实际是 MiniMax M3.1 Flash Preview，「网安垂域」是 NCC 那层的包装。

[^cyber]: 来源：`/llm` 页静态资源 `assets/index-CBWWllFl.js`（模型介绍与四条价格）+ `assets/docs-BPtJeN6U.js`（Base URL 与 `cyber-model-1` 请求示例）+ `tools/bin/llmCenter-*.js`（兑换规则「1 安全币 = 1 模型积分」、免费池/兑换池）。实机复核：`curl https://ai.nccsec.cn/v1/models`。

> **⚠ npm 扩展包的位置别放错（实测差 6 倍）**：`.pi/agent/npm/` 是**软链 → `/root/pi-lab-npm`（ext4）**。
> 同一份代码放 D:（drvfs 小文件读 **5 MB/s**）启动 **79.7s**，放 ext4 **12.6s**；真实全量发现从 **110s → 7.5s**。
> 换机/重装后若这目录变成真目录且在 /mnt/d 下，`selfcheck` 会告警。`pi install` 写入会跟随软链，不用特殊处理。

### TSecBench 跑分平台（★ 2026-09-30 新增，接口契约见桌面 `CHALLENGES_API.md`）
| 命令 | 干什么 |
|---|---|
| `tsec config` | 看生效配置（base_url / token 打码 / vpn_file / 来源） |
| `tsec list` | 题目列表 + 进度（难度 / level / flag 数 / 容器状态 / 直连地址） |
| `tsec start <unique_code>` | 启动题目容器，返回 `container_addr`（一个题可能多个地址） |
| `tsec addr <unique_code>` | 拿地址：`stopped` 自动 start，`pending` 轮询到 `available` |
| `tsec hint <unique_code>` | 取提示（★**会按 `hint_cost_radio` 扣分**；已通关的题不给看） |
| `tsec submit <unique_code> <flag>` | 提交 flag；`duplicate` = 该 flag 早交过了（幂等，别改 flag 硬试） |
| `tsec close <unique_code>` | 释放容器（同时最多 3 道，满了先关一道） |
| `tsec vpn up --file <x.ovpn>` / `status` / `down` | 起/查/停跑分 VPN（OpenVPN；日志出现 `Initialization Sequence Completed` 才算通）。★ 2026-11-04 起 **status/down 零依赖**（没 openvpn/tun/sudo 也能跑，只报「缺什么」），**只有 up 会被缺件挡住**并给出可执行指引；容器内要真起隧道需 `devices:/dev/net/tun` + `cap_add:NET_ADMIN` + openvpn（compose/Dockerfile 已配好）。回归：`python3 board/tests/test_tsec_vpn.py`（12 项，宿主与容器都要跑） |
| `tsec healthcheck` | 探 `http://10.0.100.58`（平台 SDK 用的靶场探活口）——**它在 VPN 里才可达，通了才算 VPN 真通** |
| `tsec run-new --agent N [--set 4]` | 用平台 UI 的 JWT 建跑分任务 → 拿 `BENCHMARK_TOKEN` + 该任务 ovpn（写回 `~/.config`）。★建完马上 `tsec list`，平台只等 10 分钟（否则 `wait_expired`） |
| `tsec run-status [run_id]` | 看任务状态/剩余时间/得分/异常，并顺手把 `vpn_config` 落盘 |
| `tsec sets` | 列可用试题集（要 platform_token）：id / 题数 / 满分 / 时长 / **能否本地跑**。`hosted_only=True` 的（Tsecbench v2、CourseCrack、T-Vuls、hideonbush）只能平台托管；本地可选 **4** Tsecbench v1(63题/23000/6h)、**9** bctf-2026、**1** XBOW、**5** Cybench |
| `tsec plat-token [--token X]` | 取平台 UI 的 JWT（`platform_token`）→ 判过期 → 写进配置（0600）。不给 `--token` 就**自动扫本机浏览器 profile 的 Local Storage leveldb**（Chrome/Edge 都认，键是 `_https://tsecbench.zc.tencent.com\x00\x01token`）—— 平台是腾讯众测 OAuth，没有「用户名密码换 token」的接口，这是唯一免手抄的路。回归：`python3 board/tests/test_tsec_platform.py`（32 项） |
| `tsec --selftest` | 本地 mock server 跑全流程（24 项断言，不碰真平台） |

> 实现 `tools/tsecbench.py`（**只用标准库**，评测环境裸跑）。配置优先级：环境变量
> `TSEC_BASE_URL` / `TSEC_TOKEN`（或 `BENCHMARK_*`）→ `~/.config/pi-lab/tsecbench.json`
> → `<repo>/.pi/tsecbench.json`；**token 别写进仓库**。错误码已翻译：
> `task_not_found`=token 不对/任务不在 · `invalid_state`=任务结束或活跃实例>3 ·
> `duplicate`=重复提交 · `resource_unavailable`=靶场没就绪。

> **为什么要有它**：会话单文件可到 5MB、单行可到 576KB。手写
> `py - <<EOF ... open(p).read().splitlines() ... EOF` 这种整读+拼接的脚本
> 会涨到 7G，把 8G 的 WSL VM 打到 global OOM（2026-09-29 三次事故）。
> `sesscan` 逐行流式 + 自带 RLIMIT_AS（`--mem-mb`，默认 1200）+ 输出截断（`--width`，默认 400）。
>
> 同理：**`fgs` 收外部路径的参数**（`verify --from` / `publish --op-file` /
> `swarm --targets-file`）一律走 `board/fgs.py:read_capped()` —— 只认普通文件 +
> 32MB 上限（`PI_FGS_MAX_READ_BYTES` 可调）。`/dev/zero`/FIFO/目录会被**快速拒绝**。

> **崩过一次怎么续跑（判据，别猜）**：
> 1. `sesscan where <会话>` 有**悬空工具调用** ⇒ 那次是在工具执行中被切断的
>    （典型：2026-09-29 起 WSL 拆发行版 → pi 吃 SIGKILL = 退码 137）；
> 2. 启动器现在自带**保活**：崩了会退避重启并自动 `--continue`，同一个 60s 内连崩 3 次才停手
>    （留痕 `log/pi-restarts.jsonl`；关掉用 `PI_LAB_KEEPALIVE=0`）；
> 3. 窗口已经关掉了就手动续：`run-pi.bat --session <id>`（已经钉住会话时不再叠加 `--continue`）；
> 4. 会话文件是 **append-only** 的 —— 崩不会丢历史；真正会丢的是「没写进图、没提交的推断」。

> `nosqlmap` 未挂命令：源码是 Python2（`print` 语句），本机只有 py3，跑不了。目录保留，需要时用容器。

---

### Windows 侧逆向工具（WSL interop · ★ 2026-09-30 新增）

IDA Pro 8.3 与 Ghidra 11.4 **只有 Windows 版** —— 已拷进仓内自用副本 **`tools/win/`**（重物不进 git；
来源与再同步见 `tools/win/README.md` + `tools/win-sync.sh`），`tools/winbridge.py` 负责找路径 + 调。
WSL interop 能直接执行 `.exe`，所以下面这些在 WSL 里直接敲就行（实测均通过）：

| 命令 | 干什么 | 实测结果 |
|---|---|---|
| `ida check` | 找 IDA/Ghidra/Java 并打印路径（不装东西） | ✓ 命中仓内副本 `tools/win/IDA_Pro_v8.3_Portable/ida64.exe` · `tools/win/ghidra_11.4.1_PUBLIC` · JDK 26 |
| `ida <binary>` | IDA 文本模式批量反汇编 → `<bin>.asm` + `<bin>.i64`（**不弹 GUI**） | ✓ `/bin/true` 出 256KB 反汇编 |
| `ida <binary> --decompile` | 再导出**伪 C** → `<bin>.decomp.c`（走 Hex-Rays） | ✓ `成功 124 / 失败 0`（67KB 伪 C） |
| `ghidra <binary>` | Ghidra headless import + 自动分析（项目落在 `D:\AI\tmp\ghidra-proj`） | ✓ `Analysis succeeded / Import succeeded`，小 ELF 4 秒 |
| **`tools/win/` 里还有（GUI，Windows 直开）** | `CFF_Explorer`（.NET 反编译）· `GDA`（Android dex）· `DIE` / `ExeinfoPe`（壳与编译器识别）· `LogParser`（Windows 事件日志，应急用） | 用途见 `tools/win/README.md` |
| `win-sync.sh [--check]` | 重新同步这些工具（换机/升级后跑） | 幂等：已在就跳过 |

```bash
ida check                                   # 先看路径对不对
cp ./chall /mnt/d/AI/tmp/ && ida /mnt/d/AI/tmp/chall --decompile
grep -n "strcmp\|memcmp" /mnt/d/AI/tmp/chall.decomp.c   # 直接读伪 C 找校验点
ghidra /mnt/d/AI/tmp/chall --postscript Dump.java
```

> **坑（实测踩过）**：`cmd.exe` 只认 Windows 路径 —— 给它 `/mnt/...` 会回「系统找不到指定的路径」。
> `winbridge.py` 因此对 IDA 走"WSL 路径执行 exe + 相对参数 + cwd 落在目标目录"，对 Ghidra（必须经 cmd.exe）才做 `wslpath -w` 转换。
> **pwndbg 不在** WSL/Windows 的普通目录里，它在那台 Kali 虚拟机镜像里（`D:\AI\kali\kali_zss完整版\*.vmdk`）—— 要用先起 VM。

## 一·B、音视频 / 文档 / 移动端（★ 2026-10-10 补齐，先前**完全没有**）

| 命令 | 干什么 |
|---|---|
| `ffmpeg` / `ffprobe` | 音视频解码：抽帧（`-vf fps=1`）、转码、**频谱图**（`-lavfi showspectrumpic`）、拼接/截段。视频题、音频隐写题的入口 |
| `sox` | 音频处理：`sox in.wav -n spectrogram -o spec.png`（频谱图）、重采样、声道分离、倒放、变速 |
| `mediainfo` | 媒体容器/编码/时长元数据一眼看全（比 ffprobe 好读） |
| `multimon-ng` | **DTMF / Morse / POCSAG 等信令自动解码**：`multimon-ng -t wav -a DTMF -a MORSE_CW in.wav` |
| `pdftotext` / `pdfimages` / `pdfinfo` | PDF 文本/图片/元数据提取（先前只有 pypdf） |
| `qpdf` / `mutool` | PDF 解密/修复/结构分析（`qpdf --decrypt`、`mutool clean/info`） |
| `apktool` + `java` | APK 反编译/回编（`apktool d app.apk`）；**WSL 里终于有 java 了**（JDK 25，apktool 拉的 JRE） |
| `regripper` | Windows 注册表取证（`regripper -r NTUSER.DAT -a`） |
| `jq` / `zip` | 先前文档写"本机没有"的 jq 已装；`zip` 用于造测试用加密包 |
| `strace` / `ltrace` / `patchelf` / `yara` | 动态跟踪（syscall/库调用；pwn 看程序行为、re 看校验点）· ELF 依赖改写（改 RPATH/解释器，pwn 常客）· YARA 规则扫样本（恶意样本题） |
| **`pcapmap`** | ★ **pcap 一键画像**（`tools/pcapmap.py`）：一条命令给七块面板 —— 基本画像 / 协议分层 / TCP 会话（内外网标注）/ **明文凭据**（FTP USER·PASS 与 230·530 配对，含爆破计数）/ **文件传输**（声明大小 vs 实际抓到 / 中断 426 / **REST 断点续传**）/ 异常与隧道迹象（DNS 长名·ICMP 载荷·TLS 体积·expert 告警）/ 自动下一步提示。`--export DIR` 顺带导出对象。**做流量题第一件事就跑它**（2026-10-10 实测：ez_ftp 手敲十几条 tshark 的活，它一条命令给全） |
| `tcpflow` | **JPEG 隐写三件套**（都端到端实测过）：`jsteg hide|reveal in.jpg [FILE] [out.jpg]`（系数 LSB，无口令）· `outguess -k <口令> -d <in> <cover.jpg> <out.jpg>` 嵌入 / `-r <stego.jpg> <out>` 提取 · `f5 e <载体.bmp> <out.jpg> -e <msg> -p <口令>` / `f5 d <stego.jpg> -p <口令>`（F5 矩阵编码，抗卡方）。⚠ `f5` wrapper 里已带 `xvfb-run` + `--add-exports java.base/sun.security.provider=ALL-UNNAMED`（JpegEncoder 继承 awt.Frame，且用了 JDK 内部类）；⚠ apktool 拉的 `default-jre-headless` **没有 AWT**，要用 `default-jre` |
| `xvfb-run` | 虚拟 X 显示：跑「继承 awt.Frame」的 java 工具（F5 等） |
| `bkcrack` | **加密 zip 已知明文攻击**（`tools/bin/bkcrack`，源码经 ghproxy 编译）：`bkcrack -C enc.zip -c known.txt -p known.txt` 恢复 keys → `-D plain.zip` 解密。实测端到端通过 |

> 依赖补充（`py` 的 venv）：`oletools`（Office 宏/vba 分析）· `sklearn`（AI/ML 题）· `imageio`/`soundfile`（图像/音频 IO）·
> `pikepdf`（PDF）· `py7zr`/`pyzipper`/`zipfile_deflate64`（压缩包；`pyzipper` 支持 AES zip）· `patool`（万能解包）。
> ⚠ **GitHub 直连不通**（pip/apt 走镜像能过）：要拉 GitHub 源码用 **`https://ghproxy.net/https://github.com/...`**。

## 一·C、pwn / crypto 迭代台（★ 2026-10-10 新增，全部端到端实测）

| 命令 | 干什么 |
|---|---|
| **`libcsearch`** | ★ **用 leak 反查 libc 版本**（走 libc.rip API，本机可达）：`libcsearch puts=0x80e50 read=0x1148c0`；`--download DIR` 直接把命中的 libc 下下来；`--local <libc>` 读本地符号偏移（对照用）。**pwn 流程里的关键一步**：leak → 定版本 → 拿对应 libc/偏移，别再靠猜 |
| **`pwnsetup`** | ★ **把题目二进制改成用题目自带 libc/ld 跑**（pwninit 的轻量替代）：`bash tools/pwnsetup.sh ./chal ./libc.so.6 ./ld-2.31.so` → 拷 libc/ld + `patchelf --set-interpreter/--set-rpath` + ldd/版本检查。流程：`libcsearch` 下载 libc → `pwnsetup` → `pggdb` 调 |
| **`pggdb`** | ★ **带 gef 的 gdb**（`gdb -q -x tools/gobin/gef.py`）：`heap`/`vmmap`/`telescope`/`checksec` 等。**不污染 `~/.gdbinit`**。注：pwndbg 本机装不上（ghproxy tarball 404、PyPI 无包）→ 用 gef 顶上；Kali VM 里仍有 pwndbg |
| **`rsactf`** | ★ **RsaCtfTool**（源码 editable 装进 venv；PyPI 只有 py3.8 旧包）：`rsactf --publickey pub.pem --decryptfile flag.enc --attack all`。⚠ 参数名是 `--decryptfile`（老版才叫 `--uncipherfile`）。实测：Fermat 友好 RSA → 秒破并解出明文 |
| **`sage`** | ★ **SageMath**（2026-11-04 补齐）：大格约化 / Coppersmith 小根 / 离散对数 / 椭圆曲线 / `GF(p)` 有限域。用法：`sage -c "print(factor(2**64-1))"` · `sage script.sage`（`^` 在 .sage 里自动变幂运算）· 裸 `sage` 进 REPL。**为什么是 passagemath**：Ubuntu 24.04(noble) 已下架 `sagemath` 包（`apt-cache policy` → Candidate: none），`pip install sagemath-standard` 要现场编译 10 万行 Cython 且依赖 python<3.12（容器是 3.12，ResolutionImpossible）。passagemath 是纯 manylinux 轮子版，不编译。**装完必须跑 `bash docker/sage-shim.sh`**——它生成上游的 `sage/all.py` 与 `sage/all_cmdline.py`（passagemath 只提供 `sage/all__sagemath_<模块>`，缺了 `from sage.all import *` 与 `sage script.sage` 全崩，实测）。⚠ 别用 `python3 -m pip`：PEP 668。用 `tools/venv/bin/pip` |

> 典型 pwn 循环：拿到 leak → `libcsearch` 定版本并 `--download` → `pwnsetup` 拼环境 → `pggdb` 调堆/栈 →
> `py exp.py`（pwntools，远程前先本地跑通）。

## 一·D、WASM / EVM / .NET 盲区补位（★ 2026-10-12 新增，端到端实测）

> 由来：2026-10-12 实测本机 `wasm2wat/ pywasm/ wasmtime/ solc/ web3/ ilspycmd/ dnSpy` **全无**，
> 而 `skills/ctf-reverse/SKILL.md` 里 `wasm|evm|.net` **零命中**——三类一直在 CTF 里出现的载体没有入口。
> 参照物是 REA（`github.com/morluto/rea`）的**无引擎路径**（它值钱的不是 MCP，是这三条固定入口 + 输出边界）。

| 命令 | 干什么 |
|---|---|
| **`revkit wasm`** | ★ **WASM 一条龙**：`.wat`→`.wasm` 自动编（wat2wasm）· `--info`（段/导入导出，wasm-objdump）· `--wat`（wasm2wat）· `--decompile`（伪码，默认）· **`--run FN --args 1,2` 用 wasmtime 直接跑导出函数**（rev 题最常用的“喂输入看输出”） |
| **`revkit evm`** | ★ **EVM 字节码离线分析**：`--disasm`（pyevmasm，pc 为主键）· `--push4`（dispatcher 的 selector **候选**）· `--selectors "transfer(address,uint256);balanceOf(address)"`（keccak256(sig)[:4]，分号分隔；**别用逗号**——签名里的逗号会被拆） |
| **`revkit dotnet`** | ★ **.NET 纯静态**（不加载程序集、不跑 CLR）：`--types` · `--methods NS.Type` · `--strings`（#US 堆的 UTF-16 可打印串）· `--il 'NS.Type::Method'`（dncil 反汇编 IL，带 maxstack / locals / handler 数） |
| **`revkit --selftest`** | 三块能力自检（不联网）：wat2wasm+wasmtime 跑 `add(2,40)=42` · pyevmasm+keccak selector `a9059cbb` · dnfile 解一个真程序集 |

**边界（写死在工具输出尾部，别自己脑补）**：WAT/反编译**不是**原始源码 · IL **不是** C# 源码 ·
EVM 的 selector/参数是**推断候选**且本工具**不做链上查询**。

**底层（写脚本时直接用）**：`py` 里已装 `wasmtime pywasm pyevmasm eth_utils eth_abi web3 dnfile dncil`；`wat2wasm/wasm2wat/wasm-decompile/wasm-objdump/wasm-interp/wasm2c` 在 PATH（apt `wabt`）。
上游注意：REA 要求 WABT **1.0.42**（本机 apt 给的是 1.0.36）——做题时以**本机实测版本**为准，别照抄外部文档的版本号。

```bash
revkit wasm ./chal.wasm --info --decompile --run check --args 1,2
revkit wasm ./chal.wat  --info                 # .wat 会自动编成 .wasm
revkit evm  0x60003560e01c8063a9059cbb1461001c --disasm --push4
revkit evm  ./runtime.bin --selectors "transfer(address,uint256)"
revkit dotnet ./App.dll --types --limit 20
revkit dotnet ./App.dll --il 'App.Program::Check' --limit 40
```

## 二、系统已装命令（`apt` 来的，实测可用）

```
curl wget nmap ffuf sqlmap john hashcat git unzip 7z
python3 pip3 ruby perl gcc gdb
readelf objdump strings file openssl
binwalk exiftool tshark steghide zsteg tesseract
upx (upx-ucl 4.2.4，2026-10-01 装) nc socat r2(radare2) node
```

**明确没有，技能里不要写**：`zbarimg`

> 2026-10-01：`upx` **已装**（`apt-get install -y upx-ucl`）；0xGame id=36 实盘用过 ——
> `upx -d <bin> -o <out>` 即可脱壳（Everest 那类，进 PATH 就行）。
>
> ★ 2026-11-21（R5-C）：上面「明确没有」是按**宿主**写的，容器 `pictf` 里
> `jq / java / patchelf / ltrace / strace` **都有**（实测 `command -v`）。
> 跑分/解题一律以容器为准，别引用宿主的缺项。

### 二·B、容器 `pictf` 专有（R5-C 2026-11-21 补装，实测 `command -v`）

| 命令 | 用途 | 实测 |
|---|---|---|
| `sshpass` | SSH 口令/跳板自动化（pwn/内网题） | `/usr/bin/sshpass` |
| `php` | PHP 反序列化 / phar / 模板题的本地生成与验证 | `PHP 8.3.6` |
| `sqlite3` | 直接读题给/落盘的 SQLite（web 题常见 `*.db`） | `/usr/bin/sqlite3` |
| `redis-cli` | Redis 未授权/读写题 | `/usr/bin/redis-cli` |
| `tcpdump` | 抓包（tshark 之外的第二条取证路） | `/usr/bin/tcpdump` |
| `mysql` / `mysqldump` | 指向 `mariadb` / `mariadb-dump` 的兼容软链 | `MariaDB 15.1` |

> 缺什么先 `bash /opt/pi-ctf/tools/ensure-tool.sh --check`；纯系统命令用
> `apt-get install -y <包>`（容器内即时生效），并同步 `docker/Dockerfile` 的
> apt 列表，否则重建镜像就丢。

---

## 三、Python 库

| 解释器 | 已装 |
|---|---|
| 系统 `python3`（3.14） | `requests bs4 lxml yaml sympy numpy scipy PIL capstone` |
| `py`（= `tools/venv/bin/python`） | 上面全部 **+** `pwn(pwntools) z3 Crypto(pycryptodome) gmpy2 ecdsa ropgadget unicorn` |

> 系统 `python3` **没有** `pwn z3 Crypto gmpy2 unicorn ropgadget ecdsa`。
> pwn / crypto / reverse 题的脚本一律用 `py`。

---

## 四、字典（`tools/wordlists/`）

| 路径 | 内容 |
|---|---|
| `wordlists/弱口令/` | 弱口令表（admin/后台/9000 用户名等） |
| `wordlists/手机号字典/` | 手机号段 |
| `wordlists/dirsx/dicts/` | 目录/文件/后缀/年份/月份等 fuzzing 字典（给 ffuf/feroxbuster 用） |
| `wordlists/7kbscan/` | 7kbscan 配套字典 |
| `knowledge/riftx/recommended-skills/security-passwords/references/Passwords/` | NCSC 10 万口令 / probable-v2 top12000 / darkweb2017 top10000 |
| `knowledge/riftx/recommended-skills/security-usernames/references/Usernames/` | 用户名字典 |

---

## 四·B、离线知识基线（`tools/knowledge/`）

**方法论 / payload / 脚本 / 字典的离线副本** —— 无外网也能查。索引：`tools/knowledge/INDEX.md`。

| 路径 | 内容 | 来源 |
|---|---|---|
| `knowledge/INDEX.md` | **先读这个**：36 个 skill → 用途 → 对应 pi-lab 哪个 skill | 生成物 |
| `knowledge/riftx/recommended-skills/<skill>/references/` | 深水区手册（idor-deep 578 行、cache-poisoning 1297 行、request-smuggling 40K、ghost-bits-cast 743 行…） | RiftX（MIT） |
| `knowledge/riftx/recommended-skills/<skill>/assets/` | payload 集（SQLi 报错/时间盲注、DBMS 指纹、路径穿越文件名…） | RiftX（MIT） |
| `knowledge/riftx/recommended-skills/<skill>/scripts/` | 可直接跑的脚本（`boolean_sqli_tester.py`、`sqli_payload_generator.py`、`response_analyzer.py`…） | RiftX（MIT） |
| `knowledge/riftx/recommended-skills/security-*/references/` | payload / 规律 / 字典（`php-magic-hashes.txt` 等） | RiftX（MIT） |
| `knowledge/noptrace/INDEX.md` | **手册主题索引（454 行 / 286 条索引行）** —— 按事件类型定位 → 行号直达，别整读手册 | 生成物（`ir-manual-index` 项目） |
| `knowledge/noptrace/NOPTrace-Linux.md` | NOPTrace Linux 应急手册（504 个标题 / 7904 行）—— 查某个技术的细节 | NOPTrace（见该目录 PROVENANCE） |
| `knowledge/noptrace/NOPTrace-Windows.md` | NOPTrace Windows 应急手册（692 个标题 / 11903 行） | 同上 |

```bash
# 手册别整读：先看目录，再 grep 定位
grep -n '^#' $PI_TOOLS/knowledge/noptrace/NOPTrace-Linux.md | head -60
grep -n '内存马\|无文件\|memfd' $PI_TOOLS/knowledge/noptrace/NOPTrace-Linux.md
```

### 应急响应成套 SOP（`skills/ir-response/`）

| 入口 | 用途 |
|---|---|
| `skills/ir-response/SKILL.md` | 6 阶段 FSM + 事件分流 + 三条线 + HITL 闸门 |
| `skills/ir-response/by-event/index.md` | **分流入口**：9 类事件的 最小验证 → 最小隔离 → 清除恢复 |
| `skills/ir-response/by-event/ctf-ir.md` | CTF 应急题：题目问题转任务板 + VBR 证据锚点 |
| `skills/ir-response/by-platform/linux.md` | Linux 现场：命令序列 + 持久化面（合并版，一条链读完） |
| `skills/ir-response/by-platform/windows.md` | Windows 现场：命令序列 + 持久化面 + EVTX 速查 |
| `skills/ir-response/by-platform/web-server.md` | access.log 还原攻击链 + webshell 三层交叉排查 |
| `skills/ir-response/practice/{evidence-and-custody,post-incident-and-lateral}.md` | 证据规范 / 横向定损 |
| `skills/ir-response/templates/` | 报告 + 时间线模板 |

```bash
# 从黑板直接生成处置报告 + 时间线（不编时间：无时间戳就用于记录时间并打 *）
python3 skills/ir-response/scripts/ir-report.py <pid> --out reports/ir-report.md

# 交出去能直接进封禁流程的那张表（只看后缀：.csv / .json / .md，不认识就拒）
python3 skills/ir-response/scripts/ir-report.py <pid> --ioc-out reports/iocs.csv

# 打转检测 + 纠偏提示（重复 Fact / 反复 claim / 反复被拒投递 / 停摆；exit 1 = 在打转）
python3 skills/ir-response/scripts/loop-check.py <pid>
```

> 两者都**优先读 Fact 的结构化元数据**（`--phase/--time/--ioc`），没有再退回正则兜底。
> 记一条结构化的：
> ```bash
> fgs fact-add <pid> "异常外联 pool.bad.xyz:3333" --confidence verified \
>   --evidence "ss -antp 输出第 4 行" --phase triage --action collect \
>   --tool ss --target srv-01 --time "2026-09-01T03:12:00Z" --ioc "IP=203.0.113.77"
> ```
```bash
# 索引怎么重建（改了搬运范围就跑它，别手改 INDEX.md）
python3 $PI_TOOLS/knowledge/build-index.py

# 典型用法：卡在某个攻击面时按目录名直接定位
grep -rn "redis" $PI_TOOLS/knowledge/riftx/recommended-skills/exploit-ssrf/references/
```

> **坑**：RiftX 的 `SKILL.md` 正文里混着它自己工作台的 API（`browser navigate`、`record_finding`、
> `spawn_subagent`）—— 本机没有这些。**只取方法论 / payload / 脚本**，工具调用段落换成
> `httpx` / `katana` / `curl` / `sqlmap` / `py`。
> 许可与搬运范围见 `knowledge/riftx/PROVENANCE.md`（MIT, Copyright (c) 2026 Ch1nfo）。

---

## 四·B-0、残留进程清理（`sweep`）

| 入口 | 用途 |
|---|---|
| `sweep ls` | 只读：列出登记过的 PID + 扫到的可疑残留（http.server / ssh -N / nmap / socat / nc -l / 后台 py） |
| `sweep track <名> [PID]` | 登记（PID 可来自 stdin：`cmd & echo $! \| sweep track x`） |
| `sweep kill <名\|--all>` | 杀登记的：SIGTERM → 3s → SIGKILL，带审计 |
| `sweep clean [--force]` | 扫描式清理：默认只报告，`--force` 才动手 |

防自杀护栏：拒绝「自己/祖先/命令行含 sweep|pgrep」的 PID（开发时真踩过：把执行命令的 shell 自己杀了）。
登记目录 `work/.procs/`（gitignore），审计 `work/.procs/sweep.log`。

---

## 四·B-1、对抗实战：别把 IP 打没（`quiet`）



| 入口 | 用途 |
|---|---|
| **`quiet ip`** | 当前出口 IP + Clash 当前选中节点（`.com`/`.cn` 那种 ICP 拦截与出口无关，先看清） |
| **`quiet trap <IP> <域名>`** | ★ **判据**：域名层拦截 vs IP 被封（A 域名 / B IP / C IP+Host 三方对比） |
| `quiet probe <URL>` | 封禁探测：连发小请求判 403/429/连接重置/超时 |
| `quiet req <URL> --qps 1 --times N` | 节流请求：QPS 限制 + 429/5xx 指数退避 + 抖动（默认禁用环境代理，直连目标） |
| `quiet scan <IP> [-p -]` | 低速扫描：`nmap -Pn -sS -p- -T1 --min-rate 80 --max-retries 5 --open`（实测参数，详见文档） |
| `quiet egress ls \| use <节点>` | 换出口（Clash RESTful API：`CLASH_API` / `CLASH_SECRET`） |

判据与实战纪律：`skills/src-recon/references/对抗-别把IP打没.md`（三形态判据表 + 低速扫描参数 + 出口切换 + "异常即情报"）。

---

## 四·B-2、Windows 远程执行（防御靶场 / 应急响应）

| 入口 | 用途 |
|---|---|
| **`winrm selftest`** | 先验通道本身（WSL interop / UTF-8 / 环境变量转发 / Remoting cmdlet），**不需要靶机** |
| `winrm probe <host> [--https]` | 探测 5985/5986（401/405 = WinRM 活着；000 = 不通，给备选通道） |
| `winrm run <host> '<PS 命令>'` | 一次性执行（`Invoke-Command`）。凭据走 `PI_WINRM_USER/PASS` |
| `winrm session <host> < cmds.txt` | ★ **一次认证跑 N 条命令**（排查主力，输出带 `### 命令` 分隔） |
| `winrm via-jump <host> '<cmd>'` | 穿跳板机（`PI_WINRM_JUMP=user@jump`，走 `ssh -L`） |
| `winrm trust-add <host\|*>` | 工作组/IP 靶机必配（客户端 `TrustedHosts`，需管理员） |

原理与踩坑：`skills/ir-response/references/winrm-远程通道.md`（含六条实测坑 + 按"三条线"组织的取证命令集 + 修复纪律 + 无 WinRM 时的备选链路）。

---

## 四·B-3、本地知识库（`kb`）—— 参考 / 手法 / PoC 一个入口（★ 2026-10-12 新增）

> 由来：本机早就有一堆好料（`skills/*/references` 185 篇 · `tools/knowledge` 194 篇 ·
> `docs/writeups` 手法库/WP · `ctfpoc` 1.13 万条 PoC），但**没有检索面**，只能人 `grep` + 猜目录。
> 参照《CTF Agent 调优》那篇的入库/检索/列来源三件套，**但不上向量库**：要查的是"怎么打"的方法论与 payload，
> 词法检索够用，而且离线、零 API key、零外网（向量方案要 embedding key；CTFusion 2026 实测"检索=污染通道"，
> 所以本库**只收方法论/reference/PoC，不收答案**，每条结果都带来源路径+行号，当证据用）。

```bash
kb build                                   # 建/更新索引（增量：只重算改动过的文件；全量 ~6s）
kb search "堆溢出 tcache" --cat pwn --top 5   # 一次搜完：参考 + 笔记 + PoC + 手法库
kb search "泛微 e-cology"                    # 中文直接搜（bigram 分词）
kb search "CVE-2021-44228" --kind poc         # CVE 走精确通道（只回编号命中的）
kb read skills/ctf-pwn/references/heap-chains.md --lines 16-33   # 取命中那一段（有界输出）
kb read 2 --query "nacos"                     # 也可以用检索结果编号
kb poc "nacos"                                # PoC 检索转发给 `poc`（它仍是唯一真相）
kb stats / kb stale                           # 概况 / 索引是否落后（exit 1 = 需重建）
```

| 要点 | 值 / 说明 |
|---|---|
| 索引规模 | **≈3.11 万块**（2026-11-18 实测 31154 块 / 609 文件）：PoC 11290 · ctfwp 7902 · ref 6751 · notes 4135 · doc 697 · writeup 205 · skill 174（以 `kb stats` 为准；随 docs 增改小幅浮动，别背死） |
| 索引位置 | **`$HOME/pi-lab-kb/`**（**快盘**；仓库在 `/mnt/d` 是 drvfs 小文件 ~5MB/s）。★ **不写死 `/root`** —— 非 root 用户（发布包 / WSL 普通账号）读不到 `/root` 会整库不可用，2026-11-02 修。`PI_LAB_KB_DIR` 可显式覆盖；索引是派生物，**不进 git** |
| 性能 | 全量重建 5.7s · 增量 3.5s（PoC 加载占大头）· 单次检索 **0.6s** |
| 分类过滤 | `--cat web|pwn|reverse|crypto|misc|forensics|osint|malware|ai-ml|binary|ir|src|kit|meta|wp|poc` |
| 来源过滤 | `--kind ref,skill,notes,writeup,poc,doc,ctfwp` ｜ 路径过滤 `--path 2026/review` |
| 收什么 / 不收什么 | ✅ `skills/**/references` · `tools/knowledge`（**除 `ctf-wp/`，它是独立来源**） · `docs/writeups` · `docs/*.md` 手册 · **`tools/knowledge/ctf-wp/**`（WP 仓库，kind=`ctfwp`，cat=方向）** · PoC 索引。❌ `ctf-wp/2026/raw/`（6.3MB 原始抓取，会稀释检索）❌ `docs/状态快照.md`（churn 高，放进来 `kb stale` 天天报红）· `架构设计.md` |
| 打分 | BM25（标题 3x / tags 2x / 正文 1x）+ 标题整串命中加成 + **带判定式的 PoC 优先** |
| 改分块规则后 | 必须改 `CHUNKER_VERSION`（否则增量重建会因 mtime 没变而全复用旧块 —— 踩过） |
| ★ 索引过期怎么办 | **永远 `kb build`，永远不要删库**。索引是 100% 派生物（`index.jsonl` 47MB + `meta.json`，全量重建 6.8s），删了只会白等一次全量，任何「清除/重置」都是错的动作。判断口径：`kb stale`（exit 1 = 落后）。容器内 `/opt/pi-ctf/kb` **已挂宿主 `./kb`**（2026-11-04），重建结果跨容器重建存活；之前没挂 → 每次 recreate 都退回镜像构建时刻那份 |
| 回归 | `board/tests/test_kb.py`（25 条：增量/切块/过滤/CVE 精确通道/有界输出/stale）· `kb --selftest` |

### `wpfetch` —— 外部 WP → 本地 Markdown（喂给 `kb`）

> 落盘位置：`tools/knowledge/ctf-wp/<年>/<方向>/`（**2025 与 2026 的 WP 合并成一个仓库，按年分桶**；2026 还有 `review/` 放我们自己的研读笔记）

```bash
wpfetch --list tools/knowledge/ctf-wp/sources.json       # 按清单抓（幂等；--force 重抓；按年份自动分桶）
wpfetch --from-page <分类页/归档页>                        # 从列表页抽候选文章链接（很多国内博客没有可用 sitemap）
wpfetch --discover <站点>                                 # 走 sitemap.xml / RSS 捞候选
wpfetch --index-only / --selftest                         # 重写 INDEX.md / 离线自检
```

判据与纪律（写进脚本，不靠记性）：
- **日期红线** `--min-year 2025`：页面上识别到更早的发布日期 → **拒收**（识别不到就收，但头部写 `published: unknown`，不假装确定）
- 每篇落盘时带**来源 URL + 发布日 + 抓取日**头注释；`_fetch-log.jsonl` 记账；`INDEX.md` / `PROVENANCE.md` 自动/手工维护
- **只存本机离线查阅**（版权归原作者，不外发、不再分发）；`--sleep 0.3` 别打疼人家站点
- 抓不到的**如实记**在 `PROVENANCE.md`（已知 2 篇 JS 渲染页 + 1 篇日期红线拒收）

## 四·C、黑板与沉淀工具（`board/`）

> **先跑自检**：`selfcheck`（或 `selfcheck --quick` 跳过测试）。它把下面这些测试与软链/黑板/密钥检查
> 包成一条命令，失败项直接印修法，退出码 1 = 有硬失败。
>
| 入口 | 用途 |
|---|---|
| **`selfcheck`** | **★ 9 组自检**：技能软链 / 插件崩溃（含 lab-preflight · flag-watch 在不在）/ `fgs doctor` / 29 条回归测试 / git 干净度 / 密钥泄漏 / 工具链 / 已知坑守卫 / **CTF 题型体检**（py 弹药 + 六类工具链 + ida 桥） |
| **`bashfind`**（`python3 tools/bashfind.py`） | ★ **Python 里起 bash 的唯一入口**（零依赖）：解析出**实测能跑**的 bash 绝对路径，供 `lint_sh` / `bench_local` 共用。来历（2026-11-23）：`lint_sh` 把 **267 个**脚本全判语法错、`bench_local` 判分恒假红，而 shell 里手跑全绿 —— 根因是 Windows 的 CreateProcess 把 **System32 排在 PATH 之前**，本机 `C:/Windows/System32/bash.exe` 是**起不来的 WSL 存根**，裸名 `bash` 恒返回 rc=1 + UTF-16「拒绝访问。」。**探针口径**：必须 `-c` 真执行（`--version` 在坏 shim 上也能返回 0，用它探会把坏的当好的）；`is_access_denied()` 负责把「起不来」与「脚本语法错」分流（前者 skip，不许判红）。环境变量 `PI_LAB_BASH` 可覆盖。`board/tests/test_bashfind.py` 20 条断言（含「lint_sh/bench_local 不许改回裸名」的静态断言） |
| **`lint_sh`**（`python3 tools/lint_sh.py`） | ★ **shell 脚本体检（零依赖，补 ruff 管不到的那半边）**：① `bash -n` 语法 ② **`set -u` 下引用了全文件从未赋值的 `$NAME`**。来历：2026-11-18 重建镜像后容器无限重启，日志只有 `provision-agent.sh: line 163: ROOT: unbound variable` —— 脚本头 `set -euo pipefail`、补丁段引用 `$ROOT` 却从没定义过，旧镜像那份是更早版本所以藏在源码里几天没被发现。本机没有 shellcheck（pip 拉不动），故自写。判据故意宽（`${X:-}` 兜底 / 引号 heredoc / `for ((i=…))` / `\$` 字面量 / source 了别的文件 → 全豁免），只为不制造狼来了。实测全仓 140+ 个脚本 FAIL 0（数量随 tools/bin 增减浮动，别背死）；喂修复前的 provision-agent.sh 能精确指到 L163 $ROOT。`board/tests/test_lint_sh.py` 9 条断言钉住正反两侧，`selfcheck` 已挂闸门（有问题 = 硬失败） |
| **`skilllint`** | ★ **技能库体检**（`tools/skilllint.py`）：**断链**（SKILL.md + references 都扫；目标按四处基准解析：技能目录 / 其 `references/` / 仓库根 / `$KB`）· 入口 SKILL.md 超重（>9000 字节）· frontmatter 齐不齐 · 未被引用的 references。**改完技能跑一次**；`selfcheck` 已挂闸门（断链 = 硬失败）。判据故意保守（剥代码块、只要本地文件扩展名、跳过来源署名行）—— 假阳性会让我去修不存在的 bug |
| **`handover`** | **★ 打交接包**：把状态快照 / 架构 / 手法库 / 留档 / 黑板快照 + `SHA256SUMS.txt` 打到桌面；README 的数字全部现场抓取，人写的待办原样搬运 |
| `/selfcheck`（TUI 内） | 同上，但是扩展 `.pi/agent/extensions/lab-preflight.ts` 提供的；`/selfcheck full` 含回归测试。它还会在**每次会话启动**时查软链与崩溃记录，只在有问题时报警（软链断了否则零报错）。**`flag-watch.ts`** 是另一个扩展：每个工具输出都过一遍 flag 形态扫描，命中把警告插进结果并记 `log/flag-watch.jsonl` |
| `fgs` | 黑板：goal/step/fact/hint、`next`（含首灯身份检查）、`lead`、`publish`/`apply`/`outbox`、**`brief [--restart]`**（任务简报 / **pass@k 重启简报**：题面+已坐实+★已证伪路线+知识指针）、`cost`/`gate`/`verify` |
| `python3 board/tests/test_outbox.py` | 投递终态回归（48 条断言） |
| `python3 board/tests/test_lead.py` | 调度者身份回归（25 条断言） |
| `python3 board/tests/test_retrospect.py` | 手法库闸门回归（30 条断言） |
| `python3 board/tests/test_snapshot.py` | 快照语义哈希回归（14 条断言） |
| `python3 board/tests/test_cli_smoke.py` | **CLI 冒烟：31 个子命令全覆盖 + 集合相等哨兵（16 条断言）** |
| `python3 board/tests/test_locking.py` | 写锁：并发不丢更新 + AST 静态不变量（11 条断言） |
| `python3 board/tests/test_matrix_fuzz.py` | 端点矩阵 × 全组合 + 30 个畸形载荷 + 边界（444 条断言） |
| `python3 board/tests/test_selfcheck.py` | **工程底座回归：selfcheck 正面 + 反面（断软链必须变红）+ .gitignore 密钥不变量（31 条断言）** |
| `python3 board/tests/test_solved_gate.py` | **solved 门禁：孤点 Fact 不许授权 solved（退出码 3）+ doctor 能抓历史孤儿 + 连通是传递的（23 条断言）** |
| `python3 board/tests/test_lease.py` | **Step 租约 + reason 终止性：到期→reclaim、只能本人续租、回收留痕、空手不许再问一遍（34 条断言）** |
| `python3 board/tests/test_swarm.py` | **通杀：源必须 verified、派生边指回源、同文本幂等、默认 5/硬上限 20（27 条断言）** |
| `python3 board/tests/test_scope.py` | **授权范围守卫：子域/通配/CIDR 匹配、基础设施与 OOB 白名单、界外 fail-closed、越界留痕（27 条断言）** |
| `python3 board/tests/test_doctor_checks.py` | **判据对拉（防误诊 + 防漏诊）：每个门禁都测正例不误报 + 反例能抓到；含配置写错、无 goal、否定句误报等退化路径（38 条断言）** |
| `python3 board/tests/test_gate.py` | **逐字溯源 + 成本画像 + 统一门禁（33 条断言）** |
| `python3 board/tests/test_local_deny_tag.py` | **本机拒绝必须自报「非靶机」**：lane_gate 拒 / fgs gate 退 3 / verify 失败三处文案 + 任务书 0d 条（10 条断言） |
| `python3 board/tests/test_dirty_bytes.py` | **脏字节不丢整轮**：fact 正文含孤立代理项时 rc=0、图能 load 回、CJK 仍可 grep（8 条断言） |
| `python3 board/tests/test_relate_idempotent.py` | **连边幂等**：重复 relate 两次都 rc=0、第二次审计键 relate.dedup、自环/成环仍拒（13 条断言） |
| `python3 board/tests/test_knob_zero.py` | **哑旋钮**：PI_FGS_BUDGET_WARN=0 真关告警、写错≠关闭、PI_FGS_MAX_READ_BYTES=0 判配置写错并回退（13 条断言） |
| `python3 board/tests/test_handover.py` | 交接包回归：README 数字必须与 git/技能/测试现场值一致 + 待办原样 + 剔二进制 + 哈希回验（24 条断言） |
| `fgs snapshot <pid> --hash` | 只打印语义哈希 —— 判断图有没有变 |
| `fgs trace <pid>` | 工具留痕摘要流（每条一行 ≤100 字，含 `tNNNN`）—— 压缩后重建「我跑过什么」 |
| `fgs trace <pid> --q <关键词>` | 只回命中的留痕 + 命中处上下文（找 token / 报错） |
| `fgs trace <pid> --step t0001,t0002` | 取全文（一次最多 5 条，多的列 `omitted_step_ids`；**裁剪过，不是原文**） |
| `python3 board/retrospect.py scan` | 从黑板图抽**候选手法**（不直接写正本） |
| `python3 board/retrospect.py --list / --show <id>` | 看候选（含与正本的重合度） |
| `python3 board/retrospect.py --merge <id> --reviewer <人名>` | 人审通过后合并进 `docs/writeups/手法库.md` |
| `python3 board/retrospect.py --drop <id> --reviewer <人名> --reason "…"` | 弃用候选（弃用也是结论） |

> 手法库闸门是 **fail-closed**：与正本有任何可察觉重合就默认拦下，要写必须人显式 `--force-dup`。
> 审计流水：`docs/writeups/手法库.audit.jsonl`。

---

## 四·D、写锁与完整性自检（`board/fgs.py`）

| 入口 | 用途 |
|---|---|
| `fgs doctor <pid>` | **[只读] 完整性自检**：schema / 节点 id 前缀 / 悬空边 / 重复边 / 端点矩阵 / done step 有无 produced / **僵尸 claim（提醒）** / active goal 唯一 / solved 依据是否 verified **且与 goal 连通** / **SRC 复审与无害证据** / **授权范围（越界 target 判死）** / outbox 与台账可解析 / 残留锁与半成品文件。退出码 1 = 有问题 |
| `fgs snapshot <pid> --ref` | 快照写 `work/<pid>/_snapshot.txt`，**只打印路径**（prompt 里放引用不放正文；Cairn 的 write_graph_snapshot_reference） |
| `fgs swarm <pid> --from <fact> --targets a,b,c` | **通杀（一打一片）**：一条 verified 结论 → N 条并行 Step（带 `derived_from` 指回源）；`--dry-run` 预览、同文本幂等、默认上限 5/硬上限 20 |
| `fgs aid <pid>` | 列出 Worker 的「请求增援」（自然语言 → 调度清单，标待派/已派） |
| **`fgs verify <pid> <fact> --from <原始输出>`** | **逐字溯源盖章**：结论里声称的值必须 byte-exact 出现在真实输出里（否则退 3）；盖 `verified_by=verbatim` |
| **`fgs gate <pid>`** | **统一门禁**：一次过完 solved 依据 / 逐字盖章 / 授权范围 / outbox / 复审结论 → 给「能不能报」的唯一结论（退 3 = 不能报） |
| **`fgs step-add … --est N`** + `fgs doctor` | **入路成本预估 + 过度承诺机器检测**：`--est` 记预估步数；`claim→release` 自动累计 `attempts`（无产出尝试）。`doctor` 在 **attempts≥3 且未落 Fact** 时报「**枯竭，换攻击面**」并给出 `step-conclude` 命令
—— 打 arXiv:2602.17622 的 Type B 根因（缺实时难度预估→过度承诺低价值分支）；与「原地打转≥3」同阈值（BreachWeave Observer）|
| **`fgs coverage <pid>`** | **攻击面覆盖率矩阵**（目标×漏洞类型：已证/证伪/未测）**+ 下一步排序器**（按 `目标优先级.md` 权重 × 本项目该类历史产出 × 可达性）。**`gate` 已接入同一判据**（`_coverage_gaps` 同源）→ **未测完且未 solved = 不许收工**（AGENTS.md 第六·五 §8 的机器化）。`--json` 机器读、`--classes` 换类表 |
| `fgs cost <pid> [--json]` | 成本画像：步数 / 时长 / 坐实密度（verified÷done）/ 边际递减信号 |
| `fgs cost <pid> --usage <dir> --by-step [--json]` | 逐 Step 成本：按 step 绑的会话归集四分类 token/cost（归不上的不摊派；不知道报未知，不当 0） |
| `fgs scope <pid> --set "a.edu.cn,b.edu.cn"` | **声明授权范围**（支持 `*.x.com` / CIDR；`--add/--remove/--clear`）。声明后 `swarm` 与 `step-add --target` **fail-closed** 拦界外目标；github/pypi 等基础设施与 dnslog/ceye 等 OOB 天生放行 |
| `fgs step-heartbeat <pid> <sid> --by X` | 续租（长任务必须定期跑，否则 claim 过期被回收） |
| `fgs step-reclaim <pid> [--ttl N]` | 回收 claim 过期的僵尸 Step（不回收会把项目死锁在 wait） |
| `fgs snapshot <pid> --hash` | 语义哈希（判断图变没变） |

**写锁不变量**（踩过：14 处 `save()` 里只有 5 处套了锁，`fact-add`/`hint`/`step-add`/`relate`/`apply`
全是裸的读-改-写 —— 两条写命令撞在同一秒会**静默丢更新**）：

- 任何含 `save(` 的函数**必须**套 `with Lock(proj_dir(pid))`
- `Lock` 是**可重入**的（`init` 会在自己的锁里调 `_set_goal`，不可重入就自己锁死）
- `board/tests/test_locking.py` 用 AST 静态检查守这条不变量（新增写函数忘加锁 → 测试失败）

---

## 五、常用组合（抄作业）

```bash
# SRC 信息收集  ⚠ 本机没有 enscan（改用 subfinder/amass/assetfinder）
subfinder -d 目标.com -silent   # 资产（替代 enscan）
naabu -host 目标.com -top-ports 1000
httpx -l hosts.txt -title -tech-detect -o alive.txt
katana -u https://目标 -jc -o urls.txt
gau 目标.com | sort -u > hist.txt

# 目录爆破
dirsearch -u https://目标 -e php,html,js,bak
ffuf -u https://目标/FUZZ -w $PI_TOOLS/wordlists/dirsx/dicts/fuzzing-dirs-common.txt

# 漏洞  ⚠ 本机没有 dalfox / commix（XSS 用 xsstrike；命令注入手工构造或 poc 查骨架）
nuclei -l alive.txt -t http/ -o nuclei.txt
xsstrike -u "https://目标/?q=1"
# commix 的等价手工形态：在注入点试 ; id / $(id) / `id` / %0a id

# 源码泄露
githack https://目标/.git/
rip-git -u https://目标/.git/ -v

# 微信小程序（自研 tools/wxapkg.py：解包 + PC 端 V1MMWX 解密 + 17 类攻击面 + 小程序码）
py tools/wxapkg.py selftest                       # 5/5 合成包自测（无网络）
py tools/wxapkg.py decrypt __APP__.wxapkg --appid wx0123456789abcdef
py tools/wxapkg.py unpack  app.wxapkg -o ./src
py tools/wxapkg.py scan    ./src                  # 越权路由(xxxByUserId)/密钥/云env/身份证/OSS直链
py tools/wxapkg.py qr      qrcode.png             # 标准码直出明文；菊花码给取 appid/path 的路线
# 打法：skills/src-recon/references/小程序渗透.md

# 反序列化 / 密钥 静态检查（本机已装，实测可判）
py -m fickling --check-safety --print-results --json-output out.json suspect.pkl
#   → JSON severity = LIKELY_OVERTLY_MALICIOUS / SUSPICIOUS（机器可判）；拿第三方模型文件先扫再加载
py -m detect_secrets scan ./源码/          # 密钥扫描（本机没有 gitleaks/trufflehog）
#   ⚠ ysoserial 需 java —— WSL 没有 java，要在 Windows 侧跑（见 playbooks/5-注入.md）

# 竞态条件（单包攻击 / HTTP/2 同步）—— 订单/券/提现重复消费是 SRC 高价值类
#   本机已装 h2spacex（Scapy 底层，不需要 Burp 也能做单包攻击）
#   ⚠ API 有坑（已实测校正）：port_number 是位置参数；headers_string 是**字符串**；
#     create_single_packet_*_request_frames 返回 **2 元组** = (不含末字节的头部帧, 只含末字节的数据帧)
#   完整配方（照官方示例，别自己推）见 playbooks/3-业务逻辑.md
# 打法与判据：skills/src-recon/playbooks/（按品类）

# 外部知识基线（按需取，**不预存** —— 全量入库＝臃肿，且会过时）
bash tools/kb_fetch.sh list                      # 看有哪些源
bash tools/kb_fetch.sh get howtohunt | less      # 各类漏洞测试方法论 + test case
bash tools/kb_fetch.sh get bounty-reports        # ★ HackerOne 公开报告 = oracle 源头
bash tools/kb_fetch.sh get tob-skills            # ★ Trail of Bits 窄技能 + Trophy Case
bash tools/kb_fetch.sh save <名>                 # 落盘 tools/knowledge/external/

# GitHub / 境外采集（2026-10-03 实测：直连不通 ≠ 真的不通，走代理全通）
PX=http://127.0.0.1:7897
curl -s -x $PX -H "Accept: application/vnd.github+json" \
  "https://api.github.com/search/repositories?q=<urlencoded>&sort=stars&per_page=100"   # 匿名 60/h
curl -sL -x $PX "https://raw.githubusercontent.com/<owner>/<repo>/main/README.md"
curl -s -x $PX -o /dev/null -w '%{http_code}' "https://github.com/topics/llm-pentest"
# 采集脚手架（12 个定向查询 × 100 条 → 去重 TSV）：work/refs-github/collect.sh
# ⚠ 只有境外采集挂 -x；国内目标一律直连（quiet req 默认禁代理）

# 鉴权差分（拿到接口清单之后的主力：同接口 × 不同身份，确定性比对）python3 tools/authzdiff.py selftest                          # 本地 mock，10/10 判据 + 4 护栏
python3 tools/authzdiff.py run --endpoints eps.txt \
    --profile anon=none.json --profile low=a.json --profile high=b.json --qps 1
#   profile 五档：anon 无token / invalid 无效token / low 普通用户 / high admin / sessB 同权第二账号
#   判定：INNER_DENY 自证未授权(响应体说未登录却有数据) · ANON_OK 未授权 · WRITE_IDOR 写型越权
#        CROSS_IDENT 水平越权 · VERT_ESCALATE 垂直越权 · UNKNOWN 模糊/5xx(必须人工看) · ANON_DENIED 已防护
#   已内置借鉴：动态字段过滤(timestamp/traceId/nonce…先剔) · 三态 UNKNOWN · 身份字段重合判定
#   护栏：默认只 GET/POST（DELETE/PUT/PATCH 需 --allow-writes）· qps 默认 1 · 单响应 256KB

# EDU 公开情报索引（"挖哪个学校/哪个厂商"用数据说话；只读平台公开页，站内 1 rps）
python3 tools/edu_index.py fetch                     # 拉 EDUSRC /list/ 最新页 + /rank/company/ 全榜 → work/edu/
python3 tools/edu_index.py show                     # 等级分布（现在什么级别能被确认）+ 确认节奏
python3 tools/edu_index.py vendors --top 40         # 教育软件厂商榜（修复率高=产品确实有洞且被收录）+ 品牌词已抽好
python3 tools/edu_index.py selftest                 # 15/15 离线断言，不发请求

# WSL 侧探活/指纹（★ 替代跑不通的 Windows 侧 httpx.exe；默认 1 rps、只读、JSONL 落盘）
py tools/probe.py https://a.edu.cn https://b.edu.cn --tech
py tools/probe.py -l hosts.txt --qps 1 --out work/edu/alive.jsonl --tech
#   输出：状态码 / 大小 / 耗时 / 标题 / Server；--tech 额外给出命中技术栈 + poc 查询提示
#   为什么不用 httpx：实测 httpx.exe 报 no address found for host 且退出码 0（DNS 失败、无告警）

# pwn
py -c "from pwn import *; ..."      # 必须用 py，不是 python3
pwnkit reach nc1.ctfplus.cn 29989   # 解析(多 DNS 兜底)+TCP+读 banner；国内目标**别挂代理**
pwnkit info ./pwn                   # 架构/保护/关键符号 → 该走哪条路
pwnkit fmt probe --host H --port P --args 6-40   # format string：自动定 arg 偏移(buffer/libc/栈)
pwnkit fmt leak  --host H --port P --addr 0x404018 --arg 8 --libc libc.so.6 --symbol puts
pwnkit fmt write --addr 0x404018 --value 0x7f.. --arg 9 --bytes 4
#   ★ 地址一律放 buffer **末尾**——放开头会被地址里的 0x00 把 printf 截断（实测踩过）
#   ★ 泄漏用**固定尾标记**解析，别用 '|' split——泄漏字节里可能就有 0x7c（实测踩过）
```

# ★ 已知环境陷阱（2026-10-04 实测，两个子代理 + 主会话都踩到）
# 经本机 Clash（127.0.0.1:7897）访问**不可达或不存在**的目标 → 统一回 `502 / Content-Length: 0`
#   或 TLS `unexpected eof while reading`；拿**随机不存在的域名**试探，响应逐字节相同（NXDOMAIN 不可区分）。
#   后果：把 502/0B 当"存活"会虚报资产，当"反代后端挂了"会误判原因。
#   判据：**只有拿到非空响应体且与假路径 md5 不同，才算真存活**；502/0B 一律记"未判定"。
#   probe.py 会把这类结果标 `unreachable`（JSONL 字段）并在输出里提示；绕开代理用 `--proxy ''`。
# ★ 另两条同晚踩到的（probe.py 已内建修正）：
#   · **默认不跟随跳转**（`--follow` 才跟）—— 跟随会让状态码/大小/指纹全变成"跳转终点"的：
#     www.cslg.edu.cn 因此被打上「致远A8+正方教务」（标签来自它 302 到的 www.szut.edu.cn）；
#     debot/dzrgjy/exw 被当成"所有路径回同一个 59KB 页"（那是落到 CAS 登录页的尺寸）。
#   · **外链不算本体指纹** —— haystack 里先剔除 `href=/src=/action=` 属性值再匹配，
#     否则首页超链到 seeyon/jwglxt 就会误判产品。

# ★ 离线 PoC 库安全执行器（2026-10-04，治"别拿水洞糊弄我"）
#   本机 11290 条 PoC 里混着 RCE/上传/写文件类 —— 跑那些 = 真写目标系统（红线）。
#   pocrun 只执行"判定式（读）"类，把写/执行类**挑出来列清单不执行**（安全闸不可关）。
python3 tools/pocrun.py --targets work/edu/hosts.txt --poc-dir tools/ctfpoc/pocs --grep 泛微 --qps 2 \
    --out work/edu/poc-hits.jsonl --evidence work/edu/poc-ev.jsonl
python3 tools/pocrun.py --grep unauthorized --limit 40 --dry-run   # 只看"能跑哪些/拦下哪些"，不发请求
python3 tools/pocrun.py --selftest                                  # 12/12 离线断言（含"RCE PoC 必须被拦"）
#   判定式支持：response.status == N · response.body.bcontains(b'X') · headers.contains · && || ! 与括号
#   安全闸命中关键词（跳过并登记）：<% Runtime exec( ProcessBuilder CreateCaFile upload delete wget curl bash …

# ★ 多源被动子域枚举（2026-10-04，治"资产没搜全就下结论"）
#   单靠 hackertarget 每域只 30~50 个 host；实测 wayback(历史URL) 是最大金矿：
#   cslg 30→113、szut 0→76，且新 host 里就有 webvpn/pas/cjgl/idp-lib 这类真有系统的资产。
python3 tools/eduassets.py --domain a.edu.cn --domain b.edu.cn --max-pages 15 --wayback-limit 20000
python3 tools/eduassets.py --selftest                    # 10/10 离线断言（含"分源重跑不许覆盖已有 host"）
#   源：certspotter(CT日志,分页) · hackertarget · wayback(CDX) · urlscan · otx(限流自动跳过)
#   输出：work/edu/assets/<domain>.hosts.txt（并集去重）+ <domain>.sources.json（各源计数）
