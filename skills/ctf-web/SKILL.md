---
name: ctf-web
description: Web 题打法：响应头/框架指纹 → 前端 JS 找隐藏接口与参数 → IDOR/未授权 → 注入（SQLi/SSTI/命令）→ 文件类（上传绕过/穿越/SSRF）→ 认证（JWT/会话）。payload 被拦时走绕过算子表。当题目给了 URL 或 HTTP 服务、或在做接口越权/注入时参考。解题时优先自行分析判断，本 skill 提供 web 打法参考，不限制跨题型思考（比如 web+逆向、web+取证结合的场景都欢迎）。
license: MIT
compatibility: ctf-agent（Windows 宿主 + WSL 执行；本 Skill 只读，命令/工具/网络可用性按题探测）
allowed-tools: Bash Read Write Edit Grep Glob
metadata:
  user-invocable: "false"
---

# Web 题

## 硬禁止（事前自查，先看这 6 条）

| 禁止 | 撞上了怎么办 |
| --- | --- |
| 同一条命令 / payload **失败 ≥2 次还重复** | 换**策略**（换编码/换参数/换大小写都算同一策略）；先回读题面与响应差异 |
| 只 `curl` 静态 HTML 就断言「无 XSS / 无渲染后 DOM」 | 用 `tools/browser.py`（eval/goto/shot）看渲染后的 DOM、存储与 console |
| 不读前端 JS 就断言「没有隐藏接口」 | 把 `/static/*.js` 拉下来 `grep -oE '/(api|v[0-9])/[a-z0-9_/-]+'`，再决定枚举面 |
| 用别的题的 flag 形状凑一个 | flag 必须**逐字**出现在工具输出里；`fgs verify <pid> <fact> --from <原始输出>` 才盖章 |
| 对目标做批量/破坏性操作（脱库、删库、批量拉真实数据） | 立刻停手：最小样本（1 条）取证即止，见 AGENTS.md §二 红线 |
| 报「环境不支持」前不补工具 | 先 `bash /opt/pi-ctf/tools/ensure-tool.sh --check`，再不行用纯标准库替代 |

## 完成定义（可打勾，缺一项别说「做完了」）

- [ ] 攻击面清单已落盘 `work/<pid>/`，每条附来源证据（响应头 / JS 片段 / 响应差异）
- [ ] 每个候选漏洞有**原始请求 + 原始响应**（逐字，能复算）
- [ ] flag 在工具输出里逐字出现过（可 `fgs verify` 盖章），并**已提交**
- [ ] 已证伪的路线写进黑板（`fgs fact-add` + `--confidence inferred`），下轮不再重试
- [ ] 依赖浏览器的结论（XSS / DOM）有真浏览器证据，不是 curl 推断
- [ ] `fgs solved` + **平台回执**（没回执只能说「待确认」，不许自评通过）


## 起手（按顺序，每步留证据）

> **第 0 步不要跳**：小应用上「先爆破」是产出最低的路 —— 攻击面是应用**自己的词汇表**（个位数），不是通用字典（万位数）。
> 先把页面产物存下来跑推演器，再决定要不要开字典：
>
> ```bash
> curl -s -o work/<pid>/index.html <url> && curl -s -o work/<pid>/style.css <url>/assets/style.css
> ctfsurf --dir work/<pid>          # 悬空类名/表单 action/题面动词 → P0·P1 攻击面清单 + 反爆破闸门
> ```
> 解读见 `references/frontend-residue.md`（含 msgboard 真实复盘：2.7 万条字典打一个 6 端点应用）。

1. `curl -i <url>` 看响应头：`Server` / `Set-Cookie` / `X-Powered-By` / 重定向 —— 先定框架
2. 读**前端 JS**：`/static/*.js`、`app.js`、webpack 产物里的 API 路径与隐藏参数
   （题面说"接口？这是什么"这类暗示时，答案基本都在 JS 里）
   → 一步到位：`apiharvest --dir work/<pid>` 出端点清单；四层打法（静态 → 运行时 hook → 参数逆向 → 门禁顺序）见 [references/api-recon.md](references/api-recon.md)
3. 接口面：改 ID 试**越权（IDOR）**、去掉/替换 token 试**未授权**、换 HTTP 方法试（GET 通了 POST 未必）
4. 注入面：`'` / `"` / `1 or 1=1` / `{{7*7}}` / `${7*7}` / `;id`；SQLi 上 `sqlmap -u "<url>" --batch`
5. 文件面：上传点试后缀与 Content-Type 绕过；`?file=../../etc/passwd`（穿越）、`?url=` 试 **SSRF**
6. 认证面：JWT 试 `alg:none`、弱密钥爆破（`hashcat -m 16500`）、`kid` 注入
7. ★ **XSS / DOM 类要用真浏览器验**（2026 证据：这类题失败的主因是**环境缺浏览器**，不是推理错 ——
   agent 看不到 JS 渲染后的 DOM）→ 用已配的 `playwright` MCP 开页、看 console、看渲染后的 HTML/存储。
   别只靠 curl 看静态 HTML 就断言「无 XSS」

拿到 shell / 任意文件读之后，flag 常在：`/flag`、`/flag.txt`、环境变量、数据库表、备份文件。

## 本类工具

> 细节见 [`references/工具索引.md`](references/工具索引.md)（侦察/利用工具与用法）。

## 被拦了怎么办

`references/filter-bypass.md` 是算子表（编码 / 协议 / 参数 / payload 变形 / 逻辑五层）。
**换通道之后必须证明后端真的处理了参数**（不能只看"没报 403"就当成绕过成功）。

## 坑

- **XSS/DOM 题只 curl 不看渲染** —— 静态 HTML 看不到 JS 跑完的 DOM；用 `playwright` MCP 真开页（2026 实测：这是环境缺陷类失败）
- **业务逻辑题只找技术漏洞**（注入/配置错误）—— 2026 实测这是 agent 成片失败的类别；先问「这个流程**本应该**怎么走、哪一步能绕」，
  `ctfsurf` 的悬空类名 + 题面业务动词就是这条路的入口
- **盲注/命令注入时 payload 越滚越大** —— payload 一长就吃满上下文 → 推理退化（2026 实测）；中间提取结果**立刻落 `work/<pid>/`**，
  上下文只留「已经取到第几段」的摘要
- **小应用先开字典**（2 万条打 6 个端点）—— 先 `ctfsurf` 抠词汇表，字典永远排最后（`references/frontend-residue.md`）
- **多组件题只盯单一组件**（网关+上游 / 中间件+页面 / 清洗器+消费者）—— 2026 难题的两条主轴就在这里：
  **① 状态残留**（池化缓冲区归还不清零 → 读回别人的数据）**② parser differential**（同一输入两处解析不同）
  → `references/state-and-parser-differential.md`（含 SCTF `Treasury Gateway` 1000pt 全场最高分拆解）
- **不先枚举「门」就猜 payload** —— 开局先写「这题声称有几道隔离/校验/边界」，再逐条问「它真的存在吗」
  （SekaiCTF 2026 四题同场验证：作者设计的那道门往往不是最短的那道）
- **悬空 CSS 类名被当噪声**：`preview/draft/admin-*/actions` 是「未渲染功能」的指纹，是 P0 线索不是排版垃圾
- 只测了一个方法/一个路径就说"不存在" —— 对照过 405/501 再下结论
- 越权只测了 ID+1，没测"删掉鉴权头"和"换成别人的 ID"
- 报错信息里出现路径/版本却没顺手去查该版本的已知 POC

> **Web3 / 链上题**（钱包签名、合约状态机、助记词）→ 先读 [../ctf-crypto/references/web3-and-chains.md](../ctf-crypto/references/web3-and-chains.md)

## 参考材料（按路线取）

> **`filter-bypass.md` 已扩到「七层」**：含 ★**转义/解码顺序**反模式、★**响应头二次拼装**
> 两个新增层。偏离点分布在五个层次，按**成本从低到高**逐层试。
>

> 详见 [`references/参考材料索引.md`](references/参考材料索引.md)。

## 纪律（所有题型共用，逐条照做）

> **正本：[`../ctf-kit/references/common-discipline.md`](../ctf-kit/references/common-discipline.md)**（能力检查 / 产物只写 cwd / 结论三级 / flag 逐字 / 卡住换面 / 机器兜底）。
> 本节不再抄写内容 —— 2026-11-09 实测 9 份副本里已有 1 份漂移，改一处漏 8 处。**读那一份就够了。**
