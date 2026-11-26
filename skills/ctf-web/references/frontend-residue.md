# 前端残迹 → 攻击面推演（自写版 · 先推演再爆破）

> ## 定位
> 用于**黑盒 web 题**、尤其是**小应用**（一把 PHP/Flask 写的题、只有几页）。
> 这里的核心判断是：**小应用的攻击面是它自己的词汇表（个位数），不是通用字典（万位数）。**
> 跑 `ctfsurf` 把词汇表抠出来 → 按 P0/P1 清单逐条试 → 字典永远排最后。
>
> 适用条件：拿到了页面源码（HTML/CSS/JS 任一部分）或能先 curl 一页。
> 不适用：纯静态站、大型框架（接口在打包产物里，走 `urlfinder`/JS 逆向）、无任何前端产物的协议题。

## 核心原理（一句话）

**出题人写的 CSS / 表单 / 题面文案，是他自己业务逻辑的泄漏 —— 他把实现功能时用完就关掉的界面留在了样式表里。**

一个功能「存在但不对你渲染」，就会在 CSS 里留下**定义了却没人用**的类名。这类**悬空类名 = 未露出功能的指纹**：

| 悬空类名（正则） | 业务含义 | 对应攻击面 |
|---|---|---|
| `preview / draft / pending / temp` | 预览态 / 草稿态 | 未发布对象只给管理员看 → **越权读(IDOR) / 存储型 XSS 打 admin 审核** |
| `admin*` | 后台/管理面 | 管理员专属视图 → 找它的端点与**鉴权判定点** |
| `action / toolbar / controls / btn-group` | 操作区/按钮组 | 页面上没这区域 = **还有一组操作**（改状态/删除/发布） |
| `upload / drop / attach / file / avatar` | 上传面 | 类型/后缀/内容绕过 → 上传即 RCE 或 XSS |
| `notice / success / error / alert` | 提示分支 | 同请求的不同分支 → **错误 oracle**（拿报错/路径/版本） |
| `modal / dialog / confirm` | 交互/确认面 | 隐藏 action（删除/审批/改状态）常只在这里 |
| `badge / label / status` | 状态字段 | 改状态型**逻辑越权**（未审 → 已通过） |
| `pager / cursor / load-more` | 分页参数 | `page/offset/cursor` → 注入 / 越权枚举 |
| `export / download / backup / report` | 导出面 | 常绕过鉴权 / 路径穿越 / 命令注入 |
| `hero/card/board/grid/shell/panel`（纯布局） | — | ★ 大概率无攻击面，**别浪费时间** |

另外两类残迹同样值钱：

1. **表单里的 hidden 值 = 已在用的 action 取值**
   例：`<input type="hidden" name="action" value="login">` → 说明这套应用**用一个 `action` 参数分流业务**。
   那么别的取值（`preview` `draft` `publish` `approve` `post`…）是**个位数枚举**，配同样几个参数名直接试 —— 不是 2 万条字典。
2. **题面中文文案里的业务动词**
   「留言 / 值班 / 归档 / 审核 / 维护」不是装饰，是出题人**给业务动作起的名字** → 直接当 `action`/参数值试（含拼音）。

## 工具

```bash
ctfsurf work/<pid>/index.html work/<pid>/style.css      # 分析已存产物（离线，不发请求）
ctfsurf --dir work/<pid> --json > work/<pid>/surf.json  # 目录里所有 html/css/js
ctfsurf --url http://target/ --save work/<pid>/index.html  # 只读 GET 一次再分析
ctfsurf --selftest                                     # 离线自检
```

输出：表单词汇表 / 悬空类名+语义 / 题面动词→action 候选 / JS 路径 / **P0·P1·P9 攻击面清单** + 反爆破闸门。
`--url` 只发**一次 GET**，不爬站、不写操作；文件读取走 `read_capped`（拒 FIFO/`/dev/zero`/符号链接/>8MB）。

## 反爆破闸门（纪律）

`ctfsurf` 在端点/表单 ≤6 时会直接打印闸门。人也要守：

- **小应用开字典前，必须先把 P0/P1 清单跑完**，并给自己定**请求上限**（例：单一攻击面 ≤300 请求）。
- 一条攻击面**连续两轮无新事实**就换面（换面 = 换入口/参数/协议层，**不是换 payload 细节**）。
- 判定式**写死**（例：`302 或 200 且不含固定失败串`）；`502/503/504` 一律**重试**，不算结果 —— 并发打靶机会间歇 502，把它当命中是假阳性。

## 案例：msgboard（2026-10-05，真实复盘）

| 我们做了什么 | 结果 |
|---|---|
| 目录字典 27,712 条 + 备份后缀 1080 组合 + `.git/.svn` 泄露 | 全 404 / 基线 |
| 弱口令全表（admin/tom/eve/ops × 1776 + 更多用户） | 只拿到 `guest/guest123`、`tom/tom12345`（普通用户） |
| sqlmap + 14 种注入 + 类型混淆 + 参数名枚举 + 30+ gate 绕过技法 | 全负 |
| **通过 `ctfsurf` 复盘同一批产物** | **`.preview` → 预览/草稿态、`.admin-grid` → 后台面、`.actions` → 操作区；表单已有 `action=login`；题面「留言/值班/归档/维护」** |

结论：**从未找到「发布留言」的入口**（board 事实 f009 里“以 guest 会话 POST 唯一标记未出现”正是这个后果）——
而发布功能恰好是「留言板」这道题的**本体**。悬空 `.preview` + `.actions` 直指「发布→草稿/预览→管理员看」这条链。
**2.7 万条字典打的是一个 6 个端点的应用** —— 这就是产出最低的路。

### 干跑自检（证明它不是安慰剂）

`bash work/ctfsurf-dryrun/run.sh`（离线，约 15s）在一个**结构性复刻**的 mock 上跑完整链：

| 步骤 | 结果 |
|---|---|
| `ctfsurf` 推演 | P0 = 「穷举 action 取值（已在用 `login`）」+ 悬空 `.preview`/`.admin-grid`/`.actions` |
| 按 P0 走（`action=login` → `action=preview&id=2`） | **1 次请求命中管理员草稿 → `FLAG_MOCK{...}`** |
| 对照：guest → `/admin.php` | HTTP **302**（门禁没坏；洞在 preview 分支只判登录不判角色） |
| 对照：通用目录字典前 300 条 | 200 命中 **0** 个 |

原文留档 `work/ctfsurf-dryrun/proof.txt`；复盘细节见同目录 `README.md`。

## 坑

- 只 curl 了首页就下结论 → 先 `ctfsurf --url` 把词汇表抠出来（首页往往只是一半）
- 把纯布局悬空类（`.hero-small`、`.panel`）当攻击面 → 语义表已把它们压到最后，别回头去啃
- 悬空类名是**猜测**不是结论 → 必须用一次真实请求**证明该功能存在**（状态码/长度/分支差异），否则只能算 `inferred`
- JS 里的路径要**真的请求一遍**再说“不存在”；`405/501` 与 `404` 含义不同
