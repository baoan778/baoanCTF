#!/usr/bin/env python3
"""_pkgpaths.py —— 回归测试共用的「产物位置解析」+ 跳过约定。

★ 为什么要有它（2026-10-30 发布包实测暴露的缺陷）
-------------------------------------------------
39 条回归测试里有 9 条写死了**开发仓布局**：
    <仓库>/.pi/agent/extensions/<x>.ts          扩展
    <仓库>/.pi/settings.json                    项目设置
    <仓库>/run-pi.sh                            终端启动器
    <仓库>/work/tsecbench-run/radar.py          运维脚本
交付包（买家拿到的 PiCTF 发布包）里这些**不在**同一位置：扩展随
`agent-template/extensions/` 铺出去（extensions-extra/ 已于 2026-11-19 删除，
它是 extensions/ 的旧快照、独有文件数 0，且不在 pi 的扩展发现路径里），
设置模板在 `agent-template/settings.json`，而 run-pi.sh / radar.py 根本没带。

结果：包里的测试**一条都过不了**（不是代码坏了，是测试认不出包内布局）。
这不是「测试太严」，而是**发布物不自洽**——买家跑一遍回归会看到一片红。

本文件做两件事：
  1. `ext_path(name)` / `agent_setting(name)`：按候选布局解析，命中的那个返回；
  2. `skip(reason)`：产物**确实不在发布包里**时显式跳过（退出码 77），
     打印 `SKIP ...` —— 跳过 ≠ 通过，selfcheck 会单独统计，不混进「全绿」。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

SKIP_RC = 77

# 扩展文件的候选位置（顺序即优先级）
EXT_DIRS = (
    ROOT / ".pi" / "agent" / "extensions",   # 开发仓
    ROOT / "agent-template" / "extensions",  # 发布包：随 agent 铺出去
)
# ★ 2026-11-19 删掉第三个候选 agent-template/extensions-extra/：它是 extensions/ 的整份旧快照
#   （14 个文件全部有同名主版本，独有文件数 0），仓库已 git rm。留着只会让 ext_path() 在
#   主版本缺失时悄悄命中旧副本 —— 回归测试假绿，而运行时 pi 根本不从那里加载。

# agent 设置的候选位置
SETTING_FILES = (
    ROOT / ".pi" / "agent" / "settings.json",   # 开发仓
    ROOT / "agent-template" / "settings.json",  # 发布包模板
    ROOT / "agent-template" / "settings.default.json",
)


def agent_file(name: str):
    """agent 目录里的任意配置文件（models.json / settings.json / sol-pi.json …）。

    候选顺序：运行中的 agent 目录（PI_CODING_AGENT_DIR，容器里是
    /home/ctf/.pi-ctf/agent）→ 开发仓 .pi/agent → 发布包 agent-template。
    发布包**只带模板不带运行态**，所以有些文件真的不在包里（返回 None）。
    """
    import os
    cands = []
    ad = os.environ.get("PI_CODING_AGENT_DIR")
    if ad:
        cands.append(pathlib.Path(ad) / name)
    cands += [ROOT / ".pi" / "agent" / name, ROOT / "agent-template" / name]
    for c in cands:
        if c.is_file():
            return c
    return None


def ext_path(name: str) -> pathlib.Path | None:
    """扩展文件名 → 真实存在的路径（找不到返回 None）。"""
    for d in EXT_DIRS:
        p = d / name
        if p.is_file():
            return p
    return None


def require_ext(name: str) -> pathlib.Path:
    """同 ext_path，但找不到时**大声报错退出**（说明包真的缺产物）。"""
    p = ext_path(name)
    if p is None:
        print(f"FAIL 找不到扩展 {name}；已找过：", file=sys.stderr)
        for d in EXT_DIRS:
            print(f"  - {d}", file=sys.stderr)
        raise SystemExit(2)
    return p


def agent_setting(name: str) -> pathlib.Path | None:
    for p in SETTING_FILES:
        if p.name == name and p.is_file():
            return p
    return None


def skip(reason: str) -> "None":
    """显式跳过：打印 SKIP 并退出 77（selfcheck 单列统计，不算通过）。"""
    print(f"SKIP {reason}")
    raise SystemExit(SKIP_RC)


def find_node() -> "str | None":
    """找 node 解释器：PATH → 包内 vendor 兜底。

    ★ 2026-11-02 修：四个测试各自写 `shutil.which("node")`，WSL 的 PATH 上没有 node，
      但包内带着 vendor/node —— 于是 `NODE=None` 被当成「扩展跑不了」直接 FAIL。
      同一个坑先后在 selfcheck 和 test_model_switch 上踩过两次，这里收成一处。
    """
    import shutil
    cands = [shutil.which("node")]
    root = pathlib.Path(__file__).resolve().parent.parent.parent
    cands += [str(root / "vendor" / "node" / "node"),
              str(root / "vendor" / "node" / "node.exe")]
    for c in cands:
        if c and pathlib.Path(c).is_file():
            return c
    return None
