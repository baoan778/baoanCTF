# Volatility 2 ↔ 3 分流（本机实况）

> **为什么单开一篇**：本机 `vol`（v3）和 `volatility`（v2）**同时装着**，两周法完全不同 ——
> 写错一条命令都跑不出来；而 v3 的符号依赖网络，离线就哑火。判据先看这篇再动手。
> **事实核验时间：2026-10-10**（下面带 ✅ 的都是本机真跑出来的原始输出，不是查文档抄的）。

---

## 0. 两条命令的实况（✅ 实测）

| | `vol` | `volatility` |
|---|---|---|
| 版本 | **Volatility 3 Framework 2.28.2** ✅ | **Volatility Foundation Volatility Framework 2.6** ✅ |
| 本体 | venv 里的 Python 程序（`tools/venv/bin/vol`，经 `tools/hand/vol` wrapper 暴露） | **Windows PE**：`tools/gobin/volatility.exe`，靠 WSL interop 跑；内部是 py2 的 `vol.py` ✅ |
| 写法 | `vol -f x.raw windows.pslist` | `volatility -f x.raw --profile=Win7SP1x64 pslist` |
| profile | **不需要**（自动从内核 PDB 定） | **必须** `--profile=`，用 `volatility --info` 挑 |
| 符号 | 需 ISF；Windows 从 **`msdl.microsoft.com`** 现取 PDB 再转（首次慢） | 内置各版本 profile，**离线可用** |
| 插件名 | `windows.pslist` / `windows.registry.hashdump` / `linux.bash` | `pslist` / `printkey` / `linux_pslist` |
| `--help` | ✅ 正常 | ❌ **崩**（`UnicodeDecodeError: 'utf8' codec can't decode byte 0xe7`，退出码 255）→ **只能用 `--info`** ✅ |
| 别名 | ⚠ **没有 `vol3` 这条命令** ✅（参考文档里那种 `vol3 -f ...` 在本机不存在） | 命令名就叫 `volatility` |

两个都有 `volshell`，但实现不同：v2 是 `volatility volshell` 插件；v3 是**独立命令** `$PI_LAB_ROOT/tools/venv/bin/volshell`（**没接到 PATH**，要用全路径）。

---

## 1. 分流判据（决策树，从默认往下走）

1. **默认用 `vol`（v3）** —— 免挑 profile、插件多、行为新。
2. **退到 `volatility`（v2）**，当出现下面任一条：
   - **v3 符号取不到**：离线 / `msdl.microsoft.com` 不通。v3 首次分析 Windows 镜像要联网取内核 PDB（本机 ✅ `http://msdl.microsoft.com/download/symbols/` 返回 **200**，缓存落在 `~/.cache/volatility3/symbols`）；真跑不了就 `--offline` 会直接失败，别在那儿等。
   - **需要 v2 独有插件**：`mftparser`（按 `--offset` 雕**已删除**文件，v3 的 `windows.mftscan` 做不到）、`shellbags`、`apihooks`、`clipboard`、`volshell`(v2 版)、`imagecopy`、`vaddump`、`procdump`。
   - **profile 已知且时间紧**：经典 CTF 镜像多半是 `Win7SP1x64` / `Win10x64`，v2 一条命令就出结果，不必等 v3 下符号。
3. **Linux 镜像**：v3 的 `linux.*` 需要**对应内核的 ISF**；本机 `volatility3/symbols/` 里**只有 `generic`**（✅ 无 linux ISF）→ 没联网/没有 `vmlinux` 时先 `vol -f x.raw banners.Banners` 认内核版本，再找匹配 ISF（`-s <目录>` 或 `--symbol-dirs`），认不出来就退回 v2 的 `linux_*` 插件或手工 `strings`+`vmlinux` 重建。

**两条都别盲试**：v2 错了会 `ERROR: volatility.debug : The requested file doesn't exist` / profile 不匹配；v3 错了是 `Unable to validate the plugin requirements` / 找不到符号表 —— 报错形态完全不同，看报错就知道自己踩了哪条。

---

## 2. 命令映射表（v2 → v3，常用的）

> v3 里 `windows.registry.*` 是一层◆嵌套命名空间，别漏掉 `registry.` 这截。

| 用途 | v2 (2.6) | v3 (`vol`) |
|---|---|---|
| 系统/内核信息 | `imageinfo` | `windows.info` |
| 进程列表 | `pslist` | `windows.pslist` |
| 进程树 | `pstree` | `windows.pstree` |
| 隐藏进程扫描 | `psscan` | `windows.psscan` |
| 交叉视图 | `psxview` | `windows.psxview` |
| 命令行 / 历史 | `cmdline` · `cmdscan` · `consoles` | `windows.cmdline` · `windows.cmdscan` · `windows.consoles` |
| 环境变量 | `envars` | `windows.envars` |
| 网络连接 | `netscan`（XP 用 `connections`/`connscan`） | `windows.netscan`（XP 也走它） |
| 句柄 | `handles` | `windows.handles` |
| 进程模块 | `dlllist` · `ldrmodules` | `windows.dlllist` · `windows.malware.ldrmodules` |
| 内核模块 | `modules` · `modscan` | `windows.modules` · `windows.modscan` |
| 文件对象 / 导出 | `filescan` · `dumpfiles` | `windows.filescan` · `windows.dumpfiles` |
| 进程转储 | `procdump` | `windows.pedump` · `windows.memmap --dump` |
| VAD | `vadinfo`/`vadwalk`/`vadtree`/`vaddump` | `windows.vadinfo` · `windows.vadwalk` · `windows.memmap --dump` |
| 恶意代码扫描 | `malfind` | `windows.malfind`（另有一族 `windows.malware.*`） |
| 服务 | `svcscan` | `windows.svcscan` · `windows.svclist` |
| 注册表 hive | `hivelist` · `hivescan` | `windows.registry.hivelist` · `.hivescan` |
| 读注册表键 | `printkey` | `windows.registry.printkey` |
| 用户活动 | `userassist` | `windows.registry.userassist` |
| amcache | `amcache` | `windows.amcache` · `windows.registry.amcache` |
| 密码哈希 | `hashdump` | `windows.hashdump` · `windows.registry.hashdump` |
| LSA 秘密 | `lsadump` | `windows.lsadump` · `windows.registry.lsadump` |
| 域缓存凭据 | `cachedump` | `windows.cachedump` · `windows.registry.cachedump` |
| 计划任务 | `scheduled_tasks` | `windows.scheduled_tasks` · `windows.registry.scheduled_tasks` |
| MFT | **`mftparser`**（可 `--offset` 雕已删） | `windows.mftscan`（**只扫内存里的 FILE 对象，无 `--offset`**） |
| Yara | `yarascan` | `windows.vadyarascan` · `windows.vadregexscan` ·（顶层 `yarascan`） |
| 权限 / 会话 / SID | `privs` · `sessions` · `getsids` | `windows.privileges` · `windows.sessions` · `windows.getsids` |
| 字符串 | `strings` | `windows.strings` |
| 回调 | `callbacks` | `windows.callbacks` |
| 时间线 | `timeliner` | `timeliner`（顶层，输出 JSON） |
| **v3 独有** | — | `windows.malware.{hollowprocesses,processghosting,skeleton_key_check,direct_system_calls,indirect_system_calls,unhooked_system_calls,svcdiff,suspicious_threads}` · `windows.etwpatch` · `windows.orphan_kernel_threads` · `windows.bigpools` · `windows.registry.certificates` |

---

## 3. 起步序列（复制即用）

**v3（默认）**
```bash
vol -f mem.raw windows.info                 # 先确认系统/内核/架构（也会触发取符号）
vol -f mem.raw windows.pslist | grep -viE "svchost|conhost"   # 先看异常进程
vol -f mem.raw windows.cmdline ; vol -f mem.raw windows.netscan
vol -f mem.raw windows.malfind               # 注入/可疑内存
vol -f mem.raw windows.filescan | grep -i flag ; vol -f mem.raw windows.mftscan | grep -i flag
# 模块没装时：--parallelism processes 提速；符号缓存：~/.cache/volatility3/symbols
```

**v2（兜底 / 离线 / 独有插件）**
```bash
volatility --info | grep -iE "Win7|Win10|WinXP"     # 挑 profile（别用 --help！）
P=Win7SP1x64
volatility -f mem.raw --profile=$P imageinfo
volatility -f mem.raw --profile=$P pslist
volatility -f mem.raw --profile=$P psxview          # 隐藏进程
volatility -f mem.raw --profile=$P netscan
volatility -f mem.raw --profile=$P hivelist         # 拿 SAM/SYSTEM 偏移
volatility -f mem.raw --profile=$P hashdump -y <SYSTEM_off> -s <SAM_off>
volatility -f mem.raw --profile=$P mftparser | grep -A2 <文件名>   # v3 做不到的雕已删
```

---

## 4. 本机坑清单（踩过的）

1. **`volatility --help` 必崩**（locale 的锅，`LANG=C.UTF-8`，py2 + cp936 界面串）✅ —— 不是镜像坏了。要插件/profile 列表就用 `--info`。
2. **没有 `vol3` 这条命令** ✅ —— 上游文档里的 `vol3 -f ...` 在本机一律改成 `vol -f ...`。
3. **v3 首次分析 Windows 镜像会联网取符号**（走 `msdl.microsoft.com`）；网络不通/要离线 → 直接走 v2，别等 v3 超时。
4. **v2 是 Windows PE** ✅：给它的路径写 Linux 风格 `/mnt/...` 一般没事（interop 会转），但**别把管道喂进去当参数**；exit code 可能不老实。
5. **`v3` `windows.mftscan` ≠ v2 `mftparser`**：前者只枚举内存中的 MFT FILE 对象，**不能按 offset 雕已删除文件** —— 「已删文件恢复」这条路只有 v2 有。
6. `volshell`（v3）没接进 PATH：用全路径 `$PI_LAB_ROOT/tools/venv/bin/volshell`。

---

## 5. 尚未验证（别当结论）

- v3 在本机**对真实 Windows 镜像端到端跑通**还没做过（本机无镜像）—— 符号可达是 ✅，但「取到 PDB → 转 ISF → 插件出结果」这一整链未实测。
- `windows.zip`（839 MB）是 volatilityfoundation 的**离线整包**（✅ 200 可达），但要验证 v3 2.28.2 是否真会用它（源码里 Windows 符号源是 `SYMBOL_SERVER_URL = http://msdl.microsoft.com/download/symbols`）；离线场景建议实测后把结论补到这节。
- Linux 内存镜像的 ISF 获取路径（`-s` 指本地 / 自动 dwarf）未实测。
