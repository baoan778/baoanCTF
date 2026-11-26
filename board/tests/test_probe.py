#!/usr/bin/env python3
"""probe.py 回归测试 —— 治「侦察主链跑不通却没人知道」。

背景（2026-10-04 实测）：`tools/bin/httpx` → `gobin/httpx.exe`（Windows 侧 Go 程序）在本机
**DNS 直接失败**（`no address found for host`），却仍返回退出码 0 —— 也就是「安静地什么都没做」。
同一条 URL 用 WSL 侧 `curl`/`py urllib` 是 200。于是侦察主链改成 WSL 侧自建（`tools/probe.py`）。

本测试守住三件事（全部离线，不发请求）：
  ① 指纹匹配正确（标题/正文/响应头三个来源，命中要去重、要给出 poc 查询词）；
  ② 失败路径不抛异常（连不上 → err 字段，而不是 Traceback）；
  ③ 静态守卫：默认只读（GET/HEAD）、有限速、清单文件有大小上限（不能喂 FIFO/超大文件）。

    python3 board/tests/test_probe.py
"""
from __future__ import annotations
import sys

import importlib.util
import pathlib

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
#   自包含（自己 import sys），不依赖本文件是否已 import sys 或用别名。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = pathlib.Path(__file__).resolve().parents[2]
PROBE = LAB / "tools" / "probe.py"


def load():
    spec = importlib.util.spec_from_file_location("probe_mod", PROBE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    checks: list = []

    def ok(name, cond, extra=""):
        checks.append((name, bool(cond), str(extra)))

    m = load()

    # ① URL 归一化
    ok("裸域名补 https", m.normalize("a.edu.cn") == "https://a.edu.cn")
    ok("已带 scheme 不动", m.normalize("http://x:8080/a") == "http://x:8080/a")

    # ② 指纹：正文命中（统一认证 / 正方教务），并给出 poc 词
    blob = "<html><title>统一身份认证平台</title><body>jwglxt</body></html>"
    hits = [(t, p) for n, t, p in m.SIGS if n.lower() in blob.lower()]
    techs = {t for t, _ in hits}
    ok("正文指纹：统一认证 + 正方教务",
       "统一认证" in techs and "正方教务" in techs, techs)
    ok("指纹带 poc 查询词（jwglxt → 正方）",
       any(t == "正方教务" and p == "正方" for t, p in hits), hits)

    # ③ 响应头来源：rememberMe cookie → Shiro
    hdr_blob = "set-cookie: rememberMe=deleteMe"
    ok("响应头指纹：Shiro",
       any(t == "Shiro" and n in hdr_blob for n, t, _ in m.SIGS),
       [t for n, t, _ in m.SIGS if "rememberMe" in n])

    # ④ 失败路径不抛
    r = m.fetch("http://127.0.0.1:9/", timeout=1, proxy=None)
    ok("连不上 → err 字段而非异常", r["status"] is None and bool(r["err"]), r["err"])
    ok("结果结构完整", {"url", "status", "len", "title", "server", "tech",
                        "poc_hints", "final_url", "err"} <= set(r))

    # ⑤ 静态守卫（默认只读 / 限速 / 清单上限）
    src = PROBE.read_text(encoding="utf-8")
    ok("默认只读：只有 GET 与 HEAD 两种方法",
       'method="HEAD" if args.head else "GET"' in src
       and 'method="DELETE"' not in src and 'method="POST"' not in src)
    ok("默认限速 1 qps", "--qps" in src and "default=1.0" in src)
    ok("清单文件有大小上限", "8 * 1024 * 1024" in src)
    ok("不带凭据/不做写操作", "password" not in src.lower() and "upload" not in src.lower())

    # ⑥ 与自检脚本一致：selftest 自己必须能过（子进程，不发网络请求）
    import subprocess
    p = subprocess.run([  # noqa: S603
        "python3", str(PROBE), "--selftest"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    ok("probe --selftest 全绿且退出码 0",
       p.returncode == 0 and "断言通过" in p.stdout, p.stdout[-200:] + p.stderr[-200:])

    passed = sum(1 for _, c, _ in checks if c)
    for name, c, extra in checks:
        print(f"  {'✓' if c else '✗'} {name}" + (f"  {extra}" if extra and not c else ""))
    print(f"\n{passed}/{len(checks)} 断言通过")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
