#!/usr/bin/env python3
"""逐字溯源闸门 / 成本画像 / 统一门禁（`fgs verify` `cost` `gate`）回归。

跑法：    python3 board/tests/test_gate.py

为什么要有它
------------
2026 国内复盘的共同结论：**冠军队伍都把"flag 必须逐字出现在真实输出里"做成了机器闸门**
（hxbai 的 grounding 门槛、Muteki 的 Gate），而不是写在提示词里靠自觉。
本仓把这个缺口补成三件套：

* `fgs verify <pid> <fact> --from <原始输出>` —— **逐字溯源盖章**：声称的值必须 byte-exact 出现，
  否则拒绝（退出码 3）。这防的是「模型自己拼出来的 flag / 从别处抄来的 flag」。
* `fgs gate <pid>` —— **统一门禁**：一次过完 solved 依据 / 逐字盖章 / 授权范围 / outbox / 复审结论，
  给"能不能报"的唯一结论。
* `fgs cost <pid>` —— **成本画像**：步数、时长、坐实密度（verified/done），给"值不值得继续"的锚。

测试不碰真产物：`PI_LAB_BOARD_DIR` 指向临时目录。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
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
    tmp = Path(tempfile.mkdtemp(prefix="pilab-gate-"))
    PID = "g"
    env = dict(os.environ, PI_LAB_BOARD_DIR=str(tmp / "projects"))

    def fgs(*args, ok=(0,)):
        p = subprocess.run([sys.executable, str(FGS), *args],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=120)
        return p.returncode, p.stdout, p.stderr

    def graph():
        return json.loads((tmp / "projects" / PID / "graph.json").read_text(encoding="utf-8"))

    try:
        fgs("init", PID, "--kind", "ctf", "--goal", "拿到 flag")

        print("1) 逐字溯源：模型自己拼的值必须被拒（byte-exact 铁律）")
        fgs("fact-add", PID, "flag{Guessed_By_Model}", "--confidence", "verified")
        ev = tmp / "out.txt"
        ev.write_text("shell> cat /flag\nno such file or directory\n", encoding="utf-8")
        rc, out, err = fgs("verify", PID, "f001", "--from", str(ev), ok=(0, 3))
        check("伪造值 → 退出码 3", rc == 3, f"rc={rc}")
        check("报错点明这是「模型自己拼出来的值」", "模型自己拼出来" in out + err, (out + err)[:300])
        check("提示怎么补救（喂原始输出）", "原始输出" in out + err, (out + err)[:300])
        g = graph()
        check("被拒后**没有**盖章", (g["nodes"]["f001"].get("meta") or {}).get("verified_by") != "verbatim")
        gl = tmp / "projects" / PID / "gates.jsonl"      # append-only 台账（一次拦截一行）
        check("逐字拦截被计数（verify-verbatim）",
              gl.exists() and any(json.loads(x).get("g") == "verify-verbatim"
                                  for x in gl.read_text(encoding="utf-8").splitlines() if x.strip()),
              "gates.jsonl 缺失")

        print("2) 真实回显 → 通过并盖章（verbatim）")
        ev2 = tmp / "out2.txt"
        ev2.write_text("shell> cat /flag\nflag{Real_From_Output}\n", encoding="utf-8")
        fgs("fact-add", PID, "flag{Real_From_Output}，来源：/flag 回显", "--confidence", "verified")
        rc, out, err = fgs("verify", PID, "f002", "--from", str(ev2),
                           "--repro", "curl -s http://t/flag")
        check("真实值 → 退出码 0", rc == 0, (out + err)[:200])
        g = graph()
        m = g["nodes"]["f002"].get("meta") or {}
        check("盖上 verified_by=verbatim", m.get("verified_by") == "verbatim", str(m))
        check("记了可重放命令", m.get("repro") == "curl -s http://t/flag", str(m))
        check("记了证据文件路径", "out2.txt" in str(g["nodes"]["f002"].get("provenance")), str(g["nodes"]["f002"].get("provenance")))

        print("3) 逐字溯源的边界（不给证据 / 空证据 / 自定义 claim / 管道）")
        rc, out, err = fgs("verify", PID, "f002", "--from", str(tmp / "nope.txt"), ok=(0, 3))
        check("证据文件不存在 → 拒", rc == 3 and "不存在" in out + err, (out + err)[:200])
        rc, out, err = fgs("verify", PID, "f002", "--text", "   ", ok=(0, 3))
        check("空证据 → 拒", rc == 3, f"rc={rc}")
        rc, out, err = fgs("verify", PID, "f002", "--claim", "flag{Absent}", "--text", "无关内容", ok=(0, 3))
        check("自定义 claim 不存在 → 拒", rc == 3, f"rc={rc}")
        rc, out, err = fgs("verify", PID, "f002", "--claim", "flag{Real_From_Output}",
                           "--text", "前缀 flag{Real_From_Output} 后缀")
        check("自定义 claim 存在 → 通过", rc == 0, (out + err)[:200])
        p = subprocess.run([sys.executable, str(FGS), "verify", PID, "f002"], input="flag{Real_From_Output}\n",
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=60)
        check("管道喂证据也可用", p.returncode == 0, (p.stdout + p.stderr)[:200])

        print("4) doctor 会提醒「含 flag 形状却没有逐字证据」的结论")
        fgs("fact-add", PID, "flag{Never_Verified}", "--confidence", "verified")
        rc, out, err = fgs("doctor", PID, ok=(0, 1))
        check("doctor 提醒未盖章那条", "没有逐字证据" in out + err or "未盖章" in out + err, (out + err)[:400])
        check("提醒里给了 verify 命令", "fgs verify" in out + err, (out + err)[:400])
        check("只是提醒不判死（rc=0）", rc == 0, f"rc={rc}")

        print("5) fgs gate：没 solved 就是不能报")
        rc, out, err = fgs("gate", PID, ok=(0, 3))
        check("退出码 3", rc == 3, f"rc={rc}")
        check("说清缺的是 solved", "还没 solved" in out + err, (out + err)[:300])

        print("6) fgs gate：solved 但未盖章 / 未声明范围 → 仍不能报")
        fgs("relate", PID, "--type", "satisfies", "--from", "f003", "--to", "g001")
        fgs("solved", PID, "--fact", "f003", "--desc", "flag{Never_Verified}", "--no-lanes", "测试：单命令流程",
            "--chain", "测试用路径：入口→利用→flag")
        rc, out, err = fgs("gate", PID, ok=(0, 3))
        check("solved 但依据未逐字盖章 → 不能报", rc == 3, f"rc={rc}")
        check("点名要跑 verify", "verify" in out + err, (out + err)[:400])

        print("7) fgs gate：盖章 + 声明范围 + outbox 干净 → READY")
        fgs("scope", PID, "--set", "a.example")
        fgs("verify", PID, "f003", "--claim", "flag{Never_Verified}", "--text", "真实回显 flag{Never_Verified}")
        rc, out, err = fgs("gate", PID)
        check("退出码 0", rc == 0, (out + err)[:400])
        check("给出 READY 与下一步（solved → confirm）", "READY" in out and "confirm" in out, out[:400])

        print("7b) 太快复核（G7-toofast）：秒解且没给路径说明 → 不能报")
        fgs("init", PID + "f", "--kind", "ctf", "--goal", "x")
        fgs("fact-add", PID + "f", "flag{FAST}", "--confidence", "verified")
        fgs("relate", PID + "f", "--type", "satisfies", "--from", "f001", "--to", "g001")
        fgs("solved", PID + "f", "--fact", "f001", "--desc", "秒解", "--no-lanes", "测试")
        rc, out, err = fgs("gate", PID + "f", ok=(0, 3))
        check("秒解无路径 → 门禁拦（rc=3）", rc == 3 and "太快复核" in out + err, f"rc={rc} {(out + err)[:300]}")
        check("给出 --chain 修法", "--chain" in out + err, (out + err)[:300])
        gpf = tmp / "projects" / (PID + "f") / "gates.jsonl"
        check("拦截被记进台账（gate-toofast ≥ 1）",
              gpf.exists() and any(json.loads(x).get("g") == "gate-toofast"
                                   for x in gpf.read_text(encoding="utf-8").splitlines() if x.strip()),
              "gates.jsonl 缺失")

        print("8) fgs gate：被驳回的 verdict 不能报（deepen / ignored）")
        fgs("fact-add", PID, "疑似 IDOR，未打穿", "--confidence", "verified", "--action", "verify",
            "--verdict", "deepen")
        rc, out, err = fgs("gate", PID, ok=(0, 3))
        check("有 deepen 结论 → 不能报", rc == 3, f"rc={rc}")
        check("点明 verdict=deepen", "deepen" in out + err, (out + err)[:300])

        print("8b) gate 取「时间最新」的 verdict，不是字典里最后插入的那个（图会被 merge/手改，顺序不可信）")
        fgs("init", PID + "3", "--kind", "ctf", "--goal", "x")
        fgs("fact-add", PID + "3", "旧：被驳回", "--confidence", "verified",
            "--action", "verify", "--verdict", "deepen")
        fgs("fact-add", PID + "3", "新：复审通过", "--confidence", "verified",
            "--action", "verify", "--verdict", "accepted")
        g3p = tmp / "projects" / (PID + "3") / "graph.json"
        g3 = json.loads(g3p.read_text(encoding="utf-8"))
        f3 = [i for i, n in g3["nodes"].items() if n["type"] == "fact"]
        # 插入顺序：f3[0]=deepen 在前、f3[1]=accepted 在后；把时间改成「反过来」
        g3["nodes"][f3[0]]["updated_at"] = "2026-09-29T23:59:00+08:00"
        g3["nodes"][f3[1]]["updated_at"] = "2026-09-29T01:00:00+08:00"
        g3p.write_text(json.dumps(g3, ensure_ascii=False, indent=2), encoding="utf-8")
        rc, out, err = fgs("gate", PID + "3", ok=(0, 3))
        check("按时间取最新（deepen）而不是最后插入的 accepted",
              rc == 3 and "deepen" in out + err, f"rc={rc} {(out + err)[:300]}")

        print("9) fgs cost：步数/时长/坐实密度 + 边际递减信号")
        for i in (1, 2, 3):
            fgs("step-add", PID, f"试第 {i} 条路径")
        for sid in ("s001", "s002", "s003"):
            fgs("step-claim", PID, sid, "--by", "w1")
            fgs("step-conclude", PID, sid, "试过，不行", "--confidence", "inferred")
        rc, out, err = fgs("cost", PID)
        check("退出码 0", rc == 0, (out + err)[:200])
        for kw in ("Step 总数", "已完成", "坐实结论/步", "量级参考"):
            check(f"输出含 {kw}", kw in out, out[:400])
        check("坐实密度按 2 位小数打印（与 verified/done 一致）",
              f"{round(4/3, 2):.2f}" in out, out[:300])

        print("9b) 边际递减信号：一个「全是 inferred 产出」的项目应当报警")
        fgs("init", PID + "2", "--kind", "ctf", "--goal", "x")
        for i in (1, 2, 3):
            fgs("step-add", PID + "2", f"试第 {i} 条")
            fgs("step-claim", PID + "2", f"s00{i}", "--by", "w1")
            fgs("step-conclude", PID + "2", f"s00{i}", "试过不行", "--confidence", "inferred")
        rc, out, err = fgs("cost", PID + "2")
        check("密度 0.00", "0.00" in out, out[:300])

        print("9c) 手改过的图（垃圾时间戳）不能让 cost 崩（None 不能拿去和 datetime 比大小）")
        g2p = tmp / "projects" / (PID + "2") / "graph.json"
        g2 = json.loads(g2p.read_text(encoding="utf-8"))
        ids = [i for i, n in g2["nodes"].items() if n["type"] == "fact"]
        g2["nodes"][ids[0]]["updated_at"] = "not-a-timestamp"     # 非空但解析不了
        g2p.write_text(json.dumps(g2, ensure_ascii=False, indent=2), encoding="utf-8")
        rc, out, err = fgs("cost", PID + "2")
        check("cost 不崩（rc=0）", rc == 0, f"rc={rc} {(out + err)[:300]}")
        check("cost 没有 Traceback", "Traceback (most recent call last)" not in out + err, (out + err)[:300])
        check("触发边际递减提醒", "边际递减" in out, out[:400])
        rc, out, err = fgs("cost", PID, "--json")
        try:
            j = json.loads(out.strip())
            check("--json 可解析且含 verified_per_step", "verified_per_step" in j, out[:200])
        except Exception as e:
            check("--json 可解析", False, f"{e} {out[:200]}")

        print("9d) 该轮修复 points_per_slot_minute：数值必须能复算（points ÷ slot_minutes）")
        # 造一个已知场景：派活 20 分钟前开始 + 一条 awarded=300 的 accepted 回执
        # → 期望 300 ÷ 20 = 15 分/槽位分钟。算不出来就说明口径错了。
        fgs("init", PID + "3", "--kind", "ctf", "--goal", "该轮修复 复算")
        fgs("step-add", PID + "3", "面1")
        fgs("run-register", PID + "3", "s001", "--task", "打面1", "--by", "op")
        runs_p = tmp / "projects" / (PID + "3") / "runs.jsonl"
        rec = json.loads(runs_p.read_text(encoding="utf-8").splitlines()[0])
        rec["started_at"] = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        runs_p.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
        fgs("flag-record", PID + "3", "--id", "F1", "--flag", "flag{a}")
        fgs("flag-submit", PID + "3", "--id", "F1", "--reply", '{"correct":true,"awarded":300}')
        rc, out, err = fgs("cost", PID + "3", "--json")
        try:
            j3 = json.loads(out.strip())
            ppm, sm, pts = j3.get("points_per_slot_minute"), j3.get("slot_minutes"), j3.get("points")
            check("points=300（只认 reply.awarded）", pts == 300, f"实际 {pts}")
            check("slot_minutes≈20", sm is not None and abs(sm - 20) < 1.5, f"实际 {sm}")
            check("★ ppm 可由 points ÷ slot_minutes 复算",
                  ppm is not None and abs(ppm - pts / sm) < 0.02,
                  f"ppm={ppm} points={pts} slot_minutes={sm}")
        except Exception as e:
            check("该轮修复 --json 可解析", False, f"{e} {out[:300]}")

        print("9e) ★ 拿不到 awarded → 报「无法计算」，绝不当 0（多旗题每面分值不同，推分就是编数据）")
        fgs("init", PID + "4", "--kind", "ctf", "--goal", "无 awarded")
        fgs("step-add", PID + "4", "面1")
        fgs("run-register", PID + "4", "s001", "--task", "t", "--by", "op")
        fgs("flag-record", PID + "4", "--id", "F1", "--flag", "flag{a}")
        fgs("flag-submit", PID + "4", "--id", "F1", "--reply", '{"correct":true}')
        rc, out, err = fgs("cost", PID + "4", "--json")
        try:
            j4 = json.loads(out.strip())
            check("points 是 None（不是 0）", j4.get("points") is None, f"实际 {j4.get('points')}")
            check("ppm 是 None（不是 0）", j4.get("points_per_slot_minute") is None,
                  f"实际 {j4.get('points_per_slot_minute')}")
            check("缺 awarded 的 flag 被点名", j4.get("points_unknown_flags") == ["F1"],
                  f"实际 {j4.get('points_unknown_flags')}")
        except Exception as e:
            check("无 awarded --json 可解析", False, f"{e} {out[:300]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ 逐字溯源/成本/门禁三件套：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
