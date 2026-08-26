"""V1.4 §三 RecommendationGate 标准确认 / 强确认 测试（§四十二 Recommendation 组）。

依据：crypto_radar_v1.4_fix_update_plan.md §3.1（标准确认）/ §3.2（强确认）/ §四（5m 决策
边界）。覆盖 §四十二「Recommendation」组中由门禁直接决定的前置条件：
- COOLDOWN / 非正式状态不得进入正式推荐
- 三门槛（opportunity / signal_confirmation / data_confidence）
- 5m 未收盘不得通过（§四）
- 突破类 Setup 未 5m 收盘确认突破不得通过（§四）
- Hard Veto / Trade Plan / RR / Pump Risk / stale / 证据投票
- 标准确认 vs 强确认（§3.2 六项）

纯单元测试：GateContext 进 → GateResult 出，不构造 runtime。
"""

from __future__ import annotations

from src.config import RecommendationConfig
from src.recommendations import FORMAL_STATES, GateContext, RecommendationGate


def _cfg() -> RecommendationConfig:
    return RecommendationConfig()


def _gate() -> RecommendationGate:
    return RecommendationGate(_cfg())


def _good_ctx(**over) -> GateContext:
    """一份通过标准确认的 GateContext（ACCUMULATION，非突破类）。"""
    kw = dict(
        state="START_CONFIRMED",
        setup_type="ACCUMULATION",
        opportunity_score=80.0,
        signal_confirmation=80.0,
        data_confidence=90.0,
        trade_plan={
            "status": "ACTIVE",
            "chase_status": "ok",
            "reference_entry_low": 100.0,
            "reference_entry_high": 110.0,
            "rr_tp1": 2.0,
            "tp1": 120.0, "tp2": 130.0, "tp3": 140.0,
            "invalidation_price": 95.0,
        },
        pump_risk=20.0,
        stale_flag=0.0,
        direction="LONG",
        hard_veto=False,
        five_min_closed=True,
        core_passed=3, core_total=3,
        aux_passed=3, aux_total=5,
    )
    kw.update(over)
    return GateContext(**kw)


class TestGateStandardConfirmation:
    def test_passes_standard_when_all_good(self):
        res = _gate().evaluate(_good_ctx())
        assert res.passed is True
        assert res.confirmation_level == "STANDARD"

    def test_cooldown_state_rejected(self):
        """§四十二：COOLDOWN 不得进入正式推荐。"""
        res = _gate().evaluate(_good_ctx(state="COOLDOWN"))
        assert res.passed is False
        assert any("正式范围" in f for f in res.failed_checks)

    def test_sleeping_state_rejected(self):
        res = _gate().evaluate(_good_ctx(state="SLEEPING"))
        assert res.passed is False
        assert any("正式范围" in f for f in res.failed_checks)

    def test_anomaly_state_rejected(self):
        res = _gate().evaluate(_good_ctx(state="ANOMALY"))
        assert res.passed is False

    def test_continuation_state_accepted(self):
        res = _gate().evaluate(_good_ctx(state="CONTINUATION"))
        assert res.passed is True

    def test_low_opportunity_rejected(self):
        res = _gate().evaluate(_good_ctx(opportunity_score=60.0))
        assert res.passed is False
        assert any("opportunity" in f for f in res.failed_checks)

    def test_opportunity_none_rejected(self):
        res = _gate().evaluate(_good_ctx(opportunity_score=None))
        assert res.passed is False

    def test_low_signal_confirmation_rejected(self):
        res = _gate().evaluate(_good_ctx(signal_confirmation=70.0))
        assert res.passed is False
        assert any("signal_confirmation" in f for f in res.failed_checks)

    def test_low_data_confidence_rejected(self):
        res = _gate().evaluate(_good_ctx(data_confidence=80.0))
        assert res.passed is False
        assert any("data_confidence" in f for f in res.failed_checks)


class TestGate5mBoundary:
    def test_5m_not_closed_rejected(self):
        """§四十二：5m 未收盘不得正式发布（门禁层 five_min_closed=false）。"""
        res = _gate().evaluate(_good_ctx(five_min_closed=False))
        assert res.passed is False
        assert any("5m 收盘决策窗口" in f for f in res.failed_checks)

    def test_breakout_setup_without_confirm_rejected(self):
        """§四十二：突破类 Setup 未 5m 收盘确认突破不得发布。"""
        res = _gate().evaluate(_good_ctx(
            setup_type="TREND_CONTINUATION", breakout_confirmed=False))
        assert res.passed is False
        assert any("突破类 Setup" in f for f in res.failed_checks)

    def test_breakout_setup_confirm_none_rejected(self):
        res = _gate().evaluate(_good_ctx(
            setup_type="SHORT_SQUEEZE", breakout_confirmed=None))
        assert res.passed is False
        assert any("突破类 Setup" in f for f in res.failed_checks)

    def test_breakout_setup_with_confirm_passes(self):
        res = _gate().evaluate(_good_ctx(
            setup_type="TREND_CONTINUATION", breakout_confirmed=True))
        assert res.passed is True

    def test_non_breakout_setup_does_not_require_breakout_confirmed(self):
        """非突破类 Setup（ACCUMULATION）不需要 breakout_confirmed。"""
        res = _gate().evaluate(_good_ctx(
            setup_type="ACCUMULATION", breakout_confirmed=None))
        assert res.passed is True


class TestGateVetoPlanRrPumpStale:
    def test_hard_veto_rejected(self):
        res = _gate().evaluate(_good_ctx(hard_veto=True))
        assert res.passed is False
        assert any("Hard Veto" in f for f in res.failed_checks)

    def test_trade_plan_not_active_rejected(self):
        plan = {**_good_ctx().trade_plan, "status": "PENDING"}
        res = _gate().evaluate(_good_ctx(trade_plan=plan))
        assert res.passed is False
        assert any("ACTIVE" in f for f in res.failed_checks)

    def test_chase_status_blocked_rejected(self):
        plan = {**_good_ctx().trade_plan, "chase_status": "too_late"}
        res = _gate().evaluate(_good_ctx(trade_plan=plan))
        assert res.passed is False
        assert any("chase_status" in f for f in res.failed_checks)

    def test_invalid_entry_zone_rejected(self):
        plan = {**_good_ctx().trade_plan, "reference_entry_low": 0}
        res = _gate().evaluate(_good_ctx(trade_plan=plan))
        assert res.passed is False
        assert any("Entry Zone" in f for f in res.failed_checks)

    def test_inverted_entry_zone_rejected(self):
        plan = {**_good_ctx().trade_plan,
                "reference_entry_low": 120.0, "reference_entry_high": 110.0}
        res = _gate().evaluate(_good_ctx(trade_plan=plan))
        assert res.passed is False
        assert any("Entry Zone" in f for f in res.failed_checks)

    def test_low_rr_rejected(self):
        plan = {**_good_ctx().trade_plan, "rr_tp1": 1.0}
        res = _gate().evaluate(_good_ctx(trade_plan=plan))
        assert res.passed is False
        assert any("RR" in f for f in res.failed_checks)

    def test_rr_none_rejected(self):
        plan = {**_good_ctx().trade_plan, "rr_tp1": None}
        res = _gate().evaluate(_good_ctx(trade_plan=plan))
        assert res.passed is False
        assert any("RR" in f for f in res.failed_checks)

    def test_high_pump_risk_rejected(self):
        res = _gate().evaluate(_good_ctx(pump_risk=55.0))
        assert res.passed is False
        assert any("pump_risk" in f for f in res.failed_checks)

    def test_stale_rejected(self):
        res = _gate().evaluate(_good_ctx(stale_flag=1.0))
        assert res.passed is False
        assert any("stale" in f for f in res.failed_checks)


class TestGateEvidenceVote:
    def test_core_evidence_insufficient_rejected(self):
        res = _gate().evaluate(_good_ctx(core_passed=2, core_total=3))
        assert res.passed is False
        assert any("核心证据" in f for f in res.failed_checks)

    def test_aux_evidence_insufficient_rejected(self):
        res = _gate().evaluate(_good_ctx(aux_passed=2, aux_total=5))
        assert res.passed is False
        assert any("辅助证据" in f for f in res.failed_checks)

    def test_core_total_below_min_rejected(self):
        res = _gate().evaluate(_good_ctx(core_passed=2, core_total=2))
        assert res.passed is False
        assert any("核心证据" in f for f in res.failed_checks)

    def test_aux_total_below_min_rejected(self):
        res = _gate().evaluate(_good_ctx(aux_passed=2, aux_total=3))
        assert res.passed is False
        assert any("辅助证据" in f for f in res.failed_checks)


class TestGateStrongConfirmation:
    """§3.2：标准确认通过后，6 项全满足 → STRONG；缺任一 → STANDARD。"""

    def _good_strong_ctx(self, **over) -> GateContext:
        strong = dict(
            setup_type="TREND_CONTINUATION",
            breakout_confirmed=True,
            breakout_hold=True,
            retest_confirmed=True,
            second_impulse_confirmed=True,
            context_15m=0.01,        # LONG 同向
            context_1h=0.005,       # 不逆向
            spot_perp_agreement=0.4,  # >= 0.3
            direction="LONG",
        )
        strong.update(over)   # 调用方覆盖优先
        return _good_ctx(**strong)

    def test_strong_when_all_six_met(self):
        res = _gate().evaluate(self._good_strong_ctx())
        assert res.passed is True
        assert res.confirmation_level == "STRONG"
        assert res.strong_missing == []

    def test_standard_when_breakout_hold_missing(self):
        res = _gate().evaluate(self._good_strong_ctx(breakout_hold=False))
        assert res.passed is True
        assert res.confirmation_level == "STANDARD"
        assert "breakout_hold" in res.strong_missing

    def test_standard_when_retest_missing(self):
        res = _gate().evaluate(self._good_strong_ctx(retest_confirmed=False))
        assert res.confirmation_level == "STANDARD"
        assert "retest_confirmed" in res.strong_missing

    def test_standard_when_second_impulse_missing(self):
        res = _gate().evaluate(self._good_strong_ctx(second_impulse_confirmed=False))
        assert res.confirmation_level == "STANDARD"
        assert "second_impulse_confirmed" in res.strong_missing

    def test_standard_when_15m_adverse(self):
        res = _gate().evaluate(self._good_strong_ctx(context_15m=-0.01))
        assert res.confirmation_level == "STANDARD"
        assert "15m_direction_aligned" in res.strong_missing

    def test_standard_when_1h_strongly_opposite(self):
        res = _gate().evaluate(self._good_strong_ctx(context_1h=-0.6))
        assert res.confirmation_level == "STANDARD"
        assert "1h_not_strongly_opposite" in res.strong_missing

    def test_standard_when_spot_agreement_low(self):
        res = _gate().evaluate(self._good_strong_ctx(spot_perp_agreement=0.1))
        assert res.confirmation_level == "STANDARD"
        assert "spot_perp_agreement" in res.strong_missing

    def test_strong_for_short_when_15m_down_aligned(self):
        """SHORT 方向：15m 为负且与方向同向 → 15m 对齐成立。"""
        res = _gate().evaluate(self._good_strong_ctx(direction="SHORT",
                                                    context_15m=-0.01,
                                                    context_1h=-0.005))
        assert res.confirmation_level == "STRONG"


class TestGateResultSerialization:
    def test_to_dict_roundtrip(self):
        res = _gate().evaluate(_good_ctx(state="COOLDOWN"))
        d = res.to_dict()
        assert d["passed"] is False
        assert isinstance(d["failed_checks"], list)
        assert len(d["failed_checks"]) > 0
        assert d["confirmation_level"] is None
        assert d["strong_missing"] == []
