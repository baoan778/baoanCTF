#!/usr/bin/env python3
"""reflect.py -- 失败后自动反思：是缺工具，还是反复用同一种方法？

为什么要有（2026-11-14 用户要求）：
  「你要有反思机制，为什么好长时间没有解出来——是缺少什么工具，还是老用一种方法没换？」
  只靠提示词说「要反思」没用，模型会随口说「已尝试多种方法」。
  这里做**基于证据的判定**：
    1. 从会话里抽所有 bash 命令 -> 归类攻击面，数一数到底试了几类；
    2. 从工具输出里找「缺工具」信号（ImportError / command not found）；
    3. 给结论：MISSING_TOOL / SAME_METHOD / MULTI_METHOD。
  结论写进 work/solve/reflections/<code>.md，下一轮提示词会带上。

用法: python3 tools/reflect.py <code>
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOLVE = ROOT / "work" / "solve"
REFL = SOLVE / "reflections"

SURFACES = {
    "SQLi": r"union\s+select|or\s+1=1|sqlmap|sleep\(|benchmark\(",
    "认证": r"login|password|passwd|brute-login|admin:|token|jwt",
    "路径泄露": r"\.git|\.env|robots\.txt|backup|\.swp|server-status|\.map",
    "上传": r"multipart|upload|filename=|boundary",
    "SSTI": r"\{\{|jinja|ssti|render_template",
    "命令注入": r";id|\|id\b|whoami|;cat |\$\(",
    "路径穿越": r"\.\./|%2e%2e|/etc/passwd|file=",
    "IDOR": r"user_id|uid=|/orders/|/user/",
    "XSS": r"<script|javascript:|onerror=|img src=",
    "反序列化": r"pickle|yaml\.load|__reduce__|ObjectInputStream",
    "源码泄露": r"\.js\.map|view-source|\.zip|\.tar|/_next/",
}

# ★ 2026-11-14 修：原来含裸 "not found"，把 HTTP 404 页面也判成缺工具（088/089 误判）。
#   只认明确的「命令/模块不存在」形态。
# ★ 2026-11-14 二次收紧：只认「明确缺模块/缺命令」，不认裸 Traceback。
#   实测 089 的 `File "<string>", line 1, in <module>` 只是脚本语法错，不该判成缺工具。
# ★ 2026-11-19：Python 2 专属、pip 装不上的模块 —— 对它们 "去装包" 是死路，
#   必须直接给 py3 等价物。实测 xben-051 就因为 urllib2 被建议去 ensure-tool.sh。
_PY2_ONLY = {
    "urllib2", "urlparse", "cookielib", "httplib", "commands",
    "StringIO", "cStringIO", "ConfigParser", "Queue", "Tkinter",
    "thread", "dummy_thread", "htmlentitydefs", "HTMLParser",
    "xmlrpclib", "SimpleHTTPServer", "SocketServer", "copy_reg",
}

MISSING_TOOL = re.compile(
    r"ModuleNotFoundError: No module named|"
    r"(?:bash|sh|zsh): (?:line \d+: )?[a-zA-Z0-9_.-]+: command not found|"
    r"is not recognized as an internal or external command"
)

# ★ 2026-11-24：输入过滤器把可用空间封死时，多少次拒绝才算「封死」。
#   3 的依据：一次是试错（可能只是 payload 写法不对），两次是换写法再撞，
#   三次以上说明**规则本身**把空间收窄了，再换 payload 极难绕开。
#   取 3 而不是 2，是为了不让正常试错被判成「题坏了」——误判的代价是
#   放弃一道可解题（R 系列踩过：把可解题标废），比晚一点发现更贵。
FILTER_HIT_MIN = 3

# ★ 2026-11-24：光是「被拒了 N 次」不足以说明题坏了 —— 实测 004/008/013 只封了
#   script/onerror/alert 几个**关键字**，换载体就能过，那是正常考点。
#   真封死的特征是「**极小输入**也被拒」（087 拒单字符 S 与 >，010 拒单引号），
#   说明文法是死的、不是黑名单。所以要求「至少 2 次极小输入被拒」。
#   取 2 而不是 1：单次可能是探测脚本写法问题（比如把 payload 截断了）。
FILTER_TINY_MIN = 2


def latest_session(code: str):
    d = SOLVE / code
    files = sorted(d.glob("*.jsonl"), key=lambda f: f.stat().st_mtime)
    return files[-1] if files else None


# 从拒绝提示里抽出「被拒的那个值」：只认被引号包住的那一小段
_DENIAL_VALUE = re.compile(
    r"""Sorry you can(?:'t|not) use:\s*.{1,2}?([^"']{0,40})["']|forbidden characters?[.:]?\s*["']?([^"'\s]{0,40})|invalid characters?[.:]?\s*["']?([^"'\s]{0,40})""",
    re.I,
)
def _is_tiny_denial(line: str) -> bool:
    """这行拒绝提示里被拒的那个值是「极小输入」吗（语法死掉的证据）。

    判据：从提示里抽出被引号包住的那个值，满足任一即是极小 ——
      · 长度 <= 2（如 S、>、单引号、..）
      · 不含任何字母数字（纯符号，如 <>、{{、%00）
    反例（**不算**极小，属正常黑名单）：script / onerror / alert / svg。
    """
    if not line:
        return False
    m = _DENIAL_VALUE.search(line)
    if not m:
        return False
    val = next((g for g in m.groups() if g is not None), "")
    if len(val) <= 2:
        return True
    return not any(c.isalnum() for c in val)


def _line_at(text: str, pos: int, cap: int = 160) -> str:
    """取 pos 所在的**整行**（不是 ±N 字符切片的首行）。

    ★ 2026-11-19 修：旧写法 `t[max(0,i-40):i+120].strip().splitlines()[0]` 在 i 不落在行首时，
        切片的"首行"是**上一行的尾巴**，于是反思纸条给 agent 的证据全是乱码，实测出现过
        「1, in <module>」「forever preferred_lft forever」「to server」这种片段 ——
        既误导模型，也让人工复核时无法定位是哪个模块/命令缺失。
    """
    start = text.rfind(chr(10), 0, pos) + 1
    end = text.find(chr(10), pos)
    if end < 0:
        end = len(text)
    return text[start:end].strip()[:cap]


def _really_missing(evidence: str, timeout: int = 15) -> bool:
    """现场验证「证据行里提到的模块/命令」是不是真的还缺。

    返回 True = 真缺（保留为 MISSING_TOOL 证据）；False = 已经能用了（陈旧信号，丢弃）。

    ★ 2026-11-19 新增。动机见 analyze() 里的注释：环境修复后旧会话里的报错会**永久留在**
      会话文件里，只看历史就会一直判缺工具。这里把判断改成「当下事实」。

    判定规则：
      · 命中 `No module named 'x.y'` → 用当前解释器真 import x，成功即视为不缺
        （子模块名 a.b 只测顶层 a —— 顶层能进来才是关键）。
      · 命中 `x: command not found` → 用 shutil.which(x) 看命令在不在。
      · 解析不出具体名字 → 保守返回 True（宁可多报，不可漏掉真缺）。
      · 任何异常（超时/解释器崩） → 保守返回 True。
    """
    mods = [m.split(".")[0] for m in re.findall(r"No module named '?([A-Za-z0-9_.]+)'?", evidence)]
    names = list(dict.fromkeys(mods))
    for m in names:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", m):
            continue
        try:
            rc = subprocess.run([sys.executable, "-c", "import " + m],
                                capture_output=True, timeout=timeout).returncode
        except Exception:
            return True
        if rc == 0:
            continue          # 这个模块如今能 import → 陈旧信号
        return True           # 有任一模块仍缺 → 是真缺
    if names:
        return False          # 全部模块都能 import
    cmds = re.findall(r"([A-Za-z0-9_.+-]+): command not found", evidence)
    for c in dict.fromkeys(cmds):
        if shutil.which(c):
            continue
        return True
    if cmds:
        return False
    return True               # 解析不出具体对象 → 保守


def analyze(code: str) -> dict:
    f = latest_session(code)
    if not f:
        return {"code": code, "error": "无会话"}
    cmds = []
    outs = []
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"code": code, "error": str(exc)}
    for ln in text.splitlines():
        try:
            r = json.loads(ln)
        except Exception:
            continue
        msg = r.get("message") or {}
        role = msg.get("role")
        # ★ 2026-11-14 修：toolResult 是 **message 的 role**，不是 content 的 type。
        #   原来按 content.type 判断，导致 outs 永远为空，BROKEN_CHALLENGE 永远判不出来。
        if role == "toolResult":
            for p2 in msg.get("content") or []:
                if isinstance(p2, dict) and p2.get("type") == "text":
                    t = p2.get("text") or ""
                    if t.strip():
                        outs.append(t)
            continue
        for part in msg.get("content") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "toolCall":
                a = part.get("arguments") or {}
                c = a.get("command") or a.get("cmd") or ""
                if c:
                    cmds.append(str(c))
    # ★ 2026-11-19 修：计数口径从「关键词出现次数」改成「**碰过这条攻击面的命令条数**」。
    #   旧写法 len(re.findall(pat, blob)) 是在全部命令拼成的长文本里数关键词：
    #     实测 xben-025 只有 30 次工具调用，却报「认证x106」—— 因为一条命令里出现
    #     `token`/`login` 各算一次，长 payload 能刷出上百。
    #   给 agent 看「认证x106」只会让它以为已经试了 106 轮，实际只试了十来轮，**直接误导决策**
    #   （它据此认为这条路已经穷尽，转而去换面）。
    #   新口径：每条命令最多给该面 +1 → 「认证x12」= 12 条命令碰过认证面，这才是可据以判断的数。
    #   注意：只改计数**不改判定** —— verdict 用的是 len(covered)（面数），面集合不变，故无回归。
    covered = {}
    for name, pat in SURFACES.items():
        n = sum(1 for c in cmds if re.search(pat, c, re.I))
        if n:
            covered[name] = n
    # ★ 2026-11-14 修：outs 现在收全部工具输出（为了 BROKEN_CHALLENGE），
    #   so missing 必须**只从命中 MISSING_TOOL 的输出里取**，否则每条页面都能被当成缺工具。
    # ★ 2026-11-19 修：**判缺工具前先现场验证一遍**，不要相信历史会话里的报错。
    #   根因：MISSING_TOOL 看的是「上一轮会话里出现过 ModuleNotFoundError」，但环境可能
    #     早就被修好了 —— .pth 桥接落地后 requests/PIL/jinja2 全都能 import，可是旧会话
    #     里那句报错还在，于是 reflect 继续判「缺工具」，把已可用的库当成缺失，
    #     甚至引导 agent 去重装（ensure-tool.sh 白跑）。
    #   做法：把命中的模块名/命令名**当场 import / command -v 一次**，只有真的缺才算。
    raw_missing = []
    for t in outs:
        hit = MISSING_TOOL.search(t)
        if not hit:
            continue
        line = _line_at(t, hit.start())
        if line and line not in raw_missing:
            raw_missing.append(line)
    missing = [ln for ln in raw_missing if _really_missing(ln)]
    # ★ 2026-11-19 修（P0 实测）：**ISE 不是题库故障，是攻击信号** —— 已从判据里剔除。
    #   旧判据含 "Internal Server Error"，于是 xben-063 的 SSTI 探测把应用打出 500，
    #   被判 BROKEN_CHALLENGE，render() 直接告诉 agent「本题标废，不再重试」
    #   → 它当场放弃了一道可解题。三次 A/B 实测（tools/_ab_broken.py / _ab2.py）：
    #     ① 去掉 ISE 后，84 个有会话的题里 10 题判定翻转：
    #        006/022/023/026/027/063/075/076/098/101；
    #     ② ISE>=2 的 10 题里，「真连接层错误>=2」= 0 个；ISE>=2 且已通关的 = 0
    #        → ISE 与「题坏了」毫无相关性；
    #     ③ 余下判据（getaddrinfo/Connection refused/SQLSTATE/mysqli/database is locked）
    #        全库没有任何一题命中 >=2 —— 也就是说旧判据的「保护作用」100% 来自 ISE，纯误伤。
    #   新判据 = 双条件：① 连接层故障 >=2 ② **整份会话没有任何一次成功 HTTP 响应**。
    #     应用被 payload 打崩也会返回大量 200/403/302；真故障则是从头到尾连不上。
    fatal_pat = re.compile(
        r"getaddrinfo failed|Connection refused|ECONNREFUSED|"
        r"SQLSTATE\[HY000\]|mysqli::__construct\(\)|database is locked|"
        r"could not connect|couldn't connect to server|Connection timed out",
        re.I,
    )
    # 「应用活着」的硬证据：任何一个 HTTP 状态行都算
    alive_pat = re.compile(r"HTTP/\d(?:\.\d)?\s+(?:[12345]\d\d)\b")
    fatal_hits = sum(1 for t in outs if fatal_pat.search(t))
    alive_hits = sum(1 for t in outs if alive_pat.search(t))
    fatal_lines = [_line_at(t, fatal_pat.search(t).start())
                   for t in outs if fatal_pat.search(t)]
    # ★★ 2026-11-23 新增（xben-010 手工取证）：**语义级不可解**。
    #   010 的过滤器把全部 26 个字母 + 数字 + < > 都封了（逐字符探测实证），
    #   而任何 XSS payload 都需要字母 —— 它跑了 6 轮、提交 3 个不同候选全被平台判错。
    #   这是"题坏了"，不是"模型不行"，但旧判据只认连接层故障，认不出来。
    #   判据：输出里**反复出现输入过滤器拒绝提示**，且累计 >= 3 次
    #   （一次可能是试错，三次以上说明规则把可用空间封死了）。
    #   ★ 只做"提示人工核"，不直接标废 —— 因为有些题故意用黑名单当考点
    #     （要的是绕过），贸然标废会像 R？-? 那次"把可解题标废"一样误伤。
    filter_pat = re.compile(
        r"Sorry you can(?:not|'t) use|"
        r"invalid characters?|"
        r"blacklist|forbidden characters?|"
        r"blocked by.*(?:filter|waf)",
        re.I,
    )
    # ★★ 2026-11-24 真实数据回放后重写（104 题全量跑过一遍，见下方判据来历）。
    #   旧版只数「命中了多少次拒绝提示」>=3 就判 FILTER_BLOCKED —— 实测 **7 题命中，
    #   其中 4 题是误判**：
    #     · 004/008/013：只封了 script / onerror / alert 这几个**具体关键字**，
    #       换载体（不带这些词）就能过 → 这是**正常考点**，不是题坏了；
    #     · 087：连单字符 S 与 > 都拒；010：连单引号都拒 → 这才是真把空间封死。
    #   区别不在「被拒了几次」，而在**被拒的东西有多小**：
    #     单个可见 ASCII 字符被拒 = 文法是死的；被拒的是一整个关键字 = 只是黑名单。
    #   所以判据改成两条同时成立：① 拒绝次数 >= 3 ② **至少 2 次是「极小输入」被拒**
    #   （拒绝提示里引号包着的那个值，长度 <= 2，或本身不是字母数字串）。
    #   这样 004/008/013 归 MULTI_METHOD（继续换载体），只有 010/087 类才升 FILTER_BLOCKED。
    filter_hits = sum(1 for t in outs if filter_pat.search(t))
    filter_lines = [_line_at(t, filter_pat.search(t).start())
                    for t in outs if filter_pat.search(t)]
    tiny_hits = sum(1 for t in outs if filter_pat.search(t)
                    and _is_tiny_denial(_line_at(t, filter_pat.search(t).start())))
    filter_lines_tiny = [_line_at(t, filter_pat.search(t).start())
                         for t in outs if filter_pat.search(t)
                         and _is_tiny_denial(_line_at(t, filter_pat.search(t).start()))]
    broken = fatal_hits >= 2 and alive_hits == 0
    # ★ 2026-11-24：FILTER_BLOCKED 是**独立判定**，不是 BROKEN_CHALLENGE 的子类。
    #   两者证据相反：BROKEN 是「连一次成功的 HTTP 都没有」，FILTER 是
    #   「应用活得好好的，只是把输入拒了」。混在一起会让 render() 给出
    #   「先 curl 看靶场活没活」这种**文不对题**的下一步 —— 靶场显然活着。
    #   也不放进 SAME_METHOD/MULTI_METHOD 的 if-else 链末尾：它是「换面」信号的
    #   上一层（面已经换不动了），必须排在它们**之前**才有意义。
    # 两条同时成立才算「封死」：次数够 + 有极小输入被拒（见上面 2026-11-24 的说明）
    filter_blocked = (filter_hits >= FILTER_HIT_MIN
                      and tiny_hits >= FILTER_TINY_MIN
                      and not broken)
    if broken:
        verdict = "BROKEN_CHALLENGE"
    elif missing:
        verdict = "MISSING_TOOL"
    elif filter_blocked:
        verdict = "FILTER_BLOCKED"
    elif len(covered) <= 2:
        verdict = "SAME_METHOD"
    else:
        verdict = "MULTI_METHOD"
    return {
        "code": code,
        "session": f.name,
        "commands": len(cmds),
        "surfaces": covered,
        "missing_tool": (fatal_lines[:3] + missing)[:4] if broken else missing[:4],
        "filter_hits": filter_hits,
        "filter_tiny": tiny_hits,
        "filter_lines": (filter_lines_tiny or filter_lines)[:3],
        "verdict": verdict,
    }


def render(info: dict) -> str:
    if info.get("error"):
        return "# " + info["code"] + chr(10) + chr(10) + "反思：" + str(info["error"]) + chr(10)
    v = info["verdict"]
    surf = "、".join(k + "x" + str(n) for k, n in info["surfaces"].items())
    out = []
    out.append("# " + info["code"] + " 反思（基于 " + info["session"] + "）")
    out.append("")
    out.append("- 工具调用：" + str(info["commands"]) + " 次")
    out.append("- 覆盖攻击面 " + str(len(info["surfaces"])) + " 类：" + (surf or "（无）"))
    out.append("")
    if v == "BROKEN_CHALLENGE":
        out.append("## 判定：疑似连接层故障（**先自己验一次，别急着标废**）")
        out.append("整份会话里**没有任何一次成功的 HTTP 响应**，且反复出现连接层错误：")
        for m in info["missing_tool"][:3]:
            out.append("- " + m)
        out.append("")
        out.append("**下一步（必做，一条命令）**：确认靶场到底活没活 ——")
        out.append("    curl -s -o /dev/null -w '%{http_code}' -m 5 http://<host>:<port>/")
        out.append("- 拿到 200/301/403 → 靶场活着，**继续打**：那是探测姿势的问题，不是题坏了；")
        out.append("- 只拿到 000 / Connection refused / 超时 → 才真的标废，把时间让给其它题。")
    elif v == "MISSING_TOOL":
        out.append("## 判定：缺工具（已当场复验过，确实还没有）")
        out.append("上次失败的直接原因是环境缺东西：")
        for m in info["missing_tool"]:
            out.append("- " + m)
        out.append("")
        # ★ 2026-11-19：区分「装得上的」与「装不上的」，别再让 agent 去装 Python 2 时代的模块。
        mods = re.findall(r"No module named '?([A-Za-z0-9_.]+)'?", " ".join(info["missing_tool"]))
        py2 = [m for m in dict.fromkeys(mods) if m in _PY2_ONLY]
        if py2:
            out.append("**⚠ 这几个是 Python 2 时代的东西，pip 装不上（别浪费时间）**：" + "、".join(py2))
            out.append("- `urllib2` → 用 `urllib.request`（py3 等价物；`from urllib.request import urlopen, Request`）")
            out.append("- `urlparse` → `urllib.parse`；`cookielib` → `http.cookiejar`；`httplib` → `http.client`")
            out.append("- `commands` → `subprocess`；`StringIO` → `io.StringIO`")
            out.append("  （如果是在**跑一段别人给的 py2 脚本**，别改 import，直接 `2to3 -w <脚本>` 一次转完）")
            out.append("")
        if mods and not py2:
            out.append("**下一步**：先补齐再打 —— `bash /opt/pi-ctf/tools/ensure-tool.sh " + " ".join(dict.fromkeys(mods)) + "`，")
            out.append("或改用纯标准库替代（Flask session 用 `tools/flask-session.py`）。补齐后重跑同一思路。")
        else:
            out.append("**下一步**：按上面的等价物改写后重跑同一思路（不用装包）。")
    elif v == "FILTER_BLOCKED":
        out.append("## 判定：输入过滤器把可用空间封死了（疑似题坏了，先人工核）")
        out.append("应用活着（有过成功的 HTTP 响应），但已累计 "
                   + str(info.get("filter_hits", 0)) + " 次「输入被拒」：")
        for m in (info.get("filter_lines") or [])[:3]:
            out.append("- " + m)
        out.append("")
        out.append("**这条判定的边界（读之前先看）**：")
        out.append("- 有些题故意把黑名单当考点，要的是绕过 —— 那种题会命中这里但是能解的。")
        out.append("- 所以我们不标废，只提醒你：把时间预算降下来，别再无限换 payload。")
        out.append("")
        out.append("**下一步（按顺序，做满三条再判）**：")
        out.append("1. 花 1 分钟做字符级探测：逐个送 A-Z a-z 0-9 与尖括号引号反引号，看哪些被拒，")
        out.append("   把允许的字符集写进 facts —— 这是「封死」还是「只封一部分」的唯一实证。")
        out.append("2. 若全部字母都被拒：确认是否要求非文本载体（JSON 转义 / Unicode / 数字型参数 /")
        out.append("   头部注入 / multipart 文件名），再试一次不同载体而不是不同 payload。")
        out.append("3. 若确实连载体都封死 → 记 refuted 并换题，把剩余时间给别的题。")
    elif v == "SAME_METHOD":
        out.append("## 判定：反复用同一种方法（没换攻击面）")
        out.append("只覆盖 " + str(len(info["surfaces"])) + " 类攻击面；同一类里改参数不算换方法。")
        out.append("")
        out.append("**时机**：这是**事后**判定（回看上次会话的工具输出）；事前自查见对应 SKILL 首屏的"
                   "「硬禁止表」—— 同一条命令失败 ≥2 次就该换策略，别等这张反思纸条。")
        out.append("")
        out.append("**下一步**：必须换攻击面大类，优先试还没碰过的：")
        out.append("- 源码/配置泄露：`/.git/HEAD`、`/.env`、`/*.js.map`、`/backup.zip`")
        out.append("- 未授权接口：目录扫描后直接打 `/api/*`")
        out.append("- 上传/模板注入/反序列化：换载荷类型，不是换参数")
        out.append("- 换身份：注册新用户、改 id、改 role 字段")
    else:
        out.append("## 判定：方法覆盖够了，卡在**利用细节**")
        out.append("已试：" + "、".join(info["surfaces"].keys()) + "。入口大概对了，卡在构造/绕过。")
        out.append("")
        out.append("**下一步**：别再扩面，回到响应差异上：")
        out.append("- 每个尝试存完整响应做对比（状态码/长度/Set-Cookie/报错原文）")
        out.append("- 找绕过点：大小写、编码、双写、注释截断、`/**/`、`%00`、二次编码")
        out.append("- 有源码映射先读源码，凭据/过滤逻辑常在里面")
    return chr(10).join(out) + chr(10)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    code = sys.argv[1]
    info = analyze(code)
    text = render(info)
    REFL.mkdir(parents=True, exist_ok=True)
    (REFL / (code + ".md")).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
