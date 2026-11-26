#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""libcsearch —— 用 leak 反查 libc 版本（走 libc.rip API；本机可达）

为什么需要（pwn 提速的关键一步）：
  拿到一个 leak（比如 `puts` 的真实地址）后，要确定 libc 版本才能算 system/one_gadget 偏移。
  本机只有 Ubuntu 26.04 的 glibc 2.43（靶机几乎不会是它），以前只能猜 / 手工翻表。
  libc.rip 提供按符号偏移批量匹配的 API（本机实测可达 200）。

用法：
  libcsearch puts=0x80e50 read=0x1148c0 system=0x52290     # 至少给 2 个符号更稳
  libcsearch --leak 0x7ffff7a0d000 puts=0x80e50 system=0x52290   # 带上基址则直接打印目标地址
  libcsearch ... --download /tmp/libc        # 把命中的 libc 下到目录（并打印路径）
  libcsearch --local /lib/x86_64-linux-gnu/libc.so.6   # 从本地 libc 直接读符号偏移（自测/对照用）

输出：候选 libc 列表（版本/ID/下载链接）+ 命中符号数；按命中数与版本新→旧排序。
注意：只发符号名与偏移（不发题目信息）；无网络/接口变更时如实报错，不猜。
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
#   自包含（自己 import sys），不依赖本文件是否已 import sys 或用别名。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

API = "https://libc.rip/api/find"
MAX_SYMS = 40


def http_json(url, payload=None, timeout=30):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data,
                                headers={"Content-Type": "application/json",
                                         "User-Agent": "pi-lab-libcsearch/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def local_symbols(path, want):
    """从本地 libc.so 读符号偏移（用 readelf，避免依赖 pyelftools）"""
    try:
        out = subprocess.run(["readelf", "-sW", path], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=120).stdout
    except (OSError, subprocess.SubprocessError) as e:
        print("✗ readelf 失败:", e, file=sys.stderr)
        return {}
    found = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 8 and parts[3] == "FUNC":
            name = parts[7].split("@")[0]
            if name in want and name not in found:
                try:
                    found[name] = int(parts[1], 16)
                except ValueError:
                    pass
    return found


def parse_pairs(items):
    syms = {}
    for it in items:
        if "=" not in it:
            print(f"✗ 参数要写成 符号=偏移 的形式：{it!r}", file=sys.stderr)
            sys.exit(2)
        k, v = it.split("=", 1)
        try:
            syms[k.strip()] = hex(int(v.strip(), 16))
        except ValueError:
            print(f"✗ 偏移不是十六进制：{it!r}", file=sys.stderr)
            sys.exit(2)
    return dict(list(syms.items())[:MAX_SYMS])


def main():
    ap = argparse.ArgumentParser(description="用 leak 反查 libc 版本（libc.rip）")
    ap.add_argument("symbols", nargs="*", help="符号=偏移，例：puts=0x80e50 system=0x52290")
    ap.add_argument("--leak", default="", help="已知的某个符号绝对地址（会用它算出其它符号的绝对地址）")
    ap.add_argument("--download", default="", help="把命中 libc 下到该目录")
    ap.add_argument("--local", default="", help="从本地 libc 读符号偏移（自测用）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.local:
        want = set(parse_pairs(a.symbols).keys()) or {"puts", "system", "read"}
        got = local_symbols(a.local, want)
        if a.json:
            print(json.dumps(got, indent=2))
        else:
            print(f"本地 {a.local} 的符号偏移：")
            for k, v in sorted(got.items(), key=lambda kv: kv[1]):
                print(f"  {k:<14} {hex(v)}")
        return 0

    syms = parse_pairs(a.symbols)
    if len(syms) < 2:
        print("⚠ 只给 1 个符号命中会很多（libc.rip 也建议 ≥2）；继续，但结果仅供参考", file=sys.stderr)
    try:
        hits = http_json(API, {"symbols": syms})
    except urllib.error.HTTPError as e:
        print(f"✗ API 返回 {e.code}（符号名拼错？或接口变了）", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"✗ 连不上 libc.rip（{type(e).__name__}: {e}）—— 如实报错，不猜版本", file=sys.stderr)
        return 1

    if not hits:
        print("没有命中：检查符号名（别用 puts@GLIBC' 这种形式）或偏移是否十六进制")
        return 1

    # 按命中符号数 + 版本号排序（新版本优先，通常靶机是较新的）
    def key(h):
        ver = h.get("version", "")
        nums = tuple(int(x) for x in re.findall(r"\d+", ver)[:3]) or (0,)
        return (-len(set(h.get("symbols", {})) & set(syms)), tuple(-n for n in nums))
    hits.sort(key=key)

    print(f"命中 {len(hits)} 个 libc（按命中符号数 / 版本新→旧）：")
    for h in hits[:10]:
        common = sorted(set(h.get("symbols", {})) & set(syms))
        line = f"  {h.get('id','?'):<34} {h.get('version','?'):<12} 命中 {len(common)}: {', '.join(common[:5])}"
        print(line)
        if a.leak:
            for sym, off in h.get("symbols", {}).items():
                if sym in ("puts", "read", "write", "system", "printf") and sym in syms:
                    base = int(a.leak, 16) - int(syms[sym], 16)
                    print(f"      基址候选 = {hex(base)}（由 {sym} 反推）")
                    break
        if a.download:
            url = h.get("download_url")
            if url:
                os.makedirs(a.download, exist_ok=True)
                dst = os.path.join(a.download, f"{h.get('id','libc')}.so")
                if not os.path.exists(dst):
                    try:
                        urllib.request.urlretrieve(url, dst)   # noqa: S310 (题内用途，URL 来自 libc.rip)
                    except Exception as e:
                        print("      下载失败:", e)
                        continue
                print("      → " + dst)
    if a.json:
        print(json.dumps(hits[:10], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
