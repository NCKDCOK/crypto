"""Score Engine 测试 — 依据 V1.1 计划 §三十五。"""

from __future__ import annotations

import pytest

from src.config import ScoringConfig
from src.domain import ConfidenceState, FeatureSnapshot, FeatureValue, State
from src.scoring.engine import ScoreEngine


def _make_snap(features: dict[str, float | None]) -> FeatureSnapshot:
    """构建测试用 FeatureSnapshot。"""
    feats = {}
    for k, v in features.items():
        feats[k] = FeatureValue(value=v, available=v is not None, window="30s")
    return FeatureSnapshot(symbol="TESTUSDT", asof=0, features=feats)


def _engine() -> ScoreEngine:
    return ScoreEngine(ScoringConfig())


class TestScoreRange:
    """score range 0~100。"""

    def test_score_in_range(self):
        eng = _engine()
        snap = _make_snap({"volume_z": 5.0, "trade_count_z": 4.0, "oi_change_5m": 0.05})
        bd = eng.compute(snap, State.START_CONFIRMED, "LONG", 5, 0, 0, sample_count=20)
        assert bd.available
        assert 0 <= bd.opportunity_score <= 100

    def test_all_subscores_in_range(self):
        eng = _engine()
        snap = _make_snap({"volume_z": 5.0, "trade_count_z": 4.0})
        bd = eng.compute(snap, State.ANOMALY, "LONG", 3, 0, 0, sample_count=20)
        for ss in bd.subscores.values():
            assert 0 <= ss.score <= 100

    def test_extreme_values_no_overflow(self):
        """极端值不应导致 OverflowError（math.exp 溢出）。"""
        eng = _engine()
        snap = _make_snap({
            "volume_z": 99999.0,
            "trade_count_z": -99999.0,
            "cvd_slope_z": 99999.0,
            "cvd_accel_z": -99999.0,
            "signed_delta": 999999999.0,
            "oi_change_5m": 999.0,
            "price_acceleration": 99999.0,
            "price_efficiency": 999.0,
            "price_return_30s": 999.0,
            "price_return_5m": 999.0,
            "funding": 999.0,
            "funding_percentile": 999.0,
            "acceptance": 999.0,
            "retrace_ratio": 999.0,
            "context_1m": 999.0,
            "context_5m": 999.0,
            "context_15m": 999.0,
            "context_1h": 999.0,
        })
        # 不应抛出 OverflowError
        bd = eng.compute(snap, State.START_CONFIRMED, "LONG", 5, 0, 0, sample_count=20)
        assert bd.available
        assert 0 <= bd.opportunity_score <= 100


class TestWarmup:
    """no evidence → no fake high score（评分预热）。"""

    def test_warmup_not_available(self):
        eng = _engine()
        snap = _make_snap({"volume_z": 10.0})
        bd = eng.compute(snap, State.ANOMALY, "LONG", 0, 0, 0, sample_count=3)
        assert not bd.available
        assert bd.opportunity_score == 0.0

    def test_warmup_available(self):
        eng = _engine()
        snap = _make_snap({"volume_z": 3.0})
        bd = eng.compute(snap, State.ANOMALY, "LONG", 3, 0, 0, sample_count=15)
        assert bd.available


class TestStrongStartup:
    """strong startup fixture → high startup score。"""

    def test_strong_startup_high_score(self):
        eng = _engine()
        snap = _make_snap({
            "volume_z": 5.0,
            "trade_count_z": 4.5,
            "price_acceleration": 0.8,
            "oi_change_1m": 0.04,
            "cvd_slope_z": 3.5,
            "acceptance": 0.9,
        })
        bd = eng.compute(snap, State.START_CONFIRMED, "LONG", 5, 0, 0, sample_count=20)
        assert bd.available
        sq = bd.subscores["startup_quality"]
        assert sq.score > 70, f"startup_quality should be high, got {sq.score}"

    def test_strong_capital_inflow(self):
        eng = _engine()
        snap = _make_snap({
            "oi_change_5m": 0.05,
            "signed_delta": 50000,
            "cvd_slope_z": 3.5,
            "cvd_accel_z": 2.5,
            "price_return_30s": 0.3,
        })
        bd = eng.compute(snap, State.START_CONFIRMED, "LONG", 5, 0, 0, sample_count=20)
        ci = bd.subscores["capital_inflow"]
        assert ci.score > 70, f"capital_inflow should be high, got {ci.score}"


class TestRiskScores:
    """风险分独立展示。"""

    def test_withdrawal_risk_high_on_oi_decay(self):
        eng = _engine()
        snap = _make_snap({
            "oi_change_5m": -0.05,
            "signed_delta": -30000,
            "cvd_slope_z": -3.0,
            "price_efficiency": 0.1,
            "acceptance": 0.2,
        })
        bd = eng.compute(snap, State.WITHDRAWAL, "LONG", 3, 0, 0, sample_count=20)
        wr = bd.subscores["withdrawal_risk"]
        assert wr.score > 50, f"withdrawal_risk should be high, got {wr.score}"
        assert wr.is_risk

    def test_top_risk_high_on_divergence(self):
        eng = _engine()
        # LONG direction but CVD going negative = divergence
        snap = _make_snap({
            "cvd_slope_z": -3.0,
            "volume_z": 4.0,
            "price_efficiency": 0.1,
            "oi_change_5m": -0.02,
            "acceptance": 0.2,
        })
        bd = eng.compute(snap, State.EXHAUSTION, "LONG", 3, 0, 0, sample_count=20)
        tr = bd.subscores["top_risk"]
        assert tr.score > 40, f"top_risk should be elevated, got {tr.score}"
        assert tr.is_risk

    def test_risk_penalty_reduces_opportunity(self):
        eng = _engine()
        # High base scores but also high risk
        snap = _make_snap({
            "volume_z": 5.0,
            "trade_count_z": 4.0,
            "oi_change_5m": 0.05,
            "cvd_slope_z": 3.0,
            "signed_delta": 50000,
            # Risk factors
            "funding": 0.03,
            "funding_percentile": 95,
            "premium_percentile": 90,
            "price_return_5m": 0.15,
            "retrace_ratio": 0.7,
        })
        bd = eng.compute(snap, State.CONTINUATION, "LONG", 5, 0, 0, sample_count=20)
        assert bd.risk_penalty > 0
        assert bd.opportunity_score < bd.base_score


class TestBreakdown:
    """每个分数可展开 Evidence。"""

    def test_breakdown_has_components(self):
        eng = _engine()
        snap = _make_snap({"volume_z": 3.0, "oi_change_5m": 0.02})
        bd = eng.compute(snap, State.ANOMALY, "LONG", 3, 0, 0, sample_count=20)
        d = bd.to_dict()
        assert "subscores" in d
        for key, ss in d["subscores"].items():
            assert "components" in ss
            assert "score" in ss
            assert "label" in ss

    def test_config_weights_used(self):
        """权重全部放配置文件。"""
        cfg = ScoringConfig(w_capital_inflow=0.5, w_startup_quality=0.3, w_trend=0.2)
        eng = ScoreEngine(cfg)
        assert eng.cfg.w_capital_inflow == 0.5
