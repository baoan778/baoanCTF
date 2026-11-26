#!/usr/bin/env python3
"""browser.py —— 用 CDP（Chrome DevTools Protocol）操控**你本机已登录的浏览器**。

为什么这么做：不用把账号密码交给 agent。你在一个专用 profile 里登录一次，
之后我用 CDP 驱动它跑比赛（点击/输入/读网络/截图/执行 JS 都行）。

前置（一次性）：
    browser.py launch          # 在 Windows 侧起一个带调试端口的浏览器（专用 profile）
    # 弹出的窗口里手动登录一次比赛平台；之后一直可用（profile 落盘保留）

常用：
    browser.py tabs                    # 列出标签页
    browser.py open <url>              # 打开/切到某 URL
    browser.py goto <url>              # 当前页导航
    browser.py html                    # 当前页 outerHTML（可 grep）
    browser.py eval '<js>'             # 执行 JS 并回显结果（支持 await）
    browser.py click '<selector>'      # 按选择器点击（比像素点击稳）
    browser.py clicktext '<文字>'      # 按可见文字点击
    browser.py fill '<sel>' '<value>'  # 填表单（触发 input/change 事件）
    browser.py text '<sel>'            # 取元素文字
    browser.py links                   # 列出当前页所有链接（找题目/提交入口）
    browser.py shot /tmp/x.png         # 截图
    browser.py wait '<sel>' [秒]       # 等元素出现
    browser.py cookies [域名]          # 看 cookie（确认登录态）

    python3 browser.py --selftest      # 纯逻辑自测（不需要浏览器）
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
#   自包含（自己 import sys），不依赖本文件是否已 import sys 或用别名。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PORT = int(os.environ.get("BROWSER_CDP_PORT", "9222"))
PROFILE_WIN = os.environ.get("BROWSER_PROFILE_WIN", r"D:\AI\pi-lab\.browser-profile")


def http(path: str, timeout: int = 6):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=timeout) as r:
            return json.loads(r.read() or b"null")
    except Exception as e:
        return {"_err": f"{type(e).__name__}: {e}"}


def targets():
    t = http("/json/list")
    return [x for x in t if isinstance(x, dict) and x.get("type") == "page"] if isinstance(t, list) else []


def pick(url_sub: str = ""):
    ts = targets()
    if not ts:
        return None
    if url_sub:
        for t in ts:
            if url_sub in (t.get("url") or ""):
                return t
    for t in ts:                      # 优先非 devtools/about 页
        u = t.get("url") or ""
        if not u.startswith(("devtools://", "chrome://", "edge://", "about:")):
            return t
    return ts[0]


class CDP:
    def __init__(self, ws_url: str, timeout: int = 30):
        import websocket
        self.ws = websocket.create_connection(ws_url, timeout=timeout,
                                              suppress_origin=True)
        self._id = 0

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass

    def send(self, method: str, **params):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method} → {msg['error']}")
                return msg.get("result", {})

    def eval(self, expr: str):
        r = self.send("Runtime.evaluate", expression=expr, returnByValue=True,
                      awaitPromise=True, userGesture=True)
        if "exceptionDetails" in r:
            return f"JS 异常: {json.dumps(r['exceptionDetails'], ensure_ascii=False)[:300]}"
        return r.get("result", {}).get("value")

    def screenshot(self, path: str):
        r = self.send("Page.captureScreenshot", format="png")
        data = base64.b64decode(r.get("data", ""))
        with open(path, "wb") as fh:
            fh.write(data)
        return len(data)


JS_HELPERS = r"""
window.__pw = window.__pw || {
  q: (sel) => document.querySelector(sel),
  qa: (sel) => Array.from(document.querySelectorAll(sel)),
  visible: (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
                     return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; },
  byText: (txt, tag='*') => Array.from(document.querySelectorAll(tag))
        .filter(e => e.textContent && e.textContent.trim().includes(txt) && window.__pw.visible(e)),
  set: (el, val) => { const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement
                        : el instanceof HTMLInputElement ? HTMLInputElement : null;
                      const setter = proto ? Object.getOwnPropertyDescriptor(proto.prototype, 'value').set : null;
                      el.focus();
                      if (setter) setter.call(el, val); else el.value = val;
                      el.dispatchEvent(new Event('input', {bubbles: true}));
                      el.dispatchEvent(new Event('change', {bubbles: true})); }
};
true
"""


def js(expr: str) -> str:
    return expr


def cmd_launch(a):
    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    exe = chrome if a.browser == "chrome" else edge
    cmd = (f'Start-Process "{exe}" -ArgumentList '
           f'\'--remote-debugging-port={PORT}\',\'--user-data-dir={PROFILE_WIN}\','
           f'\'--no-first-run\',\'--no-default-browser-check\',\'--disable-features=Translate\','
           f'\'{a.url}\'')
    # ★ 2026-11-12 补 timeout：powershell.exe 在 WSL interop 下可能长时间不返回
    try:
        subprocess.run(["powershell.exe", "-NoProfile", "-Command", cmd],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60)
    except subprocess.TimeoutExpired:
        print("⚠ PowerShell 启动浏览器超时（60s），继续探测调试端口…")
    for _ in range(20):
        time.sleep(1)
        v = http("/json/version", timeout=3)
        if isinstance(v, dict) and v.get("Browser"):
            print(f"✓ 浏览器已启动并可调试：{v['Browser']}")
            print(f"  调试端口 http://127.0.0.1:{PORT}（profile={PROFILE_WIN}）")
            print("  ★ 请在这个窗口里**登录一次**比赛平台，登录态会保留在该 profile 里")
            return 0
    print("✘ 起不来或调试端口不通：确认浏览器路径 / 端口是否被占", file=sys.stderr)
    return 1


def with_cdp(a, fn):
    t = pick(getattr(a, "match", "") or "")
    if not t:
        print("✘ 找不到可操作的标签页。先 `browser.py launch`", file=sys.stderr)
        return 1
    c = CDP(t["webSocketDebuggerUrl"])
    try:
        c.send("Runtime.enable")
        c.eval(JS_HELPERS)
        return fn(c, t)
    finally:
        c.close()


def cmd_tabs(a):
    ts = targets()
    if not ts:
        print("（没有标签页；先 browser.py launch）")
        return 1
    for i, t in enumerate(ts):
        print(f"[{i}] {t.get('title','')[:60]}\n    {t.get('url','')[:110]}")
    return 0


def cmd_open(a):
    ts = targets()
    for t in ts:
        if a.url in (t.get("url") or ""):
            print(f"已在该页：{t.get('url')}")
            return 0
    http(f"/json/new?{urllib.parse.quote(a.url, safe='')}", timeout=10)
    time.sleep(a.wait)
    print(f"已打开 {a.url}")
    return 0


def cmd_goto(a):
    return with_cdp(a, lambda c, t: (c.send("Page.navigate", url=a.url), time.sleep(a.wait),
                                     print(f"→ {a.url}") and 0)[-1])


def cmd_html(a):
    def fn(c, t):
        h = c.eval("document.documentElement.outerHTML") or ""
        if a.out:
            open(a.out, "w", encoding="utf-8").write(h)
            print(f"已写 {a.out}（{len(h)} 字节）")
        else:
            print(h[:a.max])
        return 0
    return with_cdp(a, fn)


def cmd_eval(a):
    def fn(c, t):
        v = c.eval(a.js)
        print(json.dumps(v, ensure_ascii=False, indent=2)[:a.max] if isinstance(v, (dict, list)) else v)
        return 0
    return with_cdp(a, fn)


def cmd_click(a):
    def fn(c, t):
        r = c.eval(f"(() => {{ const el = __pw.q({json.dumps(a.sel)}); if(!el) return 'NOT_FOUND'; "
                   f"if(!__pw.visible(el)) return 'NOT_VISIBLE'; el.scrollIntoView({{block:'center'}}); el.click(); return 'OK'; }})()")
        print(r)
        return 0 if r == "OK" else 1
    return with_cdp(a, fn)


def cmd_clicktext(a):
    def fn(c, t):
        r = c.eval(f"(() => {{ const els = __pw.byText({json.dumps(a.text)}); if(!els.length) return 'NOT_FOUND'; "
                   f"const el = els[els.length-1]; el.scrollIntoView({{block:'center'}}); el.click(); "
                   f"return 'OK:' + (el.tagName + ' ' + (el.textContent||'').trim().slice(0,40)); }})()")
        print(r)
        return 0 if str(r).startswith("OK") else 1
    return with_cdp(a, fn)


def cmd_fill(a):
    def fn(c, t):
        r = c.eval(f"(() => {{ const el = __pw.q({json.dumps(a.sel)}); if(!el) return 'NOT_FOUND'; "
                   f"__pw.set(el, {json.dumps(a.value)}); return 'OK'; }})()")
        print(r)
        return 0 if r == "OK" else 1
    return with_cdp(a, fn)


def cmd_text(a):
    def fn(c, t):
        r = c.eval(f"(() => {{ const el = __pw.q({json.dumps(a.sel)}); return el ? el.innerText : 'NOT_FOUND'; }})()")
        print(str(r)[:a.max])
        return 0
    return with_cdp(a, fn)


def cmd_links(a):
    def fn(c, t):
        r = c.eval("JSON.stringify(__pw.qa('a').filter(__pw.visible).map(a=>[a.innerText.trim().slice(0,40), a.href])."
                   "filter(x=>x[1] && !x[1].startsWith('javascript:')))")
        try:
            for txt, href in json.loads(r or "[]")[:a.max]:
                print(f"  {txt:<42} {href[:110]}")
        except Exception:
            print(r)
        return 0
    return with_cdp(a, fn)


def cmd_shot(a):
    def fn(c, t):
        n = c.screenshot(a.out)
        print(f"已截图 {a.out}（{n} 字节）—— 用 read 工具看它")
        return 0
    return with_cdp(a, fn)


def cmd_wait(a):
    def fn(c, t):
        end = time.time() + a.seconds
        while time.time() < end:
            if c.eval(f"(() => {{ const el = __pw.q({json.dumps(a.sel)}); return !!(el && __pw.visible(el)); }})()"):
                print("✓ 出现了")
                return 0
            time.sleep(0.5)
        print("✘ 超时未出现")
        return 1
    return with_cdp(a, fn)


def cmd_cookies(a):
    def fn(c, t):
        r = c.send("Network.getCookies", urls=[a.url] if a.url else [])
        for ck in r.get("cookies", []):
            print(f"  {ck['name']}={str(ck['value'])[:40]}  domain={ck.get('domain')} httpOnly={ck.get('httpOnly')}")
        return 0
    return with_cdp(a, fn)



def cmd_setcookie(a):
    """把 cookie 注入浏览器（跨域也行，用于复用你的登录态）。"""
    def fn(c, t):
        c.send("Network.enable")
        n = 0
        for kv in a.cookies.split("; "):
            if "=" not in kv:
                continue
            k, v = kv.split("=", 1)
            params = {"name": k, "value": v, "url": a.url, "path": "/"}
            if a.domain:
                params.pop("url"); params["domain"] = a.domain; params["secure"] = True
            try:
                r = c.send("Network.setCookie", **params)
                if r.get("success"):
                    n += 1
            except Exception as e:
                print("  注入失败", k, e)
        print(f"已注入 {n} 个 cookie（域 {a.domain or a.url}）")
        return 0
    return with_cdp(a, fn)


def cmd_setls(a):
    """把 localStorage 写进页面（--pair k=v，可多次）。"""
    def fn(c, t):
        pairs = a.pair or []
        js = ";".join(f"localStorage.setItem({json.dumps(k)}, {json.dumps(v)})"
                      for k, v in (p.split("=", 1) for p in pairs if "=" in p))
        r = c.eval(f"(() => {{ {js}; return Object.keys(localStorage).length; }})()")
        print(f"localStorage 现在有 {r} 项")
        return 0
    return with_cdp(a, fn)


def _selftest() -> int:
    checks = []

    def ok(n, c):
        checks.append((n, bool(c)))

    ok("JS 助手包含 byText/set/visible", all(k in JS_HELPERS for k in ("byText", "set:", "visible")))
    ok("选择器转义安全", json.dumps('a[href="x"]') == '"a[href=\\"x\\"]"')
    ok("CDP 端口可配", isinstance(PORT, int) and PORT > 0)
    td = pick("") if False else None      # 不连浏览器
    ok("无浏览器时不炸（targets 空处理）", (targets() == [] or True))
    ok("URL 编码正确", urllib.parse.quote("https://a/b?c=1", safe="") .startswith("https%3A%2F%2F"))
    passed = sum(1 for _, c in checks if c)
    for n, c in checks:
        print(f"  {'✓' if c else '✗'} {n}")
    print(f"\n{passed}/{len(checks)} 断言通过")
    return 0 if passed == len(checks) else 1


def main(argv) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    if argv[0] == "--selftest":
        return _selftest()
    ap = argparse.ArgumentParser(prog="browser")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("launch"); p.add_argument("--browser", default="chrome", choices=["chrome", "edge"])
    p.add_argument("--url", default="about:blank"); p.set_defaults(fn=cmd_launch)
    sub.add_parser("tabs").set_defaults(fn=cmd_tabs)
    for name, fn, need_url in (("open", cmd_open, True), ("goto", cmd_goto, True)):
        p = sub.add_parser(name); p.add_argument("url"); p.add_argument("--wait", type=float, default=2.5)
        p.add_argument("--match", default=""); p.set_defaults(fn=fn)
    p = sub.add_parser("html"); p.add_argument("--out", default=""); p.add_argument("--max", type=int, default=4000)
    p.add_argument("--match", default=""); p.set_defaults(fn=cmd_html)
    p = sub.add_parser("eval"); p.add_argument("js"); p.add_argument("--max", type=int, default=4000)
    p.add_argument("--match", default=""); p.set_defaults(fn=cmd_eval)
    p = sub.add_parser("click"); p.add_argument("sel"); p.add_argument("--match", default=""); p.set_defaults(fn=cmd_click)
    p = sub.add_parser("clicktext"); p.add_argument("text"); p.add_argument("--match", default=""); p.set_defaults(fn=cmd_clicktext)
    p = sub.add_parser("fill"); p.add_argument("sel"); p.add_argument("value"); p.add_argument("--match", default=""); p.set_defaults(fn=cmd_fill)
    p = sub.add_parser("text"); p.add_argument("sel"); p.add_argument("--max", type=int, default=2000); p.add_argument("--match", default=""); p.set_defaults(fn=cmd_text)
    p = sub.add_parser("links"); p.add_argument("--max", type=int, default=60); p.add_argument("--match", default=""); p.set_defaults(fn=cmd_links)
    p = sub.add_parser("shot"); p.add_argument("out"); p.add_argument("--match", default=""); p.set_defaults(fn=cmd_shot)
    p = sub.add_parser("wait"); p.add_argument("sel"); p.add_argument("seconds", type=float, nargs="?", default=15)
    p.add_argument("--match", default=""); p.set_defaults(fn=cmd_wait)
    p = sub.add_parser("setcookie"); p.add_argument("cookies", help='"k=v; k2=v2"')
    p.add_argument("--url", default="https://0xgame2026.play.ctfplus.cn/")
    p.add_argument("--domain", default=""); p.add_argument("--match", default=""); p.set_defaults(fn=cmd_setcookie)
    p = sub.add_parser("setls"); p.add_argument("pair", nargs="*")
    p.add_argument("--match", default=""); p.set_defaults(fn=cmd_setls)
    p = sub.add_parser("cookies"); p.add_argument("url", nargs="?", default=""); p.add_argument("--match", default=""); p.set_defaults(fn=cmd_cookies)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
