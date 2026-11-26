# 从 access.log 还原攻击链 + webshell 排查

> Web 服务器是入侵入口的**最高频**载体。这条线的产出通常是整份报告的时间骨架。
> 命令在本机（pi-lab）就能跑，日志文件从目标取回即可。

---

## 1. 先做三件事（不做这三件就会误判）

```bash
# ① 看清日志格式（自定义格式的字段顺序差别很大）
head -3 access.log
# 典型 combined：
# 1.2.3.4 - - [01/Sep/2026:03:12:44 +0800] "GET /a.php?x=1 HTTP/1.1" 200 512 "-" "sqlmap/1.7"

# ② 确认时区（日志时区 vs 服务器时区 vs 你所在的时区）
head -1 access.log | grep -oE '[+-][0-9]{4}$'      # → 全部换算成 UTC 再进时间线

# ③ 看总量与时间范围，判断是否有缺口
wc -l access.log
sed -n '1p;$p' access.log
sort -u <(cut -c1-13 access.log) | head -5     # 有无整天缺失 = 日志被删过
```

---

## 2. 攻击链：从"攻击特征"到"被打成功的点"

**关键区别**：
- **攻击尝试**（大量 404 / 400 / 403）→ 是扫描器，不一定成功
- **攻击成功**（对应请求返回 **200**，且后续有利用行为）→ 才是入口

```bash
# ① 常见攻击特征粗筛（先拿候选，不进结论）
grep -Ein 'union([ \*/]|%20)|select .*from|sleep\(|benchmark\(|extractvalue|updatexml' access.log | head -50
grep -Ein '\.\./(\.\./)+|%2e%2e|/etc/passwd|/proc/self/environ|win\.ini'               access.log | head -50
grep -Ein 'eval\(|assert\(|base64_decode|system\(|shell_exec|preg_replace.*/e'        access.log | head -50
grep -Ein '/\?.*=.*(cmd|exec|command|run)='                                           access.log | head -50
grep -Ein 'php://|data://|expect://|file://|gopher://|dict://'                        access.log | head -50

# ② 扫描器指纹（直接锁定"有人在打"，通常是前期侦察）
grep -Ein 'sqlmap|nikto|nuclei|nmap|masscan|acunetix|nessus|dirsearch|ffuf|gobuster|wpscan' access.log | head -30

# ③ ★ 把"尝试"升级为"成功"：同一 IP 的攻击请求里挑出 200
awk '$9==200' access.log | grep -Ei 'union|select|\.\./|eval\(|base64|cmd=' | head -50

# ④ 按 IP 聚合，挑"请求多 + 状态码分布异常"的
awk '{print $1}' access.log | sort | uniq -c | sort -rn | head -20

# ⑤ 单 IP 的完整活动（拿候选 IP 后看全貌，这是最终证据）
grep '^1.2.3.4 ' access.log | awk '{print $4,$6,$7,$9,$10}' | less
```

**判成功的三个硬条件**（凑不齐就别写"已被入侵"）：
1. 攻击请求收到了 **200**（或 302 到可疑位置）
2. 紧接着有**利用行为**（读文件、写文件、连接数据库、返回了数据长度异常大的响应）
3. 落地物**能在文件系统上被验证**（时间戳落在该请求之后，且内容与 payload 对应）

---

## 3. webshell 排查

### 3.1 从访问日志找（最有效）

```bash
# ① 上传接口的后续访问：先找上传动作，再看有没有人访问"刚传上去的那个文件"
grep -Ei 'POST .*(upload|file|attach|image|import)' access.log | head -30

# ② 异常文件名（随机 8 位、双扩展名、伪装成图片/静态资源）
grep -Eoi '"[A-Z]{2,5} /[^"]*\.(php|jsp|jspx|asp|aspx|ashx|phtml|php[0-9])' access.log \
  | sed 's/.* //' | sort -u \
  | grep -Ei '[a-z0-9]{6,}\.(php|[a-z]+\.php)$|\.(jpg|png|gif|ico)\.(php|jsp)$' | head -30

# ③ 参数里带命令的访问（webshell 交互特征）
grep -Eoi '"[A-Z]{2,5} /[^"]*(php|jsp)[^"]*(cmd|exec|shell|c=|=whoami|=id|=uname)' access.log | head -30

# ④ POST 到静态资源路径（正常不会发生）
grep -E 'POST [^"]*\.(jpg|png|gif|css|js|txt)' access.log | head -20

# ⑤ ★ 用 IP 关联：同一 IP 在写入时间后立刻访问新文件
#    先列出该 IP 的所有 200 请求（按时间）
grep '^1.2.3.4 ' access.log | awk '$9==200 {print $4,$7}' | less
```

### 3.2 从文件系统找

```bash
# 时段内新增/修改的脚本（时间窗 = 缩小 100 倍）
find /var/www /srv /opt -xdev -type f \( -name '*.php' -o -name '*.jsp' -o -name '*.jspx' \
  -o -name '*.asp' -o -name '*.aspx' \) -newermt "$T0" ! -newermt "$T1" -printf '%T+ %s %p\n' 2>/dev/null | sort

# 内容特征（按"能力关键词"搜，比按文件名搜可靠）
grep -rlEi '\$_(POST|GET|REQUEST|COOKIE)\[.{0,12}\]\s*\(\s*\$' /var/www 2>/dev/null
grep -rlEi 'eval|assert|preg_replace\s*\(.*/e|create_function|call_user_func' /var/www 2>/dev/null | head -30
grep -rlEi 'base64_decode|gzinflate|str_rot13|gzuncompress' /var/www 2>/dev/null | head -30
grep -rlEi 'shell_exec|passthru|proc_open|popen|system\s*\(' /var/www 2>/dev/null | head -30
grep -rlEi 'Runtime\.getRuntime\(\)\.exec|ProcessBuilder|Class\.forName|defineClass' /var/www 2>/dev/null
grep -rlEi 'eval\s+Request|execute\s+Request|JScript\.Encode|FromBase64String' /var/www 2>/dev/null

# 混淆特征（长单行 + 大量变量函数 + 拼接）
awk 'length($0)>500 {print FILENAME": "length($0)" 字节单行"}' $(find /var/www -name '*.php' 2>/dev/null) | head -30
```

### 3.3 判断"为什么它能被写进来"

这是报告里最容易被问、也最能体现水平的一段。按顺序排：

| 入口 | 对应日志特征 |
|---|---|
| 文件上传绕过 | `POST` 到 upload 接口 + 之后访问新文件 |
| 文件包含 | 参数里 `../`、`php://`、路径指向 `/tmp`、`/var/log` |
| 任意文件写 | 请求体里出现完整脚本源码（`<?php`） |
| 反序列化 / 表达式注入 | 参数里 `O:`、`%7B%7B`、`${`、`T(` |
| 后台弱口令 → 传马 | 登录成功（302/200）后立刻 POST 到模板/插件目录 |
| 组件漏洞 | 组件路径 + 已知 payload（拿 `poc "<组件>"` 查本地索引） |

### 3.4 确认仍是"活的"

```bash
# 之后还有没有访问（webshell 访问通常 POST 且参数短、频率低）
grep -E 'POST [^"]*(shell|cmd|upload|[a-z0-9]{8})\.(php|jsp)' access.log | tail -20
```

**要点**：webshell 的**文件名可能是正常的**（改一个已有文件的内容更隐蔽）——
所以别只靠文件名，**用内容特征 + mtime + 访问日志三者交叉**。

---

## 4. 其它 Web 侧痕迹

```bash
# ① 数据外传：响应体异常大（配合 body 日志；无 body 日志则看 bytes 字段）
awk '$10>1000000 {print $4,$7,$10}' access.log | head -30

# ② 日志注入 / 日志清洗痕迹
grep -Ei '(\r|\n|%0a|%0d)' access.log | head -20

# ③ 中间件/容器层
ls -la /var/log/nginx/ /var/log/httpd/ /var/log/apache2/ 2>/dev/null
grep -Ei '<\?php' /var/log/nginx/*error*.log | head        # 错误日志里也能落 payload
```

**别漏**：数据库日志（`general_log` 可能记录了 Web 层的注入语句）、
WAF 日志（拦了什么、放过了什么 —— 放过的那条最值钱）、
反向代理/负载均衡日志（源 IP 才是真实攻击者）。

---

## 5. 输出格式

时间线每条：

```
2026-09-01T03:12:44Z  web     GET /api/export?file=../../etc/passwd → 200, 1834B   access.log:412
2026-09-01T03:13:10Z  web     POST /upload.php → 200（写入 shell.php）             access.log:418
2026-09-01T03:13:22Z  web     POST /images/aa7f3c.php (cmd=id) → 200                access.log:421
2026-09-01T03:14:02Z  host    新文件 /var/www/html/images/aa7f3c.php sha256=…      find + sha256sum
```

**每条都要能指着某一行的原始输出。** 只有"可疑请求"、没有"落地物"的，写进
「建议进一步排查」，不要写进「已确认入侵」。
