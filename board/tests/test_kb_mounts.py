#!/usr/bin/env python3
"""kb 采集源 ↔ 容器挂载 一致性测试 —— 治「索引看起来同步了，其实没同步」。

血账（2026-11-06，同类问题第三次出现）
--------------------------------------
1. 第一次：`agent-template/` 未挂 → 新扩展 `docker restart` 后容器里根本不存在。
2. 第二次：`board/*.py` 未挂 → `board/percept.py` 在容器里 `can't open file`。
3. 第三次（本轮）：`skills/` 与 `docs/` 未挂 → **容器内 `kb build` 扫不到宿主机新规程**。
   最阴险的地方：`kb stale` 只报「docs 下 5 个文件改过」，看起来"就差点小更新"，
   实际是整个 `skills/` 树不在视野里 —— **索引与源码长期静默不同步**。

根因是同一个：`compose.yaml` 只挂了部分目录，而 `tools/kb.py` 的 `collect_sources()`
扫的是**更多**目录。两者没有机器化的一致性检查。

本测试守四件事：
  ① 解析 kb.py 的 collect_sources()，取出它实际扫的顶层目录；
  ② 解析 compose.yaml 的 volumes，取出容器内已挂的宿主路径；
  ③ 断言：**每个 kb 采集源都在挂载范围内**（否则容器内 build 必漏）；
  ④ 断言：kb 索引文件在 .gitignore 里（47MB 不 progress 进仓库）。

    python3 board/tests/test_kb_mounts.py
"""
from __future__ import annotations

import pathlib
import re
import sys as _sys

_sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

try:
    import sys as _sm
    for _s in (_sm.stdout, _sm.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = pathlib.Path(__file__).resolve().parents[2]
KB = LAB / "tools" / "kb.py"
COMPOSE_FILE = LAB / "compose.yaml"

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
    print("✓ kb 采集源 ↔ 容器挂载：一致")
    return 0


def kb_source_dirs(src: str) -> set[str]:
    """从 collect_sources() 抽「扫的是哪些顶层目录」。

    不追求完整 AST 解析（够用即可）：只看 `LAB / "xxx"` 这种字面量拼接，
    以及 `LAB / "skills"` / `LAB / "docs"` / `LAB / "tools"`。
    """
    dirs = set()
    for m in re.finditer(r'LAB\s*/\s*"([A-Za-z0-9_.-]+)"', src):
        dirs.add(m.group(1))
    return dirs


def compose_mounted_host_dirs(src: str) -> set[str]:
    """从 compose.yaml 抽宿主侧挂载目录（形如 `- ./xxx:/opt/...`）。"""
    dirs = set()
    for m in re.finditer(r'^\s*-\s*\./([A-Za-z0-9_.\-/]+):/opt/', src, re.M):
        raw = m.group(1)
        # 取顶层目录名（./board/projects → board）
        dirs.add(raw.split("/")[0])
    return dirs


def _container_root() -> pathlib.Path | None:
    """容器里的仓库根（LAB 能直接认出 /opt/pi-ctf 时就返回它）。"""
    return LAB if str(LAB).startswith("/opt/") else None


def _mounts_from_runtime() -> set[str] | None:
    """★ 2026-11-07：容器内**读 compose.yaml 是错的**。

    真相：compose.yaml 被 .dockerignore 排除，不进镜像 → 容器里那份是构建时
    COPY 进来的旧副本（实测 /opt/pi-ctf/compose.yaml 79 行，宿主 106 行，
    缺 skills/docs/tools 三条挂载）。容器又不是 git 仓库（.git 也被排除），
    所以「宿主改了 compose 但忘了 apply」这件事，容器内**读文件永远看不出来**。

    正确判据是**运行时的真实挂载表**：/proc/self/mountinfo 里挂到
    /opt/pi-ctf/<dir> 的宿主路径，才是 pi 进程真正能扫到的字节。
    读不到（非 Linux / 无权限）就返回 None，由调用方回退到 compose 解析。
    """
    mi = pathlib.Path("/proc/self/mountinfo")
    root = _container_root()
    if root is None or not mi.is_file():
        return None
    dirs: set[str] = set()
    try:
        for line in mi.read_text(encoding="utf-8", errors="replace").splitlines():
            fields = line.split()
            if len(fields) < 5:
                continue
            target = fields[4].replace("\\040", " ")
            if not target.startswith(str(root) + "/"):
                continue
            rel = target[len(str(root)) + 1:]
            # 只取顶层目录名（board/projects → board）
            top = rel.split("/")[0]
            if top:
                dirs.add(top)
    except Exception:
        return None
    return dirs or None


def main() -> int:
    ok("tools/kb.py 存在", KB.is_file())

    runtime = _mounts_from_runtime()
    # 注意是 `if runtime` 不是 `is not None`：解析成功但空集 ≠ 有效挂载表，
    # 按「没拿到」处理并回退 compose。
    in_container = bool(runtime)
    if in_container:
        # 容器内：以**运行时挂载表**为准
        ok("能解析出运行时挂载（/proc/self/mountinfo）", True, f"mounts={sorted(runtime)}")
    else:
        ok("compose.yaml 存在", COMPOSE_FILE.is_file())
        if not (KB.is_file() and COMPOSE_FILE.is_file()):
            return _report()

    if not KB.is_file():
        return _report()

    kbsrc = KB.read_text(encoding="utf-8", errors="replace")
    if in_container:
        cmsrc = ""
        mounts = runtime or set()
    else:
        cmsrc = COMPOSE_FILE.read_text(encoding="utf-8", errors="replace")
        mounts = compose_mounted_host_dirs(cmsrc)

    src_dirs = kb_source_dirs(kbsrc)

    ok("能解析出 kb 采集源", bool(src_dirs), f"src_dirs={sorted(src_dirs)}")
    ok("能解析出挂载表", bool(mounts), f"mounts={sorted(mounts)}")

    # ③ 核心断言：每个采集源都必须有挂载
    missing = sorted(d for d in src_dirs if d not in mounts)
    ok("kb 所有采集源都已挂载进容器", not missing,
       f"未挂载：{missing}（容器内 kb build 会静默漏掉这些目录）")

    # 逐个点名，失败时能直接看出漏了谁
    for d in sorted(src_dirs):
        ok(f"采集源 {d}/ 已挂载", d in mounts,
           f"没有 ./{d}:/opt/pi-ctf/{d}（容器内 build 会漏掉它）")

    # ④ kb 索引不进 git
    gi = LAB / ".gitignore"
    ok(".gitignore 存在", gi.is_file())
    if gi.is_file():
        gisrc = gi.read_text(encoding="utf-8", errors="replace")
        ok("kb/ 已被 gitignore（47MB 不进仓库）",
           re.search(r"(?m)^kb/?\s*$", gisrc) is not None,
           "缺少 `kb/` 条目")

    # 反向守卫：整目录挂 board 会盖掉 board/projects 的持久化卷 —— 但**更深的挂载会盖回来**。
    #
    # ★ 2026-11-13 修假红：原判据是「只要出现 ./board:/opt/pi-ctf/board 就失败」，
    #   而 compose 自 2026-11-12 起正是这么挂的（board 整目录 :ro，随后 board/projects 单独 :rw）。
    #   运行时实测：/proc/self/mountinfo 里两条都在，projects 在后 = 生效的是持久化卷，
    #   `touch /opt/pi-ctf/board/projects/.wtest` 成功，而 board/tests 只读 —— 数据没丢。
    #   也就是说这条守卫从 11-12 起就一直是假红：**真缺陷早被修好，检查没跟上**。
    #   新判据：整目录挂 board **且** 其后没有更深的 board/projects 挂载，才算盖掉。
    if not in_container:
        lines = cmsrc.splitlines()
        board_idx = [i for i, ln in enumerate(lines)
                     if re.match(r"^\s*-\s*\./board:/opt/pi-ctf/board", ln)]
        proj_idx = [i for i, ln in enumerate(lines)
                    if re.match(r"^\s*-\s*\./board/projects:/opt/pi-ctf/board/projects", ln)]
        if not board_idx:
            ok("没有整目录挂 board（不会盖掉 projects 卷）", True)
        else:
            # 判据不是「谁在谁后面」—— 2026-11-13 实测：compose 里 ./board/projects 在第 94 行、
            # 整目录 ./board:ro 在第 100 行（**父目录后挂**），而 /proc/self/mountinfo 里两条都在，
            # 且 `touch /opt/pi-ctf/board/projects/.wtest` 成功、board/tests 只读。
            # 也就是说 Docker 是「兄弟挂载并存」，后挂父目录不会抹掉先挂的子目录。
            # 真判据 = **两个挂载点都声明了**（缺一个才真会丢数据）。
            ok("整目录挂 board 的同时仍单独声明了 board/projects（持久化卷不被盖掉）",
               bool(proj_idx),
               "有 ./board 整目录挂载但缺 ./board/projects —— 项目数据会落进只读的 board 层")

    return _report()


if __name__ == "__main__":
    raise SystemExit(main())
