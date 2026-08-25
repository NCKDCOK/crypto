"""时钟抽象 — deterministic replay 的基础。

所有时间相关逻辑必须通过注入的 Clock 接口，禁止直接调用 time.time() / datetime.now()。
依据：ARCHITECTURE.md §4
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod


class Clock(ABC):
    """统一时钟接口。"""

    @abstractmethod
    def now_ms(self) -> int:
        """返回当前 UTC 毫秒。"""
        ...


class SystemClock(Clock):
    """生产用 — wall time。"""

    def now_ms(self) -> int:
        return int(time.time() * 1000)


class TestClock(Clock):
    """测试 / replay 用 — 可控虚拟时间。

    初始时间默认 0；通过 advance() 推进。
    """

    def __init__(self, initial_ms: int = 0) -> None:
        self._now = initial_ms

    def now_ms(self) -> int:
        return self._now

    def advance(self, ms: int) -> None:
        """推进虚拟时间 ms 毫秒。"""
        if ms < 0:
            raise ValueError("cannot rewind time; ms must be >= 0")
        self._now += ms

    def set(self, ms: int) -> None:
        """直接设定虚拟时间。"""
        if ms < 0:
            raise ValueError("time must be >= 0")
        self._now = ms
