"""Freshness Watchdog 测试。

核心不变量：connected ≠ healthy。
WS socket 可能 open 但长时间无推送（半死状态）。
"""

from __future__ import annotations

import pytest

from src.clock import TestClock
from src.domain import HealthLevel
from src.health.freshness_watchdog import (
    FreshnessBudget,
    FreshnessWatchdog,
    StreamType,
)


class TestFreshnessWatchdogBasic:
    def test_not_registered_returns_fail(self):
        wd = FreshnessWatchdog(clock=TestClock(initial_ms=1000))
        hs = wd.check_health("nonexistent")
        assert hs.status == HealthLevel.FAIL
        assert hs.connected is False

    def test_connected_no_data_returns_warn(self):
        """连接了但从未收到数据 → WARN。"""
        wd = FreshnessWatchdog(clock=TestClock(initial_ms=1000))
        wd.register_stream("aggTrade:BTCUSDT", "BTCUSDT", StreamType.AGGTRADE)
        wd.mark_connected("aggTrade:BTCUSDT", True)
        hs = wd.check_health("aggTrade:BTCUSDT")
        assert hs.status == HealthLevel.WARN
        assert hs.connected is True

    def test_connected_with_recent_data_returns_ok(self):
        """连接且数据新鲜 → OK。"""
        clock = TestClock(initial_ms=10000)
        wd = FreshnessWatchdog(clock=clock)
        wd.register_stream("aggTrade:BTCUSDT", "BTCUSDT", StreamType.AGGTRADE)
        wd.mark_connected("aggTrade:BTCUSDT", True)
        wd.record_event("aggTrade:BTCUSDT", event_time=9000, receive_time=9000)
        # 现在 10000，上次收到 9000，age=1000ms < 5000ms budget
        hs = wd.check_health("aggTrade:BTCUSDT")
        assert hs.status == HealthLevel.OK
        assert hs.connected is True

    def test_message_count_increments(self):
        clock = TestClock(initial_ms=10000)
        wd = FreshnessWatchdog(clock=clock)
        wd.register_stream("aggTrade:BTCUSDT", "BTCUSDT", StreamType.AGGTRADE)
        wd.mark_connected("aggTrade:BTCUSDT", True)
        wd.record_event("aggTrade:BTCUSDT")
        wd.record_event("aggTrade:BTCUSDT")
        wd.record_event("aggTrade:BTCUSDT")
        hs = wd.check_health("aggTrade:BTCUSDT")
        assert hs.message_count == 3


class TestConnectedNotEqualHealthy:
    """核心不变量：connected=true 不能推出 healthy=true。"""

    def test_ws_half_dead_detected(self):
        """WS 半死状态：TCP open 但无数据推送 → STALE。

        场景：连接着，30s 无 aggTrade → STALE。
        """
        clock = TestClock(initial_ms=0)
        wd = FreshnessWatchdog(
            budget=FreshnessBudget(aggtrade_active_ms=5000),
            clock=clock,
        )
        wd.register_stream("aggTrade:BTCUSDT", "BTCUSDT", StreamType.AGGTRADE)
        wd.mark_connected("aggTrade:BTCUSDT", True)
        wd.record_event("aggTrade:BTCUSDT", event_time=1000, receive_time=1000)

        # 30s 后
        clock.set(31_000)

        hs = wd.check_health("aggTrade:BTCUSDT")
        assert hs.connected is True  # 连接着
        assert hs.status == HealthLevel.STALE  # 但不健康

    def test_stale_then_recovers(self):
        """STALE 后收到新数据恢复 OK。"""
        clock = TestClock(initial_ms=0)
        wd = FreshnessWatchdog(
            budget=FreshnessBudget(aggtrade_active_ms=5000),
            clock=clock,
        )
        wd.register_stream("aggTrade:BTCUSDT", "BTCUSDT", StreamType.AGGTRADE)
        wd.mark_connected("aggTrade:BTCUSDT", True)
        wd.record_event("aggTrade:BTCUSDT", event_time=1000, receive_time=1000)

        clock.set(10_000)  # 超过 budget
        assert wd.check_health("aggTrade:BTCUSDT").status == HealthLevel.STALE

        # 收到新数据
        wd.record_event("aggTrade:BTCUSDT", event_time=10_000, receive_time=10_000)
        clock.set(10_001)
        assert wd.check_health("aggTrade:BTCUSDT").status == HealthLevel.OK

    def test_disconnected_returns_fail(self):
        clock = TestClock(initial_ms=1000)
        wd = FreshnessWatchdog(clock=clock)
        wd.register_stream("aggTrade:BTCUSDT", "BTCUSDT", StreamType.AGGTRADE)
        wd.mark_connected("aggTrade:BTCUSDT", True)
        wd.record_event("aggTrade:BTCUSDT")
        wd.mark_connected("aggTrade:BTCUSDT", False)
        hs = wd.check_health("aggTrade:BTCUSDT")
        assert hs.status == HealthLevel.FAIL


class TestFreshnessBudgetByType:
    def test_kline_budget_90s(self):
        clock = TestClock(initial_ms=0)
        wd = FreshnessWatchdog(
            budget=FreshnessBudget(kline_1m_ms=90_000),
            clock=clock,
        )
        wd.register_stream("kline:BTCUSDT", "BTCUSDT", StreamType.KLINE)
        wd.mark_connected("kline:BTCUSDT", True)
        wd.record_event("kline:BTCUSDT", receive_time=1000)

        clock.set(50_000)  # 50s < 90s
        assert wd.check_health("kline:BTCUSDT").status == HealthLevel.OK

        clock.set(92_000)  # 92s > 90s
        assert wd.check_health("kline:BTCUSDT").status == HealthLevel.STALE

    def test_oi_poller_budget(self):
        clock = TestClock(initial_ms=0)
        wd = FreshnessWatchdog(
            budget=FreshnessBudget(oi_poller_ms=10_000),
            clock=clock,
        )
        wd.register_stream("oi_poller:BTCUSDT", "BTCUSDT", StreamType.OI_POLLER)
        wd.mark_connected("oi_poller:BTCUSDT", True)
        wd.record_event("oi_poller:BTCUSDT", receive_time=1000)

        clock.set(5_000)  # 4s < 10s
        assert wd.check_health("oi_poller:BTCUSDT").status == HealthLevel.OK

        clock.set(12_000)  # 11s > 10s
        assert wd.check_health("oi_poller:BTCUSDT").status == HealthLevel.STALE

    def test_reconnect_count_tracked(self):
        clock = TestClock(initial_ms=0)
        wd = FreshnessWatchdog(clock=clock)
        wd.register_stream("aggTrade:BTCUSDT", "BTCUSDT", StreamType.AGGTRADE)
        wd.mark_connected("aggTrade:BTCUSDT", True)
        wd.mark_reconnected("aggTrade:BTCUSDT")
        wd.mark_reconnected("aggTrade:BTCUSDT")
        hs = wd.check_health("aggTrade:BTCUSDT")
        assert hs.reconnect_count == 2


class TestCheckAll:
    def test_check_all_returns_all_streams(self):
        clock = TestClock(initial_ms=1000)
        wd = FreshnessWatchdog(clock=clock)
        wd.register_stream("aggTrade:BTCUSDT", "BTCUSDT", StreamType.AGGTRADE)
        wd.register_stream("kline:BTCUSDT", "BTCUSDT", StreamType.KLINE)
        results = wd.check_all()
        assert len(results) == 2
