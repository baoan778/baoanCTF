---
name: ctf-kit
description: 本机 CTF 工具入口 —— 命令在哪、poc 本地 POC 库怎么用、py（带 pwntools/z3/pycryptodome）怎么跑、大输出怎么省上下文、flag 真实性怎么判、卡住时的强制换路线顺序。当需要跑工具、判断 flag 能不能提交、或一条路线推不动时用。
---

# 本机 CTF 工具入口

> **完整清单以 `$PI_LAB_ROOT/tools/TOOLS.md` 为准。**
> `tools/bin` 已加进 PATH，下面的命令名**直接敲**就行。

## 速查

| 要干什么 | 用什么 | 典型调用 |
|---|---|---|
| 认出的组件/CVE 查现成 POC | `poc` | `poc "<组件>"`、`poc --cve CVE-2024-xxxx`、`poc --stats` |
| 跑 pwn / crypto / reverse 脚本 | `py` | `py exp.py`（= `tools/venv/bin/python`，带 pwntools/z3/Crypto/…） |
| 大输出怕烧上下文（**单文件 >3000 token 不许整读**） | 先切再看 | `head -50`、`wc -l`、`grep -c`、`sort \| uniq -c \| sort -rn \| head` |
| 哈希类型识别 | `hashid` | `hashid '<hash>'` |
| 目录/接口爆破 | `dirsearch` / `feroxbuster` / `ffuf` | 见 TOOLS.md 第一节 |
| HTTP 存活/指纹 | `httpx` / `nuclei` | `httpx -l hosts.txt -title -tech-detect` |
| 源码泄露 | `githack` / `rip-git` 等 | `githack https://目标/.git/` |
| 取证 | `binwalk` `foremost` `volatility` `tshark` `exiftool` `steghide` `zsteg` | 见 TOOLS.md |
| 字典 | `$PI_TOOLS/wordlists/` | 目录/弱口令/手机号 |

## 四条本地索引（都离线、零 token、不联网）

0. ★ **统一入口 `kb`**（2026-10-12 新增）—— 下面 1/2 两件 + `tools/knowledge/`（应急手册等）
   + `docs/writeups/手法库.md` **合成一个检索面**，一次搜完，结果带**文件:行号**指针：
   `kb search "堆溢出 tcache" --cat pwn --top 5` · `kb search "泛微 e-cology"` · `kb read <路径> --lines 120-180` · `kb stats`。
   **先搜再读、只读命中的那一两段**（别再整目录吞）。
1. **POC 索引** —— `poc "<组件>"`：11290 条 CVE/CNVD/指纹/请求骨架/判定式。
   `poc --cve CVE-xxxx-xxxx`、`poc --lib afrog`、`poc --usable "<词>"`、`poc --cat 0 "<词>"` 读原文；
   在 `kb` 里用 `kb search "<词>" --kind poc` 可直接搜到（原文仍走 `poc`）。
2. **技能参考材料** —— 各 skill 的 `references/*.md`，**按当前路线只读那几篇**，别全部预加载（用 `kb search ... --kind ref` 定位）。
3. **本机工具清单** —— `$PI_LAB_ROOT/tools/TOOLS.md`（命令表 + 系统命令 + Python 库 + 字典）。

## 卡住时的强制顺序（别跳过）

一条路线连续两轮没进展时，按顺序做：

1. 把失败路线的**特征词**（报错原文、参数名、产品名、可疑代码片段）拿去检索：
   `kb search "<特征词>"`（一次搜完参考/手法/PoC）；只要 PoC 就 `poc "<特征词>"`
2. 回到当前 skill 的 `references/`，按路线表打开对应那篇实战笔记
3. 查本机还有什么能用：`cat $PI_LAB_ROOT/tools/TOOLS.md`
4. 三条都空 → 换**攻击面**（换入口/换参数/换协议层），不是换 payload 细节

同一条死路**不要试第二次** —— 要么证明它不成立并写明，要么换路线。

## ★ 通用武器：报错即 oracle（跨题型 · 2026 反复出现）

**一句话**：系统的**边界行为**（报错 / 返回码 / 崩溃地址 / 计时 / 命名对象）往往就是可枚举的侧信道 ——
比找「正常输出」容易得多。

| 把什么当 oracle | 题 | 赛事 | 怎么用 |
|---|---|---|---|
| **链接器重定位溢出** | `Linkme` | KalmarCTF 2026 | 用 `reloc_overflow` 报错地址逐字符泄露 flag |
| **协议的“指令太长”** | `Waybird Machine` | DEF CON Quals 2026 | FTP `500 Command too long` 会**清空 buffer** → 免费的行分隔 |
| **进程退出码** | `Aros-25` | KalmarCTF 2026 | `write()` 坏了 → 改用 exit code 每次泄 1 字节 |
| **界面渲染差异** | `Bejeweled` | SekaiCTF 2026 | **胜利画面把 flag 渲染在第 13 行，超时画面第 13 行是空的** → 按模板过滤的 dumper 永远看不到 |
| **内核命名对象** | `ShadowGateApp` | 腾讯游戏安全 2026 | 命名事件 / 信号量 / TEB `LastError` / `SetHandleInformation` 回读 / `TickCount` 计时 —— **驱动不给任何反馈，全靠侧信道** |
| **正则/过滤器报错** | 多题 | — | 过滤器只挡部分输入 → 报错与不报错的分界就是判定逻辑 |

**操作化**：拿到题先问 —— **“这个系统在失败时会把什么告诉我？”**
（报错文案 / 状态码 / 耗时 / 连接断开时机 / 崩溃地址 / 日志行数）
→ 把其中可枚举的那一项当成**1 字节（或 1 bit）通道**。

**与其它武器的关系**：
- **parser differential**（两个组件解析不同）→ `skills/ctf-web/references/state-and-parser-differential.md`
- **把构建工具当 VM** → `skills/ctf-reverse/references/vm-host-abuse.md`

## 参考材料（按路线取）

> **按路线取**：只读你当前这条路线的那几篇（每篇都是几千字的实战笔记），不要全部预加载。

**被拦了 / Web 打法**
- [filter-bypass.md](references/filter-bypass.md) — 【我们的】偏离点分布在五个层次，按**成本从低到高**逐层试。
- [web-playbook.md](references/web-playbook.md) — 【我们的】原文反复强调：漏掉 `/challenge/` 是「明明解出来却没收尾」的头号原因。

**服务端反序列化（深度专题）**
- [btfly-server-side-deser.md](references/btfly-server-side-deser.md) — 核心注入类：SQLi / SSTI / SSRF / XXE / 命令注入

**现代密码（深度专题）**
- [btfly-modern-ciphers-3.md](references/btfly-modern-ciphers-3.md) — 自定义哈希求逆、CRC 爆破、带噪 RSA oracle

**通用手法**
- [shared-playbook.md](references/shared-playbook.md) — 【我们的】③哈希（碰撞/长度扩展/弱算法）④PRNG（LCG/截断输出）⑤**自定义代数结构**（置换/矩阵/多项式/CRC）。

## 纪律（所有题型共用，逐条照做）

> **正本：[`../ctf-kit/references/common-discipline.md`](../ctf-kit/references/common-discipline.md)**（能力检查 / 产物只写 cwd / 结论三级 / flag 逐字 / 卡住换面 / 机器兜底）。
> 本节不再抄写内容 —— 2026-11-09 实测 9 份副本里已有 1 份漂移，改一处漏 8 处。**读那一份就够了。**
