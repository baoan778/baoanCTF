---
name: awdp
description: AWDP（AWD Plus）线下攻防赛打法：Break（打穿拿攻击分）+ Fix（打补丁过平台 check 拿防御分），只有 Web/Pwn。当比赛说明写 AWDP / AWD Plus / 线下攻防 / 要求提交 patch（fix.tar.gz、update.tar.gz）时使用。
license: MIT
compatibility: 命令/工具可用性按题探测；本机工具以 tools/TOOLS.md 为准
---

# AWDP（AWD Plus）—— 先修后打

> 事实来源：本仓 `work/refs-2026/laneR4-渗透与AWDP实战.md` 第二节（含原文与 URL）。
> 它的两个环节是 **Break**（打穿）与 **Fix**（修好），**只有 Web 与 Pwn** 两类题。

## 0. 一句话判据

**防御分按轮次累计 → 修得越早，吃的轮次越多。所以「先修后打」，而且越快越好。**

原文：**「Fix 或者 Break 越早越好」**「前期 Break / Fix 速度越快越好」「@Err0r233 迅速修了一道 java，**拿了很多轮次的防守分**」。

## 1. 赛制与判分

| 项 | 内容 |
|---|---|
| **Break** | 自己的 payload 打通 → 提交 flag → **攻击分** |
| **Fix** | 让**主办方 payload** 打不通、且**过平台 check** → **防御分** |
| 计分 | 攻击分与防御分都在**积分轮次内**结算 → 早修 = 多轮次 |
| 题型 | **只有 Web 和 Pwn** |
| 交付物 | `fix.tar.gz` / `update.tar.gz` = **patch 后的文件** + **一个替换脚本**（有的平台要求 web patch **只能用 `sed`**） |
| 平台差异 | CISCN 西南复赛：ftp 传包 → 平台**自动解包 + 跑 `update.sh` + 自动重启服务**；有的平台要你在脚本里自己 kill/restart |

## 2. 动手顺序（照做，别自由发挥）

1. **全题通看一遍**（源码/附件/端口/描述）→ 标出"**能快速修的**"（这类分最稳）
2. **先修**：改 → 打包 → 上传 → **盯 check 结果**
3. **再打**：Break 拿攻击分（有白盒源码时，Break 往往比盲打快）
4. **每轮回看**：修得对不对、服务有没有挂

## 3. 两条铁律

1. **修复的前提是 check 不能挂**
   > 原文：「一个**被打穿但能跑**的服务，比一个**安全但宕机**的服务值钱得多。一切防御动作都要先问一句：这样改 checker 还能过吗？」
2. **patch 前备份原程序；patch 后盯 GameBox**
   > 原文：patch 时要**备份原来的漏洞程序**供队内分析；patch 完要**关注 GameBox 状态**，失误导致不可用要**及时挽救**。
   > 但**别轻易把漏洞程序换回去**：down 掉是全场平分，把漏洞程序放回去会被强队直接吃分。

## 4. patch 包怎么打

```sh
# pwn（通用）
#!/bin/sh
mv xxx /home/ctf/pwn && chmod 777 /home/ctf/pwn
```
```sh
# Node（远程 /app/app.js）
#!/bin/sh
cp ./app.js /app/app.js
ps -ef | grep app.js | grep -v grep | awk '{print $2}' | xargs kill -9
nohup node /app/app.js || tail -f /dev/null &
```
```sh
# Python / Flask（远程 /app/app.py）
#!/bin/sh
cat app.py > /app/app.py
ps -ef | grep app | grep -v grep | awk '{print $2}' | xargs kill -9
cd /app && nohup python3 app.py &
```
```sh
tar -zcvf fix.tar.gz *        # 文件与脚本放同一目录再打包
```

**踩坑（原文真事）**：有人上传 fix 包一直提示服务异常，公告才说**题目描述把 `patch.sh` 写成了 `pacth.sh`** → 一直修复失败。
→ **上传前核对平台要求的脚本名/文件名**（差一个字母 = 白打一轮）。

**二进制 patch**（Pwn）：IDA 三种方式 byte / word / assemble，**逐字节改最小、最不容易破坏逻辑**；改完先在本地跑通再用。

### 4.1 2026 的 check 到底有多严（一手实测，CISCN×长城杯半决赛）

> 原文：「今年的 pwn check **非常严格**。一些去年的**取巧修复**的思路全都不能用了。」
> ——「**上通防、nop free 函数都会报『服务异常』**……甚至把整数溢出漏洞前的**汇编 `JA` 修改为 `JG` 也会报『服务异常』」
> check 判据：「完全没修好就报『漏洞利用成功』，影响程序正常运行或者是**修复方式不满足平台要求**都会报『服务异常』」
> 交付物格式（同场）：`tar -czvf update.tar.gz pwn update.sh`，pwn 程序绝对路径 **`/home/ctf/pwn`**
> 来源：`work/refs-2026/laneR1-国内赛事WP.md` / `docs/参考-2026国内赛事与大佬经验.md`

→ **推论（要当纪律用）**：能过 check 的修复必须是「**语义上真堵住漏洞、又不改变程序对外行为**」的那一类；
"绕过式修复"（屏蔽调用、改无关分支、让程序早退）在 2026 的平台上**大概率被判服务异常**。
所以顺序是：**先看清漏洞的语义 → 做最小语义修复 → 本地跑通正常路径 → 再上传**。

## 5. 与 pi-lab 的接口

| 要干什么 | 用哪个 |
|---|---|
| 修复前的备份/最小改动/复验三件套 | `skills/ir-response/practice/winrm-修复模板.txt`（本仓已有，含回滚） |
| 修完的**自检**（服务在听、正常响应） | 目标上跑一条明确判据（如 `curl -sS -o /dev/null -w '%{http_code}'`、`ss -lntp`），**check 过 = 唯一的"修好了"** |
| 多题并行时先给每题一个 worker | `fgs plan`（广度优先） |
| 卡住换面 / 重试带上下文 | `fgs next` 的停滞提示、`fgs brief` |
| 少看提示（看提示可能扣分） | 赛制相关：TCH 那类"看漏洞提示扣 10%"只针对特定赛事，**AWDP 本身没有这条**，别混用 |

## 6. 一句话流程

```
通看全部题 → 先修"容易修的"（拿稳防御分）→ 再 Break（拿攻击分）→ 每轮复验服务活着
patch 前备份、patch 后盯 GameBox、脚本名逐字核对平台要求
```
