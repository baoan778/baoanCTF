#!/usr/bin/env python3
"""CLI 冒烟测试：**每一个子命令都得真跑一遍**。

跑法：
    python3 board/tests/test_cli_smoke.py

为什么要有这个文件（血的教训）
------------------------------
重写 `cmd_snapshot` 那一段时，替换区间跨过了 `_fmt_meta`，把它整段删掉了 ——
`cmd_show` 一调用就 `NameError` 崩溃。

而当时 `test_outbox` / `test_lead` / `test_retrospect` / `test_snapshot` **四条全绿**：
因为它们各自只跑自己那几条命令，**没有任何一条跑过 `fgs show`**。
缺陷就这么活到了下次真用的时候（半小时后 `fgs show ir-manual-index` 直接崩）。

所以这个文件的定位是「**兜底层**」：功能测试盯语义，这里盯「别崩」。
判据很简单却很有效 —— **stderr 里不许出现 Traceback**，退出码必须在允许集合里。

另外它还做**集合相等**校验：`fgs --help` 里列出的子命令，必须全部被这里覆盖。
以后加了新子命令而不加冒烟用例，这个测试会失败（照 ADR 0033 的思路：
「任何增删都必须先改测试」）。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
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

ROOT = Path(__file__).resolve().parents[1]
FGS = ROOT / "fgs.py"
PID = "_test_cli_smoke"

# ★ 隔离：不能把临时项目建到真 board/projects 里。
# 踩过的坑（2026-09-29）：这个文件最初直接在 ROOT/projects 下 init/删除，
# 而 4 路并行跑时 test_handover 刚好在数「真目录里几块黑板」—— 它一会看到 5、
# 一会看到 6，于是 `test_handover` 偶发变红，还被当成「drvfs 负载抖动」记进文档。
# 真因是**测试之间抢真产物**（本仓自己的规矩就写着“测试别碰真产物”）。
TMPDIR = Path(tempfile.mkdtemp(prefix="pilab-cli-smoke-"))
PDIR = TMPDIR / "projects" / PID
# ★ 2026-11-07（X-4）：work/ 也必须隔离。`fgs ledger` 落在 work/<pid>/ledger.md，
#   原来只隔离了 board/projects → 台账写进真 work/，第二次跑「未建台账」前提就不成立（假红），
#   而且真 work/ 里会留下测试残渣。两处都指到临时目录才叫真隔离。
# ★ 2026-11-14（本地跑分 run 47736 实测）：平台凭证也必须隔离。
#   原来只隔离了 board/work —— 容器里配好 BENCHMARK_TOKEN + VPN 之后，
#   `fgs flags-left` 会真的拿到 104 道题单，于是「无平台时必须说不知道」
#   这条断言反被真凭证打成假红（selfcheck 在容器内直接失败）。
#   判据：测试要么完全离网，要么显式注入假凭证，绝不吃宿主真配置。
# ★ 2026-11-14（本地跑分 run 47736 实测）：平台凭证也必须隔离。
#   原来只隔离了 board/work —— 容器里配好 BENCHMARK_TOKEN + VPN 之后，
#   `fgs flags-left` 会真的拿到 104 道题单，于是「无平台时必须说不知道」
#   这条断言反被真凭证打成假红（selfcheck 在容器内直接失败）。
#   ⚠ 光把 PI_LAB_TSEC_CONFIG 指到**不存在的路径**没用：tsec 的候选列表会
#     继续往后找 ~/.config/pi-lab/tsecbench.json 与 <repo>/.pi/tsecbench.json，
#     照样吃到真 token。必须放一个**真实存在但没有凭证**的配置文件把它钉住。
_TSEC_EMPTY = TMPDIR / "tsecbench-empty.json"
_TSEC_EMPTY.write_text("{}\n", encoding="utf-8")
ENV = dict(os.environ,
           PI_LAB_BOARD_DIR=str(TMPDIR / "projects"),
           PI_LAB_WORK_DIR=str(TMPDIR / "work"),
           PI_LAB_TSEC_CONFIG=str(_TSEC_EMPTY))
for _k in ("TSEC_TOKEN", "BENCHMARK_TOKEN", "TSEC_BASE_URL", "BENCHMARK_BASE_URL"):
    ENV.pop(_k, None)
# ★ 2026-11-17（#38）：工具留痕检索要有个**假留痕文件**可读，否则 `fgs trace`
#   只能走「文件不存在 → rc=2」这一条分支，等于没冒烟到主路径。
TRACEF = TMPDIR / "tool-trace.jsonl"
TRACEF.write_text(json.dumps({
    "ts": "2026-11-17T09:00:00+08:00", "tool": "bash", "cmd": "echo SMOKE_TRACE",
    "is_error": False, "out_len": 11, "omitted": 0, "out": "SMOKE_TRACE",
    "pid": PID, "session": "smoke"}, ensure_ascii=False) + "\n", encoding="utf-8")
ENV["PI_FGS_TRACE_PATH"] = str(TRACEF)

passed, failed = [], []
CALLED = set()


def run(*args, ok=(0,)):
    """跑一条命令；返回 (rc, out, err)。stderr 带 Traceback 一律记失败。"""
    # ★ encoding/errors 必须显式写：fgs.py 自己把 stdout 重配成 utf-8，
    #   而 Windows 上 subprocess 的 text=True, encoding="utf-8", errors="replace" 默认用 locale(cp936/GBK) 解码 →
    #   UnicodeDecodeError 把整个测试脚本打死（stderr=None 后 p.stderr 直接炸）。
    p = subprocess.run([sys.executable, str(FGS), *args], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=ENV)
    CALLED.add(args[0])
    tb = "Traceback" in p.stderr
    if tb:
        check(f"`{' '.join(args[:2])}` 没有 Traceback", False, p.stderr.strip().splitlines()[-1][:160])
    elif p.returncode not in ok:
        check(f"`{' '.join(args[:2])}` 退出码在 {ok}", False, f"rc={p.returncode} {p.stderr[:160]}")
    return p.returncode, p.stdout, p.stderr


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail and not cond else ""))


def main():
    shutil.rmtree(PDIR, ignore_errors=True)

    print("1) 写命令")
    run("init", PID, "--kind", "ctf", "--goal", "冒烟测试")
    run("hint", PID, "范围：冒烟")
    run("step-add", PID, "试一下入口 A")
    run("step-add", PID, "试一下入口 B")
    # #40 ①：终态必须带原因 —— timeout 可续上、refuted 不能（这里只走一遍可逆的那条）
    run("step-abandon", PID, "s002", "--reason", "timeout", "--why", "冒烟：先放弃再续上")
    run("step-reopen", PID, "s002")
    # #28：纠偏通道 + 止损（队列 FIFO / 队列视图 / 只杀这一路后可直接续上）
    run("steer-send", PID, "s002", "--text", "冒烟：切到入口 C")
    run("steer-drain", PID, "s002", "--by", "smoke")
    run("steer-queue", PID, "--json")
    run("step-kill", PID, "s002", "--why", "冒烟：止损再续")
    run("step-reopen", PID, "s002", "--why", "冒烟：续上")
    run("step-claim", PID, "s001", "--by", "smoke")
    run("step-conclude", PID, "s001", "结论：入口 A 通了", "--confidence", "verified")
    run("step-claim", PID, "s002", "--by", "smoke")
    run("step-heartbeat", PID, "s002", "--by", "smoke")
    # 冒烟测试不想真等 TTL：--ttl 0 表示「凡 claimed 皆视为过期」，回收完再放回 open
    run("step-reclaim", PID, "--ttl", "0")
    run("step-claim", PID, "s002", "--by", "smoke")
    run("step-conclude", PID, "s002", "结论：入口 B 未发现任何回显", "--confidence", "verified")
    run("step-release", PID, "s002")
    # 主会话监督循环：绑会话 / 监督视图 / 转向简报（2026-10-05 新增三件套）
    run("step-claim", PID, "s002", "--by", "smoke")
    run("bind", PID, "s002", "--agent", "sess-smoke")
    run("workers", PID, "--json")
    run("steer", PID, "s002", "--record")
    run("step-release", PID, "s002")
    # 授权范围（声明后 swarm / step-add --target 据此拦越界）
    run("verify", PID, "f001", "--claim", "TEST{smoke}", "--text", "回显 TEST{smoke} 出现")   # 逐字溯源盖章
    run("verify", PID, "f001", "--claim", "TEST{absent}", "--text", "不含", ok=(0, 3))          # 被拒也覆盖
    run("nonce", PID)                                                    # T5 生成 nonce 挑战
    run("nonce", PID, "--list")                                          # T5 列出现有挑战
    run("cost", PID)                                                                          # 成本画像
    run("coverage", PID)                  # 攻击面覆盖率矩阵（目标×漏洞类；测全了没有）
    run("coverage", PID, "--json")        # 同上的机器可读形态
    run("coverage", PID, "--classes", "越权,注入")   # 自定义漏洞类
    run("gate", PID, ok=(0, 3))                                                               # 统一门禁
    run("brief", PID)                      # 任务简报（相关事实+已证伪路线+知识指针）
    # 重启简报：换一份干净上下文重来（2026 证据：多跑买的是「一致性」，不是峰值）
    rc, out, _ = run("brief", PID, "--restart")
    check("brief --restart 出重启简报", "重启简报" in out and "一致性" in out)
    check("brief --restart 不依赖 open step（卡住时也能用）", "题面 / 目标" in out)
    check("brief --restart 把「未发现…」的 verified 结论列成不要再走",
          "已证伪的路线" in out and "入口 B 未发现" in out)
    check("brief --restart 带重启纪律（换攻击面 / 先用 ctfsurf）",
          "重启纪律" in out and "换攻击面" in out and "ctfsurf" in out)
    # 接力块（移植 hxbai 的会话接力：原语 / 死路 / 下一步，放正文最前）
    check("brief --restart 有接力块且在最前",
          "★ 接力块" in out and out.find("★ 接力块") < out.find("## ★ 已证伪的路线"))
    for _sect in ("① 已达成原语", "② 已证死路", "③ 下一步动作"):
        check(f"brief 含「{_sect}」", _sect in out)
    # --ref：只回路径（正文不进主会话上下文）
    rc, out_ref, err_ref = run("brief", PID, "--restart", "--ref")
    _ref_lines = [l for l in out_ref.splitlines() if l.strip()]
    check("brief --ref 的 stdout 只有一个路径",
          len(_ref_lines) == 1 and _ref_lines[0].strip().endswith(".txt"), out_ref[:200])
    check("brief --ref 不把正文打到 stdout", "★ 接力块" not in out_ref)
    if _ref_lines:
        _rp = Path(_ref_lines[0].strip())
        check("brief --ref 真的落了盘",
              _rp.exists() and "★ 接力块" in _rp.read_text(encoding="utf-8"))
    run("plan")                            # 多题广度优先计划（无 project 参数）
    run("pending")                         # [G-A] 跨项目待提交 flag（无 project 参数）
    run("diff", PID, "--clear")            # [T16] 开局定级（清除即冒烟，不脏板）
    run("aid", PID)
    # 终局兜底（2026-11-02 新增）：deadline 单源 + finish-all 幂等收口
    run("deadline", PID)                                   # show（未设）
    run("deadline", PID, "--at", "+90m")                   # 相对时间
    run("deadline", PID, "--at", "2026-11-02T21:00:00+08:00", "--note", "冒烟")
    rc, out, err = run("deadline", PID, "--json")
    check("deadline --json 带剩余秒数", "left_seconds" in out, out[:200])
    rc, out, err = run("deadline", PID, "--at", "2026-11-02T21:00:00", ok=(1,))
    check("deadline 拒绝无时区的时间（容器 UTC/宿主 CST 差 8h）",
          "没写时区" in (out + err), (out + err)[-200:])
    run("finish-all", PID)                                 # 五步收口（人在场形态）
    rc, out, err = run("finish-all", PID, "--json")
    check("finish-all --json 给出未确认 flag 清单",
          "flags_unconfirmed" in out and "claimed_steps" in out, out[:200])
    rc, out2, err = run("finish-all", PID, "--json")
    # 幂等的真判据是 **graph 不涨 revision**（不重复写盘/不刷噪声），
    # 而不是 stdout 逐字相同 —— 项目设了 deadline 时 left_seconds 本来就随时间变。
    _rev = lambda: json.loads(run("show", PID, "--json")[1]).get("revision")
    _r1 = _rev()
    run("finish-all", PID, "--json")
    run("finish-all", PID, "--json")
    _r2 = _rev()
    check("finish-all 幂等（重跑不涨 revision）", _r1 == _r2, f"revision {_r1} → {_r2}")
    run("scope", PID)
    run("scope", PID, "--set", "a.example,b.example")
    run("scope", PID, "--add", "c.example")
    run("scope", PID, "--remove", "c.example")

    # ── S2.5 压缩交接 / S3 升级决策表 / S0.5 槽位密度 / S2 子代理保活（2026-11-02）──
    rc, out, err = run("checkpoint", PID, "--state")
    check("checkpoint --state 出五段式骨架",
          all(k in out for k in ("Allocation", "Parked", "Intel log", "Queue", "Next moves")),
          out[:200])
    rc, out, err = run("checkpoint", PID, "--state")
    check("checkpoint --state 印出 85/90 阈值纪律", "85" in out and "150" in out, out[-200:])
    # --state 不许改图（它是给「即将被压缩的你自己」看的，写进 board 会搅浑 worker 的图变更信号）
    _r0 = _rev()
    run("checkpoint", PID, "--state")
    check("checkpoint --state 只读（不涨 revision）", _rev() == _r0)

    rc, out, err = run("ladder", PID)
    check("ladder 出档位表", "L0 侦察" in out and "L4 弃题" in out, out[:200])
    rc, out, err = run("ladder", PID, "--json")
    check("ladder --json 带 rows 与配额纪律",
          "rows" in out and "quota_rule" in out, out[:200])
    _r0 = _rev()
    run("ladder", PID)
    check("ladder 只读（不涨 revision）", _rev() == _r0)

    # -- T3: read-only deadline countdown + current rung. NO go/no-go advice. --
    run("deadline", PID, "--at", "+90m")
    rc, out, err = run("ladder", PID)
    check("T3 ladder 展示剩余时间", "deadline left" in out or "还剩" in out, out[:300])
    # 守住红线：只读展示，不许出现「该停/建议弃题/应当停止」这类替代人判断的措辞
    check("T3 ladder 不替人决定该停",
          not any(w in out for w in ("建议停止", "建议弃题", "应当停止", "该停了")), out[:400])
    rc, out, err = run("ladder", PID, "--json")
    check("T3 ladder --json 带 deadline 且标注只读",
          "deadline" in out and "left_seconds" in out, out[:300])
    _r0 = _rev()
    run("ladder", PID)
    check("T3 ladder 仍只读（不涨 revision）", _rev() == _r0)

    # -- T4: difficulty tier -> hint budget only, never rung/scheduling --
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    import fgs as _fgs
    check("T4 难度分档 easy", _fgs._diff_norm("easy") == 0.0)
    check("T4 难度分档 medium", _fgs._diff_norm("Medium") == 0.5)
    check("T4 难度分档 hard", _fgs._diff_norm("hard") == 1.0)
    check("T4 未知难度返回 None 不瞎猜", _fgs._diff_norm("???") is None)
    check("T4 easy 不买 hint", "do not buy" in _fgs._hint_tier(0.0))
    check("T4 medium 需 attempts>=2", "attempts>=2" in _fgs._hint_tier(0.5))
    check("T4 未知难度按 easy 处理", "easy" in _fgs._hint_tier(None))

    rc, out, err = run("cost", PID, "--json")
    check("cost --json 带槽位密度指标",
          "flags_per_min" in out and "still_claimed_after_score" in out, out[:200])
    # P2-1：槽位得分密度字段必须在（§9 验收原文点名了 points_per_slot_minute）。
    # ★ 这里只验「字段存在且可解析」；**不验它非空** —— 空项目没有派活登记/没有 awarded 时
    #   该字段合法地是 null（"无法计算"），要求非空就等于逼实现编一个数出来。
    check("cost --json 带 P2-1 字段 points_per_slot_minute",
          "points_per_slot_minute" in out and "slot_minutes" in out
          and "points_unknown_flags" in out, out[:300])

    rc, out, err = run("revive", PID)
    check("revive 建议模式跑通", "子代理保活" in out or "没有需要救" in out, out[:200])
    rc, out, err = run("revive", PID, "--json")
    check("revive --json 带 plan", "plan" in out, out[:200])
    rc, out, err = run("revive", PID, "--ttl", "1", "--apply")
    check("revive --apply 不崩（失败计数落盘）", rc == 0, (out + err)[-200:])

    # 通杀：从一条坐实的结论批量派生（源用前面已验证的 f001）
    # 变体分析（--auto）：从源 Fact 的漏洞类自动推同款候选；允许 rc=3（无候选时 Fail）
    run("swarm", PID, "--from", "f001", "--auto", "--dry-run", ok=(0, 3))
    run("swarm", PID, "--from", "f001", "--targets", "a.example,b.example",
        "--text", "同款验证 {target}", "--dry-run")
    run("swarm", PID, "--from", "f001", "--targets", "a.example",
        "--text", "同款验证 {target}")
    # 界外 + 显式越界通道（--allow-out-of-scope）都要覆盖
    run("swarm", PID, "--from", "f001", "--targets", "out.example",
        "--allow-out-of-scope", "--text", "越界 {target}")
    run("step-add", PID, "带 target 的步", "--target", "a.example")
    run("fact-add", PID, "带 meta 的事实", "--confidence", "verified",
        "--evidence", "smoke", "--phase", "triage", "--action", "collect",
        "--tool", "ss", "--target", "srv-01", "--time", "2026-09-01T00:00:00Z",
        "--ioc", "IP=203.0.113.9")
    # P5：doc 长文档下钻（brief 只显示有无 doc，正文靠 fgs doc 取）
    run("doc-add", PID, "f001", "--doc", "冒烟用长证据：curl -s /api/x 返回 200")
    run("doc", PID, "f001")
    run("relate", PID, "--type", "supports", "--from", "f001", "--to", "g001", "--reason", "smoke")
    # 跨题情报广播（P4）：R1 源未 verified 必须拒，R4 族字段空缺必须拒，
    # --revert 批次不存在必须拒。三条都是**反向用例**，允许非 0 退出。
    run("broadcast", PID, "--fact", "f001", ok=(0, 3))
    run("broadcast", PID, "--fact", "f001", "--to", "nowhere", ok=(0, 3))
    run("broadcast", "--revert", "bc-does-not-exist", ok=(0, 3))
    run("publish", PID, "--by", "smoke", "--op", json.dumps(
        {"op": "fact", "text": "经 outbox 的事实", "confidence": "verified",
         "meta": {"phase": "triage", "tool": "grep"}}))
    run("apply", PID)
    run("outbox", PID, "--withdraw", "nosuch", "--reason", "x", ok=(2,))   # 撤回不存在的 seq：应报错不崩
    run("checkpoint", PID)
    run("doctor", PID, ok=(0, 1))     # 体检允许 rc=1（发现问题时），但不许崩
    run("audit", PID, ok=(0, 1))      # S9 节点审计：rc=1 = 有坏节点（合法结果），不许崩
    run("audit", PID, "-v", ok=(0, 1))  # --verbose 连原值一起打

    print("2) 读命令（这次事故就死在这一层）")
    for args in (["show", PID], ["show", PID, "--json"],
                 ["snapshot", PID], ["snapshot", PID, "--json"], ["snapshot", PID, "--hash"],
                 ["next", PID], ["next", PID, "--json"],
                 ["chain", PID], ["chain", PID, "--json"],
                 ["trace", PID], ["trace", PID, "--json"], ["trace", PID, "--q", "SMOKE_TRACE"],
                 ["lead", PID], ["log", PID, "--tail", "5"], ["outbox", PID]):
        name = " ".join(args[:1] + args[2:])
        rc, out, err = run(*args)
        check(f"`{name}` 有输出", bool(out.strip()) or bool(err.strip()))

    print("2b) 图压缩：fold / expand 走一遍（#34，折叠后读视图不许崩）")
    _env0 = ENV.get("PI_FGS_COLD_SEC")
    ENV["PI_FGS_COLD_SEC"] = "0"
    try:
        run("fact-add", PID, "冷事实甲")
        run("fact-add", PID, "冷事实乙")
        _ns = json.loads(run("show", PID, "--json")[1])["nodes"]
        _facts = sorted(i for i, n in _ns.items() if n["type"] == "fact")
        _a, _b = _facts[-2], _facts[-1]
        run("fold", PID, _a, _b, "--title", "冒烟摘要")
        rc, out, _ = run("show", PID)
        check("折叠后 show 打出 🗂 段", "🗂" in out, out[-300:])
        _digs = [i for i, n in json.loads(run("show", PID, "--json")[1])["nodes"].items()
                 if n["type"] == "digest"]
        run("expand", PID, _digs[0])
        rc, out, _ = run("show", PID)
        check("展开后摘要段消失", "🗂" not in out, out[-300:])
    finally:
        if _env0 is None:
            ENV.pop("PI_FGS_COLD_SEC", None)
        else:
            ENV["PI_FGS_COLD_SEC"] = _env0

    print("3) 带 meta 的 Fact 真的渲染出来了（_fmt_meta 那条路径）")
    rc, out, _ = run("show", PID)
    check("show 里有 ⟨…⟩ 元数据摘要", "⟨" in out, out[-200:])
    check("show 里有 IOC 行", "IOC IP=203.0.113.9" in out, out[-200:])
    rc, out, _ = run("snapshot", PID, "--json")
    snap = json.loads(out)
    check("快照里有 meta", any("meta" in v for v in snap["facts"].values()))

    print("3b) 分析台账（X-4：与 flag 申报账本同名不同物）")
    # 幂等：台账落在 work/<pid>/（不在 TMPDIR 里），上一轮跑过就会残留 → 先清掉，
    # 否则第二次跑时"未建台账"这个前提不成立，测试会假红。
    _lp = Path(TMPDIR).parent / PID / "ledger.md"
    try:
        _lp.unlink()
    except OSError:
        pass
    rc, out, err = run("ledger", PID, ok=(3,))       # 未建台账 → rc=3 且给修法，不许崩
    check("缺台账给 rc=3", rc == 3, out + err)
    check("缺台账提示怎么建", "--init" in (out + err), (out + err)[-200:])
    run("ledger", PID, "--init", "--goal", "冒烟")
    rc, out, err = run("ledger", PID)
    check("建完能读", "分析台账" in out, out[-200:])
    check("空模板不进注入", "不会被注入" in out, out[-200:])
    rc, out, err = run("ledger", PID, "--json")
    lj = json.loads(out)
    check("--json 三节齐全", lj.get("missing") == [], out[-200:])
    check("--json 空模板不算填充", lj["sections"]["结论台账"]["filled"] is False, out[-200:])

    # 填一条真结论 → 应变成 filled（这是"会不会被注入"的唯一判据）
    _lp = Path(TMPDIR)
    for _cand in _lp.rglob("ledger.md"):
        _t = _cand.read_text(encoding="utf-8")
        _t = _t.replace("- （暂无）", "- 冒烟结论 ｜ observed ｜ 仅本行", 1)
        _cand.write_text(_t, encoding="utf-8")
        break
    rc, out, err = run("ledger", PID)
    check("填了内容 → 会被注入", "原样" in out, out[-260:])

    print("4) 危险命令（需人工，只用 scratch 项目试）")
    run("goal", PID, "改一下目标再改回来")
    run("solved", PID, "--fact", "f001", "--desc", "冒烟", "--no-lanes", "冒烟测试：单命令流程")
    run("confirm", PID, "--flag", "TEST{smoke}", "--note", "冒烟")

    # S6 提交账本三件套（AGENTS.md §三-4：新子命令必须进冒烟）
    run("flag-record", PID, "--flag", "TEST{smoke}", "--id", "smoke1")
    run("flag-submit", PID, "--id", "smoke1", "--reply", '{"correct": true}')
    run("flag-record", PID, "--flag", "TEST{smoke2}", "--id", "smoke2")
    run("flag-retest", PID, "--id", "smoke2", "--verdict", "inconclusive", "--why", "冒烟：判不了")
    run("flag-reconcile", PID)

    # T1 声明式提交闸门（2026-11-06 新增）：declaration = 声明即校验
    # ★ 三条硬规则必须在冒烟里各留一条：无 --source 拒 / 值不在证据里拒 / 命中则落 Fact
    _ev = os.path.join(TMPDIR, "smoke-ev.txt")
    with open(_ev, "w", encoding="utf-8") as _fh:
        _fh.write("hello\nX-Debug: TEST{t1gate}\nbye\n")
    rc, out, err = run("flag-declare", PID, "--value", "TEST{t1gate}",
                       "--source", _ev, "--id", "decl1", ok=(0,))
    check("T1 flag-declare 命中时通过", "byte-exact" in out, (out + err)[-300:])
    check("T1 flag-declare 明说不判平台对错",
          "不判平台对错" in out or "reply.correct" in out, (out + err)[-300:])
    rc, out, err = run("flag-declare", PID, "--value", "TEST{nope}",
                       "--source", _ev, ok=(3,))
    check("T1 值不在证据里必须拒（rc=3）", "没有逐字出现" in (out + err), (out + err)[-300:])
    rc, out, err = run("flag-declare", PID, "--value", "TEST{t1gate}", ok=(2,))
    check("T1 未带 --source 必须拒", "--source" in (out + err), (out + err)[-300:])

    # P1-1 flag-auto（2026-11-03 新增）：无平台时必须**明确失败**、不许静默成功
    # ★ 这里刻意不给真 tsec：验的是「拿不到回执 → 记 error 且 rc=1」，不是平台连通性。
    rc, out, err = run("flag-auto", PID, "--code", "smoke-01",
                       "--flag", "TEST{auto}", ok=(0, 1))
    check("flag-auto 无平台时不静默（明说拿不到回执）",
          ("拿不到" in out or "不算提交失败" in out), (out + err)[-300:])
    check("flag-auto 不出现 Traceback", "Traceback" not in err, err[-300:])

    # P1-4 flags-left：无平台时必须说「不知道」，不许报「没有剩余旗」
    rc, out, err = run("flags-left", ok=(0, 1))
    check("flags-left 无平台时说「拿不到题单」而非「没有剩余」",
          ("拿不到题单" in out or "不知道" in out), (out + err)[-300:])
    rc, out, err = run("flags-left", "--json", ok=(0, 1))
    check("flags-left --json 不崩", "Traceback" not in err, err[-300:])

    # P1-3 slot：槽位账只出建议不代劳，且只读
    _rs0 = _rev()
    rc, out, err = run("slot", PID)
    check("slot 报在跑数与上限", "在跑" in out and "上限" in out, out[:300])
    check("slot 明说「不代劳」", "不代劳" in out, out[-300:])
    check("slot 只读（不涨 revision）", _rev() == _rs0)
    rc, out, err = run("slot", PID, "--json")
    check("slot --json 有 concurrency 字段", "concurrency" in out, out[:300])

    # S2 派活登记 + 保活（同上）
    run("run-register", PID, "s001", "--task", "冒烟任务书", "--id", "run1")
    run("run-show", PID, "s001")
    run("revive", PID, "--ttl", "0")
    rc, out, _ = run("show", PID)
    check("solved+confirm 后 show 仍正常", "SOLVED" in out and "人工确认" in out, out[:200])

    print("4b) kickoff（新题第一动作：建板 + 立目标 + 打印该派哪几路）")
    KP = "smoke-kickoff"
    rc, out, _ = run("kickoff", KP, "--kind", "ctf", "--goal", "冒烟目标", "--target", "nc1.test:1")
    check("kickoff 给出第1轮「独立全题分析」", "独立全题分析" in out, out[-300:])
    check("kickoff 给出 subagent 调用模板", "subagent(" in out)
    check("kickoff 说明第2轮起是单一可验证工作项", "彼此不重复" in out)
    check("kickoff 提醒换不同模型取多样性", "不同模型" in out)
    # P2-2：`--target` 必须真落盘（过去只打印不存，兄弟图没据可判）
    rc, out2, _ = run("show", KP)
    check("P2-2 kickoff --target 落到图上（兄弟图判据的来源）",
          "nc1.test:1" in out2, out2[-300:])

    print("4b-2) preset（#46 题型模板：必查点与约束一次带齐）")
    rc, out, _ = run("preset", "list")
    check("preset list 列出内置题型模板", "题型模板" in out and "web" in out and "pwn" in out, out[-400:])
    rc, out, _ = run("preset", "show", "web")
    check("preset show 打印必查点", "必查点 1." in out, out[-300:])
    rc, out, _ = run("kickoff", "smoke-tpl", "--template", "web", "--goal", "带模板起题")
    check("kickoff --template 打印模板块", "模板 web" in out and "必查点" in out, out[-500:])

    print("4b-bis) E1 blindspot：未验证却被当前提用的 Fact 必须被点名（sapper blindspot）")
    # 用一个**独立项目**，别污染 KP 的 solved 闸门测试
    run("init", "BSP")
    run("goal", "BSP", "盲区")
    run("fact-add", "BSP", "入口是 8080（估计）", "--confidence", "inferred")
    run("step-add", "BSP", "打内网读 flag", "--from", "f001")
    rc, out, _ = run("blindspot", "BSP")
    check("blindspot 点出未验证前提", "unverified-premise" in out, out[-300:])
    check("blindspot 说明它被谁当前提用着", "被当前提" in out, out[-300:])
    rc, out, _ = run("blindspot", "BSP", "--json")
    check("blindspot --json 可机读", '"count"' in out, out[-200:])
    rc, out, _ = run("blindspot", "BSP", "--as-step", "0")
    check("blindspot --as-step 真落一个可认领 Step", "Step s002" in out or "(open)" in out, out[-200:])

    print("4c) 并行闸门：**一路都没派过**的项目，solved 必须被拒（在干净的 kickoff 项目上测）")
    run("fact-add", KP, "flag=TEST{gate}", "--confidence", "verified")
    fid = "f001"   # KP 是 kickoff 新建的干净项目，第一条 fact 就是 f001
    run("relate", KP, "--type", "satisfies", "--from", fid, "--to", "g001")
    rc, out, err = run("solved", KP, "--fact", fid, "--desc", "闸门测试", ok=(3,))
    check("未派活时 solved 被闸门拦下", "一路子代理都没派过" in (out + err), (out + err)[-200:])
    run("solved", KP, "--fact", fid, "--desc", "闸门测试", "--no-lanes", "冒烟：无可拆面")
    rc, out, _ = run("show", KP)
    check("--no-lanes 能把理由记进去", "无可拆面" in out or "SOLVED" in out, out[-200:])

    print("5) 覆盖率哨兵：覆盖全部子命令（集合相等）")
    p = subprocess.run([sys.executable, str(FGS), "--help"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=ENV)
    m = re.search(r"\{([a-z0-9,\-]+)\}", (p.stdout or "") + (p.stderr or ""))
    all_cmds = set(m.group(1).split(",")) if m else set()
    print(f"    fgs 现有子命令 {len(all_cmds)} 个：{' '.join(sorted(all_cmds))}")
    missed = all_cmds - CALLED
    check("冒烟测试覆盖了所有子命令", not missed, f"没覆盖：{sorted(missed)}")
    check("没有多余的名字", not (CALLED - all_cmds), f"多余：{sorted(CALLED - all_cmds)}")

    shutil.rmtree(TMPDIR, ignore_errors=True)
    print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ CLI 冒烟：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
