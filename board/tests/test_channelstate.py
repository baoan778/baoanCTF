#!/usr/bin/env python3
"""回归测试：T6 通道状态七类分类（2026-11-07）

跑法：
    python3 board/tests/test_channelstate.py

为什么要有它
------------
修 T6 之前，"网断了"和"路走错了"在图上**是同一件事**：都只是带「不可达」字样的 fact。
后果是网络抖动被算进 dry_streak → 一条其实能走的攻击路线被标死。
本文件守三件事：

  C1 七类各自能被认出（每类 2 正：「英文报错」+「中文报错」）
  C2 七类各自**不该**被误认（每类 2 反：长相近但语义不同的串）
  C3 两道守卫真的有效：
     · 描述规则 ≠ 报告事件（「判别式：超时即认为不可达」→ ok）
     · 猜测不算事件（「可能不可达」→ ok）
  C4 网络层 vs 认证层的判定边界：auth/denied 必须 **不算** unreachable
     —— 这是整套判据的意义所在（目标活着 vs 目标死了）

用法约定（与其余测试一致）：退出码 0=通过，1=失败。
本测试是纯函数测试，不需要 PI_LAB_BOARD_DIR 隔离（不写任何文件）。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
FGS = HERE.parent / "fgs.py"
passed, failed = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        passed.append(name)
    else:
        failed.append(name)
        print(f"  ✗ {name}  {detail[:400]}")


def load_mod():
    spec = importlib.util.spec_from_file_location("_fgs_under_test", FGS)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main() -> int:
    m = load_mod()
    cs, iu = m.channel_state, m.is_unreachable

    # ── C1：每类 2 正（英文 + 中文）─────────────────────────────────────
    POSITIVES = {
        "session_lost": ["session lost after 300s", "会话失效，请重新登录"],
        "denied_userlist": ["530 permission denied", "用户 ftpuser 不在白名单"],
        "auth_fail": ["401 Unauthorized", "认证失败：密码错误"],
        "refused": ["Connection refused by 10.0.0.5", "连接被拒，端口 3306"],
        "timeout": ["curl: (28) timed out", "操作超时，目标不存活"],
        "reset": ["Connection reset by peer", "连接被重置"],
        "conn_broken": ["connection broken: unexpected eof", "连接被关闭"],
    }
    for want, samples in POSITIVES.items():
        for s in samples:
            check(f"C1 {want} ← {s[:34]}", cs(s) == want, f"得到 {cs(s)!r}")

    # ── C2：每类 2 反（不该被误认）─────────────────────────────────────
    # 反例必须挑「同一个大领域、但语义不同」的串，否则测试没有信息量。
    NEGATIVES = {
        "session_lost": ["session started ok", "会话已建立"],
        "denied_userlist": ["user admin logged in", "用户已登录"],
        "auth_fail": ["403 bytes transferred", "认证成功"],
        "refused": ["I refused to guess", "拒绝执行危险命令"],
        "timeout": ["timeoutMs=1500 配置项", "超时阈值设为 30s"],  # 后一条含「阈值」→ 被守卫拦
        "reset": ["reset the password", "重置密码为 admin"],
        "conn_broken": ["eof marker in file", "关闭连接后重试成功"],
    }
    for notwant, samples in NEGATIVES.items():
        for s in samples:
            got = cs(s)
            check(f"C2 不误判 {notwant} ← {s[:30]}", got != notwant, f"被误判成 {got!r}")

    # ── C3：两道守卫 ───────────────────────────────────────────────────
    check("C3 描述规则不算事件（判别式）",
          cs("判别式：curl 超时即认为不可达") == "ok",
          f"得到 {cs('判别式：curl 超时即认为不可达')!r}")
    check("C3 描述规则不算事件（阈值）",
          cs("判定标准：connection refused 视为端口关闭") == "ok")
    check("C3 猜测不算事件（可能）",
          cs("目标可能不可达，待确认") == "ok",
          f"得到 {cs('目标可能不可达，待确认')!r}")
    check("C3 猜测不算事件（maybe）",
          cs("maybe timed out") == "ok")
    check("C3 空输入返回 ok", cs("") == "ok" and cs(None) == "ok")
    check("C3 正常输出返回 ok",
          cs("200 OK，拿到 3 条记录") == "ok",
          f"得到 {cs('200 OK，拿到 3 条记录')!r}")

    # ── C4：网络层 vs 认证层（本判据的意义所在）────────────────────────
    UNREACH = ["session_lost", "refused", "timeout", "reset", "conn_broken"]
    REACHABLE = ["auth_fail", "denied_userlist"]
    for st in UNREACH:
        s = POSITIVES[st][0]
        check(f"C4 {st} 算网络层不可达", iu(s) is True, f"is_unreachable={iu(s)}")
    for st in REACHABLE:
        s = POSITIVES[st][0]
        check(f"C4 {st} **不算**不可达（目标活着）", iu(s) is False, f"is_unreachable={iu(s)}")
    check("C4 ok 不算不可达", iu("200 OK") is False)
    check("C4 空串不算不可达", iu("") is False)

    # ── C5：与既有 UNREACHABLE_RX 不冲突（不替换它的口径）──────────────
    check("C5 模块仍导出 UNREACHABLE_RX", hasattr(m, "UNREACHABLE_RX"))
    check("C5 CHANNEL_LABEL 覆盖全部状态 + ok",
          set(m.CHANNEL_LABEL) == {"ok"} | set(POSITIVES),
          str(sorted(set(m.CHANNEL_LABEL))))

    total = len(passed) + len(failed)
    print(f"\n通过 {len(passed)} / 失败 {len(failed)}（共 {total} 条）")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ 通道状态七类分类（T6）：全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
