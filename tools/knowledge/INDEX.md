# 离线知识基线 · INDEX

> **这是什么**：从参考项目搬进来的**方法论 / payload / 字典 / 脚本**，全在本机盘上，无外网也能读。
> 设计对应 CyberPenda ADR 0037（把离线知识基线烤进镜像）。
>
> **怎么用**：卡住 → 先按 `docs/writeups/手法库.md` → 再查这里对应的 `references/` → 最后才靠模型记忆。
> 应急响应方向另有成套 SOP：`skills/ir-response/`（那一层是可执行流程，这里只是资料本体）。
> **别整读**：单文件 >3000 token 先 `head -50` / `grep` 定位，再读那一段。

## 来源：noptrace

  - **上游**：NOPTrace 应急响应手册 — `https://books.noptrace.com/linux/` 、 `/windows/`
  - **许可**：上游**未声明开源许可** → 本目录只作**本机离线查阅**，不外发、不再分发。

### `NOPTrace-Linux.md` — 504 个标题 / 7904 行
- **别整读**：先 `grep -n '^#' <文件>` 看目录，再定位到那一段

### `NOPTrace-Windows.md` — 692 个标题 / 11903 行
- **别整读**：先 `grep -n '^#' <文件>` 看目录，再定位到那一段

## 来源：riftx

  - **上游**：RiftX — https://github.com/Ch1nfo/RiftX （本机副本 `/mnt/d/AI/claudecode/_ref/RiftX`）
  - **许可**：MIT License, Copyright (c) 2026 Ch1nfo（见同目录 `LICENSE-RiftX-MIT.txt`）
  - **注意**：RiftX 的 SKILL.md 正文里含 RiftX 自己的工具调用语法（`browser navigate`、`record_finding`、

### `api-testing` — API/GraphQL 测试面
- SKILL.md 74 行　→ 对应 pi-lab：`src-recon`
- references/（2）：api-gateway.md(503行)、graphql-deep.md(518行)

### `exploit-access-bypass` — 访问控制绕过（ghost bits、路径归一化差异）
- SKILL.md 26 行　→ 对应 pi-lab：`src-recon`
- references/（3）：auth-bypass.md(321行)、ghost-bits-cast.md(743行)、type-juggling.md(285行)

### `exploit-authz` — 越权：IDOR / 水平与垂直越权
- SKILL.md 63 行　→ 对应 pi-lab：`src-recon`
- references/（1）：idor-deep.md(578行)

### `exploit-cache` — Web 缓存欺骗与缓存投毒
- SKILL.md 24 行　→ 对应 pi-lab：`src-recon`
- references/（1）：cache-poisoning.md(1297行)

### `exploit-csrf` — CSRF：token 缺失/可预测/同站绕过
- SKILL.md 24 行　→ 对应 pi-lab：`src-recon`
- references/（1）：csrf.md(593行)

### `exploit-dev-exposure` — 开发/调试暴露面：actuator、swagger、.git
- SKILL.md 25 行　→ 对应 pi-lab：`src-recon`
- references/（2）：cloud-ide-rce-chain.md(203行)、insecure-scm.md(159行)

### `exploit-file-download` — 任意文件下载与日志/配置泄露
- SKILL.md 81 行　→ 对应 pi-lab：`src-recon`
- references/（2）：bypass_techniques.md(314行)、sensitive_files.md(260行)
- assets/（3）：linux_sensitive_files.txt(130行)、traversal_payloads.txt(111行)、windows_sensitive_files.txt(131行)
- scripts/（1）：file_download_tester.py(254行)

### `exploit-file-upload` — 上传绕过：后缀/魔术字节/解析器差异
- SKILL.md 68 行　→ 对应 pi-lab：`src-recon`

### `exploit-host-header` — Host 头注入：密码重置/路由欺骗
- SKILL.md 24 行　→ 对应 pi-lab：`src-recon`
- references/（1）：host-header.md(322行)

### `exploit-info-leak` — 信息泄露汇总面
- SKILL.md 24 行　→ 对应 pi-lab：`src-recon`
- references/（1）：info-leak.md(253行)

### `exploit-java` — Java 生态：反序列化、Fastjson/Log4j、内存马
- SKILL.md 27 行　→ 对应 pi-lab：`src-recon / ctf-reverse`
- references/（4）：deserialization.md(1217行)、el-injection.md(229行)、jndi-injection.md(271行)、xxe.md(648行)

### `exploit-lfi` — 文件包含/路径穿越读文件
- SKILL.md 103 行　→ 对应 pi-lab：`ctf-web`
- references/（4）：bypass_techniques.md(309行)、lfi_techniques.md(337行)、php_wrappers.md(434行)、rce_methods.md(293行)
- assets/（5）：encoding_bypass_payloads.txt(148行)、sensitive_files_linux.txt(219行)、sensitive_files_windows.txt(154行)、traversal_payloads.txt(87行)、wrapper_payloads.txt(62行)
- scripts/（3）：lfi_bypass_tester.py(343行)、lfi_detector.py(410行)、lfi_exploiter.py(445行)

### `exploit-logic` — 业务逻辑缺陷
- SKILL.md 24 行　→ 对应 pi-lab：`src-recon`
- references/（1）：logic-flaws.md(210行)

### `exploit-oauth` — OAuth / OIDC 流程缺陷
- SKILL.md 24 行　→ 对应 pi-lab：`src-recon`
- references/（1）：oauth-jwt.md(833行)

### `exploit-proto-pollution` — 原型链污染（Node/JS 服务端）
- SKILL.md 24 行　→ 对应 pi-lab：`src-recon`
- references/（1）：prototype-pollution.md(771行)

### `exploit-race` — 条件竞争 / 并发绕过
- SKILL.md 24 行　→ 对应 pi-lab：`src-recon`
- references/（1）：race-conditions.md(1068行)

### `exploit-smuggling` — HTTP 请求走私（CL.TE/TE.CL/TE.TE）
- SKILL.md 24 行　→ 对应 pi-lab：`src-recon`
- references/（1）：request-smuggling.md(1342行)

### `exploit-sqli` — SQL 注入：回显/布尔/时间盲注、DBMS 差异、sqlmap
- SKILL.md 85 行　→ 对应 pi-lab：`src-recon / ctf-web`
- references/（3）：manual_sqli_techniques.md(174行)、nosql_injection.md(99行)、sqlmap_guide.md(106行)
- assets/（3）：common_error_payloads.txt(174行)、dbms_fingerprints.txt(209行)、time_based_payloads.txt(121行)
- scripts/（3）：boolean_sqli_tester.py(224行)、response_analyzer.py(274行)、sqli_payload_generator.py(244行)

### `exploit-ssrf` — SSRF：内网探测、协议利用、云元数据
- SKILL.md 61 行　→ 对应 pi-lab：`src-recon / ctf-web`

### `exploit-waf` — WAF 识别与绕过算子
- SKILL.md 24 行　→ 对应 pi-lab：`src-recon`
- references/（1）：waf-bypass.md(474行)

### `exploit-websocket` — WebSocket 测试面
- SKILL.md 22 行　→ 对应 pi-lab：`src-recon`
- references/（1）：websocket.md(1053行)

### `exploit-xss` — XSS：反射/存储/DOM，绕过与编码
- SKILL.md 86 行　→ 对应 pi-lab：`ctf-web`
- references/（11）：csp_bypass_guide.md(350行)、dalfox_guide.md(229行)、dom_xss_guide.md(420行)、framework_xss_guide.md(488行)、mutation_xss_guide.md(475行)、svg_xss_guide.md(417行)、websocket_xss_guide.md(524行)、xspear_guide.md(320行)、xss-deep.md(242行)、xss_payload_techniques.md(457行)、xsstrike_guide.md(274行)
- assets/（14）：attribute_xss_payloads.txt(40行)、blind_xss_payloads.txt(43行)、common_xss_payloads.txt(19行)、csp_bypass_payloads.txt(779行)、dom_xss_payloads.txt(39行)、framework_xss_payloads.txt(211行)、modern_browser_xss_payloads.txt(700行)、mutation_xss_payloads.txt(306行)、mxss_payloads.txt(179行)、polyglot_xss_payloads.txt(329行)、svg_xss_payloads.txt(523行)、waf_bypass_payloads.txt(111行)…
- scripts/（11）：blind_xss_tester.py(386行)、csp_detector.py(476行)、framework_xss_tester.py(511行)、mutation_xss_tester.py(459行)、mxss_detector.py(477行)、svg_xss_tester.py(432行)、websocket_xss_tester.py(509行)、xss_context_analyzer.py(359行)、xss_full_scan.py(521行)、xss_payload_generator.py(198行)、xss_tester.py(380行)

### `llm-testing` — LLM 测试：偏见/数据泄露/记忆回放/发散
- SKILL.md 137 行　→ 对应 pi-lab：`ctf-ai-ml`

### `pentest-report` — 报告模板与写法
- SKILL.md 205 行　→ 对应 pi-lab：`wp-writer（可借结构）`
- references/（1）：example_report.md(910行)
- assets/（1）：pentest_report_template.md(137行)

### `recon-crawl` — 爬虫取链接/表单/JS 接口（katana/gau/urlfinder）
- SKILL.md 67 行　→ 对应 pi-lab：`src-recon`
- references/（1）：js-reverse.md(200行)

### `recon-dir-scan` — 目录/文件爆破（ffuf/feroxbuster/dirsearch + 字典）
- SKILL.md 83 行　→ 对应 pi-lab：`src-recon`
- references/（3）：ffuf_guide.md(177行)、gobuster_guide.md(163行)、wordlist_guide.md(201行)
- assets/（4）：api-endpoints.txt(330行)、common-dirs.txt(765行)、common-files.txt(471行)、hidden-files.txt(247行)
- scripts/（3）：ffuf_results_parser.py(153行)、merge_wordlists.py(81行)、status_code_analyzer.py(174行)

### `recon-fingerprint` — 指纹识别：响应头/框架/中间件/版本
- SKILL.md 78 行　→ 对应 pi-lab：`src-recon`
- references/（4）：fingerprinting_techniques.md(225行)、httpx_guide.md(94行)、wafw00f_guide.md(101行)、whatweb_guide.md(119行)
- assets/（3）：cms-fingerprints.txt(159行)、tech-headers.txt(137行)、waf-signatures.txt(106行)
- scripts/（3）：extract_headers.py(225行)、tech_matcher.py(257行)、waf_detector.py(268行)

### `recon-port-scan` — 端口与服务发现（naabu/masscan/nmap 编排）
- SKILL.md 79 行　→ 对应 pi-lab：`src-recon`
- references/（4）：masscan_guide.md(299行)、nmap_cheatsheet.md(399行)、rustscan_guide.md(248行)、scanning_techniques.md(332行)
- assets/（3）：common-services.txt(143行)、top-100-ports.txt(1行)、top-1000-ports.txt(1行)
- scripts/（3）：masscan_to_nmap.py(170行)、merge_scan_results.py(287行)、parse_nmap_xml.py(221行)

### `recon-subdomain` — 子域枚举与资产扩展
- SKILL.md 78 行　→ 对应 pi-lab：`src-recon`
- references/（5）：amass_guide.md(94行)、dns_techniques.md(283行)、dnsx_guide.md(108行)、subdomain-takeover.md(230行)、subfinder_guide.md(92行)
- assets/（3）：resolvers.txt(62行)、subdomains-top5k.txt(625行)、wildcard-test.txt(35行)
- scripts/（3）：filter_resolved.py(113行)、merge_subdomains.py(102行)、subdomain_stats.py(127行)

### `results-storage` — 结果落盘约定
- SKILL.md 40 行　→ 对应 pi-lab：`—— （pi-lab 用 work/<pid>/ + 黑板）`

### `security-fuzzing` — 模糊测试payload（命令注入/特殊字符/数据库）
- SKILL.md 36 行　→ 对应 pi-lab：`src-recon`
- references/（8）：Generic-SQLi.txt(268行)、MySQL.fuzzdb.txt(6行)、NoSQL.txt(22行)、quick-SQLi.txt(77行)、sqli.auth.bypass.txt(96行)、LDAP.Fuzzing.txt(26行)、command-injection-commix.txt(926KB)、special-chars.txt(32行)

### `security-passwords` — 口令字典：NCSC 10 万 / probable-v2 / darkweb2017
- SKILL.md 34 行　→ 对应 pi-lab：`src-recon（配合 hydration）`
- references/（10）：100k-most-used-passwords-NCSC.txt(913KB)、10k-most-common.txt(10000行)、2024-197_most_used_passwords.txt(197行)、500-worst-passwords.txt(499行)、best1050.txt(1049行)、darkweb2017_top-100.txt(99行)、darkweb2017_top-1000.txt(999行)、darkweb2017_top-10000.txt(9999行)、probable-v2_top-12000.txt(12645行)、top-passwords-shortlist.txt(25行)

### `security-patterns` — 匹配规律：php 魔术哈希、正则绕过等
- SKILL.md 36 行　→ 对应 pi-lab：`ctf-web / ctf-misc`
- references/（10）：php-auditing.txt(8行)、dangerous-functions-angular.txt(14行)、errors.txt(90行)、grepstrings-auditing-php.md(46行)、grepstrings-basic.txt(14行)、malicious.txt(96行)、pcap-strings.txt(13行)、php-magic-hashes.txt(1545行)、repo-scan.txt(119行)、thickclient-basic.txt(13行)

### `security-payloads` — payload 集：路径穿越、文件名、Flash 等
- SKILL.md 32 行　→ 对应 pi-lab：`src-recon / ctf-misc`
- references/（4）：Hello$(hostname)World.txt(1行)、Hello`hostname`World.txt(1行)、Hello%00World.txt(1行)、Hello.php%00World.txt(2行)

### `security-usernames` — 用户名字典
- SKILL.md 30 行　→ 对应 pi-lab：`src-recon`
- references/（3）：names.txt(10713行)、cirt-default-usernames.txt(828行)、top-usernames-shortlist.txt(17行)

### `security-webshells` — webshell 样本（检测/理解用）
- SKILL.md 33 行　→ 对应 pi-lab：`应急响应（webshell 排查）`
- references/（48）：shell.cfm.html(2KB)、cmd.php(25行)、cmd.sh(372行)、list.jsp(77行)、list.php(33行)、list.sh(47行)、nc.exe(27KB)、up.php(33行)、up.sh(43行)、newadmin-Inchoo.php(77行)、newadmin-KINKCreative.php(69行)、Dysco.php(59行)…

