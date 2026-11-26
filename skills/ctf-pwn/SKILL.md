---
name: ctf-pwn
description: Pwn 题打法：file/查保护 → 静态找漏洞点与偏移 → 按保护选打法（栈溢出 / 泄漏 Canary / 泄漏基址 / ret2libc / hook）→ 本地跑通再打远程。当附件是可执行文件 + 给了 nc 地址、或在做栈溢出/格式化字符串/堆利用时参考。解题时优先自行分析判断，本 skill 提供 pwn 打法参考，不限制跨题型思考。
license: MIT
compatibility: ctf-agent（Windows 宿主 + WSL 执行；本 Skill 只读，命令/工具/网络可用性按题探测）
allowed-tools: Bash Read Write Edit Grep Glob
metadata:
  user-invocable: "false"
---

# Pwn 题

## 硬禁止（事前自查，先看这 6 条）

| 禁止 | 撞上了怎么办 |
| --- | --- |
| 用**本机 glibc** 的偏移猜靶机 | 先 `libcsearch <sym>=<off>` 定版本再下载对应 libc/ld，`tools/pwnsetup.sh` 拼环境 |
| 没 `checksec` 就开写 exp | `py -c "from pwn import *; print(ELF('./bin').checksec())"`，先定 NX/PIE/Canary/RELRO |
| 交互脚本不带 `timeout` | `timeout 120 py exp.py`；远程挂住不返回 = 白烧整轮时间 |
| 同一偏移 / 同一假设试 **≥2 次不换** | 换泄漏点或换打法（ret2win → ret2libc → hook），不是改填充字节 |
| 拿到 shell 先去提权不收 flag | 先 `cat /flag /flag.txt`（限时赛里 flag 才是分，提权是下一道题的事） |
| 本地没跑通就打远程 | 远程崩了分不清是你错还是环境差；先本地同 libc/ld 跑通 |

## 完成定义（可打勾，缺一项别说「做完了」）

- [ ] 架构 + checksec 结果已记录（命令 + 原始输出）
- [ ] 本地 exp 在**同版本 libc/ld** 下跑通（不是「看起来能打」）
- [ ] 远程命中并拿到 flag（逐字出现在输出里），已提交
- [ ] 偏移 / 泄漏值有可复现证据（`cyclic_find` 输出、leak 原始行）
- [ ] 失败的保护组合已记黑板（下轮不再从零试同一组合）
- [ ] `fgs solved` + **平台回执**


## 起手

1. `file <bin>` + 查保护：`py -c "from pwn import *; print(ELF('<bin>').checksec())"`（本机**没有**独立 `checksec` 命令）
   —— 先定架构与保护：NX / PIE / Canary / RELRO
2. 静态：`strings -n 6 <bin>`、`objdump -d <bin>`、`r2 -A -c 'afl' <bin>`、`rabin2 -I`
3. 找溢出点与偏移：`cyclic(200)` → 崩了之后 `cyclic_find(<值>)`
4. 按保护选打法：
   - 无 Canary → 栈溢出直接控制返回地址（ret2win / ret2libc）
   - 有 Canary → 先想办法泄漏 canary（格式化字符串 / 部分覆写）
   - PIE → 先泄漏代码基址（`%p`、写 GOT 的部分覆写）
   - Full RELRO → GOT 不可写，走 ret2libc / `__free_hook` / `__malloc_hook`
5. **先本地跑通再打远程**（本地 `process('./bin')`，通了把 host/port 换成远程）
6. 拿到 shell 立刻 `cat flag.txt` / `cat /flag`，然后马上提交

## 本类工具
> **libc 版本闭环（★ 2026-10-10 起）**：拿到任何 leak → `libcsearch <sym>=<off> …`（libc.rip）定版本
> → `--download work/<pid>/libc` 拿对应 libc → `bash tools/pwnsetup.sh ./chal <libc> <ld>` 拼本地环境
> → `pggdb ./chal`（带 gef）调堆/栈 → 本地跑通再打远程。**别用本机 glibc 的偏移猜靶机。**


> 工具以 $PI_LAB_ROOT/tools/TOOLS.md 为准；tools/bin 已在 PATH 里，命令名直接用。

```
# 用 py（= tools/venv/bin/python）跑，系统 python3 没有 pwn/z3/Crypto 这些库
py -c "from pwn import *; e=ELF('./bin'); print(e.checksec())"   # checksec 只有这条，没有独立 checksec 命令
py -c "from pwn import *; print(cyclic(200), cyclic_find(0x6161616b))"
timeout 120 py exp.py                                             # 交互脚本一定带超时
```
系统命令已装：`gdb objdump readelf strings nc socat file r2(radare2) python3`。
Python 库已装：系统 `python3` 有 `requests bs4 lxml yaml sympy numpy scipy PIL capstone`；
`py` 在上面基础上多 `pwn(pwntools) z3 Crypto(pycryptodome) gmpy2 ecdsa ropgadget unicorn` ——
**pwn / reverse / crypto 脚本一律用 `py` 跑，系统 `python3` 没有 `pwn z3 Crypto gmpy2 ecdsa ropgadget unicorn`**。
pi-lab 自带命令（pwn 线常用）：`py`、`pycdc` `pycdas` `pyinstxtractor`（.pyc / PyInstaller 解包）、`foremost`（文件雕复）、
`poc "<组件>"` / `poc --cve CVE-xxxx-xxxx`（本地 POC 索引）。
本机**没有** `upx` `patchelf` `java` `jq` `strace` `ltrace`；用不了就换不依赖它的路线，别去改系统环境装。

## 坑

- 附件里**缺 libc** 时：题给的 `libc-*.so` 就是答案；用它的版本算偏移，别用本机的
  （本机 glibc 版本不同 → 偏移全错 → 打不通还找不到原因）
- 有 PIE 时 `system` 地址要加基址泄漏值，别把 `elf.symbols['system']` 当绝对地址用
- 交互脚本一定带超时：`timeout 120 py exp.py`

## 题材

附件是 `attachment.zip` 时先 `unzip -l` 看清单 —— pwn 题常是 `pwn` 二进制 + `libc-*.so` 两件套，
而**题面里的"libc 在哪"往往就是 libc 版本这个考点本身**。

## 参考材料（按路线取）

> **按路线取**：只读你当前这条路线的那几篇（每篇都是几千字的实战笔记），不要全部预加载。

**栈溢出 / ROP / 格式化字符串**
- [overflow-basics.md](references/overflow-basics.md) — Stack/global buffer overflow, ret2win, canary bypass
- [rop-and-shellcode.md](references/rop-and-shellcode.md) — Core ROP chains (ret2libc, syscall ROP, rdx control, shell interaction)
- [rop-advanced.md](references/rop-advanced.md) — Advanced ROP techniques: double stack pivot to BSS via leave;ret
- [format-string.md](references/format-string.md) — Format string exploitation (leaks, GOT overwrite, blind pwn, filter bypass

**堆**
- [heap-techniques.md](references/heap-techniques.md) — House of Apple 2 (+ setcontext SUID variant), House of Einherjar
- [heap-techniques-2.md](references/heap-techniques-2.md) — CTF-writeup heap variants: UAF vtable pointer encoding shell argument
- [heap-fsop.md](references/heap-fsop.md) — FILE-structure (_IO_FILE) exploitation: fastbin stdout vtable two-stage h…
- [heap-chains.md](references/heap-chains.md) — **堆精细链（多步状态编排）**：unsortedbin 切分 → tcache 填满 → `malloc_consolidate`；House of Apple 2 / Botcake；**自研 allocator（题目自带 malloc.c）= 先读源码**；每条链先画状态表再写 exp

**内核**
- [kernel.md](references/kernel.md) — Linux kernel exploitation fundamentals: environment setup, QEMU debug
- [kernel-techniques.md](references/kernel-techniques.md) — Kernel exploitation techniques: tty_struct kROP (fake vtable + stack pivo…
- [kernel-bypass.md](references/kernel-bypass.md) — Kernel protection bypass: KASLR/FGKASLR bypass (__ksymtab)

**沙箱逃逸**
- [sandbox-escape.md](references/sandbox-escape.md) — Custom VM exploitation, FUSE/CUSE devices, busybox/restricted shell

**进阶综合利用**
- [advanced.md](references/advanced.md) — Seccomp advanced techniques, UAF, JIT, esoteric GOT
- [advanced-exploits.md](references/advanced-exploits.md) — Advanced exploit techniques (part 1): VM signed comparison
- [advanced-exploits-2.md](references/advanced-exploits-2.md) — Advanced exploit techniques (part 2): bytecode validator bypass via self-…
- [advanced-exploits-3.md](references/advanced-exploits-3.md) — Advanced exploit techniques (part 3): stack variable overlap / carry corr…
- [advanced-exploits-4.md](references/advanced-exploits-4.md) — Advanced exploit techniques (part 4): Windows SEH overwrite + pushad Virt…
- [advanced-exploits-5.md](references/advanced-exploits-5.md) — Advanced exploit techniques (part 5): data-interpretation exploitation —…

**实战笔记与综合**
- [field-notes.md](references/field-notes.md) — Detailed pwn notes: heap exploitation quick reference
- [shared-playbook.md](references/shared-playbook.md) — 【我们的】③哈希（碰撞/长度扩展/弱算法）④PRNG（LCG/截断输出）⑤**自定义代数结构**（置换/矩阵/多项式/CRC）。
- [btfly-SKILL.md](references/btfly-SKILL.md) — 【BTFly 原路由文件，备查】Provides binary exploitation techniques for CTF challenge…

> **卡住时**（同路线失败 ≥2 次无新信息 / references 翻完仍不知下一步 / 需版本相关已知漏洞）：先 `kb search` → `poc` → 本类 references → 再读 [`../ctf-kit/references/retrieval-sources.md`](../ctf-kit/references/retrieval-sources.md)（各题型来源表 + 查询式约定）。

## 纪律（所有题型共用，逐条照做）

> **正本：[`../ctf-kit/references/common-discipline.md`](../ctf-kit/references/common-discipline.md)**（能力检查 / 产物只写 cwd / 结论三级 / flag 逐字 / 卡住换面 / 机器兜底）。
> 本节不再抄写内容 —— 2026-11-09 实测 9 份副本里已有 1 份漂移，改一处漏 8 处。**读那一份就够了。**
