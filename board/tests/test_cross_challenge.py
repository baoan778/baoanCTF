#!/usr/bin/env python3
"""跨题调度层回归（G-A / G-B / G-C，2026-11-09 移植 sapper commander doctrine）。

    python3 board/tests/test_cross_challenge.py

## 这三项治什么（血账）

### G-A 已拿到但没提交的 flag = 纯亏
sapper docker/commander/SKILL.md §2 步骤 a 要求 commander **每轮**扫
「flag fact showing recorded (not submitted)」，因为成本已经花了、分数还没到账。
baoan 原来只有到 finish-all（终局）才扫 —— 比赛**中途**完全看不见。
实测证据：改前 decide() 函数体对 ledger|recorded|submit|flag 全部 0 命中。

### G-B 跨题视图没有优先级
cmd_plan 改前 30 行，按 sorted(PROJECTS.glob(...)) **名字母序**输出。
sapper §4.1「Score locks at first solve」+ §7.2「Depth-first on easy」要求
先扫能拿分的题。baoan 跨题视图没有难度/得分意识 = 可能在难题上耗光预算。

### G-C 配额纪律只有字符串，没有执行点
LADDER_QUOTA_RULE（fgs.py）只挂了 2 处 print，**没有任何地方真的检查**。
sapper §4.6「Easy queue non-empty? → never spend a general」形同虚设。

## 本测试锁住什么

  A. ★ 三条红线：decide() 签名不变 / 不猜数 / 不自动执行
  B. ★ pending_submits 只认账本、按闲置时长排序、accepted 排除
  C. ★ plan 按优先级排序：未提交 > 临门 > 有进展 > 未动 > 已解
  D. ★ next 的待提交提示是 display-only（调度结论不变）
  E. ★ quota_violation 两侧都要成立才告警
  F. ★ 新子命令必须进 test_cli_smoke 的集合校验
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = Path(__file__).resolve().parents[2]
FGS = LAB / "board" / "fgs.py"
PY = sys.executable or "python3"

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (("   " + str(detail)[:240]) if detail and not cond else ""))


def run(board, *args, ok=(0,)):
    env = dict(os.environ)
    env["PI_LAB_BOARD_DIR"] = str(board)
    env["PI_LAB_WORK_DIR"] = str(Path(board).parent / "work")
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([PY, str(FGS), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180,
                       cwd=str(LAB), env=env)
    if "Traceback" in (p.stderr or ""):
        check("`%s` 没有 Traceback" % " ".join(args[:2]), False, (p.stderr or "")[-200:])
    elif p.returncode not in ok:
        check("`%s` 退出码在 %s" % (" ".join(args[:2]), ok), False,
              "rc=%s %s" % (p.returncode, (p.stderr or "")[:200]))
    return p


def main():
    check("board/fgs.py 存在", FGS.is_file())
    if not FGS.is_file():
        return report()
    src = FGS.read_text(encoding="utf-8", errors="replace")

    # ---------- A. 三条红线 ----------
    print("1) 三条红线（与 test_deadline_next.py 同一套）")
    check("红线①：decide() 签名未变", "def decide(g: dict):" in src)
    check("红线①：新增层不调用 decide 之外的东西（含 decide( 的调用点仍为 3 处）",
          src.count("decide(g)") == 3, "count=%d" % src.count("decide(g)"))
    check("红线②：pending_submits 不用 or 0 把缺失当 0",
          "_age_since" in src)
    check("红线③：pending 帮助里写明不自动提交",
          "只出清单和建议" in src or "不替人提交" in src)

    # ---------- F. 冒烟覆盖 ----------
    print("2) 新子命令必须进冒烟集合校验")
    smoke = (LAB / "board" / "tests" / "test_cli_smoke.py")
    stext = smoke.read_text(encoding="utf-8", errors="replace") if smoke.is_file() else ""
    check("test_cli_smoke 里有 run(\"pending\")", "run(\"pending\")" in stext)

    with tempfile.TemporaryDirectory() as td:
        board = Path(td) / "projects"
        board.mkdir(parents=True, exist_ok=True)

        # ---------- B/C. 构造四档项目 ----------
        print("3) 构造四档：A 已拿分未交 / B 临门 / C 未动 / D 已解")
        run(board, "init", "pa", "--kind", "ctf", "--goal", "A")
        run(board, "fact-add", "pa", "flag{aaa_test_value} 来自 echo",
            "--confidence", "verified", "--evidence", "echo flag{aaa_test_value}")
        p = run(board, "flag-record", "pa", "--flag", "flag{aaa_test_value}", "--id", "fid1")
        check("flag-record 成功（state=recorded）", "已记账" in (p.stdout or ""))

        run(board, "init", "pb", "--kind", "ctf", "--goal", "B")
        run(board, "fact-add", "pb", "已坐实结论 xyz", "--confidence", "verified",
            "--evidence", "echo xyz")

        run(board, "init", "pc", "--kind", "ctf", "--goal", "C")

        run(board, "init", "pd", "--kind", "ctf", "--goal", "D")
        run(board, "fact-add", "pd", "答案 42", "--confidence", "verified", "--evidence", "echo 42")
        # ★ fgs 的既有门禁：孤点 Fact 不能授权 solved（必须挂在 goal 上）
        run(board, "relate", "pd", "--type", "satisfies", "--from", "f001", "--to", "g001")
        p = run(board, "solved", "pd", "--fact", "f001", "--desc", "test solved",
                "--chain", "a→b", "--no-lanes", "测试")
        check("pd 已真的置为 solved（夹具前提）", "solved" in (p.stdout or "").lower()
              or "已解决" in (p.stdout or ""), (p.stdout or "")[-300:])

        # ---------- B: pending 跨项目 ----------
        print("4) G-A：pending 跨项目汇总")
        p = run(board, "pending")
        out = p.stdout or ""
        check("pending 只在 pa 上命中 1 条", "待提交 flag · 1 条" in out, out[-300:])
        check("pending 显示 flag_id 与状态", "fid1" in out and "recorded" in out, out[-300:])
        check("pending 不列已解决的 pd", "pd" not in out, out[-300:])

        pj = run(board, "pending", "--json")
        try:
            js = json.loads(pj.stdout or "{}")
        except Exception:
            js = {}
        check("pending --json 结构完整",
              js.get("count") == 1 and (js.get("rows") or [{}])[0].get("pid") == "pa",
              str(js)[:240])

        # 幂等：再跑一次结果不变
        p2 = run(board, "pending")
        check("pending 幂等（再跑一次同样 1 条）", "待提交 flag · 1 条" in (p2.stdout or ""))

        # ---------- C: plan 优先级 ----------
        print("5) G-B：plan 按优先级排序")
        p = run(board, "plan")
        out = p.stdout or ""
        i_pa = out.find("pa ")
        i_pc = out.find("pc ")
        check("plan 把已拿分未交的 pa 排在未动的 pc 前面",
              i_pa >= 0 and i_pc >= 0 and i_pa < i_pc, out[:600])
        check("plan 有优先级理由列", "已拿分但未提交" in out, out[:600])
        check("plan 有待提交标记 ★", "★" in out, out[:400])
        check("plan 有全仓待提交汇总段", "全仓待提交 flag" in out, out[-600:])

        pj = run(board, "plan", "--json")
        try:
            rows = json.loads(pj.stdout or "[]")
        except Exception:
            rows = []
        check("plan --json 带 rank 与 pending_submit 字段",
              bool(rows) and all("rank" in r and "pending_submit" in r for r in rows),
              str(rows)[:300])
        if rows:
            check("plan --json 已按 rank 升序",
                  [r["rank"] for r in rows] == sorted(r["rank"] for r in rows),
                  str([r["rank"] for r in rows]))
            pa = [r for r in rows if r["pid"] == "pa"]
            check("pa 的 rank 是 0（最优先）", bool(pa) and pa[0]["rank"] == 0,
                  str(pa)[:200])
            pd = [r for r in rows if r["pid"] == "pd"]
            check("pd（已解）rank 最大（排最后）",
                  bool(pd) and pd[0]["rank"] == 4, str(pd)[:200])

        # ---------- B2: 同值多 id 去重（2026-11-10 真盘发现的缺陷）----------
        print("4b) G-A：同一个 flag **值**被记成不同 flag_id 时不许误报")
        board3 = Path(td) / "projects3"
        board3.mkdir(parents=True, exist_ok=True)
        run(board3, "init", "dup", "--kind", "ctf", "--goal", "重复记账")
        # 复刻 demo 板的真实形态：同值 id1 recorded → id1 accepted，另有 id2 recorded
        run(board3, "flag-record", "dup", "--flag", "flag{abc123}", "--id", "id1")
        run(board3, "flag-submit", "dup", "--id", "id1",
            "--reply", json.dumps({"correct": True}), ok=(0, 1, 2))
        run(board3, "flag-record", "dup", "--flag", "flag{abc123}", "--id", "id2", ok=(0, 1, 2))
        p = run(board3, "pending")
        out = p.stdout or ""
        check("★ 同值已 accepted → 另一条 recorded 不算待提交",
              "待提交 flag · 0 条" in out, out[-400:])

        # 对照：完全没提交过的值必须被列出（证明上面不是"全都不报"）
        run(board3, "flag-record", "dup", "--flag", "flag{zzz999}", "--id", "id3")
        p = run(board3, "pending")
        out = p.stdout or ""
        check("★ 对照：确实没交过的值仍被列出",
              "待提交 flag · 1 条" in out and "zzz999" in out, out[-400:])

        # ---------- D: next 的提示是 display-only ----------
        print("6) G-A 在 next 上只提示，不改调度")
        p = run(board, "next", "pa")
        out = p.stdout or ""
        check("next 提示了未提交 flag", "已拿到但没交" in out, out[-400:])
        check("next 给了提交命令", "fgs flag-submit" in out, out[-400:])
        check("next 指向跨项目视图 fgs pending", "fgs pending" in out, out[-400:])

        # 关键：调度结论与"有无待提交 flag"无关 —— 把 flag 标记 accepted 后
        # next 的 task 必须与之前相同
        before = json.loads(run(board, "next", "pa", "--json").stdout.strip().splitlines()[-1])
        run(board, "flag-submit", "pa", "--id", "fid1",
            "--reply", json.dumps({"correct": True}), ok=(0, 1, 2))
        after = json.loads(run(board, "next", "pa", "--json").stdout.strip().splitlines()[-1])
        check("★ 提交 flag 后 next 的 task 不变（display-only 硬证）",
              before.get("task") == after.get("task"),
              "before=%s after=%s" % (before.get("task"), after.get("task")))

        # ---------- E: quota_violation ----------
        print("7) G-C：配额纪律两侧都要成立")
        board2 = Path(td) / "projects2"
        board2.mkdir(parents=True, exist_ok=True)
        run(board2, "init", "hard", "--kind", "ctf", "--goal", "难题")
        run(board2, "step-add", "hard", "重侦察")
        run(board2, "step-claim", "hard", "s001", "--by", "w1")

        # 未过 L3：不该告警（右侧不成立）
        p = run(board2, "plan")
        check("step 才刚起 → 不告警", "配额违规" not in (p.stdout or ""), (p.stdout or "")[-400:])

        # 把 created_at 改到 40 分钟前 → 左右都成立 → 告警
        gp = board2 / "hard" / "graph.json"
        g = json.loads(gp.read_text(encoding="utf-8"))
        import datetime as _dt
        old = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=40)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        g["nodes"]["s001"]["created_at"] = old
        gp.write_text(json.dumps(g, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        run(board2, "init", "novice", "--kind", "ctf", "--goal", "没动过")
        p = run(board2, "plan")
        out = p.stdout or ""
        check("★ 重资源 + 有易题在等 → 告警", "配额违规" in out, out[-500:])
        check("告警点名了等待的易题", "novice" in out, out[-500:])

        # 把易题动一下 → 左侧不成立 → 不该告警
        run(board2, "fact-add", "novice", "已经开始探了", "--confidence", "inferred")
        p = run(board2, "plan")
        check("★ 易题已被动过 → 不再告警", "配额违规" not in (p.stdout or ""), (p.stdout or "")[-400:])


        # ---------- G-C2: 半场审计（T3） ----------
        print("8) G-C2：半场审计（sapper §6 囤弹药不杀敌）")
        board4 = Path(td) / "projects4"
        board4.mkdir(parents=True, exist_ok=True)
        run(board4, "init", "ht", "--kind", "ctf", "--goal", "半场")

        # A. 没设 deadline → 不出现半场段（不假装有节奏感）
        p = run(board4, "plan")
        check("A. 未设 deadline → 无半场审计段", "半场审计" not in (p.stdout or ""), (p.stdout or "")[-300:])

        # B. 过半场 + 零压强 → hoarding
        run(board4, "deadline", "ht", "--at", "+20m")
        p = run(board4, "plan")
        out = p.stdout or ""
        check("B. 过半场 + 零压强 → hoarding", "hoarding" in out, out[-400:])
        check("B. 告警写明「零压强投入」", "零压强投入" in out, out[-400:])

        # C. 有 claimed step → 不再报（压强已投入）
        run(board4, "step-add", "ht", "侦察")
        run(board4, "step-claim", "ht", "s001", "--by", "w1")
        p = run(board4, "plan")
        out = p.stdout or ""
        check("C. 已有 claimed step → 不再报 hoarding", "hoarding" not in out, out[-400:])

        # D. 未过半场 + 未开工 → not_started 但**不进 plan 输出**
        board5 = Path(td) / "projects5"
        board5.mkdir(parents=True, exist_ok=True)
        run(board5, "init", "ht2", "--kind", "ctf", "--goal", "半场2")
        run(board5, "deadline", "ht2", "--at", "+2h")
        p = run(board5, "plan")
        check("D. 未过半场 → 无半场审计段", "半场审计" not in (p.stdout or ""), (p.stdout or "")[-300:])

        # E. 判据顺序守卫：半场 + 零 step 必须是 hoarding，不是 not_started
        src2 = FGS.read_text(encoding="utf-8", errors="replace")
        check("E. 源码里 halftime 判据先看 in_halftime 再看 pressure",
              "if not in_halftime:" in src2 and "elif pressure == 0:" in src2,
              "顺序反了会把「过半场且零投入」误判成 not_started")
        check("E. halftime_audit 是纯函数（不写盘）",
              "def halftime_audit(g: dict" in src2)


        # ---------- 失败模式检测（OS docs/进化 三条实证）----------
        print("9) 失败模式：反复失败 / 反应过度 / 闭眼开干")
        board6 = Path(td) / "projects6"
        board6.mkdir(parents=True, exist_ok=True)

        # A. 反复失败：同一攻击面 3 次证伪 → 报
        run(board6, "init", "rp", "--kind", "ctf", "--goal", "g")
        for i in range(3):
            run(board6, "fact-add", "rp", "试了 LFI 不通 第%d次未发现" % i,
                "--confidence", "unverified", "--tool", "curl", "--target", "http://t/a.php")
        p = run(board6, "plan")
        out = p.stdout or ""
        check("A. 同一攻击面 3 次失败 → 报反复失败", "反复失败" in out, out[-400:])
        check("A. 提示写「换面不是换 payload」", "换**面**不是换 payload" in out, out[-400:])

        # A2. 反向：换面后不再连续 → 不报
        board6b = Path(td) / "projects6b"
        board6b.mkdir(parents=True, exist_ok=True)
        run(board6b, "init", "rp2", "--kind", "ctf", "--goal", "g")
        run(board6b, "fact-add", "rp2", "LFI 不通", "--confidence", "unverified",
            "--tool", "curl", "--target", "http://t/a")
        run(board6b, "fact-add", "rp2", "SQLi 不通", "--confidence", "unverified",
            "--tool", "sqlmap", "--target", "http://t/b")
        p = run(board6b, "plan")
        check("A2. 换面之后不计连续失败（不误报）", "反复失败" not in (p.stdout or ""),
              (p.stdout or "")[-300:])

        # B. 反应过度
        board7 = Path(td) / "projects7"
        board7.mkdir(parents=True, exist_ok=True)
        run(board7, "init", "or1", "--kind", "ctf", "--goal", "g")
        run(board7, "fact-add", "or1", "彻底放弃所有动态方案，一律走 100% 静态分析",
            "--confidence", "verified")
        p = run(board7, "plan")
        check("B. 全称否定且无替代路径 → 报反应过度", "反应过度" in (p.stdout or ""),
              (p.stdout or "")[-400:])

        # B2. 反向：给了替代路径 → 不报
        board7b = Path(td) / "projects7b"
        board7b.mkdir(parents=True, exist_ok=True)
        run(board7b, "init", "or2", "--kind", "ctf", "--goal", "g")
        run(board7b, "fact-add", "or2", "不走 GUI，改为纯静态反编译", "--confidence", "verified")
        p = run(board7b, "plan")
        check("B2. 给了替代路径（改为…）→ 不报过度反应", "反应过度" not in (p.stdout or ""),
              (p.stdout or "")[-300:])

        # C. 闭眼开干
        board8 = Path(td) / "projects8"
        board8.mkdir(parents=True, exist_ok=True)
        run(board8, "init", "bp1", "--kind", "ctf", "--goal", "g")
        run(board8, "fact-add", "bp1", "目标是 PHP 站", "--confidence", "verified")
        run(board8, "step-add", "bp1", "写爆破脚本")
        run(board8, "step-claim", "bp1", "s001", "--by", "w1")
        p = run(board8, "plan")
        check("C. 已开工但无环境事实 → 报闭眼开干", "闭眼开干" in (p.stdout or ""),
              (p.stdout or "")[-400:])

        # C2. 反向：有环境事实 → 不报
        board8b = Path(td) / "projects8b"
        board8b.mkdir(parents=True, exist_ok=True)
        run(board8b, "init", "bp2", "--kind", "ctf", "--goal", "g")
        run(board8b, "fact-add", "bp2", "环境已确认：gcc 可用 已装 gmpy2",
            "--confidence", "verified", "--tool", "env")
        run(board8b, "step-add", "bp2", "写求解器")
        run(board8b, "step-claim", "bp2", "s001", "--by", "w1")
        p = run(board8b, "plan")
        check("C2. 有 verified 环境事实 → 不报闭眼开干", "闭眼开干" not in (p.stdout or ""),
              (p.stdout or "")[-300:])

        # D. 三个检测器都是纯函数（不写盘）
        src3 = FGS.read_text(encoding="utf-8", errors="replace")
        for fn in ("def repeat_streak(", "def overreaction_rows(", "def blind_pass("):
            check("D. %s 存在" % fn.strip("def ("), fn in src3)


        # ---------- 表述强度校验（OS cognition.ts 封闭词表）----------
        print("10) 表述强度：封闭词必须配未测条件")
        board9 = Path(td) / "projects9"
        board9.mkdir(parents=True, exist_ok=True)
        run(board9, "init", "dw", "--kind", "ctf", "--goal", "g")
        run(board9, "fact-add", "dw", "已排除所有 SQL 注入可能，该站完备安全",
            "--confidence", "verified")
        p = run(board9, "plan")
        out = p.stdout or ""
        check("A. 有封闭词且台账空 → 告警", "表述强度" in out, out[-400:])
        check("A. 告警点名封闭词", "完备" in out and "已排除" in out, out[-400:])
        check("A. 告警指出台账未测条件为空", "未测条件" in out, out[-400:])

        # 反向 1：台账写了未测条件 → 标为可接受
        wd = Path(td) / "work" / "dw"   # run() 把 PI_LAB_WORK_DIR 设成 board.parent/work
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "ledger.md").write_text(
            "# 分析台账\n\n## 结论台账\n\n- f001 已排除 SQLi | observed | 测了3参数\n\n"
            "## 未测条件\n\n- 未测：POST body JSON 分支\n\n## 观测记录\n\n- curl 200\n",
            encoding="utf-8")
        p = run(board9, "plan")
        out = p.stdout or ""
        check("B. 台账已写未测条件 → 提示可接受", "已写未测条件" in out, out[-400:])

        # 反向 2：没有封闭词 → 完全不出现该段
        board10 = Path(td) / "projects10"
        board10.mkdir(parents=True, exist_ok=True)
        run(board10, "init", "nw", "--kind", "ctf", "--goal", "g")
        run(board10, "fact-add", "nw", "参数 a 存在布尔注入，已验证",
            "--confidence", "verified")
        p = run(board10, "plan")
        check("C. 无封闭词 → 不出现表述强度段", "表述强度" not in (p.stdout or ""),
              (p.stdout or "")[-300:])
        check("C. 词表不含普通词（没有误杀『已验证』）",
              "已验证" not in (p.stdout or "") or "表述强度" not in (p.stdout or ""))

        # 结构：三条纯函数存在
        src4 = FGS.read_text(encoding="utf-8", errors="replace")
        for fn in ("def universal_deny_hits(", "def deny_without_untested(",
                   "def _untested_section_filled(", "def deny_advice("):
            check("D. %s 存在" % fn.replace("def ", "").replace("(", ""), fn in src4)
        check("D. 封闭词表与 OS cognition.ts 一致",
              'UNIVERSAL_DENY_WORDS = ("永远", "所有情况", "完备", "恒真", "不可能", "已排除")' in src4)


        # ---------- G1/G2/G3/G5（本轮 4 项改造）----------
        print("11) G1 探针三要素 / G2 六维表 / G3 来源标注 / G5 危险开关")

        # G1-A：死路缺三要素 → 报
        board11 = Path(td) / "projects11"
        board11.mkdir(parents=True, exist_ok=True)
        run(board11, "init", "p1", "--kind", "ctf", "--goal", "g")
        run(board11, "fact-add", "p1", "LFI 试过不行，无回显", "--confidence", "verified")
        p = run(board11, "blindspot", "p1")
        check("G1-A 死路缺三要素 → 报 dead-unverified", "dead-unverified" in (p.stdout or ""),
              (p.stdout or "")[-400:])

        # G1-B：三要素齐全 → 不报
        board11b = Path(td) / "projects11b"
        board11b.mkdir(parents=True, exist_ok=True)
        run(board11b, "init", "p2", "--kind", "ctf", "--goal", "g")
        run(board11b, "fact-add", "p2",
            "LFI 不行：带 admin session；走 127.0.0.1 直连；POST body 直提交",
            "--confidence", "verified")
        p = run(board11b, "blindspot", "p2")
        check("G1-B 三要素齐全 → 不报 dead-unverified",
              "dead-unverified" not in (p.stdout or ""), (p.stdout or "")[-300:])

        # G3-A：verified 未标来源 → plan 点名
        board12 = Path(td) / "projects12"
        board12.mkdir(parents=True, exist_ok=True)
        run(board12, "init", "o1", "--kind", "ctf", "--goal", "g")
        run(board12, "fact-add", "o1", "算出的结果", "--confidence", "verified")
        p = run(board12, "plan")
        check("G3-A verified 未标来源 → 点名", "证据来源未声明" in (p.stdout or ""),
              (p.stdout or "")[-400:])

        # G3-B：标了 remote → 不点名
        board12b = Path(td) / "projects12b"
        board12b.mkdir(parents=True, exist_ok=True)
        run(board12b, "init", "o2", "--kind", "ctf", "--goal", "g")
        run(board12b, "fact-add", "o2", "目标返回", "--confidence", "verified", "--origin", "remote")
        p = run(board12b, "plan")
        check("G3-B 标了 origin → 不点名", "证据来源未声明" not in (p.stdout or ""),
              (p.stdout or "")[-300:])

        # G2：六维表出现在 coverage
        p = run(board12, "coverage", "o1")
        check("G2 六维表出现在 coverage", "六维" in (p.stdout or ""), (p.stdout or "")[-400:])
        check("G2 六个维度名都在", all(d in (p.stdout or "") for d in
              ("对象", "时机", "状态", "顺序", "通道", "生命周期")), (p.stdout or "")[-400:])

        # G5：--ignore-deps 打危险开关警告
        board13 = Path(td) / "projects13"
        board13.mkdir(parents=True, exist_ok=True)
        run(board13, "init", "d1", "--kind", "ctf", "--goal", "g")
        run(board13, "step-add", "d1", "前置")
        run(board13, "step-add", "d1", "后继", "--depends", "s001")
        p = run(board13, "step-claim", "d1", "s002", "--by", "w1", "--ignore-deps")
        check("G5 --ignore-deps 打危险开关警告", "危险开关" in (p.stdout or ""),
              (p.stdout or "")[-400:])
        check("G5 警告里写清「图上内容会当行动依据」",
              "行动依据" in (p.stdout or ""), (p.stdout or "")[-400:])

        # 结构：四个纯函数存在
        src5 = FGS.read_text(encoding="utf-8", errors="replace")
        for fn in ("def probe_elements(", "def dead_without_probe(",
                   "def dimension_coverage(", "def origin_of(",
                   "def undeclared_origins(", "def warn_dangerous("):
            check("G 结构: %s" % fn.replace("def ", "").replace("(", ""), fn in src5)
        check("G3 meta 白名单含 origin", '"origin")' in src5 or '"origin",' in src5)
        check("G1 词表是宽进取向（含 get/post）", '"get", "post"' in src5)


        # ---------- G4 双保险提醒 ----------
        print("12) G4 双保险：多路并行时提醒分出整理一路")

        board14 = Path(td) / "projects14"
        board14.mkdir(parents=True, exist_ok=True)
        run(board14, "init", "dt1", "--kind", "ctf", "--goal", "g")

        # A：只有 1 路 → 不提
        run(board14, "step-add", "dt1", "一")
        run(board14, "step-claim", "dt1", "s001", "--by", "w1")
        p = run(board14, "workers", "dt1")
        check("G4-A 单路 → 不提双保险", "双保险" not in (p.stdout or ""), (p.stdout or "")[-300:])

        # B：2 路并行 → 提
        run(board14, "step-add", "dt1", "二")
        run(board14, "step-claim", "dt1", "s002", "--by", "w2")
        p = run(board14, "workers", "dt1")
        out = p.stdout or ""
        check("G4-B 两路并行 → 提双保险", "双保险" in out, out[-300:])
        check("G4-B 提示里写了「整理（kb/复盘/WP）」", "复盘" in out and "WP" in out, out[-300:])

        # 结构
        src6 = FGS.read_text(encoding="utf-8", errors="replace")
        check("G4 dual_track_hint 存在", "def dual_track_hint(" in src6)
        check("G4 阈值常量存在", "DUAL_TRACK_MIN = 2" in src6)


        # ---------- T14 / T16 / T15（v10 三项）----------
        print("13) T14 替代环境推演 / T16 开局定级排序 / T15 纪律正本")

        # T14-A：自述推演 + 标 remote → 报
        b14a = Path(td) / "p14a"; b14a.mkdir(parents=True, exist_ok=True)
        run(b14a, "init", "s1", "--kind", "ctf", "--goal", "g")
        run(b14a, "fact-add", "s1", "用脚本模拟了浏览器行为，确认存在 XSS",
            "--confidence", "verified", "--origin", "remote")
        p = run(b14a, "plan")
        check("T14-A 自述推演标 remote → 报替代环境推演",
              "替代环境推演" in (p.stdout or ""), (p.stdout or "")[-400:])

        # T14-B：自述推演 + 标 local → 不报（如实承认即合规）
        b14b = Path(td) / "p14b"; b14b.mkdir(parents=True, exist_ok=True)
        run(b14b, "init", "s2", "--kind", "ctf", "--goal", "g")
        run(b14b, "fact-add", "s2", "用脚本模拟了浏览器行为",
            "--confidence", "verified", "--origin", "local")
        p = run(b14b, "plan")
        check("T14-B 标了 local → 不报（承认推演即合规）",
              "替代环境推演" not in (p.stdout or ""), (p.stdout or "")[-300:])

        # T14-C：真实测量无推演措辞 → 不报
        b14c = Path(td) / "p14c"; b14c.mkdir(parents=True, exist_ok=True)
        run(b14c, "init", "s3", "--kind", "ctf", "--goal", "g")
        run(b14c, "fact-add", "s3", "目标返回 200，flag 出现在响应体",
            "--confidence", "verified", "--origin", "remote")
        p = run(b14c, "plan")
        check("T14-C 真实测量 → 不报", "替代环境推演" not in (p.stdout or ""),
              (p.stdout or "")[-300:])

        # T16：difficulty 影响同档排序
        b16 = Path(td) / "p16"; b16.mkdir(parents=True, exist_ok=True)
        for pid, d in (("zhard", "hard"), ("aeasy", "easy"), ("mmed", "medium")):
            run(b16, "init", pid, "--kind", "ctf", "--goal", "g")
            run(b16, "diff", pid, "--set", d)
        p = run(b16, "plan", "--json")
        try:
            rows16 = json.loads(p.stdout or "[]")
        except Exception:
            rows16 = []
        order = [r["pid"] for r in rows16 if r.get("rank") == 3]
        check("T16 同档内按难度排（easy < medium < hard）",
              order == ["aeasy", "mmed", "zhard"], str(order))
        check("T16 理由里带难度标签",
              any("｜easy" in (r.get("priority_reason") or "") for r in rows16),
              str([r.get("priority_reason") for r in rows16])[:200])

        # T16-B：改定级后顺序跟着变
        run(b16, "diff", "zhard", "--set", "easy")
        p = run(b16, "plan", "--json")
        try:
            rows16b = json.loads(p.stdout or "[]")
        except Exception:
            rows16b = []
        order2 = [r["pid"] for r in rows16b if r.get("rank") == 3]
        check("T16-B 改定级后顺序跟着变（zhard 进 easy 档）",
              order2[:2] == ["aeasy", "zhard"], str(order2))

        # T16-C：--clear 后回到未定级
        run(b16, "diff", "zhard", "--clear")
        p = run(b16, "plan", "--json")
        try:
            rows16c = json.loads(p.stdout or "[]")
        except Exception:
            rows16c = []
        zr = [r for r in rows16c if r["pid"] == "zhard"]
        check("T16-C --clear 后 difficulty 为 None",
              bool(zr) and zr[0].get("difficulty") is None, str(zr)[:200])

        # T15：纪律正本第 8 节存在
        disc = LAB / "skills" / "ctf-kit" / "references" / "common-discipline.md"
        dtext = disc.read_text(encoding="utf-8", errors="replace") if disc.is_file() else ""
        check("T15 纪律正本有第 8 节「最小方案」",
              "## 8." in dtext and "最小" in dtext, dtext[:200])
        check("T14 纪律正本有第 7 节「真实环境」",
              "## 7." in dtext and "真实环境" in dtext, dtext[:200])
        check("纪律正本两节都带判据（自检两问 / 三类违规）",
              "自检两问" in dtext and "三类" in dtext, "")


        # ---------- T17：run 记录自动化（halftime 用真实用量）----------
        print("14) T17 run 记录：step-claim 自动落盘 + halftime 优先真实用量")

        b17 = Path(td) / "p17"; b17.mkdir(parents=True, exist_ok=True)
        run(b17, "init", "h1", "--kind", "ctf", "--goal", "g")
        run(b17, "step-add", "h1", "s1")
        run(b17, "step-add", "h1", "s2")

        runs17 = b17 / "h1" / "runs.jsonl"
        claim17 = b17 / "h1" / "claim_log.jsonl"
        check("T17-A claim 前没有 claim_log", not claim17.exists(), str(claim17))

        run(b17, "step-claim", "h1", "s001", "--by", "w1")
        check("T17-B claim 后自动产生 claim_log.jsonl", claim17.exists(), str(claim17))
        # ★ 关键：claim 流水**不**写进 runs.jsonl（那是带任务书的派活登记）
        check("T17-B2 claim 不污染 runs.jsonl", not runs17.exists(), str(runs17))
        lines = [l for l in claim17.read_text(encoding="utf-8").splitlines() if l.strip()] if claim17.exists() else []
        recs = []
        for l in lines:
            try:
                recs.append(json.loads(l))
            except Exception:
                pass
        check("T17-C 记录里有 step=s001", any(r.get("step") == "s001" for r in recs), str(recs)[:200])

        # halftime 用真实数据：直接测函数（它只在文本输出里出现，不进 --json）
        run(b17, "deadline", "h1", "--at", "+0.1h")
        import subprocess as _sp
        _code = ("import sys, json; sys.path.insert(0, r'%s'); import fgs; "
                 "g = fgs.load('h1'); d = fgs.halftime_audit(g, 'h1'); "
                 "print(json.dumps({'src': d.get('pressure_source'), "
                 "'p': d.get('pressure'), 'v': d.get('verdict')}, ensure_ascii=False))") % str(LAB / "board")
        _r17 = _sp.run([sys.executable, "-c", _code], cwd=str(b17),
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, "PI_LAB_BOARD_DIR": str(b17),
                            "PI_LAB_WORK_DIR": str(b17 / "dw")})
        _out = (_r17.stdout or "").strip()
        try:
            _d = json.loads(_out)
        except Exception:
            _d = {}
        check("T17-E halftime 用真实 run 数据（pressure_source=run）",
              _d.get("src") == "run", _out[:200] + " | err=" + (_r17.stderr or "")[:120])
        check("T17-F 有派活记录 → 不再判 hoarding",
              _d.get("v") != "hoarding", _out[:200])

        # 结构：两处改动都在
        src17 = FGS.read_text(encoding="utf-8", errors="replace")
        check("T17 claim_append 在 step-claim 里被调用",
              "claim_append(a.project" in src17, "")
        check("T17 claim_log 与 runs.jsonl 分开存储",
              "def claim_log_path(" in src17 and "def claim_append(" in src17, "")
        check("T17 pressure_source 存在", "pressure_source" in src17, "")

    return report()


def report():
    print("")
    print("通过 %d / 失败 %d" % (len(passed), len(failed)))
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ 跨题调度层（G-A/G-B/G-C）：全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
