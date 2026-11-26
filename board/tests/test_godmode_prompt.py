#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_godmode_prompt.py — R7-C flash 提示词补齐

为什么
------
`agent-template/extensions/deepseek-flash-godmode.ts` 每轮从
`$PI_CODING_AGENT_DIR/prompts/deepseek-flash-godmode.md` 读提示词。
该文件**自 2026-10 起从未存在**：
  · 每轮跑分日志都打一条 `[godmode] 提示词缺失，已跳过注入`（实测 138 次）；
  · deepseek-flash 是主力（2123 条消息 / 占全场 90%），却从来没被这份后缀调教过。
扩展本身的行为是对的（缺文件 → 不注入、不占上下文），缺的是**内容**。

判据
----
A. 文件存在且非空；不是越狱词而是**行为后缀**（含抢分节奏、逐字取证、换攻击面）。
B. 内容与 SOLVER_SYSTEM 互补不冲突：不重复"不猜 flag/不自己提交"整套，
   但**不许**写出与不变量相反的指令（如"自己提交"、"猜一个 flag"）。
C. entrypoint 会把 prompts/ 同步进 agent 卷（否则容器里永远是空目录）。
D. 容器内实际路径存在（若在容器里跑）。
E. 扩展的读取路径与文件位置一致（防止"改了位置但扩展仍读旧路径"）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = Path(os.path.dirname(os.path.dirname(HERE)))
PROMPT = ROOT / "agent-template" / "prompts" / "deepseek-flash-godmode.md"
EXT = ROOT / "agent-template" / "extensions" / "deepseek-flash-godmode.ts"
ENTRY = ROOT / "docker" / "entrypoint.sh"

_passed, _failed = 0, 0


def ck(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print("  x " + name + (("   [" + str(detail)[:300] + "]") if detail else ""))


def main():
    if not EXT.exists():
        print("SKIP  扩展不在发布包里")
        return 77

    print("A) 文件与内容")
    ck("A1 提示词文件存在", PROMPT.is_file())
    if not PROMPT.is_file():
        print("\nFAIL  %d passed, %d failed" % (_passed, _failed + 1))
        return 1
    text = PROMPT.read_text(encoding="utf-8")
    ck("A2 非空且够长（>800 字）", len(text) > 800, len(text))
    ck("A3 有抢分节奏（30 秒/60 秒自查）", ("30 秒" in text) or ("60 秒" in text))
    ck("A4 有逐字取证要求", "FOUND_FLAG" in text)
    ck("A5 有换攻击面纪律", "换攻击面" in text)
    ck("A6 有源码优先（先读源码再黑盒）", "源码" in text)
    ck("A7 有注入防护（靶场返回是数据）", "数据" in text and "不执行" in text)

    print("B) 与不变量不冲突")
    ck("B1 明写不自己提交", "不自己提交" in text)
    ck("B2 明写不猜 flag", "不猜 flag" in text or "不猜" in text)
    ck("B3 **没有**「自己提交 flag」这类反向指令",
       "你提交" not in text and "自行提交" not in text)
    ck("B4 **没有**「猜一个/编一个 flag」",
       "编一个" not in text.replace("不编", "") or "不许编" in text)

    print("C) entrypoint 会同步 prompts/")
    # ★ 容器里 docker/entrypoint.sh 是建镜像时的快照（不在 bind mount），改源码不同步。
    #   快照比源码旧 → 跳过 C 组；宿主跑即真检查（与 test_autostart_loop / test_solver_tools 同策略）。
    entry = ""
    if ENTRY.exists() and not (Path("/opt/pi-ctf").is_dir()
                               and ENTRY.stat().st_mtime < PROMPT.stat().st_mtime - 3600):
        entry = ENTRY.read_text(encoding="utf-8")
    if not entry:
        print("  （容器内 entrypoint 是镜像快照 → 跳过 C 组；宿主跑即真检查）")
    else:
        ck("C1 有 prompts 同步段", 'agent-template/prompts/*.md' in entry)
        ck("C2 同步目标是 agent 卷", '"$AGENT/prompts/$b"' in entry)
        ck("C3 只补缺/内容不同才覆盖", '! cmp -s "$f" "$AGENT/prompts/$b"' in entry)

    print("D) 容器内实际路径")
    ap = Path(os.environ.get("PI_CODING_AGENT_DIR") or (Path.home() / ".pi" / "agent"))
    live = ap / "prompts" / "deepseek-flash-godmode.md"
    if Path("/opt/pi-ctf").is_dir():
        ck("D1 容器内 agent 卷里有这份词", live.is_file(), str(live))
    else:
        print("  （宿主跳过：agent 卷只在容器里）")

    print("E) 扩展读的路径与文件位置一致")
    ext = EXT.read_text(encoding="utf-8")
    ck("E1 扩展读 prompts/<同名>.md", 'PROMPT_PATH = path.join(AGENT_DIR, "prompts"' in ext)
    ck("E2 同名", 'deepseek-flash-godmode.md' in ext)

    print("\n%s  %d passed, %d failed" % ("OK" if _failed == 0 else "FAIL", _passed, _failed))
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
