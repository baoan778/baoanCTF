#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""thinkcheck —— 派活前的 thinkingLevel 天花板预检（只读）

为什么要有它（2026-11-02 实测血账）：
`pi-subagents` 的 `applySubagentMaxThinking`（agents.js:1111-1115）会**无条件**把
settings 里的 `maxThinking` 覆盖到**每一个** agent 头上：

    return agents.map((a) => a.maxThinking === maxThinking ? a : { ...a, maxThinking });

随后 `assertThinkingWithinCeiling` 一旦发现某个 agent 自己的 `thinkingLevel`
超过这个被覆盖后的天花板，**直接抛错**，整次派活失败。

也就是说：把 `settings.json` 的 `maxThinking` 从 "max" 调到 "medium" 这个看起来
无害的动作，会让 `ctf-verify`（thinkingLevel=max）当场炸掉 —— 而**报错发生在派活那一刻**，
不是改配置那一刻。改配置的人根本不会预料到，等到真正要取证时才崩。

本工具把那个「事后爆炸」提前成「事前红灯」：读 settings 与角色文件，静态比对，
不联网、不写盘、不启动 pi。

退出码：0 = 全部在天花板内（或未设天花板）；1 = 有角色超限；2 = 配置读不到。
"""
import json
import os
import sys

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 思考层级强度序（数值越大越强）。pi 的档位名按其在 pi-subagents 里的写法。
LEVELS = ["off", "none", "minimal", "low", "medium", "high", "max"]
RANK = {n: i for i, n in enumerate(LEVELS)}
# 常见别名（不同 pi 版本的写法差异）
ALIAS = {"": None, None: None, "default": None, "inherit": None}


def rank_of(v):
    """层级名 → 强度；未知/未设置返回 None（不参与比较，不误报）。"""
    if v in ALIAS:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().lower()
    if s in ALIAS:
        return None
    return RANK.get(s)


def agent_dir() -> str:
    """找 settings.json 与 roles/ 所在的目录。

    顺序：$PI_CODING_AGENT_DIR → ~/.pi-ctf/agent → 仓库的 agent-template/。

    ★ 2026-11-19 加最后一级：宿主上 ~/.pi-ctf/agent 只有 skills/（真正的 agent 目录在
      容器里），于是 `thinkcheck` 在宿主上永远报「读不到 settings.json」——  一个只在
      容器里能用的体检命令，等于没装。而仓库的 agent-template/ 与它结构完全一致
      （settings.json + roles/*.json），拿来核「角色档位有没有越天花板」正是同一件事。
    """
    env = (os.environ.get("PI_CODING_AGENT_DIR") or "").strip()
    if env:
        return env
    home = os.path.expanduser("~/.pi-ctf/agent")
    if os.path.isfile(os.path.join(home, "settings.json")):
        return home
    repo = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    tpl = os.path.join(repo, "agent-template")
    if os.path.isfile(os.path.join(tpl, "settings.json")):
        return tpl
    return home


def load_json(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"⚠ 读不到 {p}（{e.__class__.__name__}: {e}）", file=sys.stderr)
        return None


def main(argv) -> int:
    agent = agent_dir()
    settings_p = os.path.join(agent, "settings.json")
    roles_d = os.path.join(agent, "roles")

    settings = load_json(settings_p)
    if settings is None:
        print(f"✗ 读不到 settings.json: {settings_p}")
        print("  先确认 PI_CODING_AGENT_DIR 指向对：", agent)
        return 2

    # maxThinking 可能嵌在 subagents 段里，也可能在顶层
    ceiling = None
    for path in (("maxThinking",),
                 ("subagents", "maxThinking"),
                 ("extensions", "pi-subagents", "maxThinking")):
        cur = settings
        ok = True
        for k in path:
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                ok = False
                break
        if ok and cur is not None:
            ceiling = cur
            break

    if not os.path.isdir(roles_d):
        print(f"✗ 找不到角色目录: {roles_d}")
        return 2

    roles = {}
    for fn in sorted(os.listdir(roles_d)):
        if not fn.endswith(".json"):
            continue
        d = load_json(os.path.join(roles_d, fn))
        if not isinstance(d, dict):
            continue
        roles[fn[:-5]] = d

    c_rank = rank_of(ceiling)
    if ceiling is None:
        print(f"✓ 未设 maxThinking 天花板（settings={settings_p}）")
        print("  → 各角色按自己的 thinkingLevel 生效，不会被覆盖")
        _list(roles, None)
        return 0

    if c_rank is None:
        print(f"⚠ maxThinking={ceiling!r} 不是已知层级名（已知：{', '.join(LEVELS)}）")
        print("  → 无法静态比较，请人工确认")
        _list(roles, None)
        return 0

    print(f"天花板 maxThinking = {ceiling!r}（强度 {c_rank}）")
    over = []
    for name, d in roles.items():
        lv = d.get("thinkingLevel")
        r = rank_of(lv)
        if r is not None and r > c_rank:
            over.append((name, lv, r))

    _list(roles, c_rank)

    if over:
        print()
        print(f"✗ {len(over)} 个角色超过天花板 —— 派它们会**直接抛错**（不是降级，是失败）：")
        for name, lv, r in over:
            print(f"   · {name}: thinkingLevel={lv}（{r}） > maxThinking={ceiling}（{c_rank}）")
        print()
        print("  两条修法（选一）：")
        print(f"    a) 把 settings.json 的 maxThinking 调回 >= {max(lv for _, lv, _ in over)!r}")
        print(f"    b) 把上述角色的 thinkingLevel 降到 <= {ceiling!r}")
        print()
        print("  ⚠ 别指望它「自动降级」：applySubagentMaxThinking 是无条件覆盖，")
        print("    随后 assertThinkingWithinCeiling 抛错 —— 派活当场失败。")
        return 1

    print()
    print(f"✓ 全部 {len(roles)} 个角色都在天花板内（≤ {ceiling}）")
    return 0


def _list(roles, c_rank):
    print()
    print(f"{'角色':<16} {'thinkingLevel':<14} 状态")
    print("-" * 44)
    for name, d in roles.items():
        lv = d.get("thinkingLevel")
        r = rank_of(lv)
        if lv is None:
            mark = "（未设，跟随默认）"
        elif c_rank is None or r is None:
            mark = "?"
        elif r > c_rank:
            mark = "✗ 超限"
        else:
            mark = "✓"
        print(f"{name:<16} {str(lv if lv is not None else '(unset)'):<14} {mark}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
