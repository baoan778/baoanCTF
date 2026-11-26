#!/usr/bin/env python3
"""kb 回归：本地知识库（参考资料 + 手法库 + PoC 一个入口）。

为什么要有它：`kb` 的核心不是"能搜"，而是三条容易悄悄烂掉的性质——
  ① **增量重建真的只重建改过的文件**（靠 chunker_version + mtime/size 签名；
     2026-10-12 踩过：改了切块规则但 mtime 没变 → 重建报"0 个文件重建"，索引悄悄是旧的）
  ② **索引边界写清**（CVE 只回编号命中的；`--cat` 真的过滤；摘要真的被截断）
  ③ **索引放派生目录**（`PI_LAB_KB_DIR`），**不碰真产物**

约定：一切在临时目录里跑（`PI_LAB_ROOT` / `PI_LAB_KB_DIR` 都指向 tmp），
真仓库的 `tools/knowledge`、`poc-index.json` 一个字节都不动。

    py board/tests/test_kb.py
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys
import tempfile

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
#   自包含（自己 import sys），不依赖本文件是否已 import sys 或用别名。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = pathlib.Path(__file__).resolve().parents[2]
PY = LAB / "tools" / "venv" / "bin" / "python"
KB = LAB / "tools" / "kb.py"


def _exists(p) -> bool:
    """★ 2026-11-19：tools/venv/bin/python 是 WSL 里建的 Linux 符号链接，在 Windows 侧是断链。
    对这种断链，Path.exists() 在 Windows 上会抛 OSError [WinError 1920]（系统无法访问此文件），
    于是「宿主机跑这套测试」直接 TypeError 崩掉，看起来像产品坏了 —— 其实只是跨平台探测。
    这里把它当「不存在」处理，让测试在宿主上正常走 skip 分支。"""
    try:
        return p.exists()
    except OSError:
        return False


def main() -> int:
    checks = []

    def ok(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    if not KB.exists():
        ok("tools/kb.py 存在", False, "找不到 kb.py")
        for n, c, d in checks:
            print(f"  {'✓' if c else '✗'} {n}{(' — ' + str(d)) if d and not c else ''}")
        return 1
    if not _exists(PY):
        print("  ⏭ 跳过：tools/venv 不在（交接包可以不带 venv / Windows 侧是断链）")
        return 0

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="pikb-"))
    root = tmp / "lab"
    kdir = tmp / "kbidx"
    try:
        # ── fixture：一个最小的"仓库"（覆盖四种来源 + PoC 索引）──────────
        (root / "skills" / "ctf-web" / "references").mkdir(parents=True)
        (root / "skills" / "ctf-pwn" / "references").mkdir(parents=True)
        (root / "tools" / "knowledge" / "noptrace").mkdir(parents=True)
        (root / "tools" / "knowledge" / "riftx" / "recommended-skills" / "exploit-java" / "references").mkdir(parents=True)
        (root / "docs" / "writeups").mkdir(parents=True)
        (root / "tools" / "knowledge" / "ctf-wp" / "2026" / "review").mkdir(parents=True)
        (root / "tools" / "knowledge" / "ctf-wp" / "2026" / "raw").mkdir(parents=True)
        (root / "tools" / "knowledge" / "ctf-wp" / "2025" / "web").mkdir(parents=True)
        (root / "tools" / "ctfpoc" / "pocs").mkdir(parents=True)
        (root / "tools" / "knowledge" / "ctf-wp" / "2025" / "web" / "2025强网杯-wp.md").write_text(
            "<!-- 来源：https://example.invalid/x ｜ 发布：2025-10-20 -->\n"
            "# 2025强网杯 web wp\n\n> 赛事：2025强网杯 ｜ 方向：web\n\n"
            "## 第一题\n\n用 tcache poisoning 打 house of apple，注意 chunk 对齐。\n", encoding="utf-8")
        (root / "docs" / "竞赛模式.md").write_text(
            "# 竞赛模式\n\n## 少即是多\n\n限时多题时广度优先，先每题派一路，再给难题加人。\n", encoding="utf-8")
        (root / "docs" / "状态快照.md").write_text(
            "# 状态快照\n\nSNAPSHOTSENTINEL 这条不该进索引（churn 高）。\n\n补足长度避免被小块阈值丢掉。\n", encoding="utf-8")
        (root / "tools" / "knowledge" / "ctf-wp" / "2026" / "review" / "难题深读.md").write_text(
            "# 难题深读\n\n## Linkme\n\n把 linker 当 VM 用，段里没有函数只有重定位。\n", encoding="utf-8")
        (root / "tools" / "knowledge" / "ctf-wp" / "2026" / "raw" / "抓取.md").write_text(
            "# 原始抓取\n\nRAWSENTINEL 这是抓下来的原始材料，不该进索引免得稀释检索。\n", encoding="utf-8")
        (root / "skills" / "ctf-web" / "SKILL.md").write_text(
            "---\nname: ctf-web\n---\n# Web 题\n\n## 起手\n\n先摸指纹与响应头，再看 JS 里的路由表与未渲染的模板类名。\n\n## 常见面\n\nIDOR、注入、上传与认证绕过。\n", encoding="utf-8")
        (root / "skills" / "ctf-web" / "references" / "sqli.md").write_text(
            "# SQL 注入\n\n## 布尔盲注\n\n用 sleep 观察响应时间差来判断条件真假。\n", encoding="utf-8")
        (root / "skills" / "ctf-pwn" / "references" / "heap.md").write_text(
            "# 堆利用\n\n## tcache poisoning\n\n堆溢出后改写 next 指针，把 chunk 分配到目标地址。\n", encoding="utf-8")
        (root / "tools" / "knowledge" / "noptrace" / "Linux.md").write_text(
            "# 应急响应 Linux\n\n## webshell 排查\n\n看 access.log 里的异常 UA 与 POST 体积。\n", encoding="utf-8")
        (root / "tools" / "knowledge" / "riftx" / "recommended-skills" / "exploit-java" / "references" / "jndi.md").write_text(
            "# JNDI 注入\n\n## LOG4J2 — CVE-2021-44228\n\n用 ldap:// 回连。\n", encoding="utf-8")
        (root / "docs" / "writeups" / "手法库.md").write_text(
            "# 手法库\n\n### PNG IHDR 尺寸篡改（图片题）\n\n**触发条件**：CRC 对不上。\n", encoding="utf-8")
        (root / "tools" / "ctfpoc" / "poc-index.json").write_text(json.dumps({
            "built_at": "2026-01-01", "count": 2, "stats": {}, "items": [
                {"id": "afrog-nacos-unauth", "name": "Nacos 未授权下载配置信息", "lib": "afrog",
                 "path": "/v1/cs/configs?export=true", "expression": "response.body contains \"nacos\"",
                 "file": "pocs/afrog/nacos.yaml", "severity": "high", "tags": ["nacos"]},
                {"id": "mitan-log4j", "name": "Log4j CVE-2021-44228", "lib": "mitan",
                 "cve": ["CVE-2021-44228"], "file": "pocs/mitan/log4j.yaml", "packed": True},
            ]}, ensure_ascii=False), encoding="utf-8")

        env = dict(os.environ, PI_LAB_ROOT=str(root), PI_LAB_KB_DIR=str(kdir))

        def run(args, timeout=300):
            import subprocess
            p = subprocess.run([str(PY), str(KB), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                               env=env, timeout=timeout)
            return p.returncode, p.stdout + p.stderr

        # ── ① 建库 ───────────────────────────────────────────────────
        rc, out = run(["build"])
        ok("build 成功", rc == 0, out[-300:])
        ok("索引落在 PI_LAB_KB_DIR（不写仓库）", (kdir / "index.jsonl").exists())
        meta = json.loads((kdir / "meta.json").read_text(encoding="utf-8"))
        ok("meta 有 chunker_version（增量失效的钥匙）", meta.get("chunker_version", 0) >= 1)
        ok("PoC 进索引（kind=poc 计数=2）", meta["by_kind"].get("poc") == 2, str(meta["by_kind"]))
        ok("四种来源都进了（ref/skill/notes/writeup）",
           all(meta["by_kind"].get(k, 0) > 0 for k in ("ref", "skill", "notes", "writeup")), str(meta["by_kind"]))
        ok("分类正确（ctf-web→web / noptrace→ir / riftx-exploit→web）",
           meta["by_cat"].get("web", 0) >= 2 and meta["by_cat"].get("ir", 0) >= 1 and meta["by_cat"].get("pwn", 0) >= 1,
           str(meta["by_cat"]))

        ok("WP 仓库进索引（kind=ctfwp）", meta["by_kind"].get("ctfwp", 0) >= 1, str(meta["by_kind"]))
        _idx = [json.loads(l) for l in (kdir / "index.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        _dup = [d for d in _idx if d["path"].endswith("ctf-wp/2025/web/2025强网杯-wp.md")]
        ok("同一路径不得同时以两种 kind 进索引（ctfwp 不得被 notes 再收一遍）",
           _dup and all(d["kind"] == "ctfwp" for d in _dup) and not any(d["path"].endswith("ctf-wp/2025/web/2025强网杯-wp.md") and d["kind"] == "notes" for d in _idx),
           str([(d["kind"], d["cat"]) for d in _dup]))
        ok("docs/*.md 与 WP 仓库（2026/review 研读笔记）都进索引（kind=doc/ctfwp）",
           meta["by_kind"].get("doc", 0) >= 1 and meta["by_kind"].get("ctfwp", 0) >= 2, str(meta["by_kind"]))
        rc, out = run(["search", "RAWSENTINEL", "--top", "3"])
        ok("WP 仓库的 raw/ 被排除（检索不到）", "无命中" in out, out[-200:])
        rc, out = run(["search", "SNAPSHOTSENTINEL", "--top", "3"])
        ok("状态快照.md 被排除（churn 高）", "无命中" in out, out[-200:])

        # ── ② 检索：中文 / 分类 / PoC / CVE 精确通道 ─────────────────
        rc, out = run(["search", "堆溢出 tcache", "--cat", "pwn", "--top", "3"])
        ok("中文查询命中堆题", "tcache" in out or "堆利用" in out, out[-300:])

        rc, out = run(["search", "nacos 未授权", "--kind", "poc", "--top", "3"])
        ok("PoC 检索给请求骨架", "/v1/cs/configs" in out, out[-300:])

        rc, out = run(["search", "CVE-2021-44228", "--top", "3"])
        ok("CVE 精确通道命中", "CVE-2021-44228" in out, out[-300:])
        rc2, out2 = run(["search", "CVE-2019-0001", "--top", "3"])
        ok("不存在的 CVE 不回无关条目", "CVE-2021-44228" not in out2, out2[-200:])

        rc, out = run(["search", "webshell", "--cat", "ir", "--top", "3"])
        ok("--cat 过滤（ir 只回 ir）", "ir/ir" in out or "notes/ir" in out, out[-300:])

        rc, out = run(["search", "tcache", "--path", "2025/web", "--top", "3"])
        ok("--path 子串过滤（一个仓库按年分桶时用）", "ctf-wp/2025/web" in out or "无命中" in out, out[-200:])

        # ── ③ 有界输出 + 指针 ────────────────────────────────────────
        rc, out = run(["search", "webshell", "--top", "1", "--excerpt", "60"])
        ok("摘要受 --excerpt 约束", len(out.split("      ")[-1].splitlines()[0]) <= 90, out[-200:])
        ok("结果带 文件:行号 指针（.md:）", ".md:" in out, out[-300:])

        # ── ④ 增量：没改动 → 0 重建；改一个文件 → 只重建它 ────────────
        rc, out = run(["build"])
        ok("增量：无改动时 0 重建", "0 个文件重建" in out, out[-200:])
        target = root / "skills" / "ctf-pwn" / "references" / "heap.md"
        target.write_text(target.read_text(encoding="utf-8") + "\n## 新增小节\n\nhouse of apple 2 的用法。\n", encoding="utf-8")
        rc, out = run(["build"])
        ok("增量：改动后只重建 1 个文件", "1 个文件重建" in out, out[-200:])

        # ── ⑤ stale：落后能报出来，新索引不报 ────────────────────────
        rc, out = run(["stale"])
        ok("stale 在最新时不报错", rc == 0, out[-200:])
        target.write_text(target.read_text(encoding="utf-8") + "\n改了但没重建。\n", encoding="utf-8")
        rc, out = run(["stale"])
        ok("stale 在落后时 exit 1 并指出文件", rc == 1 and "heap.md" in out, out[-300:])

        # ── ⑥ read：按路径取片段 + 截断 ──────────────────────────────
        rc, out = run(["read", "skills/ctf-pwn/references/heap.md", "--lines", "1-3"])
        ok("read 按行区间取", "堆利用" in out, out[-200:])
        rc, out = run(["read", "skills/ctf-pwn/references/heap.md", "--chars", "20"])
        ok("read 有界（--chars 截断）", "截断" in out, out[-200:])

        # ── ⑥b PoC 原文路径解析（2026-11-18 根 pocs/ 归并后的回归）──────
        # 背景：PoC 原文曾同时躺在 根 pocs/ 和 tools/ctfpoc/pocs/ 两处，
        # 索引里的 file 字段写的是 "pocs/<x>"。cmd_read 原来两条 POC_ROOT 分支
        # 都会拼成 tools/ctfpoc/pocs/pocs/<x> 或 .../tools/ctfpoc/pocs/<x>（都永不存在），
        # 全靠「根 pocs/ 恰好存在」让 LAB/rel 命中才没暴露。
        # 根 pocs/ 删掉之后只剩兜底，这里把三条分支都钉死。
        (root / "tools" / "ctfpoc" / "pocs" / "community").mkdir(parents=True, exist_ok=True)
        (root / "tools" / "ctfpoc" / "pocs" / "community" / "demo.yaml").write_text(
            "# demo poC\nid: demo-poroot\n", encoding="utf-8")
        rc, out = run(["read", "pocs/community/demo.yaml"])
        ok("read：索引写 pocs/<x>、根 pocs/ 不存在时落到 tools/ctfpoc/pocs/<x>",
           rc == 0 and "demo-poroot" in out, out[-300:])
        rc, out = run(["read", "tools/ctfpoc/pocs/community/demo.yaml"])
        ok("read：显式 tools/ctfpoc/ 前缀直接命中（不再拼成 .../pocs/tools/ctfpoc/...）",
           rc == 0 and "demo-poroot" in out, out[-300:])
        rc, out = run(["read", "pocs/community/nonexistent-deadlink.yaml"])
        ok("read：死链如实报不存在，并列出试过的两个路径",
           rc == 0 and "不存在" in out and "tools/ctfpoc/pocs/community/nonexistent-deadlink.yaml" in out,
           out[-300:])

        # ── ⑦ 自检脚本本身 ───────────────────────────────────────────
        rc, out = run(["--selftest"], timeout=120)
        ok("kb --selftest 通过", rc == 0 and "PASS" in out, out[-300:])

        # ── ⑧ 索引目录默认值：不许写死 /root（2026-11-02 一类问题）──────
        # 原缺陷：KB_DIR 默认 "/root/pi-lab-kb"，非 root 用户（发布包 / WSL 普通账号）
        # 每条子命令都 PermissionError，知识库整个不可用。修法是默认 $HOME/pi-lab-kb ——
        # root 的 $HOME 就是 /root，所以容器行为逐字节不变，非 root 用户自动可用。
        import subprocess as _sp
        probe = (
            "import importlib.util as u,sys,pathlib;"
            "s=u.spec_from_file_location('kb', sys.argv[1]);m=u.module_from_spec(s);"
            "s.loader.exec_module(m);print(m.KB_DIR)"
        )
        env = {k: v for k, v in os.environ.items() if k != "PI_LAB_KB_DIR"}

        env_root = dict(env, HOME="/root")
        _p = _sp.run([str(PY), "-c", probe, str(KB)], capture_output=True, text=True,
                     encoding="utf-8", errors="replace", env=env_root, timeout=60)
        ok("HOME=/root 时索引仍是 /root/pi-lab-kb（容器行为不变）",
           _p.stdout.strip() == "/root/pi-lab-kb", (_p.stdout + _p.stderr)[-200:])

        env_user = dict(env, HOME="/home/someuser")
        _p = _sp.run([str(PY), "-c", probe, str(KB)], capture_output=True, text=True,
                     encoding="utf-8", errors="replace", env=env_user, timeout=60)
        ok("HOME=/home/someuser 时索引落到该用户家目录（非 root 可用）",
           _p.stdout.strip() == "/home/someuser/pi-lab-kb", (_p.stdout + _p.stderr)[-200:])

        env_expl = dict(env, HOME="/home/someuser", PI_LAB_KB_DIR="/tmp/explicit-idx")
        _p = _sp.run([str(PY), "-c", probe, str(KB)], capture_output=True, text=True,
                     encoding="utf-8", errors="replace", env=env_expl, timeout=60)
        ok("PI_LAB_KB_DIR 显式覆盖优先于 $HOME",
           _p.stdout.strip() == "/tmp/explicit-idx", (_p.stdout + _p.stderr)[-200:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for _n, c, _d in checks if c)
    for n, c, d in checks:
        print(f"  {'✓' if c else '✗'} {n}{(' — ' + str(d)[:200]) if d and not c else ''}")
    print(f"\n通过 {passed} / 失败 {len(checks) - passed}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
