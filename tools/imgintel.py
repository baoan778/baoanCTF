#!/usr/bin/env python3
"""imgintel.py —— 图片情报流水线（治「图寻/建筑识别靠猜」这个最弱项）

为什么有它：2026-10-01 的比赛里，两道路线题（图寻/建筑识别）我们**失败得很彻底** ——
用 md5 oracle 扫了 176 万点坐标、猜了 200+ 个建筑名，全部落空。
根因不是"想不出"，而是**缺一条把图像翻译成可检索语义的稳定流水线**：元数据、文字、坐标、反查链接
这几件事每次都在手搓，于是只能靠猜名字。

它把这件事固化成 6 步（每步都留证据，可复核）：
  1. 元数据：EXIF/GPS/相机/时间（常有题把线索放这里，也常被剥）
  2. OCR：整图 + **分块**（题面说"角落"时，整图 OCR 会漏）—— 每块给坐标
  3. 线索筛选：把 OCR 文本按「招牌/路牌/型号/坐标/编号」粗分类，先看高价值的
  4. 反查链接：Bing 图搜（关键词）/ Yandex、Google 反向图搜（需公网图床 URL）+ 图床上传命令
  5. 坐标：WGS84 → GCJ02 → BD09 → **BD09MC**（百度墨卡托，米）—— 与官方包逐位对齐
  6. 落档：`notes/osint-<图名>.md`（元数据/OCR 原文/坐标/反查链接/下一步）

用法：
    python3 tools/imgintel.py 图片.jpg                      # 全流程
    python3 tools/imgintel.py 图片.jpg --blocks 3x3         # 分块 OCR（默认就是 3x3）
    python3 tools/imgintel.py 图片.jpg --geo 32.0578 118.7745   # 额外做坐标换算
    python3 tools/imgintel.py 图片.jpg --search "五台山 钟楼"    # 生成图搜链接
    python3 tools/imgintel.py --selftest                     # 离线自检（含官方坐标测试向量）
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import shutil
import subprocess
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

# ── 可选的图像库（缺了也能降级跑）──
try:
    from PIL import Image, ImageOps  # type: ignore
except Exception:                     # pragma: no cover
    Image = None
    ImageOps = None

# ══════════════ 1) 元数据 ══════════════
def metadata(path: pathlib.Path) -> dict:
    """EXIF/基础信息。优先 exiftool（信息全），没有就用 PIL 兜底。"""
    out = {"file": path.name, "size": path.stat().st_size if path.is_file() else 0}
    if shutil.which("exiftool") and path.is_file():
        try:
            r = subprocess.run(["exiftool", "-a", "-G1", "-s", "-json", str(path)],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=30)
            if r.returncode == 0 and r.stdout.strip():
                raw = json.loads(r.stdout)[0]
                keys = ("Make", "Model", "Software", "DateTimeOriginal", "CreateDate",
                        "GPSLatitude", "GPSLongitude", "GPSPosition", "GPSAltitude",
                        "ImageWidth", "ImageHeight", "FileType")
                for k, v in raw.items():
                    name = k.split(":")[-1]
                    if name in keys:
                        out[name] = v
        except Exception as e:
            out["exiftool_error"] = f"{type(e).__name__}: {e}"
    if "ImageWidth" not in out and Image is not None and path.is_file():
        try:
            with Image.open(path) as im:
                out["ImageWidth"], out["ImageHeight"] = im.size
                out["mode"] = im.mode
        except Exception as e:
            out["pil_error"] = f"{type(e).__name__}: {e}"
    out["has_gps"] = bool(out.get("GPSPosition") or (out.get("GPSLatitude") and out.get("GPSLongitude")))
    return out


# ══════════════ 2) OCR（整图 + 分块）══════════════
def _tesseract(img_path: pathlib.Path, lang: str = "chi_sim+eng", psm: int = 11) -> str:
    if not shutil.which("tesseract"):
        return ""
    try:
        r = subprocess.run(["tesseract", str(img_path), "stdout", "-l", lang, "--psm", str(psm)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180)
        return r.stdout or ""
    except Exception:
        return ""


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return 0x3400 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF


def _line_plausible(s: str) -> bool:
    """一行 OCR 文本是否**可信**（宁可漏，也不要把噪声当线索）。

    判据（针对 tesseract 在照片上的典型噪声："ee" / "Re" / "EPE" / "ae" 这类）：
      ① 长度 ≥2；
      ② 含 CJK 字 → 直接可信（中文 OCR 基本不会凭空造字）；
      ③ 纯拉丁/数字 → 必须「有 ≥4 个字符的词」且「字符种类 ≥ 3」且「字母数字占比 ≥ 0.6」；
      ④ 单一字符重复（如 "eeee"、"...." ）一律丢弃。
    """
    if len(s) < 2:
        return False
    cjk = sum(1 for c in s if _is_cjk(c))
    if cjk >= 2:                     # 单个汉字常是噪声（"-一"），要求 ≥2 个
        return True
    if cjk == 1 and len(s) <= 2:     # 只留「单字行」里明显有意义的（字母数字混排）
        return False
    if s.isdigit():
        return len(s) >= 4          # 编号/门禁码/坐标片段是有意义的，但 "12" 不是
    if len(s) < 4:
        return False
    if len(set(s.lower())) < 3:
        return False
    alnum = sum(c.isalnum() for c in s)
    if alnum < len(s) * 0.6:
        return False
    return any(len(w) >= 4 for w in re.findall(r"[A-Za-z0-9]+", s))


def _clean_text(t: str) -> str:
    """OCR 原始输出噪声很大：按 _line_plausible 逐行过滤 + 压缩空白。

    ★ 这一层是**防"看起来有字"**的：2026-10-01 的比赛里，两张确实没文字的照片被
    OCR 出了 1000+ 字符噪声（"ee/fe/Re/EPE"），如果不拦，后续判断会被带偏。
    """
    lines = []
    for ln in (t or "").splitlines():
        s = " ".join(ln.split())
        if _line_plausible(s):
            lines.append(s)
    return "\n".join(lines)


def _tesseract_tsv(img_path: pathlib.Path, lang: str = "chi_sim+eng",
                   psm: int = 11, timeout: int = 180) -> list:
    """用 tesseract 的 TSV 输出拿**每段文字的置信度**。

    为什么必须看置信度：照片上的纹理/反光会被 OCR "造字"（实测能凭空造出「二二」「三二」「Hilt」），
    只靠字符规则拦不住；而 tesseract 自己会给这些噪声很低的 conf，按 conf 一刀切最干净。
    返回 [(conf: float, text: str), ...]。
    """
    if not shutil.which("tesseract"):
        return []
    try:
        r = subprocess.run(["tesseract", str(img_path), "stdout", "-l", lang, "--psm", str(psm), "tsv"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
    except Exception:
        return []
    rows = []
    for ln in (r.stdout or "").splitlines()[1:]:
        f = ln.split("\t")
        if len(f) < 12:
            continue
        try:
            conf = float(f[10])
        except ValueError:
            continue
        txt = f[11].strip()
        if txt and conf >= 0:
            rows.append((conf, txt))
    return rows


def _filter_by_conf(rows: list, conf_min: float = 60.0) -> str:
    """按置信度 + 可读性双重过滤，拼回文本。"""
    keep = [t for c, t in rows if c >= conf_min and _line_plausible(t)]
    return "\n".join(keep)


def ocr_blocks(img_path: pathlib.Path, blocks: str = "3x3", lang: str = "chi_sim+eng",
               out_dir: pathlib.Path = None) -> dict:
    """整图 + 分块 OCR。分块是为了抓"角落"里的字（题面常这么暗示）。"""
    res = {"full": "", "blocks": [], "engine": shutil.which("tesseract") or "缺失（apt-get install -y tesseract-ocr tesseract-ocr-chi-sim）"}
    if Image is None:
        res["error"] = "没装 Pillow（pip install pillow），无法分块"
        return res
    try:
        m = re.match(r"(\d+)x(\d+)$", blocks or "")
        nx, ny = (int(m.group(1)), int(m.group(2))) if m else (3, 3)
    except Exception:
        nx, ny = 3, 3
    rows = _tesseract_tsv(img_path, lang)
    if rows:
        res["full"] = _filter_by_conf(rows, getattr(res, "conf_min", 60.0))
        res["avg_conf"] = round(sum(c for c, _ in rows) / len(rows), 1)
        res["raw_words"] = len(rows)
    else:
        res["full"] = _clean_text(_tesseract(img_path, lang))
    if not shutil.which("tesseract"):
        return res
    tmp = (out_dir or img_path.parent) / f".imgintel-{img_path.stem}"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(img_path) as im:
            W, H = im.size
            for iy in range(ny):
                for ix in range(nx):
                    box = (int(W * ix / nx), int(H * iy / ny), int(W * (ix + 1) / nx), int(H * (iy + 1) / ny))
                    tile = im.crop(box)
                    tile = ImageOps.grayscale(tile)
                    tile = tile.resize((tile.width * 2, tile.height * 2))     # 放大有助小字
                    p = tmp / f"t{iy}{ix}.png"
                    tile.save(p)
                    trows = _tesseract_tsv(p, lang)
                    txt = (_filter_by_conf(trows) if trows
                           else _clean_text(_tesseract(p, lang)))
                    if txt:
                        conf = round(sum(c for c, _ in trows) / len(trows), 1) if trows else None
                        res["blocks"].append({"row": iy, "col": ix, "box": box,
                                              "text": txt, "conf": conf})
    finally:
        for f in tmp.glob("*"):
            try:
                f.unlink()
            except OSError:
                pass
        try:
            tmp.rmdir()
        except OSError:
            pass
    return res


# ══════════════ 3) 线索筛选 ══════════════
PATTERNS = [
    ("坐标", r"-?\d{1,3}\.\d{4,}\s*[,，]\s*-?\d{1,3}\.\d{4,}"),
    ("编号/BV/ID", r"\b(BV[0-9A-Za-z]{10}|av\d{1,7}|[A-Z]{2,5}[-_]\d{2,})\b"),
    ("时间", r"\b(20\d{2}[-/年]\d{1,2}[-/月]\d{1,2}|\d{1,2}:\d{2}(:\d{2})?)\b"),
    ("型号", r"\b(HUAWEI|HONOR|Xiaomi|Redmi|OPPO|vivo|Samsung|Canon|Nikon|SONY|iPhone)[^,，。\n]{0,18}"),
    ("可能的路名/店名", r"[\u4e00-\u9fa5]{2,10}(?:路|街|道|巷|广场|大厦|中心|医院|学校|酒店|花园|桥|站|馆|寺|塔)"),
    ("疑似线索串", r"[A-Za-z0-9._&+-]{4,24}\.\d{2,6}"),
]


def extract_leads(text: str) -> list:
    """从 OCR 文本里挑出"像线索"的片段，按类别归类（先看高价值的）。"""
    out = []
    for label, pat in PATTERNS:
        for m in re.finditer(pat, text or ""):
            out.append((label, m.group(0).strip()))
    seen, uniq = set(), []
    for label, s in out:
        if (label, s) not in seen:
            seen.add((label, s))
            uniq.append((label, s))
    return uniq


# ══════════════ 4) 反查链接 ══════════════
def search_links(keywords: str, image_name: str = "") -> list:
    """生成可点开的检索/反查链接。

    实测（2026-10-01）：
      · Bing 图搜可用（关键词）→ 能捞到同款照片/地标名
      · 反向图搜需要**公网可访问的图片 URL**，本地文件不能直接用 → 先上传图床（见 --upload-help）
      · Wayback / 部分维基镜像在本机不可达，别把宝压在那上面
    """
    from urllib.parse import quote
    kw = quote(keywords or "")
    return [
        ("Bing 图搜（关键词，可用）", f"https://www.bing.com/images/search?q={kw}"),
        ("Bing 网页（关键词）", f"https://www.bing.com/search?q={kw}"),
        ("360 搜索（中文长尾更好，实测可直连）", f"https://www.so.com/s?q={kw}"),
        ("Yandex 反向图搜（需公网 URL）", "https://yandex.com/images/search?rpt=imageview&url=<图片公网URL>"),
        ("Bing 反向图搜（需公网 URL）", "https://www.bing.com/images/search?view=detailv2&iss=sbi&form=SBIVSP&sbisrc=UrlPaste&q=imgurl:<图片公网URL>"),
        ("Google Lens（需公网 URL）", "https://lens.google.com/uploadbyurl?url=<图片公网URL>"),
    ]


UPLOAD_HELP = """上传图床拿公网 URL（反查图搜的前提，任选一个能通的）：
  curl -s -F 'fileToUpload=@<图片>' https://catbox.moe/user/api.php -F reqtype=fileupload
  curl -s -F 'file=@<图片>' https://0x0.st
  curl -s -F 'file=@<图片>' https://tmpfiles.org/api/v1/upload
  # 拿到 URL 后替换上面的 <图片公网URL> 再点开反查"""


# ══════════════ 5) 坐标换算（与官方包逐位对齐）══════════════
X_PI = math.pi * 3000.0 / 180.0
A, EE = 6378245.0, 0.00669342162296594323
LLBAND = [75, 60, 45, 30, 15, 0]
LL2MC = [
    [-0.0015702102444, 111320.7020616939, 1704480524535203, -10338987376042340,
     26112667856603880, -35149669176653700, 26595700718403920, -10725012454188240,
     1800819912950474, 82.5],
    [0.0008277824516172526, 111320.7020463578, 647795574.6671607, -4082003173.641316,
     10774905663.51142, -15171875531.51559, 12053065338.62167, -5124939663.577472,
     913311935.9512032, 67.5],
    [0.00337398766765, 111320.7020202162, 4481351.045890365, -23393751.19931662,
     79682215.47186455, -115964993.2797253, 97236711.15602145, -43661946.33752821,
     8477230.501135234, 52.5],
    [0.00220636496208, 111320.7020209128, 51751.86112841131, 3796837.749470245,
     992013.7397791013, -1221952.21711287, 1340652.697009075, -620943.6990984312,
     144416.9293806241, 37.5],
    [-0.0003441963504368392, 111320.7020576856, 278.2353980772752, 2485758.690035394,
     6070.750963243378, 54821.18345352118, 9540.606633304236, -2710.55326746645,
     1405.483844121726, 22.5],
    [-0.0003218135878613132, 111320.7020701615, 0.00369383431289, 823725.6402795718,
     0.46104986909093, 2351.343141331292, 1.58060784298199, 8.77738589078284,
     0.37238884252424, 7.45],
]


def wgs84_to_gcj02(lat, lon):
    def _tlat(x, y):
        r = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
        r += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
        r += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
        r += (160.0 * math.sin(y / 12.0 * math.pi) + 320.0 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
        return r

    def _tlon(x, y):
        r = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
        r += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
        r += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
        r += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
        return r

    dlat, dlon = _tlat(lon - 105.0, lat - 35.0), _tlon(lon - 105.0, lat - 35.0)
    rad = lat / 180.0 * math.pi
    m = 1 - EE * math.sin(rad) ** 2
    sq = math.sqrt(m)
    dlat = (dlat * 180.0) / ((A * (1 - EE)) / (m * sq) * math.pi)
    dlon = (dlon * 180.0) / (A / sq * math.cos(rad) * math.pi)
    return lat + dlat, lon + dlon


def gcj02_to_bd09(lat, lon):
    z = math.sqrt(lon * lon + lat * lat) + 0.00002 * math.sin(lat * X_PI)
    t = math.atan2(lat, lon) + 0.000003 * math.cos(lon * X_PI)
    return z * math.sin(t) + 0.006, z * math.cos(t) + 0.0065


def bd09_to_bd09mc(lat, lon):
    """BD09 经纬度 → 百度墨卡托（米）。★ 纬度要先除以该带阈值 c[9] 再代入多项式。"""
    la, lo = abs(lat), abs(lon)
    c = LL2MC[-1]
    for i, band in enumerate(LLBAND):
        if la >= band:
            c = LL2MC[i]
            break
    x = c[0] + c[1] * lo
    t = la / c[9]
    y = (c[2] + c[3] * t + c[4] * t ** 2 + c[5] * t ** 3 + c[6] * t ** 4
         + c[7] * t ** 5 + c[8] * t ** 6)
    return (x * (-1 if lon < 0 else 1)), (y * (-1 if lat < 0 else 1))


def wgs84_to_bd09mc(lat, lon):
    blat, blon = gcj02_to_bd09(*wgs84_to_gcj02(lat, lon))
    return bd09_to_bd09mc(blat, blon)


# ══════════════ 6) 落档 ══════════════
def write_report(out: pathlib.Path, img: pathlib.Path, meta: dict, ocr: dict, leads: list,
                 geo=None, links=None, keywords: str = "") -> pathlib.Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    L = [f"# 图片情报：{img.name}", ""]
    L += ["## 1. 元数据", ""]
    for k in sorted(meta):
        L.append(f"- `{k}`: {meta[k]}")
    if not meta.get("has_gps"):
        L += ["", "> **没有 GPS** —— 这是常态（题面常会把 EXIF 剥掉）。别在这上面耗，直接走文字/视觉线索。"]
    L += ["", "## 2. OCR（整图）", "", "```text", ocr.get("full") or "（没读到文字）", "```"]
    L += ["", "## 3. OCR（分块，抓“角落”文字）", "", f"引擎：{ocr.get('engine')}", ""]
    if ocr.get("blocks"):
        for b in ocr["blocks"]:
            L.append(f"- 第 {b['row'] + 1} 行第 {b['col'] + 1} 块 {b['box']}：")
            L.append("  ```text")
            for ln in b["text"].splitlines():
                L.append("  " + ln)
            L.append("  ```")
    else:
        L += ["（分块没读到文字：可能是纯图形题 / 分辨率不够 / 需要先裁切放大指定区域）"]
    L += ["", "## 4. 线索候选（按类别）", ""]
    if leads:
        for label, s in leads:
            L.append(f"- **{label}**：`{s}`")
    else:
        L += ["（没筛出线索；下一步：把疑似区域单独裁切放大后再 OCR，或人工看一遍）"]
    if geo:
        L += ["", "## 5. 坐标换算", "",
              f"- 输入（WGS84）：{geo['lat']}, {geo['lon']}",
              f"- GCJ02：{geo['gcj'][0]:.6f}, {geo['gcj'][1]:.6f}",
              f"- BD09：{geo['bd09'][0]:.6f}, {geo['bd09'][1]:.6f}",
              f"- **BD09MC（米）**：{geo['mc'][0]:.1f}, {geo['mc'][1]:.1f}",
              f"- 若题目要 `x,y` 整数：`{round(geo['mc'][0])},{round(geo['mc'][1])}`",
              "", "> 口径别搞错：题目说「百度墨卡托」= 在 BD09 之上做的投影（见报告第 5 节的第 4 行）；",
              "> 若你的坐标来源是百度地图＝BD09，直接调用 `bd09_to_bd09mc`，**不要再转一次**。"]
    if links:
        L += ["", "## 6. 反查/检索链接", ""]
        for name, url in links:
            L.append(f"- {name}：{url}")
        L += ["", "```text", UPLOAD_HELP, "```"]
    L += ["", "## 7. 下一步（人机配合）", "",
          "1. **找一句人话**：把这张图给同事/群里看一眼，问「这是哪」。**一句人话能顶我 200 次猜测** —— 拿到后写进这里。",
          "2. 把 4 节的线索逐个去检索（先用 Bing 图搜关键词），命中的页面把链接贴回来。",
          "3. 坐标类题目：拿到经纬度先确认口径（WGS84/GCJ02/BD09），再换算，然后用题目给的校验（如 flag 的 md5）**本地验证后再提交**。",
          "4. 猜名字/猜坐标要有预算：**超过 30 次还没命中就停**，换路线或找人 —— 这是硬纪律。", ""]
    out.write_text("\n".join(L), encoding="utf-8")
    return out


def cmd_run(args) -> int:
    img = pathlib.Path(args.image)
    if not img.is_file():
        print(f"[!] 文件不存在：{img}")
        return 2
    if Image is None:
        print("[!] 没装 Pillow（py -m pip install pillow）—— 元数据/OCR 都需要它")
        return 2
    try:
        with Image.open(img) as im:
            im.verify()
    except Exception as e:
        print(f"[!] 这不像图片文件（{type(e).__name__}）：{img.name}\n"
              f"    → 本工具处理 png/jpg/webp 等图像；文本文件请直接读，压缩包先解开")
        return 2
    meta = metadata(img)
    ocr = ocr_blocks(img, args.blocks, args.lang)
    leads = extract_leads((ocr.get("full") or "") + "\n" +
                          "\n".join(b["text"] for b in ocr.get("blocks") or []))
    geo = None
    if args.geo:
        lat, lon = float(args.geo[0]), float(args.geo[1])
        gj = wgs84_to_gcj02(lat, lon)
        bd = gcj02_to_bd09(*gj)
        geo = {"lat": lat, "lon": lon, "gcj": gj, "bd09": bd, "mc": bd09_to_bd09mc(*bd)}
    links = search_links(args.search, img.name) if args.search else None
    out = pathlib.Path(args.out) if args.out else pathlib.Path("notes") / f"osint-{img.stem}.md"
    note = write_report(out, img, meta, ocr, leads, geo, links, args.search)
    print(f"[+] 元数据：{ '有 GPS ✅' if meta.get('has_gps') else '无 GPS（常态）' }  尺寸 {meta.get('ImageWidth')}x{meta.get('ImageHeight')}")
    print(f"[+] OCR：整图 {len(ocr.get('full') or '')} 字 / 分块命中 {len(ocr.get('blocks') or [])} 块")
    if leads:
        print("[+] 线索候选：" + "; ".join(f"{a}={b}" for a, b in leads[:6]))
    if geo:
        print(f"[+] BD09MC：{geo['mc'][0]:.1f}, {geo['mc'][1]:.1f}  （整数 {round(geo['mc'][0])},{round(geo['mc'][1])}）")
    print(f"[+] 报告已写：{note}")
    return 0


def selftest() -> int:
    import tempfile
    checks = []

    def ok(n, c, extra=""):
        checks.append((n, bool(c), extra))

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="imgintel-"))

    # 1) 坐标：官方 bd09convertor 的测试向量（逐位）
    mc = bd09_to_bd09mc(39.961627, 116.274625)
    ok("BD09MC 与官方测试向量一致（116.274625,39.961627）",
       abs(mc[0] - 12943772.884424742) < 1e-6 and abs(mc[1] - 4832666.423350099) < 1e-6,
       mc)
    mc2 = wgs84_to_bd09mc(31.2304, 121.4737)
    ok("WGS84 全链（上海人民广场量级）", 13_500_000 < mc2[0] < 13_600_000 and 3_500_000 < mc2[1] < 3_800_000, mc2)
    ok("纬度带归一化（高纬不越界）", bd09_to_bd09mc(74.0, 180.0)[1] > 0)

    # 2) 线索筛选
    txt = ("0888 咖啡机 五台山体育中心 32.0578,118.7745 BV1db4y117Q1 "
           "2026-04-06 19:59:15 HUAWEI nova 14 A.S.F.03019")
    leads = dict((l, s) for l, s in extract_leads(txt))
    ok("筛出坐标", "坐标" in leads and "32.0578" in leads["坐标"], leads)
    ok("筛出 BV/编号", "编号/BV/ID" in leads and "BV1db4y117Q1" in leads["编号/BV/ID"], leads)
    ok("筛出时间", "时间" in leads)
    ok("筛出型号", "型号" in leads)
    ok("筛出疑似线索串", "疑似线索串" in leads and "A.S.F.03019" in leads["疑似线索串"], leads)

    # 3) 检索链接
    links = search_links("南京 钟楼", "a.jpg")
    ok("生成 Bing 图搜链接", any("bing.com/images" in u for _, u in links))
    ok("反向图搜模板带占位符", any("<图片公网URL>" in u for _, u in links))
    ok("图床上传说明包含 catbox", "catbox.moe" in UPLOAD_HELP)

    # 4) OCR 噪声清洗
    ok("清洗 OCR 噪声（符号行/单字符行丢掉）",
       _clean_text("~~\n|\n南京 钟楼\n..\nA") == "南京 钟楼", repr(_clean_text("~~\n|\n南京 钟楼\n..\nA")))
    ok("★ 照片噪声被拦住（ee/Re/EPE 这类不再当线索）",
       _clean_text("ee\nfe\nRe\nEPE\nae\nSu\n....") == "",
       repr(_clean_text("ee\nfe\nRe\nEPE\nae\nSu\n....")))
    ok("中文招牌/英文单词能留下",
       _clean_text("咖啡机\nChina Bank\nA.S.F.03019") == "咖啡机\nChina Bank\nA.S.F.03019",
       repr(_clean_text("咖啡机\nChina Bank\nA.S.F.03019")))
    rows_fake = [(91.0, "值班室"), (88.0, "咖啡机"), (12.0, "二二"), (7.0, "Hilt"),
                 (3.0, "Sane"), (75.0, "8848"), (62.0, "K4-19"), (40.0, "钟楼")]
    kept = _filter_by_conf(rows_fake, 60.0)
    ok("★ 置信度过滤：噪声（二二/Hilt/Sane）被拦、真字留下",
       "值班室" in kept and "咖啡机" in kept and "8848" in kept
       and "二二" not in kept and "Hilt" not in kept and "Sane" not in kept, repr(kept))
    ok("置信度阈值可调（60 阈值拦掉 conf=40 的行）", "钟楼" not in kept and "钟楼" in _filter_by_conf(rows_fake, 30.0))
    ok("纯数字短串（<4）丢掉、长数字留下",
       _clean_text("12\n8848\n132023") == "8848\n132023", repr(_clean_text("12\n8848\n132023")))

    # 5) 元数据/报告：降级不炸
    meta = {"file": "x.png", "ImageWidth": 100, "ImageHeight": 50, "has_gps": False}
    ocr = {"full": "五台山 体育中心", "blocks": [{"row": 0, "col": 0, "box": (0, 0, 10, 10), "text": "值班室"}],
           "engine": "tesseract"}
    geo = {"lat": 32.0578, "lon": 118.7745, "gcj": (32.0592, 118.7808), "bd09": (32.0656, 118.7872),
           "mc": (13224123.4, 3754412.9)}
    note = write_report(tmp / "n" / "r.md", pathlib.Path("x.png"), meta, ocr, [("可能的路名/店名", "五台山体育中心")],
                        geo, search_links("k"), "k")
    t = note.read_text(encoding="utf-8")
    ok("报告含元数据/OCR/线索", "没有 GPS" in t and "五台山 体育中心" in t and "值班室" in t)
    ok("报告含坐标三种口径 + 整数形式", "BD09MC" in t and "13224123" in t)
    ok("报告含人机配合与猜测预算纪律", "一句人话" in t and "30 次" in t)

    # 6) 不存在的图片：给明确错误
    ok("文件不存在时返回码 2", cmd_run(argparse.Namespace(image=str(tmp / "nope.jpg"), blocks="3x3",
                                                         lang="chi_sim+eng", geo=None, search="",
                                                         out=str(tmp / "o.md"))) == 2)

    import shutil as _sh
    _sh.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for _, c, _ in checks if c)
    for n, c, e in checks:
        print(f"  {'✓' if c else '✗'} {n}" + (f"  {e}" if e and not c else ""))
    print(f"\n{passed}/{len(checks)} 项自检通过")
    return 0 if passed == len(checks) else 1


def main(argv) -> int:
    ap = argparse.ArgumentParser(description="图片情报流水线（元数据/OCR/线索/坐标/反查）")
    ap.add_argument("image", nargs="?", help="图片路径")
    ap.add_argument("--blocks", default="3x3", help="分块 OCR 网格，如 3x3（默认）、2x2、1x3")
    ap.add_argument("--lang", default="chi_sim+eng", help="tesseract 语言包")
    ap.add_argument("--geo", nargs=2, metavar=("LAT", "LON"), help="额外做坐标换算（WGS84 输入）")
    ap.add_argument("--search", default="", help="生成图搜链接所用的关键词")
    ap.add_argument("--out", default="", help="报告输出路径（默认 notes/osint-<图名>.md）")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.image:
        ap.print_help()
        return 2
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
