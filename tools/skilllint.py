#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""skilllint —— 技能库体检：链接是否断、入口是否超重、frontmatter 是否规范

存在理由（2026-10-10）：
  「优化 skill 加快解题速度」这种要求，最容易变成拍脑袋重写 —— 改完没人知道
  是变好还是变坏，甚至把链接改断、把工具名改成本机没有的。
  所以先把能机器判的判掉：
    · **断链**：SKILL.md 里 `references/xxx.md` 指向不存在的文件
      → 解题时按图索骥读不到，比没有更糟（会以为自己读过了）
    · **超重**：入口 SKILL.md 是「每次都要读的那一份」，它越大越慢；
      参考文档（references/）是按需读的，层级不能颠倒
    · **frontmatter**：name/description 缺失 → pi 的技能索引里显示不出来
  → 把这三条变成命令，并在 selfcheck 里挂闸门（防止优化完又长回去）

2026-10-12 补四条「路由判据」（借 dsh ANCHOR 的 skill_lint，但**按 pi 的真实语义重写**，
不是照搬 —— dsh 那边「嵌套不加载/whenToUse 不渲染/500 上限」是 dsh 自己的规则）：
  判据来源：pi 源码 `dist/core/skills.js`（本机实测读完）：
    · `MAX_NAME_LENGTH = 64`、`MAX_DESCRIPTION_LENGTH = 1024`（**规格硬限，不是建议值**）
    · 发现规则是「目录里有 SKILL.md 就当技能根，不再往下钻；否则**递归**找 SKILL.md」
      → 嵌套 SKILL.md **会被当成独立技能加载**（不是不加载），是个脏数据点
    · frontmatter 只认 `name` / `description` / `disable-model-invocation`，
      其余键（如 `whenToUse`）**不渲染、不参与路由** —— 写了等于没写
    · 路由全靠 `name` + `description`（系统提示原文：load a skill's file when the task
      matches its description）→ **description 里没有「何时加载」= 这张卡不会被路由到**

判据（可调）：
  · **断链 = 硬失败**（退出码 1）
  · name 不合规（非 kebab-case / >64 / 以连字符开头结尾 / 连续连字符）= **硬失败**
  · description 缺失 = 硬失败；超 1024（pi 规格）= 告警
  · description 里找不到「何时加载」判据 = 告警（路由失效风险，改了会红才算闸门，先只报）
  · 写了 `whenToUse`（pi 不渲染）= 告警
  · 两条技能 description 逐字相同 = 告警（路由打架）
  · 嵌套 SKILL.md（会被 pi 当成额外技能加载）= 告警
  · SKILL.md > CTF_BUDGET 字节 = 告警（默认 9000；越线说明该把正文挪进 references/）

用法:
  skilllint                # 全量体检
  skilllint --json         # 机器可读
  skilllint --quiet        # 只报失败/告警
"""

import argparse
import json
import os
import re
import sys

# ★ 发布包（exe / Windows）适配：控制台默认编码可能是 cp936，直接 print 中文会抛
#   UnicodeEncodeError。两只流都改成 utf-8 + replace（与 tools/kb.py、wpfetch.py 同惯用法）。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = os.environ.get("PI_LAB_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKILLS = os.path.join(LAB, "skills")
CTF_BUDGET = int(os.environ.get("PI_LAB_SKILL_BUDGET", "9000"))   # 入口 SKILL.md 字节上限
DESC_MIN = 20
# ★ 1024 是 **pi 的规格硬限**（dist/core/skills.js: MAX_DESCRIPTION_LENGTH）——
#   不是拍脑袋的建议值。原来是 1400，等于允许写一份 pi 会判不合规的 description。
PI_DESC_MAX = 1024
DESC_MAX = PI_DESC_MAX
PI_NAME_MAX = 64                                   # dist/core/skills.js: MAX_NAME_LENGTH
NAME_RE = re.compile(r"^[a-z0-9-]+$")
# 「何时加载」判据：中英各几种最常见写法。宁可漏（告警）不可错（误报会让人无视）
TRIGGER_RE = re.compile(
    r"(当[^。；\n]{0,60}时|时使用|时参考|使用时|适用于|用于|需要[^。；\n]{0,30}时"
    r"|use when|when you|when the|Use this when)", re.I)
# pi 只渲染这三个 frontmatter 键（其余键存在但不参与任何行为）
PI_FM_KEYS = ("name", "description", "disable-model-invocation")
# 上游 Agent Skills 标准的**溯源类**键：pi 确实不渲染，但它们是出处信息，不是「想当路由用」的键。
# 拿它们报「静默失效」= 一次刷 15 条同类告警 → 人会开始无视告警（比不检查更坏）。
PI_FM_IGNORED = ("license", "compatibility", "allowed-tools", "metadata", "version",
                 "author", "homepage", "repository", "tags", "category")

FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
CODE_RE = re.compile(r"`([A-Za-z0-9_./\-]+\.(?:md|py|sh|txt|json|yaml|yml))`")


def parse_fm(text):
    m = FM_RE.match(text)
    if not m:
        return None
    fm = {}
    for line in m.group(1).splitlines():
        mm = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if mm:
            fm[mm.group(1)] = mm.group(2).strip().strip('"').strip("'")
    return fm


SKIP_LINE_MARKERS = ("来源", "上游", "License", "MIT", "©", "署名", "原作者")
# 只认「像本地文件」的目标：白名单扩展名 + 排除外部工具/上游项目路径
# （`sqlmap/lib/request/connect.py`、`app/agents/*.py` 这类是别人仓库里的路径，不是我的指针）
EXT_OK = (".md", ".txt", ".py", ".sh", ".json", ".yaml", ".yml", ".c", ".cpp", ".h",
          ".js", ".ts", ".php", ".html", ".log", ".csv", ".pcap", ".conf", ".ini")
SKIP_PREFIX = ("sqlmap/", "app/", "wp-content/", "vendor/", "node_modules/", "site-packages/")
FENCE_RE = re.compile(r"(?ms)^\s*```.*?^\s*```\s*$")


def link_targets(text):
    return link_targets_impl(text)


def link_targets_impl(text):
    """返回待校验的相对路径。宁漏不错：假阳性会让我去修不存在的 bug（第一版就报了 28 条）

    排除类：
      · http(s)/锚点/mailto
      · 绝对路径（`/flag.txt` 这种是**靶场里的路径**，不是仓库文件）
      · 带占位符的（`<项目id>`）
      · 明确的产物路径（WPs/ work/ archive/），它们是运行时生成的
    """
    text = "\n".join(ln for ln in text.splitlines()
                      if not any(k in ln for k in SKIP_LINE_MARKERS))
    text = FENCE_RE.sub("", text)          # 代码块里的 `[x](y)` 是代码，不是链接
    # 缩进代码块同理（`    headers={'jku': '.../.well-known/jwks.json'}` 这种）
    text = "\n".join("" if (ln.startswith("    ") or ln.startswith("\t")) else ln
                     for ln in text.splitlines())
    out = []
    for m in LINK_RE.finditer(text):
        t = m.group(1).strip()
        if t.startswith(("http://", "https://", "#", "mailto:", "/")):
            continue
        if "<" in t or ">" in t or "{" in t or "*" in t:
            continue
        if t.startswith(("WPs/", "work/", "archive/", "log/")):
            continue
        out.append(t.split("#")[0])
    for m in CODE_RE.finditer(text):
        t = m.group(1)
        if t.startswith(("http", "/", "tools/", "board/", "docs/", "work/", "WPs/")):
            continue
        if "/" in t:
            out.append(t)
    keep = []
    for t in out:
        # 外部工具/别人仓库里的路径（sqlmap/、app/agents/…）不是我的指针
        if t.startswith(SKIP_PREFIX) or not t.lower().endswith(EXT_OK):
            continue
        if any(c in t for c in "'\"$[]()+ "):     # 含引号/美元号/运算符/空格 = 代码片段
            continue
        keep.append(t)
    return keep


def kb_roots():
    """离线 KB 根：技能里大量路径是相对 `$KB`（tools/knowledge/riftx/recommended-skills/）
    写的 —— 不认这个基准就会把 KB 里的好文件全报成断链（第一版报了 18 条假阳性）"""
    out = []
    kn = os.path.join(LAB, "tools", "knowledge")
    try:
        for d in sorted(os.listdir(kn)):
            for sub in ("recommended-skills", ""):
                p = os.path.join(kn, d, sub) if sub else os.path.join(kn, d)
                if os.path.isdir(p) and os.path.isfile(os.path.join(p, "INDEX.md")):
                    out.append(p)
                elif os.path.isdir(os.path.join(p, "recommended-skills")):
                    out.append(os.path.join(p, "recommended-skills"))
    except OSError:
        pass
    return out


def resolve(skill_dir, target, bases=None):
    """四处基准都试：技能目录 → 仓库根 → skills/ 根 → 离线 KB 根。
    技能里几种写法都有（`references/x.md` / `skills/ctf-web/references/x.md` /
    `prompts/human-hint.md` / `$KB` 相对），只按一族基准解必然误报。"""
    # ★ `references/` 自己也要当基准：references 之间常用**兄弟文件名**互引
    #   （`model-attacks.md` → 实际是 `references/model-attacks.md`）
    for base in [skill_dir, os.path.join(skill_dir, "references"), LAB,
                 os.path.join(LAB, "skills")] + (bases or []):
        p = os.path.normpath(os.path.join(base, target))
        if os.path.exists(p):
            return p
    return None


def audit(root=SKILLS):
    rows, failures, warns = [], [], []
    kbs = kb_roots()
    _descs = []                                   # (skill, description) → 查逐字重复
    for d in sorted(os.listdir(root)):
        sd = os.path.join(root, d)
        sk = os.path.join(sd, "SKILL.md")
        if not os.path.isdir(sd) or not os.path.isfile(sk):
            continue
        try:
            text = open(sk, encoding="utf-8").read()
        except OSError as e:
            failures.append("%s: 读不了 SKILL.md (%s)" % (d, e))
            continue
        size = len(text.encode())
        fm = parse_fm(text)
        refs = sorted(
            f for f in (os.listdir(os.path.join(sd, "references"))
                        if os.path.isdir(os.path.join(sd, "references")) else [])
            if f.endswith(".md")
        )
        # ── 断链 ──（SKILL.md + references/*.md 都要查：references 也是我会读的东西，
        #    第一版只扫 SKILL.md，结果藏在 references 里的坏路径漏网）
        broken = []
        for t in link_targets(text):
            if resolve(sd, t, kbs) is None:
                broken.append(("SKILL.md", t))
        for r in refs:
            rp = os.path.join(sd, "references", r)
            try:
                rtext = open(rp, encoding="utf-8").read()
            except OSError:
                continue
            for t in link_targets(rtext):
                if resolve(sd, t, kbs) is None:
                    broken.append(("references/" + r, t))
        for where, t in broken:
            failures.append("%s [%s]: 断链 %s" % (d, where, t))
        # ── frontmatter ──
        if fm is None:
            failures.append("%s: 缺 frontmatter" % d)
        else:
            for k in ("name", "description"):
                if not fm.get(k):
                    failures.append("%s: frontmatter 缺 %s" % (d, k))
            if fm.get("name") and fm["name"] != d:
                warns.append("%s: frontmatter name=%r 与目录名不一致" % (d, fm["name"]))
            # ── name 合规（pi 规格：小写 a-z0-9 与连字符，≤64，不得首尾连字符/连续连字符）──
            nm = fm.get("name") or ""
            if nm:
                bad_nm = []
                if len(nm) > PI_NAME_MAX:
                    bad_nm.append("超 %d 字符" % PI_NAME_MAX)
                if not NAME_RE.match(nm):
                    bad_nm.append("只能小写 a-z / 0-9 / 连字符")
                if nm.startswith("-") or nm.endswith("-"):
                    bad_nm.append("不能以连字符开头或结尾")
                if "--" in nm:
                    bad_nm.append("不能含连续连字符")
                if bad_nm:
                    failures.append("%s: frontmatter name=%r 不合 pi 规格（%s）"
                                    % (d, nm, "；".join(bad_nm)))
            # ── description：长度 + 「何时加载」判据（路由全靠它）──
            desc = fm.get("description", "") or ""
            dl = len(desc)
            if dl and not (DESC_MIN <= dl <= DESC_MAX):
                warns.append("%s: description %d 字符（允许 %d~%d；上限是 pi 规格，超了会被判不合规）"
                             % (d, dl, DESC_MIN, DESC_MAX))
            if desc and not TRIGGER_RE.search(desc):
                warns.append("%s: description 里找不到「何时加载」判据 —— pi 只按 name+description 路由，"
                             "缺判据 = 这张卡不会被按场景路由到（补一句「当…时使用 / Use when…」）" % d)
            # ── whenToUse 一类「看着像路由、实际不渲染」的键（静默失效）──
            # 只报未知键：上游标准的溯源键（license/...）pi 也不渲染，但那是出处信息，
            # 报它们只会把真信号淹掉。
            ghost = [k for k in fm if k not in PI_FM_KEYS and k not in PI_FM_IGNORED]
            if ghost:
                warns.append("%s: frontmatter 里的 %s pi 不渲染（只认 %s）—— 判据要写进 description"
                             % (d, "、".join("`%s`" % k for k in ghost), "/".join(PI_FM_KEYS)))
            if desc:
                _descs.append((d, desc))
        # ── 体量 ──
        if size > CTF_BUDGET:
            warns.append("%s: SKILL.md %d 字节 > %d（正文该挪进 references/）" % (d, size, CTF_BUDGET))
        # ── 未被引用的参考（信息性）──
        mentioned = " ".join(link_targets(text))
        orphan = [r for r in refs if r not in mentioned]
        rows.append({
            "skill": d, "bytes": size, "tok": size // 3, "refs": len(refs),
            "orphan": len(orphan), "broken": len(broken),
            "desc": len(fm.get("description", "")) if fm else 0,
        })

    # ── description 逐字重复（两本规则书，路由打架）──
    _seen = {}
    for d, desc in _descs:
        if desc in _seen:
            warns.append("%s: description 与 %s 逐字相同 —— pi 按 description 路由，两份会打架"
                         % (d, _seen[desc]))
        else:
            _seen[desc] = d

    # ── 纪律正文重复（L5，2026-11-09）──
    # 为什么查这个：9 份 SKILL.md 曾各抄一份「纪律」正文，其中 ctf-kit 那份**已经漂移**
    # （少了 `--status dropped` 半句）—— 改一处漏 8 处，而且没人会发现。
    #
    # ★ 判据按**整节**而不是滑动窗口（第一版用 3 行窗口，反向对照立刻误报：
    #   两份纪律正文前半段相同、结尾不同，3 行窗口只在相同的那半边命中，
    #   于是「不同」被判成「重复」。整节比对才是真判据；文本略改即视为不同，
    #   这正是想要的 —— 我们要抓的是**逐字抄**，不是「意思差不多」。
    _secs = {}
    for cur, _dirs, files in os.walk(root):
        if "SKILL.md" not in files:
            continue
        sk2 = os.path.join(cur, "SKILL.md")
        try:
            raw = open(sk2, encoding="utf-8").read()
        except OSError:
            continue
        _rel = os.path.relpath(sk2, root)
        # 切出每个二级标题下的整节正文（去掉首行标题本身）
        _cur_title, _buf = None, []
        def _flush(_t, _b, _rel=_rel):
            if _t and _b:
                _key = "\n".join(x.strip() for x in _b if x.strip())
                if len(_key.encode()) < 200:       # 太小的节不算（frontmatter/一行说明）
                    return
                # ★ 指向公共正本的**指针节**要排除：它们本来就该逐字一样，
                #   否则检测器会把自己推荐的修法（「抽正本、各家留指针」）判成新问题。
                if "common-discipline.md" in _key or "shared-playbook.md" in _key:
                    return
                _secs.setdefault(_key, []).append((_rel, _t))
        for _ln in raw.splitlines():
            if _ln.startswith("## "):
                _flush(_cur_title, _buf)
                _cur_title, _buf = _ln[3:].strip(), []
            elif _cur_title:
                _buf.append(_ln)
        _flush(_cur_title, _buf)
    _dup = {}
    for _k, _v in _secs.items():
        _skills = sorted({r for r, _t in _v})
        if len(_skills) >= 2:
            _dup[_k] = (_skills, _v[0][1])
    if _dup:
        _worst = max(_dup.values(), key=lambda sv: len(sv[0]))
        warns.append(
            "纪律正文重复：%d 个整节在多个技能的 SKILL.md 里**逐字**出现"
            "（最严重：节「%s」出现在 %d 个技能里）→ 抽成 references 里的公共正本，"
            "各技能只留指针（不然改一处漏 N 处）"
            % (len(_dup), _worst[1], len(_worst[0])))


    # ── 嵌套 SKILL.md（pi 会递归找，所以它会被当成**额外技能**加载，不是不加载）──
    for cur, _dirs, files in os.walk(root):
        if "SKILL.md" not in files:
            continue
        rel = os.path.relpath(cur, root)
        if sep_needed(rel):
            warns.append("%s: 嵌套的 SKILL.md 会被 pi 递归扫到并当成**独立技能**加载"
                         "（像脏数据；若只是参考文档请改名，否则它会出现在技能目录里）" % rel)
    return rows, failures, warns


def sep_needed(rel: str) -> bool:
    """rel 比根深（含路径分隔符）→ 是嵌套的 SKILL.md。"""
    return rel not in (".", "") and (os.sep in rel or "/" in rel)


def main():
    ap = argparse.ArgumentParser(description="技能库体检（断链/超重/frontmatter/路由判据）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true", help="只报失败与告警")
    a = ap.parse_args()

    rows, failures, warns = audit()
    total_tok = sum(r["tok"] for r in rows)
    if a.json:
        print(json.dumps({"rows": rows, "failures": failures, "warns": warns}, ensure_ascii=False, indent=2))
    else:
        if not a.quiet:
            print("%-16s %8s %7s %5s %5s %5s  %s" % ("skill", "字节", "≈tok", "refs", "断链", "未引用", "desc"))
            for r in sorted(rows, key=lambda r: -r["bytes"]):
                flag = " ←超重" if r["bytes"] > CTF_BUDGET else ""
                print("%-16s %8d %7d %5d %5d %5d  %4d%s"
                      % (r["skill"], r["bytes"], r["tok"], r["refs"], r["broken"], r["orphan"], r["desc"], flag))
            print("\n入口合计 ≈%d tok（按需读；只有当前路线那 1~2 份进上下文）" % total_tok)
        for f in failures:
            print("✗ " + f)
        for w in warns:
            print("! " + w)
        if not failures and not warns:
            print("✓ 技能库体检通过（断链 0 / frontmatter 齐 / 入口都 ≤%d 字节）" % CTF_BUDGET)
        elif not failures:
            print("✓ 无硬失败（%d 条告警）" % len(warns))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
