"""Efficiency 特征测试 — 手工验证 directional_efficiency / flow_impact。"""

from __future__ import annotations

from decimal import Decimal

from src.domain import AggressorSide, TradeEvent
from src.features.efficiency_features import (
    compute_directional_efficiency,
    compute_efficiency_features,
    compute_flow_impact,
    compute_retrace_ratio,
)


def _trade(trade_id, price, qty, side, receive_time=None):
    rt = receive_time if receive_time is not None else trade_id * 1000
    return TradeEvent(
        symbol="BTCUSDT", exchange="binance", trade_id=trade_id,
        event_time=rt, receive_time=rt,
        price=Decimal(str(price)), qty=Decimal(str(qty)),
        quote_notional=Decimal(str(price)) * Decimal(str(qty)),
        aggressor_side=side, is_maker=(side == AggressorSide.SELL),
    )


class TestDirectionalEfficiency:
    def test_pure_trend(self):
        """纯趋势（价格单调上涨）→ efficiency=1.0。"""
        trades = [
            _trade(1, "100", "0.1", AggressorSide.BUY, 1000),
            _trade(2, "110", "0.1", AggressorSide.BUY, 2000),
            _trade(3, "120", "0.1", AggressorSide.BUY, 3000),
        ]
        eff = compute_directional_efficiency(trades)
        # |120-100| / (|110-100| + |120-110|) = 20 / 20 = 1.0
        assert eff == 1.0

    def test_back_and_forth(self):
        """来回震荡 → efficiency 接近 0。"""
        trades = [
            _trade(1, "100", "0.1", AggressorSide.BUY, 1000),
            _trade(2, "120", "0.1", AggressorSide.SELL, 2000),
            _trade(3, "100", "0.1", AggressorSide.BUY, 3000),
        ]
        eff = compute_directional_efficiency(trades)
        # |100-100| / (|120-100| + |100-120|) = 0 / 40 = 0
        assert eff == 0.0

    def test_partial_trend(self):
        """部分趋势。"""
        trades = [
            _trade(1, "100", "0.1", AggressorSide.BUY, 1000),
            _trade(2, "110", "0.1", AggressorSide.BUY, 2000),
            _trade(3, "105", "0.1", AggressorSide.SELL, 3000),
        ]
        eff = compute_directional_efficiency(trades)
        # |105-100| / (|110-100| + |105-110|) = 5 / 15 = 0.333
        assert abs(eff - 0.333) < 0.01

    def test_empty(self):
        assert compute_directional_efficiency([]) is None

    def test_single(self):
        assert compute_directional_efficiency([_trade(1, "100", "0.1", AggressorSide.BUY)]) is None


class TestFlowImpact:
    def test_basic(self):
        """flow_impact = signed_return / max(|net_taker_notional|, ε)。"""
        trades = [
            _trade(1, "100", "0.1", AggressorSide.BUY, 1000),
            _trade(2, "110", "0.1", AggressorSide.BUY, 2000),
        ]
        # signed_return = (110-100)/100 = 0.1
        # net_taker = 0.1*100 + 0.1*110 = 21
        # flow_impact = 0.1 / 21 = 0.00476
        fi = compute_flow_impact(trades, epsilon=1.0)
        assert fi is not None
        assert abs(fi - 0.00476) < 0.001

    def test_epsilon_when_notional_small(self):
        """net_taker_notional 极小时用 ε 保护。"""
        trades = [
            _trade(1, "100", "0.00001", AggressorSide.BUY, 1000),
            _trade(2, "101", "0.00001", AggressorSide.SELL, 2000),
        ]
        # net_taker ≈ 0.001 - 0.00101 ≈ -0.00001 → max(|net|, 1.0) = 1.0
        fi = compute_flow_impact(trades, epsilon=1.0)
        assert fi is not None  # 不抛异常

    def test_empty(self):
        assert compute_flow_impact([]) is None


class TestRetraceRatio:
    def test_no_retrace(self):
        """无回吐 → ratio 接近 0。"""
        trades = [
            _trade(1, "100", "0.1", AggressorSide.BUY, 1000),
            _trade(2, "120", "0.1", AggressorSide.BUY, 2000),
            _trade(3, "120", "0.1", AggressorSide.BUY, 3000),
        ]
        ratio = compute_retrace_ratio(trades)
        # breakout=20 (100→120), retrace = 120-120 = 0
        assert ratio is not None
        assert abs(ratio) < 0.01

    def test_full_retrace(self):
        """完整回吐 → ratio=1.0。"""
        trades = [
            _trade(1, "100", "0.1", AggressorSide.BUY, 1000),
            _trade(2, "120", "0.1", AggressorSide.BUY, 2000),
            _trade(3, "100", "0.1", AggressorSide.SELL, 3000),
        ]
        ratio = compute_retrace_ratio(trades)
        # breakout=20, retrace=120-100=20 → 20/20=1.0
        assert abs(ratio - 1.0) < 0.01

    def test_empty(self):
        assert compute_retrace_ratio([]) is None


class TestComputeEfficiencyFeatures:
    def test_all_present(self):
        trades = [
            _trade(1, "100", "0.1", AggressorSide.BUY, 1000),
            _trade(2, "110", "0.1", AggressorSide.BUY, 2000),
        ]
        result = compute_efficiency_features(trades)
        assert result.directional_efficiency is not None
        assert result.flow_impact is not None

    def test_empty_all_none(self):
        """缺数据 → 全部 None。"""
        result = compute_efficiency_features([])
        assert result.directional_efficiency is None
        assert result.flow_impact is None
        assert result.retrace_ratio is None
