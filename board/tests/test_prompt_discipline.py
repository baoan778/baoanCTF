#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# #1/#2/#3 提示词纪律的回归：子代理「单意图 + 写回」、planner 七条硬规则、身份与不变量进系统提示。
# 这三条都是**提示词层**约束，最容易在改文案时被悄悄删掉：删了不会报错，
# 只会让子代理重新开始发散、让编排者重新开始原地打转。所以源码级断言是唯一的护栏。
import importlib.util
import io
import os
import re
import sys
import tempfile

# ★ 2026-11-21：原来硬编码 'D:/baoan' —— 容器里（/opt/pi-ctf）跑必 FileNotFoundError，
#   全量回归因此长期红一条。与 test_context_budget.py / test_skill_firstscreen.py 同一修法：
#   从 __file__ 推，宿主与容器都对。
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fails = []
total = 0


def eq(name, got, want):
    global fails, total
    total += 1
    ok = got == want
    print(('ok  ' if ok else 'NO  ') + name + ' got=' + repr(got))
    if not ok:
        fails.append(name)


def has(name, text, sub):
    eq(name, sub in text, True)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sd = load('sdkshard_t', os.path.join(ROOT, 'tools', 'sdkshard.py'))
src = io.open(os.path.join(ROOT, 'tools', 'sdkshard.py'), encoding='utf-8').read()
fgs = io.open(os.path.join(ROOT, 'board', 'fgs.py'), encoding='utf-8').read()
doc = io.open(os.path.join(ROOT, 'docs', '并行工作.md'), encoding='utf-8').read()

# A #3：身份与不变量在系统提示里，题面留在 user 首条
S = getattr(sd, 'SOLVER_SYSTEM', '')
has('A1 有 SOLVER_SYSTEM', str(bool(S)), 'True')
has('A2 标题是「不变量」', S, '## 不变量')
has('A3 flag 逐字', S, '逐字')
has('A4 不许猜/编', S, '不许猜')
has('A5 不自己提交', S, '不自己提交')
has('A6 单题单线', S, '一次只做这一道题、这一条线')
has('A7 探了不写回=没做', S, '探了不写回 = 没做')
has('A8 INFRA_BLOCKED 探针', S, 'INFRA_BLOCKED')
has('A9 本机拒绝≠靶机防御', S, '本机拒绝 ≠ 靶机防御')
has('A10 FOUND_FLAG 即刻打印', S, 'FOUND_FLAG=')
has('A11 有 write_solver_system', src, 'def write_solver_system(sessdir, scratch=None) -> Path:')
has('A12 真跑 --append-system-prompt', src, "[\"--append-system-prompt\", str(sysfile)]")
i_sys = src.find('sysfile = write_solver_system(sessdir, _scratch)')
i_arg = src.find('[\"--append-system-prompt\", str(sysfile)]')
eq('A13 先写文件再拼参数', i_sys > 0 and 0 < i_sys < i_arg, True)
p = sd.build_prompt('demo', ['1.2.3.4:80'], 1, 120)
eq('A14 题面 prompt 不再重复提交纪律', '不要自己 tsec submit' in p, False)
has('A15 题面指向系统提示', p, '见系统提示')
has('A16 题面仍给侦察命令', p, 'fast-recon.sh')
tmp = tempfile.mkdtemp(prefix='t3-sys-')
sp = sd.write_solver_system(tmp)
eq('A17 写到 <sessdir>/system.md', str(sp).replace('\\', '/').endswith('/system.md'), True)
eq('A18 文件内容就是 SOLVER_SYSTEM', io.open(sp, encoding='utf-8').read(), S)
sp2 = sd.write_solver_system(tmp)
eq('A19 重复调用幂等（同一路径）', str(sp2), str(sp))

# B #1：硬条款 0e 单意图
has('B1 TASK_CLAUSES 有 0e 单意图', fgs, '0e.【单意图】')
has('B2 探了不写回 = 没做', fgs, '探了不写回 = 没做')
has('B3 不要自己再派活', fgs, '不要自己再派活')
has('B4 指向 fact-add 写回', fgs, 'fgs fact-add` 写回')

# C #2：planner 七条硬规则表 + 诚实边界
has('C1 有 4.6e 节', doc, '### 4.6e planner 七条硬规则')
for mark in ['①', '②', '③', '④', '⑤', '⑥', '⑦']:
    has('C2 规则行 ' + mark, doc, '| ' + mark + ' |')
has('C3 点名 fgs verify', doc, 'fgs verify')
has('C4 点名 fgs gate', doc, 'fgs gate')
has('C5 诚实边界段', doc, '诚实边界')
has('C6 只有 ①②⑤ 有真门禁', doc, '只有 ①②⑤ 有真门禁')
has('C7 点名 SAME_METHOD', doc, 'SAME_METHOD')
has('C8 点名 MULTI_METHOD', doc, 'MULTI_METHOD')
has('C9 四种失败写清', doc, '原地打转 / 过早收敛 / 假完成 / 静默停摆')

print('')
print(str(total) + ' 组断言')
if fails:
    print('FAILED ' + str(len(fails)) + ': ' + ', '.join(fails))
    sys.exit(1)
print('ALL PASSED')
