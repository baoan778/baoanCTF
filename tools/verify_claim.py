#!/usr/bin/env python3
"""verify_claim.py —— 把「结论分级」变成机器可查的东西。

为什么要有它
-----------
2026-11-13 一轮审计里我报了 9 个真缺陷、6 次误报（误报率 40%）。
复盘后发现两组根因，其中**「从单次观察跳到根因」代价最高**——
我曾宣布「根因锁定为 import fgs」，做了对照实验后当场被证伪。

光写纪律（skills/audit-discipline）不够：纪律靠自觉，而我会忘。
这个工具把「结论必须有证据等级」变成**交付前跑一条命令**：
没给证据的结论、说「确认」却没对照组的，直接拒。

用法
----
    verify_claim.py --claim "测试隔离缺失" --level confirmed --evidence "test_a.py:12"
    verify_claim.py --claim "疑似断链" --level suspected
    verify_claim.py --list        # 看当前会话已登记的结论

级别
----
  confirmed  有对照实验 / 稳定复现 / 逐字读到原文   → 才准说「已确认」「根因锁定」
  likely     有直接证据但没做对照                   → 只能说「证据指向」
  suspected  只有单次观察                           → 只能说「疑似，待验证」

退出码：0 合规；1 不合规（缺证据却说 confirmed）；2 参数错。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

LEVELS = ('confirmed', 'likely', 'suspected')
STATE = pathlib.Path(os.environ.get('PI_LAB_CLAIMS') or
                     (pathlib.Path.home() / '.pi-ctf' / 'claims.jsonl'))


def record(claim: str, level: str, evidence: str, refuted_by: str = '') -> int:
    problems = []
    if level not in LEVELS:
        problems.append(f'未知级别 {level!r}，只能是 {LEVELS}')
    # 核心闸门：confirmed 必须有证据；有证据还必须有「会不会是巧合」的判断
    if level == 'confirmed' and not evidence.strip():
        problems.append('confirmed 级别必须给 --evidence（哪一行/哪次实验）')
    if level == 'confirmed' and not refuted_by.strip():
        problems.append('confirmed 级别必须给 --refuted-by（如果这是巧合会是什么样，你怎么排除的）')
    if problems:
        print('✗ 结论不合规：', file=sys.stderr)
        for p in problems:
            print(f'  - {p}', file=sys.stderr)
        print('\n  这是硬闸门：说「根因锁定」前必须能回答「如果这是误报会是什么样」。',
              file=sys.stderr)
        print('  修法：降级为 --level likely/suspected，或补齐上面两项。', file=sys.stderr)
        return 1

    STATE.parent.mkdir(parents=True, exist_ok=True)
    rec = {'ts': time.strftime('%Y-%m-%dT%H:%M:%S'), 'claim': claim,
           'level': level, 'evidence': evidence, 'refuted_by': refuted_by}
    with open(STATE, 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + '\n')

    label = {'confirmed': '已确认', 'likely': '证据指向', 'suspected': '疑似'}[level]
    print(f'✓ 已登记 [{label}] {claim}')
    print(f'  交付措辞请用：「{label}」')
    print(f'  台账：{STATE}')
    return 0


def list_all() -> int:
    if not STATE.exists():
        print(f'（还没有登记过结论）{STATE}')
        return 0
    for line in STATE.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        label = {'confirmed': '已确认', 'likely': '证据指向', 'suspected': '疑似'}.get(r['level'], r['level'])
        print(f"  {r['ts']}  [{label}]  {r['claim']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description='结论分级闸门：防「单次观察当根因」')
    ap.add_argument('--claim', help='结论一句话')
    ap.add_argument('--level', choices=LEVELS, help='证据等级')
    ap.add_argument('--evidence', default='', help='证据：文件:行号 / 实验命令与结果')
    ap.add_argument('--refuted-by', dest='refuted_by', default='',
                    help='如果这是巧合会是什么样，你怎么排除的（confirmed 必填）')
    ap.add_argument('--list', action='store_true', help='列出已登记结论')
    a = ap.parse_args()
    if a.list:
        return list_all()
    if not a.claim or not a.level:
        ap.error('要么 --list，要么同时给 --claim 和 --level')
    return record(a.claim, a.level, a.evidence, a.refuted_by)


if __name__ == '__main__':
    raise SystemExit(main())
