"""Market Regime Engine 测试 — V1.2 §8。"""

from __future__ import annotations

from src.config import MarketRegimeConfig
from src.market.regime import MarketRegimeEngine, MarketSnapshot, REGIME_LABELS


def _eng() -> MarketRegimeEngine:
    return MarketRegimeEngine(MarketRegimeConfig())


class TestRegimeClassification:
    def test_alt_risk_on(self):
        eng = _eng()
        snap = MarketSnapshot(
            btc_return_1h=0.005, eth_return_1h=0.01,
            breadth_up=70, breadth_down=30,
            anomaly_ratio=0.2, oi_expansion_ratio=0.6, oi_contraction_ratio=0.2,
        )
        r = eng.compute(snap)
        assert r.regime == "ALT_RISK_ON"
        assert r.label == "山寨偏强"

    def test_alt_risk_off(self):
        eng = _eng()
        snap = MarketSnapshot(
            btc_return_1h=-0.005,
            breadth_up=30, breadth_down=70,
            anomaly_ratio=0.05, oi_expansion_ratio=0.2, oi_contraction_ratio=0.6,
        )
        r = eng.compute(snap)
        assert r.regime == "ALT_RISK_OFF"

    def test_btc_dominant(self):
        eng = _eng()
        snap = MarketSnapshot(
            btc_return_1h=0.03, eth_return_1h=0.005,
            breadth_up=50, breadth_down=50,
        )
        r = eng.compute(snap)
        assert r.regime == "BTC_DOMINANT"

    def test_deleveraging(self):
        eng = _eng()
        snap = MarketSnapshot(
            btc_return_1h=-0.02,
            breadth_up=20, breadth_down=80,
            oi_contraction_ratio=0.5, oi_expansion_ratio=0.1,
        )
        r = eng.compute(snap)
        assert r.regime == "DELEVERAGING"

    def test_panic_overrides(self):
        eng = _eng()
        snap = MarketSnapshot(
            btc_return_1h=-0.04,
            breadth_up=10, breadth_down=90,
        )
        r = eng.compute(snap)
        assert r.regime == "PANIC"

    def test_chop(self):
        eng = _eng()
        snap = MarketSnapshot(
            btc_return_1h=0.0,
            breadth_up=50, breadth_down=50,
            anomaly_ratio=0.02,
        )
        r = eng.compute(snap)
        assert r.regime == "CHOP"

    def test_neutral_default(self):
        eng = _eng()
        snap = MarketSnapshot(breadth_up=52, breadth_down=48, anomaly_ratio=0.08)
        r = eng.compute(snap)
        assert r.regime in ("NEUTRAL", "CHOP", "ALT_RISK_ON")

    def test_panic_priority_over_deleverage(self):
        eng = _eng()
        snap = MarketSnapshot(
            btc_return_1h=-0.05, breadth_up=5, breadth_down=95,
            oi_contraction_ratio=0.8,
        )
        r = eng.compute(snap)
        assert r.regime == "PANIC"


class TestRegimeOutput:
    def test_detail_and_label(self):
        eng = _eng()
        snap = MarketSnapshot(
            btc_return_1h=0.005, breadth_up=70, breadth_down=30,
            anomaly_ratio=0.2, oi_expansion_ratio=0.6, oi_contraction_ratio=0.2,
        )
        r = eng.compute(snap)
        assert r.label in REGIME_LABELS.values()
        assert "BTC" in r.detail
        d = r.to_dict()
        assert "regime" in d and "factors" in d
