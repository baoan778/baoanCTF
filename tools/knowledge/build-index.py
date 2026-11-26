#!/usr/bin/env python3
"""生成 tools/knowledge/INDEX.md —— 离线知识基线索引。

数据源：knowledge/<来源>/recommended-skills/<skill>/{SKILL.md,references/,assets/,scripts/}
用途：回答两个问题 —— 「这块料在哪」「什么时候该读它」。

改了搬运范围就重跑本脚本，别手改 INDEX.md。
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# skill -> (一句话用途, 对应 pi-lab 的 skill)
CATALOG = {
    # ── 侦察 ──
    "recon-fingerprint": ("指纹识别：响应头/框架/中间件/版本", "src-recon"),
    "recon-port-scan": ("端口与服务发现（naabu/masscan/nmap 编排）", "src-recon"),
    "recon-subdomain": ("子域枚举与资产扩展", "src-recon"),
    "recon-dir-scan": ("目录/文件爆破（ffuf/feroxbuster/dirsearch + 字典）", "src-recon"),
    "recon-crawl": ("爬虫取链接/表单/JS 接口（katana/gau/urlfinder）", "src-recon"),
    # ── 利用（Web）──
    "exploit-sqli": ("SQL 注入：回显/布尔/时间盲注、DBMS 差异、sqlmap", "src-recon / ctf-web"),
    "exploit-xss": ("XSS：反射/存储/DOM，绕过与编码", "ctf-web"),
    "exploit-ssrf": ("SSRF：内网探测、协议利用、云元数据", "src-recon / ctf-web"),
    "exploit-lfi": ("文件包含/路径穿越读文件", "ctf-web"),
    "exploit-file-download": ("任意文件下载与日志/配置泄露", "src-recon"),
    "exploit-file-upload": ("上传绕过：后缀/魔术字节/解析器差异", "src-recon"),
    "exploit-authz": ("越权：IDOR / 水平与垂直越权", "src-recon"),
    "exploit-access-bypass": ("访问控制绕过（ghost bits、路径归一化差异）", "src-recon"),
    "exploit-csrf": ("CSRF：token 缺失/可预测/同站绕过", "src-recon"),
    "exploit-cache": ("Web 缓存欺骗与缓存投毒", "src-recon"),
    "exploit-host-header": ("Host 头注入：密码重置/路由欺骗", "src-recon"),
    "exploit-smuggling": ("HTTP 请求走私（CL.TE/TE.CL/TE.TE）", "src-recon"),
    "exploit-proto-pollution": ("原型链污染（Node/JS 服务端）", "src-recon"),
    "exploit-race": ("条件竞争 / 并发绕过", "src-recon"),
    "exploit-logic": ("业务逻辑缺陷", "src-recon"),
    "exploit-oauth": ("OAuth / OIDC 流程缺陷", "src-recon"),
    "exploit-java": ("Java 生态：反序列化、Fastjson/Log4j、内存马", "src-recon / ctf-reverse"),
    "exploit-dev-exposure": ("开发/调试暴露面：actuator、swagger、.git", "src-recon"),
    "exploit-info-leak": ("信息泄露汇总面", "src-recon"),
    "exploit-waf": ("WAF 识别与绕过算子", "src-recon"),
    "exploit-websocket": ("WebSocket 测试面", "src-recon"),
    "api-testing": ("API/GraphQL 测试面", "src-recon"),
    "security-fuzzing": ("模糊测试payload（命令注入/特殊字符/数据库）", "src-recon"),
    # ── 资料库（payload / 字典 / 规律）──
    "security-payloads": ("payload 集：路径穿越、文件名、Flash 等", "src-recon / ctf-misc"),
    "security-patterns": ("匹配规律：php 魔术哈希、正则绕过等", "ctf-web / ctf-misc"),
    "security-passwords": ("口令字典：NCSC 10 万 / probable-v2 / darkweb2017", "src-recon（配合 hydration）"),
    "security-usernames": ("用户名字典", "src-recon"),
    "security-webshells": ("webshell 样本（检测/理解用）", "应急响应（webshell 排查）"),
    # ── 其它 ──
    "llm-testing": ("LLM 测试：偏见/数据泄露/记忆回放/发散", "ctf-ai-ml"),
    "results-storage": ("结果落盘约定", "—— （pi-lab 用 work/<pid>/ + 黑板）"),
    "pentest-report": ("报告模板与写法", "wp-writer（可借结构）"),
}

SKIP = {"SKILL.md", "README.md"}


def walk_files(d: Path):
    if not d.is_dir():
        return []
    return sorted([p for p in d.rglob("*") if p.is_file() and p.name not in SKIP])


def describe(p: Path) -> str:
    try:
        n = sum(1 for _ in p.open("rb"))
        size = p.stat().st_size
        unit = f"{n}行" if size < 400_000 and p.suffix in (".md", ".txt", ".py", ".sh", ".php", ".jsp", ".asp") else f"{size // 1024}KB"
    except Exception:
        unit = "?"
    return f"{p.name}({unit})"


def main():
    out = ["# 离线知识基线 · INDEX", ""]
    out += [
        "> **这是什么**：从参考项目搬进来的**方法论 / payload / 字典 / 脚本**，全在本机盘上，无外网也能读。",
        "> 设计对应 CyberPenda ADR 0037（把离线知识基线烤进镜像）。",
        ">",
        "> **怎么用**：卡住 → 先按 `docs/writeups/手法库.md` → 再查这里对应的 `references/` → 最后才靠模型记忆。",
        "> 应急响应方向另有成套 SOP：`skills/ir-response/`（那一层是可执行流程，这里只是资料本体）。",
        "> **别整读**：单文件 >3000 token 先 `head -50` / `grep` 定位，再读那一段。",
        "",
    ]
    for src in sorted(p for p in ROOT.iterdir() if p.is_dir()):
        prov = src / "PROVENANCE.md"
        out += [f"## 来源：{src.name}", ""]
        if prov.exists():
            for line in prov.read_text(encoding="utf-8").splitlines():
                if line.startswith(("- **上游**", "- **许可**", "- **注意**")):
                    out.append("  " + line)
            out.append("")
        base = src / "recommended-skills"
        if not base.is_dir():
            # 松散结构的来源（例：noptrace —— 整本手册直接放目录里）
            loose = [f for f in sorted(src.glob("*.md")) if f.name != "PROVENANCE.md"]
            for f in loose:
                n = sum(1 for _ in f.open("rb"))
                heads = sum(1 for l in f.open(encoding="utf-8", errors="ignore") if l.startswith("#"))
                out.append(f"### `{f.name}` — {heads} 个标题 / {n} 行")
                out.append("- **别整读**：先 `grep -n '^#' <文件>` 看目录，再定位到那一段")
                out.append("")
            continue
        for sk in sorted(p for p in base.iterdir() if p.is_dir()):
            purpose, ours = CATALOG.get(sk.name, ("（未登记）", "——"))
            sm = sk / "SKILL.md"
            lines = len(sm.read_text(encoding="utf-8", errors="ignore").splitlines()) if sm.exists() else 0
            out.append(f"### `{sk.name}` — {purpose}")
            out.append(f"- SKILL.md {lines} 行　→ 对应 pi-lab：`{ours}`")
            for sub in ("references", "assets", "scripts"):
                fs = walk_files(sk / sub)
                if fs:
                    out.append(f"- {sub}/（{len(fs)}）：" + "、".join(describe(f) for f in fs[:12])
                               + ("…" if len(fs) > 12 else ""))
            out.append("")
    (ROOT / "INDEX.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"✓ 写出 {ROOT / 'INDEX.md'}（{len(out)} 行）")


if __name__ == "__main__":
    main()
