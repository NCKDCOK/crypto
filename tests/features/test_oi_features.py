"""OI 特征测试 — 单位、缺数据、oi_change=0。"""

from __future__ import annotations

from decimal import Decimal

from src.domain import OpenInterestSnapshot
from src.features.oi_features import (
    compute_oi_accel,
    compute_oi_change,
    compute_oi_features,
    compute_oi_velocity,
)


def _oi(symbol, receive_time, oi, event_time=None):
    et = event_time if event_time is not None else receive_time
    return OpenInterestSnapshot(
        symbol=symbol, exchange="binance", event_time=et,
        receive_time=receive_time, open_interest=Decimal(oi),
        source="binance_rest_openinterest", freshness_ms=0,
    )


class TestComputeOIChange:
    def test_basic_change(self):
        snaps = [_oi("BTCUSDT", 100000, "100.0"), _oi("BTCUSDT", 160000, "120.0")]
        change = compute_oi_change(snaps, 60_000, 15_000)
        # target = 160000 - 60000 = 100000, asof=100000, current=120
        assert change == 20.0

    def test_price_up_oi_flat_change_zero(self):
        """价格涨但 open_interest 不变 → oi_change=0。

        这是核心 P0 不变量：不得把美元名义 OI 上涨误判为新增仓位。
        """
        snaps = [_oi("BTCUSDT", 100000, "100.0"), _oi("BTCUSDT", 160000, "100.0")]
        change = compute_oi_change(snaps, 60_000, 15_000)
        assert change == 0.0

    def test_no_nearby_snapshot_unavailable(self):
        """容差外无快照 → None。"""
        snaps = [_oi("BTCUSDT", 0, "100.0"), _oi("BTCUSDT", 600000, "120.0")]
        change = compute_oi_change(snaps, 300_000, 15_000)
        # target = 600000 - 300000 = 300000
        # 最近的在 0，差 300s >> 15s → None
        assert change is None

    def test_empty(self):
        assert compute_oi_change([], 60_000, 15_000) is None


class TestComputeOIVelocity:
    def test_basic(self):
        snaps = [_oi("BTCUSDT", 100000, "100.0"), _oi("BTCUSDT", 160000, "120.0")]
        # dt=60s, change=20 → velocity = 20/60 = 0.333
        vel = compute_oi_velocity(snaps)
        assert abs(vel - 0.333) < 0.01

    def test_insufficient(self):
        assert compute_oi_velocity([_oi("BTCUSDT", 100, "100")]) is None
        assert compute_oi_velocity([]) is None


class TestComputeOIAccel:
    def test_basic(self):
        snaps = [
            _oi("BTCUSDT", 100000, "100.0"),
            _oi("BTCUSDT", 160000, "120.0"),  # v1 = 20/60
            _oi("BTCUSDT", 220000, "150.0"),  # v2 = 30/60
        ]
        accel = compute_oi_accel(snaps)
        # v1 = 20/60 ≈ 0.333, v2 = 30/60 = 0.5
        # accel = 0.5 - 0.333 ≈ 0.167
        assert accel is not None
        assert abs(accel - 0.167) < 0.01

    def test_insufficient(self):
        assert compute_oi_accel([_oi("BTCUSDT", 100, "100")]) is None


class TestComputeOIFeatures:
    def test_all_none_with_empty(self):
        """空快照 → 全部 None。"""
        result = compute_oi_features([])
        assert result.oi_change_1m is None
        assert result.oi_change_5m is None
        assert result.oi_velocity is None
        assert result.oi_accel is None

    def test_oi_unit_is_base_asset(self):
        """OI 单位 = 基础资产数量。"""
        snaps = [_oi("BTCUSDT", 100000, "50000.5"), _oi("BTCUSDT", 160000, "50000.5")]
        result = compute_oi_features(snaps)
        # 不变 → change=0
        assert result.oi_change_1m == 0.0
