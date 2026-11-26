#!/usr/bin/env python3
"""pyenv_check.py —— 对照官方工具清单体检 Python 环境（缺什么、装什么，一条命令）。

为什么要有它：赛项《工具清单》明确列了 python>=3.8 与四个库
（pyCryptodome / libnum / gmpy2 / requests）。考场上再发现库没装 = 白丢分。
这个脚本把"环境是否可打"变成机器判据：官方库缺任何一个 → 退出码 1。

用法：
    python3 pyenv_check.py            # 人看
    python3 pyenv_check.py --json     # 机器读
    python3 pyenv_check.py --install  # 只打印补装命令，不动手
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
#   自包含（自己 import sys），不依赖本文件是否已 import sys 或用别名。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# (导入名, 官方/加分, 说明, pip 名)
CHECKS = [
    ("Crypto",     "官方", "pyCryptodome —— AES/RSA/哈希",        "pycryptodome"),
    ("libnum",     "官方", "libnum —— n2s/s2n/invmod 等数论小工具", "libnum"),
    ("gmpy2",      "官方", "gmpy2 —— 大整数加速/开方/逆元",        "gmpy2"),
    ("requests",   "官方", "requests —— HTTP 会话（web 自动化）",   "requests"),
    ("pwn",        "加分", "pwntools —— pwn/交互式协议",           "pwntools"),
    ("z3",         "加分", "z3-solver —— 约束求解（逆向/crypto）",  "z3-solver"),
    ("scapy",      "加分", "scapy —— 造包/读包（流量题脚本化）",    "scapy"),
    ("dpkt",       "加分", "dpkt —— 轻量 pcap 解析",               "dpkt"),
    ("websocket",  "加分", "websocket-client —— WS 隧道/协议",      "websocket-client"),
    ("sympy",      "加分", "sympy —— 符号计算（数论/矩阵）",        "sympy"),
    ("numpy",      "加分", "numpy —— 数值/数组（图片、音频题）",     "numpy"),
    ("PIL",        "加分", "Pillow —— 图片处理（隐写/杂项）",        "Pillow"),
]

def probe():
    rows = []
    for mod, level, desc, pipname in CHECKS:
        ok = importlib.util.find_spec(mod) is not None
        ver = ""
        if ok:
            try:
                m = __import__(mod)
                ver = getattr(m, "__version__", "") or ""
            except Exception:
                ver = ""
        rows.append({"模块": mod, "级别": level, "说明": desc, "pip": pipname, "有": ok, "版本": ver})
    return rows

def main() -> int:
    ap = argparse.ArgumentParser(description="对照官方工具清单体检 Python 环境")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--install", action="store_true", help="只打印补装命令")
    a = ap.parse_args()

    rows = probe()
    missing_official = [r for r in rows if r["级别"] == "官方" and not r["有"]]
    missing_extra = [r for r in rows if r["级别"] == "加分" and not r["有"]]
    pyver = ".".join(map(str, sys.version_info[:3]))
    pyok = sys.version_info >= (3, 8)

    if a.install:
        need = sorted({r["pip"] for r in rows if not r["有"]})
        print("python3 -m pip install " + " ".join(need) if need else "# 什么都不缺")
        return 0

    if a.json:
        print(json.dumps({
            "python": pyver, "python_ok": pyok,
            "官方缺": [r["pip"] for r in missing_official],
            "加分缺": [r["pip"] for r in missing_extra],
            "明细": rows,
        }, ensure_ascii=False, indent=2))
        return 1 if (missing_official or not pyok) else 0

    print("Python %s  %s" % (pyver, "✓ (>=3.8)" if pyok else "✗ 需要 >=3.8"))
    print("-" * 58)
    for level in ("官方", "加分"):
        print("[%s]" % level)
        for r in rows:
            if r["级别"] != level:
                continue
            mark = "✓" if r["有"] else "✗"
            tail = ("  %s" % r["版本"]) if r["版本"] else ""
            print("  %s %-10s %s%s" % (mark, r["模块"], r["说明"], tail))
    print("-" * 58)
    if missing_official:
        print("✗ 官方要求的库缺 %d 个 —— 考场上前必须补上：" % len(missing_official))
        print("    python3 -m pip install " + " ".join(r["pip"] for r in missing_official))
    else:
        print("✓ 官方工具清单要求的库齐全")
    if missing_extra:
        print("· 加分库缺 %d 个（不急，按需装）：%s" % (len(missing_extra), " ".join(r["pip"] for r in missing_extra)))
    return 1 if (missing_official or not pyok) else 0

if __name__ == "__main__":
    sys.exit(main())
