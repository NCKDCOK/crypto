"""Structure Engine 测试 — V1.2 §16。"""

from __future__ import annotations

from decimal import Decimal

from src.domain import KlineEvent, KlineInterval
from src.engines.structure import StructureEngine


def _kline(o, h, l, c, open_time, vol=10) -> KlineEvent:
    return KlineEvent(
        symbol="BTCUSDT", interval=KlineInterval.M15, open_time=open_time,
        close_time=open_time + 900_000, event_time=open_time, receive_time=open_time,
        open=Decimal(str(o)), high=Decimal(str(h)), low=Decimal(str(l)), close=Decimal(str(c)),
        volume=Decimal(str(vol)), quote_volume=Decimal(str(vol * c)), trade_count=100, is_closed=True,
    )


class TestStructureEngine:
    def test_insufficient_klines(self):
        eng = StructureEngine(swing_window=2)
        r = eng.compute([_kline(100, 105, 99, 102, 1000)])
        assert r.local_high is None
        assert r.swing_highs == []

    def test_swing_detection(self):
        eng = StructureEngine(swing_window=1)
        # 构造一个明显的高点在中间
        klines = [
            _kline(100, 102, 99, 101, 1000),
            _kline(101, 103, 100, 102, 2000),
            _kline(102, 110, 101, 105, 3000),  # swing high
            _kline(105, 107, 103, 104, 4000),
            _kline(104, 106, 102, 103, 5000),
        ]
        r = eng.compute(klines)
        assert len(r.swing_highs) >= 1
        assert r.swing_highs[0].price == 110.0

    def test_vwap(self):
        eng = StructureEngine(swing_window=2)
        klines = [
            _kline(100, 105, 95, 100, 1000, vol=10),
            _kline(100, 110, 100, 105, 2000, vol=20),
        ]
        r = eng.compute(klines)
        assert r.vwap is not None and r.vwap > 0

    def test_atr(self):
        eng = StructureEngine(swing_window=2, atr_period=5)
        klines = [
            _kline(100, 105, 95, 100, 1000),
            _kline(100, 108, 98, 102, 2000),
            _kline(102, 110, 100, 105, 3000),
        ]
        r = eng.compute(klines)
        assert r.atr is not None and r.atr > 0

    def test_local_high_low(self):
        eng = StructureEngine(swing_window=2)
        klines = [_kline(100 + i, 102 + i, 99 + i, 101 + i, 1000 + i * 1000) for i in range(10)]
        r = eng.compute(klines)
        assert r.local_high is not None
        assert r.local_low is not None
        assert r.local_high > r.local_low

    def test_resistance_support_from_swings(self):
        eng = StructureEngine(swing_window=1)
        klines = [
            _kline(100, 102, 98, 100, 1000),
            _kline(100, 103, 97, 101, 2000),  # swing low 97
            _kline(101, 108, 100, 105, 3000),  # swing high 108
            _kline(105, 107, 103, 104, 4000),
            _kline(104, 106, 102, 103, 5000),
        ]
        r = eng.compute(klines)
        assert r.resistance is not None
        assert r.support is not None

    def test_breakout_level_and_retest_zone(self):
        eng = StructureEngine(swing_window=1, atr_period=3)
        klines = [
            _kline(100, 102, 98, 100, 1000),
            _kline(100, 103, 97, 101, 2000),
            _kline(101, 108, 100, 105, 3000),
            _kline(105, 107, 103, 104, 4000),
            _kline(104, 106, 102, 103, 5000),
        ]
        r = eng.compute(klines)
        assert r.breakout_level is not None
        assert r.retest_zone_low is not None
        assert r.retest_zone_high is not None
        assert r.retest_zone_low < r.retest_zone_high

    def test_structure_sequence(self):
        eng = StructureEngine(swing_window=1)
        # 振荡上升：明确 swing，最后一根留作 context
        klines = [
            _kline(100, 102, 98, 101, 1000),
            _kline(101, 108, 100, 105, 2000),   # swing high 108
            _kline(105, 106, 99, 100, 3000),     # swing low 99
            _kline(100, 115, 101, 112, 4000),   # swing high 115 (HH)
            _kline(112, 113, 95, 96, 5000),      # swing low 95 (LL: 95 < 99)
            _kline(96, 116, 95, 110, 6000),      # context
        ]
        r = eng.compute(klines)
        assert len(r.structure_sequence) > 0
        assert "HH" in r.structure_sequence
        assert "LL" in r.structure_sequence
