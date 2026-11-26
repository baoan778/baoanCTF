#!/usr/bin/env python3
"""percept.py 回归测试 —— 治「把模型看错的东西当成真的」。

血账（2026-11-06）
------------------
OpenSecurity-2.0 的 `llm-perception-verification.md` 记了一个真实案例：
低对比度监控视频帧上，vision-LLM **稳定读出 7 位数字**，双经典引擎全图/裁剪
零检出、消融失效、差异图无字形簇 —— 稳定复现的读数其实是幻觉。

本仓移植时遇到真实约束：**只有 tesseract 一个引擎**（无 rapidocr/easyocr/
paddleocr；pip 受 PEP 668 管控），且无 PIL/numpy。
→ 判据降级为「同一引擎 4 种 PSM 复现」，通过文案**必须**声明"非双引擎"。

本测试守五件事（全离线；`--selftest` 不依赖图片）：
  ① board/percept.py --selftest 全绿（12 组断言）；
  ② 正向：真图上真文字 → 判通过（退出码 0）；
  ③ 反向：图上没有的串 → 判高度可疑（退出码 3），且结论说"幻觉/不要写进结论"；
  ④ 诚实边界：通过文案必须带「非双引擎」，不许假装做了双引擎验证；
  ⑤ 静态守卫：四种 PSM 都在、无 PIL 硬依赖、退出码契约稳定。

    python3 board/tests/test_percept.py
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import tempfile

import sys as _sys
_sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

try:
    import sys as _sm
    for _s in (_sm.stdout, _sm.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = pathlib.Path(__file__).resolve().parents[2]
MOD = LAB / "board" / "percept.py"
PY = _sys.executable or "python3"

checks: list = []


def ok(name, cond, extra=""):
    checks.append((name, bool(cond), str(extra)))


def _report():
    bad = [c for c in checks if not c[1]]
    for name, good, extra in checks:
        print(f"  {'✓' if good else '✗'} {name}" + (f"   {extra[:240]}" if extra and not good else ""))
    print(f"\n通过 {len(checks) - len(bad)} / 失败 {len(bad)}")
    if bad:
        print("失败项：" + "、".join(c[0] for c in bad))
        return 1
    print("✓ 感知交叉验证（percept）：全部通过")
    return 0


def _report_skip():
    """静态 + selftest 已过，E2E 因平台缺依赖跳过 → rc=77（跳过 ≠ 通过）。"""
    bad = [c for c in checks if not c[1]]
    for name, good, extra in checks:
        print(f"  {'✓' if good else '✗'} {name}" + (f"   {extra[:240]}" if extra and not good else ""))
    if bad:
        print(f"\n通过 {len(checks) - len(bad)} / 失败 {len(bad)}")
        print("失败项：" + "、".join(c[0] for c in bad))
        return 1
    print(f"\n通过 {len(checks)} / 失败 0（E2E 跳过）")
    print("SKIP 本机没有 tesseract/text2image（容器内跑才有意义）："
          "docker exec pictf bash -lc 'cd /opt/pi-ctf && python3 board/tests/test_percept.py'")
    return 77


def run(*args, cwd=None):
    return subprocess.run(  # noqa: S603
        [PY, str(MOD), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180, cwd=cwd or str(LAB),
    )


def main() -> int:
    ok("board/percept.py 存在", MOD.is_file())
    if not MOD.is_file():
        return _report()

    src = MOD.read_text(encoding="utf-8", errors="replace")

    # ⑤ 静态守卫
    ok("四种 PSM 配置都在",
       all(f'"psm{n}"' in src for n in (3, 6, 7, 11)))
    ok("无 PIL 硬依赖（本机没有）", "from PIL" not in src and "import PIL" not in src)
    ok("无 numpy 硬依赖（本机没有）", "import numpy" not in src)
    ok("通过文案强制声明「非双引擎」", "非双引擎" in src)
    ok("零命中文案要求归档/不要写进结论",
       "不要写进结论" in src and "幻觉" in src)
    ok("退出码契约：通过 0 / 可疑 3", "return 0 if v[\"ok\"] else 3" in src.replace("'", '"'))

    # ① selftest
    p = run("--selftest")
    ok("--selftest 全绿（rc=0）",
       p.returncode == 0 and "OK" in p.stdout, (p.stdout + p.stderr)[-300:])

    # ②③ 需要真实图片 + tesseract
    #    ★ 宿主机没有 tesseract（实测），E2E 只能在容器内跑：
    #      docker exec pictf bash -lc 'cd /opt/pi-ctf && python3 board/tests/test_percept.py'
    #      按本仓约定：产物确实不在当前平台 → skip(77)，**跳过 ≠ 通过**。
    if not shutil.which("tesseract") or not shutil.which("text2image"):
        print("  ~ SKIP E2E：本机没有 tesseract/text2image（在容器内跑才有意义）")
        # 静态 + selftest 已过，E2E 部分跳过
        return _report_skip()

    ok("tesseract 可用（E2E 前提）", True)
    ok("text2image 可用（造测试图）", True)

    with tempfile.TemporaryDirectory() as td:
        txt = pathlib.Path(td) / "t.txt"
        txt.write_text("SUN{test123}\n", encoding="utf-8")
        base = pathlib.Path(td) / "base"
        g = subprocess.run(  # noqa: S603
            ["text2image", f"--text={txt}", f"--outputbase={base}",
             "--font=DejaVu Sans", "--ptsize=40"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120, cwd=td,
        )
        img = pathlib.Path(str(base) + ".tif")
        ok("造出测试图", img.is_file(), (g.stdout + g.stderr)[-200:])
        if not img.is_file():
            return _report()

        # ② 正向：真文字应通过
        p = run("verify", "--img", str(img), "--claim", "SUN{test123}")
        out = (p.stdout or "") + (p.stderr or "")
        ok("正向：rc=0", p.returncode == 0, out[-300:])
        ok("正向：结论含「通过」", "通过" in out, out[-300:])
        # ④ 诚实边界
        ok("正向：声明「非双引擎」", "非双引擎" in out, out[-300:])

        # ③ 反向：图上没有的串必须被判可疑
        p = run("verify", "--img", str(img), "--claim",
                "sun{fr4gm3nt3d_r3fl3ct10ns_1n_th3_futur3}")
        out = (p.stdout or "") + (p.stderr or "")
        ok("反向：rc=3（可疑）", p.returncode == 3, out[-300:])
        ok("反向：结论含「幻觉」", "幻觉" in out, out[-300:])
        ok("反向：给出可照做的动作（不要写进结论）",
           "不要写进结论" in out, out[-300:])
        ok("反向：不被判通过", "✓ 通过" not in out, out[-300:])

        # scan 子命令能出内容
        p = run("scan", "--img", str(img))
        ok("scan：能读出文字", "SUN{test123}" in (p.stdout or ""), (p.stdout or "")[-200:])

        # pixels 子命令给旁证且声明边界
        p = run("pixels", "--img", str(img))
        ok("pixels：声明「旁证不是终裁」", "旁证" in (p.stdout or ""), (p.stdout or "")[-200:])

    # 输入错误契约
    p = run("verify", "--img", "/nonexistent/x.png", "--claim", "x")
    ok("输入错误：rc=4", p.returncode == 4, (p.stdout or p.stderr or "")[-200:])

    return _report()


if __name__ == "__main__":
    raise SystemExit(main())
