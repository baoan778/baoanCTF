#!/usr/bin/env python3
"""回归测试：应急交付两件套（`ir-report.py` 报告/IOC 导出 + `loop-check.py` 打转检测）

跑法：
    python3 board/tests/test_ir_report.py

为什么要有它
------------
应急交付最容易翻车的地方不是"查不出来"，而是**交出去的东西别人用不了**：
  · 报告里 IOC 是一张 markdown 表 → 封禁/SIEM 导入要人肉抄；
  · 同一条 IOC 被五条 Fact 提到 → 表里出现五行，设备侧还得自己去重；
  · `inferred` 的"疑似 IOC"混进正式表 → 下游照单封禁，误伤。

这个文件守五件事：
  T1 报告能生成，且 `inferred` 的 IOC **绝不进** IOC 表（只出现在「待坐实」节）
  T2 同一条 IOC 被多条 Fact 提到 → **合并成一行**：出处并列、首见/末见取极值、工具并列
  T3 `--ioc-out .csv` 机器可读：列名固定、utf-8-sig（Excel 中文不乱码）、行数正确
  T4 `--ioc-out .json` 可解析、带 count/project；不认识的后缀**直接拒**（fail-closed）
  T5 空白板不崩（给"先补事实，别猜"的提示）；`loop-check` 对重复 Fact 判打转 exit 1
  T6 带时区的时间**换成 UTC**（否则时间线按字符串排会错 8 小时），排序真按 UTC
  T7 处置四段从 `meta.phase` **自动归位**；没写 phase 的段保留占位符（不编）
"""
import csv
import json
import os
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

ROOT = Path(__file__).resolve().parents[2]
FGS = ROOT / "board" / "fgs.py"
IR = ROOT / "skills" / "ir-response" / "scripts" / "ir-report.py"
LOOP = ROOT / "skills" / "ir-response" / "scripts" / "loop-check.py"
IOC_FIELDS = ["type", "value", "first_seen", "last_seen", "facts", "phases", "tools", "targets", "origin"]

passed: list[str] = []
failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        passed.append(name)
    else:
        failed.append(name)
        print(f"  ✗ {name}  {str(detail)[:400]}")


def run(cmd, env, timeout=60):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=timeout)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pilab-irreport-"))
    try:
        board = tmp / "projects"          # = PI_LAB_BOARD_DIR（fgs 直接写这里）
        broot = tmp                        # = --board（ir-report/loop-check 会自己接 projects/）
        env = dict(os.environ, PI_LAB_BOARD_DIR=str(board))   # 隔离：不动真 board/projects
        PID = "irx"
        outdir = tmp / "reports"

        # ── 造现场：3 条 verified（其中两条讲同一个 IP）+ 1 条 inferred ──
        check("fgs init 成功", run(["python3", str(FGS), "init", PID, "--kind", "pentest",
                                    "--goal", "某主机异常外联，查清并出处置结论"], env).returncode == 0)
        f1 = run(["python3", str(FGS), "fact-add", PID,
                  "异常外联 pool.bad.xyz:3333（本机 203.0.113.77 发起）",
                  "--confidence", "verified", "--evidence", "ss -antp 输出第 4 行",
                  "--phase", "triage", "--tool", "ss", "--target", "srv-01",
                  "--time", "2026-09-01T03:12:00Z", "--ioc", "IP=203.0.113.77",
                  "--ioc", "域名=pool.bad.xyz"], env)
        check("fact-add f001（结构化 IOC）成功", f1.returncode == 0, f1.stderr)
        f2 = run(["python3", str(FGS), "fact-add", PID,
                  "同一外联在 04:05 再次出现，端口未变",
                  "--confidence", "verified", "--evidence", "tcpdump 抓包第 12 行",
                  "--phase", "triage", "--tool", "tcpdump", "--target", "srv-01",
                  "--time", "2026-09-01T04:05:00Z", "--ioc", "IP=203.0.113.77"], env)
        check("fact-add f002（同一 IOC 再来一次）成功", f2.returncode == 0, f2.stderr)
        f3 = run(["python3", str(FGS), "fact-add", PID,
                  "疑似还有 198.51.100.9 参与（尚未坐实）",
                  "--confidence", "inferred", "--ioc", "IP=198.51.100.9"], env)
        check("fact-add f003（inferred）成功", f3.returncode == 0, f3.stderr)
        f4 = run(["python3", str(FGS), "fact-add", PID,
                  "落地的下载地址 http://pool.bad.xyz/a.sh 已取证",
                  "--confidence", "verified", "--evidence", "_evidence/a.sh.sha256"], env)
        check("fact-add f004（正文里带 URL，走正则兜底）成功", f4.returncode == 0, f4.stderr)
        f5 = run(["python3", str(FGS), "fact-add", PID,
                  "已在边界封禁该地址（HITL 批准）",
                  "--confidence", "verified", "--evidence", "edge-fw 策略 412 行",
                  "--phase", "contain", "--time", "2026-09-01T12:00:00+08:00",
                  "--ioc", "域名=tz.example.xyz"], env)
        check("fact-add f005（contain 阶段 + 带时区偏移的时间）成功", f5.returncode == 0, f5.stderr)
        f6 = run(["python3", str(FGS), "fact-add", PID,
                  "清掉持久化并轮换 srv-01 上两个凭据",
                  "--confidence", "verified", "--phase", "eradicate"], env)
        check("fact-add f006（eradicate 阶段）成功", f6.returncode == 0, f6.stderr)

        # ── T1 报告 ──
        rep = outdir / "ir-report.md"
        p = run(["python3", str(IR), PID, "--board", str(broot), "--out", str(rep)], env)
        check("ir-report 退出码 0", p.returncode == 0, p.stderr)
        text = rep.read_text(encoding="utf-8") if rep.exists() else ""
        check("报告文件已生成", rep.exists(), str(rep))
        check("报告里出现逐字 IOC（pool.bad.xyz）", "pool.bad.xyz" in text)
        check("报告有「待坐实」节", "待坐实" in text)
        check("inferred 的 IOC 没混进 IOC 表（198.51.100.9 只在待坐实节）",
              text.count("198.51.100.9") <= 1, f"出现 {text.count('198.51.100.9')} 次")
        check("时间线文件同目录生成", (outdir / "timeline.md").exists())

        # ── T2/T3 CSV：合并 + 机器可读 ──
        csv_path = outdir / "iocs.csv"
        p = run(["python3", str(IR), PID, "--board", str(broot), "--out", str(rep),
                 "--ioc-out", str(csv_path)], env)
        check("--ioc-out .csv 退出码 0", p.returncode == 0, p.stderr)
        raw = csv_path.read_bytes() if csv_path.exists() else b""
        check("CSV 带 BOM（Windows Excel 中文表头不乱码）", raw.startswith(b"\xef\xbb\xbf"), raw[:8])
        rows = list(csv.DictReader(raw.decode("utf-8-sig").splitlines()))
        check("CSV 列名固定", list(rows[0].keys()) == IOC_FIELDS if rows else False, rows[:1])
        check("CSV 行数 = 4（IP / 两个域名 / URL，同一条合并）", len(rows) == 4, [r["value"] for r in rows])
        by_val = {r["value"]: r for r in rows}
        ip = by_val.get("203.0.113.77", {})
        check("同一 IP 的两条 Fact 合并到一行", ip.get("facts") == "f001;f002", ip.get("facts"))
        check("首见/末见取极值", (ip.get("first_seen"), ip.get("last_seen"))
              == ("2026-09-01T03:12:00", "2026-09-01T04:05:00"),
              (ip.get("first_seen"), ip.get("last_seen")))
        check("工具并列（ss + tcpdump）", ip.get("tools") == "ss;tcpdump", ip.get("tools"))
        check("结构化来源标 meta", ip.get("origin") == "meta", ip.get("origin"))
        check("inferred 的 198.51.100.9 不在 CSV 里",
              all(r["value"] != "198.51.100.9" for r in rows))
        check("正文里抠出的 URL 也进了表（逐字出现）",
              any(r["type"] == "URL" for r in rows), [r["type"] for r in rows])

        tz = by_val.get("tz.example.xyz", {})
        check("带 +08:00 的时间换算成 UTC（12:00+08:00 → 04:00Z）",
              (tz.get("first_seen"), tz.get("last_seen")) == ("2026-09-01T04:00:00", "2026-09-01T04:00:00"),
              (tz.get("first_seen"), tz.get("last_seen")))
        tltext = (outdir / "timeline.md").read_text(encoding="utf-8")
        check("时间线真按 UTC 排序（04:00 夹在 03:12 与 04:05 之间）",
              tltext.index("03:12:00") < tltext.index("04:00:00") < tltext.index("04:05:00"),
              [ln for ln in tltext.splitlines() if "2026-09-01" in ln][:5])
        check("处置段自动归位：CONTAINMENT 里出现 f005 的证据",
              "edge-fw 策略 412 行" in text or "已在边界封禁该地址" in text)
        check("处置段自动归位：ERADICATION 里出现 f006",
              "轮换 srv-01" in text)
        check("没写 phase 的段保留占位符（不编内容）", "按现场填写" in text)

        # ── T4 JSON + 拒未知后缀 ──
        jpath = outdir / "iocs.json"
        p = run(["python3", str(IR), PID, "--board", str(broot), "--out", str(rep),
                 "--ioc-out", str(jpath)], env)
        check("--ioc-out .json 退出码 0", p.returncode == 0, p.stderr)
        try:
            j = json.loads(jpath.read_text(encoding="utf-8"))
            check("JSON 含 count/project/generated_at",
                  j.get("count") == 4 and j.get("project") == PID and bool(j.get("generated_at")), j)
            check("JSON 的 iocs 字段结构完整",
                  all({"type", "value", "first_seen", "facts"} <= set(x) for x in j["iocs"]), j["iocs"][:1])
        except Exception as e:
            check("JSON 可解析", False, f"{e}")

        p = run(["python3", str(IR), PID, "--board", str(broot), "--out", str(rep),
                 "--ioc-out", str(outdir / "iocs.txt")], env)
        check("不认识的后缀被拒（非 0 退出）", p.returncode != 0, f"rc={p.returncode}")
        check("拒绝理由里点明是后缀问题", "后缀" in (p.stdout + p.stderr), (p.stdout + p.stderr)[-200:])
        check("拒绝时不产出 .txt", not (outdir / "iocs.txt").exists())

        # ── T5 空白板不崩 + loop-check 判据 ──
        run(["python3", str(FGS), "init", "empty-ir", "--kind", "pentest", "--goal", "空白板"], env)
        ep = outdir / "empty.md"
        p = run(["python3", str(IR), "empty-ir", "--board", str(broot), "--out", str(ep)], env)
        etext = ep.read_text(encoding="utf-8") if ep.exists() else ""
        check("空白板生成报告不崩（rc=0）", p.returncode == 0, p.stderr)
        check("空白板给的是「先补事实，别猜」而不是空表", "先补事实" in etext, etext[-300:])

        lc = run(["python3", str(LOOP), PID, "--board", str(broot)], env)
        check("干净项目 loop-check 判「没打转」（rc=0）", lc.returncode == 0, lc.stdout[-300:])

        spin = tmp / "spin"
        sroot = spin
        senv = dict(os.environ, PI_LAB_BOARD_DIR=str(spin / "projects"))
        run(["python3", str(FGS), "init", "spin", "--kind", "pentest", "--goal", "打转样本"], senv)
        for _ in range(2):
            run(["python3", str(FGS), "fact-add", "spin", "又试了一次同样的 XSS，还是没绕过 WAF",
                 "--confidence", "unverified"], senv)
        lc2 = run(["python3", str(LOOP), "spin", "--board", str(sroot)], senv)
        check("重复 Fact → loop-check 判打转（rc=1）", lc2.returncode == 1, f"rc={lc2.returncode}")
        check("纠偏提示里含「换攻击面」", "换**攻击面**" in lc2.stdout or "换攻击面" in lc2.stdout,
              lc2.stdout[-300:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ 应急交付两件套（报告 / IOC 合并与导出 / 空板 / 打转检测）全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
