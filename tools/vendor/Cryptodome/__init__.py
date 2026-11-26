"""Cryptodome 别名包 —— 惰性把 Cryptodome.* 映射到已装的 Crypto.*（pycryptodome）。

为什么要有（2026-11-14 实测）：jwt_tool 硬编码 `from Cryptodome...`，容器装的是
pycryptodome（导入名 Crypto）。两者 API 一致，只是顶层包名不同。
容器 pip 到 pypi 极慢，下载 pycryptodomex（几十 MB）不现实。

★ 第一版用 pkgutil.walk_packages 急切导入全部子模块 → 触发 pycryptodome 的
  "self-tests require pytest" 报错。改为**惰性 finder**：只在实际 import 时才映射。
"""
import importlib
import importlib.abc
import importlib.machinery
import sys

_PREFIX = "Cryptodome."
_SRC = "Crypto."


class _Finder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """把 Cryptodome.X 惰性映射到 Crypto.X。"""

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(_PREFIX):
            return None
        return importlib.machinery.ModuleSpec(fullname, self, is_package=True)

    def create_module(self, spec):
        real = _SRC + spec.name[len(_PREFIX):]
        try:
            mod = importlib.import_module(real)
        except Exception:
            return None
        sys.modules[spec.name] = mod
        return mod

    def exec_module(self, module):
        pass


if not any(isinstance(f, _Finder) for f in sys.meta_path):
    sys.meta_path.insert(0, _Finder())

# 顶层自身也转发到 Crypto（保持 __version__ 等属性可用）
try:
    import Crypto as _Crypto
    __version__ = getattr(_Crypto, "__version__", "?")
    __all__ = [a for a in dir(_Crypto) if not a.startswith("_")]
except Exception:
    __version__ = "?"
    __all__ = []