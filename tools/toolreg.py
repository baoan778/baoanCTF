#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""toolreg —— 工具清单的**自登记 + 自检测**（不是又一份手写的 TOOLS.md）。

来源与借鉴
----------
ZeroA 的 `tool-catalog` 扩展（`.pi/extensions/tool-catalog.ts`，GPL-3.0 只借思路不抄码）
把这件事说得很准，直接抄它的问题陈述：

  > 「清单最初只有人工维护的一张表，但真实使用里有两件事只有运行期才知道 ——
  >   1. 工具装在 PATH 之外（工具箱自带目录、便携包、pip 的 Scripts）；
  >   2. **做题过程中会现装新库/新工具，装完不登记，下次启动又变成『本机没找到』**。」

pi-lab 的 `tools/TOOLS.md` 是**唯一事实来源**，但它是散文、只能人工维护。本工具补的是
「运行期那半张表」：机器可读的清单 + 真检测 + 把实测路径写回。TOOLS.md 仍然是人读的
权威说明；两者分工 —— TOOLS.md 讲「怎么用」，tool-registry.json 讲「在不在、在哪」。

设计取向（照 ANCHOR 的规矩）
----------------------------
* **fail-soft**：查不到就是查不到，不猜、不硬编码假设；缺工具只报「不可用 + 为什么」。
* **不静默销毁**：清单读坏时降级到种子清单且不动坏文件；一旦要写回，**先把坏文件备份成
  `<name>.corrupt-<时间戳>`**（这条是独立复核 `ctf-verify` 找出的反例逼出来的 —— 原来
  只有提示写着「不会覆盖」，而 `check --record` 随后就把它盖掉了）。
* **不执行、不联网**（默认）：`check` 只做「存在性解析」（记录路径 → PATH → Python import
  → Node 模块）。要版本号必须显式 `--version` —— 那会真的去跑目标程序，属于执行外部代码，
  默认关掉（工具描述与清单都是**不可信输入**，见 AGENTS §三.1）。
* **发布包 / exe 友好**：只用标准库；路径自解析（`PI_LAB_ROOT` 或脚本位置）；
  UTF-8 强制；Windows 上靠 `shutil.which` + `PATHEXT` 找 `.exe`。

用法
----
    toolreg list                      # 清单 + 在不在（默认视图）
    toolreg check [--record]          # 逐条真检测；--record 把新发现的路径写回
    toolreg check --version           # 顺带取版本（**会执行**目标程序，默认关）
    toolreg add ffuf --commands ffuf,ffuf.exe --direction web --note "目录爆破"
    toolreg record ffuf /usr/local/bin/ffuf      # 实测到路径后写回
    toolreg missing                   # 只列不可用的（脚本/门禁可用）
    toolreg show ffuf

退出码：0 全部可用 / 1 有缺失（`check`/`missing`）/ 2 用法错
环境变量：`PI_LAB_TOOL_REGISTRY` 换清单路径；`PI_LAB_ROOT` 换仓库根。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ★ 发布包（exe / Windows）适配：控制台默认编码可能是 cp936，直接 print 中文会抛
#   UnicodeEncodeError。两只流都改成 utf-8 + replace（与 tools/kb.py 同惯用法）。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = Path(os.environ.get("PI_LAB_ROOT") or Path(__file__).resolve().parent.parent)
REGISTRY = Path(os.environ.get("PI_LAB_TOOL_REGISTRY") or (LAB / "tools" / "tool-registry.json"))

# ★ 可移植路径（发布包/容器适配）：清单里记的路径写成 "$PI_LAB_ROOT/tools/bin/py"，
#   而不是某台机器的绝对路径。换目录 / 进容器时不用重跑 --record 就仍然指得对。
#   记录时自动折叠前缀；解析时自动展开；两边对称，清单因此可以入库、可跨机器。
LAB_TOKEN = "$PI_LAB_ROOT"


def portablize(p):
    """绝对路径 → "$PI_LAB_ROOT/..."（不在仓库内就原样返回）。"""
    if not p or not isinstance(p, str):
        return p
    root = str(LAB)
    if p == root:
        return LAB_TOKEN
    if p.startswith(root + os.sep):
        return LAB_TOKEN + "/" + p[len(root) + 1:].replace(os.sep, "/")
    return p


def unportable(p):
    """'$PI_LAB_ROOT/...' → 本机绝对路径（其它写法原样返回）。"""
    if not p or not isinstance(p, str):
        return p
    if p == LAB_TOKEN:
        return str(LAB)
    if p.startswith(LAB_TOKEN + "/"):
        return str(LAB / p[len(LAB_TOKEN) + 1:])
    return p


RC_OK, RC_MISSING, RC_USAGE = 0, 1, 2

# 版本探测参数：**白名单**，不接受清单里给的任意命令（否则清单被改 = 任意命令执行）
VERSION_ARGS = (["--version"], ["-version"], ["-V"], ["-ver"], ["version"])
VERSION_TIMEOUT = 5          # 秒；超时就放弃版本，不拖慢 check
VERSION_CAP = 200            # 版本输出截断字符数

# ── 种子清单（★ 来自 tools/TOOLS.md 的必记条目；错一条的代价只是显示"不可用"）──────
# 格式： id | 显示名 | 方向 | 一句话 | commands(逗号) | 探测参数(逗号)
_SEED = [
    ("py", "py（venv Python）", "base", "带 pwntools/z3/pycryptodome/gmpy2/unicorn 的 Python", "py", ""),
    ("python3", "python3", "base", "系统 Python3", "python3,python3.exe,python.exe", ""),
    ("node", "Node.js", "base", "跑 .mjs 扩展与前端", "node,node.exe", "--version"),
    ("git", "git", "base", "版本控制", "git,git.exe", "--version"),
    ("poc", "poc（离线 PoC 库）", "base", "1.1 万条离线 PoC / 指纹", "poc", ""),
    ("pocbomber", "pocbomber", "base", "POC 批量匹配", "pocbomber", ""),
    ("kb", "kb（本地知识库）", "base", "≈2.49 万块统一检索入口", "kb", ""),
    ("fgs", "fgs（黑板内核）", "base", "Goal/Step/Fact 黑板 + 门禁", "fgs", ""),
    ("selfcheck", "selfcheck", "base", "全套/快速自检", "selfcheck", ""),
    ("skilllint", "skilllint", "base", "技能库体检", "skilllint", ""),
    ("sesscan", "sesscan", "base", "查 pi 会话（list/find/show/where）", "sesscan", ""),
    ("handover", "handover", "base", "打交接包", "handover", ""),
    ("flagwatch", "flag-watch", "base", "工具输出里的 flag 形状兜底告警", "flagwatch", ""),
    ("lane_gate", "lane_gate", "base", "危险命令事前闸门", "lane_gate", ""),
    ("lane_guard", "lane_guard", "base", "车道事后看门狗", "lane_guard", ""),
    ("ctfsurf", "ctfsurf", "web", "前端 JS / 隐藏接口挖掘", "ctfsurf", ""),
    ("authzdiff", "authzdiff", "web", "鉴权差分探测器", "authzdiff", ""),
    ("probe", "probe", "web", "接口探测", "probe", ""),
    ("nuclei", "nuclei", "web", "模板化漏洞扫描", "nuclei,nuclei.exe", "-version"),
    ("httpx", "httpx", "web", "HTTP 批量探活/指纹", "httpx,httpx.exe", "-version"),
    ("ffuf", "ffuf", "web", "目录/参数爆破", "ffuf,ffuf.exe", "-V"),
    ("katana", "katana", "web", "爬虫", "katana,katana.exe", "-version"),
    ("sqlmap", "sqlmap", "web", "SQL 注入", "sqlmap", "--version"),
    ("jwt_tool", "jwt_tool", "web", "JWT 测试", "jwt_tool", ""),
    ("wafw00f", "wafw00f", "web", "WAF 指纹", "wafw00f", ""),
    ("nmap", "nmap", "base", "端口/服务扫描", "nmap,nmap.exe", "--version"),
    ("masscan", "masscan", "base", "快速端口扫描", "masscan", "--version"),
    ("naabu", "naabu", "base", "端口扫描（Go）", "naabu", "-version"),
    ("pwnkit", "pwnkit", "pwn", "pwn 一键侦察/利用台", "pwnkit", ""),
    ("pggdb", "pggdb（gdb+gef）", "pwn", "调试器（本机没有 pwndbg）", "pggdb", ""),
    ("pwnsetup", "pwnsetup", "pwn", "libc 环境准备", "pwnsetup", ""),
    ("libcsearch", "libcsearch", "pwn", "libc 版本检索", "libcsearch", ""),
    ("revkit", "revkit", "reverse", "逆向三块能力一个入口", "revkit", ""),
    ("ida", "IDA Pro（Windows 侧）", "reverse", "走 WSL interop 调 Windows IDA", "ida", ""),
    ("ghidra", "Ghidra（Windows 侧）", "reverse", "同上", "ghidra", ""),
    ("rsactf", "rsactf", "crypto", "RSA 迭代台", "rsactf", ""),
    ("vol", "volatility3", "forensics", "内存取证（vol 语法）", "vol", ""),
    ("binwalk", "binwalk", "forensics", "固件/镜像拆包", "binwalk", "--version"),
    ("foremost", "foremost", "forensics", "文件雕刻", "foremost", "-V"),
    ("exiftool", "ExifTool", "forensics", "元数据（不认 --version）", "exiftool", "-ver"),
    ("tcpflow", "tcpflow", "forensics", "流量重组", "tcpflow", "-h"),
    ("testdisk", "testdisk", "forensics", "分区/文件恢复", "testdisk", ""),
    ("unsquashfs", "unsquashfs", "forensics", "squashfs 解包", "unsquashfs", "-version"),
    ("ffmpeg", "ffmpeg", "misc", "音视频处理", "ffmpeg,ffmpeg.exe", "-version"),
    ("sox", "sox", "misc", "音频处理", "sox", "--version"),
    ("mediainfo", "mediainfo", "misc", "媒体信息", "mediainfo", "--Version"),
    ("multimon-ng", "multimon-ng", "misc", "无线/DTMF 解码", "multimon-ng", ""),
    ("pdftotext", "pdftotext", "misc", "PDF 取文", "pdftotext", "-v"),
    ("qpdf", "qpdf", "misc", "PDF 结构", "qpdf", "--version"),
    ("mutool", "mutool", "misc", "PDF 工具集", "mutool", "-v"),
    ("apktool", "apktool", "reverse", "APK 反编译", "apktool,apktool.bat", "--version"),
    ("java", "java", "reverse", "跑 apktool/jadx", "java,java.exe", "-version"),
    ("bkcrack", "bkcrack", "misc", "ZipCrypto 明文攻击", "bkcrack", ""),
    ("jsteg", "jsteg", "misc", "JPEG 隐写", "jsteg", ""),
    ("outguess", "outguess", "misc", "JPEG 隐写", "outguess", ""),
    ("f5", "f5", "misc", "JPEG 隐写（F5）", "f5", ""),
    ("jq", "jq", "base", "JSON 处理", "jq,jq.exe", "--version"),
    ("yara", "yara", "forensics", "YARA 规则", "yara", "--version"),
    ("strace", "strace", "reverse", "系统调用跟踪", "strace", "--version"),
    ("ltrace", "ltrace", "reverse", "库调用跟踪", "ltrace", ""),
    ("patchelf", "patchelf", "pwn", "改 ELF（interp/rpath）", "patchelf", "--version"),
]


def seed_registry() -> dict:
    tools = {}
    for tid, name, direction, note, cmds, vargs in _SEED:
        tools[tid] = {
            "id": tid, "name": name, "direction": direction, "note": note,
            "commands": [c for c in cmds.split(",") if c],
            "versionArgs": [a for a in vargs.split(",") if a],
            "path": None, "pythonImport": None, "nodeModule": None,
            "capabilities": [], "registeredBy": "seed",
        }
    return {"version": 1,
            "note": "机器可读的工具清单（运行期那半张表）。人读的权威说明仍是 tools/TOOLS.md。",
            "tools": tools}


# 清单读坏时的降级标志：`load()` 置位，`save()` 据此**写前先备份**。
# 为什么要有：实测踩到 —— 只看 `load()` 的提示会以为「永不覆盖」，但 `check --record` /
# `add` 随后就会落盘，把坏文件**静默销毁**（本仓铁律：坏数据不许静默销毁，要留痕）。
_DEGRADED: dict = {"corrupt": False}


def load() -> dict:
    if not REGISTRY.exists():
        return seed_registry()
    try:
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        _DEGRADED["corrupt"] = True
        print(f"! 清单读不了（{e}）→ 本次用种子清单跑；这里**不会**动它；"
              f"但若这一次要写回，会先把坏文件备份成 {REGISTRY.name}.corrupt-<时间戳>", file=sys.stderr)
        return seed_registry()
    if not isinstance(data, dict) or not isinstance(data.get("tools"), dict):
        print("! 清单结构不对，这次用种子清单跑", file=sys.stderr)
        return seed_registry()
    # 与新种子合并：种子里有、清单里没有的补上（升级后新条目不会丢）
    for tid, row in seed_registry()["tools"].items():
        data["tools"].setdefault(tid, row)
    return data


def save(data: dict) -> bool:
    """原子写。返回 False = **拒绝写入**（坏清单备份失败时宁可不写，也不静默销毁）。"""
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    if _DEGRADED["corrupt"] and REGISTRY.exists():
        backup = REGISTRY.with_name(f"{REGISTRY.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
        try:
            shutil.copy2(REGISTRY, backup)
        except OSError as e:
            print(f"✗ 坏清单备份失败（{e}）→ 拒绝写入（宁可不写，也不静默销毁）。"
                  f"先手工修好或移走 {REGISTRY}", file=sys.stderr)
            return False
        print(f"! 坏清单已备份到 {backup}（写前保全）", file=sys.stderr)
    tmp = REGISTRY.with_name(f".{REGISTRY.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, REGISTRY)
    return True


def _which(cmds) -> str | None:
    for c in cmds or []:
        p = shutil.which(c)
        if p:
            return p
    return None


def _py_import_ok(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def _node_module_ok(mod: str) -> bool:
    for base in (LAB / "node_modules", LAB / "tools" / "node_modules",
                 Path(os.environ.get("NODE_PATH", "/nonexistent"))):
        if (base / mod).is_dir():
            return True
    return False


def resolve(row: dict) -> dict:
    """解析一条工具到底在不在。返回 {available, path, source, detail}。

    顺序（照 ZeroA catalog 的口径）：**记录路径 → PATH（含 .exe）→ Python import → Node 模块**。
    记录路径要 `isfile` 才算 —— 记过的路径可能因为重装/移动失效，那种要落回 PATH 再找。
    """
    p = unportable(row.get("path"))
    if p and Path(p).is_file():
        return {"available": True, "path": p, "source": "recorded", "detail": ""}
    hit = _which(row.get("commands"))
    if hit:
        return {"available": True, "path": hit, "source": "PATH", "detail": ""}
    pyi = row.get("pythonImport")
    if pyi and _py_import_ok(pyi):
        return {"available": True, "path": f"(python module) {pyi}", "source": "python", "detail": ""}
    nm = row.get("nodeModule")
    if nm and _node_module_ok(nm):
        return {"available": True, "path": f"(node module) {nm}", "source": "node", "detail": ""}
    why = []
    if p:
        why.append(f"记录的路径已不存在（{p}）")
    if row.get("commands"):
        why.append("PATH 里没有 " + "/".join(row["commands"]))
    if pyi:
        why.append(f"Python 模块 {pyi} 导不进")
    if nm:
        why.append(f"Node 模块 {nm} 找不到")
    return {"available": False, "path": "", "source": "", "detail": "；".join(why) or "没有任何查找线索"}


def version_of(path: str, args) -> str:
    """取版本号 —— **只对真实可执行文件**、**只认白名单参数**、带超时与截断。

    为什么这么谨慎：清单文件与工具本身都是不可信输入（AGENTS §三.1）。如果按清单里
    写的任意参数去跑，改一行 JSON 就等于任意命令执行。
    """
    if not path or path.startswith("("):
        return ""
    cand = list(args or []) or [a[0] for a in VERSION_ARGS]
    try:
        r = subprocess.run([path, *cand], capture_output=True, text=True,
                           encoding="utf-8", timeout=VERSION_TIMEOUT, errors="replace")
    except (OSError, subprocess.SubprocessError):
        return ""
    blob = (r.stdout or r.stderr or "").strip().splitlines()
    return (blob[0][:VERSION_CAP] if blob else "")


def cmd_check(a) -> int:
    data = load()
    ids = sorted(data["tools"])
    if a.direction:
        ids = [i for i in ids if (data["tools"][i].get("direction") or "") == a.direction]
    rows, miss, recorded = [], [], []
    for tid in ids:
        row = data["tools"][tid]
        st = resolve(row)
        ver = version_of(st["path"], row.get("versionArgs")) if (a.version and st["available"]) else ""
        rows.append((tid, row, st, ver))
        if not st["available"]:
            miss.append(tid)
        elif a.record and st["source"] != "recorded":
            row["path"] = portablize(st["path"])
            row["lastFound"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "path": portablize(st["path"]),
                                "source": st["source"]}
            recorded.append(tid)
    if recorded:
        if not save(data):
            print("✗ 路径没能写回（见上方原因）", file=sys.stderr)
            return RC_USAGE
    if a.json:
        print(json.dumps({"total": len(rows), "missing": miss,
                          "recorded": recorded, "registry": str(REGISTRY),
                          "tools": [{"id": t, "available": s["available"], "path": s["path"],
                                     "source": s["source"], "detail": s["detail"], "version": v,
                                     "direction": row.get("direction"), "note": row.get("note")}
                                    for t, row, s, v in rows]}, ensure_ascii=False, indent=2))
    else:
        for tid, row, st, ver in rows:
            mark = "✓" if st["available"] else "✗"
            where = f"  {st['path']}" if st["path"] else ""
            why = "" if st["available"] else f"  ({st['detail']})"
            vv = f"  {ver}" if ver else ""
            print(f"{mark} {tid:14s}{where}{vv}{why}")
        print(f"\n可用 {len(rows) - len(miss)} / {len(rows)}"
              + (f"；不可用 {len(miss)}：{'、'.join(miss)}" if miss else "")
              + (f"\n已把 {len(recorded)} 条新路径写回：{'、'.join(recorded)}" if recorded else ""))
        print(f"清单：{REGISTRY}")
    return RC_MISSING if miss else RC_OK


def cmd_missing(a) -> int:
    a.direction = getattr(a, "direction", None)
    data = load()
    miss = [(tid, row, resolve(row)) for tid, row in sorted(data["tools"].items())
            if (not a.direction or row.get("direction") == a.direction)]
    miss = [(t, r, s) for t, r, s in miss if not s["available"]]
    if a.json:
        print(json.dumps([{"id": t, "detail": s["detail"], "note": r.get("note")}
                          for t, r, s in miss], ensure_ascii=False, indent=2))
    else:
        for t, r, s in miss:
            print(f"✗ {t:14s} {r.get('note', '')}  —— {s['detail']}")
        print(f"\n不可用 {len(miss)} 条（清单：{REGISTRY}）"
              + ("\n提示：装好之后 `toolreg record <id> <实测路径>` 写回，下次就不用再找" if miss else ""))
    return RC_MISSING if miss else RC_OK


def cmd_list(a) -> int:
    data = load()
    rows = [(t, r) for t, r in sorted(data["tools"].items())
            if (not a.direction or r.get("direction") == a.direction)]
    if a.json:
        print(json.dumps({t: r for t, r in rows}, ensure_ascii=False, indent=2))
        return RC_OK
    for t, r in rows:
        caps = ("  能力:" + ",".join(r["capabilities"])) if r.get("capabilities") else ""
        print(f"{t:14s} [{(r.get('direction') or '?'):8s}] {r.get('name', '')}{caps}")
        if r.get("note"):
            print(f"{'':16s}{r['note']}")
    print(f"\n共 {len(rows)} 条；`toolreg check` 逐条真检测，`--record` 写回新路径")
    return RC_OK


def cmd_show(a) -> int:
    data = load()
    row = data["tools"].get(a.id)
    if not row:
        print(f"✗ 清单里没有 {a.id}（`toolreg list` 看现有条目）", file=sys.stderr)
        return RC_USAGE
    st = resolve(row)
    print(json.dumps({**row, "_resolve": st}, ensure_ascii=False, indent=2))
    return RC_OK


def cmd_add(a) -> int:
    if not a.id or not (a.path or a.commands or a.python_import or a.node_module):
        print("✗ 至少给一条查找线索：--path（最准）/ --commands / --python-import / --node-module",
              file=sys.stderr)
        return RC_USAGE
    data = load()
    old = data["tools"].get(a.id, {})
    if old and not a.force and old.get("registeredBy") != "seed":
        print(f"✗ {a.id} 已在清单里（要改加 --force）", file=sys.stderr)
        return RC_USAGE
    row = {**old,
           "id": a.id,
           "name": a.name or old.get("name") or a.id,
           "direction": a.direction or old.get("direction") or "base",
           "note": a.note if a.note is not None else old.get("note", ""),
           "commands": [c for c in (a.commands.split(",") if a.commands else old.get("commands") or []) if c],
           "path": portablize(a.path) or old.get("path"),
           "pythonImport": a.python_import or old.get("pythonImport"),
           "nodeModule": a.node_module or old.get("nodeModule"),
           "capabilities": [c for c in (a.capabilities.split(",") if getattr(a, "capabilities", None)
                                        else old.get("capabilities") or []) if c],
           "versionArgs": [c for c in (getattr(a, "version_args", None).split(",")
                                       if getattr(a, "version_args", None)
                                       else old.get("versionArgs") or []) if c],
           "registeredBy": "ai"}
    data["tools"][a.id] = row
    if not save(data):
        print("✗ 登记未落盘（见上方原因）", file=sys.stderr)
        return RC_USAGE
    st = resolve(row)
    print(f"✓ 已登记 {a.id} → {'（已找到）' + st['path'] if st['available'] else '（这次没找到）'}"
          + (f"\n  ⚠ 登记成功但这次没找到：{st['detail']} —— 检查线索写对没有" if not st["available"] else "")
          + f"\n  清单：{REGISTRY}")
    return RC_OK


def cmd_record(a) -> int:
    data = load()
    row = data["tools"].get(a.id)
    if not row:
        # 没登记过就直接建 —— 做题时「先实测到路径」比「先登记」更常见
        row = {"id": a.id, "name": a.id, "direction": "base", "note": "",
               "commands": [], "versionArgs": [], "pythonImport": None,
               "nodeModule": None, "capabilities": [], "registeredBy": "ai"}
        data["tools"][a.id] = row
    row["path"] = portablize(a.path)
    row["lastFound"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "path": portablize(a.path), "source": "manual"}
    if not save(data):
        print("✗ 路径未落盘（见上方原因）", file=sys.stderr)
        return RC_USAGE
    st = resolve(row)
    print(f"✓ 已记录 {a.id} 的路径：{a.path}"
          + ("" if st["available"] else f"\n  ⚠ 这个路径现在就不存在（{st['detail']}）—— 是不是写错了？"))
    return RC_OK


def main() -> int:
    ap = argparse.ArgumentParser(description="pi-lab 工具清单（自登记 + 自检测）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, **kw):
        p = sub.add_parser(name, **kw)
        p.set_defaults(fn=fn)
        return p

    p = add("list", cmd_list, help="列清单（含方向/说明）")
    p.add_argument("--direction", default=None, help="只看某个方向：base/web/pwn/reverse/crypto/forensics/ir/misc")
    p.add_argument("--json", action="store_true")

    p = add("check", cmd_check, help="逐条真检测（记录路径→PATH→py import→node 模块）")
    p.add_argument("--record", action="store_true", help="把本次新发现的路径写回清单")
    p.add_argument("--version", action="store_true",
                   help="顺带取版本号（**会执行**目标程序；白名单参数 + 5s 超时 + 截断）")
    p.add_argument("--direction", default=None)
    p.add_argument("--json", action="store_true")

    p = add("missing", cmd_missing, help="只列不可用的（脚本/门禁可用；有缺失则退出码 1）")
    p.add_argument("--direction", default=None)
    p.add_argument("--json", action="store_true")

    p = add("show", cmd_show, help="看一条的完整字段 + 本次解析结果")
    p.add_argument("id")

    p = add("add", cmd_add, help="登记一条（至少一条线索：--path/--commands/--python-import/--node-module）")
    p.add_argument("id")
    p.add_argument("--name"); p.add_argument("--path")
    p.add_argument("--commands", help="命令名，逗号分隔（PATH 里按顺序找）")
    p.add_argument("--python-import", dest="python_import", help="Python 模块名")
    p.add_argument("--node-module", dest="node_module", help="Node 包名")
    p.add_argument("--direction", default=None)
    p.add_argument("--note", default=None)
    p.add_argument("--capabilities", default=None, help="能力标签，逗号分隔（例 web.fuzz）")
    p.add_argument("--version-args", dest="version_args", default=None,
                   help="取版本的参数，逗号分隔（ExifTool 这类不认 --version）")
    p.add_argument("--force", action="store_true", help="覆盖已有条目")

    p = add("record", cmd_record, help="把实测到的路径写回（做题装完新工具就调它）")
    p.add_argument("id"); p.add_argument("path")

    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
