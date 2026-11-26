#!/usr/bin/env python3
"""board/tests/test_cap_counts.py — 历史决策 #15：有 cap 的地方必须并列真实总数。

为什么：闸门/视图把 8 条剪成 6 条却不报总数时，读的人会把「没显示」当成「没发生」——
planner 据此判「这一片没东西了」→ 整条方向被判死。restart_brief 早就写对了（「另有 N 条，
看 fgs show <pid>」），但 _brief_body 三处与 budget_warn 没写。本测试守两件事：
① 超 cap 时出现「只显示 s/t」+「没显示 ≠ 不存在」；② 没超 cap 时不出现。

跑法：PYTHONIOENCODING=utf-8 python3 board/tests/test_cap_counts.py
"""

import os
import subprocess
import sys
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FGS = ROOT / "board" / "fgs.py"
PASS = 0
FAILED = 0


def ok(cond, name, extra=None):
    global PASS, FAILED
    if cond:
        PASS += 1
    else:
        FAILED += 1
        print('FAIL ' + name + ('' if extra is None else '  <<< ' + str(extra)[:600]))


def run(lab, *args, extra_env=None):
    env = dict(os.environ)
    env.update({"PI_LAB_BOARD_DIR": str(lab), "PI_FGS_TOOFAST_SEC": "0",
                "PYTHONIOENCODING": "utf-8"})
    if extra_env:
        env.update(extra_env)
    return subprocess.run([sys.executable, str(FGS)] + list(args), capture_output=True,
                          text=True, encoding='utf-8', errors='replace', env=env)


def make_board(lab, n_verified=8, n_weak=6, n_dead=8, n_steps=1, link=True):
    r = run(lab, 'kickoff', 'p', '--kind', 'ctf', '--goal', '摘要', '--target', 'http://t.local')
    if r.returncode != 0:
        raise SystemExit('kickoff 失败: ' + (r.stderr or r.stdout))
    for i in range(n_steps):
        s = run(lab, 'step-add', 'p', '步骤 %d' % (i + 1))
        if s.returncode != 0:
            raise SystemExit('step-add 失败: ' + (s.stderr or s.stdout))
    n = 0
    ids = []

    def add(text, conf):
        nonlocal n
        r2 = run(lab, 'fact-add', 'p', text, '--confidence', conf)
        if r2.returncode != 0:
            raise SystemExit('fact-add 失败: ' + (r2.stderr or r2.stdout))
        n += 1
        fid = 'f%03d' % n
        ids.append(fid)
        return fid

    for i in range(n_verified):
        add('已坐实事实 %d 号' % (i + 1), 'verified')
    for i in range(n_weak):
        add('未坐实猜测 %d 号' % (i + 1), 'unverified')
    for i in range(n_dead):
        add('这一路不通 未发现 %d 号' % (i + 1), 'verified')
    if link:
        for fid in ids:
            e = run(lab, 'relate', 'p', '--type', 'derived_from', '--from', 's001', '--to', fid)
            if e.returncode != 0:
                # ★ 2026-11-21（R7-D）：原来只打 (stderr or stdout) —— 进程被信号打死时
                #   两者都为空，报出的是「relate 失败: 」这种**零信息量**的字符串，
                #   实测全量回归里就这么红过（重跑又绿），根本查不出是哪条/什么原因。
                #   现在至少给出 rc（含负数=信号）、是哪条 to，以及有没有 Traceback。
                raise SystemExit(
                    'relate 失败: rc=%s to=%s out=%r err=%r' % (
                        e.returncode, fid, (e.stdout or '')[-300:], (e.stderr or '')[-300:]))
    return ids


def main():
    src = (ROOT / 'board' / 'fgs.py').read_text(encoding='utf-8')
    lines = src.splitlines()
    calls = [l for l in lines if '_cap_note(' in l and not l.lstrip().startswith('def ')]
    ok(len(calls) == 5, 'A1 _cap_note 恰好 5 处调用（brief 3 + restart 2）', len(calls))
    ok(any(l.lstrip().startswith('def _cap_note(') for l in lines), 'A2 _cap_note 有定义')
    ok('没显示 ≠ 不存在' in src, 'A3 文案点名「没显示 ≠ 不存在」')
    ok('另有' in src, 'A4 沿用 restart_brief 的「另有 N 条」范式')
    ok(all('fgs show' in l for l in calls), 'A5 每条 cap 都给了看全的命令', calls)
    ok('这里只显示' in src, 'A6 budget_warn 也有计数文案')

    tmp = Path(tempfile.mkdtemp(prefix='fgs15-'))
    try:
        # B：大样本 brief，三处 cap 都要报总数
        b = tmp / 'b'
        make_board(b, n_verified=8, n_weak=6, n_dead=8, n_steps=1)
        r = run(b, 'brief', 'p', '--step', 's001')
        out = r.stdout + r.stderr
        ok(r.returncode == 0, 'B1 brief 不受 cap 提示影响（rc=0）', r.returncode)
        ok(out.count('只显示 6/8') >= 2, 'B2 已坐实与已证伪都报 6/8', out.count('只显示 6/8'))
        ok('只显示 4/6' in out, 'B3 未坐实段 cap 是 4/6')
        ok(out.count('没显示 ≠ 不存在') >= 3, 'B4 三处 cap 都写「没显示 ≠ 不存在」', out.count('没显示 ≠ 不存在'))
        ok(out.count('另有 2 条') >= 3, 'B5 三处都报出被剪掉的 2 条', out.count('另有 2 条'))
        ok('`fgs show p` 看全' in out, 'B6 看全命令带真 pid')

        # C：小样本不许出现 cap 噪音
        c = tmp / 'c'
        make_board(c, n_verified=3, n_weak=2, n_dead=1, n_steps=1)
        rc = run(c, 'brief', 'p', '--step', 's001')
        cout = rc.stdout + rc.stderr
        ok(rc.returncode == 0, 'C1 小样本 brief rc=0', rc.returncode)
        ok('只显示' not in cout, 'C2 没超 cap 时不出现「只显示」')
        ok('没显示 ≠ 不存在' not in cout, 'C3 没超 cap 时不出现免责句')

        # D：重启简报两处 cap（未坐实 6/7、步骤 12/13）
        d = tmp / 'd'
        make_board(d, n_verified=2, n_weak=7, n_dead=0, n_steps=13)
        rd = run(d, 'brief', 'p', '--restart')
        dout = rd.stdout + rd.stderr
        ok(rd.returncode == 0, 'D1 --restart rc=0', rd.returncode)
        ok('只显示 6/7' in dout, 'D2 重启简报的未坐实段报 6/7')
        ok('只显示 12/13' in dout, 'D3 重启简报的步骤段报 12/13')
        ok('另有 1 条' in dout, 'D4 被剪掉的条数写在句首')

        # E：快照预算告警也要并列总数（孤点 8 条只列 6）
        e = tmp / 'e'
        make_board(e, n_verified=0, n_weak=8, n_dead=0, n_steps=1, link=False)
        re_ = run(e, 'snapshot', 'p', extra_env={'PI_FGS_BUDGET_WARN': '1'})
        eout = re_.stdout + re_.stderr
        ok(re_.returncode == 0, 'E1 snapshot rc=0', re_.returncode)
        ok('这里只显示 6/8 条非 verified' in eout, 'E2 预算告警报非 verified 的 6/8')
        ok('6/8 条孤点' in eout, 'E3 预算告警报孤点的 6/8')
        ok('没显示 ≠ 不存在' in eout, 'E4 预算告警也写免责句')

        # F：阈值没到就不刷告警（默认 16000）
        rf = run(e, 'snapshot', 'p')
        fout = rf.stdout + rf.stderr
        ok('这里只显示' not in fout, 'F1 没到预算阈值不出现计数行')
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)

    print('%d passed / %d failed' % (PASS, FAILED))
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
