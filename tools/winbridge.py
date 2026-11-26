#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""winbridge.py —— 调 Windows 侧的逆向工具（IDA Pro / Ghidra），给 WSL 用

为什么要它
----------
IDA Pro 8.3 与 Ghidra 11.4 **只有 Windows 版**（Linux 版 IDA 是商业另卖，Ghidra 要 JDK），
本机这两个都装在 Windows 盘（`D:\\AI\\...`）。而我在 WSL 里干活 ——
好在 WSL interop 能直接执行 `.exe`（`tools/make-bin.sh` 早就在用这条路挂 gobin 里的 .exe）。

实测（2026-09-30）：
  · `idat64.exe -B target.elf` → 生成 `target.elf.asm`（反汇编清单）+ `target.elf.i64`（数据库），**不弹 GUI**
  · `support\\analyzeHeadless.bat` 能跑起来（它自己找到了 Windows 上的 JDK 26）
  · 有 Hex-Rays 时可用 `--decompile` 导出伪 C（`<name>.decomp.c`）

用法
----
    winbridge.py check                       # 找 IDA / Ghidra / Java，打印路径（不装东西）
    winbridge.py ida <binary>                # 批量反汇编 → <binary>.asm
    winbridge.py ida <binary> --decompile    # 再导出伪 C → <binary>.decomp.c
    winbridge.py ida <binary> --script x.idc # 跑自定义脚本（-S）
    winbridge.py ghidra <binary>             # import + 自动分析（默认临时项目）
    winbridge.py ghidra <binary> --postscript Dump.java --keep

路径覆盖：`PI_LAB_IDA` / `PI_LAB_GHIDRA` / `PI_LAB_WIN_TMP`。
找不到就打印候选清单与修法 —— 不猜、不装、不静默。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
#   自包含（自己 import sys），不依赖本文件是否已 import sys 或用别名。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = Path(__file__).resolve().parents[1]
WIN = LAB / "tools" / "win"          # ★ 本仓自己的副本（2026-09-30 从 D:\AI 拷来，不进 git）

# ★ 2026-11-07 大修：原候选全是 **WSL 视角**的 /mnt/d/... 且写死 v8.3。
#   实测本机（= 项目唯一支持的部署面：Windows 宿主 D:\baoan）：
#     · 根本没有 /mnt/... 这层（那是 WSL 的挂载点，Git Bash 里不存在）
#     · 也没有 wslpath 命令 → winpath() 一调就 SystemExit，桥必然"不通"
#     · 真实 IDA 是 **D:\IDA Professional 9.4**，且 9.x 已把 ida64.exe 并回 ida.exe，
#       批量文本模式是 idat.exe
#   所以候选改成"跨平台两种视角都列"，由 _win_root() 决定用哪一套。
_DRIVES = [Path("D:/"), Path("C:/"), Path("/mnt/d"), Path("/mnt/c")]
_ROOT_NAMES = [
    "IDA Professional 9.4", "IDA Professional 9.3", "IDA Professional 9.2",
    "IDA Pro 9.4", "IDA Pro 9.3", "IDA Pro 8.4", "IDA Pro 8.3",
]

def _win_roots() -> list[Path]:
    out: list[Path] = []
    for d in _DRIVES:
        for n in _ROOT_NAMES:
            out.append(d / n)
    return out

# 顺序有意义：**先看仓内副本**（不怕外部目录被移走/改名），再看安装位置
IDA_CANDIDATES = [
    WIN / "IDA_Pro_v8.3_Portable/ida64.exe",
    *[r for r in _win_roots()],
    *[r / "ida64.exe" for r in _win_roots()],
    *[r / "idat.exe" for r in _win_roots()],
    Path("D:/AI/gongju/IDA_Pro_v8.3_Portable/ida64.exe"),
    Path("/mnt/d/AI/gongju/IDA_Pro_v8.3_Portable/ida64.exe"),
]
GHIDRA_CANDIDATES = [
    *sorted(WIN.glob("ghidra_*")),      # 版本号变了也不用改代码
    *[d / n for d in _DRIVES for n in ("ghidra_11.4.1_PUBLIC", "ghidra_11.3_PUBLIC",
                                       "ghidra_11.2.1_PUBLIC", "ghidra_11.1_PUBLIC")],
    Path("/mnt/d/AI/gongju/ghidra_11.4.1_PUBLIC"),
]
# 临时区：优先仓库内（跨平台都可写），别默认往 /mnt/d 这种可能不存在的路径写
WIN_TMP = Path(os.environ.get("PI_LAB_WIN_TMP", str(LAB / "work" / "_win-tmp")))


def winpath(p: Path) -> str:
    r"""路径 → Windows 路径（IDA/Ghidra 是 Windows 程序，只认反斜杠绝对路径）。

    ★ 2026-11-07：原来**只**走 `wslpath -w`，而 wslpath 只存在于 WSL。本机是
    Windows 宿主（项目唯一支持的部署面），实测 `command -v wslpath` 无输出 →
    每次调用都 sys.exit，桥"不通"其实是转换器不存在，不是找不到 IDA。
    现在三级：wslpath（WSL）→ 已是 Windows 盘符路径就直接用（Git Bash / Cygwin 的
    /d/xxx 形如 POSIX，要转 D:\xxx）→ 交给 pathlib/系统。
    """
    s = str(p)
    # 已经是 D:\... 这种原生 Windows 路径 → 原样返回
    if len(s) >= 2 and s[1] == ":":
        return s
    try:
        r = subprocess.run(["wslpath", "-w", s], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=20, check=True)
        if r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    # Git Bash 风格 /d/baoan/x 或 /mnt/d/baoan/x → D:\baoan\x
    m = re.match(r"^/(?:mnt/)?([a-zA-Z])/(.*)$", s)
    if m:
        return f"{m.group(1).upper()}:\\" + m.group(2).replace("/", "\\")
    # 实在不认识就原样给出去（让 IDA 自己报错，比我们瞎猜好）
    return s


def find_ida() -> Path | None:
    env = os.environ.get("PI_LAB_IDA")
    cands = ([Path(env)] if env else []) + IDA_CANDIDATES
    for c in cands:
        if c.is_dir():
            c = c / "ida64.exe"
        if c.exists():
            return c
    return None


def find_ghidra() -> Path | None:
    env = os.environ.get("PI_LAB_GHIDRA")
    for c in ([Path(env)] if env else []) + GHIDRA_CANDIDATES:
        if (c / "support" / "analyzeHeadless.bat").exists():
            return c
    return None


def find_java() -> Path | None:
    for p in sorted(Path("/mnt/c/Program Files/Java").glob("*/*/bin/java.exe")) + \
            sorted(Path("/mnt/c/Program Files/Java").glob("*/bin/java.exe")):
        if p.exists():
            return p
    return shutil.which("java") and Path(shutil.which("java"))  # type: ignore[arg-type]


def cmd_check() -> int:
    ida, gh, jv = find_ida(), find_ghidra(), find_java()
    print("── Windows 侧逆向工具（WSL interop 调用）──")
    print(f"  IDA Pro   : {ida or '✗ 没找到（候选：' + ' · '.join(str(c) for c in IDA_CANDIDATES) + '）'}")
    print(f"  Ghidra    : {gh or '✗ 没找到（候选：' + ' · '.join(str(c) for c in GHIDRA_CANDIDATES) + '）'}")
    print(f"  Java      : {jv or '✗ 没找到（Ghidra 需要它）'}")
    if ida:
        print("  ↳ 用法    : ida <binary> [--decompile] （idat64.exe 文本模式，不弹窗）")
    if gh:
        print("  ↳ 用法    : ghidra <binary> [--postscript X.java]")
    print(f"  Windows 临时区: {WIN_TMP}（可用 PI_LAB_WIN_TMP 改）")
    extras = sorted(p.name for p in WIN.glob("*") if p.is_dir()) if WIN.is_dir() else []
    if extras:
        print(f"  仓内副本 tools/win/：{' · '.join(extras)}")
        print("     ↳ CFF_Explorer=.NET 反编译 · GDA=Android dex · DIE/ExeinfoPe=壳与编译器识别 · LogParser=Windows 日志")
    print("\n注：pwndbg **不在** Windows/WSL 的普通目录里 —— 它大概率在那台 Kali 虚拟机镜像里")
    print("    （D:\\AI\\kali\\kali_zss完整版\\*.vmdk）。要用就先起 VM，本机不重复装。")
    return 0 if (ida or gh) else 3


IDA_DECOMP_SCRIPT = r'''
import ida_auto, ida_funcs, idautils, ida_pro

ida_auto.auto_wait()
out = open(r"{out}", "w", encoding="utf-8", errors="replace")
n_ok = n_bad = 0
try:
    import ida_hexrays
    have = True
except Exception as exc:
    have = False
    out.write("// Hex-Rays 不可用：%s\n" % exc)
if have:
    for ea in idautils.Functions():
        try:
            cf = ida_hexrays.decompile(ea)
            out.write("// %s  @ %#x\n%s\n\n" % (ida_funcs.get_func_name(ea), ea, cf))
            n_ok += 1
        except Exception as exc:
            out.write("// %#x 反编译失败：%s\n" % (ea, exc))
            n_bad += 1
out.write("// 完成：成功 %d / 失败 %d\n" % (n_ok, n_bad))
out.close()
ida_pro.qexit(0)
'''


def cmd_ida(binary: Path, decompile: bool, script: str | None, timeout: int = 1800) -> int:
    ida = find_ida()
    if not ida:
        print("✗ 找不到 IDA（设 PI_LAB_IDA=<ida64.exe 路径>）", file=sys.stderr)
        return 3
    binary = binary.resolve()
    if not binary.exists():
        print(f"✗ 没有这个文件：{binary}", file=sys.stderr)
        return 2
    # 实测（2026-09-30）：传 Windows 绝对路径给 cmd.exe 会「系统找不到指定的路径」；
    # 正确姿势是 —— 以 WSL 路径执行 exe（interop 搞定）、cwd 落在目标目录、参数用**相对路径**。
    args = ["-B"]                                     # 批量模式：不弹 GUI，产出 .asm/.i64
    if decompile:
        sp = binary.parent / (binary.name + ".decomp.py")
        sp.write_text(IDA_DECOMP_SCRIPT.format(out=winpath(binary.parent / (binary.name + ".decomp.c"))),
                      encoding="utf-8")
        args += ["-S" + sp.name]
    elif script:
        src = Path(script).resolve()
        args += ["-S" + str(src.relative_to(binary.parent)) if src.parent == binary.parent
                 else "-S" + winpath(src)]
    args.append(binary.name)
    print(f"→ {ida.name} {' '.join(args)}  (cwd={binary.parent})")
    # ★ 2026-11-18：IDA 全量分析可能卡死（GUI 弹窗/许可证等待）→ 必须限时，
    #   否则 lane 脚本会静默挂住一整晚。--timeout 可放宽。
    rc = subprocess.run([str(ida), *args], cwd=str(binary.parent), timeout=timeout).returncode
    outs = [p for p in sorted(binary.parent.glob(binary.name + "*"))
            if p.name != binary.name and p.suffix in (".asm", ".i64", ".idb", ".c", ".py")]
    print(f"rc={rc}；产出：")
    for p in outs:
        print(f"  {p}  ({p.stat().st_size} 字节)")
    if not outs:
        print("  （没有产出 —— 看上面的 rc 与 IDA 自己的报错）")
    return rc


def cmd_ghidra(binary: Path, postscript: str | None, project_dir: Path | None, keep: bool,
               timeout: int = 1800) -> int:
    gh = find_ghidra()
    if not gh:
        print("✗ 找不到 Ghidra（设 PI_LAB_GHIDRA=<ghidra_*_PUBLIC 目录>）", file=sys.stderr)
        return 3
    binary = binary.resolve()
    if not binary.exists():
        print(f"✗ 没有这个文件：{binary}", file=sys.stderr)
        return 2
    proj = project_dir or (WIN_TMP / "ghidra-proj")
    proj.mkdir(parents=True, exist_ok=True)
    name = f"proj-{binary.stem}"
    bat = gh / "support" / "analyzeHeadless.bat"
    # 注意：cmd.exe 只认 Windows 路径（传 /mnt/d/… 会报「系统找不到指定的路径」）
    args = [winpath(proj), name, "-import", winpath(binary)]
    if postscript:
        args += ["-postScript", postscript]
    print(f"→ analyzeHeadless {name} -import {binary.name}")
    rc = subprocess.run(["cmd.exe", "/c", winpath(bat), *args], timeout=timeout).returncode
    print(f"rc={rc}；项目目录：{proj}（项目名 {name}）")
    if not keep:
        print("  （--keep 可保留项目；默认也保留 —— 删项目请手动删，免得误删你分析到一半的东西）")
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description="调 Windows 侧的 IDA / Ghidra（WSL interop）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="找工具、打印路径")
    p1 = sub.add_parser("ida", help="IDA 批量反汇编（可加 --decompile 出伪 C）")
    p1.add_argument("binary")
    p1.add_argument("--decompile", action="store_true")
    p1.add_argument("--script", default=None, help="自定义 -S 脚本（.idc / .py）")
    p1.add_argument("--timeout", type=int, default=1800, help="秒；IDA 卡死时被杀（默认 1800）")
    p2 = sub.add_parser("ghidra", help="Ghidra headless import + 自动分析")
    p2.add_argument("binary")
    p2.add_argument("--postscript", default=None, help="分析后跑这个 Ghidra 脚本")
    p2.add_argument("--project-dir", default=None)
    p2.add_argument("--keep", action="store_true")
    p2.add_argument("--timeout", type=int, default=1800, help="秒；Ghidra 卡死时被杀（默认 1800）")
    a = ap.parse_args()

    if a.cmd == "check" or getattr(a, "binary", None) in ("check", "--check", "-c"):
        return cmd_check()          # `ida check` / `ghidra check` 也走这里，别真去解析一个叫 check 的文件
    if a.cmd == "ida":
        return cmd_ida(Path(a.binary), a.decompile, a.script, a.timeout)
    return cmd_ghidra(Path(a.binary), a.postscript,
                      Path(a.project_dir) if a.project_dir else None, a.keep, a.timeout)


if __name__ == "__main__":
    sys.exit(main())
