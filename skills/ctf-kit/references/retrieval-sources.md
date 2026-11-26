# 检索来源正本（卡住时去哪儿查）

> **2026-11-10 落地（T5）**：来源清单移植自 OpenSecurity-2.0 的
> `.opencode/agents-rules/dynamic-by-agent-searcher-*.md`（5 份）。
> 原版按「agent 角色」各写一份，**内容大量重复**；这里按「题型」合成一份正本，
> 各 SKILL.md 只留指针 —— 与本仓 `common-discipline.md` 同一套做法（改一处不漏 N-1 处）。

## 0. 何时读这一篇

**不是"先读一遍再解题"**，是**卡住且本地已穷尽时才读**。判据（三条满足任一）：

1. 同一路线已失败 ≥2 次且没有新信息（换 payload 不算换路线）
2. 已经翻完本仓对应的 `references/`，仍然不知道下一步试什么
3. 需要**版本相关的已知漏洞**（产品+版本 → CVE / payload），本仓没收录

> ⚠ 本仓有离线知识库 `kb search`（容器内，≈2.49 万块）与 `poc`（离线 PoC 1.1 万条）。
> **先 `kb search` 再联网**：离线命中更快、不依赖网络。这一篇是离线也查不到时的补充。

## 1. 通用铁律（所有题型适用）

| 规则 | 为什么 |
|---|---|
| **查询必须带版本/型号** | `"Next.js 14 middleware header injection"` 远好于 `"middleware vuln"`；不带版本 = 命中一大堆无关结果 |
| **优先一手源**：官方文档 / RFC / 规范 > 厂商 advisory > 博客 > 问答站 | 二手转述经常抄错参数顺序与边界条件 |
| **CVE 查三处**：NVD（评分+引用）→ 厂商 advisory（受影响版本）→ Exploit-DB（可用 PoC） | 只看一处容易漏「哪个版本才受影响」 |
| **拿到 PoC 先读再跑** | 别人的 PoC 常带硬编码目标/破坏性操作；读一遍改成只读探测 |
| **copy 来的命令先 `--help`** | 版本差异会让参数静默失效（尤其 nmap/sqlmap/curl） |

## 2. 按题型的来源与查询式

### 2.1 Crypto（来源：OS `searcher-crypto-analysis.md`）

| 来源 | 地址 | 用途 |
|---|---|---|
| SageMath 文档 | `https://doc.sagemath.org/` | `IntegerLattices` / `EllipticCurve` / `PolynomialRing` / `matrix.kernel()` |
| CTF Wiki | `https://ctf-wiki.org/` | 中文最系统的 RSA/格/ECC/背包/离散对数 |
| CryptoHack | `https://cryptohack.org/` | 分类挑战：RSA/Lattices/ECC/Isogenies/DH |
| factordb | `https://factordb.com/` | 大 n 先查已分解结果 |
| Alpertron ECM | `https://www.alpertron.com.ar/ECM.HTM` | 在线 ECM/SIQS 分解中等规模 n |
| RsaCtfTool | `https://github.com/RsaCtfTool/RsaCtfTool` | 自动化 RSA 攻击套件（文档说明哪种攻击对应哪种特征） |
| ePrint (IACR) | `https://eprint.iacr.org/` | 密码学预印本，标准攻击失败时查 |
| CTF writeup 聚合 | `https://ctftime.org/writeups` | 按 tag(crypto) + 年份过滤 |

**必须读的经典论文**：Coppersmith 1996（一元模多项式小根）/ Howgrave-Graham 1997（二元）/ Wiener 1990（短私钥指数）/ Boneh-Durfee 1999（改进短指数）/ Nguyen-Stern 1997（格攻击）。

**查询式约定**（带攻击族名，比描述现象有效）：

```
"LLL lattice attack"  "Coppersmith small roots"  "HNP hidden number problem"
"Bleichenbacher padding oracle"  "RSA short d"  "RSA small e"  "RSA multi-prime"
"P-256 invalid curve attack"  "ECDLP Pollard rho"  "anomalous curve smart attack"
"AES-CBC padding oracle"  "DES weak key"  "RC4 bias"
"MD5 collision"  "length extension MD5"  "SHA1 chosen prefix"
"BSGS"  "Pohlig-Hellman"  "index calculus"  "nested CRT"
"orthogonal lattice attack"  "subset sum LLL"  "approximate GCD"
"LCG state recovery"  "Mersenne Twister untwist"
```

### 2.2 Binary / Pwn / Reverse（来源：OS `searcher-binary-analysis.md`）

| 来源 | 地址 | 用途 |
|---|---|---|
| NVD | `https://nvd.nist.gov/vuln/detail/<CVE-ID>` | CVE 详情：影响版本/CVSS/引用 |
| NVD REST API | `https://services.nvd.nist.gov/rest/json/cves/2.0?cveId=<CVE-ID>` | 机器可读，比 HTML 更适合脚本 |
| Exploit-DB | `https://www.exploit-db.com/exploits/<id>` | 可用 PoC |
| IDA/Hex-Rays 文档 | `https://docs.hex-rays.com/` | IDAPython API、类型库、处理器模块 |
| Ghidra 文档 | `https://ghidra.re/ghidra_docs/api/` | 备用反编译器、脚本 API |
| Detect-It-Easy | `https://github.com/horsicq/Detect-It-Easy` | 壳/保护器签名（UPX/Themida/VMProtect/Enigma） |
| YARA 文档 | `https://yara.readthedocs.io/` | 规则与签名引擎 |
| MITRE ATT&CK | `https://attack.mitre.org/` | 技术 ID（T-ID）对照 |
| CISA KEV | `https://www.cisa.gov/known-exploited-vulnerabilities-catalog` | 实战在利用的 CVE 列表 |

**查询式约定**：

```
始终带二进制格式/架构： "PE32+ x64"  "ELF ARM64"  "Mach-O"
带壳/编译器：         "UPX packed"  "Go binary"  "Rust binary"
IDA 特性问题：         "IDAPython <version>"  "Hex-Rays decompiler annotation"
未知函数：             "called from main"  "after stdio init"（带调用点上下文）
二进制特征：           "imports ws2_32"  "entropy > 7.0"  "section .rsrc RWX"
利用技术：             "ROP chain"  "heap spray"  "UAF"  "type confusion"
```

### 2.3 Web（来源：OS `searcher-web-analysis.md`）

| 来源 | 地址 | 用途 |
|---|---|---|
| HTTP RFC | `https://www.rfc-editor.org/` | RFC 9110（语义）/9112（HTTP1.1）/9113（HTTP2）/9114（HTTP3）/7234（缓存） |
| PortSwigger 缓存投毒 | `https://portswigger.net/web-security/web-cache-poisoning` | 缓存投毒/欺骗专题 |
| Spring Security | `https://spring.io/security` | Spring 系 advisory |
| Next.js advisories | `https://github.com/vercel/next.js/security/advisories` | Next.js 系 |
| npm advisories | `https://github.com/advisories?query=type%3Areviewed+ecosystem%3Anpm` | Node 生态 |
| Rails security | `https://github.com/rails/rails/security/advisories` | Rails 系 |
| Node.js WG | `https://github.com/nodejs/security-wg` | 平台本体 |
| Chrome Status | `https://chromestatus.com/` | 平台新特性与弃用 |
| MDN | `https://developer.mozilla.org/` | HTML/JS/Web API 行为与兼容性 |
| WHATWG HTML 规范 | `https://html.spec.whatwg.org/` | 解析/导航/历史行为规范源 |

**查询式约定**：

```
带框架+版本：  "Laravel 11"  "Next.js 14"  "Spring Boot 3.2"
payload 绕过： "XSS filter bypass"  "WAF bypass"  "CSP bypass"
缓存层：       "Cloudflare cache"  "Varnish cache"  "nginx fastcgi cache"
走私变体：     "CL.TE"  "TE.CL"  "TE.TE"  "H2.TE"  "H2.CL"  "H2.H2"
CRLF：         "CRLF injection"  "HTTP response splitting"
查新：         chrome <版本> new features / chromestatus <关键词>
机制向：       <行为关键词> site:developer.chrome.com  /  <机制关键词> MDN
```

### 2.4 Forensics / Misc / Malware（本仓补充，OS 无对应 searcher）

> OS 只给了 crypto / binary / web / ai-security / mobile 五个方向；
> **取证、杂项、恶意样本**这三类没有对应来源清单。下面是我方补充的，标注了哪条来自实战。

| 来源 | 地址 | 用途 |
|---|---|---|
| CyberChef | `https://gchq.github.io/CyberChef/` | 编码/加密/解析的**交互式验证**；不确定多层编码顺序时先在这试 |
| File Signature 表 | `https://en.wikipedia.org/wiki/List_of_file_signatures` | 文件头魔数对照（改后缀的题必查） |
| Volatility 文档 | `https://volatility3.readthedocs.io/` | 内存镜像插件参数（`windows.pslist` 等） |
| SIGMA / ATT&CK | `https://attack.mitre.org/` | 行为映射到技术 ID，写结论时用 |
| MalwareBazaar | `https://bazaar.abuse.ch/` | 样本按家族/哈希下载 |
| VirusTotal | `https://www.virustotal.com/` | 哈希查询、AV 检测、行为分析 |

**实战补充（本仓踩过）**：

- **多层编码**：不要凭猜顺序 —— 用 CyberChef 的 Magic 模式先跑一遍，再手工验证每一层
- **改后缀的题**：先 `xxd | head -2` 看魔数，**不要**直接按扩展名选工具
- **时间线题**：`tshark -r x.pcap -T fields -e frame.time` 比 Wireshark GUI 稳（可脚本化）

### 2.5 AI / LLM 安全（来源：OS `searcher-ai-security-analysis.md`）

| 来源 | 地址 | 用途 |
|---|---|---|
| OWASP LLM Top 10 | `https://genai.owasp.org/llm-top-10/` | LLM01-LLM10 权威分类 |
| Lakera research | `https://www.lakera.ai/research` | prompt injection / jailbreak 研究 |
| MITRE ATLAS | `https://atlas.mitre.org/` | ATT&CK for AI，case studies + mitigations |
| NVIDIA Garak | `https://github.com/NVIDIA/garak` | LLM 漏洞扫描器，probe 列表可当 attack pattern 索引 |
| Microsoft PyRIT | `https://github.com/microsoft/PyRIT` | 微软 AI 红队框架 |
| Protect AI / Huntr | `https://huntr.com` | AI/ML 框架真实漏洞库 |

**查询式约定**：

```
injection：  "indirect prompt injection"  "system prompt leak"  "template injection"
jailbreak：  "prefix injection"  "roleplay jailbreak"  "DAN-style"  "base64 encoded payload"
数据泄漏：   "training data extraction"  "system prompt disclosure"  "RAG context leak"
工具滥用：   "tool injection"  "MCP prompt injection"  "agent goal hijack"
供应链：     "malicious pickle"  "torch.load RCE"  "model serialization attack"
MCP 专项：   "MCP tool poisoning"  "MCP rug pull"  "confused deputy MCP"
评测基准：   "JailbreakBench"  "HarmBench"  "b3 benchmark"
```

## 3. 本机替代顺序（重要：先离线后联网）

```
1. kb search "<症状关键词>"        # 离线 ≈2.49 万块，最快
2. poc "<组件>" / poc --cve CVE-x  # 离线 PoC 1.1 万条
3. 本仓 skills/*/references/       # 按路线读那几篇
4. 本篇的来源表（联网）            # 上面都没有才走这里
```

> ⚠ GitHub 直连不通 → 源码走 `https://ghproxy.net/<github url>`（见 AGENTS.md §五）。

## 4. 来源清单的维护规则

| 规则 | 说明 |
|---|---|
| **只加被真实卡住过的** | 没卡过的题型不预先堆来源（防机械堆砌） |
| **加了就要能验证** | 每条来源要么有「查询式约定」示范，要么标注它在哪次实战里救过场 |
| **定期查死活** | 死链比没有更糟（会让人以为查了）；发现就删或换 |
