#!/usr/bin/env python3
"""S12 回归：kb import-markdown 的解析规则（首个 # 作标题 / 其后 > 行作摘要 / 余下为正文）。

    python3 board/tests/test_kb_import.py

为什么测这个：本库有 ≈2.49 万块索引，但此前**没有「从一篇 WP/笔记快速规范入库」的规则**
（全靠人手写），写出来的标题形态五花八门，检索时对不上。抄 sapper `kb.py:177-209`
的三条规则补上，本测试锁住它。

不变量：
  A. 首个 `# ` 行 → title；没有则用文件名（连字符/下划线转空格）
  B. 紧随其后的首个非空行若以 `>` 开头 → description
  C. 其余 → detail
  D. ★ 摘要**不许在正文里重复**（踩过：detail 从 body_start 起截，把 `> 摘要` 又算了一遍）
  E. `## ` 是正文章节，**不许**当成标题（否则结构被压平）
  F. 默认只预览、不落盘；`--write` 才写
  G. 有界读：目录 / 非普通文件必须被拒（仓库纪律：不许 `Path(用户路径).read_text()`）
  H. 落盘结果能被 `parse_markdown_entry` 再解析回同样的 title/description（幂等）
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent.parent
KB = ROOT / "tools" / "kb.py"
passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail and not cond else ""))


def run(*args):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PI_LAB_ROOT"] = str(ROOT)
    p = subprocess.run([sys.executable, str(KB), *args],
                       capture_output=True, env=env, timeout=120)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


def w(tmp: Path, name: str, body: str) -> Path:
    f = tmp / name
    f.write_text(body, encoding="utf-8")
    return f


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="kbimport-"))
    try:
        print("1) 三条解析规则")
        f = w(tmp, "wp.md",
              "# 泛微 e-cology SQL 注入实战\n\n"
              "> 通过 WorkflowServiceXml 未授权注入，可 dump 管理员哈希\n\n"
              "## 环境\n\n目标版本 9.0。\n")
        rc, out = run("import-markdown", str(f))
        check("预览 rc=0", rc == 0, f"rc={rc} :: {out[:200]}")
        check("★ title 取首个 # 行", "泛微 e-cology SQL 注入实战" in out, out[:300])
        check("★ description 取 > 行", "通过 WorkflowServiceXml 未授权注入" in out)
        check("detail 含正文章节", "目标版本 9.0" in out or "环境" in out)
        check("★ 默认不落盘", not (tmp / "wp.kb.md").exists())

        print("2) ★ 摘要不许在正文重复（曾经的 bug）")
        rc, _ = run("import-markdown", str(f), "--write")
        check("--write rc=0", rc == 0)
        dst = tmp / "wp.kb.md"
        check("落盘文件存在", dst.exists(), str(dst))
        if dst.exists():
            body = dst.read_text(encoding="utf-8")
            n = body.count("通过 WorkflowServiceXml 未授权注入")
            check("★ 摘要只出现一次", n == 1, f"出现 {n} 次\n{body[:400]}")
            check("首行是 title", body.splitlines()[0].startswith("# 泛微"))
            check("第二节是摘要引述", body.splitlines()[2].startswith("> "))

        print("3) 无标题 → 回落文件名（下划线/连字符转空格）")
        f2 = w(tmp, "my_wp_note.md", "没有标题的笔记\n\n正文。\n")
        rc, out = run("import-markdown", str(f2))
        check("rc=0", rc == 0, f"rc={rc}")
        check("★ 文件名转空格", "my wp note" in out, out[:300])

        print("4) 只有标题 / 有标题无摘要")
        f3 = w(tmp, "onlytitle.md", "# 只有标题\n")
        rc, out = run("import-markdown", str(f3))
        check("只有标题 rc=0", rc == 0)
        check("title 正确", "只有标题" in out)
        check("说明无摘要", "无 ——" in out or "不是 > 引述块" in out, out[:300])

        f4 = w(tmp, "nodesc.md", "# 标题A\n正文第一行不是引述\n")
        rc, out = run("import-markdown", str(f4))
        check("无摘要 rc=0", rc == 0)
        check("★ 不把正文误当摘要", "正文第一行不是引述" not in out.split("description")[1][:60]
              if "description" in out else True, out[:300])

        print("5) ★ ## 不许当标题")
        f5 = w(tmp, "noH1.md", "## 这是二级标题\n\n正文。\n")
        rc, out = run("import-markdown", str(f5))
        check("rc=0", rc == 0)
        check("★ ## 未被当作 title", "这是二级标题" not in out.split("title")[1][:40]
              if "title" in out else True, out[:300])
        check("★ 回落到文件名", "noH1" in out, out[:300])

        print("6) 有界读：非普通文件必须被拒（仓库纪律）")
        rc, out = run("import-markdown", str(tmp))
        check("★ 目录被拒", rc == 2, f"rc={rc} :: {out[:200]}")
        if Path("/dev/zero").exists():
            rc, out = run("import-markdown", "/dev/zero")
            check("★ /dev/zero 被拒", rc == 2, f"rc={rc} :: {out[:200]}")
        else:
            print("  (跳过 /dev/zero：本平台没有)")

        print("7) 幂等：落盘结果能被再解析回同样 title/description")
        sys.path.insert(0, str(ROOT / "tools"))
        import kb as kbmod
        if dst.exists():
            again = kbmod.parse_markdown_entry(dst.read_text(encoding="utf-8"))
            check("★ 二次解析 title 一致", again["title"] == "泛微 e-cology SQL 注入实战",
                  repr(again["title"]))
            check("★ 二次解析 description 一致",
                  again["description"] == "通过 WorkflowServiceXml 未授权注入，可 dump 管理员哈希",
                  repr(again["description"]))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failed:
        print(f"通过 {len(passed)} / 失败 {len(failed)}")
        print("失败项：" + "、".join(failed[:10]))
        return 1
    print(f"通过 {len(passed)} / 失败 0")
    print("✓ kb import-markdown（S12）：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
