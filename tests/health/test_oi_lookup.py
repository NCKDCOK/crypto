"""OI as-of lookup 测试 — 容差外 unavailable，不得回退取更旧数据。"""

from __future__ import annotations

from decimal import Decimal

from src.domain import OpenInterestSnapshot
from src.health.oi_lookup import OILookup


def _make_oi(symbol: str, receive_time: int, oi: str = "100.0") -> OpenInterestSnapshot:
    return OpenInterestSnapshot(
        symbol=symbol,
        exchange="binance",
        event_time=receive_time,
        receive_time=receive_time,
        open_interest=Decimal(oi),
        source="binance_rest_openinterest",
        freshness_ms=0,
    )


class TestOILookupBasic:
    def test_exact_match(self):
        lookup = OILookup(default_tolerance_ms=1000)
        snap = _make_oi("BTCUSDT", 100000, "100.0")
        lookup.add_snapshot(snap)

        result = lookup.lookup("BTCUSDT", 100000)
        assert result.found is True
        assert result.snapshot is not None
        assert result.snapshot.open_interest == Decimal("100.0")

    def test_within_tolerance(self):
        lookup = OILookup(default_tolerance_ms=1000)
        lookup.add_snapshot(_make_oi("BTCUSDT", 100000))

        result = lookup.lookup("BTCUSDT", 100500)  # 500ms 差
        assert result.found is True

    def test_outside_tolerance_unavailable(self):
        """容差外无数据 → unavailable。"""
        lookup = OILookup(default_tolerance_ms=1000)
        lookup.add_snapshot(_make_oi("BTCUSDT", 100000))

        result = lookup.lookup("BTCUSDT", 105000)  # 5000ms 差 > 1000ms 容差
        assert result.found is False
        assert result.snapshot is None
        assert result.available is False


class TestOILookupNoFallback:
    """核心不变量：不得回退取更旧数据。"""

    def test_does_not_fallback_to_older(self):
        """5m 前无接近 OI 快照 → unavailable（非取 9m 前数据）。

        场景：需要 5m 前的快照，但最近的快照在 9m 前 → 必须返回 unavailable。
        """
        lookup = OILookup(default_tolerance_ms=15_000)  # 15s 容差
        # 只有 9 分钟前的快照
        lookup.add_snapshot(_make_oi("BTCUSDT", 0))  # receive_time=0
        # 需要约 5 分钟前的快照（target=300000），但最近的在 0，差 300s >> 15s 容差
        result = lookup.lookup("BTCUSDT", 300000)
        assert result.found is False
        assert result.snapshot is None

    def test_nearest_in_tolerance_picked(self):
        """多个快照在容差内，取最近的。"""
        lookup = OILookup(default_tolerance_ms=10_000)
        lookup.add_snapshot(_make_oi("BTCUSDT", 95000, "100.0"))
        lookup.add_snapshot(_make_oi("BTCUSDT", 100000, "200.0"))
        lookup.add_snapshot(_make_oi("BTCUSDT", 105000, "300.0"))

        # target=100000，三个都在容差内
        result = lookup.lookup("BTCUSDT", 100000)
        assert result.found is True
        assert result.snapshot is not None
        # 最近的是 receive_time=100000
        assert result.snapshot.open_interest == Decimal("200.0")

    def test_no_snapshots_returns_unavailable(self):
        lookup = OILookup()
        result = lookup.lookup("BTCUSDT", 100000)
        assert result.found is False
        assert result.reason == "no_snapshots"


class TestOIComputeChange:
    def test_compute_change_success(self):
        lookup = OILookup(default_tolerance_ms=1000)
        lookup.add_snapshot(_make_oi("BTCUSDT", 100000, "100.0"))

        current = _make_oi("BTCUSDT", 160000, "120.0")  # 60s 后
        # lookback 60s → target=100000
        change = lookup.compute_change("BTCUSDT", current, lookback_ms=60_000)
        assert change == Decimal("20.0")

    def test_compute_change_unavailable(self):
        """5m 前 OI 快照不在容差内 → oi_change=None（unavailable）。"""
        lookup = OILookup(default_tolerance_ms=1000)
        lookup.add_snapshot(_make_oi("BTCUSDT", 0, "100.0"))  # 很早

        current = _make_oi("BTCUSDT", 300000, "120.0")
        # lookback 300s → target=0，容差 1s，恰好匹配
        # 但如果 target 偏移就不行
        change = lookup.compute_change("BTCUSDT", current, lookback_ms=200_000)
        # target=100000，最近的在 0，差 100s >> 1s 容差
        assert change is None

    def test_per_symbol_isolated(self):
        lookup = OILookup(default_tolerance_ms=1000)
        lookup.add_snapshot(_make_oi("BTCUSDT", 100000, "100.0"))
        # ETHUSDT 没有快照
        result = lookup.lookup("ETHUSDT", 100000)
        assert result.found is False
