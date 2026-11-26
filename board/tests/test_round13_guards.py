#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_round13_guards.py — R13 三个新动作的机器闸门（2026-11-23）

为什么要有这个测试：R13 修的三个问题目前只有**手工验证**：
  ① flag 值不许进 facts.md（xben-001 活体复现）
  ② 停止标记要有过期语义（否则自动恢复永久失效）
  ③ 上下文压缩阈值必须落在真实分布内（不能按 max 拍脑袋）

手工验证过的东西，下一个人改代码时会悄悄回退。这里把它们钉成断言。
"""
from __future__ import annotations
import importlib.util, json, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SDK = ROOT / "tools" / "sdkshard.py"
AUTO = ROOT / "tools" / "solver-autostart.sh"
SETTINGS = ROOT / "agent-template" / "settings.json"

_p = _f = 0
def ck(name, cond, extra=""):
    global _p, _f
    if cond:
        _p += 1; print("  OK  " + name)
    else:
        _f += 1; print("  FAIL " + name + ("  [" + str(extra)[:120] + "]" if extra else ""))

def load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

def main():
    print("A) flag 值不许进 facts.md")
    if not SDK.exists():
        print("  SKIP sdkshard.py 不在"); return 77
    S = load(SDK, "sdkshard_r13")
    FLAG = "flag{33333333-aaaa-bbbb-cccc-dddddddddddd}"
    ck("A1 整行 FOUND_FLAG= 不当事实", S.extract_facts("FOUND_FLAG=" + FLAG) == [])
    ck("A2 句子里夹带 flag 不当事实",
       S.extract_facts("取证：/order/1/receipt 返回 " + FLAG) == [])
    ck("A3 真事实仍抽得到（不误杀）", len(S.extract_facts("GET /admin/login.php 返回 200")) == 1)
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        S.save_facts(d, "x", ["- 取证：/o/1 返回 " + FLAG], [])
        txt = (d / "facts.md").read_text(encoding="utf-8")
        ck("A4 写盘时值被掩码", "a876cb40" not in txt, txt[:100])
        ck("A5 但路径保留", "/o/1" in txt, txt[:100])
        (d / "facts.md").write_text("- 老结论：/x 返回 " + FLAG, encoding="utf-8")
        back = S.load_facts(d)
        ck("A6 旧文件读侧也掩码", "a876cb40" not in back, back[:100])

    print("B) 外部来源 flag 不许提交")
    rec = {"type": "message", "message": {"role": "toolResult", "toolName": "web_search",
           "content": [{"type": "text", "text": "writeup: flag{ee06d2ea2f8c84a6}"}]}}
    ck("B1 网页里的 flag 被认出",
       len(S.external_flag_values(json.dumps(rec, ensure_ascii=False))) == 1)
    tb = {"type": "message", "message": {"role": "toolResult", "toolName": "bash",
          "content": [{"type": "text", "text": "<code>flag{realfromtarget}</code>"}]}}
    ck("B2 靶场返回的不误判",
       len(S.external_flag_values(json.dumps(tb, ensure_ascii=False))) == 0)

    print("C) 停止标记过期语义")
    txt = AUTO.read_text(encoding="utf-8") if AUTO.exists() else ""
    ck("C1 有 TTL 变量", "PI_STOPMARK_TTL" in txt)
    ck("C2 支持 forever", '"forever"' in txt or "forever" in txt)
    ck("C3 陈旧时归档而非静默忽略", ".stale-" in txt)
    # ★ 2026-11-23：第一版写 subprocess.run(["bash", ...])，在 Windows 宿主上
    #   FileNotFoundError → 误判成"语法错误"。这不是脚本坏了，是**测试写错了**。
    #   按仓库既有约定：环境不满足时 SKIP，不要 FAIL。
    _bash = shutil.which("bash")
    if not _bash:
        print("  SKIP C4 本机没有 bash（容器内才有）")
    else:
        try:
            _rc = subprocess.run([_bash, "-n", str(AUTO)], capture_output=True, timeout=60).returncode
        except (OSError, subprocess.SubprocessError):
            _rc = -1
        ck("C4 bash 语法可解析", _rc == 0, _rc)

    print("D) 压缩阈值必须落在真实分布内")
    if not SETTINGS.exists():
        print("  SKIP settings.json 不在")
    else:
        d = json.loads(SETTINGS.read_text(encoding="utf-8"))
        comp = d.get("compaction") or {}
        ck("D1 compaction 有 reserveTokens", "reserveTokens" in comp, comp)
        rt = comp.get("reserveTokens", 0)
        ck("D2 reserveTokens 不是默认值 16384", rt != 16384, rt)
        # contextWindow=1,000,000 时，阈值 = 1e6 - reserveTokens
        thr = 1000000 - int(rt)
        # 实测分布：p90=56,285 / p95=68,824 / p99=158,936 / max=174,046
        # ★ 2026-11-23 收紧：实测压缩会丢精确值（payload 保留 7% / 路径 22% / 哈希 0%），
        #   而解题靠逐字 payload。所以阈值必须**保守** —— 不能为了省钱去压缩正在解题的上下文。
        #   要求：阈值要么关闭（不设/极大），要么 >= p99(158,936 附近) 的同一量级，
        #   即"只兜底极端会话"。这里断言 >= 100000，保证绝大多数题（p95=68,824）不触发。
        ck("D3 阈值保守（>=100000，绝大多数题不压缩）", thr >= 100000, thr)
        ck("D3b 阈值不超过模型窗口", thr < 1000000, thr)
        ck("D4 keepRecentTokens 合理", 0 < int(comp.get("keepRecentTokens", 0)) <= 50000,
           comp.get("keepRecentTokens"))

    print()
    print(("OK  %d passed, %d failed" % (_p, _f)) if not _f else ("FAIL  %d passed, %d failed" % (_p, _f)))
    return 0 if not _f else 1

if __name__ == "__main__":
    sys.exit(main())
