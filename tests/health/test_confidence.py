"""ConfidenceState 派生测试 — 关键流状态变化 → ConfidenceState 联动。

Fail Closed 约束：
- 关键流 STALE/DRIFT/FAIL → UNKNOWN → 禁止 CONFIRMED
- 关键流 WARN → DEGRADED → 禁止 CONFIRMED，允许 SUSPECTED
- 全部 OK → CONFIDENT → 允许全部
"""

from __future__ import annotations

from src.domain import ConfidenceState, HealthLevel, HealthStatus
from src.health.confidence import (
    ConfidenceTracker,
    can_confirm,
    can_suspect,
    derive_confidence,
    is_critical_stream,
)


def _make_health(stream: str, status: HealthLevel, symbol: str = "BTCUSDT") -> HealthStatus:
    return HealthStatus(
        stream=stream,
        symbol=symbol,
        status=status,
        connected=True,
        message_count=100,
        reconnect_count=0,
    )


class TestIsCriticalStream:
    def test_aggtrade_is_critical(self):
        assert is_critical_stream("aggTrade:BTCUSDT") is True

    def test_kline_is_critical(self):
        assert is_critical_stream("kline:BTCUSDT") is True

    def test_oi_poller_is_critical(self):
        assert is_critical_stream("oi_poller:BTCUSDT") is True

    def test_funding_not_critical(self):
        assert is_critical_stream("funding_premium:BTCUSDT") is False


class TestDeriveConfidence:
    def test_all_ok_confident(self):
        """全部关键流 OK → CONFIDENT。"""
        statuses = [
            _make_health("aggTrade:BTCUSDT", HealthLevel.OK),
            _make_health("kline:BTCUSDT", HealthLevel.OK),
            _make_health("oi_poller:BTCUSDT", HealthLevel.OK),
        ]
        assert derive_confidence(statuses) == ConfidenceState.CONFIDENT

    def test_warn_degraded(self):
        """存在 WARN → DEGRADED。"""
        statuses = [
            _make_health("aggTrade:BTCUSDT", HealthLevel.WARN),
            _make_health("kline:BTCUSDT", HealthLevel.OK),
            _make_health("oi_poller:BTCUSDT", HealthLevel.OK),
        ]
        assert derive_confidence(statuses) == ConfidenceState.DEGRADED

    def test_stale_unknown(self):
        """任一关键流 STALE → UNKNOWN。"""
        statuses = [
            _make_health("aggTrade:BTCUSDT", HealthLevel.STALE),
            _make_health("kline:BTCUSDT", HealthLevel.OK),
            _make_health("oi_poller:BTCUSDT", HealthLevel.OK),
        ]
        assert derive_confidence(statuses) == ConfidenceState.UNKNOWN

    def test_drift_unknown(self):
        statuses = [
            _make_health("aggTrade:BTCUSDT", HealthLevel.OK),
            _make_health("kline:BTCUSDT", HealthLevel.DRIFT),
        ]
        assert derive_confidence(statuses) == ConfidenceState.UNKNOWN

    def test_fail_unknown(self):
        statuses = [
            _make_health("aggTrade:BTCUSDT", HealthLevel.FAIL),
        ]
        assert derive_confidence(statuses) == ConfidenceState.UNKNOWN

    def test_non_critical_doesnt_affect(self):
        """非关键流状态不影响 confidence。"""
        statuses = [
            _make_health("aggTrade:BTCUSDT", HealthLevel.OK),
            _make_health("funding_premium:BTCUSDT", HealthLevel.STALE),  # 非关键
        ]
        assert derive_confidence(statuses) == ConfidenceState.CONFIDENT

    def test_no_critical_streams_unknown(self):
        """没有关键流 → 保守 UNKNOWN。"""
        statuses = [
            _make_health("funding_premium:BTCUSDT", HealthLevel.OK),
        ]
        assert derive_confidence(statuses) == ConfidenceState.UNKNOWN


class TestCanConfirm:
    def test_confident_can_confirm(self):
        assert can_confirm(ConfidenceState.CONFIDENT) is True

    def test_degraded_cannot_confirm(self):
        assert can_confirm(ConfidenceState.DEGRADED) is False

    def test_unknown_cannot_confirm(self):
        assert can_confirm(ConfidenceState.UNKNOWN) is False


class TestCanSuspect:
    def test_confident_can_suspect(self):
        assert can_suspect(ConfidenceState.CONFIDENT) is True

    def test_degraded_can_suspect(self):
        assert can_suspect(ConfidenceState.DEGRADED) is True

    def test_unknown_cannot_suspect(self):
        assert can_suspect(ConfidenceState.UNKNOWN) is False


class TestConfidenceTracker:
    def test_update_and_get(self):
        tracker = ConfidenceTracker()
        statuses = [
            _make_health("aggTrade:BTCUSDT", HealthLevel.OK),
            _make_health("kline:BTCUSDT", HealthLevel.OK),
            _make_health("oi_poller:BTCUSDT", HealthLevel.OK),
        ]
        result = tracker.update("BTCUSDT", statuses)
        assert result == ConfidenceState.CONFIDENT
        assert tracker.get("BTCUSDT") == ConfidenceState.CONFIDENT

    def test_default_unknown(self):
        tracker = ConfidenceTracker()
        assert tracker.get("UNKNOWNUSDT") == ConfidenceState.UNKNOWN

    def test_can_confirm_flag(self):
        tracker = ConfidenceTracker()
        statuses = [_make_health("aggTrade:BTCUSDT", HealthLevel.OK)]
        tracker.update("BTCUSDT", statuses)
        assert tracker.can_confirm("BTCUSDT") is True

    def test_stale_blocks_confirm(self):
        """关键数据 STALE → detector 无权进入 CONFIRMED。"""
        tracker = ConfidenceTracker()
        statuses = [
            _make_health("aggTrade:BTCUSDT", HealthLevel.STALE),
            _make_health("kline:BTCUSDT", HealthLevel.OK),
        ]
        tracker.update("BTCUSDT", statuses)
        assert tracker.can_confirm("BTCUSDT") is False
        assert tracker.get("BTCUSDT") == ConfidenceState.UNKNOWN

    def test_degraded_allows_suspect_blocks_confirm(self):
        """DEGRADED → 允许 SUSPECTED 但禁止 CONFIRMED。"""
        tracker = ConfidenceTracker()
        statuses = [
            _make_health("aggTrade:BTCUSDT", HealthLevel.WARN),
            _make_health("kline:BTCUSDT", HealthLevel.OK),
        ]
        tracker.update("BTCUSDT", statuses)
        assert tracker.can_confirm("BTCUSDT") is False
        assert tracker.can_suspect("BTCUSDT") is True
