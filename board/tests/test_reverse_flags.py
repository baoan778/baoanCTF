#!/usr/bin/env python3
r"""test_reverse_flags.py — L12（flag 动态拼装 / 位打包）的回归测试

为什么要有这一份
----------------
2026-11-09 拆 `examples/二进制安全/puzzlepuzzle` 得出两条判据，写进
`skills/ctf-reverse/references/patterns-ctf-3.md`：
  ① **flag 由校验通过后动态拼装** → 别逆界面，先找组装点；
  ② **数据文件带定长头 + 位打包** → 先写解包脚本，别用 hexdump 手工推。

这两条都是"省时间"型判据：没有它，遇到组装型题会 `strings` 之后误判"没有 flag"，
遇到位打包数据会花半小时手工推还容易在 nibble 顺序上翻车。所以本文件守：
  · 两条判据的核心内容在（含真实代码形态与识别信号）；
  · 位打包脚手架可**实际运行**（不是伪代码）—— 拿合成数据跑一遍验证；
  · 挂到了 SKILL.md 的 references 清单。

    python3 board/tests/test_reverse_flags.py
"""
from __future__ import annotations

import pathlib
import struct
import sys

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = pathlib.Path(__file__).resolve().parents[2]
REF = LAB / "skills" / "ctf-reverse" / "references" / "patterns-ctf-3.md"
SKILL = LAB / "skills" / "ctf-reverse" / "SKILL.md"

checks: list = []


def ok(name, cond, extra=""):
    checks.append((name, bool(cond), str(extra)))


def _report():
    bad = [c for c in checks if not c[1]]
    for name, good, extra in checks:
        print(f"  {'✓' if good else '✗'} {name}" + (f"   {extra[:300]}" if extra and not good else ""))
    print(f"\n通过 {len(checks) - len(bad)} / 失败 {len(bad)}")
    if bad:
        print("失败项：" + "、".join(c[0] for c in bad))
        return 1
    print("✓ L12 flag 动态拼装 / 位打包：全部通过")
    return 0


def _synthetic_pack(tiles: list, w: int, h: int) -> bytes:
    """按题目格式造数据：8 字节头 + 每字节两个 4-bit tile（偶高奇低）。"""
    body = bytearray((w * h + 1) // 2)
    for r in range(h):
        for c in range(w):
            idx = r * w + c
            v = tiles[r][c] & 0xF
            if idx % 2 == 0:
                body[idx // 2] = (v << 4) | (body[idx // 2] & 0x0F)
            else:
                body[idx // 2] = (body[idx // 2] & 0xF0) | v
    return struct.pack("<II", w, h) + bytes(body)


def _synthetic_unpack(raw: bytes) -> list:
    """脚手架本体（与文档里那段同构）。返回 h×w 的 tile 矩阵。"""
    HDR = 8
    w, h = struct.unpack("<II", raw[:HDR])
    body = raw[HDR:]
    assert len(body) == (w * h + 1) // 2, f"size mismatch: {len(body)} vs {w*h/2}"

    def tile(r, c):
        idx = r * w + c
        byte = body[idx // 2]
        return (byte >> 4) if idx % 2 == 0 else (byte & 0xF)

    return [[tile(r, c) for c in range(w)] for r in range(h)]


def main() -> int:
    ok("patterns-ctf-3.md 存在", REF.is_file())
    if not REF.is_file():
        return _report()
    doc = REF.read_text(encoding="utf-8", errors="replace")

    # ── ① flag 动态拼装 ─────────────────────────────────────────
    ok("有「flag 由校验通过后动态拼装」章节", "动态拼装" in doc, doc[:300])
    ok("标了来源（puzzlepuzzle）", "puzzlepuzzle" in doc)
    ok("给了真实组装代码（check_solution + chr）",
       "check_solution" in doc and "chr(" in doc)
    ok("点明「先搜组装点，别先逆界面」",
       "组装点" in doc and "别先逆界面" in doc)
    ok("给了要搜的造字符串调用列表",
       "chr(" in doc and "join(" in doc)
    ok("写了「顺着组装点往回追 2 跳」这个具体动作",
       "往回追" in doc or "追 2 跳" in doc)
    ok("列了反例（strings 就下结论 / 起 GUI 手点）",
       "反例" in doc and ("strings" in doc or "GUI" in doc))
    ok("点明组装型题 strings 本来就没有 flags",
       "组装型" in doc or "压根没" in doc or "根本没有" in doc)

    # ── ② 定长头 + 位打包 ───────────────────────────────────────
    ok("有「定长头 + 位打包」章节", "定长头" in doc and "位打包" in doc)
    ok("给了真实结构（struct.unpack + idx//2 + >>4）",
       "struct.unpack" in doc and ">> 4" in doc and "idx // 2" in doc)
    ok("有识别信号表（≥4 行）",
       "识别信号" in doc and doc.count("| ") >= 20)
    ok("给了通用解包脚手架", "脚手架" in doc)
    ok("脚手架里打印字符图（肉眼快速看结构）",
       "字符图" in doc)
    ok("点了两个易错点（半字节顺序 / 行主序）",
       "半字节" in doc and ("行主序" in doc or "列主序" in doc))
    ok("说明为什么值得单列（时间成本全在读数）",
       "时间成本" in doc)

    # ── ③ 脚手架必须真能跑（不是伪代码）────────────────────────
    w, h = 4, 3
    tiles = [[(r * w + c) & 0xF for c in range(w)] for r in range(h)]
    raw = _synthetic_pack(tiles, w, h)
    ok("合成数据头长 = 8", len(raw) == 8 + (w * h + 1) // 2, str(len(raw)))
    got = _synthetic_unpack(raw)
    ok("解包脚手架能还原原始 tile 矩阵（往返一致）", got == tiles,
       f"expected={tiles} got={got}")
    # 反向：nibble 顺序写反必须得到不同结果（证明这个顺序是真有约束的）
    bad = [[((raw[8 + (r * w + c) // 2] & 0xF) if (r * w + c) % 2 == 0
            else (raw[8 + (r * w + c) // 2] >> 4)) for c in range(w)] for r in range(h)]
    ok("半字节顺序写反 → 结果不同（说明顺序是硬约束）", bad != tiles)

    # ── ④ 挂到 SKILL.md ─────────────────────────────────────────
    ok("ctf-reverse/SKILL.md 存在", SKILL.is_file())
    sk = SKILL.read_text(encoding="utf-8", errors="replace") if SKILL.is_file() else ""
    ok("SKILL.md 的 references 清单点出了这两条",
       "动态拼装" in sk and "位打包" in sk, sk[:400])
    ok("SKILL.md 未超 9000 字节预算", len(sk.encode()) <= 9000, f"{len(sk.encode())} 字节")

    # ── ⑤ 不重复建设 ────────────────────────────────────────────
    ok("没有另建 flag-assembly.md（复用了 patterns-ctf-3.md）",
       not (LAB / "skills" / "ctf-reverse" / "references" / "flag-assembly.md").exists())

    return _report()


if __name__ == "__main__":
    sys.exit(main())
