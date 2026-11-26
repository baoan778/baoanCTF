# 工控协议取证（S7comm / Modbus / DNP3）—— 铸网 2026 那类题

> **为什么单列**：`work/wp-2026/国内盲区题清单.md` 的四类漏掉盲区之一就是**工控/ICS**
> （代表：铸网 2026，**S7comm / Modbus**，火力发电场景）。这类题在 CTF 里是**流量/日志取证 + 协议语义**，
> 不需要真 PLC —— 会用 tshark 解协议 + 能读懂"哪个操作是危险操作"就够。

## 1. 分流：给了什么载体

| 载体 | 你要做的 |
|---|---|
| pcap（最常见） | `pcapmap` 先画像 → 看有没有 `s7comm` / `modbus` / `dnp3` / `enip` / `opcua` 协议 |
| 组态文件 / 工程文件（`.cxp`/`.ap*`/`*.scd`/`plc`） | 解包 + `strings` 找点位表/密码/逻辑 |
| 日志（HMI/SCADA） | 按时间排 → 找"写线圈/写寄存器"异常序列 |
| 固件 | 走 `ctf-binary`（binwalk + 文件系统） |

## 2. S7comm（西门子，端口 102）

```bash
tshark -r ics.pcap -Y s7comm -T fields -e frame.time_relative -e ip.src -e ip.dst \
       -e s7comm.param.func -e s7comm.header.rosctr
```
- **关键字段**：`s7comm.param.func`（`0x04` Read Var / `0x05` Write Var / `0x1a` 请求下载 / `0x1b` 下载块 / `0x1c` 下载结束 / `0x1d` 启动 / `0x1e` 内存复制 / `0x28` PLC 控制）
- **危险操作**（CTF 里常是"攻击者的动作"）：
  `Write Var 到 DB`（改设定值）· `PLC Stop`（`0x29`/`0x28` 里 `P_PROGRAM`）· `Download`（下装恶意逻辑）· `Memory Copy`
- **判据**：把"谁在什么时候写了哪个地址"排成时间线；**地址→点位名**要对照组态文件（`strings` 里常见 `DB1.DBW10` 之类）。
- 设备标识：`s7comm.cpu` / `s7comm.param.setup.comm`（模块型号、序列号）→ 能确认"哪台 PLC"。

## 3. Modbus/TCP（端口 502）

```bash
tshark -r ics.pcap -Y modbus -T fields -e frame.time_relative -e ip.src -e modbus.func_code \
       -e modbus.reference_num -e modbus.word_cnt -e modbus.regval_uint16
```
- **功能码**：`1` 读线圈 · `2` 读离散输入 · `3` 读保持寄存器 · `4` 读输入寄存器 ·
  **`5` 写单线圈 · `6` 写单寄存器 · `15` 写多线圈 · `16` 写多寄存器** ← 写操作是重点
- **无认证**：Modbus 本身没有鉴权 → 分析重点是**谁在写、写了什么值**（例如把频率/温度上限改大 → 触发事故）
- **判据**：`func_code ∈ {5,6,15,16}` 的源 IP 与时间点 = 攻击者在操作；配合 HMI 日志确认现场语义。

## 4. 其他协议（见到再查）

- `DNP3`（电力，端口 20000）：`dnp3.al.func`（`1` Read / `2` Write / `13/14` 冷热启动）· 有 `0x0D` 的安全认证（旧设备常关）。
- `EtherNet/IP + CIP`（罗克韦尔，44818）：`enip.cip.service`（`0x4C` CIP Write Tag / `0x52` 未连接发送）。
- `OPC UA`（4840）：看 `opcua.servicenodeid`，重点是 `WriteRequest` 与证书/匿名登录。
- `IEC 60870-5-104`（2404）：`asdu.typeid`（`45/46/49/50` 控制类指令）。

## 5. 输出纪律（应急/取证题要的交付物）

1. **时间线**：`时间 | 源 | 目标 | 协议 | 操作(读/写) | 地址/点位 | 值` —— 写操作优先。
2. **IOC**：攻击者 IP、被写地址清单、异常值、首次/末次出现时间。
3. **结论**：是"误操作"还是"恶意写入"？判据 = 写入值是否与 HMI 操作日志一致 + 是否有 `PLC Stop/Download`。
4. **不要编点位名**：组态文件里没有的点位就写地址原值，标"未映射"。

## 6. 本机现状

- ✅ `tshark` 4.x 自带 s7comm/modbus/dnp3/enip/opcua 解析器；`pcapmap` 可直接给协议分层与凭据面板。
- ❌ 没有真 PLC 模拟器（`snap7`/`pymodbus` 可 pip 装，做"本地重放/构造流量"时有用）：
  需要时 `py -m pip install pymodbus snap7`（构造 Modbus 请求，不用硬件）。
- 检索关键词：`S7comm write var CTF writeup` · `Modbus function 16 attack pcap` · `铸网 2026 S7comm 题解` · `PLC stop command forensics`
