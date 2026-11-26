# 黑板契约（FGS v1）

> 这份文件定义 pi-lab 黑板的**语义模型**。实现见 [`fgs.py`](fgs.py)。
>
> 架构来源：
> - **Cairn**（腾讯云黑客松智能渗透赛 AK / 610 队）：Blackboard Architecture + `Fact` / `Intent` / `Hint`
>   三原语 + 不预设角色与工作流 + `bootstrap` / `reason` / `explore` 三类任务 + Dispatcher 是唯一协议写入者。
> - **CyberPenda**（同赛道，pi 原生落地）：把上面收敛成 **FGS = Goal / Step / Fact** 三节点
>   （ADR 0035）、**11 种受限关系**（ADR 0012）、**原子重建的单一状态文件**（ADR 0007）、
>   **Runtime Outbox 与 Harness 校验分离**（ADR 0035）。

---

## DO NOT regress（契约级不变量，改代码前先读）

> **这一节不新增任何规则** —— 它只是把本文件**已经写明**的硬约束归拢到一处，
> 每条都给出原出处（`文件:行号`）。理由是：这些约束原先散在 7 个章节里，
> 改 `fgs.py` 时很容易只看到自己那一段，回头又把另一条踩回去。
> **改这一节 = 改契约**，必须同时改出处章节，别只改一处（同一判据写两遍就是漂移的开始）。
>
> 形式抄 sapper `SPEC.md:173-179` 的 "DO NOT regress" 清单：一行一条、说清禁止什么 + 为什么。

| # | 不许做什么 | 为什么（违反了会怎样） | 出处 |
|---|---|---|---|
| 1 | **不许让非 `verified` 的 Fact 授权 `solved`** | `unverified`/`inferred` 只是怀疑，拿去结论 = 把猜测当分交；`solved --fact X` 会直接退 3 | `schema.md:105`、`:109`、`:114` |
| 2 | **不许用「孤点 Fact」当 solved 依据** | 必须沿 active 边能走到 active goal；否则"谁也没引用过的 Fact"就能授权 solved，图成了装饰。2026-09-28 实测 4 个项目全不连通 | `schema.md:376-382` |
| 3 | **不许让 Worker 直接写图** | Worker 只能 `publish` 到 outbox，Dispatcher 是唯一写入者；破了两路并发写会互相覆盖，且没有校验 | `schema.md:258-263` |
| 4 | **不许对不过校验的 outbox 用 `--force-partial` 蒙** | 有任何一条不过就**整批拒绝、图一个字节不动**；`--force-partial` 会把被拒条目静默销毁，下一个人无从知道"这条试过、为什么不行" | `schema.md:304`、`:317-318`、`:325`、`:332-333` |
| 5 | **不许在 outbox 非空时给 `next` 派新活** | 有未结算投递时 `next` 只给 `wait`（先 `apply`）；否则新活会踩在未结算的结论上 | `schema.md:343` |
| 6 | **不许让 `next` 自己释放 claim** | `next` 不是写命令，只报告 + 给命令；写入与留痕统一进 `step-reclaim`（否则谁释放的没留痕） | `schema.md:98`、`:184` |
| 7 | **不许让 `next` 自激重派**（终止性） | ck 已记且图上一点没变（无新 Fact/新 Hint/无 open Step）→ 必须给 `none`。再读一遍只会得到同一个答案，纯烧钱 | `schema.md:224-243` |
| 8 | **不许把 `deepen` 裁决的 Fact 当 solved 依据** | `deepen` 是"打回深挖"的下一轮方向，不是结论；`doctor` 判死、`gate` 退 3 | `schema.md:428`、`:438`、`:471` |
| 9 | **不许在没回执时声称「已被接受」** | 没有平台回执只能说"已解出，待平台确认"；自评 / HTTP 200 / 退出码 0 都不是证据 | `schema.md:397`、`:735` |
| 10 | **不许重复已证伪的路线** | 图上写了"试过、不行"就换**攻击面**（换入口/参数类型/协议层/身份），不是换 payload 细节 | `schema.md:209`、`:427` |
| 11 | **不许拿 A 目标的证据证明 B 目标** | 每个目标各自取证 | `schema.md:460` |
| 12 | **不许让 `cost` 在垃圾时间戳上崩** | 时间线要过滤解析失败的串，否则 `max()` 在 `None` 上比大小 → TypeError | `schema.md:472` |
| 13 | **不许把「试过一次」的否定结论写成 `verified`** | 否定结论会让规划者放弃整条方向：轻率否定 = 一条真路被永久封死，任务越早错越难自愈。`verified` 的否定结论必须带 `meta.attempts`（≥3 种互不相同）；不够格就降 `inferred`。`fgs gate` 是软检查（警告不拦） | `schema.md:102`、`:113`、`board/fgs.py` NEGATIVE_PAT |

### 设计原则：约束落在创建路径上，不在提示词里（#51，2026-11-17）

模型的自觉不是门禁。凡是「必须做 X」的规矩，要么在**写入那一刻**被代码校验，
要么有一条命令在最后一跳替你查；只写在提示词里（「请记得给 --from」）的规矩，
在压力下第一批被跳过 —— 越早跳越难自愈。

| 约束 | 落在哪（创建路径） | 不是落在哪 |
|---|---|---|
| 血缘必须指向真实存在的节点 | `check_relation()` 在 `fact-add --from` / `relate` / `step-add --depends` 写入前校验节点存在与端点矩阵，挂错直接 `Fail` | 不是「建议模型挂对」 |
| verified Fact 要能说出处 | 无 `--from` 时软提醒 + 审计 `fact.verified-no-source`；`solved` 的 `solved-orphan` 门禁把孤点挡在交卷前 | 不是只靠「请记得给来源」 |
| 放弃必须写 WHY | `step-abandon --why` 空串直接 `Fail`（`board/fgs.py`） | 不是「文案提醒要写原因」 |
| 交卷只能由主会话做 | Worker 只 `publish` 到 outbox；`solved` / `confirm` 不授予 worker（与 ARTEX `defaultUnbound={"goal_met":true}` 同构） | 不是「worker 别自己交卷」 |

防退化的机器判据：`board/tests/test_constraints_at_creation.py` —— 校验点被挪回提示词、
或软提醒/审计消失，它就红。
---

## 0. 为什么要有它

单 agent 解题最大的浪费不是"不会"，是**忘了自己做过什么**：

- 同一条死路反复试（上下文里没有"已证伪"这个事实）
- 子代理各自为战，结论回来变成一段散文，下一轮没人读得出来
- 卡住时没有"全局在哪"的视图，只能在最近的上下文里打转

黑板解决的是这个：**图是唯一的共享真相**。每个 Worker 只读图、只出一条经过校验的写入，
下一个 Worker 从图上继续。Cairn 管这叫 stigmergy（通过环境间接协作）——Worker 之间不通信。

---

## 1. 四种节点

| 类型 | 前缀 | 语义 | 谁能写 |
|---|---|---|---|
| `goal` | `g` | **要达成什么**。有且只有一个 active。 | 操作者 / 主会话 |
| `step` | `s` | **一个待执行的探索方向**（Cairn 的 Intent）。不是计划，是"下一步试什么"。 | 主会话 / `reason` |
| `fact` | `f` | **已确认的客观结论**（Cairn 的 Fact）。必须能被别人按同样步骤复现。 | 探索它的 Worker |
| `hint` | `h` | **人的判断**（Cairn 的 Hint）。随时注入，Worker 下次读图时吸收。 | 只能是人 |

### Step 的状态机

```
            claim                conclude
  open ───────────► claimed ──────────────► done
   ▲                  │
   │                  │ 租约到期（TTL 默认 1800s，PI_FGS_STEP_TTL 可调）
   │                  ▼
   └──────────── zombie（`fgs next` 报 reclaim，`fgs step-reclaim` 放回 open）
        release（主动放弃）
```

- `open` = 可认领。**谁先 claim 谁做**，claim 失败就换别的（`fgs.py step-claim` 退出码 2）。
- `claimed` = 正在做。带 `claimed_by`，避免两个 Worker 做同一件事。
- `done` = 已收口，**必然伴随一个 Fact**（`produced` 边）。
- `abandoned` = 停了，但**必须带原因**（#40 ①，2026-11-17）：
  - `abandon_reason = refuted`（默认）= 有证据说明这条路不通 → 后继按「试过、不行」处理，别再派；
    `step-reopen` 要 `--force-refuted`（留审计）。**原先只有一个 `abandoned` 状态，把「超时被杀」也混成
    「证伪」**，于是规划者自己把没被否定的路封死 —— 这是选错题级别的自伤。
  - `abandon_reason = timeout` = 只是这次没跑完（超时 / 引擎崩 / 到点收工）→ `step-reopen` 可直接续上，
    STATE 的 §2 Parked 与 `fgs chain`（⏱）都会标出来。
  - 两种都要留 `abandon_why`（`fgs step-abandon <pid> <step> --why "…"`，空 WHY 拒收）：
    **机器只知道 dry_streak，WHY 必须是人写的**，否则下一个人只能猜。
  - `abandon_reason = killed` = 人主动止损（`fgs step-kill <pid> <step> --why …`，#28 ③，2026-11-17）
    → **不声称任何证据**；`step-reopen` 可直接续（同 timeout，`fgs chain` 标 ⛔）。
    它与 timeout 的区别在**写入面**：止损之后该 Step 的 `conclude` 在 `publish` 与 `apply` 两处都拒收
    （#28 ④）—— kill 杀不掉正在跑的子代理，它被打断前可能已经交出一条结论，
    若还让它入图，「已经停掉的一路」就又往地基里塞了半截结论。
  - 纠偏队列 `steer.jsonl`（#28 ①）：主会话 `fgs steer-send` 入队、Worker `fgs steer-drain` 取一条（FIFO）。
    **不是强制注入**（pi 没有 `PreToolUse` 阻塞 hook），取走才算数（审计 `step.steer.drain`），
    `fgs workers` 显示每个 Step「待取纠偏 N 条」。

#### 租约：claim 会过期（Cairn 的 `HeartbeatLease` / intent heartbeat）

Cairn 的做法：Worker 每隔 `interval` 给 intent 发 heartbeat，连续两个 interval 失败
（`HEARTBEAT_FAILURE_GRACE_MULTIPLIER = 2`）就判 Worker 死了。
**pi-lab 原先没有这一层** —— `step-claim` 只写 `claimed_by`，没有任何到期概念。
后果是**静默死锁**：Worker 一死（子代理崩了 / 会话丢了 / 超时被砍），step 永远停在
`claimed`，`decide()` 永远返回 `wait`，整个项目停住且**零报错**。

现在的契约（三条）：

| 命令 | 干什么 | 何时用 |
|---|---|---|
| `fgs step-heartbeat <pid> <sid> --by X` | 续租（写 `updated_at`，进 log） | 长任务（大字典/全盘扫/长交互）中途跑一次 |
| `fgs next` | 发现过期 claim → 报 `reclaim` + 给回收命令 | 调度者每轮都看得到 |
| `fgs step-reclaim <pid> [--ttl N]` | 把僵尸 step 放回 `open`，记 `was`（原属谁） | 看到 `reclaim` 就跑 |

- 阈值：`STEP_TTL` 默认 **1800s**（pi 子代理跑一次 explore 常见 5~20 分钟），
  环境变量 `PI_FGS_STEP_TTL` 或 `--ttl` 可覆盖。
- **只有 claim 本人能续租**（`--by` 不匹配 → 退出码 2）：不让一个 Worker 替另一个续命。
- `fgs next` **不自己释放**（它不是写命令）——只报告 + 给命令；写入与留痕统一进 `fgs step-reclaim`。
- `fgs doctor` 把过期 claim 列为**提醒**（只读，不判死）。

### Fact 的置信度分级

| 级别 | 含义 | 能不能当结论 |
|---|---|---|
| `verified` | 独立复现过一次，或有原始输出逐字佐证 | ✅ **只有这个能宣告 solved** |
| `inferred` | 从输出推出来的（例：从报错推版本号） | ❌ 必须写明是推断 |
| `unverified` | 只是怀疑 | ❌ |

这条是硬的：`fgs.py solved --fact X` 在 `X` 不是 `verified` 时**直接拒绝**（退出码 3）。
对应 Cairn 的纪律 —— 「只写已证实的事实，不允许把猜测写成事实」。

**否定结论另有一条软门槛（#7，2026-11-17）**：`不可注入 / 端口关闭 / 无登录入口 / 不存在`
这类否定会让规划者**放弃整条方向**，所以 `verified` 的否定结论要能说出「试过哪些手段」——
`--attempts` 至少 3 种互不相同（换编码 / 参数 / 路径 / 方法 / 身份）。没记够就退回 `inferred`，
别用轻率结论把路线焊死（原文金句：「任务早期的错误否定尤其会把方向带偏且难以自愈」）。
**这是软检查**：`fgs fact-add` 回显提醒、`fgs gate` 出一行 `!`，都不改退出码 ——
证据强弱机器判不了，机器只判「有没有记」。

### Fact 的**可选**元数据（结构化字段，不是新节点类型）

`confidence` 是唯一的**门禁**维度（只有 `verified` 能当结论）；
元数据只是同一批事实的**投影字段**，回答「这条属于哪个阶段 / 用了什么工具 / 打到哪个目标 / 涉及哪些 IOC」。

| 字段 | 取值 | 用途 |
|---|---|---|
| `phase` | `detect` / `triage` / `contain` / `eradicate` / `recover` / `report` | 报告按阶段分组 |
| `action` | `collect` / `verify` / `contain` / `eradicate` / `recover` … | 动作类别 |
| `verdict` | **两套词表共用**：IR 用 `pass` / `fail` / `inconclusive`；SRC 复审用 `accepted` / `ignored` / `deepen` | 这一步的判定（写入时校验，拼错会被拒） |
| `severity` | `critical` / `high` / `medium` / `low` / `info` | SRC 定级 |
| `proof_kind` | `sentinel` / `authz_diff` / `idempotent` / `side_read` / `weak` / `none` | 写/删/改类的**无害证据类型**（`weak`/缺 → doctor 提醒） |
| `tool` | 命令/工具名（`ssh`、`tshark`、`volatility`…） | 可复现性 |
| `target` | 主机 / IP / 账号 / service | 影响面 |
| `time` | ISO8601（尽量 UTC） | **时间线用这个**，没有才退回正文时间戳 / 记录时间 |
| `ioc` | `类型=值` 字符串数组（`IP=1.2.3.4`） | **IOC 表用这个**，没有才退回正文正则 |

**为什么要它**：应急报告 / 时间线 / IOC 表**本质上是同一批事实的三种投影**。
属性只写在散文里，生成报告就得靠正则去抠（会抠错、会抠漏）。写一次结构化字段，三个投影都免费：

```bash
fgs fact-add <pid> "异常高 CPU 进程 xmrig，外联 pool.bad.xyz:3333" \
  --confidence verified --evidence "ps --sort=-%cpu 输出第 2 行" \
  --phase triage --action collect --tool ssh --target srv-01 \
  --time "2026-09-01T03:12:00Z" \
  --ioc "域名=pool.bad.xyz" --ioc "IP=203.0.113.77" --ioc "文件=/tmp/.x/xmrig"
```

- **全部可选**：一个都不写也能用（三个投影都退回兜底逻辑）。
- **Worker 也能投**：outbox 的 `fact` 载荷支持 `"meta": {...}`，非法键 / 非法阶段 / 非法 IOC 格式
  会**整批拒绝**（幻觉防火墙对元数据同样生效）。
- **纪律**：meta **不能替代**结论。带 meta 的 `inferred` Fact 依然是 `inferred`，
  照样不许 `fgs solved`，也照样不进报告里的 IOC 表。


---

## 2. 十一种关系（ADR 0012，带端点矩阵）

不允许任意连边，每种关系有封闭的 from/to 类型矩阵。不合法的组合**直接拒绝**，
这样推理、校验、可视化共用一套语法。

| 关系 | 允许方向 | 带理由 |
|---|---|---|
| `about` | step \| fact → goal | |
| `part_of` | step → step，goal → goal（无环，不传播状态） | |
| `tests` | step → goal \| fact | |
| `produced` | step → fact | |
| `evidences` | fact → fact \| goal | |
| `supports` | fact → fact \| goal | ✅ |
| `contradicts` | fact → fact \| goal（互指合法，不自动改状态） | ✅ |
| `derived_from` | step → fact \| goal；fact → fact \| goal | |
| `depends_on` | step → step（无环） | ✅ |
| `satisfies` | fact → goal | |
| `supersedes` | 同类型 → 同类型（无环，最多一个当前替代） | |
| `covers` | digest → step \| fact \| hint（**只能由 `fgs fold` 生成**） | |

约束：**自环一律非法**；`part_of` / `derived_from` / `depends_on` / `supersedes` 各自无环；
只有 `supports` / `contradicts` / `depends_on` 能带一句简短理由。

#### 图压缩：`digest` 节点与 `covers` 边（#34，2026-11-17）

- `digest` 是第五种节点类型（id 前缀 `d`）：`text` = 摘要标题，`covers` = 成员 id 列表，
  `signature` = 成员语义字段（id/type/text/confidence/status）的 `sha256[:16]`，`generation` = 折叠代数。
- 被折成员**保留在 `nodes` 里**、只加 `superseded_by = <digest id>`；`covers` 边与之同时成立（**不许 `fgs relate` 手连**）。
- 渲染（`fgs show` / `fgs snapshot`）只隐藏**仍冷**的成员：goal、`open`/`claimed` 的 step、`verified` 的 fact、
  与活 step 相连、或 `PI_FGS_COLD_SEC`（默认 1800s）内更新过 —— 这些都会自动重现（hotAtRender）。
- `fgs expand` 前重算 `signature`：不符默认拒绝（rc=3，说明折叠后被改过），`--force` 越过并留审计 `digest.expand`。
- 摘要**不是证据**：任何门禁都不认 digest 当 verified Fact；要原文就 `fgs show --json` 或 `expand`。


---

## 3. 三类任务（Cairn 的核心，别自己发明第四类）

`fgs.py next <project>` 会按下面的顺序给出唯一答案 —— **这是调度，不是建议**：

| 任务 | 触发条件 | 干什么 | 产出 |
|---|---|---|---|
| `goal` | 还没有 Goal | 先明确目标 | 一个 `goal` |
| `bootstrap` | 初始态（无 fact 无 step 且没 bootstrap 过） | **直接尝试解决整个问题**，不要先画计划 | 一个 Fact（+ 可能直接 solved） |
| `explore` | 有 `open` 的 step | 认领**一个** step，执行，给出一条客观结论 | 一个 Fact |
| `reason` | 没有 open step、也没有僵尸 step | **读全图**判断：够了吗？下一步该试什么？ | `solved` ／ 1~3 个新 Step ／ 一条"死路论证"Fact |
| `wait` | 还有**未过期**的 step 在 `claimed` | 等它们收口（或续租） | —— |
| `reclaim` | 有 step 的 claim **已过期** | 僵尸 claim 会把项目死锁在 wait → 回收 | `fgs step-reclaim` |
| `none` | 已 solved，**或** `reason` 空手（图没变） | 停（后者等人注入 Hint） | —— |

> `wait` / `reclaim` / `none` 是**调度状态**，不是第四类任务 —— 任务只有
> `bootstrap` / `explore` / `reason` 三类（Cairn 的原则：任务从图状态生成，不从岗位描述生成）。

### `reason` 的产出义务（Cairn：**不许空手**）
Cairn 在代码层硬禁：`validate_reason_payload()` 里 `open_intents` 为空时
`intents` 为空会直接 `raise("intents must not be empty when open_intents is empty")`。
道理：你被叫起来，就是因为**没人在干活**。

所以 `reason` 的产出必居其一：**solved** ／ **1~3 个新 Step** ／ **一条"死路论证"Fact**
（把"哪几条结论封死了哪些出口"写成 `verified` 的 Fact）。最后一条不是退路：
它改变后续所有人的搜索空间，而空手只是白烧一轮。

一轮最多 3 个 Step（Cairn `max_intents` 默认 3）。多条时必须**互不重叠、可并行**
（覆盖不同攻击面／不同事实来源），而不是同一条路换 payload。

### `reason` 的**重复度止损**（提 Step 前的自检）

提 Step 之前必须过一遍：

1. **核心名词检查**：这条 Step 的核心名词，在图上的**已证伪 Fact** 里出现过吗？
2. **次数检查**：同一方向**已经失败 ≥ 2 次**了吗？

任一命中 → **不许再提同方向**，改提**换攻击面**的（换入口 / 换参数类型 / 换协议层 / 换身份）。
真想不出新方向时，落一条**"死路论证" Fact**（逐条列出已被封死的出口）——这是合法的产出，重复已证伪路线不是。

### `explore` 的在途自检（没有外部观察者，就自己当观察者）

> 多支 TCH 决赛队伍都外置了一个 Observer 来打断跑偏（绿盟：监控最近记录、判断偏离主线即打断）。
> 本仓没有这一层进程，所以把它写成**纪律**：每 ~8 次工具调用自问三条 ——
> ① 还在原方向吗？② 有没有「本不该通却通了」的异常（**最高价值情报**）？③ 这条 Step 还值得继续吗？

### 时间盒**递增**（重试边界是时间，不是次数）

同一道题：第一次给基础时长；**第二次起 1.5×、第三次 2×（封顶 2×）**。
理由：避免「每次都刚分析到关键位置就被砍、下一轮又从差不多的地方重走」。
代价是总时长要封住 → 所以**第一轮必须把"我到哪了"落成 Fact**，让下一轮持续而非重来。

### `reason` 的去重与**终止性**（防自激）

`reason` 不是每轮都跑。只有出现**新态势**才重触发：

- Fact 数量增加，或
- Hint 数量增加，或
- 从"有 open step"变成"没有 open step"

单纯"上次 reason 刚建了个新 step"**不算新态势**（否则会自激）。
主会话在 `reason` 收尾时用 `fgs.py checkpoint <project>` 记录水位。

**终止性（原先的代码漏洞）**：checkpoint 只记 `facts/hints/open_intents`，
所以"空手的 reason"之后三者都没变 —— 而旧代码最后一个分支是
无条件 `return "reason"`，于是**每轮都再问一遍同样的问题，图却一动不动**
（纯烧钱自激）。现在：ck 已记且图上一点没变 → `next` 给 **`none`**，
并明确要求人出手（`fgs hint`）或显式收工。

> 另一个坑：`fgs checkpoint --bootstrapped` 标记 bootstrap 收尾时，
> **必须把 reason 水位清零**（`reason_checkpoint = None`），否则
> "bootstrap → 一次 reason"这一步会被终止性门禁吃掉，项目刚 bootstrap 完就停。

### 双阶段（Cairn 的 execute + conclude）

`bootstrap` 和 `explore` 都是**两阶段**：

1. 第一阶段：正常执行。超时或输出不合法 → 别丢，**带着同一个 session 进第二阶段**。
2. 第二阶段（`*_conclude`）：明确要求"**不要继续探索，只总结截至目前的结论**"，
   只产出一个 Fact。

理由：探索了 8 分钟被超时砍掉、结果一个字都没留下，是最没意义的浪费。
收尾阶段至少把"试过什么、看到什么"落成 Fact。

---

## 4. 谁能写（Cairn 的 Dispatcher 是唯一写入者）

| 角色 | 权限 | 命令 |
|---|---|---|
| 操作者 / 主会话 | 全量写入 | `init` `goal` `step-*` `fact-add` `relate` `hint` `solved` `checkpoint` |
| **人（只能是人）** | 确认 flag 正确 | `confirm` |
| **Worker 子代理** | **只能投 Outbox** | `publish` |

### 调度者身份：一个项目只有一个 dispatcher（ADR 0034）

**主会话 = Dispatcher**，但"我是不是这个项目的 dispatcher"要有客观依据 ——
否则两个 pi 窗口同时 `fgs next`，两边都会自认调度者：同一个 step 被派两次、
两批 outbox 交错、预算失效。CyberPenda ADR 0034 记的就是这个事故形态
（孤儿会话自封第二个 orchestrator → 末段 100 分钟零产出）。

机制只借它的两件事（**首灯身份检查 + 单 epoch 心跳**），不搬它那套 runtime 协议：

| 概念 | pi-lab |
|---|---|
| `graph/leader.lock` | `<project>/lead.json`：`{owner, epoch_at, started_at, note}` |
| 心跳新鲜度 | `epoch_at` 距今 **≤ 300s** 视为当值（`LEAD_TTL`） |
| 首灯身份检查 | `fgs next` 先查；**无主/过期 → 自动接管**，别人当值 → **exit 2 停手** |
| owner 是谁 | 默认取 `PI_SESSION_ID`（天然区分窗口），可用 `--as` / `--by` 覆盖 |

```bash
fgs lead <pid>                     # 查看/续期（默认以当前会话身份）
fgs lead <pid> --by win-A --note "接手"   # 指定身份
fgs lead <pid> --release           # 主动释放（只有持有者能释放，否则 exit 2）
fgs lead <pid> --force             # 见过时锁 / 确认对方已停之后接管
fgs next <pid>                     # 含首灯检查；被占则 exit 2（--json 时给 task=wait）
fgs next <pid> --force             # 强行接管并继续
```

设计取舍：
- **自动接管**（无主/过期时）是为了单窗口零摩擦 —— 不需要每次都先 `lead` 一下。
- **过期自动接管**对应 ADR 0034 的 "a missing or stale lock means takeover"：
  死掉的 lead 不该占着位置，否则整个项目卡住。
- 心跳只在 `next` / `lead` 时更新，不做后台线程 —— pi-lab 是单机，够用。

> 回归测试：`python3 board/tests/test_lead.py`（25 条断言）。


Worker 没有直接写图的权限。它把语义更新投进 `outbox.jsonl`，主会话 `apply` 时：

1. 逐条**校验**（节点存在？关系矩阵允许？step 是不是被别人抢了？）
2. 全部通过 → 一次性合入，并原子替换 `graph.json`
3. 有任何一条不过 → **整批拒绝，图一个字节都不动**，outbox 保留下来等人修

这样单个 Worker 的幻觉不会污染全局图。对应 Cairn 的
「Agent 不直接认领 Intent、不直接调用 Cairn API，只返回结构化结果，Dispatcher 再决定如何请求 Server」。


### 投递终态：拒绝也要留痕（ADR 0021 / 0023）

**每条投递最终必须落到四种终态之一**，写进 `deliveries.jsonl`（append-only 台账）与 `log.jsonl`：

| 终态 | 含义 | 之后会怎样 |
|---|---|---|
| `applied` | 已校验并合入图 | —— |
| `rejected` | 校验不过（内容非法） | 条目留在 outbox 等人修；`--force-partial` 时归档到 `outbox-rejected.jsonl` |
| `action_required` | 校验过了，但同批有非法条目 → **整批未合入** | 等人修好同批的坏条目后重新 `apply` |
| `duplicate` | 同指纹此前已 `applied` | **跳过，不二次写图** |
| `withdrawn` | 人显式撤回（写明了原因） | 原文归档到 `outbox-rejected.jsonl`，从 outbox 移除 |

为什么这三条是硬的（首版缺过，实盘压测踩出来）：

- **没有终态 = 等于没发生过。** 旧版拒绝只打在 stderr，`log.jsonl` 里 0 条记录；
  `--force-partial` 时更是把被拒条目**静默销毁**。下一个人无从知道「这条试过、为什么不行」。
- **迟到的结果不能改变黑板状态**（ADR 0021）：投递指纹（剔除 `_seq` 等投递元数据后的内容哈希）
  相同时直接判 `duplicate`，而不是再合入一遍 —— 否则同一条事实会被写两次，图里长出重复节点。
- **attempt 计数不重置**（ADR 0023）：同一 `seq` 的第 N 次尝试记在台账里（`attempts` 字段），
  预算属于义务，不随新的投递尝试清零。禁止「通用重试」式的盲目重放。

```bash
fgs apply  <pid>                  # 校验并合入；有任何一条不过 → 整批拒绝 + exit 3 + 留痕
fgs apply  <pid> --force-partial  # 只合入通过的部分；被拒条目**归档保留**，不销毁
fgs outbox <pid> [--tail 20]      # 人读视图：待办 + 投递台账 + 合计
fgs outbox <pid> --withdraw <seq> --reason "…"   # 显式撤回（唯一能清被拒条目的通道）
fgs publish <pid> --by w1 --resolves <seq> --op '…'   # 指名"这一批修的是哪条被拒记录"
```

### 未结算投递会挡住派活（outbox-protocol §6 / v2-spec §10）

> Finish readiness reports outstanding delivery; it never equates Runtime silence with a complete Goal.

**outbox 非空时，`fgs next` 只给 `wait`**，理由是「有 N 条未结算投递，先 `apply`」。
还有人的结论没进图就派新活 = 让两条结论赛跑。

这条规则必须配一个**撤回通道**才成立（否则被拒条目永久堵住调度就是死锁）：

- `--withdraw <seq> --reason "…"` —— **唯一**能清掉被拒条目的通道，必须写原因，落终态 `withdrawn` + 归档原文。
- 修复关系统一用 `publish --resolves <seq>` 表达：**被拒那条的终态不可改写**（ADR 0021），
  新记录在台账里带上 `resolves` 链接 —— 于是「哪一批修了哪一批」是可审计的，不靠人记。
- 原则：**"没有回执"不等于"被拒绝"**（missing receipts are not proof of rejection）。

### 注意力预算（v2-spec §10）

`fgs snapshot` 会量**确定性快照本身**的字节数并给出提示（只算快照，不含历史与流水）：

| 规模 | 动作 |
|---|---|
| ≈16k token | 健康线，过了就留意 |
| ≈32k token | 提示考虑归并低价值 Fact |
| ≈64k token | 提示"该做归并了"（压结论、合并已证伪路线） |

**永不截断、永不按相关性过滤** —— 阈值只提示，不改变行为。这跟「快照必须拓扑完整」不冲突：
图是工作记忆（必须完整），归并意味着**真的去合并节点**，而不是把快照切小。

> 回归测试：`python3 board/tests/test_outbox.py`（48 条断言，覆盖五种终态、审计动作名、结构化 meta、
> 未结算阻塞与 withdraw/resolves 通道）。

### `solved` 与 `confirm` 是两回事（两道门）

| 门 | 命令 | 含义 | 谁下的 | 副作用 |
|---|---|---|---|---|
| 1 | `fgs solved <pid> --fact fNNN` | agent 认为解出来了 | 我（主会话） | 「我认为完成了」 |
| 2 | `fgs confirm <pid> --flag "..."` | **人看过平台回显，确认 flag 是真的** | **只能是人** | **开 WP 闸门** |

- `solved` 有两道门禁（都退出码 3）：依据的 Fact 必须 `verified`，**且必须挂在图上**——
  从它沿 active 边（无向）能走到 active goal（`path_to_goal()`）。
  孤点 Fact 不许当结论：Cairn 要求 `complete.from` 来自图上有效的 fact
  （`"data.complete.from must come from Valid facts"`），否则"一个谁也没引用过的
  Fact"就能授权 solved，图反而成了装饰。
  > 2026-09-28 实测：当时 4 个已有项目的 `solved.by` 到 goal **全不连通**，
  > 已用 `fgs relate --type satisfies` 回填。`fgs doctor` 也会抓这道。
- `confirm` 在项目未 solved 时也能用，但**必须带 `--flag`**（明确人确认的是什么值）。

#### 什么才算「平台确认」（回执判据）

> 来源：heimdall-agent `resources/prompts/observer/observer_recap.md` 的口径，按 pi-lab 重写。
> `confirm` 不是「人说一声就算」—— 它盖的是**平台侧回执**，所以得写清什么算回执。

| 算证据 | 不算证据（常见自欺） |
|---|---|
| 平台回显「正确 / 通过 / accepted」 | agent 自称成功 |
| 平台给出计数关系 `correctCount >= totalCount > 0`（能直接证明**全部** flag 被接受） | 输出里出现一个候选 flag |
| 平台里的得分 / 排名变化 | HTTP 200、退出码 0、「提交成功」字样 |
| 平台返回的 flag 原样回显 | flag 格式看起来对、按规律推出来的 |

- **没有回执时不许声称「已被接受」**：只能说「已解出，待平台确认」（`fgs show` 会显示 `solved 待确认 ⏳`）。
- **历史回执要标轮次**：`log.jsonl` 里旧任务的 `confirmed` 不能当本次的证据，多轮 / 实例轮换后尤其。
- **多 flag 题**：只接受了部分 flag 时，不要把整题写成完成。
- `confirm` 没有 Worker 通道（`publish` 不能投 `confirm`）：人没看回显就不该开 WP 闸门。

#### 提交四态：**平台限流 ≠ 答错**

提交结果必须分成四种，别混：

| 结果 | 含义 | 该做什么 |
|---|---|---|
| `CORRECT` | 平台确认正确 | 记 solved |
| `WRONG` | 平台明确判错 | 回去重做 |
| **`SUBMIT_BUSY`** | **平台限流/未受理**（不是判错！） | **等一会重提同一个 flag** —— 千万别因为限流就把已经正确的 flag 改掉 |
| `SUBMIT_ERROR` | 接口/网络异常、未受理 | 重试提交，不算解题结论 |

> 来源于 Rotom 的实战教训：「平台限流如果被模型误认为 WRONG，它很可能会把一个本来正确的 flag 改掉，
> 然后重新回去分析，白白浪费时间。」——**"没被受理"与"被否定"是两件事**。
- **WP 生成只认第二道门。** 对外产物不能建立在我的自评上 ——
  这是 Cairn「Dispatch/自评分离」与 StrikeAgent「红队二次验证」的同一个思路。

### SRC / 渗透类结论的字段约定（三裁决 ↔ 图操作）

判据正本：`skills/src-recon/references/收洞判定.md`；独立复审代理：`skills/src-recon/references/src-reviewer.md`。
AutoHunter 的 Reviewer 返回 accepted / ignored / deepen 三种裁决；
在黑板里它们**不是三种文案，而是三种图操作**：

| 裁决 | 黑板动作 | 为什么 |
|---|---|---|
| `accepted` | 落一条 `verified` Fact（带 `meta`），并 `--type satisfies` 指向 goal | 「结论必须挂在图上」门禁（§4）；评审过的结论才能满足目标 |
| `ignored` | 同样落一条 `verified` Fact（"这条被证伪/不收"） | 一条已证伪的 Fact 会阻止后面所有人重跑这条路 |
| `deepen` | **不落 Fact**，派生 1~N 条 Step（带 `derived_from` 指回线索 Fact） | 打回深挖 = 下一轮探索方向，正是 `reason` 的产出形态 |

Fact 的 `meta` 是可选的，但 SRC 类结论**建议写全** —— 它是机器可查的判据，
写在散文里就得靠正则抠（会抠错、会漏）。
**注意 `phase` 是应急专用词表**（detect/triage/…，没有 `verify`）：
SRC 结论不要往 `phase` 里塞「verify」，那会直接写不进去（`--phase` 会拒）。
doctor 按 `verdict ∈ {accepted,ignored,deepen}` 或 `severity` / `proof_kind` 是否存在来识别 SRC 结论。

| 键 | 取值 | 用途 |
|---|---|---|
| `verdict` | `accepted` / `ignored` / `deepen` | 三裁决；`deepen` 的 Fact **不许**当 solved 依据（doctor 判死） |
| `action` | 建议填 `verify` | 可选；`action=verify` 表示这是复审结论（且 `action` 本身是自由文本，不占用 `phase`） |
| `severity` | `critical` / `high` / `medium` / `low` / `info` | 定级 |
| `proof_kind` | `sentinel` / `authz_diff` / `idempotent` / `side_read` / `weak` / `none` | 写/删/改类的**无害证据类型**；`weak` 或缺 = 半成品，doctor 提醒 |
| `target` | 资产标识（域名 / URL / 主机） | 跟其他目标对账、通杀派生 |
| `repro` | 一句可重放的命令/请求 | 保证证据可复现 |

### 通杀（一打一片）：`fgs swarm`

「一条坐实的结论 + 一批同款目标 → N 条并行 Step」。这是 AutoHunter 的"通杀 Hunter"在黑板里的形态：
**判据（算不算通用缺陷）留给人/主会话，批活交给黑板** ——
让 Agent 自己决定"要不要通杀、通杀多少"正是失控的来源。

```bash
fgs swarm <pid> --from f003 --targets a.edu.cn,b.edu.cn --dry-run   # 先预览，不落盘
fgs swarm <pid> --from f003 --targets-file targets.txt \
    --text "在 {target} 用同一 payload 验证，各自留响应证据"          # 真派生
```

- 源必须是 **`verified`**（未坐实的拿去复制 = 把不确定性乘以 N）；
- 每条都带 `derived_from` 边指回源 Fact（图上一眼看得出这一片从哪来）；
- **幂等**：同文本 Step 不重复建；**默认上限 5，硬上限 20**（想超出要 `--force`）；
- 每个目标**各自取证**，不许拿 A 站的响应证明 B 站。

---

### 三件套：`fgs verify` / `fgs gate` / `fgs cost`（能不能报，机器说了算）

这三条是 2026 国内复盘里冠军队伍的共性（grounding 门槛 / 统一 Gate / 成本锚），在本仓的形态：

| 命令 | 契约 |
|---|---|
| `fgs verify <pid> <fact> --from <文件>` | **逐字溯源盖章**：`--claim`（不给就从正文抠 flag 形状）必须 **byte-exact** 出现在证据里，否则退 **3**；通过则盖 `meta.verified_by=verbatim` + 记 `provenance.evidence` / `meta.repro`。证据来源三选一：`--from` 文件 / `--text` / 管道（stdin） |
| `fgs gate <pid> [--json]` | **统一门禁**：一次过完 ①solved 依据是 verified ②逐字盖章 ③授权范围 ④outbox 结算 ⑤**复审结论取「时间最新」的 verdict**（`metas` 排序按 `meta.time`→`updated_at`→`created_at`，**不按字典插入顺序**）；`deepen`/`ignored` → 退 **3 = 不能报** |
| `fgs cost <pid> [--json]` | **成本画像**：步数 / 已完成 / verified / 证伪数 / 墙钟时长 / `verified÷done` 密度 + 边际递减提醒；时间线取**所有节点**的 `created_at`∪`updated_at` 且**过滤解析失败的垃圾串**（否则 `max()` 会在 `None` 上比大小 → TypeError） |

**读外部路径的硬规矩**（`board/fgs.py: read_capped()`）：`verify --from` / `publish --op-file` /
`swarm --targets-file` 收的都是「外面给的路径」——**只认普通文件**（目录 / `/dev/zero` /
`/dev/urandom` / FIFO / socket 一律**快速拒绝**），且**上限 32MB**（`PI_FGS_MAX_READ_BYTES` 可调）。
理由：2026-09-29 一晩三次 `--from /dev/zero` 类输入把 8G 的 WSL VM 打到 global OOM，
整台发行版连 pi 一起被 SIGKILL；护栏必须落在工具里，不能靠「记得别喂」。
回归：`board/tests/test_read_guards.py`（16 条）。

### 简报：`fgs brief`（派活 / 重启的唯一投喂口）

| 形态 | 契约 |
|---|---|
| `fgs brief <pid> [--step sNNN]` | **任务简报**：某一步是什么 + 该步相关的 verified Fact + 该步相关但**未坐实**的 + 这一片**已证伪的路线（不要再走）** + 关键词命中的 references 指针。省略 `--step` 时按 `fgs next` 选一个可认领步；无可认领步就只报调度状态 |
| `fgs brief <pid> --restart` | **pass@k 重启简报**（2026-10-06，外部证据支持）：给一个**零上下文**的新执行者。固定四段：**题面/目标** → **★已证伪的路线（不要再走）** → **已坐实** → **知识指针**，再加每一步的「试过什么」账（只给 status+摘要，不给过程）+ **重启纪律**。**不依赖 open step**（卡住时正是没 step 可认领）。读只读，不改图 |

为何把「重启」单列一档：2026 在 XBOW（30 道真实 Web）上的实测是 —— 同一套 agent 多跑，
**best-case 都是 19/30**（GPT-5；claude-code 也是 19/30），但「三次都解出」从 12 → 14 → 16。
也就是说**多跑买的是「一致性」，不是「更强」**（arXiv 2605.21497）。本仓原先的 `fgs brief` 只服务
「认领某步」，卡住时人只能手拼上下文 → 要么不重试，要么把失败轨迹一起背着走。
取舍与不采纳清单（含「加厚架构换不来峰值」「逐行 Evaluator 会互踢死循环」）见 `docs/参考-外部CTF-agent-可借鉴.md`。

---

## 5. 落盘格式

```
board/
├── fgs.py                      ← 内核（唯一写入者）
├── schema.md                   ← 本文件
├── tests/test_outbox.py        ← 投递终态的回归测试
└── projects/<project_id>/
    ├── graph.json              ← ★ 真相源（原子替换，带 revision）
    ├── log.jsonl               ← 审计流水（append-only，只给人看，不参与语义）
    ├── outbox.jsonl            ← Worker 待校验的写入（pending）
    ├── deliveries.jsonl        ← 投递台账：applied / rejected / action_required / duplicate
    ├── outbox-rejected.jsonl   ← 被拒条目归档（--force-partial 时不再静默销毁）
    └── .lock/                  ← 跨进程写锁（mkdir 原子性；/mnt/d 上 flock 不可靠）
```

- `graph.json` 是**唯一真相**。流水、outbox、台账都不是（ADR 0006：存带版本号的语义状态，不存事件账本）。
- **投递台账不是图的存储格式**（ADR 0035）：它只回答「这次写入送到了没、为什么被拒」，
  不参与语义推理 —— 图上永远不会长出「投递」节点。
- 每次写入 `revision` +1；整体重建后 `os.replace` 原子生效，不会留下半写状态。
- **给 Worker 的快照是拓扑完整的**（`fgs.py snapshot`）：不裁剪成"相关子集"。
  裁剪会让 Worker 以为自己看到的是全部，从而重试已知失败的路线。

### 写锁：所有写命令都必须套（`board/tests/test_locking.py` 守这条）

`fgs` 是单写者模型，但**"单写者"指的是主会话，不是"没有并发"** ——
同一秒发两条写命令（我自己就习惯并行发 bash）是常态。

所以有一条硬不变量：

> **任何含 `save(` 的函数，必须整体套在 `with Lock(proj_dir(pid))` 里。**

为什么是"整体"：写是**读-改-写**三步（`load` → 改内存 → `save`），只锁 `save` 没用 ——
两个进程都先读到旧图，后写的那个会把先写的覆盖掉，而且**不报任何错**。

实测踩过：14 处 `save()` 里只有 5 处套了锁，`fact-add` / `hint` / `step-add` / `relate` / `apply`
全是裸写。修完 8 个函数后，8 条并发 `fact-add` 才 8 条都落盘。

两个实现细节：

- **`Lock` 必须可重入**：`cmd_init` 会在自己的锁里调 `_set_goal`，而 `_set_goal` 也要锁 ——
  不可重入的话第二次 `mkdir` 必然失败，等 15s 后报"拿不到写锁"，**自己把自己锁死**。
- **`cmd_init` 必须先建目录再抢锁**：锁文件是 `<项目>/.lock`，目录还不存在时 `mkdir` 直接
  `FileNotFoundError`。

### `fgs doctor`：查**数据**的完整性（`tests/*` 只查代码）

```bash
fgs doctor <pid>      # 只读；退出码 1 = 发现问题
```

它逐条验的不变量：schema 版本、节点 id 前缀与类型一致、悬空边、重复边、端点矩阵、
`done` 的 step 有没有 `produced` 的 Fact、`claimed` 的 step 有没有 `claimed_by`、
active goal 是否唯一、`solved` 依据是不是 `verified`、`confirmed` 有没有 flag、
outbox 与台账能否逐行解析、残留 `.lock` 与半成品临时文件。

**校验器本身也要被校验**：往一个 scratch 项目里注入 7 种已知问题（悬空边 / 矩阵违规 /
claimed 无主 / 双 active goal / solved 用了 inferred / confirmed 无 flag / outbox 坏行），
doctor 全部抓到。

### 注意力预算的**分层清洗**（三保三弃）+ 预算上限

> 来源：两届腾讯云黑客松（TCH）决赛队伍的横向对比（见 `docs/参考-2026国内赛事与大佬经验.md`）：
> 绿盟用 **30k / 100k 两档阈值**做三层上下文清洗，并区分「可丢弃信息 / **必须保护的工具输出**」；
> 另一支队伍把 `Path Map + Global Memory` 用来记录**失败方向**（对应本仓 `fgs brief`）。
> 作者归纳一句话：**「状态必须成为系统资产，而不是模型回忆。」**

快照超过 16k/32k/64k 时 `fgs snapshot` 会**直接给出归并候选**（非 verified 事实 / 无人引用的孤点 / 必须保护的清单）。纪律：

| 必须保护（**永不压**） | 可归并 | 可丢弃 |
|---|---|---|
| 含 **flag / 凭据 / 口令 / token / hash** 的 `verified` 事实 | 同一路线的多条 `inferred`/`unverified` → 压成一条结论 | 已被 `supersedes` 取代的旧结论 |
| `solved` 所依据的那条事实（它是结论本身） | 已被证伪的同类路线 → 合并成一条"死路论证"，**但必须保留「试过什么 + 为什么失败」的最小可复述形式**（见下） | 纯过程性噪声（重复的探测记录） |
| 与 goal 直接相连的 `satisfies`/`evidences` 边 | 无人引用的**孤点** → 挂到 goal 或淘汰 | — |

> **归并 ≠ 抹掉失败轨迹**（2026-10-02 加，两条独立证据）：
> ① **Manus**《Context Engineering for AI Agents》：上下文应当 **append-only**，不要修改历史动作与观测，
> **错误轨迹要保留**（"Error recovery is one of the clearest signals of true agentic behavior"）；
> ② **阿里云 CTF 2026 承影 Agent（26 题 0 解）** 的事后归因原文：*"承影 Agent 会在同一道题里，
> 反复执行已经失败过的操作。**不是它笨，是我让它忘了。**"* —— 粗糙压缩是那次的直接死因。
> 详见 `docs/参考-2026赛事与Agent-第三轮.md` §二②。
>
> **判据（可复核）**：把一条死路 Fact 交给没看过原始记录的人，他**必须能答出「试过 X、因为 Y 失败」**；
> 只能答出"已证伪"三个字的，视为**归并过度**，回退。**归并的是结论条数，不是失败信息量。**

**预算上限（每轮/每次探索）**——确定性的事交给框架，别靠模型自觉：

| 项 | 建议值 | 落地方式 |
|---|---|---|
| 单次 `explore` 工具调用 | **≤ 25 次** | prompt 纪律（`prompts/fgs-explore.md`）；超了就进 conclude 落一条事实 |
| 单次任务时长 | 由双阶段超时控制 | 第一阶段超时 → 同 session 渲染 `*-conclude.md` |
| 单条命令 | `timeout 60`（重活 `PI_WINRM_TIMEOUT` / `quiet req --qps`） | 工具层硬超时 |
| 学到步骤上限 | 每个 project 连续 3 个 Step 无 verified 产出 → 换攻击面 | `fgs next` 已提示 |

---

### 快照的**语义哈希**（ADR 0004：同一语义状态 → 相同字节 → 相同哈希）

`fgs snapshot` 输出的头部带一个 `hash=…`（`--hash` 只打印它，给脚本用）：

```bash
fgs snapshot <pid> --hash      # → d116b994357d06a6
```

**Worker 拿它判断「我上次读到的图，和现在读到的，是不是同一份」** ——
不用重读全文比字符串，比一个哈希就够了。

要做到这一点，序列化必须是**确定性**的：

| 要求 | 做法 |
|---|---|
| 字段顺序无关 | `sort_keys=True` + 紧凑分隔符（canonical JSON） |
| 关系顺序无关 | relations 按 `(from, type, to, reason)` 排序后再序列化 |
| **非语义字段不进哈希** | 排除 `revision`（「第几次写」的计数器，不是语义） |
| 字段是白名单 | 只出 `text/status/claimed_by/confidence/meta` —— 不出 `provenance`、时间戳、内部 id |

于是 `fgs checkpoint` 这类**只动元数据**的写入会让 `revision` 变、**哈希不变** ——
这正是要的信号：图在语义上没变，Worker 不需要重新推理。

> 回归测试：`python3 board/tests/test_snapshot.py`（14 条断言，含「只动元数据哈希不变」这条核心）。

---

## 6. 快速上手

```bash
F="$PI_LAB_ROOT/board/fgs.py"

python3 $F init mytask --kind ctf --goal "拿到 flag 并提交成功"
python3 $F next mytask                    # 该干什么？唯一答案（含首灯身份检查）
python3 $F lead mytask                    # 看/续期调度者身份（多窗口防双 dispatcher）
python3 $F snapshot mytask                # 给 Worker 的完整快照
python3 $F show mytask                    # 人读视图
python3 $F confirm mytask --flag "LHFLAG{...}"   # 人确认 flag → 开 WP 闸门
python3 $F log mytask --tail 20           # 审计

# Worker 侧（只能用这个）
python3 $F publish mytask --by w1 --op '{"op":"fact","text":"...","confidence":"verified"}'
# 主会话侧
python3 $F outbox mytask                 # 待办 + 投递台账（谁被拒了、为什么）
python3 $F apply mytask
```

`--json` 加到 `show` / `snapshot` / `next` 上给程序读。

## 6.5 终局兜底：`deadline` / `finish-all`（2026-11-02）

限时赛到点那一刻，「已拿到但还没交出去」的 flag 是最容易白丢的分。
全仓原先 `grep deadline board/fgs.py` **零命中** —— 没有任何机制保证这一下。
（来源：sapper-v2 `watchdog.sh:423-459` 的 `finish_all`；赛事实测它有 9 题在
得分后 20+ 分钟才清账，全靠这层不白丢分。）

```bash
python3 $F deadline   <pid> --at "2026-11-02T21:00:00+08:00"   # 设（也可 --at +90m）
python3 $F deadline   <pid>                                    # 看还剩多少
python3 $F deadline   <pid> --clear                            # 清
python3 $F finish-all <pid> [--json]                           # 终局收口五步
```

**两条设计铁律（刻意避开参考项目的两个坑）：**

1. **deadline 单源**：只在 `--at` 时解析**一次**落盘，之后所有读者只读不算。
   参考项目在 `entrypoint.sh:148` 与 `watchdog.sh:48` **各算一次** END_AT，
   守护进程一重生截止时刻就往前漂、`budget_left` 虚高。这里没有第二处计算。
   → `--at` **必须带时区**：容器 UTC / 宿主 CST 差 8 小时，猜错就是整场偏移。
2. **finish-all 幂等可重入**：状态不变时**不写盘、不涨 revision、不刷审计**。
   参考项目四个脚本 `trap` 零命中（`docker stop` 时收口根本不执行），
   所以这里不依赖任何信号：什么时候跑都安全，反复跑无副作用。

**五步顺序**（反了会「边收边写」）：

| # | 步骤 | 自动化程度 |
|---|---|---|
| 1 | 停写：列出仍 `claimed` 的 Step | **需人工**（fgs 杀不掉子代理） |
| 2 | 收投递：未结算 outbox | 给命令（`apply` / `outbox --withdraw`） |
| 3 | 扫 flag：图上 verified 的真 flag 形状结论 | ✅ **全自动**（复用 `_is_real_flag`，占位符/样例串不算） |
| 4 | 释放平台实例 | **需人工**（本机无平台 API） |
| 5 | 落审计 `finish_all` | ✅ 自动 |

> ★ 诚实边界：第 1、4 步只出清单不动手。假装能自动做，比不做更危险 ——
> 到点你以为交完了，其实一条没交。
> `fgs finish-all` 的 `flags_unconfirmed` 非空 = **收口未完成**，逐条去平台核对回执后
> `fgs confirm <pid> --flag '…'`。⚠️ 没确认 ≠ 没拿到。

### 退出码（脚本要按它分支）

| 码 | 含义 | 该怎么办 |
|---|---|---|
| 0 | 成功 | —— |
| 1 | 用法错 | 看 `--help` |
| 2 | **状态冲突** | step 被抢了 / 项目不存在 / 锁拿不到 → 换路线，**不要重试同一个** |
| 3 | **校验失败** | 不合法节点或关系 / confidence 不够 → 修内容，别绕过 |

## 7. 主会话监督循环：`bind` / `workers` / `steer`（2026-10-05）

派完 worker 之后，主会话不空等 ——「谁在跑、卡没卡、该转向还是回收」都从图上读。
这是 AGENTS.md §六·七「主会话 = 调度者 + 统一记忆池」的落地工具。

```bash
python3 $F bind    <pid> <step> --agent <子代理会话id>   # 派完立刻绑（地址簿；**不续租**）
python3 $F workers <pid> [--json] [--ttl N] [--stale-only]
python3 $F steer   <pid> <step> [--record]               # 生成转向简报 → 喂 steer_subagent
python3 $F step-reclaim <pid>                            # 僵尸回收（顺带清旧会话绑定）
```

- **verdict 三态**：心跳用掉 <60% = 🏃 `active`；≥60% = ⏳ `stale`（该 steer）；>TTL = 💀 `zombie`（回收）。
- **转向计数**：`steer --record` 写审计；同一 Step 转向 ≥2 次仍无产出就别再 steer 了
  （换 worker / 拆 Step / 换面），否则“转向”会变成新的烧钱循环。
- **绑定生命周期**：`claim` 清旧绑定，`release` / `reclaim` 也清 —— 会话地址永不指向死 worker。
- **bind 不是心跳**：它**不写 `updated_at`**（否则一个死掉的 worker 会被一次 bind 伪装成
  “刚活动过”，僵尸回收被骗过去）。
- `fgs next` 在 `wait` 时会主动指路：`fgs workers <pid>` 看持有者/租约/该不该 steer。

## 8. 平台回执闭环 / 多旗剩余 / 槽位账（2026-11-03，P1-1/3/4）

三件套补上「抢分」链路最后一段：账本 → 真提交 → 回执落账 → 剩余旗 → 槽位该关谁。

```bash
python3 $F flag-auto  <pid> --code <unique_code> --flag 'FLAG{x}' [--id ID] [--timeout 60]
python3 $F flags-left [--json] [--timeout 60]
python3 $F slot      <pid> [--cap 3] [--warn-min 20] [--stale-min 40] [--json]
```

### `flag-auto`：唯一新增的一跳是「真去问平台」

语义**严格等于**人工做三步，账本记录逐字一致：

```bash
fgs flag-record --flag F      # 先落盘（崩溃安全点）
tsec submit <code> F          # 真去问平台
fgs flag-submit --reply '…'   # 整份回执存进账本
```

- **判定唯一来源不变**：`_judge_reply`，即 `bool(reply["correct"])`。**rc 不是判定** ——
  `tsec submit` 判错时也返回非 0，拿 rc 当判定就会把正确答案写成 rejected（清单 B2 的同型错误）。
- ★ **`code=duplicate` 记 accepted**：平台说「这条以前提交成功过，不再加分」= 事实上的已确认。
  记成 rejected 的话，`finish-all` 的「未确认 flag」清单会一直报它，终局收口时误导人。
- ★ **拿不到回执 → `error` + rc=1，绝不静默**：断网 / 缺 token / 命令不在 / 超时 / 非 JSON，
  全部显式失败并原样保留 stdout 前 2000 字符。**「假装提交了」比不做更危险。**
- 已 `accepted` 的 `flag_id` 不许重复提交（要重投先换 `--id`）。
- ⚠ `PI_FGS_TSEC` 按**命令行**解析（允许 `"python3 /path/fake.py"`）：
  Windows 不能直接执行 `.py`，只当单个可执行名会让所有非 PE 实现起不来。

### `flags-left`：多旗题还剩几面（只读，不动 `solved`）

`§4「solved 与 confirm 是两回事」` 里的 `solved` 仍是二态 —— 本命令**不改它**，
只读 `tsec list` 的 `correct_flag_count` / `flag_count` 算剩余，让「拿了一面但还有三面」显性化。

- 字段缺失 → 记 `unknown` 并单独列出，**绝不当 0**（当 0 会把可续打的题漏掉）。
- 拿不到题单 → 明确说「不知道」，**不是「没有剩余旗」**。这两句话混了就是静默丢分。

### `slot`：槽位账（只出建议，不代劳）

并发上限 3（Q12）之后，「该关哪个」比「还能不能开」更值钱。按「已跑时长 + 有无产出」排序。

- **不代劳的边界写进输出**：`fgs` 杀不掉 DSH 子代理，也没有平台关闭 API ——
  只给「该关哪个 + 具体命令」，与 `finish-all` 第 1/4 步同一条原则。
- 免责声明**不依赖分支**：空槽位时也说清「不代劳」（测试 `test_flag_auto.py` 锁这条）。
- 终局紧迫度：设了 `deadline` 且剩余 < 15 分钟时，超过剩余时长的槽位直接标「立刻停」。

---

## 9. 兄弟图 / 同前缀识别（2026-11-03，P2-2，Q16=A）

> 背景：`LADDER_ROWS` 的 L0 档从落地起就写着「前缀与已解题相同时跳过侦察、直接用兄弟图简报」，
> 但那句话**一直没有任何实现**（全仓 grep「兄弟图」只有它自己）。用户 Q16 选了 **A=实现**，
> 所以这一节把"实现"写成契约；验收在 `board/tests/test_sibling_graph.py`（30 条断言）。

**做什么**：`fgs brief` 在多出「兄弟图」一节 —— 把**同前缀**的题已 `verified` 的结论列出来，
让 worker 跳过重复侦察。

| 项 | 规定 | 为什么 |
|---|---|---|
| 前缀怎么取 | URL → `scheme://host[:port]/第一段路径`；非 URL 原样；空/非法 → `None` | 同站点不同端点常是同一道题的不同入口；不做文本相似度（与 R4 同一纪律） |
| 数据源 | 图顶层 `target`（`fgs kickoff --target` 落盘） | 过去 `--target` **只打印不存**，兄弟图无据可判 —— 这是本项的前置修复 |
| 只搬什么 | **仅 `verified`**，且非已证伪（`DEAD_RX` 不命中） | 未坐实的搬过去 = 把不确定性乘以 N 道题（与广播 R1 同理） |
| **不搬什么** | ① `inferred`/`unverified` ② 已证伪结论 ③ **步骤收尾套话**（`done`/`完成`/`ok`…） | ②跨题直接说"这条路不行"会误杀别题里可能成立的路；③`step-conclude` 每收一步都落一条 verified Fact，不滤就把真结论挤掉 |
| 判不出怎么办 | 一律**当不同族**（`_task_prefix` 返回 `None` 即不匹配） | 宁可漏判（少省点时间），不可误判（把人带沟里） |
| 只读性 | 看兄弟图**不得**改动兄弟项目的 `graph.json` | 测试 S8 逐字节比对锁死 |
| 可见性 | `fgs show` 打印目标资产 + 算出的同族前缀 | 否则"为什么没串上"没法排查 |

> 与 §8 的 `broadcast` 的区别：`broadcast` 是**写**（把结论放进别人的图，走 batch + 可回滚）；
> 兄弟图是**读**（只在简报里提示，源图一个字节不动）。两者判据也不同：`broadcast` 按
> tool/proof_kind/vuln 三元组判同族（R4），兄弟图按**题面前缀**判同一道题。

