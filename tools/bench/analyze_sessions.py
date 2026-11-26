#!/usr/bin/env python3
"""量化每题会话里的时间浪费：容器冷启动等待、连接失败探测、无产出工具调用。

会话 jsonl 结构（pi session v3）：
  {"type":"message","timestamp":"...","message":{"role":..,"content":str|[{type:text|thinking|toolCall|toolResult}]}}
外层消息 type 只有 message 一种；内容在 message.content 里。
"""
import glob
import json
import os
import re
from datetime import datetime

SESSION_GLOB = "/opt/pi-ctf/work/solve/*/*.jsonl"
OK_RE = re.compile(r"HTTP/1\.[01] \d{3}|=== 1\) 响应头|\"Server\"|uvicorn|Apache/")
FAIL_RE = re.compile(r"Couldn't connect|Connection refused|conn-refused|Failed to connect")


def ts(s):
    for f in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(s, f)
        except ValueError:
            pass
    return None


def parts(msg):
    c = msg.get("content")
    if isinstance(c, str):
        yield "text", c
        return
    for p in c or []:
        yield p.get("type"), p


def txt_of(p):
    if isinstance(p, str):
        return p
    return json.dumps(p, ensure_ascii=False)


def analyze(path):
    code = os.path.basename(os.path.dirname(path))
    lines = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    msgs = [d for d in lines if d.get("type") == "message"]
    if not msgs:
        return None
    t0 = ts(msgs[0]["timestamp"])
    t_end = ts(msgs[-1]["timestamp"])
    first_ok = None
    fails = 0
    tool_calls = 0
    infra_probe = 0
    found = False
    for d in msgs:
        m = d.get("message") or {}
        role = m.get("role")
        for typ, p in parts(m):
            s = txt_of(p)
            if typ == "toolCall":
                tool_calls += 1
                if re.search(r"\bip (a|route)\b|/dev/tcp|nmap -Pn|ping -c", s):
                    infra_probe += 1
            if role == "toolResult":
                if "FOUND_FLAG=" in s:
                    found = True
                if FAIL_RE.search(s):
                    fails += 1
                    continue
                if first_ok is None and OK_RE.search(s):
                    first_ok = ts(d["timestamp"])
    wait = (first_ok - t0).total_seconds() if (first_ok and t0) else None
    return dict(code=code, dur=(t_end - t0).total_seconds() if t_end and t0 else 0,
                tools=tool_calls, fails=fails, infra=infra_probe,
                wait=wait, found=found)


def main():
    rows = [r for r in (analyze(p) for p in glob.glob(SESSION_GLOB)) if r]
    rows.sort(key=lambda r: -(r["wait"] or 0))
    print("%-14s %7s %6s %6s %6s %8s %s" % ("code", "dur", "tools", "fail", "infra",
                                            "首次可达", "flag"))
    tw = 0
    for r in rows:
        w = r["wait"]
        tw += w or 0
        print("%-14s %7.1f %6d %6d %6d %8s %s" % (
            r["code"], r["dur"], r["tools"], r["fails"], r["infra"],
            "-" if w is None else "%.1f" % w, "Y" if r["found"] else ""))
    waiters = [r for r in rows if (r["wait"] or 0) > 2]
    print("\n共 %d 题；首次可达等待合计 %.0fs；等待>2s 的 %d 题（平均 %.1fs）；"
          "连接失败工具调用合计 %d；基础设施探测调用合计 %d"
          % (len(rows), tw, len(waiters),
             (sum(r["wait"] for r in waiters) / len(waiters)) if waiters else 0,
             sum(r["fails"] for r in rows), sum(r["infra"] for r in rows)))


if __name__ == "__main__":
    main()
