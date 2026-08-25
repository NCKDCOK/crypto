"""§66.2 State Hysteresis — 状态滞回：降级需要连续失去核心条件，hard Veto 立即失效。"""

from __future__ import annotations

import pytest

from src.config import SupervisionConfig
from src.domain import State, Veto, VetoSeverity, VetoType
from src.supervision.state_pool import PoolName
from src.supervision.supervisor import (
    SupervisionAction,
    SupervisorEngine,
)


def _engine(min_dwell_s: float = 0.001, streak: int = 3) -> SupervisorEngine:
    cfg = SupervisionConfig(min_pool_dwell_s=min_dwell_s, hysteresis_downgrade_streak=streak)
    return SupervisorEngine(cfg)


def _hard_veto(veto_type: VetoType = VetoType.RAPID_RETRACE) -> Veto:
    return Veto(type=veto_type, triggered=True, severity=VetoSeverity.HARD)


class TestSingleDropNoDowngrade:
    """单次评分下降不降级。"""

    def test_single_failure_keeps_streak_below_threshold(self):
        eng = _engine()
        eng.update("BTCUSDT", State.START_CONFIRMED, now_ms=0)
        d = eng.evaluate("BTCUSDT", core_conditions_met=False, now_ms=1000)
        assert d is not None
        assert d.action == SupervisionAction.STAY
        assert d.condition_fail_streak == 1
        rec = eng.get_record("BTCUSDT")
        assert rec.last_action == SupervisionAction.STAY
        assert rec.condition_fail_streak == 1

    def test_recovery_resets_streak(self):
        """失去一次核心条件后恢复 → 计数清零，永不降级。"""
        eng = _engine()
        eng.update("BTCUSDT", State.START_CONFIRMED, now_ms=0)
        eng.evaluate("BTCUSDT", core_conditions_met=False, now_ms=0)
        d = eng.evaluate("BTCUSDT", core_conditions_met=True, now_ms=10_000)
        assert d is not None and d.action == SupervisionAction.STAY
        assert eng.get_record("BTCUSDT").condition_fail_streak == 0


class TestConsecutiveLossDowngrade:
    """连续失去证据才降级。"""

    def test_downgrade_after_threshold_consecutive_failures(self):
        eng = _engine()
        eng.update("BTCUSDT", State.START_CONFIRMED, now_ms=0)
        for i in range(1, 3):
            d = eng.evaluate("BTCUSDT", core_conditions_met=False, now_ms=20_000 * i)
            assert d.action == SupervisionAction.STAY, f"第 {i} 次不应降级"
        d = eng.evaluate("BTCUSDT", core_conditions_met=False, now_ms=20_000 * 3)
        assert d is not None
        assert d.action == SupervisionAction.DOWNGRADE
        assert d.reason.startswith("连续 3 次失去核心条件")
        # 降级后计数清零
        assert eng.get_record("BTCUSDT").condition_fail_streak == 0

    def test_streak_configurable(self):
        eng = _engine(streak=2)
        eng.update("BTCUSDT", State.CONTINUATION, now_ms=0)
        d1 = eng.evaluate("BTCUSDT", core_conditions_met=False, now_ms=1000, force=True)
        assert d1.action == SupervisionAction.STAY
        d2 = eng.evaluate("BTCUSDT", core_conditions_met=False, now_ms=2000, force=True)
        assert d2.action == SupervisionAction.DOWNGRADE


class TestHardVetoImmediate:
    """明确 Veto 可以立即失效。"""

    def test_hard_veto_invalidates_immediately(self):
        eng = _engine()
        eng.update("BTCUSDT", State.START_CONFIRMED, now_ms=0)
        d = eng.evaluate("BTCUSDT", core_conditions_met=True,
                         vetoes=[_hard_veto()], now_ms=1000)
        assert d is not None
        assert d.action == SupervisionAction.INVALIDATE
        assert "Veto" in d.reason
        assert eng.get_record("BTCUSDT").condition_fail_streak == 0

    def test_hard_veto_ignores_dwell_protection(self):
        """驻留保护不豁免 hard Veto（明确 Veto 立即失效）。"""
        eng = _engine(min_dwell_s=60.0)
        eng.update("BTCUSDT", State.START_CONFIRMED, now_ms=0)
        d = eng.evaluate("BTCUSDT", core_conditions_met=False,
                         vetoes=[_hard_veto()], now_ms=5_000)
        assert d.action == SupervisionAction.INVALIDATE

    def test_soft_veto_not_immediate(self):
        """soft Veto 不构成明确失效，仍走滞回。"""
        eng = _engine()
        eng.update("BTCUSDT", State.START_CONFIRMED, now_ms=0)
        veto = Veto(type=VetoType.ONE_BAR_SPIKE, triggered=True, severity=VetoSeverity.SOFT)
        d = eng.evaluate("BTCUSDT", core_conditions_met=True, vetoes=[veto], now_ms=1000)
        assert d.action == SupervisionAction.STAY


class TestDwellProtection:
    """§10 驻留保护：进入新池后 min_pool_dwell_s 内不降级。"""

    def test_failures_within_dwell_held(self):
        eng = _engine(min_dwell_s=60.0)
        eng.update("BTCUSDT", State.START_CONFIRMED, now_ms=0)
        # 60s 内连续 3 次失去条件 → 滞回到位但驻留未满 → 仍 STAY
        for i in range(1, 4):
            d = eng.evaluate("BTCUSDT", core_conditions_met=False, now_ms=10_000 * i)
            assert d.action == SupervisionAction.STAY
            assert "驻留保护" in d.reason

    def test_downgrade_after_dwell_elapsed(self):
        eng = _engine(min_dwell_s=60.0)
        eng.update("BTCUSDT", State.START_CONFIRMED, now_ms=0)
        for i in range(1, 4):
            d = eng.evaluate("BTCUSDT", core_conditions_met=False, now_ms=10_000 * i)
            assert d.action == SupervisionAction.STAY
        # 驻留已过（>60s），再连续 3 次 → 降级
        for i in range(4, 7):
            d = eng.evaluate("BTCUSDT", core_conditions_met=False, now_ms=25_000 * i)
            if i < 6:
                assert d.action == SupervisionAction.STAY
        assert d.action == SupervisionAction.DOWNGRADE


class TestCadence:
    """§6 每池独立监督节奏。"""

    def test_not_due_returns_none(self):
        eng = _engine()
        eng.update("BTCUSDT", State.START_CONFIRMED, now_ms=0)
        # 新入池立即检查（design A）→ 首次评估直接出决策
        d0 = eng.evaluate("BTCUSDT", core_conditions_met=True, now_ms=0)
        assert d0 is not None
        assert d0.next_check_at == 2_000  # CONFIRMED 池默认 2s
        # 未到期 → None
        assert eng.evaluate("BTCUSDT", core_conditions_met=True, now_ms=1_000) is None
        # 到期 → 有决策
        d = eng.evaluate("BTCUSDT", core_conditions_met=True, now_ms=2_000)
        assert d is not None
        assert d.next_check_at == 4_000  # 2s 间隔

    def test_force_bypasses_cadence(self):
        eng = _engine()
        eng.update("BTCUSDT", State.START_CONFIRMED, now_ms=0)
        d = eng.evaluate("BTCUSDT", core_conditions_met=True, now_ms=1_000, force=True)
        assert d is not None

    def test_transition_triggers_immediate_check(self):
        eng = _engine()
        eng.update("BTCUSDT", State.SLEEPING, now_ms=0)
        rec = eng.records["BTCUSDT"]
        assert rec.next_check_at == 0  # 新入池立即检查
        # 状态迁移 → 立即检查
        rec = eng.update("BTCUSDT", State.ANOMALY, now_ms=5_000)
        assert rec.next_check_at == 5_000

    def test_pool_interval_from_config(self):
        cfg = SupervisionConfig()
        eng = SupervisorEngine(cfg)
        assert eng.interval_sec(PoolName.NORMAL) == cfg.normal_interval_sec
        assert eng.interval_sec(PoolName.ANOMALY) == cfg.anomaly_interval_sec
        assert eng.interval_sec(PoolName.ARCHIVE) == cfg.archive_interval_sec


class TestMetadata:
    """§7 每 symbol 监督元数据。"""

    def test_record_fields(self):
        eng = _engine()
        rec = eng.update("BTCUSDT", State.EXHAUSTION, setup_type="DISTRIBUTION", now_ms=1234)
        assert rec.symbol == "BTCUSDT"
        assert rec.current_pool == PoolName.RISK
        assert rec.current_state == State.EXHAUSTION
        assert rec.setup_type == "DISTRIBUTION"
        assert rec.entered_pool_at == 1234
        assert rec.entered_state_at == 1234
        assert rec.last_transition_at == 1234

    def test_pool_migration_resets_dwell_and_streak(self):
        eng = _engine()
        eng.update("BTCUSDT", State.START_CONFIRMED, now_ms=0)
        eng.evaluate("BTCUSDT", core_conditions_met=False, now_ms=1000)
        assert eng.get_record("BTCUSDT").condition_fail_streak == 1
        # 迁移到新池 → 计数清零 + entered_pool_at 更新
        rec = eng.update("BTCUSDT", State.SUSPECTED_START, now_ms=10_000)
        assert rec.condition_fail_streak == 0
        assert rec.entered_pool_at == 10_000

    def test_by_pool_grouping(self):
        eng = _engine()
        eng.update("AAAUSDT", State.ANOMALY, now_ms=0)
        eng.update("BBBUSDT", State.ANOMALY, now_ms=0)
        eng.update("CCCUSDT", State.SLEEPING, now_ms=0)
        groups = eng.by_pool()
        assert [r.symbol for r in groups[PoolName.ANOMALY]] == ["AAAUSDT", "BBBUSDT"]
        assert [r.symbol for r in groups[PoolName.NORMAL]] == ["CCCUSDT"]
        assert groups[PoolName.EXIT] == []

    def test_evaluate_unregistered_raises(self):
        eng = _engine()
        with pytest.raises(KeyError):
            eng.evaluate("NOPEUSDT", core_conditions_met=True, now_ms=0)