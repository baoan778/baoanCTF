#!/usr/bin/env python3
"""通杀（`fgs swarm`）回归：把「一打一片」做成黑板原生动作。

跑法：
    python3 board/tests/test_swarm.py

为什么要有这个文件
------------------
AutoHunter 的「通杀 Hunter」是个会自己发包的 Agent：出洞后判断能否一打一片，再去实打几个同款站点。
搬进 pi-lab 时做了一个**刻意的改造**：这里它是**纯机械动作** ——

    一条坐实的结论（verified Fact）+ 一批同款目标  →  黑板上的 N 条并行 Step

判据（算不算通用缺陷）留给人/主会话（写在 `skills/src-recon/references/目标优先级.md`），
批活交给黑板。理由：让 Agent 自己决定"要不要通杀 + 通杀多少"正是失控的来源；
拆成「人确认判据 → `fgs swarm` 机械派生 → 每个 Step 各自取证」，通杀就不会变成批量垃圾。

所以这个文件钉住四条：
  * 源 Fact 必须 **verified**（未坐实拿去复制 = 把不确定性乘以 N）
  * 派生的每条 Step 都带 `derived_from` 边指向源 Fact（图上一眼看得出这一片从哪来）
  * **幂等**：同文本的 Step 不会重复建（防重复劳动）
  * **上限**：默认 5，硬上限 20，超过要 `--force`（防一口气烧掉预算）

测试不碰真产物：`PI_LAB_BOARD_DIR` 指向临时目录。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
#   自包含（自己 import sys），不依赖本文件是否已 import sys 或用别名。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BOARD = Path(__file__).resolve().parents[1]
FGS = BOARD / "fgs.py"

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail and not cond else ""))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="pilab-swarm-"))
    PID = "sw"
    env = dict(os.environ, PI_LAB_BOARD_DIR=str(tmp / "projects"))

    def fgs(*args, ok=(0,)):
        p = subprocess.run([sys.executable, str(FGS), *args],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=120)
        return p.returncode, p.stdout, p.stderr

    def graph():
        return json.loads((tmp / "projects" / PID / "graph.json").read_text(encoding="utf-8"))

    def steps():
        return [n for n in graph()["nodes"].values() if n["type"] == "step"]

    try:
        print("1) 前置：一条坐实的结论（某指纹存在未授权注入）")
        check("init", fgs("init", PID, "--kind", "pentest", "--goal", "通杀验证")[0] == 0)
        fgs("fact-add", PID, "某 CMS 1.2 未授权 SQL 注入（500 + 延时 3s）", "--confidence", "verified")

        print("2) dry-run：只预览，不落盘")
        rc, out, err = fgs("swarm", PID, "--from", "f001",
                           "--targets", "a.edu.cn,b.edu.cn", "--dry-run")
        check("退出码 0", rc == 0, err[:200])
        check("预览里能看到两个目标", "a.edu.cn" in out and "b.edu.cn" in out, out[:300])
        check("标明未落盘", "dry-run" in out, out[:300])
        check("图里没有真的建 Step", not steps(), str(steps())[:200])

        print("3) 真派生：每条都带 derived_from 指回源 Fact")
        rc, out, err = fgs("swarm", PID, "--from", "f001",
                           "--targets", "a.edu.cn,b.edu.cn,c.edu.cn",
                           "--text", "在 {target} 用同一 payload 验证未授权注入，各自留响应证据")
        check("退出码 0", rc == 0, err[:200])
        check("建了 3 条", len(steps()) == 3, f"{len(steps())}")
        check("文本模板里的 {target} 被替换", "a.edu.cn" in steps()[0]["text"], steps()[0]["text"])
        edges = [e for e in graph()["edges"] if e["type"] == "derived_from"]
        check("3 条 derived_from 边指向 f001",
              len(edges) == 3 and all(e["to"] == "f001" for e in edges), str(edges)[:300])
        check("Step 都是 open（可直接派 explore）", all(s["status"] == "open" for s in steps()))

        print("4) 幂等：同样目标+同样模板不会重复建")
        rc, out, err = fgs("swarm", PID, "--from", "f001", "--targets", "a.edu.cn",
                           "--text", "在 {target} 用同一 payload 验证未授权注入，各自留响应证据")
        check("退出码 0", rc == 0, err[:200])
        check("明确说跳过", "跳过" in out, out[:300])
        check("Step 数量没变（仍 3 条）", len(steps()) == 3, f"{len(steps())}")

        print("5) 源必须是 verified：未坐实的结论不许通杀")
        fgs("fact-add", PID, "猜测：可能有反序列化", "--confidence", "inferred")
        rc, out, err = fgs("swarm", PID, "--from", "f002", "--targets", "d.edu.cn", ok=(0, 3))
        check("退出码 3", rc == 3, f"rc={rc}")
        check("说清为什么（不确定性乘以 N）", "乘以 N" in out + err, (out + err)[:300])
        check("没建出新 Step", len(steps()) == 3, f"{len(steps())}")

        print("6) 上限：默认 5，超硬上限要 --force")
        targets = ",".join(f"t{i}.edu.cn" for i in range(1, 26))
        rc, out, err = fgs("swarm", PID, "--from", "f001", "--targets", targets)
        check("默认只派生 5 条", len(steps()) == 8, f"{len(steps())}（3+5）")
        check("提示还有目标没派", "未派" in out, out[:300])
        rc, out, err = fgs("swarm", PID, "--from", "f001", "--targets", targets,
                           "--max", "50", "--text", "另一套模板 {target}", ok=(0, 3))
        check("--max 50 被拒（退出码 3）", rc == 3, f"rc={rc}")
        check("提示要 --force", "--force" in out + err, (out + err)[:300])
        rc, _, err = fgs("swarm", PID, "--from", "f001", "--targets", "x1.edu.cn,x2.edu.cn",
                         "--max", "50", "--force", "--text", "带 force 的模板 {target}")
        check("加 --force 后放行", rc == 0, err[:200])

        print("7) 目标去重 + 文件输入 + 审计留痕")
        fgs("swarm", PID, "--from", "f001", "--targets", "dup.edu.cn,dup.edu.cn",
            "--text", "去重测试 {target}")
        dup_steps = [s for s in steps() if "dup.edu.cn" in s["text"]]
        check("同一批里的重复目标只建一条", len(dup_steps) == 1, f"{len(dup_steps)}")
        tf = tmp / "targets.txt"
        tf.write_text("# 注释行\nf1.edu.cn\n\nf2.edu.cn\n", encoding="utf-8")
        rc, out, err = fgs("swarm", PID, "--from", "f001", "--targets-file", str(tf),
                           "--text", "文件输入 {target}")
        check("--targets-file 可用", rc == 0 and "f1.edu.cn" in out, (out + err)[:200])
        log = (tmp / "projects" / PID / "log.jsonl").read_text(encoding="utf-8")
        check("log 记了 swarm 且带源 Fact", "swarm" in log and '"from": "f001"' in log, log[-300:])
        check("log 记了目标数", '"given":' in log, log[-300:])

        print("8) 空目标要拒绝，别静默什么都不做")
        rc, out, err = fgs("swarm", PID, "--from", "f001", ok=(0, 3))
        check("没有目标时退出码 3", rc == 3, f"rc={rc}")
        check("说明怎么给目标", "--targets" in out + err, (out + err)[:200])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ 通杀（fgs swarm）：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
