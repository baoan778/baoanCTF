# 前端接口侦察（api-recon 简化借鉴）

> 历史决策 #30 的落点。上游是 ARTEX 的 `skills/api-recon/SKILL.md`（Phase0-5、两层模型、完成定义、流程硬禁止），
> 这里只搬 CTF 用得上的四层，并把每层绑到**本仓已有的工具**上。

## 0. 两层模型（先分清你在看哪一层）

| 层 | 看什么 | 可信度 | 本仓工具 |
| --- | --- | --- | --- |
| **静态层** | 代码里**写着**的端点、路径、参数名、路由表 | 高（源码即事实），但会被压缩/拼接/条件裁剪藏起来 | `apiharvest` · `ctfsurf` |
| **运行时层** | 页面**实际发出**的请求（含前端算出来的 query、签名、动态 id） | 最高（网络层事实） | `tools/browser.py`（eval 注入 hook） |

两层的分工一句话：**静态层给你靶子清单，运行时层给你真实弹药**。
只做静态 → 会漏掉前端拼出来的参数；只做运行时 → 没进壳之前一个请求都录不到。

## 1. 第①层：静态 harvest（最便宜，先做完再谈别的）

```bash
# 1) 把页面与 asset 存下来（不要整读大 bundle，见下方闸门）
curl -s -o work/<pid>/index.html <url>
curl -s -o work/<pid>/app.js     <url>/static/app.js

# 2) 一键抠端点（api / route / static 分类 + 去重 + 参数名）
apiharvest --dir work/<pid>                 # 已存下来的 asset
apiharvest --url http://target/ --save work/<pid>   # 直接抓 HTML + 同源 script
apiharvest --dir work/<pid> --json > work/<pid>/api.json

# 3) 再把「未渲染功能」捞一遍（悬空 CSS 类 / 表单 action 词汇表）
ctfsurf --dir work/<pid>
```

抠到什么算数：

| 类型 | 例子 | 下一步 |
| --- | --- | --- |
| `api` | `/api/user/list` · `/v2/orders/{orderId}` | 直接打：改方法、去 token、换 id（IDOR） |
| `route` | `/admin/audit` · `/login` | 前端路由 = 后端往往也有同名端点；未登录直访试 |
| `path` | `/download?file=` | 带参数名 → 进第③层做参数逆向 |
| `static` | `/static/logo.png` | 忽略（除非是 sourcemap / 备份） |

### 闸门：主 entry > 500 KB 不许整读

压缩后的主 bundle 常 1–5 MB。**整读进上下文 = 一次吃掉几万 token 且找不到东西**（实测：读进来也读不完）。

- `apiharvest` 对单文件超过 `--big`（默认 512000 B）会打警告 `主 entry 不许整读（闸门）`；
- 正确做法：**按 URL 抓**（`--url`）或**分段**（只抓重点 chunk / 按 sourcemap 定位），
  绝不 `cat main.js`、也不 `grep` 整个 bundle 把命中行全灌回上下文；
- 命中多时先 `sort | uniq -c | sort -rn | head`，上下文只留端点表。

## 2. 第②层：运行时 hook + stub（无凭据进壳）

核心判断：**后端返回什么不重要，重要的是前端还会发哪些接口**。
SPA 在拿不到登录态时会**提前 return**，于是「真业务请求」一个都不发 —— 这正是很多题看起来「只有登录页一个接口」的原因。

做法两步（都在 `tools/browser.py eval` 里做，不改目标、不改源码）：

1. **stub**：把登录/鉴权/权限接口改成返回**假成功**，让前端以为已登录 → 路由进壳、组件挂载；
2. **hook**：包住 `fetch` / `XMLHttpRequest`，把 **outbound**（method/url/body）记到 `window.__hits`，只记不发业务判断。

```js
// 在页面里 eval 这一段（browser.py eval --js-file work/<pid>/hook.js）
window.__hits = [];
const _f = window.fetch;
window.fetch = function(u, o) {
  try { window.__hits.push({ k: "fetch", u: String(u), m: (o && o.method) || "GET",
                            b: (o && o.body) ? String(o.body).slice(0, 400) : "" }); } catch (e) {}
  return _f.apply(this, arguments);
};
const _o = XMLHttpRequest.prototype.open;
XMLHttpRequest.prototype.open = function(m, u) {
  window.__hits.push({ k: "xhr", u: String(u), m: String(m) });
  return _o.apply(this, arguments);
};
```

stub 的写法（按题面猜语义，不猜路径）：把鉴权接口拦下返回 `{"code":0,"data":{"role":"admin"}}` 这类形状，
再看前端**多发**了哪些请求 —— 那些就是真业务面。**只录 outbound，不把 stub 的假数据当结论。**

判据：`window.__hits` 里出现第①层没见过的路径 → 补进端点清单（附来源 "运行时 hook"）。

## 3. 第③层：参数逆向（四招，按成本从低到高）

| 招 | 怎么做 | 什么时候用 |
| --- | --- | --- |
| **锚点扩窗** | 端点附近前后各 300 字符一起看（`grep -o` 命中后 `sed -n` 取窗），参数名/字段名几乎都在窗里 | 单点命中但不知道参数名 |
| **UI 绑定链** | 表单字段 → `v-model`/onChange` → 请求体字段，顺着组件找 | 有表单/编辑页 |
| **多样本 diff** | 同一请求换两个输入各发一次，用 `diff-resp` 比响应与请求差异 | 猜不出哪个字段生效 |
| **错误反推** | 去掉/改坏一个字段看报错：`missing param: xxx` / `invalid enum` 会直接告诉你名字与取值域 | 有校验但无文档 |

参数逆向的产出必须落到「**字段名 + 类型 + 取值域 + 证据**」四元组，只有名字不算。

## 4. 第④层：门禁强制顺序（违反就白打）

```
① 静态 harvest（apiharvest + ctfsurf）
   ↓ 没跑完不许开字典
② 运行时 hook（进壳录 outbound）
   ↓ 先把前端会发的都录到
③ 参数逆向（四招，落到四元组）
   ↓ 端点与参数都有证据了
④ 才允许字典 / 爆破（并给自己定请求上限）
```

为什么强制：2026-10-05 msgboard 实测 —— 2.7 万条目录字典 + 弱口令爆破 + sqlmap 全负，
而答案就在页面的悬空类名与表单 action 里（见 `references/frontend-residue.md`）。
小应用的攻击面是**应用自己的词汇表**（个位数），不是通用字典（万位数）。

## 5. 完成定义（6 项，缺一项不算「前端面做完了」）

- [ ] 端点清单落盘 `work/<pid>/api.json`（含来源：静态 / 运行时）
- [ ] 每个候选端点有**原始请求 + 原始响应**或**没有该端点的证据**（405/404 也算证据）
- [ ] 进壳成功（stub 后前端路由挂载）或写明「为什么进不去」
- [ ] 参数字段以「名字/类型/取值域/证据」四元组记录
- [ ] 悬空类名与表单 action 词汇表已过一遍（`ctfsurf`）
- [ ] 开字典之前跑完了①②③（顺序可在黑板里核对）

## 6. 流程硬禁止

| 禁止 | 撞上了怎么办 |
| --- | --- |
| 整读主 entry（>500KB）或把整份 bundle grep 结果灌回上下文 | 改 `--url`/分段；命中先 `uniq -c` 压成表 |
| 只看静态就断言「没有隐藏接口」 | 进第②层：stub + hook，录 outbound |
| 把 stub 的假数据当业务结论 | stub 只用来**放行路由**；结论必须来自真实响应 |
| 没跑完①②③就开字典/爆破 | 回第①层；字典永远排最后 |
| 参数只记名字不记取值域 | 用错误反推补枚举值 |
| 对目标批量拉真实数据 | 最小样本 1 条取证即止（AGENTS.md §二 红线） |

## 7. 工具映射（本仓）

| 用途 | 命令 | 备注 |
| --- | --- | --- |
| 一键静态 harvest | `apiharvest --dir/--url --json` | `tools/apiharvest.py`；`--selftest` 离线自检 |
| 悬空类名/表单词汇表 | `ctfsurf --dir work/<pid>` | 反爆破闸门 |
| 运行时 hook / stub | `browser.py eval --js-file …` | 真浏览器，别用 curl 推断 DOM |
| 响应 diff | `diff-resp` | 多样本 diff 用 |

> 上游原文（ARTEX）：`E:/a/ARTEX-main/ARTEX-main/skills/api-recon/SKILL.md`。简化点：去掉 Phase0 环境准备与
> 平台专用资产库流程，只保留「静态 → 运行时 → 参数 → 门禁」这条与 CTF 单目标一致的链。
