---
name: src-recon
description: SRC 漏洞挖掘与渗透测试工作流——先识别目标资产（响应头/指纹/路径/版本），再用本地离线 PoC 库（poc.py，含 11290 条 CVE/CNVD/指纹/判定式）匹配可用漏洞，最后按 PoC 的请求骨架与判定式验证。当任务是对授权目标做信息收集、指纹识别、漏洞挖掘、越权/注入测试、或拿到一个组件/版本想知道有没有现成 POC 时使用。不要用于 CTF 二进制/pwn/reverse/crypto 题，这些走专门的题型 skill。
license: MIT
compatibility: pi-lab（Windows 宿主 + WSL 执行；poc.py 用 Python3 跑，本地离线可用）
allowed-tools: Bash Read Write Edit Grep Glob
metadata:
  user-invocable: "true"
---

# SRC 漏洞挖掘 / 渗透测试

本 skill 把「目标识别 → 离线 PoC 匹配 → 验证」这条链串起来。核心是复用本地
PoC 库 **`poc.py`**（11290 条，含 CVE/CNVD、指纹、请求骨架、判定式、原文）。

## 唯一工具入口（务必记住）

```bash
POC=$PI_LAB_ROOT/tools/ctfpoc/poc.py
python3 $POC <关键词>            # 关键词查（产品名/厂商/PoC id/文件名）
python3 $POC --cve CVE-xxxx-xxxx # 按 CVE 查
python3 $POC --cnvd CNVD-xxxx    # 按 CNVD 查
python3 $POC --lib afrog         # 限定库：afrog/tscanplus/poc-bomber/mitan
python3 $POC --usable "<词>"     # 只看"能直接照抄"的（带 path 或 expression）
python3 $POC --show 3 "<词>"     # 打印前 3 条完整细节
python3 $POC --cat 0 "<词>"      # 读第 0 条 PoC 原文（含完整请求+判定式）
python3 $POC --stats             # 看库里有哪些 lib、多少条可用
```

> ⚠️ 中文查询词务必用引号包住，`--cat N` 里的 N 是「当前查询命中结果里的序号」。

> → **第二工具入口：离线知识基线（卡住时先查这里）** 的细节已移到 [离线知识基线.md](references/离线知识基线.md)（3936 字节）—— 需要时读它
## 本类工具
## 本类工具

> **完整索引见 `references/工具索引.md`**（侦察/利用/爆破/小程序/鉴权差分 各环节对应工具）。
> 工具名与用法**正本在 `tools/TOOLS.md`**（唯一事实来源）；本 SKILL 只讲流程与判据。

## 第一步：识别目标（先探明是什么，再查 PoC）

对目标 web/API 做基础侦察，把「这个组件叫什么、什么版本」定下来：

```bash
# 1. 响应头先定框架/中间件
curl -sI <url> | grep -iE 'server|x-powered-by|set-cookie|location'

# 2. 抓首页找指纹特征（版权、路径、js/css 特征）
curl -s <url> | head -50

# 3. 常见指纹关键词 → 直接转成 poc.py 的查询词
#    泛微→weaver  致远→seeyon  通达→tongda  用友→yonyou  nacos→nacos
#    log4j→log4j  spring→spring  shiro→shiro  weblogic→weblogic
```

**产出**：一条「目标 = 组件名 + 版本/指纹」的结论，然后进第二步。

## 第二步：查本地 PoC 库

把识别出的组件/版本/CVE 喂给 poc.py：

```bash
python3 $POC "<组件名>" --usable        # 优先看能直接照抄的
python3 $POC --cve CVE-2024-xxxx        # 已知 CVE 直接查
python3 $POC "<厂商别名>" --show 5      # 看多条细节
```

判断逻辑：
- 命中且 `可照抄`（带 `path` + `expression`）→ 直接进第三步验证；
- 命中但 `仅名单` → 用 `--cat N` 读原文，看有没有 path/请求细节；
- 无命中 → 换厂商英文名/别名再查，或 `--stats` 看库里有哪些库。

## 第三步：按 PoC 验证（关键纪律）

PoC 原文里 `request` 段给了 `method`/`path`/`body`，`expression` 给了判定式。验证原则：

1. **先读原文再动手**：`python3 $POC --cat N "<词>"`，把请求和判定式看全；
2. **用最小验证**：先发一个无害的探测请求（如 `sleep` 注入用短延时、文件读取读一个确定存在的文件），确认漏洞真实存在；
3. **记录证据**：每次验证都留命令 + 响应，别只记结论；
4. **不破坏目标**：只做验证，不做批量利用、不删数据、不搞持久化（授权范围内）。

```bash
# 举例：拿到一条 SQL 延时注入 PoC 后
python3 $POC --cat 0 "泛微"      # 读原文拿请求体与判定式
# 然后按原文的 request 构造 curl 验证，确认 response.latency 命中判定式
```

> → **卡在「不知道还能测什么」时：三条外部知识基线（按需取）** 的细节已移到 [深度打法与裁决.md](references/深度打法与裁决.md)（663 字节）—— 需要时读它
> → **目标硬化时的下一步：「绕过去」优先于「穿过去」** 的细节已移到 [深度打法与裁决.md](references/深度打法与裁决.md)（1690 字节）—— 需要时读它
> → **拿到接口清单之后：跑鉴权差分（别手搓 curl）** 的细节已移到 [深度打法与裁决.md](references/深度打法与裁决.md)（909 字节）—— 需要时读它
> → **越权测完还要测的两类（传统扫描器一律看不见，`Pattyboi101/LogicGate`）** 的细节已移到 [深度打法与裁决.md](references/深度打法与裁决.md)（716 字节）—— 需要时读它
## 第四步：收洞判定 + 独立复审（别跳过这步）

验证通过 **≠ 可以报**。SRC 的尺子是「**实际可利用 + 实锤危害**」，
判据全文在 **[`references/收洞判定.md`](references/收洞判定.md)** —— 出手前必读，重点：

- **三裁决**：`accepted`（实锤）／ `ignored`（垃圾/不收/已证伪）／ **`deepen`（线索真但没打穿 → 定向再挖一轮）**
- **敏感信息四类死规矩**：只认 身份证照片 / 人脸照 / 身份证号 / 密码哈希（姓名手机邮箱订单统计都不算）
- **公开接口识别**：未登录首页就在调、返回公开展示信息的，既不是泄露也不是未授权
- **未授权访问门槛**：突破鉴权后必须拿到/干成三选一（死规矩数据 / 可直接利用的凭证或 getshell / 未授权敏感写操作）
- **写删改必走「无害证据协议」**：自建哨兵增→改→删、未登录 401/403 对照、幂等回写原值；
  **禁止硬删真数据**，**写删 PoC 不许现场复放**

**复审要交给独立视角**：把 Finding 与证据交给一个 fresh-context 代理，
用 **[`references/src-reviewer.md`](references/src-reviewer.md)** 跑一遍，
它只回一个 JSON（`verdict` / `severity` / `reason` / `deepen_directive`）。

> 为什么不能自己审自己：挖洞的人天然有确认偏误（花了两小时，会倾向于相信打穿了）。
> 这跟 pi-lab 的 `solved → confirm`、`publish → apply` 是同一个道理。

> → **裁决怎么落回黑板（这就是它跟「写份报告」的区别）** 的细节已移到 [深度打法与裁决.md](references/深度打法与裁决.md)（875 字节）—— 需要时读它
> → **通杀：`fgs swarm`（一打一片）** 的细节已移到 [深度打法与裁决.md](references/深度打法与裁决.md)（689 字节）—— 需要时读它
> → **⚠ 被打回来/被封的判据（实战高频，别猜）** 的细节已移到 [深度打法与裁决.md](references/深度打法与裁决.md)（806 字节）—— 需要时读它
## 纪律（所有题型共用，逐条照做）

> **正本：[`../ctf-kit/references/common-discipline.md`](../ctf-kit/references/common-discipline.md)**（能力检查 / 产物只写 cwd / 结论三级 / flag 逐字 / 卡住换面 / 机器兜底）。
> 本节不再抄写内容 —— 2026-11-09 实测 9 份副本里已有 1 份漂移，改一处漏 8 处。**读那一份就够了。**

## 常用别名速查

| 中文/常见名 | 查询词 |
|---|---|
| 泛微 OA / E-Cology | weaver |
| 致远 OA | seeyon |
| 通达 OA | tongda |
| 用友 | yonyou |
| 帆软 | fanruan |
| Nacos | nacos |
| Log4j | log4j / --cve CVE-2021-44228 |
| Shiro | shiro |
| Spring | spring |
| WebLogic | weblogic |

> **本技能 references 索引**（其余按需读）：[edu-edusrc.md](references/edu-edusrc.md) · [edu-系统级攻击面.md](references/edu-系统级攻击面.md) · [借鉴来源-AutoHunter.md](references/借鉴来源-AutoHunter.md) · [对抗-别把IP打没.md](references/对抗-别把IP打没.md) · [小程序渗透.md](references/小程序渗透.md) · [目标优先级.md](references/目标优先级.md)
