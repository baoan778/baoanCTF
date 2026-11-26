#!/usr/bin/env python3
"""authz-diff —— 鉴权差分探测器（未授权 / 水平越权 / 垂直越权）

补的是「拿到接口清单之后怎么办」这个缺口：不做组件/CVE 匹配（那是 pocbomber），
而是把**同一条接口放到不同身份下请求**，用确定性判据比对响应，输出可作无害证据的判定表。

  身份档（profile）= 一组请求头（含 Cookie）。
    anon            不带任何身份（未登录）
    sessA / sessB   两个不同账号的会话
    低权/高权        同一账号的不同角色

判据（全部确定性，不靠模型主观判断）：
  ANON_OK        未登录与已登录响应等价 → 疑似未授权访问
  ANON_DENIED    未登录被正确拒绝（401/403/未登录/登录失效） → 已防护 ✓
  CROSS_IDENT    A 身份能拿到 B 身份的数据/等价响应 → 疑似水平越权
  VERT_ESCALATE  低权身份拿到高权响应 → 疑似垂直越权
  INNER_DENY     ★ 双层响应包：外层 status 成功、内层 status/flag 表示未登录 → 解析混淆风险
  NOISE          各身份响应一致且无敏感差异

用法：
  python3 tools/authzdiff.py run --endpoints eps.txt --profile anon=anon.json \
      --profile sessA=a.json --baseline sessA [--qps 1] [--json]
  python3 tools/authzdiff.py selftest          # 本地起 mock 服务自测（不出网）

eps.txt 每行一条：
  GET  https://host/path
  POST https://host/path  {"k":"v"}
  # 井号开头是注释

安全护栏（默认开，不可静默绕过）：
  · 默认只发 GET/POST；DELETE/PUT/PATCH 需显式 --allow-writes
  · 单响应上限 256KB；请求间隔按 --qps（默认 1，最小 0.2）
  · 输入文件走 read_capped（防 /dev/zero / FIFO / 超大文件）
  · 拒绝非 http(s) scheme；不跟随跳转（--follow 才跟）
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
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

MAX_INPUT = 32 * 1024 * 1024
MAX_RESP = 256 * 1024

# 未登录/被拒 的通用特征词（中英 + 常见错误码）
DENY_PAT = re.compile(
    r"未登录|登录失效|请重新登录|请先登录|not\s*logg?ed|unauthoriz|forbidden|"
    r"login\s*required|no\s*permission|无权限|没有权限|access\s*denied|"
    r"\"errcode\"\s*:\s*(?:22032|1(?:0)?0[0-9]{2})|"
    r"\"code\"\s*:\s*(?:401|403|1001|2001)|HTTP\s*(?:401|403)",
    re.I,
)
# 内层包特征：被嵌套的身份/状态字段
INNER_PAT = re.compile(
    r"[\"'](?:qlogin|tlogin|isLogin|loggedIn)[\"']\s*:\s*(?:false|0)|\"status\"\s*:\s*\d{3}",
    re.I,
)
SENSITIVE_PAT = re.compile(
    r"\b1[3-9]\d{9}\b|[\"'](?:idCard|idNo|id_number|passportNo|realName|mobile|phone)"
    r"[\"']\s*:\s*[\"'][^\"']{2,}", re.I)

# 借鉴 PrivHunterAI：比对前剔除动态字段，否则同一数据只因 trace_id 不同就判成「不一致」（假阴性）
VOLATILE_KEYS = {
    "timestamp", "ts", "_t", "time", "servertime", "requesttime", "date",
    "requestid", "reqid", "req_id", "traceid", "trace_id", "nonce",
    "sessionid", "session_id", "uuid", "guid", "random", "rand", "sign",
    "req-id", "seq", "serialno", "serial_no", "pageToken".lower(),
}
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# 写操作「成功语义」特征
WRITE_OK_PAT = re.compile(
    r'"(?:success|succuss|ret|ok)"\s*:\s*(?:true|1)|"(?:code|status|errcode)"\s*:\s*(?:0|200)\b|操作成功|success',
    re.I,
)
AMBIG_LO, AMBIG_HI = 0.50, 0.80  # 借鉴 PrivHunterAI：相似度模糊区间 → UNKNOWN


def strip_volatile(canon):
    """从规范形里剔除动态字段（JSON 走结构化剔键，非 JSON 走正则）。

    借鉴 PrivHunterAI 的「动态字段过滤」：不做这一步会让「同一份数据」
    因为 trace_id/timestamp 不同而指纹不等，产生大量假阴性。
    """
    try:
        obj = json.loads(canon)
    except Exception:
        return re.sub(r'"[A-Za-z_]*(?:timestamp|trace|nonce|session|request_?id|req_?id|uuid|random)[A-Za-z_]*"\s*:\s*("[^"]*"|-?[0-9.]+)', '"<vol>"', canon, flags=re.I)

    def walk(o):
        if isinstance(o, dict):
            return {k: walk(v) for k, v in o.items()
                    if k.lower().replace("-", "_") not in VOLATILE_KEYS}
        if isinstance(o, list):
            return [walk(x) for x in o]
        return o

    return json.dumps(walk(obj), sort_keys=True, ensure_ascii=False)


def identity_vals(canon):
    """抽出响应里的「身份类字段」值（uid/user_id/account/phone/email…）。

    借鉴 PrivHunterAI 判定树 #2/#4：两个身份的响应都携带**同一身份值**
    （B 看到 A 的 uid）—— 即使整体响应不同，也已经是跨身份数据。
    """
    try:
        obj = json.loads(canon)
    except Exception:
        return {}
    ID_KEYS = {"uid", "userid", "user_id", "account", "accountid", "account_id",
               "phone", "mobile", "email", "openid", "memberid", "member_id",
               "customerno", "customer_no"}
    found = {}

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k.lower().replace("-", "_") in ID_KEYS and isinstance(v, (str, int)):
                    found[k.lower()] = v
                walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(obj)
    return found


def similarity(a, b):
    """两个规范形的相似度（difflib，不需要外部库）"""
    import difflib
    if not a and not b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def read_capped(path, limit=MAX_INPUT):
    """只认普通文件 + 限长（仓库硬规矩：拒 /dev/zero / FIFO / 符号链接 / 超大）"""
    if os.path.islink(path):
        raise ValueError(f"拒绝符号链接: {path}")
    if not os.path.isfile(path):
        raise ValueError(f"不是普通文件: {path}")
    size = os.path.getsize(path)
    if size > limit:
        raise ValueError(f"文件过大 {size}B > {limit}B: {path}")
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read(limit)


def load_json_file(path):
    if not path:
        return {}
    txt = read_capped(path, 1024 * 1024)
    d = json.loads(txt)
    if not isinstance(d, dict):
        raise ValueError(f"{path} 必须是 JSON 对象（header 名→值）")
    return {str(k): str(v) for k, v in d.items()}


def parse_endpoints(text):
    """解析接口清单：GET url / POST url body / #注释"""
    eps = []
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z]+)\s+(\S+)\s*(.*)$", s)
        if not m:
            raise ValueError(f"第 {i} 行格式错（应 `METHOD URL [BODY]`）: {s[:80]}")
        method, url, body = m.group(1).upper(), m.group(2), m.group(3).strip() or None
        if not re.match(r"^https?://", url, re.I):
            raise ValueError(f"第 {i} 行 scheme 非法（只允许 http/https）: {url[:80]}")
        eps.append({"method": method, "url": url, "body": body})
    return eps


def norm_body(raw_bytes, content_type=""):
    """归一化成可比对指纹，并返回**反转义**文本供判据匹配。

    注意：很多 API 返回 ensure_ascii 风格的 JSON（中文变成 \\u767b\\u5f55），
    直接在原始字节上匹配「未登录」这类词会全漏 —— 所以判据必须跑在反转义后的规范形上。
    """
    import gzip
    b = raw_bytes
    if b[:2] == b"\x1f\x8b":
        try:
            b = gzip.decompress(b)
        except Exception:
            pass
    text = b.decode("utf-8", "replace")
    try:
        obj = json.loads(text)
        canon = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    except Exception:
        canon = re.sub(r"\s+", " ", text).strip()
        canon = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), canon)
    return text, canon


def fingerprint(status, canon):
    return hashlib.sha256(canon.encode("utf-8", "replace")).hexdigest()[:16], status, len(canon)


def send(ep, headers, timeout=15, follow=False):
    req = urllib.request.Request(ep["url"], method=ep["method"])
    hdrs = {"User-Agent": "Mozilla/5.0 (authz-diff)"}
    hdrs.update(headers or {})
    body = ep["body"].encode() if ep["body"] else None
    if body is not None and "content-type" not in {k.lower() for k in hdrs}:
        hdrs["content-type"] = "application/json" if body.startswith(b"{") else "application/x-www-form-urlencoded"
    for k, v in hdrs.items():
        req.add_header(k, v)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):
            return None if not follow else super().redirect_request(*a, **kw)

    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, data=body, timeout=timeout) as r:
            raw = r.read(MAX_RESP)
            return r.status, r.headers.get("content-type", ""), raw, None
    except urllib.error.HTTPError as e:
        raw = e.read(MAX_RESP)
        return e.code, e.headers.get("content-type", "") if e.headers else "", raw, None
    except Exception as e:
        return None, "", b"", f"{type(e).__name__}: {e}"


def classify(profiles_out, method="GET"):
    """给定 {profile: (status, fp, len, canon)}，判定关系。返回 (verdict, reason)

    判据优先级（借鉴多个项目后收敛，见 docs/参考-AI挖洞-SRC专用-第五轮.md §六）：
      INNER_DENY  响应体自证未授权但仍有数据（服务端自己给出的 oracle）
      ANON_OK     未授权可访问
      WRITE_IDOR  写操作，两个身份都拿到成功语义
      CROSS_IDENT 两个已登录身份响应等价
      VERT_ESCALATE 低权与高权等价
      UNKNOWN     模糊区间 / 5xx / 乱码（借鉴 PrivHunterAI 三态）
      ANON_DENIED 已防护 ✓
      NOISE       无可判定差异
    """
    is_write = method.upper() in WRITE_METHODS
    names = list(profiles_out)
    anon = profiles_out.get("anon")
    if not names or all(v[0] is None for v in profiles_out.values()):
        return "ERROR", "全部请求失败（网络/超时）"

    def denied(o):
        return o[0] in (401, 403) or bool(DENY_PAT.search(o[3] or ""))

    def blank(o):
        t = (o[3] or "").strip()
        return t in ("", "null", "[]", "{}") or len(o[3] or "") <= 4

    def server_err(o):
        return o[0] is not None and o[0] >= 500

    # 0. 5xx / 乱码 → UNKNOWN（不再往下猜）
    if any(server_err(o) for o in profiles_out.values() if o[0]):
        return "UNKNOWN", "存在 5xx 响应 —— 无法判定（借鉴 PrivHunterAI：5xx/乱码 → unknown）"

    # 1. ★ 响应体自证：服务端自己说未登录/失效，但数据还在
    for n, o in profiles_out.items():
        t = o[3] or ""
        self_declared_noauth = bool(INNER_PAT.search(t) and DENY_PAT.search(t))
        if not self_declared_noauth:
            continue
        if o[0] == 200 and '"status":0' in t.replace(" ", ""):
            return "INNER_DENY", (f"{n}: HTTP200 外层成功、内层自证未登录/状态码 —— "
                                  f"若客户端只判外层即绕过")
        if not blank(o):
            return "INNER_DENY", (f"{n}: 响应体自证未授权（登录失效/未登录）"
                                  f"但**仍携带业务数据**（{o[2]}B）—— 最强信号")

    authed = {n: o for n, o in profiles_out.items() if n != "anon" and o[0] is not None}

    # 2. 未授权：anon 未被拒，且与某已登录身份等价
    if anon and anon[0] is not None and not denied(anon):
        for n, o in authed.items():
            if not denied(o) and anon[1] == o[1]:
                tag = "写操作型未授权！" if is_write else "疑似未授权"
                return "ANON_OK", f"anon 响应与 {n} 指纹一致（fp={anon[1]}）且未被拒 → {tag}"
        if not authed:
            return "ANON_OK", f"anon 未被拒（HTTP {anon[0]}，无拒绝特征）→ 需人工确认是否公开接口"
        for n, o in authed.items():
            if denied(o) and not denied(anon):
                return "ANON_OK", f"anon 未被拒但 {n} 被拒 → 鉴权行为不一致，疑似未授权"

    # 3. 写操作型越权：两个身份都拿到成功语义（借鉴 PrivHunterAI 快速通道 #1）
    if is_write:
        idents = [o for n, o in authed.items() if not denied(o) and not blank(o)]
        if len(idents) >= 2 and all(WRITE_OK_PAT.search(o[3] or "") for o in idents) \
           and len({o[1] for o in idents}) == 1:
            return "WRITE_IDOR", "写操作：两个身份都返回写入成功语义且响应一致 → 写操作型越权"

    # 4. 水平越权（两已登录身份等价 **或** 共享同一身份值）
    ns = list(authed)
    if len(ns) >= 2:
        for i in range(len(ns)):
            for j in range(i + 1, len(ns)):
                a, b = authed[ns[i]], authed[ns[j]]
                if denied(a) or denied(b) or blank(a) or blank(b):
                    continue
                if a[1] == b[1] and a[2] > 20:
                    return "CROSS_IDENT", f"{ns[i]} 与 {ns[j]} 响应指纹一致（fp={a[1]}）→ 疑似同一数据被两身份共享"
                iv_a, iv_b = identity_vals(a[3]), identity_vals(b[3])
                shared = {k: v for k, v in iv_a.items() if iv_b.get(k) == v}
                if shared:
                    kv = next(iter(shared.items()))
                    return "CROSS_IDENT", (f"{ns[i]} 与 {ns[j]} 响应都携带同一身份值 {kv[0]}={kv[1]}"
                                           f" → 跨身份数据（即使整体响应不同）")
                sim = similarity(a[3], b[3])
                if AMBIG_LO <= sim <= AMBIG_HI:
                    return "UNKNOWN", (f"{ns[i]} 与 {ns[j]} 相似度 {sim:.0%} 落在模糊区间"
                                       f"（{AMBIG_LO:.0%}–{AMBIG_HI:.0%}）→ unknown，需人工看")

    # 5. 垂直越权
    if "low" in authed and "high" in authed:
        lo, hi = authed["low"], authed["high"]
        if not denied(lo) and not denied(hi) and lo[1] == hi[1] and lo[2] > 20:
            return "VERT_ESCALATE", "low 与 high 响应等价 → 疑似垂直越权"

    if anon and denied(anon):
        return "ANON_DENIED", "未登录被正确拒绝（鉴权生效 ✓）"
    return "NOISE", "各身份响应无可判定差异"


def run(args):
    eps = parse_endpoints(read_capped(args.endpoints, 8 * 1024 * 1024))
    profs = {}
    for spec in args.profile:
        name, _, path = spec.partition("=")
        if not _:
            raise ValueError(f"--profile 格式应为 名字=json文件，收到 {spec}")
        profs[name] = load_json_file(path)
    if "anon" not in profs:
        profs["anon"] = {}
    if args.baseline and args.baseline not in profs:
        raise ValueError(f"--baseline {args.baseline} 不在 profile 列表里")

    interval = max(0.2, 1.0 / max(0.2, args.qps))
    rows = []
    for ep in eps:
        if ep["method"] in ("DELETE", "PUT", "PATCH") and not args.allow_writes:
            print(f"[跳过·写方法] {ep['method']} {ep['url']}", file=sys.stderr)
            continue
        out = {}
        for name, hdrs in profs.items():
            st, ct, raw, err = send(ep, hdrs, timeout=args.timeout, follow=args.follow)
            text, canon = norm_body(raw, ct)
            stable = strip_volatile(canon)   # 借鉴 PrivHunterAI：先剔动态字段再指纹
            fp, _, ln = fingerprint(st, stable)
            # 判据一律跑在反转义规范形 stable 上（text 只留作人工复核原文）
            out[name] = (st, fp, ln, stable, err)
            time.sleep(interval)
        verdict, reason = classify({n: (o[0], o[1], o[2], o[3]) for n, o in out.items()}, ep["method"])
        sens = bool(SENSITIVE_PAT.search(next((o[3] for o in out.values() if o[3]), "")))
        rows.append({"endpoint": ep, "evidence": {
            n: {"status": o[0], "fp": o[1], "len": o[2], "err": o[4]} for n, o in out.items()},
            "verdict": verdict, "reason": reason, "sensitive_data_seen": sens})

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return rows

    print(f"[*] {len(rows)} 条接口 × {len(profs)} 个身份（间隔 {interval:.2f}s）\n")
    order = {"INNER_DENY": 0, "ANON_OK": 1, "WRITE_IDOR": 2, "CROSS_IDENT": 3,
             "VERT_ESCALATE": 4, "UNKNOWN": 5, "ANON_DENIED": 6, "NOISE": 7, "ERROR": 8}
    labels = {"INNER_DENY": "★自证未授权?", "ANON_OK": "★未授权?", "WRITE_IDOR": "★写型越权?",
              "CROSS_IDENT": "★越权?", "VERT_ESCALATE": "★垂直越权?", "UNKNOWN": "?需人工",
              "ANON_DENIED": "已防护✓", "NOISE": "—", "ERROR": "!错误"}
    for r in sorted(rows, key=lambda r: order.get(r["verdict"], 9)):
        print(f"{labels[r['verdict']]:<12} {r['endpoint']['method']:<5} {r['endpoint']['url'][:88]}")
        print(f"             {r['reason']}")
        if r["sensitive_data_seen"]:
            print("             ⚠ 响应中出现证件/手机号/姓名字段 —— 命中敏感信息死规矩，立即核验")
    unk = [r for r in rows if r["verdict"] == "UNKNOWN"]
    if unk:
        print(f"\n[?] {len(unk)} 条落在 UNKNOWN（模糊区间/5xx）—— 这些必须人工看，不要当噪音丢掉")
    print("\n[!] ★ 只是疑似。定级前必须：① 确认数据归属（是不是别人的）② 无害证据（authz_diff/哨兵）"
          "\n    ③ 独立复审（references/src-reviewer.md）。公开接口（未登录首页就在调）不算漏洞。")
    return rows


def selftest():
    """本地起 mock 服务，验证 6 类判据 + 护栏（不出网）"""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _j(self, code, obj, extra=None):
            b = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            if extra:
                for k, v in extra.items():
                    self.send_header(k, v)
            self.send_header("content-length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            ck = self.headers.get("Cookie", "")
            p = self.path
            if p == "/open":
                self._j(200, {"ok": True, "data": {"city": "北京"}})
            elif p == "/volatile":
                # 每次调用 ts 都不同 —— 剔动态字段后才能认出「各身份等价」
                self._j(200, {"ok": True, "data": {"city": "北京"},
                              "ts": self.headers.get("X-N", "1")})
            elif p == "/ambig":
                if "sessA" in ck:
                    self._j(200, {"a": "x" * 40, "b": "y" * 40})
                elif "sessB" in ck:
                    self._j(200, {"a": "x" * 40, "b": "z" * 40})
                else:
                    self._j(403, {"errcode": 22032, "errmsg": "用户未登录"})
            elif p == "/idor2":
                # 响应整体不同，但两个身份都携带同一 uid=A
                if "sessA" in ck:
                    self._j(200, {"uid": "A", "scope": "self", "rows": 3})
                elif "sessB" in ck:
                    self._j(200, {"uid": "A", "scope": "other", "rows": 7})
                else:
                    self._j(403, {"errcode": 1, "errmsg": "forbidden"})
            elif p == "/err5xx":
                self._j(500, {"errmsg": "internal"})
            elif p == "/protected":
                if "sessA" in ck:
                    self._j(200, {"ok": True, "orders": ["A-1"]})
                else:
                    self._j(401, {"errcode": 22032, "errmsg": "用户未登录"})
            elif p == "/idor":
                if "sessA" in ck:
                    self._j(200, {"uid": "A", "orders": ["A-1"]})
                elif "sessB" in ck:
                    self._j(200, {"uid": "A", "orders": ["A-1"]})  # B 看到 A 的数据
                else:
                    self._j(403, {"errcode": 1, "errmsg": "forbidden"})
            elif p == "/double":
                self._j(200, {"data": {"qlogin": False, "msg": "登录失效，请重新登录", "status": 600},
                              "msg": "操作成功", "status": 0})
            elif p == "/deny":
                self._j(403, {"errmsg": "forbidden"})
            else:
                self._j(404, {"errmsg": "not found"})

        def do_POST(self):
            ck = self.headers.get("Cookie", "")
            if self.path == "/write":
                if "sessA" in ck or "sessB" in ck:
                    self._j(200, {"success": True, "code": 0, "msg": "操作成功"})
                else:
                    self._j(403, {"errcode": 22032, "errmsg": "用户未登录"})
            else:
                self._j(200, {"success": True, "code": 0, "msg": "操作成功"})

        def do_DELETE(self):
            self._j(200, {"deleted": True})

    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        eps = os.path.join(td, "eps.txt")
        with open(eps, "w") as f:
            f.write("# 自测\n")
            for r in ("open", "volatile", "ambig", "idor2", "err5xx", "protected", "idor", "double", "deny"):
                f.write(f"GET {base}/{r}\n")
            f.write(f"POST {base}/write\n")
        a = os.path.join(td, "a.json"); open(a, "w").write('{"Cookie":"sessA=x"}')
        b = os.path.join(td, "b.json"); open(b, "w").write('{"Cookie":"sessB=y"}')
        print("[1] mock 服务 :%d 起好了" % port)
        n = os.path.join(td, "n.json"); open(n, "w").write("{}")
        rows = run(argparse.Namespace(endpoints=eps,
                                     profile=[f"anon={n}", f"sessA={a}", f"sessB={b}"],
                                     baseline=None, qps=20, timeout=5, follow=False,
                                     allow_writes=False, json=True))
        got = {r["endpoint"]["url"].split("/")[-1]: r["verdict"] for r in rows}
        exp = {"open": "ANON_OK", "volatile": "ANON_OK", "ambig": "UNKNOWN", "idor2": "CROSS_IDENT",
               "err5xx": "UNKNOWN", "protected": "ANON_DENIED", "idor": "CROSS_IDENT",
               "double": "INNER_DENY", "deny": "ANON_DENIED", "write": "WRITE_IDOR"}
        ok = 0
        for k, v in exp.items():
            good = got.get(k) == v
            print(f"[2] {k:<10} 期望 {v:<12} 实际 {str(got.get(k)):<12} {'✓' if good else '✗'}")
            ok += good
        assert got.get("open") == "ANON_OK", "未授权判据失效"
        assert got.get("protected") == "ANON_DENIED", "拒绝判据失效"
        assert got.get("idor") == "CROSS_IDENT", "越权判据失效"
        assert got.get("double") == "INNER_DENY", "自证未授权判据失效"
        assert got.get("volatile") == "ANON_OK", "动态字段过滤失效（ts 不同就判不出等价）"
        assert got.get("ambig") == "UNKNOWN", "三态 UNKNOWN 失效（相似度模糊区间）"
        assert got.get("err5xx") == "UNKNOWN", "三态 UNKNOWN 失效（5xx 触发）"
        assert got.get("idor2") == "CROSS_IDENT", "身份值重合判据失效"
        assert got.get("write") == "WRITE_IDOR", "写操作型越权判据失效"

        print("[3] 护栏：DELETE 默认跳过  ", end="")
        eps2 = os.path.join(td, "eps2.txt")
        open(eps2, "w").write("DELETE %s/open\n" % base)
        r2 = run(argparse.Namespace(endpoints=eps2, profile=["anon=" + n, "sessA=" + a], baseline=None,
                                    qps=20, timeout=5, follow=False, allow_writes=False, json=True))
        assert len(r2) == 0, "写方法未被拦"
        print("✓")

        print("[4] 护栏：非 http scheme 拒绝  ", end="")
        try:
            parse_endpoints("GET file:///etc/passwd")
            raise AssertionError("file:// 未被拒")
        except ValueError:
            print("✓")

        print("[5] 护栏：/dev/zero 拒绝  ", end="")
        try:
            read_capped("/dev/zero")
            raise AssertionError("/dev/zero 未被拒")
        except ValueError:
            print("✓")

        print("[6] 敏感数据标记  ", end="")
        eps3 = os.path.join(td, "eps3.txt"); open(eps3, "w").write("GET %s/open\n" % base)
        # 直接测正则
        assert SENSITIVE_PAT.search('{"mobile":"13800138000"}'), "手机号未识别"
        assert SENSITIVE_PAT.search('{"idCard":"110101199001011234"}'), "证件字段未识别"
        print("✓")
        print(f"\n[✓] selftest 通过 {ok}/{len(exp)} 判据 + 4 项护栏")
    srv.shutdown()
    return 0


def main():
    ap = argparse.ArgumentParser(description="鉴权差分探测器（未授权/越权）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="跑差分")
    p.add_argument("--endpoints", required=True, help="接口清单文件")
    p.add_argument("--profile", action="append", default=[], help="名字=headers.json（anon 自动补空档）")
    p.add_argument("--baseline", help="基准身份名（记录用）")
    p.add_argument("--qps", type=float, default=1.0, help="每秒请求数，默认 1（最小 0.2）")
    p.add_argument("--timeout", type=int, default=15)
    p.add_argument("--follow", action="store_true", help="跟随跳转（默认不跟）")
    p.add_argument("--allow-writes", action="store_true", help="允许 DELETE/PUT/PATCH（默认拦）")
    p.add_argument("--json", action="store_true")
    sub.add_parser("selftest", help="本地 mock 自测")
    a = ap.parse_args()
    try:
        if a.cmd == "selftest":
            return selftest()
        run(a)
    except (ValueError, OSError, json.JSONDecodeError) as e:
        print(f"[x] {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
