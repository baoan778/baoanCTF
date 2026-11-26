#!/usr/bin/env python3
"""回归测试：手法库的「候选 → 审核 → 合并」闸门（board/retrospect.py）

跑法：
    python3 board/tests/test_retrospect.py

为什么值得留（这套闸门是踩了三次才定型的）
------------------------------------------
第一版用「标题精确比对」→ 漏；第二版用「做法正文子串」→ 漏（wp-writer 会改写，原文对不上）；
第三版用「等权 token 覆盖率」→ 还是漏（中文 bigram 噪声把分数压到 34%，低于 40% 阈值）。
**三次都真的往正本里写进了重复条目**，每次都手工回滚 + 记审计。

结论：自动分数永远调不准 —— 调高漏判、调低误拦，而漏判的代价是库不可逆地变脏。
所以改成 **fail-closed**：只要有可察觉的重合就默认拦下，要写必须人显式 `--force-dup`。
下面这几条守的就是这个姿态别退化。

（测试全程在临时目录里跑，**不碰真手法库** —— 靠 PI_LAB_WRITEUPS_DIR 覆盖路径。）
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

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "retrospect.py"
PID = "_test_retrospect"

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail and not cond else ""))


def run(tmp, *args):
    env = {**os.environ, "PI_LAB_WRITEUPS_DIR": str(tmp),
           "PI_LAB_BOARD_DIR": str(tmp / "projects")}
    p = subprocess.run([sys.executable, str(SCRIPT), *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    return p.returncode, p.stdout, p.stderr


def cands(tmp):
    f = tmp / "手法库.candidates.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def audit(tmp):
    f = tmp / "手法库.audit.jsonl"
    return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()] if f.exists() else []


def main():
    tmp = Path(tempfile.mkdtemp(prefix="pi-lab-retrospect-"))
    try:
        # 造一个测试用的黑板项目（含 done step + produced verified fact + 整题兜底场景）
        proj = tmp / "projects" / PID
        proj.mkdir(parents=True)
        (proj / "graph.json").write_text(json.dumps({
            "schema": "fgs/v1", "project_id": PID, "kind": "pentest",
            "revision": 3, "nodes": {
                "g001": {"id": "g001", "type": "goal", "text": "测试用目标", "status": "active"},
                "s001": {"id": "s001", "type": "step", "text": "试一下用 feroxbuster 扫目录",
                         "status": "done"},
                "f001": {"id": "f001", "type": "fact", "confidence": "verified",
                         "text": "feroxbuster 命中 /admin 备份目录，返回 200 且可列目录",
                         "meta": {"phase": "triage", "action": "collect", "tool": "feroxbuster"},
                         "created_at": "2026-01-01T00:00:00Z",
                         "provenance": {"actor": "w1", "evidence": "feroxbuster -u ... 输出第 12 行"}},
                "f002": {"id": "f002", "type": "fact", "confidence": "unverified",
                         "text": "这条没坐实，不该进候选", "created_at": "2026-01-01T00:00:00Z",
                         "provenance": {}},
                # ── 死路通道用：abandoned step 产出的 verified 证伪结论 ──
                "s002": {"id": "s002", "type": "step",
                         "text": "试一下把 zip 伪加密当突破口", "status": "abandoned"},
                "f003": {"id": "f003", "type": "fact", "confidence": "verified",
                         "text": "排除：本地头与中央目录 flags 逐字段一致 → 不是伪加密（zipinfo -v 逐行比对）",
                         "created_at": "2026-01-01T00:00:00Z",
                         "provenance": {"actor": "w1",
                                        "evidence": "zipinfo -v out.zip 第 8 行 flags 与本地头相等"}},
            },
            "edges": [{"from": "s001", "type": "produced", "to": "f001", "reason": None,
                       "state": "active", "created_at": "2026-01-01T00:00:00Z"},
                      {"from": "s002", "type": "produced", "to": "f003", "reason": None,
                       "state": "active", "created_at": "2026-01-01T00:00:00Z"}],
            "solved": None, "bootstrapped": True, "reason_checkpoint": None,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        # 造一本已有条目的正本（跟待合并的那条**明显不同**，用于测成功路径）
        (tmp / "手法库.md").write_text(
            "# 手法库（跨题积累）\n\n---\n\n### 已有的老条目（Web 扫描）\n\n"
            "**触发条件**：目标开放 80/443 且无明显指纹\n\n**做法**：\n1. httpx 摸指纹\n\n"
            "**来源**：old-case\n", encoding="utf-8")

        print("T1 scan 从图里抽候选（只认 done Step + verified Fact）")
        rc, out, err = run(tmp, "scan", "--project", PID)
        check("exit=0", rc == 0, f"rc={rc} {err}")
        got = [c for c in cands(tmp) if c["status"] == "candidate"]
        check("抽到候选", len(got) >= 1, f"{len(got)} 条")
        check("每条都带 trigger/method/source",
              all(c.get("trigger") and c.get("method") and c.get("source") for c in got))
        check("来源指向具体 step 与 fact", all(c["source"]["facts"] for c in got))
        check("unverified 的 Fact 没进候选", all("f002" not in c["source"]["facts"] for c in got))
        check("带了结构化 meta 的被标为 ◆手法", any(c.get("signal") == "meta" for c in got))

        print("T2 merge 必须带 --reviewer")
        cid = got[0]["id"]
        rc, out, err = run(tmp, "--merge", cid)
        check("exit=1", rc == 1, f"rc={rc}")
        check("提示要署名", "--reviewer" in err, err[:100])

        print("T3 merge 成功路径")
        rc, out, err = run(tmp, "--merge", cid, "--reviewer", "张三",
                           "--generalize", "任何校验字段未随数据更新的格式都可反解")
        check("exit=0", rc == 0, f"rc={rc} {err}")
        body = (tmp / "手法库.md").read_text(encoding="utf-8")
        check("正本多了条目", body.count("### ") == 2, f"{body.count('### ')} 条")
        check("条目带来源", "**来源**：" in body)
        check("写了通用化", "任何校验字段未随数据更新" in body)
        check("审计记了合并", any(a["action"] == "merge" and a["reviewer"] == "张三" for a in audit(tmp)))
        check("审计记了覆盖率", any("lib_similarity" in a for a in audit(tmp)))

        print("T4 同一候选不能重复合并")
        rc, out, err = run(tmp, "--merge", cid, "--reviewer", "张三")
        check("exit=1", rc == 1, f"rc={rc}")
        check("说明已是 merged", "merged" in err, err[:100])

        print("T5 fail-closed：与正本有重合就默认拦下")
        # 造一条跟既有条目高度重合的候选（同一套词）
        cc = cands(tmp)
        cc.append({
            "id": "cand-deadbeef", "dedupe": "x", "status": "candidate", "signal": "meta",
            "created_at": "2026-01-01T00:00:00Z", "project": "x",
            "trigger": "目标开放 80/443 且无明显指纹，先摸指纹再扫目录",
            "method": ["`f001` 用 httpx 摸指纹，用 feroxbuster 扫目录，命中 /admin"],
            "source": {"project": "x", "step": "s001", "facts": ["f001"]},
            "evidence": [], "generalize": None, "pitfall": None,
            "reviewer": None, "reviewed_at": None,
        })
        (tmp / "手法库.candidates.json").write_text(json.dumps(cc, ensure_ascii=False, indent=2), encoding="utf-8")
        n_before = (tmp / "手法库.md").read_text(encoding="utf-8").count("### ")
        rc, out, err = run(tmp, "--merge", "cand-deadbeef", "--reviewer", "张三")
        check("exit=1（拦下）", rc == 1, f"rc={rc}")
        check("指出了最像的那条", "已有的老条目" in err, err[:200])
        check("给了两条出路", "--force-dup" in err and "--drop" in err, err[:200])
        check("正本没被写", (tmp / "手法库.md").read_text(encoding="utf-8").count("### ") == n_before)

        print("T6 --force-dup 是人明确确认后的唯一通道")
        rc, out, err = run(tmp, "--merge", "cand-deadbeef", "--reviewer", "张三", "--force-dup")
        check("exit=0", rc == 0, f"rc={rc} {err}")
        check("审计记了 force_dup", any(a.get("force_dup") for a in audit(tmp)))
        check("审计记了最像的条目", any(a.get("closest") for a in audit(tmp)))

        print("T7 drop 也是结论（弃用后不再冒出来）")
        cc = cands(tmp)
        cc.append({
            "id": "cand-cafebabe", "dedupe": "y", "status": "candidate", "signal": "prose",
            "created_at": "2026-01-01T00:00:00Z", "project": "x",
            "trigger": "某个只对本题成立、换道题就没用的过程记录",
            "method": ["`f009` 只在本机成立的操作"],
            "source": {"project": "x", "step": "s009", "facts": ["f009"]},
            "evidence": [], "generalize": None, "pitfall": None,
            "reviewer": None, "reviewed_at": None,
        })
        (tmp / "手法库.candidates.json").write_text(json.dumps(cc, ensure_ascii=False, indent=2), encoding="utf-8")
        cid2 = "cand-cafebabe"
        rc, out, err = run(tmp, "--drop", cid2, "--reviewer", "张三", "--reason", "太具体")
        check("exit=0", rc == 0, f"rc={rc}")
        check("状态变 dropped", any(c["id"] == cid2 and c["status"] == "dropped" for c in cands(tmp)))
        check("审计记了 drop", any(a["action"] == "drop" for a in audit(tmp)))
        rc, out, err = run(tmp, "--list")
        check("list 里不再出现它", cid2 not in out, out[:200])

        print("T8 drop 也要署名")
        cc = cands(tmp)
        remain = [c["id"] for c in cc if c["status"] == "candidate"]
        if remain:
            rc, out, err = run(tmp, "--drop", remain[0])
            check("exit=1", rc == 1, f"rc={rc}")
        else:
            check("exit=1", True)

        print("T10 死路通道：kind=deadend 单独一条，且不入手法库")
        got = cands(tmp)
        dead = [c for c in got if c.get("kind") == "deadend"]
        tech = [c for c in got if c.get("kind") != "deadend"]
        check("抽到死路候选", len(dead) == 1, f"{len(dead)} 条")
        check("死路来自 abandoned step（signal=abandoned）",
              bool(dead) and dead[0].get("signal") == "abandoned", str(dead[:1]))
        check("f003 没被当成手法", all("f003" not in c["source"]["facts"] for c in tech))
        check("abandoned step 不产手法候选", all(c["source"]["step"] != "s002" for c in tech))

        n_tech_before = (tmp / "手法库.md").read_text(encoding="utf-8").count("### ")
        did = dead[0]["id"]
        rc, out, err = run(tmp, "--merge", did, "--reviewer", "张三",
                           "--generalize", "本地头/中央目录 flags 不一致才是伪加密；一致就直接排除")
        check("merge exit=0", rc == 0, f"rc={rc} {err}")
        check("写进了死路库.md", (tmp / "死路库.md").exists())
        dead_body = (tmp / "死路库.md").read_text(encoding="utf-8")
        check("死路模板带「为什么（现象与判据）」", "**为什么（现象与判据）**" in dead_body, dead_body[:200])
        check("死路条目带 ✗ 前缀", "### ✗ " in dead_body)
        check("手法库没被写", (tmp / "手法库.md").read_text(encoding="utf-8").count("### ") == n_tech_before)
        check("审计记了 kind=deadend 与目标库",
              any(a.get("kind") == "deadend" and "死路库" in (a.get("lib") or "") for a in audit(tmp)))

        print("T11 库开头的 ``` 格式示例不会被当成真条目")
        # 血账：2026-10-12 实测 —— 死路库.md 头部的「记录格式」示例里有 `### ✗ <走不通的路线>（一句话）`，
        # 不剥掉围栏就会在 `fgs brief` 里冒出一条假命中。
        (tmp / "死路库.md").write_text(
            "# 死路库\n\n```markdown\n### ✗ 围栏里的假条目\n"
            "**走不通的路线**：围栏里的假条目（整段都被围栏包着，不该被索引）\n```\n",
            encoding="utf-8")
        cc = cands(tmp)
        cc.append({"id": "dead-fence01", "dedupe": "z", "status": "candidate", "kind": "deadend",
                   "signal": "prose", "created_at": "2026-01-01T00:00:00Z", "project": "x",
                   "trigger": "围栏里的假条目", "method": ["`f009` 围栏里的假条目"],
                   "source": {"project": "x", "step": "(f009 直投)", "facts": ["f009"]},
                   "evidence": [], "generalize": None, "pitfall": None,
                   "reviewer": None, "reviewed_at": None})
        (tmp / "手法库.candidates.json").write_text(json.dumps(cc, ensure_ascii=False, indent=2), encoding="utf-8")
        rc, out, err = run(tmp, "--list")
        check("exit=0", rc == 0, f"rc={rc}")
        check("围栏里的假条目不参与去重",
              not any("dead-fence01" in l and "疑似已在正本" in l for l in out.splitlines()),
              [l for l in out.splitlines() if "dead-fence01" in l][:2])
        check("list 把两类分开标", "✗" in out and "死路" in out, out[:120])

        print("T9 真手法库没被测试碰过")
        real = ROOT.parent / "docs" / "writeups" / "手法库.md"
        text = real.read_text(encoding="utf-8")
        real_titles = [l for l in text.splitlines() if l.startswith("### ")]
        # 只断言「基线还在 + 没被测试污染」，**不锁死条数**——
        # 2026-10-01 踩过：原来写死 ==2，结果加了一条正当手法（GOT[puts]→system 那个）就红。
        # 本用例的意图是「隔离」，不是「库不许长大」。
        check("基线条目还在（PNG IHDR 那条 + 格式示例）",
              any("PNG IHDR" in x for x in real_titles) and "### <手法名>" in text,
              f"{len(real_titles)} 条")
        check("没有测试条目混进去",
              not any("deadbeef" in x or "老条目" in x for x in real_titles))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ 手法库候选→审核→合并闸门：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
