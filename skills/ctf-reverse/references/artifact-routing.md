# 按交付物形态先路由（三块盲区：WASM / EVM / .NET）

> **什么时候读这篇**：手上有附件、但**没想清楚该从哪个命令开始**时。
> 从「这是一道 reverse」开始想会走错路——先看**交付物是什么**，再选第一个命令。
>
> **来历**：2026-10-12 逐个实测本机 —— `wasm2wat / pywasm / wasmtime / solc / web3 / ilspycmd / dnSpy`
> **全无**，而 `SKILL.md` 里 `wasm|evm|.net` **零命中**。这三类载体一直在 CTF 里出现（rev / misc / blockchain），
> 没路由表就会出现「拿到 `.wasm` 去 `file` 一下然后卡住」。当日已补齐并用 `revkit` 收成一个入口（见 `TOOLS.md` 一·D）。
> 路由写法借自 REA（`github.com/morluto/rea`）的 skill：它值钱的不是那个 MCP，是
> 「**按交付物形态选第一个工具**」+「**把自己的输出边界写死**」。

| 交付物形态 | 第一个命令 | 边界（写死，别自己脑补） |
|---|---|---|
| `.wasm` / `.wat`（含浏览器 / WASM VM 题） | `revkit wasm <f> --info --decompile`；要喂输入看输出就直接 `--run FN --args 1,2` | WAT / 反编译**不是**原始源码；函数名来自 name 段，strip 过就没有 |
| EVM 字节码（blockchain 题） | `revkit evm <hex\|file> --disasm --push4`；签名算 selector 用 `--selectors "a(b,uint256)"`（**分号**分隔，逗号会被拆） | selector / 参数 / 可变性是**推断候选**；工具**不做链上查询**；要连 RPC 的题配合 [ctf-web 的 web3.md](../../ctf-web/references/web3.md) |
| `.dll` / `.exe`（.NET 托管程序） | `revkit dotnet <f> --types` → `--methods NS.Type` → `--il 'NS.Type::Method'`；字符串用 `--strings` | IL **不是** C# 源码；纯静态，**不加载程序集、不跑 CLR**。真要读 C# 级伪码 → Windows 侧 `GDA`（`tools/win/`） |
| `.asar` / Electron 目录 / 小程序包 | `wxapkg`；Electron 直接看解包后的 JS | 别把打包产物当源码读 |
| core dump / 崩溃现场 | `pggdb` 看寄存器；`pwnkit info` 看保护 | 历史 PID 不能当活进程用（这条是 REA 明写的防错） |
| 原生 ELF / PE（老路） | `file` → `pwnkit info` → IDA / Ghidra（Windows 侧）/ `r2 -A` | 三大引擎在 Windows 侧，在 WSL 里别干找 |

## 三块能力的底层是什么（写脚本时直接用）

| 载体 | 命令行 | Python（`py` = `tools/venv/bin/python`） |
|---|---|---|
| WASM | `wat2wasm wasm2wat wasm-decompile wasm-objdump wasm-interp wasm2c`（apt `wabt`） | `wasmtime`（进程内跑导出函数）、`pywasm`（纯 py，便于单步） |
| EVM | — | `pyevmasm`（反汇编/汇编）、`eth_utils`/`eth_abi`/`Crypto.Hash.keccak`（selector）、`web3`（RPC 交互） |
| .NET | — | `dnfile`（元数据表/堆）、`dncil`（IL 反汇编） |

> ⚠ 版本别照抄外部文档：REA 要求 WABT **1.0.42**，本机 apt 给的是 **1.0.36** ——
> 做题时以**本机实测版本**为准（`wasm2wat --version`），外部文档的版本号只是它们的依赖约束。

## `revkit` 用不到时的退路

- 只要 WAT：`wasm2wat x.wasm`；只要伪码：`wasm-decompile x.wasm`；要段信息：`wasm-objdump -x x.wasm`
- `.wat` → `.wasm`：`wat2wasm x.wat -o x.wasm`（`revkit wasm x.wat` 会自动做）
- EVM 只需要反汇编：`py -c "from pyevmasm import disassemble_all; [print(i.pc, i.name, i.operand) for i in disassemble_all(open('rt.bin','rb').read())]"`
- .NET 要 C# 级伪码：Windows 侧 `tools/win/GDA`（或 ILSpy/dnSpy 类工具）；`revkit dotnet` 给的是 IL + 元数据，**不是** C# 源码
