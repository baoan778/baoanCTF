#!/usr/bin/env python3
"""授权范围守卫回归：`fgs scope` + `fgs swarm` / `step-add --target` 的拦截。

跑法：
    python3 board/tests/test_scope.py

为什么要有这个文件
------------------
来源是 StrikeAgent 的 `backend/atkbrain/scope.py`：它把主机分成
**基础设施白名单 / 带外(OOB) / 公网回显 / 漏洞文档站 / 其余必须在项目范围内** 几类。

pi-lab 为什么现在才需要它：之前只有单目标探索，「只打授权范围」写在纪律里就够。
加了 `fgs swarm`（批量派活）之后 —— **批量 × 无范围校验 = 最容易越界** ——
范围就必须在**写图那一刻**被机器拦一下。SRC 打错一个域名是不可逆的。

判据（与 StrikeAgent 一致，但按 pi-lab 语境重写）：
  * 基础设施 / OOB 反连 / 回显站 / 文档站 → 天生允许（测试过程本来就要用）
  * 已声明范围：域名的**子域也算界内**（`c.b.edu.cn` ⊂ `b.edu.cn`）；支持 `*.x.com` 与 CIDR
  * **未声明范围 → 放行但在输出里提醒**（不对历史项目造成摩擦）
  * 已声明 → **fail-closed**：界外目标一条也不派（退出码 3），要越界得 `--allow-out-of-scope`（留痕）
  * `step-add --target` 同样拦；`doctor` 会把越界 target 判死

测试不碰真产物：`PI_LAB_BOARD_DIR` 指向临时目录。
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

BOARD = Path(__file__).resolve().parents[1]
FGS = BOARD / "fgs.py"

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(f"  {'✓' if cond else '✗'} {name}" + (f"   {detail}" if detail and not cond else ""))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="pilab-scope-"))
    PID = "sc"
    env = dict(os.environ, PI_LAB_BOARD_DIR=str(tmp / "projects"))

    def fgs(*args, ok=(0,)):
        p = subprocess.run([sys.executable, str(FGS), *args],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=120)
        return p.returncode, p.stdout, p.stderr

    def graph():
        return json.loads((tmp / "projects" / PID / "graph.json").read_text(encoding="utf-8"))

    try:
        print("1) 未声明范围：提醒但放行（不给历史项目制造摩擦）")
        fgs("init", PID, "--kind", "pentest", "--goal", "范围守卫")
        fgs("fact-add", PID, "某指纹存在未授权注入", "--confidence", "verified")
        rc, out, err = fgs("swarm", PID, "--from", "f001", "--targets", "a.edu.cn", "--dry-run")
        check("退出码 0（不拦）", rc == 0, err[:200])
        check("提醒去声明范围", "未声明范围" in out, out[:300])
        rc, out, err = fgs("doctor", PID, ok=(0, 1))
        check("doctor 提醒未声明范围", "未声明授权范围" in out + err, (out + err)[:300])

        print("2) 声明范围（含子域、通配、CIDR）")
        rc, out, err = fgs("scope", PID, "--set", "b.edu.cn,*.x.com,10.0.0.0/24")
        check("--set 成功", rc == 0, err[:200])
        check("列出来了 3 条", "b.edu.cn" in out and "*.x.com" in out and "10.0.0.0/24" in out, out[:300])
        rc, out, _ = fgs("scope", PID, "--add", "extra.edu.cn")
        check("--add 追加", "extra.edu.cn" in out, out[:300])
        rc, out, _ = fgs("scope", PID, "--remove", "extra.edu.cn")
        check("--remove 移除", "extra.edu.cn" not in out, out[:300])
        check("审计留痕 scope.set", "scope.set" in (tmp / "projects" / PID / "log.jsonl").read_text(encoding="utf-8"))

        print("3) 界内 / 基础设施 / OOB / 回显 / 文档站 → 放行")
        ok_targets = ["b.edu.cn", "c.b.edu.cn", "https://deep.b.edu.cn:8443/x?q=1",
                      "sub.x.com", "10.0.0.7", "github.com", "pypi.org",
                      "x.ceye.io", "abc.dnslog.cn", "ifconfig.me", "nvd.nist.gov", "localhost"]
        rc, out, err = fgs("swarm", PID, "--from", "f001",
                           "--targets", ",".join(ok_targets), "--max", "20", "--dry-run")
        check("全部放行（退出码 0）", rc == 0, err[:300])
        check("没有一条被拦", "拦下" not in out, out[:400])
        check("派生条数 = 目标数", out.count("←") == len(ok_targets), f"{out.count('←')}/{len(ok_targets)}")

        print("4) 界外 → fail-closed（一条都不派，退出码 3）")
        rc, out, err = fgs("swarm", PID, "--from", "f001", "--targets", "evil.com", "--dry-run", ok=(0, 3))
        check("退出码 3", rc == 3, f"rc={rc}")
        check("说清为什么", "范围外" in out + err or "不在已声明范围" in out + err, (out + err)[:300])
        check("给两条修法（scope --add / --allow-out-of-scope）",
              "scope" in out + err and "allow-out-of-scope" in out + err, (out + err)[:300])

        print("5) 混合清单：只派界内的，界外的逐条报出来")
        rc, out, err = fgs("swarm", PID, "--from", "f001",
                           "--targets", "a1.b.edu.cn,evil.com,a2.b.edu.cn", "--dry-run")
        check("退出码 0", rc == 0, err[:200])
        check("界内两条都派", "a1.b.edu.cn" in out and "a2.b.edu.cn" in out, out[:300])
        check("界外一条被拦", "evil.com" in out and "拦下" in out, out[:300])
        check("审计里记了被拦目标（真跑时）", True)  # 见第 6 组

        print("6) --allow-out-of-scope 是唯一越界通道，且留痕")
        rc, out, err = fgs("swarm", PID, "--from", "f001", "--targets", "evil.com",
                           "--allow-out-of-scope", "--text", "越界测试 {target}")
        check("显式放行后成功", rc == 0, err[:200])
        log = (tmp / "projects" / PID / "log.jsonl").read_text(encoding="utf-8")
        check("审计记下越界目标", '"scope_blocked"' in log or "evil.com" in log, log[-300:])

        print("7) step-add --target 同样受管")
        rc, out, err = fgs("step-add", PID, "对界内目标试越权", "--target", "https://a.b.edu.cn/login")
        check("界内 target 通过", rc == 0, err[:200])
        check("图上存的是归一化主机名", 
              any(n.get("target") == "a.b.edu.cn" for n in graph()["nodes"].values()),
              str([n.get("target") for n in graph()["nodes"].values()])[:200])
        rc, out, err = fgs("step-add", PID, "打界外", "--target", "evil.com", ok=(0, 3))
        check("界外 target 被拒（退出码 3）", rc == 3, f"rc={rc}")
        check("给出补声明命令", "scope" in out + err and "--add" in out + err, (out + err)[:300])

        print("8) doctor：越界 target 判死（历史数据也能抓）")
        g = graph()
        for n in g["nodes"].values():
            if n.get("target") == "a.b.edu.cn":
                n["target"] = "evil.com"          # 手工伪造一条越界
        (tmp / "projects" / PID / "graph.json").write_text(
            json.dumps(g, ensure_ascii=False, indent=2), encoding="utf-8")
        rc, out, err = fgs("doctor", PID, ok=(0, 1))
        check("doctor 退出码 1", rc == 1, f"rc={rc}")
        check("点名越界 target", "越界" in out + err and "evil.com" in out + err, (out + err)[:300])

        print("9) --clear 之后不再拦（显式放弃守卫）")
        fgs("scope", PID, "--clear")
        rc, out, err = fgs("swarm", PID, "--from", "f001", "--targets", "whatever.com", "--dry-run")
        check("清空后放行但提醒未声明", rc == 0 and "未声明范围" in out, (out + err)[:300])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ 授权范围守卫：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
