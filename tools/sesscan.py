#!/usr/bin/env python3
"""sesscan —— 安全地查 pi 会话文件（<sessions>/*.jsonl）

为什么会有这个工具（2026-09-29 三次事故的直接修法）
────────────────────────────────────────────────
会话 JSONL 单文件能到 5MB，单行能到 500KB+。手写
    py - <<'EOF' ... open(p).read().splitlines() ... 循环里累加 ... EOF
这种"整读 + 二次拼接"的临时脚本会一路涨到 6.5~7.2G，
把 8G 的 WSL VM 打到 **global OOM**（21:09 / 21:45 / 22:32 三次都是
`python3` 吃 7.2G 被 oom-kill，整台发行版连 pi 一起崩）。

本工具的三条护法：
  1. **逐行流式**：json.loads 一行丢一行，内存与文件大小无关；
  2. **自带 RLIMIT_AS**：真出 bug 也是 MemoryError，不会拖垮 VM（默认 1200MB）；
  3. **输出截断**：每条 `--width`（默认 400 字符），绝不把 500KB 单行打上屏。
坏行（半截写入 / 非 JSON）跳过不抛。

用法
────
  sesscan list [N]                     最近 N 个会话（默认 15）
  sesscan find <关键词> [--in all|user|assistant|tool]
  sesscan show <会话> [--head N | --tail N | --line N]
  sesscan where <会话>                 ★ 这个会话"停在哪"（最后事件 / 最后工具调用 / 有没有半截）
  sesscan stats                        会话目录总账
  sesscan --selftest                   自检（用临时目录，不碰真会话）

公共开关
────────
  --dir DIR      会话目录（可重复；默认 $PI_LAB_SESSIONS 或 ~/.pi-lab-sessions）
  --mem-mb N     内存上限，0 = 关（默认 1200）
  --width N      每条输出截断宽度（默认 400）
  --json         机器可读输出

会话参数可以给：完整路径 / 文件名 / 文件名前缀 / session uuid 前缀。
"""

import argparse
import glob
import json
import os
import sys
import time
from collections import Counter, deque

# ★ 2026-11-06：`resource` 是 **POSIX-only**（Windows 没有这个模块）。
#   原来无条件 `import resource` → Windows 上直接 ModuleNotFoundError，
#   整个 sesscan 用不了，selfcheck 报「存在但跑不了」。
#   它只用于 setrlimit 防内存爆（读会话 JSONL 会涨到 7G 那个坑）；
#   Windows 没有等价物 → 降级为"不设上限"，但**必须显式告知**，
#   不能让"没设限"变成"以为设了限"。
try:
    import resource as _resource          # noqa: F401
    _HAS_RESOURCE = True
except ImportError:                        # Windows / 其他非 POSIX
    _resource = None                       # type: ignore[assignment]
    _HAS_RESOURCE = False

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
#   自包含（自己 import sys），不依赖本文件是否已 import sys 或用别名。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_MEM_MB = 1200
DEFAULT_WIDTH = 400


# ── 基础设施 ────────────────────────────────────────────────────────────────
def set_memory_cap(mem_mb):
    if not mem_mb or mem_mb <= 0:
        return
    # ★ 2026-11-06：Windows 没有 resource 模块 → 这个上限设不了。
    #   必须**明说**，不能让"没设限"被当成"设了限"：读会话 JSONL 涨到 7G 是真实事故。
    if not _HAS_RESOURCE:
        warn(f"本平台无 resource 模块，内存上限 {mem_mb}MB **未生效**"
             f"（Windows 限制）；读大会话前先 `sesscan where` 缩小范围")
        return
    lim = mem_mb * 1024 * 1024
    try:
        soft, hard = _resource.getrlimit(_resource.RLIMIT_AS)
        new_soft = lim if hard == _resource.RLIM_INFINITY else min(lim, hard)
        _resource.setrlimit(_resource.RLIMIT_AS, (new_soft, hard))
    except Exception as exc:                      # 上限设不上不是致命错
        warn(f"内存上限设置失败（{exc}），继续但请小心")


def warn(msg):
    print(f"[sesscan] 警告：{msg}", file=sys.stderr)


def clip(text, width):
    text = text.replace("\r", " ").replace("\n", "\\n")
    if len(text) <= width:
        return text
    return f"{text[:width]}…(+{len(text) - width})"


def default_dirs():
    env = os.environ.get("PI_LAB_SESSIONS")
    if env:
        return [env]
    return [os.path.expanduser("~/.pi-lab-sessions")]


def session_files(dirs):
    out = []
    for d in dirs:
        if not d or not os.path.isdir(d):
            warn(f"会话目录不存在：{d}")
            continue
        out.extend(glob.glob(os.path.join(d, "*.jsonl")))
    out = [p for p in out if os.path.isfile(p)]
    out.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return out


def resolve_session(spec, dirs):
    """把 路径/文件名/前缀/uuid 前缀 解析成唯一文件；歧义时列候选。"""
    if os.path.isfile(spec):
        return spec
    files = session_files(dirs)
    for exact in (spec, spec + ".jsonl"):
        hit = [p for p in files if os.path.basename(p) == exact]
        if len(hit) == 1:
            return hit[0]
    hit = [p for p in files if os.path.basename(p).startswith(spec)]
    # uuid 前缀：文件名形如 <iso>_<uuid>.jsonl
    if not hit:
        hit = [p for p in files if spec in os.path.basename(p)]
    if len(hit) == 1:
        return hit[0]
    if not hit:
        die(f"找不到会话：{spec}（用 `sesscan list` 看现有会话）")
    print(f"[sesscan] {spec} 匹配到 {len(hit)} 个会话：")
    for p in hit[:20]:
        print("   ", os.path.basename(p))
    die("请给更长的前缀")


def die(msg, code=2):
    print(f"[sesscan] {msg}", file=sys.stderr)
    sys.exit(code)


def iter_entries(path):
    """逐行流式产出 (行号, obj)；坏行 obj=None。内存 O(行长)。"""
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield lineno, json.loads(line)
            except Exception:
                yield lineno, None


def entry_kind(obj):
    if not isinstance(obj, dict):
        return "bad"
    if obj.get("type") == "message":
        return obj.get("message", {}).get("role", "message")
    return obj.get("type", "?")


def text_of(message):
    content = message.get("content")
    if isinstance(content, str):
        return content
    parts = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
    return "\n".join(parts)


def tool_calls_of(message):
    out = []
    content = message.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "toolCall":
                out.append(item)
    return out


def user_text_of(obj):
    if isinstance(obj, dict) and obj.get("type") == "message":
        msg = obj.get("message", {})
        if msg.get("role") == "user":
            return text_of(msg)
    return ""


# ── 各子命令 ────────────────────────────────────────────────────────────────
def cmd_list(args):
    files = session_files(args.dir)
    if not files:
        die("没有会话文件")
    rows = []
    for path in files[: args.n]:
        n_entries = 0
        first_user = ""
        last_ts = ""
        last_kind = ""
        for lineno, obj in iter_entries(path):
            n_entries += 1
            if obj is None:
                continue
            if not first_user:
                first_user = user_text_of(obj)
            last_ts = obj.get("timestamp", "") or last_ts
            last_kind = entry_kind(obj)
        st = os.stat(path)
        rows.append({
            "file": os.path.basename(path),
            "mtime": time.strftime("%m-%d %H:%M", time.localtime(st.st_mtime)),
            "size_kb": round(st.st_size / 1024, 1),
            "entries": n_entries,
            "last_kind": last_kind,
            "last_ts": last_ts,
            "first_user": clip(first_user, args.width),
        })
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return
    print(f"共 {len(files)} 个会话，列出最近 {len(rows)} 个"
          f"（目录：{', '.join(args.dir)}）\n")
    for r in rows:
        print(f"{r['mtime']}  {r['size_kb']:>8}KB  {r['entries']:>5} 条  "
              f"末条={r['last_kind']}")
        print(f"    {r['file']}")
        if r["first_user"]:
            print(f"    首问：{r['first_user']}")
        print()


def cmd_find(args):
    files = session_files(args.dir)
    if not files:
        die("没有会话文件")
    total = 0
    for path in files:
        hits = []
        for lineno, obj in iter_entries(path):
            if args.in_ == "all":
                blob = user_text_of(obj) if obj else ""
                if obj is not None and obj.get("type") == "message":
                    blob = text_of(obj.get("message", {}))
                elif obj is not None:
                    blob = json.dumps(obj, ensure_ascii=False)
                else:
                    blob = ""
            elif args.in_ == "tool":
                blob = json.dumps(obj, ensure_ascii=False) if obj else ""
            else:
                if not (isinstance(obj, dict) and obj.get("type") == "message"
                        and obj.get("message", {}).get("role") == args.in_):
                    continue
                blob = text_of(obj.get("message", {}))
            if args.pattern in blob:
                hits.append((lineno, entry_kind(obj), clip(blob, args.width)))
        if hits:
            total += len(hits)
            print(f"### {os.path.basename(path)}  （{len(hits)} 处命中）")
            for lineno, kind, snippet in hits[: args.max_hits]:
                print(f"  行{lineno}  [{kind}]  {snippet}")
            if len(hits) > args.max_hits:
                print(f"  …还有 {len(hits) - args.max_hits} 处（--max-hits 调大）")
            print()
    print(f"合计 {total} 处命中")


def cmd_show(args):
    path = resolve_session(args.session, args.dir)
    if args.line:
        for lineno, obj in iter_entries(path):
            if lineno == args.line:
                print(json.dumps(obj, ensure_ascii=False)[: args.width * 10]
                      if obj else "(坏行)")
                return
        die(f"没有第 {args.line} 行")
    if args.tail:
        buf = deque(maxlen=args.tail)
        for lineno, obj in iter_entries(path):
            buf.append((lineno, obj))
        picked = list(buf)
    elif args.head:
        picked = []
        for lineno, obj in iter_entries(path):
            picked.append((lineno, obj))
            if len(picked) >= args.head:
                break
    else:                                    # 默认给 head 3 + tail 8
        head, buf = [], deque(maxlen=8)
        for lineno, obj in iter_entries(path):
            if len(head) < 3:
                head.append((lineno, obj))
            buf.append((lineno, obj))
        picked = head + [x for x in buf if x not in head]
    print(f"# {os.path.basename(path)}")
    for lineno, obj in picked:
        print(f"\n行{lineno}  [{entry_kind(obj)}]  {_ts_of(obj)}")
        if obj is None:
            print("   (坏行：不是合法 JSON)")
            continue
        if obj.get("type") == "message":
            msg = obj.get("message", {})
            txt = text_of(msg)
            if txt:
                print("   " + clip(txt, args.width))
            for tc in tool_calls_of(msg):
                cmd = (tc.get("arguments") or {}).get("command", "")
                print(f"   ⟶ 工具 {tc.get('name')}: {clip(str(cmd), args.width)}")
        else:
            print("   " + clip(json.dumps(obj, ensure_ascii=False), args.width))


def _ts_of(obj):
    return obj.get("timestamp", "") if isinstance(obj, dict) else ""


def cmd_where(args):
    """会话"停在哪"：最后事件 / 最后工具调用 / 有没有半截（工具没回结果）。"""
    path = resolve_session(args.session, args.dir)
    kinds = Counter()
    n = 0
    bad = 0
    last = (0, None)
    last_user = (0, "")
    last_tool = (0, None)
    pending = {}                                  # toolCallId -> (lineno, name, cmd)
    for lineno, obj in iter_entries(path):
        n += 1
        if obj is None:
            bad += 1
            continue
        kinds[entry_kind(obj)] += 1
        last = (lineno, obj)
        if obj.get("type") == "message":
            msg = obj.get("message", {})
            role = msg.get("role")
            if role == "user":
                last_user = (lineno, text_of(msg))
            if role == "assistant":
                for tc in tool_calls_of(msg):
                    cmd = (tc.get("arguments") or {}).get("command", "")
                    last_tool = (lineno, (tc.get("name"), str(cmd)))
                    pending[tc.get("id")] = (lineno, tc.get("name"), str(cmd))
            if role == "toolResult":
                pending.pop(obj.get("toolCallId") or msg.get("toolCallId"), None)

    st = os.stat(path)
    print(f"会话：{os.path.basename(path)}")
    print(f"大小：{st.st_size / 1024:.1f}KB    条目：{n}   坏行：{bad}")
    print(f"最后事件：行{last[0]} [{entry_kind(last[1])}]  {_ts_of(last[1])}")
    if last_user[1]:
        print(f"最后用户消息：行{last_user[0]}  {clip(last_user[1], args.width)}")
    if last_tool[1]:
        name, cmd = last_tool[1]
        print(f"最后工具调用：行{last_tool[0]}  {name}")
        print(f"    {clip(cmd, args.width)}")
    if pending:
        print("⚠ 有工具调用没等到结果（会话是在工具执行中被切断的）：")
        for tid, (lineno, name, cmd) in list(pending.items())[:5]:
            print(f"    行{lineno} {name}: {clip(cmd, args.width)}")
    else:
        print("（没有悬空的工具调用 —— 会话是正常收尾的）")
    print("\n条目类型分布：" + "  ".join(f"{k}={v}" for k, v in kinds.most_common()))


def cmd_stats(args):
    files = session_files(args.dir)
    if not files:
        die("没有会话文件")
    total = 0
    rows = []
    for path in files:
        st = os.stat(path)
        total += st.st_size
        rows.append((st.st_size, path))
    rows.sort(reverse=True)
    print(f"目录：{', '.join(args.dir)}")
    print(f"会话数：{len(files)}    总大小：{total / 1024 / 1024:.1f}MB")
    print("\n最大的 5 个：")
    for size, path in rows[:5]:
        print(f"  {size / 1024 / 1024:>7.2f}MB  {os.path.basename(path)}")


def cmd_selftest(_args):
    """不碰真会话：临时目录里造样本，验证流式/坏行/截断/where。"""
    import tempfile
    ok = True

    def check(label, cond):
        nonlocal ok
        print(("  ✓ " if cond else "  ✗ ") + label)
        ok = ok and cond

    with tempfile.TemporaryDirectory() as tmp:
        big = "x" * 500000                        # 50 万字符单行（模拟巨型 toolResult）
        p = os.path.join(tmp, "2026-01-01T00-00-00-000Z_aaaabbbb.jsonl")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "session", "id": "aaaabbbb",
                                 "timestamp": "2026-01-01T00:00:00Z"},
                                ensure_ascii=False) + "\n")
            fh.write("{ 这不是合法 JSON\n")        # 坏行
            fh.write(json.dumps({"type": "message", "timestamp": "T1",
                                 "message": {"role": "user",
                                             "content": [{"type": "text",
                                                          "text": "目标关键词 在这里"}]}},
                                ensure_ascii=False) + "\n")
            fh.write(json.dumps({"type": "message", "timestamp": "T2",
                                 "message": {"role": "toolResult",
                                             "toolCallId": "c1", "content": [
                                                 {"type": "text", "text": big}]}},
                                ensure_ascii=False) + "\n")
            fh.write(json.dumps({"type": "message", "timestamp": "T3",
                                 "message": {"role": "assistant", "content": [
                                     {"type": "toolCall", "id": "c2", "name": "bash",
                                      "arguments": {"command": "py - <<EOF 半截"}}]}},
                                ensure_ascii=False) + "\n")

        global _DIRS_OVERRIDE
        args = argparse.Namespace(dir=[tmp], n=5, width=60, json=False,
                                  pattern="目标关键词", in_="all", max_hits=5,
                                  session="aaaabbbb", head=None, tail=None, line=None)
        # list：坏行要计数、首问要抓到
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_list(args)
        out = buf.getvalue()
        check("list 能跑并认出 5 条条目", "5 条" in out)
        check("list 首问被截断（不会打 500KB）", "首问" in out and big not in out)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_find(args)
        check("find 命中关键词", "目标关键词" in buf.getvalue())

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_show(argparse.Namespace(dir=[tmp], session="aaaabbbb", width=60,
                                        head=None, tail=2, line=None))
        check("show --tail 不打印巨型行", big not in buf.getvalue())

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_where(args)
        out = buf.getvalue()
        check("where 报出悬空工具调用", "没等到结果" in out)

        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "message", "timestamp": "T4",
                                 "message": {"role": "toolResult",
                                             "toolCallId": "c2",
                                             "content": [{"type": "text",
                                                          "text": "done"}]}},
                                ensure_ascii=False) + "\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_where(args)
        check("where 工具回完结果后不再报悬空",
              "正常收尾" in buf.getvalue())

    print("\n自检结果：" + ("全部通过" if ok else "有失败"))
    return 0 if ok else 1


_DIRS_OVERRIDE = None


# ── CLI ─────────────────────────────────────────────────────────────────────
def build_parser():
    ap = argparse.ArgumentParser(
        prog="sesscan",
        description="安全查 pi 会话（流式 + 内存上限 + 输出截断）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("用法\n────\n")[-1])
    ap.add_argument("--dir", action="append", default=None,
                    help="会话目录（可重复；默认 ~/.pi-lab-sessions）")
    ap.add_argument("--mem-mb", type=int, default=DEFAULT_MEM_MB,
                    help=f"内存上限 MB，0=关（默认 {DEFAULT_MEM_MB}）")
    ap.add_argument("--width", type=int, default=DEFAULT_WIDTH,
                    help=f"每条输出截断宽度（默认 {DEFAULT_WIDTH}）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--selftest", action="store_true", help="自检")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("list", help="最近 N 个会话")
    p.add_argument("n", nargs="?", type=int, default=15)

    p = sub.add_parser("find", help="跨会话搜关键词")
    p.add_argument("pattern")
    p.add_argument("--in", dest="in_", choices=["all", "user", "assistant", "tool"],
                   default="all")
    p.add_argument("--max-hits", type=int, default=5)

    p = sub.add_parser("show", help="看某个会话的条目")
    p.add_argument("session")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--head", type=int)
    g.add_argument("--tail", type=int)
    g.add_argument("--line", type=int)

    p = sub.add_parser("where", help="会话停在哪（最后事件 / 悬空工具调用）")
    p.add_argument("session")

    sub.add_parser("stats", help="会话目录总账")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    if getattr(args, "selftest", False) or args.cmd == "selftest":
        return cmd_selftest(args)
    if not args.cmd:
        build_parser().print_help()
        return 1
    args.dir = args.dir or default_dirs()
    set_memory_cap(args.mem_mb)
    return {"list": cmd_list, "find": cmd_find, "show": cmd_show,
            "where": cmd_where, "stats": cmd_stats}[args.cmd](args) or 0


if __name__ == "__main__":
    sys.exit(main())
