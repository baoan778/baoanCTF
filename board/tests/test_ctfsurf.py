#!/usr/bin/env python3
"""ctfsurf.py 回归测试 —— 治「明明该推演却先开了 2 万条字典」。

背景（2026-10-05 msgboard 复盘）：一道 PHP 留言板题，2.7 万条目录字典 + 全量弱口令 + sqlmap
全负；而页面上一直摆着两类线索：①style.css 定义了 `.preview` `.admin-grid` `.actions` 却没渲染
（悬空类名 = 未露出的功能）；②表单里已有 hidden `action=login`（说明业务由 action 参数分流，
候选取值是个位数，不是字典）。ctfsurf 把这两类线索自动抠出来，并在小应用上给出**反爆破闸门**。

本测试守住四件事（全部离线，不发请求、不读真实产物）：
  ① 解析正确：表单/hidden 值、悬空类（已渲染的不算）、JS 路径（含变量）、题面中文动词；
  ② 语义与排序：真攻击面进 P0，纯布局压到最后不进 P0，未分类不丢；
  ③ 失败路径与硬规矩：read_capped 拒 FIFO//dev/zero/符号链接，不执行页面 JS，只 GET 一次，无写操作；
  ④ selftest 子进程全绿。

    python3 board/tests/test_ctfsurf.py
"""
from __future__ import annotations
import sys

import importlib.util
import os
import pathlib
import subprocess
import tempfile

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
SURF = LAB / "tools" / "ctfsurf.py"

HTML = """<html><head><link href="/assets/style.css"></head><body>
<div class="shell"><form method="post" class="stack">
  <input type="hidden" name="action" value="login">
  <input type="text" name="username"><input type="password" name="password">
  <button type="submit">登录</button></form>
  <p>旧系统内部留言板；值班留言请按时发布，旧通知已归档。</p>
  <script>fetch('/api/preview.php?draft=1'); var u='/msg/post.php';</script>
</div>
<section class="board"><article class="message-card">x</article></section>
</body></html>"""
CSS = ".shell{}.preview{}.actions{}.admin-grid{}.error{}.message-card{}.hero-card{}.totally-made-up{}"


def load():
    spec = importlib.util.spec_from_file_location("ctfsurf_mod", SURF)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    checks: list = []

    def ok(name, cond, extra=""):
        checks.append((name, bool(cond), str(extra)))

    m = load()
    rep = m.analyze({"index.html": HTML, "style.css": CSS})

    # ① 表单 / hidden
    ok("抽到一个表单", len(rep["forms"]) == 1, len(rep["forms"]))
    f = rep["forms"][0]
    ok("action 缺省=当前URL（不当成没有端点）", f["action"] == "" and f["method"] == "POST", f)
    names = {x["name"] for x in f["fields"]}
    ok("字段名齐", {"action", "username", "password"} <= names, names)
    ok("hidden 取值 = 已在用的 action",
       any(x["type"] == "hidden" and x["value"] == "login" for x in f["fields"]))

    # ② 悬空类
    dc = {d["class"]: d["surface"] for d in rep["dangling"]}
    for want, surf in (("preview", "预览/草稿态"), ("admin-grid", "后台/管理面"),
                       ("actions", "操作区/按钮组"), ("error", "提示分支")):
        ok(f"悬空类 {want} → {surf}", dc.get(want) == surf, dc.get(want))
    for cls in ("shell", "stack", "board", "message-card"):   # 已渲染的
        ok(f"已渲染的类不误报: {cls}", cls not in dc, dc.get(cls))
    ok("未分类也不丢（悬空本身就是证据）", dc.get("totally-made-up") == "未分类", dc.get("totally-made-up"))
    ok("纯布局被识别并压后", dc.get("hero-card") == "纯布局", dc.get("hero-card"))

    # ③ JS 路径
    jp = rep["js_paths"]
    ok("JS 里 fetch 的路径抓到", any("preview.php" in p for p in jp), jp)
    ok("JS 变量里的路径也抓到", any("post.php" in p for p in jp), jp)
    ok("同一路径按去 query 去重", len({p.split("?")[0] for p in jp}) == len(jp), jp)

    # ④ 题面动词
    verbs = " ".join(v["why"] for v in rep["verbs"])
    ok("抽到「留言=发布」", "留言=发布" in verbs, verbs[:80])
    ok("抽到归档/导出", "导出面" in verbs, verbs[:200])
    actions = {a for v in rep["verbs"] for a in v["actions"]}
    ok("动词给出候选 action 取值", {"post", "archive"} <= actions, sorted(actions)[:8])

    # ⑤ 排序：真攻击面 P0，纯布局不进 P0
    p0 = " ".join(p["surface"] for p in rep["surfaces"] if p["prio"] == 0)
    ok("action 取值穷举进 P0", "穷举 action" in p0, p0[:60])
    ok("悬空类进 P0", "悬空类名" in p0, p0[:60])
    ok("纯布局不进 P0", "hero-card" not in p0 and "纯布局" not in p0, p0[:120])
    ok("字典被排到 P9（最后）",
       rep["surfaces"][-1]["prio"] == 9 and "字典" in rep["surfaces"][-1]["surface"])
    ok("闸门对小应用触发", (len(rep["forms"]) + len(rep["js_paths"])) <= 6)

    # ⑥ 安全硬规矩
    for bad in ("/dev/zero", "/dev/null"):
        if not os.path.exists(bad):
            # 本平台无该设备文件（Windows）→ 构造不出来，显式跳过；
            # 否则 read_capped 抛的 ValueError 是「文件不存在」而非「拒绝特殊文件」，会假绿。
            print(f"  SKIP read_capped 拒绝 {bad}（本平台无该设备文件）")
            continue
        try:
            m.read_capped(bad)
            ok(f"read_capped 拒绝 {bad}", False)
        except ValueError:
            ok(f"read_capped 拒绝 {bad}", True)
    with tempfile.TemporaryDirectory() as td:
        fifo = os.path.join(td, "f")
        if not hasattr(os, "mkfifo"):
            # Windows 无 mkfifo（POSIX-only）→ 这条用例构造不出来，显式跳过，不伪装通过。
            print("  SKIP read_capped 拒绝 FIFO（本平台无 os.mkfifo）")
        else:
            os.mkfifo(fifo)
            try:
                m.read_capped(fifo)
                ok("read_capped 拒绝 FIFO", False)
            except ValueError:
                ok("read_capped 拒绝 FIFO", True)
        link = os.path.join(td, "l")
        try:
            os.symlink("/etc/hostname", link)
        except (OSError, NotImplementedError) as exc:
            # Windows 默认无 SeCreateSymbolicLinkPrivilege（WinError 1314）→ 造不出链接。
            # 显式跳过（跳过 ≠ 通过），不把「创建失败」伪装成「拒绝符号链接」。
            print(f"  SKIP read_capped 拒绝符号链接（本平台建不了 symlink: {type(exc).__name__}）")
        else:
            try:
                m.read_capped(link)
                ok("read_capped 拒绝符号链接", False)
            except ValueError:
                ok("read_capped 拒绝符号链接", True)
        big = os.path.join(td, "big")
        with open(big, "wb") as fh:
            fh.truncate(m.MAX_INPUT + 1)
        try:
            m.read_capped(big)
            ok("read_capped 拒绝超限文件", False)
        except ValueError:
            ok("read_capped 拒绝超限文件", True)

    src = SURF.read_text(encoding="utf-8")
    import re as _re
    ok("不执行页面 JS（无 eval/exec 调用）",
       not _re.search(r"(?<![\w.])(eval|exec)\s*\(", src))
    ok("只发 GET（无 POST 提交 / 不构造请求体）",
       'method="POST"' not in src and "urlencode" not in src
       and not _re.search(r"Request\([^)]*data\s*=", src))
    ok("--url 限单次请求", "fetch_once" in src and "爬" not in src.split("def main")[0])
    ok("输入有大小上限", "MAX_INPUT" in src and "8 * 1024 * 1024" in src)
    ok("文件数有上限", "MAX_FILES" in src)
    ok("入口文件存在且可执行", SURF.is_file() and os.access(SURF, os.X_OK))

    # ⑦ selftest 子进程
    p = subprocess.run(["python3", str(SURF), "--selftest"],   # noqa: S603
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    ok("ctfsurf --selftest 全绿（rc=0）", p.returncode == 0 and "OK" in p.stdout,
       (p.stdout + p.stderr)[-200:])

    # ⑧ 真实产物抽查（存在才查，不硬依赖）
    mb = LAB / "work" / "msgboard" / "style.css"
    if mb.is_file():
        r2 = m.analyze({"i.html": (LAB / "work" / "msgboard" / "index.html").read_text(
            encoding="utf-8", errors="replace"), "style.css": mb.read_text(encoding="utf-8")})
        dd = {d["class"]: d["surface"] for d in r2["dangling"]}
        ok("msgboard 真产物：.preview 判为预览/草稿态", dd.get("preview") == "预览/草稿态", dd.get("preview"))
        ok("msgboard 真产物：.admin-grid 判为后台/管理面", dd.get("admin-grid") == "后台/管理面", dd.get("admin-grid"))

    passed = sum(1 for _, c, _ in checks if c)
    for name, c, extra in checks:
        print(f"  {'✓' if c else '✗'} {name}" + (f"  {extra}" if extra and not c else ""))
    print(f"\n{passed}/{len(checks)} 断言通过")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
