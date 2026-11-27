#!/usr/bin/env python3
"""回归测试：该轮修复 兄弟图 / 同前缀识别（2026-11-03，用户 既定决策）

跑法：
    python3 board/tests/test_sibling_graph.py

为什么要有它
------------
`LADDER_ROWS`（board/fgs.py 的 L0 侦察档）从落地起就写着：

    「前缀与已解题相同时跳过侦察、直接用兄弟图简报」

但**这句话一直没有任何实现** —— 全仓 grep「兄弟图」只有那一行。用户在 Q16 选了 A
（实现），所以这里把"实现"钉成可执行的验收，不允许再退回"文案大于实现"。

本文件守六件事：
  S1 前缀归一化：URL 取 scheme://host[:port]/第一段路径；非 URL 原样；空 → None
  S2 `kickoff --target` 真的**落盘**（过去只打印不存 → 兄弟图无据可判）
  S3 同前缀才联通；**不同前缀绝不串题**（这是"误判带沟里"的唯一防线）
  S4 只搬 `verified`：`inferred`/`unverified` 不许出现在兄弟图里
     （未坐实的搬过去 = 把不确定性乘以 N 道题，与广播 R1 同理）
  S5 已证伪（DEAD_RX 命中）的**不许**当建议搬过去：那是"这条路不行"，
     跨题直接说"不行"会误杀别题里可能成立的路
  S6 步骤收尾套话（`step-conclude --fact done` 之类）被滤掉，别挤掉真结论
  S7 没有 target 的题不崩、也不误判
  S8 兄弟图是**只读**提示：本命令不得改动兄弟项目的图

用法约定（与其余测试一致）：退出码 0=通过，1=失败；用 PI_LAB_BOARD_DIR 隔离开。
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess  # noqa: S404
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FGS = HERE.parent / "fgs.py"
passed, failed = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        passed.append(name)
    else:
        failed.append(name)
        print(f"  ✗ {name}  {detail[:400]}")


def run(env: dict, *args, timeout=60):
    p = subprocess.run([sys.executable, str(FGS), *args], capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       env=env, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def load_mod(board_dir: Path):
    """导入 fgs 模块，并**先**把 PI_LAB_BOARD_DIR 设成隔离目录。

    ★ 顺序不能倒：`fgs.py:63` 的 `PROJECTS = Path(os.environ.get("PI_LAB_BOARD_DIR") ...)`
      是**导入时求值**的模块级常量。先 import 再改 env 的话，模块仍然指真板子
      —— 那会让测试读到/写到真实项目（本次就踩了：报"项目 pa 不存在"）。
    """
    os.environ["PI_LAB_BOARD_DIR"] = str(board_dir)
    spec = importlib.util.spec_from_file_location("fgs_under_test", FGS)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)  # noqa: S301
    return m


def main() -> int:
    if not FGS.exists():
        print(f"SKIP 找不到 {FGS}")
        return 77
    tmp = Path(tempfile.mkdtemp(prefix="sib-"))
    env = dict(os.environ)
    env["PI_LAB_BOARD_DIR"] = str(tmp)
    env["PI_LAB_WRITEUPS_DIR"] = str(tmp / "wps")
    env.pop("PI_LAB_KEEPALIVE", None)

    try:
        m = load_mod(tmp)

        # ── S1 前缀归一化 ────────────────────────────────────────────────
        print("1) 前缀归一化")
        cases = [
            ("http://a.example.com/app1/index.php?x=1", "http://a.example.com/app1"),
            ("http://a.example.com/app1/", "http://a.example.com/app1"),
            ("http://a.example.com", "http://a.example.com"),
            ("http://a.example.com/", "http://a.example.com"),
            ("http://a.example.com:8080/x/y", "http://a.example.com:8080/x"),
            ("10.1.2.3", "10.1.2.3"),
            ("notaurl", "notaurl"),
        ]
        for src, want in cases:
            check(f"S1 {src!r} → {want}", m._task_prefix(src) == want,
                  f"got={m._task_prefix(src)!r}")
        check("S1 空串 → None（判不出就当不同族）", m._task_prefix("") is None)
        check("S1 None → None", m._task_prefix(None) is None)

        # ── S2 kickoff --target 必须落盘 ────────────────────────────────
        print("2) kickoff --target 落盘（过去只打印不存）")
        rc, out = run(env, "kickoff", "pa", "--kind", "ctf", "--goal", "A",
                      "--target", "http://t.example.com/app1/a.php")
        check("S2 kickoff 成功", rc == 0, out[-300:])
        ga = m.load("pa")
        check("S2 图上真有 target", ga.get("target") == "http://t.example.com/app1/a.php",
              f"target={ga.get('target')!r}")

        # 幂等：同值再写不刷 revision —— 但**只测 target 这一层**。
        # `kickoff --goal` 每次都会 bump goal.version（既有行为，不归 该轮修复 管），
        # 所以直接调 `_set_target` 验证幂等，别把两件事混在一个断言里。
        rev1 = m.load("pa").get("revision")
        m._set_target("pa", "http://t.example.com/app1/a.php")
        check("S2 target 同值重写不白刷 revision",
              m.load("pa").get("revision") == rev1,
              f"{rev1} → {m.load('pa').get('revision')}")
        m._set_target("pa", "http://t.example.com/app9/z.php")
        check("S2 target 变了要真写进去",
              m.load("pa").get("target") == "http://t.example.com/app9/z.php",
              f"target={m.load('pa').get('target')!r}")
        m._set_target("pa", "http://t.example.com/app1/a.php")   # 复原，后面用例要用

        # ── S3 同前缀才联通；不同前缀绝不串题 ───────────────────────────
        print("3) 同前缀联通 / 不同前缀隔离")
        run(env, "step-add", "pa", "s")
        run(env, "step-claim", "pa", "s001", "--by", "w1")
        run(env, "step-conclude", "pa", "s001", "app1 有 SSTI，模板引擎是 Jinja2")

        run(env, "kickoff", "pb", "--kind", "ctf", "--goal", "B",
            "--target", "http://t.example.com/app2/b.php")
        run(env, "step-add", "pb", "s")
        rc, out = run(env, "brief", "pb", "--step", "s001")
        check("S3 不同前缀**不**出兄弟图", "兄弟图" not in out, out[-400:])

        run(env, "kickoff", "pd", "--kind", "ctf", "--goal", "D",
            "--target", "http://t.example.com/app1/d.php")
        run(env, "step-add", "pd", "s")
        rc, out = run(env, "brief", "pd", "--step", "s001")
        check("S3 同前缀出兄弟图", "兄弟图" in out, out[-400:])
        check("S3 兄弟图里点出真结论", "Jinja2" in out, out[-400:])
        check("S3 兄弟图点出源项目名", "pa" in out, out[-400:])

        # ── S4 只搬 verified ────────────────────────────────────────────
        print("4) 只搬 verified（未坐实的不许跨题）")
        run(env, "fact-add", "pa", "猜测：可能存在 SSRF", "--confidence", "inferred")
        rc, out = run(env, "brief", "pd", "--step", "s001")
        check("S4 inferred 不进兄弟图", "SSRF" not in out, out[-500:])

        # ── S5 已证伪的不许当建议 ───────────────────────────────────────
        print("5) 已证伪的不许搬（会误杀别题的路）")
        run(env, "fact-add", "pa", "union 注入已证伪，走不通", "--confidence", "verified")
        rc, out = run(env, "brief", "pd", "--step", "s001")
        check("S5 证伪结论不进兄弟图", "union" not in out.lower(), out[-500:])
        check("S5 真结论仍在", "Jinja2" in out, out[-500:])

        # ── S6 收尾套话被滤掉 ──────────────────────────────────────────
        print("6) 步骤收尾套话不许挤掉真结论")
        run(env, "step-add", "pa", "s2")
        run(env, "step-claim", "pa", "s002", "--by", "w1")
        run(env, "step-conclude", "pa", "s002", "done")
        check("S6 _is_boilerplate 认得出 done", m._is_boilerplate("done"))
        check("S6 _is_boilerplate 认得出 完成", m._is_boilerplate("完成"))
        check("S6 真结论不算套话", not m._is_boilerplate("app1 有 SSTI，模板引擎是 Jinja2"))
        rc, out = run(env, "brief", "pd", "--step", "s001")
        sib = out.split("兄弟图", 1)[-1].split("## 知识指针", 1)[0] if "兄弟图" in out else ""
        # 语义断言（不钉 f002 这种编号 —— 编号会随用例顺序变，钉编号等于钉死顺序）
        check("S6 兄弟图里没有裸 done 条目",
              not any(ln.strip().endswith(" done") for ln in sib.splitlines()), sib[:400])
        check("S6 兄弟图里没有「完成」条目",
              "完成" not in sib.replace("已确认", ""), sib[:400])
        check("S6 真结论没被套话挤掉", "Jinja2" in sib, sib[:400])

        # ── S7 无 target 不崩、不误判 ──────────────────────────────────
        print("7) 无 target 的题不崩不误判")
        run(env, "kickoff", "pc", "--kind", "ctf", "--goal", "C")
        run(env, "step-add", "pc", "s")
        rc, out = run(env, "brief", "pc", "--step", "s001")
        check("S7 无 target 的 brief 仍 rc=0", rc == 0, f"rc={rc} {out[-300:]}")
        check("S7 无 target 不出兄弟图", "兄弟图" not in out, out[-300:])
        check("S7 无 target 时 _sibling_projects 返回空",
              m._sibling_projects("pc", m.load("pc")) == [])

        # ── S8 只读：查兄弟图不得改动兄弟项目 ──────────────────────────
        print("8) 只读性（不许因为看一眼就把别人的图改脏）")
        before = (tmp / "pa" / "graph.json").read_bytes()
        run(env, "brief", "pd", "--step", "s001")
        after = (tmp / "pa" / "graph.json").read_bytes()
        check("S8 兄弟项目的 graph.json 逐字节未变", before == after,
              f"{len(before)} → {len(after)}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ 兄弟图 / 同前缀识别：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
