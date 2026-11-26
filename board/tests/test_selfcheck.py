#!/usr/bin/env python3
"""工程底座回归：`tools/bin/selfcheck` 与 `.pi/agent/extensions/lab-preflight.ts`。

跑法：
    python3 board/tests/test_selfcheck.py

为什么要有这个文件
------------------
2026-09-28 把两件「防静默故障」的东西从人变成脚本：
  1. `tools/bin/selfcheck` —— 技能软链断了**零报错**，以前只能靠人读快照手敲 6 条命令
  2. `.pi/agent/extensions/lab-preflight.ts` —— 启动时查同一条软链

问题在于：**检测器本身没有测试**。如果哪天有人把 selfcheck 改坏（比如判据从
`grep -E '\\.mkdir\\(\\)'` 改成裸匹配 `flock` 这个只在注释里出现的词），它会一直报
假警告，然后所有人开始无视警告 —— 那它就跟不存在一样。

所以这里干两件事：
  * **正向**：健康仓库里 `--quick` 必须绿、9 组必须全都跑到
  * **反向**：**故意造一个坏样本**（软链指向不存在的目标），selfcheck 必须变红
    且指出「技能」——「不会红的检测器」才是真故障

另外锁两条**安全不变量**（人容易顺手改掉）：
  * `.gitignore` 必须继续挡住密钥与会话：`.env` / `auth.json` / `models.json` /
    `sessions/` / `.pi/agent/npm/` / `tools/vendor/`
  * `.pi/agent/extensions/lab-preflight.ts` 不许出现 `setEditorComponent`
    （think-clock 踩过：替换编辑器会成为 TUI 崩溃源）

测试**不碰真产物**：坏样本一律在 `tempfile.mkdtemp()` 里造。
"""
import os
import shutil
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _pkgpaths import require_ext, find_node  # noqa: E402

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
#   自包含（自己 import sys），不依赖本文件是否已 import sys 或用别名。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BOARD = Path(__file__).resolve().parents[1]
LAB = BOARD.parent
SELF = LAB / "tools" / "bin" / "selfcheck"
PREFLIGHT = require_ext("lab-preflight.ts")
# ★ 跨平台 file URL：直接拼 "file://" + 裸路径在 Windows 上会产出
#   file://D:\... （反斜杠被当转义）→ node 报 ERR_INVALID_URL。
#   Path.as_uri() 在两边都合法：Windows→file:///D:/...，POSIX→file:///mnt/...。
PREFLIGHT_URI = require_ext("lab-preflight.ts").as_uri()

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail and not cond else ""))


def run_selfcheck(lab_script, *args, env=None):
    e = dict(os.environ)
    e.update(env or {})
    p = subprocess.run(
        ["bash", str(lab_script), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", env=e, timeout=300
    )
    return p.returncode, p.stdout, p.stderr


def make_broken_lab(*, broken_link: bool, n_skills: int) -> Path:
    """造一个假 lab：只有 selfcheck 脚本 + skills/ + .pi/skills。

    broken_link=True  → 软链指向不存在的目标（这就是「断了但零报错」的形态）
    broken_link=False → 软链正确，skills/ 下有 n_skills 个真 skill（不该报警）
    """
    tmp = Path(tempfile.mkdtemp(prefix="pilab-selfcheck-"))
    (tmp / "tools" / "bin").mkdir(parents=True)
    shutil.copy2(SELF, tmp / "tools" / "bin" / "selfcheck")
    skills = tmp / "skills"
    skills.mkdir()
    for i in range(n_skills):
        d = skills / f"fake-skill-{i:02d}"
        d.mkdir()
        (d / "SKILL.md").write_text(f"# fake {i}\n", encoding="utf-8")
    (tmp / ".pi").mkdir()
    link = tmp / ".pi" / "skills"
    if broken_link:
        link.symlink_to("/nonexistent/skills-target")
    else:
        link.symlink_to("../skills")
    return tmp


def main():
    # ★ 本用例整体是**在测 tools/bin/selfcheck 这个 POSIX shell 脚本**：
    #   它自己内部就调 wsl/python3/skilllint 等本机工具链，还依赖 /dev/*、软链、可执行位。
    #   Windows 侧实测 rc=127（wsl: 无法将"localhost"解析…）——测的不是判据而是平台有没有装备。
    #   → 非 POSIX 平台整体 SKIP（77），跳过 ≠ 通过。
    if os.name != "posix":
        print("SKIP tools/bin/selfcheck 是 POSIX shell 脚本（本平台无 wsl 工具链）")
        return 77
    print("1) 脚本基本形态")
    check("tools/bin/selfcheck 存在", SELF.exists(), str(SELF))
    check("tools/bin/selfcheck 可执行", os.access(SELF, os.X_OK))
    rc, out, err = run_selfcheck(SELF, "--help")
    check("--help 退出码 0", rc == 0, f"rc={rc} {err[:160]}")
    check("--help 提到 --quick", "--quick" in (out + err), (out + err)[:160])
    rc, _, _ = run_selfcheck(SELF, "--no-such-flag")
    check("未知参数退出码非 0（不静默吞掉）", rc != 0, f"rc={rc}")

    print("2) 正向：健康仓库里必须全绿、9 组全跑")
    # ★ 2026-11-25：这一节断言「**健康仓库**里 selfcheck --quick 必须 rc=0」，
    #   而"健康"隐含「依赖已装好」。CI 是干净 clone（无 tools/venv*、无 py 弹药、
    #   无 kb 索引），selfcheck 必然报「弹药不可用」——那不是脚本坏了，是环境没装。
    #   判据：两个 venv 都不在 → 跳过本节（本地开发机有 venv-win，照跑）。
    _venv_ok = (LAB / "tools" / "venv").exists() or (LAB / "tools" / "venv-win").exists()
    if not _venv_ok:
        print("   · 跳过本节：本机无 tools/venv*（干净 clone / 发布包形态）")
        rc = out = err = None
    else:
        rc, out, err = run_selfcheck(SELF, "--quick")
        check("--quick 退出码 0", rc == 0, f"rc={rc}\n{(out + err)[-400:]}")
        check("输出里有结论行", "结论:" in out, out[-200:])
        missing = [f"{i}/9" for i in range(1, 10) if f"{i}/9" not in out]
        check("9 组检查全部执行", not missing, f"缺：{missing}")
        for _cat in ("pwn", "reverse", "crypto", "misc", "forensics", "web"):
            check(f"CTF 题型体检报到：{_cat}", _cat in out, out[-300:])
        check("CTF 体检给出 py 弹药结论", "py 弹药" in out, out[-300:])
        check("--quick 明确说跳过了测试（不假装跑了）", "跳过" in out, out[:400])

        # ── 常驻提示词预算（4c，2026-10-05 加）：AGENTS.md 每轮都进上下文，防再臃肿 ──
        # ★ 2026-11-11 修：原断言找的是「常驻提示词预算」—— 那个字串只在 **OK 分支** 出现。
        #   AGENTS.md 一进 warn 区间（>18500）文案就变成「常驻提示词偏胖」，断言立刻假红；
        #   进 hard 区间则变成「常驻提示词臃肿」。三种分支都是**正确行为**，断言不该绑死一种文案。
        #   改成匹配共同前缀「常驻提示词」，再各自核对档位判据。
        check("--quick 报出常驻提示词预算", "常驻提示词" in out, out[-400:])
    _ag = LAB / "AGENTS.md"
    if _ag.exists():
        _ag_b = _ag.stat().st_size
        check(f"真实 AGENTS.md 在软预算内（{_ag_b} ≤ 22000 字节）", _ag_b <= 22000, f"{_ag_b} 字节")
        # 反面：胖 AGENTS.md 必须真会红（不是写了不挂）
        tmp_fat = make_broken_lab(broken_link=False, n_skills=16)
        try:
            (tmp_fat / "AGENTS.md").write_text("x" * 27000, encoding="utf-8")
            rc_f, out_f, err_f = run_selfcheck(tmp_fat / "tools" / "bin" / "selfcheck", "--quick")
            both_f = out_f + err_f
            check("胖 AGENTS.md → 报「常驻提示词臃肿」", "常驻提示词臃肿" in both_f, both_f[-300:])
            check("胖 AGENTS.md → 退出码 1（硬红）", rc_f == 1, f"rc={rc_f}")
            check("胖 AGENTS.md 的修法指向 docs/经验", "docs/经验" in both_f, both_f[-300:])
        finally:
            shutil.rmtree(tmp_fat, ignore_errors=True)

    print("3) 反向：故意造坏样本，检测器必须变红")
    tmp_broken = make_broken_lab(broken_link=True, n_skills=0)
    try:
        rc, out, err = run_selfcheck(tmp_broken / "tools" / "bin" / "selfcheck", "--quick")
        both = out + err
        check("软链断掉时退出码 1（真会红）", rc == 1, f"rc={rc}")
        check("报错指向「技能」", "技能" in both, both[-300:])
        check("给出了修法（ln -sfn）", "ln -sfn" in both, both[-300:])
    finally:
        shutil.rmtree(tmp_broken, ignore_errors=True)

    print("4) 反向对照：软链正常 + 15 个 skill → 不许误报技能")
    tmp_ok = make_broken_lab(broken_link=False, n_skills=15)
    try:
        rc, out, err = run_selfcheck(tmp_ok / "tools" / "bin" / "selfcheck", "--quick")
        both = out + err
        check(
            "软链正常时不误报技能问题",
            "✓ 软链在（../skills），pi 可加载 15 个 skill" in both,
            both[:500],
        )
        check("空 lab 里仍能跑完（缺黑板/仓库只警告不崩）", "结论:" in out, both[-300:])
    finally:
        shutil.rmtree(tmp_ok, ignore_errors=True)

    print("5) 安全不变量：.gitignore 必须继续挡住密钥与会话")
    if not (LAB / ".git").exists():
        # ★ 2026-10-30：发布包不含 .git（买家拿到的是导出目录）—— 这时 git 判据无法执行，
        #   但**不可提交性**仍然要守住，所以退回「静态检查 .gitignore 规则」而不是判红。
        #   静默跳过也不行：打印出来，说清降级成了什么判据。
        print("    (无 .git —— 发布包形态：git 判据降级为 .gitignore 静态检查)")
        gi = LAB / ".gitignore"
        check(".gitignore 存在（无 git 时的唯一防线）", gi.is_file())
        rules = gi.read_text(encoding="utf-8").splitlines() if gi.is_file() else []
        flat = "\n".join(rules)
        for must in (".env", "auth.json"):
            check(f".gitignore 仍挡住 {must}", must in flat, flat[:200])
        for must in ("tools/venv/", "sessions"):
            check(f".gitignore 仍挡住 {must}", must in flat, flat[:200])
    else:
        for rel in (
            ".env",
            ".pi/agent/auth.json",
            ".pi/agent/models.json",
            ".pi/agent/sessions/x.jsonl",
            ".pi/agent/missions/x.json",
            ".pi/agent/npm/whatever.js",
            # ★ 2026-11-25 修两处过时判据（CI 上才暴露：容器/发布包无 .git 走降级分支，
            #   本地 Windows 又 SKIP，所以这两条一直没被真跑过）：
            #   1. `tools/vendor/x.py` —— tools/vendor **是合法跟踪目录**
            #      （Cryptodome/ratelimit/termcolor/jwt_tool 共 14 个第三方 shim 文件），
            #      它本来就不该被 ignore。原判据把"第三方"和"不该入库"混为一谈了。
            #   2. `archive/...` —— .gitignore 里的规则是 `.archive-*/`（带前导点），
            #      测试却写 `archive/`（无点），永远匹配不上。改成真实规则形态。
            "tools/venv/bin/python",
            ".archive-20260101-0000/a.jsonl",
        ):
            p = subprocess.run(
                ["git", "check-ignore", "-v", rel], cwd=LAB, capture_output=True, text=True, encoding="utf-8", errors="replace"
            )
            out = (p.stdout or "") + (p.stderr or "")
            # `.pi/agent/npm` 现在是指向 /root/pi-lab-npm（ext4）的**软链**：git 不穿越软链，
            # 对链内路径直接报 `beyond a symbolic link`（rc=128）——
            # 这比「被 ignore」更彻底（链内文件根本进不了版本库），所以两种都算通过。
            # 判据写成「能不能提交」而不是「是不是 ignore」，否则换个存法就又假红（2026-10-10 踩过）
            beyond = "beyond a symbolic link" in out
            check(
                f"不可提交：{rel}",
                p.returncode == 0 or beyond,
                "软链内路径（git 不穿越，无法提交）" if beyond else "已进版本库风险",
            )
        tracked = subprocess.run(
            ["git", "ls-files"], cwd=LAB, capture_output=True, text=True, encoding="utf-8", errors="replace"
        ).stdout.splitlines()
        check("auth.json 未被跟踪", ".pi/agent/auth.json" not in tracked)
        check("models.json 未被跟踪", ".pi/agent/models.json" not in tracked)
        check(
            "没有 sessions/ 下的文件被跟踪",
            not any(t.startswith(".pi/agent/sessions/") for t in tracked),
        )

    print("7) 不变量：测试不许碰真产物（在真 board/projects 里建项目的，一律不算隔离）")
    # 2026-09-29 踩过的坑：6 个测试文件（cli_smoke / locking / lead / matrix_fuzz /
    # outbox / snapshot）把临时项目建到了**真的** board/projects 里，而仓库文档
    # 却写着“测试别碰真产物”。后果：4 路并行时 test_handover 在数真目录里几块黑板，
    # 一会 4、一会 6 → 偶发红，还被误当成 drvfs 负载抖动记进了状态快照。
    # 判据（静态、不跑东西）：只要跑 `fgs init`，就必须用 PI_LAB_BOARD_DIR 隔离。
    tests_dir = BOARD / "tests"
    # 跳过自己：本文件里就写着这两个模式串（否则自匹配，永远红）
    test_files = [t for t in sorted(tests_dir.glob("test_*.py"))
                  if t.name != "test_selfcheck.py"]
    polluters = []
    # ★ 2026-11-24 修误报：判据原来是「源码里有 "init" 且没有 PI_LAB_BOARD_DIR」，
    #   而新写的 test_handoff_card.py 里有 `git init` —— 与 fgs 黑板毫无关系，
    #   却被判成「没隔离黑板目录」。判据必须只认**fgs 的 init 子命令**。
    #   判据：出现 fgs 调用 且 该调用里有 init 参数。
    import re as _re
    _FGS_INIT = _re.compile(r'fgs["\'\s,]+["\']?init', _re.I)
    for t in test_files:
        src_t = t.read_text(encoding="utf-8")
        if _FGS_INIT.search(src_t) and "PI_LAB_BOARD_DIR" not in src_t:
            polluters.append(t.name)
    check("跑 fgs init 的测试都隔离了黑板目录", not polluters, f"没隔离：{polluters}")
    for pattern in ('ROOT / "projects"', "PDIR = ROOT"):
        hits_p = [t.name for t in test_files if pattern in t.read_text(encoding="utf-8")]
        check(f"没有测试硬写真路径（{pattern}）", not hits_p, f"命中：{hits_p}")

    print("6) 不变量：启动自检扩展不许碰 TUI")
    check("lab-preflight.ts 存在", PREFLIGHT.exists(), str(PREFLIGHT))
    src = PREFLIGHT.read_text(encoding="utf-8") if PREFLIGHT.exists() else ""
    # 判据是「真的调用了」——注释里提到这个方法名是允许的（那份注释正是为了记录踩过的坑）
    check(
        "没有调用 setEditorComponent（think-clock 踩过的 TUI 崩溃源）",
        "setEditorComponent(" not in src,
    )
    check("注册了 session_start 钩子", 'pi.on("session_start"' in src)
    check("注册了 /selfcheck 命令", 'registerCommand("selfcheck"' in src)
    # ★ 2026-11-02 补（实测坐实的「永远亮的红灯」）：技能的真实加载位置是
    #   <agent-dir>/skills（pi docs/configuration.md 资源表），不是 <LAB>/.pi/skills
    #   （仓库里根本没有 .pi 目录）。老实现只查后者 → 每次启动都报
    #   「⛔ 技能一个都没加载」，而当时 16 个技能正常在场（阈值 15）。
    #   永远亮的红灯 = 没有红灯：真断链会被当成"又是那条老误报"划过去。
    #   这里锁死「必须按 pi 的口径解析 agent-dir」，不许退回单一写死路径。
    # ★ 判据必须锚在 SKILL_DIRS 这一行本身，不能用全文件子串 ——
    #   第一版写成 `'path.join(AGENT_DIR, "skills")' in src` 是**假守卫**：
    #   SYMLINK_HINT 那行（第 52 行）也含同样的子串，把 SKILL_DIRS 改回写死
    #   `<LAB>/.pi/skills` 后断言照样绿（实测变异测试 42/0 通过）。
    #   现在用正则锚定 `const SKILL_DIRS = [...]` 整行，并要求该行同时含 AGENT_DIR
    #   **且**不含 `path.join(LAB, ".pi", "skills")` 作为首选（仅允许兼容兜底）。
    skill_dirs_line = next(
        (ln for ln in src.splitlines() if ln.strip().startswith("const SKILL_DIRS")), ""
    )
    check(
        "技能路径首选 agent-dir（不是写死 <LAB>/.pi/skills）",
        'path.join(AGENT_DIR, "skills")' in skill_dirs_line
        and skill_dirs_line.index('path.join(AGENT_DIR, "skills")')
        < skill_dirs_line.find('path.join(LAB, ".pi", "skills")'),
        f"SKILL_DIRS={skill_dirs_line.strip()[:140]}",
    )
    check(
        "crashes.json 也从 agent-dir 读（同一处路径口径）",
        'path.join(AGENT_DIR, "crashes.json")' in src,
    )
    node = find_node()
    if node:
        r = subprocess.run(
            [node, "--experimental-strip-types", "--input-type=module", "-e",
             f'const m = await import("{PREFLIGHT_URI}"); '
             f'if (typeof m.default !== "function") {{ console.error("default 不是函数"); process.exit(1); }}'],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        check("能被 node 加载且 default 是函数", r.returncode == 0, (r.stderr or "")[-200:])
    else:
        print("    (跳过语法加载：本机没有 node)")

    print("6) 包装脚本可换目录跑（发布包 / exe 适配）")
    # 装到 exe 分发包 / 换台机器时，仓库根路径完全不同。包装脚本必须**自解析位置**，
    # 不能写死旧的发行方绝对路径 —— 写死的话命令会报「找不到实现」而看不出根因。
    tmp_lab = Path(tempfile.mkdtemp(prefix="pilab-reloc-"))
    try:
        (tmp_lab / "board").mkdir(parents=True)
        (tmp_lab / "tools" / "hand").mkdir(parents=True)
        shutil.copy2(LAB / "board" / "fgs.py", tmp_lab / "board" / "fgs.py")
        shutil.copy2(LAB / "tools" / "hand" / "fgs", tmp_lab / "tools" / "hand" / "fgs")
        (tmp_lab / "tools" / "hand" / "fgs").chmod(0o755)
        wrapper = tmp_lab / "tools" / "hand" / "fgs"
        src = wrapper.read_text(encoding="utf-8")
        # 注释里可以提历史，但**可执行行**不许再出现绝对路径
        live = [ln for ln in src.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
        # 判据改成**通用正则**：任何绝对路径形式的 board/fgs.py（旧发行方路径、容器路径、别的机器路径）
        # 都算硬编码。写死具体字符串的话，换个旧路径就漏检了。
        HARDCODED = re.compile(r"/(?:mnt|opt|home|root|usr)/[^\\s\"']*board/fgs\\.py")
        check("包装脚本可执行行里没有写死的仓库路径",
              not any(HARDCODED.search(ln) for ln in live), str(live)[:200])
        r = subprocess.run(["sh", str(wrapper), "--help"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        check("换目录后 --help 仍能跑（rc=0）", r.returncode == 0, (r.stdout + r.stderr)[:200])
        check("换目录后确实是这份 fgs（不是别的）", "step-reclaim" in (r.stdout + r.stderr),
              (r.stdout + r.stderr)[:200])
    finally:
        shutil.rmtree(tmp_lab, ignore_errors=True)

    print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ 工程底座（selfcheck + preflight）：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
