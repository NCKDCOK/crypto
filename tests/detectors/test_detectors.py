"""Detector 测试 — Anomaly / Startup / FalseStart / Continuation / Withdrawal。"""

from __future__ import annotations

from decimal import Decimal

from src.domain import (
    ConfidenceState,
    Direction,
    EvidenceFamily,
    FeatureSnapshot,
    FeatureValue,
    VetoSeverity,
    VetoType,
)
from src.detectors.anomaly import AnomalyDetector
from src.detectors.continuation_withdrawal import (
    ContinuationDetector,
    ExhaustionDetector,
    WithdrawalDetector,
)
from src.detectors.false_start import FalseStartFilter
from src.detectors.startup import StartupDetector
from src.detectors.anomaly import AnomalyResult


def _snap(
    symbol="BTCUSDT",
    volume_z=None,
    trade_count_z=None,
    taker_delta=None,
    cvd_slope_z=None,
    oi_change_1m=None,
    directional_efficiency=None,
    flow_impact=None,
    retrace_ratio=None,
    funding_percentile=None,
):
    features = {}
    if volume_z is not None:
        features["volume_z"] = FeatureValue(value=volume_z, available=True, window="30s")
    if trade_count_z is not None:
        features["trade_count_z"] = FeatureValue(value=trade_count_z, available=True, window="30s")
    if taker_delta is not None:
        features["taker_delta"] = FeatureValue(value=taker_delta, available=True, window="30s")
    if cvd_slope_z is not None:
        features["cvd_slope_z"] = FeatureValue(value=cvd_slope_z, available=True, window="30s")
    if oi_change_1m is not None:
        features["oi_change_1m"] = FeatureValue(value=oi_change_1m, available=True, window="1m")
    if directional_efficiency is not None:
        features["directional_efficiency"] = FeatureValue(value=directional_efficiency, available=True, window="30s")
    if flow_impact is not None:
        features["flow_impact"] = FeatureValue(value=flow_impact, available=True, window="30s")
    if retrace_ratio is not None:
        features["retrace_ratio"] = FeatureValue(value=retrace_ratio, available=True)
    if funding_percentile is not None:
        features["funding_percentile"] = FeatureValue(value=funding_percentile, available=True)
    return FeatureSnapshot(symbol=symbol, asof=1000, features=features)


class TestAnomalyDetector:
    def test_normal_no_trigger(self):
        """正常噪声 → 不触发。"""
        snap = _snap(volume_z=1.0, trade_count_z=1.0, cvd_slope_z=0.5)
        det = AnomalyDetector()
        result = det.detect(snap)
        assert result.is_anomaly is False

    def test_volume_spike_triggers(self):
        """volume+trade count 同步尖峰 → 触发。"""
        snap = _snap(volume_z=4.72, trade_count_z=3.81, cvd_slope_z=3.12)
        det = AnomalyDetector()
        result = det.detect(snap)
        assert result.is_anomaly is True
        assert len(result.evidence) >= 2

    def test_stale_blocks_anomaly(self):
        """关键数据 stale → 不触发可升级 anomaly。"""
        snap = _snap(volume_z=10.0)
        det = AnomalyDetector()
        result = det.detect(snap, confidence=ConfidenceState.UNKNOWN)
        assert result.is_anomaly is False

    def test_direction_hint_from_delta(self):
        snap = _snap(volume_z=5.0, cvd_slope_z=4.0)
        det = AnomalyDetector()
        result = det.detect(snap)
        assert result.direction_hint == "LONG"

    def test_no_long_short_decision(self):
        """不输出 LONG/SHORT 决策。"""
        snap = _snap(volume_z=5.0)
        det = AnomalyDetector()
        result = det.detect(snap)
        # direction_hint 可为 None 或提示，但不是决策
        assert not hasattr(result, "decision")


class TestStartupDetector:
    def test_squeeze_cover_not_confirmed(self):
        """price↑ vol↑ OI↓ → squeeze cover，不 CONFIRMED。"""
        snap = _snap(
            taker_delta=10000,
            oi_change_1m=-5.0,  # OI 收缩
            directional_efficiency=0.5,
            retrace_ratio=0.1,
        )
        anomaly = AnomalyResult(is_anomaly=True)
        det = StartupDetector()
        result = det.detect(snap, anomaly)
        assert result.is_squeeze_cover is True
        assert result.confirmed is False

    def test_clean_startup_suspected(self):
        """干净多头启动 → SUSPECTED。"""
        snap = _snap(
            taker_delta=10000,
            oi_change_1m=5.0,  # OI 扩张
            directional_efficiency=0.8,
            retrace_ratio=0.1,
        )
        anomaly = AnomalyResult(is_anomaly=True)
        det = StartupDetector()
        result = det.detect(snap, anomaly, confidence=ConfidenceState.CONFIDENT)
        assert result.suspected is True
        assert result.direction == Direction.LONG

    def test_confirmed_needs_hold(self):
        """单次 spike 不直接确认 → 需 hold。"""
        snap = _snap(
            taker_delta=10000,
            oi_change_1m=5.0,
            directional_efficiency=0.8,
            retrace_ratio=0.1,
        )
        anomaly = AnomalyResult(is_anomaly=True)
        det = StartupDetector(confirmation_hold_s=15.0)
        # hold=0 → 不确认
        result = det.detect(snap, anomaly, hold_duration_s=0)
        assert result.confirmed is False
        # hold=20 → 确认
        result = det.detect(snap, anomaly, hold_duration_s=20)
        assert result.confirmed is True

    def test_degraded_blocks_confirm(self):
        """DEGRADED → 最高 SUSPECTED，禁止 CONFIRMED。"""
        snap = _snap(
            taker_delta=10000,
            oi_change_1m=5.0,
            directional_efficiency=0.8,
            retrace_ratio=0.1,
        )
        anomaly = AnomalyResult(is_anomaly=True)
        det = StartupDetector()
        result = det.detect(snap, anomaly, confidence=ConfidenceState.DEGRADED, hold_duration_s=20)
        assert result.suspected is True
        assert result.confirmed is False


class TestFalseStartFilter:
    def test_rapid_retrace_rejected(self):
        """先拉升后完整回吐 → REJECTED。"""
        snap = _snap(retrace_ratio=0.9)  # 回吐 90%
        det = FalseStartFilter()
        result = det.check(snap, direction=Direction.LONG)
        assert result.rejected is True
        # 应有 rapid_retrace hard veto
        veto = next(v for v in result.vetoes if v.type == VetoType.RAPID_RETRACE)
        assert veto.triggered is True
        assert veto.severity == VetoSeverity.HARD

    def test_oi_contraction_rejected(self):
        """price↑ OI↓ → oi_contraction hard veto。"""
        snap = _snap(oi_change_1m=-5.0)
        det = FalseStartFilter()
        result = det.check(snap, direction=Direction.LONG)
        assert result.rejected is True

    def test_data_stale_rejected(self):
        """关键输入 stale → data_stale hard veto。"""
        snap = _snap()
        det = FalseStartFilter()
        result = det.check(snap, is_confident=False)
        assert result.rejected is True
        veto = next(v for v in result.vetoes if v.type == VetoType.DATA_STALE)
        assert veto.triggered is True

    def test_no_veto_passes(self):
        """无 veto 命中 → 不拒绝。"""
        snap = _snap(retrace_ratio=0.1, oi_change_1m=5.0, directional_efficiency=0.8)
        det = FalseStartFilter()
        result = det.check(snap, direction=Direction.LONG)
        assert result.rejected is False

    def test_absorption_soft_veto(self):
        """delta 大但 flow_impact 极低 → soft veto。"""
        snap = _snap(taker_delta=50000, flow_impact=0.0001)
        det = FalseStartFilter()
        result = det.check(snap, direction=Direction.LONG)
        veto = next(v for v in result.vetoes if v.type == VetoType.LOW_EFFICIENCY_ABSORPTION)
        assert veto.triggered is True
        assert veto.severity == VetoSeverity.SOFT
        # soft veto 不直接拒绝
        # 但如果有其他 hard veto 就会拒绝


class TestContinuationDetector:
    def test_continuing(self):
        """OI 维持 + CVD 同向 → CONTINUATION。"""
        snap = _snap(
            oi_change_1m=2.0,
            cvd_slope_z=1.0,
            directional_efficiency=0.5,
        )
        det = ContinuationDetector()
        result = det.detect(snap, direction=Direction.LONG)
        assert result.is_continuing is True

    def test_weakening(self):
        """OI 坍缩 → weakening。"""
        snap = _snap(
            oi_change_1m=-5.0,
            cvd_slope_z=-2.0,
            directional_efficiency=0.1,
        )
        det = ContinuationDetector()
        result = det.detect(snap, direction=Direction.LONG)
        assert result.is_weakening is True
        assert result.is_continuing is False


class TestExhaustionDetector:
    def test_divergence_detected(self):
        """价创新高但 CVD/OI 不确认 → EXHAUSTION。"""
        snap = _snap(
            cvd_slope_z=-2.0,  # CVD 反向
            oi_change_1m=-3.0,  # OI 收缩
            flow_impact=0.0001,  # flow impact 极低
        )
        det = ExhaustionDetector(min_divergence_count=2)
        result = det.detect(snap, direction=Direction.LONG)
        assert result.is_exhausted is True


class TestWithdrawalDetector:
    def test_withdrawal_confirmed(self):
        """OI 收缩 + delta 反转 + 效率失守 → WITHDRAWAL。"""
        snap = _snap(
            oi_change_1m=-5.0,
            taker_delta=-10000,  # 方向反转（LONG 但 delta 负）
            directional_efficiency=0.05,
            retrace_ratio=0.7,
        )
        det = WithdrawalDetector(min_evidence_count=3)
        result = det.detect(snap, direction=Direction.LONG)
        assert result.is_withdrawal is True

    def test_no_withdrawal_without_evidence(self):
        snap = _snap(
            oi_change_1m=2.0,
            taker_delta=10000,
            directional_efficiency=0.8,
        )
        det = WithdrawalDetector(min_evidence_count=3)
        result = det.detect(snap, direction=Direction.LONG)
        assert result.is_withdrawal is False
