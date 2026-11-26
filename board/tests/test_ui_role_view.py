#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''分角色监督视图 —— 静态回归：分组口径 + 结果维度优先 + 不许退回平铺。'''
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fails = []


def ck(name, cond):
    if not cond:
        fails.append(name)


def main():
    p = os.path.join(ROOT, 'work/ui/web/src/pages/SubagentsPage.tsx')
    s = io.open(p, encoding='utf-8').read()

    ck('A1 有分组函数 groupOf', 'function groupOf(' in s)
    ck('A2 角色键取 agentName', 'x.agentName ||' in s and '(未标角色)' in s)
    ck('A3 线路键取 mission.missionId', 'x.mission?.missionId ||' in s and '(无线路)' in s)
    ck('A4 在跑计数用 state', '(x.state || x.status || ' in s)
    ck('A5 token 只累加有限数', 'Number.isFinite(x.tokens)' in s)
    ck('A6 排序：在跑多的在前，其次 token 高的', 'b.running - a.running || b.tokens - a.tokens' in s)
    ck('A7 tokens 不拿 0 冒充没花', 'n <= 0' in s and 'toFixed(1) + ' in s)

    ck('B1 有按角色/按线路切换', 'setBy(' in s and '按角色' in s and '按线路' in s)
    ck('B2 结果维度摘要在明细之前', s.index('在跑 {runningCount} 路') < s.index('按角色'))
    ck('B3 摘要含角色数与 token 合计', '个角色' in s and 'token 合计' in s)
    ck('B4 分组头带路数/在跑/token', '路 · 在跑 {g.running} · {fmtTokens(g.tokens)}' in s)
    ck('B5 线路模式显示 mission 摘要', 'g.mission?.summary' in s)
    ck('B6 每行显示 token 与状态', '{fmtTokens(x.tokens)}' in s and '{x.state || x.status ||' in s)
    ck('B7 运行点用 state 判定', '(x.state || x.status) === ' in s)
    ck('B8 历史行兼容 agent 字段', 'x.agentName || x.agent || x.name' in s)
    ck('B9 仍保留选中/详情/发指令', 'setSel(id)' in s and 'say(' in s and 'follow_up' in s)
    ck('B10 分组用 useMemo', 'useMemo(() => groupOf(live, by), [live, by])' in s)
    ck('B11 没有退回 setInterval 轮询', 'setInterval' not in s)
    ck('B12 类型补了 agent/tokens/mission', 'agent?: string;' in s and 'tokens?: number | null;' in s and 'mission?: Mission;' in s)

    total = 19
    if fails:
        print('%d passed / %d failed' % (total - len(fails), len(fails)))
        for f in fails:
            print('  FAIL ' + f)
        return 1
    print('%d passed / 0 failed' % total)
    return 0


if __name__ == '__main__':
    sys.exit(main())