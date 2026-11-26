# 过滤/WAF 绕过速查（自写版）

> ## 定位
> 本页的**七层检查树**服务两件事：① CTF 题的**判分型/过滤型端点**（提交即评分、`/check` 返回命中规则，
> 或题面自带字符黑名单）—— 这类“过滤器”是题目的一部分，没有生产 WAF 的速率/告警顾虑，
> 可以用 detector-in-the-loop 快速收敛（仍限次）；② 授权渗透里的 403/WAF 绕过。
> 流程：分流 → 403 变体 + 状态码×`size_download` 双判据 →
> 载荷级七层 → **“换通道后必须证明后端真的处理了参数”** → 出口被封首选源站 IP → 检测器在环路里限次变异。
>
> 来源说明：本文件是**重写整理**的，思路参考了两处：
> ① MIT 项目 hxbai 的 `web-cmdi-filter-bypass` 知识卡（**本机未随附该知识卡，思路已并入本文**）；
> ② AGPL-3.0 项目 StrikeAgent_AtkBrain-Flash 的 `waf-bypass-methodology`（**许可为强 copyleft，故只学思路、不搬原文**）。
> 纪律：只做证明性操作；限速；不出题面/授权范围。

## 核心原理（一句话）

**WAF 与后端对同一个 HTTP 请求的解析不一致** —— 我们的活是构造一个「WAF 看见合法、后端看见恶意」的请求。
偏离点分布在七个层次，按**成本从低到高**逐层试。

> 第 1–5 层是"我们绕过目标的过滤器"；**第 6–7 层是相反方向** ——
> 目标自己的**转义/解码顺序**或**二次拼装**把防线拆了（2026-11-09 从 OpenSecurity `examples/web` 读码补入）。

## 第 1 层：编码层（最便宜，先试）

| 手段 | 说明 | 例 |
|---|---|---|
| 双重 URL 编码 | WAF 解一层、后端解两层 | `%2527` → 后端得 `%27` → `'` |
| Unicode / 全角 | 某些 WAF 只匹配 ASCII 形态 | `＇`、`％`、`．` |
| 大小写混写 | 关键字黑名单常大小写敏感 | `SeLeCt`、`UnIoN` |
| NUL 截断 | 部分解析器在 `%00` 处截断 | `1%00'` |
| 十六进制/八进制/Base64 | 交给解释器自己解 | `0x75...`、`\165`、`echo <b64>\|base64 -d\|sh` |
| HTML 实体 / 反斜杠转义 | 针对 XSS/cmd 上下文 | `&lt;`、`c\at` |

## 第 2 层：协议层（WAF 要解析 HTTP 才能看内容）

| 手段 | 要点 |
|---|---|
| **分块传输 chunked** | 把 payload 切成多个 chunk，许多 WAF 不重组 —— 手写原始报文或 `curl --data-binary` + `Transfer-Encoding: chunked` |
| **Content-Type 切换** | `application/x-www-form-urlencoded` ↔ `multipart/form-data` ↔ `application/json`（WAF 只解析它认识的那种） |
| **HTTP 方法切换** | GET 被拦试 POST/PUT/PATCH/HEAD；有的规则只挂 GET |
| **HTTP/2 与伪头** | 部分 WAF 的 H2 解析不完整 |
| **请求走私** | CL.TE / TE.CL 绕前置（**危险且破坏性大，仅在明确授权与靶场标注下做**） |

## 第 3 层：参数层（同一参数名多值 → 谁赢）

| 手段 | 要点 |
|---|---|
| **HPP 参数污染** | `?id=1&id=2`：WAF 看第一个、后端取最后一个（或反之）—— 先测清楚"谁取哪个"再利用 |
| **数组/嵌套** | `id[]=1`（PHP）、`id[0]=1`、JSON 嵌套 `{"id":{"x":"1"}}` |
| **Multipart 包裹** | 把 payload 塞进 multipart 字段，绕开只看 query 的规则 |
| **同名不同位置** | query vs body vs header vs cookie —— 后端优先级与 WAF 检测面常不一致 |

## 第 4 层：Payload 变形（针对关键字/字符黑名单）

| 目标 | 手段 |
|---|---|
| **空格被禁** | `${IFS}`、`$IFS$9`、制表符 `%09`、花括号展开 `{cat,/etc/passwd}`、重定向 `<` |
| **命令分隔符被禁** | `;` `\|` `\|\|` `&&` `&`、**换行 `%0a` / `%0d`**、命令替换 `$(cmd)`、反引号 |
| **关键字被禁** | 引号打断 `c'a't`、反斜杠 `c\at`、通配 `/???/??t`、变量拼接 `a=fl;b=ag;$a$b`、空插入 `ca''t` |
| **函数名被禁** | 等价函数、拼接构造（`Runtime`↔反射取 `forName`）、字符自增 / 布尔算术造整数再 `chr` |
| **几乎无元字符可用** | **单次解释器调用 + Base64 参数**：`python -c "exec(__import__('base64').b64decode('...'))"` —— 命令串本身不含黑名单字符，把命令注入升级为任意代码执行 |

> ⚠️ 实战要点（我踩过）：黑名单**从不完整**。封了空格常漏制表符，封了 `;` 常漏换行，封了双引号常漏单引号。
> **枚举等价写法**是这类题的唯一要害；一个变体被顶回 ≠ 没洞。

## 第 5 层：逻辑层（前四层都不行时）

| 手段 | 要点 |
|---|---|
| **分多次请求** | 先探测把状态/条件铺好，再一击利用（TOCTOU 型） |
| **白名单路径 + 穿越** | 走 WAF 放行的路径，再用 `../` 或参数差异落到目标 |
| **源站 IP 直连** | ⚠️ **仅在题面/授权范围内**；Host 头仍用原域名。CTF 里通常没这需求，渗透里须先确认范围 |
| **换触发点** | 同一功能换个接口/参数（如 `?page=` 被拦就找 `/api/export?path=`） |

## 验证纪律（比绕过本身更重要）

1. **换通道后必须证明后端真的处理了参数** —— 不要只看状态码变化（403→200 可能只是 WAF 放行但后端也没执行）。
   证据形态：时间差（`sleep` 基线对比）／OOB 回连／回显里出现命令或文件内容。
2. **必须有负对照**：说"绕过了"就要能给出"没绕过时被拒"的对照。
3. 判据**必须落在真实回显 / 带外证据上**，不接受“状态码变了”当成功。三种能收的证据：
   回显里出现命令或文件内容 ／ `sleep` 时间差（同一参数先跑基线对照）／ OOB 回连真的收到。
   把原始输出存进 `out.txt`，结论必须能指到里面某一行。
4. **爆破/喷射必须带错误核算**（尝试数/拒绝数/连接错误数）—— 没有核算的喷射等于没做（见开工检查单第 3 节）。

## 第 6 层：**自己拆自己** —— 转义/解码顺序反模式（2026-11-09 补）

> 这一层不是"绕过外部过滤"，而是**目标自己把防线写反了**。同一份代码里出现
> `sanitize` + `decode` 两个动作时，顺序就是漏洞。

**真实形态（OpenSecurity `examples/web` snailnet，2026-11-09 读码确认）**：

```php
// web/lib/content.php
$text = htmlspecialchars($text, ENT_QUOTES, 'UTF-8');   // ← 先转义全文
// ... 再跑 markdown 正则（![]() 与 []() 两种链接）
$url = safe_markdown_url(htmlspecialchars_decode($m[2], ENT_QUOTES));  // ← 又把实体还原了
return '<img src="' . $url . '" alt="...">';
```

**为什么这是洞**：转义发生在**解析之前**，而解析器只认 `]`、`(`、`)` 这几个 ASCII 分隔符 ——
它们本来就不在 `htmlspecialchars` 的转义表里。于是：

1. 用户输入 `&quot;` → 转义成 `&amp;quot;`（安全）
2. 正则匹配到链接后，`htmlspecialchars_decode` 把 `&amp;quot;` **还原回** `&quot;`
3. 拼进 `src="…"` 时，浏览器把 `&quot;` 解成 `"` → **属性闭合，XSS**

正确顺序是**先解析出结构，再对每个字段分别转义**（或干脆不解码，直接对 URL 做协议白名单）。

### 怎么在题里识别（按这个顺序查）

| 步骤 | 查什么 | 命中信号 |
| --- | --- | --- |
| ① | 搜 `htmlspecialchars` / `escape` / `sanitize` 与 `decode` / `unescape` / `html_entity_decode` 是否**在同一个函数里** | 同现即高度可疑 |
| ② | 看 `escape` 是否发生在**分割/解析之前** | 是 → 转义的分隔符可能被解码回来 |
| ③ | 看 `decode` 的结果**流向哪里** | 流向 HTML 属性/JS 字符串/URL → 可闭合 |
| ④ | 数 `decode` 的层数 | 两层 `decode` = 双重编码绕过（`%2522`、`&amp;amp;quot;`） |

**测试 payload 生成思路**：不要先想 XSS 语法，先想"目标解码几次"。
它解码 1 次就用 1 层编码，解码 2 次就用 2 层：

```text
1 层：&quot; onerror=alert(1) x=&quot;
2 层：&amp;quot; onerror=alert(1) x=&amp;quot;
URL 层叠加：%26quot%3B%20onerror%3Dalert(1)
```

---

## 第 7 层：**响应头二次拼装**（2026-11-09 补）

> 来自 OpenSecurity `examples/web` futurejs（Next.js middleware，读码确认）。形态是：
> 校验了"头字段的主类型"，但把**子参数原样拼回去**。

```ts
// middleware.ts:9-24
const match = normalized.match(
  /^([a-zA-Z0-9!#$&^_.+-]+\/[a-zA-Z0-9!#$&^_.+-]+)(?:\s*;\s*charset=(.+))?$/,
)
const charset = match[2]
return charset ? `text/html; charset=${charset}` : 'text/html; charset=utf-8'
```

前面只挡了 `\r\n`（防头注入），但 `charset` 的捕获组 `(.+)` **什么字符都收**，
最后又被拼进 `response.headers.set('Content-Type', …)`。

### 为什么值得单列

| 层次 | 常见做法 | 这里的问题 |
| --- | --- | --- |
| 有校验 | ✅ 正则限制了 mime 主类型 | 主类型确实限死了 |
| 有防注入 | ✅ 挡了 `\r\n` | 只能防**新起一行**，防不了**同一行内的语义篡改** |
| **缺的** | ❌ 子参数未做字符集白名单 | `charset` 里塞 `utf-7`、````、引号等 → 改变响应解析方式 |

**实战意义**：`charset` 可被改成 `utf-7` 之类的编码，配合反射内容可实现**字符集混淆 XSS**；
或者塞入 `;` 追加别的头参数。

### 识别与测试

```bash
# ① 探测：把 charset 改成非法值，看是否原样回显
curl -si $T/ -H 'Content-Type: text/plain; charset=UTF-7' | head -20

# ② 若原样回显，再试带分隔符的值（看能否追加参数）
curl -si $T/ -H 'Content-Type: text/plain; charset=utf-8; x=y' | head -20

# ③ 看响应体是否用同一 charset 渲染（字符集混淆的前提）
```

**通用判据**：凡是代码里出现 `f"…; {变量}"` 或 `'…' + 变量` 拼 header 值，
就查那个变量的字符集白名单在不在。**只挡换行的校验等于没校验子参数。**

---

## 配套工具（pi-lab 本机，命令已入 PATH）

```bash
poc "<组件>"                  # 该组件/WAF 的已知绕过 POC（1.1 万条离线索引，带请求骨架+判定式）
poc --usable "<关键词>"       # 只看可直接打的条目
commix -u "<url>" --batch     # 命令注入（自带多种编码绕过）
tplmap -u "<url>"             # SSTI（含 RCE）
dalfox url "<url>"            # XSS
jwt_tool <token> -M at        # JWT（alg:none / 弱密钥 / kid）
ssrfmap / xxeinjector         # SSRF / XXE
```

工具全表见 `$PI_LAB_ROOT/tools/TOOLS.md`（**唯一事实来源**）。
