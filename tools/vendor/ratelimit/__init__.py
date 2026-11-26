"""ratelimit 最小替身 —— jwt_tool 只用到 limits/sleep_and_retry/RateLimitException。

为什么自己写（2026-11-14 实测）：容器 pip 到 pypi 极慢且 ratelimit 是 sdist 需要编译，
装不上导致 jwt_tool 完全不可用。这里实现等价语义（装饰器 + 限速），纯标准库。
"""
import functools
import time


class RateLimitException(Exception):
    pass


def limits(calls=15, period=60):
    """装饰器工厂：period 秒内最多 calls 次，超出抛 RateLimitException。"""
    def deco(fn):
        history = []

        @functools.wraps(fn)
        def wrapper(*a, **kw):
            now = time.time()
            history[:] = [t for t in history if now - t < period]
            if len(history) >= calls:
                raise RateLimitException(
                    f"rate limit exceeded: {calls} calls per {period}s")
            history.append(now)
            return fn(*a, **kw)
        return wrapper
    return deco


def sleep_and_retry(fn):
    """装饰器：撞到 RateLimitException 就睡一会儿再试。"""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        while True:
            try:
                return fn(*a, **kw)
            except RateLimitException:
                time.sleep(1)
    return wrapper