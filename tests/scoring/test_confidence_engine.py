"""Confidence Engine 测试 — 依据 V1.1 计划 §三十五。"""

from __future__ import annotations

from src.config import ScoringConfig
from src.domain import ConfidenceState, FeatureSnapshot, FeatureValue
from src.scoring.confidence import ConfidenceEngine


def _make_snap(features: dict[str, float | None]) -> FeatureSnapshot:
    feats = {}
    for k, v in features.items():
        feats[k] = FeatureValue(value=v, available=v is not None, window="30s")
    return FeatureSnapshot(symbol="TESTUSDT", asof=0, features=feats)


def _engine() -> ConfidenceEngine:
    return ConfidenceEngine(ScoringConfig())


class TestConfidenceRange:
    def test_confidence_in_range(self):
        eng = _engine()
        snap = _make_snap({"oi_contracts": 100, "funding": 0.001})
        bd = eng.compute(ConfidenceState.CONFIDENT, snap, 5, sample_count=20)
        assert 0.0 <= bd.confidence <= 1.0

    def test_confident_high(self):
        eng = _engine()
        snap = _make_snap({"oi_contracts": 100, "funding": 0.001})
        bd = eng.compute(ConfidenceState.CONFIDENT, snap, 5, sample_count=20)
        assert bd.confidence > 0.8


class TestStaleData:
    """stale data → confidence drop。"""

    def test_unknown_drops_confidence(self):
        eng = _engine()
        snap = _make_snap({"oi_contracts": 100, "funding": 0.001})
        bd = eng.compute(ConfidenceState.UNKNOWN, snap, 5, sample_count=20)
        assert bd.confidence < 0.7
        assert any("STALE" in p or "FAIL" in p for p in bd.penalties)

    def test_degraded_drops_confidence(self):
        eng = _engine()
        snap = _make_snap({"oi_contracts": 100, "funding": 0.001})
        bd_confident = eng.compute(ConfidenceState.CONFIDENT, snap, 5, sample_count=20)
        bd_degraded = eng.compute(ConfidenceState.DEGRADED, snap, 5, sample_count=20)
        assert bd_degraded.confidence < bd_confident.confidence


class TestMissingOI:
    """missing OI → confidence drop。"""

    def test_missing_oi_drops_confidence(self):
        eng = _engine()
        snap_with_oi = _make_snap({"oi_contracts": 100, "funding": 0.001})
        snap_no_oi = _make_snap({"oi_contracts": None, "funding": 0.001})
        bd_with = eng.compute(ConfidenceState.CONFIDENT, snap_with_oi, 5, sample_count=20)
        bd_without = eng.compute(ConfidenceState.CONFIDENT, snap_no_oi, 5, sample_count=20)
        assert bd_without.confidence < bd_with.confidence
        assert any("OI" in p for p in bd_without.penalties)


class TestLowEvidence:
    """证据不足 → confidence drop。"""

    def test_low_evidence_drops_confidence(self):
        eng = _engine()
        snap = _make_snap({"oi_contracts": 100, "funding": 0.001})
        bd_high = eng.compute(ConfidenceState.CONFIDENT, snap, 10, sample_count=20)
        bd_low = eng.compute(ConfidenceState.CONFIDENT, snap, 1, sample_count=20)
        assert bd_low.confidence < bd_high.confidence


class TestWarmup:
    def test_warmup_zero_confidence(self):
        eng = _engine()
        snap = _make_snap({"oi_contracts": 100, "funding": 0.001})
        bd = eng.compute(ConfidenceState.CONFIDENT, snap, 5, sample_count=3)
        assert not bd.available
        assert bd.confidence == 0.0


class TestIndependentFromScore:
    """置信度独立于机会分。"""

    def test_confidence_does_not_depend_on_score(self):
        eng = _engine()
        # Same data health → same confidence regardless of "opportunity"
        snap = _make_snap({"oi_contracts": 100, "funding": 0.001})
        bd1 = eng.compute(ConfidenceState.CONFIDENT, snap, 5, sample_count=20)
        # Different evidence count but same health
        bd2 = eng.compute(ConfidenceState.CONFIDENT, snap, 10, sample_count=20)
        # Confidence should be similar (only evidence count differs slightly)
        assert abs(bd1.confidence - bd2.confidence) < 0.2
