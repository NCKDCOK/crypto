"""Accumulation / Dormant Revival / Distribution 引擎测试 — V1.2 §11-13。"""

from __future__ import annotations

from src.engines.accumulation import AccumulationEngine
from src.engines.dormant_revival import DormantRevivalEngine
from src.engines.distribution import DistributionEngine


class TestAccumulation:
    def test_sell_absorption_high(self):
        eng = AccumulationEngine()
        # LONG：大量主动卖（delta<0）但价格效率低 → 承接
        fv = {"signed_delta": -50000, "price_efficiency": 0.1, "retrace_ratio": 0.2,
              "oi_change_5m": 0.02, "cvd_slope_z": -2.0, "acceptance": 0.8,
              "spot_delta": 5000, "price_return_5m": 0.001}
        r = eng.compute(fv, "LONG")
        assert r.absorption_score is not None and r.absorption_score > 70
        assert r.accumulation_score is not None and r.accumulation_score > 50

    def test_no_absorption(self):
        eng = AccumulationEngine()
        fv = {"signed_delta": 50000, "price_efficiency": 0.8, "retrace_ratio": 0.1,
              "oi_change_5m": 0.02, "acceptance": 0.9}
        r = eng.compute(fv, "LONG")
        assert r.absorption_score is not None and r.absorption_score < 50

    def test_insufficient_data(self):
        eng = AccumulationEngine()
        r = eng.compute({}, "LONG")
        assert r.accumulation_score is None


class TestDormantRevival:
    def test_revival_detected(self):
        eng = DormantRevivalEngine()
        # 成交活跃 + OI转正 + 价格未大涨
        fv = {"volume_z": 3.0, "trade_count_z": 2.5, "oi_change_5m": 0.01,
              "price_return_5m": 0.005, "spot_delta": 1000}
        r = eng.compute(fv)
        assert r.revival_score is not None and r.revival_score >= 75
        assert "活跃" in r.label

    def test_already_pumped(self):
        eng = DormantRevivalEngine()
        fv = {"volume_z": 3.0, "oi_change_5m": 0.01, "price_return_5m": 0.08}
        r = eng.compute(fv)
        assert r.revival_score is not None and r.revival_score < 30


class TestDistribution:
    def test_distribution_risk_high(self):
        eng = DistributionEngine()
        # 高量低效 + CVD强但价不动 + OI衰减 + 突破失败 + 现货卖
        fv = {"volume_z": 5.0, "price_efficiency": 0.1, "cvd_slope_z": 2.5,
              "oi_change_5m": -0.03, "acceptance": 0.2, "spot_delta": -5000,
              "signed_delta": 30000, "price_return_5m": 0.001}
        r = eng.compute(fv, "LONG")
        assert r.distribution_risk_score is not None and r.distribution_risk_score > 50
        assert "卖压" in r.label or "派发" in r.label

    def test_no_distribution(self):
        eng = DistributionEngine()
        fv = {"volume_z": 1.0, "price_efficiency": 0.8, "acceptance": 0.9,
              "oi_change_5m": 0.03, "signed_delta": 10000, "price_return_5m": 0.02}
        r = eng.compute(fv, "LONG")
        assert r.distribution_risk_score is not None and r.distribution_risk_score < 40
