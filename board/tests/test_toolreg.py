#!/usr/bin/env python3
"""toolreg 回归：工具清单的**自登记 + 自检测**（运行期那半张表）。

跑法：
    python3 board/tests/test_toolreg.py

为什么要有这个文件
------------------
来源：ZeroA 的 `tool-catalog` 扩展（GPL-3.0，只借思路）。它的问题陈述在 pi-lab 同样成立：
  > 「做题过程中会现装新库/新工具，装完不登记，下次启动又变成『本机没找到』。」

`tools/TOOLS.md` 是**人读的唯一事实来源**，但它是散文；本工具补机器可读的那半张表。
判据（这里逐条钉住，防止以后改坏）：
  * 查找顺序：记录路径 → PATH（含 .exe）→ Python import → Node 模块
  * **记录路径失效要落回 PATH**（重装/移动后不能一直信旧路径）
  * `check` 有缺失 → 退出码 1（脚本/门禁可用）；清单坏了 → 降级到种子清单，**只读路径不动它**，
    写回路径（`--record`/`add`/`record`）**先备份成 `.corrupt-<时间戳>` 再写**（不许静默销毁）
  * `check` 默认**不执行任何东西**；`--version` 才跑，且只认白名单参数
    （清单与工具都是不可信输入，见 AGENTS §三.1）

测试**不碰真产物**：`PI_LAB_TOOL_REGISTRY` 指向临时文件。
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

LAB = Path(__file__).resolve().parents[2]
TOOLREG = LAB / "tools" / "toolreg.py"

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail and not cond else ""))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="pilab-toolreg-"))
    reg = tmp / "tool-registry.json"
    env = dict(os.environ, PI_LAB_TOOL_REGISTRY=str(reg), PI_LAB_ROOT=str(LAB))

    def tr(*args, ok=None, extra_env=None):
        e = dict(env, **(extra_env or {}))
        p = subprocess.run([sys.executable, str(TOOLREG), *args],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=e, timeout=120)
        if ok is not None:
            check(f"`toolreg {' '.join(args[:2])}` 退出码在 {ok}", p.returncode in ok,
                  f"rc={p.returncode} {(p.stdout + p.stderr)[-200:]}")
        return p.returncode, p.stdout, p.stderr

    try:
        print("1) 种子清单可用：list 有内容、check 能跑完、--json 可解析")
        rc, out, err = tr("list")
        check("list 列出种子条目", "fgs" in out and "kb" in out, out[:200])
        rc, out, err = tr("check", "--json")
        j = json.loads(out)
        check("check --json 可解析且 total>0", j.get("total", 0) > 10, out[:200])
        check("check 给了清单路径", "tool-registry.json" in (out + err), (out + err)[-200:])

        print("2) add：登记 → 检测 → 缺失时退出码 1")
        tr("add", "no-such-tool-xyz", "--commands", "no-such-tool-xyz-nope",
           "--direction", "web", "--note", "测试用假工具")
        rc, out, err = tr("check", "--json")
        j = json.loads(out)
        ids = [t["id"] for t in j["tools"]]
        check("新条目出现在清单里", "no-such-tool-xyz" in ids, str(ids[-5:]))
        check("假工具被判不可用", "no-such-tool-xyz" in j["missing"], str(j["missing"]))
        check("有缺失时 check 退出码 1", rc == 1, f"rc={rc}")
        rc, out, err = tr("missing", "--json")
        miss = json.loads(out)
        check("missing 只列不可用且带原因",
              all(m["detail"] for m in miss) and any(m["id"] == "no-such-tool-xyz" for m in miss),
              out[:200])

        print("3) 最小线索要求 + 重复登记要 --force")
        rc, out, err = tr("add", "no-clue-at-all")
        check("没有任何查找线索 → 退出码 2", rc == 2, f"rc={rc}")
        rc, out, err = tr("add", "no-such-tool-xyz", "--commands", "x")
        check("重复登记且没 --force → 退出码 2", rc == 2, f"rc={rc}")

        print("4) record：实测到路径就写回；写错的路径要给警告")
        fake = tmp / "fakebin" / "faketool"
        fake.parent.mkdir(parents=True, exist_ok=True)
        fake.write_text("#!/bin/sh\necho fake\n", encoding="utf-8")
        fake.chmod(0o755)
        tr("record", "no-such-tool-xyz", str(fake))
        rc, out, err = tr("check", "--json")
        j = json.loads(out)
        row = next(t for t in j["tools"] if t["id"] == "no-such-tool-xyz")
        check("记录路径后判可用", row["available"] is True, str(row))
        check("来源标成 recorded", row["source"] == "recorded", str(row))
        rc, out, err = tr("record", "no-such-tool-xyz", str(tmp / "不存在的路径"))
        check("记录一个不存在的路径要当场警告（不静默）", "现在就不存在" in out + err, (out + err)[-200:])
        tr("record", "no-such-tool-xyz", str(fake))     # 复原

        print("5) 记录路径失效 → 落回 PATH 再找（不能一直信旧路径）")
        # ★ POSIX-only：本用例造的是 `#!/bin/sh` 可执行脚本，靠 chmod 0o755 拿到 exec 位，
        #   并用 ":" 拼 PATH。Windows 没有 POSIX shell、没有 exec 位、PATH 分隔符是 ";"，
        #   这套假设不成立 → 显式跳过（跳过 ≠ 通过），不伪装成路径解析失败。
        if os.name != "posix":
            print("  SKIP 用例 5/6（本平台非 POSIX：无 #!/bin/sh、无 exec 位、PATH 用 ';'）")
        else:
            tr("add", "onpath-tool", "--commands", "onpath-tool", "--note", "PATH 里的假工具")
            pathbin = tmp / "pathbin"
            pathbin.mkdir(parents=True, exist_ok=True)
            (pathbin / "onpath-tool").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
            (pathbin / "onpath-tool").chmod(0o755)
            # 先记一个**不存在**的路径，再把真身放进 PATH —— 必须靠 PATH 找到
            data = json.loads(reg.read_text(encoding="utf-8"))
            data["tools"]["onpath-tool"]["path"] = str(tmp / "gone" / "onpath-tool")
            reg.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            rc, out, err = tr("check", "--json", extra_env={"PATH": f"{pathbin}:{env['PATH']}"})
            j = json.loads(out)
            row = next(t for t in j["tools"] if t["id"] == "onpath-tool")
            check("失效记录路径 → 回落到 PATH", row["available"] and row["source"] == "PATH", str(row))

            print("6) check --record 把新发现的路径写回清单（下次不用再找）")
            rc, out, err = tr("check", "--record", extra_env={"PATH": f"{pathbin}:{env['PATH']}"})
            data = json.loads(reg.read_text(encoding="utf-8"))
            check("写回了实测路径", str(pathbin / "onpath-tool") in json.dumps(data),
                  json.dumps(data["tools"].get("onpath-tool"))[:300])
            check("回报里说明写了哪几条", "写回" in out, out[-300:])

        print("7) 清单坏了：读路径不碰它；**写路径先备份**（不许静默销毁）")
        bad = tmp / "bad-registry.json"
        bad.write_text("{ 这不是 JSON", encoding="utf-8")
        rc, out, err = tr("check", "--json", extra_env={"PI_LAB_TOOL_REGISTRY": str(bad)})
        check("坏清单不崩（仍给 JSON 输出）", out.strip().startswith("{"), out[:200])
        check("坏清单会明说降级了", "降级" in out + err or "读不了" in out + err, (out + err)[:200])
        check("只读路径不动坏文件（md5 不变）",
              bad.read_text(encoding="utf-8") == "{ 这不是 JSON")
        # ★ 反例（由独立复核 ctf-verify 找出，2026-10-12）：check --record 会覆盖坏文件，
        #   与提示里「不会覆盖」矛盾 —— 静默销毁坏数据违反本仓铁律。修法：写前先备份。
        rc, out, err = tr("check", "--record", extra_env={"PI_LAB_TOOL_REGISTRY": str(bad)})
        baks = sorted(tmp.glob("bad-registry.json.corrupt-*"))
        check("写回前把坏文件备份下来（不静默销毁）", bool(baks), str([p.name for p in tmp.iterdir()]))
        check("备份内容 = 原始坏内容（可恢复）",
              bool(baks) and baks[0].read_text(encoding="utf-8") == "{ 这不是 JSON")
        check("备份动作有明确提示（不靠人猜）", "已备份" in out + err, (out + err)[-200:])

        print("8) 默认不执行任何东西（版本探测必须显式开）")
        src = TOOLREG.read_text(encoding="utf-8")
        check("源码里有版本白名单常量", "VERSION_ARGS" in src)
        check("默认 check 不会带 --version 语义（闸门是显式选项）",
              '"--version", action="store_true"' in src, "没找到 --version 开关")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ 工具清单自登记（toolreg）：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
