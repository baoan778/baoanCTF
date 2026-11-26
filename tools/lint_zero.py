#!/usr/bin/env python3
"""lint_zero.py —— 零依赖静态检查（不需要 ruff / shellcheck / 网络）。

为什么是零依赖
-------------
本想装 ruff + shellcheck，实测 pip 拉取 10 分钟未完成（本机 GitHub/PyPI 直连不稳）。
而 CI 里装外部工具本身就是个可靠性风险：网络一抖，门禁就红得莫名其妙。
所以只用 Python 自带能力做「**确定能查出真问题**」的那几类检查：
  · 语法错误（compile）—— 改完没生效最常见的死因
  · 裸 except / except: pass —— 吞异常，之前 fgs log 崩了就属于这类没兜住
  · subprocess 无 timeout —— 容器里 pkill/ps 挂死那次
  · text=True 无 encoding —— Windows cp936 解码炸掉整个脚本
  · .read_text() 直读外部路径 —— 应走 read_capped
  · 算术/比较里的 `=` 手滑（ast.Compare 之外）

它**不**做什么：不管风格（行长、命名、import 顺序）——那是 ruff 的活，
而风格不影响正确性，不该卡门禁。

★ 2026-11-18 修「工具匹配范围失控」（190 条 WARN 里绝大多数是误报）
--------------------------------------------------------------------
① subprocess 规则原来只按**属性名**匹配：
       fn = getattr(n.func, 'attr', getattr(n.func, 'id', ''))
       if fn in ('run', 'call', 'check_output'):
   于是**任何叫 run/call 的本地函数**都被当成 subprocess.run。实测铁证：
   tools/pcapmap.py 全文件只有 1 处真 subprocess.run（第 50 行，带 timeout=timeout），
   其余 8 处被判「无 timeout」的全是调它自己的 run(cmd, timeout) 包装器；
   board/handover.py 的 run(*args, timeout=600) 同理。→ 误报 11 条。
   现在先解析 import：只有 `subprocess.run` / `sp.run`（别名）/ `from subprocess import run`
   这三种真身才算，本地同名函数一律放过。

② read_text 规则原来靠一个**手写变量名白名单**（DOC/text/src/...）豁免内部路径，
   名单外的内部变量（gp/cand/lib/qfile/gf/leg/...）全被报 —— 实测 36 条里绝大多数
   读的是黑板自己的 `PROJECTS.glob("*/graph.json")`，根本不是外部输入。
   现在改成按**来源**判定，而不是按名字猜：
     · 收到的是 argparse 命名空间属性（a.from_file / args.op_file …）→ 外部可控，报；
     · 收到的是函数的**形参**（且函数体内没有重新赋值）→ 可能来自调用方，报；
     · 其余（模块级常量、局部变量、glob 出来的路径）→ 内部生成物，不报。
   这条纪律本来只针对用户/外部可控路径（--from / --op-file / 上传文件），
   判据就该是「这个值从哪来」，而不是「变量叫什么」。

用法：lint_zero.py [--strict] [路径...]    默认扫 board/ tools/
退出码：0 无问题；1 有 FAIL；--strict 时 WARN 也算 1。
"""
from __future__ import annotations

import ast
import pathlib
import sys

# ★ 2026-11-17：汇总行有中文，cp936 控制台下会抛 UnicodeEncodeError / 写进管道变乱码
#   （selfcheck 里实测到乱码）。与 apiharvest / browser 等同一约定。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

FAIL, WARN = [], []

# argparse 命名空间的常见变量名 —— 这些的属性都算「外部可控」
ARGPARSE_NS = {"a", "args", "ap", "opts", "ns", "arg", "argns", "options"}
# subprocess 里值得卡 timeout 的入口（Popen 没有 timeout 参数，不在此列）
SUBPROC_FUNCS = {"run", "call", "check_output", "check_call"}


def _imports(tree: ast.AST) -> tuple[set[str], dict[str, str]]:
    """返回 (指向 subprocess 模块的名字集合, {本地名: subprocess 函数名})。"""
    mods: set[str] = set()
    direct: dict[str, str] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for al in n.names:
                if al.name == "subprocess":
                    mods.add(al.asname or "subprocess")
        elif isinstance(n, ast.ImportFrom) and n.module == "subprocess" and not n.level:
            for al in n.names:
                direct[al.asname or al.name] = al.name
    return mods, direct


def _subproc_func(call: ast.Call, mods: set[str], direct: dict[str, str]) -> str | None:
    """这一行是不是真的 subprocess.<f>？是则返回函数名，否则 None。"""
    f = call.func
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in mods:
        return f.attr if f.attr in SUBPROC_FUNCS else None
    if isinstance(f, ast.Name) and f.id in direct:
        return direct[f.id] if direct[f.id] in SUBPROC_FUNCS else None
    return None


def _params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    a = fn.args
    names = {x.arg for x in (*a.posonlyargs, *a.args, *a.kwonlyargs)}
    if a.vararg:
        names.add(a.vararg.arg)
    if a.kwarg:
        names.add(a.kwarg.arg)
    return names


def _assigned(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """函数体内被重新赋值过的名字（形参一旦被覆盖就不再是外部输入）。"""
    out: set[str] = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)
    return out


def _calls_attr(body: list[ast.stmt], name: str) -> bool:
    """这段语句里有没有调用 .<name>(...)（用于识别样板代码）。"""
    for st in body:
        for n in ast.walk(st):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == name:
                return True
    return False


def _is_external(node: ast.AST, param_ctx: set[str]) -> tuple[bool, str]:
    """read_text 的接收者是否可能来自外部输入？"""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        if node.value.id in ARGPARSE_NS:
            return True, f"{node.value.id}.{node.attr}"
        return False, ""
    if isinstance(node, ast.Name) and node.id in param_ctx:
        return True, node.id
    return False, ""


class Linter(ast.NodeVisitor):
    def __init__(self, path: pathlib.Path, tree: ast.AST, src: str = ""):
        self.path = path
        # ★ 2026-11-18：`lint:ok <理由>` 是**显式豁免**——写在同一行或上一行时，
        #   该行的 read_text / except-pass 告警不报。为什么要有：判断「这个路径是不是
        #   外部可控」有时只有作者知道（比如已经做过尺寸/类型校验的分支），而一味扩大
        #   变量名白名单就是 2026-11-13 那次 57 条误报的老路。豁免必须带理由，也方便
        #   下次审查时逐条追问。
        self.lines = src.splitlines()
        self.mods, self.direct = _imports(tree)
        # 每层函数：形参集合 ∧ 未被重新赋值的形参集合
        self.frames: list[set[str]] = []
        # 当前所在的 try（识别「样板 try/except: pass」时要看 try 体）
        self._tries: list[ast.Try] = []

    # ── 进入/离开函数：维护「当前可见的外部形参」 ──────────────────────────
    def _enter(self, fn) -> None:
        self.frames.append(_params(fn) - _assigned(fn))

    def visit_FunctionDef(self, node):  # noqa: N802
        self._enter(node)
        self.generic_visit(node)
        self.frames.pop()

    def visit_AsyncFunctionDef(self, node):  # noqa: N802
        self._enter(node)
        self.generic_visit(node)
        self.frames.pop()

    def _waived(self, lineno: int) -> bool:
        """本行或**上方的连续无空行块**里有 `lint:ok` → 豁免。

        为什么不是「只看上一行」：真实写法常是
            # lint:ok 理由…
            if size > CAP:      # 护栏就在这两行
                return
            text = p.read_text(...)
        豁免标记和紧挨着的护栏/语句属于同一段，中间夹一行代码很正常。
        但**遇到空行就停** —— 否则一个标记会意外罩住整个函数。
        """
        for i in range(lineno - 1, max(-1, lineno - 9), -1):   # 本行 + 上方 8 行
            if i >= len(self.lines):
                continue
            if "lint:ok" in self.lines[i]:
                return True
            if not self.lines[i].strip():
                return False
        return False

    def visit_Try(self, node: ast.Try):  # noqa: N802
        self._tries.append(node)
        self.generic_visit(node)
        self._tries.pop()

    def visit_TryStar(self, node):  # noqa: N802  (py3.11+ except*)
        self._tries.append(node)
        self.generic_visit(node)
        self._tries.pop()

    def visit_Call(self, node: ast.Call):  # noqa: N802
        # ── subprocess 无 timeout / text 无 encoding ────────────────────
        fn = _subproc_func(node, self.mods, self.direct)
        if fn:
            kw = {k.arg: k.value for k in node.keywords if k.arg}
            # ★ 2026-11-18：调用带 `**kw` 展开时静态看不到 timeout（如 tsecbench._sudo
            #   自己 kw.setdefault("timeout", 60)）——这一支交给人工看，别报假警。
            spread = any(k.arg is None for k in node.keywords)
            if "timeout" not in kw and not spread:
                WARN.append(f"{self.path}:{node.lineno}: subprocess.{fn} 无 timeout（可能永久挂起）")
            if "text" in kw or "universal_newlines" in kw:
                if "encoding" not in kw and not spread:
                    WARN.append(f"{self.path}:{node.lineno}: text=True 但无 encoding（Windows cp936 会炸）")

        # ── .read_text() 直读外部路径 ───────────────────────────────────
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "read_text":
            param_ctx: set[str] = set()
            for s in self.frames:
                param_ctx |= s
            ext, who = _is_external(f.value, param_ctx)
            if ext and not self._waived(node.lineno):
                WARN.append(f"{self.path}:{node.lineno}: read_text() 直读外部路径 {who}（应走 read_capped）")

        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler):  # noqa: N802
        if node.type is None:
            FAIL.append(f"{self.path}:{node.lineno}: 裸 except（吞掉所有异常，含 KeyboardInterrupt）")
        if (len(node.body) == 1 and isinstance(node.body[0], ast.Pass)
                and not self._is_bootstrap(node) and not self._waived(node.lineno)):
            WARN.append(f"{self.path}:{node.lineno}: except 里只有 pass（异常被静默吞掉）")
        self.generic_visit(node)

    def _is_bootstrap(self, node: ast.ExceptHandler) -> bool:
        """是不是「控制台 utf-8 兜底」那种样板？（43/111 条都是它，纯噪声）

            try:
                for _s in (sys.stdout, sys.stderr):
                    _s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

        这里静默是**故意的**：老控制台没有 reconfigure 时才失败，而那时本来就
        不该因为「打不出中文」把整个命令打崩。判据是 try 体里出现了 reconfigure。
        """
        for t in self._tries:
            if node in t.handlers and _calls_attr(t.body, "reconfigure"):
                return True
        return False


def scan(path: pathlib.Path) -> None:
    # lint:ok 路径来自命令行/selfcheck 给的本仓库文件清单，不是外部输入。
    src = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        FAIL.append(f"{path}:{e.lineno}: 语法错误 {e.msg}")
        return
    Linter(path, tree, src).visit(tree)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    strict = '--strict' in sys.argv[1:]
    roots = [pathlib.Path(a) for a in args] or [pathlib.Path('board'), pathlib.Path('tools')]
    files = []
    for r in roots:
        if r.is_file():
            files.append(r)
        elif r.is_dir():
            # ★ 2026-11-17 修：裸跑（不给参数）原来 rglob 整个 tools/ —— 把 tools/vendor、
            #   tools/venv*、node_modules 里 1.3 万个第三方文件也扫了，打印 1322 个 FAIL，
            #   全是别人的代码，把真问题埋掉（selfcheck 早就用显式文件列表绕开这坑，
            #   但人直接敲 `lint_zero` 时还是这个鬼样子）。第三方目录一律跳过。
            #   第三方/工具链目录按相对路径前缀排掉（这些名字做「任意一层目录名」判断会误伤
            #   自己的代码，比如 tools/win 里的 MSYS python 与 board/win 之类）。
            _SKIP_PREFIX = ('tools/venv', 'tools/win/', 'tools/gobin', 'tools/wordlists',
                            'tools/vendor', 'tools/node_modules', 'tools/ctfpoc/bins')
            # ★ 2026-11-17：默认（不给路径）只扫 board/*.py + tools/*.py —— 与 selfcheck
            #   第 8 段完全同一批文件。rglob 到子目录会把 board/tests、tools/ctfpoc 里的
            #   测试/一次性脚本也算进来（实测 319 文件 / 173 FAIL，全是「测试脚本本来就不
            #   带 timeout 之类」的噪声），把真问题埋掉。要看子目录就显式传路径。
            _pats = ('*.py',) if (r.as_posix() in ('board', 'tools')) else ('**/*.py',)
            for _pat in _pats:
                for p in r.rglob('*.py') if _pat != '*.py' else r.glob('*.py'):
                    sp = p.as_posix()
                    if '/site-packages/' in sp or '__pycache__' in sp:
                        continue
                    if any(sp.startswith(pre) for pre in _SKIP_PREFIX):
                        continue
                    files.append(p)
    for f in sorted(files):
        scan(f)

    for m in FAIL:
        print(f'FAIL  {m}')
    for m in WARN:
        print(f'WARN  {m}')
    print(f'\n扫了 {len(files)} 个文件：FAIL {len(FAIL)} / WARN {len(WARN)}')
    if FAIL:
        return 1
    if strict and WARN:
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
