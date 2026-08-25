"""Impulse Asymmetry 测试 — V1.2 §10。"""

from __future__ import annotations

from decimal import Decimal

from src.domain import AggressorSide, TradeEvent
from src.engines.impulse_asymmetry import ImpulseAsymmetryEngine
from src.features.impulse_features import compute_impulse_asymmetry


def _trade(tid, price, qty, side, rt):
    p = Decimal(str(price))
    q = Decimal(str(qty))
    return TradeEvent(
        symbol="X", exchange="binance", trade_id=tid, event_time=rt, receive_time=rt,
        price=p, qty=q, quote_notional=p * q, aggressor_side=side, is_maker=(side == AggressorSide.SELL),
    )


class TestImpulseFeatures:
    def test_slow_up_fast_down(self):
        # 缓涨（多次小涨）+ 急跌（一次大跌）
        trades = [
            _trade(1, "100", "1", AggressorSide.BUY, 1000),
            _trade(2, "101", "1", AggressorSide.BUY, 2000),   # +1 涨
            _trade(3, "102", "1", AggressorSide.BUY, 3000),   # +1 涨
            _trade(4, "90", "1", AggressorSide.SELL, 4000),   # -12 跌
        ]
        f = compute_impulse_asymmetry(trades)
        assert f.downside_velocity > f.upside_velocity
        assert f.impulse_ratio is not None and f.impulse_ratio > 1.0

    def test_insufficient_trades(self):
        f = compute_impulse_asymmetry([_trade(1, "100", "1", AggressorSide.BUY, 1000)])
        assert f.upside_velocity is None


class TestImpulseAsymmetryEngine:
    def test_down_dominant(self):
        eng = ImpulseAsymmetryEngine()
        fv = {
            "upside_velocity": 2.0, "downside_velocity": 20.0,
            "upside_volume_efficiency": 0.0001, "downside_volume_efficiency": 0.01,
            "impulse_ratio": 10.0,
        }
        r = eng.compute(fv)
        assert r.dominant_side == "down"
        assert "空头" in r.label

    def test_unknown_when_no_data(self):
        eng = ImpulseAsymmetryEngine()
        r = eng.compute({})
        assert r.dominant_side == "unknown"
        assert r.upside_efficiency_score is None
