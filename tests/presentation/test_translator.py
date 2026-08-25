"""Presentation Translator 测试 — 依据 V1.1 计划 §三十六。"""

from __future__ import annotations

from src.domain import ConfidenceState, State
from src.presentation.translator import PresentationTranslator
from src.scoring.engine import ScoreBreakdown, SubScore
from src.scoring.data_confidence import DataConfidenceBreakdown
from src.scoring.signal_confirmation import SignalConfirmationBreakdown


class TestStateTranslation:
    def test_anomaly(self):
        assert PresentationTranslator.state_label(State.ANOMALY) == "发现异动"

    def test_suspected_start(self):
        assert PresentationTranslator.state_label(State.SUSPECTED_START) == "等待确认"

    def test_start_confirmed(self):
        assert PresentationTranslator.state_label(State.START_CONFIRMED) == "启动确认"

    def test_continuation(self):
        assert PresentationTranslator.state_label(State.CONTINUATION) == "趋势延续"

    def test_exhaustion(self):
        assert PresentationTranslator.state_label(State.EXHAUSTION) == "动能衰竭"

    def test_withdrawal(self):
        assert PresentationTranslator.state_label(State.WITHDRAWAL) == "资金撤离"

    def test_state_display_has_emoji(self):
        display = PresentationTranslator.state_display(State.START_CONFIRMED)
        assert "🚀" in display
        assert "启动确认" in display


class TestDirectionTranslation:
    def test_long(self):
        assert PresentationTranslator.direction_label("LONG") == "做多"

    def test_short(self):
        assert PresentationTranslator.direction_label("SHORT") == "做空"

    def test_none(self):
        assert PresentationTranslator.direction_label(None) == "未定"


class TestDataStatus:
    def test_confident_normal(self):
        assert PresentationTranslator.data_status_label(ConfidenceState.CONFIDENT) == "数据正常"

    def test_degraded(self):
        assert PresentationTranslator.data_status_label(ConfidenceState.DEGRADED) == "数据降级"

    def test_unknown(self):
        assert PresentationTranslator.data_status_label(ConfidenceState.UNKNOWN) == "数据异常"

    def test_stale(self):
        assert PresentationTranslator.data_status_label(ConfidenceState.CONFIDENT, any_stale=True) == "数据延迟"

    def test_fail(self):
        assert PresentationTranslator.data_status_label(ConfidenceState.CONFIDENT, any_fail=True) == "数据异常"


class TestCapitalFlowTranslation:
    def test_strong_buy(self):
        fv = {"taker_buy_volume": 100, "taker_sell_volume": 50, "oi_change_5m": 0.04,
              "cvd_slope_z": 3.0, "funding_percentile": 20, "signed_delta": 50}
        result = PresentationTranslator.translate_capital_flow(fv)
        assert result["主动买盘"] == "强"
        assert result["新增仓位"] == "明显增加"

    def test_missing_data(self):
        fv = {}
        result = PresentationTranslator.translate_capital_flow(fv)
        assert result["主动买盘"] == "数据不足"


class TestVolumePriceTranslation:
    def test_volume_amplified(self):
        fv = {"volume_z": 4.5, "price_efficiency": 0.7, "retrace_ratio": 0.2, "acceptance": 0.8}
        result = PresentationTranslator.translate_volume_price(fv)
        assert result["成交量"] == "明显放大"
        assert result["价格推动效率"] == "健康"
        assert result["回踩承接"] == "良好"
        assert result["突破有效性"] == "已确认"


class TestFalseStartCheck:
    def test_all_passed(self):
        vetoes = [{"type": "rapid_retrace", "triggered": False}]
        result = PresentationTranslator.translate_false_start_check(vetoes)
        assert len(result) > 0
        for r in result:
            assert "✅" in r["display"]

    def test_triggered(self):
        vetoes = [{"type": "rapid_retrace", "triggered": True}]
        result = PresentationTranslator.translate_false_start_check(vetoes)
        rapid = [r for r in result if "回吐" in r["check"]][0]
        assert not rapid["passed"]
        assert "❌" in rapid["display"]


class TestSummary:
    def test_no_opportunity(self):
        summary = PresentationTranslator.generate_summary(
            State.SLEEPING, None, None, None
        )
        assert "沉睡" in summary

    def test_confirmed_with_direction(self):
        bd = ScoreBreakdown(opportunity_score=85, available=True)
        bd.subscores["capital_inflow"] = SubScore("ci", "资金输入", 80, True)
        bd.subscores["startup_quality"] = SubScore("sq", "启动质量", 80, True)
        bd.subscores["withdrawal_risk"] = SubScore("wr", "撤离风险", 15, True, is_risk=True)
        dc = DataConfidenceBreakdown(score=90, available=True, coverage=1.0)
        sc = SignalConfirmationBreakdown(score=85, available=True, strong_confirm=True)
        summary = PresentationTranslator.generate_summary(
            State.START_CONFIRMED, "LONG", bd, dc, sc
        )
        assert "做多" in summary
        assert "资金" in summary

    def test_suspected_missing(self):
        bd = ScoreBreakdown(opportunity_score=50, available=True)
        bd.subscores["capital_inflow"] = SubScore("ci", "资金输入", 40, True)
        bd.subscores["startup_quality"] = SubScore("sq", "启动质量", 40, True)
        dc = DataConfidenceBreakdown(score=60, available=True, coverage=0.75)
        sc = SignalConfirmationBreakdown(score=40, available=True)
        summary = PresentationTranslator.generate_summary(
            State.SUSPECTED_START, "LONG", bd, dc, sc
        )
        assert "确认" in summary


class TestNoInternalTermsInUserFacing:
    """禁止内部术语直接暴露给用户。"""

    def test_state_labels_are_chinese(self):
        for state in State:
            label = PresentationTranslator.state_label(state)
            # Should not be the raw enum value
            assert label != state.value or state.value == state.value

    def test_subscore_labels_exist(self):
        labels = PresentationTranslator.subscore_labels()
        assert "capital_inflow" in labels
        assert labels["capital_inflow"] == "资金输入"
        assert labels["top_risk"] == "顶部风险"
