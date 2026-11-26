#!/usr/bin/env python3
"""skilllint 路由判据回归：把 pi 的真实技能加载语义变成可执行判据。

跑法：
    python3 board/tests/test_skilllint.py

为什么要有这个文件
------------------
2026-10-12 借 dsh ANCHOR 的 skill_lint，但**没照搬** —— 先读了 pi 源码
`dist/core/skills.js` 才定判据（dsh 自己的规则与 pi 不同，照抄会造出假判据）：

  · `MAX_NAME_LENGTH = 64` / `MAX_DESCRIPTION_LENGTH = 1024` —— **规格硬限**
  · 发现规则：目录里有 SKILL.md 就当技能根、不再下钻；否则**递归**找 SKILL.md
    → 嵌套 SKILL.md **会被当成独立技能加载**（不是不加载，与 dsh 相反）
  · frontmatter 只渲染 name / description / disable-model-invocation
    → 写 `whenToUse` 是「看着像路由、实际不渲染」的静默失效
  · 路由全靠 name + description（系统提示原文：load a skill's file when the task matches
    its description）→ description 里没有「何时加载」= 这张卡不会被路由到

判据比功能更容易**静默失效**，所以每个检查都在这里**先造出这种病再断言它被抓住**
（含正向对照：合规技能必须放行，否则就是误报）。

测试**不碰真产物**：临时技能库建在 `tempfile.mkdtemp()` 里；只读仓库真技能库做正例。
"""
import importlib.util
import shutil
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

LAB = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("skilllint", LAB / "tools" / "skilllint.py")
sl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sl)

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail and not cond else ""))


def write_skill(root: Path, name: str, frontmatter: str, body: str = "正文"):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n\n{body}\n", encoding="utf-8")


GOOD_DESC = "做一些示例事情。当目标是样例、需要演示时使用。"


def desc_for(name: str) -> str:
    """每张测试卡用**不同**的 description —— 否则会被「逐字重复」检查误连坐（踩过）。"""
    return f"做一些 {name} 示例事情。当目标是 {name} 样例、需要演示时使用。"


def main():
    tmp = Path(tempfile.mkdtemp(prefix="pilab-skilllint-"))
    try:
        print("1) 正向对照：合规技能必须放行（不许误报）")
        write_skill(tmp, "good-skill", f"name: good-skill\ndescription: {GOOD_DESC}")
        _rows, failures, warns = sl.audit(tmp)
        check("合规技能：零硬失败", not failures, str(failures))
        check("合规技能：零告警（阈值内、有判据）", not warns, str(warns))

        print("2) 缺 frontmatter / name / description → 硬失败")
        (tmp / "bare").mkdir()
        (tmp / "bare" / "SKILL.md").write_text("没有 frontmatter\n", encoding="utf-8")
        write_skill(tmp, "no-desc", "name: no-desc")
        write_skill(tmp, "no-name", f"description: {desc_for('no-name')}")
        _rows, failures, _w = sl.audit(tmp)
        check("缺 frontmatter 被抓（\"缺 frontmatter\"）", any("缺 frontmatter" in f for f in failures), str(failures))
        check("缺 description 被抓（\"缺 description\"）", any("缺 description" in f for f in failures), str(failures))
        check("缺 name 被抓（\"缺 name\"）", any("缺 name" in f for f in failures), str(failures))

        print("3) name 不合 pi 规格 → 硬失败（含长度 / 大小写 / 连字符三条）")
        write_skill(tmp, "BadName", f"name: BadName\ndescription: {desc_for('BadName')}")
        write_skill(tmp, "-lead", f"name: -lead\ndescription: {desc_for('-lead')}")
        write_skill(tmp, "double--dash", f"name: double--dash\ndescription: {desc_for('double--dash')}")
        long_name = "a" * 70
        write_skill(tmp, long_name, f"name: {long_name}\ndescription: {desc_for(long_name)}")
        _rows, failures, _w = sl.audit(tmp)
        joined = " | ".join(failures)
        check("大写名不合法被抓（BadName）", "BadName" in joined, joined[-300:])
        check("首连字符被抓（-lead）", "-lead" in joined, joined[-300:])
        check("连续连字符被抓（double--dash）", "double--dash" in joined, joined[-300:])
        check("超 64 字符被抓", long_name in joined, joined[-300:])

        print("4) description 超 pi 规格上限（1024）→ 告警；边界 1024 必须放行")
        over = "当测试时使用。" + "x" * 1100
        write_skill(tmp, "over-desc", f"name: over-desc\ndescription: {over}")
        _rows, _f, warns = sl.audit(tmp)
        check("超 1024 被抓（提 pi 规格）", any("over-desc" in w and "1024" in w for w in warns), str(warns)[-300:])
        shutil.rmtree(tmp / "over-desc")
        exact = "当测试时使用。" + "x" * (1024 - len("当测试时使用。"))
        write_skill(tmp, "exact-desc", f"name: exact-desc\ndescription: {exact}")
        _rows, _f, warns = sl.audit(tmp)
        check("正好 1024 字符不报（边界不误伤）",
              not any("exact-desc" in w for w in warns), str(warns)[-300:])
        shutil.rmtree(tmp / "exact-desc")

        print("5) description 缺「何时加载」判据 → 告警（路由失效）")
        write_skill(tmp, "no-trigger", "name: no-trigger\ndescription: 一个做某事的工具集合。")
        _rows, _f, warns = sl.audit(tmp)
        check("无判据被抓", any("no-trigger" in w and "何时加载" in w for w in warns), str(warns)[-300:])
        check("英文 Use when 也算判据（不误报）",
              bool(sl.TRIGGER_RE.search("Does X. Use when attacking Y.")))
        check("中文「当…时使用」算判据", bool(sl.TRIGGER_RE.search(GOOD_DESC)))

        print("6) whenToUse 不渲染 → 告警；上游溯源键（license 等）不报（防告警疲劳）")
        write_skill(tmp, "ghost-key", f"name: ghost-key\nwhenToUse: 每次做题时\ndescription: {desc_for('ghost-key')}")
        write_skill(tmp, "src-key", f"name: src-key\nlicense: MIT\nmetadata:\n  author: x\ndescription: {desc_for('src-key')}")
        _rows, _f, warns = sl.audit(tmp)
        check("whenToUse 被抓", any("ghost-key" in w and "whenToUse" in w for w in warns), str(warns)[-400:])
        check("license/metadata 这类溯源键不报（否则一次刷 15 条）",
              not any("src-key" in w and "不渲染" in w for w in warns), str(warns)[-400:])

        print("7) description 逐字重复 / 嵌套 SKILL.md → 告警")
        write_skill(tmp, "dup-a", "name: dup-a\ndescription: 同一段描述。当测试时使用。")
        write_skill(tmp, "dup-b", "name: dup-b\ndescription: 同一段描述。当测试时使用。")
        _rows, _f, warns = sl.audit(tmp)
        check("逐字重复被抓", any("dup-" in w and "逐字相同" in w for w in warns), str(warns)[-400:])
        nested = tmp / "good-skill" / "references"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "SKILL.md").write_text(f"---\nname: nested\ndescription: {desc_for('nested')}\n---\nx\n", encoding="utf-8")
        _rows, _f, warns = sl.audit(tmp)
        check("嵌套 SKILL.md 被抓（pi 会把它当独立技能）",
              any("嵌套" in w for w in warns), str(warns)[-400:])

        print("8) 真技能库：硬失败必须为 0（本仓自己的 16 张卡要一直合规）")
        rows, failures, warns = sl.audit(sl.SKILLS)
        check(f"仓库技能库 {len(rows)} 张，零硬失败", not failures, str(failures)[:400])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ skilllint 路由判据（pi 语义）：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
