"""Trade Plan Engine 测试 — V1.2 §25 + V1.3 §18/§19。"""

from __future__ import annotations

import pytest

from src.domain.enums import State
from src.engines.structure import StructureResult
from src.engines.trade_plan import (
    STATUS_ACTIVE,
    STATUS_CANDIDATE,
    STATUS_EXPIRED,
    STATUS_NOT_LEGAL,
    TradePlan,
    TradePlanEngine,
    plan_gate,
)
from src.engines.volume_profile import VolumeProfileResult


def _struct():
    return StructureResult(support=95.0, resistance=115.0, retest_zone_low=96.0,
                           retest_zone_high=98.0, vwap=100.0)


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


# ────────────────────────────────────────────────────────────────────
# V1.3 §18 状态限制 + §19 版本冻结


class TestV13PlanGate:
    """§18 状态分级：formal / candidate / none。"""

    def test_formal_states(self):
        assert plan_gate(State.START_CONFIRMED) == "formal"
        assert plan_gate(State.CONTINUATION) == "formal"
        assert plan_gate("START_CONFIRMED") == "formal"  # str 兼容

    def test_suspected_start_candidate(self):
        assert plan_gate(State.SUSPECTED_START) == "candidate"

    def test_sub_stage_labels_candidate(self):
        # 方案A：ACCUMULATION / RETEST_PENDING 是子阶段标签，非机器状态
        assert plan_gate(State.SLEEPING, sub_stage="ACCUMULATION") == "candidate"
        assert plan_gate(State.ANOMALY, sub_stage="RETEST_PENDING") == "candidate"
        assert plan_gate(State.SUSPECTED_START, sub_stage="ACCUMULATION") == "candidate"

    def test_none_states(self):
        for st in (State.SLEEPING, State.ANOMALY, State.COOLDOWN,
                   State.EXHAUSTION, State.WITHDRAWAL, State.REJECTED):
            assert plan_gate(st) == "none", st

    def test_state_none_backward_compat(self):
        # 未传状态的旧调用按正式处理（向后兼容）
        assert plan_gate(None) == "formal"


class TestV13TradePlanStateGate:
    """§18：只有合法状态生成计划；候选预案标记「候选预案，尚未确认」。"""

    NO_PLAN_STATES = (State.SLEEPING, State.ANOMALY, State.COOLDOWN,
                      State.EXHAUSTION, State.WITHDRAWAL, State.REJECTED)

    def test_no_plan_states(self):
        eng = TradePlanEngine()
        for st in self.NO_PLAN_STATES:
            plan = eng.compute(100.0, "LONG", structure=_struct(), atr=2.0, state=st)
            assert plan.status == STATUS_NOT_LEGAL, st
            assert plan.chase_status == "no_plan"
            assert plan.reference_entry_low is None
            assert "不生成交易计划" in plan.plan_reason

    def test_suspected_start_candidate_plan(self):
        eng = TradePlanEngine()
        plan = eng.compute(100.0, "LONG", structure=_struct(), atr=2.0,
                           state=State.SUSPECTED_START)
        assert plan.status == STATUS_CANDIDATE
        # 候选也生成完整 Entry/失效位（供监督预览），但标记尚未确认
        assert plan.reference_entry_low == 96.0
        assert plan.invalidation_price is not None
        assert "候选预案，尚未确认" in plan.plan_reason

    def test_sub_stage_accumulation_candidate(self):
        eng = TradePlanEngine()
        plan = eng.compute(100.0, "LONG", structure=_struct(), atr=2.0,
                           state=State.SLEEPING, sub_stage="ACCUMULATION")
        assert plan.status == STATUS_CANDIDATE

    def test_formal_states_active(self):
        eng = TradePlanEngine()
        for st in (State.START_CONFIRMED, State.CONTINUATION):
            plan = eng.compute(100.0, "LONG", structure=_struct(), atr=2.0, state=st)
            assert plan.status == STATUS_ACTIVE, st
            assert plan.chase_status == "ok"

    def test_data_missing_still_not_legal(self):
        """数据不足优先于状态判定，仍是不可用计划。"""
        eng = TradePlanEngine()
        plan = eng.compute(None, "LONG", state=State.START_CONFIRMED)
        assert plan.status == STATUS_NOT_LEGAL
        assert "数据不足" in plan.plan_reason

    def test_no_state_backward_compat(self):
        eng = TradePlanEngine()
        plan = eng.compute(100.0, "LONG", structure=_struct(), atr=2.0)
        assert plan.status == STATUS_ACTIVE


class TestV13TradePlanVersionFreeze:
    """§19：正式 Setup 冻结版本；V1 → EXPIRED，V2 → NEW PLAN；禁止覆盖。"""

    def test_freeze_assigns_id_version_created_at(self):
        eng = TradePlanEngine()
        plan = eng.compute(100.0, "LONG", structure=_struct(), atr=2.0,
                           state=State.START_CONFIRMED)
        eng.freeze(plan, 1000, symbol="BTCUSDT")
        assert plan.status == STATUS_ACTIVE
        assert plan.frozen is True
        assert plan.frozen_at_ms == 1000
        assert plan.created_at == 1000
        assert plan.version == 1
        assert plan.trade_plan_id

    def test_new_setup_increments_version(self):
        """V1 → EXPIRED → 新 Setup → V2 NEW PLAN。"""
        eng = TradePlanEngine()
        plan1 = eng.compute(100.0, "LONG", structure=_struct(), atr=2.0,
                            state=State.START_CONFIRMED)
        eng.freeze(plan1, 1000, symbol="BTCUSDT")
        # 新 Setup（重新计算的新计划对象）
        plan2 = eng.compute(102.0, "LONG", structure=_struct(), atr=2.0,
                            state=State.START_CONFIRMED)
        eng.freeze(plan2, 2000, symbol="BTCUSDT")
        assert plan2.version == plan1.version + 1 == 2
        assert plan2.trade_plan_id != plan1.trade_plan_id
        assert plan2.created_at == 2000

    def test_frozen_plan_never_overwritten(self):
        """已冻结计划再次 freeze 不覆盖（§19 禁止每秒重算覆盖）。"""
        eng = TradePlanEngine()
        plan = eng.compute(100.0, "LONG", structure=_struct(), atr=2.0,
                           state=State.START_CONFIRMED)
        eng.freeze(plan, 1000, symbol="BTCUSDT")
        plan_id, version = plan.trade_plan_id, plan.version
        eng.freeze(plan, 2000, symbol="BTCUSDT")
        assert plan.trade_plan_id == plan_id
        assert plan.version == version
        assert plan.created_at == 1000

    def test_cannot_freeze_non_active(self):
        eng = TradePlanEngine()
        plan = eng.compute(100.0, "LONG", structure=_struct(), atr=2.0,
                           state=State.SLEEPING)
        eng.freeze(plan, 1000, symbol="BTCUSDT")
        assert plan.frozen is False
        assert plan.version == 0
        assert plan.trade_plan_id is None

    def test_version_independent_per_symbol(self):
        eng = TradePlanEngine()
        a1 = eng.compute(100.0, "LONG", structure=_struct(), atr=2.0,
                         state=State.START_CONFIRMED)
        b1 = eng.compute(100.0, "LONG", structure=_struct(), atr=2.0,
                         state=State.START_CONFIRMED)
        eng.freeze(a1, 1000, symbol="AAAUSDT")
        eng.freeze(b1, 1000, symbol="BBBUSDT")
        assert a1.version == 1 and b1.version == 1

    def test_expire_marks_v_expired(self):
        eng = TradePlanEngine()
        plan = eng.compute(100.0, "LONG", structure=_struct(), atr=2.0,
                           state=State.START_CONFIRMED)
        eng.freeze(plan, 1000, symbol="BTCUSDT")
        eng.expire(plan, 2000)
        assert plan.status == STATUS_EXPIRED
        assert plan.expired is True
        assert plan.frozen is True  # 冻结快照保留，便于历史追溯

    def test_to_from_dict_roundtrip(self):
        """冻结快照 to_dict → from_dict 原样还原（§19 禁止漂移）。"""
        eng = TradePlanEngine()
        plan = eng.compute(100.0, "LONG", structure=_struct(), atr=2.0,
                           state=State.START_CONFIRMED)
        eng.freeze(plan, 1234, symbol="BTCUSDT")
        restored = TradePlan.from_dict(plan.to_dict())
        assert restored.to_dict() == plan.to_dict()
        assert restored.reference_entry_low == plan.reference_entry_low
        assert restored.trade_plan_id == plan.trade_plan_id
        assert restored.version == plan.version
        assert restored.frozen is True
        assert restored.created_at == 1234
