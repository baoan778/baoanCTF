# WP 流程（旗子确认之后）—— 细节与模板

> **为什么单独成文**：AGENTS.md 只在**确认 flag 之后**才需要这段；平时每轮都带着它纯属浪费。
> 入口（AGENTS.md §七）留「两道门 + 用户特权 + 产出路径 + 指针」，模板与格式铁律在这里。

## 1. 两道门（缺一不可）

| 门 | 是什么 | 谁下的 |
|---|---|---|
| 1. `fgs solved` | agent 认为解出来了（flag 已拿到且证据链 `verified`） | 我 |
| 2. **`fgs confirm`** | **人确认 flag 是真的**（看过平台回显） | **用户** |

- `solved` 只是**我自己的判断**；WP 是**对外产物**，不能建立在自评上。
- **没解出的题不起 WP**（除非用户明确要一份失败记录）。
- **一题只起一次**；起点是「用户确认」而不是「我觉得对了」。

## 2. ★ 用户特权（2026-10-06 明确指令，别再回头问）

> **用户直接说「写 WP / 生成 WP / 帮我出个 WP」= 门 2 自动满足**，等同于他已确认 flag 正确。
> （用户原话：「我让你生成 wp 肯定是确认正确了」）

此时的动作：**直接 `fgs confirm <pid> <fact> --by operator` + 起 WP 代理** ——
不要再说「未确认」「待平台回执」之类的话，也不要把这件事当成阻塞。

## 3. 起法（终端默认；派完立刻继续解下一题，不等、不轮询）

```
subagent({ agent: "worker", context: "fresh", async: true, task:
  "读 /mnt/d/baoan/skills/wp-writer/SKILL.md 并严格按它执行。project_id=<pid>；
   完整任务说明见 /mnt/d/baoan/prompts/wp.md。你只有黑板和 work/<pid>/ 留档，没有我的对话历史
   —— 不够就写「记录缺失」，不要靠猜补圆。最后回：一行回执 + 七行摘要（模板见 skill）" })
```

UI 侧等价：`Agent({subagent_type:"general-purpose", run_in_background:true, prompt:…})`。

## 4. 产出

- `docs/writeups/WPs/<pid>_WP.md` —— 正文（格式铁律见 `skills/wp-writer/SKILL.md`）
  · 其中「失败与调整」一节要**一行一条**（路线 + 判据 + `fNNN`）：那是死路库的原料。
- `docs/writeups/INDEX.md` —— 追一行
- `docs/writeups/手法库.md` —— **只在真有可复用手法时写**（宁缺毋滥：灌水的库下次没人读）
- `docs/writeups/死路库.md` —— **走不通的路线**（同样宁缺毋滥）：由 `board/retrospect.py scan`
  从图上 `verified` 的证伪事实抽候选 → 人 `--merge` 写入（`kind=deadend` 自动路由）。
  它会被 `kb` 索引，并在 `fgs brief` 里单独提示给新 Worker —— **同类题第二道直接跳掉一整条路线**。

## 5. 回执纪律

- 回执**一行**；没有值得提的沉淀就**沉默**。
- 代理只有黑板与 `work/<pid>/` 留档：**记录缺失就写「记录缺失」**，不要靠猜把过程补圆（编出来的 WP 比没有更糟）。
