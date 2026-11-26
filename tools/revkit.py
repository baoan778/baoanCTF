#!/usr/bin/env python3
"""revkit —— WASM / EVM / .NET 三块盲区的最小能力面（2026-10-12 补）。

为什么会有它
------------
2026-10-12 逐个实测本机：`wasm2wat` 无、`py` 里 `pywasm`/`wasmtime` 无、EVM 一整套
（`solc`/`web3`/`ethereum`）无、`.NET` 的 `ilspycmd`/`dnSpy` 无，
而 `skills/ctf-reverse/SKILL.md` 里 `wasm|evm|.net` **零命中** ——
三类载体一直在 CTF 里出现（rev/misc/blockchain），我们一个入口都没有。

参照物是 REA（`github.com/morluto/rea`）。但它值钱的**不是那个 MCP**：
它的 native 路径要 Hopper（macOS only）/ 自带 Ghidra / 正版 IDA，我们这台上不了；
真正能抄的是它**不需要引擎**的三条固定入口 + 明确的输出边界。

边界（照 REA 的写法立在最前面，别自己脑补 —— 每条都对应一个易犯的错）
--------------------------------------------------------------------
  · **WAT / wasm-decompile 不是原始源码**。给的是结构、类型与关系；
    名字（`add`）来自 name 段，是没有的（strip 过的题）就别假装有。
  · **EVM 的 selector / 参数 / 可变性是「推断候选」**。本工具**不做链上查询**、
    不执行字节码；`--push4` 列出的常量只是候选，不是「这就是那几个函数」。
  · **IL 不是 C# 源码**。`.NET` 走的是纯静态元数据 + IL，不加载程序集、不跑 CLR。
  · 反编译产物一律当**证据**用：结论要能指回 `pc` / `方法 RVA` / 段偏移。

用法
----
    revkit wasm  <a.wasm|a.wat> [--info] [--wat] [--decompile] [--run FN] [--args 1,2]
    revkit evm   <hex|file>     [--disasm] [--push4] [--limit N] [--selectors "transfer(address,uint256)"]
    revkit dotnet <a.dll|a.exe> [--types] [--methods NS.Type] [--strings] [--il NS.Type::Method] [--limit N]
    revkit --selftest

设计取向：**薄**。每个子命令只做该载体最常走的那几步，输出有上限（本仓纪律：大输出先摸形状），
细节需要时直接下沉到 `py`（wasmtime/pyevmasm/dnfile 都在 venv 里）。
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from dncil.cil.body.reader import CilMethodBodyReaderBase

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
#   自包含（自己 import sys），不依赖本文件是否已 import sys 或用别名。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_LIMIT = 40
MAX_SCAN = 64 * 1024 * 1024


# ────────────────────────── 公共小件 ──────────────────────────

def _need(cmd: str) -> str:
    p = shutil.which(cmd)
    if not p:
        sys.exit(f"revkit: 缺外部命令 `{cmd}`（wabt 装法：apt-get install -y wabt）")
    return p


def _run(cmd: list[str], limit: int) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=180)
    out = (r.stdout or "") + (r.stderr or "")
    lines = out.splitlines()
    if len(lines) > limit:
        out = "\n".join(lines[:limit]) + f"\n…（共 {len(lines)} 行，已截 {limit}；--limit 可调）"
    return r.returncode, out


def _bounds() -> str:
    return ("\n边界：WAT/反编译不是原始源码 · IL 不是 C# 源码 · EVM 的 selector/参数是推断候选（本工具不做链上查询）")


# ────────────────────────── wasm ──────────────────────────

def cmd_wasm(a) -> int:
    src = Path(a.target)
    if not src.is_file():
        sys.exit(f"revkit: 不是文件：{src}")
    tmp = None
    if src.suffix == ".wat":
        wat2wasm = _need("wat2wasm")
        tmp = Path(f"/tmp/revkit-{src.stem}.wasm")
        rc, out = _run([wat2wasm, str(src), "-o", str(tmp)], a.limit)
        if rc != 0:
            print(out)
            return rc
        print(f"[wat2wasm] {src} → {tmp}")
        target = tmp
    else:
        target = src

    did = False
    if a.run:
        did = True
        rc = _wasm_run(target, a.run, a.args)
        if rc:
            return rc
    if a.decompile or not (a.info or a.wat):
        did = True
        print(f"\n[wasm-decompile] {target}")
        rc, out = _run([_need("wasm-decompile"), str(target)], a.limit)
        print(out)
    if a.wat:
        did = True
        print(f"\n[wasm2wat] {target}")
        rc, out = _run([_need("wasm2wat"), str(target)], a.limit)
        print(out)
    if a.info:
        did = True
        print(f"\n[wasm-objdump -x] {target}")
        rc, out = _run([_need("wasm-objdump"), "-x", str(target)], a.limit)
        print(out)
    if not did:  # 兜底：至少给个规模形状
        raw = target.read_bytes()[:MAX_SCAN]
        print(f"[shape] {len(raw)} bytes · magic={raw[:4]!r} · version={int.from_bytes(raw[4:8],'little')}")
    print(_bounds())
    return 0


def _wasm_run(path: Path, fn: str, args: str) -> int:
    """用 wasmtime 在**进程内**跑一个导出函数（rev 题最常用的那一步：喂输入看输出）。"""
    try:
        from wasmtime import Engine, Instance, Module, Store
    except Exception as e:
        print(f"[wasmtime] 不可用：{e}（pip install wasmtime）")
        return 1
    engine = Engine()
    store = Store(engine)
    module = Module(engine, path.read_bytes())
    inst = Instance(store, module, [])
    exports = inst.exports(store)
    if fn not in exports:
        names = list(exports.keys())[:60]
        print(f"[wasmtime] 没有导出 `{fn}`；可用导出：{names}")
        return 1
    func = exports[fn]
    try:
        ftype = func.type(store)
        kinds = [str(p) for p in ftype.params]
    except Exception:
        kinds = []
    raw_args = [x for x in (args or "").split(",") if x.strip() != ""]
    vals = []
    for i, s in enumerate(raw_args):
        k = kinds[i] if i < len(kinds) else "i32"
        s = s.strip()
        if "f32" in k or "f64" in k:
            vals.append(float(s))
        else:
            vals.append(int(s, 0))
    print(f"[wasmtime] {fn}{tuple(kinds)} <- {vals}")
    try:
        print(f"[wasmtime] => {func(store, *vals)}")
    except Exception as e:
        print(f"[wasmtime] 调用失败：{type(e).__name__}: {e}")
        return 1
    return 0


# ────────────────────────── evm ──────────────────────────

def _load_evm(target: str) -> bytes:
    p = Path(target)
    if p.is_file():
        raw = p.read_bytes()
        if raw[:2] in (b"0x", b"0X"):
            raw = raw[2:]
        txt = raw.decode("utf-8", "ignore").strip()
        if txt and len(txt) >= 2 and all(c in "0123456789abcdefABCDEF" for c in txt) and len(txt) % 2 == 0:
            return bytes.fromhex(txt)
        return raw
    txt = target.strip()
    txt = re.sub(r"^0[xX]", "", txt)
    if not re.fullmatch(r"[0-9a-fA-F]*", txt) or len(txt) % 2:
        sys.exit("revkit evm: 既不是存在的文件，也不是偶数长度的 hex 串")
    return bytes.fromhex(txt)


def _split_sigs(raw: str) -> list[str]:
    """按逗号拆签名，但**别把 `transfer(address,uint256)` 里的逗号也拆了**。

    2026-10-12 踩过：`--selectors "transfer(address,uint256)"` 直接被 split(',') 拆成两条，
    算出两个没意义的哈希。现在还支持分号/换行作分隔符。
    """
    parts = re.split(r"[;\n]", raw)
    out: list[str] = []
    for part in parts:
        out.extend(re.split(r",(?![^()]*\))", part))
    return [p.strip() for p in out if p.strip()]


def cmd_evm(a) -> int:
    code = _load_evm(a.target)
    print(f"[carrier] {len(code)} bytes  keccak(code)[:16]={_evm_digest(code)}")

    if a.selectors:
        from Crypto.Hash import keccak
        print("\n[selectors]（keccak256(sig)[:4]；推断候选，不是链上事实）")
        for sig in _split_sigs(a.selectors):
            k = keccak.new(digest_bits=256)
            k.update(sig.encode())
            print(f"  {k.hexdigest()[:8]}  {sig}")
        print("  （交叉核对：eth_utils.keccak(text=sig).hex()[:8] 应一致）")

    if a.disasm or not a.push4:
        from pyevmasm import disassemble_all
        print(f"\n[disasm] 前 {a.limit} 条指令（pc 为主键，结论要指回来）")
        for i, ins in enumerate(disassemble_all(code)):
            if i >= a.limit:
                print(f"  …（停在第 {a.limit} 条；--limit 可调）")
                break
            op = ins.operand
            if op is None:
                op = ""
            elif isinstance(op, int) and ins.name.startswith("PUSH"):
                op = f"0x{op:x} ({op})"
            elif isinstance(op, bytes):
                op = op.hex()
            print(f"  {ins.pc:05x}  {ins.name:<12s} {op}")

    if a.push4 or a.disasm:
        from pyevmasm import disassemble_all
        consts: dict[str, int] = {}
        for ins in disassemble_all(code):
            if ins.name == "PUSH4" and ins.operand is not None:
                consts[f"{ins.operand:08x}"] = consts.get(f"{ins.operand:08x}", 0) + 1
        if consts:
            top = sorted(consts.items(), key=lambda kv: -kv[1])[: a.limit]
            print("\n[push4 常量]（dispatcher 的 selector 候选；**不能**反推函数名）")
            for hexv, n in top:
                print(f"  0x{hexv}  ×{n}")
    print(_bounds())
    return 0


def _evm_digest(code: bytes) -> str:
    """字节码自身的 keccak 摘要前 16 位 —— 只是给「同一份字节码」一个可比对的指纹。"""
    try:
        from Crypto.Hash import keccak
        k = keccak.new(digest_bits=256)
        k.update(code)
        return k.hexdigest()[:16]
    except Exception:
        return "-"


# ────────────────────────── dotnet ──────────────────────────

class _ReaderBytes(CilMethodBodyReaderBase):
    """dncil 要的 reader：bytes + offset。

    ⚠ 必须继承 `CilMethodBodyReaderBase` —— 它不只定义 read/tell/seek 三个抽象方法，
    还提供了 read_uint8/read_uint16/read_inline_br_target/read_inline_token 等
    整个方法体解析要用的具体方法（2026-10-12 踩过：只实现三个抽象方法 →
    `AttributeError: '_ReaderBytes' object has no attribute 'read_uint8'`）。
    """

    def __init__(self, data: bytes, offset: int = 0) -> None:
        self._d, self._o = data, offset

    def read(self, n: int) -> bytes:
        b = self._d[self._o:self._o + n]
        self._o += len(b)
        return b

    def tell(self) -> int:
        return self._o

    def seek(self, rva: int) -> int:
        self._o = rva
        return self._o


def _dn_pe(path: Path):
    try:
        import dnfile
    except Exception as e:
        sys.exit(f"revkit dotnet: 缺 dnfile（pip install dnfile dncil）：{e}")
    pe = dnfile.dnPE(str(path))
    if getattr(pe, "net", None) is None:
        sys.exit("revkit dotnet: 这不是 .NET 程序集（没有 CLR 元数据头）")
    return pe


def _md_rows(pe, name: str) -> list:
    t = getattr(pe.net.mdtables, name, None)
    return list(t.rows) if t is not None else []


def _methods_of(pe, td) -> list:
    out = []
    for idx in list(getattr(td, "MethodList", []) or []):
        try:
            out.append(idx.row)
        except Exception:
            continue
    return out


def _type_name(td) -> str:
    ns, n = getattr(td, "TypeNamespace", ""), getattr(td, "TypeName", "")
    return f"{ns}.{n}" if ns else n


def cmd_dotnet(a) -> int:
    src = Path(a.target)
    if not src.is_file():
        sys.exit(f"revkit dotnet: 不是文件：{src}")
    pe = _dn_pe(src)
    types = _md_rows(pe, "TypeDef")
    print(f"[assembly] {src.name}  TypeDef={len(types)}  MethodDef={len(_md_rows(pe,'MethodDef'))}  "
          f"AssemblyRef={len(_md_rows(pe,'AssemblyRef'))}")

    if a.types or not (a.methods or a.strings or a.il):
        print(f"\n[types] 前 {a.limit} 个（含方法数）")
        for td in types[: a.limit]:
            print(f"  {_type_name(td):<60s} methods={len(_methods_of(pe, td))}")
        if len(types) > a.limit:
            print(f"  …（共 {len(types)} 个类型；--limit 可调）")

    if a.methods:
        want = a.methods.lower()
        hit = 0
        print(f"\n[methods] 匹配 `{a.methods}`（建议带命名空间，别只给类名短词）")
        for td in types:
            tn = _type_name(td)
            if want not in tn.lower():
                continue
            for m in _methods_of(pe, td):
                print(f"  {tn}::{m.Name}   RVA=0x{m.Rva:x}")
                hit += 1
                if hit >= a.limit:
                    break
            if hit >= a.limit:
                break
        if not hit:
            print("  （无命中；先用 --types 看真实全名）")

    if a.strings:
        raw = getattr(getattr(pe.net, "user_strings", None), "__data__", b"") or b""
        found = [m.decode("utf-16le", "ignore") for m in re.findall(rb"(?:[\x20-\x7e]\x00){4,}", raw)]
        uniq = list(dict.fromkeys(found))
        print(f"\n[user_strings #US] 可打印 UTF-16 串 {len(found)} 个（去重 {len(uniq)}），列前 {a.limit}：")
        for s in uniq[: a.limit]:
            print(f"  {s}")
        if len(uniq) > a.limit:
            print("  …（--limit 可调）")

    if a.il:
        return _dotnet_il(pe, a.il, a.limit)

    print("\n边界：IL 不是 C# 源码；本工具纯静态，不加载程序集、不跑 CLR")
    return 0


def _dotnet_il(pe, spec: str, limit: int) -> int:
    from dncil.cil.body import CilMethodBody
    from dncil.cil.error import MethodBodyFormatError

    if "::" in spec:
        tn, mn = spec.split("::", 1)
    else:
        tn, mn = spec, ""
    tn, mn = tn.strip().lower(), mn.strip().lower()
    target = None
    for td in _md_rows(pe, "TypeDef"):
        tname = _type_name(td)
        if tn and tn not in tname.lower():
            continue
        if not tn and mn:
            continue
        for m in _methods_of(pe, td):
            if mn and mn != str(m.Name).lower():
                continue
            target = (tname, m)
            break
        if target:
            break
    if not target:
        print(f"\n[il] 找不到 `{spec}`（用 --methods NS.Type 先确认全名，大小写不敏感但必须全名匹配）")
        return 1

    tname, m = target
    if not getattr(m, "Rva", 0):
        print(f"\n[il] {tname}::{m.Name} 是抽象/外部方法（RVA=0，没有 IL 体）")
        return 0
    data = pe.get_data(m.Rva, 0x4000)
    try:
        body = CilMethodBody(_ReaderBytes(data, 0))
    except MethodBodyFormatError as e:
        print(f"\n[il] 方法体解析失败：{e}")
        return 1
    print(f"\n[il] {tname}::{m.Name}  RVA=0x{m.Rva:x}  size={body.size}  maxstack={body.max_stack}  "
          f"locals_sig=0x{getattr(body.local_var_sig_tok, 'value', 0) or 0:x}  handlers={len(body.exception_handlers)}")
    for i, ins in enumerate(body.instructions):
        if i >= limit:
            print(f"  …（共 {len(body.instructions)} 条 IL，停在第 {limit}；--limit 可调）")
            break
        op = ins.operand if ins.operand is not None else ""
        print(f"  IL_{ins.offset:04x}: {str(ins.opcode):<14s} {op}")
    return 0


# ────────────────────────── selftest ──────────────────────────

def cmd_selftest() -> int:
    ok = True
    print("== revkit --selftest ==")
    # wasm
    if shutil.which("wat2wasm"):
        wat = Path("/tmp/revkit-selftest.wat")
        wat.write_text('(module (func $add (export "add") (param i32 i32) (result i32)\n'
                       '  local.get 0 local.get 1 i32.add))\n')
        rc, out = _run(["wat2wasm", str(wat), "-o", "/tmp/revkit-selftest.wasm"], 5)
        print(f"[wasm] wat2wasm rc={rc}")
        try:
            rc = _wasm_run(Path("/tmp/revkit-selftest.wasm"), "add", "2,40")
            print(f"[wasm] wasmtime add(2,40) rc={rc}")
            ok &= rc == 0
        except Exception as e:
            print(f"[wasm] FAIL {e}")
            ok = False
    else:
        print("[wasm] SKIP（无 wat2wasm）")
    # evm
    try:
        from pyevmasm import disassemble_all
        n = len(list(disassemble_all(bytes.fromhex("600160020160005260206000f3"))))
        print(f"[evm] pyevmasm 反汇编 {n} 条 rc=0")
        from Crypto.Hash import keccak
        k = keccak.new(digest_bits=256)
        k.update(b"transfer(address,uint256)")
        assert k.hexdigest()[:8] == "a9059cbb", k.hexdigest()
        print("[evm] keccak selector a9059cbb ✓")
    except Exception as e:
        print(f"[evm] FAIL {e}")
        ok = False
    # dotnet：现找一个样本，找不到就 SKIP（不联网）
    cands = list(Path("/tmp").glob("**/Newtonsoft.Json.dll")) + \
            list((Path(os.environ.get("PI_LAB_ROOT") or Path(__file__).resolve().parent.parent) / "tools" / "win").glob("**/*.dll"))
    if cands:
        try:
            pe = _dn_pe(cands[0])
            print(f"[dotnet] {cands[0].name} TypeDef={len(_md_rows(pe,'TypeDef'))} rc=0")
        except SystemExit as e:
            print(f"[dotnet] FAIL {e}")
            ok = False
    else:
        print("[dotnet] SKIP（/tmp 与 tools/win 下没有 .NET 样本）")
    print("== selftest", "PASS" if ok else "FAIL", "==")
    return 0 if ok else 1


# ────────────────────────── main ──────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(prog="revkit", description="WASM / EVM / .NET 三块盲区的最小能力面")
    ap.add_argument("--selftest", action="store_true", help="自检三块能力（不联网）")
    sub = ap.add_subparsers(dest="cmd")

    w = sub.add_parser("wasm", help="WASM：信息 / WAT / 反编译 / 直接跑导出函数")
    w.add_argument("target")
    w.add_argument("--info", action="store_true", help="wasm-objdump -x（段/导入导出）")
    w.add_argument("--wat", action="store_true", help="wasm2wat（可读文本）")
    w.add_argument("--decompile", action="store_true", help="wasm-decompile（伪码，默认动作）")
    w.add_argument("--run", metavar="FN", help="用 wasmtime 跑导出函数")
    w.add_argument("--args", default="", help="逗号分隔参数，如 1,2（按函数签名转 i32/i64/f32/f64）")
    w.add_argument("--limit", type=int, default=200)
    w.set_defaults(fn=cmd_wasm)

    e = sub.add_parser("evm", help="EVM 字节码：反汇编 / selector 计算 / dispatcher 常量")
    e.add_argument("target", help="hex 串（可带 0x）或文件（hex 文本 / 原始字节码）")
    e.add_argument("--disasm", action="store_true", help="反汇编（默认动作）")
    e.add_argument("--push4", action="store_true", help="列出 PUSH4 常量（selector 候选）")
    e.add_argument("--selectors", help="逗号分隔签名，算 keccak256(sig)[:4]，如 'transfer(address,uint256)'")
    e.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    e.set_defaults(fn=cmd_evm)

    d = sub.add_parser("dotnet", help=".NET：类型 / 方法 / user_strings / 指定方法的 IL")
    d.add_argument("target")
    d.add_argument("--types", action="store_true", help="列类型（默认动作）")
    d.add_argument("--methods", metavar="NS.Type", help="列该类型的方法 + RVA")
    d.add_argument("--strings", action="store_true", help="扫 #US 堆里的 UTF-16 可打印串")
    d.add_argument("--il", metavar="NS.Type::Method", help="反汇编某个方法的 IL")
    d.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    d.set_defaults(fn=cmd_dotnet)

    a = ap.parse_args()
    if a.selftest:
        return cmd_selftest()
    if not a.cmd:
        ap.print_help()
        return 2
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
