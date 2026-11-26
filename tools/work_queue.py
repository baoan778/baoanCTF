#!/usr/bin/env python3
"""work_queue.py —— shard 共享取题队列（原子 pop）。

为什么要有（2026-11-14 实测）：原来按 index%shards 静态切桶，
  一道题快慢不同，先跑完的 shard 就空等其余 shard —— 实测 3 个 shard
  有 1/3 时间只有 2 个在干活。改成共享队列后，谁先完谁接着取下一题。

实现：文件 + mkdir 原子锁（drvfs/容器内都可靠，不依赖 flock）。
"""
from __future__ import annotations
import os, time
from pathlib import Path


class WorkQueue:
    def __init__(self, qfile: Path, lockdir: Path | None = None) -> None:
        self.qfile = Path(qfile)
        self.lockdir = Path(lockdir or (self.qfile.parent / (self.qfile.name + ".lock")))

    def _acquire(self, timeout: float = 30.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            try:
                self.lockdir.mkdir(parents=False, exist_ok=False)
                return True
            except FileExistsError:
                # 锁超过 60s 视为陈旧（进程被杀留下的）
                try:
                    if time.time() - self.lockdir.stat().st_mtime > 60:
                        self.lockdir.rmdir()
                        continue
                except OSError:
                    pass
                time.sleep(0.05)
        return False

    def _release(self) -> None:
        try:
            self.lockdir.rmdir()
        except OSError:
            pass

    def pop(self) -> str | None:
        """取一条；空则返回 None。"""
        if not self._acquire():
            return None
        try:
            try:
                lines = [ln.strip() for ln in self.qfile.read_text(encoding="utf-8").splitlines() if ln.strip()]
            except OSError:
                return None
            if not lines:
                return None
            head, rest = lines[0], lines[1:]
            self.qfile.write_text("\n".join(rest) + ("\n" if rest else ""), encoding="utf-8")
            return head
        finally:
            self._release()

    def push(self, codes: list[str]) -> None:
        if not codes:
            return
        if not self._acquire():
            raise RuntimeError("队列锁超时")
        try:
            cur = ""
            try:
                cur = self.qfile.read_text(encoding="utf-8")
            except OSError:
                pass
            if cur and not cur.endswith("\n"):
                cur += "\n"
            self.qfile.write_text(cur + "\n".join(codes) + "\n", encoding="utf-8")
        finally:
            self._release()