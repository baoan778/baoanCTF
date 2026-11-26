#!/usr/bin/env python3
"""tsec 平台登录态（platform_token）与试题集：取证 / 判活 / 落盘的回归。

跑法：
    python3 board/tests/test_tsec_platform.py

为什么要有这个文件（2026-11-19）
--------------------------------
病根：`tsec run-new` 打的是平台自身的 /api/v1（Bearer JWT），而 BENCHMARK_TOKEN
只对 /openapi/v1 有效。JWT 只能来自浏览器登录态，原先全靠人手抄 —— 一断就再也没法
新建跑分任务，实战验证卡在这里。

现在 `tsec plat-token` 会去扫本机浏览器 profile 的 Local Storage leveldb，
把 `_https://tsecbench.zc.tencent.com\x00\x01token` 后面那串 JWT 取出来、判过期、
写进 ~/.config/pi-lab/tsecbench.json（0600）。

判据：
  · 解析/判活必须对（过期的不许当成活的）
  · leveldb 里没有 tsecbench 的键就不许乱认（别的站点的 JWT 不许混进来）
  · 多个候选挑过期最晚的
  · 环境变量优先于扫盘
  · 找不到时必须给出可执行指引，不许静默返回 0

★ 不碰真平台、不碰真浏览器 profile：全程临时目录 + 构造的 leveldb 字节。
"""
import base64
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LAB = Path(__file__).resolve().parents[2]
TSEC = LAB / 'tools' / 'tsecbench.py'

try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

sys.path.insert(0, str(LAB / 'tools'))
import tsecbench as T  # noqa: E402

passed, failed = [], []


def check(name, cond, detail=''):
    if cond:
        passed.append(name)
        print('  ✓ ' + name)
    else:
        failed.append(name)
        print('  ✗ ' + name + '   ' + str(detail)[:300])


def b64(obj):
    raw = json.dumps(obj).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def mk_jwt(payload, sig='sig_' + 'A' * 20):
    return 'eyJhbGciOiJIUzI1NiJ9.' + b64(payload) + '.' + sig


def leveldb_dir(root: str) -> str:
    d = os.path.join(root, 'Local Storage', 'leveldb')
    os.makedirs(d, exist_ok=True)
    return d


def main() -> int:
    print('1) JWT 解析与判活')
    alive = mk_jwt({'sub': '1547', 'exp': int(time.time()) + 3600})
    dead = mk_jwt({'sub': '1547', 'exp': int(time.time()) - 60})
    noexp = mk_jwt({'sub': '9'})
    check('JWT_RE 认标准三段 JWT', bool(T.JWT_RE.fullmatch(alive)), alive)
    check('JWT_RE 不认随便两段串', not T.JWT_RE.fullmatch('abc.def'))
    check('payload 解出 sub', T._jwt_payload(alive).get('sub') == '1547', T._jwt_payload(alive))
    check('活 token 判活', T._jwt_alive(alive)[0] is True, T._jwt_alive(alive))
    check('过期 token 判死', T._jwt_alive(dead)[0] is False, T._jwt_alive(dead))
    check('没 exp 的当活', T._jwt_alive(noexp)[0] is True, T._jwt_alive(noexp))
    check('坏串不炸、payload 为空', T._jwt_payload('not-a-jwt') == {})

    print('2) leveldb 扫描')
    with tempfile.TemporaryDirectory() as td:
        d = leveldb_dir(td)
        blob = (b'\x00\x01_https://' + T.TSEC_HOST.encode() + b'\x00\x01token'
                b'\x01' + alive.encode() + b'\x00')
        io.open(os.path.join(d, '000003.log'), 'wb').write(blob)
        toks = T._scan_leveldb(d)
        check('扫到 1 条', len(toks) == 1, toks)
        check('扫到的就是那条', toks and toks[0] == alive, toks[:1])

    with tempfile.TemporaryDirectory() as td:
        d = leveldb_dir(td)
        other = (b'\x00\x01_https://example.com\x00\x01token\x01' + alive.encode() + b'\x00')
        io.open(os.path.join(d, '000004.ldb'), 'wb').write(other)
        check('没有 tsecbench 的键就一条都不认', T._scan_leveldb(d) == [])

    with tempfile.TemporaryDirectory() as td:
        d = leveldb_dir(td)
        # 同一个 leveldb 里混进别的站点的、字段更多的 JWT（应当被特征过滤掉）
        foreign = mk_jwt({'sub': 'x', 'exp': int(time.time()) + 3600, 'name': 'other-site'})
        blob = (b'_https://' + T.TSEC_HOST.encode() + b'\x00\x01token\x01' + alive.encode()
                + b'\x00' + foreign.encode() + b'\x00')
        io.open(os.path.join(d, '000005.log'), 'wb').write(blob)
        toks = T._scan_leveldb(d)
        check('字段不像平台 token 的被滤掉', foreign not in toks, toks)
        check('真的那条还在', alive in toks, toks)

    print('3) 多候选挑最新的')
    soon = mk_jwt({'sub': '1', 'exp': int(time.time()) + 600})
    later = mk_jwt({'sub': '1', 'exp': int(time.time()) + 86400})
    pick, _why = T._pick_newest([soon, later])
    check('挑过期最晚的', pick == later, pick[:20])
    check('全过期时返回空', T._pick_newest([dead])[0] == '')
    check('空列表返回空', T._pick_newest([])[0] == '')

    print('4) find_platform_token：环境变量优先')
    old = os.environ.get('TSEC_PLATFORM_TOKEN')
    try:
        os.environ['TSEC_PLATFORM_TOKEN'] = alive
        tok, why = T.find_platform_token()
        check('环境变量给的 token 被采纳', tok == alive, tok[:20])
        check('说明里点了环境变量', 'TSEC_PLATFORM_TOKEN' in why, why)
        os.environ['TSEC_PLATFORM_TOKEN'] = dead
        tok2, why2 = T.find_platform_token()
        check('过期的环境变量不被采纳', tok2 != dead, tok2[:20])
    finally:
        if old is None:
            os.environ.pop('TSEC_PLATFORM_TOKEN', None)
        else:
            os.environ['TSEC_PLATFORM_TOKEN'] = old

    print('5) 子命令真的接上了')
    src = io.open(TSEC, encoding='utf-8').read()
    check('注册了 sets', '"sets": cmd_sets' in src)
    check('注册了 plat-token', '"plat-token": cmd_plat_token' in src)
    check('有 cmd_sets 实现', 'def cmd_sets(' in src)
    check('有 cmd_plat_token 实现', 'def cmd_plat_token(' in src)
    for fn in ('cmd_sets', 'cmd_plat_token', 'find_platform_token'):
        check('可调用：' + fn, callable(getattr(T, fn, None)))

    print('6) 找不到 token 时给可执行指引（不许静默成功）')
    with tempfile.TemporaryDirectory() as home:
        env = dict(os.environ)
        env['HOME'] = home
        env['USERPROFILE'] = home
        env['LOCALAPPDATA'] = os.path.join(home, 'nowhere')
        env['PI_LAB_TSEC_CONFIG'] = os.path.join(home, 'cfg.json')
        env.pop('TSEC_PLATFORM_TOKEN', None)
        env['PYTHONIOENCODING'] = 'utf-8'
        r = subprocess.run([sys.executable, str(TSEC), 'plat-token'], cwd=str(LAB),
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', env=env, timeout=180)
        out = (r.stdout or '') + (r.stderr or '')
        check('rc != 0', r.returncode != 0, r.returncode)
        check('没崩出 Traceback', 'Traceback' not in out, out[-400:])
        check('告诉人去哪个网址登录', 'tsecbench.zc.tencent.com' in out, out[-400:])

    print('7) 落了配置就是 0600（在临时 HOME 里验）')
    with tempfile.TemporaryDirectory() as home:
        cfgp = os.path.join(home, 'cfg.json')
        env = dict(os.environ)
        env['HOME'] = home
        env['PI_LAB_TSEC_CONFIG'] = cfgp
        env['TSEC_PLATFORM_TOKEN'] = alive
        env['PYTHONIOENCODING'] = 'utf-8'
        r = subprocess.run([sys.executable, str(TSEC), 'plat-token'], cwd=str(LAB),
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', env=env, timeout=180)
        check('rc == 0', r.returncode == 0, (r.stdout or '') + (r.stderr or ''))
        check('配置写了', os.path.exists(cfgp), cfgp)
        if os.path.exists(cfgp):
            d = json.loads(io.open(cfgp, encoding='utf-8').read())
            check('platform_token 落进去了', d.get('platform_token') == alive)
            check('没动 BENCHMARK_TOKEN', 'token' in d)
        if os.name != 'nt' and os.path.exists(cfgp):
            check('权限 0600', (os.stat(cfgp).st_mode & 0o777) == 0o600,
                  oct(os.stat(cfgp).st_mode & 0o777))

    print('\n通过 ' + str(len(passed)) + ' / 失败 ' + str(len(failed)))
    if failed:
        print('失败项：' + '、'.join(failed))
        return 1
    print('✓ tsec 平台登录态回归：全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())