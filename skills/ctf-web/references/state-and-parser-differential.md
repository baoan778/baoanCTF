# 状态残留 与 组件语义不一致（自写版 · 2026 难题的两条主轴）

> ## 定位
> 用于**多组件 / 有状态**的 web 题（网关、代理、模板引擎、反序列化、CDN、浏览器）。
> 本文件收的是 2026 全年重点赛事里**反复出现**的两条主轴：
> **① 系统里"本该干净但没清"的地方**（状态残留）；**② 两个组件对同一输入理解不同**（parser differential）。
> 第三条「枚举每一道门」是元方法，放最后。
>
> 适用条件：目标有 ≥2 个组件（框架+DB、网关+上游、客户端+服务端），或有池化/缓存/复用结构。
> 不适用：单体小应用（走 `frontend-residue.md` 抠词汇表）。

---

## 主轴 ①：状态残留 —— 找"本该干净但没清"的地方

**判据**：代码里出现 **buffer pool / object pool / arena / scratch / cache**，
且**归还时不 memset / 不清零** → 查数据残留。

### ★★ 代表题：SCTF 2026 `Treasury Gateway`（Web **1000pt 全场最高**）
来源：<https://blog.xmcve.com/2026/06/15/SCTF2026-Writeup/>

Rust 网关里有个 per-tenant **scratch buffer 池**（`Box<[u8; 512]>`，零分配解析 HTTP header）：
```rust
const SCRATCH_CAP: usize = 512;
const NONCE_PREVIEW_BYTES: usize = 4;
type Scratch = Box<[u8; 512]>;
type TenantPools = HashMap<String, Vec<Scratch>>;
```
- `defer_trace()` 把 scratch **还池后仍持引用** → **UAF**
- 而**后台任务每 1000ms** 把「含 flag 的合规快照 JSON」**写进 scratch**

**利用**（三步，每步都是"没想到"）：
1. **保持连接**（`hold_seconds=5`）→ 让后台快照任务**覆盖你手上那块 buffer**
2. 触发 `defer_trace` 的 UAF → **读回那块已被快照写过的缓冲区**
3. flag 在快照 JSON **偏移 402** → **每次读 4 字节**（`NONCE_PREVIEW_BYTES=4`）→ 偏移 `402,406,410…` 读到 512

★ **两条可迁移的元手法**：
- **「回收缓冲区不清零」= 信息泄露通道**（我原来只想到读接口/报错/时序）
- **「后台任务定期写某个共享缓冲区」= 天然注入源** —— 你不用自己去写脏数据

### 同族其它形态
| 形态 | 判据 |
|---|---|
| 请求/响应对象复用（如 Java 容器、Node 中间件） | 上一个请求的字段残留到下一个 |
| 模板编译缓存 | 改模板后旧编译结果仍在 |
| 连接池中的 session/事务状态 | 上一个用户的状态泄漏 |
| **数据库事务 / snapshot 状态不一致** | CHAOS;HEAD（SCTF 2026）：用事务与 snapshot 的状态不一致造**堆泄露** |

---

## 主轴 ②：parser differential —— 两个组件对同一输入理解不同

**判据**：同一个字符串被**两处**解析（框架+DB、清洗器+消费者、两个库、客户端+服务端），
**只要两处规则不同，就能构造出"一处认为是数据、另一处认为是指令"的输入**。

### 2026 实测四例

| 题 | 两个解析器 | 差异点 |
|---|---|---|
| **DEF CON Quals 2026 `Birdblog`** | **PostgreSQL** vs **`pg-minify`** | PG 用 `''` 转义、`\` 是普通字符；`pg-minify` 却把 `\'` 当转义 → **minify 后 SQL 结构被改写**，注入生效 |
| **L3akCTF 2026 `Zebda`** | **NFKC 规范化** + **js-yaml** vs **PyYAML** | NFKC-slug 差异；**YAML merge-key（`<<:`）** 两库处理不同 |
| **SekaiCTF 2026 `Migurimental`** | **Next.js middleware** vs **page handler** | **重复 cookie 两处取值不同**；`assetPrefix` 的 rewrite **跳过 matcher** |
| **京麒CTF 2026 `ColorNote`** | **Chrome HTML 解析器** | 用 **`charset=utf-16`** 让后续 HTML 被当**乱码**解释 → **polyglot 绕过悬空标记防御**；再被 **CSS Leak 按位泄露 token** 非预期 |

### 同一族的相关武器
- **Unicode 同形字符 / 规范化**：`Birdblog` 用 `anyascii()` 把 `ʺ→''`、`⸨→((`、`≬→()` 展开，
  而「删除危险字符」的正则**不是全局的** → 每个 Unicode 字符只被删一个，剩下的还原成真语法
- **polyglot 文件**：`Waybird Machine`（DEF CON 2026）用 **TGA** 同时满足 ImageMagick 与 **MS-TDS** 协议
- **obs-fold 注入 CRLF**：`Set-Cookie: a="x\r\n y"`（续行以空格开头）可塞进换行
- **大小写/编码二次处理**：`%0Fflag%0Etxt`（破阵阁 2026 `Secure File Viewer`）

---

## 主轴 ③（元方法）：**枚举每一道门，而不是从作者设计的那道开始**

> SekaiCTF 2026 官方题解原话：
> *"the intended bug is one chain, and the shipped artefact **accidentally permits a shorter one**."*
> *"The skill is **enumerating every supposed gate** before assuming the puzzle starts at the intended one."*

同场四题验证：

| 题 | 作者设计的门 | 实际更短的门 |
|---|---|---|
| PP Farming 2 | 重入锁 + 透明代理只拒一个 selector | helper 的 storage **故意对齐**了 ATM → 无鉴权的 `setATM()` 经 fallback 改掉 `processor` |
| Migurimental | middleware 校验 id 与 session | `assetPrefix` 的 rewrite **跳过了 matcher** |
| SekaiID | 一整套 Android IPC 伪造链 | 两个 manifest 都写了 **`android:debuggable="true"`** → 一条 `adb run-as` |
| Open World | API 按 UUID 隔离 | **钱包共享同一个 TON 世界**（隔离是假的） |

**操作化**：开局先写「**这题声称有几道门**」（隔离/校验/边界/权限），然后**逐条问"它真的存在吗"**，
而不是先猜 payload。这条与我方 `ctfsurf`（前端残迹 → 先列攻击面）同源，是它的推广版。

★ **附带教训**（我方真实事故）：msgboard 上**用 2.7 万条字典 + 全量弱口令打一个 6 端点应用，
从头到尾没找到"发布留言"入口** —— 那不是字典不够大，是**没先枚举门**。

---

## 与其它文件的关系

- 小应用先抠词汇表（悬空 CSS 类 / 表单 action / 题面动词）→ `frontend-residue.md`
- 原型链 / 反序列化链 → `node-and-prototype.md`
- 真实 CVE（含 CNEXT / RSC 等）→ `cves.md`
- 「报错即 oracle」通用武器 → `skills/ctf-kit/SKILL.md`
