#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pi-bench —— 量「扩展/插件到底让我慢多少、贵多少」

存在理由（2026-10-10 实测）：
  我问过自己「装了这么多扩展，会不会影响解题速度」——当时**答不上来**，只能猜。
  量完才发现结论跟直觉相反：
    · 我手写的 12 个 .ts 扩展：每轮 **+226 token**、启动 +0s、每次工具调用 0.05~0.4ms → 可忽略
    · 5 个 npm 扩展包：每轮 **+11,581 token**、启动 **+65s**
    · 而且那 65s 里 90% 不是逻辑慢 —— 是它们装在 D: 盘（小文件读 **5 MB/s**），
      同一份代码搬到 ext4/tmpfs 后启动从 79.7s 掉到 12.6s
  这三个数字决定了「该优化谁」，所以不该每次重新造探针。

怎么量（原理）：
  分别起一个**一次性 pi**（`--mode json --no-session -p ...`，不落会话文件），
  用 `-ne`（关扩展发现）+ `-e <路径>` 精确控制加载哪些，然后读它返回的
  `usage.input + usage.cacheRead`（= 该轮真实提示词大小）与事件时间戳，
  把墙钟拆成 **启动 / 模型 / 收尾** 三段，避免把模型抖动算到扩展头上。

用法：
  pi-bench                    # 基线(godmode) + 每个扩展单独 + 默认发现（=真实会话）
  pi-bench --runs 2           # 每个配置跑 2 次（模型延迟抖动大时用）
  pi-bench --mins             # 跳过逐个扩展，只跑 基线/默认 两个配置（快）
  pi-bench --ext ctf-ops.ts   # 只量指定扩展
  pi-bench --json             # 机器可读

注意：
  · 每次运行会真发一次模型请求（token 极省：一次约 22K 输入 / 几条输出）
  · 不写会话文件、不动黑板；任何异常都如实打印，不猜
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import time

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
#   自包含（自己 import sys），不依赖本文件是否已 import sys 或用别名。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = os.environ.get("PI_LAB_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
AGENT = os.path.join(LAB, ".pi", "agent")
EXT_DIR = os.path.join(AGENT, "extensions")
GOD = os.path.join(EXT_DIR, "deepseek-flash-godmode.ts")   # 操作者自己的那套，算基线
PROMPT = "Reply with exactly: PING_OK"


def pi_pkg_dir():
    """pi 包根目录（跟着 PATH 里的 pi 走，不写死）"""
    from shutil import which
    b = which("pi")
    if not b:
        return None
    real = os.path.realpath(b)                      # …/dist/bundle/cli.js
    root = os.path.dirname(os.path.dirname(os.path.dirname(real)))   # → 包根
    return root if os.path.isdir(os.path.join(root, "dist")) else None


def npm_extensions():
    """已装的 npm 扩展：<包目录>/node_modules/<pkg> 里 package.json 的 pi.extensions"""
    out = []
    nm = os.path.join(AGENT, "npm", "node_modules")
    for pj in sorted(glob.glob(os.path.join(nm, "*", "package.json"))):
        try:
            with open(pj, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        pi = d.get("pi") or {}
        for rel in pi.get("extensions") or []:
            p = os.path.join(os.path.dirname(pj), rel)
            out.append((d.get("name", os.path.basename(os.path.dirname(pj))), p))
    return out


def run_once(label, expaths, timeout=600, cwd=None):
    cmd = ["pi", "--mode", "json", "--no-session", "-ne", "-e", GOD]
    for p in expaths:
        cmd += ["-e", p]
    cmd += ["-p", PROMPT]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, cwd=cwd or LAB)
    except subprocess.TimeoutExpired:
        return {"label": label, "error": "超时(>%ds)" % timeout}
    except OSError as e:
        return {"label": label, "error": "起不来: %s" % e}
    t1 = time.time()
    r = {"label": label, "wall": t1 - t0, "model_ms": None, "boot": None,
         "tail": None, "tok": None, "out": None, "err": ""}
    for line in (p.stdout or "").splitlines():
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("type") == "message_end":
            m = o.get("message") or {}
            u = m.get("usage") or {}
            if m.get("role") == "assistant" and u:
                ts = m.get("timestamp") or 0
                r["model_ms"] = m.get("durationMs")
                r["tok"] = u.get("input", 0) + u.get("cacheRead", 0)
                if ts:
                    r["boot"] = ts / 1000.0 - t0                       # 启动 + 排队 + TTFT
                    r["tail"] = t1 - (ts / 1000.0 + (m.get("durationMs") or 0) / 1000.0)  # 出话完成→退出
    # pi 的报错常在 stderr 或非 JSON 行里；不吞掉，但只留第一行
    for src in (p.stderr or "", p.stdout or ""):
        for ln in src.splitlines():
            if ln.lstrip().startswith("{"):
                continue                      # JSON 事件行不是报错（系统提示词里全是 error 字样）
            low = ln.lower()
            if any(k in low for k in ("error", "cannot find", "syntaxerror", "failed")):
                r["err"] = ln.strip()[:140]
                break
        if r["err"]:
            break
    return r


def fmt(r):
    if r.get("error"):
        return "%-30s %s" % (r["label"], r["error"])
    boot = r["boot"] if r["boot"] else float("nan")
    tail = r["tail"] if r["tail"] is not None else float("nan")
    s = ("%-30s 墙钟=%6.1fs  启动→出话=%5.1fs(含TTFT)  模型=%5sms  收尾=%5.1fs  提示=%6s tok"
         % (r["label"], r["wall"], boot, r["model_ms"], tail, r["tok"]))
    return s + ("   ⚠ " + r.get("err", "") if r.get("err") else "")


def main():
    ap = argparse.ArgumentParser(add_help=True, description="量 pi 扩展的启动/提示词成本")
    ap.add_argument("--runs", type=int, default=1, help="每个配置跑几次（默认 1）")
    ap.add_argument("--ext", action="append", default=[], help="只量这些扩展（文件名或绝对路径）")
    ap.add_argument("--mins", action="store_true", help="只跑 基线 / 默认发现 两个配置")
    ap.add_argument("--timeout", type=int, default=600, help="单次运行的秒级上限")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()

    if not os.path.exists(GOD):
        print("⚠ 找不到基线扩展 %s（%s 还在吗？）" % (GOD, EXT_DIR), file=sys.stderr)
    root = pi_pkg_dir()
    print("pi 包: %s" % (root or "?"))
    print("探针: 每个配置起一次性 pi（--no-session），读 usage + 事件时间戳；基线=只装 godmode\n")

    def multi(label, exts):
        rs = [run_once(label, exts, a.timeout) for _ in range(a.runs)]
        best = min(rs, key=lambda r: r.get("wall", 1e9))
        if a.runs > 1 and not best.get("error"):
            walls = [r["wall"] for r in rs if not r.get("error")]
            if walls:
                best["wall_note"] = "min=%.1f avg=%.1f (n=%d)" % (min(walls), sum(walls) / len(walls), len(walls))
        return rs, best

    results = []
    _, base = multi("基线：只 godmode", [])
    results.append(base)
    print(fmt(base))

    if not a.mins:
        exts = []
        if a.ext:
            for e in a.ext:
                exts.append((os.path.basename(e), e if "/" in e else os.path.join(EXT_DIR, e)))
        else:
            for p in sorted(glob.glob(os.path.join(EXT_DIR, "*.ts"))):
                if os.path.basename(p) != os.path.basename(GOD):
                    exts.append((os.path.basename(p), p))
            exts += npm_extensions()
        for name, path in exts:
            _, r = multi("  " + name, [path])
            results.append(r)
            print(fmt(r))

    # 默认发现（真实会话形态）：不加 -ne —— 用 run_once 的固定 -ne 不成，单起一次
    if not a.ext:
        cmd = ["pi", "--mode", "json", "--no-session", "-p", PROMPT]
        t0 = time.time()
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=a.timeout, cwd=LAB)
            t1 = time.time()
            tok = None
            ts = None
            dur = None
            for line in (p.stdout or "").splitlines():
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("type") == "message_end":
                    m = o.get("message") or {}
                    u = m.get("usage") or {}
                    if m.get("role") == "assistant" and u:
                        tok = u.get("input", 0) + u.get("cacheRead", 0)
                        ts = m.get("timestamp")
                        dur = m.get("durationMs")
            rr = {"label": "默认发现（真实会话）", "wall": t1 - t0, "model_ms": dur, "tok": tok,
                  "boot": (ts / 1000.0 - t0) if ts else None, "tail": (t1 - ts / 1000.0) if ts else None,
                  "err": ""}
        except Exception as e:
            rr = {"label": "默认发现（真实会话）", "error": str(e)}
        results.append(rr)
        print(fmt(rr))

    d = [r for r in results if r.get("tok") and r["label"].startswith("  ")]
    if base.get("tok") and d:
        print("\n相对基线的每轮提示词增量（这就是「每轮都要付一次」的成本）:")
        for r in sorted(d, key=lambda r: -(r["tok"] - base["tok"])):
            print("  %-30s %+7d tok" % (r["label"].strip(), r["tok"] - base["tok"]))
    if a.json:
        print("\n" + json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
