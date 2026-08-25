"""Volume 特征测试 — 手算 fixture 一致。"""

from __future__ import annotations

from decimal import Decimal

from src.domain import AggressorSide, TradeEvent
from src.features.volume_features import (
    compute_volume_features,
    compute_window_trade_count,
    compute_window_volume,
)


def _trade(symbol, trade_id, price, qty, side=AggressorSide.BUY):
    return TradeEvent(
        symbol=symbol, exchange="binance", trade_id=trade_id,
        event_time=trade_id * 1000, receive_time=trade_id * 1000,
        price=Decimal(str(price)), qty=Decimal(str(qty)),
        quote_notional=Decimal(str(price)) * Decimal(str(qty)),
        aggressor_side=side, is_maker=False,
    )


class TestComputeWindowVolume:
    def test_basic(self):
        trades = [_trade("BTCUSDT", 1, "50000", "0.1"), _trade("BTCUSDT", 2, "50000", "0.2")]
        vol = compute_window_volume(trades)
        assert abs(vol - 0.3) < 0.001

    def test_empty(self):
        assert compute_window_volume([]) == 0.0


class TestComputeWindowTradeCount:
    def test_basic(self):
        trades = [_trade("BTCUSDT", i, "50000", "0.1") for i in range(5)]
        assert compute_window_trade_count(trades) == 5

    def test_empty(self):
        assert compute_window_trade_count([]) == 0


class TestComputeVolumeFeatures:
    def test_empty_trades(self):
        """空窗口 → volume=0，rvol=0/median=0.0（非 None）。"""
        result = compute_volume_features([], [100, 200, 150], [10, 20, 15])
        assert result.rvol == 0.0  # 0 / 150 = 0.0
        assert result.volume_z is not None  # robust z of 0
        assert result.trade_count_z is not None

    def test_with_baseline(self):
        """有基线时正常计算。"""
        trades = [_trade("BTCUSDT", 1, "50000", "0.5")]
        baseline_vols = [0.1, 0.2, 0.15]  # median=0.15
        baseline_counts = [10, 20, 15]  # median=15

        result = compute_volume_features(trades, baseline_vols, baseline_counts)
        # rvol = 0.5 / 0.15 ≈ 3.33
        assert result.rvol is not None
        assert abs(result.rvol - 3.333) < 0.01

    def test_empty_baseline(self):
        """无基线 → volume_z/trade_count_z 为 None。"""
        trades = [_trade("BTCUSDT", 1, "50000", "0.1")]
        result = compute_volume_features(trades, [], [])
        assert result.volume_z is None
        assert result.trade_count_z is None
        assert result.rvol is None  # median=0 → None
