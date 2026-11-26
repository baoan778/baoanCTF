# NOPTrace 应急手册 · 主题索引（by-event）

> **这是什么**：把 `NOPTrace-Linux.md`（7904 行 / 504 标题）和 `NOPTrace-Windows.md`（11903 行 / 692 标题）
> 按 **pi-lab 自己的 by-event 分类**（`skills/ir-response/by-event/index.md`）重排成**可直达的主题索引**。
> 手册原有的 `## 目录` 是按章节编号排的，应急时不知道「我现在遇到的事」落在哪章 —— 本索引解决这个。
>
> **行号是什么**：`grep -n '^#'` / `grep -n '<关键字>'` 输出的**文件绝对行号**（第一列），
> 不是章节编号。所有行号落笔前都在 `grep -n` 输出里逐条核对过。
> 路径相对 pi-lab 根目录，即 `tools/knowledge/noptrace/`。

## 怎么用

1. **先按事件类型定位**：在下面找到你手上事件的类别（webshell / 挖矿远控 / 勒索 / 暴破 / 非持续 / 钓鱼 / 隧道 / 数据库 / 无落地 / 善后）。
2. **再按行号跳过去**，不要通读整本手册：

   ```bash
   # 方式一：直接跳到行号
   less +4731 tools/knowledge/noptrace/NOPTrace-Linux.md
   sed -n '4731,4790p' tools/knowledge/noptrace/NOPTrace-Linux.md

   # 方式二：只知道关键字、不知道行号时，先 grep 再跳
   grep -n "内存中搜索字符串" tools/knowledge/noptrace/NOPTrace-Linux.md
   ```

3. 每条格式为：**主题 → 文件:行号 → 一句话说明**。
4. 标 `⚠缺口` 的类别表示**手册本体没有覆盖**，只能去 `skills/ir-response/` 对应的 SOP 里找，别在手册里硬翻。

---

## 两份手册各自的覆盖范围与差异

| 维度 | Linux 手册 | Windows 手册 |
|---|---|---|
| 体量 | 7904 行 / 504 标题 / 17 章 | 11903 行 / 692 标题 / 18 章 |
| 挖矿病毒 | §6（:139） | §5（:176） |
| 远控后门 | §7（:407） | §6（:684） |
| 勒索病毒 | §8（:662） | §7（:1181） |
| 暴力破解 | §9（:777，含 SSH/MySQL/FTP/Redis/Mongo/SMTP） | §8（:1303，含 RDP/SMB/SNMP/FTP/MSSQL） |
| 钓鱼 | **无专章** | §9（:2117） |
| 非持续性事件 | §10（:1303） | §10（:2240） |
| 恶意软件包供应链 | §11（:1560） | **无** |
| 隧道 | §12（:1781，SSH/DNS/ICMP/HTTP/SSL/Socks/Wi-Fi） | §11（:2620，DNS/ICMP/TCP/UDP/KCP/QUIC/Web） |
| badusb | **无** | §12（:3026） |
| 数据库专章 | **无专章**（MySQL/Redis/Mongo 散在 §9 暴破内） | §13 MSSQL 事件排查（:3150） |
| 常规安全检查 | §13（:2196，39 项 0x01–0x39） | §15（:3831，42 项 0x00–0x41） |
| 善后阶段 | §14（:4201） | §14（:3796） |
| 常见问题 | §15（:4233） | §18（:11789） |
| 小技巧 | §16（:4371） | §16（:9798） |
| 知识点附录 | §17（:5822） | §17（:10043） |
| **webshell 专章** | **无** | **无** |
| **内存马 / 无落地专章** | **无** | **无** |

**差异要点**：Linux 侧强在「无文件/隐藏类后门」（LD_PRELOAD、PAM、udev、.pth、内核模块、iptables 端口复用）；
Windows 侧强在「数据库（MSSQL）、持久化点枚举（WMI/IFEO/AppInit/COM/服务/计划任务）、落地痕迹取证（Prefetch/Amcache/ShimCache）」。
**两边都缺 webshell 与内存马专章** —— 这是本索引最大的两个缺口，详见文末「覆盖缺口」。

---

## 1. Webshell / 服务器被入侵 —— ⚠缺口（手册无专章）

> 手册**没有 webshell 排查章节**，只有零散工具提及。落地后的**持久化排查**可借「常规安全检查」，
> 但 access.log 攻击链还原、中间件（Tomcat/JSP/ASPX/PHP）产物排查**不在手册内**。

- webshell 查杀工具清单 → NOPTrace-Windows.md:140 → 「工具准备」里把 webshell 查杀归为工具，只列 D 盾等，不讲方法
- webshell 检测工具（列入排查工具）→ NOPTrace-Windows.md:113 → 事前准备「排查工具」的 D 盾条目
- webshell 检测（写在 Web 隧道里）→ NOPTrace-Windows.md:2990 → 隧道章节顺带一句「用 webshell 检测工具检测」
- web 目录定位 → NOPTrace-Windows.md:9180 → 常规安全检查「敏感文件夹检查」指出 web 目录取决于 web 服务器配置
- Linux webshell 查杀 → NOPTrace-Linux.md:1916 → HTTP 隧道章节里提「使用 D盾等 webshell 查杀工具」
- Linux webshell 批量截图 → NOPTrace-Linux.md:4695 → 小技巧「批量查找文件并打印信息」，适合批量截图 webshell
- Apache 访问日志位置 → NOPTrace-Linux.md:7822 → 以 Ubuntu `/var/log/apache2/access.log` 为例
- 落地后的持久化排查（Linux）→ NOPTrace-Linux.md:2196 → 「常规安全检查」总入口，webshell 清理后必跑
- 落地后的持久化排查（Windows）→ NOPTrace-Windows.md:3831 → 「常规安全检查」总入口

⚠缺口：**访问日志目录扫描/可疑参数/时间线还原、中间件编译产物（Tomcat `work/`）、WAF 层封禁**
手册未覆盖 → 用 `skills/ir-response/by-platform/web-server.md`。

## 2. 挖矿 / 木马 / 远控（高 CPU、异常外联）

**Linux（§6 挖矿 :139 / §7 远控 :407）**
- 挖矿病毒总章 → NOPTrace-Linux.md:139 → 6 章入口
- 挖矿整体流程 → NOPTrace-Linux.md:143 → 先看这张流程图再动手
- 挖矿·梳理现场情况 → NOPTrace-Linux.md:145 → 确认告警真实存在、固定线索
- 挖矿·获取异常进程 pid → NOPTrace-Linux.md:147 → top/ps/netstat 定位高占用与外联 pid
- 挖矿·寻找恶意文件样本 → NOPTrace-Linux.md:170 → 由 pid 反查 `/proc/<pid>/exe` 与工作目录
- 挖矿·确定程序运行时间 → NOPTrace-Linux.md:203 → `/proc/<pid>` 时间戳确定落地时间
- 挖矿·处理异常进程 → NOPTrace-Linux.md:219 → 先暂停/采样再终止，不要直接 `kill -9`
- 挖矿·删除恶意文件 → NOPTrace-Linux.md:302 → 清理文件前先算 hash、留证据
- 挖矿·采集 ioc 信息 → NOPTrace-Linux.md:375 → 外联域名/IP/样本 hash 汇总
- 远控后门总章 → NOPTrace-Linux.md:407 → 7 章入口
- 远控·通过 EDR 定位文件 → NOPTrace-Linux.md:415 → 有 EDR 时直接拿落地文件
- 远控·态势感知取外连 ip+端口 → NOPTrace-Linux.md:421 → 由网络侧告警反查 pid
- 远控·查找进程信息 → NOPTrace-Linux.md:433 → 五元组 → pid → 命令行
- 远控·处理异常进程 → NOPTrace-Linux.md:472 → 与挖矿同法
- 远控·删除恶意文件 → NOPTrace-Linux.md:553 → 含被占用/隐藏文件处理
- 与 C&C 隐藏技术的对抗 → NOPTrace-Linux.md:7261 → 找不到 pid 时的隐藏 C&C 专节（附录 0x05）

**Windows（§5 挖矿 :176 / §6 远控 :684）**
- 挖矿病毒总章 → NOPTrace-Windows.md:176 → 5 章入口
- 挖矿·固定证据 → NOPTrace-Windows.md:180 → 系统快照/磁盘/针对性/内存取证四选
- 挖矿·确定 ioc 信息 → NOPTrace-Windows.md:189 → 外联域名/IP/端口汇总
- 挖矿·获取异常进程 pid → NOPTrace-Windows.md:209 → 任务管理器/Process Hacker/OpenArk 三条路
- 挖矿·寻找恶意样本 → NOPTrace-Windows.md:211 → 由 pid 反查文件与占用
- 挖矿·确定进程启动时间 → NOPTrace-Windows.md:217 → 落地时间线
- 挖矿·处理异常进程 → NOPTrace-Windows.md:223 → 暂停→杀进程树→杀线程
- 挖矿·删除恶意文件 → NOPTrace-Windows.md:225 → 含文件占用解锁
- 挖矿·内存搜索关键字 → NOPTrace-Windows.md:558 → 用域名/IP 在内存镜像里搜
- 远控后门总章 → NOPTrace-Windows.md:684 → 6 章入口
- 远控·固定证据 → NOPTrace-Windows.md:688 → 同上四类取证
- 远控·确定 ioc → NOPTrace-Windows.md:697 → 外连情报
- 远控·主机安全定位文件 → NOPTrace-Windows.md:713 → 主机侧直接定位
- 远控·流量检测域名/IP/端口 → NOPTrace-Windows.md:733 → 网络侧定位
- 远控·寻找恶意样本 → NOPTrace-Windows.md:737 → pid → 文件
- 远控·处理异常进程 → NOPTrace-Windows.md:747 → 服务/进程/线程处置
- 远控·删除恶意文件 → NOPTrace-Windows.md:749 → 删文件+清残留
- 远控·内存搜索关键字 → NOPTrace-Windows.md:1121 → 僵尸网络查杀工具内存搜索
- 地址诱骗（DNS 反查 C2）→ NOPTrace-Windows.md:1077 → 远控域名解析反查
- 内存取证（DumpIt）→ NOPTrace-Windows.md:1174 → 远控章节内的内存镜像取得

## 3. 勒索

**Linux（§8 :662）**
- 勒索病毒总章 → NOPTrace-Linux.md:662 → 8 章入口
- 整体流程 → NOPTrace-Linux.md:666 → 先看处置流程
- 勒索病毒简述 → NOPTrace-Linux.md:668 → 家族与加密行为概览
- 梳理现场情况 → NOPTrace-Linux.md:676 → 判断是否仍在加密
- 确定勒索病毒家族 → NOPTrace-Linux.md:678 → 按加密后缀/勒索信识别家族
- 根据类型寻找解决方法 → NOPTrace-Linux.md:687 → 是否有公开解密工具
- 寻找加密器 → NOPTrace-Linux.md:703 → 定位加密进程/文件
- 解决勒索 → NOPTrace-Linux.md:709 → 处置与恢复
- 确定加密时间 → NOPTrace-Linux.md:728 → 加密起始时间点
- 查找加密开始前的活动 → NOPTrace-Linux.md:739 → 入口线索（日志/进程）
- 对加密器逆向分析 → NOPTrace-Linux.md:745 → 加壳/密钥分析
- 保护现场 → NOPTrace-Linux.md:755 → **恢复前完成取证**
- 采集并确定 ioc 信息 → NOPTrace-Linux.md:769 → 加密器 hash/外联

**Windows（§7 :1181）**
- 勒索病毒总章 → NOPTrace-Windows.md:1181 → 7 章入口
- 简述 → NOPTrace-Windows.md:1185 → 家族与行为概览
- 固定证据 → NOPTrace-Windows.md:1193 → 快照/内存/磁盘
- 确定勒索病毒家族 → NOPTrace-Windows.md:1202 → 后缀/勒索信识别
- 根据类型寻找解决方法 → NOPTrace-Windows.md:1210 → 解密工具检索
- 寻找加密器 → NOPTrace-Windows.md:1226 → 定位加密者
- 解决勒索 → NOPTrace-Windows.md:1232 → 处置
- 确定加密时间 → NOPTrace-Windows.md:1251 → 时间线
- 查找加密开始前的活动 → NOPTrace-Windows.md:1257 → 入口线索
- 对加密器逆向分析 → NOPTrace-Windows.md:1263 → 加壳/密钥分析

## 4. 账号被盗 / 权限滥用 / 暴力破解

**Linux（§9 暴破 :777；§13 账号检查 :2196）**
- 暴力破解总章 → NOPTrace-Linux.md:777 → 9 章入口
- 整体流程 → NOPTrace-Linux.md:781 → 流程先看
- 梳理现场情况 → NOPTrace-Linux.md:783 → 判断暴破方向
- SSH 暴力破解 → NOPTrace-Linux.md:785 → auth.log/secure 成功失败时间关系
- Mysql 暴力破解 → NOPTrace-Linux.md:980 → 连接来源与错误日志
- FTP 暴力破解 → NOPTrace-Linux.md:992 → vsftpd 日志
- Redis 未授权访问&暴力破解 → NOPTrace-Linux.md:1045 → 绑定地址/未授权
- Mongodb 暴力破解 → NOPTrace-Linux.md:1128 → 监听与认证日志
- smtp 暴力破解 → NOPTrace-Linux.md:1206 → 邮件日志
- 暴力破解·善后阶段 → NOPTrace-Linux.md:1274 → 加固与封禁
- 暴力破解类型 → NOPTrace-Linux.md:1292 → 各类暴破特征汇总
- 账户信息检查 → NOPTrace-Linux.md:2253 → `/etc/passwd`/`shadow` 异常账号
- 特权账户检查 → NOPTrace-Linux.md:2339 → UID 0 账户
- 登录信息 → NOPTrace-Linux.md:2347 → `w`/`who`/`last` 当前与历史登录
- ssh key → NOPTrace-Linux.md:2647 → `authorized_keys` 免密后门
- ssh config → NOPTrace-Linux.md:2673 → `sshd_config` 被改（AllowUsers/端口）
- 密码填充检查 → NOPTrace-Linux.md:2907 → PAM 密码填充后门
- history 信息 → NOPTrace-Linux.md:2209 → 命令历史排查
- history 显示执行时间 → NOPTrace-Linux.md:4705 → 给 history 补时间戳
- history 无记录的可能原因 → NOPTrace-Linux.md:7263 → 历史被清空的原因（附录 0x06）

**Windows（§8 暴破 :1303；§15 账号检查 :3831）**
- 暴力破解总章 → NOPTrace-Windows.md:1303 → 8 章入口
- 固定证据 → NOPTrace-Windows.md:1307 → 日志/内存取证
- 暴力破解类型 → NOPTrace-Windows.md:1316 → RDP/SMB/SNMP/FTP/MSSQL 特征
- RDP 暴力破解 → NOPTrace-Windows.md:1328 → 网络+登录+日志三段
- SMB 暴力破解 → NOPTrace-Windows.md:1330 → 同上
- SNMP 暴力破解 → NOPTrace-Windows.md:1332 → 团体名排查
- FTP 暴力破解 → NOPTrace-Windows.md:1338 → IIS FTP 会话
- MSSQL 暴力破解 → NOPTrace-Windows.md:1342 → 1433 连接与登录
- FTP 暴破·日志分析 → NOPTrace-Windows.md:1652 → IIS FTP 匿名/错误登录日志
- MSSQL 暴破·日志分析 → NOPTrace-Windows.md:1752 → SSMS/sa 错误登录日志
- RDP 暴破·日志分析 → NOPTrace-Windows.md:1814 → 事件查看器里大量登录失败（4625）
- SMB 暴破·日志分析 → NOPTrace-Windows.md:1949 → SMB 账户登录失败日志
- 账号信息 → NOPTrace-Windows.md:3865 → 本地账户枚举
- 登录信息 → NOPTrace-Windows.md:3867 → 登录事件
- 命令历史 → NOPTrace-Windows.md:3902 → cmd 历史
- PowerShell 日志 → NOPTrace-Windows.md:3941 → 脚本块日志（账号操作用 PS）
- 隐藏用户（lusrmgr）→ NOPTrace-Windows.md:4972 → 图形化看 `admin$` 类隐藏账号
- 注册表查看隐藏账户 → NOPTrace-Windows.md:7292 → SAM 中 `$` 结尾账户
- 查找隐藏账户 → NOPTrace-Windows.md:7502 → 隐藏账号专项
- wmic 查询用户/隐藏账户 → NOPTrace-Windows.md:8431 → wmic 列出含隐藏账户
- 删除隐藏账户 → NOPTrace-Windows.md:8567 → D 盾/注册表删除
- 谁可以使用远程桌面服务 → NOPTrace-Windows.md:10047 → 远程登录组审计（附录 0x01）
- RDP 爆破登录的日志情况 → NOPTrace-Windows.md:10138 → 4625/4624/4778/4779 详解
- RDP 和 SMB 登录失败日志区别 → NOPTrace-Windows.md:10352 → 区分暴破方向

## 5. 非持续性事件（偶发恶意外联 / 偶发执行）

**Linux（§10 :1303）**
- 非持续性事件总章 → NOPTrace-Linux.md:1303 → 10 章入口
- 简介 → NOPTrace-Linux.md:1307 → 为什么不能靠「没看到」下结论
- 确定目标域名或 IP → NOPTrace-Linux.md:1313 → 收集同类病毒域名/IP
- 修改域名解析记录 → NOPTrace-Linux.md:1317 → 改 hosts 固定解析以捕捉
- 设置监控程序 → NOPTrace-Linux.md:1333 → 短时网络监控脚本
- 等待恶意程序执行 → NOPTrace-Linux.md:1383 → 布控等复现
- 确定程序运行时间 → NOPTrace-Linux.md:1385 → 复现后拿时间线
- 处理异常进程 → NOPTrace-Linux.md:1401 → 拿到稳定线索后转常规流程
- 删除恶意文件 → NOPTrace-Linux.md:1489 → 处置
- 修改 HOSTS 建解析记录 → NOPTrace-Linux.md:7503 → 附录里的落地手法

**Windows（§10 :2240）**
- 非持续性事件总章 → NOPTrace-Windows.md:2240 → 10 章入口
- 简介 → NOPTrace-Windows.md:2244 → 概念
- 固定证据 → NOPTrace-Windows.md:2250 → 先固定再布控
- 确定目标域名或 IP → NOPTrace-Windows.md:2259 → 情报收集
- 修改域名解析记录 → NOPTrace-Windows.md:2267 → 改 hosts/内网 DNS
- 设置监控程序 → NOPTrace-Windows.md:2285 → 监控脚本
- 等待恶意程序执行 → NOPTrace-Windows.md:2313 → 布控复现
- 确定进程启动时间 → NOPTrace-Windows.md:2327 → 时间线
- 处理异常进程 → NOPTrace-Windows.md:2333 → 处置
- 删除恶意文件 → NOPTrace-Windows.md:2335 → 处置
- DNS 缓存排查 → NOPTrace-Windows.md:1051 → 远控章内的 DNS 缓存
- 查看 DNS 缓存记录 → NOPTrace-Linux.md:7353 → 附录里的命令（Linux 取 DNS 缓存较麻烦）

## 6. 钓鱼 —— Linux ⚠缺口，仅 Windows 有专章

**Windows（§9 :2117）**
- 钓鱼事件总章 → NOPTrace-Windows.md:2117 → 9 章入口
- 固定证据 → NOPTrace-Windows.md:2121 → 快照/磁盘/针对性/内存
- 切断传播途径 → NOPTrace-Windows.md:2130 → 终端可直接断网/关机
- 确定钓鱼方式 → NOPTrace-Windows.md:2136 → 邮件/IM/网站/附件分类
- 梳理受害范围 → NOPTrace-Windows.md:2153 → 谁点了、点了什么
- 隔离或排查受害主机 → NOPTrace-Windows.md:2164 → 受影响终端处置
- 分析被钓鱼原因 → NOPTrace-Windows.md:2172 → 人员/网关/模板
- 加固防御体系 → NOPTrace-Windows.md:2191 → 转化为网关规则+培训
- 善后阶段 → NOPTrace-Windows.md:2202 → 善后
- 浏览器排查 → NOPTrace-Windows.md:4215 → 下载/访问记录
- 浏览器插件 → NOPTrace-Windows.md:8551 → 恶意扩展
- 下载记录 → NOPTrace-Windows.md:5458 → 落地文件线索
- 临时目录 → NOPTrace-Windows.md:5448 → 附件落地位置
- PowerShell 脚本块日志 → NOPTrace-Windows.md:3941 → 钓鱼附件执行痕迹

⚠缺口：**Linux 手册无钓鱼章节**；且两份手册都**没有**邮件网关规则、邮件转发规则、凭据泄露、
SSO/AD 侧处置 → 见 `skills/ir-response/by-event/index.md` §6 与 `by-platform/windows.md`。

## 7. 隧道 / 代理 / 非授权中转

**Linux（§12 :1781）**
- 隧道总章 → NOPTrace-Linux.md:1781 → 12 章入口，含隧道来源与共同特征
- 隧道处置方法 → NOPTrace-Linux.md:1807 → 先找隧道进程，再按远控后门处置
- SSH 隧道 → NOPTrace-Linux.md:1815 → 端口转发特征
- DNS 隧道 → NOPTrace-Linux.md:1827 → 短时单目标频繁 DNS
- ICMP 隧道 → NOPTrace-Linux.md:1872 → 最难找进程的一类
- HTTP/HTTPS 隧道 → NOPTrace-Linux.md:1901 → webshell 型隧道（:1916 工具）
- SSL 加密隧道 → NOPTrace-Linux.md:1947 → 加密流量特征
- Socks 隧道 → NOPTrace-Linux.md:1969 → 本地回环连接
- Wi-Fi or Bluetooth 隧道 → NOPTrace-Linux.md:2005 → 非网线外联
- bpftrace 安装 → NOPTrace-Linux.md:2047 → 隧道监控依赖
- 上传监控脚本 → NOPTrace-Linux.md:2054 → eBPF 抓隧道脚本
- 设置监听 → NOPTrace-Linux.md:2115 → 监控部署
- 本地转发隧道 → NOPTrace-Linux.md:2124 → `-L` 特征
- 远程转发隧道 → NOPTrace-Linux.md:2150 → `-R` 特征
- 动态隧道 → NOPTrace-Linux.md:2172 → `-D` socks 特征
- iptables 端口复用 → NOPTrace-Linux.md:2786 → 端口复用型隐藏
- 进程&容器抓包 → NOPTrace-Linux.md:4788 → 抓指定进程的包以确认隧道
- SSH 隧道（附录）→ NOPTrace-Linux.md:7239 → 附录补充

**Windows（§11 :2620）**
- 隧道事件总章 → NOPTrace-Windows.md:2620 → 11 章入口
- 简介 → NOPTrace-Windows.md:2624 → 隧道分类概览
- 固定证据 → NOPTrace-Windows.md:2646 → 先抓包留证据
- DNS 隧道 → NOPTrace-Windows.md:2655 → 原理在 :2690
- ICMP 隧道 → NOPTrace-Windows.md:2657 → 原理在 :2716
- TCP 隧道 → NOPTrace-Windows.md:2659 → 原理在 :2730（含 SSL/TLS）
- UDP 隧道 → NOPTrace-Windows.md:2663 → 原理在 :2748
- KCP 隧道 → NOPTrace-Windows.md:2667 → 原理在 :2765
- QUIC 隧道 → NOPTrace-Windows.md:2671 → 原理在 :2777
- Web 隧道 → NOPTrace-Windows.md:2673 → 原理在 :2790
- 隧道处置流程 → NOPTrace-Windows.md:2677 → 最难点是找隧道进程
- MMA（内存分析）→ NOPTrace-Windows.md:2704 → DNS 隧道工具
- Netsh 端口转发 → NOPTrace-Windows.md:2815 → Windows 自带隧道
- CobaltStrike DNS 隧道演示 → NOPTrace-Windows.md:10385 → 附录实验
- Pingtunnel ICMP 隧道演示 → NOPTrace-Windows.md:10389 → 附录实验
- Kcptun KCP 隧道演示 → NOPTrace-Windows.md:10393 → 附录实验
- Gost QUIC 隧道演示 → NOPTrace-Windows.md:10401 → 附录实验
- 隐藏服务实验·流量分析 → NOPTrace-Windows.md:11585 → 服务隐藏章实验的流量视角
- NetSh 排查 → NOPTrace-Windows.md:4238 → 常规安全检查里的端口转发检查

## 8. 数据库事件（MSSQL / MySQL / Redis / Mongo）

**Windows（§13 MSSQL :3150；§8 MSSQL 暴破 :1342）**
- MSSQL 事件排查总章 → NOPTrace-Windows.md:3150 → 13 章入口
- MSSQL·固定证据 → NOPTrace-Windows.md:3162 → 先取证再登库排查
- MSSQL·简介 → NOPTrace-Windows.md:3171 → 1433 对外暴露历史背景
- MSSQL·用户及会话分析 → NOPTrace-Windows.md:3194 → 可登录用户/当前会话
- MSSQL·登录日志分析 → NOPTrace-Windows.md:3235 → 登录失败/成功位置
- MSSQL·SQL 执行日志分析 → NOPTrace-Windows.md:3247 → 默认不记录需另开
- MSSQL·存储过程排查 → NOPTrace-Windows.md:3319 → `xp_cmdshell` 等
- MSSQL·程序集排查 → NOPTrace-Windows.md:3357 → CLR 程序集后门
- MSSQL·作业(Job) → NOPTrace-Windows.md:3365 → 定时作业持久化
- MSSQL·函数排查 → NOPTrace-Windows.md:3382 → 自定义函数
- MSSQL·数据库触发器 → NOPTrace-Windows.md:3392 → 触发器后门
- MSSQL·排查系统存储过程 → NOPTrace-Windows.md:3400 → 系统 SP 全量
- MSSQL·排查扩展存储过程 → NOPTrace-Windows.md:3576 → 扩展 SP
- MSSQL·用户自定义存储过程 → NOPTrace-Windows.md:3700 → 自定义 SP
- MSSQL·查找程序集文件 → NOPTrace-Windows.md:3683 → 对应 DLL 路径
- MSSQL·添加恶意程序集日志 → NOPTrace-Windows.md:3710 → 定位落地 DLL
- MSSQL·删除程序集 → NOPTrace-Windows.md:3751 → 清除
- MSSQL·获取所有作业 → NOPTrace-Windows.md:3518 → Job 枚举
- MSSQL·新建/删除作业 → NOPTrace-Windows.md:3694 / :3749 → Job 攻防对照
- MSSQL 暴力破解 → NOPTrace-Windows.md:1342 → 登录侧
- MSSQL 暴破·日志分析 → NOPTrace-Windows.md:1752 → 日志字段

**Linux（无数据库专章，散在 §9 暴破）**
- MySQL 暴破 → NOPTrace-Linux.md:980 → MySQL 连接日志
- Redis 未授权&暴破 → NOPTrace-Linux.md:1045 → 绑定地址/未授权
- Mongodb 暴破 → NOPTrace-Linux.md:1128 → 认证与监听
- Redis 日志分析 → NOPTrace-Linux.md:1069 → Redis 内部日志位置

⚠缺口：**Windows 侧 MySQL/Redis/Mongo、Linux 侧 MSSQL 均无专章** —— 手册只按平台焦点覆盖了一侧数据库。

## 9. 无落地 / 内存型驻留 —— ⚠缺口（手册无专章，以下为可替代锚点）

> 两份手册**都没有**「内存马 / 无文件 / memfd 匿名执行」章节。
> 但 Linux §13/§15/§17 与 Windows §15 有大量**进程隐藏、内存取证、持久化点**的可复用锚点，先靠这些顶。

**Linux 替代锚点**
- ps/top 看不到恶意进程 → NOPTrace-Linux.md:4243 → 常见问题专节，先看这里
- ps、top 命令被替换 → NOPTrace-Linux.md:4313 → 用 busybox 绕过
- /proc 与 ps 进程对比 → NOPTrace-Linux.md:3628 → `/proc` 有而 `ps` 没有 = 可疑
- LD_PRELOAD 共享库劫持 → NOPTrace-Linux.md:4354 → 动态库劫持导致命令失真
- 动态链接库劫持 → NOPTrace-Linux.md:2371 → `$LD_PRELOAD` 排查
- 内存中搜索字符串 → NOPTrace-Linux.md:4731 → 无需落盘即可在内存里搜 IOC
- 线程内存相关信息文件存储位置 → NOPTrace-Linux.md:7249 → `/proc/<pid>` 内存映射位置（附录 0x04）
- 与 C&C 隐藏技术的对抗 → NOPTrace-Linux.md:7261 → 五元组找不到 pid 时的专节
- 存在隐藏挂载（netstat pid 显示 -）→ NOPTrace-Linux.md:4273 → `mount -o bind` 隐藏进程，用 `/proc/$$/mountinfo` 查
- 通过挂载进行了隐藏（ps/top 看不到）→ NOPTrace-Linux.md:4286 → 同一 bind mount 手法，`umount /proc/PID` 恢复
- 文件存在隐藏属性（删不掉）→ NOPTrace-Linux.md:4299 → `lsattr` 查 `a`/`i` 属性
- 内核模块签名配置检查 → NOPTrace-Linux.md:3539 → 非法内核模块（rootkit）
- 签名不合法的内核模块 → NOPTrace-Linux.md:3576 → 从日志辅助校验
- udev 后门排查 → NOPTrace-Linux.md:3950 → 触发式后门
- Python .pth 后门排查 → NOPTrace-Linux.md:3974 → 导入即执行
- PAM 检查 → NOPTrace-Linux.md:3626 → PAM 后门入口
- 针对直接修改 PAM 库的后门 → NOPTrace-Linux.md:4171 → 二进制级 PAM 后门
- 针对修改 PAM 模块的后门 → NOPTrace-Linux.md:4177 → 模块级
- 针对修改 PAM 配置文件的后门 → NOPTrace-Linux.md:4185 → 配置级
- iptables 端口复用 → NOPTrace-Linux.md:2786 → 内核态端口复用
- 如何暂停/冻结进程 → NOPTrace-Linux.md:4725 → 处置前保留现场
- 暂停/冻结一个进程 → NOPTrace-Linux.md:4859 → SIGSTOP 细节
- 恢复/解冻一个进程 → NOPTrace-Linux.md:4867 → SIGCONT

**Windows 替代锚点**
- 无文件挖矿 → NOPTrace-Windows.md:215 → PowerShell 远程加载，无落地样本
- 内存搜索关键字（挖矿）→ NOPTrace-Windows.md:558 → 内存镜像搜域名/IP
- 内存搜索关键字（远控）→ NOPTrace-Windows.md:1121 → 同上
- 内存取证（挖矿）→ NOPTrace-Windows.md:677 → DumpIt 取内存镜像
- 内存取证（远控）→ NOPTrace-Windows.md:1174 → 同上
- 内存取证（勒索）→ NOPTrace-Windows.md:1296 → 同上
- 内存取证（暴破/钓鱼/非持续/隧道/badusb/MSSQL）→ NOPTrace-Windows.md:2110 / :2233 / :2613 / :3019 / :3090 / :3789 → 各章同款内存取证节
- DNS 缓存内存搜索 → NOPTrace-Windows.md:2263 → 用域名/IP 内存搜真的有惊喜
- WMI 事件订阅（无落地持久化）→ NOPTrace-Windows.md:4149 → 会话结束仍持续的永久订阅
- 删除 WMI 后门 → NOPTrace-Windows.md:8709 → 清除订阅
- IFEO Injection 排查 → NOPTrace-Windows.md:4346 → 调试键劫持
- AppInit DLL → NOPTrace-Windows.md:4289 → 进程加载即注入
- COM 劫持 → NOPTrace-Windows.md:4407 → CLR/COM 劫持
- 环境变量 CLR 劫持 → NOPTrace-Windows.md:9000 → CLR 劫持排查
- Application Shimming → NOPTrace-Windows.md:4312 → 兼容性 shim 持久化
- AppCert DLLs → NOPTrace-Windows.md:4272 → 进程创建即加载
- 进程角度排查无落地服务 → NOPTrace-Windows.md:10868 → 服务章实验，进程视角
- 通过内存获取隐藏服务 → NOPTrace-Windows.md:11579 → 注册表找不到时的内存法
- 查看进程关联的活动服务 → NOPTrace-Windows.md:8534 → 进程→服务反查
- NTFS 备用数据流 → NOPTrace-Windows.md:11864 → 恶意内容藏在 ADS
- 设置隐藏属性 → NOPTrace-Windows.md:11803 → 文件隐藏
- 使用自带命令查看/删除备用数据流 → NOPTrace-Windows.md:11821 → dir /r
- PowerShell 查看/删除备用数据流 → NOPTrace-Windows.md:11890 → Get-Item -Stream
- PowerShell 历史缺失 → NOPTrace-Windows.md:6905 → 历史被清的可能
- Prefetch（执行痕迹）→ NOPTrace-Windows.md:9546 → 无落地也能证明执行过
- Amcache → NOPTrace-Windows.md:7504 → 程序执行痕迹
- ShimCache → NOPTrace-Windows.md:8674 → AppCompat 执行痕迹
- UserAssist → NOPTrace-Windows.md:9154 → GUI 执行痕迹
- MUICache → NOPTrace-Windows.md:9342 → 程序名缓存
- SRUM → NOPTrace-Windows.md:5398 → 资源使用记录
- Jump Lists → NOPTrace-Windows.md:5372 → 最近打开
- LastActivityView → NOPTrace-Windows.md:4958 → 时间线工具
- sysmon → NOPTrace-Windows.md:9487 → 进程/网络高保真日志

⚠缺口：**内存马专门排查**（Java Filter/Servlet/Listener 型、.NET 注入、`/proc/<pid>/maps` 匿名
`rw xp` 段、memfd 匿名执行、LD_PRELOAD 之外的用户态 shellcode）—— 手册未覆盖。
> 注：`memfd` 在 Linux 手册里只作为 `lsof` 正常输出出现（NOPTrace-Linux.md:3312 起），**不是**排查方法。
→ 用 `skills/ir-response/by-platform/linux.md` §9、`skills/ir-response/by-platform/windows.md`。

## 10. 善后与横向定损

**Linux**
- 善后阶段总章 → NOPTrace-Linux.md:4201 → 14 章入口
- 定损 → NOPTrace-Linux.md:4205 → 与安全/系统/应用/网络负责人确认受害范围
- 针对性排查处理 → NOPTrace-Linux.md:4225 → 按资产重要性扩散排查
- 常规安全检查（善后必跑）→ NOPTrace-Linux.md:2196 → 39 项安全检查清单

**Windows**
- 善后阶段总章 → NOPTrace-Windows.md:3796 → 14 章入口
- 定损 → NOPTrace-Windows.md:3800 → 受害范围确认
- 针对性排查处理 → NOPTrace-Windows.md:3822 → 扩散排查
- 常规安全检查（善后必跑）→ NOPTrace-Windows.md:3831 → 42 项安全检查清单

- Linux 常规安全检查·明细锚点 → NOPTrace-Linux.md:2202–4199 → 杀毒/history/计划任务/账户/特权/登录/启动项/ssh key/日志/内核模块/PAM/udev/.pth/TCP Wrappers/敏感目录
- Windows 常规安全检查·明细锚点 → NOPTrace-Windows.md:3845–4758 → 杀软/证书/账号/登录/启动项/计划任务/服务/RDP/DLL/WMI/IFEO/COM/Network Provider/Winsock/NetSh/IFEO/Sandbox

⚠缺口：**横向定损（同密码/同服务/同管理员/同互信链路的横向排查）两份手册均无专章** →
用 `skills/ir-response/practice/post-incident-and-lateral.md`；
取证规范用 `skills/ir-response/practice/evidence-and-custody.md`。

---

## 附：手册有、但 by-event 九类没覆盖的高价值章节

这些不属于上述按事件分流的类别，但应急时会用到，别漏：

- 恶意软件包供应链攻击（Linux 独有）→ NOPTrace-Linux.md:1560 → 由 pid 定位所属软件包、打包取证、卸载
- badusb 投毒事件（Windows 独有）→ NOPTrace-Windows.md:3026 → 设备 VID/PID、日志分析、处置
- Linux 小技巧 → NOPTrace-Linux.md:4371 → 查找文件/内容、拷贝取证、数据恢复、进程抓包
- Windows 小技巧 → NOPTrace-Windows.md:9798 → 文件查找、内存搜索、文件占用解锁、内网传输
- Linux 知识点附录 → NOPTrace-Linux.md:5822 → 进程组/session、启动项、C&C 隐藏对抗、history 无记录
- Windows 知识点附录 → NOPTrace-Windows.md:10043 → RDP 权限、暴破日志、隧道演示、服务/计划任务隐藏
- 应急通用小坑·删除文件问题 → NOPTrace-Linux.md:133 → `rm ./*` 删不掉点开头文件
- 应急通用小坑·文件无法删除 → NOPTrace-Linux.md:4237 → 被占用/隐藏挂载/隐藏属性/SBIT
- 应急通用小坑·文件被进程占用 → NOPTrace-Linux.md:4260 → lsof / 释放后删除
- 应急通用小坑·文件被隐藏（Windows）→ NOPTrace-Windows.md:11793 → attrib + NTFS ADS
- 应急通用小坑·恶意文件被删除（Windows）→ NOPTrace-Windows.md:11801 → 回收站/ADS 恢复
- 数据恢复 → NOPTrace-Linux.md:4685 → 恢复被删样本/日志
- 拷贝取证 → NOPTrace-Linux.md:4701 → 全盘/进程拷贝
- 进程启动文件检查 → NOPTrace-Linux.md:3296 → 由 `/proc/<pid>` fd 找启动文件（Linux cachedump 思路）

---

## 覆盖缺口汇总（写报告时必须如实标注）

| by-event 类别 | 手册覆盖情况 |
|---|---|
| 1. Webshell / 服务器被入侵 | ⚠ **无专章**。仅工具提及 + 落地后持久化排查。缺口：access.log 攻击链、中间件产物、WAF 封禁 → `by-platform/web-server.md` |
| 2. 挖矿 / 木马 / 远控 | ✅ 双平台各有独立章节 |
| 3. 勒索 | ✅ 双平台各有独立章节 |
| 4. 账号被盗 / 权限滥用 / 暴破 | ✅ 双平台暴破章节 + 常规安全检查账号项 |
| 5. 非持续性事件 | ✅ 双平台各有独立章节 |
| 6. 钓鱼 | ⚠ **仅 Windows 有专章**；Linux 无。邮件网关/转发规则/凭据泄露/SSO 均缺 |
| 7. 隧道 / 代理 | ✅ 双平台各有独立章节 |
| 8. 数据库 | ⚠ **仅 Windows 有 MSSQL 专章**；MySQL/Redis/Mongo 散在 Linux 暴破章，无数据库独立排查章 |
| 9. 无落地 / 内存型驻留 | ⚠ **无专章**。有进程隐藏/内存取证/持久化点替代锚点，但无内存马专项 → `by-platform/linux.md` §9、`windows.md` |
| 10. 善后与横向定损 | ⚠ **有定损/针对性排查**（双平台 §14），但**横向定损无专章** → `practice/post-incident-and-lateral.md` |

**结论**：按 by-event 九类逐项核对 —— 手册**完全覆盖 5 类**（挖矿远控 / 勒索 / 暴破 / 非持续 / 隧道），
**半覆盖 1 类**（数据库，只有 Windows MSSQL），**缺口 3 类**（webshell、钓鱼的 Linux 侧、无落地/内存型）。
善后（第 10 类）有定损与针对性排查，但**横向定损缺专章**；三类缺口必须靠 `skills/ir-response/` 的 SOP 补。
