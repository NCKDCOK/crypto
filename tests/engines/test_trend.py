"""Trend Engine V1.2 测试 — V1.2 §20。"""

from __future__ import annotations

from decimal import Decimal

from src.domain import KlineEvent, KlineInterval
from src.engines.trend import TrendEngine
from src.engines.structure import StructureResult


def _k(o, h, l, c, t, vol=10) -> KlineEvent:
    return KlineEvent(
        symbol="X", interval=KlineInterval.M15, open_time=t, close_time=t + 1,
        event_time=t, receive_time=t, open=Decimal(str(o)), high=Decimal(str(h)),
        low=Decimal(str(l)), close=Decimal(str(c)), volume=Decimal(str(vol)),
        quote_volume=Decimal("1"), trade_count=1, is_closed=True,
    )


class TestTrendEngine:
    def test_uptrend(self):
        eng = TrendEngine(slope_lookback=5)
        klines = [_k(100 + i, 102 + i, 99 + i, 101 + i, 1000 + i) for i in range(10)]
        fv = {"context_1m": 0.01, "context_5m": 0.02, "context_15m": 0.015, "context_1h": 0.01}
        r = eng.compute(fv, klines)
        assert r.trend_score is not None and r.trend_score > 50
        assert r.direction == "up"
        assert r.per_tf["5m"] == "多头"

    def test_downtrend(self):
        eng = TrendEngine(slope_lookback=5)
        klines = [_k(110 - i, 112 - i, 109 - i, 111 - i, 1000 + i) for i in range(10)]
        fv = {"context_1m": -0.01, "context_5m": -0.02, "context_15m": -0.015, "context_1h": -0.01}
        r = eng.compute(fv, klines)
        assert r.trend_score is not None and r.trend_score < 50
        assert r.direction == "down"

    def test_neutral(self):
        eng = TrendEngine()
        r = eng.compute({"context_5m": 0.0})
        if r.trend_score is not None:
            assert 30 <= r.trend_score <= 70

    def test_no_data(self):
        eng = TrendEngine()
        r = eng.compute({})
        assert r.trend_score is None
        assert r.direction == "neutral"

    def test_vwap_relation(self):
        eng = TrendEngine()
        klines = [_k(100, 105, 99, 104, 1000), _k(104, 106, 103, 105, 2000)]
        struct = StructureResult(vwap=100.0)
        r = eng.compute({}, klines, structure=struct)
        assert r.factors["vwap_score"] is not None
