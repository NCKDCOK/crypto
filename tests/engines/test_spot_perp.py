"""Spot×Perp Confirmation 测试 — V1.2 §9。"""

from __future__ import annotations

from src.engines.spot_perp import SpotPerpConfirmationEngine


def _eng() -> SpotPerpConfirmationEngine:
    return SpotPerpConfirmationEngine()


class TestSpotPerpConfirmation:
    def test_spot_absent(self):
        eng = _eng()
        r = eng.compute({"signed_delta": 1000.0, "oi_change_5m": 0.05}, "LONG")
        assert r.classification == "spot_absent"
        assert r.spot_confirmed is False

    def test_healthy_startup(self):
        eng = _eng()
        fv = {
            "spot_taker_buy": 5000, "spot_taker_sell": 1000, "spot_delta": 4000,
            "signed_delta": 3000, "oi_change_5m": 0.05, "funding": 0.0001,
            "spot_perp_agreement": 1.0,
        }
        r = eng.compute(fv, "LONG")
        assert r.classification == "healthy"
        assert r.spot_confirmed is True
        assert "同步进入" in r.label

    def test_leverage_dominant(self):
        eng = _eng()
        # 现货极弱 vs 合约强 + OI大涨 + funding偏热
        fv = {
            "spot_taker_buy": 100, "spot_taker_sell": 80, "spot_delta": 20,
            "signed_delta": 50000, "oi_change_5m": 0.08, "funding": 0.0005,
            "spot_perp_agreement": 1.0,
        }
        r = eng.compute(fv, "LONG")
        assert r.classification == "leverage_dominant"
        assert r.leverage_dominant is True
        assert "杠杆" in r.label

    def test_disagreement_unclear(self):
        eng = _eng()
        # 现货卖、合约买 → 不一致
        fv = {
            "spot_taker_buy": 100, "spot_taker_sell": 5000, "spot_delta": -4000,
            "signed_delta": 3000, "oi_change_5m": 0.02, "funding": 0.0001,
            "spot_perp_agreement": -1.0,
        }
        r = eng.compute(fv, "LONG")
        assert r.classification == "unclear"
        assert "不一致" in r.label

    def test_short_direction(self):
        eng = _eng()
        fv = {
            "spot_taker_buy": 100, "spot_taker_sell": 5000, "spot_delta": -4000,
            "signed_delta": -3000, "oi_change_5m": 0.05, "funding": -0.0001,
            "spot_perp_agreement": 1.0,
        }
        r = eng.compute(fv, "SHORT")
        assert r.classification == "healthy"
        assert r.spot_confirmed is True
