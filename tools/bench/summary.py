#!/usr/bin/env python3
"""每题一行：难度、工具数、是否拿到 flag、最后一个动作（判断卡点）。"""
import glob
import json
import os
import sys
import urllib.request

BASE = os.environ.get("BENCHMARK_BASE_URL", "https://tsecbench.zc.tencent.com")
TOK = os.environ.get("BENCHMARK_TOKEN", "1b0b3f85-c2ca-4905-8d32-3b7fbccc699a")
NL = chr(10)


def platform():
    req = urllib.request.Request(BASE.rstrip("/") + "/openapi/v1/challenges",
                                 headers={"BENCHMARK_TOKEN": TOK})
    with urllib.request.urlopen(req, timeout=30) as r:
        return {i["unique_code"]: i for i in json.load(r)}


def session_stat(path):
    raw = open(path, encoding="utf-8").read()
    tools = 0
    last = ""
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") != "message":
            continue
        c = (d.get("message") or {}).get("content")
        if not isinstance(c, list):
            continue
        for p in c:
            t = p.get("type")
            if t == "toolCall":
                tools += 1
                last = str((p.get("arguments") or {}).get("command", ""))[:150]
            elif t == "thinking":
                last = str(p.get("thinking", ""))[-200:].replace(NL, " ")
    return dict(tools=tools, found="FOUND_FLAG=" in raw, last=last)


def main():
    plat = platform()
    rows = []
    for p in glob.glob("/opt/pi-ctf/work/solve/*/*.jsonl"):
        code = os.path.basename(os.path.dirname(p))
        st = session_stat(p)
        info = plat.get(code, {})
        rows.append((code, info.get("difficulty", "?"), info.get("is_completed"),
                     st["found"], st["tools"], st["last"]))
    rows.sort(key=lambda r: (r[1], r[0]))
    only = sys.argv[1] if len(sys.argv) > 1 else "all"
    for code, diff, done, found, tools, last in rows:
        if only not in (diff, "all"):
            continue
        mark = "OK" if done else ("cand" if found else "FAIL")
        print("%-13s %-6s %-4s tools=%-3d %s" % (code, diff, mark, tools, last[:110]))


if __name__ == "__main__":
    main()