"""Data Confidence + Signal Confirmation 测试 — 依据 V1.2 计划 §3-§5。

V1.2 将单一「置信度」拆为：
- data_confidence（数据可信度，§3.3）
- signal_confirmation（信号确认度，§3.2）

§5 缺失数据规则：不得默认 50；缺失从分母移除；降低 coverage。
"""

from __future__ import annotations

from src.config import ScoringConfig
from src.domain import ConfidenceState, FeatureSnapshot, FeatureValue
from src.scoring.data_confidence import DataConfidenceEngine
from src.scoring.signal_confirmation import (
    ConfirmationContext,
    SignalConfirmationEngine,
)


def _make_snap(features: dict[str, float | None]) -> FeatureSnapshot:
    feats = {}
    for k, v in features.items():
        feats[k] = FeatureValue(value=v, available=v is not None, window="30s")
    return FeatureSnapshot(symbol="TESTUSDT", asof=0, features=feats)


def _dc_engine() -> DataConfidenceEngine:
    return DataConfidenceEngine(ScoringConfig())


def _sc_engine() -> SignalConfirmationEngine:
    return SignalConfirmationEngine(ScoringConfig())


# ── Data Confidence ──


class TestDataConfidenceRange:
    def test_score_in_range(self):
        eng = _dc_engine()
        snap = _make_snap({"oi_contracts": 100, "funding": 0.001,
                           "context_1m": 0.01, "stale_flag": 0})
        bd = eng.compute(ConfidenceState.CONFIDENT, snap, sample_count=20)
        assert bd.available
        assert 0.0 <= bd.score <= 100.0

    def test_confident_high(self):
        eng = _dc_engine()
        snap = _make_snap({"oi_contracts": 100, "funding": 0.001,
                           "context_1m": 0.01, "stale_flag": 0})
        bd = eng.compute(ConfidenceState.CONFIDENT, snap, sample_count=20)
        assert bd.score > 80


class TestDataConfidenceStale:
    def test_unknown_drops_score(self):
        eng = _dc_engine()
        snap = _make_snap({"oi_contracts": 100, "funding": 0.001,
                           "context_1m": 0.01, "stale_flag": 0})
        bd = eng.compute(ConfidenceState.UNKNOWN, snap, sample_count=20)
        assert bd.score < 70
        assert any("STALE" in p or "FAIL" in p for p in bd.penalties)

    def test_degraded_drops_score(self):
        eng = _dc_engine()
        snap = _make_snap({"oi_contracts": 100, "funding": 0.001,
                           "context_1m": 0.01, "stale_flag": 0})
        bd_confident = eng.compute(ConfidenceState.CONFIDENT, snap, sample_count=20)
        bd_degraded = eng.compute(ConfidenceState.DEGRADED, snap, sample_count=20)
        assert bd_degraded.score < bd_confident.score


class TestDataConfidenceMissing:
    """§5：缺失数据不默认 50，降 coverage，降分。"""

    def test_missing_oi_drops_score_and_coverage(self):
        eng = _dc_engine()
        snap_full = _make_snap({"oi_contracts": 100, "funding": 0.001,
                                "context_1m": 0.01, "stale_flag": 0})
        snap_no_oi = _make_snap({"oi_contracts": None, "funding": 0.001,
                                 "context_1m": 0.01, "stale_flag": 0})
        bd_full = eng.compute(ConfidenceState.CONFIDENT, snap_full, sample_count=20)
        bd_no = eng.compute(ConfidenceState.CONFIDENT, snap_no_oi, sample_count=20)
        assert bd_no.score < bd_full.score
        assert "oi" in bd_no.missing
        assert bd_no.coverage < bd_full.coverage

    def test_missing_no_default_50(self):
        """缺失不得产生 50 的中位分。"""
        eng = _dc_engine()
        snap_empty = _make_snap({"oi_contracts": None, "funding": None,
                                 "context_1m": None, "stale_flag": 1})
        bd = eng.compute(ConfidenceState.UNKNOWN, snap_empty, sample_count=20)
        # 全缺 + stale → 分数远低于 50
        assert bd.score < 50
        assert len(bd.missing) >= 3

    def test_coverage_reflects_sources(self):
        eng = _dc_engine()
        snap = _make_snap({"oi_contracts": 100, "funding": 0.001,
                           "context_1m": 0.01, "stale_flag": 0})
        bd = eng.compute(ConfidenceState.CONFIDENT, snap, sample_count=20,
                         spot_available=True)
        # 4 核心 + spot = 5 源全可用 → coverage 1.0
        assert bd.coverage == 1.0
        bd2 = eng.compute(ConfidenceState.CONFIDENT, snap, sample_count=20,
                          spot_available=False)
        assert "spot" in bd2.missing
        assert bd2.coverage < bd.coverage


class TestDataConfidenceWarmup:
    def test_warmup_unavailable(self):
        eng = _dc_engine()
        snap = _make_snap({"oi_contracts": 100, "funding": 0.001})
        bd = eng.compute(ConfidenceState.CONFIDENT, snap, sample_count=3)
        assert not bd.available
        assert bd.score == 0.0


# ── Signal Confirmation ──


class TestSignalConfirmationRange:
    def test_score_in_range(self):
        eng = _sc_engine()
        snap = _make_snap({"oi_change_5m": 0.05, "signed_delta": 1000.0,
                           "cvd_slope_z": 2.0, "volume_z": 3.0,
                           "acceptance": 0.8, "retrace_ratio": 0.2,
                           "context_1m": 0.01, "context_5m": 0.02,
                           "context_15m": 0.01, "context_1h": 0.005})
        ctx = ConfirmationContext(direction="LONG", evidence_count=5, veto_count=0)
        bd = eng.compute(snap, ctx, sample_count=20)
        assert bd.available
        assert 0.0 <= bd.score <= 100.0

    def test_all_core_pass_high(self):
        eng = _sc_engine()
        snap = _make_snap({"oi_change_5m": 0.05, "signed_delta": 1000.0,
                           "cvd_slope_z": 2.0, "volume_z": 3.0,
                           "acceptance": 0.8, "retrace_ratio": 0.2,
                           "context_15m": 0.01})
        ctx = ConfirmationContext(direction="LONG", evidence_count=5, veto_count=0)
        bd = eng.compute(snap, ctx, sample_count=20)
        assert bd.core_passed == bd.core_total == 3
        assert bd.score > 70


class TestSignalConfirmationVeto:
    def test_veto_lowers_score(self):
        eng = _sc_engine()
        snap = _make_snap({"oi_change_5m": 0.05, "signed_delta": 1000.0,
                           "cvd_slope_z": 2.0})
        ctx_ok = ConfirmationContext(direction="LONG", evidence_count=5, veto_count=0)
        ctx_veto = ConfirmationContext(direction="LONG", evidence_count=5, veto_count=1)
        bd_ok = eng.compute(snap, ctx_ok, sample_count=20)
        bd_veto = eng.compute(snap, ctx_veto, sample_count=20)
        assert bd_ok.veto_passed
        assert not bd_veto.veto_passed
        assert bd_veto.score < bd_ok.score


class TestSignalConfirmationMissing:
    """§5：缺失证据从分母移除，不默认通过/不默认 50。"""

    def test_missing_core_excluded_from_denominator(self):
        eng = _sc_engine()
        snap_full = _make_snap({"oi_change_5m": 0.05, "signed_delta": 1000.0,
                                "cvd_slope_z": 2.0})
        snap_partial = _make_snap({"oi_change_5m": 0.05})  # 缺 delta/cvd
        ctx = ConfirmationContext(direction="LONG", evidence_count=3, veto_count=0)
        bd_full = eng.compute(snap_full, ctx, sample_count=20)
        bd_partial = eng.compute(snap_partial, ctx, sample_count=20)
        assert bd_full.core_total == 3
        assert bd_partial.core_total == 1
        # 缺失项不应让分默认成 50；通过的那项仍计 1/1
        assert bd_partial.core_passed == 1

    def test_no_evidence_no_strong_confirm(self):
        eng = _sc_engine()
        snap = _make_snap({})
        ctx = ConfirmationContext(direction="LONG", evidence_count=0, veto_count=0)
        bd = eng.compute(snap, ctx, sample_count=20,
                         data_confidence_score=90.0)
        assert not bd.strong_confirm


class TestSignalConfirmationStrongConfirm:
    def test_strong_confirm_requires_dc_and_multitf(self):
        eng = _sc_engine()
        snap = _make_snap({"oi_change_5m": 0.05, "signed_delta": 1000.0,
                           "cvd_slope_z": 2.0,
                           "context_1m": 0.01, "context_5m": 0.02,
                           "context_15m": 0.01, "context_1h": 0.005})
        ctx = ConfirmationContext(direction="LONG", evidence_count=5, veto_count=0)
        # DC 不足 → 不强确认
        bd_low = eng.compute(snap, ctx, sample_count=20, data_confidence_score=50.0)
        assert not bd_low.strong_confirm
        # DC 足够 + 多周期对齐 → 强确认
        bd_high = eng.compute(snap, ctx, sample_count=20, data_confidence_score=90.0)
        assert bd_high.strong_confirm

    def test_strong_confirm_blocked_by_veto(self):
        eng = _sc_engine()
        snap = _make_snap({"oi_change_5m": 0.05, "signed_delta": 1000.0,
                           "cvd_slope_z": 2.0,
                           "context_1m": 0.01, "context_5m": 0.02})
        ctx = ConfirmationContext(direction="LONG", evidence_count=5, veto_count=1)
        bd = eng.compute(snap, ctx, sample_count=20, data_confidence_score=90.0)
        assert not bd.strong_confirm


class TestSignalConfirmationWarmup:
    def test_warmup_unavailable(self):
        eng = _sc_engine()
        snap = _make_snap({"oi_change_5m": 0.05})
        ctx = ConfirmationContext(direction="LONG", evidence_count=1, veto_count=0)
        bd = eng.compute(snap, ctx, sample_count=3)
        assert not bd.available
        assert bd.score == 0.0


class TestSignalConfirmationExtensibility:
    """后续 Phase 注入字段（breakout_hold/retest/spot_agreement）生效。"""

    def test_breakout_hold_injected(self):
        eng = _sc_engine()
        snap = _make_snap({"oi_change_5m": 0.05, "signed_delta": 1000.0,
                           "cvd_slope_z": 2.0})
        ctx = ConfirmationContext(
            direction="LONG", evidence_count=5, veto_count=0,
            breakout_hold=True, retest_confirmed=True,
            spot_perp_agreement=0.6,
        )
        bd = eng.compute(snap, ctx, sample_count=20)
        # 注入的辅助证据应计入
        assert bd.supporting_total >= 3
        assert bd.supporting_passed == bd.supporting_total
