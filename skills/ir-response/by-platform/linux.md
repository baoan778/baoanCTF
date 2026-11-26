# Linux 现场手册（合并版：命令序列 + 持久化面全清单）

> 这是 Linux 侧的**唯一入口**：合并了「现场命令序列」（怎么查）与「持久化/后门面」（查什么）。
> 按下面的顺序读，不要跳着看 —— 顺序本身就是方法论。
>
> **命令在哪跑**：**在目标主机上**执行（活体排查），不是 pi-lab 本地。
> 本机没装的工具不影响这份清单。对**镜像**排查时把 `/` 换成挂载点，并只读挂载：
> `mount -o ro,noatime /dev/sdX1 /mnt/img`。

---

## 0. 现场通用顺序（企业 SOP）

```
二次研判告警真实性
  → 固定最小证据集（§8）
  → 用时间窗把范围框住（§1）
  → 资源异常定位 PID（§2）
  → 由 PID 反查 文件/命令行/父进程/映射/端口（§3）
  → 连接与外联（§4）
  → 持久化面（§5）
  → 账号与登录（§6）
  → 文件与权限（§7）
  → 日志（§8）
  → 先暂停验证，再决定是否清除
  → 善后扩面（§11）
```

**铁律**：删除/隔离前先确认文件占用、属性、挂载与隐藏点；**先暂停（SIGSTOP）再处置**，
不要一上来 `kill -9`（会毁掉内存态证据）。

---

## 1. 先框时间窗（最省时间的一招）

```bash
T0='2026-09-01 00:00'; T1='2026-09-15 00:00'
# 时段内新增的文件
find / -xdev -newermt "$T0" ! -newermt "$T1" -type f -printf '%T+ %s %p\n' 2>/dev/null | sort
# 时段内新增的可执行文件
find / -xdev -newermt "$T0" -type f -perm -u+x -printf '%T+ %s %p\n' 2>/dev/null | sort
# 时段内被改过的配置
find /etc -newermt "$T0" -type f 2>/dev/null | sort
# 按访问时间（atime 常被忽略，但很值钱）
find /path -type f -newerat "$T0" ! -newermt "$T1" 2>/dev/null
```

> `-newermt` 用 mtime；`-newerat` 用 atime。两者一起看能区分「落地」与「被读过」。

---

## 2. 资源异常与进程树

```bash
top -c -o %CPU                      # 先看谁在吃
top -c -o %MEM
ps -w -eo pid,ppid,user,lstart,etime,%cpu,%mem,cmd --sort=-%cpu | head -n 10
ps -w -eo pid,ppid,user,lstart,etime,%cpu,%mem,cmd --sort=-%mem | head -n 10
ps -w ajfx                          # 进程树（看父子关系是否合理）
pstree -agplU
ps -eo pid,ppid,user,lstart,etime,cmd --sort=start_time   # 按启动时间排：找"案发后才起来的"
```

**强信号**：父进程是 `nginx`/`apache`/`mysqld`/`java` 却起了 `sh`/`bash`/`curl`；
进程名伪装成内核线程（`[kworker/0:1]`）但来自用户态路径；启动时间正好落在案发时段。

---

## 3. 由 PID 反查（这一步决定能不能坐实）

```bash
P=<pid>
ls -l /proc/$P/exe          # ★ 真实可执行路径（显示 "(deleted)" = 文件已删但进程还在）
ls -l /proc/$P/cwd          # 工作目录（/tmp /dev/shm /var/tmp = 高度可疑）
tr '\0' '\n' < /proc/$P/environ    # 环境变量（反弹 shell / 恶意负载常在这留痕）
tr '\0' ' '  < /proc/$P/cmdline    # 完整命令行（比 ps 更可靠，ps 会被改）
cat /proc/$P/maps | grep -Ei 'deleted|/tmp|/dev/shm|memfd'   # 无文件/内存马特征
lsof -p $P                  # 打开的文件与网络句柄
pwdx $P                     # 工作目录（等价 cwd）
ps -w -Lf $P                # 线程
top -H -p $P                # 按线程看 CPU
pstree -agplU $P            # 该进程的子树
stat /proc/$P               # 启动时间（= 进程开始时间）
ps -w -eo pid,lstart,etime,cmd | grep $P
```

**内核线程冒充检测**：`/proc/<pid>/exe` 指向真实文件 = 不是内核线程（内核线程该是空的）。

---

## 4. 连接与外联

```bash
ss -antp                    # 监听 + 已建立（本机没有 netstat，用这个）
ss -antp | grep -v ':22 '   # 排掉正常 SSH 之后剩下的
ss -lntup                   # 只看监听
lsof -i -n -P               # 哪个进程占哪个连接
lsof -i:<port>
```

**强信号**：固定间隔的心跳连接（每 30s/60s 一次，对同一 IP:PORT）、
监听在高端口且由 `/tmp` 下的进程持有、连接到一个陌生 IP 且进程名是 `[kworker]` 之类。

**抓包确认**（判断是不是心跳型 C2）：

```bash
timeout 60 tcpdump -i any -nn -c 200 host <可疑IP> -w /tmp/c2.pcap
tshark -r /tmp/c2.pcap -q -z io,stat,10        # 每 10 秒一格的包/字节数 → 看周期性
```

---

## 5. 持久化面（**重启后还能不能回来** · A 线核心）

### 5.1 定时任务（最常用）

```bash
crontab -l; crontab -l -u root 2>/dev/null
ls -la /etc/cron.* /etc/crontab /etc/anacrontab 2>/dev/null
grep -rn '' /etc/cron.d/ /var/spool/cron/ /var/spool/cron/crontabs/ 2>/dev/null
find /etc/cron* /var/spool/cron -newermt "$T0" 2>/dev/null
systemctl list-timers --all
find /etc/systemd /lib/systemd /usr/lib/systemd -name '*.timer' -newermt "$T0" 2>/dev/null
```

**强信号**：内容是 `curl|wget … | sh`、`base64 -d`、指向 `/tmp` 的脚本；
间隔极短（`* * * * *`）；注释写着 `# system update` 之类伪装；`/etc/cron.d/` 里属主非 root。

### 5.2 systemd 服务

```bash
systemctl list-unit-files --state=enabled
systemctl list-units --type=service --state=running
find /etc/systemd/system /usr/lib/systemd/system /lib/systemd/system \
     -name '*.service' -newermt "$T0" 2>/dev/null
systemctl cat <可疑unit>            # 一次看全（含 ExecStart/ExecStartPre）
```

**强信号**：`ExecStart` 指向 `/tmp`、`/dev/shm`、`/var/tmp`，或 `sh -c 'curl …'`；
Description 伪装成系统组件（`systemd-networkd-helper`）；`Restart=always` + 短间隔。

### 5.3 登录/启动脚本

```bash
cat /etc/rc.local 2>/dev/null
ls -la /etc/init.d/ /etc/rc*.d/ 2>/dev/null
grep -n '' /etc/profile /etc/profile.d/*.sh /etc/bash.bashrc /etc/bashrc 2>/dev/null
for h in /root /home/*; do
  tail -n 30 "$h/.bashrc" "$h/.bash_profile" "$h/.profile" 2>/dev/null
done
```

### 5.4 SSH / 账号后门

```bash
cat /root/.ssh/authorized_keys
find / -xdev -name authorized_keys -newermt "$T0" 2>/dev/null      # 时段内被写过的最可疑
for h in $(awk -F: '$3>=1000 && $3<65534 {print $6}' /etc/passwd); do
  [ -f "$h/.ssh/authorized_keys" ] && echo "== $h" && cat "$h/.ssh/authorized_keys"
done
grep -rn 'ssh-rsa\|ssh-ed25519' /etc/ssh/ /root/ 2>/dev/null | grep -v '\.pub$'
```

### 5.5 提权面 / 内核层

```bash
grep -rn '' /etc/sudoers /etc/sudoers.d/ 2>/dev/null | grep -v '^.*#'
cat /etc/pam.d/* | grep -i 'pam_exec\|pam_python\|pam_unix'         # pam 后门
cat /etc/ld.so.preload 2>/dev/null                                   # 非空 = LD_PRELOAD rootkit
ls -la /etc/ld.so.conf.d/                                            # 新增条目
lsmod; cat /proc/modules | head -50
find /lib/modules -newermt "$T0" -type f 2>/dev/null                 # 新内核模块
sudo dmesg | grep -i taint
```

---

## 6. 账号与登录

```bash
awk -F: '$3==0 {print}' /etc/passwd          # 所有 UID=0（除 root 外都要能解释）
awk -F: '$2=="" {print}' /etc/shadow         # 空口令
awk -F: '$2!~/^[!*]/ {print $1}' /etc/shadow # 有可用口令哈希的账号
ls -la /etc/passwd /etc/shadow /etc/sudoers* /etc/group   # mtime 是否落在案发时段
w; who; who -a
lastlog                                       # 各账号最后登录（本机没有 last 时用 py 解 wtmp，见 §8）
lslogins
```

---

## 7. 文件与权限

```bash
# 落地目录（最常见的投放点）
ls -la /tmp /var/tmp /dev/shm /run/shm 2>/dev/null
find /tmp /var/tmp /dev/shm -type f -newermt "$T0" 2>/dev/null

# 隐藏文件与可疑权限
find / -xdev -name '.*' -type f -perm -u+x -newermt "$T0" 2>/dev/null | head -50
find / -xdev -perm -4000 -type f 2>/dev/null    # SUID
find / -xdev -perm -2000 -type f 2>/dev/null    # SGID
find / -xdev -perm -6000 -type f 2>/dev/null    # SUID+SGID
getcap -r / 2>/dev/null                          # capabilities（比 SUID 更隐蔽）

# 文件属性（+i 不可改 = 攻击者防删除）
lsattr 可疑文件
stat 可疑文件
```

---

## 8. 日志与取证保全

### 8.1 日志

```bash
journalctl --since "$T0" --until "$T1" -o short-iso        # 系统日志（优先）
journalctl --since "$T0" -p warning --no-pager
ls -la /var/log/                                            # 有没有被删/被清空
journalctl --verify 2>&1 | head                             # 日志完整性
grep -aiE 'Accepted|Failed password|Invalid user|session opened' \
     /var/log/auth.log /var/log/secure 2>/dev/null | head -50
```

**强信号**：`Accepted publickey` 且来源 IP 陌生；短时间大量 `Failed password`；
非工作时段的 `session opened`；**日志文件有时间缺口**（= 被人删过）。

> **本机没有 `last` / `lastb`**（pi-lab 侧）—— 在目标上正常用；若目标也没有，用 `py` 解 wtmp：
> ```bash
> py -c "import struct;f=open('/var/log/wtmp','rb');[print(struct.unpack('hi32s4s32s256shhiii4i20x',c)) for c in iter(lambda:f.read(384),b'')]"
> ```

### 8.2 取证与保全（动手前必看）

```bash
# 暂停而不是杀死：证据还在、影响面可控
kill -SIGSTOP <pid>
kill -SIGCONT <pid>          # 验证完再决定放行还是处置

# 删除前先确认占用与属性
lsof 可疑文件
ls -li 可疑文件
cat /proc/$$/mountinfo        # 确认挂载点（别在只读挂载上白折腾）

# 文件已删除但进程还在 → 从 /proc 恢复
cp /proc/<pid>/exe /tmp/recovered.bin

# 留证
sha256sum 可疑文件 > 可疑文件.sha256
strings -a -n 6 可疑文件 | grep -Ei 'http|\.onion|pass|key|token|bash -|/bin/sh'
```

**涉及恢复文件时不要再往原分区写入。** 要镜像就整盘镜像（`dd` + 只读挂载）。

---

## 9. 无文件 / 内存型驻留（别只靠 `ps`）

```bash
ls -l /proc/*/exe 2>/dev/null | grep -i 'memfd\|deleted'         # memfd_create 落地
for p in /proc/[0-9]*; do
  grep -qE 'rwxp' "$p/maps" 2>/dev/null && echo "可执行+可写映射: $p $(cat $p/comm 2>/dev/null)"
done
# 只看匿名可执行段（JIT/注入也可能命中，要人工分辨）
grep -l '^[0-9a-f]*-[0-9a-f]* rwxp 00000000 00:00' /proc/*/maps 2>/dev/null | head
```

**要点**：`ps`/`top` 看不到 ≠ 不存在。要坐实必须靠内存镜像（`volatility` 2.6）或 `/proc` 直查。

---

## 10. 高风险误区

- 不要一上来就 `kill -9` —— 会毁掉内存态证据，且子进程可能重启回来
- 不要只杀单个 pid，忽略**进程组/子进程**
- 不要把 `history` 当唯一证据（缺失非常常见）
- 不要认为 `ps`/`top` 看不到就一定不存在
- **不要先删样本再算哈希/记时间线**
- 不要忽视隐藏文件与点目录
- 不要把「有签名」「在系统目录下」当成无害依据（被替换的系统命令是常规手法）
- 不要在生产现场贸然执行会触发程序行为的检测工具

---

## 11. 善后与扩面

**同源风险优先清单**（少一个都算没做完）：
- 用**相同密码**的服务器/账号
- 部署**相同漏洞版本/相同服务**的资产
- 由**同一管理员/运维组**维护的服务器
- 与受害资产**有互信关系**的（SSH key、RDP 保存凭据、域信任、自动同步/备份代理）
- 案发时段与其**高频交互**的主机
- 同业务集群、同网段、同堡垒机可达资产

**最低动作**：改密 / 清 SSH key → 打补丁 → 限制监听地址 → 备份重要数据。
详见 `practice/post-incident-and-lateral.md`。
