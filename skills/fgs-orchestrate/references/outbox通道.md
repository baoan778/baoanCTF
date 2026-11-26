# 子代理的唯一写入通道：Outbox

> 从 `fgs-orchestrate/SKILL.md` 拆出（原文件 >9000 B，`skilllint` 告警）。
> **契约正本仍是 `board/schema.md`**，本文件讲「怎么用」。

## 子代理的唯一写入通道：Outbox

**Worker 不能直接写图。** 它 `publish` 到 outbox，你 `apply` 时校验：

```
Worker  ──publish──►  outbox.jsonl  ──apply(校验)──►  graph.json
                                        │
                                        └─ 任一条不过 → 整批拒绝，图一个字节不动
                                       
                                        ↓ 不论成败，都落一条投递终态
                                     deliveries.jsonl
```

```bash
# Worker 侧（在它的 prompt 里明确要求）
fgs publish $PID --by w1 --op '{"op":"fact","text":"...","confidence":"verified"}'

# 你（主会话）侧
fgs apply $PID                    # 校验并合入
fgs outbox $PID                   # ★ 看台账：哪条被拒了、为什么、第几次尝试
```

这一步是**幻觉防火墙**：子代理编的节点、乱连的关系、抢别人 step 的 conclude，
全在这里被拦下，不会污染全局图。`apply` 退出码 3 = 有人写了不合法的东西 ——
**先 `fgs outbox $PID` 看拒绝原因**，修内容，别用 `--force-partial` 蒙过去。

### 投递终态（四种，缺一不可）—— ADR 0021 / 0023

| 终态 | 含义 | 你该做什么 |
|---|---|---|
| `applied` | 已合入图 | —— |
| `rejected` | 内容非法 | 看 `reason`，让 Worker 改内容重投 |
| `action_required` | 校验过了但被同批坏条目连坐 | 修好坏的，重新 `apply` |
| `duplicate` | 同指纹已 applied 过 | 忽略，图上不会有重复节点 |

**踩过的坑**（首版）：拒绝只打在 stderr，`log.jsonl` 里 0 条记录；`--force-partial`
还会把被拒条目**静默销毁** —— 等于"这条投递从没发生过"，下一个人必然重新踩。
现在四种终态都进 `deliveries.jsonl` + 审计流水，重复投递也不会二次写图。
回归测试：`python3 board/tests/test_outbox.py`。

