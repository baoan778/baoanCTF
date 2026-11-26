# 分工：CTF（pi-lab 终端）与 SRC/渗透/应急（dsh）

> 2026-10-05 操作者定：**CTF 归本仓（终端 pi）**，**SRC 挖洞 / 渗透测试 / 应急响应归 dsh**。
> 本文收拢 SRC/渗透/应急 的流程与判据——**本仓仍保留技能文件**（`skills/src-recon/`、`skills/ir-response/`），
> 只当**工具书**用（例如 CTF 撞到 web 题需要"收洞判定/无害证据"这类判据时翻一下），**默认不主动接这类任务**。
> 谁接到目标谁负责留痕；不要跨线。

---

## 一、SRC / 渗透测试的固定顺序（原文保留）

```
信息收集  →  指纹识别  →  匹配 PoC  →  验证  →  复审  →  取证留痕
```

1. **先信息收集，不许跳步**
   - 存活 / 端口 / 目录 / 接口 / 参数 / 前端 JS 里的 API 路径
   - 响应头、`Server`、`X-Powered-By`、`Set-Cookie`、报错页、favicon
   - 用 `httpx -l hosts.txt -title -tech-detect`、`nuclei`、`dirsearch` / `feroxbuster` / `ffuf`
   - 前端 JS（webpack 产物 / `app.js`）里的**隐藏接口与隐藏参数**往往是突破口
2. **认组件、认版本** → `cmseek`（CMS）、`wafw00f`（WAF）、`graphw00f`（GraphQL）、
   `hashid`（哈希），响应头/报错页/静态资源特征一起看
3. **一旦匹配到对应 PoC，直接调用跑一遍**，看能不能打通 —— 不要先手搓 payload 重造轮子
   - `poc "<组件>"`、`poc --cve CVE-xxxx-xxxx`、`poc --stats` —— 查本地 1.1 万条 PoC 索引
   - `pocbomber` —— POC 批量验证（照索引里的请求骨架直接打）
   - PoC 带了**请求骨架 + 判定式**，照它的骨架发请求、按判定式确认，别只看返回 200 就喊成功
4. **验证**：能复现、能拿到实际效果（数据 / 回显 / 权限）才算洞。**没有证据的"疑似"不算发现。**
5. **复审：报到人面前之前，换一双眼睛看**
   - 判据正本：`skills/src-recon/references/收洞判定.md`（实际可利用 + 实锤危害；
     敏感信息四类死规矩；公开接口识别；未授权访问收取门槛；三裁决 accepted/ignored/**deepen**）。
   - 交给独立视角：用 `skills/src-recon/references/src-reviewer.md` 派一个 fresh-context 代理，只回一个 JSON。
   - 写/删/改类必走**无害证据协议**（自建哨兵增改删闭环 / 未登录 401-403 对照 / 幂等回写原值）；
     **不硬删真数据、不现场复放写删 PoC**。
   - 多个目标时先排优先级：`skills/src-recon/references/目标优先级.md`（评分只决定先打谁，不代表放弃）。
6. **留痕**：命令、原始请求/响应、时间戳。结论必须能被别人按同样步骤复现。

**授权边界**：只打题面/授权范围给的目标。不碰判定源、不删文件、不改系统配置、不停服。
**★ 禁止脱库 / 删库 / 批量拉取真实数据** —— 这一条的优先级**高于任何「深入验证」的冲动**：
- 证明未授权/越权访问 → **取最小样本（1 条记录）即停**，不做分页遍历、不导出、不落盘成数据集；
- 发现能读全表/全库 → **立刻停手**，报告里写「可读全表（**未**遍历）」+ 单条样本，**不要真去遍历**；
- 写/删/改类只能走无害证法（自建哨兵 / 鉴权对照 / 幂等回写原值），**不许碰真数据**。
**声明范围**（`fgs scope <pid> --set "…"`）之后，`fgs swarm` 与 `step-add --target` 会自动拦下范围外目标
（github/pypi 这类基础设施、dnslog/ceye 这类反连 OOB 天生允许）。

---

## 二、应急响应（走 dsh；本仓工具书入口）

- 技能：`skills/ir-response/SKILL.md`（6 阶段 FSM + 9 类事件分流 + HITL 闸门）
- 报告工具：`skills/ir-response/scripts/ir-report.py`（IOC 合并 + csv/json/md 导出）、`loop-check.py`（打转检测）
- 数据来源：`tools/knowledge/noptrace/`（NOPTrace 手册）+ `tools/knowledge/INDEX.md`（36 个攻击面）

## 三、跨线时怎么协作

| 情形 | 做法 |
|---|---|
| 比赛里出现真实站点（非靶场） | **不做**；要外部能力就找 dsh/操作者，别自己扩面 |
| CTF web 题需要"什么算打穿" | 翻 `skills/src-recon/references/收洞判定.md`（判据可复用），但结论仍按 CTF 标准（拿 flag 为准） |
| 操作者把 SRC 任务派给本仓 | 按本文 §一 流程走，并把目标写进 `fgs scope`（越界拦截会自动生效） |
