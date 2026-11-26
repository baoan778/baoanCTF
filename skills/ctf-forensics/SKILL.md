---
name: ctf-forensics
description: 取证题打法：先 file/binwalk 摸形状 → 按类型分流（流量 pcap / 磁盘镜像 / 内存镜像 / 日志 / 图片元数据）→ 遵循「载体重放 → 提取对象 → 解码 → 得到 flag」的还原链。当给了 pcap/镜像/日志/一堆文件、或 flag 不在明面上要逐层还原时参考。解题时优先自行分析判断，本 skill 提供取证还原打法参考，不限制跨题型思考。
license: MIT
compatibility: ctf-agent（Windows 宿主 + WSL 执行；本 Skill 只读，命令/工具/网络可用性按题探测）
allowed-tools: Bash Read Write Edit Grep Glob
metadata:
  user-invocable: "false"
---

# 取证题

## 起手

1. `file *` + `binwalk <file>` —— 先看是什么容器、里面嵌了什么
2. 按类型分流：
   - **流量（pcap/pcapng）**：**先 `pcapmap x.pcap --export out/`** —— 一条命令给七块面板
     （分层 / 会话 / **明文凭据** / **文件传输：声明大小 vs 实抓 · 426 中断 · REST 续传** / 异常与隧道迹象 / 下一步提示），
     再按它的提示跟流：`tshark -r x.pcap -q -z follow,tcp,raw,<stream>`。
     ★ 端口要按**被动端口**（229/227 里那个）关联数据通道，别按 IP 汇总（会把一个客户端的多次传输算混）
   - **磁盘/文件系统镜像**：`binwalk -e`、`unsquashfs`、`fls`/`icat`（sleuthkit）；
     找 `/etc/passwd`、启动脚本、硬编码凭据、删除文件
   - **内存镜像**：本机 **v3 与 v2 并存**，先看 [vol2-vol3.md](references/vol2-vol3.md) 的分流判据 ——
     **默认 `vol`（Volatility 3，`vol -f x.raw windows.pslist`，免 profile、插件名带 `windows.` 前缀）**；
     只有「v3 取不到符号（离线）/ 要 v2 独有插件（`mftparser` 按 offset 雕已删、`shellbags`、`apihooks`、`clipboard`）/ profile 已知且赶时间」才退
     `volatility`（**Volatility 2.6**，`volatility -f x.raw --profile=<Profile> pslist`，用 `volatility --info` 挑 profile）。
     ⚠️ `volatility --help` 在本机会因 locale 报 `UnicodeDecodeError`（**别用 `--help`，用 `--info`**）
     ⚠️ 本机**没有 `vol3` 这条命令**，上游文档的 `vol3 -f ...` 一律改写成 `vol -f ...`）
   - **日志**：先按时间排序再找异常（登录失败密集、半夜的外连、异常进程名）
   - **图片/音频**：`exiftool` + `zsteg`/`steghide`/`jsteg`/`outguess`/`f5`（JPEG 三件套）+ `ffmpeg`/`sox` 频谱；**题面带文字就 OCR**（`tesseract`，中文用 `-l chi_sim`）
3. 常见还原链：**载体重放 → 提取传输对象/命令 → 反编译样本拿密钥 → 用密钥解外传数据 → flag**

## 本类工具

> 工具以 $PI_LAB_ROOT/tools/TOOLS.md 为准；tools/bin 已在 PATH 里，命令名直接用。

```
poc "<组件>"                 # 组件 → 本地 POC 索引
poc --cve CVE-xxxx-xxxx      # CVE → 本地 POC 索引
```

pi-lab 自带命令：`foremost`（文件雕复）、`vol`（内存取证 **Volatility 3**，默认用它）、`volatility`（内存取证 **Volatility 2.6**，离线/独有插件兜底）、`pycdc`/`pycdas`（.pyc 反编译/反汇编）、`pyinstxtractor`（PyInstaller 解包）、`poc`（本地 POC 索引）。
系统命令已装：`binwalk exiftool tshark steghide zsteg jsteg outguess f5 ffmpeg sox multimon-ng 7z unzip file strings tesseract nc socat openssl gdb objdump readelf r2`。
Python 库已装：系统 `python3` 有 `requests bs4 lxml yaml sympy numpy scipy PIL capstone`；`py`（= tools/venv/bin/python）在此之上多 `pwn z3 Crypto gmpy2 ecdsa ropgadget unicorn`。要 pwn/z3/crypto 的脚本一律用 `py xxx.py` 跑，系统 `python3` 没有这些库。

## 坑

- 只看 `strings` 找 flag 就交：取证题的分数在**还原链**上，flag 通常要解密出来
- pcap 只看 HTTP：DNS 隧道、ICMP 隐写、TLS 里的明文 SNI 都是常见外传通道
- 图片题直接上 `zsteg` 而不先 `exiftool`：元数据里就写着提示（甚至答案）很常见

## 时间预算

取证题容易无限深挖。**每 15 分钟回到"我现在的还原链走到第几步"**，
把已经排除的载体写进黑板 —— 排除也是产出。

## 参考材料（按路线取）

> **按路线取**：只读你当前这条路线的那几篇（每篇都是几千字的实战笔记），不要全部预加载。

**磁盘 / 内存**
- [vol2-vol3.md](references/vol2-vol3.md) — **内存镜像先读这篇**：本机 `vol`(v3)/`volatility`(v2) 两条命令的实况、**分流决策树**、v2→v3 插件映射表、起步序列、6 条本机坑（含符号/离线判据）
- [disk-and-memory.md](references/disk-and-memory.md) — Core disk/memory forensics (Volatility, disk mounting/carving, VM/OVA/VMDK
- [disk-advanced.md](references/disk-advanced.md) — Advanced disk and memory techniques (deleted partitions, ZFS forensics
- [disk-recovery.md](references/disk-recovery.md) — Disk recovery and extraction patterns (LUKS master key recovery

**网络流量分析**
- [network.md](references/network.md) — Network forensics basics (tcpdump, TLS/SSL keylog decryption
- [network-advanced.md](references/network-advanced.md) — Advanced network forensics (packet interval timing encoding
- [ics-protocols.md](references/ics-protocols.md) — **工控协议取证**（S7comm / Modbus / DNP3 / ENIP / OPC UA）：哪些功能码是「危险写操作」、如何把「谁在什么时候写了哪个地址」排成时间线、IOC 与结论怎么落

**隐写（图 / 音频 / 通用）**
- [steganography.md](references/steganography.md) — General steganography (binary border stego, PDF multi-layer stego
- [stego-advanced.md](references/stego-advanced.md) — Advanced steganography part 1: audio and signal techniques (FFT frequency…
- [stego-advanced-2.md](references/stego-advanced-2.md) — Advanced steganography part 2: video, image transform
- [stego-image.md](references/stego-image.md) — Image-specific steganography (JPEG unused DQT table LSB

**系统取证（Windows / Linux）**
- [windows.md](references/windows.md) — Windows forensics (registry, SAM, event logs, recycle bin
- [linux-forensics.md](references/linux-forensics.md) — Linux/app forensics (log analysis, Docker image forensics, attack chains

**信号 / 硬件 / 外设 / 3D**
- [signals-and-hardware.md](references/signals-and-hardware.md) — Hardware signal decoding with decode code (VGA frame parsing
- [peripheral-capture.md](references/peripheral-capture.md) — USB/HID/Bluetooth peripheral traffic reconstruction (USB HID mouse/pen dr…
- [3d-printing.md](references/3d-printing.md) — 3D printing forensics (PrusaSlicer binary G-code, QOIF, heatshrink)

**综合与工具**
- [shared-playbook.md](references/shared-playbook.md) — 【我们的】③哈希（碰撞/长度扩展/弱算法）④PRNG（LCG/截断输出）⑤**自定义代数结构**（置换/矩阵/多项式/CRC）。
- [btfly-SKILL.md](references/btfly-SKILL.md) — 【BTFly 原路由文件，备查】Provides digital forensics and signal analysis techniques…

> **卡住时**（同路线失败 ≥2 次无新信息 / references 翻完仍不知下一步 / 需版本相关已知漏洞）：先 `kb search` → `poc` → 本类 references → 再读 [`../ctf-kit/references/retrieval-sources.md`](../ctf-kit/references/retrieval-sources.md)（各题型来源表 + 查询式约定）。

## 纪律（所有题型共用，逐条照做）

> **正本：[`../ctf-kit/references/common-discipline.md`](../ctf-kit/references/common-discipline.md)**（能力检查 / 产物只写 cwd / 结论三级 / flag 逐字 / 卡住换面 / 机器兜底）。
> 本节不再抄写内容 —— 2026-11-09 实测 9 份副本里已有 1 份漂移，改一处漏 8 处。**读那一份就够了。**
