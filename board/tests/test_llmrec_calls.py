#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_llmrec_calls.py — LLM 调用级留痕（transcript 层）

为什么（2026-11-17）：ARTEX 用 Recorder 装饰 llm.Provider，让 HTTP transport 捕获**原始 wire 体**，
且把没有 session 的调用（拦截 judge）单列计费。baoan 没有自有 LLM transport —— 模型调用在外部
pi 进程里，wire 体在本机拿不到。所以落地取舍是：**只做拿得到的那一层**（每条 assistant 调用的
model/provider/usage/cost/入出字符/工具），并把「拿不到 wire 体」写进文档 —— 不伪造一份看起来很像的。

判据（跑出来的行为）：
  A. 解析：model_change 提供默认 provider/model；每条 assistant 一行；usage 缺失单独计数不当 0。
  B. CLI：--json 合法、--tail 只取末尾且重新编号、--out 落盘一行一调用（LF、无 .staging 残留）、
     坏行/半行 JSON 跳过不崩、路径不存在或空会话 rc=2、--label 落到记录里。
  C. 文档/接线：docs §4.6u 写明「wire 体拿不到、不伪造」；TOOLS.md 有 llmrec 行；bin 包装器在。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TOOL = os.path.join(ROOT, "tools", "llmrec.py")

_passed, _failed = 0, 0


def ck(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print("  x " + name + ("   [" + str(detail)[:300] + "]" if detail else ""))


def run(*args):
    r = subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=ROOT, timeout=120)
    return r.returncode, (r.stdout or ""), (r.stderr or "")


def line(**kw):
    return json.dumps(kw, ensure_ascii=False)


def fixture(dirpath, name="s1.jsonl", assistants=3, with_usage=True, extra_bad=False):
    p = Path(dirpath, name)
    rows = [
        line(type="session", version=3, id="s1", timestamp="2026-11-17T10:00:00.000Z", cwd="D:\\baoan"),
        line(type="model_change", provider="deepseek", modelId="deepseek-flash", timestamp="2026-11-17T10:00:01.000Z"),
        line(type="message", timestamp="2026-11-17T10:00:02.000Z",
             message={"role": "user", "content": [{"type": "text", "text": "先读源码"}]}),
    ]
    for i in range(assistants):
        msg = {"role": "assistant", "provider": "deepseek", "model": "deepseek-flash",
               "api": "openai-completions", "content": [{"type": "text", "text": "第 %d 步" % i}]}
        if i == 1:
            msg["content"].append({"type": "tool_use", "name": "bash", "input": {"command": "ls"}})
        if with_usage and i != assistants - 1:      # 最后一条故意不带 usage
            msg["usage"] = {"input": 100 + i, "output": 7 + i, "cache_read": 3, "cache_write": 1,
                            "cost": 0.001 * (i + 1)}
        rows.append(line(type="message", timestamp="2026-11-17T10:00:0%d.500Z" % (3 + i), message=msg))
        if i == 1:                                  # 工具结果之后紧跟一条用户消息：验证「请求侧字符」累计
            rows.append(line(type="message", timestamp="2026-11-17T10:00:09.500Z",
                             message={"role": "user", "content": [{"type": "text", "text": "继续"}]}))
    if extra_bad:
        rows.append('{"type": "message", "message": {"role": "assist')   # 半行（被 kill 现场）
        rows.append('')
    Path(p).write_text("\n".join(rows) + "\n", encoding="utf-8")
    return p


def main():
    tmp = tempfile.mkdtemp(prefix="llmrec-")
    p1 = fixture(tmp, "s1.jsonl", assistants=3)

    print("A) 解析")
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("llmrec", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    meta, recs = mod.calls(str(p1))
    ck("A1 三条调用", len(recs) == 3, len(recs))
    ck("A2 记录带 model/provider", recs[0]["model"] == "deepseek-flash" and recs[0]["provider"] == "deepseek", recs[0])
    ck("A3 序号从 1 连续", [r["n"] for r in recs] == [1, 2, 3], [r["n"] for r in recs])
    ck("A4 默认标签 main", recs[0]["label"] == "main", recs[0]["label"])
    ck("A5 首条 in_chars = 用户那轮字数", recs[0]["in_chars"] == len("先读源码"), recs[0]["in_chars"])
    ck("A6 带 usage 的取到值", recs[0]["usage"].get("input") == 100 and recs[0]["usage"].get("cache_read") == 3, recs[0]["usage"])
    ck("A7 缺 usage 标 missing 不当 0", recs[2]["missing_usage"] is True and not recs[2]["usage"], recs[2])
    ck("A8 cost 取 provider 回传", abs((recs[0]["cost"] or 0) - 0.001) < 1e-9, recs[0]["cost"])
    ck("A9 工具名抽出来", recs[1]["tools"] == ["bash"], recs[1]["tools"])
    ck("A10 请求侧字符只算非 assistant（工具结果/用户话）", recs[1]["in_chars"] == 0 and recs[2]["in_chars"] == len("继续"),
       (recs[1]["in_chars"], recs[2]["in_chars"]))
    s = mod.summarize(meta, recs)
    ck("A11 汇总调用数", s["calls"] == 3, s)
    ck("A12 汇总输入累加（100+101）", s["tokens"]["input"] == 201, s["tokens"])
    ck("A13 汇总 cost", abs((s["cost"] or 0) - 0.003) < 1e-9, s["cost"])
    ck("A14 汇总 missing", s["missing_usage"] == 1, s["missing_usage"])
    ck("A15 by_model 分组", s["by_model"].get("deepseek-flash", {}).get("calls") == 3, s["by_model"])

    print("B) CLI")
    rc, out, err = run("--file", str(p1), "--json")
    ck("B1 --json rc=0", rc == 0, err[:200])
    try:
        arr = json.loads(out)
        ok = True
    except ValueError:
        arr, ok = [], False
    ck("B2 --json 是合法 JSON 数组", ok and len(arr) == 3, out[:200])
    rc, out, err = run("--file", str(p1), "--tail", "2", "--json")
    arr2 = json.loads(out) if rc == 0 else []
    ck("B3 --tail 只取末尾", len(arr2) == 2, len(arr2))
    ck("B4 --tail 重新编号 1,2", [r["n"] for r in arr2] == [1, 2], [r["n"] for r in arr2])
    outp = Path(tmp, "rec.jsonl")
    rc, out, err = run("--file", str(p1), "--out", str(outp))
    ck("B5 --out rc=0", rc == 0, err[:200])
    raw = outp.read_bytes()
    ck("B6 一行一调用", raw.count(b"\n") == 3, raw.count(b"\n"))
    ck("B7 无 CRLF", b"\r" not in raw, "有 \\r")
    ck("B8 每行可解析", all(json.loads(x) for x in raw.decode("utf-8").splitlines()), raw[:120])
    ck("B9 没有 .staging 残留", not any(x.name.endswith(".staging") for x in Path(tmp).iterdir()), list(Path(tmp).iterdir()))
    rc, out, err = run("--file", str(p1), "--label", "judge", "--json")
    ck("B10 --label 落到记录", '"label": "judge"' in out, out[:120])
    pbad = fixture(tmp, "s2.jsonl", assistants=2, extra_bad=True)
    rc, out, err = run("--file", str(pbad), "--json")
    ck("B11 坏行跳过不崩", rc == 0 and "Traceback" not in err, (rc, err[:200]))
    rc, out, err = run("--dir", str(tmp), "--json")
    ck("B12 --dir 合并多会话", rc == 0 and len(json.loads(out)) >= 5, (rc, out[:80]))
    rc, out, err = run("--file", str(Path(tmp, "nope.jsonl")))
    ck("B13 路径不存在 rc=2", rc == 2 and "没有找到" in err, (rc, err[:120]))
    only = Path(tmp, "empty.jsonl")
    only.write_text(line(type="session", id="e") + "\n", encoding="utf-8")
    rc, out, err = run("--file", str(only))
    ck("B14 空会话 rc=2", rc == 2 and "没有 assistant" in err, (rc, err[:120]))
    empty_dir = Path(tmp, "nodir")
    empty_dir.mkdir(exist_ok=True)
    rc, out, err = run("--dir", str(empty_dir))
    ck("B15 空目录 rc=2", rc == 2, (rc, err[:120]))
    rc, out, err = run("--file", str(p1))
    ck("B16 人读带「拿不到 wire」提示", "wire" in out and "§4.6u" in out, out[:200])
    ck("B17 人读给合计", "次调用" in out and "输入" in out, out[:300])
    ck("B18 人读明说缺 usage 不是 0", "不是真的 0" in out, out[:400])

    print("C) 文档与接线")
    docs = Path(ROOT, "docs", "并行工作.md").read_text(encoding="utf-8", errors="replace")
    ck("C1 文档有 §4.6u", "4.6u" in docs, "")
    ck("C2 文档写明 wire 拿不到/不伪造", "拿不到" in docs and "不伪造" in docs, "")
    ck("C3 文档点名 judge 单列", "judge" in docs, "")
    tools_md = Path(ROOT, "tools", "TOOLS.md").read_text(encoding="utf-8", errors="replace")
    ck("C4 TOOLS.md 登记 llmrec", "llmrec" in tools_md, "")
    ck("C5 bin 包装器在", Path(ROOT, "tools", "bin", "llmrec").is_file(), "")
    src = Path(TOOL).read_text(encoding="utf-8", errors="replace")
    ck("C6 USAGE_KEYS 四类", 'USAGE_KEYS = ("input", "output", "cache_read", "cache_write")' in src, "")
    # ★ 2026-11-17 加：本仓 CLI 必须在**任何控制台代码页**下都能打印中文（约定见 apiharvest.py）。
    #   血账：llmrec 原来没 reconfigure → Windows cp936 下 print「…」直接 UnicodeEncodeError 崩，
    #   被 selfcheck 全量模式抓到 6 条假红；而手工 shell 里因为设了 PYTHONIOENCODING 一直没暴露。
    ck("C9 stdout 强制 UTF-8（Windows cp936 不崩）",
       'sys.stdout.reconfigure(encoding="utf-8"' in src, "")
    _env = dict(os.environ)
    _env.pop("PYTHONIOENCODING", None)
    _env.pop("PYTHONUTF8", None)
    _r = subprocess.run([sys.executable, TOOL, "--dir", str(Path(tmp, "s1.jsonl"))],
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        cwd=ROOT, timeout=120, env=_env)
    ck("C10 无 PYTHONIOENCODING 也 rc=0（不再 GBK 崩）", _r.returncode == 0,
       (_r.stderr or "")[-200:])
    ck("C11 输出是 UTF-8 中文而不是乱码", "调用" in (_r.stdout or ""), (_r.stdout or "")[:120])
    ck("C7 落盘走 staging+replace", ".staging" in src and "os.replace" in src, "")
    ck("C8 坏行跳过而不是抛", "except ValueError" in src, "")

    print("")
    print("ALL PASSED" if _failed == 0 else ("FAILED " + str(_failed)), "·", _passed, "assertions")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
