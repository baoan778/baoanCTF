# Web3 / 链上题（Solana · TON · 助记词）—— 我原来的空白区

> **为什么单列**：`work/wp-2026/国内盲区题清单.md` 把「Web3/链上」列为四类漏掉的盲区之一
> （代表：阿里CTF **Solana PDA 复用**、Sekai CTF **TON 跨实例经济**、VNCTF **BIP39 助记词恢复**）。
> 我 skill 里原本 0 内容。这篇是**判据 + 工具 + 坑**，不追求覆盖所有链。

## 0. 先分流：这是"链上逻辑题"还是"密码学题"

| 形态 | 归属 | 典型 |
|---|---|---|
| 助记词/私钥派生（BIP39/32/44）、签名可塑性、nonce 重用 | **纯密码学** | VNCTF BIP39 恢复 |
| 合约/程序状态机、账户模型、权限校验 | **链上逻辑** | Solana PDA 复用、Aptos/Sui 资源 |
| 跨链/跨实例的经济模型（前置条件可操纵） | **经济逻辑** | TON 跨实例 |
| 客户端钱包/签名请求（钓鱼、盲签、EIP-712 域混淆） | **前端/协议** | 常见于 web3 web 题 |

## 1. 通用判据清单（拿到任何链上题先问这六个）

1. **资产在哪**？合约/程序账户/自管钱包/托管服务 —— flag 是"把某个账户余额转走"还是"读出链上数据"。
2. **谁有权限**？owner/admin/signer 校验在哪一行？有没有 `init_if_needed`/`close` 之类可重入的账户指令？
3. **能不能重放**？签名有没有 nonce/chainId/有效期；**同一笔交易能不能被二次执行**。
4. **账户是不是"可伪造"**？Solana 的 `AccountMeta` 由调用方提供 → **PDA/账户复用**是重灾区（用自己的账户冒充别人的）。
5. **数值边界**：整数溢出（Solidity <0.8 要 `SafeMath`；Rust 用 `checked_*` 但很多人不写）、精度/舍入（`u64` 除法截断）。
6. **前置条件可否被操纵**？预言机价格、时间戳、随机数（`block.timestamp` / `slot` / VRF 是否真随机）。

## 2. Solana（阿里CTF 那类）

- **账户模型**：程序无状态；所有账户由交易显式传入 → **必须校验 `account.key == expected` / `has_one` / `seeds`**。
  缺校验 ⇒ 攻击者传入自己的账户或别人同名账户 = **PDA 复用**。
- 常见漏洞：`init_if_needed` 忘记判 `is_initialized`、`close` 后未清 owner（复用到别的指令）、
  `seeds` 里少一个可变字段导致多个用户共享同一 PDA、CPI 调用未校验被调程序 ID。
- 工具：`solana-cli`（本机无 → 用 `cargo test-bpf`/`anchor test` 也行，需 Rust）、
  `solana-program-test`（Rust 集成测试里直接跑指令）、`anchor` 框架的 `#[derive(Accounts)]` 校验规则要逐条核对。

## 3. TON / 跨实例经济（Sekai 那类）

- TON 是**异步消息**模型（不是同步调用）→ 状态在消息之间可能被改（**同一实例的两次消息之间插一刀**）。
- 关注：`recv_internal` 的 `sender` 校验、消息里的 `query_id` 重放、实例间转账的**前置条件**（先用小额触发状态变化，再吃利差）。
- 跨实例题的核心：**在 A 实例制造一个"瞬时状态"，去 B 实例兑换** —— 找"谁能改谁不能改"的错配。

## 4. 助记词 / 派生（纯密码学，最可能自己独立做出来）

- **BIP39**：2048 词表 + 校验和（**熵的一部分是校验位**）。缺词/错词时：
  ① 用校验和筛掉 15/16 的猜测；② 按 `index` 已知时只需枚举该词；③ 多词缺 → 约束满足（z3/DP）。
  词表与校验算法要**自己实现一遍**（别信现成库的默认参数，路径/语言/空格都可能不同）。
- **BIP32/44**：派生路径（`m/44'/60'/0'/0/0`）猜错就得不到同一地址 —— 用"生成地址与目标地址比对"反过来定路径。
- 其他常见：`nonce` 重用（两条签名 → 恢复私钥：`k=(z1-z2)/(s1-s2)`）、低熵私钥（弱随机 → 用已知随机数生成器复现）、
  ECDSA 签名的 `s` 可塑性、`hash` 前缺少 `\x19Ethereum Signed Message` 前缀导致的跨协议签名复用。

## 5. 本机现状与缺口

- ✅ 已有：`py` 的 `ecdsa`、`hashlib`、`z3`、`sympy`、`sage`（大数/格/椭圆曲线运算都够）。
- ❌ 缺：`solana-cli`、`anchor`（Rust 工具链）、`cast`/`forge`（Foundry）。
  **需要时**：`apt install rustc cargo`（本机已有 golang 经验；Rust 装完 `cargo install --locked anchor-cli` 很慢），
  或走 ghproxy 取 prebuilt（Foundry 有 release 二进制）。
- **先做能做的**：助记词/派生/签名类题**不需要链上工具**，纯 python + sage 就能打 —— 这类务必拿下。

## 6. 检索关键词（补知识时用这些词去找 2026 的公开 WP）

`Solana PDA reuse exploit` · `account confusion` · `anchor init_if_needed exploit` ·
`TON async message state race CTF` · `BIP39 partial mnemonic recovery checksum` ·
`ECDSA nonce reuse recovery python` · `EIP-712 domain confusion` · `跨实例 经济模型 CTF`
