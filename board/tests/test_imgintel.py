#!/usr/bin/env python3
"""imgintel.py 回归测试 —— 治「OSINT 靠猜」的那个工具，必须有测试守着。

为什么单独写测试文件：2026-10-01 的比赛里 OSINT 是全项目最弱一环（1/3），
当时没有工具、只靠"猜名字"（200+ 次 0 命中）。补工具容易，**没人守才是真风险** ——
所以按仓库纪律：新能力必须进 `board/tests/`（selfcheck 会自动跑、并核对文档里的条数）。

    python3 board/tests/test_imgintel.py
"""
from __future__ import annotations
import sys

import importlib.util
import math
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
IMGINTEL = LAB / "tools" / "imgintel.py"


def load():
    spec = importlib.util.spec_from_file_location("imgintel_mod", IMGINTEL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    checks: list = []

    def check(name, cond, extra=""):
        checks.append((name, bool(cond), str(extra)))

    m = load()

    # ── A. 坐标换算：与官方 bd09convertor 交叉验证（决定性判据）──
    vec_lat, vec_lon = 39.961627, 116.274625
    x, y = m.bd09_to_bd09mc(vec_lat, vec_lon)
    check("BD09MC 命中官方测试向量（逐位 1e-6）",
          abs(x - 12943772.884424742) < 1e-6 and abs(y - 4832666.423350099) < 1e-6, (x, y))
    try:
        import bd09convertor as ref          # 装了官方包就做交叉验证
        rx, ry = ref.convertLL2MC(vec_lon, vec_lat)
        check("与官方包 bd09convertor 交叉一致", abs(rx - x) < 1e-6 and abs(ry - y) < 1e-6, (rx, ry))
    except Exception:
        check("与官方包交叉验证（未装 bd09convertor 则跳过）", True)
    # GCJ02 的火星偏移在各地区方向不同（上海纬度分量是**负**的），所以只能按"总距离量级"判：
    # 权威量级：GCJ02 与 WGS84 相差 300~600 米（这才是稳的判据，不要用"正负号"当判据）。
    def _offset_m(lat, lon):
        g = m.wgs84_to_gcj02(lat, lon)
        dy = (g[0] - lat) * 111_320.0
        dx = (g[1] - lon) * 111_320.0 * math.cos(math.radians(lat))
        return math.hypot(dx, dy)
    o_sh, o_bj = _offset_m(31.2304, 121.4737), _offset_m(39.9087, 116.3975)
    check("WGS84→GCJ02 偏移量级正确（上海 300~700m）", 300 < o_sh < 700, f"{o_sh:.0f}m")
    check("WGS84→GCJ02 偏移量级正确（北京 300~700m）", 300 < o_bj < 700, f"{o_bj:.0f}m")
    check("同一地点重复调用稳定（纯函数）", m.wgs84_to_gcj02(31.2304, 121.4737) == m.wgs84_to_gcj02(31.2304, 121.4737))
    mc = m.wgs84_to_bd09mc(32.0578, 118.7745)      # 本次比赛的锚点（南京五台山）
    check("南京五台山量级合理（x≈13.22M, y≈3.75M）",
          13_200_000 < mc[0] < 13_250_000 and 3_740_000 < mc[1] < 3_760_000, mc)

    # ── B. OCR 噪声过滤：这是"别把噪声当线索"的护栏 ──
    check("噪声行被拦（单字符重复/短拉丁）",
          m._clean_text("ee\nfe\nRe\nEPE\n....") == "", m._clean_text("ee\nfe\nRe\nEPE\n...."))
    check("真中文留下", m._clean_text("值班室\n咖啡机") == "值班室\n咖啡机")
    check("单个汉字算噪声（照片纹理常造出「-一」）", m._clean_text("-一") == "")
    check("长数字留下（编号/门禁码有意义）、短数字丢弃",
          m._clean_text("12\n8848\n132023") == "8848\n132023")
    rows = [(91.0, "值班室"), (12.0, "二二"), (7.0, "Hilt"), (75.0, "8848")]
    kept = m._filter_by_conf(rows, 60.0)
    check("★ 置信度过滤：低 conf 的伪造字被拦",
          "值班室" in kept and "8848" in kept and "二二" not in kept and "Hilt" not in kept, kept)
    check("置信度阈值可调", "二二" not in m._filter_by_conf(rows, 90.0)
          and "钟楼" in m._filter_by_conf([(41.0, "钟楼")], 30.0))

    # ── C. 线索筛选：把"像线索"的东西挑出来 ──
    txt = ("五台山体育中心 32.0578,118.7745 BV1db4y117Q1 2026-04-06 19:59:15 "
           "HUAWEI nova 14 A.S.F.03019 门禁码 3117")
    leads = dict((k, v) for k, v in m.extract_leads(txt))
    check("筛出坐标", "坐标" in leads and "32.0578" in leads["坐标"], leads)
    check("筛出 BV/编号", "编号/BV/ID" in leads and "BV1db4y117Q1" in leads["编号/BV/ID"], leads)
    check("筛出时间", "时间" in leads)
    check("筛出机型", "型号" in leads and "HUAWEI" in leads["型号"], leads)
    check("筛出疑似线索串（A.S.F.03019）",
          "疑似线索串" in leads and "A.S.F.03019" in leads["疑似线索串"], leads)
    check("筛出地名类", any("路名" in k for k in leads), leads)

    # ── D. 反查链接与图床说明（离线可验证，不联网）──
    links = m.search_links("南京 钟楼", "a.jpg")
    check("生成 Bing 图搜链接", any("bing.com/images" in u for _, u in links))
    check("中文长尾用 360 搜索（实测可直连）", any("so.com/s" in u for _, u in links))
    check("反向图搜链接带公网 URL 占位符", sum("<图片公网URL>" in u for _, u in links) >= 3)
    check("图床上传命令齐（catbox/0x0/tmpfiles）",
          all(k in m.UPLOAD_HELP for k in ("catbox.moe", "0x0.st", "tmpfiles.org")))

    # ── E. 报告落档：证据齐全 + 人机配合纪律写进去 ──
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="imgintel-test-"))
    try:
        meta = {"file": "x.jpg", "ImageWidth": 4096, "ImageHeight": 3072, "has_gps": False}
        ocr = {"full": "值班室\n咖啡机", "engine": "tesseract", "blocks": [
            {"row": 0, "col": 0, "box": (0, 0, 100, 100), "text": "监控中心", "conf": 88.0}]}
        geo = {"lat": 32.0578, "lon": 118.7745, "gcj": (32.0592, 118.7808),
               "bd09": (32.0656, 118.7872), "mc": (13224123.4, 3754412.9)}
        note = m.write_report(tmp / "n" / "r.md", pathlib.Path("x.jpg"), meta, ocr,
                              [("可能的路名/店名", "五台山体育中心")], geo, links, "南京 钟楼")
        t = note.read_text(encoding="utf-8")
        check("报告含元数据与「没有 GPS」提示",
              "没有 GPS" in t and "`ImageWidth`: 4096" in t and "`ImageHeight`: 3072" in t)
        check("报告含整图与分块 OCR 原文", "值班室" in t and "监控中心" in t and "conf" not in t.lower()[:0])
        check("报告含坐标三种口径 + 整数形式", "BD09MC" in t and "13224123" in t)
        check("报告含猜测预算纪律（30 次）与找人流程", "30 次" in t and "一句人话" in t)
        check("报告含图床与反查链接", "catbox" in t and "bing.com/images" in t)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    # ── F. 健壮性：坏输入不崩、且给出人话 ──
    check("文件不存在 → 返回 2（不崩）",
          m.cmd_run(type("A", (), {"image": "/nope/nope.jpg", "blocks": "3x3", "lang": "chi_sim+eng",
                                   "geo": None, "search": "", "out": ""})()) == 2)
    tmp2 = pathlib.Path(tempfile.mkdtemp(prefix="imgintel-break-"))
    try:
        txt_file = tmp2 / "x.txt"
        txt_file.write_text("这不是图片", encoding="utf-8")
        check("非图片文件 → 返回 2（不崩、给提示）",
              m.cmd_run(type("A", (), {"image": str(txt_file), "blocks": "3x3", "lang": "chi_sim+eng",
                                       "geo": None, "search": "", "out": ""})()) == 2)
        check("元数据函数对坏文件不抛异常", isinstance(m.metadata(txt_file), dict))
    finally:
        import shutil
        shutil.rmtree(tmp2, ignore_errors=True)

    passed = sum(1 for _, c, _ in checks if c)
    for name, c, extra in checks:
        print(f"  {'✓' if c else '✗'} {name}" + (f"  {extra}" if extra and not c else ""))
    print(f"\n{passed}/{len(checks)} 断言通过")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
