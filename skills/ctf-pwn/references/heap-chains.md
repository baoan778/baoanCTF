# 堆利用精细链（多步状态编排）—— 从 tcache poisoning 往上走

> **为什么单列**：`work/wp-2026/国内盲区题清单.md` 把我原清单漏掉的第 8 类标成「**出现频率最高**」——
> 堆精细链。我原来的能力只到 `ret2libc` / 单步 `tcache poisoning`；2026 国内题的形态是
> **把内存状态编排成一条多步链条**，每一步都为下一步铺路。这类题不是"新漏洞"，是"**会算状态**"。

## 0. 先判断：这题要的是"链条"还是"单步"

| 题面信号 | 你要做的 |
|---|---|
| 只有一次 add/delete 机会，但堆大小可选 | 单步（tcache poisoning / unsortedbin attack），别过度设计 |
| 多次 free + 不同大小 + 有"扩容/合并"路径 | **链条**：先列**状态机**再写 exp |
| 题目自带 `malloc.c` / 自定义 allocator | **读源码**（自研 allocator = 通读它，找它没做的事） |
| glibc ≥ 2.32 且要打 `__free_hook` 之外的东西 | 转向 `_IO_2_1_stdout_` / `_IO_list_all` / `House of Apple` 系列 |

## 1. 链条题的通用解法：先画状态表

**不要**从 payload 开始写。先在纸上/文件里列出：

```
堆块名 | size | 状态(alloc/free/tcache/unsorted/small/large) | 在哪个 bin | 头部字段被写过什么(prev_size/size/fd/bk/fd_nextsize/bk_nextsize)
```

然后逐条写"**这一步要达成什么状态**"，例（`StudentManagement` 那类）：

1. 把旧的大块（`0x110`）丢进 **unsortedbin**（通过扩容：`0x100 → 0x108`，旧块被 free）
2. 从 unsortedbin **切出小块** `0x70`（切割后剩余块回 unsortedbin）
3. **删 7 个** 填满 **tcache**（同 size 的 bin 被占满）
4. 再申请触发 **`malloc_consolidate`**（tcache 满 → 走 smallbin/unsorted 合并路径）
5. 合并后的块覆盖到目标地址 → 写入

**判据**：每一步之后都能用 `gdb` 打印出预期状态；打印不出来就是链条错了，别硬往下推。

## 2. 常见链条组件（按 glibc 版本）

| 组件 | 作用 | 触发条件 | 坑 |
|---|---|---|---|
| `tcache poisoning`（`fd` 覆写） | 任意地址分配 | tcache 有块且能改 `fd` | glibc ≥ 2.32 有 `key` 校验：需先 `free` 掉目标位置的块或伪造 key |
| `unsortedbin 切分` | 造出精确大小的块 / 泄漏 libc | 申请小于 unsorted 块的大小 | 切分后 `remainder` 的 `fd/bk` 会暴露 libc 地址 |
| `unsortedbin attack`（写 `bk`） | 任意地址写"一个大值" | glibc < 2.30（新版加校验） | 值不可控，通常只用于把 `_IO_list_all` 之类指向它 |
| `malloc_consolidate` | 合并 fastbin/相邻块 | tcache 满 + 请求较大 | 合并后 size/prev_inuse 变化会连锁影响后续步骤 |
| `House of Apple 2` | 走 `_IO_wfile_jumps` 拿到 FSOP 调用 | glibc ≥ 2.34（hook 被删） | 需要能写 `_IO_2_1_stdout_` 附近 + 触发 flush/exit |
| `House of Botcake` | 跨 tcache/unsorted 的重叠（overlap） | 有 UAF + 可控两次 free | 需要精确控制 tcache 数与 chunk 邻接 |
| 自研 allocator | 打它自己的元数据 | 题目自带 `malloc.c` | **先把它的 `free`/`malloc` 读一遍**，通常漏了某处校验（ez 级题目都在这儿） |

## 3. 工具与命令（本机）

```bash
# 本地跑：pwntools + gdb（WSL 无 pwndbg；Kali 虚拟机里有）
py exp.py                      # 带 pwn 的 python
# 观察堆状态（原生 gdb 也能用）
gdb -q ./chal -ex 'b *main+0x1' -ex r
(gdb) p/x *(void**)0x...       # 直接读 chunk 头
(gdb) heap                      # 若无 pwndbg，用 python 脚本读内存更稳
# 结构偏移速查
one_gadget /path/libc.so.6     # 已有
ROPgadget --binary ./chal | grep 'pop rdi'
patchelf --set-interpreter ./ld.so --set-rpath . ./chal    # 本地跑题目自带 libc
```
**注意**：本机 WSL 只有 glibc 2.43，**靶机 libc 不同**时链条行为会变（`tcache key`、`unsortedbin` 校验、hook 是否被删）。
拿到 leak 后先定版本：`libc-database`/`libc.rip`（本机未装库，可用在线或按 leak 末三位比对常见版本）。

## 4. 写 exp 的纪律

1. **每一步一个函数**：`step1_put_unsorted()`、`step2_split()`… 便于中途 dump 状态。
2. **每步打日志**：`log.info(f"after step2: chunk@{hex(addr)} size={size}")`，跑挂时一眼看出哪步错。
3. **先本地复现再打远程**（远程一崩容器就没了）。
4. 泄漏顺序：先 libc（unsortedbin `fd/bk`）→ 再 heap（`tcache fd` 或 `main_arena` 偏移）→ 再栈（`environ`）。
5. 链条里**任何一步依赖"差不多"都是错的**：地址/大小/计数必须是算出来的。

## 5. 练法（照着补手感，别只读）

- 找 `House of Apple 2` / `StudentManagement` 类题的公开 exp，**自己重跑一遍**并 dump 每步状态；
- 把 glibc 2.31 / 2.35 / 2.39 三个版本的 `malloc.c` 关键分支对照读一遍（`tcache_get`、`_int_malloc` 的 unsorted 循环、`malloc_consolidate`）；
- 自研 allocator 题：**先通读再动手**（这类题的漏洞几乎都是"作者忘了一个校验"，而不是需要高深技巧）。
