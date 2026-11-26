#!/usr/bin/env python3
"""P5 回归：fact 的 doc 字段（写入/读取/覆盖/隔离/brief 标记）。

    python3 board/tests/test_doc.py

为什么单独一条：doc 的意义是「把长证据挪出高频路径」——
brief 只报**有无 doc + 字数**，正文靠 `fgs doc <id>` 按需下钻。
所以两个方向都得测：① 存得进、读得出；② **不准**泄漏进 brief（否则等于没挪）。
"""
import os
import shutil
import subprocess
import sys
import tempfile

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
    env["PI_LAB_BOARD_DIR"] = lab          # 隔离真产物（子进程传 env 即可，无需 shell）
    p = subprocess.run([sys.executable, FGS, *args], capture_output=True,
                       env=env, timeout=120)
    return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="doc-test-")
    try:
        rc, out = run(tmp, "init", "dt", "--goal", "验证 doc 字段")
        check("init 成功", rc == 0, out[:150])

        # 1) 写入：fact-add 带 --doc
        rc, out = run(tmp, "fact-add", "dt", "8080 存在未授权接口",
                      "--confidence", "verified", "--doc", "复现：curl -s /api/users 返回 200")
        check("fact-add --doc 成功", rc == 0, out[:150])

        # 2) 读取：fgs doc 能拿到正文
        rc, out = run(tmp, "doc", "dt", "f001")
        check("fgs doc 能读出正文", rc == 0 and "curl -s /api/users" in out, out[:200])
        check("fgs doc 也给出处", "actor=" in out, out[:200])

        # 3) 覆盖：doc-add 换掉旧正文
        rc, out = run(tmp, "doc-add", "dt", "f001", "--doc", "（新）证据在 log/x.out:12")
        check("doc-add 覆盖成功", rc == 0, out[:150])
        rc, out = run(tmp, "doc", "dt", "f001")
        check("覆盖后读到新正文", "log/x.out:12" in out, out[:200])
        check("覆盖后旧正文不再出现", "curl -s /api/users" not in out, out[:200])

        # 4) 边界：不存在的节点必须报错（不能静默成功）
        rc, _ = run(tmp, "doc", "dt", "f999")
        check("查不存在节点非 0 退出", rc != 0, f"rc={rc}")

        # 5) 空 doc 不许写入（防拿空串骗人以为写过了）
        rc, _ = run(tmp, "doc-add", "dt", "f001", "--doc", "   ")
        check("doc-add 空正文被拒", rc != 0, f"rc={rc}")

        # 6) ★ 反向核心：doc 正文**不准**泄漏进 brief
        run(tmp, "goal", "dt2", "拿下接口") if False else None
        rc, out = run(tmp, "init", "dt2", "--goal", "验证 brief 标记")
        run(tmp, "goal", "dt2", "拿下 8080 接口")
        run(tmp, "fact-add", "dt2", "8080 有未授权列表接口",
            "--confidence", "verified", "--doc", "秘密细节ZZZ42条记录")
        run(tmp, "step-add", "dt2", "枚举 8080 接口面", "--from", "f001")
        rc, out = run(tmp, "brief", "dt2", "--step", "s001")
        check("brief 跑通", rc == 0, out[:200])
        check("brief 标出有 doc", "doc(" in out and "fgs doc f001" in out, out[:300])
        check("★ doc 正文未泄漏进 brief", "ZZZ42条记录" not in out, out[:300])

        print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
        if failed:
            print("失败项：" + "、".join(failed))
            return 1
        print("✓ doc 字段：全部通过")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
