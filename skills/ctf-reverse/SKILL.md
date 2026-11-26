---
name: ctf-reverse
description: Reverse 题打法：file/strings 摸形状 → 静态看主逻辑（IDA/Ghidra 或 objdump+r2）→ 找校验循环/异或常量/base64 变表/S-box → 动态兜底（gdb 断比较点）→ 写脚本复算并确认。当附件是二进制/字节码/自研 VM（含 WASM / .NET / EVM 字节码）且要逆出算法或授权码时参考。解题时优先自行分析判断，本 skill 提供逆向打法参考，不限制跨题型思考。
license: MIT
compatibility: ctf-agent（Windows 宿主 + WSL 执行；本 Skill 只读，命令/工具/网络可用性按题探测）
allowed-tools: Bash Read Write Edit Grep Glob
metadata:
  user-invocable: "false"
---

# Reverse 题

## 起手

1. `file`、`strings -n 6`（先找 flag 格式串、可疑常量、错误提示）、`rabin2 -I`、`py -c "from pwn import *; print(ELF('./bin').checksec())"`
2. 静态看主逻辑：IDA / Ghidra 打开；命令行不够就
   `objdump -d -M intel` 配合 `r2 -A -c 'afl'`、`r2 -A -c 'pdf @ main'`
3. 常见结构（先按这几种去套）：
   - 输入校验循环（逐字符比较）
   - 异或 / 加减常量（看立即数）
   - base64 **变表**（表里字符顺序被改过）
   - S-box / 置换表 / 自研 VM 的 opcode 分派
4. 动态兜底：`gdb` 断 `strcmp`/`memcmp`/`ptrace`（本机**没有** `ltrace`/`strace`，看库调用/系统调用改用 gdb 断点或 `r2 -A` 静态补）
5. **写脚本复算一遍**（z3 / 手工逆运算 / 直接用 python 重实现），确认结果再提交

## 按要求形态先路由（★ 2026-10-12）

先看**交付物**再选命令：**`.wasm`/`.wat` → `revkit wasm`｜EVM → `revkit evm`｜`.dll`/`.exe`(.NET) → `revkit dotnet`**。
边界与退路（core dump/`.asar`/原生）→ **[artifact-routing.md](references/artifact-routing.md)**；自检 `revkit --selftest`。
> 为何：本机这些工具原本全无 —— 不先路由就会“拿到 `.wasm` 去 `file` 一下然后卡住”。

## 本类工具

> 工具以 $PI_LAB_ROOT/tools/TOOLS.md 为准；tools/bin 已在 PATH 里，命令名直接用。

```
revkit wasm  ./chal.wasm --info --decompile --run check --args 1,2   # WASM 四件套（--run 用 wasmtime 真跑）
revkit evm   0x60003560e01c8063a9059cbb1461001c --disasm --push4      # EVM 反汇编 + selector 候选
revkit dotnet ./App.dll --types --il 'App.Program::Check'            # .NET 元数据 + IL（纯静态）
revkit --selftest                                                    # 三块能力自检
poc "<组件>"                    # 已知壳/已知加密器 → 查本地 POC 索引
poc --cve CVE-xxxx-xxxx         # 按 CVE 查本地 POC
py -c "from z3 import *; ..."   # 要 z3/pwn/capstone/unicorn/ropgadget 的脚本一律用 py
pycdc target.pyc                # .pyc 反编译
pycdas target.pyc               # .pyc 反汇编
pyinstxtractor target.exe       # PyInstaller 打包程序解包
binwalk -e target               # 拆嵌套/嵌包
```
系统命令已装：`objdump readelf strings r2(radare2) gdb file binwalk`。
Python 库已装：系统 `python3` 有 `requests bs4 lxml yaml sympy numpy scipy PIL capstone`；
`py`（= `tools/venv/bin/python`）另有 `pwn(pwntools) z3 Crypto(pycryptodome) gmpy2 ecdsa ropgadget unicorn`。
**系统 `python3` 没有 `pwn z3 Crypto gmpy2 unicorn ropgadget ecdsa`**：逆 z3/符号执行、pwntools、unicorn 模拟的脚本一律用 `py` 跑。

## 坑

- 只看了 `main` 就下结论 —— 校验常在 `init_array` / 构造函数 / 线程里
- 遇到自研 VM 去硬啃 opcode：先把**数据段**（字节码数组、表）dump 出来，
  离线推导比在反汇编里追跳转快得多
- 结果没复算就提交：逆出来的"答案"必须能被同一份逻辑反算回去
- **把反编译产物当源码**：WAT / wasm-decompile / IL 只是结构与关系（结论要指回 `pc` / 方法 RVA / 段偏移）；
  手里是 `.wasm`/`.dll` 却死磕原生工具链时先跑 `revkit`（别 `objdump` wasm、别用 Ghidra 啃 .NET）

## script 不生效时

`binwalk -e` 拆一层看看是不是**嵌了另一个可执行/压缩包**（常见"题中题"）。

## 参考材料（按路线取）

> **按路线取**：只读你当前这条路线的那几篇（每篇都是几千字的实战笔记），不要全部预加载。

**语言与平台识别**
- [artifact-routing.md](references/artifact-routing.md) — 【我们的】按交付物形态路由（WASM/EVM/.NET/core dump/.asar/原生）+ `revkit` 用法与退路
- [languages.md](references/languages.md) — Language-specific: Python bytecode & opcode remapping
- [languages-compiled.md](references/languages-compiled.md) — Go binary reversing (GoReSym, goroutines, memory layout, channel ops
- [languages-platforms.md](references/languages-platforms.md) — Platform/framework-specific: Roblox place file analysis
- [platforms.md](references/platforms.md) — Platform-specific RE: macOS/iOS (Mach-O, code signing, Objective-C runtime
- [platforms-hardware.md](references/platforms-hardware.md) — Hardware and advanced architecture RE: HD44780 LCD controller GPIO recons…

**常见模式（怎么看出这是哪种算法/结构）**
- [patterns.md](references/patterns.md) — Foundational binary patterns: custom VMs, anti-debugging, nanomites
- [patterns-ctf.md](references/patterns-ctf.md) — Competition-specific patterns (Part 1): hidden emulator opcodes
- [patterns-ctf-2.md](references/patterns-ctf-2.md) — Competition-specific patterns (Part 2): multi-layer self-decrypting brute…
- [patterns-ctf-3.md](references/patterns-ctf-3.md) — Competition-specific patterns (Part 3)：Z3 单行 Python 电路、★**flag 校验后动态拼装**（别逆界面，先找组装点）、★**定长头 + 位打包数据文件**（先写解包脚本）等
- [patterns-runtime.md](references/patterns-runtime.md) — Runtime patching and oracle techniques: malware anti-analysis bypass

**工具（静态 / 动态 / 模拟执行）**
- [tools.md](references/tools.md) — Static analysis tools (GDB, Ghidra, radare2, IDA, Binary Ninja
- [tools-dynamic.md](references/tools-dynamic.md) — Dynamic analysis tools: Frida (hooking, anti-debug bypass, memory scanning
- [tools-emulation.md](references/tools-emulation.md) — Emulation frameworks and side-channel tooling: Qiling (cross-platform OS-…
- [tools-advanced.md](references/tools-advanced.md) — Advanced tools (Part 1): VMProtect/Themida analysis
- [tools-advanced-2.md](references/tools-advanced-2.md) — Advanced tools (Part 2): advanced GDB (Python scripting, brute-force

**把宿主当 VM（我们的 · 2026 新增）**
- [vm-host-abuse.md](references/vm-host-abuse.md) — 【我们的】判据：**段里没函数只有重定位** / **静态动态不一致** / **架构对不上** → 换思路。含 2026 十一例（Linkme 把 linker 当 VM、pixelflow 的 ShaderVM、JustROM 的 SPARC 裸 ROM、ezPython 的 marshal 改字节码…）+ 反教训（SU_Lock 的 VM 只有 5 个 opcode，是最简单一环）

**反分析 / 加壳 / 混淆对抗**
- [anti-analysis.md](references/anti-analysis.md) — Anti-analysis taxonomy: Linux anti-debug (ptrace, /proc, timing, signals
- [anti-analysis-ctf.md](references/anti-analysis-ctf.md) — CTF writeup techniques: SIGILL handler for execution mode switching (Hack…

**实战笔记与综合**
- [field-notes.md](references/field-notes.md) — Quick reference notes: binary types, anti-debugging bypass
- [shared-playbook.md](references/shared-playbook.md) — 【我们的】③哈希（碰撞/长度扩展/弱算法）④PRNG（LCG/截断输出）⑤**自定义代数结构**（置换/矩阵/多项式/CRC）。
- [btfly-SKILL.md](references/btfly-SKILL.md) — 【BTFly 原路由文件，备查】Provides reverse engineering techniques for CTF challenge…

> **卡住时**（同路线失败 ≥2 次无新信息 / references 翻完仍不知下一步 / 需版本相关已知漏洞）：先 `kb search` → `poc` → 本类 references → 再读 [`../ctf-kit/references/retrieval-sources.md`](../ctf-kit/references/retrieval-sources.md)（各题型来源表 + 查询式约定）。

## 纪律（所有题型共用，逐条照做）

> **正本：[`../ctf-kit/references/common-discipline.md`](../ctf-kit/references/common-discipline.md)**（能力检查 / 产物只写 cwd / 结论三级 / flag 逐字 / 卡住换面 / 机器兜底）。
> 本节不再抄写内容 —— 2026-11-09 实测 9 份副本里已有 1 份漂移，改一处漏 8 处。**读那一份就够了。**
