#!/usr/bin/env python3
"""lane_gate —— 长任务 / 危险命令的**事前**闸门。

与 `lane_guard` 的分工（两个都要，别混）：
  · `lane_guard`  = **事后**看门狗：扫正在跑的 lane，报 NO_NOTES / STUCK / OVER_BOX / NEAR_DL / ORPHAN
  · `lane_gate`   = **事前**拦截：命令跑之前就判，**拒了必须给改写模板**（不是只说「不许」）

来源与血账
----------
2026-10-11 移植自 inwpu/hxbai 的 `longtask_guard.py`（Tsecbench v1 榜一，同款 deepseek-v4-flash），
逐条保留它的**实测理由**（原文理由照抄，不美化）：

  R1 长任务管道接 tail/head
     「管道缓冲会吞掉全部输出，SIGTERM 时结果全丢（实测：一次定向爆破结果因此一份不剩）」
  R2 爆破 / 大扫描 / 长超时**前台**跑
     「会话随时被掐，前台长任务 = 白干」（本仓同类血账：45 分钟 / 31.8 万 token 零产物）
  R3 宽窗口 `grep -o '.{0,N≥50}'`
     「-o 每个位置都要试，大文件/超长单行上线性慢（实测 120s）」
  R4 裸 `grep/awk/sed` 无文件无管道
     「它在等 stdin，会一直阻塞 / 无输出」
  R5 裸 `&` 起长驻中继（socat / nc -l / http.server）
     「session 结束时被 SIGHUP 杀死（实测 69 次重建 / 0 次存活检查）」

另加两条本仓口径（不是抄的，是本仓已有纪律的机器化）：
  R6 不可逆破坏：`rm -rf` 系统目录 / `mkfs` / `dd of=/dev/*`
     —— 靶机实例是唯一立足点，「想要的状态拿不到就换只读思路，不是毁现场」
  R7 网段级扫描（`/16` `/24` …）：慢、越界、可能把**别题的实例**当成本题
     —— 与 `fgs scope` 是同一件事的两面（scope 管授权，本闸门管「会打错目标」）

设计取向（重要）
----------------
**给正路，不只给禁令。** 每条 deny 都带**可直接粘的改写命令**；另提供
`lane_gate bg <名字> <命令…>` 作为「一键正确路径」——把正确做法做成比错误做法更省事，
才可能真被用上（而不是又一条没人读的纪律文字）。

用法
----
    lane_gate check '<命令>'          # 判一条命令：exit 0 = 放行，exit 10 = 拒绝（并打印改写模板）
    lane_gate check '<命令>' --json   # 机器可读
    lane_gate bg hydra-ssh hydra -l root -P rockyou.txt ssh://10.0.0.1
                                      # 正路：tmux 后台 + 落盘 + 打印轮询命令
    lane_gate audit <日志或会话文件>   # 事后合规审计：扫已发生的违规（用于收口抽查）
    lane_gate --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
#   自包含（自己 import sys），不依赖本文件是否已 import sys 或用别名。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ── 读取护栏（纪律：外部给的路径只认普通文件 + 限长；不许 Path(...).read_text()）────
READ_CAP_DEFAULT = int(os.environ.get("PI_LANE_GATE_MAX_READ_BYTES", 8 * 1024 * 1024))


def read_capped(path, what="文件") -> str:
    """只认普通文件 + 限长；否则拒读（/dev/zero、FIFO 会把整台 VM 读爆）。"""
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"[lane_gate] {what}不存在：{p}")
    if p.is_dir():
        raise SystemExit(f"[lane_gate] {what}是目录，不是文件：{p}")
    if not p.is_file():
        raise SystemExit(f"[lane_gate] {what}不是普通文件（设备/FIFO/socket）：{p} —— 拒绝读")
    size = p.stat().st_size
    if size > READ_CAP_DEFAULT:
        raise SystemExit(f"[lane_gate] {what}太大：{size} B > 上限 {READ_CAP_DEFAULT} B（{p}）；"
                         f"先 head/grep 裁剪，或调大 PI_LANE_GATE_MAX_READ_BYTES")
    with p.open("r", encoding="utf-8", errors="replace") as fh:
        return fh.read(READ_CAP_DEFAULT + 1)


# ── 模式表 ──────────────────────────────────────────────────────────────────
# 生产型工具（会长时间跑、输出巨大）：本机有的 + 常被装的，取超集（多认几个无害）
# 注：Python 3.14 起不允许表达式**中段**出现 `(?i)`（global flags not at the start）
#     → 统一用编译期 flags=re.I，别写内联 (?i)。
_I = re.I

PRODUCER = re.compile(
    r"(?:^|[\s;|&(])/?(?:\S*/)?(?:hydra|medusa|john|hashcat|nuclei|ffuf|feroxbuster|"
    r"gobuster|dirb|dirsearch|nikto|masscan|wpscan|wfuzz|patator|crowbar|dalfox|commix|"
    r"sqlmap|katana|gau|subfinder|amass)\b"
    r"|\b\w*(?:spray|brute|crack)\w*\b|authspray", _I)
RECON_HEAVY = re.compile(r"(?:^|[\s;|&(])/?(?:\S*/)?(?:nmap|naabu|rustscan|zmap|netscan)\b", _I)
NMAP_HEAVY = re.compile(r"-p-|--allports|-p\s*1-65535|--script\s|(?:^|\s)-A(?:\s|$)|-T[45]\b", _I)
SQLMAP_HEAVY = re.compile(
    r"--dump(?:-all)?\b|--tables\b|--columns\b|--os-shell\b|--level\s*[3-5]\b|--risk\s*[2-3]\b|--threads", _I)
# 网段扫描：/8~/24 视为越界（/25~/32 是单机/小段，放行）
# BIGSCAN 与它是**同一判据**（防止「单机 /32」被当成大扫描误伤）—— 保持别名，别两处各写一份。
CIDR_WIDE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){2,3}/(?:[0-9]|1[0-9]|2[0-4])\b", _I)
BIGSCAN = CIDR_WIDE   # 别名
TIMEOUT = re.compile(r"\btimeout\s+(\d+)")
BG = re.compile(r"\b(?:tmux|nohup|setsid|systemd-run|screen)\b|\d?>\s*/dev/null\s*2>&1\s*&|&\s*$", _I)
PIPE_TAIL = re.compile(r"\|\s*(?:\/\w+\/)?(?:tail|head|less|more)\b", _I)
READER = re.compile(
    r"^\s*(?:tail|head|cat|less|more|grep|egrep|rg|awk|sed|wc|nl|strings|find|ls|ps|"
    r"tmux\s+ls|sort|uniq|jq|cut|tr)\b(?!\s*[>+])", _I)
WIDE_OE = re.compile(r"\b(?:grep|egrep)\b[^;|&]*\s-o[^;|&]*\.\{0,(?:[5-9]\d|\d{3,})\}", _I)
HAS_MAXCOUNT = re.compile(r"(?:^|\s)(?:-[a-zA-Z]*m(?:\s|\d|$)|--max-count)(?:\s|\d|$)")
BARE_STDIN = re.compile(r"^\s*(?:(?:grep|egrep)|(?:awk|sed))\s+(?:-[a-zA-Z]+\s+)*(?:'[^']*'|\"[^\"]*\"|\S+)\s*$", _I)
LONG_LIVE = re.compile(
    r"\b(?:socat\s+TCP-LISTEN|nc\s+-{0,2}l(?:p|isten)?\b|ncat\s+-{0,2}l\b|"
    r"python3?\s+-m\s+http\.server|php\s+-S\b|chisel\s+server|frpc?\b|sshd\b)", _I)
DESTRUCTIVE = re.compile(
    r"\brm\s+(?:-\w*\s+)*-\w*(?:rf|fr)\w*\s+(?:/|/\*|/etc|/usr|/var|/boot|/lib|/bin|/sbin|\$\{?HOME\}?)(?:\s|$)"
    r"|\bmkfs(?:\.\w+)?\b|\bdd\b[^;|&]*\bof=/dev/\w+|\bfdisk\b|\bparted\b|:\(\)\s*\{\s*:\|:&\s*\};:"
    r"|\bshutdown\b|\breboot\b|\bpoweroff\b|\binit\s+0\b", _I)



def _strip_pipe_tail(cmd: str) -> str:
    """R1 的改写建议不能把 `| tail` 也包进去 —— 那正是要拆掉的东西。

    例：`hydra ... | tail -20` → `hydra ...`
    （否则会给出「后台跑一条带 | tail 的命令」这种坏建议，等于没改。）
    """
    m = PIPE_TAIL.search(cmd)
    return cmd[: m.start()].strip() if m else cmd.strip()


def _bg_hint(orig: str, name: str = "<名字>") -> str:
    return (f"  tmux new-session -d -s {name} '{orig} >> /tmp/{name}.log 2>&1'\n"
            f"  # 然后轮询进度（别重启同任务）：\n"
            f"  tail -5 /tmp/{name}.log\n"
            f"  # 或者直接用本工具一键：\n"
            f"  lane_gate bg {name} {orig}")


RULES: list[dict] = [
    dict(id="R6", deny=True, rx=DESTRUCTIVE,
         why="不可逆破坏：靶机实例是你唯一的立足点，杀了它=本题归零还搭上一轮重启",
         fix="想要的状态拿不到就换**只读**思路（读配置 / 读进程 / 读 socket / 读数据库），不是毁现场。\n"
             "  · 要在容器里验证「删掉会怎样」→ 先 `cp -a` 一份或另起实例，别动原实例\n"
             "  · 真要清理，只清你自己在 /tmp 或 work/ 下建的文件"),
    dict(id="R7", deny=True, rx=CIDR_WIDE,
         why="网段级扫描：慢、越界、可能把**别题的实例**当成本题（实测把别题靶机当目标）",
         fix="改单主机定向探测；重建实例后按**服务指纹**重定位（端口 / 标题 / Set-Cookie / 横幅），"
             "脚本一律 `$TARGET` 参数化。\n"
             "  · 例：`nmap -sT -p1-65535 $TARGET`（而不是 nmap 10.0.0.0/24）\n"
             "  · 授权面见 `fgs scope <pid>`"),
    dict(id="R4", deny=True, rx=BARE_STDIN,
         why="裸 grep/awk/sed：没有文件参数也没有管道输入 —— 它在等 stdin，会一直阻塞 / 无输出",
         fix="补上文件参数，或结尾加 `</dev/null`，或接进管道：\n"
             "  echo \"$X\" | {cmd}"),
    dict(id="R3", deny=True, rx=WIDE_OE, negate=HAS_MAXCOUNT,
         why="宽窗口 `grep -o '.{0,N}'`（N≥50）：-o 在每个位置都要试，大文件/超长单行上线性慢（实测 120s）",
         fix="加命中上限 `-m 20`、把窗口降到 `.{0,30}` 以内、或先 `wc -c` 确认规模：\n"
             "  grep -o -m 20 -E '.{0,30}flag.{0,30}' <file>\n"
             "  # 超大单行用 python 逐块读，别交给 grep"),
    dict(id="R5", deny=True, rx=LONG_LIVE, negate=BG,
         why="裸 `&` 起长驻服务/中继：session 结束时被 SIGHUP 杀死"
             "（其实测：69 次重建 / 0 次存活检查）",
         fix="用 setsid 脱离会话进程组，并在起之前**先检查是否已经活着**：\n"
             "  nc -z <host> <port> && echo RELAY_OK || \\\n"
             "  bash -c 'setsid nohup socat TCP-LISTEN:2222,reuseaddr,fork TCP:<目标>:22 "
             ">/dev/null 2>&1 &'\n"
             "  # 或：lane_gate bg relay socat TCP-LISTEN:2222,reuseaddr,fork TCP:<目标>:22"),
    dict(id="R1", deny=True, rx=PIPE_TAIL, negate=None,
         why="长任务的管道接进 tail/head —— 管道缓冲会吞掉全部输出，SIGTERM 时结果全丢"
             "（实测：一次定向爆破结果因此一份不剩）",
         fix="改成「后台 + 落盘」，再轮询取进度：\n" + _bg_hint("{cmd}")),
    dict(id="R2", deny=True, rx=None,  # 由 decide() 组合判定（见下）
         why="爆破 / 大扫描 / 长超时任务**前台**跑 —— 会话随时被掐，前台长任务 = 白干",
         fix="一律后台 + 落盘：\n" + _bg_hint("{cmd}")),
]

_PRODUCER_IDS = {"R1", "R2"}


def decide(command: str) -> dict:
    """判一条命令。返回 {decision: allow|deny, rule, why, fix}（fix 里的 {cmd} 已替换）。"""
    cmd = (command or "").strip()
    if not cmd:
        return dict(decision="allow", rule="", why="", fix="")

    def out(r: dict) -> dict:
        fix = (r["fix"] or "").replace("{cmd}", _strip_pipe_tail(cmd) if r["id"] == "R1" else cmd)
        return dict(decision="deny", rule=r["id"], why=r["why"], fix=fix)

    # 读者的只读命令先放行（否则 `tail -5 log` 会被 R2 误伤）
    is_reader = bool(READER.match(cmd))

    # R6 / R7 / R4 / R3 / R5：模式类
    for r in RULES:
        if r["rx"] is None or not r["rx"].search(cmd):
            continue
        if r.get("negate") is not None and r["negate"].search(cmd):
            continue
        if r["id"] in _PRODUCER_IDS and is_reader:
            continue
        return out(r)

    # R2：组合判定（生产工具 / 重侦察 / 长超时）
    if not is_reader and not BG.search(cmd):
        has_producer = bool(PRODUCER.search(cmd) or BIGSCAN.search(cmd))
        if RECON_HEAVY.search(cmd):
            has_producer = has_producer or bool(NMAP_HEAVY.search(cmd))
        if PRODUCER.search(cmd) and SQLMAP_HEAVY.search(cmd):
            has_producer = True
        m = TIMEOUT.search(cmd)
        secs = int(m.group(1)) if m else 0
        if re.search(r"(?:--help|-h\b|\?\s*$)", cmd):
            has_producer = False
        if has_producer and (secs == 0 or secs >= 30):
            return out(next(r for r in RULES if r["id"] == "R2"))
        if not has_producer and secs >= 120:
            return out(next(r for r in RULES if r["id"] == "R2"))
    return dict(decision="allow", rule="", why="", fix="")


# ── 子命令 ──────────────────────────────────────────────────────────────────
def cmd_check(a) -> int:
    res = decide(" ".join(a.command) if isinstance(a.command, list) else a.command)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif res["decision"] == "deny":
        # ★ 必须自报「本机」：CTF 里「拒绝」会被读成「靶机有防御」→ 换 payload 重试 = 纯烧时间。
        print(f"✗ 【本机管控·非靶机防御】拒绝 [{res['rule']}] {res['why']}")
        print("  （这是**本机命令护栏**在拦你，不是靶机有 WAF/防护 —— 换 payload 重试不会变）")
        print(f"\n改写：\n{res['fix']}")
    else:
        print("✓ 放行")
    return 0 if res["decision"] == "allow" else 10


def cmd_bg(a) -> int:
    """正路：把长命令丢后台 + 落盘 + 打印轮询命令。"""
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", a.name)[:40] or "lane"
    log = f"/tmp/{name}.log"
    cmd = " ".join(a.command)
    if not shutil.which("tmux"):
        print(f"[lane_gate] 本机没有 tmux —— 退回 setsid nohup：\n"
              f"  setsid nohup bash -c '{cmd}' >> {log} 2>&1 &", file=sys.stderr)
        subprocess.Popen(["setsid", "nohup", "bash", "-lc", f"{cmd} >> {log} 2>&1"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    else:
        # 已有同名 session 就不重复起（防「69 次重建 / 0 次存活检查」重演）
        have = subprocess.run(["tmux", "has-session", "-t", name], timeout=15,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        if have:
            print(f"[lane_gate] session `{name}` 已经在跑 —— 不重启（就地续力）。")
        else:
            subprocess.run(["tmux", "new-session", "-d", "-s", name,
                            f"{cmd} >> {log} 2>&1"], timeout=20, check=False)
            print(f"[lane_gate] 已后台起：{name}")
    print(f"  日志：{log}")
    print(f"  看进度：tail -5 {log}")
    print(f"  看进程：tmux ls   （或 ps -ef | grep -F '{name}'）")
    return 0


# audit：事后合规审计（收口抽查用）——只回答「这条 lane 有没有犯这几类」
AUDIT_SIGS = [
    ("R1", PIPE_TAIL, "管道接 tail/head（结果可能全丢）"),
    ("R3", WIDE_OE, "宽窗口 grep -o（已知慢）"),
    ("R4", BARE_STDIN, "裸 grep/awk/sed（等 stdin 阻塞）"),
    ("R5", LONG_LIVE, "起长驻服务未 setsid（会被 SIGHUP）"),
    ("R6", DESTRUCTIVE, "不可逆破坏类命令"),
    ("R7", CIDR_WIDE, "网段级扫描"),
]


def cmd_audit(a) -> int:
    text = read_capped(a.path, "待审文件")
    lines = text.splitlines()
    hits: list[tuple[int, str, str]] = []
    for n, line in enumerate(lines, 1):
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("//"):
            continue
        for rid, rx, label in AUDIT_SIGS:
            if rx.search(s):
                hits.append((n, rid, label))
                break
        else:
            # R2 单独走 decide()，避免重复发明判定
            r = decide(s)
            if r["decision"] == "deny" and r["rule"] == "R2":
                hits.append((n, "R2", "前台长任务"))
    if not hits:
        print(f"✓ {a.path}：没有命中 {len(AUDIT_SIGS) + 1} 类违规模式")
        return 0
    print(f"⚠ {a.path}：{len(hits)} 处命中（只报行号+类别，不贴原文以免二次污染上下文）")
    for n, rid, label in hits[: a.limit]:
        print(f"  L{n:<6} [{rid}] {label}")
    if len(hits) > a.limit:
        print(f"  …另有 {len(hits) - a.limit} 处，`--limit` 调大或 grep 定位")
    return 0


def _selftest() -> int:
    checks: list[tuple[str, bool, str]] = []

    def ok(name: str, cond: bool, extra: str = "") -> None:
        checks.append((name, bool(cond), extra))

    def d(cmd: str) -> str:
        return decide(cmd)["rule"]

    # 该拒的
    ok("R1 爆破管道接 tail", d("hydra -l root -P p.txt ssh://10.0.0.1 | tail -20") == "R1")
    ok("R1 大扫描接 head", d("nmap -p- 10.0.0.1 | head -5") == "R1" or d("nmap -p- 10.0.0.1 | head -5") in ("R2",))
    ok("R2 前台 hydra", d("hydra -l root -P rockyou.txt ssh://10.0.0.1") == "R2")
    ok("R2 前台 masscan", d("masscan -p1-65535 10.0.0.1 --rate 10000") == "R2")
    ok("R2 sqlmap --dump", d("sqlmap -u http://x/?id=1 --dump-all") == "R2")
    ok("R2 长 timeout", d("timeout 300 python3 brute.py") == "R2")
    ok("R2 nmap 重扫描", d("nmap -p- -A 10.0.0.1") == "R2")
    ok("R3 宽窗口 grep -o", d("grep -o -E '.{0,80}flag.{0,80}' big.txt") == "R3")
    ok("R4 裸 grep", d("grep 'flag'") == "R4")
    ok("R5 裸 socat 中继", d("socat TCP-LISTEN:2222,reuseaddr,fork TCP:10.0.0.2:22") == "R5")
    ok("R6 rm -rf /", d("rm -rf /") == "R6")
    ok("R6 dd of=/dev/sda", d("dd if=/dev/zero of=/dev/sda bs=1M") == "R6")
    ok("R7 /24 扫描", d("nmap 10.0.0.0/24") == "R7")

    # 该放的（防误伤 —— 误伤比漏报更致命：会把好习惯一起禁掉）
    ok("放行 只看日志", d("tail -5 /tmp/hydra.log") == "")
    ok("放行 带限长 grep", d("grep -o -m 20 -E '.{0,80}flag' big.txt") == "")
    ok("放行 窄窗口 grep", d("grep -o -E '.{0,20}flag' a.txt") == "")
    ok("放行 有文件的 grep", decide("grep flag a.txt")["decision"] == "allow")
    ok("放行 后台爆破", d("tmux new-session -d -s h 'hydra -l r -P p.txt ssh://10.0.0.1'") == "")
    ok("放行 setsid 中继", d("setsid nohup socat TCP-LISTEN:2222,reuseaddr,fork TCP:10.0.0.2:22") == "")
    ok("放行 短 timeout", d("timeout 20 curl -s http://10.0.0.1/") == "")
    ok("放行 单机 nmap", d("nmap -sT -p80,443 10.0.0.1") == "")
    ok("放行 --help", decide("hydra --help")["decision"] == "allow")
    ok("放行 /32 不算网段越界", d("masscan 10.0.0.1/32 -p1-100") != "R7")   # 但 masscan 前台仍按 R2 拒
    ok("放行 空命令", decide("")["decision"] == "allow")
    # 边界：/25 之后的段算单机
    ok("放行 /30 小段", d("nmap 10.0.0.0/30") == "")

    # 每条 deny 必须带改写模板（给正路，不只给禁令）
    for r in RULES:
        ok(f"{r['id']} 有改写模板", bool((r["fix"] or "").strip()), r["id"])

    # 退出码语义
    ok("返回结构含 decision", decide("rm -rf /")["decision"] == "deny")

    passed = sum(1 for _, c, _ in checks if c)
    for name, c, extra in checks:
        print(f"  {'✓' if c else '✗'} {name}" + (f"   [{extra}]" if extra and not c else ""))
    print(f"\n{passed}/{len(checks)} 断言通过")
    return 0 if passed == len(checks) else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="lane_gate", description="长任务/危险命令的事前闸门")
    p.add_argument("--selftest", action="store_true", help="跑内置断言")
    sub = p.add_subparsers(dest="cmd")

    c = sub.add_parser("check", help="判一条命令：exit 0 放行 / exit 10 拒绝")
    c.add_argument("command", nargs="+")
    c.add_argument("--json", action="store_true")

    b = sub.add_parser("bg", help="正路：后台 + 落盘 + 打印轮询命令")
    b.add_argument("name")
    b.add_argument("command", nargs="+")

    au = sub.add_parser("audit", help="事后合规审计（扫日志/会话里的违规模式）")
    au.add_argument("path")
    au.add_argument("--limit", type=int, default=30)

    a = p.parse_args(argv)
    if a.selftest:
        return _selftest()
    if a.cmd == "check":
        return cmd_check(a)
    if a.cmd == "bg":
        return cmd_bg(a)
    if a.cmd == "audit":
        return cmd_audit(a)
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
