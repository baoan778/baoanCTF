# NOPTrace-Windows.md 逐节摘要（Worker w1-probe / step s001）

> 来源：tools/knowledge/noptrace/NOPTrace-Windows.md（11903 行，692 节）
> 规则：每个标题一行「这一节解决什么问题 → 关键命令/操作」。
> 生成方式：sed -n 'A,Bp' 逐节读取后人工归纳。未完成节会在文末标注。

## 批次 1（第 1–50 节）

001. 手册封面/标题 → 版本标识，无命令。
002. 目录 → 列出全书 18 个章节入口（封面→常见问题）。
003. 封面 → 仅原文链接，无内容。
004. 简介 → 说明这是 NOP Team 的 Windows 应急响应参考手册，目标「为什么/怎么办」。
005. 更新日记 → v1.0–v1.3 版本变更记录（新增 ADS/Sandbox/pathext 排查等）。
006. 事前准备（章） → 章节导语，准备写保护 U 盘/数据盘/启动盘/虚拟机/32 位工具。
007. 0x01 操作系统基本配置 → 空节，仅占位标题。
008. 0x02 工具准备 → 准备介质与镜像清单（写保护U盘、Server 镜像、32位工具）。
009. 开启显示隐藏文件和文件后缀 → 各版本 Windows 打开「显示隐藏文件+扩展名」的菜单位置。
010. 排查工具 → 列举 System Informer/OpenArk/火绒剑/D盾/Sysinternals/FullEventLogView 等。
011. 杀毒工具 → 列 360/火绒/腾讯管家/D盾/安全狗/深信服僵尸网络查杀。
012. 漏洞验证工具 → Fscan、Goby、Nuclei。
013. 编解码与文本对比工具 → He3。
014. 内网文件传输工具 → Localsend。
015. 日常使用小工具 → Everything、bandzip/360压缩、浏览器、VSCode/Sublime/Editplus。
016. 取证工具 → NOPTrace-Collector、DumpIt。
017. 挖矿病毒（章） → 章节导语，展开挖矿事件处置流程。
018. 0x00 固定证据 → 事件确认后先固定证据：系统快照/磁盘/针对性/内存取证。
019. 0x01 确定 ioc 信息 → 从 DNS/流量审计/主机安全取 IOC(域名/IP/MD5)，再靠 VT/微步/360 等情报定家族。
020. 0x02 获取异常进程的 pid → 空节占位，指向后文任务管理器取 PID 方法。
021. 0x03 寻找恶意样本 → 由 PID 定位恶意文件路径与启动参数；注意无文件 powershell 挖矿。
022. 0x04 确定进程启动时间 → 比对进程启动与文件修改时间，判断文件是否被替换。
023. 0x05 处理异常进程 → 空节占位，指向杀进程小节。
024. 0x06 删除恶意文件 → 空节占位，指向删除文件小节。
025. 0x07 善后阶段 → 指向善后章节，做定损与针对性排查。
026. 0x08 常规安全检查阶段 → 指向常规安全检查章节，找隐藏后门。
027. 1) 任务管理器 → Ctrl+Shift+Esc 打开，点 CPU 列降序取异常进程 PID。
028. 1. CPU占用 → 空节占位，作为「CPU 占用」分类标题。
029. 1. Powershell → `(Get-Process -Id $pid).StartTime` 取进程启动时间。
030. 1) 任务管理器 → 点内存列降序排序找内存占用高进程。
031. 1) 资源监视器 → `resmon` 启动，网络标签看高网络占用进程并可排序。
032. 1. 任务管理器 → 勾选「路径名称/命令行」列以显示文件位置与启动参数。
033. 1. 恶意文件样本采样 → 通过 U 盘/网络直接取样。
034. 1) 暂停进程 → 资源监视器或 `pssuspend.exe <pid>` / `-r` 暂停恢复以验证目标进程。
035. 1. 确定文件占用情况 → 用 Sysinternals `handle` 查文件被哪些进程打开。
036. 2. 内存占用 → 空节占位，作为「内存占用」分类标题。
037. 2) Powershell → `Get-Process | Sort CPU -Descending | Select -First 5` 列 CPU 前 5。
038. 2) Powershell → `Sort WorkingSet -Descending` 列内存前 5 进程。
039. 2. Powershell → `Get-WmiObject Win32_Process -Filter ProcessId` 取名称/路径/命令行(可压一行)。
040. 2) Process Hacker → 标题栏右键加 `Network total rate` 列并降序找流量大进程。
041. 2. wmic → `wmic process where ProcessId=<pid> get ProcessId, CreationDate` 查启动时间。
042. 2. 威胁分析 → 用微步/VT/奇安信/360 等沙箱做样本分析。
043. 2) 杀死进程 → `taskkill /F /PID` / `Stop-Process -Force` / `wmic ... call Terminate` / `pskill64`。
044. 2. 查询注册表 → `regedit` 全局搜索恶意程序名，确认前勿删改。
045. 3. 网络占用 → 空节占位，作为「网络占用」分类标题。
046. 3) Process Explorer → 介绍 Sysinternals Process Explorer 可替换任务管理器。
047. 3) Process Explorer → 点 `Working Set` 降序找内存占用高进程。
048. 3. Process Explorer → 右键属性查看进程详情。
049. 3) System Informer → Choose columns 搜 `network` 加 Network total rate 列并排序。
050. 3. wmic → `get Name, ExecutablePath, CommandLine /format:list` 查进程详情。

