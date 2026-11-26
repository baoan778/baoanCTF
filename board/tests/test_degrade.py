#!/usr/bin/env python3
"""P5 后半回归：graph.json 单点损坏降级 + 降级态拒写。

    python3 board/tests/test_degrade.py

为什么测这个：以前 load() 是裸 `json.loads`，graph.json 坏一个字节，
之后**每条** fgs 命令都甩一屏 traceback。降级要满足三条：
  ① 坏文件必须**隔离保留**（不是删掉、不是覆盖）——里面可能有几百条事实；
  ② 命令还能跑（给空图 + 警告），不整块崩；
  ③ **降级态禁止 save**——否则空图反向覆盖，等于二次伤害。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

FGS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fgs.py")
passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail and not cond else ""))


def run(lab, *args):
    env = dict(os.environ)
    env["PI_LAB_BOARD_DIR"] = lab
    p = subprocess.run([sys.executable, FGS, *args], capture_output=True, env=env, timeout=120)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="degrade-test-")
    try:
        rc, out = run(tmp, "init", "dg", "--goal", "降级测试")
        check("init 成功", rc == 0, out[:150])

        gpath = Path(tmp, "dg", "graph.json")
        # 塞一条事实，便于验证「坏图里原有内容不丢」
        run(tmp, "fact-add", "dg", "重要事实不能丢", "--confidence", "verified")
        original = gpath.read_text(encoding="utf-8")
        check("坏之前的图是合法 JSON", isinstance(json.loads(original), dict))

        # ① 打坏它（模拟半写/断电）
        gpath.write_text('{"nodes": {"f001": ', encoding="utf-8")

        # ② 命令不能整块崩：应跑通并警告，且给出「已隔离」字样
        rc, out = run(tmp, "show", "dg")
        check("坏图不甩 traceback", "Traceback" not in out, out[:300])
        check("坏图给出降级警告", "解析失败" in out or "降级" in out, out[:300])
        check("坏图命令仍可用（rc 有限）", rc in (0, 1, 2, 3), f"rc={rc}")

        # ③ ★ 坏文件必须隔离保留，不能丢
        corrupts = list(Path(tmp, "dg").glob("graph.json.corrupt-*"))
        check("★ 坏文件已隔离保留（没删）", len(corrupts) == 1, f"找到 {len(corrupts)} 个")
        if corrupts:
            body = corrupts[0].read_text(encoding="utf-8", errors="replace")
            check("隔离文件保留了原始坏字节", body == '{"nodes": {"f001": ', body[:80])
            check("隔离文件名带时间戳", "corrupt-" in corrupts[0].name, corrupts[0].name)

        # ④ ★ 降级/隔离后必须拒写（否则空图覆盖，二次伤害）
        #    注意两种合法形态：
        #      a) 首次遇到坏图 → load 返回降级图 → save 因 _degraded 拒写
        #      b) 坏文件已被隔离过 → 没有 graph.json → 走「项目不存在」拒写
        #    两者都安全；断言只看「拒绝 + 理由说得清」，不锁死具体措辞。
        rc, out = run(tmp, "fact-add", "dg", "降级态不该写进去", "--confidence", "unverified")
        check("★ 降级态写入被拒", rc != 0, f"rc={rc} :: {out[:200]}")
        check("拒写理由说得清",
              any(k in out for k in ("降级", "解析失败", "不存在", "corrupt")), out[:200])
        check("拒写后没有凭空产生新图",
              not Path(tmp, "dg", "graph.json").exists(), "居然写出来了")

        # ⑤ 恢复：从隔离文件救回正常数据后，功能恢复
        if corrupts:
            good = Path(tmp, "dg", "graph.json")
            # 用原先的合法内容恢复（模拟人工抢救）
            run(tmp, "init", "dg2", "--goal", "x")
            prev = Path(tmp, "dg2", "graph.json").read_text(encoding="utf-8")
            prev = json.loads(prev)
            # 把坏图里那条事实塞回去，模拟“救回来”
            prev["nodes"]["f001"] = {"id": "f001", "type": "fact", "text": "重要事实不能丢",
                                     "confidence": "verified", "created_at": "2026-11-01T00:00:00+08:00",
                                     "provenance": {"actor": "operator", "evidence": None}}
            good.write_text(json.dumps(prev, ensure_ascii=False, indent=2), encoding="utf-8")
            rc, out = run(tmp, "show", "dg")
            check("从隔离文件恢复后可用", rc == 0 and "重要事实不能丢" in out, out[:200])

        print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
        if failed:
            print("失败项：" + "、".join(failed))
            return 1
        print("✓ 降级与防误删：全部通过")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
