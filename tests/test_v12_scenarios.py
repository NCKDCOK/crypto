"""V1.2 §45 综合回归测试 — Accumulation / Distribution / Breakout / Setup / Location / TradePlan / Confidence。

覆盖计划要求的全部测试场景。
"""

from __future__ import annotations

from decimal import Decimal

from src.domain import (
    AggressorSide, ConfidenceState, FeatureSnapshot, FeatureValue, KlineEvent,
    KlineInterval, State,
)
from src.engines.accumulation import AccumulationEngine
from src.engines.distribution import DistributionEngine
from src.engines.breakout_lifecycle import BreakoutLifecycleEngine
from src.engines.setup_type import SetupTypeEngine
from src.engines.location import LocationEngine
from src.engines.structure import StructureResult
from src.engines.trade_plan import TradePlanEngine
from src.scoring.data_confidence import DataConfidenceEngine
from src.scoring.signal_confirmation import SignalConfirmationEngine, ConfirmationContext
from src.config import ScoringConfig


def _snap(**feats) -> FeatureSnapshot:
    f = {}
    for k, v in feats.items():
        f[k] = FeatureValue(value=v, available=v is not None, window="30s")
    return FeatureSnapshot(symbol="X", asof=0, features=f)


# ── Accumulation（§45）──


class TestAccumulationScenarios:
    def test_sell_absorption(self):
        eng = AccumulationEngine()
        fv = {"signed_delta": -50000, "price_efficiency": 0.05, "retrace_ratio": 0.1,
              "oi_change_5m": 0.02, "acceptance": 0.8, "spot_delta": 5000}
        r = eng.compute(fv, "LONG")
        assert r.absorption_score is not None and r.absorption_score > 70

    def test_cvd_price_divergence(self):
        eng = AccumulationEngine()
        fv = {"cvd_slope_z": -2.0, "price_return_5m": 0.01, "signed_delta": -10000,
              "price_efficiency": 0.1}
        r = eng.compute(fv, "LONG")
        assert r.factors.get("cvd_divergence") is not None

    def test_false_accumulation(self):
        """无承接迹象（卖压不显著）→ 承接分低。"""
        eng = AccumulationEngine()
        fv = {"signed_delta": 50000, "price_efficiency": 0.9, "retrace_ratio": 0.05}
        r = eng.compute(fv, "LONG")
        # 承接分低（非吸筹），但 turnover 可能高（换手≠吸筹）
        assert r.absorption_score is not None and r.absorption_score < 40


# ── Distribution（§45）──


class TestDistributionScenarios:
    def test_high_volume_no_progress(self):
        eng = DistributionEngine()
        fv = {"volume_z": 5.0, "price_efficiency": 0.05, "signed_delta": 30000,
              "price_return_5m": 0.001}
        r = eng.compute(fv, "LONG")
        assert r.distribution_risk_score is not None and r.distribution_risk_score > 40

    def test_false_distribution(self):
        eng = DistributionEngine()
        fv = {"volume_z": 1.0, "price_efficiency": 0.8, "signed_delta": 10000,
              "price_return_5m": 0.02, "acceptance": 0.9, "oi_change_5m": 0.03}
        r = eng.compute(fv, "LONG")
        assert r.distribution_risk_score is not None and r.distribution_risk_score < 40


# ── Breakout（§45）──


def _kline5m(o, c, t=1000, closed=True, h=None, l=None):
    return KlineEvent(symbol="X", interval=KlineInterval.M5, open_time=t,
                      close_time=t + 1, event_time=t, receive_time=t,
                      open=Decimal(str(o)), high=Decimal(str(h or max(o, c))),
                      low=Decimal(str(l or min(o, c))), close=Decimal(str(c)),
                      volume=Decimal("10"), quote_volume=Decimal("1"), trade_count=1, is_closed=closed)


class TestBreakoutScenarios:
    def test_true_5m_breakout(self):
        eng = BreakoutLifecycleEngine()
        r = eng.update("X", 1000, breakout_level=100.0, current_price=105.0,
                       kline_5m=_kline5m(99, 105))
        assert r.breakout_confirmed is True

    def test_wick_fake_breakout(self):
        eng = BreakoutLifecycleEngine()
        r = eng.update("X", 1000, breakout_level=100.0, current_price=100.0,
                       kline_5m=_kline5m(99, 100, h=106))
        assert not (r.breakout_confirmed and r.breakout_direction == "up")

    def test_healthy_retest(self):
        eng = BreakoutLifecycleEngine()
        eng.update("X", 1000, breakout_level=100.0, current_price=110.0, kline_5m=_kline5m(99, 110))
        r = eng.update("X", 2000, breakout_level=100.0, current_price=107.0,
                       fv={"acceptance": 0.7, "oi_change_5m": 0.01, "signed_delta": 5000})
        assert r.retest_started is True

    def test_second_confirmation(self):
        eng = BreakoutLifecycleEngine()
        eng.update("X", 1000, breakout_level=100.0, current_price=110.0, kline_5m=_kline5m(99, 110))
        eng.update("X", 2000, breakout_level=100.0, current_price=107.0,
                   fv={"acceptance": 0.7, "oi_change_5m": 0.01, "signed_delta": 5000})
        r = eng.update("X", 3000, breakout_level=100.0, current_price=107.5,
                       fv={"acceptance": 0.8, "oi_change_5m": 0.02, "signed_delta": 8000})
        assert r.retest_confirmed is True


# ── Setup（§45）──


class TestSetupScenarios:
    def test_accumulation_setup(self):
        eng = SetupTypeEngine()
        r = eng.compute(State.ANOMALY, "LONG", {}, accumulation_score=80)
        assert r.setup_type == "ACCUMULATION"

    def test_breakout_start_setup(self):
        eng = SetupTypeEngine()
        r = eng.compute(State.START_CONFIRMED, "LONG", {"acceptance": 0.8, "price_return_5m": 0.01})
        assert r.setup_type == "BREAKOUT_START"

    def test_short_squeeze_setup(self):
        eng = SetupTypeEngine()
        r = eng.compute(State.START_CONFIRMED, "LONG",
                        {"signed_delta": 10000, "oi_change_5m": -0.03, "price_return_5m": 0.02})
        assert r.setup_type == "SHORT_SQUEEZE"

    def test_distribution_setup(self):
        eng = SetupTypeEngine()
        r = eng.compute(State.EXHAUSTION, "LONG", {}, distribution_risk=75)
        assert r.setup_type == "DISTRIBUTION"

    def test_pump_risk_setup(self):
        eng = SetupTypeEngine()
        r = eng.compute(State.ANOMALY, "LONG", {"price_return_5m": 0.2})
        assert r.setup_type == "PUMP_RISK"


# ── Location（§45）──


class TestLocationScenarios:
    def test_near_support(self):
        eng = LocationEngine(near_support_pct=0.02)
        struct = StructureResult(support=99.0, resistance=120.0)
        r = eng.compute(100.0, {"price_return_5m": 0.01, "retrace_ratio": 0.3}, structure=struct)
        assert r.classification == "reasonable"

    def test_near_resistance(self):
        eng = LocationEngine()
        struct = StructureResult(support=80.0, resistance=101.0)
        r = eng.compute(100.0, {"price_return_5m": 0.01, "retrace_ratio": 0.1}, structure=struct)
        # 距阻力 1% → 近阻力
        assert r.distance_to_resistance is not None

    def test_chase_too_far(self):
        eng = LocationEngine(chase_too_far_pct=0.05)
        r = eng.compute(110.0, {"price_return_5m": 0.1, "retrace_ratio": 0.1})
        assert r.classification == "high"

    def test_healthy_retest_zone(self):
        eng = LocationEngine()
        r = eng.compute(105.0, {"price_return_5m": 0.02, "retrace_ratio": 0.2, "acceptance": 0.7})
        assert r.classification == "reasonable"


# ── Trade Plan（§45）──


class TestTradePlanScenarios:
    def test_entry_zone(self):
        eng = TradePlanEngine()
        struct = StructureResult(support=95.0, resistance=115.0, retest_zone_low=96.0, retest_zone_high=98.0)
        plan = eng.compute(100.0, "LONG", structure=struct, atr=2.0)
        assert plan.reference_entry_low == 96.0
        assert plan.reference_entry_high == 98.0

    def test_invalidation(self):
        eng = TradePlanEngine()
        struct = StructureResult(support=95.0, resistance=115.0)
        plan = eng.compute(100.0, "LONG", structure=struct, atr=2.0)
        assert plan.invalidation_price is not None
        assert plan.invalidation_price < 100.0

    def test_rr_2r_3_2r(self):
        eng = TradePlanEngine(tp1_r=2.0, tp2_r=3.2)
        struct = StructureResult(support=95.0, resistance=115.0, retest_zone_low=96.0, retest_zone_high=98.0)
        plan = eng.compute(100.0, "LONG", structure=struct, atr=2.0)
        assert plan.rr_tp1 == 2.0
        assert plan.rr_tp2 == 3.2

    def test_insufficient_rr(self):
        eng = TradePlanEngine(min_rr=10.0)
        struct = StructureResult(support=99.0, resistance=101.0, retest_zone_low=99.0, retest_zone_high=99.5)
        plan = eng.compute(100.0, "LONG", structure=struct, atr=0.5)
        assert plan.chase_status == "insufficient_rr"


# ── Confidence（§45）──


class TestConfidenceScenarios:
    def test_stale_data(self):
        eng = DataConfidenceEngine(ScoringConfig())
        snap = _snap(oi_contracts=100, funding=0.001, context_1m=0.01, stale_flag=1)
        bd = eng.compute(ConfidenceState.UNKNOWN, snap, sample_count=20)
        assert bd.score < 70

    def test_missing_oi(self):
        eng = DataConfidenceEngine(ScoringConfig())
        snap = _snap(oi_contracts=None, funding=0.001, context_1m=0.01, stale_flag=0)
        bd = eng.compute(ConfidenceState.CONFIDENT, snap, sample_count=20)
        assert "oi" in bd.missing
        assert bd.coverage < 1.0

    def test_missing_spot(self):
        eng = DataConfidenceEngine(ScoringConfig())
        snap = _snap(oi_contracts=100, funding=0.001, context_1m=0.01, stale_flag=0)
        bd = eng.compute(ConfidenceState.CONFIDENT, snap, sample_count=20, spot_available=False)
        assert "spot" in bd.missing

    def test_low_evidence_coverage(self):
        eng = SignalConfirmationEngine(ScoringConfig())
        snap = _snap(oi_change_5m=0.05)  # 仅 1 项核心
        ctx = ConfirmationContext(direction="LONG", evidence_count=1, veto_count=0)
        bd = eng.compute(snap, ctx, sample_count=20, data_confidence_score=90.0)
        assert bd.core_total == 1  # 缺失项移出分母
        assert bd.core_total < 3  # 不足 3 项核心证据
