#!/usr/bin/env python3
"""守 tools/bin 里的入口在宿主 Windows 上真的敲得出来。

## 为什么有这个测试（2026-11-19 血账）

六个入口（quiet / thinkcheck / mswitch / kimi-token / nccsec-token / opencode-token）
曾经是直接扔进 tools/bin/ 与 tools/hand/ 的**裸 Python 脚本**，首行 `#!/usr/bin/env python3`。
它们在容器里（Linux，内核真的解析 shebang）敲得出来，在**宿主 Windows** 上必炸：

    $ ./quiet --help
    ./quiet: line 26: quiet: command not found
    ./quiet: line 52: syntax error near unexpected token '('

根因不是 CRLF、不是执行位、也不是 python3 找不到 —— 是 **MSYS/Git-Bash 的 exec 层
不解析脚本 shebang**：凡是非 MSYS 原生（也就是原生 PE）的解释器，一律退化成
「用 shell 把整个文件解释一遍」。实测判据是**解释器是不是 MSYS 原生**，与语言无关：

    #!/usr/bin/env bash   + echo hi   -> 成功（bash 自己是 MSYS 原生）
    #!/usr/bin/env python3 + print(1) -> 失败
    #!/usr/bin/env node    + console.log(1) -> 失败
    #!/c/Users/.../python3.exe         -> 仍失败（原生 exe 可 exec，但当不了 shebang 解释器）

修法是结构性的：Python 实现住在 `tools/<name>.py`，由 `tools/make-bin.sh` 生成一个
`#!/bin/bash` + `exec "$PY" "$T/<name>.py" "$@"` 的壳放进 tools/bin/。

这个测试守两件事：
① tools/bin/ 与 tools/hand/ 里**没有**非 bash 的 shebang 脚本（ELF 二进制不算）；
② 六个搬过家的入口确实还是 bash 壳，并且壳指向的 tools/<name>.py 真的存在。

反向验证（造一个 `#!/usr/bin/env python3` 的探针）会让①变红 —— 见本文件 §C。
"""
import os
import pathlib
import sys

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = pathlib.Path(__file__).resolve().parents[2]
BIN = LAB / "tools" / "bin"
HAND = LAB / "tools" / "hand"

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail and not cond else ""))


def first_line(p: pathlib.Path):
    """取首行；二进制用 errors='replace'，遇到换行就停。"""
    try:
        with open(p, "rb") as f:
            head = f.read(200)
    except OSError:
        return ""
    return head.split(b"\n", 1)[0].decode("utf-8", "replace")


def offenders(dirs):
    """列出 shebang 存在但不是 bash/sh 的文件名（无 shebang 的 = 二进制，跳过）。"""
    out = []
    for d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if not p.is_file():
                continue
            h = first_line(p)
            if not h.startswith("#!"):
                continue
            if "bash" in h or h.strip() == "#!/bin/sh" or h.endswith("/sh"):
                continue
            out.append(p.name)
    return out


# 搬过家的六个入口：名字 -> 正本相对 tools/ 的路径
MOVED = {
    "quiet": "tools/quiet.py",
    "mswitch": "tools/mswitch.py",
    "kimi-token": "tools/kimi_token.py",
    "nccsec-token": "tools/nccsec_token.py",
    "opencode-token": "tools/opencode_token.py",
    "thinkcheck": "tools/thinkcheck.py",
}


def main() -> int:
    print("1) tools/bin 与 tools/hand 里没有非 bash 的 shebang 脚本")
    bad = offenders([BIN, HAND])
    check("没有裸非 bash 脚本", not bad, f"这些在宿主 Windows 上敲不出来: {' '.join(bad)}")

    print("2) 六个搬过家的入口：正本在 tools/*.py")
    for name, rel in MOVED.items():
        p = LAB / rel
        check(f"{rel} 存在", p.is_file(), str(p))

    print("3) 六个入口都在 tools/bin 里，是 bash 壳，且指向自己的 tools/*.py")
    # ★ 只查 bin/：这六个是 make-bin.sh 的 self_tools（从 tools/<name>.py 现生成），
    #   从来不是 hand/ 的正本 —— 查 hand/ 会把「本来就不该在」判成缺失。
    import re
    for name, rel in MOVED.items():
        f = BIN / name
        if not f.is_file():
            check(f"bin/{name} 存在", False, "文件不在（重跑 tools/make-bin.sh）")
            continue
        txt = f.read_text(encoding="utf-8", errors="replace")
        check(f"bin/{name} 是 bash 壳", txt.startswith("#!/bin/bash"), first_line(f))
        # 只认 exec 行里的路径，避免把注释里的例子当证据
        m = re.search(r'^exec\s+"\$PY"\s+"\$T/([A-Za-z0-9_./-]+)"', txt, re.M)
        check(f"bin/{name} 的 exec 指向 {rel}", bool(m) and m.group(1) == pathlib.PurePath(rel).name,
              m.group(1) if m else "没有 exec \"$PY\" \"$T/...\" 行")
        check(f"hand/{name} 不存在（它是 self_tools，不该在 hand/）", not (HAND / name).exists(),
              "hand/ 里还留着这个文件，下次 make-bin 会当成手写入口覆盖")

    print("4) 反向验证：造一个裸 python 探针，判定式必须能抓到")
    probe = HAND / "__probe_naked_py"
    try:
        probe.write_bytes(b"#!/usr/bin/env python3\nprint(1)\n")
        got = offenders([BIN, HAND])
        check("探针被点名", "__probe_naked_py" in got, f"实际: {got}")
    finally:
        try:
            probe.unlink()
        except OSError:
            pass
    check("探针已删，判定式回绿", "__probe_naked_py" not in offenders([BIN, HAND]))

    print()
    print(f"通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        for n in failed:
            print(f"  ✗ {n}")
        return 1
    print("✓ 入口可执行性：全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
