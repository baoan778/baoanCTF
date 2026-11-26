# 记录 / 时间线 / IOC（从 SKILL.md 搬出）

> 从 `SKILL.md` 搬出的细节（原文未改）；入口见 SKILL.md。

## 5. 记录：写进黑板 `fgs`（不另起 WAL）

**[by-event/index.md](by-event/index.md) 等文档里的 `note.py`（上游技能包自带，**本机没有**） 是上游技能包的 WAL；
本机**一律用 `fgs`** —— 图是唯一共享真相，不要维护第二份会话状态。

| 上游 WAL 动作 | pi-lab 等价物 |
|---|---|
| 初始化（事件名/范围/规则） | `fgs init <pid> --kind pentest --goal "…"` + `fgs hint <pid> "范围"` |
| `--set incident_name=…` | goal / hint 正文 |
| `--action collect --evidence …` | `fgs fact-add <pid> "…" --confidence verified --evidence "<命令+输出>"` |
| `--ioc-ip/--ioc-domain` | 同上，正文写成 `类型 / 值 / 出处 / 时间 / 置信度` 一行 |
| `--timeline "…"` | 同上，正文**以时间戳开头**（脚本会自动抽成时间线） |
| `--flag "问题=答案"` | `fgs fact-add … --confidence verified` → `fgs solved` → **人** `fgs confirm` |

```bash
fgs fact-add <pid> "2026-09-01T03:12Z GET /api/export?file=../../etc/passwd → 200,1834B（access.log:412）" \
  --confidence verified --evidence "grep -n 'export' access.log 输出第 412 行"
fgs next <pid>          # 含首灯身份检查：多窗口防双 dispatcher
```

### 结构化记录：写一次，报告 / 时间线 / IOC 表都免费

应急报告、时间线、IOC 表**本质是同一批事实的三种投影**。属性只写在散文里，生成报告就得靠正则抠
（会抠错、会抠漏）。带上元数据字段，`ir-report.py` 直接用，不用猜：

```bash
fgs fact-add <pid> "异常高 CPU 进程 xmrig，外联 pool.bad.xyz:3333" \
  --confidence verified --evidence "ps -w -eo pid,lstart,cmd --sort=-%cpu 输出第 2 行" \
  --phase triage --action collect --tool ssh --target srv-01 \
  --time "2026-09-01T03:12:00Z" \
  --ioc "域名=pool.bad.xyz" --ioc "IP=203.0.113.77" --ioc "文件=/tmp/.x/xmrig"
```

| 字段 | 影响哪块产出 |
|---|---|
| `--time` | **时间线**（没有才退回正文时间戳 / 记录时间并打 `*`） |
| `--ioc 类型=值`（可重复） | **IOC 表**（没有才退回正文正则；表里会标「来源 = meta / 正文正则」） |
| `--phase` | 报告「按应急阶段」分组 |
| `--action/--verdict/--tool/--target` | 证据与动作的可复现性 |

**纪律**：meta **不改变置信度** —— 带 meta 的 `inferred` 依然是 `inferred`，照样不许宣告 solved、
照样不进报告的 IOC 表。全部字段都可选，一个不写也能跑。

多轮/卡住时按 `skills/fgs-orchestrate/SKILL.md` 走（bootstrap/explore/reason + outbox）。

---

## 6. IOC 与时间线

### IOC 必须结构化（否则交出去没人能用）

```tsv
类型	值	出处（命令 + 文件:行）	时间(UTC)	置信度
域名	c2.example[.]xyz	ss -antp 输出 / access.log:412	2026-09-01T03:12:00Z	verified
文件	/tmp/.x/a.sh sha256=ab12…	sha256sum /tmp/.x/a.sh	2026-09-01T03:05:00Z	verified
账号	svc_backup (uid=0)	/etc/passwd:44	2026-09-01T02:58:00Z	verified
```

**置信度只有两档可用**：`verified`（原始输出逐字可见）/ `inferred`（从输出推的，写明怎么推的）。
`unverified` 的怀疑可写进报告，**不能进 IOC 表**。

### 时间线归一

```bash
# 每源先转成 "<ISO8601 UTC>\t<来源>\t<一句话>\t<出处>"，再合并排序
{ ...; } | sort > timeline.tsv
```

- **统一到 UTC**，并在报告里写明原日志时区（本地时区偏差是最常见的还原错误）
- 每条必须带**出处**（文件:行 / 命令），否则时间线不可复核
- 时间戳精度别混用（秒级日志与毫秒级事件排一起要注明）

### 自动生成报告与时间线 / 导出 IOC 表

```bash
python3 skills/ir-response/scripts/ir-report.py <pid>            # 读黑板图 → 报告 + timeline.md
python3 skills/ir-response/scripts/ir-report.py <pid> --out reports/ir-report.md
# 交出去能直接进封禁流程的那张表（只看后缀，不猜格式）：
python3 skills/ir-response/scripts/ir-report.py <pid> --ioc-out reports/iocs.csv    # 也支持 .json / .md
```

模板在 `skills/ir-response/templates/`。脚本的原则：

- **不编时间**：`meta.time` → 正文时间戳 → Fact 记录时间（最后一档打 `*` 标记「这是记录时间，不是事件时间」）；
  带偏移量的时间会**换成 UTC**（`12:00+08:00` → `04:00Z`），否则时间线按字符串排会错 8 小时；
  没写时区的**不替你猜**（原样保留，自己去确认）
- **处置四段自动归位**：Fact 带 `--phase contain/eradicate/recover/lesson` 就进报告对应段，
  没写的段保留「按现场填写」占位符 —— 结构化记一次，报告就少写四段
- **不升级置信度**：`inferred`/`unverified` 的 Fact 单列「待坐实」，**不进 IOC 表**
- **标明来源**：IOC 表最后一列写清这个值是来自 `meta` 还是 `正文正则`，方便复核
- **一条 IOC 一行**：同一条被多条 Fact 提到就合并（出处并列、首见/末见取极值、`--tool` 并列）；
  CSV 用 `utf-8-sig`（Excel 双击中文不乱码），JSON 带 `count`/`project`/`generated_at`；
  不认识的后缀**直接拒**（fail-closed）—— 宁可让人显式说要什么，也不静默写一份没人能用的文件

---

