#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_negative_claim.py — 否定结论的证据门槛

问题（2026-11-17 落地）：CTF 里「不可注入 / 端口关闭 / 无登录入口」这类否定结论会让
规划者**放弃整条方向**。一条轻率的否定 = 一条真路被永久封死，而且任务越早错越难自愈
（ARTEX CHANGELOG.md:636 原文：「任务早期的错误否定尤其会把方向带偏且难以自愈」）。

原来 confidence 三档只按「看过没看过」分，不区分「我试了 3 种编码都失败」和
「我 curl 了一次 404」。现在：
  - 否定类 Fact 在 fact-add 时回显提醒，要求 meta.attempts >= 3 种互不相同；
  - fgs gate 增加**软检查**（! 警告，不改退出码）—— 证据强弱机器判不了，
    但「有没有记」机器判得了。

断言：
  A. 否定事实 + verified + 无 attempts -> rc=0，回显软提醒，Fact 照常写入
  B. 带 3 种互不相同手段 -> 不再提醒，meta.attempts 原样落盘
  C. 去重是大小写不敏感的：「URL 编码 / url 编码 / 参数」只算 2 种 -> 仍提醒
  D. attempts 里有空串 -> 写入口直接拒（rc!=0），不让「3 种」变成假的
  E. gate 软检查：solved 板 + 没记够的否定结论 -> rc=0，出现 ! 行且写「软提醒，不拦」
  F. gate：attempts 够 -> ✓ 行「都够格」
  G. inferred 的否定结论不算数（不是结论就不拦）
  H. 肯定结论（端口开放 / 可注入）不触发
  I. 契约落盘：schema.md 的 DO NOT regress 第 13 条 + 软门槛说明
  J. 代码常量与 CLI 开关在位（NEGATIVE_PAT / NEGATIVE_MIN_ATTEMPTS / --attempts）

    python3 board/tests/test_negative_claim.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import shutil

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
FGS = os.path.join(ROOT, "board", "fgs.py")

_passed, _failed = 0, 0


def ck(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  ✗ {name}" + (f"   [{detail}]" if detail else ""))


def run(lab, *args):
    env = dict(os.environ, PI_LAB_BOARD_DIR=lab, PI_FGS_TOOFAST_SEC="0")
    r = subprocess.run([sys.executable, FGS, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=ROOT, env=env, timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def graph(lab, pid="n1"):
    with open(os.path.join(lab, pid, "graph.json"), encoding="utf-8") as fh:
        return json.load(fh)


def make_board(lab, pid="n1"):
    rc, out = run(lab, "kickoff", pid, "--kind", "ctf", "--goal", "g", "--target", "x")
    assert rc == 0, out
    return pid


def main():
    lab = tempfile.mkdtemp(prefix="negclaim-")
    try:
        # ── A：否定结论没记手段 -> 提醒但放行 ──────────────────────────────
        make_board(lab)
        rc, out = run(lab, "fact-add", "n1", "22 端口关闭，无登录入口", "--confidence", "verified")
        ck("A1 rc=0（软提醒不拦）", rc == 0, f"rc={rc} out={out[-300:]}")
        ck("A2 回显命中词", "否定结论证据门槛（#7）" in out and "无登录入口" in out, out[-400:])
        ck("A3 明说软提醒", "软提醒，不拦" in out, out[-300:])
        g = graph(lab)
        ck("A4 Fact 照常写入", g["nodes"].get("f001", {}).get("confidence") == "verified")
        ck("A5 没 attempts 键", "attempts" not in (g["nodes"]["f001"].get("meta") or {}))

        # ── B：3 种互不相同 -> 不提醒 ────────────────────────────────────
        rc, out = run(lab, "fact-add", "n1", "SQL 注入不可注入", "--confidence", "verified",
                      "--attempts", "URL 编码", "--attempts", "大小写绕过",
                      "--attempts", "注释符变体")
        ck("B1 rc=0", rc == 0, out[-200:])
        ck("B2 不提醒", "否定结论证据门槛" not in out, out[-300:])
        g = graph(lab)
        att = (g["nodes"]["f002"].get("meta") or {}).get("attempts")
        ck("B3 attempts 落盘 3 种", att == ["URL 编码", "大小写绕过", "注释符变体"], str(att))

        # ── C：去重大小写不敏感 ─────────────────────────────────────────
        rc, out = run(lab, "fact-add", "n1", "目录扫描无回显", "--confidence", "verified",
                      "--attempts", "URL 编码", "--attempts", "url 编码", "--attempts", "参数")
        ck("C1 rc=0", rc == 0, out[-200:])
        ck("C2 只算 2 种仍提醒", "只记了 2 种已试手段" in out, out[-400:])

        # ── D：空串 attempts 写入口拒 ───────────────────────────────────
        rc, out = run(lab, "fact-add", "n1", "任意子目录不存在", "--attempts", "  ")
        ck("D1 rc!=0", rc != 0, f"rc={rc}")
        ck("D2 说清是非空字符串", "非空字符串" in out, out[-300:])

        # ── E/F/G/H：gate 软检查 ────────────────────────────────────────
        # 让板子能过硬门禁：把否定 Fact 挂到 goal 上再 solved
        rc, out = run(lab, "relate", "n1", "--type", "satisfies", "--from", "f001", "--to", "g001")
        ck("E0 挂图 rc=0", rc == 0, out[-200:])
        rc, out = run(lab, "solved", "n1", "--fact", "f001", "--desc", "done",
                      "--chain", "a", "--no-lanes", "test")
        ck("E1 solved rc=0", rc == 0, out[-300:])
        rc, out = run(lab, "gate", "n1")
        ck("E2 gate 仍 rc=0（软检查不改退出码）", rc == 0, f"rc={rc} out={out[-400:]}")
        ck("E3 出现 ! 行", re.search(r"! 否定结论证据门槛", out) is not None, out[-600:])
        ck("E4 明说软提醒不拦", "软提醒，不拦" in out, out[-400:])

        # 补一条 attempts 够的否定结论 -> 不再出现在弱名单里
        rc, out = run(lab, "fact-add", "n1", "上传点不可利用", "--confidence", "verified",
                      "--from", "g001", "--attempts", "改扩展名", "--attempts", "改 MIME",
                      "--attempts", "改路径")
        ck("F0 补记 rc=0", rc == 0, out[-200:])
        rc, out = run(lab, "gate", "n1")
        ck("F1 够格的不进弱名单", "f004" not in out, out[-600:])

        # 另一块只有够格否定结论的板 -> gate 应报「都够格」
        make_board(lab, "n2")
        rc, out = run(lab, "fact-add", "n2", "端口 8080 未开放", "--confidence", "verified",
                      "--attempts", "TCP 直连", "--attempts", "SYN 扫描", "--attempts", "反向代理")
        ck("F2 rc=0", rc == 0, out[-200:])
        run(lab, "relate", "n2", "--type", "satisfies", "--from", "f001", "--to", "g001")
        run(lab, "solved", "n2", "--fact", "f001", "--desc", "done", "--chain", "a",
            "--no-lanes", "test")
        rc, out = run(lab, "gate", "n2")
        ck("F3 报「都够格」", "条否定结论都够格" in out, out[-600:])

        # G：inferred 的否定不触发
        rc, out = run(lab, "fact-add", "n1", "管理后台不存在", "--confidence", "inferred")
        ck("G1 inferred 不提醒", "否定结论证据门槛" not in out, out[-300:])

        # H：肯定结论不触发
        rc, out = run(lab, "fact-add", "n1", "22 端口开放，SSH 可连接", "--confidence", "verified")
        ck("H1 肯定结论不提醒", "否定结论证据门槛" not in out, out[-300:])

        # ── I：契约落盘 ────────────────────────────────────────────────
        with open(os.path.join(ROOT, "board", "schema.md"), encoding="utf-8") as fh:
            sch = fh.read()
        ck("I1 不变量第 13 条", "不许把「试过一次」的否定结论写成" in sch)
        ck("I2 软门槛说明段", "否定结论另有一条软门槛" in sch)
        ck("I3 写清软检查不改退出码", "都不改退出码" in sch)

        # ── J：代码常量与开关 ─────────────────────────────────────────
        with open(FGS, encoding="utf-8") as fh:
            src = fh.read()
        ck("J1 NEGATIVE_PAT", 'NEGATIVE_PAT = re.compile(' in src)
        ck("J2 阈值=3", "NEGATIVE_MIN_ATTEMPTS = 3" in src)
        ck("J3 --attempts 开关", 'p.add_argument("--attempts", action="append"' in src)
        ck("J4 help 写明防「一次 404」", "curl 了一次 404" in src)

    finally:
        shutil.rmtree(lab, ignore_errors=True)

    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
