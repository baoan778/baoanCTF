#!/usr/bin/env python3
"""SoL-Pi vendored 副本的回归：树完整 + 本地补丁在位 + 开关默认值 + 两份副本不漂移。

跑法：
    python3 board/tests/test_solpi_vendor.py

为什么要有这个文件
------------------
vendored 第三方代码有两种**静默**腐烂方式，都不会报错：

1. **本地补丁丢了**。SoL-Pi 上游是照 pi 0.85.1 写的，而本机（和 PiCTF 买家包）跑的是
   pi **1.1.0**：1.1.0 起 bash 工具**非零退出不再抛异常**，改成 resolve 成
   `{ content, isError: true }`（pi `dist/core/tools/bash.js`：`if (exitCode !== 0) return {...isError:true}`）。
   上游只用 `try/catch` 判失败 → 「改完文件顺手跑验证」这条链会把**失败的验证报成
   `[then_run:succeeded]`**（假绿）。补丁 `pictf-1` 补的就是这个判断（4 行）。
   它一旦被上游覆盖丢掉，功能照跑、日志照打，只是**开始撒谎** —— 所以既有断言，也有造病。

2. **两份副本漂移**。同一份 vendor 同时存在两处（本仓 `agent-template/extensions/sol-pi/`
   与 PiCTF 买家包 `engine/agent-pack/extensions/sol-pi/`），改了一边忘了另一边时，
   两边"看起来都对"，出问题却无从对比 —— 这条断言直接 diff。

测试**只读**，不写任何真产物（缺哪一侧就跳过对应项，不误报）。
"""
import difflib
import json
import os
import sys
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


def safe_isdir(p: Path) -> bool:
    """权限不足/坏链接一律当「不存在」——测试不该因为探不到别人的 HOME 就崩。"""
    try:
        return p.is_dir()
    except OSError:
        return False

REPO = Path(__file__).resolve().parents[2]           # <repo>

# ★ 2026-10-30 发布包适配：同一份 vendored 树在两处布局里位置不同
#    本仓/发布包的实际位置：<repo>/agent-template/extensions/sol-pi + <repo>/agent-template/sol-pi.json
#    另一处候选 <repo>/.pi/config/... 是 2026-11-18 复核确认**从未存在过**的旧约定
#    （`git log --all -- .pi/config` 为空，`git ls-files .pi` 为 0）——保留它只是
#    为了让老布局万一出现时仍能识别，不要再往那个目录放东西。
_VENDOR_CANDIDATES = (
    REPO / ".pi" / "config" / "extensions" / "sol-pi",
    REPO / "agent-template" / "extensions" / "sol-pi",
)
_CONF_CANDIDATES = (
    REPO / ".pi" / "config" / "sol-pi.json",
    REPO / "agent-template" / "sol-pi.json",
)
VENDOR = next((p for p in _VENDOR_CANDIDATES if p.is_dir()), _VENDOR_CANDIDATES[0])
CONF = next((p for p in _CONF_CANDIDATES if p.is_file()), _CONF_CANDIDATES[0])
# ★ 2026-10-30 发布包实测：非 root 用户 stat("/root/pi-lab-agent") 会抛
#   PermissionError（不是返回 False）→ 整条测试 ERROR 退出。改成「探不到就当不存在」。
LIVE = Path(os.environ.get("PI_CODING_AGENT_DIR") or "/root/pi-lab-agent")
PICTF = Path("/mnt/c/Users/幺零零/Desktop/PiCTF/engine/agent-pack/extensions/sol-pi")

TOTAL_TS = 24                    # 上游 src/sol-pi 下的 .ts 文件数（06b3ee3）
GUARD = "bashResult as { isError?: boolean }).isError"
UPSTREAM_COMMIT = "06b3ee358ddc5cebc5fb21a8e7493fab2db508ce"

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail and not cond else ""))


def has_guard(text: str) -> bool:
    return GUARD in text


def main() -> int:
    print("== 1. vendored 树完整（目录形态：<name>/index.ts，pi 才认）==")
    check("扩展目录存在", VENDOR.is_dir(), str(VENDOR))
    if not VENDOR.is_dir():
        print("\n✗ 没有 vendored 树，后面无法继续")
        return 1
    n_ts = len(list(VENDOR.rglob("*.ts")))
    check(f"源文件 {TOTAL_TS} 个 `.ts` 齐", n_ts == TOTAL_TS, f"实际 {n_ts}")
    check("入口 index.ts 在（目录形态的前提）", (VENDOR / "index.ts").is_file())
    entry = (VENDOR / "index.ts").read_text(encoding="utf-8", errors="replace")
    check("入口有 default 导出（否则 pi 不认这个扩展）", "export default" in entry)
    for f in ("LICENSE", "PATCHES.md", "SOL-PI-VENDOR.txt"):
        check(f"溯源文件 {f} 在", (VENDOR / f).is_file())

    print("== 2. 本地补丁 pictf-1（then_run 按 isError 判成败）==")
    then_run = VENDOR / "extensions" / "action-fusion" / "then-run.ts"
    text = then_run.read_text(encoding="utf-8", errors="replace") if then_run.is_file() else ""
    check("then-run.ts 在", bool(text))
    check("补丁守卫在位（否则失败会被报成 [then_run:succeeded]＝假绿）", has_guard(text))
    check("同文件仍保留 succeeded/failed 两个标记（补丁没改歪语义）",
          "THEN_RUN_SUCCEEDED" in text and "THEN_RUN_FAILED" in text)
    # 造病：把守卫换成恒假条件 —— 同一条断言必须变红（判据自证，不是自己说自己对）
    sick = text.replace("if ((bashResult as { isError?: boolean }).isError)", "if (false)")
    check("造病生效：删掉守卫后断言确实变红", text != sick and not has_guard(sick))

    print("== 3. 开关默认值（只开两个纯本地机制）==")
    check("sol-pi.json 在", CONF.is_file(), str(CONF))
    if CONF.is_file():
        try:
            c = json.loads(CONF.read_text(encoding="utf-8"))
        except Exception as e:                                    # noqa: BLE001
            check("sol-pi.json 是合法 JSON", False, str(e))
            c = {}
        check("actionFusion 开（纯本地：省一轮模型往返）", c.get("actionFusion") is True)
        check("observationPack 开（纯本地：大输出存本地 + 句柄）", c.get("observationPack") is True)
        check("evidencePreservingReducer 关（会把日志发给另一个模型）",
              c.get("evidencePreservingReducer") is False)
        check("onlineContextCompact 关（会 abort 当前回合并自动续跑）",
              c.get("onlineContextCompact") is False)

    print("== 4. 溯源标记与记录一致（防「换了 vendor 忘改记录」）==")
    marker = (VENDOR / "SOL-PI-VENDOR.txt").read_text(encoding="utf-8", errors="replace")
    check("vendor 标记里写着上游 commit", UPSTREAM_COMMIT in marker, marker[:120])
    patches = (VENDOR / "PATCHES.md").read_text(encoding="utf-8", errors="replace")
    check("PATCHES.md 记了同一条补丁 id", "pictf-1" in patches)
    check("PATCHES.md 写了「为什么」（pi 1.1.0 非零退出不再抛）",
          "isError" in patches and "1.1.0" in patches)

    print("== 5. 运⾏侧已同步（缺则跳过——发布包/exe 里没有 live 目录）==")
    live_ext = LIVE / "extensions" / "sol-pi"
    if safe_isdir(LIVE):
        check("live extensions/sol-pi/ 已就位", live_ext.is_dir(), str(live_ext))
        check("live index.ts 与仓库副本逐字节一致",
              live_ext.is_dir() and (live_ext / "index.ts").is_file()
              and (live_ext / "index.ts").read_bytes() == (VENDOR / "index.ts").read_bytes())
        check("live sol-pi.json 已就位", (LIVE / "sol-pi.json").is_file())
    else:
        print(f"  （跳过：探不到 live agent 目录 {LIVE}）")

    print("== 6. 两份 vendored 副本不漂移（PiCTF 侧可见时才比）==")
    if safe_isdir(PICTF):
        a = sorted(p.relative_to(PICTF).as_posix() for p in PICTF.rglob("*") if p.is_file())
        b = sorted(p.relative_to(VENDOR).as_posix() for p in VENDOR.rglob("*") if p.is_file())
        check("文件清单一致", a == b, f"PiCTF {len(a)} 个 / 仓库 {len(b)} 个")
        drift = []
        for rel in a:
            if rel not in b:
                continue
            if (PICTF / rel).read_bytes() != (VENDOR / rel).read_bytes():
                drift.append(rel)
        check("同名文件逐字节一致", not drift, "漂移：" + "、".join(drift[:5]))
        if drift:
            for rel in drift[:1]:
                d = list(difflib.unified_diff(
                    (VENDOR / rel).read_text(encoding="utf-8", errors="replace").splitlines(),
                    (PICTF / rel).read_text(encoding="utf-8", errors="replace").splitlines(),
                    "repo/" + rel, "pictf/" + rel, lineterm=""))
                print("    " + "\n    ".join(d[:20]))
    else:
        print("  （跳过：这台机器上没有 PiCTF 树）")

    print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ SoL-Pi vendored 副本：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
