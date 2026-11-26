#!/usr/bin/env python3
"""微信小程序攻击面工具链（本机离线实现，无外部依赖）

  wxapkg.py unpack  <file.wxapkg> [-o outdir]     # 解 wxapkg 容器（自动识别是否加密）
  wxapkg.py decrypt <file.wxapkg> --appid wx...   # PC 微信 V1MMWX 加密包解密
  wxapkg.py scan    <dir|file> [--json]           # 猎接口/密钥/越权路由/云环境
  wxapkg.py qr      <image> [--json]              # 解二维码；识别"菊花码"并给官方路线
  wxapkg.py selftest                              # 合成包自测（不发网络请求）

格式依据（三方独立来源一致，非推测）：
  wxapkg = Header(14B) + fileCount(4B) + Index + Data
    Header: 0xBE | info1(u32 BE) | indexInfoLength(u32 BE) | bodyInfoLength(u32 BE) | 0xED
    Index 项: nameLen(u32 BE) | name | offset(u32 BE, 绝对偏移) | size(u32 BE)
    校验: index 读完的位置 == filesize - bodyInfoLength，且 offset+size <= filesize
  PC 微信外层加密: "V1MMWX" + AES-256-CBC(PBKDF2(appid,"saltiest",1000,32))
                  前 1024B + (剩余 XOR appid[-2])
"""
import argparse
import hashlib
import json
import os
import re
import struct
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

MAX_INPUT = 64 * 1024 * 1024  # 本地附件上限，防超大/畸形输入把内存打爆
WXAPKG_MARKER = b"V1MMWX"


# ---------------------------------------------------------------- 安全读文件
def read_capped(path, limit=MAX_INPUT):
    """只认普通文件 + 限长，拒绝 /dev/zero、FIFO、超大文件（仓库硬规矩）"""
    if not os.path.isfile(path):
        raise ValueError(f"不是普通文件: {path}")
    if os.path.islink(path):
        raise ValueError(f"拒绝符号链接（防目录穿越）: {path}")
    size = os.path.getsize(path)
    if size == 0:
        raise ValueError(f"空文件: {path}")
    if size > limit:
        raise ValueError(f"文件过大 {size}B > {limit}B: {path}")
    with open(path, "rb") as f:
        return f.read(limit)


# ---------------------------------------------------------------- 解包
def parse_wxapkg(data):
    """解析 wxapkg，返回 [(name, offset, size), ...]；失败抛 ValueError"""
    if len(data) < 18:
        raise ValueError("文件太小，不是 wxapkg")
    if data[0] != 0xBE:
        raise ValueError(f"首标记不是 0xBE（实际 0x{data[0]:02X}）—— 可能是 PC 加密包，先跑 decrypt")
    info1, index_len, body_len = struct.unpack(">III", data[1:13])
    if data[13] != 0xED:
        raise ValueError(f"尾标记不是 0xED（实际 0x{data[13]:02X}）—— 文件损坏或版本不兼容")
    if index_len + body_len > len(data):
        raise ValueError(f"长度自相矛盾: index({index_len}) + body({body_len}) > filesize({len(data)})")

    pos = 14
    file_count = struct.unpack(">I", data[pos:pos + 4])[0]
    pos += 4
    index_end = len(data) - body_len

    if file_count > 200000:
        raise ValueError(f"文件数 {file_count} 不合理，可能是格式误判")

    files = []
    for i in range(file_count):
        if pos + 4 > index_end:
            raise ValueError(f"index 段读完前就越界（第 {i} 项）")
        name_len = struct.unpack(">I", data[pos:pos + 4])[0]
        pos += 4
        if not (0 < name_len <= 1024):
            raise ValueError(f"第 {i} 项文件名长度 {name_len} 不合理")
        name = data[pos:pos + name_len].decode("utf-8", "replace")
        pos += name_len
        offset, size = struct.unpack(">II", data[pos:pos + 8])
        pos += 8
        if offset + size > len(data):
            raise ValueError(f"{name} 结束位置 {offset + size} 超出文件长度 {len(data)}")
        files.append((name, offset, size))

    if pos != index_end:
        raise ValueError(f"index 段长度不符: 读到 {pos}，预期 {index_end}")
    return files, info1, index_len, body_len


def unpack_file(path, outdir=None, quiet=False):
    data = read_capped(path)
    if data.startswith(WXAPKG_MARKER):
        raise ValueError("这是 PC 微信加密包（V1MMWX）—— 先跑: wxapkg.py decrypt <file> --appid wxXXXX")
    files, info1, index_len, body_len = parse_wxapkg(data)
    outdir = outdir or (os.path.splitext(path)[0] + "_unpacked")
    outdir_abs = os.path.abspath(outdir)
    written = []
    for name, offset, size in files:
        # 路径穿越防护：解出来的路径必须仍在 outdir 内
        full = os.path.abspath(os.path.join(outdir_abs, name.lstrip("/")))
        if not full.startswith(outdir_abs + os.sep):
            print(f"  [!] 跳过越界路径: {name}", file=sys.stderr)
            continue
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(data[offset:offset + size])
        written.append((name, size))
    if not quiet:
        print(f"[+] 解包 {path} → {outdir}  ({len(written)} 个文件, info1={info1})")
        total = sum(s for _, s in written)
        print(f"    索引 {index_len}B / 数据 {body_len}B / 原始 {len(data)}B")
        for name, size in sorted(written, key=lambda x: -x[1])[:12]:
            print(f"    {size:>9,}  {name}")
    return outdir, written


# ---------------------------------------------------------------- PC 端解密
def decrypt_file(path, appid, outpath=None):
    """PC 微信 __APP__.wxapkg 解密：AES-256-CBC 前 1024B + 其余逐字节 XOR"""
    data = read_capped(path)
    if not data.startswith(WXAPKG_MARKER):
        if data[0] == 0xBE:
            print("[i] 本来就是明文 wxapkg，无需解密")
            return path
        raise ValueError(f"既不是 V1MMWX 也不是明文 wxapkg（首字节 0x{data[0]:02X}）")

    if not re.fullmatch(r"wx[0-9a-fA-F]{16}", appid):
        print(f"[!] appid 形状可疑: {appid}（正常是 wx + 16 位十六进制）—— 继续尝试", file=sys.stderr)

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    key = hashlib.pbkdf2_hmac("sha1", appid.encode(), b"saltiest", 1000, dklen=32)
    iv = b"the iv: 16 bytes"
    xor_key = appid.encode()[-2] if len(appid) >= 2 else 0x66

    for start in (6, 7):  # 不同版本 marker 后可能多 1 字节版本号
        body = data[start:]
        if len(body) <= 1024:
            continue
        aes_in = body[:1024]
        dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        plain = dec.update(aes_in) + dec.finalize()
        if plain[0] != 0xBE:
            continue
        # AES-CBC 段尾部有 PKCS7 填充，按填充长度切回原始 1023 字节
        pad = plain[-1]
        if 1 <= pad <= 16:
            plain = plain[:len(plain) - pad]
        rest = bytes(b ^ xor_key for b in body[1024:])
        out = plain + rest
        try:
            parse_wxapkg(out)  # 解出来必须是合法 wxapkg 才算成功
        except ValueError as e:
            print(f"[!] start={start} 解出来不是合法 wxapkg: {e}", file=sys.stderr)
            continue
        outpath = outpath or (os.path.splitext(path)[0] + ".decrypted.wxapkg")
        with open(outpath, "wb") as f:
            f.write(out)
        print(f"[+] 解密成功 (marker 偏移 {start}, xorKey=0x{xor_key:02X}) → {outpath}")
        print(f"    {len(data)}B → {len(out)}B（应少 6~7B：去掉了 V1MMWX 头）")
        return outpath
    raise ValueError("两个 marker 偏移都失败 —— appid 大概率不对（这是唯一需要 wxid 的地方）")


# ---------------------------------------------------------------- 攻击面猎取
P = {
    "appid":        (r"\bwx[0-9a-f]{16}\b", "小程序 appid"),
    "越权路由":      (r"[\w/-]*(?:By(?:UserId|Id|Uid|Phone|Username|Name|Mobile|No|Code|Openid|Account))[\w/-]*", "★ 越权高发路由名（xxxByUserId / xxxByUsername 类）"),
    "管理路由":      (r"[\"'][^\"']*(?:/admin|/manage|/system|/console|/backend|/internal)[^\"']*[\"']", "后台/内部接口"),
    "删除类接口":    (r"[\"'][^\"']*(?:delete|remove|drop|clear)[A-Za-z]*[^\"']*[\"']", "写删类接口（验证必走无害证据）"),
    "上传接口":      (r"[\"'][^\"']*(?:upload|import|transfer)[A-Za-z]*[^\"']*[\"']", "文件上传/导入"),
    "支付金额":      (r"(?:total_fee|totalFee|amount|price|money)\s*[:=]", "★ 客户端金额字段（0.01 元改价高发）"),
    "http接口":      (r"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{6,200}", "外部 URL / 接口域名"),
    "云开发env":     (r"\benv\s*[:=]\s*[\"'][A-Za-z0-9_-]{4,40}[\"']|cloud1-\d|tcb-[a-z0-9]+", "★ 云开发 env（云函数/云库越权入口）"),
    "云函数调用":    (r"callFunction\s*\(|cloud\.database\s*\(|cloudbase", "云开发 API 调用"),
    "JWT":          (r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}", "★ 硬编码 JWT（可篡改声明）"),
    "密钥候选":      (r"(?i)\b(?:app_?secret|api_?key|apikey|access_?key|secret_?key|private_?key|auth_?token)\b\s*[:=]\s*[\"'][^\"']{6,120}[\"']", "硬编码密钥"),
    "云厂商AK":      (r"\b(?:AKIA[0-9A-Z]{16}|LTAI[0-9A-Za-z]{12,}|AKID[A-Za-z0-9]{13,})\b", "★ 云厂商 AccessKey"),
    "OpenAI/LLM":   (r"\bsk-[A-Za-z0-9]{20,}", "LLM API Key"),
    "地图Key":       (r"(?:amap|gaode|qqmap|txmap|baidu)[\w.]*(?:key|ak)[\"']?\s*[:=]\s*[\"'][^\"']{8,}[\"']|AK_[A-Za-z0-9]{20,}", "地图/AK 泄露"),
    "OSS/存储桶":    (r"https?://[\w.-]+\.(?:oss-[\w-]+\.aliyuncs\.com|cos\.[\w-]+\.myqcloud\.com|bcebos\.com|amazonaws\.com)", "★ 对象存储直链（可枚举/未授权读写）"),
    "手机号":        (r"\b1[3-9]\d{9}\b", "硬编码手机号"),
    "身份证":        (r"\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b", "★ 身份证号（敏感信息死规矩类）"),
}

TEXT_EXT = {".js", ".json", ".wxml", ".wxss", ".wxs", ".ts", ".html", ".css", ".txt",
            ".xml", ".csv", ".md", ".map", ".vue", ".jsx"}
PRIORITY = ("app-service.js", "appservice.js", "app.js", "vendor.js", "config.js",
            "app.json", "project.config.json", "ext.json", "chunk_0.appservice.js")


def scan_path(root, as_json=False, max_hits=400):
    """扫解包目录（或单文件），按正则表猎取攻击面"""
    targets = []
    if os.path.isfile(root):
        targets = [root]
    else:
        for dirpath, _dirs, names in os.walk(root):
            for n in names:
                if os.path.splitext(n)[1].lower() in TEXT_EXT:
                    targets.append(os.path.join(dirpath, n))

    # 优先级文件先扫（接口/密钥基本都在这里）
    targets.sort(key=lambda p: (0 if os.path.basename(p).lower() in PRIORITY else 1, -os.path.getsize(p)))

    findings = {k: [] for k in P}
    scanned = 0
    for path in targets:
        try:
            raw = read_capped(path)
        except ValueError:
            continue
        text = raw.decode("utf-8", "replace")
        scanned += 1
        rel = os.path.relpath(path, root if os.path.isdir(root) else os.path.dirname(root))
        for key, (pat, _desc) in P.items():
            for m in re.finditer(pat, text):
                val = m.group(0)
                if len(val) > 300:
                    val = val[:300] + "…"
                findings[key].append({"file": rel, "value": val})
                if len(findings[key]) >= max_hits:
                    break

    if as_json:
        print(json.dumps({"scanned": scanned, "findings": findings}, ensure_ascii=False, indent=2))
        return findings

    print(f"[*] 扫描 {scanned} 个文本文件（按攻击面热度排序）")
    for key, (pat, desc) in P.items():
        hits = findings[key]
        if not hits:
            continue
        uniq = {}
        for h in hits:
            uniq.setdefault(h["value"], h["file"])
        print(f"\n=== {key}（{desc}）— {len(hits)} 处 / {len(uniq)} 个唯一值 ===")
        for val, f in list(uniq.items())[:15]:
            print(f"  {val}")
            print(f"       ← {f}")
        if len(uniq) > 15:
            print(f"  … 还有 {len(uniq) - 15} 个")
    print("\n[!] 命中 ≠ 漏洞。硬编码密钥要验证「能用」；写删接口走无害证据协议；"
          "越权路由要拿两个身份做 A/B 对照。")
    return findings


# ---------------------------------------------------------------- 二维码
def qr_decode(path, as_json=False):
    """解标准二维码；识别微信「菊花码」并给官方取 appid/path 的路线"""
    try:
        import cv2
        from pyzbar.pyzbar import decode as zbar_decode
    except ImportError as e:
        print(f"[!] 缺依赖（用 tools/venv/bin/python 跑）: {e}", file=sys.stderr)
        return None
    img = cv2.imread(path)
    if img is None:
        print(f"[!] 读不了图片: {path}", file=sys.stderr)
        return None
    results = []
    for r in zbar_decode(img):
        results.append({"type": r.type, "data": r.data.decode("utf-8", "replace")})
    if not results and hasattr(cv2, "QRCodeDetector"):
        data, _pts, _ = cv2.QRCodeDetector().detectAndDecode(img)
        if data:
            results.append({"type": "QRCODE(cv2)", "data": data})

    if results:
        if as_json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        else:
            print(f"[+] 解出 {len(results)} 个码：")
            for r in results:
                print(f"  [{r['type']}] {r['data']}")
        return results

    # 解不出 → 判断是不是微信「菊花码」（圆形，三主定位点 + 一个辅助定位点）
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1, minDist=min(h, w) // 4,
                               param1=100, param2=30, minRadius=int(min(h, w) * 0.03),
                               maxRadius=int(min(h, w) * 0.2))
    n_circle = 0 if circles is None else len(circles[0])
    print(f"[-] 标准二维码解码失败（图片 {w}x{h}，检测到 {n_circle} 个圆形定位点候选）")
    if n_circle >= 3:
        print("    → 特征符合微信「小程序码（菊花码）」：它是私有码制，标准解码器解不出明文。")
    else:
        print("    → 也不是典型菊花码，可能是清晰度/裁剪问题，或非二维码图片。")
    print("""
    取 appid / path 的可行路线（不需要破码制）：
      A. 微信开发者工具 → 工具栏「自定义编译条件」→「通过二维码编译」→ 选这张图
         （需 devtools 1.02.1803130+）→ 直接编译到目标页，appid+path 全部到手 ★ 推荐
      B. 微信客户端扫码打开 → PC 微信目录
         %USERPROFILE%\\Documents\\WeChat Files\\Applet\\wx<APPID>\\  ← 目录名就是 appid
         （Mac: ~/Library/Containers/com.tencent.xinWeChat/Data/.../Applet/）
      C. 页面/公众号文章里找明文 scheme：
         weixin://dl/business/?appid=xxx&path=xxx&query=xxx
      D. 真需要码内明文 → 属「已知的未知」，按 prompts/human-hint.md 找人要一句话，
         比猜 200 次划算（本仓库纪律：名字/坐标/口令猜 >30 次没命中就停手换面）
    """)
    return None


# ---------------------------------------------------------------- 自测
def selftest():
    """合成一个 wxapkg，验证 parse + unpack + 路径穿越防护（无网络）"""
    import tempfile
    body = {}
    body["app.js"] = b"App({onLaunch(){wx.request({url:'https://api.demo.cn/user/getUserInfoByUserId?id=1'})}});"
    body["config.js"] = b"module.exports={env:'cloud1-9gabcdef',appSecret:'abcdef1234567890',amapKey:'AK_TESTKEY1234567890123456'};"
    body["project.config.json"] = b'{"appid":"wx0123456789abcdef"}'
    body["pages/index/index.wxml"] = b"<view>hi</view>"
    body["../evil.js"] = b"path traversal attempt"

    names = list(body.keys())
    index_len = 4
    for n in names:
        index_len += 4 + len(n.encode()) + 8
    header = 14
    data_start = header + index_len
    offsets, cur = {}, data_start
    for n in names:
        offsets[n] = cur
        cur += len(body[n])
    body_len = cur - data_start

    out = bytearray()
    out += bytes([0xBE]) + struct.pack(">III", len(names), index_len, body_len) + bytes([0xED])
    out += struct.pack(">I", len(names))
    for n in names:
        nb = n.encode()
        out += struct.pack(">I", len(nb)) + nb + struct.pack(">II", offsets[n], len(body[n]))
    assert len(out) == data_start, f"index 长度算错: {len(out)} vs {data_start}"
    for n in names:
        out += body[n]

    with tempfile.TemporaryDirectory() as td:
        pkg = os.path.join(td, "test.wxapkg")
        with open(pkg, "wb") as f:
            f.write(bytes(out))
        print(f"[1] 合成包 {len(out)}B / {len(names)} 文件")
        files, info1, il, bl = parse_wxapkg(bytes(out))
        assert len(files) == len(names), "文件数不符"
        assert il == index_len and bl == body_len, "长度字段不符"
        print(f"[2] parse_wxapkg ✓ 索引 {il}B 数据 {bl}B")
        od, written = unpack_file(pkg, quiet=True)
        assert os.path.exists(os.path.join(od, "app.js")), "app.js 未解出"
        with open(os.path.join(od, "config.js")) as f:
            assert "cloud1-9gabcdef" in f.read(), "内容不符"
        print(f"[3] unpack_file ✓ 解出 {len(written)} 个文件")
        assert not os.path.exists(os.path.join(td, "evil.js")), "★★ 路径穿越未被拦住！"
        print("[4] 路径穿越防护 ✓ （../evil.js 已被拒绝）")
        f = scan_path(od, as_json=True, max_hits=20)
        assert any("ByUserId" in h["value"] for h in f["越权路由"]), "漏了越权路由"
        assert f["云开发env"], "漏了云环境"
        assert f["云厂商AK"] or f["地图Key"], "漏了 AK"
        print(f"[5] scan_path ✓ 越权路由 {len(f['越权路由'])} / 云env {len(f['云开发env'])} / 密钥类 {len(f['密钥候选'])}")
        print("\n[✓] selftest 全通过（5/5）")
    return 0


def main():
    ap = argparse.ArgumentParser(description="微信小程序攻击面工具链")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("unpack", help="解 wxapkg 容器")
    p.add_argument("file"); p.add_argument("-o", "--out")

    p = sub.add_parser("decrypt", help="PC 微信 V1MMWX 加密包解密")
    p.add_argument("file"); p.add_argument("--appid", required=True); p.add_argument("-o", "--out")

    p = sub.add_parser("scan", help="猎取接口/密钥/越权路由")
    p.add_argument("path"); p.add_argument("--json", action="store_true")

    p = sub.add_parser("qr", help="解二维码 / 识别菊花码")
    p.add_argument("image"); p.add_argument("--json", action="store_true")

    sub.add_parser("selftest", help="合成包自测")

    a = ap.parse_args()
    try:
        if a.cmd == "unpack":
            unpack_file(a.file, a.out)
        elif a.cmd == "decrypt":
            decrypt_file(a.file, a.appid, a.out)
        elif a.cmd == "scan":
            scan_path(a.path, a.json)
        elif a.cmd == "qr":
            qr_decode(a.image, a.json)
        elif a.cmd == "selftest":
            return selftest()
    except (ValueError, OSError) as e:
        print(f"[x] {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
