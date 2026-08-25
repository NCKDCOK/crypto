"""Trade Plan Engine 测试 — V1.2 §25。"""

from __future__ import annotations

from src.engines.trade_plan import TradePlanEngine
from src.engines.structure import StructureResult
from src.engines.volume_profile import VolumeProfileResult


class TestTradePlan:
    def test_long_plan_from_support(self):
        eng = TradePlanEngine()
        struct = StructureResult(support=95.0, resistance=115.0, retest_zone_low=96.0, retest_zone_high=98.0, vwap=100.0)
        plan = eng.compute(100.0, "LONG", structure=struct, atr=2.0)
        assert plan.reference_entry_low is not None
        assert plan.invalidation_price is not None
        assert plan.tp1 is not None
        assert plan.rr_tp1 is not None
        assert plan.chase_status == "ok"

    def test_short_plan_from_resistance(self):
        eng = TradePlanEngine()
        struct = StructureResult(support=85.0, resistance=110.0, vwap=100.0)
        plan = eng.compute(100.0, "SHORT", structure=struct, atr=2.0)
        assert plan.invalidation_price is not None
        assert plan.invalidation_price > 100.0  # SHORT invalidation 在上方
        assert plan.tp1 is not None and plan.tp1 < 100.0  # TP 在下方

    def test_chase_too_far(self):
        eng = TradePlanEngine(chase_too_far_pct=0.05)
        struct = StructureResult(support=90.0, resistance=200.0)
        plan = eng.compute(150.0, "LONG", structure=struct, atr=2.0)
        assert plan.chase_status == "chase_too_far"
        assert "不建议" in plan.plan_reason

    def test_rr_calculated(self):
        eng = TradePlanEngine(tp1_r=2.0, tp2_r=3.2)
        struct = StructureResult(support=95.0, resistance=115.0, retest_zone_low=96.0, retest_zone_high=98.0)
        plan = eng.compute(100.0, "LONG", structure=struct, atr=2.0)
        assert plan.rr_tp1 == 2.0
        assert plan.rr_tp2 == 3.2

    def test_insufficient_rr(self):
        eng = TradePlanEngine(min_rr=5.0)  # 极高阈值
        struct = StructureResult(support=99.0, resistance=101.0, retest_zone_low=99.0, retest_zone_high=99.5)
        plan = eng.compute(100.0, "LONG", structure=struct, atr=0.5)
        # entry≈99.25, invalidation≈98.75, 1R=0.5, tp1=99.25+0.5*2=100.25 → rr1=2 < 5
        assert plan.chase_status == "insufficient_rr"

    def test_no_data(self):
        eng = TradePlanEngine()
        plan = eng.compute(None, "LONG")
        assert plan.chase_status == "no_plan"

    def test_freeze(self):
        eng = TradePlanEngine()
        struct = StructureResult(support=95.0, resistance=115.0, retest_zone_low=96.0, retest_zone_high=98.0)
        plan = eng.compute(100.0, "LONG", structure=struct, atr=2.0)
        eng.freeze(plan, 5000)
        assert plan.frozen is True
        assert plan.frozen_at_ms == 5000

    def test_entry_from_structure_not_ai(self):
        """Entry 必须来自结构，不能 AI 自由生成。"""
        eng = TradePlanEngine()
        struct = StructureResult(support=95.0, resistance=115.0, retest_zone_low=96.0, retest_zone_high=98.0)
        plan = eng.compute(100.0, "LONG", structure=struct, atr=2.0)
        # entry 应在 retest_zone 范围
        assert plan.reference_entry_low == 96.0
        assert plan.reference_entry_high == 98.0
