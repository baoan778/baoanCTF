# Windows 现场手册（合并版：命令序列 + 持久化面 + EVTX 速查）

> Windows 侧的**唯一入口**：合并了「现场命令序列」（怎么查）、「持久化面」（查什么）与 EVTX 事件速查。
>
> **命令在哪跑**：**在目标主机上**（活体）或对镜像换出的 hive / EVTX 离线分析。
> pi-lab 本机**没有** `chainsaw` / `hayabusa` / `sigcheck` / `vol3` → 离线 EVTX 走 §7 的替代路线。

---

## 0.0 通道优先：远程命令执行（别用 RDP 截图）

Windows 现场排查的**第一件事是拿到命令执行通道**——有了它，下面所有命令才能批量跑、输出可复现。

```bash
winrm probe <host>                       # 5985/5986 开？开 → 直接上
winrm session <host> < skills/ir-response/practice/winrm-三条线采集.txt   # 只读采集（triage）
PI_WINRM_EVIDENCE_DIR=work/<pid>/evidence winrm session <host> < …        # 证据自动落盘
```

- 手册（11 条实测坑 / 认证矩阵 / 跳板机穿法 / 开局 runbook）：[`../references/winrm-远程通道.md`](../references/winrm-远程通道.md)
- 修复纪律与模板：[`../practice/winrm-修复模板.txt`](../practice/winrm-修复模板.txt)（先备份→最小改动→改后复验）
- 只有 RDP 时：用**那一次** RDP 把 WinRM 打开，立刻关掉，之后全走命令行

---

## 0. 现场通用顺序（企业 SOP）

```
确认告警真实性 + 固定日志/取证副本
  → 用时间窗框范围（§1）
  → 资源异常 → PID（§2）
  → 由 PID/会话/登录用户反查 路径/命令行/启动时间/连接（§3）
  → 持久化面（§4）
  → 账号与认证（§5）
  → EVTX 关键事件（§6）
  → 文件与时间戳取证（§7）
  → 必要时先暂停进程验证，再隔离/终止
  → 善后扩面（§9）
```

**铁律**：先备份日志再分析；需要内存态时**快照不能替代内存取证**；不要过早删文件或清日志。

---

## 1. 先框时间窗

```powershell
$T0 = Get-Date '2026-09-01 00:00'; $T1 = Get-Date '2026-09-15 00:00'
Get-ChildItem C:\ -Recurse -File -ErrorAction SilentlyContinue |
  Where-Object { $_.LastWriteTime -ge $T0 -and $_.LastWriteTime -le $T1 } |
  Select-Object LastWriteTime, Length, FullName | Sort-Object LastWriteTime
```

常见落地点（**优先看这些目录，比全盘扫快一个量级**）：
`C:\Users\Public`、`C:\ProgramData`、`C:\Windows\Temp`、`%TEMP%`、`C:\PerfLogs`、
`C:\Windows\System32\Tasks`、`C:\Users\<user>\AppData\Local\Temp`。

---

## 2. 资源异常与进程

```powershell
Get-Process | Sort-Object CPU -Descending | Select-Object -First 10 ProcessName, Id, CPU, StartTime
Get-Process | Sort-Object WorkingSet -Descending | Select-Object -First 10 Id, ProcessName, WorkingSet
(Get-Process -Id <pid>).StartTime
Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'powershell|cmd|mshta|rundll32' } |
  Select-Object ProcessId,ParentProcessId,Name,CommandLine
```

```cmd
tasklist /svc
tasklist /v
wmic process where ProcessId=<PID> get ProcessId, CreationDate
wmic process where ProcessId=<PID> get Name, ExecutablePath, CommandLine /format:list
```

**强信号**：`lsass.exe`/`svchost.exe` 的 Path 不在 `C:\Windows\System32`；
`rundll32`/`regsvr32`/`mshta`/`wmic`/`bitsadmin`/`certutil` 出现在 CommandLine；
父进程是 `winword`/`excel`/`outlook` 却起了 `powershell`。

---

## 3. 网络与会话

```powershell
Get-NetTCPConnection -State Listen          # 监听
Get-NetTCPConnection -State Established     # 已建立（找外联）
Get-SmbSession                              # SMB 会话
query user; query session                   # 登录会话
```

```cmd
netstat -ano | findstr ":<port>"
netstat -anob                                # 带进程名（需管理员）
```

---

## 4. 持久化面（**重启后还能不能回来** · A 线核心）

### 4.1 自启动（Run 键 / 启动目录 / Winlogon）

```powershell
$keys = @(
 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce',
 'HKLM:\SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Run',
 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce',
 'HKU:\<SID>\SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
)
foreach ($k in $keys) { "== $k"; Get-ItemProperty $k -ErrorAction SilentlyContinue }

Get-ChildItem "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\StartUp",
              "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup" -Force
```

`Winlogon` 的 `Shell` / `Userinit` 被改 = 经典后门（正常是 `explorer.exe`、`userinit.exe,`）。

### 4.2 服务与驱动

```powershell
Get-CimInstance Win32_Service | Where-Object { $_.PathName -notlike 'C:\Windows\*' } |
  Select-Object Name,DisplayName,State,StartMode,PathName
Get-CimInstance Win32_SystemDriver | Select-Object Name,State,PathName
```

```cmd
reg query HKLM\SYSTEM\CurrentControlSet\Services /s | findstr /i "ImagePath"
```

**强信号**：`ImagePath` 指向 `C:\Users\Public`、`C:\ProgramData`、`%TEMP%`；
服务名模仿系统服务（`WinDefendUpdate`）；`Start=2`（自动）且路径含空格却**没加引号**。

### 4.3 计划任务

```powershell
Get-ScheduledTask | Where-Object { $_.State -ne 'Disabled' } | ForEach-Object {
  $a = $_.Actions
  [pscustomobject]@{ Task=$_.TaskName; Path=$_.TaskPath; State=$_.State
                     Exec=($a.Execute -join ';'); Args=($a.Arguments -join ';') }
} | Format-Table -Auto
```

```cmd
schtasks /query
schtasks /query /fo LIST /v
```

**强信号**：TaskPath 是根目录 `\` 的随机名；Action 指向 `powershell -enc` / `mshta` / `rundll32`；
触发器是「登录时」或极高频；藏在 `\Microsoft\Windows\` 伪装目录下。

### 4.4 WMI 事件订阅（隐蔽性最高）

```powershell
Get-CimInstance -Namespace root\subscription -ClassName __EventFilter
Get-CimInstance -Namespace root\subscription -ClassName __EventConsumer
Get-CimInstance -Namespace root\subscription -ClassName __FilterToConsumerBinding
Get-CimInstance -Namespace root -ClassName __Namespace | Select-Object Name
```

**只要有 Consumer，就要逐个解释它是干什么的** —— 正常环境这块通常是空的。

### 4.5 其它劫持面

```powershell
Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Windows' -Name AppInit_DLLs
reg query 'HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options' /s   # IFEO 映像劫持
reg query 'HKCU\Control Panel\Desktop' /v SCRNSAVE.EXE                                          # 屏保后门
```

---

## 5. 账号与认证

```powershell
Get-LocalUser | Select-Object Name,Enabled,LastLogon,PasswordLastSet
Get-LocalGroupMember -Group Administrators
Get-LocalGroupMember -Group 'Remote Desktop Users'
```

```cmd
net user
net localgroup administrators
```

**强信号**：新增本地管理员、Guest 被启用、`PasswordLastSet` 落在案发时段、
内置 Administrator（RID 500）被启用且近期登录。

**域环境**：

```powershell
Get-ADUser -Filter { AdminCount -eq 1 } -Properties MemberOf,LastLogonDate
Get-ADGroupMember 'Domain Admins'
# DCSync：4662 事件里出现目录复制相关属性
```

---

## 6. EVTX 关键事件速查

| EventID | 含义 | 看什么 |
|---|---|---|
| **4624** | 登录成功 | LogonType（2 本地 / 3 网络 / 10 RDP）、源 IP、账号 |
| **4625** | 登录失败 | 短时间大量 = 爆破。**别只筛 LogonType=10**，很多场景类型是 3 |
| **4634 / 4647** | 注销 | 会话时长 |
| **4648** | 显式凭据登录（runas） | 横向线索 |
| **4672** | 授予特殊权限 | 管理员登录常伴随出现 |
| **4688** | **进程创建** | ⚠️ 默认可能不含 CommandLine，需开审核策略 |
| **4697 / 7045** | **服务安装** | 新增服务的 ImagePath（7045 在 System 日志） |
| **4698 / 4699 / 4700 / 4702** | 计划任务 创建/删除/启用/更新 | 任务内容与创建者 |
| **4720 / 4722 / 4724 / 4726 / 4728** | 账号 创建/启用/改密/删除/加管理组 | 攻击者新建账号 |
| **4103 / 4104** | PowerShell 模块/脚本块日志 | **脚本内容明文**，最值钱 |
| **1102** | **审核日志被清空** | 出现即高度可疑 |
| **104** | 系统日志被清空 | 同上 |
| **5140 / 5145** | 网络共享访问 | 横向移动 |
| **5156 / 5157** | 网络连接 允许/阻止 | 带进程的连出记录 |
| **4768 / 4769 / 4771** | Kerberos TGT/服务票/预认证失败 | 票据攻击（Kerberoasting） |
| **4778 / 4779** | 会话重连/结束 | RDP 会话轨迹 |

```powershell
Get-WinEvent -LogName Security -FilterXPath '*/System/EventID=4624' -MaxEvents 50
Get-WinEvent -LogName Security -FilterXPath '*/System/EventID=4625' -MaxEvents 50
```

```cmd
wevtutil qe Security /q:"Event/System/EventID=4624" /f:text /rd:false /c:10
```

---

## 7. 离线 EVTX 分析（本机没有 chainsaw/hayabusa 时）

```bash
# ① strings + grep（EVTX 里关键字是 UTF-16LE，用 -el）
strings -a -el 安全.evtx | grep -nEi 'powershell|cmd\.exe|mshta|rundll32|bitstransfer|Invoke-|DownloadString' | head -40
strings -a -el 系统.evtx | grep -nEi 'Service_Install|7045|\.tmp|Users\\\\Public' | head -40

# ② py + python-evtx（本机未装则装进 venv）
py -c "import Evtx" 2>/dev/null && echo "python-evtx 可用" || py -m pip install python-evtx

# ③ 只抓"新增服务"这一类（最常用来落地）
strings -a -el 系统.evtx | grep -A3 -i 'Service_Install' | head -40
```

> **速度提示**：EVTX 全量转 XML 很慢。应急里先按**时间窗 + EventID 白名单**筛，再展开内容。

---

## 8. 文件、时间戳与取证

```powershell
# 常见落地目录（同 §1）
'C:\Users\Public','C:\ProgramData','C:\Windows\Temp',"$env:TEMP",'C:\PerfLogs' |
  ForEach-Object { Get-ChildItem $_ -Force -Recurse -ErrorAction SilentlyContinue |
                   Select-Object LastWriteTime,Length,FullName }

# 时段内新增的 exe/dll/ps1/bat
Get-ChildItem C:\ -Recurse -Include *.exe,*.dll,*.ps1,*.bat,*.vbs -ErrorAction SilentlyContinue |
  Where-Object { $_.LastWriteTime -ge $T0 } | Select LastWriteTime,FullName

# 备用数据流（ADS）
Get-Item -Path C:\* -Stream * -ErrorAction SilentlyContinue |
  Where-Object { $_.Stream -ne ':$DATA' }
```

**时间戳伪造（timestomping）**：Windows 有 **四个时间戳**（`$STANDARD_INFORMATION` 与
`$FILE_NAME`，各含创建/修改/访问/MFT 变更）。攻击者常只改前者 —— **两者不一致就是伪造信号**。

```bash
# 本机 fls/mactime（sleuthkit）出 MACB 时间线
fls -r -m C: /dev/sdX > body.txt
mactime -b body.txt -d > timeline.csv
```

### 暂停与终止（先暂停、后处置）

```cmd
pssuspend.exe <pid>
pssuspend.exe -r <pid>
```

```cmd
taskkill /pid <pid>
taskkill /t /pid <pid>            # 连子进程
taskkill /f /pid <pid>            # 强制（最后手段）
```

```powershell
Stop-Process -Id <pid> -Force
```

---

## 9. 高风险误区

- 不要一上来就强制结束进程（会毁证据、也可能触发 watchdog 重启）
- 不要只依赖单一日志源（`4625` 要看 LogonType 与源 IP 一起）
- 不要把「有微软签名」视为无害
- 不要在生产现场贸然运行会触发程序行为的检测工具
- 不要把杀软白名单范围自动视为安全区
- 现场不应过早删除文件或清理日志

---

## 10. 善后与扩面

至少检查：同密码服务器 / 同漏洞版本资产 / 同一管理员维护的服务器 /
可直接互信访问的资产（SSH key、RDP 保存凭据、域信任）/ 案发时段高频交互的主机。

最低恢复动作：改密 + 吊销高风险凭据 → 加固邮件与终端策略 → 同类资产横向排查 →
把事件转化为**检测规则 + 培训材料**。详见 `practice/post-incident-and-lateral.md`。
