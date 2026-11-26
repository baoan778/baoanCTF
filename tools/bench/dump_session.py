#!/usr/bin/env python3
"""把一个 agent 会话 jsonl 的消息流压成紧凑可读格式（排查用）。"""
import glob
import json
import os
import sys


def short(s, n=300):
    return str(s).replace("\n", " | ")[:n]


def dump(path, n=400):
    lines = [json.loads(l) for l in open(path, encoding="utf-8").read().splitlines() if l.strip()]
    print("==", os.path.basename(os.path.dirname(path)), "lines", len(lines))
    for d in lines[-n:]:
        t = d.get("type")
        if t in ("session", "model_change", "thinking_level_change"):
            continue
        if t == "message":
            msg = d.get("message") or {}
            role = msg.get("role")
            content = msg.get("content")
            if isinstance(content, str):
                print(" ", role, short(content, 400))
                continue
            for c in content or []:
                ct = c.get("type")
                if ct == "text":
                    print("  %s.text:" % role, short(c.get("text"), 500))
                elif ct in ("tool_use", "tool_call", "toolCall"):
                    print("  %s.tool[%s]:" % (role, c.get("name")),
                          short(json.dumps(c.get("input") or c.get("arguments"),
                                           ensure_ascii=False), 400))
                elif ct in ("tool_result", "tool-result", "toolResult"):
                    print("   result:", short(json.dumps(c.get("content"), ensure_ascii=False), 400))
                else:
                    print("  %s.%s:" % (role, ct), short(json.dumps(c, ensure_ascii=False), 300))
        else:
            print(" ", t, short(json.dumps(d, ensure_ascii=False), 250))


if __name__ == "__main__":
    if len(sys.argv) > 1:
        for p in sys.argv[1:]:
            dump(p)
    else:
        for f in sorted(glob.glob("/opt/pi-ctf/work/solve/*/*.jsonl"))[-3:]:
            dump(f)
