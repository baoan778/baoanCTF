#!/usr/bin/env python3
"""把 roles/<id>.json + prompts/<id>.md 生成 pi-subagents 认的 agents/<id>.md。

★ 为什么要有这一层（2026-11-06 实测暴露的缺陷）
---------------------------------------------
本仓有两套互不相认的"智能体定义"：

  roles/<id>.json + prompts/<id>.md   ← 我们自己的 WebUI 读（work/ui/server.mjs）
  agents/<id>.md（YAML frontmatter）  ← pi-subagents 只认这个

结果：settings.json 里给 10 个角色写的 subagents.agentOverrides **全是空转**。
  /subagents-models 列出 18 个 agent，全是被 builtin 覆盖的（scout/worker/researcher…），
  ctf-recon / ctf-route / ctf-verify / ctf-vision / ctf-wp / ctf / ir / forensics / general / assistant
  一个都不在；/subagents-doctor 报 "agents: total 13 (builtin 13, user 0, project 0)"。
  派人时写 agent: "ctf-recon" 只会得到 Unknown agent。

分工：roles/*.json 仍是唯一真源（WebUI 要改的就是它），本脚本负责单向派生。
跑法：python3 tools/gen-agent-bridge.py [--check]
  --check 只比对不写，有任何漂移退出码 1（给 CI / selfcheck 用）
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

# Windows 控制台默认 GBK，打印 ✓ 会 UnicodeEncodeError（实测踩过）；统一转 UTF-8
try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
ROLE_DIR = ROOT / "agent-template" / "roles"
OUT_DIR = ROOT / "agent-template" / "agents"

# ★ 2026-11-19 改：tier 不再由本脚本自带一张表（原来 TIER = {...} 是 settings.json
#   tierRule 的**第三份副本**，改一处忘另两处）。现在唯一真源是 settings.json 的
#   subagents.tierRule.<档>.agents，本脚本只负责反查：角色 → 它在哪个档。
#   核对由 board/tests/test_tier_rule.py 机器看住（三档覆盖全部角色、model/thinking 与
#   agentOverrides 实际值一致）。
def _tier_map() -> dict:
    """{role_id: tier}，从 settings.json 的 subagents.tierRule 反查出来。"""
    sp = ROOT / "agent-template" / "settings.json"
    try:
        # lint:ok 读的是仓库自带的 settings.json，不是外部输入。
        cfg = json.loads(sp.read_text(encoding="utf-8"))
    except Exception:
        return {}
    rule = (cfg.get("subagents") or {}).get("tierRule") or {}
    out = {}
    for tier, spec in rule.items():
        if tier.startswith("_") or not isinstance(spec, dict):
            continue
        for rid in spec.get("agents") or []:
            out[rid] = tier
    return out


_TIER_MAP = _tier_map()

# 只读角色不许带写工具（验收角色别自己给自己盖章）
READONLY = {"ctf-recon", "ctf-verify"}
DEFAULT_NO_WRITE = {"read", "bash", "grep", "web_search", "fetch_content", "get_search_content"}


def build(role_path: pathlib.Path) -> str:
    # lint:ok 调用方只喂 ROLE_DIR.glob("*.json")（仓库自带角色表），不是外部输入。
    role = json.loads(role_path.read_text(encoding="utf-8"))
    rid = role.get("id") or role_path.stem
    tools = list(role.get("tools") or [])
    if rid in READONLY:
        tools = [t for t in tools if t in DEFAULT_NO_WRITE]
    thinking = role.get("thinkingLevel") or "low"
    tier = _TIER_MAP.get(rid) or "worker"
    lines = [
        "---",
        f"name: {rid}",
        f"description: {(role.get('description') or rid).strip()}",
        f"thinking: {thinking}",
        # ★ 2026-11-14 修 P1：tools 为空时**必须省略整行**。
        #   `tools: ` 会被 pi 解析成「显式白名单 = 空」→ createToolNameMatcher([]) 恒 false
        #   → 该角色派活后**零内置工具**（只能说话，read/bash 全无）。
        #   实测：assistant/ctf/forensics/general/ir 五个角色都是 tools:null，全部命中。
        #   省略该行 = undefined = 不限制，这才是 null 的本意。
        *([f"tools: {', '.join(tools)}"] if tools else []),
        "systemPromptMode: replace",
        "inheritProjectContext: true",
        "inheritSkills: false",
        "defaultContext: fresh",
        "---",
        "",
        f"<!-- 由 roles/{rid}.json 生成，勿手改；正文见 {role.get('promptFile') or 'prompts/' + rid + '.md'} ｜ 档位 {tier}（说明用，pi 不读） -->",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只比对不写")
    args = ap.parse_args()

    if not ROLE_DIR.is_dir():
        print(f"SKIP 找不到 {ROLE_DIR}")
        return 77
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    drift = []
    written = 0
    for rp in sorted(ROLE_DIR.glob("*.json")):
        want = build(rp)
        target = OUT_DIR / f"{rp.stem}.md"
        # ★ 2026-11-14 修 P4：按 **bytes** 比对，别让通用换行把 CRLF/LF 差异吃掉（假绿）。
        have = target.read_bytes().decode("utf-8") if target.is_file() else None
        if have == want:
            continue
        drift.append(target.name)
        if not args.check:
            # ★ 2026-11-14 修 P4：缺 newline="\n" 时 Windows 会写 CRLF，
            #   10 个 agents/*.md 永久 dirty，而 --check 因为读取归一化永远报「一致」（假绿）。
            target.write_text(want, encoding="utf-8", newline="\n")
            written += 1

    # ★ 2026-11-14 修 P3：roles/*.json 与 settings.json 的 subagents.agentOverrides
    #   是**两份都会生效**的定义，且 agentOverrides 优先级更高（实测覆盖 roles 的
    #   thinkingLevel：ctf-verify 声明 max、实跑 high）。原来 --check 只比「派生是否
    #   同步」，完全不看这份漂移，于是 --check 报「一致」而实际行为早已偏离。
    #   这里做交叉校验，把「声明 vs 生效」的差摊在明面上。
    conflicts = []
    settings_p = ROOT / "agent-template" / "settings.json"
    if settings_p.is_file():
        try:
            cfg = json.loads(settings_p.read_text(encoding="utf-8"))
            ov = ((cfg.get("subagents") or {}).get("agentOverrides") or {})
        except Exception:  # noqa: BLE001
            ov = {}
        for rp in sorted(ROLE_DIR.glob("*.json")):
            try:
                role = json.loads(rp.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            rid = role.get("id") or rp.stem
            o = ov.get(rid)
            if not isinstance(o, dict):
                continue
            want = role.get("thinkingLevel")
            got = o.get("thinking")
            if want and got and want != got:
                conflicts.append(f"{rid}: roles 声明 {want} / settings 生效 {got}")

    if conflicts:
        print("⚠ 双真源漂移（settings.agentOverrides 覆盖 roles/*.json，实际按后者生效）：")
        for c in conflicts:
            print("  - " + c)
        print("  修法：二选一做成唯一真源 —— 要么删 override 的 thinking，要么把它当派生目标")
        if args.check:
            drift.append("<settings 与 roles 的 thinking 漂移>")

    if args.check:
        if drift:
            print(f"漂移 {len(drift)} 个：" + "、".join(drift))
            print("跑 python3 tools/gen-agent-bridge.py 重新生成")
            return 1
        print(f"✓ agent 桥接与 roles 一致（{len(list(ROLE_DIR.glob('*.json')))} 个角色）")
        return 0

    print(f"生成 {written} 个 agent 定义 → {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
