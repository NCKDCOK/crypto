"""Continuation 真实证据化测试 — V1.2 §21。

禁止「因为在 CONTINUATION 所以高分」。持续启动必须来自真实资金证据。
"""

from __future__ import annotations

from src.detectors.continuation_withdrawal import ContinuationDetector
from src.domain import Direction, FeatureSnapshot, FeatureValue


def _snap(**feats) -> FeatureSnapshot:
    features = {}
    for k, v in feats.items():
        features[k] = FeatureValue(value=v, available=v is not None, window="30s")
    return FeatureSnapshot(symbol="BTCUSDT", asof=1000, features=features)


class TestContinuationEvidence:
    def test_no_evidence_not_continuing(self):
        """无真实证据 → 不算 continuing。"""
        det = ContinuationDetector(min_evidence_count=2)
        snap = _snap()  # 全空
        r = det.detect(snap, Direction.LONG)
        assert r.is_continuing is False

    def test_all_evidence_failing_not_continuing(self):
        det = ContinuationDetector(min_evidence_count=2)
        snap = _snap(oi_change_1m=-0.05, cvd_slope_z=-3.0, taker_delta=-10000,
                     directional_efficiency=0.05, retrace_ratio=0.8)
        r = det.detect(snap, Direction.LONG)
        assert r.is_continuing is False
        assert r.is_weakening is True

    def test_real_evidence_continuing(self):
        """OI 持续 + CVD 持续 + 效率健康 → continuing（真实证据）。"""
        det = ContinuationDetector(min_evidence_count=2)
        snap = _snap(oi_change_1m=0.03, cvd_slope_z=2.0, taker_delta=5000,
                     directional_efficiency=0.6, retrace_ratio=0.2)
        r = det.detect(snap, Direction.LONG)
        assert r.is_continuing is True
        assert len(r.evidence) >= 3

    def test_min_evidence_threshold(self):
        """只通过 1 项但 min=2 → 不 continuing。"""
        det = ContinuationDetector(min_evidence_count=2)
        snap = _snap(oi_change_1m=0.03, cvd_slope_z=-1.0, taker_delta=-1000,
                     directional_efficiency=0.05, retrace_ratio=0.8)
        r = det.detect(snap, Direction.LONG)
        assert r.is_continuing is False  # 只有 1 项通过

    def test_evidence_types_real(self):
        """证据类型应为 persistence 类（非空泛 maintained）。"""
        det = ContinuationDetector()
        snap = _snap(oi_change_1m=0.03, cvd_slope_z=2.0, taker_delta=5000)
        r = det.detect(snap, Direction.LONG)
        types = {e.type for e in r.evidence}
        assert "oi_persistence" in types
        assert "cvd_persistence" in types
        assert "delta_persistence" in types
