"""Location Engine 测试 — V1.2 §19。"""

from __future__ import annotations

from src.engines.location import LocationEngine
from src.engines.structure import StructureResult
from src.engines.volume_profile import VolumeProfileResult


class TestLocationEngine:
    def test_no_price(self):
        eng = LocationEngine()
        r = eng.compute(None, {})
        assert r.classification == "unknown"

    def test_chase_too_far(self):
        eng = LocationEngine(chase_too_far_pct=0.05)
        r = eng.compute(110.0, {"price_return_5m": 0.1, "retrace_ratio": 0.1})
        assert r.classification == "high"
        assert "不建议追" in r.label

    def test_near_support_reasonable(self):
        eng = LocationEngine(near_support_pct=0.02)
        struct = StructureResult(support=99.0, resistance=120.0, vwap=105.0)
        r = eng.compute(100.0, {"price_return_5m": 0.01, "retrace_ratio": 0.3}, structure=struct)
        assert r.classification == "reasonable"
        assert "承接" in r.label or "合理" in r.label

    def test_healthy_retrace(self):
        eng = LocationEngine()
        r = eng.compute(105.0, {"price_return_5m": 0.02, "retrace_ratio": 0.2, "acceptance": 0.7})
        assert r.classification == "reasonable"

    def test_distances_computed(self):
        eng = LocationEngine()
        struct = StructureResult(support=95.0, resistance=115.0, vwap=100.0)
        vp = VolumeProfileResult(poc=98.0)
        r = eng.compute(100.0, {"price_return_5m": 0.01, "retrace_ratio": 0.1},
                        structure=struct, volume_profile=vp)
        assert r.distance_to_support is not None
        assert r.distance_to_resistance is not None
        assert r.distance_to_poc is not None
        assert r.distance_to_vwap is not None

    def test_score_in_range(self):
        eng = LocationEngine()
        struct = StructureResult(support=95.0, resistance=115.0)
        r = eng.compute(100.0, {"price_return_5m": 0.01, "retrace_ratio": 0.2}, structure=struct)
        if r.location_score is not None:
            assert 0.0 <= r.location_score <= 100.0
