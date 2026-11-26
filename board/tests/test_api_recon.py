#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_api_recon.py — #30 前端接口侦察（api-recon 简化借鉴）

为什么（2026-11-17）：web 题的接口面就在前端 asset 里，但人工翻几百 KB 的 bundle
既慢又常漏（拼接字符串、模板字面量、axios 方法藏在配置里）。ARTEX 用一套
「静态 harvest → 运行时 hook → 参数逆向 → 门禁顺序」把这件事做成流程；baoan 把
第一层做成工具（tools/apiharvest.py），其余三层写进 ctf-web 的参考文档。

判据（跑出来的行为）：
  A. 工具静态自检 9 项全过（含模板变量归一 / query 拆参 / router 归 route / 静态归 static）。
  B. --dir / --json / --big 闸门 / 空输入 / 不存在目录 的行为。
  C. 文档四层齐全 + 完成定义 6 项 + 硬禁止表 + 工具映射。
  D. SKILL.md 与参考材料索引都指向 api-recon.md；TOOLS.md 有条目；bin 包装器指向本工具。
"""
import json
import os
import re
import subprocess
import sys
import tempfile

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TOOL = os.path.join(ROOT, "tools", "apiharvest.py")
DOC = os.path.join(ROOT, "skills", "ctf-web", "references", "api-recon.md")

_passed, _failed = 0, 0


def ck(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print("  ok   " + name)
    else:
        _failed += 1
        print("  FAIL " + name + (("  << " + str(detail)[:400]) if detail else ""))


def run(args, cwd=None):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, TOOL] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=cwd or ROOT, env=env, timeout=120)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


print("A. 工具自检与核心解析")
rc, out = run(["--selftest"])
ck("A1 --selftest rc=0", rc == 0, out)
ck("A2 自检报 9 项", "9 项全过" in out, out)

print("B. CLI 行为")
tmp = tempfile.mkdtemp(prefix="apih_")
open(os.path.join(tmp, "app.js"), "w", encoding="utf-8").write(
    "fetch('/api/a');axios.post('/api/b',{x:1});var r={path:'/admin/x'};")
rc, out = run(["--dir", tmp, "--json"])
ok_json = False
data = None
try:
    data = json.loads(out)
    ok_json = True
except Exception as e:
    ok_json = False
ck("B1 --dir --json 是合法 JSON", rc == 0 and ok_json, out[:300])
paths = [e["path"] for e in (data or {}).get("endpoints", [])]
ck("B2 抠到 /api/a 与 /api/b", "/api/a" in paths and "/api/b" in paths, paths)
ck("B3 axios.post 的方法归到 /api/b",
   any(e["path"] == "/api/b" and "POST" in e["methods"] for e in (data or {}).get("endpoints", [])),
   data)
rc, out = run(["--dir", tmp])
ck("B4 人读输出含门禁顺序提示", rc == 0 and "门禁顺序" in out, out[:300])
open(os.path.join(tmp, "big.js"), "w", encoding="utf-8").write("x" * 50)
rc, out = run(["--dir", tmp, "--big", "20"])
ck("B5 超 --big 打闸门警告", "闸门" in out and "主 entry" in out, out[:400])
rc, out = run([])
ck("B6 无输入 rc=2 且提示", rc == 2 and "没有输入" in out, out)
rc, out = run(["--dir", os.path.join(tmp, "nope")])
ck("B7 不存在目录 rc=2", rc == 2 and "不是目录" in out, out)

print("C. 文档四层与完成定义")
ck("C1 文档存在", os.path.isfile(DOC))
doc = read(DOC) if os.path.isfile(DOC) else ""
for key, name in (("两层模型", "C2 两层模型"), ("静态 harvest", "C3 静态层"),
                  ("运行时 hook", "C4 运行时层"), ("参数逆向", "C5 参数层"),
                  ("门禁强制顺序", "C6 门禁层")):
    ck(name + " 有专节", key in doc)
ck("C7 写死 500 KB 闸门", "500 KB" in doc and "不许整读" in doc)
ck("C8 完成定义 6 项", len(re.findall(r"^- \[ \]", doc, re.M)) == 6,
   len(re.findall(r"^- \[ \]", doc, re.M)))
ck("C9 硬禁止表含「没跑完①②③就开字典」", "没跑完①②③就开字典" in doc)
ck("C10 工具映射含 apiharvest/ctfsurf/browser.py/diff-resp",
   all(x in doc for x in ("apiharvest", "ctfsurf", "browser.py", "diff-resp")))

print("D. 接线：SKILL / 索引 / TOOLS / bin")
skill = read(os.path.join(ROOT, "skills", "ctf-web", "SKILL.md"))
ck("D1 SKILL 起手第 2 步指向 api-recon.md", "references/api-recon.md" in skill)
idx = read(os.path.join(ROOT, "skills", "ctf-web", "references", "参考材料索引.md"))
ck("D2 参考材料索引有 api-recon 行", "api-recon.md" in idx)
tools_md = read(os.path.join(ROOT, "tools", "TOOLS.md"))
ck("D3 TOOLS.md 登记 apiharvest", "apiharvest" in tools_md and "tools/apiharvest.py" in tools_md)
binp = os.path.join(ROOT, "tools", "bin", "apiharvest")
ck("D4 bin 包装器存在且指向本工具", os.path.isfile(binp) and "apiharvest.py" in read(binp))
ck("D5 文档标注上游 ARTEX 出处", "ARTEX" in doc and "api-recon" in doc)

print("")
print("test_api_recon: %d passed, %d failed" % (_passed, _failed))
sys.exit(1 if _failed else 0)
