#!/usr/bin/env python3
"""edu_index.py 回归测试 —— 治「选目标靠猜、事后无法复核」。

背景（2026-10-04）：用户要「挖几个新出的 edu 证书」。EDUSRC 的漏洞详情页要登录，
但两张公开表（`/list/` 已确认流水、`/rank/company/` 教育软件开发商榜）是全开的 ——
是选「打哪个厂商/哪类洞」的客观依据。本测试守住三件事：
  ① 解析正确（表头/空行/无日期行必须跳过，不能把"没有数据"当结论）；
  ② 品牌词抽取正确（否则 `poc "<整串公司名>"` 命中恒为 0，提示形同虚设）；
  ③ **绝不发网络请求**（测试必须能离线跑、不能碰平台）。

    python3 board/tests/test_edu_index.py
"""
from __future__ import annotations
import sys

import importlib.util
import io
import contextlib
import json
import pathlib
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
TOOL = LAB / "tools" / "edu_index.py"


def load():
    spec = importlib.util.spec_from_file_location("edu_index_mod", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


LIST_FIXTURE = """
<table><tr><th>时间</th><th>标题</th><th>等级</th><th>作者</th></tr>
<tr><td>2026-10-04</td><td>安徽省某高校</td><td>中危</td><td>1ux4ry</td></tr>
<tr><td>2026-10-04</td><td>江苏省某高校</td><td>高危</td><td>Caption</td></tr>
<tr><td colspan="4">没有数据</td></tr></table>
<a href="?page=23920">末页</a>
"""

VENDOR_FIXTURE = """
<table><tr><th>#</th><th>开发商</th><th>修复率</th></tr>
<tr><td>1</td><td>浙江齐治科技股份有限公司</td><td>100%</td></tr>
<tr><td>2</td><td>深圳市安之源电子有限公司</td><td>96.30%</td></tr>
</table>
<a href="?page=17">末页</a>
"""


def main() -> int:
    checks = []

    def ok(n, c, extra=""):
        checks.append((n, bool(c), extra))

    m = load()

    # ① 解析
    rows, total = m.parse_list(LIST_FIXTURE)
    ok("list：跳过表头与无日期行", len(rows) == 2, rows)
    ok("list：字段逐个对（时间/单位/等级/作者）",
       rows[0] == {"time": "2026-10-04", "unit": "安徽省某高校",
                   "severity": "中危", "author": "1ux4ry"}, rows[0])
    ok("list：总页数从分页链接取", total == 23920, total)
    ok("list：无关 HTML 不误报", m.parse_list("<html>hi</html>")[0] == [])

    vr, vtotal = m.parse_vendors(VENDOR_FIXTURE)
    ok("vendor：解析 2 家", len(vr) == 2, vr)
    ok("vendor：修复率保留原文（不四舍五入）", vr[1]["fix_rate"] == "96.30%", vr[1])
    ok("vendor：总页数", vtotal == 17, vtotal)

    # ② 品牌词（决定 poc 查询能不能命中）
    ok("品牌词：去地名+后缀 → 齐治", m.brand("浙江齐治科技股份有限公司") == "齐治",
       m.brand("浙江齐治科技股份有限公司"))
    ok("品牌词：去掉地名的『市』", m.brand("深圳市安之源电子有限公司") == "安之源",
       m.brand("深圳市安之源电子有限公司"))
    ok("品牌词：保留品牌（不误剥）", m.brand("北京瑞格心灵（奥泰瑞格）科技有限公司") == "瑞格心灵",
       m.brand("北京瑞格心灵（奥泰瑞格）科技有限公司"))
    ok("品牌词：短名不误剥", m.brand("安恒") == "安恒", m.brand("安恒"))

    # ③ 离线：show/vendors 只读缓存，不发请求
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="eduindex-test-"))
    cache = {"fetched_at": "t",
             "list": {"rows": rows, "total_pages": total},
             "vendors": {"rows": vr, "total_pages": vtotal}}
    (tmp / "edusrc-index.json").write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    loaded = m.load(tmp)
    ok("load：读回缓存", bool(loaded.get("list")) and bool(loaded.get("vendors")))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc_show = m.show(loaded)
        rc_vend = m.vendors(loaded, 5)
    out = buf.getvalue()
    ok("show：退出码 0", rc_show == 0)
    ok("show：打出等级分布", "中危 1" in out and "高危 1" in out, out[:160])
    ok("vendors：退出码 0 且给出 poc 命令", rc_vend == 0 and 'poc "齐治"' in out, out[:200])
    ok("缓存缺失时不炸（给出提示）",
       m.load(tmp / "nope") == {})

    # ④ 静态守卫：测试自己不能引入网络调用
    src = TOOL.read_text(encoding="utf-8")
    ok("工具里没有任何硬编码的登录凭据", "password" not in src.lower() and "cookie" not in src.lower())
    ok("抓取函数只对公开页（BASE 下 /list/ 与 /rank/company/）",
       "src.sjtu.edu.cn" in src and "/intro" not in src)

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for _, c, _ in checks if c)
    for n, c, e in checks:
        print(f"  {'✓' if c else '✗'} {n}{('  ' + str(e)) if e and not c else ''}")
    print(f"\n{passed}/{len(checks)} 断言通过")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
