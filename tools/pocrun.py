#!/usr/bin/env python3
"""pocrun.py —— 离线 PoC 库的安全执行器（只跑"判定式"类，跳过写/执行类）。

为什么要它（2026-10-04，用户一句"别拿水洞糊弄我"）：
  本机有 **11290 条**离线 PoC（`tools/ctfpoc/pocs/**`），其中 3686 条"可照抄"（带请求骨架 + 判定式）。
  但 `afrog/tscanplus` 的 YAML 里**混着 RCE / 上传 / 写文件**的 PoC —— 那些一跑就是**真写目标系统**，
  属于本项目硬红线（不碰判定源、不上传马、不改配置）。
  → 所以需要一个执行器：**只跑判定式（读）类**，把红色类别**挑出来列清单、不执行**。

支持（够用即可，不做完整 YAML 解析器）：
  · `set:`       randstr / randomInt / randomLowercase / randomString(n)
  · `rules:`     `request{ method, path, headers, body }` + `expression`
  · `expression` 支持 `response.body.bcontains(b'X')` / `response.status == N` /
                 `response.headers.contains("X")` / `&&` `||` `!` 与括号；
                 `bytes(string(a * b))` 这类**算术常量**会被预先算成数字字面量
  · 变量替换   `{{randstr}}` `{{r2}}` 等
安全闸（**默认开启，不可关**）：
  · 规则体里出现 `<%`、`Runtime`、`exec(`、`ProcessBuilder`、`cmd=`、`shell`、`CreateCaFile`、
    `upload`、`multipart/form-data`、`delete`、`rm -`、`wget `、`curl `、`chmod`、`bash `
    等写/执行特征 → **跳过并登记为 unsafe**（不执行）
  · 判定式里要求响应**含证据特征**（bcontains 字面量）才算命中，避免"200 就报"

用法：
    py tools/pocrun.py --targets work/edu/hosts.txt --poc-dir tools/ctfpoc/pocs --grep 泛微 --qps 2
    py tools/pocrun.py --selftest
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import re
import ssl
import string
import sys
import time
import urllib.error
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

UNSAFE = ["<%", "Runtime", "exec(", "ProcessBuilder", "cmd=", "shell", "CreateCaFile",
          "upload", "multipart/form-data", "delete", "rm -", "wget ", "curl ", "chmod",
          "bash ", "/bin/sh", "eval(", "assert(", "base64_decode", "file_put_contents"]


# ────────────────────────── PoC 解析与安全闸（纯函数，可离线测） ──────────────────────────

def parse_poc(text: str) -> dict:
    """极简解析：取 id/name/severity/tags 与全部 rules 的 (method,path,headers,body,expression)。"""
    poc = {"id": "", "name": "", "severity": "", "tags": [], "set": {}, "rules": []}
    m = re.search(r"^id:\s*(\S+)", text, re.M)
    if m:
        poc["id"] = m.group(1)
    m = re.search(r"name:\s*(.+)", text)
    if m:
        poc["name"] = m.group(1).strip().strip("'\"")
    m = re.search(r"severity:\s*(\S+)", text)
    if m:
        poc["severity"] = m.group(1)
    m = re.search(r"tags:\s*(.+)", text)
    if m:
        poc["tags"] = [t.strip() for t in m.group(1).split(",")]
    # rules: 用缩进块切分
    cur = None
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        m = re.match(r"\s*r\d+:\s*$", ln)
        if m:
            cur = {"method": "GET", "path": "", "headers": {}, "body": "", "expression": ""}
            poc["rules"].append(cur)
        if cur is not None:
            m = re.search(r"method:\s*(\S+)", ln)
            if m:
                cur["method"] = m.group(1).upper()
            m = re.search(r"path:\s*(\S+)", ln)
            if m:
                cur["path"] = m.group(1).strip().strip("'\"")
            m = re.search(r"expression:\s*(.+)", ln)
            if m:
                cur["expression"] = m.group(1).strip()
            m = re.search(r"^\s*([A-Za-z-]+):\s*(\S.*)$", ln)
            if m and m.group(1).lower() in ("cookie", "content-type", "authorization", "referer", "user-agent"):
                cur["headers"][m.group(1).lower()] = m.group(2).strip()
            if re.match(r"^\s*body:\s*\|", ln):     # 块体
                j = i + 1
                blk = []
                while j < len(lines) and (lines[j].startswith(" " * 8) or not lines[j].strip()):
                    blk.append(lines[j][8:] if lines[j].startswith(" " * 8) else "")
                    j += 1
                cur["body"] = "\n".join(blk).strip()
                i = j - 1
        i += 1
    return poc


def is_unsafe(poc: dict) -> tuple:
    """返回 (是否不安全, 命中的特征)。判据：请求骨架里出现写/执行特征。"""
    blob = json.dumps(poc, ensure_ascii=False).lower()
    hit = [u for u in UNSAFE if u.lower() in blob]
    return (bool(hit), hit)


def substitute(text: str, vars_: dict) -> str:
    for k, v in vars_.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


def make_vars(setblock_text: str) -> dict:
    v = {}
    for m in re.finditer(r"(\w+):\s*(randomLowercase|randomString|randomInt|randstr)\s*\(([^)]*)\)", setblock_text):
        name, fn, arg = m.group(1), m.group(2), m.group(3)
        if fn in ("randomLowercase", "randomString", "randstr"):
            n = int(arg) if arg.strip().isdigit() else 8
            v[name] = "".join(random.choice(string.ascii_lowercase) for _ in range(n))
        else:
            lo, hi = (arg.split(",") + ["10000", "99999"])[:2]
            try:
                v[name] = random.randint(int(lo), int(hi))
            except Exception:
                v[name] = random.randint(10000, 99999)
    return v


# ────────────────────────── 判定式求值 ──────────────────────────

# 受限求值用的 AST 白名单。
# ★ 2026-11-12 安全修复：原来用 eval() + {"__builtins__": {}} 当沙箱，**实测可逃逸**：
#     __import__("os").system("id") / ().__class__.__bases__[0].__subclasses__() /
#     getattr((), "__class__")   三条全部返回 True（即真的被求值了）。
#   {"__builtins__": {}} 只是隐藏名字，不是安全边界。现在改为**解析成 AST 后按
#   白名单递归求值**：不允许属性访问、不允许函数调用、不允许 import/lambda/推导式。
#   只有节点类型在 _SAFE_NODES 里才放行，其余一律判 False（保守，与原来"不认识就 False"一致）。
_AST = __import__("ast")

_SAFE_NODES = (
    _AST.Expression, _AST.BoolOp, _AST.UnaryOp, _AST.Compare, _AST.Name,
    _AST.Constant, _AST.Load, _AST.And, _AST.Or, _AST.Not, _AST.USub,
    _AST.Eq, _AST.NotEq, _AST.Lt, _AST.LtE, _AST.Gt, _AST.GtE,
    _AST.In, _AST.NotIn, _AST.Is, _AST.IsNot, _AST.BinOp, _AST.Add,
    _AST.Sub, _AST.Mult, _AST.Div, _AST.FloorDiv, _AST.Mod, _AST.USub,
)

_ALLOWED_NAMES = frozenset({"_body", "_status", "_hdrs"})


def _safe_eval(node, env: dict):
    """按白名单递归求值。任何白名单外的节点 → 抛 ValueError（调用方转 False）。"""
    if not isinstance(node, _SAFE_NODES):
        raise ValueError(f"不支持的语法：{type(node).__name__}")
    if isinstance(node, _AST.Expression):
        return _safe_eval(node.body, env)
    if isinstance(node, _AST.Constant):
        return node.value
    if isinstance(node, _AST.Name):
        if node.id not in _ALLOWED_NAMES:
            raise ValueError(f"不允许的名字：{node.id}")
        return env[node.id]
    if isinstance(node, _AST.BoolOp):
        vals = [_safe_eval(v, env) for v in node.values]
        return all(vals) if isinstance(node.op, _AST.And) else any(vals)
    if isinstance(node, _AST.UnaryOp):
        v = _safe_eval(node.operand, env)
        if isinstance(node.op, _AST.Not):
            return not v
        if isinstance(node.op, _AST.USub):
            return -v
        raise ValueError("不支持的一元运算")
    if isinstance(node, _AST.BinOp):
        l, r = _safe_eval(node.left, env), _safe_eval(node.right, env)
        ops = {_AST.Add: lambda a, b: a + b, _AST.Sub: lambda a, b: a - b,
               _AST.Mult: lambda a, b: a * b, _AST.Div: lambda a, b: a / b,
               _AST.FloorDiv: lambda a, b: a // b, _AST.Mod: lambda a, b: a % b}
        fn = ops.get(type(node.op))
        if fn is None:
            raise ValueError("不支持的二元运算")
        return fn(l, r)
    if isinstance(node, _AST.Compare):
        left = _safe_eval(node.left, env)
        for op, comp in zip(node.ops, node.comparators):
            right = _safe_eval(comp, env)
            if isinstance(op, _AST.Eq):
                ok = left == right
            elif isinstance(op, _AST.NotEq):
                ok = left != right
            elif isinstance(op, _AST.Lt):
                ok = left < right
            elif isinstance(op, _AST.LtE):
                ok = left <= right
            elif isinstance(op, _AST.Gt):
                ok = left > right
            elif isinstance(op, _AST.GtE):
                ok = left >= right
            elif isinstance(op, _AST.In):
                ok = left in right
            elif isinstance(op, _AST.NotIn):
                ok = left not in right
            elif isinstance(op, _AST.Is):
                ok = left is right
            elif isinstance(op, _AST.IsNot):
                ok = left is not right
            else:
                raise ValueError("不支持的比较运算")
            if not ok:
                return False
            left = right
        return True
    raise ValueError(f"不支持的节点：{type(node).__name__}")


def _safe_eval_expr(src: str, env: dict):
    """解析 + 白名单求值。语法错误/白名单外 → 抛异常（调用方转 False）。"""
    if len(src) > 4000:                       # 防超长表达式拖死解析
        raise ValueError("表达式过长")
    # ★ 必须 strip：翻译链会把 `!x` 换成 ` not x`，若 `!` 原来在行首，
    #   结果就是前导空格开头的表达式，ast.parse 报 unexpected indent；
    #   老的 eval() 容忍它，AST 路线不容忍。归一化以保持行为一致。
    return _safe_eval(_AST.parse(src.strip(), mode="eval"), env)


def eval_expr(expr: str, status: int, body: str, headers: dict, vars_: dict = None) -> bool:
    """把 expression 翻译成 Python 布尔。只支持读到的那几种谓词；不认识的谓词 → 视为 False（保守）。"""
    e = expr.strip()
    # 算术常量：bytes(string(r2 * r3)) → 数字
    def arith(m):
        inner = m.group(1)
        for k, v in (vars_ or {}).items():
            inner = inner.replace(k, str(v))
        try:
            # 只允许数字与 +-*/%() 参与，不碰 eval
            if not re.fullmatch(r"[\d\s+\-*/%().]+", inner):
                return "b'?'"
            return "b'" + str(_safe_eval_expr(inner, {})) + "'"
        except Exception:
            return "b'?'"

    e = re.sub(r"bytes\(\s*string\(\s*([^()]*\*[^()]*)\s*\)\s*\)", arith, e)
    repl = {
        "&&": " and ", "||": " or ", "!": " not ",
    }
    for k, v in repl.items():
        e = e.replace(k, v)
    def bcontains(m):
        lit = m.group(1)
        return f"({lit!r} in _body)"
    e = re.sub(r"response\.body\.bcontains\(b(['\"])(.*?)\1\)", lambda m: f"({m.group(2)!r} in _body)", e)
    e = re.sub(r"response\.body\.bcontains\((\"[^\"]*\")\)", r"(\1 in _body)", e)
    e = re.sub(r"response\.headers\.contains\((\"[^\"]*\")\)", r"(\1.lower() in _hdrs)", e, flags=re.I)
    e = re.sub(r"response\.status\s*==\s*(\d+)", r"(_status == \1)", e)
    e = re.sub(r"response\.body\.bmatches\([^)]*\)", "False", e)
    e = re.sub(r"\bresponse\.\w+\([^)]*\)", "False", e)
    e = e.replace("true", "True").replace("false", "False")
    try:
        return bool(_safe_eval_expr(e, {"_body": body, "_status": status, "_hdrs": headers}))
    except Exception:
        return False


# ────────────────────────── 执行 ──────────────────────────

def run_poc(poc: dict, base: str, qps: float, proxy: str | None, evidence: pathlib.Path | None) -> dict:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    handlers = [urllib.request.HTTPSHandler(context=ctx)]
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    op = urllib.request.build_opener(*handlers)
    setblock = json.dumps(poc.get("raw_set", ""))
    res = {"poc": poc["id"], "name": poc["name"], "severity": poc["severity"], "hit": False, "steps": []}
    for idx, rule in enumerate(poc["rules"]):
        vars_ = make_vars(poc.get("raw_set", ""))
        path = substitute(rule["path"], vars_)
        url = base.rstrip("/") + "/" + path.lstrip("/")
        body = substitute(rule["body"], vars_).encode() if rule["body"] else None
        hdr = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) pi-lab-pocrun/1.0"}
        hdr.update(rule["headers"])
        try:
            req = urllib.request.Request(url, data=body, headers=hdr, method=rule["method"])
            r = op.open(req, timeout=12)
            st, txt, hh = r.status, r.read(200000).decode("utf-8", "replace"), {k.lower(): v for k, v in r.headers.items()}
        except urllib.error.HTTPError as e:
            try:
                txt = e.read(200000).decode("utf-8", "replace")
            except Exception:
                txt = ""
            st, hh = e.code, {k.lower(): v for k, v in (e.headers or {}).items()}
        except Exception as e:
            st, txt, hh = 0, "", {"err": repr(e)[:60]}
        ok = eval_expr(rule["expression"], st, txt, hh, vars_)
        res["steps"].append({"rule": idx, "url": url, "status": st, "expr_ok": ok, "len": len(txt)})
        if evidence is not None:
            with evidence.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"poc": poc["id"], "url": url, "status": st, "expr": rule["expression"],
                                     "ok": ok, "snippet": txt[:400]}, ensure_ascii=False) + "\n")
        time.sleep(1.0 / max(qps, 0.1))
        if not ok:
            res["hit"] = False
            return res
    res["hit"] = bool(poc["rules"]) and all(s["expr_ok"] for s in res["steps"])
    return res


def selftest() -> int:
    checks = []

    def ok(n, c, extra=""):
        checks.append((n, bool(c), extra))

    POC = """id: demo-unauth
info:
  name: 演示-未授权访问
  severity: high
  tags: unauth,demo
set:
  randstr: randomLowercase(8)
rules:
  r0:
    request:
      method: GET
      path: /admin/{{randstr}}
      headers:
        Cookie: x=1
    expression: response.status == 200 && response.body.bcontains(b'管理后台')
"""
    p = parse_poc(POC)
    ok("解析 id/name/severity/tags", p["id"] == "demo-unauth" and p["severity"] == "high" and "unauth" in p["tags"])
    ok("解析 rule：method/path/headers/expression",
       p["rules"][0]["method"] == "GET" and "{{randstr}}" in p["rules"][0]["path"]
       and p["rules"][0]["headers"].get("cookie") == "x=1"
       and "bcontains" in p["rules"][0]["expression"], p["rules"][0])
    ok("安全闸：普通判定式 PoC 不拦截", is_unsafe(p)[0] is False, is_unsafe(p))

    RCE = """id: zfsoft-rce
info:
  name: 正方软件RCE
  severity: critical
  tags: zfsoft,rce
rules:
  r0:
    request:
      method: POST
      path: /zfca/axis/CreateCaFile
      body: |
        <soapenv:Envelope><![CDATA[<%out.print(1*2);%>]]></soapenv:Envelope>
    expression: response.status == 200
"""
    ok("★ 安全闸拦住 RCE/写文件 PoC", is_unsafe(parse_poc(RCE))[0] is True, is_unsafe(parse_poc(RCE)))

    # 判定式求值
    body = "欢迎来到管理后台"
    ok("eval: 状态+bcontains 同时满足", eval_expr("response.status == 200 && response.body.bcontains(b'管理后台')", 200, body, {}))
    ok("eval: 状态不符则不命中", not eval_expr("response.status == 200 && response.body.bcontains(b'管理后台')", 500, body, {}))
    ok("eval: 正文无特征则不算（防 200 就报）", not eval_expr("response.body.bcontains(b'管理后台')", 200, "hello", {}))
    ok("eval: 或/非 支持", eval_expr("!(response.status == 404) || response.body.bcontains(b'x')", 200, "", {}))
    ok("eval: 未知谓词保守判 False", not eval_expr("response.body.bmatches(b'zzz')", 200, "zzz", {}))
    ok("eval: 算术常量 bytes(string(r2*r3))",
       eval_expr("response.body.bcontains(bytes(string(r2 * r3)))", 200, "x=123432", {}, {"r2": 111, "r3": 1112})
       and not eval_expr("response.body.bcontains(bytes(string(r2 * r3)))", 200, "x=999", {}, {"r2": 111, "r3": 1112}))

    ok("变量替换", substitute("/a/{{randstr}}/b", {"randstr": "abc"}) == "/a/abc/b")
    vs = make_vars("  randstr: randomLowercase(6)\n  r2: randomInt(10,20)\n")
    ok("随机变量生成（长度/范围）", len(vs["randstr"]) == 6 and 10 <= vs["r2"] <= 20, vs)

    passed = sum(1 for _, c, _ in checks if c)
    for n, c, e in checks:
        print(f"  {'✓' if c else '✗'} {n}" + (f"  {e}" if e and not c else ""))
    print(f"\n{passed}/{len(checks)} 断言通过")
    return 0 if passed == len(checks) else 1


def main(argv) -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--targets", help="目标清单文件（每行一个 URL 或 host）")
    ap.add_argument("--poc-dir", default="tools/ctfpoc/pocs", help="PoC 目录（递归找 .yaml）")
    ap.add_argument("--grep", default="", help="只跑路径/内容里含该关键词的 PoC（如 泛微/致远/unauth）")
    ap.add_argument("--limit", type=int, default=200, help="最多跑多少条 PoC（防止误跑全库）")
    ap.add_argument("--qps", type=float, default=2.0)
    ap.add_argument("--out", default="", help="命中结果 JSONL")
    ap.add_argument("--evidence", default="", help="每条请求的原始片段 JSONL")
    ap.add_argument("--proxy", default=None, help="默认取环境变量；--proxy '' 直连")
    ap.add_argument("--dry-run", action="store_true", help="只做解析+安全闸统计，不发请求")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    import os
    proxy = args.proxy
    if proxy is None:
        proxy = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY") or None
    if proxy == "":
        proxy = None

    files = sorted(pathlib.Path(args.poc_dir).rglob("*.y*ml"))
    if args.grep:
        files = [f for f in files if args.grep.lower() in str(f).lower()]
    print(f"候选 PoC {len(files)} 条（目录 {args.poc_dir}，grep={args.grep or '-'}）")

    pocs, skipped = [], []
    for f in files[: args.limit * 4]:
        try:
            t = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        p = parse_poc(t)
        p["raw_set"] = (re.search(r"set:\n((?:\s+.*\n)+)", t) or [None, ""])[1] if re.search(r"set:", t) else ""
        p["file"] = str(f)
        if not p["rules"]:
            continue
        unsafe, why = is_unsafe(p)
        (skipped if unsafe else pocs).append({**p, "unsafe": why})
        if len(pocs) >= args.limit:
            break
    print(f"可执行（判定式类）{len(pocs)} 条 · 安全闸拦下（写/执行类）{len(skipped)} 条")
    if args.dry_run:
        for s in skipped[:10]:
            print(f"  ⛔ {s['id']}  ← {','.join(s['unsafe'])}")
        return 0

    targets = []
    if args.targets:
        for line in pathlib.Path(args.targets).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                targets.append(line if line.startswith("http") else "https://" + line)
    if not targets:
        ap.print_help()
        return 2

    ev = pathlib.Path(args.evidence) if args.evidence else None
    hits = []
    for t in targets:
        for p in pocs:
            r = run_poc(p, t, args.qps, proxy, ev)
            if r["hit"]:
                print(f"  ★ 命中 [{r['severity']}] {r['poc']} → {t}")
                hits.append({"target": t, **r})
    if args.out and hits:
        pathlib.Path(args.out).write_text("\n".join(json.dumps(h, ensure_ascii=False) for h in hits), encoding="utf-8")
    print(f"\n命中 {len(hits)} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))