"""State Machine 测试 — 状态转移 + evidence + fail closed。"""

from __future__ import annotations

from src.domain import (
    ConfidenceState,
    Direction,
    FeatureSnapshot,
    FeatureValue,
    State,
)
from src.health.confidence import ConfidenceTracker
from src.state_machine.machine import StateMachine


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
):
    features = {}
    for name, val in [
        ("volume_z", volume_z), ("trade_count_z", trade_count_z),
        ("taker_delta", taker_delta), ("cvd_slope_z", cvd_slope_z),
        ("oi_change_1m", oi_change_1m),
        ("directional_efficiency", directional_efficiency),
        ("flow_impact", flow_impact), ("retrace_ratio", retrace_ratio),
    ]:
        if val is not None:
            features[name] = FeatureValue(value=val, available=True, window="30s")
    return FeatureSnapshot(symbol=symbol, asof=1000, features=features)


class TestStateMachineTransitions:
    def test_sleeping_to_anomaly(self):
        """SLEEPING → ANOMALY (T1)。"""
        sm = StateMachine()
        sm.confidence._confidence["BTCUSDT"] = ConfidenceState.CONFIDENT
        snap = _snap(volume_z=5.0, trade_count_z=4.0, cvd_slope_z=4.0)
        event = sm.process(snap, now_ms=1000)
        assert event is not None
        assert event.new_state == State.ANOMALY
        assert event.previous_state == State.SLEEPING
        assert len(event.evidence) > 0

    def test_anomaly_to_sleeping(self):
        """ANOMALY → SLEEPING (T2) — 异常消退。"""
        sm = StateMachine(anomaly_decay_s=0.1)  # 短 decay 加速测试
        sm.confidence._confidence["BTCUSDT"] = ConfidenceState.CONFIDENT
        # 触发 anomaly
        snap1 = _snap(volume_z=5.0, cvd_slope_z=4.0)
        sm.process(snap1, now_ms=1000)
        assert sm.get_symbol("BTCUSDT").state == State.ANOMALY
        # 异常消退
        snap2 = _snap(volume_z=0.5, cvd_slope_z=0.5)
        event = sm.process(snap2, now_ms=2000)  # 超过 decay
        assert event is not None
        assert event.new_state == State.SLEEPING

    def test_rejected_to_cooldown(self):
        """REJECTED → COOLDOWN (T11)。"""
        sm = StateMachine(cooldown_s=0.1)
        sym = sm.get_symbol("BTCUSDT")
        sym.state = State.REJECTED
        snap = _snap()
        event = sm.process(snap, now_ms=1000)
        assert event is not None
        assert event.new_state == State.COOLDOWN

    def test_withdrawal_to_cooldown(self):
        """WITHDRAWAL → COOLDOWN (T10)。"""
        sm = StateMachine(cooldown_s=0.1)
        sym = sm.get_symbol("BTCUSDT")
        sym.state = State.WITHDRAWAL
        snap = _snap()
        event = sm.process(snap, now_ms=1000)
        assert event is not None
        assert event.new_state == State.COOLDOWN

    def test_cooldown_to_sleeping(self):
        """COOLDOWN → SLEEPING (T12)。"""
        sm = StateMachine(cooldown_s=0.1)
        sym = sm.get_symbol("BTCUSDT")
        sym.state = State.COOLDOWN
        sym.cooldown_until_ms = 500  # 已过期
        snap = _snap()
        event = sm.process(snap, now_ms=1000)
        assert event is not None
        assert event.new_state == State.SLEEPING

    def test_no_transition_returns_none(self):
        """无状态变化 → None。"""
        sm = StateMachine()
        sm.confidence._confidence["BTCUSDT"] = ConfidenceState.CONFIDENT
        snap = _snap(volume_z=0.5)  # 不触发 anomaly
        event = sm.process(snap, now_ms=1000)
        assert event is None


class TestStateMachineFailClosed:
    def test_stale_blocks_confirm(self):
        """关键数据 STALE → 禁止 CONFIRMED。"""
        sm = StateMachine()
        sm.confidence._confidence["BTCUSDT"] = ConfidenceState.UNKNOWN
        snap = _snap(volume_z=10.0, trade_count_z=10.0)
        event = sm.process(snap, now_ms=1000)
        # UNKNOWN → 不触发 anomaly
        assert event is None
        assert sm.get_symbol("BTCUSDT").state == State.SLEEPING

    def test_every_transition_has_evidence(self):
        """每次状态变化必须有 evidence。"""
        sm = StateMachine()
        sm.confidence._confidence["BTCUSDT"] = ConfidenceState.CONFIDENT
        snap = _snap(volume_z=5.0, trade_count_z=4.0, cvd_slope_z=4.0)
        event = sm.process(snap, now_ms=1000)
        assert event is not None
        # SLEEPING→ANOMALY 必须有 evidence
        assert len(event.evidence) > 0
