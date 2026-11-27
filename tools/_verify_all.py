# -*- coding: utf-8 -*-
"""功能级验证：每一条修复都要有「改前会错 / 改后正确」的对照证据。

★ 2026-11-19 跨环境改造：原来写死 `sys.path.insert(0, "/opt/pi-ctf/tools")` 与
`/usr/bin/python3`，**只能在容器里跑**；被 tools/run_all_tests.sh 接进常驻回归后，
在宿主上跑必然 FileNotFoundError，看起来像产品坏了 —— 其实只是前提不成立。
现在：
  · 仓库根按本文件位置推导（宿主 / 容器都对）；
  · A/A2 两组（验 .pth 让系统 python3 看见 venv 库）只有在「/usr/bin/python3 真的存在」
    时才有意义 —— 宿主没有这个路径，明确打 SKIP 并计入 skip，不冒充 PASS 也不报 FAIL。
"""
import io, json, os, re, sys, tempfile, shutil, subprocess, pathlib

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sdkshard, reflect

# 这两组只在容器（有系统 python3 + .pth 桥）里有意义
SYS_PY3 = "/usr/bin/python3"
HAS_SYS_PY3 = os.path.exists(SYS_PY3)
# procscan 全部判据读 /proc —— Windows 宿主没有这个目录，相关断言必须 SKIP 不是 FAIL
HAS_PROC = os.path.isdir("/proc/self")
HAS_PROC_OS = os.path.isdir("/proc")
SKIP = 0


def skip(name, why):
    global SKIP
    SKIP += 1
    print("  SKIP  " + name + " — " + why)

PASS = FAIL = 0
def chk(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  PASS  " + name + ((" — " + detail) if detail else ""))
    else:
        FAIL += 1
        print("  FAIL  " + name + ((" — " + detail) if detail else ""))

# ─────────────────────────────────────────────────────────────
print("=== A. python3 能否 import venv 库 ===")
if not HAS_SYS_PY3:
    skip("A 组", "本机没有 %s（宿主/Windows），这条只在容器里成立" % SYS_PY3)
for m in ([] if not HAS_SYS_PY3 else ["requests", "PIL", "numpy", "jinja2", "bs4", "lxml", "pwn", "z3", "Crypto", "cv2"]):
    rc = subprocess.run([SYS_PY3, "-c", "import " + m], capture_output=True).returncode
    chk("python3 -c 'import %s'" % m, rc == 0)

print("=== A2. 标准库未被遮盖（这是 .pth 方案的唯一风险）===")
out = (subprocess.run([SYS_PY3, "-c",
    "import json,re,os,subprocess,pathlib,http,socket,ssl,hashlib,email,xml;"
    "import sys;"
    "mods=[json,re,os,subprocess,pathlib,http,socket,ssl,hashlib,email,xml];"
    "print(chr(10).join(m.__name__+'='+(getattr(m,'__file__','') or '') for m in mods))"
], capture_output=True, text=True).stdout) if HAS_SYS_PY3 else ""
if HAS_SYS_PY3:
    bad = [ln for ln in out.splitlines() if "tools/venv" in ln]
    chk("标准库全部来自 /usr/lib", not bad, "被遮盖: " + str(bad) if bad else "11 个模块全部 OK")
else:
    skip("A2 标准库未被遮盖", "同上：没有 %s" % SYS_PY3)

# ─────────────────────────────────────────────────────────────
print()
print("=== B. reflect: ISE 不再被判题库故障 ===")
# 造一份「把应用打出 500」的假会话，旧判据会 BROKEN，新判据不该
tmp = pathlib.Path(tempfile.mkdtemp())
sessdir = tmp / "xben-fake-01"
sessdir.mkdir()
lines = []
def add(role, content):
    lines.append(json.dumps({"message": {"role": role, "content": content}}, ensure_ascii=False))
add("toolCall", [{"type": "toolCall", "arguments": {"command": "curl 'http://t/?q={{7*7}}'"}}])
add("toolResult", [{"type": "text", "text": "HTTP/1.1 500 Internal Server Error\n<title>500 Internal Server Error</title>"}])
add("toolCall", [{"type": "toolCall", "arguments": {"command": "curl 'http://t/?q=\';--'"}}])
add("toolResult", [{"type": "text", "text": "HTTP/1.1 500 Internal Server Error"}] * 1)
add("toolCall", [{"type": "toolCall", "arguments": {"command": "curl 'http://t/'"}}])
add("toolResult", [{"type": "text", "text": "HTTP/1.1 200 OK"}])
(sessdir / "s.jsonl").write_text(chr(10).join(lines), encoding="utf-8")

old_solve = reflect.SOLVE
reflect.SOLVE = tmp
info = reflect.analyze("xben-fake-01")
reflect.SOLVE = old_solve
chk("打崩应用(500) 不判 BROKEN_CHALLENGE", info.get("verdict") != "BROKEN_CHALLENGE",
    "verdict=" + str(info.get("verdict")))

# 真故障（连不上 + 无 HTTP 响应）应仍判 BROKEN
sessdir2 = tmp / "xben-fake-02"
sessdir2.mkdir()
l2 = []
l2.append(json.dumps({"message": {"role": "toolCall", "content": [{"type": "toolCall", "arguments": {"command": "curl http://t/"}}]}}))
l2.append(json.dumps({"message": {"role": "toolResult", "content": [{"type": "text", "text": "curl: (7) Failed to connect: Connection refused"}]}}))
l2.append(json.dumps({"message": {"role": "toolCall", "content": [{"type": "toolCall", "arguments": {"command": "curl http://t/"}}]}}))
l2.append(json.dumps({"message": {"role": "toolResult", "content": [{"type": "text", "text": "getaddrinfo failed: Name or service not known"}]}}))
(sessdir2 / "s.jsonl").write_text(chr(10).join(l2), encoding="utf-8")
reflect.SOLVE = tmp
info2 = reflect.analyze("xben-fake-02")
reflect.SOLVE = old_solve
chk("真连不上 仍判 BROKEN_CHALLENGE", info2.get("verdict") == "BROKEN_CHALLENGE",
    "verdict=" + str(info2.get("verdict")))

# ─────────────────────────────────────────────────────────────
print()
print("=== C. reflect: 证据行是整行，不是切片 ===")
_line = "ModuleNotFoundError: No module named 'definitely_not_a_module_xyz'"
_multi = "第一行无关内容" + chr(10) + "prefix junk " + _line + " suffix" + chr(10) + "第三行也无关"
_ev = reflect._line_at(_multi, _multi.index("ModuleNotFoundError"))
chk("_line_at 从多行文本取命中所在整行", _ev == "prefix junk " + _line + " suffix", "got=" + repr(_ev))
chk("证据行不含其它行的内容", "第一行" not in _ev and "第三行" not in _ev)
_single = "单行文本 " + _line
chk("_line_at 单行文本也能取全", reflect._line_at(_single, 5) == _single)

print()
print("=== C2. 陈旧信号不复报（库已能 import 就不算缺）===")
chk("已可用的 requests 判为不缺", reflect._really_missing("ModuleNotFoundError: No module named 'requests'") is False)
chk("真缺的假模块判为缺", reflect._really_missing("ModuleNotFoundError: No module named 'zzz_nope_zzz'") is True)

# ─────────────────────────────────────────────────────────────
print()
print("=== D. py2 模块给出等价物而不是「去装」 ===")
sessdir3 = tmp / "xben-fake-03"
sessdir3.mkdir()
l3 = [
    json.dumps({"message": {"role": "toolCall", "content": [{"type": "toolCall", "arguments": {"command": "python3 x.py"}}]}}),
    json.dumps({"message": {"role": "toolResult", "content": [{"type": "text", "text": "ModuleNotFoundError: No module named 'urllib2'"}]}}),
]
(sessdir3 / "s.jsonl").write_text(chr(10).join(l3), encoding="utf-8")
reflect.SOLVE = tmp
info3 = reflect.analyze("xben-fake-03")
txt3 = reflect.render(info3)
reflect.SOLVE = old_solve
chk("urllib2 判 MISSING_TOOL", info3.get("verdict") == "MISSING_TOOL")
chk("纸条给出 urllib.request 等价物", "urllib.request" in txt3)
chk("纸条不再让去 ensure-tool.sh 装 urllib2", "ensure-tool.sh urllib2" not in txt3)

# ─────────────────────────────────────────────────────────────
print()
print("=== E. 攻击面计数按命令条数，不按关键词次数 ===")
sessdir4 = tmp / "xben-fake-04"
sessdir4.mkdir()
l4 = []
for i in range(3):
    l4.append(json.dumps({"message": {"role": "toolCall", "content": [{"type": "toolCall", "arguments": {"command": "curl -b 'login=1;token=a;password=b;admin:x;login=2' http://t/%d" % i}}]}}))
(sessdir4 / "s.jsonl").write_text(chr(10).join(l4), encoding="utf-8")
reflect.SOLVE = tmp
info4 = reflect.analyze("xben-fake-04")
reflect.SOLVE = old_solve
n = info4["surfaces"].get("认证", 0)
chk("3 条命令含大量认证关键词 → 计数=3（不是 15）", n == 3, "got=" + str(n))

# ─────────────────────────────────────────────────────────────
print()
print("=== F. sdkshard: 污染会话识别 + 隔离必须让 pi 看不见 ===")
qd = tmp / "q" / "xben-fake-05"
qd.mkdir(parents=True)
clean = qd / "2026-01-01T00-00-00-000Z_clean.jsonl"
clean.write_text(json.dumps({"message": {"role": "system", "sections": {"tools": "<tools>\n- bash: run\n- read: read\n</tools>"}}}) + chr(10), encoding="utf-8")
chk("干净会话不判污染", sdkshard.session_poisoned(qd) is False)
chk("干净会话不判 no_tools", sdkshard.session_no_tools(clean) is False)

dirty = qd / "2026-01-02T00-00-00-000Z_dirty.jsonl"
dirty.write_text(json.dumps({"message": {"role": "system", "sections": {"tools": "<tools>\n(none)\n</tools>"}}}) + chr(10), encoding="utf-8")
# ★ newest_session() 按 mtime 取最新；Windows 上两次 write_text 可能落在同一
#   mtime 刻度内，排序退化成目录顺序 → 会挑到 clean 而误判「没被污染」。
#   显式把时间戳拉开，让这条断言测的是判据本身而不是文件系统的时钟分辨率。
import time as _t
_t.sleep(0.02)
os.utime(clean, (1000000000, 1000000000))
os.utime(dirty, (1000000000 + 10, 1000000000 + 10))
chk("无工具会话判 no_tools", sdkshard.session_no_tools(dirty) is True)
chk("目录被判污染（最新会话无 bash）", sdkshard.session_poisoned(qd) is True)

nq = sdkshard.quarantine_poisoned(qd)
chk("隔离执行", nq >= 1, "隔离 %d 个" % nq)
left = [p.name for p in qd.glob("*.jsonl")]
chk("★ 中毒会话已从 pi 的 glob 范围消失", all("dirty" not in n for n in left), "剩余: " + str(left))
chk("干净会话被保留（可继承侦察结果）", any("clean" in n for n in left), "剩余: " + str(left))
qfiles = list((qd.parent / "_quarantine" / "xben-fake-05").glob("*.jsonl"))
chk("隔离文件被完整保留（取证）", len(qfiles) >= 1, "保留 %d 个" % len(qfiles))
chk("隔离后不再判污染", sdkshard.session_poisoned(qd) is False)

# ─────────────────────────────────────────────────────────────
print()
print("=== G. sdkshard: facts 噪声过滤 ===")
for s in ["bash: Tool bash not found",
          "连续 3 次调用 bash 均返回 Tool bash not found",
          "INFRA_BLOCKED 无法继续",
          "工具通道不可用"]:
    chk("噪声被过滤: " + s[:24], sdkshard.is_fact_noise(s) is True)
for s in ["GET /login 返回 200，标题 Login - Trading",
          "secret=abc123456 出现在 /app.py",
          "/admin/dashboard 存在，回应 403",
          # ★ 2026-11-20 R2-B：INFRA_BLOCKED 的靶场结论必须保留（原来被当噪声整条丢掉，
          #   xben-081 因此把「DB 永久 NXDOMAIN + 其余路线全证伪」烧了第二遍）。
          "INFRA_BLOCKED: DB 主机 NXDOMAIN，flag 只可能在不可达的库里"]:
    chk("真事实被保留: " + s[:24], sdkshard.is_fact_noise(s) is False)

# ─────────────────────────────────────────────────────────────
print()
print("=== H. 收尾轮 fork 到独立目录（不污染原会话）===")
sd = tmp / "h" / "xben-fake-06"
sd.mkdir(parents=True)
src = sd / "2026-01-03T00-00-00-000Z_x.jsonl"
src.write_text("{}\n", encoding="utf-8")
args = sdkshard.wrapup_args(sd, "deepseek-flash")
chk("wrapup 用 --fork", args and "--fork" in args, str(args[:3]) if args else "None")
chk("wrapup session-dir 是独立目录", args and str(sdkshard.wrapup_dir(sd)) in args,
    str(sdkshard.wrapup_dir(sd)))
chk("wrapup 带 --no-tools", args and "--no-tools" in args)
chk("wrapup 目录不等于原 sessdir", sdkshard.wrapup_dir(sd) != sd)
chk("无会话时 wrapup_args 返回 None", sdkshard.wrapup_args(tmp / "nope", "m") is None)

# ─────────────────────────────────────────────────────────────
print()
print("=== I. system prompt 瘦身===")
sysmd = sdkshard.solver_system(None)
# AGENTS.md 关掉后必须靠 SOLVER_SYSTEM 保住的 4 条纪律
for kw, why in [("注入信号", "注入防护"), ("ensure-tool.sh", "缺工具自装"),
                ("大输出不许整读", "输出限读"), ("换攻击面", "换面纪律")]:
    chk("SOLVER_SYSTEM 含「%s」（%s）" % (kw, why), kw in sysmd)
chk("SOLVER_SYSTEM 仍含 FOUND_FLAG 约定", "FOUND_FLAG" in sysmd)
chk("SOLVER_SYSTEM 仍含逐字取证", "逐字" in sysmd)

# 瘦身开关确实落在 pi 参数上（源码层断言，防止「改了但没接线」）
src = (sdkshard.__file__ and open(sdkshard.__file__, encoding="utf-8").read()) or ""
chk("solve_one 里有 --no-context-files", '"--no-context-files"' in src)
chk("solve_one 里有 --no-skills", '"--no-skills"' in src)
chk("瘦身可被 PI_SOLVE_SLIM=0 关掉", 'PI_SOLVE_SLIM' in src)

# ─────────────────────────────────────────────────────────────
print()
print("=== J. 容器降权后可写性===")
_hf = HERE / "home-fix.sh"
chk("tools/home-fix.sh 存在且可执行", _hf.exists() and os.access(str(_hf), os.X_OK), str(_hf))
if _hf.exists():
    _hs = open(_hf, encoding="utf-8", errors="replace").read()
    chk("home-fix 白名单含 .sage（sage 就是被它卡死的）", ".sage" in _hs)
    chk("home-fix 用 find ! -user 精确修（不是 chown -R 全量扫）",
        "! -user" in _hs and "chown -R" not in _hs)
    chk("home-fix 支持 --check（只报告不改）", "--check" in _hs)
    # 真跑一次 --check：退出码必须是 0 或 1，不许崩。
    # ★ 别把绝对路径喂给 MSYS bash：Windows 侧 "D:\..." 里 \b 会被当退格，"/d/..." 也不认
    #   （实测 rc=127 No such file）。**切到脚本目录用 basename 调**最稳，两边一致。
    _r = subprocess.run(["bash", "home-fix.sh", "--check"], cwd=str(HERE),
                        capture_output=True, text=True, timeout=60)
    chk("home-fix --check 能正常跑完（rc 0/1，不崩）", _r.returncode in (0, 1),
        "rc=%d %s" % (_r.returncode, (_r.stdout or _r.stderr).strip()[:120]))
# 接线：entrypoint 必须真的调用它（防止脚本写完没人用）
#
# ★ 2026-11-19：容器里的 /opt/pi-ctf/docker/ **不是挂载**，是镜像内副本
#   （只有 tools/ board/ AGENTS.md agent-template 是 9p rw）。所以在容器里读到的
#   entrypoint.sh 永远是"构建镜像那一刻"的版本，改宿主仓库不会同步过来 ——
#   这不是接线漏了，是**看不到真实文件**。这种情况必须 SKIP，不能报 FAIL
#   （报 FAIL 会误导成"改完没接线"）。
_ep = HERE.parent / "docker" / "entrypoint.sh"


def _docker_dir_is_mounted() -> bool:
    try:
        for ln in open("/proc/mounts", encoding="utf-8", errors="replace"):
            if " /opt/pi-ctf/docker " in ln:
                return True
    except OSError:
        pass
    return False


# 判据：在容器里 (/opt/...) 且 docker/ 不是挂载点 → 读到的 entrypoint 是镜像内副本，看不到宿主改动
_sees_guest_copy = str(HERE).startswith("/opt/") and not _docker_dir_is_mounted()
if not _ep.exists():
    skip("entrypoint 接线检查", "找不到 docker/entrypoint.sh")
elif _sees_guest_copy:
    skip("entrypoint 接线检查", "容器内 docker/ 是镜像副本（非挂载），看不到宿主改动；请在宿主跑这条")
else:
    _eps = open(_ep, encoding="utf-8", errors="replace").read()
    chk("entrypoint 在 root 阶段调用了 home-fix.sh", "home-fix.sh" in _eps)
    chk("home-fix 调用发生在 setpriv 降权之前",
        _eps.find("home-fix.sh") < _eps.find("exec setpriv") or _eps.find("exec setpriv") < 0)
# ruff 不许再因缓存不可写而误判
_sc1 = (HERE / "bin" / "selfcheck")
if _sc1.exists():
    _s1 = open(_sc1, encoding="utf-8", errors="replace").read()
    chk("selfcheck 调 ruff 时加了 --no-cache（缓存写不了不许报成代码有问题）", "--no-cache" in _s1)
    chk("selfcheck 有 HOME 缓存属主守卫", "home-fix.sh" in _s1)

# ─────────────────────────────────────────────────────────────
print()
print("=== K. 某轮优化：预算与 LPT 队列排序（并发容量利用率）===")
import importlib.util as _ilu  # noqa: E402
_pspec = _ilu.spec_from_file_location("_psolver", str(HERE / "parallel_solver.py"))
_ps = _ilu.module_from_spec(_pspec)
_pspec.loader.exec_module(_ps)
# 基线必须与 cap 匹配：cap < 基线×2 会让 3 步递增（1.5^n）被卡死，改了基线等于没改
chk("DIFF_BUDGET 是 150/300/480（干净轮截断模拟 73/75 旗）",
    _ps.DIFF_BUDGET == {"easy": 150, "medium": 300, "hard": 480}, str(_ps.DIFF_BUDGET))


class _FakeL:
    def avg_sec(self, c):
        return {"slow": 300.0, "fast": 10.0}.get(c, 0.0)


_ord = _ps.lpt_order(["fast", "slow", "fresh_hard", "fresh_easy"], _FakeL(),
                     {"slow": "hard", "fast": "easy", "fresh_hard": "hard", "fresh_easy": "easy"})
# 历史 300s 最前；未跑过的 hard（先验 240s）次之；历史 10s 最后
chk("lpt_order 把历史最耗时的题排最前（LPT）",
    _ord[0] == "slow" and _ord[-1] == "fast", str(_ord))
chk("lpt_order 对没跑过的题用难度先验（hard 先于 easy）",
    _ord.index("fresh_hard") < _ord.index("fresh_easy"), str(_ord))
chk("lpt_order 不丢题（重排而非过滤）",
    sorted(_ord) == sorted(["fast", "slow", "fresh_hard", "fresh_easy"]), str(_ord))
chk("lpt_order 对空列表安全", _ps.lpt_order([], None, {}) == [])
# ledger 不可用时不能抛（否则整轮队列写入失败 → 退回静态切桶 → 排序白改）
chk("lpt_order 在 ledger 为 None 时不抛异常", isinstance(_ps.lpt_order(["a", "b"], None, {}), list))

# supervise 每轮题数：**保持 24**。模拟证明收益几乎全部来自 LPT，
# 轮大小只微调 LPT 的发挥空间（12→2359s、24→2338s、48→2330s、不限→2328s）；
# 改 12 反而慢 21s。这条守住「别为了某个直觉去改一个已经被测过的参数」。
_sup = (HERE / "supervise.py").read_text(encoding="utf-8", errors="replace")
chk("supervise 每轮题数仍是 24（改 12 实测更慢 21s，已回退）",
    'SUP_PER_ROUND", "24"' in _sup)
# 队列写入必须真的用 LPT 顺序，而不是「算完就扔」
_psrc = (HERE / "parallel_solver.py").read_text(encoding="utf-8", errors="replace")
chk("队列写入用的是 LPT 顺序（不是算完就扔）",
    "_q_order = lpt_order(" in _psrc and "_wb.get(c, c) for c in _q_order" in _psrc)

# ─────────────────────────────────────────────────────────────
print()
print("=== L. 某轮优化：砍掉解题用不到的扩展工具（system prompt 第一大成本）===")
_sd = _ilu.spec_from_file_location("_sdkshard", str(HERE / "sdkshard.py"))
_sdm = _ilu.module_from_spec(_sd)
_sd.loader.exec_module(_sdm)
# 默认必须真的包含这四个（少一个就等于没砍）
_ex = _sdm.solver_exclude()
for _bad in ("subagent", "subagent_supervisor", "bg_wait", "source_check"):
    chk("默认排除清单含 %s" % _bad, _bad in _ex.split(","), _ex)
# 必须**保留** obs_recall/web 三件套：它们是「出事时唯一的救命工具」
# （大输出被换出后回捞不了 / 没法查资料），省那点 KB 不值得冒这个险。
for _keep in ("obs_recall", "fetch_content", "get_search_content", "web_search"):
    chk("默认**不**排除 %s（保留救命工具）" % _keep, _keep not in _ex.split(","), _ex)
# ★ 2026-11-21（R5-C）判据变更：triage 从「必须保留」改为「默认排除」。
#   理由（实测，不是偏好）：历史会话 / 2350 次工具调用里 triage **0 次**；
#   sdkshard 跑的是 TSecBench 远程靶场，题面没有附件，triage 的“附件入场分流”
#   能力在这条链路上用不上，而它的 schema 每轮随 system prompt 重发。
#   风险控制：① 只影响 sdkshard 这条跑分链路，本地 CTF 走常规 pi（全套工具）；
#   ② PI_SOLVE_EXCLUDE=0 / 显式改环境变量随时可加回来做对照。
chk("默认排除 triage（跑分链路无附件；可 PI_SOLVE_EXCLUDE 覆盖）",
    "triage" in _ex.split(","), _ex)
# bash/read/write 绝不能被误砍（P0 那课：参数能连带把 bash 摘掉）。
#   edit 不在此列：2350 次调用里 0 次，且 bash(python/sed) 完全可替代（R5-C）。
chk("默认排除清单不含 bash/read/write", not any(
    k in _ex.split(",") for k in ("bash", "read", "write")), _ex)
_src_sd = (HERE / "sdkshard.py").read_text(encoding="utf-8", errors="replace")
chk("solve_one 真的把 --exclude-tools 拼进 pi 参数",
    '"--exclude-tools", _ex' in _src_sd)
chk("有 PI_SOLVE_EXCLUDE=0 逃生门（可关掉做对照）",
    'PI_SOLVE_EXCLUDE' in _src_sd and '"0"' in _src_sd)
# 说明文档必须写清「为什么是这四个」——否则下一个人会凭直觉加回去
chk("排除理由与 A/B 数据写进了源码注释", "A/B" in _src_sd and "33.7KB" in _src_sd)

shutil.rmtree(tmp, ignore_errors=True)

# ─────────────────────────────────────────────────────────────
print()
print("=== M. 某轮优化：进程识别统一 + 预算策略单一事实来源 ===")
_procs = HERE / "procscan.py"
chk("tools/procscan.py 存在（进程识别的唯一实现）", _procs.is_file())
sys.path.insert(0, str(HERE))
import procscan  # noqa: E402
import importlib.util as _ilu  # noqa: E402

# ① argv 语义：命令行里「提到」不算，真的被解释器执行才算
_decoy = subprocess.Popen(["bash", "-c", "sleep 6 # pp_not_real.py"])
import time as _t  # noqa: E402
_t.sleep(0.4)
_hit = procscan.python_script_pids("pp_not_real.py")
chk("procscan 不被 bash 命令行里的文件名骗到", _decoy.pid not in _hit, str(_hit))
_decoy.kill(); _decoy.wait()

# ② 真进程必须能被找到（否则守护永远以为没在跑，会重复拉起）
_tdir = pathlib.Path(tempfile.mkdtemp())
(_tdir / "pp_target.py").write_text("import time\ntime.sleep(15)\n")
_real = subprocess.Popen([sys.executable, "pp_target.py"], cwd=str(_tdir))
_t.sleep(0.8)
_hit2 = procscan.python_script_pids("pp_target.py") if HAS_PROC else []
if not HAS_PROC:
    skip("procscan 能找到真的在跑的 python 脚本", "本机无 /proc（Windows 宿主），procscan 只在容器里成立")
else:
    chk("procscan 能找到真的在跑的 python 脚本", _real.pid in _hit2, str(_hit2))
_real.kill(); _real.wait()
shutil.rmtree(_tdir, ignore_errors=True)

# ③ CLI 契约：空结果必须 exit 1（早一版无条件 exit 0 → autostart 永远说「已在运行」）
_rc = subprocess.run([sys.executable, str(_procs), "--script", "pp_definitely_absent.py"],
                     capture_output=True, text=True).returncode
chk("procscan --script 无命中时退出码非 0（shell 判据才成立）", _rc != 0, "rc=%d" % _rc)

# ④ ★ 这里查**行为**而不是源码文本。
#   第一版查文本（`"if pattern in args" not in 源码`）连自己写的注释/docstring 一起命中，
#   报了两个假 FAIL —— 与 2026-11-19 那次「断言写错当产品 bug」是同一类错：
#   断言要盯着**可观察行为**，不要盯着「源码里有没有某个字符串」。
_tdir2 = pathlib.Path(tempfile.mkdtemp())
(_tdir2 / "sup_decoy.py").write_text("import time\ntime.sleep(10)\n")
_dec = subprocess.Popen(["bash", "-c", "sleep 10 # sup_decoy.py"])
_t.sleep(0.4)
_supmod = _ilu.spec_from_file_location("_supmod", str(HERE / "supervise.py"))
_supm = _ilu.module_from_spec(_supmod)
_supmod.loader.exec_module(_supm)
chk("supervise.procs 不被 bash 命令行里的脚本名骗到", _dec.pid not in _supm.procs("sup_decoy.py"),
    str(_supm.procs("sup_decoy.py")))
_dec.kill(); _dec.wait()

(_tdir2 / "sdkshard.py").write_text(
    "import subprocess, time\n"
    "subprocess.Popen(['sleep', '8'])\n"
    "time.sleep(10)\n")
_sh = subprocess.Popen([sys.executable, "sdkshard.py"], cwd=str(_tdir2))
_t.sleep(0.8)
if not HAS_PROC:
    skip("supervise.procs 能找到真的 shard 进程", "同上：本机无 /proc")
else:
    chk("supervise.procs 能找到真的 shard 进程（漏判会导致重复拉起）",
        _sh.pid in _supm.procs("sdkshard.py"), str(_supm.procs("sdkshard.py")))
_sh.kill(); _sh.wait()
shutil.rmtree(_tdir2, ignore_errors=True)

# live.procs 必须能把「解题用的 pi 子进程」数出来 —— 原实现按 /dist/bundle/cli.js 找，
# 而 pi 启动后 argv 被重写成 ["pi"]，那条判据恒为 0（用户看到的「pi 子进程 —」）。
_lvsrc = (HERE / "bench" / "live.py").read_text(encoding="utf-8", errors="replace")
chk("live.py 通过 procscan 找进程（不再解析 ps 文本）",
    "import procscan" in _lvsrc and "procscan.snapshot()" in _lvsrc)
chk("live.py 借 token 也走 procscan（子串匹配会读到错进程）",
    "procscan.python_script_pids(\"sdkshard.py\")" in _lvsrc)
_auto_src = (HERE / "solver-autostart.sh").read_text(encoding="utf-8", errors="replace")
chk("solver-autostart 优先调 procscan", "procscan.py" in _auto_src)

# ⑤ 预算策略单一事实来源：三处副本必须同值（某轮优化 改基线时漏过两处）
sys.path.insert(0, str(HERE / "bench"))
import solve_policy  # noqa: E402
chk("solve_policy 基线是 150/300/480",
    solve_policy.DIFF_BUDGET == {"easy": 150, "medium": 300, "hard": 480},
    str(solve_policy.DIFF_BUDGET))
chk("cap = 基线 ×2（3 步递增不被卡死）",
    solve_policy.cap_for("hard") == 960 and solve_policy.cap_for("easy") == 640,
    "%d/%d" % (solve_policy.cap_for("hard"), solve_policy.cap_for("easy")))
chk("parallel_solver.DIFF_BUDGET 与 solve_policy 同值（不再是第二份副本）",
    _ps.DIFF_BUDGET == solve_policy.DIFF_BUDGET, str(_ps.DIFF_BUDGET))
try:
    import importlib.util as _ilu2
    _lspec = _ilu2.spec_from_file_location("_live", str(HERE / "bench" / "live.py"))
    _lv = _ilu2.module_from_spec(_lspec)
    _lspec.loader.exec_module(_lv)
    _mismatch = [
        (d, t, _lv._budget_for(d, t), solve_policy.budget_for(d, t))
        for d in ("easy", "medium", "hard") for t in range(5)
        if _lv._budget_for(d, t) != solve_policy.budget_for(d, t)
    ]
    chk("live.py 与 solve_policy 的预算逐档逐次一致（实况页数字=真实下发值）",
        not _mismatch, str(_mismatch[:3]))
except Exception as _e:  # noqa: BLE001
    chk("live.py 与 solve_policy 的预算逐档逐次一致（实况页数字=真实下发值）", False,
        type(_e).__name__ + ": " + str(_e)[:80])

_todo_src = (HERE / "bench" / "todo.py").read_text(encoding="utf-8", errors="replace")
# 查「有没有把凭据写进赋值」，不查「源码里是否出现某串」——
# 第一版查「源码里不出现某个旧凭据串」，结果被 docstring 里的说明文字自己命中。
chk("bench/todo.py 没有把 token 写死在代码里（旧 token 恒 409 却说不出原因）",
    not re.search(r'(?m)^\s*(TOK|TOKEN|BENCHMARK_TOKEN)\s*=\s*["\'][^"\']+["\']', _todo_src))
chk("bench/todo.py 预算走 solve_policy",
    "from solve_policy import" in _todo_src and "budget_for(diff, at)" in _todo_src)

# ─────────────────────────────────────────────────────────────
print()
print("=== N. 某轮优化：/proc 读 D 态进程会永久阻塞（把整条链路挂死）===")
# 背景（2026-11-20 实测）：容器里出现一批 D 态残留（ps/find/grep 卡在
# __access_remote_vm / __vma_start_write / proc_map_files_readdir）。
# 读它们的 /proc/<pid>/cmdline 或 /environ 会**永久阻塞**，而 /proc/<pid>/stat 不会。
# 后果链：procscan 挂 → /api/bench 报 "live.py 超时（15s）"（实况页全空，像 UI 坏了）
#         → supervise 巡检卡死（守护等于不存在）→ sweep 清理卡死。
# 修法统一为「先读 stat 看 state，Z/D/X 跳过，再决定要不要读 cmdline/environ」。
# 真 D 态没法按需制造，用假 /proc（PI_LAB_PROC_ROOT）验证判据本身。
_fake = pathlib.Path(tempfile.mkdtemp(prefix="fakeproc-"))


def _mkproc(root, pid, state, argv):
    d = root / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "stat").write_text("%d (prog) %s 1 1 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 100 0 0\n"
                            % (pid, state), encoding="utf-8")
    (d / "comm").write_text("prog\n", encoding="utf-8")
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")


_mkproc(_fake, 100, "D", ["/usr/bin/python3", "/w/dead.py"])   # D 态：不该被读
_mkproc(_fake, 101, "S", ["/usr/bin/python3", "/w/alive.py"])  # 正常：必须被读到
_mkproc(_fake, 102, "Z", ["/usr/bin/python3", "/w/zombie.py"])  # 僵尸：cmdline 本应空
(_fake / "uptime").write_text("1000.00 1.00\n", encoding="utf-8")
_old_root = os.environ.get("PI_LAB_PROC_ROOT")
os.environ["PI_LAB_PROC_ROOT"] = str(_fake)
try:
    chk("状态守卫：能读出 D 态（说明判据读得到 state）",
        procscan._read_state(100) == "D", procscan._read_state(100))
    chk("状态守卫：D 态进程的 argv 被跳过（读 cmdline 会永久阻塞）",
        procscan._read_argv(100) == [], str(procscan._read_argv(100)))
    chk("状态守卫：Z 态进程的 argv 也被跳过", procscan._read_argv(102) == [])
    chk("状态守卫不误伤正常进程（S 态照常读到 argv）",
        procscan._read_argv(101) == ["/usr/bin/python3", "/w/alive.py"],
        str(procscan._read_argv(101)))
    chk("状态守卫下 python_script_pids 只返回活着的那个",
        procscan.python_script_pids("alive.py") == [101]
        and procscan.python_script_pids("dead.py") == [],
        "%s / %s" % (procscan.python_script_pids("alive.py"),
                     procscan.python_script_pids("dead.py")))
finally:
    if _old_root is None:
        os.environ.pop("PI_LAB_PROC_ROOT", None)
    else:
        os.environ["PI_LAB_PROC_ROOT"] = _old_root
shutil.rmtree(_fake, ignore_errors=True)

# 其余四处读 /proc 的地方都必须有同一道守卫（判据抄多份必然漂移 —— 某轮优化 的教训）
chk("live.py 借 token 前先看状态（environ 同样会阻塞）",
    "_alive_state" in _lvsrc and 'in ("Z", "D", "X", "x")' in _lvsrc)
chk("live.py 的状态判据复用 procscan（不另写一份）", "procscan._read_state" in _lvsrc)
_auto2 = _auto_src
chk("solver-autostart 内联兜底也先看状态（否则守护卡死）",
    'in ("Z", "D", "X", "x")' in _auto2)
_reap = (HERE / "reap_orphan_agents.py").read_text(encoding="utf-8", errors="replace")
chk("reap_orphan_agents 读 cmdline 前先看状态", 'in ("Z", "D", "X", "x")' in _reap)
_tsb = (HERE / "tsecbench.py").read_text(encoding="utf-8", errors="replace")
chk("tsecbench 扫 openvpn cmdline 前先看状态",
    'in ("D", "X", "x")' in _tsb)
for _sw in ("bin/sweep", "hand/sweep"):
    _sws = (HERE / _sw).read_text(encoding="utf-8", errors="replace")
    chk("%s 读 cmdline 前先看状态（清残留要遍历一堆 pid）" % _sw,
        "case \"$_st\" in D|X|x)" in _sws)

# ── O 组：某轮优化 的提示词侧（findflag.sh 取代 find / 全盘扫） ────────────────
_ff = HERE / "findflag.sh"
chk("findflag.sh 存在且可执行", _ff.exists() and os.access(str(_ff), os.X_OK))
_ffs = _ff.read_text(encoding="utf-8", errors="replace") if _ff.exists() else ""
chk("findflag.sh 排除了 /proc /sys /dev /run（不进搜索根，而不是靠 -path 过滤）",
    "-path /proc" in _ffs and "-path /sys" in _ffs)
chk("findflag.sh 有硬限时（否则自己也会卡住）", "timeout 20" in _ffs)
chk("findflag.sh 覆盖常见 flag 落点", "/var/www" in _ffs and "/tmp" in _ffs)
chk("findflag.sh 有 --deep 逃生门", "--deep" in _ffs)
# 行为验证：造一个真 flag 文件，必须能找到（只查文本存在会把「写坏了」当通过）
# ★ 两个路径形式：Python 写盘用绝对路径；喂给 MSYS bash 用相对名（Windows 绝对路径会被 find 当成不存在）
_ff_abs = os.path.join(str(HERE), "_ff_probe_tmp")
shutil.rmtree(_ff_abs, ignore_errors=True)
os.makedirs(os.path.join(_ff_abs, "app"), exist_ok=True)
with open(os.path.join(_ff_abs, "app", "flag.txt"), "w", encoding="utf-8") as _fh:
    _fh.write("flag{unit-test-findflag}")
# ★ text=True 在 Windows 上用 cp936 解码：findflag.sh 的中文输出会把读取线程弄崩（stdout 变 None），必须显式 utf-8
_ffr = subprocess.run(["bash", "findflag.sh", "_ff_probe_tmp"], cwd=str(HERE),
                      capture_output=True, text=True, encoding="utf-8",
                      errors="replace", timeout=60)
chk("findflag.sh 真能找到 flag 文件",
    "flag.txt" in (_ffr.stdout or ""), (_ffr.stdout or "")[:200])
shutil.rmtree(os.path.join(str(HERE), "_ff_probe_tmp"), ignore_errors=True)

# 提示词必须把它写死，否则 agent 还是会去 find /
_had_sd = str(HERE) in sys.path
if not _had_sd:
    sys.path.insert(0, str(HERE))
try:
    import sdkshard as _sd2
    _pr = _sd2.build_prompt("xben-t-24", ["10.0.0.1:80"], 1, 300, attempt_no=1)
    chk("提示词指向 findflag.sh", "findflag.sh" in _pr)
    chk("提示词明确禁用 find / 全盘扫",
        "find / -name" in _pr and "\u7edd\u5bf9\u4e0d\u8981" in _pr)
    chk("提示词说明了 /proc 会永久阻塞的理由", "\u6c38\u4e45\u963b\u585e" in _pr)
finally:
    if not _had_sd:
        sys.path.remove(str(HERE))


print()
print("=" * 60)
print("结果：PASS %d / SKIP %d / FAIL %d" % (PASS, SKIP, FAIL))
# ★ 2026-11-19：再打一行**纯 ASCII** 摘要。run_all_tests.sh 原来用 grep '^结果：' 抓这一行，
#   但 Windows 上 python3 的 stdout 是 cp936，中文在管道里变成乱码字节，
#   正则永远匹配不上 → 回归报告里「---- agent 功能回归 ----」下面是空白，
#   即使这一组全红也只会体现成 pass 少 1，看不出是它。ASCII 行与编码无关。
print("RESULT: PASS %d / SKIP %d / FAIL %d" % (PASS, SKIP, FAIL))
sys.exit(1 if FAIL else (77 if SKIP and not PASS else 0))
