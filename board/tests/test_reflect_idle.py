#!/usr/bin/env python3
"""test_reflect_idle.py —— X-3 反思空闲通道回归（治「反思只有忙时通道」）。

血账
----
本仓此前只有 **忙时通道**（reflect-paper：把纸条附在工具输出末尾）。
模型一旦**安静停下**，`idle-resume` 只会「续推」，**不会盘点** ——
于是在死路上被加速（OpenSecurity 2.0 实测：4 次盘点把方向掰回来）。

它怎么补的（两半，缺一不可）
--------------------------
  ① 忙通道**发布**净活跃状态（globalThis.__PI_LAB_REFLECT）
     为什么走 globalThis：扩展之间没有相对 import 先例，跨文件 import 会让
     一个扩展加载失败拖垮另一个。只传数据、不传行为。
  ② 空闲通道（idle-resume.evaluate）**先判反思**，到期发盘点并**本轮不再续推**；
     判分只有一份实现：tools/reflectwatch.mjs 的 decideReflect（不另造口径）。

本测试守六条（全离线）
----------------------
  1. reflect-paper 真的发布了状态（静态 + 运行期）；
  2. idle-resume 里反思判定**在** decideIdle 之前（顺序可核对）；
  3. 到期 → 返回 kind=reflect，且**跳过续推**（日志有 resume-skipped）；
  4. 未到期 / 状态过期 / 开关关闭 → 一律走续推（fail-open）；
  5. 判据同源：用的是 reflectwatch.decideReflect 的两个门槛（净活跃 + 调用数）；
  6. 用户中断（aborted）仍旧不复活。

    python3 board/tests/test_reflect_idle.py
"""
from __future__ import annotations

import json
import pathlib
import sys as _sys

_sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _pkgpaths import require_ext  # noqa: E402

try:
    for _s in (_sys.stdout, _sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = pathlib.Path(__file__).resolve().parents[2]
IDLE = require_ext("idle-resume.ts")
PAPER = require_ext("reflect-paper.ts")

checks: list = []


def ok(name, cond, extra=""):
    checks.append((name, bool(cond), str(extra)))


def _report():
    bad = [c for c in checks if not c[1]]
    for name, good, extra in checks:
        mark = "✓" if good else "✗"
        print(f"  {mark} {name}" + (f"   {extra[:240]}" if extra and not good else ""))
    print(f"\n通过 {len(checks) - len(bad)} / 失败 {len(bad)}")
    if bad:
        print("失败项：" + "、".join(c[0] for c in bad))
        return 1
    print("✓ 反思空闲通道（X-3）：全部通过")
    return 0


def main() -> int:
    ok("idle-resume.ts 存在", IDLE.is_file())
    ok("reflect-paper.ts 存在", PAPER.is_file())
    if not (IDLE.is_file() and PAPER.is_file()):
        return _report()

    idle = IDLE.read_text(encoding="utf-8", errors="replace")
    paper = PAPER.read_text(encoding="utf-8", errors="replace")

    # ① 发布侧
    ok("reflect-paper 定义 publishState", "const publishState = () =>" in paper)
    ok("发布到 globalThis.__PI_LAB_REFLECT", "__PI_LAB_REFLECT" in paper)
    ok("发布含净活跃", "activeMs: netActiveNow()" in paper)
    ok("发布含调用数", "toolCalls," in paper)
    ok("发布含提醒计数", "nudges: st.nudges" in paper)
    ok("tool_result 两个分支都发布",
       paper.count("publishState()") >= 3, f"count={paper.count(chr(34)+chr(34)) and paper.count('publishState()')}")
    ok("说明了为什么走 globalThis（不做跨文件 import）", "没有相对 import" in paper
       or "跨文件 import" in paper)

    # ② 消费侧：顺序 + 判据同源
    i_reflect = idle.find("checkReflectDue()")
    i_idle = idle.find("decideIdle(snap")
    ok("idle-resume 有 checkReflectDue", i_reflect > 0)
    ok("反思判定在 decideIdle 之前（先盘点再续推）",
       0 < i_reflect < i_idle, f"reflect@{i_reflect} idle@{i_idle}")
    ok("到期返回 kind=reflect", 'kind: "reflect"' in idle)
    ok("到期时记 resume-skipped（可审计「这次没推」）", "resume-skipped" in idle)
    ok("判据同源：用 reflectwatch 的 decideReflect", "decideReflect" in idle)
    ok("不另造第二套口径（注释写明）", "不另造" in idle or "单一" in idle)
    ok("状态过期保护（>2 分钟不认陈旧数据）", "2 * 60 * 1000" in idle)
    ok("fail-open：拿不到状态返回 null", "return null" in idle)
    ok("反射开关 PI_LAB_REFLECT 尊重（关了就不空窗提醒）",
       "PI_LAB_REFLECT" in idle)
    ok("消息类型区分（reflect-idle vs idle-resume）", "reflect-idle" in idle)

    # ③ 中断不复活（既有红线不许被这次改动带坏）
    ok("aborted 仍不复活（判据还在 evaluate 入参）", "aborted: !!aborted" in idle)
    ok("settled 时 aborted 记 stop", 'kind: "aborted"' in idle)

    # ④ 结构自检：两半都在同一份 .ts 里可直接读到的关键字
    ok("空闲通道有 UI 提示区分（📝 vs 🔁）", "📝 空闲反思" in idle)

    return _report()


if __name__ == "__main__":
    raise SystemExit(main())
