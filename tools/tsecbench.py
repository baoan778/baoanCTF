#!/usr/bin/env python3
"""TSecBench 跑分平台接入客户端（CHALLENGES_API.md 的落地实现）。

设计要点
- 只用标准库（urllib），不引第三方依赖 —— 评测环境要能裸跑。
- 配置来源（按优先级）：
    1. 环境变量  TSEC_BASE_URL / BENCHMARK_BASE_URL、TSEC_TOKEN / BENCHMARK_TOKEN
    2. 配置文件  $PI_LAB_TSEC_CONFIG，否则依次找
                 ~/.config/pi-lab/tsecbench.json、<repo>/.pi/tsecbench.json
       形如 {"base_url": "https://x", "token": "uuid", "vpn_file": "/path/x.ovpn"}
- 读任何外部文件都走 read_capped()（只认普通文件 + 限长），避免 FIFO/超大文件把 VM 打爆
  （2026-09-29 血教训，见 AGENTS.md「移植外部经验的验收清单」）。
- 平台错误码直接翻译成人话，别让人对着 code 猜。

用法
    tsec config                     看当前生效配置（token 打码）
    tsec list                       题目列表 + 进度
    tsec start <unique_code>        启动题目容器，打印直连地址
    tsec addr <unique_code>         拿地址（未启动自动 start；pending 会轮询）
    tsec hint <unique_code>         取提示（会扣分！）
    tsec submit <unique_code> <flag>
    tsec close <unique_code>
    tsec vpn up|status|down [--file x.ovpn]
    tsec healthcheck                走 VPN 里的 http://10.0.100.58 探活（SDK 用的就是它）
    tsec run-new [--set 4] [--agent N]
                                    用平台 UI token 新建跑分任务 → 拿 BENCHMARK_TOKEN+ovpn
    tsec run-status [run_id]        看当前/指定跑分任务状态（含剩余时间）
    tsec sets                       列可用试题集（要 platform_token）
    tsec plat-token [--token X]     取平台 UI 的 JWT（platform_token）→ 判活 → 写进配置
                                    不给 --token 就自动扫本机浏览器 profile
    tsec --selftest                 本地 mock server 跑一遍全流程
"""

from __future__ import annotations

import base64
import glob
import json
import os
import re
import shutil
import subprocess
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

MAX_CFG_BYTES = 64 * 1024
DEFAULT_TIMEOUT = 20.0

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def config_candidates() -> list[str]:
    """配置候选（每次调用现算 —— 环境变量 PI_LAB_TSEC_CONFIG 后设也认）。"""
    return [
        os.environ.get("PI_LAB_TSEC_CONFIG", ""),
        os.path.expanduser("~/.config/pi-lab/tsecbench.json"),
        os.path.join(REPO, ".pi", "tsecbench.json"),
    ]

ERR_HINT = {
    "task_not_found": "BENCHMARK_TOKEN 无效/缺失，或任务不存在 —— 去平台核对 token（注意 uuid 别带空格）",
    "challenge_not_found": "unique_code 不在本任务用例集里 —— 先 `tsec list` 抄准确的值",
    "invalid_state": "任务已结束（超时/被停），或活跃实例数到上限（最多 3，先 close 一个）",
    "duplicate": "这个 flag 之前已经提交成功过了，不再加分（幂等保护）",
    "resource_unavailable": "靶场资源没起好/已耗尽，等一会重试或找平台",
    "internal_error": "平台内部错误，重试；持续失败就报平台",
}


# ───────────────────────── 基础设施 ─────────────────────────

def read_capped(path: str, limit: int = MAX_CFG_BYTES) -> bytes:
    """只读普通文件、限长。FIFO/字符设备/超大文件一律拒绝。"""
    if not path:
        raise ValueError("empty path")
    st = os.stat(path, follow_symlinks=True)  # FIFO/设备在这里就能被挡住
    import stat as _stat
    if not _stat.S_ISREG(st.st_mode):
        raise ValueError(f"不是普通文件：{path}")
    if st.st_size > limit:
        raise ValueError(f"文件过大（{st.st_size} > {limit}）：{path}")
    with open(path, "rb") as fh:
        return fh.read(limit)


def die(msg: str, code: int = 2):
    print(f"✗ {msg}", file=sys.stderr)
    sys.exit(code)


class Config:
    def __init__(self, base_url: str = "", token: str = "", vpn_file: str = "", source: str = "",
                 platform_token: str = "", agent_id: int = 0, set_id: str = "4"):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.vpn_file = vpn_file
        self.source = source
        self.platform_token = platform_token      # 平台 UI 的 OAuth JWT（本地存凭证）
        self.agent_id = agent_id
        self.set_id = set_id

    @property
    def ok(self) -> bool:
        return bool(self.base_url and self.token)

    @classmethod
    def load(cls) -> "Config":
        base = os.environ.get("TSEC_BASE_URL") or os.environ.get("BENCHMARK_BASE_URL") or ""
        tok = os.environ.get("TSEC_TOKEN") or os.environ.get("BENCHMARK_TOKEN") or ""
        vpn = os.environ.get("TSEC_VPN_FILE", "")
        src = "env" if (base or tok) else ""
        cfgfile = ""
        for cand in config_candidates():
            if cand and os.path.exists(cand):
                cfgfile = cand
                break
        if cfgfile:
            try:
                cfg = json.loads(read_capped(cfgfile).decode("utf-8", "replace"))
                base = base or str(cfg.get("base_url", ""))
                tok = tok or str(cfg.get("token", ""))
                vpn = vpn or str(cfg.get("vpn_file", ""))
                src = src or cfgfile
                pt = cfg.get("platform_token", "")
                agent = int(cfg.get("agent_id") or 0)
                sid = str(cfg.get("set_id") or "4")
            except Exception as exc:  # noqa: BLE001
                print(f"⚠️ 配置文件读不了（{cfgfile}）：{exc}", file=sys.stderr)
                pt, agent, sid = "", 0, "4"
        else:
            pt, agent, sid = "", 0, "4"
        return cls(base, tok, vpn, src, pt, agent, sid)

    def save(self, path: str = "") -> str:
        """写回配置文件（chmod 600；只往 ~/.config 写，绝不写进仓库）。"""
        path = path or config_candidates()[0] or config_candidates()[1]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {"base_url": self.base_url, "token": self.token, "vpn_file": self.vpn_file,
                "platform_token": self.platform_token, "agent_id": self.agent_id,
                "set_id": self.set_id}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.chmod(path, 0o600)
        return path

    def masked(self) -> str:
        t = self.token
        return f"{t[:8]}…{t[-4:]}" if len(t) > 14 else ("(空)" if not t else "***")


# ───────────────────────── HTTP ─────────────────────────

class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, raw: str = ""):
        super().__init__(f"[{status}] {code}: {message}")
        self.status, self.code, self.message, self.raw = status, code, message, raw


def _request(cfg: Config, method: str, path: str, body: dict | None = None,
             query: dict | None = None, timeout: float = DEFAULT_TIMEOUT) -> object:
    if not cfg.ok:
        die("缺 base_url / token —— 先 `tsec config` 看现状，再设环境变量或用配置文件")
    url = cfg.base_url + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = None
    headers = {"BENCHMARK_TOKEN": cfg.token, "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _urlopen(req, timeout) as resp:
            payload = resp.read(2 * 1024 * 1024)
            return json.loads(payload.decode("utf-8", "replace")) if payload else None
    except urllib.error.HTTPError as exc:
        raw = exc.read(256 * 1024).decode("utf-8", "replace")
        code, msg = "", ""
        try:
            j = json.loads(raw)
            if isinstance(j, dict) and "code" in j:
                code, msg = str(j.get("code")), str(j.get("message", ""))
            elif isinstance(j, dict) and "detail" in j:      # FastAPI 422 框架级
                code, msg = "validation_error", json.dumps(j["detail"], ensure_ascii=False)[:400]
        except Exception:  # noqa: BLE001
            pass
        raise ApiError(exc.code, code or f"http_{exc.code}", msg or raw[:200], raw) from None
    except urllib.error.URLError as exc:
        raise ApiError(0, "network_error", f"连不上 {cfg.base_url}（{exc.reason}）—— VPN 起了吗？") from None



# ───────────────── 平台 UI 登录态（platform_token）─────────────────
#
# 为什么要这一段：`run-new` 打的是平台自身的 /api/v1（Bearer JWT），
# 而 BENCHMARK_TOKEN 只对 /openapi/v1 有效。JWT 只能来自浏览器登录态：
# 平台是腾讯众测 OAuth（auth.zc.tencent.com），没有「用户名密码换 token」的接口，
# 所以只能从**已经登录过的浏览器本地存储**里取，取到后验签再落盘。
#
# 三个来源，按可靠性排序：
#   1. local  —— 浏览器 profile 的 Local Storage leveldb 里直接扫（不用起浏览器）
#   2. env    —— TSEC_PLATFORM_TOKEN（CI/临时用）
#   3. 命令行 —— tsec plat-token --token '<JWT>'
#
# ★ 2026-11-19 实测：Edge/Chrome 的 leveldb 里键是
#   `_https://tsecbench.zc.tencent.com\x00\x01token`，值就是 JWT，
#   所以「不用人再点一次登录」是可行的。
#
# 安全边界：只读本机浏览器 profile，只取 tsecbench 这一个站点的 token；
# 取到写 ~/.config/pi-lab/tsecbench.json（0600），绝不写进仓库。

JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")
TSEC_HOST = "tsecbench.zc.tencent.com"

# 浏览器 profile 里 Local Storage 的可能位置（Windows 三种常见 + Linux）
_LS_GLOBS = [
    r"%LOCALAPPDATA%\Google\Chrome\User Data\*\Local Storage\leveldb",
    r"%LOCALAPPDATA%\Microsoft\Edge\User Data\*\Local Storage\leveldb",
    r"%USERPROFILE%\.config\google-chrome\*\Local Storage\leveldb",
]


def _ls_dirs() -> list[str]:
    """枚举所有可能含 tsecbench 登录态的 Local Storage 目录（不存在就跳过）。"""
    out: list[str] = []
    for pat in _LS_GLOBS:
        pat = os.path.expandvars(os.path.expanduser(pat))
        if "%" in pat or pat.startswith("~"):
            continue                              # 环境变量没展开 = 这平台没有
        out.extend(sorted(glob.glob(pat)))
    seen, uniq = set(), []
    for d in out:
        if os.path.isdir(d) and d not in seen:
            seen.add(d)
            uniq.append(d)
    return uniq


def _jwt_payload(tok: str) -> dict:
    """解 JWT 的 payload（不验签 —— 只用来判过期与看 sub）。"""
    try:
        p = tok.split(".")[1]
        p += "=" * (-len(p) % 4)
        d = json.loads(base64.urlsafe_b64decode(p))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _jwt_alive(tok: str) -> tuple[bool, str]:
    """(还活着?, 人话说明)。平台 JWT 带 exp；没 exp 的当活。"""
    d = _jwt_payload(tok)
    exp = d.get("exp")
    if not isinstance(exp, (int, float)):
        return True, "没有 exp 字段（当活）"
    left = int(exp - time.time())
    if left <= 0:
        return False, "已过期 " + str((-left) // 3600) + "h"
    return True, "还剩 " + str(left // 3600) + "h" + str((left % 3600) // 60) + "m"


def _scan_leveldb(dir_: str, host: str = TSEC_HOST) -> list[str]:
    """在一个 Local Storage leveldb 目录里找 host 的 token。

    leveldb 的键长这样（实测 Edge/Chrome 一致）：
        _https://tsecbench.zc.tencent.com\x00\x01token   → value = JWT
    .ldb 是 sstable，.log 是刚写入还没落盘的（更可能是最新的）。
    """
    blob = b""
    try:
        names = sorted(os.listdir(dir_))
    except OSError:
        return []
    for name in names:
        if not (name.endswith(".ldb") or name.endswith(".log")):
            continue
        fp = os.path.join(dir_, name)
        try:
            if os.path.getsize(fp) > 64 * 1024 * 1024:      # 大文件跳过，防打爆内存
                continue
            with open(fp, "rb") as fh:
                blob += fh.read() + b"\n"
        except OSError:
            continue
    if host.encode() not in blob:
        return []
    cands: list[str] = []
    for m in JWT_RE.finditer(blob.decode("latin-1", "replace")):
        tok = m.group(0)
        d = _jwt_payload(tok)
        # 平台 token 只有 sub/exp 两个字段；用这个特征把别的站点的 JWT 排掉
        if set(d.keys()) <= {"sub", "exp", "iat", "nbf"} and d.get("sub"):
            cands.append(tok)
    return cands


def _pick_newest(cands: list[str]) -> tuple[str, str]:
    """从候选里挑「还活着且过期最晚」的那个。返回 (token, 说明)。"""
    alive = []
    for t in cands:
        ok, _why = _jwt_alive(t)
        if ok:
            alive.append((_jwt_payload(t).get("exp", 0), t))
    if not alive:
        return "", ("候选都过期了" if cands else "没找到")
    alive.sort(key=lambda x: x[0], reverse=True)
    tok = alive[0][1]
    return tok, _jwt_alive(tok)[1]


def find_platform_token() -> tuple[str, str]:
    """扫本机浏览器，返回 (token, 来源说明)。找不到返回 ("", 原因)。"""
    env = (os.environ.get("TSEC_PLATFORM_TOKEN") or "").strip()
    if env and JWT_RE.fullmatch(env):
        ok, why = _jwt_alive(env)
        if ok:
            return env, "环境变量 TSEC_PLATFORM_TOKEN（" + why + "）"

    stale: list[str] = []
    for d in _ls_dirs():
        toks = _scan_leveldb(d)
        if not toks:
            continue
        tok, why = _pick_newest(toks)
        if tok:
            return tok, d + "（" + why + "）"
        stale.append(d)
    if stale:
        return "", "扫了 " + str(len(stale)) + " 个 profile，token 全过期：" + stale[0]
    return "", "本机没有浏览器存过 tsecbench 登录态（先用任一浏览器登录一次平台）"


def cmd_plat_token(args):
    """取平台 JWT → 判活 → 写进配置。取不到就明说下一步怎么做。"""
    cfg = Config.load()
    given = ""
    if "--token" in args:
        i = args.index("--token")
        given = args[i + 1] if i + 1 < len(args) else ""
    if given:
        tok, why = given.strip(), "命令行 --token"
    else:
        tok, why = find_platform_token()
    if not tok:
        die("取不到 platform_token —— " + why
            + "\n  做法：在任意浏览器里登录一次 https://tsecbench.zc.tencent.com/ ，再跑本命令；"
            + "\n  或者复制浏览器里 localStorage 的 token：tsec plat-token --token '<JWT>'")
    ok, life = _jwt_alive(tok)
    if not ok:
        die("这个 token " + life + " —— 重新登录平台一次，再跑本命令")
    d = _jwt_payload(tok)
    cfg.platform_token = tok
    path = cfg.save()
    print("✅ platform_token 已写入 " + path)
    print("   来源 : " + why)
    print("   sub  : " + str(d.get("sub")) + "    " + life)
    if not cfg.agent_id:
        print("⚠ agent_id 还是空的 —— `tsec sets` 看一眼，再用 `tsec run-new --agent N` 指定")
    return 0


def cmd_sets(_args):
    """列可用试题集（要 platform_token）：id / 题数 / 满分 / 时长 / 能否本地跑。"""
    cfg = Config.load()
    sets = _platform_request(cfg, "GET", "/api/v1/benchmark-sets", timeout=30) or []
    if not isinstance(sets, list) or not sets:
        print("（平台没返回试题集）")
        return 1
    print("%-24s %-6s %-8s %-8s %-8s %s" % ("id", "题数", "满分", "时长", "接入", "名称"))
    print("-" * 100)
    for s in sets:
        dur = int(s.get("duration_seconds") or 0)
        how = "仅托管" if s.get("hosted_only") else "可本地"
        print("%-24s %-6s %-8s %-8s %-8s %s" % (
            str(s.get("id", "")), str(s.get("challenge_count", "")),
            str(s.get("max_score", "")), str(dur // 3600) + "h", how, s.get("name", "")))
    print("\n★ hosted_only=True 的集只能走平台沙箱托管（run_source=hosted）；")
    print("  本地接入只能选 hosted_only=False 的集（Tsecbench v1=4 / bctf-2026=9 / XBOW=1 / Cybench=5）。")
    return 0


# ───────────────────────── 业务命令 ─────────────────────────

def cmd_config(_args):
    cfg = Config.load()
    print(f"base_url : {cfg.base_url or '(未设置)'}")
    print(f"token    : {cfg.masked()}")
    print(f"vpn_file : {cfg.vpn_file or '(未设置)'}")
    print(f"来源     : {cfg.source or '(无)'}")
    print(f"可用     : {'✅' if cfg.ok else '❌ 还缺东西'}")
    return 0 if cfg.ok else 1


def _list_summary(items: list) -> str:
    """列表页脚（纯函数，便于自检）。

    ★ 2026-11-14 修：原来这里是死代码 ——
      sum(int(i.get("correct_flag_count",0)) and 0 or 0 ...) 恒等于 0，
      页脚永远打印「累计得分 见平台」：没算出分，却让人以为算过。
      平台有 hint 扣分，客户端没有可信的得分公式，所以只报可核对计数，
      得分口径明确指向 run-status。
    """
    done = sum(1 for i in items if i.get("is_completed"))
    got = sum(int(i.get("correct_flag_count") or 0) for i in items)
    return (f"共 {len(items)} 题，已通关 {done} 题，"
            f"已提交正确 flag {got} 个（得分以平台 run-status 为准）")


def cmd_list(args):
    cfg = Config.load()
    items = _request(cfg, "GET", "/openapi/v1/challenges") or []
    if "--json" in args:
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return 0
    print(f"{'unique_code':<28} {'难度':<7} {'L':<2} {'flag进度':<9} {'通关':<6} {'状态':<12} 地址")
    print("-" * 100)
    for it in items:
        print(f"{it.get('unique_code',''):<28} {it.get('difficulty',''):<7} {it.get('level',''):<2} "
              f"{str(it.get('correct_flag_count',0))+'/'+str(it.get('flag_count',0)):<9} "
              f"{'✅' if it.get('is_completed') else '  ':<6} {it.get('container_status',''):<12} "
              f"{','.join(it.get('container_addr') or [])}")
    print("\n" + _list_summary(items))
    return 0


def cmd_start(args):
    if not args:
        die("用法：tsec start <unique_code>")
    cfg = Config.load()
    r = _request(cfg, "POST", "/openapi/v1/challenges/start", query={"unique_code": args[0]})
    print(json.dumps(r, ensure_ascii=False, indent=2) if "--json" in args
          else "，".join((r or {}).get("container_addr") or ["(无地址)"]))
    return 0


def cmd_hint(args):
    if not args:
        die("用法：tsec hint <unique_code>")
    cfg = Config.load()
    r = _request(cfg, "GET", "/openapi/v1/challenges/hint", query={"unique_code": args[0]})
    print((r or {}).get("hint") or "(无提示)")
    return 0


def cmd_submit(args):
    if len(args) < 2:
        die("用法：tsec submit <unique_code> <flag>")
    cfg = Config.load()
    r = _request(cfg, "POST", "/openapi/v1/challenges/submit",
                 body={"unique_code": args[0], "flag": args[1]})
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if (r or {}).get("correct") else 1


def cmd_close(args):
    if not args:
        die("用法：tsec close <unique_code>")
    cfg = Config.load()
    r = _request(cfg, "POST", "/openapi/v1/challenges/close", query={"unique_code": args[0]})
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


def cmd_addr(args):
    """拿直连地址：stopped 就 start，pending 就轮询。"""
    if not args:
        die("用法：tsec addr <unique_code> [--wait 120]")
    code = args[0]
    wait = 120
    if "--wait" in args:
        wait = int(args[args.index("--wait") + 1])
    cfg = Config.load()
    items = _request(cfg, "GET", "/openapi/v1/challenges") or []
    me = next((i for i in items if i.get("unique_code") == code), None)
    if me is None:
        die(f"没有这道题：{code}（`tsec list` 看清单）")
    if me.get("container_status") == "stopped":
        _request(cfg, "POST", "/openapi/v1/challenges/start", query={"unique_code": code})
    deadline = time.time() + wait
    while time.time() < deadline:
        items = _request(cfg, "GET", "/openapi/v1/challenges") or []
        me = next((i for i in items if i.get("unique_code") == code), {})
        if me.get("container_status") == "available" and me.get("container_addr"):
            for a in me["container_addr"]:
                print(a)
            return 0
        time.sleep(3)
    die(f"{code} 在 {wait}s 内没就绪（status={me.get('container_status')}）", 1)


VPN_HEALTHCHECK_URL = "http://10.0.100.58"   # SDK 里 _VPN_HEALTHCHECK_URL，只在靶场 VPN 里可达


def _opener():
    """按需返回带代理的 opener（A3 方案）。

    ★ 2026-11-03：容器（bridge 网络）与宿主机不在同一网络命名空间，
      `--network host` 和容器内静态路由都实测不可用（详见 tools/tunnel-proxy.py 头注释）。
      可行方案是「宿主起 VPN + 宿主跑 CONNECT 代理 + 容器设 http_proxy」。
      urllib 默认就会读 http_proxy/https_proxy；这里显式支持 TSEC_PROXY，
      并把 localhost/127.0.0.1 加进 bypass，避免把自检的 mock server 也代理出去。
    """
    proxy = (os.environ.get("TSEC_PROXY") or os.environ.get("https_proxy")
             or os.environ.get("HTTPS_PROXY") or os.environ.get("http_proxy")
             or os.environ.get("HTTP_PROXY") or "").strip()
    if not proxy:
        return urllib.request.build_opener()
    bypass = "localhost,127.0.0.1,::1"
    extra = (os.environ.get("no_proxy") or os.environ.get("NO_PROXY") or "").strip()
    if extra:
        bypass = bypass + "," + extra
    os.environ["no_proxy"] = bypass
    os.environ["NO_PROXY"] = bypass
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    )


def _urlopen(req, timeout):
    """统一入口：有代理走代理，没代理走默认。"""
    return _opener().open(req, timeout=timeout)


def _platform_request(cfg: Config, method: str, path: str, body: dict | None = None,
                      timeout: float = 120.0) -> object:
    """打平台自身的 /api/v1（不是 /openapi/v1）—— 用 UI 的 OAuth JWT 鉴权。

    创建跑分任务必须走这个口；BENCHMARK_TOKEN 只对 /openapi/v1 有效。
    """
    if not cfg.platform_token:
        die("缺 platform_token（平台 UI 登录态 JWT）—— 它是新建跑分任务用的，"
            "不是 BENCHMARK_TOKEN")
    url = cfg.base_url + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {cfg.platform_token}", "Accept": "application/json"}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _urlopen(req, timeout) as resp:
            payload = resp.read(2 * 1024 * 1024)
            return json.loads(payload.decode("utf-8", "replace")) if payload else None
    except urllib.error.HTTPError as exc:
        raw = exc.read(128 * 1024).decode("utf-8", "replace")
        raise ApiError(exc.code, f"platform_http_{exc.code}", raw[:300], raw) from None
    except urllib.error.URLError as exc:
        raise ApiError(0, "network_error", f"连不上平台 {cfg.base_url}（{exc.reason}）") from None


def cmd_healthcheck(_args):
    """VPN 联通探活：平台 SDK 用的就是这个地址（http://10.0.100.58）。

    通了说明靶场容器地址也能连；不通就说明隧道没建起来（先 `tsec vpn status`）。

    ★ 2026-11-03 实测踩坑：本机有**透明代理**，80 端口对任意 IP 都 connect 成功，
      连不存在的 10.0.100.199 也"通"，代理回 `HTTP/1.1 502 Bad Gateway`。
      所以 502/503 必须单独判，且**明确告诉人「这是代理，不是平台」**，
      否则会误以为 VPN 通了。`/dev/tcp` 探活在本机完全不可信。
    """
    req = urllib.request.Request(VPN_HEALTHCHECK_URL)
    try:
        with _urlopen(req, timeout=8) as resp:
            body = resp.read(4096).decode("utf-8", "replace")
        print(f"✅ VPN 探活通过 http={resp.status}")
        print(body[:600])
        try:
            j = json.loads(body)
            print(f"   client_ip={j.get('client_ip')}  time={j.get('time')}  status={j.get('status')}")
        except Exception:  # noqa: BLE001
            pass
        return 0
    except urllib.error.HTTPError as exc:
        if exc.code in (502, 503):
            print(f"❌ VPN 探活失败：HTTP {exc.code} —— **这是本机透明代理回的，不是平台应答**。")
            print(f"   {VPN_HEALTHCHECK_URL} 并不可达，等于 VPN 没通。")
            print("   → 判据：502/503 = 打到代理；只有 2xx/3xx 才算平台真的应答。")
            print("   → 注意：`/dev/tcp` 在本机会对任意 IP 都连得上（代理拦 80），别拿它当证据。")
        else:
            print(f"❌ VPN 探活失败：HTTP {exc.code} {exc.reason}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"❌ VPN 探活失败（{VPN_HEALTHCHECK_URL}）：{type(exc).__name__} {exc}")
        print("   → 隧道大概率没通。看 `tsec vpn status`，再确认当前网络能不能把 UDP 发到网关")
        return 1




def _progress_line(d: dict) -> str:
    """进度 + 剩余时间。

    ★ 平台在 status=running 时 `duration_seconds` 是 **null**（只有跑完才回填），
    早先按它算就会出现「21s / 0s（剩 0 分钟）」这种误导输出。真正可靠的剩余时间
    是 `expired_at`（任务创建时就算好了）。所以两条腿走：有 duration 用 duration，
    没有就用 expired_at 减现在。
    """
    el = int(d.get("elapsed_seconds") or 0)
    dur = int(d.get("duration_seconds") or 0)
    if dur > 0:
        return f"{el}s / {dur}s（剩 {max(dur - el, 0) // 60} 分钟）"
    exp = (d.get("expired_at") or "").strip()
    if exp:
        try:
            dt = _parse_iso(exp)
            left = int((dt - _now_utc()).total_seconds())
            if left <= 0:
                return f"{el}s（已过 expired_at={exp}，等平台结算）"
            return f"{el}s（剩 {left // 60} 分钟，expired_at={exp}）"
        except Exception:  # noqa: BLE001
            return f"{el}s（expired_at={exp}）"
    return f"{el}s"


def _parse_iso(s: str):
    """解析平台的 ISO 时间（带 +08:00 偏移）→ 带时区的 datetime。"""
    from datetime import datetime
    t = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(t)
    except ValueError:
        return datetime.fromisoformat(t[:19])


def _now_utc():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def cmd_run_status(args):
    cfg = Config.load()
    rid = args[0] if args and args[0].isdigit() else None
    if rid is None:
        active = _platform_request(cfg, "GET", "/api/v1/runs/active", timeout=30) or {}
        run = (active.get("active_run") or {})
        rid = run.get("run_id")
        if not rid:
            print("当前没有 active run（跑分任务已结束或未创建）")
            return 1
    d = _platform_request(cfg, "GET", f"/api/v1/runs/{rid}/status", timeout=30) or {}
    print(f"run_id        {d.get('run_id')}")
    print(f"状态          {d.get('status')}")
    print(f"运行模式      {d.get('run_source')} / {d.get('evaluation_type')}  set={d.get('set_id')} {d.get('set_name')}")
    print(f"进度          {_progress_line(d)}")
    print(f"得分          {d.get('current_score')} / {d.get('max_score')}   flags={d.get('final_flags')}")
    print(f"异常          {d.get('abnormal_reason')}")
    if d.get("token"):
        print(f"BENCHMARK_TOKEN {d['token']}")
    vpn = d.get("vpn_config") or ""
    if vpn:
        p = save_ovpn(d.get("run_id"), vpn)
        print(f"vpn_config 已存 → {p}")
    return 0


def cmd_run_new(args):
    """新建跑分任务：POST /api/v1/runs → 拿 BENCHMARK_TOKEN + 该任务的 ovpn。

    注意：平台有 ~10 分钟的“等 Agent 接入”窗口，创建后要马上 `tsec list`（哪怕只列一次），
    否则任务会变成 wait_expired（2026-09-30 就踩过：14:19 创建、14:30 wait_expired）。
    """
    import uuid
    cfg = Config.load()
    set_id = cfg.set_id
    agent_id = cfg.agent_id
    for i, a in enumerate(args):
        if a == "--set" and i + 1 < len(args):
            set_id = args[i + 1]
        if a == "--agent" and i + 1 < len(args):
            agent_id = int(args[i + 1])
    if not agent_id:
        die("缺 agent_id（平台上你那个 Agent 的编号）；可用 --agent N 指定")
    body = {"set_id": str(set_id), "agent_id": agent_id, "run_mode": "full",
            "run_source": "local", "evaluation_type": "agent",
            "base_model": ["Deepseek"], "request_id": str(uuid.uuid4())}
    r = _platform_request(cfg, "POST", "/api/v1/runs", body) or {}
    print(f"✅ 已创建跑分任务 run_id={r.get('run_id')} status={r.get('status')} "
          f"submission={r.get('submission_id')}")
    tok = r.get("token") or ""
    if tok:
        cfg.token = tok
        cfg.platform_token = cfg.platform_token
        print(f"   BENCHMARK_TOKEN = {tok}")
    # 取 vpn_config（同一个 status 口给）
    rid = r.get("run_id")
    if rid:
        st = _platform_request(cfg, "GET", f"/api/v1/runs/{rid}/status", timeout=30) or {}
        vpn = st.get("vpn_config") or ""
        if vpn:
            p = save_ovpn(rid, vpn)
            cfg.vpn_file = p
            print(f"   vpn_config  → {p}")
    if tok:
        cfg.save()
        print(f"   已写入 {config_candidates()[1]}")
    print("\n下一步（趁 10 分钟窗口）：")
    print(f"  tsec vpn up --file {cfg.vpn_file or '<run-XXX.ovpn>'}")
    print("  tsec healthcheck      # 探 10.0.100.58，通了才算 VPN 真的通")
    print("  tsec list             # ★马上列一次，避免 wait_expired")
    return 0


# ───────────────────────── VPN ─────────────────────────
#
# ★ 2026-11-03 修 R-1：原来这两行是模块级的 `os.geteuid()`，而 Windows 的
#   `os` 模块**没有** geteuid → 只要 import 这个文件就 AttributeError，
#   连 `tsec --help` 都敲不出来（selfcheck 还报 ✓，属假绿）。
#   改为：跨平台判定 + 目录按平台落位；POSIX 行为完全不变（仍是 /root/tsec）。

def _is_root() -> bool:
    """跨平台 root 判定：POSIX 用 geteuid；Windows 没有该属性 → False。"""
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid and geteuid() == 0)


# ═══════════════════════════════════════════════════════════════════════════
# ★★ 该轮（2026-11-22）血的教训：**绝不给平台下发的 ovpn 加任何覆盖参数**
#
#   现象：加了 --tun-mtu 1400 后报
#     AUTH_FAILED,Option inconsistency, please check cipher, auth, comp-lzo options
#   纯净配置 25 秒内 Initialization Sequence Completed。
#
#   根因不是 cipher/auth 不匹配，而是 OCC 选项一致性校验：
#     客户端 Local Options String : link-mtu 1457, tun-mtu 1400
#     服务端期望                 : link-mtu 1557, tun-mtu 1500
#   平台 VPN 开了 --opt-verify，逐字段比对；任一项不同就回 AUTH_FAILED，
#   且**把原因归到 cipher/auth 上** —— 报错措辞会把人带向错误方向。
#
#   已实测排除的弯路（都无效）：--comp-lzo no/yes、--compat-mode 2.4/2.5、
#   --auth SHA256（更糟：同时改控制通道 HMAC，连 TLS 都握不上）、
#   --cipher AES-256-CBC、--push-peer-info、--allow-compression。
#
#   => vpn up 只用 --config + --auth-nocache。见 tools/tsec-vpn-up.sh。
#   证据：实战复盘.md §2（含完整 OCC 双串对比）
# ═══════════════════════════════════════════════════════════════════════════

def _vpn_dir() -> str:
    """VPN 工作目录。优先级：$TSEC_VPN_DIR → POSIX /root/tsec → Windows ~/.tsec。"""
    env = (os.environ.get("TSEC_VPN_DIR") or "").strip()
    if env:
        return env
    if os.name == "nt":
        return os.path.join(os.path.expanduser("~"), ".tsec")
    # ★ 2026-11-03：原来这里无条件返回 /root/tsec —— 非 root 用户 makedirs 直接
    #   PermissionError（非 root 环境实测复现）。与 AGENTS.md「★不写死 /root」同一条纪律。
    #   仍是 root 时行为完全不变。
    if _is_root():
        return "/root/tsec"
    return os.path.join(os.path.expanduser("~"), ".tsec")


VPN_DIR = _vpn_dir()
VPN_LOG = os.path.join(VPN_DIR, "vpn.log")
VPN_PID = os.path.join(VPN_DIR, "vpn.pid")
VPN_CONF = os.path.join(VPN_DIR, "task.ovpn")


def save_ovpn(run_id, vpn: str) -> str:
    """把平台下发的 vpn_config 落盘（0600），返回路径。

    ★ 2026-11-03：原来两处写死 "/root/tsec"，Windows 原生会相对 cwd 建出
      `D:\\root\\tsec`（实测已发生）。统一走 VPN_DIR。
    """
    os.makedirs(_vpn_dir(), exist_ok=True)
    p = os.path.join(_vpn_dir(), f"run-{run_id}.ovpn")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(vpn)
    try:
        os.chmod(p, 0o600)          # Windows 上是近似 no-op，不报错
    except OSError:
        pass
    return p


def _sudo(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """以 root 权限跑（POSIX）。

    ★ 2026-11-03 修 R-1d：原来非 root 一律走 `sudo -n`，但**容器里没有 sudo**
      → `FileNotFoundError: 'sudo'`（实测）。改为三级降级，且每种都给人话：
        1) 本来就是 root        → 直接跑
        2) 有 sudo              → `sudo -n`
        3) 没 sudo 也没 root    → 如果命令本身存在就直接跑（容器里 openvpn 不存在
                                  时会由下面的 precheck 给出明确指引，不是 traceback）
    """
    # ★ 2026-11-18：默认 60s 兜底 —— openvpn --daemon / tail 之类都可能卡在
    #   网络或锁上，调用方没显式给 timeout 时不能无限等（原先靠调用方自觉）。
    kw.setdefault("timeout", 60)
    kw.setdefault("encoding", "utf-8")
    kw.setdefault("errors", "replace")
    if _is_root():
        return subprocess.run(cmd, capture_output=True, text=True, **kw)
    if os.name == "nt":
        die("`tsec vpn` 只在 Linux 下可用（Windows 原生没有 sudo/openvpn）。请在容器里跑：docker exec -u ctf pictf ...")
    if shutil.which("sudo"):
        return subprocess.run(["sudo", "-n", *cmd], capture_output=True, text=True, **kw)
    # 没 sudo：直接跑（让权限/缺失错误以正常返回码暴露，而不是 FileNotFoundError）
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _tun_ifaces() -> list[str]:
    try:
        return [n for n in os.listdir("/sys/class/net") if n.startswith(("tun", "tap"))]
    except OSError:
        return []


def _proc_tool(name: str) -> str:
    """proctools 可能没装（容器里一开始就没有 pkill/pgrep）—— 先问再跑。"""
    return shutil.which(name) or ""


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(str(pid).strip()), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def _proc_state(pid: str) -> str:
    """读 /proc/<pid>/stat 的状态字段（R/S/D/Z/T…）。读不到返回空串。

    ★ 2026-11-14（本地跑分 run 47736 实测）：僵尸进程的 comm 仍然是 "openvpn"，
      只看 comm 会把它当活进程 —— 实测隧道已死时 status 依然打印
      「openvpn 进程 : ✅ pid=1630」，同一屏下面却是「隧道接口 : ❌ 无」。
      人和 agent 都会据此误判「隧道正常」而跳过修复，自动修复条件也永不成立。
    """
    root = (globals().get("_PROC_ROOT_OVERRIDE")
            or os.environ.get("PI_LAB_PROC_ROOT") or "/proc")
    try:
        with open(os.path.join(root, str(pid), "stat"), encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError:
        return ""
    # 格式：pid (comm) state ... —— comm 里可能有空格/括号，所以从最后一个 ")" 之后切
    tail = raw.rsplit(")", 1)[-1].strip()
    return tail.split(" ", 1)[0] if tail else ""


def _openvpn_pids(include_zombie: bool = False, scoped: bool = True) -> list[str]:
    """不依赖 pgrep：扫 /proc/*/comm。容器最小镜像里 pgrep 常常没有。

    ★ 默认**跳过僵尸（state=Z）**：僵尸已经没有网络能力，把它当活进程会让人
      误判隧道正常（见 _proc_state 注释）。需要列出僵尸做诊断时传 include_zombie=True。

    ★★ 2026-11-14 修（p0，实测三次杀穿生产隧道）：默认**只认本实例的隧道**
      （scoped=True）—— 判据是 /proc/<pid>/cmdline 里出现本 VPN_DIR 下的配置路径。
      原来是不加区分地扫「所有叫 openvpn 的进程」，于是：
        · `board/tests/test_tsec_vpn.py` 跑一次「vpn down 在没 openvpn 的环境里也能跑」
          这个用例，就把**真隧道**杀了（它隔离了 TSEC_VPN_DIR，却没隔离 /proc 扫描）；
        · `selfcheck` 会跑那个测试 → 每次自检都顺手干掉正在解题用的隧道；
        · `tsec vpn down` 同理，一条"停一下看看"就把整场跑分的网络拔了。
      实测证据：vpn.log 里 12:53:25 / 13:03:01 / 13:25 三次 SIGTERM，
      时间点与 selfcheck / 测试运行完全重合。
      诊断要看全机 openvpn 时传 scoped=False。
    """
    # ★ 假 /proc 支持：自检要能验证僵尸判定，又不能真的去杀真进程。
    #   只影响本函数与 _proc_state，生产路径（未设该变量）行为完全不变。
    root = (globals().get("_PROC_ROOT_OVERRIDE")
            or os.environ.get("PI_LAB_PROC_ROOT") or "/proc")
    out = []
    try:
        for name in os.listdir(root):
            if not name.isdigit():
                continue
            try:
                with open(os.path.join(root, name, "comm"), encoding="utf-8", errors="replace") as fh:
                    if fh.read().strip() != "openvpn":
                        continue
                # ★ 2026-11-14：本实例归属判定 —— 只认命令行里指向本 VPN_DIR 配置的进程。
                #   参数为空（如 ps 看不到 cmdline）时保守放行，避免漏判真进程。
                if scoped:
                    # 读不到 cmdline（老内核/受限 procfs/测试假 proc）→ 保守放行，
                    # 宁可多认一个也不漏判本实例；只有**明确看到**指向别的配置才排除。
                    # ★ 2026-11-20：D 态（不可中断睡眠）进程的 cmdline 读下去会**永久阻塞**
                    #   （实测容器里一批卡在 __access_remote_vm 的 ps/find/grep），
                    #   而这里是在「vpn 状态/停隧道」的关键路径上，一挂整套流程就死。
                    #   state 来自 /proc/<pid>/stat，不会阻塞；D 态本来也不可能在正常转发。
                    if _proc_state(name) in ("D", "X", "x"):
                        continue
                    try:
                        with open(os.path.join(root, name, "cmdline"), "rb") as fh:
                            cmdline = fh.read(4096).decode("utf-8", "replace")
                    except OSError:
                        cmdline = ""
                    if "--config" in cmdline and VPN_CONF not in cmdline:
                        continue
            except OSError:
                continue
            if not include_zombie and _proc_state(name) == "Z":
                continue
            out.append(name)
    except OSError:
        pass
    return out


def _vpn_caps() -> dict:
    """探一遍 VPN 需要的四件套，给「能不能在这台机器上起隧道」一个确定答案。

    ★ 2026-11-04 修（用户问「彻底解决 tsec vpn 在容器内必定崩溃」）：
      原实现在 cmd_vpn 开头无条件 die()，导致**连 status/down 都跑不了** ——
      一个「查状态」命令不该要求 openvpn 存在。现在拆成能力探测 + 按子命令分级降级。
    """
    caps = {
        "os": os.name,
        "openvpn": shutil.which("openvpn") or "",
        "sudo": shutil.which("sudo") or "",
        "root": _is_root(),
        "tun_dev": os.path.exists("/dev/net/tun"),
        "tun_ifaces": _tun_ifaces(),
        "container": os.path.exists("/.dockerenv"),
    }
    # NET_ADMIN：openvpn 要 ioctl(TUNSETIFF) + 改路由，没有它一定失败。
    # ★ 2026-11-04 修：只看 CapEff 会误报 —— setpriv 降权后的 ctf 用户 CapEff 全空，
    #   但 CapBnd（bounding set）里有 net_admin，sudo 到 root 后 CapEff 就继承出来。
    #   真正该问的是「**以当前身份能不能拿到**」：已经是 root 看 CapEff，
    #   有 sudo 看 CapBnd（sudo 后是完整 bounding set），两者都没有才真缺。
    caps["net_eff"] = caps["net_bnd"] = False
    try:
        with open("/proc/self/status", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("CapEff:"):
                    caps["net_eff"] = bool(int(line.split()[1], 16) & (1 << 12))
                elif line.startswith("CapBnd:"):
                    caps["net_bnd"] = bool(int(line.split()[1], 16) & (1 << 12))
    except OSError:
        pass
    caps["net_admin"] = caps["net_eff"] or (caps["net_bnd"] and (caps["root"] or caps["sudo"]))
    return caps


def _vpn_blockers(caps: dict, need_dev: bool = True) -> list[str]:
    """返回「起隧道还缺什么」，人话。空列表 = 齐了。"""
    out = []
    if caps["os"] == "nt":
        out.append("Windows 原生没有 openvpn/tun —— 请在容器里跑（docker exec -u ctf pictf）")
        return out
    if not caps["openvpn"]:
        out.append("没有 openvpn 可执行文件")
    if need_dev and not caps["tun_dev"]:
        out.append("没有 /dev/net/tun（compose 里加 devices: /dev/net/tun，或宿主 --device 传进来）")
    if need_dev and not caps["net_admin"]:
        out.append("缺 CAP_NET_ADMIN（compose 里 cap_add: [NET_ADMIN]）")
    if not caps["root"] and not caps["sudo"]:
        out.append("非 root 且无 sudo —— 建 tun/改路由一定失败")
    return out


def _tail_bytes(path: str, limit: int) -> bytes:
    """读普通文件的尾部，最多 limit 字节。

    ★ 2026-11-14 实测（本地跑分 run 47736）：openvpn 日志一旦涨过 16KB，
      tsec vpn status 直接 ValueError traceback —— read_capped 的设计是
      「超大文件一律拒绝」（那是对外部传入路径的防护），但日志天生会超，
      拒绝等于让状态查询在隧道跑久之后不可用。所以这里单独走 seek 尾部，
      既不整份读进内存，也不放松 read_capped 对不可信路径的限长。
    """
    import stat as _stat
    st = os.stat(path, follow_symlinks=True)
    if not _stat.S_ISREG(st.st_mode):
        raise ValueError(f"不是普通文件：{path}")
    with open(path, "rb") as fh:
        if st.st_size > limit:
            fh.seek(st.st_size - limit)
        return fh.read(limit)


def _read_vpn_log(n: int = 6) -> str:
    """读 vpn.log 尾部。

    ★ 2026-11-04：openvpn 以 root 身份起来时日志是 root:root 0600，
      ctf 用户直接 open() 会 PermissionError —— 原实现就是这么拿不到线索的。
      先自己读，不行再升权 tail。两条路都不行就返回空串，不抛异常。
    ★ 2026-11-14：读尾部必须容忍「日志超长」。之前只捕 OSError，日志过 16KB
      就 traceback，容器里 tsec vpn status 直接不可用。
    """
    if not os.path.exists(VPN_LOG):
        return ""
    try:
        raw = _tail_bytes(VPN_LOG, 16384).decode("utf-8", "replace")
    except (OSError, ValueError):
        r = _sudo(["tail", "-n", str(n), VPN_LOG])
        raw = r.stdout or ""
    return "\n".join(raw.strip().splitlines()[-n:])


def _claim_vpn_files() -> None:
    """把 openvpn 写出来的 root 文件还给当前用户。

    ★ 2026-11-04：`--log`/`--writepid` 都是 root 身份创建的 0600 文件。
      不还属主的话：status 读不到日志、down 读不到 pid，
      表面上就是「进程没在跑 / 日志读不到」——用户以为 VPN 没起来。
    """
    if _is_root():
        return
    # ★ Windows 没有 os.getuid —— 原来直接调用会在宿主原生环境抛 AttributeError
    #   （本回归测试当场抓到）。非 POSIX 直接跳过：那边本来也没有 chown 概念。
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        return
    me = "%d:%d" % (getuid(), os.getgid())
    for p in (VPN_LOG, VPN_PID, VPN_CONF, VPN_DIR):
        if os.path.exists(p):
            try:
                if os.stat(p).st_uid != os.getuid():
                    _sudo(["chown", me, p])
            except OSError:
                pass


def _tunnel_healthy(timeout: float = 6.0) -> bool:
    """隧道是否**真的**可用：有活进程 + 有 tun 接口 + 平台探活 2xx/3xx。

    ★ 2026-11-14：给 `vpn up` 做幂等判据用。只看进程不够（僵尸也是 openvpn），
      只看隧道路由也不够（接口在但服务端已断），所以要三件都成立。
      探活失败**不抛异常**，只返回 False —— 调用方是「要不要重启」的判断，不是报告。
    """
    if not _openvpn_pids():
        return False
    if not _tun_ifaces():
        return False
    try:
        req = urllib.request.Request(VPN_HEALTHCHECK_URL)
        with _urlopen(req, timeout=timeout) as resp:
            return 200 <= int(resp.status) < 400
    except Exception:  # noqa: BLE001 —— 任何失败都视为「不健康」
        return False


def cmd_vpn(args):
    sub = args[0] if args else "status"
    cfg = Config.load()
    src = ""
    if "--file" in args:
        src = args[args.index("--file") + 1]
    src = src or cfg.vpn_file

    caps = _vpn_caps()

    # ★ 2026-11-04：precheck 只挡 up。status/down 在任何环境都必须能跑
    #   （原实现无条件 die，容器里连 `tsec vpn status` 都崩 —— 用户报的就是这条）。
    if sub == "up":
        blockers = _vpn_blockers(caps, need_dev=True)
        if blockers:
            die(
                "起不了隧道，缺：\n  - " + "\n  - ".join(blockers) + "\n\n"
                "**隧道在容器里起**（本项目不用 WSL）：\n"
                "  docker exec -u ctf pictf bash -lc 'tsec vpn up --file /opt/pi-ctf/work/intake/<task>.ovpn'\n"
                "  容器侧：  只做容器内可达的 HTTP；`tsec list/submit/close` 走平台 API，不需要 tun。\n"
                "  要真在容器里起：compose 加 devices+cap_add（见 compose.yaml 注释），并装 openvpn。\n"
                "  自检：    `tsec vpn status`（零依赖，任何时候都能跑）"
            )

    if sub == "up":
        if not src:
            die("没给 ovpn 文件：tsec vpn up --file /mnt/c/Users/.../task_xxx.ovpn")
        if not os.path.exists(src):
            die(f"ovpn 文件不存在：{src}")
        # ★ 2026-11-04 修「容器内必崩」的第二条根因（第一条是无条件 die）：
        #   原来先用 _sudo mkdir 建 ~/.tsec → 目录属主变 root，
        #   下一行却用**普通用户身份** open(...,"wb") 写里面 → PermissionError traceback。
        #   现在：能用自己身份建就自己建（$HOME 本来就归自己），只有真没权限才升权，
        #   并且升权后把属主还回来 —— 自己的家目录不该被 root 占着。
        try:
            os.makedirs(VPN_DIR, exist_ok=True)
        except OSError:
            _sudo(["mkdir", "-p", VPN_DIR])
            _sudo(["chown", f"{os.getuid()}:{os.getgid()}", VPN_DIR])
        if not os.access(VPN_DIR, os.W_OK):
            _sudo(["chown", f"{os.getuid()}:{os.getgid()}", VPN_DIR])
            os.makedirs(VPN_DIR, exist_ok=True)

        data = read_capped(src, 512 * 1024)
        # 就地写 VPN_CONF（不再走 tmp + sudo cp）：只要目录归自己，这一步就不会因权限炸
        try:
            with open(VPN_CONF, "wb") as fh:
                fh.write(data)
            os.chmod(VPN_CONF, 0o600)
        except OSError:
            tmp = f"{VPN_DIR}/.task.ovpn.tmp"
            with open(tmp, "wb") as fh:
                fh.write(data)
            _sudo(["cp", tmp, VPN_CONF])
            os.unlink(tmp)
            _sudo(["chmod", "600", VPN_CONF])
        # ★ 2026-11-14 修 该轮修复（实测两次踩到）：原来是「无条件先 pkill 再起」。
        #   后果：① 两路并发 `vpn up` 互相杀掉对方刚建好的隧道；
        #        ② 新隧道起不来时（证书/网关抖动），把原本**好着**的隧道也杀了 →
        #          从「能用」变成「全断」，而调用方只看到一句握手失败。
        #   实测：12:39 建好的隧道被 12:53 / 13:03 两次 SIGTERM 打死。
        #   现在：已有**健康**隧道就直接返回（幂等），别去动它；只有确实没有才起。
        if _tunnel_healthy():
            print("✅ VPN 已在跑且可达，不重复启动（避免互杀）：" + " ".join(_tun_ifaces()))
            return 0
        # ★ 2026-11-14：这里也不再 pkill -x（那是「杀全机所有 openvpn」）。
        #   已经确认本实例隧道不健康，只清本实例遗留的 pid，不动别的实例。
        for _p in _openvpn_pids():
            try:
                os.kill(int(_p), 15)
            except OSError:
                pass
        time.sleep(1)
        r = _sudo(["openvpn", "--config", VPN_CONF, "--daemon",
                   "--log", VPN_LOG, "--writepid", VPN_PID, "--verb", "4"])
        if r.returncode != 0:
            # ★ 2026-11-04：`--daemon` 模式下 openvpn 把人类可读的错误写进 --log 指定的
            #   文件，**stderr 是空的** —— 原实现只贴 stderr，于是报出一句空白的
            #   "openvpn 启动失败："（实测：配错时就是这个样子，用户拿不到任何线索）。
            #   这里 stderr 空就回读日志尾部，再不行给出 ovpn 本身的检查方向。
            _claim_vpn_files()
            why = (r.stderr or "").strip()
            if not why:
                why = _read_vpn_log(6)
            if not why:
                why = ("openvpn 没有任何输出。常见原因：ovpn 里缺 --ca/--peer-fingerprint、"
                       "证书过期、或 remote 不可达。先用 `openvpn --config <文件>` 前台跑一次看原文")
            die(f"openvpn 启动失败：\n{why[:800]}")
        print("⏳ openvpn 已起，等握手……")
        for _ in range(45):
            time.sleep(2)
            log = _sudo(["tail", "-n", "60", VPN_LOG]).stdout or ""
            if "Initialization Sequence Completed" in log:
                print("✅ VPN 已连上：" + " ".join(_tun_ifaces()))
                return 0
            if "TLS Error" in log and "TLS handshake failed" in log:
                print("⚠️ 握手超时（服务端没回包）—— tail 见下：")
                print("\n".join(log.strip().splitlines()[-6:]))
                return 1
        print("⚠️ 90s 内没连上，日志尾部：")
        print((_sudo(["tail", "-n", "12", VPN_LOG]).stdout or "").strip())
        return 1

    if sub == "down":
        # ★ 2026-11-11 修（实测踩到）：原来「有 pkill 就用 pkill」是错的。
        #   Docker Desktop(WSL2) 下容器内 pkill/pgrep/ps **都要遍历 /proc**，
        #   而 /proc 穿透极慢 → pkill 会卡满 120s 超时（实测 rc=124，
        #   连「没有 openvpn 进程」时也卡）。表现：test_tsec_vpn 随机超时。
        #   正确顺序：**先按 pid 文件 + 扫 /proc 精确 kill**（这条本来就是零依赖，
        #   而且比 pkill 更精确），pkill 只作最后兜底。
        stopped = []
        if os.path.exists(VPN_PID):
            try:
                os.kill(int(read_capped(VPN_PID, 64).decode().strip()), 15)
                stopped.append("pid 文件")
            except (OSError, ValueError):
                pass
        for pid in _openvpn_pids():
            try:
                os.kill(int(pid), 15)
                stopped.append(f"pid={pid}")
            except OSError:
                pass
        # ★★ 2026-11-14 摘掉兜底 pkill（原来写成：上面没找到进程就 `pkill -x openvpn`）。
        #   这条是**三次杀穿生产隧道的直接执行者**：测试/自检用临时 TSEC_VPN_DIR，
        #   自然「本实例没进程」→ 走兜底 → 把全机所有 openvpn 一起杀掉，
        #   包括正在解题用的那条。而 pkill 又是全局的，等于「清一下自己的」变成「拔全场网线」。
        #   正确语义：down 只停**本实例**（pid 文件 + scoped 扫描）；别人的隧道不归我管。
        #   真要清全机，人工用 `tsec vpn down --all`（显式选择，不再是副作用）。
        if not stopped and ("--all" in (args or [])):
            for pid in _openvpn_pids(scoped=False):
                try:
                    os.kill(int(pid), 15)
                    stopped.append(f"all:pid={pid}")
                except OSError:
                    pass
        print("已停：" + "、".join(stopped) if stopped else "没在跑，无需停")
        return 0

    # status：★ 零依赖，任何时候都能跑（没有 openvpn/tun/sudo 也必须给出结论）
    pid = ""
    if os.path.exists(VPN_PID):
        try:
            pid = read_capped(VPN_PID, 64).decode().strip()
        except OSError:
            pid = ""
    # 只看本实例的隧道；--all 时才把别的实例也列出来（诊断用）
    # 只看本实例的隧道；--all 时才把别的实例也列出来（诊断用）
    pids = _openvpn_pids()                       # 已排除僵尸（state=Z）
    zombies = [p for p in _openvpn_pids(include_zombie=True) if p not in pids]
    # ★ 2026-11-14（实测）：本实例「没在跑」不等于机器上没有隧道 —— ctf 用户起的
    #   隧道在 /home/ctf/.tsec，root 起的在 /root/tsec，归属判定会各自只认自己的。
    #   原来 root 执行 status 报「❌ 没在跑」而隧道其实活着（tun0 + 200），
    #   于是人会以为要重启 → 再起一条 → 两条抢同一网段互杀。这里把「别人的」列出来。
    others = []
    try:
        others = [p for p in _openvpn_pids(scoped=False) if p not in pids and p not in zombies]
    except Exception:  # noqa: BLE001
        others = []
    # ★ 2026-11-14：pid 文件里的 pid 也必须查僵尸 —— 否则「pid 还活着」会把
    #   已经死掉的 openvpn（只剩僵尸壳）判成活进程，与 `隧道接口` 那行自相矛盾。
    alive = bool(pids) or bool(pid and _pid_alive(pid) and _proc_state(pid) != "Z")
    ifs = caps["tun_ifaces"]
    _claim_vpn_files()
    log = _read_vpn_log(4)
    print(f"环境         : {'容器内' if caps['container'] else '宿主'}"
          f" · {'root' if caps['root'] else ('sudo 可用' if caps['sudo'] else '普通用户无 sudo')}")
    print(f"openvpn      : {('✅ ' + caps['openvpn']) if caps['openvpn'] else '❌ 未安装'}")
    print(f"/dev/net/tun : {('✅ 存在' if caps['tun_dev'] else '❌ 不存在')}"
          f" · NET_ADMIN {('✅' if caps['net_admin'] else '❌')}")
    print(f"openvpn 进程 : {('✅ ' + ', '.join('pid=' + p for p in pids)) if pids else '❌ 没在跑'}")
    if others:
        print(f"其他实例隧道 : ⚠ pid={', '.join(others)}（别的用户/目录起的 openvpn，不归本实例管；"
              f"	别重复起——两个实例抢同一网段会互杀）")
    if zombies:
        print(f"僵尸进程     : ⚠ pid={', '.join(zombies)}（已死，不算在跑；PID1 无 reaper 会堆积）")
    print(f"隧道接口     : {', '.join(ifs) if ifs else '❌ 无'}")
    if log:
        print("日志尾部     :")
        print("  " + "\n  ".join(log.splitlines()))
    blockers = _vpn_blockers(caps, need_dev=True)
    if blockers:
        print("起隧道缺     :")
        for b in blockers:
            print("  - " + b)
        print("  （按 既定决策 VPN 归宿主；容器侧 list/submit/close 不需要隧道）")
    return 0 if (alive and ifs) else 1


# ───────────────────────── 自检（mock server） ─────────────────────────

def _selftest() -> int:
    import http.server
    import socketserver
    import threading

    state = {"started": False, "submitted": set(), "platform": False}
    FLAGS = ["flag{part_one}", "flag{part_two}"]

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # noqa: A003
            pass

        def _send(self, obj, code=200):
            b = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def _auth(self) -> bool:
            if self.headers.get("BENCHMARK_TOKEN") != "test-token-uuid":
                self._send({"code": "task_not_found", "message": "bad token", "detail": {}}, 404)
                return False
            return True

        def _q(self):
            return urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

        # ── 平台自身 /api/v1（Bearer 鉴权）—— 建任务/查状态用 ──
        def _platform(self, method: str) -> bool:
            if not self.path.startswith("/api/v1"):
                return False
            if self.headers.get("Authorization") != "Bearer plat-jwt":
                self._send({"detail": "unauthorized"}, 401)
                return True
            p = urllib.parse.urlparse(self.path).path
            if p == "/api/v1/runs" and method == "POST":
                self._send({"run_id": 23922, "token": "tok-from-run", "status": "pending",
                            "request_id": "r-1", "submission_id": 269, "creation_status": "created",
                            "set_id": "4", "set_name": "Tsecbench v1", "run_source": "local"}, 201)
            elif p == "/api/v1/runs/active":
                self._send({"active_run": {"run_id": 23922, "status": "running"}, "active_request": None})
            elif p == "/api/v1/runs/23922/status":
                self._send({"run_id": 23922, "status": "running", "token": "tok-from-run",
                            "set_id": "4", "set_name": "Tsecbench v1", "run_source": "local",
                            "evaluation_type": "agent", "duration_seconds": 21600, "elapsed_seconds": 60,
                            "current_score": 0, "max_score": 23000, "final_flags": None,
                            "abnormal_reason": None, "vpn_config": "client\ndev tun\nremote 10.0.0.1 9798\n"})
            else:
                self._send({"detail": "not found"}, 404)
            return True

        def do_GET(self):
            if self._platform("GET"):
                return
            if not self._auth():
                return
            p = urllib.parse.urlparse(self.path).path
            if p == "/openapi/v1/challenges":
                self._send([{
                    "unique_code": "web_test_01", "description": "mock", "difficulty": "easy",
                    "level": 1, "total_score": 100, "flag_count": 2,
                    "correct_flag_count": len(state["submitted"]), "is_completed": False,
                    "container_status": "available" if state["started"] else "stopped",
                    "container_addr": ["10.0.0.9:8080"] if state["started"] else [],
                }])
            elif p == "/openapi/v1/challenges/hint":
                self._send({"unique_code": self._q().get("unique_code", [""])[0], "hint": "注入点在 login"})
            else:
                self._send({"code": "challenge_not_found", "message": "no", "detail": {}}, 404)

        def do_POST(self):
            if self._platform("POST"):
                return
            if not self._auth():
                return
            p = urllib.parse.urlparse(self.path).path
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if p.endswith("/start"):
                state["started"] = True
                self._send({"unique_code": self._q().get("unique_code", [""])[0],
                            "container_addr": ["10.0.0.9:8080"]})
            elif p.endswith("/submit"):
                f = body.get("flag", "")
                if f in FLAGS and f not in state["submitted"]:
                    state["submitted"].add(f)
                    self._send({"correct": True, "awarded": 50, "cumulative_score": 50 * len(state["submitted"]),
                                "correct_flag_count": len(state["submitted"]), "total_flag_count": 2,
                                "matched_flag_index": FLAGS.index(f)})
                elif f in state["submitted"]:
                    self._send({"code": "duplicate", "message": "dup", "detail": {}}, 409)
                else:
                    self._send({"correct": False, "awarded": 0, "cumulative_score": 0,
                                "correct_flag_count": len(state["submitted"]), "total_flag_count": 2,
                                "matched_flag_index": None})
            elif p.endswith("/close"):
                state["started"] = False
                self._send({"unique_code": self._q().get("unique_code", [""])[0], "closed": True})
            else:
                self._send({"code": "challenge_not_found", "message": "no", "detail": {}}, 404)

    class Srv(socketserver.ThreadingTCPServer):
        allow_reuse_address = True

    srv = Srv(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    fails: list[str] = []

    def check(name, cond, extra=""):
        print(("  ✅ " if cond else "  ❌ ") + name + (f"  {extra}" if extra and not cond else ""))
        if not cond:
            fails.append(name)

    print("── tsec 自检（本地 mock server，不碰真平台）──")
    # ★ 预先初始化，确保 finally 里引用它们不会 NameError（自检中途失败也要能清理）
    _st = None
    _old_vpn_dir = os.environ.get("TSEC_VPN_DIR")
    try:
        cfg = Config(base, "test-token-uuid", "", "selftest")
        items = _request(cfg, "GET", "/openapi/v1/challenges")
        check("list 返回 1 题", isinstance(items, list) and len(items) == 1, str(items))
        check("未启动时地址为空", items[0]["container_addr"] == [])
        check("列表页脚不估算得分（口径指向 run-status）",
              "run-status" in _list_summary([{"is_completed": False, "correct_flag_count": 1, "flag_count": 2}]))
        r = _request(cfg, "POST", "/openapi/v1/challenges/start", query={"unique_code": "web_test_01"})
        check("start 拿到地址", r.get("container_addr") == ["10.0.0.9:8080"], str(r))
        items = _request(cfg, "GET", "/openapi/v1/challenges")
        check("启动后状态 available", items[0]["container_status"] == "available")
        r = _request(cfg, "POST", "/openapi/v1/challenges/submit",
                     body={"unique_code": "web_test_01", "flag": "flag{part_one}"})
        check("submit 正确 flag", r.get("correct") is True and r.get("awarded") == 50, str(r))
        try:
            _request(cfg, "POST", "/openapi/v1/challenges/submit",
                     body={"unique_code": "web_test_01", "flag": "flag{part_one}"})
            check("重复提交 → duplicate", False)
        except ApiError as e:
            check("重复提交 → duplicate", e.code == "duplicate", str(e))
        r = _request(cfg, "POST", "/openapi/v1/challenges/submit",
                     body={"unique_code": "web_test_01", "flag": "flag{wrong}"})
        check("错误 flag → correct=False 且 0 分",
              r.get("correct") is False and r.get("awarded") == 0, str(r))
        h = _request(cfg, "GET", "/openapi/v1/challenges/hint", query={"unique_code": "web_test_01"})
        check("hint 有内容", bool(h.get("hint")))
        # 鉴权失败
        bad = Config(base, "wrong-token", "", "selftest")
        try:
            _request(bad, "GET", "/openapi/v1/challenges")
            check("坏 token 被拒", False)
        except ApiError as e:
            check("坏 token → task_not_found", e.code == "task_not_found", str(e))
        # 网络不通
        dead = Config("http://127.0.0.1:1", "x", "", "selftest")
        try:
            _request(dead, "GET", "/openapi/v1/challenges")
            check("网络不通要报错", False)
        except ApiError as e:
            check("网络不通 → network_error", e.code == "network_error", str(e))
        # read_capped 护栏
        # ★ 2026-11-03：原来用 os.mkfifo + /tmp 写死 → Windows 无 mkfifo，且
        #   /tmp 在 D: 上会落成 D:\tmp。改为 tempfile 目录 + 平台分支：
        #   POSIX 用 FIFO 验"非普通文件被拒"；Windows 用目录验同一护栏。
        import tempfile as _tf
        _td = _tf.mkdtemp(prefix="tsec-guard-")
        try:
            if hasattr(os, "mkfifo"):
                _fifo = os.path.join(_td, "fifo")
                try:
                    os.mkfifo(_fifo)
                    read_capped(_fifo)
                    check("FIFO 被 read_capped 挡住", False)
                except ValueError:
                    check("FIFO 被 read_capped 挡住", True)
                except Exception as e:  # noqa: BLE001
                    check("FIFO 被 read_capped 挡住", False, str(e))
            else:
                # Windows：目录不是普通文件 → 必须被拒（同一护栏的等价验证）
                try:
                    read_capped(_td)
                    check("非普通文件被 read_capped 挡住", False)
                except (ValueError, OSError):
                    check("非普通文件被 read_capped 挡住", True)
        finally:
            import shutil as _sh
            _sh.rmtree(_td, ignore_errors=True)
        try:
            _big = os.path.join(_tf.gettempdir(), "tsec_big_selftest")
            open(_big, "wb").write(b"0" * 200000)
            read_capped(_big)
            check("超大文件被挡住", False)
        except ValueError:
            check("超大文件被挡住", True)
        finally:
            for f in (os.path.join(_tf.gettempdir(), "tsec_big_selftest"),):
                if os.path.exists(f):
                    os.unlink(f)
        # 配置脱敏
        check("token 打码", "test-token-uuid" not in Config("", "test-token-uuid", "").masked())

        # ── 平台口（/api/v1，Bearer）—— 建任务 / 查状态 ──
        # ★ 2026-11-03 修：原来两处写死
        #   ① tmpcfg = "/tmp/..." → Windows 上落成 D:\tmp
        #   ② save_ovpn 往真实 VPN_DIR 写 mock ovpn → 污染 ~/.tsec/run-23922.ovpn
        #   统一改成「自检专用临时目录」，自检结束删掉，绝不碰真实运行时目录。
        _st = _tf.mkdtemp(prefix="tsec-selftest-")
        os.environ["TSEC_VPN_DIR"] = os.path.join(_st, "vpn")
        tmpcfg = os.path.join(_st, "tsec_selftest_cfg.json")
        with open(tmpcfg, "w", encoding="utf-8") as fh:
            json.dump({"base_url": base, "token": "", "platform_token": "plat-jwt",
                       "agent_id": 3219, "set_id": "4"}, fh)
        os.environ["PI_LAB_TSEC_CONFIG"] = tmpcfg
        pcfg = Config.load()
        check("platform_token 读到了", pcfg.platform_token == "plat-jwt")
        r = _platform_request(pcfg, "POST", "/api/v1/runs", {"set_id": "4"})
        check("建跑分任务拿到 run_id/token", r.get("run_id") == 23922 and r.get("token") == "tok-from-run", str(r))
        rc = cmd_run_new(["--agent", "3219"])
        saved = json.load(open(tmpcfg, encoding="utf-8"))
        check("run-new 把 BENCHMARK_TOKEN 写回配置", saved.get("token") == "tok-from-run", str(saved.get("token")))
        check("run-new 留下 ovpn 文件",
              (saved.get("vpn_file") or "").endswith("run-23922.ovpn")
              and os.path.isabs(saved.get("vpn_file") or ""),
              str(saved.get("vpn_file")))
        check("run-new 返回 0", rc == 0)
        rc = cmd_run_status([])
        check("run-status 返回 0", rc == 0)
        bad = Config(base, "", "", "selftest", platform_token="wrong")
        try:
            _platform_request(bad, "GET", "/api/v1/runs/active")
            check("平台坏 token 被拒", False)
        except ApiError as e:
            check("平台坏 token 被拒", e.status == 401, str(e))
        del os.environ["PI_LAB_TSEC_CONFIG"]
        # VPN 探活：没真 VPN 时必须回 1（不能假报成功）
        check("healthcheck 无 VPN 时返回 1", cmd_healthcheck([]) == 1)
        # vpn 子命令（不真连）
        check("vpn 子命令可解析", callable(cmd_vpn))
        # ★ 2026-11-14（run 47736 实测）：僵尸 openvpn 的 comm 也叫 openvpn，
        #   只看 comm 会误报「隧道在跑」。这里用假 /proc 验证僵尸被排除。
        with _tf.TemporaryDirectory() as _p:
            os.makedirs(os.path.join(_p, "100"), exist_ok=True)
            os.makedirs(os.path.join(_p, "200"), exist_ok=True)
            open(os.path.join(_p, "100", "comm"), "w").write("openvpn\n")
            open(os.path.join(_p, "200", "comm"), "w").write("openvpn\n")
            # 100 = 僵尸 Z；200 = 正常 S
            open(os.path.join(_p, "100", "stat"), "w").write("100 (openvpn) Z 1 100 100 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 0 0 0\n")
            open(os.path.join(_p, "200", "stat"), "w").write("200 (openvpn) S 1 200 200 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 0 0 0\n")
            _old = globals().get("_PROC_ROOT_OVERRIDE")
            try:
                globals()["_PROC_ROOT_OVERRIDE"] = _p
                got = _openvpn_pids()
                allp = _openvpn_pids(include_zombie=True)
            finally:
                globals()["_PROC_ROOT_OVERRIDE"] = _old
            check("僵尸 openvpn 不算活进程", got == ["200"], f"got={got}")
            check("include_zombie 能看到僵尸", sorted(allp) == ["100", "200"], f"all={allp}")
    finally:
        srv.shutdown()
        # ★ 清理自检临时目录，并还原 TSEC_VPN_DIR —— 自检绝不污染真实运行时目录
        try:
            if _old_vpn_dir is None:
                os.environ.pop("TSEC_VPN_DIR", None)
            else:
                os.environ["TSEC_VPN_DIR"] = _old_vpn_dir
        except Exception:  # noqa: BLE001
            pass
        if _st:
            try:
                import shutil as _sh
                _sh.rmtree(_st, ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass

    print(f"\n{'✅ 全部通过' if not fails else '❌ 失败 ' + str(len(fails)) + ' 项: ' + ', '.join(fails)}")
    return 1 if fails else 0


# ───────────────────────── 入口 ─────────────────────────

def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    if argv[0] == "--selftest":
        return _selftest()
    cmd, rest = argv[0], [a for a in argv[1:]]
    table = {
        "config": cmd_config, "list": cmd_list, "ls": cmd_list, "start": cmd_start,
        "hint": cmd_hint, "submit": cmd_submit, "close": cmd_close, "addr": cmd_addr,
        "vpn": cmd_vpn, "healthcheck": cmd_healthcheck, "hc": cmd_healthcheck,
        "run-new": cmd_run_new, "run-status": cmd_run_status,
        "sets": cmd_sets, "plat-token": cmd_plat_token, "plat_token": cmd_plat_token,
    }
    if cmd not in table:
        die(f"不认识的子命令：{cmd}（--help 看用法）")
    try:
        return table[cmd](rest)
    except ApiError as e:
        hint = ERR_HINT.get(e.code, "")
        print(f"✗ {e}", file=sys.stderr)
        if hint:
            print(f"  → {hint}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
