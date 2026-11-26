#!/usr/bin/env python3
"""test_tier_rule.py —— 守住「分档」这件事只有一份真源。

为什么要有它（2026-11-19）
---------------------
分档信息原本散在**三处**各自独立维护：
  ① agent-template/settings.json 每个 agentOverrides 条目的 "tier" 字段（10 条）
  ② 同文件 subagents.tierRule 的三行散文
  ③ tools/gen-agent-bridge.py 里的 TIER 字典（派生进 agents/*.md 的 tier: 行）
实测 pi-subagents **一个都不读**：parseBuiltinOverrideEntry 只认 18 个白名单键，
tier 不在其中，未知键静默忽略；frontmatter 侧同理。
⇒ 三处在描述同一件事，全都没有运行时消费者。改一处忘另两处不会报错，只会让人
   以为某个角色被升档了。

修法：tierRule 变成**机器可对账**的结构（每档写明 model / thinking 白名单 / agents），
      ① 的 tier 字段删掉，③ 的 TIER 字典删掉（改成反查 tierRule）。
本测试就是那个「对账器」：改动任何一边，这里必须红。

    python3 board/tests/test_tier_rule.py
"""
from __future__ import annotations

import json
import pathlib
import sys as _sys

try:
    for _s in (_sys.stdout, _sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = pathlib.Path(__file__).resolve().parents[2]
SETTINGS = LAB / "agent-template" / "settings.json"
ROLES = LAB / "agent-template" / "roles"
AGENTS = LAB / "agent-template" / "agents"
BRIDGE = LAB / "tools" / "gen-agent-bridge.py"

checks: list = []


def ok(name, cond, extra=""):
    checks.append((name, bool(cond), str(extra)))


def _report():
    bad = [c for c in checks if not c[1]]
    for name, good, extra in checks:
        mark = "✓" if good else "✗"
        print(f"  {mark} {name}" + (f"   {extra[:300]}" if extra and not good else ""))
    print(f"\n通过 {len(checks) - len(bad)} / 失败 {len(bad)}")
    if bad:
        print("失败项：" + "、".join(c[0] for c in bad))
        return 1
    print("✓ 分档真源：全部通过")
    return 0


def main() -> int:
    ok("settings.json 存在", SETTINGS.is_file())
    ok("roles/ 存在", ROLES.is_dir())
    if not all(p.exists() for p in (SETTINGS, ROLES)):
        return _report()

    try:
        cfg = json.loads(SETTINGS.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:
        ok("settings.json 能解析", False, str(e)[:160])
        return _report()

    sub = cfg.get("subagents") or {}
    rule = sub.get("tierRule") or {}
    overrides = sub.get("agentOverrides") or {}

    # ── ① tierRule 是真源：必须是结构化对象，三档都在 ──
    ok("tierRule 存在且是对象", isinstance(rule, dict) and bool(rule))
    tiers = [k for k, v in rule.items() if not k.startswith("_") and isinstance(v, dict)]
    ok("三档齐全（scout/worker/general）",
       set(tiers) == {"scout", "worker", "general"}, str(sorted(tiers)))

    # ── ② tierRule 的 agents 必须覆盖全部角色，且不重复 ──
    want_roles = {p.stem for p in ROLES.glob("*.json")}
    seen: dict = {}
    dup = []
    for t in tiers:
        for rid in rule[t].get("agents") or []:
            if rid in seen:
                dup.append(rid + "(" + seen[rid] + "+" + t + ")")
            seen[rid] = t
    ok("同一角色不被分进两个档", not dup, "、".join(dup))
    ok("tierRule 覆盖 roles/ 里的全部角色",
       want_roles <= set(seen),
       "缺=" + "、".join(sorted(want_roles - set(seen))))
    ok("tierRule 不点名不存在的角色",
       set(seen) <= want_roles,
       "多=" + "、".join(sorted(set(seen) - want_roles)))

    # ── ③ 每个 override 的 model/thinking 必须与它所在档位的声明一致 ──
    drift = []
    for rid, t in sorted(seen.items()):
        spec = rule.get(t) or {}
        o = overrides.get(rid)
        if not isinstance(o, dict):
            drift.append(rid + ": 没有 agentOverrides 条目")
            continue
        if o.get("model") != spec.get("model"):
            drift.append(rid + ": model " + str(o.get("model")) + " ≠ " + str(spec.get("model")))
        allowed = spec.get("thinking") or []
        if o.get("thinking") not in allowed:
            drift.append(rid + ": thinking " + str(o.get("thinking")) + " ∉ " + str(allowed))
    ok("每个角色的 model/thinking 都与所在档位一致", not drift, "；".join(drift[:8]))

    # ── ④ 死字段必须已经清掉（这是本次修复的核心） ──
    dead = [k for k, v in overrides.items() if isinstance(v, dict) and "tier" in v]
    ok("agentOverrides 里没有 tier 死字段", not dead, "、".join(dead))

    bridge = BRIDGE.read_text(encoding="utf-8", errors="replace") if BRIDGE.is_file() else ""
    # 只认**代码行**：注释里提「原来是 TIER = {...}」是正常的来由说明，不算违规
    code_tier = [ln for ln in bridge.splitlines()
                 if ln.strip().startswith("TIER = {")]
    ok("gen-agent-bridge.py 不再自带 TIER 字典（代码行）", not code_tier,
       "；".join(code_tier[:3]))
    ok("gen-agent-bridge.py 反查 tierRule",
       "_tier_map" in bridge and "tierRule" in bridge)

    # ── ⑤ agents/*.md 的 frontmatter 里不该再有 tier: 行 ──
    if AGENTS.is_dir():
        left = []
        for f in sorted(AGENTS.glob("*.md")):
            head = f.read_text(encoding="utf-8", errors="replace").split("\n---", 1)[0]
            if any(ln.startswith("tier:") for ln in head.splitlines()):
                left.append(f.name)
        ok("agents/*.md frontmatter 里没有 tier: 行", not left, "、".join(left))
    else:
        ok("agents/ 目录存在", False, str(AGENTS))

    return _report()


if __name__ == "__main__":
    raise SystemExit(main())