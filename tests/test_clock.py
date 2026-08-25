"""Clock 抽象测试。"""

from __future__ import annotations

import pytest

from src.clock import Clock, SystemClock, TestClock

# pytest 不收集 __init__ 类，所以导入时加 __test__ = False
TestClock.__test__ = False  # type: ignore[attr-defined]


class TestSystemClock:
    def test_returns_positive_ms(self):
        clock = SystemClock()
        assert clock.now_ms() > 0

    def test_is_clock(self):
        assert isinstance(SystemClock(), Clock)


class TestTestClock:
    def test_initial_value(self):
        clock = TestClock(initial_ms=1000)
        assert clock.now_ms() == 1000

    def test_advance(self):
        clock = TestClock(initial_ms=1000)
        clock.advance(500)
        assert clock.now_ms() == 1500

    def test_set(self):
        clock = TestClock()
        clock.set(99999)
        assert clock.now_ms() == 99999

    def test_advance_negative_rejected(self):
        clock = TestClock(initial_ms=100)
        with pytest.raises(ValueError, match="rewind"):
            clock.advance(-1)

    def test_is_clock(self):
        assert isinstance(TestClock(), Clock)
