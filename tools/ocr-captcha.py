#!/usr/bin/env python3
"""ocr-captcha.py -- 抓图 -> OCR -> 自动提交（验证码类题专用）。

为什么要有（2026-11-14 实测）：xben-101 是图形验证码注册题，容器有 tesseract 但缺 pytesseract，
  agent 在限时内没法自己装+写流程。本工具一次跑完整个闭环。

用法:
  python3 tools/ocr-captcha.py <url> --user testu1 --retries 6
  python3 tools/ocr-captcha.py <url> --user u --user-field username --captcha-field captcha \
      --pass-field password --pass-value p1 --csrf-field csrf_token
"""
from __future__ import annotations

import argparse
import base64
import io
import re
import subprocess
import sys


def curl(args, stdin=None):
    # ★ 2026-11-18：curl 自带 -m 12 但那只管传输；这里再加进程级 timeout 兜住
    #   DNS 卡死/子进程不退出，并显式 utf-8（Windows cp936 解码会炸）。
    p = subprocess.run(["curl", "-sS", "-m", "12"] + args, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=30)
    return p.stdout, p.stderr


def ocr(raw_bytes):
    try:
        from PIL import Image
        import pytesseract
    except ImportError:
        print("缺 pytesseract/pillow：bash /opt/pi-ctf/tools/ensure-tool.sh pytesseract pillow", file=sys.stderr)
        return ""
    best = ""
    try:
        im = Image.open(io.BytesIO(raw_bytes))
    except Exception as e:
        print("图片打不开:", e, file=sys.stderr)
        return ""
    for psm in ("7", "8", "13", "6"):
        try:
            t = pytesseract.image_to_string(im, config="--psm " + psm).strip()
        except Exception:
            continue
        t = re.sub(r"[^A-Za-z0-9]", "", t)
        if len(t) > len(best):
            best = t
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--user", default="testu1")
    ap.add_argument("--user-field", default="username")
    ap.add_argument("--pass-field", default="")
    ap.add_argument("--pass-value", default="")
    ap.add_argument("--captcha-field", default="captcha")
    ap.add_argument("--csrf-field", default="csrf_token")
    ap.add_argument("--retries", type=int, default=6)
    a = ap.parse_args()

    jar = "/tmp/ocr-cj.txt"
    for attempt in range(1, a.retries + 1):
        html, err = curl(["-c", jar, a.url])
        if not html:
            print("第 %d 次抓页面失败: %s" % (attempt, err.strip()[:80]))
            continue
        m = re.search(r'name="' + re.escape(a.csrf_field) + r'"[^>]*value="([^"]+)"', html)
        csrf = m.group(1) if m else ""
        im = re.search(r"data:image/(png|jpeg);base64,([A-Za-z0-9+/=]+)", html)
        if not im:
            print("第 %d 次: 页面里没找到内嵌验证码图片" % attempt)
            continue
        cap = ocr(base64.b64decode(im.group(2)))
        print("第 %d 次 OCR = %r" % (attempt, cap))
        if not cap:
            continue
        args = ["-b", jar, "-c", jar, "-o", "/tmp/ocr-out.html", "-w", "%{http_code}",
                "-X", "POST"]
        if csrf:
            args += ["--data-urlencode", a.csrf_field + "=" + csrf]
        args += ["--data-urlencode", a.user_field + "=" + a.user]
        args += ["--data-urlencode", a.captcha_field + "=" + cap]
        if a.pass_field and a.pass_value:
            args += ["--data-urlencode", a.pass_field + "=" + a.pass_value]
        args.append(a.url)
        code, perr = curl(args)
        body = ""
        try:
            body = open("/tmp/ocr-out.html", encoding="utf-8", errors="replace").read()
        except OSError:
            pass
        flag = re.search(r"(flag|CTF|key)[{][^}]{4,200}[}]", body)
        if flag:
            print("FOUND_FLAG=" + flag.group(0))
            return 0
        low = body.lower()
        if "captcha" not in low and ("403" not in code):
            snippet = re.sub(r"<[^>]+>", " ", body)
            snippet = re.sub(r"\s+", " ", snippet).strip()[:200]
            print("  非验证码错误 -> HTTP %s : %s" % (code, snippet))
        else:
            print("  验证码错，换图重试")
    print("重试耗尽，未拿到 flag")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())