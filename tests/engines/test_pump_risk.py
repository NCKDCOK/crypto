"""Pump Risk + Score Engine V1.2 测试 — §41 + §24。"""

from __future__ import annotations

from src.config import ScoringConfig
from src.domain import FeatureSnapshot, FeatureValue, State
from src.engines.pump_risk import PumpRiskEngine
from src.scoring.engine import ScoreEngine


def _snap(**feats) -> FeatureSnapshot:
    features = {}
    for k, v in feats.items():
        features[k] = FeatureValue(value=v, available=v is not None, window="30s")
    return FeatureSnapshot(symbol="X", asof=0, features=features)


class TestPumpRisk:
    def test_high_pump_risk(self):
        eng = PumpRiskEngine()
        fv = {"price_return_5m": 0.2, "volume_z": 6.0, "funding": 0.001,
              "retrace_ratio": 0.7, "oi_change_5m": 0.08, "spot_delta": 100,
              "signed_delta": 50000, "price_efficiency": 0.1}
        r = eng.compute(fv)
        assert r.pump_risk_score is not None and r.pump_risk_score > 60
        assert "惩罚" in r.label

    def test_no_pump_risk(self):
        eng = PumpRiskEngine()
        r = eng.compute({"price_return_5m": 0.01, "volume_z": 1.0})
        assert r.pump_risk_score is not None and r.pump_risk_score < 40


class TestScoreEnginePumpPenalty:
    def test_pump_risk_reduces_opportunity(self):
        eng = ScoreEngine(ScoringConfig())
        snap = _snap(volume_z=5.0, oi_change_5m=0.05, signed_delta=50000, cvd_slope_z=3.0,
                     price_return_5m=0.2, funding=0.001, acceptance=0.8)
        bd_normal = eng.compute(snap, State.START_CONFIRMED, "LONG", 5, 0, 0, sample_count=20)
        bd_pump = eng.compute(snap, State.START_CONFIRMED, "LONG", 5, 0, 0, sample_count=20,
                              pump_risk_score=80.0)
        assert bd_pump.opportunity_score < bd_normal.opportunity_score
        assert bd_pump.risk_penalty > bd_normal.risk_penalty

    def test_low_pump_no_penalty(self):
        eng = ScoreEngine(ScoringConfig())
        snap = _snap(volume_z=3.0, oi_change_5m=0.03, signed_delta=5000, cvd_slope_z=2.0)
        bd_normal = eng.compute(snap, State.ANOMALY, "LONG", 3, 0, 0, sample_count=20)
        bd_low_pump = eng.compute(snap, State.ANOMALY, "LONG", 3, 0, 0, sample_count=20,
                                  pump_risk_score=30.0)
        assert bd_low_pump.opportunity_score == bd_normal.opportunity_score
