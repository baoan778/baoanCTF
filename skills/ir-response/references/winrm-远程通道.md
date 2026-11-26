# Windows 远程通道：WinRM/WSMan（防御靶场 · 应急响应）

> **为什么有这一页**：只给 RDP 时，AI 只能「截图 → OCR → 猜」，或者「盲写脚本挨个试」；
> 而排查/修复要的是**一条命令 + 原始输出 + 时间戳**的可复现证据。
> WinRM 是 Windows 侧的「SSH」——**有了它，AI 才进入「执行 → 看输出 → 改一条 → 再执行」的闭环**。
> 工具入口：`winrm`（本机实现，走 WSL interop 调 Windows 自带客户端）。

---

## 一、先认清拓扑：跳板机是常态

```
   [ 我 / WSL ]  ──① ──►  [ 跳板机 Windows ]  ──② ──►  [ 业务靶机 ×N ]
        无内网权限               有凭据、能通内网              真正的作业面
```

两条路，**优先选 B**：

| 方案 | 做法 | 优点 / 代价 |
|---|---|---|
| **A. 隧道穿跳板** | 从 WSL 起 `ssh -L 15985:靶机:5985 user@跳板机`，再对 `127.0.0.1:15985` 执行 | 我这边一条命令直达；**代价**：NTLM 下要把 `127.0.0.1` 加进客户端 `TrustedHosts`，且跳板机需支持 ssh（或改用 `netsh portproxy`） |
| **B. 在跳板机上执行**（推荐） | 先对**跳板机**建 WinRM 会话（`winrm session <跳板机>`），在里面 `Invoke-Command -ComputerName 靶机 …` | 少一层隧道，绕开 SPN/TrustedHosts 麻烦；跳板机本身就是 Windows，天然有 WSMan 客户端 |
| C. 只有 RDP | 开一次 RDP，手工把 WinRM 打开 / 配 portproxy，之后全部走 A 或 B | 唯一需要 RDP 的时刻 |

> **口径**：RDP 只用于「把通道打开」或「纯 GUI 客户端操作」。排查/取证/修复一律走命令。

### 把通道打开（在跳板机或靶机上，管理员 PowerShell）

```powershell
Enable-PSRemoting -Force                     # 一键：起服务 + 配监听器 + 防火墙例外
winrm quickconfig                            # 等价的老命令
Get-Service WinRM; winrm enumerate winrm/config/listener
# 工作组 / 非域环境（客户端侧，目标是 IP 时必需）：
Set-Item WSMan:\localhost\Client\TrustedHosts -Value '*' -Force
```

端口：**5985 = HTTP**、**5986 = HTTPS**。跑题前先 `winrm probe <host>` 确认活着（401/405 = 活）。

---

## 二、认证矩阵（选错了会全是"拒绝访问"，很容易误判成端口不通）

| 场景 | 用哪个 | 备注 |
|---|---|---|
| 域内（Kerberos 可用） | `Negotiate`/`Kerberos`（默认） | 最省事，不要密码也能用当前身份 |
| 工作组 / 本地账号 + HTTP | `Negotiate`(=NTLM) + 客户端 `TrustedHosts` | **最常见**；TrustedHosts 不配 → 直接失败 |
| 本地账号 + Basic | `Basic` + `-UseSSL`(5986) | HTTP 上 Basic 默认禁用；服务端要 `AllowUnencrypted=true`（演练环境常开，生产别开） |
| 二次跳（A→B→C 取资源） | `CredSSP` 或显式传凭据 | 经典"双跳"问题：WinRM 会话里再访问网络资源拿不到凭据 |

```bash
# 本机工具用法（凭据走环境变量，不进 argv/进程列表）
winrm probe 10.0.0.5
PI_WINRM_USER=administrator PI_WINRM_PASS='***' winrm run 10.0.0.5 'whoami; hostname'
PI_WINRM_USER=administrator PI_WINRM_PASS='***' winrm session 10.0.0.5 < cmds.txt   # ★一次认证跑 N 条
PI_WINRM_AUTH=Basic PI_WINRM_HTTPS=1 PI_WINRM_USER=u PI_WINRM_PASS=p winrm run host 'dir C:\'
PI_WINRM_JUMP=user@jump winrm via-jump 10.0.0.5 'Get-Service | ? Status -ne Running'
```

---

## 三、六个必踩的坑（都是本仓实测踩出来的，按重要性排）

| # | 坑 | 现象 | 解法 |
|---|---|---|---|
| 1 | **WSL interop 不转发 Linux 环境变量** | PS 里 `$env:PI_WINRM_HOST` 是空 → "ComputerName 为 Null" | 把变量名登记进 `WSLENV`；**方向标记别写反**：`/u` 是 Win32→WSL，`/w` 才是 WSL→Win32，不带标记=双向。工具里已处理 |
| 2 | **bash 里 `VAR=x 函数名` 不会导出变量** | 同上，换个地方又出现 | 一律 `export VAR=x`；`VAR=x func` 只对外部命令有效 |
| 3 | **PS 5.1 读 .ps1 按 ANSI(GBK) 解码** | 脚本里中文全乱码（`涓枃`） | 生成的 `.ps1` **必须带 UTF-8 BOM**（`EF BB BF`）；工具里有 `ps_check_bom` 防呆 |
| 4 | **PS 5.1 重定向输出按 OEM 代码页** | stdout 回中文乱码 | 不要靠 stdout：`Out-File -Encoding utf8` 落文件再读回来（工具已改成这个出口） |
| 5 | **`Get-Content` 路径写死在带引号的 heredoc** | 路径没展开 → 静默空输出 | 路径也走环境变量传（`PI_WINRM_CMDS`） |
| 6 | **`TrustedHosts` 没配** | "客户端无法连接到目标" / 认证失败 | 客户端侧 `winrm trust-add <host|*>`（需管理员），或用 Kerberos/Basic+SSL |
| **7** | **本地账户远程认证被 UAC 令牌过滤** | 认证阶段就 `拒绝访问`（**连 127.0.0.1 回环也一样**），极易误判成"密码错/端口不通" | ① `Set-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' -Name LocalAccountTokenFilterPolicy -Value 1 -Type DWord`；② 或改用**内置 Administrator**（RID 500 默认豁免）；③ 或用域账户。**靶场给了本地账号却一直拒绝访问，先查这条** |
| **8** | **名字撞车：Windows 自带的 `winrm.exe`** | 在 Windows PowerShell 里敲 `winrm probe` → "未知的操作名称:'probe'"（那是系统的 winrm，只有 `enumerate`/`quickconfig` 等操作名） | 本仓的 `winrm` 是 **WSL 里的 bash 脚本**，只在 WSL 的 PATH 生效；Windows 侧请直接用 `Invoke-Command` / `winrs`，或先进 WSL 再跑 |
| **9** | **账户没有本地口令**（`Get-LocalUser` 里 `PasswordRequired=False` / 口令为空） | 认证阶段 `拒绝访问`；空口令账户**禁止网络登录**（`LimitBlankPasswordUse=1`） | 给该账户设口令，或直接用**内置 Administrator**（演练最省事：`net user Administrator "<pw>"` + `/active:yes`，用完 `/active:no` 还原） |
| **10** | **在 Windows PowerShell 里 `winrm` 参数不对**（与本仓工具混用） | `set-ItemProperty` 之类多行粘贴常被 continuation 提示吞掉，命令其实**没执行**（回显里 banner 出现在命令之后就是征兆） | 一行一条、跑完**回读验证**（如 `(Get-ItemProperty <key>).<name>` 必须打印你设的值） |
| **11** | **`-Recurse` 扫候选路径 = 会话挂死** | 采集脚本里一条 `Get-ChildItem <候选路径列表> -Recurse -File` 就能把整个会话拖到超时（实测：**不存在的 `C:\wwwroot` 也能挂 45s+**，而同机其他路径 1s） | ① 先 `Test-Path` 再扫；② `-Recurse` 一律配 `-Depth N`；③ 重活单独跑并限时（`PI_WINRM_TIMEOUT=600 …`）；④ 工具已加 **240s 硬超时**兜底 |

> 这 6 条的共同点：**不报"你写错了"，只报一个含糊的错误**。所以「**先 selftest 再打靶机**」是纪律：
> `winrm selftest` 一秒钟就能区分「通道坏」和「目标坏」。

---

## 三·五、跳板机实战（长城杯防御靶场实录：**全程不用 RDP**）

> 来源：京津冀蒙长城杯（高校组一等奖）赛后分享的防御靶场部分。原文三个题目（MSSQL 后门清除 /
> DC 权限加固等）都明确写了「**全程不使用 RDP**，采用跳板 SMB + 端口转发 + impacket 方案」。
> 与本文 §三 的坑互相印证：**域管账户不受 UAC 远程令牌过滤限制**（我用本地管理员实测踩过这条）。

| 通道 | 原文做法 | 备注 |
|---|---|---|
| **atexec（SMB/445）** | `impacket-atexec` 以**域管**身份在 win10 跳板上执行命令 | 原文理由：「因为**域管不受 UAC 远程限制**」；复杂命令写成 bat 放 `C:\Users\Public`，路径用**正斜杠**避免转义 |
| **winrs（WinRM）** | `Set-Item WSMan:\localhost\Client\TrustedHosts …` 后 `winrs -r:http://<ip>:5985 -u <user> -p <pass> <cmd>` | 原文理由：目标「1433/3389/5985 开放，445/135 关闭」→ 只能走 WinRM |
| **复杂脚本传参** | `-EncodedCommand`（UTF-16LE + Base64） | 避免引号转义地狱；本仓 `winrm` 走的是「命令经 WSLENV 传给内层 ps1 + `[ScriptBlock]::Create`」，同一目的的另一条路 |
| **穿跳板不占 RDP** | 跳板上 `netsh interface portproxy add v4tov4 listenport=45999 connectaddress=<DC> connectport=445`（139/389 同理），本机再 TCP 转发到跳板 | 原文强调「**不允许直接占用 win10 的 RDP 会话**」——正好是本文开头那条「RDP 只用来把通道打开」的反面教材 |
| **执行升级** | atexec 以 SYSTEM 跑脚本 → 再由跳板经 WMI `Win32_Process.Create` 在 DC 上以域管执行 | 分层执行，跳板只当中转 |
| **凭据来源（实战情报）** | win10 桌面 `MobaXterm.ini` 的 `Passwords` 段保存了全部内网节点会话密码 | 排查时**别放过跳板机上的工具配置**（MobaXterm/Xshell/SecureCRT 的会话文件） |

```bash
# 本仓等价操作（winrs 路径；atexec 需 impacket，本机未装）
winrm trust-add <目标IP>; winrm probe <目标IP>
PI_WINRM_USER='CORP\Administrator' PI_WINRM_PASS='***' winrm run <目标IP> 'whoami; hostname'
# 穿跳板：PI_WINRM_JUMP=user@win10 winrm via-jump <目标IP> '<cmd>'   （未在真拓扑验证过，见 §八）
```

**第七条纪律（原文明说）**：「不允许直接占用跳板机的 RDP 会话」——用端口转发 + SMB/WinRM 借道，
把跳板当路由器用，而不是把跳板当桌面用。

---

## 四、防御靶场/应急：命令集按「三条线」组织

跟 `ir-response` 的取证三条线对齐，可直接写进 `cmds.txt` 一次会话跑完（**只读优先**）：

```powershell
# ── 线 1：持久化 ─────────────────────────────────────────
Get-ScheduledTask | ? State -ne Disabled | Select TaskPath,TaskName,State
Get-CimInstance Win32_StartupCommand | Select Name,Command,Location,User
Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run','HKCU:\...\Run' -EA SilentlyContinue
Get-Service | ? { $_.StartType -ne 'Disabled' } | Select Name,DisplayName,Status,StartType
Get-LocalUser | Select Name,Enabled,LastLogon
Get-LocalGroupMember -Group Administrators
# 近 7 天新增/改动的主机文件（webshell 常落 web 根）
Get-ChildItem C:\inetpub,C:\wwwroot,C:\phpstudy_pro\WWW -Recurse -File -EA SilentlyContinue |
  ? LastWriteTime -gt (Get-Date).AddDays(-7) | Select FullName,Length,LastWriteTime

# ── 线 2：外联 ───────────────────────────────────────────
Get-NetTCPConnection -State Established | ? RemoteAddress -notmatch '^(127\.|::1|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)' |
  Select LocalPort,RemoteAddress,RemotePort,OwningProcess
Get-Process | ? { $_.Path } | Select Id,ProcessName,Path,StartTime
# DNS 缓存（C2 域名线索）
Get-DnsClientCache | Select Entry,Data,TimeToLive
# 防火墙规则近期改动
Get-NetFirewallRule | ? DisplayName -ne '' | Select DisplayName,Direction,Action,Enabled

# ── 线 3：时间线与账号 ───────────────────────────────────
Get-WinEvent -FilterHashtable @{LogName='Security';Id=4624,4625,4672;StartTime=(Get-Date).AddDays(-3)} -MaxEvents 200 |
  Select TimeCreated,Id,@{n='Msg';e={($_.Message -split "`n")[0]}}
Get-WinEvent -LogName System -MaxEvents 100 | Select TimeCreated,Id,LevelDisplayName,ProviderName
Get-ChildItem C:\Windows\Temp,$env:TEMP -File -EA SilentlyContinue | ? LastWriteTime -gt (Get-Date).AddDays(-7)
Get-MpThreatDetection -EA SilentlyContinue | Select InitialDetectionTime,ThreatID,Resources
```

**修复（写操作）的纪律**（`ir-response` 的 HITL 闸门在这里落地）：

1. **先取证，再修**：任何改动前把原文件/原配置留一份 `<name>.bak.<时间戳>`，并记进证据；
2. **最小改动**：能改配置就不删文件，能禁用就不卸载；
3. **改完立刻验证**（再跑一次检查命令），并把"改前/改后"两段输出一起留档；
4. **不删日志、不重启服务**除非任务明确要求 —— 演练里重启常常等于把痕迹冲掉；
5. 批量动作前先在一台上试（`session` 里跑一台 → 确认无误 → 再铺开多台）。

---

## 五、WinRM 被关时，退到哪

| 备选 | 条件 | 说明 |
|---|---|---|
| SMB(445) + `wmiexec`/`smbexec`/`atexec`（impacket） | 有账号 + 445 通 | 本机没装 impacket，需 `py -m pip install impacket`（或搬 wheel 进来） |
| Windows OpenSSH(22) | 靶机装了 OpenSSH Server | 本机 WSL 有 `ssh`、Windows 有 `ssh.exe`，直接当 Linux 用 |
| `sc.exe` / `schtasks` 远程 | 445/135 通 | 远程建服务/计划任务执行命令（动静大，排查阶段优先只读工具） |
| RDP(3389) | 仅 GUI 必需 | **最后手段**：截图不可复现、贵、易误判；只用来"把 WinRM 打开" |

```bash
nmap -Pn -p 5985,5986,445,3389,22 --open <host>     # 先看哪条路开着
```

---

### 本机实测记录（2026-09-29 · 操作者这台 Windows）

> 追加：账户 `幺零零` 是**纯本地**账户且在 Administrators 组，但 `PasswordRequired=False`
> （无本地口令）→ `LimitBlankPasswordUse=1` 直接禁掉网络登录，任何凭据组合都会 `拒绝访问`。
> 结论：**本机预演就用内置 Administrator**（RID 500 豁免令牌过滤），别跟自己的日常账户较劲。
> 另：本次诊断输出里中文账户名显示正常 → 证明工具的 UTF-8 文件出口对中文账户名 OK。

```
Enable-PSRemoting -Force      → WinRM 服务 Running/Automatic；5985 在听；
                                但 Set-WSManQuickConfig 的**防火墙例外**因网络配置文件是 Public 被跳过
                                （回环不受影响；要远程访问需改 Private 或手动 New-NetFirewallRule -LocalPort 5985）
winrm probe 127.0.0.1         → ✓ 活着（HTTP 405）
winrm run 127.0.0.1 'whoami'  → ✗ 拒绝访问（本地管理员账户 + LocalAccountTokenFilterPolicy 未设置）
```

### 端到端跑通记录（2026-09-29 13:49 · 同一台机器）

```
前置：内置 Administrator 设口令并启用（RID 500 天然豁免 UAC 令牌过滤，**不需要**改 LocalAccountTokenFilterPolicy）
winrm run 127.0.0.1 'whoami; hostname; …'      → desktop-9vds5r7\administrator / DESKTOP-9VDS5R7 / Windows 11 专业版 ✓
winrm session 127.0.0.1 < practice/winrm-三条线采集.txt
                                               → **34 条命令 / 25.9 秒 / 0 报错 / 7969 行输出**（一次认证）
                                               → 真拿到：管理员组成员(含 SID)、建立的外联连接、Security/System 事件时间线 ✓
中文编码：诊断输出里 `幺零零`、组名等显示完全正常（UTF-8 文件出口）✓
```

**本轮为跑通它，现场修掉 6 个 bug**（全是"只报含糊错误"型，写的时候看不出来）：
WSLENV 方向写反 · `VAR=x 函数名` 不导出 · ps1 缺 BOM 致中文乱码 · PS5.1 重定向走 OEM 代码页 ·
路径写死在 heredoc 静默空输出 · session 漏导出 host。**另加**：240s 硬超时（防一条慢命令挂死全批）。

## 六、开局 runbook（拿到跳板机后的头 10 分钟，照做别想）

> 这一节的用途：**下次比赛**拿到跳板机时，别再"手连 RDP + 截图"。按顺序敲完就行。

```bash
# ── 0) 先自检通道（1 秒，不需要靶机）—— 区分"我这边坏"还是"目标坏"
winrm selftest

# ── 1) 摸清哪条路开着（别猜）
nmap -Pn -p 5985,5986,445,3389,22 --open <跳板机>
winrm probe <跳板机>            # 401/405 = WinRM 活；000 = 只能走 RDP/SMB

# ── 2) 建会话（一次认证跑 N 条；凭据走环境变量，别写进 argv/截图）
export PI_WINRM_USER=administrator PI_WINRM_PASS='***'
winrm session <跳板机> < skills/ir-response/practice/winrm-三条线采集.txt | tee /tmp/collect-$(date +%H%M).txt

# ── 3) 在跳板机会话里，去打业务靶机（方案 B，免隧道免 SPN 麻烦）
#    （把下一步那条命令也写进采集文件，或用 run 单发）
#    Invoke-Command -ComputerName 10.0.0.5 -Credential $c -ScriptBlock { … }

# ── 4) 定位问题 → 备份 → 最小改动 → 改后复验（见第四节"修复五条纪律"）
# ── 5) 留证：每台一条时间戳文件，命令+原始输出一起进证据包
```

**如果只有 RDP**（当年那场就是这种）——目标是"尽快把命令通道打开"，不是"用 RDP 干活"：

```powershell
# 在那一次 RDP 里，管理员 PowerShell 跑完这三条，之后全部转命令行：
Enable-PSRemoting -Force
Set-Item WSMan:\localhost\Client\TrustedHosts -Value '*' -Force
New-NetFirewallRule -DisplayName 'WinRM-in' -Protocol TCP -LocalPort 5985 -Action Allow -EA SilentlyContinue
# 若我在跳板机之外、只能通跳板机的某个端口，再加一条端口转发（在跳板机上）：
netsh interface portproxy add v4tov4 listenport=15985 connectaddress=<靶机IP> connectport=5985
```
打开之后立刻改用 `winrm session`，**把 RDP 窗口关掉** —— 截图干活是纯亏。

## 七、没有跳板机也能预演（强烈建议赛前做一次）

用**你自己这台 Windows** 当靶机，整套流程压一遍（需要管理员，一次性）：

```powershell
# ① 管理员 PowerShell（UAC 提权）：
Enable-PSRemoting -Force
Set-Item WSMan:\localhost\Client\TrustedHosts -Value '127.0.0.1,localhost' -Force
# ② 回到 WSL：
```
```bash
winrm selftest
winrm probe 127.0.0.1
PI_WINRM_USER=$USER PI_WINRM_PASS='<你的开机密码>' winrm run 127.0.0.1 'whoami; hostname'
printf 'Get-Process | ? CPU -gt 1 | Select -First 5 Name,Id,CPU\nGet-Service | ? Status -ne Running\n' \
  | PI_WINRM_USER=$USER PI_WINRM_PASS='***' winrm session 127.0.0.1
```
跑通这一次，下次开场就是肌肉记忆：**探测 → 会话 → 采集 → 修复 → 复验**。

## 八、通道自检（不需要靶机）

```bash
winrm selftest                 # interop / 编码 / 环境变量 / Remoting cmdlet 是否齐
winrm probe 127.0.0.1          # 端口探测路径
winrm run 127.0.0.1 'whoami'   # 应给出"客户端无法连接到目标"——说明除目标外全对
```

想要**端到端**自测，在 Windows 上以管理员跑一次 `Enable-PSRemoting -Force` +
`winrm trust-add 127.0.0.1`，然后 `winrm run 127.0.0.1 'whoami'` 就能真的拿回主机名。
