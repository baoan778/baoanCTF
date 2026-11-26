#!/usr/bin/env python3
"""doccmd_check —— 校验「文档里写的命令，本机是否真的有」

存在理由（2026-10-03 实测踩到）：
  我把外部 skill 的工具名抄进 playbooks 而**没验证本机有没有** —— 结果 5 个工具
  （subfinder/dnsx/gitleaks/h2csmuggler/ysoserial）+ 3 个 DNS 工具（dig/host/nslookup）
  本机都不存在。这正是我批评过别人的毛病（「抄来跑不了 = 没学到」）。
  → 把「适配」从口头承诺变成可执行检查。

判定：
  ✓ 在 PATH / py 模块 / 仓库内脚本        → OK
  ⚠ 不存在但文档里**标了「本机没有」**      → OK（有意标注）
  ✗ 不存在且**无标注**                     → 要修（换等价物 或 加标注）

只扫 ```bash / ```sh 代码块（散文里提到的工具名不算「命令」）。
"""
import os
import re
import shutil
import subprocess
import sys

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
#   自包含（自己 import sys），不依赖本文件是否已 import sys 或用别名。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(LAB, "tools", "venv", "bin", "python")
# 默认只扫 SRC 范围（CTF 技能卡里的 wasm2c/x64dbg/zbarimg 等属逆向取证，不属 SRC 适配）
SCAN_SRC = [os.path.join(LAB, "skills", "src-recon"), os.path.join(LAB, "tools", "TOOLS.md"),
            os.path.join(LAB, "AGENTS.md")]
SCAN_ALL = [os.path.join(LAB, "skills"), os.path.join(LAB, "tools", "TOOLS.md"),
            os.path.join(LAB, "AGENTS.md")]
SCAN = SCAN_ALL if "--all" in sys.argv else SCAN_SRC
# 已知误报（从代码块里抠出来的非命令片段）
FALSE_POS = {"with", "xref_idx", "xref_section", "a0", "a1", "a2", "a3",
             "EOF", "except", "import", "def", "class", "try", "raise", "pass"}
MAX_DOC = 512 * 1024        # 单文件上限（仓库硬规矩：文件读取要限长）

# shell 内建/常用非工具词，不当命令
SKIP = set("""if then else fi for do done while case esac function return echo cd ls cat head tail
grep sed awk sort uniq wc cut tr xargs printf test set export source local read exit true false
sudo timeout env sleep kill pgrep pkill which command type basename dirname mkdir rm cp mv touch
chmod find xargs tee less more vi nano curl wget git python python3 py bash sh zsh node npm
docker nc socat openssl base64 xxd jq vim""".split())
# 只在「命令位置」出现的、要检查的判据：行首第一个词且像工具名
CMD_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_.+-]{1,30})(?:\s|$)")

def read_capped(path, limit=MAX_DOC):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read(limit)

def md_files():
    out = []
    for root in SCAN:
        if os.path.isfile(root):
            out.append(root)
        else:
            for dp, _dn, fn in os.walk(root):
                out += [os.path.join(dp, f) for f in fn if f.endswith(".md")]
    return out

def commands_in(text):
    """只取 ```bash / ```sh 代码块里、行首的候选命令"""
    cmds, inblk = set(), False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("```"):
            lang = s[3:].strip().lower()
            inblk = lang in ("bash", "sh", "shell", "console")
            continue
        if not inblk or not s or s.startswith("#"):
            continue
        s = re.sub(r"^\$\s+", "", s)                 # 去掉提示符
        m = CMD_RE.match(s)
        if not m:
            continue
        tok = m.group(1)
        if tok in SKIP or tok in FALSE_POS or tok.startswith("-") or "/" in tok:
            continue
        cmds.add(tok)
    return cmds

def marked_absent(docs, tool):
    """文档里是否显式标注过「本机没有 X」"""
    pat = re.compile(r"(本机|WSL|系统)[^。\n]{0,24}(没有|未装|不在 PATH)[^。\n]{0,30}"
                     + re.escape(tool) + r"|" + re.escape(tool) + r"[^。\n]{0,30}(本机没有|未装|不在 PATH)", re.I)
    return any(pat.search(d) for d in docs)

def is_available(tool):
    if shutil.which(tool):
        return True
    for p in (os.path.join(LAB, "tools", tool), os.path.join(LAB, "tools", "bin", tool)):
        if os.path.exists(p):
            return True
    if os.path.exists(VENV_PY):
        r = subprocess.run([VENV_PY, "-c", f"import {tool}"], capture_output=True, timeout=20)
        if r.returncode == 0:
            return True
    return False

def main():
    files = md_files()
    docs = [read_capped(f) for f in files]
    all_cmds = set()
    for d in docs:
        all_cmds |= commands_in(d)
    ok, marked, bad = [], [], []
    for t in sorted(all_cmds):
        if is_available(t):
            ok.append(t)
        elif marked_absent(docs, t):
            marked.append(t)
        else:
            bad.append(t)
    print("══ doccmd_check · 文档命令适配检查 ══")
    print(f"  扫描 {len(files)} 个 md 文件，bash 代码块里共 {len(all_cmds)} 个候选命令")
    print(f"  ✓ 本机可用（PATH / py 模块 / 仓库脚本）：{len(ok)}")
    print(f"  ⚠ 已显式标注「本机没有」：{len(marked)}" + (f"  [{', '.join(marked)}]" if marked else ""))
    if not bad:
        print("  ✗ 无标注且不存在：0  ✓ 全部适配")
        return 0
    print(f"  ✗ 无标注且不存在：{len(bad)}")
    for t in bad:
        print(f"      {t}")
    print('\n  修法：换成验证过的等价物，或在该处显式写「⚠ 本机没有 X」——'
          '两者都不做就是「抄来跑不了」。')
    return 1

if __name__ == "__main__":
    sys.exit(main())
