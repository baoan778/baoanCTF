#!/usr/bin/env python3
"""board/tests/test_empty_turn_nudge.py — 空转续跑（thinking-only 回合注入下一步）。

为什么：最常见的烧轮形态不是报错，而是「想完了不动手」—— 一回合只有思考、没有正文也没有
任何工具调用，于是下一轮从零再想一遍。ARTEX 用 emptyTurnNudge 把它拉回执行、每轮限 2 次。
本测试守四件事：①配置语义（未设/0=2、负=关、>0=用该值）；②判空口径取 turn_end 终态；
③额度按 agent 运行重置、工具回合后刷新、用完只提醒不再注入；④判定发生在瘦身/转发之前。

跑法：PYTHONIOENCODING=utf-8 python3 board/tests/test_empty_turn_nudge.py
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "work" / "ui" / "server.mjs"
PASS = 0
FAILED = 0


def ok(cond, name, extra=None):
    global PASS, FAILED
    if cond:
        PASS += 1
    else:
        FAILED += 1
        print('FAIL ' + name + ('' if extra is None else '  <<< ' + str(extra)[:600]))


def slice_between(src, a, b):
    i = src.index(a)
    j = src.index(b, i)
    return src[i:j]


def node_run(script):
    with tempfile.NamedTemporaryFile('w', suffix='.mjs', delete=False, encoding='utf-8', newline='\n') as fh:
        fh.write(script)
        path = fh.name
    try:
        return subprocess.run(['node', path], capture_output=True, text=True, encoding='utf-8')
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main():
    src = SERVER.read_text(encoding='utf-8')

    # A 静态：接线位置
    ok('// NUDGE-SLICE-BEGIN' in src and '// NUDGE-SLICE-END' in src, 'A1 纯函数段有标记（可切出来直跑）')
    ok('function emptyTurnNudgeLimit()' in src, 'A2 有 emptyTurnNudgeLimit')
    ok('function isEmptyTurn(rec)' in src, 'A3 有 isEmptyTurn')
    ok('PI_SOLVE_EMPTY_TURN_NUDGES' in src, 'A4 配置键是 PI_SOLVE_EMPTY_TURN_NUDGES（与空响应重试共用的那个）')
    ok('EMPTY_TURN_NUDGE_DEFAULT = 2' in src, 'A5 默认 2 次（ARTEX defaultEmptyTurnNudges=2）')
    ok('this.__nudgeLeft = undefined;' in src, 'A6 构造函数初始化额度')
    ok(re.search(r'agent_start"\) \{ this\.busy = true; this\.__nudgeLeft = emptyTurnNudgeLimit\(\);', src) is not None,
       'A7 agent_start 时按配置重置额度')
    ok('this.noteEmptyTurn(rec);' in src, 'A8 事件循环里调了判定')
    i_note = src.index('this.noteEmptyTurn(rec);')
    i_guard = src.index('if (this.guardStream(rec)) continue;')
    i_emit = src.index('this.emit(rec);', i_guard)
    ok(i_note < i_guard < i_emit, 'A9 判定在熔断/瘦身转发之前（终态事件必须先看）')
    ok('setImmediate(' in src[i_note - 4000:i_note + 6000], 'A10 注入走 setImmediate（不在 stdout 回调里同步递归）')
    ok('if (toolResults > 0) this.__nudgeLeft = limit;' in src, 'A11 工具回合后刷新额度')
    ok('\u7a7a\u8f6c\u7eed\u8dd1\uff1a' in src or '空转续跑：' in src, 'A12 提醒文案点名「空转续跑」')
    ok('PI_SOLVE_EMPTY_TURN_NUDGES 设为负数' in src, 'A13 额度用完时告诉用户怎么关')

    # B 行为：切出纯函数段，用 node 直跑
    block = slice_between(src, '// NUDGE-SLICE-BEGIN', '// NUDGE-SLICE-END')
    harness = block + '''
const cases = [];
function eq(name, got, want) { cases.push([name, JSON.stringify(got), JSON.stringify(want), JSON.stringify(got) === JSON.stringify(want)]); }
function withEnv(v, fn) { const old = process.env.PI_SOLVE_EMPTY_TURN_NUDGES; if (v === undefined) delete process.env.PI_SOLVE_EMPTY_TURN_NUDGES; else process.env.PI_SOLVE_EMPTY_TURN_NUDGES = v; try { return fn(); } finally { if (old === undefined) delete process.env.PI_SOLVE_EMPTY_TURN_NUDGES; else process.env.PI_SOLVE_EMPTY_TURN_NUDGES = old; } }
eq('B1 未设=2', withEnv(undefined, emptyTurnNudgeLimit), 2);
eq('B2 0=默认2', withEnv('0', emptyTurnNudgeLimit), 2);
eq('B3 负=关闭', withEnv('-1', emptyTurnNudgeLimit), 0);
eq('B4 正=用该值', withEnv('3', emptyTurnNudgeLimit), 3);
eq('B5 非法=默认', withEnv('abc', emptyTurnNudgeLimit), 2);
eq('B6 带空格', withEnv(' 4 ', emptyTurnNudgeLimit), 4);
eq('B7 空串=默认', withEnv('', emptyTurnNudgeLimit), 2);

const think = { type: 'turn_end', message: { role: 'assistant', content: [{ type: 'thinking', thinking: '先想一步' }] }, toolResults: [] };
eq('C1 只有思考=空转', isEmptyTurn(think), true);
eq('C2 空内容也算空转', isEmptyTurn({ type: 'turn_end', message: { role: 'assistant', content: [] }, toolResults: [] }), true);
eq('C3 有正文不算', isEmptyTurn({ type: 'turn_end', message: { role: 'assistant', content: [{ type: 'thinking', thinking: 'x' }, { type: 'text', text: '结果' }] }, toolResults: [] }), false);
eq('C4 空白正文算没正文', isEmptyTurn({ type: 'turn_end', message: { role: 'assistant', content: [{ type: 'text', text: '   ' }] }, toolResults: [] }), true);
eq('C5 有 toolCall 不算', isEmptyTurn({ type: 'turn_end', message: { role: 'assistant', content: [{ type: 'toolCall', name: 'bash' }] }, toolResults: [] }), false);
eq('C6 有 toolResults 不算', isEmptyTurn({ type: 'turn_end', message: { role: 'assistant', content: [] }, toolResults: [{ x: 1 }] }), false);
eq('C7 非 turn_end 不算', isEmptyTurn({ type: 'message_end' }), false);
eq('C8 null 不崩', isEmptyTurn(null), false);

const bad = cases.filter((c) => !c[3]);
for (const c of cases) console.log((c[3] ? 'ok ' : 'NO ') + c[0] + ' got=' + c[1] + ' want=' + c[2]);
process.exit(bad.length ? 1 : 0);
'''
    r = node_run(harness)
    ok(r.returncode == 0, 'B0 node 直跑纯函数段全过', r.stdout + r.stderr)
    out = r.stdout
    for key in ['B1 未设=2', 'B2 0=默认2', 'B3 负=关闭', 'B4 正=用该值', 'B5 非法=默认', 'B6 带空格', 'B7 空串=默认',
                'C1 只有思考=空转', 'C2 空内容也算空转', 'C3 有正文不算', 'C4 空白正文算没正文', 'C5 有 toolCall 不算',
                'C6 有 toolResults 不算', 'C7 非 turn_end 不算', 'C8 null 不崩']:
        ok(('ok ' + key) in out, 'B/C 用例 ' + key)

    # D 纪律：提醒不许冒充「模型自己想说的」——必须是 ui:stderr 通道，别混进 transcript
    note = src[src.index('noteEmptyTurn(rec) {'):src.index('emit(rec) {')]
    ok('ui:stderr' in note, 'D1 提醒走 ui:stderr（不写进会话正文）')
    ok('type": "prompt' in note or '"type": "prompt"' in note, 'D2 注入的是 prompt 命令')
    ok('空转提醒' in note or '【空转提醒】' in src, 'D3 注入文案以【空转提醒】开头（模型能一眼认出是系统插话）')
    ok('没有正文' in note, 'D4 提醒里说清判据：没有正文、也没有工具调用')
    ok('额度已用完' in note, 'D5 用完额度时只提醒、不再注入（防病态循环烧钱）')

    print('%d passed / %d failed' % (PASS, FAILED))
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
