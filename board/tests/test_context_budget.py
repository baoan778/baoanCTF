#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 上下文预算五条的回归：①按字节估 token ③按块累加 ④不拆工具调用对（契约）⑤用量只认真值。
# 这份测试的重点是**诚实性**：没记录就必须报「未知（未记费）」，不能显示 0；
# 估算只能用于告警，不能冒充计费 —— 这两条被悄悄改掉时，输出看起来仍然「正常」。
import io
import json
import os
import re
import subprocess
import sys
import tempfile

# ★ 2026-11-19：原来硬编码 'D:/baoan'，在容器里跑（/opt/pi-ctf）必然 FileNotFoundError。
# 改成按本文件位置推导仓库根，宿主与容器两边都能跑。
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FGS = os.path.join(ROOT, 'board', 'fgs.py')
fails = []
total = 0


def eq(name, got, want):
    global fails, total
    total += 1
    ok = got == want
    print(('ok  ' if ok else 'NO  ') + name + ' got=' + repr(got) + ' want=' + repr(want))
    if not ok:
        fails.append(name)


def has(name, text, sub):
    eq(name, sub in text, True)


src = io.open(FGS, encoding='utf-8').read()
doc = io.open(os.path.join(ROOT, 'docs', '经验-工程纪律.md'), encoding='utf-8').read()

# A 文档：五条 + 各自落点 + ④ 的诚实边界
has('A1 文档有上下文预算节', doc, '## 9. 上下文预算五条')
for mark in ['①', '②', '③', '④', '⑤']:
    has('A2 五条都在 ' + mark, doc, '| ' + mark + ' |')
has('A3 文档点名 _est_tokens', doc, '_est_tokens()')
has('A4 文档点名 _snapshot_tokens 按块累加', doc, '_snapshot_tokens()')
has('A5 文档写「未知（未记费）」', doc, '未知（未记费）')
has('A6 ④ 写明目前无对应路径', doc, '目前无对应代码路径')
has('A7 ⑤ 点名 fgs cost --usage', doc, 'fgs cost --usage')

# B 代码静态接线
has('B1 有 _est_tokens', src, 'def _est_tokens(text: str) -> int:')
has('B2 按 UTF-8 字节除 3', src, "return len((text or '').encode('utf-8')) // 3")
has('B3 有 _snapshot_tokens', src, 'def _snapshot_tokens(snap: dict) -> int:')
has('B4 按块遍历节点', src, "for k in ('goals', 'hints', 'steps', 'facts', 'digests'):")
has('B5 按块遍历关系', src, "for rel in snap.get('relations') or []:")
has('B6 snapshot 改用按块估算', src, 'ntok = _snapshot_tokens(snap)')
eq('B7 旧的整段字节除 3 已删', src.count('len(text.encode("utf-8")) // 3'), 0)
has('B8 有 _usage_totals', src, 'def _usage_totals(target: str) -> dict:')
has('B9 读 message.usage', src, 'mu = m.get("usage")')
has('B10 读 type=usage 记录', src, 'u = obj.get("usage")')
has('B11 没 usage 的消息单独计数', src, 'out["missing_usage"] += 1')
has('B12 cost 打印未知未记费', src, '未知（未记费')
has('B13 缺 usage 明说不是 0 元', src, '不是真的 0 元')
has('B14 argparse 有 --usage', src, 'p.add_argument("--usage", default=None, metavar="DIR_OR_FILE"')
has('B15 json 里 usage 未知是 None 不是 0', src, '"usage_tokens": (ut["tokens"] if ut and ut["with_usage"] else None)')

# C 运行时：_est_tokens 的两种字符集
py = [sys.executable, '-c',
      "import sys; sys.path.insert(0, 'board'); import fgs;"
      "print(fgs._est_tokens('中文'), fgs._est_tokens('abcd'), fgs._est_tokens(''), fgs._est_tokens(None))"]
out = subprocess.run(py, cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
eq('C1 est_tokens 中文 6B→2 / 英文 4B→1 / 空串 0 / None 0',
   out.stdout.strip(), '2 1 0 0')

# D 运行时：cost 的三种 usage 形态
tmp = tempfile.mkdtemp(prefix='t39-')
os.environ['PI_LAB_BOARD_DIR'] = tmp
os.environ['PI_FGS_TOOFAST_SEC'] = '0'
env = dict(os.environ, PYTHONIOENCODING='utf-8')


def run(args):
    return subprocess.run([sys.executable, FGS] + args, cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding='utf-8')


run(['kickoff', 'p1', '--kind', 'ctf', '--goal', 'g', '--target', 't'])
r = run(['cost', 'p1', '--json'])
j = json.loads(r.stdout)
eq('D1 没给 --usage：usage_tokens 是 None（不是 0）', j['usage_tokens'], None)
eq('D2 没给 --usage：usage_cost 是 None', j['usage_cost'], None)
has('D3 未知原因点名未记费', j['usage_unknown_reason'], '没有 usage 记录或未给会话目录')
txt = run(['cost', 'p1']).stdout
has('D4 人读输出写未知未记费', txt, '未知（未记费')

sdir = os.path.join(tmp, 'sess')
os.makedirs(sdir, exist_ok=True)
lines = [
    {'type': 'message', 'message': {'role': 'assistant', 'usage': {
        'input': 1000, 'output': 200, 'cacheRead': 5000, 'cacheWrite': 0,
        'totalTokens': 6200, 'cost': {'total': 0.0123}}}},
    {'type': 'message', 'message': {'role': 'assistant', 'content': []}},
    {'type': 'usage', 'kind': 'cache_warm', 'usage': {'input': 0, 'output': 0,
                                                 'cacheRead': 1000, 'totalTokens': 1000}},
]
with io.open(os.path.join(sdir, 's1.jsonl'), 'w', encoding='utf-8', newline='\n') as f:
    for ln in lines:
        f.write(json.dumps(ln, ensure_ascii=False) + '\n')
r = run(['cost', 'p1', '--usage', sdir, '--json'])
j = json.loads(r.stdout)
eq('D5 真值累加 6200+1000', j['usage_tokens'], 7200)
eq('D6 输入累加', j['usage_input'], 1000)
eq('D7 cost 取 provider 回传', j['usage_cost'], 0.0123)
eq('D8 缺 usage 的消息被计数', j['usage_messages_without_usage'], 1)
r2 = run(['cost', 'p1', '--usage', sdir])
has('D9 人读输出给 token 真值', r2.stdout, '7,200 token')
has('D10 stderr 明说不是真的 0 元', r2.stderr, '不是真的 0 元')

r = run(['cost', 'p1', '--usage', os.path.join(tmp, 'nope'), '--json'])
j = json.loads(r.stdout)
eq('D11 路径不存在也不当 0', j['usage_tokens'], None)
has('D12 原因带上路径', j['usage_unknown_reason'], '路径不存在')

# E 快照仍按块估算告警（阈值 1 必触发）
env2 = dict(env, PI_FGS_BUDGET_WARN='1')
r = subprocess.run([sys.executable, FGS, 'snapshot', 'p1'], cwd=ROOT, env=env2,
                    capture_output=True, text=True, encoding='utf-8')
has('E1 快照告警仍触发', r.stderr, '快照 ≈')
has('E2 告警说明是估算（健康线口径）', r.stderr, '健康线')

print('')
print(str(total) + ' 组断言')
if fails:
    print('FAILED ' + str(len(fails)) + ': ' + ', '.join(fails))
    sys.exit(1)
print('ALL PASSED')
