#!/usr/bin/env python3
"""回归测试：P4 情报广播 R1-R5 五条风控（2026-11-07 补）

跑法：
    python3 board/tests/test_broadcast.py

为什么要有它
------------
fgs broadcast 是**全自动写别人图**的命令（操作者 Q4=A 定的），它能把一条结论
一次性写进最多 20 道题。这个能力方向错了会**污染多张图**，而它此前只有三条
反向用例（批次不存在 / 找不到源），等于只证明了"错入口会拒"，
**没证明五条风控真的拦得住**。

本文件把 R1-R5 钉成可执行验收：

  R1 源必须 verified —— inferred / unverified 一律拒
  R2 写入必须带来源标记 —— hint 正文以 [auto-broadcast from <pid>/<fid>] 开头
  R3 必须可回滚 —— --revert <batch> 后他题图里**零残留**，人工 hint 不动
  R4 同族判定 = tool + proof_kind + vuln 三元组，**禁止文本相似**
     （反例：两道题描述字面几乎一样，但 proof_kind 不同 → 不许广播）
  R5 永不覆盖 actor=operator 的 hint

用法约定（与其余测试一致）：退出码 0=通过，1=失败；用 PI_LAB_BOARD_DIR 隔离。
"""
from __future__ import annotations

import importlib.util
import json
import os
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
        print(f"  x {name}  {detail[:400]}")


def run(env: dict, *args, timeout=60):
    p = subprocess.run([sys.executable, str(FGS), *args], capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       env=env, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def load_mod(board_dir: Path):
    """先设 env 再 import —— PROJECTS 是导入时求值的模块级常量（见 test_sibling_graph）。"""
    os.environ["PI_LAB_BOARD_DIR"] = str(board_dir)
    spec = importlib.util.spec_from_file_location("fgs_under_test", FGS)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)  # noqa: S301
    return m


def facts(m, pid):
    return [n for n in m.load(pid)["nodes"].values() if n.get("type") == "fact"]


def hints(m, pid):
    return [n for n in m.load(pid)["nodes"].values() if n.get("type") == "hint"]


def main() -> int:
    if not FGS.exists():
        print(f"SKIP 找不到 {FGS}")
        return 77
    tmp = Path(tempfile.mkdtemp(prefix="bc-"))
    env = dict(os.environ)
    env["PI_LAB_BOARD_DIR"] = str(tmp)
    env["PI_LAB_WRITEUPS_DIR"] = str(tmp / "wps")
    env.pop("PI_LAB_KEEPALIVE", None)

    try:
        m = load_mod(tmp)

        for pid, tgt in (("pa", "http://x.example.com/a/1.php"),
                         ("pb", "http://x.example.com/b/2.php"),
                         ("pc", "http://y.example.com/c/3.php")):
            rc, out = run(env, "kickoff", pid, "--kind", "ctf", "--goal", pid, "--target", tgt)
            check(f"准备 kickoff {pid}", rc == 0, out[-200:])

        # ★ fact-add 没有 --meta，meta 只能从具名参数来：
        #   --tool / --proof-kind（枚举）/ --target ...；vuln 从正文关键词推（_fact_class）。
        #   所以这里刻意用 **不同 proof_kind** 造「字面很像但不同族」的反例。
        run(env, "fact-add", "pa", "app1 的 SSTI 模板注入可命令执行",
            "--confidence", "verified", "--tool", "curl", "--proof-kind", "sentinel")
        src = [f for f in facts(m, "pa") if "SSTI" in f["text"]][0]

        run(env, "fact-add", "pb", "app2 的 SSTI 模板注入可命令执行",
            "--confidence", "inferred", "--tool", "curl", "--proof-kind", "sentinel")
        # 非同族：正文**几乎一样**（同样的 SSTI 关键词），只有 tool 不同 —— 专打"文本相似即同族"
        run(env, "fact-add", "pc", "app3 的 SSTI 模板注入可命令执行",
            "--confidence", "inferred", "--tool", "nmap", "--proof-kind", "sentinel")

        print("1) R1 源必须 verified")
        run(env, "fact-add", "pa", "未坐实的 SSTI 猜测",
            "--confidence", "inferred", "--tool", "curl", "--proof-kind", "sentinel")
        inf = [f for f in facts(m, "pa") if f["text"].startswith("未坐实")][0]
        rc, out = run(env, "broadcast", "pa", "--fact", inf["id"])
        check("R1 inferred 源被拒", rc != 0 and "verified" in out, out[-300:])
        rc, out = run(env, "broadcast", "pa", "--fact", "f-no-such")
        check("R1 不存在的 fact 被拒", rc != 0, out[-200:])

        print("2) R4 同族判定 + R2 来源标记")
        rc, out = run(env, "broadcast", "pa", "--fact", src["id"])
        check("R4 广播执行成功", rc == 0, out[-300:])
        pb_hints, pc_hints = hints(m, "pb"), hints(m, "pc")
        check("R4 同族 pb 有 hint", len(pb_hints) == 1, str([h.get("text") for h in pb_hints]))
        check("R4 非同族 pc 无 hint（文本相似不算同族）", len(pc_hints) == 0,
              str([h.get("text") for h in pc_hints]))
        batch = ""
        if pb_hints:
            txt = pb_hints[0].get("text") or ""
            check("R2 hint 以来源标记开头", txt.startswith("[auto-broadcast from"), txt[:80])
            check("R2 标记里带 pid/fid", f"pa/{src['id']}" in txt, txt[:80])
            check("R2 meta 记了 batch 与族",
                  bool(pb_hints[0].get("meta", {}).get("batch"))
                  # vuln 由 _fact_class 从正文推：正文含 "ssti" → 归到"注入"类
                  # （不是写 "ssti" —— 判据是词表 _VULN_KEYWORDS，测试跟着判据走，不反过来）
                  and pb_hints[0]["meta"].get("family", {}).get("vuln") == "注入",

                  json.dumps(pb_hints[0].get("meta"), ensure_ascii=False)[:200])
            batch = pb_hints[0]["meta"]["batch"]

        print("3) 重复广播幂等")
        run(env, "broadcast", "pa", "--fact", src["id"])
        check("同一源重复广播不重复写", len(hints(m, "pb")) == 1, str(len(hints(m, "pb"))))

        print("4) R5 不覆盖 operator hint")
        if batch:
            run(env, "broadcast", "--revert", batch)
        run(env, "hint", "pb", "人工判断：这题走反序列化，别碰模板", "--from", "operator")
        op_before = [h for h in hints(m, "pb") if (h.get("from") or "") == "operator"]
        check("R5 人工 hint 已落盘", len(op_before) == 1)
        run(env, "broadcast", "pa", "--fact", src["id"])
        op_after = [h for h in hints(m, "pb") if (h.get("from") or "") == "operator"]
        check("R5 广播后人工 hint 原样还在",
              len(op_after) == 1 and op_after[0].get("text") == op_before[0].get("text"),
              str([h.get("text") for h in op_after]))
        auto_after = [h for h in hints(m, "pb")
                      if (h.get("text") or "").startswith("[auto-broadcast from")]
        check("R5 自动 hint 与人工 hint 并存（不覆盖不删）",
              len(auto_after) == 1, str(len(auto_after)))
        batch2 = auto_after[0]["meta"]["batch"] if auto_after else ""

        print("5) R3 回滚必须干净（零残留）")
        if batch2:
            rc, out = run(env, "broadcast", "--revert", batch2)
            check("R3 revert 成功", rc == 0, out[-300:])
        remain = [h for h in hints(m, "pb")
                  if (h.get("text") or "").startswith("[auto-broadcast from")]
        check("R3 回滚后他题图零自动 hint 残留", len(remain) == 0,
              str([h.get("text")[:60] for h in remain]))
        check("R3 回滚只删自己的，人工 hint 不动",
              len([h for h in hints(m, "pb") if (h.get("from") or "") == "operator"]) == 1)
        rc, out = run(env, "broadcast", "--revert", "bc-does-not-exist")
        check("R3 不存在的批次被拒", rc != 0, out[-200:])

        print("6) R4 三元组缺失 -> 拒，不猜")
        run(env, "fact-add", "pa", "完全裸的结论没有任何元数据", "--confidence", "verified")
        bare = [f for f in facts(m, "pa") if f["text"].startswith("完全裸")][0]
        rc, out = run(env, "broadcast", "pa", "--fact", bare["id"])
        check("R4 缺三元组且未显式 --to -> 拒", rc != 0 and ("同族判定" in out or "proof_kind" in out),
              out[-400:])
        rc, out = run(env, "broadcast", "pa", "--fact", bare["id"], "--to", "pc")
        check("R4 显式 --to 允许人工指定接收方", rc == 0, out[-300:])

    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    total = len(passed) + len(failed)
    print(f"\n通过 {len(passed)} / 失败 {len(failed)}（共 {total} 条）")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ 情报广播 R1-R5 风控：全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
