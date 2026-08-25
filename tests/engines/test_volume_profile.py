"""Volume Profile 测试 — V1.2 §17。"""

from __future__ import annotations

from decimal import Decimal

from src.domain import KlineEvent, KlineInterval
from src.engines.volume_profile import VolumeProfileEngine


def _k(o, h, l, c, t, vol=10) -> KlineEvent:
    return KlineEvent(
        symbol="BTCUSDT", interval=KlineInterval.M15, open_time=t,
        close_time=t + 1, event_time=t, receive_time=t,
        open=Decimal(str(o)), high=Decimal(str(h)), low=Decimal(str(l)), close=Decimal(str(c)),
        volume=Decimal(str(vol)), quote_volume=Decimal(str(vol * c)), trade_count=100, is_closed=True,
    )


class TestVolumeProfile:
    def test_insufficient_klines(self):
        eng = VolumeProfileEngine(num_bins=10)
        r = eng.compute([_k(100, 105, 95, 102, 1000)])
        assert r.poc is None

    def test_poc_at_high_volume_zone(self):
        eng = VolumeProfileEngine(num_bins=10)
        # 大量成交集中在 100-105
        klines = [
            _k(98, 102, 96, 100, 1000, vol=5),
            _k(100, 105, 99, 103, 2000, vol=100),  # 高成交区
            _k(103, 108, 102, 105, 3000, vol=5),
        ]
        r = eng.compute(klines)
        assert r.poc is not None
        assert 99 <= r.poc <= 106

    def test_value_area(self):
        eng = VolumeProfileEngine(num_bins=20, value_area_pct=0.7)
        klines = [
            _k(95, 100, 90, 97, 1000, vol=10),
            _k(97, 105, 96, 102, 2000, vol=50),
            _k(102, 110, 100, 105, 3000, vol=10),
        ]
        r = eng.compute(klines)
        assert r.vah is not None
        assert r.val is not None
        assert r.vah > r.val

    def test_hvn_lvn(self):
        eng = VolumeProfileEngine(num_bins=20, hvn_threshold=1.5, lvn_threshold=0.5)
        klines = [
            _k(90, 95, 88, 92, 1000, vol=2),    # 低成交
            _k(92, 100, 90, 98, 2000, vol=80),  # 高成交
            _k(98, 105, 96, 102, 3000, vol=2),  # 低成交
        ]
        r = eng.compute(klines)
        assert len(r.hvn) > 0
        assert len(r.lvn) > 0

    def test_zones_merged(self):
        eng = VolumeProfileEngine(num_bins=20)
        klines = [
            _k(90, 100, 88, 95, 1000, vol=80),
            _k(95, 105, 92, 100, 2000, vol=80),
            _k(100, 110, 98, 105, 3000, vol=2),
        ]
        r = eng.compute(klines)
        assert len(r.high_volume_zones) >= 1
        for lo, hi in r.high_volume_zones:
            assert lo < hi

    def test_to_dict(self):
        eng = VolumeProfileEngine(num_bins=10)
        klines = [_k(95, 105, 90, 100, 1000, vol=10), _k(100, 110, 95, 105, 2000, vol=20)]
        r = eng.compute(klines)
        d = r.to_dict()
        assert "poc" in d and "vah" in d and "val" in d
