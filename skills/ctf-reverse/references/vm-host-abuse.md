# 把宿主当 VM（自写版 · 先怀疑"这不是程序"，再动手反编译）

> ## 定位
> 用于**目标里"没有函数"、或"静态看到的算法和动态跑出来的不一致"**的题。
> 核心判断：**出题人可以把任何"会执行指令的东西"当虚拟机用** —— linker、编译器、打包器、
> 渲染器、引擎、驱动、甚至某个 API 的返回值。
> **先问"这东西是不是被当 VM 用了"，再决定用什么工具。**
>
> 适用条件：`file`/`strings` 结果与题面不符；反编译出来是数据不是代码；或"算法对不上"。
> 不适用：目标就是普通 ELF/PE 且静态动态一致（那是常规 RE，走 `patterns.md`）。

## 判据（三个信号，命中任一个就换思路）

| 信号 | 含义 | 第一动作 |
|---|---|---|
| **段里没有函数**，只有 relocation / 符号 / 节表 | 执行发生在**别的工具**里（链接器/加载器） | 别反汇编，去读**重定位表/符号表** |
| **静态看到的算法 ≠ 动态跑出来的** | 加载时被改了（TLS 回调 / init_array / SMC） | 查 **TLS callback**、`init_array`、`marshal`/`eval` 类运行时补丁 |
| **同一段数据"既是数据又是指令"** | polyglot / 双层用途 | 分别按两种解释各跑一遍，比对差异 |
| 架构对不上（x86 工具反不出逻辑） | 可能**根本不是这个架构** | 从字节模式认 ISA（见下方 SPARC 例） |

## 实例库（2026 实测，跨 6 个赛事 11 例）

| 把什么当 VM | 题 | 赛事 | 关键识别点 |
|---|---|---|---|
| **linker 重定位** | `Linkme` | KalmarCTF 2026 | `.flagchecker` 段**没有任何函数**，只有重定位条目；`.rel` 无可跳转控制流 → **所有重定位执行完才进入校验**；用 **`reloc_overflow` 报错当 oracle** |
| **LLVM 混淆** | `D3LLVM` | D^3CTF 2026 | OLLVM + 加密 MNN 模型 |
| **GPU shader** | `pixelflow` | 阿里CTF 2026 | 断点下在 `Shader$$Dispatch`，**shader 指令就是 VM 指令** |
| **SPARC 裸 ROM** | `JustROM` | LilacCTF 2026 | 靠字节模式认出 ISA：`9D E3 BF A0`=save、`81 E8 00 00`=restore → **SPARC**；Ghidra 反编译；再认出 **8 轮 ChaCha**（非标准 20 轮）+ 大端 key |
| **Python 字节码** | `ezPython` | LilacCTF 2026 | PyInstaller 解包（**魔数被删**）→ `crypto.py` 的 `a85decode` **运行时用 `marshal` 补丁改写 `myalgo.MX` 的字节码与操作码**（SMC）→ 还原补丁后的 XXTEA |
| **驱动内脚本** | `SU_Lock` | SUCTF 2026 | ⚠ **反例**：VM 只有 **5 个 opcode**（`0x10` MOV / `0x20` STORE_OUT / `0x30` LOAD_IN / `0x40` ASSERT_EQ / `0xFF` HALT），**是全场最简单的一环**；难的是外面 5 层壳（Inno Setup 密码 → Rust 反调 → RC4 双层 → 驱动 IOCTL → XXTEA 变种） |
| **syscall 返回值** | `vm-syscall` | VNCTF 2026 | 题目给受控 `syscall`，**rax 结果落回 VM 的 r1 寄存器** → 用 `shmget+shmat` 申请可写内存写 `/bin/sh` 再 `execve` |
| **自研 SystemKernel** | `LicenseVerifier` | SHCTF 3rd | Python 打包程序内的自研 VM，`sys.config` **双层加密字节码**（OP_PUSH/XOR/ADD/…/HALT） |
| **游戏引擎** | `DokiLogic` | CISCN×CCB 2026 | **renpy**：改 `renpy/script.py::load_file` 打印 `.rpyc`（pickle）→ 逻辑泄露；运行时释放 `.1.exe` 自删 → 文件监控抢拷贝 + UPX 脱壳 |
| **Java 栈式 VM** | `notjavaweb` | CISCN×CCB 2026 | 从**流量包正则提取 `vmContext` 指令流** → 本地补环境还原 VM → 读 `payload.enc` 生成并执行 |
| **CJK lambda 演算** | `interpreter-required` | DiceCTF 2026 | 中文古文写的 Church 编码 λ 演算；**题面明说"别跑会 OOM"** → 纯静态解析 + 记忆化求值（**不要按编码展开，要按语义求值**） |
| **NOR 为中心 + 函数指针表** | `SU_West` | SUCTF 2026 | 81 轮，每轮用 `order[round]` 作层 ID **调度函数指针表**；`~(a\|b)` 是唯一算子 → **整层抽象成 z3 约束 `f(input)==target`** |

## 通用打法（四步）

1. **判形态**：跑上面的三个信号。命中就**停止常规反汇编**。
2. **找"执行者"**：问"到底是谁在执行？"—— 链接器？渲染器？引擎？驱动？某个 API？
   然后去读**那个执行者的文档/源码**（例：`Linkme` 去读 MIPS `ld` 的 relocation 处理）。
3. **建同构模型**：把执行者的行为**用脚本重实现一遍**（例：`link.py` 同构 MIPS ld 逻辑），
   这样就能**离线跑、可搜索、可 diff**。
4. **找 oracle**：这类题常常"没有正常输出" → **报错 / 返回码 / 崩溃地址 / 计时 / 命名对象**
   都可以当 1 字节通道（见 `ctf-kit` 的「报错即 oracle」）。

## ★ 反教训：不要预设"VM 就是难的"

`SU_Lock` 我一开始贴的标签是"内核驱动 + 自研 VM 三项全中"，
读完官方 WP 发现 **VM 只有 5 个 opcode，是全题最简单的一环**。

**先数 opcode**：
- ≤ 10 个 opcode、无间接跳转 → **半小时能写完解释器**，别当难点
- 有间接跳转 / 自修改 / 多层嵌套 → 才是真难点

真正的难度通常在**外面那几层**：打包格式、反调试、多层加密、驱动交互、流量还原。

## 相关

- 静态 VM 模式（opcode 表、dispatch、handler 还原）→ `patterns.md`
- 运行时补丁 / SMC / oracle → `patterns-runtime.md`
- 「报错即 oracle」通用武器 → `skills/ctf-kit/SKILL.md`
