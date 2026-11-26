#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_sdkshard_judge.py —— R2 三项判据的回归测试（A 402 熔断 / B fact 噪声 / C 候选过滤）。

为什么单独一个文件（而不是拆三个）
----------------------------------
三项都落在 tools/sdkshard.py，共享同一个 importlib 加载成本；拆开会让全量回归多跑两遍
加载。但**断言分组严格分开**（A/B/C 各自成段），任一段红都能一眼定位到是哪项改造回退。

血账（2026-11-20 R2 复盘，证据见 历史复盘 R2-01-复盘报告.md）
----------------------------------------------------------------
· A：pi 打印供应商错误时**不带 marker**，形如
    402: {"message":"Insufficient Balance (request_id: …)","type":"insufficient_balance_error",…}
  只认 8 个 marker 的 _api_error_text 会整行丢掉 → quota_action 恒 none →
  唯一的成本熔断闸门在真实报错格式下是哑的（xben-030 第 3 轮真欠费被当普通失败重跑）。
· B：_FACT_NOISE_RE 里含 INFRA_BLOCKED，把「本机坏了」和「靶场不可解」混成一个判据 →
  xben-081 的「DB 永久 NXDOMAIN、其余路线已证伪」这条真结论被滤掉，第 5 轮重烧同一条死路。
· C：候选只做形状校验，不校验来源 → 081 把 sha256("XBEN-081-24")（公开 writeup 参考值）
  当 deterministic backup 提交被判错；025 同一值的大小写变体被重复提交。

    python3 board/tests/test_sdkshard_judge.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import pathlib
import sys
import tempfile

try:
    for _s in (sys.stdout, sys.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = pathlib.Path(__file__).resolve().parents[2]
MOD = LAB / "tools" / "sdkshard.py"
RUNNER = LAB / "tools" / "run_sdk_solver.py"

checks: list = []


def ok(name, cond, extra=""):
    checks.append((name, bool(cond), str(extra)))


def _report():
    bad = [c for c in checks if not c[1]]
    for name, good, extra in checks:
        print(f"  {'OK ' if good else 'ERR'} {name}" + (f"   {extra[:300]}" if extra and not good else ""))
    print("\n通过 %d / 失败 %d" % (len(checks) - len(bad), len(bad)))
    if bad:
        print("失败项：" + "、".join(c[0] for c in bad))
        return 1
    print("OK 判据回归（sdkshard A/B/C）：全部通过")
    return 0


def _load():
    spec = importlib.util.spec_from_file_location("sdkshard_judge", MOD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def main() -> int:
    S = _load()
    REAL_402 = ('402: {"message":"Insufficient Balance (request_id: '
                'ab5bb15e-8c10-43c4-a6b1-a0be7f811b58)","type":"insufficient_balance_error",'
                '"param":null,"code":"invalid_request_error"}')

    # ---------------- A) 402 熔断判据 ----------------
    ok("A1 真实 pi 402 行 -> exhausted", S.quota_action(REAL_402) == "exhausted",
       S.quota_action(REAL_402))
    ok("A2 裸 429 行 -> throttled（不是 exhausted）",
       S.quota_action('429: {"message":"rate limit exceeded"}') == "throttled")
    ok("A3 裸 401 行 -> auth", S.quota_action('401: {"message":"Invalid API key"}') == "auth")
    prose = "题目描述：当订单返回 HTTP 402 Payment Required 时用户需充值，flag 在错误页。"
    ok("A4 题面正文（无冒号JSON结构）-> none", S.quota_action(prose) == "none")
    ok("A5 旧式 Error code: 402 仍 exhausted（R1 不回归）",
       S.quota_action('Error code: 402 - {"error":{"message":"Insufficient Balance"}}') == "exhausted")
    ok("A6 裸 402 但 JSON 没有 message 键 -> none", S.quota_action('402: {"hint":"pay"}') == "none")
    ok("A7 裸 402 + 中文文本（不是 JSON）-> none", S.quota_action("402: 支付失败") == "none")
    ok("A8 带 HTTP 前缀的裸行也能熔断",
       S.quota_action('HTTP 402: {"message":"Insufficient Balance"}') == "exhausted")
    ok("A9 500 行但无额度措辞 -> none", S.quota_action('500: {"message":"internal error"}') == "none")
    ok("A10 过滤结果仍有界（<=4096）",
       len(S._api_error_text("\n".join(['402: {"message":"Insufficient Balance"}' * 50] * 100))) <= 4096)
    ok("A11 _is_provider_error_line 认旧 marker", S._is_provider_error_line("Error code: 429 - rate limit"))
    ok("A12 _is_provider_error_line 认裸状态码行", S._is_provider_error_line(REAL_402))
    ok("A13 _is_provider_error_line 不认普通叙述", not S._is_provider_error_line("HTTP 402 是支付页标题"))

    # ---------------- B) fact 噪声判据 ----------------
    real_facts = [
        "INFRA_BLOCKED: DB 主机 NXDOMAIN，flag 只可能在不可达的库里",
        "INFRA_BLOCKED: 10.0.184.104 的 3306 全关，Host 头伪造无效，已证伪 7 条路线",
        "GET /login 返回 200，标题 Login - Trading",
        "secret=abc123456 出现在 /app.py",
        "/admin/dashboard 存在，回应 403",
    ]
    for s in real_facts:
        ok("B 真结论保留: " + s[:26], S.is_fact_noise(s) is False)
    noise = [
        "bash: Tool bash not found",
        "连续 3 次调用 bash 均返回 Tool bash not found",
        "工具通道不可用",
        "没有挂载 bash",
        "无法执行新的探测",
        "本轮 shell 执行工具不可用",
        "sandbox 没有挂载",
        "tool  BASH  not  found",
        "INFRA_BLOCKED",
        "INFRA_BLOCKED: 本机沙箱没有挂载 shell，无法探测",
        "INFRA BLOCKED：工具通道不可用",
    ]
    for s in noise:
        ok("B 噪声仍滤: " + s[:26], S.is_fact_noise(s) is True)

    # save_facts / load_facts 端到端：真结论写进去能读回来，噪声写不进去
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td)
        S.save_facts(d, "xben-081-24", [real_facts[0], "Tool bash not found"], ["10.0.0.1:80"])
        txt = S.load_facts(d)
        ok("B11 真结论能注入下一轮", "NXDOMAIN" in txt, txt[:200])
        ok("B12 噪声不进注入", "Tool bash not found" not in txt, txt[:200])
        ok("B13 落盘时噪声已被 extract_facts 侧滤过（save 原样写，load 兜底）",
           "noise-test" not in txt)

    # ---------------- B2) flag 值绝不能进 facts.md（2026-11-23 活体实测修复）----------------
    # 事故：xben-001 第 1 轮拿到真 flag A 并提交成功，A 被 extract_facts 当"事实"写进
    #   facts.md；第 2 轮注入提示词后 agent 直接抄 A 去提交 —— 而靶场实例已换 IP，
    #   真 flag 变成 B，于是**抄旧值必错**，还被账本记成 rejected。
    # 这是"错误结论"类缺陷：不是解不出，是**用上一代的值污染这一代的判断**。
    FLAG_A = "flag{11111111-aaaa-bbbb-cccc-dddddddddddd}"
    ok("B20 整行是 FOUND_FLAG= 不再当事实",
       S.extract_facts("FOUND_FLAG=" + FLAG_A) == [],
       S.extract_facts("FOUND_FLAG=" + FLAG_A))
    ok("B21 句子里夹带 flag 也不再当事实",
       S.extract_facts("取证：/order/300998/receipt 返回 " + FLAG_A) == [])
    ok("B22 真事实仍然抽得到（不许误杀）",
       len(S.extract_facts("GET /admin/login.php 返回 200")) == 1)
    with tempfile.TemporaryDirectory() as td2:
        d2 = pathlib.Path(td2)
        S.save_facts(d2, "xben-001-24", ["- 取证：/order/300998/receipt 返回 " + FLAG_A], [])
        saved = (d2 / "facts.md").read_text(encoding="utf-8")
        ok("B23 写盘时 flag 值被掩码", "a876cb40" not in saved, saved[:120])
        ok("B24 但结论（路径）必须保留", "/order/300998/receipt" in saved, saved[:120])
        # 读侧兜底：本次修复之前写的旧 facts.md 里已经有 flag 值
        (d2 / "facts.md").write_text("- 老结论：/x 返回 " + FLAG_A + "\n- 另一条事实", encoding="utf-8")
        back = S.load_facts(d2)
        ok("B25 旧文件里的 flag 值在注入前被掩码", "a876cb40" not in back, back[:160])
        ok("B26 旧文件的结论仍保留", "/x 返回" in back, back[:160])

    # ---------------- B3) 外部来源的 flag 不许提交（2026-11-23 实测）----------------
    # 事故：xben-066 从公开 writeup 网页上抄到 FLAG{ee06d2ea…} 并提交，平台判错。
    # 原有 _is_reference_hash 只拦「本题号的 sha256」，拦不住「别人某次运行的 flag」。
    # 判据只能是**来源**，不能是形状（真 flag 完全可能是 64 位 hex）。
    import json as _json
    _wr = {"type": "message", "message": {"role": "toolResult", "toolName": "web_search",
           "content": [{"type": "text", "text": "report.json 捕获的 flag FLAG{ee06d2ea2f8c84a61fe68cd1219e4915de9}"}]}}
    _ext1 = S.external_flag_values(_json.dumps(_wr, ensure_ascii=False))
    ok("B30 网页里的 flag 被认作外部值", len(_ext1) == 1, _ext1)
    _tb = {"type": "message", "message": {"role": "toolResult", "toolName": "bash",
           "content": [{"type": "text", "text": "<code>flag{realvalue123}</code>"}]}}
    ok("B31 **靶场自己返回的 flag 不算外部**（不许误杀真值）",
       len(S.external_flag_values(_json.dumps(_tb, ensure_ascii=False))) == 0)
    ok("B32 kb / poc 也属外部来源",
       len(S.external_flag_values(_json.dumps(
           {"type": "message", "message": {"role": "toolResult", "toolName": "kb",
            "content": [{"type": "text", "text": "writeup: flag{deadbeef9999}"}]}},
           ensure_ascii=False))) == 1)
    _src = open(S.__file__, encoding="utf-8").read()
    ok("B33 提交侧确实调用了它（静态）", "external_flag_values(out)" in _src)

    # ---------------- C) 候选过滤 ----------------
    H81 = _sha("XBEN-081-24")
    ok("C0 基线：sha256('XBEN-081-24') 与线上值一致",
       H81 == "a68e6443c894f67f53c4db8f932273d21468c7acd52ee8e8a78357e88f3bccc5", H81)
    ok("C1 题号哈希被丢", S.extract_flags("FLAG{" + H81 + "}", "xben-081-24") == [])
    ok("C2 大写哈希也被丢", S.extract_flags("FLAG{" + H81.upper() + "}", "xben-081-24") == [])
    ok("C3 小写题号变体哈希也被丢",
       S.extract_flags("FLAG{" + _sha("xben-081-24") + "}", "xben-081-24") == [])
    ok("C4 带换行的题号变体哈希也被丢",
       S.extract_flags("FLAG{" + _sha("XBEN-081-24\n") + "}", "xben-081-24") == [])
    ok("C5 不给题号时不判哈希（老调用点不回归）",
       S.extract_flags("FLAG{" + H81 + "}") == ["FLAG{" + H81 + "}"])
    uuid_val = "flag{22222222-aaaa-bbbb-cccc-dddddddddddd}"
    ok("C6 真 UUID flag 不误杀", S.extract_flags(uuid_val, "xben-056-24") == [uuid_val])
    ok("C7 其它 64 位 hex 不误杀（可能是真 flag）",
       S.extract_flags("flag{" + "f" * 64 + "}", "xben-081-24") == ["flag{" + "f" * 64 + "}"])
    # ★ 2026-11-24：C8/C9 测的是「**大小写变体**去重，且留先出现的那个」，
    #   所以这两行必须只差大小写。脱敏时把它们换成同一个 REDACTED 占位，
    #   C8 仍过（都是同一个值）但 C9 的红—— 因为断言写的是原值前缀。
    #   现在用一对只差大小写的占位值，断言也跟着指向它，语义不变。
    dup = ("flag{E32D5E7D-aaaa-bbbb-cccc-dddddddddddd} "
           "flag{e32d5e7d-aaaa-bbbb-cccc-dddddddddddd}")
    got = S.extract_flags(dup, "xben-025-24")
    ok("C8 大小写变体只留一个", len(got) == 1, got)
    ok("C9 留的是先出现的那个", got and got[0].startswith("flag{E32D5E7D"), got)
    # ★ 2026-11-24：这两个值必须**内容不同**（C10 测的就是「不同值都保留」）。
    #   脱敏时曾把第二个换成 REDACTED 占位，正好与别处占位撞成同一个值 → C10 假红。
    #   所以这里用两个**不同的**占位值（都是 aaaa0000 段，一眼能看出是测试数据）。
    two = ("flag{aaaa1111-2222-3333-4444-555566667777} "
           "flag{aaaa0000-2222-3333-4444-555566667777}")
    ok("C10 内容不同的两个值都保留", len(S.extract_flags(two, "xben-025-24")) == 2)
    ok("C11 去重不因大小写把不同值合并",
       len(S.extract_flags("flag{AbCd1111} flag{XyZw2222}", "t")) == 2,
       S.extract_flags("flag{AbCd1111} flag{XyZw2222}", "t"))

    # ---------------- 静态接线 ----------------
    src = MOD.read_text(encoding="utf-8")
    ok("S1 有 _is_provider_error_line", "def _is_provider_error_line" in src)
    ok("S2 _api_error_text 走新判据",
       "def _api_error_text" in src and "if _is_provider_error_line(ln)" in src)
    # 只取正则字面量本身（_FACT_NOISE_RE = re.compile( ... ) 那一段），
    # 不能取到后面的注释块 —— 注释里必然提到 INFRA_BLOCKED（那是在解释为什么删掉它）。
    import re as _re
    _m = _re.search(r"_FACT_NOISE_RE = re\.compile\((?:[^()]|\([^()]*\))*\)", src)
    ok("S3 _FACT_NOISE_RE 正则字面量里不再含 INFRA_BLOCKED",
       bool(_m) and "INFRA_BLOCKED" not in _m.group(0), (_m.group(0)[:200] if _m else "未找到"))
    ok("S4 提交链路传了题号", "extract_flags(out, code)" in src)
    ok("S5 hashlib 已导入", "import hashlib" in src)
    if RUNNER.is_file():
        rsrc = RUNNER.read_text(encoding="utf-8")
        ok("S6 run_sdk_solver 也传题号", "extract_flags(out, ch.unique_code)" in rsrc, "未接线")
    else:
        ok("S6 run_sdk_solver 存在", False, str(RUNNER))

    return _report()


if __name__ == "__main__":
    sys.exit(main())
