---
name: fgs-orchestrate
description: 用黑板（FGS：Goal/Step/Fact）编排多轮解题——把 Cairn 的 bootstrap/reason/explore 三类任务落到 pi-lab。当任务是"一整个项目/多道题/要跑很多轮""卡住了需要有人读全局判断下一步""要把多个子代理的结论汇总成一条线"时使用。单步的小问题不要用，直接做。
license: MIT
compatibility: pi-lab（WSL + pi；黑板内核 = board/fgs.py，纯 python3 标准库）
allowed-tools: Bash Read Write Edit Grep Glob
metadata:
  user-invocable: "true"
---

# 黑板编排（FGS）

> 契约：`$PI_LAB_ROOT/board/schema.md`　内核：`$PI_LAB_ROOT/board/fgs.py`（`fgs` 已在 PATH）
> Prompt：`$PI_LAB_ROOT/prompts/fgs-*.md`
>
> 架构来自 **Cairn**（腾讯云黑客松智能渗透赛 610 队 AK）与 **CyberPenda**（同赛道，pi 原生落地）。

## 什么时候用

**用**：一整个项目 / 多道题 / 明显要跑很多轮 / 卡住了需要读全局 / 多个子代理的结论要汇总成一条线。

**不用**：单步的小问题（"这个 base64 是什么"）。直接做，起黑板是纯开销。

## 核心认知（先读，不然会白搭）

> 细节见 [`references/核心认知.md`](references/核心认知.md)（四节点/三任务/一条纪律的前置认知）。

## 四节点 / 三任务 / 一条纪律

> 详见 [`references/四节点三任务.md`](references/四节点三任务.md)。

## 起手式

```bash
PID=mytask
fgs init $PID --kind ctf --goal "拿到 flag 并提交成功"
fgs hint $PID "出题人提示：注意 argv[1]"      # 有提示就先注入
fgs next $PID                                  # 🚀 bootstrap
```

## 先确认「我是这个项目的调度者」（ADR 0034）

主会话 = Dispatcher，但**当值身份要有客观依据** —— 否则两个 pi 窗口对同一个 pid
各跑各的 `fgs next`，两边都自认调度者：同一个 step 被派两次、两批 outbox 交错。
这正是 ADR 0034 记的事故（孤儿会话自封第二个 orchestrator，末段 100 分钟零产出）。

```bash
fgs next $PID          # 先跑这个：内含首灯身份检查
# 无主 / 心跳过期 → 自动接管（单窗口零摩擦）
# 别人当值（心跳 ≤300s）→ exit 2 停手，不要 --force 硬上
fgs lead $PID          # 看/续期身份；--release 释放；--force 见过期锁再接管
```

**纪律**：看到 `⛔ 调度者是别人` 就**停下来问人** —— 是另一个窗口在干，还是那个窗口死了。
不要习惯性 `--force`，那等于把 ADR 0034 的坑重新挖开。

## 主循环

```bash
fgs next $PID          # 1. 问该干什么
fgs snapshot $PID      # 2. 取完整快照（拓扑完整，不许裁剪）
```

然后按 `next` 的结果派活，**三种任务用三种不同的 posture**：

### 派 `bootstrap` —— 自己上，别想，别规划

初始态**不要先建一堆 Step**。你对目标一无所知，规划出来的全是猜的，
还会挤掉真正该用来探索的时间。直接动手，让事实教你应该规划什么。

```bash
# 用 fgs-bootstrap.md 模板渲染出的 prompt 起一个子代理（或干脆自己干）
```

### 派 `explore` —— 一个 Worker 领一个 Step

**必须先 claim 再启动**（Cairn 的固定顺序），否则两个 Worker 会做同一件事：

```bash
fgs step-claim $PID s003 --by w1      # 退出码 2 = 被抢了 → 换别的 step，不要重试
```

一个 Step 一个 Worker，`fgs-explore.md` 模板渲染 prompt。

### 派 `reason` —— 默认**不派子代理**

`reason` 的价值在于"有人看着全局"。**主会话自己做**通常比派出去好 ——
你本来就持有完整上下文，派出去反而要先把它压缩一遍。

派出去只在一种情况：需要独立第三方视角（避免自己给自己背书）。

```bash
fgs checkpoint $PID       # reason 收尾时记水位（Cairn 的"新态势"去重）
```

## 子代理的唯一写入通道：Outbox

> 详见 [`references/outbox通道.md`](references/outbox通道.md)（publish 格式、主会话 apply 校验、整批拒绝规则）。

## 双阶段（超时不许白跑）

`bootstrap` 和 `explore` 都要**带 session 进入第二阶段**：

1. 第一阶段正常跑。超时或输出不合法 → **别丢**，用同一个 session 起收尾。
2. 第二阶段用 `fgs-*-conclude.md` 模板，明确要求：
   「**不要继续探索，只总结截至目前的结论**」，只产出一个 Fact。

理由：跑了十分钟被超时砍掉、一个字没留，是最没意义的浪费。
一条"我试到哪、看到什么"的 Fact 就算没打出漏洞，也改变了后续所有人的搜索空间。

## 收尾

```bash
fgs show $PID                    # 人读视图
fgs log $PID --tail 20           # 审计流水
```

宣告完成前**必查**：目标 Fact 是不是 `verified`、能不能指出它出自哪条命令的原始输出。

```bash
fgs solved $PID --fact f005 --desc "flag{...}，来源：nc 交互回显，已提交平台返回 CORRECT"
```

## 卡住时的强制顺序

0. ★ **先问一句：是不是该换一份干净上下文重来？**
   2026 在 XBOW（30 道真实 Web）上的实测：同样一套 agent 多跑，**best-case 都是 19/30**，
   但「三次都解出」从 12 → 14 → 16；**多跑买的是「稳定」，不是「更强」**（arXiv 2605.21497）。
   另：单体 agent 失败时会 "scholar-like enumeration"（追貌似合理其实无关的路、步数最多），
   有规划的会在 ~40 步主动放弃 —— **会止损比会蛮干省时间**。
   ```bash
   fgs brief $PID --restart     # 题面 + 已坐实 + ★已证伪路线 + 知识指针 + 重启纪律
   # 输出整段当 context:"fresh" 子代理的 task —— 不带任何失败过程
   ```
   它**不依赖 open step**（卡住时正是没 step 可认领）。≥3 条证伪时会提示先比 `fgs cost`。
   来源与取舍（含**明确不采纳**：加厚架构 / 逐行 Evaluator / 靠多跑当能力提升）：`docs/参考-外部CTF-agent-可借鉴.md`
0b. ★ **同一题要更快？竞速不同模型**（2026 证据里唯一被「赢比赛」直接验证的加速）：
   BSidesSF **2026** 冠军 52/52，架构 = 「一题同时跑 5 个模型，先拿到 flag 的赢」。
   本仓用 `subagent({model:…})` 并发 2~3 路不同模型即可；先出结果的赢、其余停。
   ★ 但 **识别 > 执行**：2026 实测 Planner 识别对 23/30、其中只 4 题败在执行 → **力气压在识别上**。
1. `fgs show $PID` —— 图上已经有"已证伪"的 Fact 了吗？别重试。
2. `poc "<特征词>"` —— 失败路线的特征词（报错原文 / 参数名 / 产品名）拿去查现成 POC。
3. 读当前题型 skill 的 `references/` 对应那篇。
4. 三条都空 → **换攻击面**（换入口 / 换参数 / 换协议层），不是换 payload 细节。
5. 还不动 → **`fgs brief $PID --restart` + 重开一份干净上下文**（回到第 0 步）

## 反模式（踩过）

| 反模式 | 为什么错 |
|---|---|
| 初始态就建 10 个 Step | 全是猜的，还占掉探索时间。Cairn 用 `bootstrap` 就是治这个 |
| 同一 Step 派两个 Worker | 重复劳动。claim 返回 2 就是这件事在提醒你 |
| 让 Worker 直接写 `graph.json` | 一次幻觉污染全局，后面所有人都建立在错的基础上 |
| 把已证伪的路线结论丢在对话里 | 没落成 Fact = 没发生。下一个人会再试一遍 |
| 用 `inferred` 的 Fact 宣告 solved | 门禁会拒。要么坐实证据升到 `verified`，要么如实说没打出来 |
| `apply` 失败就 `--force-partial` | 你在往图里放不合法的东西，后面会一直还债 |
| `reason` 每轮都跑 | 会自激（reason 建的 step 又触发 reason）。只有**新态势**才重触发 |
