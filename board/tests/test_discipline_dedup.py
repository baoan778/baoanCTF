#!/usr/bin/env python3
r"""test_discipline_dedup.py — L5 纪律正文去重 + L6 盲派复核的回归测试

为什么单独建这一份
--------------------
L5 要守的不是「代码能跑」，而是「**同一个纪律不许抄多份**」：
  2026-11-09 实测，10 个题型 SKILL.md 各抄一份「纪律」正文，其中 ctf-kit 那份
  已经漂移（少了 `--status dropped` 半句）。改一处漏 8 处，且没人会发现。
  所以本文件守三条：
    ① 真实技能库里**不许有整节逐字重复**（skilllint 的判据）；
    ② 那个判据本身**双向有效**（正向造重复必须报、反向造差异必须不报）；
    ③ 公共正本存在，且各技能都指向它。

L6 守的是「复核员不能看见解题者的推理链」—— 判据只能查文本，所以查：
  正本里有盲派规则、派活契约里有盲派条款、且明令「不该给推理过程」。

    python3 board/tests/test_discipline_dedup.py
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = pathlib.Path(__file__).resolve().parents[2]
SKILLS = LAB / "skills"
TOOLS = LAB / "tools"

checks: list = []


def ok(name, cond, extra=""):
    checks.append((name, bool(cond), str(extra)))


def _report():
    bad = [c for c in checks if not c[1]]
    for name, good, extra in checks:
        print(f"  {'✓' if good else '✗'} {name}" + (f"   {extra[:300]}" if extra and not good else ""))
    print(f"\n通过 {len(checks) - len(bad)} / 失败 {len(bad)}")
    if bad:
        print("失败项：" + "、".join(c[0] for c in bad))
        return 1
    print("✓ L5 纪律去重 / L6 盲派复核：全部通过")
    return 0


def main() -> int:
    # ── 0. 前置 ──────────────────────────────────────────────────
    ok("tools/skilllint.py 存在", (TOOLS / "skilllint.py").is_file())
    if not (TOOLS / "skilllint.py").is_file():
        return _report()
    sys.path.insert(0, str(TOOLS))
    import importlib
    import skilllint
    importlib.reload(skilllint)

    src = (TOOLS / "skilllint.py").read_text(encoding="utf-8", errors="replace")
    ok("skilllint 有重复检测", "纪律正文重复" in src)
    ok("判据按整节比对（不是滑动窗口）", "_secs.setdefault" in src and "_flush(" in src)
    ok("指向公共正本的指针节被排除", "common-discipline.md" in src)

    # ── 1. 公共正本存在，且被各技能指向 ──────────────────────────
    canon = SKILLS / "ctf-kit" / "references" / "common-discipline.md"
    ok("公共正本 common-discipline.md 存在", canon.is_file())
    body = canon.read_text(encoding="utf-8", errors="replace") if canon.is_file() else ""
    for kw in ("先做能力检查", "产物只写当前 cwd", "结论分三级写",
               "flag 要逐字出现在真实执行输出里", "卡住先查卡、再换路线",
               "--status dropped", "机器兜底"):
        ok(f"正本含「{kw}」", kw in body, body[:200])

    # 各技能都留指针（不是各自再抄一遍）
    pointed, missing = [], []
    for d in sorted(SKILLS.iterdir()):
        sk = d / "SKILL.md"
        if not d.is_dir() or not sk.is_file():
            continue
        txt = sk.read_text(encoding="utf-8", errors="replace")
        if "## 纪律" in txt:
            (pointed if "common-discipline.md" in txt else missing).append(d.name)
    ok("有纪律节的技能都指向公共正本", not missing, "未指向：" + "、".join(missing))
    ok("至少 8 个技能已接入指针（不是空跑）", len(pointed) >= 8, str(pointed))

    # 漂移检测：ctf-kit 那份曾少 `--status dropped`，现在必须从正本读
    kit = (SKILLS / "ctf-kit" / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    ok("ctf-kit 不再自带纪律正文（只留指针）",
       "同一条死路" not in kit or "common-discipline.md" in kit, kit[:200])

    # ── 2. 判据双向有效（造数据，不碰真技能库）──────────────────
    def _run(root: pathlib.Path):
        _r, _f, w = skilllint.audit(root=str(root))
        return [x for x in w if "纪律正文重复" in x]

    body3 = ("1. **先做能力检查**：工具在不在，用 command -v 实测，别假设有这种东西。\n"
             "2. **产物只写当前 cwd**：不动系统目录、不动公共工具目录、不动别人私有区。\n"
             "3. **结论分三级写**：原始输出 → 推断值 → 已验证值，只有已验证值能当结论。\n")
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="dedup-"))
    try:
        for n in ("fake-a", "fake-b"):
            d = tmp / n
            d.mkdir()
            (d / "SKILL.md").write_text(
                "---\nname: %s\n---\n## 纪律\n\n" % n + body3, encoding="utf-8")
        ok("正向：两份逐字相同 → 必须报", len(_run(tmp)) == 1, str(_run(tmp))[:200])

        # 反向：结尾不同 → 不许报（第一版用 3 行滑动窗口时这里会误报）
        d1 = tmp / "fake-a" / "SKILL.md"
        d1.write_text(
            "---\nname: fake-a\n---\n## 纪律\n\n" + body3 +
            "4. **换面**：卡住就换攻击面，不要换 payload 细节。\n", encoding="utf-8")
        ok("反向：结尾不同 → 不报（不误伤相似但不相同的纪律）",
           len(_run(tmp)) == 0, str(_run(tmp))[:200])

        # 反向：指针节不许报（否则修法本身被判成新问题）
        shutil.rmtree(tmp)
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="dedup2-"))
        ptr = ("## 纪律（所有题型共用）\n\n"
               "> **正本：[`../ctf-kit/references/common-discipline.md`]"
               "(../ctf-kit/references/common-discipline.md)**（能力检查 / 结论三级 / flag 逐字）。\n"
               "> 本节不再抄写内容 —— 改一处漏 8 处，**读那一份就够了**，别再各抄一遍啊。\n")
        for n in ("ptr-a", "ptr-b"):
            d = tmp / n
            d.mkdir()
            (d / "SKILL.md").write_text("---\nname: %s\n---\n" % n + ptr, encoding="utf-8")
        ok("指针节内容相同 → 不报（指向正本才是正解）", len(_run(tmp)) == 0, str(_run(tmp))[:200])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ── 3. 真实技能库当前干净 ────────────────────────────────────
    _r, _f, warns = skilllint.audit(root=str(SKILLS))
    dup = [x for x in warns if "纪律正文重复" in x]
    ok("真实技能库无整节逐字重复", not dup, str(dup)[:300])

    # ── 4. L6 盲派复核 ───────────────────────────────────────────
    ver = LAB / "agent-template" / "roles" / "prompts" / "ctf-verify.md"
    ok("ctf-verify 提示词存在", ver.is_file())
    vt = ver.read_text(encoding="utf-8", errors="replace") if ver.is_file() else ""
    ok("守住盲派规则（§0）", "盲派" in vt, vt[:200])
    ok("明令「只给原始输入 + 待验结论」", "原始输入" in vt and "待验结论" in vt)
    ok("明令「不该给推理过程」", "不该给" in vt and "推理" in vt, vt[:300])
    ok("给了遇到推理过程时的处置（不是只说别给）", "请重发" in vt or "忽略" in vt)
    ok("保留三档结论（已坐实/可疑/已证伪）",
       all(k in vt for k in ("已坐实", "可疑", "已证伪")))
    ok("保留「不许默认通过」", "不许默认通过" in vt)

    par = LAB / "docs" / "并行工作.md"
    pt = par.read_text(encoding="utf-8", errors="replace") if par.is_file() else ""
    ok("派活契约里有盲派条款", "盲派" in pt)
    ok("派活契约点名 ctf-verify", "ctf-verify" in pt and "盲派" in pt)

    return _report()


if __name__ == "__main__":
    sys.exit(main())
