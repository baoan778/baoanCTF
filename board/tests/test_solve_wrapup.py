#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_solve_wrapup.py — 超时收尾时序 + 收尾词/工具白名单

为什么（2026-11-17）：sdkshard 原来的超时路径是「rc=124 → killpg → 继续下一题」，
被杀的 pi 那一轮想法全丢（实测 rc=124 占已跑题约 17%）。现在先杀进程组、再续同一会话跑
一次 --no-tools 的「收尾轮」，把「已确认事实 + 证伪清单」追加进本轮输出，外层照常抽。

断言：
  A. 收尾词两版都在：WRAPUP_PROMPT（本轮）含「不要再尝试运行任何命令」「FOUND_FLAG=」；
     build_prompt 第 6 条含「接近 deadline 就停手落结论」。
  B. 工具白名单是代码里的策略：wrapup_args 含 --no-tools 与 --fork（★ 2026-11-19 从 --continue
     改为 --fork，且 fork 到**独立 session-dir**）、不出现 --tools；无会话时返回 None。
  C. 时序：solve_one 里 killpg 在 run_wrapup 之前，且只在 rc == 124 时触发。
  D. wrapup_seconds：默认 45 / 0 → 0（关闭）/ 负数 → 0 / 非法 → 45 / 120 → 120。
  E. run_wrapup(secs=0) 直接 disabled，且不创建 out 文件（0 不许被当默认值）。
  F. docs/并行工作.md §4.6b 两版都活着（文档与实现不许漂移）。
"""
import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = Path(os.path.dirname(os.path.dirname(HERE)))
SDK = ROOT / "tools" / "sdkshard.py"
DOCS = ROOT / "docs" / "并行工作.md"

_passed, _failed = 0, 0


def ck(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
    else:
        _failed += 1
        print(f"  ✗ {name}" + (f"   [{detail}]" if detail else ""))


def load():
    spec = importlib.util.spec_from_file_location("sdkshard_under_test", str(SDK))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def with_env(val, fn):
    old = os.environ.get("PI_SOLVE_WRAPUP_SEC")
    try:
        if val is None:
            os.environ.pop("PI_SOLVE_WRAPUP_SEC", None)
        else:
            os.environ["PI_SOLVE_WRAPUP_SEC"] = val
        return fn()
    finally:
        if old is None:
            os.environ.pop("PI_SOLVE_WRAPUP_SEC", None)
        else:
            os.environ["PI_SOLVE_WRAPUP_SEC"] = old


def main():
    if not SDK.exists():
        print("SKIP  tools/sdkshard.py 不在发布包里")
        return 77
    sd = load()
    src = SDK.read_text(encoding="utf-8")

    print("A) 收尾词两版都在")
    p = getattr(sd, "WRAPUP_PROMPT", "")
    ck("A1 收尾轮开场", "【收尾轮" in p, p[:80])
    ck("A2 禁止再跑命令", "不要再尝试运行任何命令" in p, p[:120])
    ck("A3 有 FOUND_FLAG 出口", "FOUND_FLAG=" in p, p[:200])
    ck("A4 要求证伪清单", "证伪" in p, p[:200])
    ck("A5 版本 B 写进 build_prompt", "收尾轮只保结论" in src)
    ck("A6 版本 B 提醒 deadline", "接近 deadline 就停手落结论" in src)
    ck("A7 主 prompt 提到外层收尾轮", "无工具收尾轮" in src)

    print("B) 工具白名单是代码里的策略（2026-11-19 改为 fork 到独立目录）")
    # ★ 必须先造一个真会话文件：wrapup_args 现在按 newest_session 找源，
    #   没有源就返回 None（旧约定是直接 --continue，空目录也照样拼参数）。
    _bd = Path(tempfile.mkdtemp(prefix="wrapup-"))
    _sd = _bd / "sess-x"
    _sd.mkdir()
    (_sd / "2026-01-01T00-00-00-000Z_seed.jsonl").write_text("{}\n", encoding="utf-8")
    args = sd.wrapup_args(_sd, "deepseek-chat")
    ck("B0 有会话时返回参数", isinstance(args, list), repr(args))
    args = args or []
    ck("B1 关工具", "--no-tools" in args, str(args))
    ck("B2 改用 --fork（不再 --continue）", "--fork" in args, str(args))
    ck("B2b 不再用 --continue", "--continue" not in args, str(args))
    ck("B2c fork 的源是那个真会话",
       "--fork" in args and str(args[args.index("--fork") + 1]).endswith("_seed.jsonl"), str(args))
    ck("B3 指定 session 目录且是独立目录",
       "--session-dir" in args and str(args[args.index("--session-dir") + 1]) == str(sd.wrapup_dir(_sd)),
       str(args))
    ck("B3b 独立目录不等于原目录", sd.wrapup_dir(_sd) != _sd, str(sd.wrapup_dir(_sd)))
    ck("B4 不另开白名单", "--tools" not in [a.split("=")[0] for a in args], str(args))
    ck("B5 走收尾词", "-p" in args and args[args.index("-p") + 1] == p)
    ck("B6 无会话时返回 None（旧代码会拼出 --continue 空跑）",
       sd.wrapup_args(_bd / "no-such-dir", "deepseek-chat") is None)
    shutil.rmtree(_bd, ignore_errors=True)

    print("C) 时序：先杀再收尾，且只在 rc=124")
    i_wait = src.find("rc = proc.wait(timeout=budget)")
    i_kill = src.find("os.killpg(os.getpgid(proc.pid), signal.SIGKILL)")
    i_wrap = src.find("run_wrapup(sessdir, outpath")
    ck("C1 三处都在", -1 not in (i_wait, i_kill, i_wrap), f"{i_wait} {i_kill} {i_wrap}")
    ck("C2 killpg 在收尾轮之前", i_kill < i_wrap, f"{i_kill} vs {i_wrap}")
    gate = src.rfind("if rc == 124:", 0, i_wrap)
    ck("C3 收尾轮被 rc==124 包住", gate != -1 and i_wrap - gate < 400, f"gate={gate}")

    print("D) wrapup_seconds 的边界（0 是真的关闭）")
    ck("D1 默认 45", with_env(None, sd.wrapup_seconds) == 45)
    ck("D2 env=0 → 0", with_env("0", sd.wrapup_seconds) == 0)
    ck("D3 负数 → 0", with_env("-5", sd.wrapup_seconds) == 0)
    ck("D4 非法 → 默认 45", with_env("abc", sd.wrapup_seconds) == 45)
    ck("D5 env=120 → 120", with_env("120", sd.wrapup_seconds) == 120)

    print("E) secs=0 不启动也不留痕")
    tmp = Path(tempfile.mkdtemp(prefix="wrapup-"))
    out = tmp / "w.out"
    st = sd.run_wrapup(tmp / "sess", out, 0, "deepseek-chat")
    ck("E1 返回 disabled", st == "disabled", st)
    ck("E2 没动 out 文件", not out.exists())
    ck("E3 0 与默认值确实不同", with_env("0", sd.wrapup_seconds) != with_env(None, sd.wrapup_seconds))

    print("F) 文档与实现不漂移")
    if DOCS.exists():
        d = DOCS.read_text(encoding="utf-8")
        ck("F1 §4.6b 在", "### 4.6b 收尾词两版" in d)
        ck("F2 版本 A 在", "版本 A" in d)
        ck("F3 版本 B 在", "版本 B" in d)
        ck("F4 写了预算变量", "PI_SOLVE_WRAPUP_SEC" in d)
        ck("F5 写了工具白名单", "--no-tools" in d)
    else:
        print("  （跳过 docs 检查：docs/并行工作.md 不在发布包里）")
    print("G) pi 不存在时不许崩（no-pi）")
    tmp2 = Path(tempfile.mkdtemp(prefix="wrapup2-"))
    out2 = tmp2 / "w.out"
    empty = tmp2 / "bin"
    empty.mkdir()
    # ★ 2026-11-19：必须先造会话，否则 wrapup_args 直接返回 None → "no-session"，
    #   测不到「pi 不在 PATH」这条分支（那才是 G 组要验的）。
    _gsd = tmp2 / "sess"
    _gsd.mkdir()
    (_gsd / "2026-01-01T00-00-00-000Z_seed.jsonl").write_text("{}\n", encoding="utf-8")
    oldpath = os.environ.get("PATH", "")
    try:
        os.environ["PATH"] = str(empty)
        st2 = sd.run_wrapup(_gsd, out2, 20, "deepseek-chat")
    finally:
        os.environ["PATH"] = oldpath
    ck("G1 返回 no-pi", st2 == "no-pi", st2)
    ck("G2 落盘了收尾轮标记", out2.exists() and "WRAPUP" in out2.read_text(encoding="utf-8", errors="replace"))


    print("H) 收尾轮事实解析（R3-B：rc=124 的结论不许整段丢）")
    wrap = sd.extract_wrapup_facts
    MARK = "===== WRAPUP（收尾轮·无工具·上限45 s）====="

    def mk(*lines):
        return MARK + "\n" + "\n".join(lines)

    ck("H0 空输入返回空", wrap("") == [])
    ck("H0b 无收尾标记时返回空", wrap("1) 已确认的事实\n- 靶机开着 22 端口，ssh 可连\n") == [])

    h1 = wrap(mk("1) 已确认的事实",
                  "- 靶机是 Flask/Werkzeug 1.0.1，入口 /page",
                  "- admin 口令在 .env 里，值 admin:test",
                  "2) 试过并证伪",
                  "- 头伪造 X-Forwarded-For 无效，响应长度不变"))
    ck("H1 两个段落都抽到", len(h1) == 3, repr(h1))
    ck("H1b 段落 1 的事实在前（配额顺序 1→2→4）",
       h1[:2] == ["靶机是 Flask/Werkzeug 1.0.1，入口 /page", "admin 口令在 .env 里，值 admin:test"], repr(h1))
    ck("H1c 证伪条目带出", any("X-Forwarded-For" in x for x in h1), repr(h1))

    h2 = wrap(mk("3) 如果拿到 flag",
                  "FOUND_FLAG=flag{deadbeef-1111-2222-3333-444455556666}",
                  "4) 还差什么",
                  "还差 admin 口令没拿到，其余都通了"))
    ck("H2 第 3 段整段跳过（flag 由 extract_flags 管）",
       not any("deadbeef" in x for x in h2), repr(h2))
    ck("H2b 第 4 段仍在", any("admin 口令没拿到" in x for x in h2), repr(h2))

    h3 = wrap(mk("1) 已确认的事实",
                  "- 目标 10.0.184.104 上只开了 80 端口，其余全关",
                  "FOUND_FLAG=flag{cafebabe-0000-1111-2222-333344445555}"))
    ck("H3 光杆 FOUND_FLAG 行整条丢掉", not any("cafebabe" in x for x in h3), repr(h3))
    ck("H3b 同段的其他事实保留", len(h3) == 1 and "10.0.184.104" in h3[0], repr(h3))

    h4 = wrap(mk("4) 还差什么",
                  "只需按平台回执提交 flag{44444444-aaaa-bbbb-cccc-dddddddddddd} 确认，不必重跑注入"))
    ck("H4 句子里的 flag 被掩码", any("<flag 已隐去>" in x for x in h4), repr(h4))
    ck("H4b 句子的其余部分保留（真结论不陪葬）", any("不必重跑注入" in x for x in h4), repr(h4))
    ck("H4c 掩码后不再有 flag 形态串", not any("cef39271" in x for x in h4), repr(h4))

    h5 = wrap(mk("1) 已确认的事实",
                  "- 首页返回 200，标题 Dalgona Challenges",
                  "{\"type\":\"message\",\"id\":\"abc\",\"role\":\"assistant\"}",
                  "- 这条在 jsonl 转储之后，必须被截断掉"))
    ck("H5 jsonl 转储处截断", len(h5) == 1 and "Dalgona" in h5[0], repr(h5))
    ck("H5b 截断后的事实不混进来", not any("截断掉" in x for x in h5), repr(h5))

    many = ["- 第%d条确认事实，长度够进判定" % i for i in range(1, 13)]
    h6 = wrap(mk("1) 已确认的事实", *many))
    ck("H6 第 1 段配额 8 条", len(h6) == 8, str(len(h6)))
    ck("H6b 保留的是最前面 8 条", h6[0].startswith("第1条") and h6[-1].startswith("第8条"), repr(h6[:2]))

    long_item = "- " + "很长的结论" * 40
    h7 = wrap(mk("1) 已确认的事实", long_item))
    ck("H7 单条截断到 100 字符", len(h7) == 1 and len(h7[0]) == 100, str([len(x) for x in h7]))

    h8 = wrap(MARK + "\n1) 已确认的事实\n- 第一段收尾里的旧事实，应当被后一段覆盖掉" +
              "\n" + MARK + "\n1) 已确认的事实\n- 第二段收尾里的新事实，这才是要留的")
    ck("H8 多个收尾段取最后一个", len(h8) == 1 and "新事实" in h8[0], repr(h8))

    h9 = wrap(mk("1) 已确认的事实",
                  "- INFRA_BLOCKED: 本机沙箱没有挂载 shell，工具通道不可用",
                  "- Tool bash not found，本机会话起不来",
                  "- 靶场 DB 主机 NXDOMAIN，flag 只可能在不可达的库里"))
    ck("H9 本机故障噪声被滤掉", not any("沙箱没有挂载" in x for x in h9), repr(h9))
    ck("H9b Tool not found 噪声被滤掉", not any("Tool bash not found" in x for x in h9), repr(h9))
    ck("H9c 靶场侧真结论保留（R2-B 的成果不许回退）",
       any("NXDOMAIN" in x for x in h9), repr(h9))

    h10 = wrap(mk("1) 已确认的事实",
                   "- 同一句话出现了两次，只应留一条",
                   "- 同一句话出现了两次，只应留一条"))
    ck("H10 段内去重", len(h10) == 1, repr(h10))

    h11 = wrap(mk("1) 已确认的事实",
                   "[shard] 外层日志行不该进事实",
                   "[godmode] 提示词缺失，已跳过注入"))
    ck("H11 外层日志行被跳过", h11 == [], repr(h11))

    print(f"\n{'OK' if _failed == 0 else 'FAIL'}  {_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
