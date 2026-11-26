#!/usr/bin/env python3
# lint_sh.py -- zero-dependency shell script checker (the half ruff cannot see).
#
# Why it exists (real incident, 2026-11-18):
#   docker/provision-agent.sh referenced $ROOT but never defined it. The script starts
#   with "set -euo pipefail", so that line aborted with "ROOT: unbound variable"
#   -> entrypoint saw provision fail -> the container entered a restart loop and the
#   UI was completely down. The 11-13 image shipped an 11-01 copy of the script
#   without the buggy block, so it only detonated when the image was rebuilt on 11-18.
#   ruff only reads Python and shellcheck is not installed (network unreliable), so
#   this file exists to keep that class of bug out of the image.
#
# Checks (only these two are FAIL; everything else stays silent):
#   1. syntax      -- bash -n on every script
#   2. unbound var -- in scripts declaring set -u / -eu / -euo, every $NAME reference
#                     must be assigned in the same file, be a compose/.env key, or be
#                     a bash builtin. Safe expansions (${NAME:-x} and friends) are exempt.
#
# Deliberately NOT reported (a linter must not force code churn):
#   - scripts that source another file (. / source): definitions may live anywhere, so
#     the unbound check is skipped for them (they are still syntax-checked)
#   - names guarded on the same line by ${NAME:-} (short-circuit safe)
#   - anything inside comments, single-quoted strings (multi-line included), or
#     arithmetic ((( ))) / $((( ))) contexts
#
# Usage: python3 tools/lint_sh.py [files...]   (no args = scan the repo)
# Exit:  0 clean / 1 FAIL
import os
import re
import subprocess
import sys

# bash builtins / special vars: usable without assignment
BUILTIN = set("""
BASH BASH_VERSION BASH_SOURCE BASH_LINENO BASH_ARGV BASH_ARGC
BASHPID BASH_SUBSHELL BASH_REMATCH BASHOPTS SHELLOPTS PIPESTATUS
CDPATH DIRSTACK EUID UID PPID PWD OLDPWD SHLVL
HOSTNAME HOSTTYPE MACHTYPE OSTYPE IFS PATH HOME
SHELL TERM USER LOGNAME LANG LANGUAGE TMPDIR TZ
RANDOM SECONDS LINENO FUNCNAME GROUPS OPTARG OPTIND
# awk 内置变量（常出现在 "$(... awk '... $NF ...')" 里，不是 bash 的）
NF FS OFS NR FNR RS ORS FILENAME RSTART RLENGTH SUBSEP
REPLY PS1 PS2 PS4 MAIL MAILPATH PROMPT_COMMAND
COLUMNS LINES EDITOR VISUAL PAGER
_
""".split())

# NAME= / NAME+= / export|local|declare|readonly|typeset NAME=
# ★ 必须带 re.M：赋值绝大多数出现在**行首**，而 (?:^|[ \t;(]) 里的 ^ 在默认
#   （非 MULTILINE）模式下只匹配整个字符串开头 → 行首赋值漏掉、变量被判未定义。
#   这正是第一版把 p2 探针（ROOT="..." 紧接 echo "$ROOT"）误报的原因。
RE_ASSIGN = re.compile(
    r"(?:^|[ \t;(])(?:export |local |declare (?:-\w+ +)*|readonly |typeset )?"
    r"([A-Za-z_][A-Za-z0-9_]*)\+?=",
    re.M,
)
RE_FOR = re.compile(r"[^A-Za-z0-9_]for +([A-Za-z_][A-Za-z0-9_]*) +in[ \t]")
# C-style loop: for ((i = 0; ...)) / for (( i=0 ; ... ))
RE_CFOR = re.compile(r"[^A-Za-z0-9_]for *\(\( *([A-Za-z_][A-Za-z0-9_]*) *=")
# read [-flags] [args...] NAME [NAME...]
#   The '''' and "" empty-string arguments of "read -r -d '''' NAME" must be allowed --
#   that is exactly how make-bin.sh builds $HDR, and missing it produced a false FAIL.
RE_READ = re.compile(
    r"[^A-Za-z0-9_]read +((?:-[A-Za-z]+ )*)(?:(?:''|\"\"|-|--?[A-Za-z]+) +)*"
    r"([A-Za-z_][A-Za-z0-9_]*(?: +[A-Za-z_][A-Za-z0-9_]*)*)"
)
RE_PRINTF_V = re.compile(r"[^A-Za-z0-9_]printf +-v +([A-Za-z_][A-Za-z0-9_]*)")
# $NAME / ${NAME...}
RE_REF = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)")
# safe expansions: ${NAME:-x} ${NAME:=x} ${NAME:+x} ${NAME:?x} (also without colon)
RE_SAFE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)[:+-=?]")
RE_SAFE_BLOCK = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*[:+-=?][^}]*\}")
RE_ARITH = re.compile(r"\$\(\(.*?\)\)", re.S)
RE_ARITH2 = re.compile(r"\(\([^()]*\)\)")
RE_SET_U = re.compile(r"^[ \t]*set +-[a-zA-Z]*u", re.M)
RE_SOURCE = re.compile(r"(?:^|[ \t;&(])(?:\.|source)[ \t]+[^ \t;&|)]+", re.M)

_ENV_CACHE = None


def strip_noise(text):
    """Blank out comments and single-quoted strings with a real scanner.

    A regex like 'anything but a quote' is not good enough here: pi-ui-patch.sh
    contains the shell idiom close-quote, double-quote, reopen; and winrm embeds a
    multi-line PowerShell blob inside single quotes. Regexes mangle both and then
    swallow real assignments, producing false positives. This scanner keeps the
    length and the newlines, so reported line numbers stay correct.
    """
    out = []
    i = 0
    n = len(text)
    single = False
    double = False
    comment = False
    while i < n:
        ch = text[i]
        if comment:
            if ch == "\n":
                comment = False
                out.append(ch)
            else:
                out.append(" ")
            i += 1
            continue
        if single:
            if ch == "\x27":
                single = False
                out.append(" ")
            else:
                out.append("\n" if ch == "\n" else " ")
            i += 1
            continue
        if double:
            if ch == "\\" and i + 1 < n:
                out.append(ch)
                out.append(text[i + 1])
                i += 2
                continue
            if ch == "\x22":
                double = False
            out.append(ch)
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            out.append(ch)
            out.append(text[i + 1])
            i += 2
            continue
        if ch == "\x27":
            single = True
            out.append(" ")
            i += 1
            continue
        if ch == "\x22":
            double = True
            out.append(ch)
            i += 1
            continue
        if ch == "#" and (i == 0 or text[i - 1] in " \t\n;()"):
            comment = True
            out.append(" ")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def repo_root(path):
    cur = os.path.abspath(os.path.dirname(os.path.abspath(path)))
    for _ in range(6):
        if os.path.isdir(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return os.getcwd()


def env_keys(path):
    """Keys injected at runtime by compose.yaml environment: / .env."""
    global _ENV_CACHE
    if _ENV_CACHE is not None:
        return _ENV_CACHE
    keys = set()
    root = repo_root(path)
    for rel in ("compose.yaml", ".env", ".env.example"):
        fp = os.path.join(root, rel)
        if not os.path.isfile(fp):
            continue
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    m = re.match(r"[ \t]*([A-Z_][A-Z0-9_]*)[ \t]*[:=]", line)
                    if m:
                        keys.add(m.group(1))
        except OSError:  # lint:ok 读不到 env 文件就是没有（权限/竞态），不影响判定
            pass
    _ENV_CACHE = keys
    return keys


RE_HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


def mask_quoted_heredocs(text):
    """Blank out bodies of quoted-delimiter heredocs (<<'EOF' / <<"EOF").

    Inside those bash expands nothing, so any $NAME there is literal text -- winrm
    embeds whole PowerShell programs via <<'EOF' full of $PSVersionTable and $hv.
    Unquoted heredocs (<<WRAP) DO expand, so they stay scanned; their escaped
    backslash-$ sequences are dropped separately in scan().
    """
    lines = text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = RE_HEREDOC.search(line)
        if not m:
            i += 1
            continue
        quoted = m.group(1) in ("'", '"')
        delim = m.group(2)
        i += 1
        while i < len(lines) and lines[i].strip() != delim:
            out.append("" if quoted else lines[i])
            i += 1
        if i < len(lines):
            out.append(lines[i])      # the closing delimiter itself
            i += 1
    return "\n".join(out)


def scan(path):
    """Return (hits, skipped_reason). hits = [(line, name)] unbound under set -u."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError:
        return [], ""
    if not RE_SET_U.search(raw):
        return [], ""
    # C-style loop vars must be read from raw text: the ((...)) body is blanked below,
    # which would otherwise hide "for ((i = 0; ...))" and flag $i.
    cfor_vars = set(RE_CFOR.findall(raw))
    body = mask_quoted_heredocs(raw)
    body = strip_noise(body)
    # backslash-$ is a literal dollar in bash (quoted and unquoted alike).
    body = body.replace("\\$", "  ")
    if RE_SOURCE.search(body):
        return [], "sources another file"
    body = RE_ARITH.sub(" ", body)
    body = RE_ARITH2.sub(" ", body)

    defined = set(cfor_vars)
    for rx in (RE_ASSIGN, RE_FOR, RE_PRINTF_V):
        defined.update(rx.findall(body))
    for _flags, names in RE_READ.findall(body):
        defined.update(names.split())
    defined.update(env_keys(path))
    defined.update(BUILTIN)

    # A name the author wrote as ${NAME:-}/${NAME:+} anywhere has been treated as
    # optional, so a later bare $NAME is safe -- that is the usual multi-line
    # "if [ -n optional ]; then use it; fi" idiom.
    guarded = set(RE_SAFE.findall(body))

    hits = []
    seen = set()
    for i, line in enumerate(body.splitlines(), 1):
        code = RE_SAFE_BLOCK.sub(" ", line)
        for name in RE_REF.findall(code):
            if name in defined or name in guarded or name in seen:
                continue
            seen.add(name)
            hits.append((i, name))
    return hits, ""


# ★ 2026-11-23：bash 的定位逻辑收进 tools/bashfind.py（单一来源）。
#   原委见那个模块的 docstring：Windows 的 CreateProcess 把 System32 排在 PATH 前，
#   而本机 C:/Windows/System32/bash.exe 是起不来的 WSL 存根 → 裸名 bash 恒失败。
#   同一个坑还打红过 bench_local 的判分，所以两边共用一份实现。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from bashfind import BASH, is_access_denied
except ImportError:                     # 发布包里没带 bashfind → 退回裸名并如实标注
    BASH = "bash"

    def is_access_denied(err):
        return False


def syntax_ok(path):
    # 相对路径 + cwd（注释②）；bash 用解析出来的完整路径（注释①）
    d = os.path.dirname(os.path.abspath(path))
    try:
        p = subprocess.run([BASH, "-n", os.path.basename(path)],
                           cwd=d, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return True, "skipped (%s)" % exc
    if p.returncode == 0:
        return True, ""
    err = p.stderr or b""
    # 「起不来」与「脚本语法错」是两件事：前者 skip，后者 FAIL（不制造狼来了）
    if is_access_denied(err):
        return True, "skipped (bash 起不来：拒绝访问)"
    lines = err.decode("utf-8", "replace").strip().splitlines()
    return False, lines[0] if lines else "bash -n failed"


SHELL_SKIP_DIRS = set("""
.git node_modules __pycache__ vendor kb knowledge
venv venv-win venv-sdk win .agents log dist public
""".split())

# ★ 2026-11-23：work/ 是**解题临时目录**，里面的 .sh 是求解器现场拼的片段
#   （截断的 payload、半截 heredoc 都很常见）。它们从来不是要维护的仓库脚本，
#   却能把「全仓 shell 体检 FAIL=0」这条闸门打红 —— 实测容器里 3 个
#   work/solve/*/scratch/*.sh 就让 selfcheck 报硬失败，而源码一个错都没有。
#   噪声闸门的下场是被关掉，所以按「非仓库脚本」跳过；要单独体检某个片段，
#   直接把路径当参数传给 lint_sh（显式给的目标永远会被检查）。
SHELL_SKIP_PREFIXES = ("work/", "work\\")


def default_targets():
    root = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".."))
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SHELL_SKIP_DIRS]
        for fn in filenames:
            if not fn.endswith(".sh"):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            if rel.startswith(SHELL_SKIP_PREFIXES):
                continue
            out.append(full)
    for d in ("tools/bin", "tools/hand"):
        dp = os.path.join(root, d)
        if not os.path.isdir(dp):
            continue
        for fn in sorted(os.listdir(dp)):
            fp = os.path.join(dp, fn)
            if not os.path.isfile(fp) or fn.endswith(".sh"):
                continue
            try:
                with open(fp, "rb") as fh:
                    head = fh.read(64)
            except OSError:
                continue
            if head.startswith(b"#!") and b"bash" in head.split(b"\n")[0]:
                out.append(fp)
    return sorted(set(out))


def main(argv):
    targets = argv[1:] or default_targets()
    fails = 0
    skipped = 0
    for path in targets:
        ok, msg = syntax_ok(path)
        # ntpath.relpath raises ValueError across drives ("path is on mount C:").
        # The regression test hands in %TEMP% paths on C: while the repo lives on D:.
        try:
            rel = os.path.relpath(path).replace("\\", "/")
        except ValueError:
            rel = os.path.abspath(path).replace("\\", "/")
        if not ok:
            print("FAIL %s: syntax error -- %s" % (rel, msg))
            fails += 1
            continue
        hits, why = scan(path)
        if why:
            skipped += 1
            continue
        if hits:
            detail = ", ".join("L%d $%s" % (ln, n) for ln, n in hits[:6])
            more = "" if len(hits) <= 6 else " (+%d more)" % (len(hits) - 6)
            print("FAIL %s: unbound var under set -u -- %s%s" % (rel, detail, more))
            fails += 1
    total = len(targets)
    if fails:
        print("RESULT: %d/%d scripts have problems" % (fails, total))
        return 1
    extra = "" if not skipped else " (%d skipped: they source another file)" % skipped
    print("RESULT: %d shell scripts OK -- syntax + set -u variable definitions%s"
          % (total, extra))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
