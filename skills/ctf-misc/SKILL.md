---
name: ctf-misc
description: 杂项打法：编码/隐写/图片/压缩包/沙箱逃逸（pyjail、pickle jail）/问卷类题。原则是先枚举载体再猜内容；压缩包先看是不是伪加密或明文攻击；沙箱题先读源码再构造绕过。当题型是 misc、给了图片/压缩包/编码串/在线执行框时参考。解题时优先自行分析判断，本 skill 提供杂项打法参考，不限制跨题型思考。
license: MIT
compatibility: ctf-agent（Windows 宿主 + WSL 执行；本 Skill 只读，命令/工具/网络可用性按题探测）
allowed-tools: Bash Read Write Edit Grep Glob
metadata:
  user-invocable: "false"
---

# 杂项题

## 硬禁止（事前自查，先看这 6 条）

| 禁止 | 撞上了怎么办 |
| --- | --- |
| 拿到附件不 `file` / `binwalk` 就猜 | 赛场实测最常漏的就是「打开看看」；三条固定：`file`、`binwalk`、`7z l` |
| 只凭后缀判断类型 | 后缀是别人给的，`file` 才是本体 |
| 同一解码层试 **≥2 次不换层** | base64 解不出不是再 base64 一次；换层（hex/URL/UTF-16LE/变表）或换载体 |
| 大文件 / 大输出整读 | 先 `head -50` / `wc -l` / `grep -c` / `sort \| uniq -c \| sort -rn \| head` |
| 猜一个 flag 形状拼上去 | flag 必须逐字出现在输出里；没出现就如实说「没打出来」 |
| 题面提到文字却不 OCR 就断言「图里没东西」 | `py` 走 `pyzbar`/`zxingcpp`（本机无 `zbarimg`），或人工看通道图 |

## 完成定义（可打勾，缺一项别说「做完了」）

- [ ] 载体枚举完整（`file` / `binwalk` / `7z l` / `exiftool`，各留输出）
- [ ] 每层解码有中间产物落盘 `work/<pid>/`（能回看是哪一层出的东西）
- [ ] flag 逐字出现在输出里并**已提交**
- [ ] 试过的层 / 口令 / 通道已记黑板（含失败的，防下轮重试）
- [ ] 结构性判断（SOF 宽高、伪加密位、CRC）有对照证据
- [ ] `fgs solved` + **平台回执**


## 起手

1. **先枚举载体**：`file *`、`binwalk`、`7z l`、`exiftool` —— 是什么、有几层
2. 按子类分流：
   - **编码串**：逐层试 base64/base32/hex/URL/HTML entity/摩斯/凯撒/栅栏；
     注意**变表 base64** 与 `UTF-16LE` 双编码（同一个附件按 ASCII 和 UTF-16 各扫一遍）
   - **图片**：`exiftool` → `zsteg`（PNG/BMP LSB）→ `steghide`（JPEG/BMP/WAV，可带口令）→ **JPEG 专用三件套**：`jsteg`（系数 LSB，无口令）/ `outguess`（带口令，统计保持）/ `f5`（矩阵编码，抗卡方检测；用法 `f5 d <stego.jpg> -p <口令>`）→ 通道分析（PIL/numpy/cv2 提 LSB、看 SOF/量化表异常）；
     题面出现文字就 **OCR**；长宽比不对时怀疑 **SOF0 宽高被改**（JPEG 只改头、重新分箱还原）
   - **压缩包**：先 `7z l` 看是否伪加密（本地文件头 flag 位）→ 明文攻击（有已知明文时）/ 弱口令
     （本机**没有** `zip2john` / `fcrackzip`：有已知明文就优先走**明文攻击**（`bkcrack` 思路，或 `py` 手搓）；
     否则用 `py` 写 `zipfile` 循环拉字典 —— `john` 虽在，但没配 2john 脚本）/ CRC 爆破（小文件）
   - **沙箱逃逸（pyjail / pickle jail）**：**先读源码**看黑名单怎么写的、允许哪些字符/名字，
     再构造：`__class__.__mro__` 链、`getattr` 拼接、`format` 取值、
     受限 `find_class` 用 BUILD/slotstate 绕过
   - **问卷类**：题面就是线索，答案常在地图/历史/百科，先确定"要问什么"再搜
3. flag 常在：解码结果、图片通道、压缩包注释、文件名、EXIF 的 UserComment

## 起手式补丁：**先打开看**（AI 的实测短板）

> 赛场实测（TMC 冠军之夜）：「拿到压缩包后，**AI 缺乏'打开看看里面有什么'的意识**。」

所以拿到任何附件，**第一件事固定是这三条**，不许跳过：

```bash
file <附件>            # 1. 它到底是什么（别信后缀）
binwalk <附件> | head   # 2. 里面套了什么
# 3. 压缩包一律先解出来数文件 + 看最小的/名字怪的/时间最新的那几个
unzip -l x.zip | head -30 ; 7z l x.zip 2>/dev/null | head -30
```

判据：**没有"打开看过"的动作，就不许说"这题没思路"**。

---

## 本类工具

> 工具以 $PI_LAB_ROOT/tools/TOOLS.md 为准；tools/bin 已在 PATH 里，命令名直接用。

```
foremost <镜像>                    # 文件雕复（carving）
volatility <命令>                  # 内存取证（Volatility 2.6）
hashid                             # 哈希类型识别
py 脚本.py                         # = tools/venv/bin/python；图片/通道分析（PIL/numpy/scipy）等都这样跑
poc "<组件>"                       # 组件 → 本地 POC 索引
poc --cve CVE-xxxx-xxxx            # CVE → 本地 POC 索引
dalfox / commix                    # web 混题：XSS / 命令注入
```
系统命令已装：`binwalk exiftool tshark steghide zsteg jsteg outguess f5 tesseract strings 7z unzip file curl wget nc socat openssl john hashcat python3`；
音视频链：`ffmpeg sox mediainfo multimon-ng`（DTMF/Morse 自动解码）；PDF：`pdftotext pdfimages qpdf mutool`；加密 zip 已知明文：`bkcrack`（详见 `tools/TOOLS.md` 一·B）。
Python 库已装：系统 `python3` 有 `requests bs4 lxml yaml sympy numpy scipy PIL capstone`；`py`（= `tools/venv/bin/python`）另有 `pwn(pwntools) z3 Crypto(pycryptodome) gmpy2 ecdsa ropgadget unicorn`。系统 `python3` 没有 `pwn z3 Crypto gmpy2 unicorn ropgadget ecdsa`——需要时用 `py` 跑。

## 坑

- 拿到一串像 flag 的就交：先核对 flag 形状（前缀/花括号/字符集）并确认它逐字出现在真实执行输出里
- 图片题不做守恒校验：改过的图要确认"改回来之后内容可读"，否则是在猜

## 参考材料（按路线取）

> **按路线取**：只读你当前这条路线的那几篇（每篇都是几千字的实战笔记），不要全部预加载。

**编码 / 加密串 / 冷门格式**
- [encodings.md](references/encodings.md) — Encodings, QR codes, esolangs, UTF-16 tricks, BCD encoding
- [encodings-advanced.md](references/encodings-advanced.md) — Verilog/HDL, Gray code cyclic encoding, RTF custom tag extraction

**沙箱逃逸（pyjail / bashjail）**
- [pyjails.md](references/pyjails.md) — Python jail/sandbox escape techniques, quine context detection
- [bashjails.md](references/bashjails.md) — Bash jail/restricted shell escape techniques, HISTFILE file read trick

**游戏 / 自制 VM / 交互题**
- [games-and-vms.md](references/games-and-vms.md) — WASM patching, Roblox place file reversing, PyInstaller, marshal analysis
- [games-and-vms-2.md](references/games-and-vms-2.md) — Cookie checkpoint game brute-forcing, Flask cookie game state leakage
- [games-and-vms-3.md](references/games-and-vms-3.md) — memfd_create packed binaries
- [games-and-vms-4.md](references/games-and-vms-4.md) — Part 4 (2018-era): XSLT as Turing-complete VM

**提权**
- [linux-privesc.md](references/linux-privesc.md) — Sudo wildcard parameter injection (fnmatch), crafted pcap for sudoers.d

**DNS / 射频**
- [dns.md](references/dns.md) — DNS exploitation (ECS spoofing, NSEC walking, IXFR, rebinding, tunneling)
- [rf-sdr.md](references/rf-sdr.md) — RF/SDR/IQ signal processing (QAM-16, carrier recovery, timing sync)
- [llm-perception-verification.md](references/llm-perception-verification.md) — LLM 感知结果交叉验证（OCR/ASR 防幻觉）；配套可执行件 `board/percept.py`（`--selftest` 12 组断言）

**平台导航与综合**
- [ctfd-navigation.md](references/ctfd-navigation.md) — CTFd platform API navigation without browser: detection, token auth
- [shared-playbook.md](references/shared-playbook.md) — 【我们的】③哈希（碰撞/长度扩展/弱算法）④PRNG（LCG/截断输出）⑤**自定义代数结构**（置换/矩阵/多项式/CRC）。
- [btfly-SKILL.md](references/btfly-SKILL.md) — 【BTFly 原路由文件，备查】Provides miscellaneous CTF challenge techniques for probl…

> **卡住时**（同路线失败 ≥2 次无新信息 / references 翻完仍不知下一步 / 需版本相关已知漏洞）：先 `kb search` → `poc` → 本类 references → 再读 [`../ctf-kit/references/retrieval-sources.md`](../ctf-kit/references/retrieval-sources.md)（各题型来源表 + 查询式约定）。

## 纪律（所有题型共用，逐条照做）

> **正本：[`../ctf-kit/references/common-discipline.md`](../ctf-kit/references/common-discipline.md)**（能力检查 / 产物只写 cwd / 结论三级 / flag 逐字 / 卡住换面 / 机器兜底）。
> 本节不再抄写内容 —— 2026-11-09 实测 9 份副本里已有 1 份漂移，改一处漏 8 处。**读那一份就够了。**
