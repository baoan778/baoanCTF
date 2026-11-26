# Playbook 抽取（二）· crypto / pwn / reverse / forensics / misc / cloud

> 来源：`inwpu/hxbai` 的 `hxbai/playbooks.py`（MIT License）。压缩重排，只留可执行判据。
> web 部分见本 skill 的 `references/`（`filter-bypass.md`、`web-playbook.md`）。

---

## B. Crypto

### B0. 铁律
- **先读全源码/输出，把方案的代数结构精确画出来再谈攻击** —— crypto 输在"没读全就开猜"。
- 分类决策树：①RSA（n 可分解？e 小？部分位/φ/d 泄露？）②对称（有 oracle？CBC 结构？MAC？）
  ③哈希（碰撞/长度扩展/弱算法）④PRNG（LCG/截断输出）⑤**自定义代数结构**（置换/矩阵/多项式/CRC）。
- 工具（**全在 `py` 里，直接 `import`**；系统 `python3` 只有 sympy/capstone/fpylll，**没有** pwn/z3/Crypto/gmpy2/unicorn/ecdsa）：
  pycryptodome（注意 PKCS1_OAEP 的 hash 算法要一致）、gmpy2、sympy、z3、fpylll（格）。
  **SageMath 本机已装**（`/opt/SageMath`，命令 `sage`）—— 大格 / Coppersmith / 数域筛这类直接写 `.sage` 跑，
  别硬用 `py` 凑（小规模才用 `py` 的 gmpy2 + fpylll）。

### B1. RSA 五分钟快检
factordb 试 n → e=3/5 无填充直接开根 → 共模攻击 → e 大试 Wiener → 部分位泄露走 branch-prune（MSB 起逐位 + 进位约束剪枝）→ φ 全泄露直接 `d = e^{-1} mod φ`。

### B2. 几个"应该想到"的转化
- **CRC 是 GF(2) 线性映射**：碰撞构造、已知 CRC 求原像、带噪声校验恢复 = 解线性方程组（**高斯消元**），别暴力 2^32。
- **非标准群上的 DLP**：置换群先循环分解（阶 = 各环长 lcm）→ 每环小 DLP 暴力 → CRT 合并；小阶群走 Pohlig-Hellman 逐素因子降阶。
- **格方法可行性判据**：未知位总熵 ≪ 模长；LCG 连续完整输出 ≥3 个直接解线性方程组恢复 a,c。
- **对称 oracle 三连**：Padding Oracle（解密错误可区分 → 逐字节解密任意密文）／CBC 比特翻转（改 IV/前块改明文，打加密 cookie）／CBC-MAC 无填充变体伪造。
- **解密 oracle 二分**：服务端只回"能否解密/形状对不对"时，构造密文让明文落入单调区间，逐位二分。
- **自定义分组密码包 CTR/流模式**：**先发两条明文测 keystream 是否与明文无关**（同 nonce 下 `ct1^ct2 == pt1^pt2` 即无关）——是则 chosen-plaintext 直接恢复 keystream 抄 flag，**不要去逆 S-box**；nonce 随机就找解密 oracle 或 nonce 重用窗口。
- 自定义结构题：**先找那个"线性/低熵"的自由度**，把问题化成方程组或小空间搜索 —— 出题人一定留了一个可解杠杆。
- 交互全走 pwntools `remote` + 每步显式 timeout；**先用小参数本地自验 solver，再打线上 oracle**。
- 验证：恢复的密钥本地重放全流程一致；**flag 要可读格式，不拿十六进制串交差**。

---

## C. Pwn / Binary

### C1. 交互纪律（吃过亏的）
- **网络服务一律 pwntools `remote(host, port)`，绝不用裸 `nc`** —— 阻塞读会挂到命令超时，白丢一轮。
- **每个 recv 都带 timeout**（`recvuntil(..., timeout=5)`）；**`recvall()`/`recv()` 不带 timeout 的一律不写**。
- 每步打印收到的东西，看清服务端真实提示再发下一个输入。
- telnet（:23 / IAC 协商）服务用能自动应答 IAC 的工具，手搓 socket 会卡在协商上。

### C2. 流程（不许跳）
```
file / checksec / strings / nm  →  分类 bug 或找到"校验点"
→ 用 gdb 实测精确偏移（cyclic → $rip）或在校验断点读期望值（不要盲猜 ROP 地址）
→ 本地对二进制验证逻辑 → 打远程 host:port → 服务端打出 flag
```

### C3. 三个高频形态
- **内存泄露型（Heartbleed 式）**：服务返回长度由**你指定的长度字段**决定时，**请求比你实际发送更多的字节** → 泄露相邻堆/栈内存。长度递增扫描、dump 出来的字节 grep `flag{`/指针/canary。
- **独立二进制卡在 authcode/密码（固件/MCU）**：不必完整逆向 —— `gdb` 在校验/比较处下断点**读期望值**，或单步解出解码后的秘密。
- **自定义字节码 VM keygen**（小解释器 + 内嵌 blob，常先打 N 个点再 "complete" 的**诱饵输出**）：
  **内嵌 blob 长度 == flag 长度时（如 31 字节对 31 字符），那个 blob 就是变换前的 flag**。
  不要完美模拟 VM —— 提 blob + 各 handler 用的可逆逐字节操作（XOR 掩码、按字节码 ROTL/ROTR、ADD/SUB 常量），
  再用已知明文（`FLAG{` 前缀、`}` 后缀）在**小的操作顺序空间**里做 meet-in-the-middle，解出整条解码管线并验证。
  `gdb` 跟一遍 putc 循环（index 0..N-1）可确认字节数与"逐字符构造"。
- 若二进制是纯 validator/checker 且**无内存破坏**（只是接受/推导输入、flag 编码在里面）→ **转 Reverse 路线**，别硬爆、别 over-invest angr。

---

## D. Reverse

### D1. 制胜路线：validator/checker/keygen 假设 `flag = ciphertext XOR keystream`
- 密文是 `.data`/`.rodata` 里的**固定块**，keystream 由程序自己的变换产生（轮密码 / FSM / 小字节码 VM）。
- **你的活是用约 30 行 Python 重实现 keystream 生成器，然后 XOR 回去**。
- **运行时可能根本没有"秘密"** —— 输入/"license" 数据常是 VM 丢弃的诱饵。
- **已知明文就是预言机**：`keystream[i] = cipher[i] ^ prefix[i]`（`FLAG{`/`flag{`）→ 用来 (a) 验证重实现 (b) 给内部状态/密钥做有界 DFS 播种。
- **VM/FSM 的 keystream 常常是机器自身的状态轨迹**（逐步演化的寄存器/计数器值），不是对输入做的算术 —— 重放机器、抽取每步内部状态、与密文块 XOR。

### D2. 工具取舍
- **优先重实现，别上重武器**：angr/z3 在有状态、变长输入循环 + 提前退出的终点上会**路径爆炸**，烧预算。
- unicorn/qiling 只用来**验证**某个重实现函数在固定输入上的行为；gdb 只用来**确认**寄存器/状态值，不做主解。
- 一次 `./binary <候选>` 就能确认"被接受"。

### D3. 从常量认密码（省一整个 session）
| 常量/形态 | 是什么 |
|---|---|
| `0x9e3779b9` | TEA/XTEA（32 轮；解密时 sum 初值 `0xC6EF3720`，每轮 `sum -= delta`） |
| 重复的单字节 | XOR 密码 |
| `0x811c9dc5` / `0x01000193` | FNV |
| `5381` | djb2 |
| 256 字节 S 盒交换循环 | RC4 KSA/PRGA |

**密钥可能来自另一个工件**（固件镜像/提取的 blob），不是硬编码 —— 先 carve（`binwalk -e`、`7z x`、`unsquashfs`、`foremost`）再从提取出的文件里取密钥字节。

### D4. 其它
- 先 `file`/`strings -t x`/`xxd` 三角；有壳先 `upx -d`。**这类题通常很小，读全部反汇编 + 常量块（`objdump -s -j .rodata -j .data`），别略读。**
- **Go 二进制**（`file` 见 Go build ID / `.gopclntab` / `strings` 满 `runtime.*`）：strip 后 Ghidra 只有 `sub_xxx` —— 先用 **GoReSym** 恢复函数名/类型再逆；Go 字符串是"指针+长度"、**无 null 结尾**（按 C 串读会连成一坨）；别陷进 runtime/morestack/gc 噪声，只盯 `main.*`。

---

## E. Forensics

- 先定类型（pcap / 磁盘镜像 / 内存 dump / 文档 / 图片）→ 选对工具（tshark、volatility、binwalk、foremost、exiftool、steghide/zsteg、stegsolve）。
- **网络取证多步链**：`tshark -r x -z conv,tcp` 找可疑会话 → `--export-objects http,dir` 抽对象 →
  外带数据常是自研加密而**密钥不在流量里** → 从 .NET 样本抠 AES/RC4 的 key/IV/模式 → 照抄逆运算解密。
  （本机**没有** `ilspycmd` / `dotnet` / `monodis`：先在 Windows 侧用 ILSpy / dnSpy 反编译，
  或 `strings -el sample.exe`（`-el` 抓 UTF-16LE，.NET 字符串就是这个编码）+ `py` 手动定位 key 常量。）
- **编码与加密的先后要反着来**：样本"先加密后 base64" ⇒ 解时"**先 base64 后解密**"。
- **内存取证：本机 `vol`（Volatility 3）与 `volatility`（Volatility 2.6）并存** → 先看 `ctf-forensics/references/vol2-vol3.md`：
  **默认 `vol -f x.raw windows.pslist`**（v3，免 profile，插件名带 `windows.` 前缀）；
  离线取不到符号 / 要 `mftparser`（雕已删文件）、`shellbags`、`apihooks`、`clipboard` 才退 v2：
  `volatility --info` 挑 profile → `volatility -f x.raw --profile=<Profile> pslist` / `pstree` / `cmdline` /
  `filescan` / `dumpfiles` / `hashdump`。
  ⚠️ `volatility --help` 在本机会因 locale 报 `UnicodeDecodeError` —— **那不是镜像坏了，是本机 locale 的锅**；
  要看用法用 `--info`，别用 `--help`。⚠️ 本机**没有 `vol3` 这条命令**（上游文档的 `vol3 -f ...` 要改成 `vol -f ...`）。
- 讲故事：抽文件 → carve 数据 → 解编码 → 重建会话。flag 藏在包载荷、被删文件、元数据或隐写通道里。

---

## F. Misc

- **字面读题**，先识别"梗"：编码链、esolang、jail（py/bash）逃逸、QR/音频谜题、逻辑/约束题。
- jail：枚举被禁了什么，找**未被禁的原语**去读 flag 或执行。
- 系统性解码（base64/hex/rot/xor），空间小就脚本暴破。

---

## G. Cloud

- 枚举云元数据/凭据（IMDS `169.254.169.254`）、配置错误的 bucket、过宽 IAM、暴露的服务/密钥。
- **SSRF → IMDS 盗凭据（云题头号路线）**：任何会取你给的 URL 的端点，走
  `/latest/meta-data/iam/security-credentials/` → `<role>` → 临时凭据；再看 `/latest/user-data/`、`/latest/dynamic/instance-identity/document`。
  **`169.254.169.254` 被过滤时的绕过**：同一地址的**十进制/十六进制/八进制**写法、补零、IPv6 映射 `[::ffff:169.254.169.254]`、
  从应用的 `/etc/hosts`/配置里发现的内部 DNS 别名、可链的开放重定向。拿到后 `aws sts get-caller-identity` / 读 bucket / 读 secret。
- **服务端反序列化 RCE 的格式识别**：`gASV`=python pickle、`rO0AB`=java、`TAR((`=ruby、`O:8:`=php
  → 构造 gadget（python 用 `__reduce__` 返回 `(os.system,(cmd,))`；java 用 ysoserial，本机盘上有 `ysoserial-all.jar`）→ 读 `/flag`。
- 产品一发（先指纹再直取）：Gradio `GET /file=/etc/passwd`；Langflow `POST /api/v1/validate/code`；
  gremlin `POST /gremlin` groovy 反射 RCE；redis 未授权 `CONFIG SET`/模块加载；telnet 选项协商 CVE；
  OAuth2 device-code（`/devicecode`→`/token`）签 token；**JDWP `:5005`（常与 :8080 应用成对）——未授权 RCE**，
  `jdb -attach <host>:5005` + 在会加载的类上下断点 → `print java.lang.Runtime.getRuntime().exec("...")`；
  ComfyUI `:8188` 自定义节点管理拉取你给的 git 仓库执行 `install.py`；
  Apache OFBiz `/webtools/control/ProgramExport` 未授权 RCE；
  Dify `:3000`（Next.js Server Actions → React2Shell CVE-2025-55182，走 `Next-Action` 头 + Flight payload → `child_process.execSync`，stdout 经 `x-action-redirect` 外带）。
- **源码/调试泄露清单**（拿到源码，洞通常一目了然）：`.git`/`.svn`/备份（`.bak`/`~`/`.swp`）；
  `/src`、`/source`、`/download?file=app.py`、`?debug=1` + 栈回溯；框架调试台（Werkzeug `/console`、Flask debug PIN、
  Spring `/actuator/*` 的 `/env`、`/heapdump`、`/mappings`）；`/swagger`、`/openapi.json`；暴露的 `.env`/配置。
- **JWT key confusion**：`kid` 路径注入（指向可读静态资源，用其字节做 HMAC 签名）、`alg:none`、HS/RS 混淆（用公钥当 HS256 密钥签 RS256 token）。
