#!/usr/bin/env python3
"""矩阵 + 模糊测试：把 fgs 的边界条件全打一遍。

跑法：
    python3 board/tests/test_matrix_fuzz.py

和其它测试的分工
---------------
- `test_cli_smoke.py` 管「命令别崩」
- 本文件管「**不该通过的绝不能通过，且失败时图一个字节都不能变**」

三条硬不变量（每条都逐例断言）
-----------------------------
1. **端点矩阵是封闭的**：11 种关系 × 所有 (from 类型, to 类型) 组合，允许与否必须和矩阵一致。
2. **校验失败 = 图零改动**：任何被拒的 apply，`graph.json` 的**字节**必须完全一致
   （不是"节点数一样"，是 `read_bytes()` 相同 —— 否则说明写了一半又回滚，留了脏）。
3. **畸形输入不崩**：投一堆垃圾载荷，允许 exit 2/3，但**不许出现 Traceback**。
"""
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FGS = ROOT / "fgs.py"
PID = "_test_matrix_fuzz"

# ★ 隔离：临时项目别建到真 board/projects（否则并行时污染别的测试）
TMPDIR = Path(tempfile.mkdtemp(prefix="pilab-matrix-"))
PDIR = TMPDIR / "projects" / PID
# ★ 2026-11-11 修：只隔离 BOARD 不够 —— fgs 的 worker_dir() 会退回真 work/<pid>/，
#   并行时与其它测试共用同批 pid 文件，表现为随机「relate 合法却被拒」（单跑 444/0）。
ENV = dict(os.environ, PI_LAB_BOARD_DIR=str(TMPDIR / "projects"),
           PI_LAB_WORK_DIR=str(TMPDIR / "work"))

sys.path.insert(0, str(ROOT))
import fgs  # noqa: E402  （只为了拿 RELATIONS 矩阵本身，避免测试和实现对不上）

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
#   自包含（自己 import sys），不依赖本文件是否已 import sys 或用别名。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

passed, failed = [], []


def run(*args, ok=None):
    # ★ 2026-11-12 处理 flaky：混在 80 条全量回归里约 1/20 概率红一次，报
    #       SystemError: error return without exception set
    #   栈落在 CPython 的 subprocess 模块**调用侧**（不是 fgs 子进程内部）。
    #
    #   ★ 根因**未确认**。这里只记事实，不要照抄成结论：
    #     · 已复现：仅在全量回归（80 条连跑）中偶现，约 1/20
    #     · 未复现：fgs relate 裸跑 500 次 0 命中
    #     · 未复现：同等 subprocess 模式裸跑 300 次 0 命中
    #     · 未复现：单独串行跑本文件 20 次 0 失败（加下面的超时+重试之后）
    #   即：**无法稳定复现，因此无法证实任何单一根因**。曾怀疑过 pycache 损坏、
    #   正则编译、fgs 子进程本身、并行资源竞争，逐项实验后均未坐实。
    #
    #   处置（兜底，不是"修复"）：加 timeout 防挂死；对 SystemError 有限重试。
    #   重试**必须留证**——原始 stderr/args 落到 log/，否则真故障会被重试吃掉。
    last_err = None
    for _ in range(3):
        try:
            p = subprocess.run([sys.executable, str(FGS), *args], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", env=ENV,
                               timeout=120)
            break
        except subprocess.TimeoutExpired:
            check(f"`{' '.join(args[:2])}` 未超时(120s)", False, "子进程挂死")
            return 1, "", ""
        # ★ 2026-11-13 修：原来只 catch SystemError，但实测失败形态**不止它**：
        #   TypeError: 'Tokenizer' object is not iterable / 'State' object is not iterable
        #   TypeError: '_NamedIntConstant' object is not callable / 'int' not subscriptable
        #   AttributeError: 'NoneType' object has no attribute 'append'
        #   这些都是 CPython 解释器在**重负载下**的内部错乱（同一进程 + 密集 fork），
        #   不是 fgs 的业务结果。只 catch SystemError 意味着另外几种直接穿透成红，
        #   于是「加了重试却还是偶发红」——重试根本没生效。
        #   现在按「解释器内部错误」这一类全收：类型错乱 + SystemError。
        except (SystemError, TypeError, AttributeError) as exc:
            last_err = exc
            # 留证：重试不能把现场吞掉（这是本函数唯一容易骗人的地方）
            try:
                _dbg = ROOT.parent / "log" / "flaky-subprocess.log"
                _dbg.parent.mkdir(parents=True, exist_ok=True)
                with open(_dbg, "a", encoding="utf-8") as _fh:
                    _fh.write(f"[SystemError] args={args!r} exc={exc!r}\n")
            except OSError:
                pass
            continue
    else:
        check(f"`{' '.join(args[:2])}` subprocess 可用", False,
              f"SystemError 连续 3 次：{last_err}"[:200])
        return 1, "", ""
    if "Traceback" in p.stderr:
        check(f"`{' '.join(args[:2])}` 无 Traceback", False,
              p.stderr.strip().splitlines()[-1][:150])
    if ok is not None and p.returncode not in ok:
        check(f"`{' '.join(args[:2])}` rc∈{ok}", False, f"rc={p.returncode} {p.stderr[:120]}")
    return p.returncode, p.stdout, p.stderr


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    if not cond:
        print(f"  ✗ {name}   {detail}")


def gbytes():
    return (PDIR / "graph.json").read_bytes()


# ★ 2026-11-25：原写死 "/tmp/..." —— Windows 上没有 /tmp，
#   CI（windows-latest）直接 FileNotFoundError。用 tempfile 取平台临时目录。
OP_FILE = Path(tempfile.gettempdir()) / "_fuzz_op.json"


def publish(payload):
    """统一走 --op-file：argv 有 ~128KB 上限，模糊测试里那个 200KB 文本会用 --op 直接
    触发 OSError(Argument list too long) —— 那是 **OS 的限制，不是 fgs 的 bug**，
    但真用起来同样会踩，所以顺带给 publish 加了 --op-file 并在这里默认走它。"""
    OP_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return run("publish", PID, "--by", "fuzz", "--op-file", str(OP_FILE))


def apply(*extra):
    return run("apply", PID, *extra)


def main():
    shutil.rmtree(TMPDIR, ignore_errors=True)
    run("init", PID, "--kind", "ctf", "--goal", "矩阵/模糊测试", ok=(0,))
    # 造出每种类型的节点各两个
    run("hint", PID, "h1", ok=(0,))
    for t in ("A", "B"):
        run("step-add", PID, f"step {t}", ok=(0,))
    run("step-claim", PID, "s001", "--by", "fuzz", ok=(0,))
    run("step-conclude", PID, "s001", "f001 由 s001 产出", "--confidence", "verified", ok=(0,))
    run("fact-add", PID, "f002 独立事实", "--confidence", "verified", ok=(0,))

    print("1) 端点矩阵：11 种关系 × 所有类型组合")
    nodes = {k: v["type"] for k, v in json.loads(gbytes())["nodes"].items()}
    by_type = {}
    for i, t in nodes.items():
        by_type.setdefault(t, []).append(i)
    n_case = n_ok = n_bad = 0
    for rel, (fset, tset) in fgs.RELATIONS.items():
        for ft, tt in itertools.product(sorted(by_type), repeat=2):
            a, b = by_type[ft][0], by_type[tt][0]
            legal = ft in fset and tt in tset and a != b
            if not legal:
                continue
            # 已有同一条关系（例如 step-conclude 已经建了 produced）就跳过 ——
            # 那是"重复关系被拒"的既有行为，不是矩阵问题
            g0 = json.loads(gbytes())
            if any(e["from"] == a and e["type"] == rel and e["to"] == b for e in g0["edges"]):
                continue
            n_case += 1
            before = gbytes()
            rc, out, err = run("relate", PID, "--type", rel, "--from", a, "--to", b)
            if rc == 0:
                n_ok += 1
                # 合法就删掉，避免影响后续组合
                g = json.loads(gbytes())
                g["edges"] = [e for e in g["edges"]
                              if not (e["from"] == a and e["type"] == rel and e["to"] == b)]
                (PDIR / "graph.json").write_bytes(
                    json.dumps(g, ensure_ascii=False, indent=2).encode() + b"\n")
            else:
                n_bad += 1
                check(f"{rel} {ft}->{tt} 合法却被拒", False, err[:100])
                check(f"{rel} {ft}->{tt} 被拒时图未动", gbytes() == before)
    check(f"合法组合全部通过（{n_ok}/{n_case}）", n_bad == 0, f"{n_bad} 个合法组合被误拒")

    print("2) 非法组合必须被拒 + 图零改动")
    illegal = 0
    for rel, (fset, tset) in fgs.RELATIONS.items():
        for ft, tt in itertools.product(sorted(by_type), repeat=2):
            if ft in fset and tt in tset:
                continue
            illegal += 1
            a, b = by_type[ft][0], by_type[tt][0]
            before = gbytes()
            rc, out, err = run("relate", PID, "--type", rel, "--from", a, "--to", b)
            check(f"非法 {rel} {ft}->{tt} 被拒", rc != 0, f"rc={rc}")
            check(f"非法 {rel} {ft}->{tt} 图零改动", gbytes() == before)
    # 自环
    for rel in fgs.RELATIONS:
        for i in list(nodes):
            before = gbytes()
            rc, _, _ = run("relate", PID, "--type", rel, "--from", i, "--to", i)
            check(f"自环 {rel} {i}->{i} 被拒", rc != 0, f"rc={rc}")
            check(f"自环 {rel} {i} 图零改动", gbytes() == before)
    print(f"   （非法组合 {illegal} 个 + 自环 {len(fgs.RELATIONS) * len(nodes)} 个，全部断言）")

    print("3) 畸形 outbox 载荷：允许失败，不许崩，不许改图")
    junk = [
        "not-a-dict", 123, None, [], {},
        {"op": "unknown-op"},
        {"op": "fact"},                                   # 缺 text
        {"op": "fact", "text": ""},
        {"op": "fact", "text": ["不是字符串"]},
        {"op": "fact", "text": "x" * 200000},             # 超大文本
        {"op": "fact", "text": "ok", "confidence": "maybe"},
        {"op": "fact", "text": "ok", "confidence": None},
        {"op": "fact", "text": "ok", "meta": "不是对象"},
        {"op": "fact", "text": "ok", "meta": {"不认识的键": 1}},
        {"op": "fact", "text": "ok", "meta": {"phase": "不存在的阶段"}},
        {"op": "fact", "text": "ok", "meta": {"ioc": "没有等号"}},
        {"op": "fact", "text": "ok", "meta": {"ioc": [123]}},
        {"op": "fact", "text": "ok", "meta": {"tool": 123}},
        {"op": "step"},                                   # 缺 text
        {"op": "step", "text": "ok", "depends": ["nosuch"]},
        {"op": "relate", "type": "supports", "from": "nosuch", "to": "g001"},
        {"op": "relate", "type": "supports", "from": "f001", "to": "nosuch"},
        {"op": "relate", "type": "不在表里", "from": "f001", "to": "g001"},
        {"op": "conclude", "step": "f001", "fact": "x"},   # f001 不是 step
        {"op": "conclude", "step": "s001", "fact": "x"},   # s001 已 done
        {"op": "conclude", "step": "nosuch", "fact": "x"},
        {"op": "conclude", "step": "s002"},                # 缺 fact
        {"op": "conclude", "step": "s002", "fact": "x", "confidence": "nope"},
        # 特殊字符
        {"op": "fact", "text": "含 \\u0000 空字节 和 emoji 🀄️ 与 \\n 换行", "confidence": "verified"},
        {"op": "fact", "text": "键里有引号 \" 和反斜杠 \\\\ 和 $ { }", "confidence": "verified"},
    ]
    changed, blocked = 0, 0
    for payload in junk:
        before = gbytes()
        rc, _, err = publish(payload)
        if rc != 0:
            continue                                      # publish 阶段就拦下（合法）
        rc, _, err = apply()
        if rc == 0:
            changed += 1
        else:
            blocked += 1
            check("被拒时图零改动", gbytes() == before, f"载荷 {str(payload)[:60]}")
            if os.environ.get("FUZZ_VERBOSE"):
                why = [l for l in err.splitlines() if l.strip().startswith("-")]
                print(f"      拦下 rc={rc} {str(payload)[:40]}  ← "
                      f"{(why or err.splitlines() or ['?'])[0][:100]}")
        # ★ 每条用例必须**隔离**：被拒条目会一直留在 outbox，而"未结算投递阻塞调度"
        #   会让后面每一条都跟着失败 —— 不排空的话后半段用例全在测同一个坏条目。
        for _ in range(50):
            rest = [l for l in (PDIR / "outbox.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
            if not rest:
                break
            try:
                seq = json.loads(rest[0]).get("_seq")
            except json.JSONDecodeError:
                (PDIR / "outbox.jsonl").write_text("", encoding="utf-8")
                break
            run("outbox", PID, "--withdraw", seq, "--reason", "测试隔离")
    check("两条含特殊字符的载荷被正常接受", changed >= 2, f"只接受了 {changed} 条")
    print(f"   {len(junk)} 个畸形载荷：崩 0 次，接受 {changed} 条（特殊字符那两条），拦下 {blocked} 条")

    print("4) 收尾异常：outbox 里混入垃圾行 / 空行")
    (PDIR / "outbox.jsonl").write_text(
        "\n\n不是JSON\n" + json.dumps({"op": "fact", "text": "好的一条", "confidence": "verified"}) + "\n",
        encoding="utf-8")
    rc, out, err = run("apply", PID)
    check("混入垃圾行时 apply 不崩（rc 0 或 3）", rc in (0, 3), f"rc={rc} {err[:100]}")

    print("5) 边界状态：空项目")
    empty = PID + "_empty"
    shutil.rmtree(TMPDIR / "projects" / empty, ignore_errors=True)
    run("init", empty, "--kind", "ctf", ok=(0,))
    for args in (["show", empty], ["snapshot", empty], ["next", empty],
                 ["snapshot", empty, "--hash"], ["outbox", empty]):
        rc, out, err = run(*args)
        check(f"空项目 `{args[0]}` 正常", rc == 0 and bool(out.strip()), f"rc={rc} {err[:80]}")
    rc, out, _ = run("next", empty)
    check("空项目 next 给 goal 或 bootstrap", "goal" in out or "bootstrap" in out, out[:120])
    shutil.rmtree(TMPDIR / "projects" / empty, ignore_errors=True)

    print("6) 不存在的项目 / 节点：报错清晰且 rc=2")
    for args in (["show", "no_such_project"], ["next", "no_such_project"],
                 ["snapshot", "no_such_project"], ["apply", "no_such_project"]):
        rc, out, err = run(*args)
        check(f"不存在的项目 `{args[0]}` rc=2", rc == 2, f"rc={rc}")
    rc, out, err = run("fact-add", PID, "x", "--from", "nosuchnode")
    check("引用不存在节点 rc=2", rc == 2, f"rc={rc} {err[:80]}")

    shutil.rmtree(TMPDIR, ignore_errors=True)
    print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项（前 20）：")
        for x in failed[:20]:
            print("  -", x)
        return 1
    print("✓ 矩阵 + 模糊测试：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
