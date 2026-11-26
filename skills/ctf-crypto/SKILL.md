---
name: ctf-crypto
description: Crypto 题打法：先识别（RSA / 分组密码 / 古典 / XOR / 哈希）→ RSA 按攻击面顺序试（小 e / 共模 / 不互素 / Fermat / Coppersmith / 已知高位）→ 分组密码看图样与模式 → 全部用脚本算、结果自校验。当给了 n/e/c、密文、密钥文件或编码后的数据时参考。解题时优先自行分析判断，本 skill 提供密码分析打法参考，不限制跨题型思考。
license: MIT
compatibility: ctf-agent（Windows 宿主 + WSL 执行；本 Skill 只读，命令/工具/网络可用性按题探测）
allowed-tools: Bash Read Write Edit Grep Glob
metadata:
  user-invocable: "false"
---

# Crypto 题

## 起手

1. **先识别**：给的是 `(n, e, c)`？AES 的 key/iv？一段 base64/hex？一个密钥文件？
2. RSA 攻击面**按顺序试**（先便宜的）：
   - 小 e 且明文短 → 直接开 e 次方根
   - 同一 n 两个 e（或两个 c）→ 共模攻击
   - `gcd(e, φ(n)) != 1` → 不互素
   - p、q 相近 → Fermat 分解
   - 已知 n 的高位/低位、或泄漏了 φ/d 的一部分 → Coppersmith / 逐位重建
   - 给了私钥 PEM 但"缺字节" → 分支出枝重连
3. 分组密码：ECB 看图样与重复块；CBC 试 bit-flipping / padding oracle；CTR 看 nonce 复用
4. 古典/编码：先看字符集与长度 → 频率分析 → 栏栅/维吉尼亚/培根/摩斯逐个试
5. **全部用脚本算**（别口算），结果做一次自校验（解出的明文是否可读、是否像 flag）

## 本类工具

> 工具以 $PI_LAB_ROOT/tools/TOOLS.md 为准；tools/bin 已在 PATH 里，命令名直接用。

```
py 脚本.py                        # = tools/venv/bin/python；要 z3 / Crypto / gmpy2 / ecdsa / sympy 的脚本一律这样跑
rsatool                           # 已知 p/q/e/d 互推其余 RSA 参数
hashid                            # 哈希类型识别
poc "<组件>"                      # 组件 → 本地 POC 索引
poc --cve CVE-xxxx-xxxx           # CVE → 本地 POC 索引
```
系统命令已装：`hashcat john python3`。
Python 库已装：系统 `python3` 有 `requests bs4 lxml yaml sympy numpy scipy PIL capstone`；`py`（= `tools/venv/bin/python`）另有 `pwn(pwntools) z3 Crypto(pycryptodome) gmpy2 ecdsa ropgadget unicorn`。系统 `python3` 没有 `pwn z3 Crypto gmpy2 unicorn ropgadget ecdsa`——RSA/密码脚本一律用 `py` 跑。

## 参考材料（按路线取）

> **按路线取**：只读你当前这条路线的那几篇（每篇都是几千字的实战笔记），不要全部预加载。

**RSA**
- [rsa-attacks.md](references/rsa-attacks.md) — RSA attacks: small e (cube root), common modulus, Wiener's, Pollard's p-1
- [rsa-attacks-2.md](references/rsa-attacks-2.md) — RSA attacks (specialized): RSA p=q validation bypass

**格 / LWE / Coppersmith**
- [lattice-and-lwe.md](references/lattice-and-lwe.md) — Lattice attack triage and workflow: LLL/BKZ/Babai

**椭圆曲线**
- [ecc-attacks.md](references/ecc-attacks.md) — ECC attacks: small subgroup, invalid curve, Smart's attack (anomalous

**现代密码（分组 / 流 / 分组模式）**
- [modern-ciphers.md](references/modern-ciphers.md) — Modern cipher attacks: AES (CFB-8, ECB leakage), CBC-MAC/OFB-MAC
- [modern-ciphers-2.md](references/modern-ciphers-2.md) — Modern cipher attacks (Part 2): Blum-Goldwasser bit-extension oracle
- [stream-ciphers.md](references/stream-ciphers.md) — Stream cipher attacks: LFSR (Berlekamp-Massey, correlation attack

**经典与历史密码**
- [classic-ciphers.md](references/classic-ciphers.md) — Classic ciphers: Vigenere (+ Kasiski examination), Atbash
- [historical.md](references/historical.md) — Historical ciphers (Lorenz SZ40/42, book cipher implementation)

**伪随机数（PRNG / LCG / MT）**
- [prng.md](references/prng.md) — PRNG attacks (foundational): MT19937
- [prng-attacks.md](references/prng-attacks.md) — PRNG attacks (CTF-era, 2017+): MT subset-sum seed recovery

**进阶数学与冷门构造**
- [advanced-math.md](references/advanced-math.md) — Advanced mathematical attacks (isogenies, Pohlig-Hellman
- [exotic-crypto.md](references/exotic-crypto.md) — Exotic algebraic structures (braid group DH / Alexander polynomial
- [exotic-crypto-2.md](references/exotic-crypto-2.md) — Exotic algebraic structures (Part 2, 2017+): BB-84 QKD MITM

**零知识证明与前沿**
- [zkp-and-advanced.md](references/zkp-and-advanced.md) — ZKP/graph 3-coloring, Z3 solver guide, garbled circuits, Shamir SSS

**综合与工具**
- [shared-playbook.md](references/shared-playbook.md) — 【我们的】③哈希（碰撞/长度扩展/弱算法）④PRNG（LCG/截断输出）⑤**自定义代数结构**（置换/矩阵/多项式/CRC）。
- [btfly-SKILL.md](references/btfly-SKILL.md) — 【BTFly 原路由文件，备查】Provides cryptography attack techniques for CTF challenge…
- [web3-and-chains.md](references/web3-and-chains.md) — **Web3/链上题**（原空白区）：Solana PDA/账户复用、TON 异步消息与跨实例经济、**BIP39 助记词校验和恢复**、ECDSA nonce 重用；含「拿到题先问的六个判据」

> **卡住时**（同路线失败 ≥2 次无新信息 / references 翻完仍不知下一步 / 需版本相关已知漏洞）：先 `kb search` → `poc` → 本类 references → 再读 [`../ctf-kit/references/retrieval-sources.md`](../ctf-kit/references/retrieval-sources.md)（各题型来源表 + 查询式约定）。

## 纪律（所有题型共用，逐条照做）

> **正本：[`../ctf-kit/references/common-discipline.md`](../ctf-kit/references/common-discipline.md)**（能力检查 / 产物只写 cwd / 结论三级 / flag 逐字 / 卡住换面 / 机器兜底）。
> 本节不再抄写内容 —— 2026-11-09 实测 9 份副本里已有 1 份漂移，改一处漏 8 处。**读那一份就够了。**
