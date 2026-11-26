#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""#14 SKILL 首屏「硬禁止表 + 完成定义」回归。

为什么有这份测试：首屏这两块是**事前**自查（区别于 tools/reflect.py 的事后判定）。
它们被删掉/改短不会报错，只会让子代理回到「已枚举完」和「还在瞎试」之间自评 ——
所以固定成断言：三份 SKILL 各有一张 ≤6 行的硬禁止表、一份 ≤6 条可打勾的完成定义，
且都排在「起手」之前（要能被第一屏看到）。
"""
import io
import os
import re
import sys

# ★ 2026-11-19：原来硬编码 'D:/baoan'，在容器里跑（/opt/pi-ctf）必然 FileNotFoundError。
# 改成按本文件位置推导仓库根，宿主与容器两边都能跑。
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKILLS = [
    ('skills/ctf-web/SKILL.md', ['≥2 次还重复', '无 XSS / 无渲染后 DOM', '逐字']),
    ('skills/ctf-pwn/SKILL.md', ['libcsearch', 'checksec', 'timeout 120']),
    ('skills/ctf-misc/SKILL.md', ['binwalk', '只凭后缀', 'OCR']),
]
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

def block_of(src, start, end):
    i = src.find(start)
    if i < 0:
        return ''
    j = src.find(end, i + len(start))
    return src[i:j if j > 0 else len(src)]

renders = {}
for rel, keys in SKILLS:
    p = os.path.join(ROOT, rel)
    src = io.open(p, encoding='utf-8').read()
    name = rel.split('/')[1]
    has(name + ' A1 有硬禁止表', src, '## 硬禁止（事前自查')
    has(name + ' A2 有完成定义', src, '## 完成定义（可打勾')
    # 硬禁止表：表头 2 行（header + 分隔）之外的数据行 <= 6
    hard = block_of(src, '## 硬禁止', '## 完成定义')
    rows = [l for l in hard.splitlines() if l.startswith('| ') and not l.startswith('| ---')]
    data_rows = [l for l in rows if '禁止' not in l.split('|')[1]]
    eq(name + ' A3 硬禁止 <=6 行', len(data_rows) <= 6 and len(data_rows) >= 3, True)
    # 完成定义：- [ ] 项 <= 6 且 >= 4
    done = block_of(src, '## 完成定义', '\n## ')
    items = [l for l in done.splitlines() if l.strip().startswith('- [ ]')]
    eq(name + ' A4 完成定义 4..6 条', 4 <= len(items) <= 6, True)
    # 两屏都必须在「起手」之前（第一屏可见）
    i_hard = src.find('## 硬禁止')
    i_start = src.find('## 起手')  # 第一个以「## 起手」开头的标题
    eq(name + ' A5 硬禁止在起手之前', 0 <= i_hard < i_start, True)
    eq(name + ' A6 完成定义在起手之前', 0 <= src.find('## 完成定义') < i_start, True)
    # 每条禁止都写了「撞上了怎么办」（右列非空）
    empty_right = [l for l in data_rows if not l.rstrip().rstrip('|').split('|')[-1].strip()]
    eq(name + ' A7 每条禁止都有对策列', empty_right, [])
    for k in keys:
        has(name + ' A8 关键条 ' + k, src, k)
    eq(name + ' A9 有起手标题', len([l for l in src.splitlines() if l.strip().startswith('## 起手')]) >= 1, True)
    renders[name] = done

# B 完成定义必须包含「提交/回执」与「黑板」两条纪律（否则可以自评通过）
for name, done in renders.items():
    eq(name + ' B1 完成定义含平台回执', '回执' in done, True)
    eq(name + ' B2 完成定义含已提交', '提交' in done, True)

# C tools/reflect.py 的事后判定文案必须点明「事前自查」分工
rp = os.path.join(ROOT, 'tools/reflect.py')
rsrc = io.open(rp, encoding='utf-8').read()
has('C1 reflect 提到这是事后判定', rsrc, '这是**事后**判定')
has('C2 reflect 指向 SKILL 首屏硬禁止表', rsrc, '硬禁止表')
has('C3 reflect 提到失败≥2次换策略', rsrc, '同一条命令失败 ≥2 次就该换策略')
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import reflect  # noqa: E402
out = reflect.render({'code': 'p', 'session': 's', 'commands': 3, 'surfaces': {'a': 2}, 'missing_tool': [], 'verdict': 'SAME_METHOD'})
has('C4 render 输出带时机说明', out, '**时机**')
has('C5 render 输出指向首屏硬禁止表', out, '硬禁止表')
# 其它三种判定不许被这句污染
out2 = reflect.render({'code': 'p', 'session': 's', 'commands': 3, 'surfaces': {'a': 1, 'b': 2, 'c': 3}, 'missing_tool': [], 'verdict': 'MULTI_METHOD'})
eq('C6 MULTI_METHOD 不带时机句', '**时机**' in out2, False)
out3 = reflect.render({'code': 'p', 'session': 's', 'commands': 3, 'surfaces': {}, 'missing_tool': ['x'], 'verdict': 'MISSING_TOOL'})
eq('C7 MISSING_TOOL 不带时机句', '**时机**' in out3, False)

# D frontmatter 仍然完好（skilllint 也能过，但这里防手改破坏）
for rel, _ in SKILLS:
    src = io.open(os.path.join(ROOT, rel), encoding='utf-8').read()
    nm = rel.split('/')[1]
    eq(nm + ' D1 frontmatter name 对得上', re.search(r'^name: ([a-z-]+)$', src, re.M).group(1), nm)
    eq(nm + ' D2 首行是 ---', src.splitlines()[0], '---')

print('')
print(str(total) + ' 组断言')
if fails:
    print('FAILED ' + str(len(fails)) + ': ' + ', '.join(fails))
    sys.exit(1)
print('ALL PASSED')
