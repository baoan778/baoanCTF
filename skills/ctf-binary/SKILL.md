---
name: ctf-binary
description: 二进制类综合打法：先判断这是哪一种「二进制」—— 逆向走 reverse、漏洞利用走 pwn、固件/镜像先拆包找文件系统；剥壳/混淆先脱再分析；提取类目标用 strings+binwalk+rabin2 组合。当附件是固件、镜像、加壳程序、或题型标注为 binary 时参考。解题时优先自行分析判断题型，本 skill 提供打法参考，不限制跨题型思考。
license: MIT
compatibility: ctf-agent（Windows 宿主 + WSL 执行；本 Skill 只读，命令/工具/网络可用性按题探测）
allowed-tools: Bash Read Write Edit Grep Glob
metadata:
  user-invocable: "false"
---

# 二进制（综合）

## 第一步：判断它是哪一种

| 看起来是 | 走哪条线 |
|---|---|
| 单个 ELF/PE，有输入校验 | 逆向（reverse）：静态看逻辑 + 写脚本复算 |
| 单个 ELF/PE + 给了远程 nc | 漏洞利用（pwn）：查保护 → 偏移 → ROP/ret2libc |
| 固件 / 磁盘镜像 / squashfs | 先拆包：`binwalk -e`、`unsquashfs`、`7z x` |
| 加壳/混淆 | 先认壳：`file` / `binwalk`（本机**没有** `upx`、`detect-it-easy`）→ 脱不掉就运行时 dump / 手工找 OEP |
| 目标是"提取内含的东西" | 提取类：证书 / 私钥 / 配置 / 编译期常量 |

## 常用组合（提取类最有效）

```
file <bin>; strings -n 6 <bin> | head -50
binwalk <bin>                      # 内嵌了什么（含偏移）
rabin2 -zz <bin> | head            # 所有字符串（含宽字符）
binwalk -e <bin>                   # 拆出来再看
```
固件拆开后**逐层找**：`/etc/passwd`、`/etc/shadow`、启动脚本、web 目录、硬编码凭据、
`*.conf` 里的密钥 —— flag 常在这些地方。

## 本类工具

> 工具以 $PI_LAB_ROOT/tools/TOOLS.md 为准；tools/bin 已在 PATH 里，命令名直接用。

```
binwalk <bin>                  # 内嵌了什么（含偏移）；-e 拆包
file <bin>; strings -n 6 <bin> | head -50
r2 -zz <bin> | head            # 所有字符串（含宽字符）
pycdc <file.pyc>               # .pyc 反编译（pycdas 反汇编；pyinstxtractor <exe> 解 PyInstaller 打包）
foremost <bin>                 # 文件雕复（carving）
```
系统命令已装：`binwalk objdump readelf strings file r2(radare2) gdb 7z nc socat python3`。
Python 库已装：系统 `python3` 有 `requests bs4 lxml yaml sympy numpy scipy PIL capstone`；
`py` 在上面基础上多 `pwn(pwntools) z3 Crypto(pycryptodome) gmpy2 ecdsa ropgadget unicorn` ——
**要 `pwn` / `z3` / `Crypto` / `unicorn` 的脚本用 `py`（= `tools/venv/bin/python`）跑，系统 `python3` 没有这些库**。
pi-lab 自带命令（二进制综合线常用）：`py`、`pycdc` `pycdas` `pyinstxtractor`、`foremost`、`binwalk`、`r2`、
`exiftool` `steghide` `zsteg` `tshark` `volatility`（固件/镜像/内存）、`poc "<组件>"` / `poc --cve CVE-xxxx-xxxx`（本地 POC 索引）。
本机**没有** `upx` `patchelf` `java` `jq` `zbarimg`；脱壳别指望 `upx -d`，走运行时 dump / 手工找 OEP 的路线。

## 坑

- 拆包拆出一堆文件后没有**先列清单再挑**：先 `find . -maxdepth 3 -type f | head -50`
  和 `grep -ril flag` 收窄，别逐个人肉翻
- 加壳程序直接把壳当"加密算法"去逆：先认壳（`file`/`binwalk`，本机无 `upx`）再找 OEP，脱了再看
- 忘了这题可能同时是 pwn：拆出来的二进制若监听端口/给了 nc 地址，回到 pwn 线走

## 参考材料（按路线取）

> **按路线取**：只读你当前这条路线的那几篇（每篇都是几千字的实战笔记），不要全部预加载。

**通用手法（综合二进制题先读这篇）**
- [shared-playbook.md](references/shared-playbook.md) — 【我们的】③哈希（碰撞/长度扩展/弱算法）④PRNG（LCG/截断输出）⑤**自定义代数结构**（置换/矩阵/多项式/CRC）。

> **卡住时**（同路线失败 ≥2 次无新信息 / references 翻完仍不知下一步 / 需版本相关已知漏洞）：先 `kb search` → `poc` → 本类 references → 再读 [`../ctf-kit/references/retrieval-sources.md`](../ctf-kit/references/retrieval-sources.md)（各题型来源表 + 查询式约定）。

## 纪律（所有题型共用，逐条照做）

> **正本：[`../ctf-kit/references/common-discipline.md`](../ctf-kit/references/common-discipline.md)**（能力检查 / 产物只写 cwd / 结论三级 / flag 逐字 / 卡住换面 / 机器兜底）。
> 本节不再抄写内容 —— 2026-11-09 实测 9 份副本里已有 1 份漂移，改一处漏 8 处。**读那一份就够了。**
