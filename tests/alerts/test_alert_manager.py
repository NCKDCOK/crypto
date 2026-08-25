"""Alert Manager 测试。"""

from __future__ import annotations

from src.alerts.manager import AlertManager, AlertRecord
from src.domain import (
    AnalysisEvent,
    ConfidenceState,
    Direction,
    Evidence,
    EvidenceFamily,
    State,
    Veto,
    VetoSeverity,
    VetoType,
)


def _make_event(
    symbol="BTCUSDT",
    new_state=State.START_CONFIRMED,
    direction=Direction.LONG,
    confidence=ConfidenceState.CONFIDENT,
    asof=1000,
):
    return AnalysisEvent(
        symbol=symbol,
        direction=direction,
        previous_state=State.SUSPECTED_START,
        new_state=new_state,
        evidence=[
            Evidence(family=EvidenceFamily.ANOMALY, type="volume_z", value=5.0, passed=True),
        ],
        vetoes=[
            Veto(type=VetoType.RAPID_RETRACE, triggered=False, severity=VetoSeverity.HARD),
        ],
        asof=asof,
        confidence_state=confidence,
    )


class TestAlertManager:
    def test_start_confirmed_triggers_alert(self):
        """START_CONFIRMED → 高等级提醒。"""
        mgr = AlertManager()
        event = _make_event(new_state=State.START_CONFIRMED)
        record = mgr.process_event(event, now_ms=1000)
        assert record is not None
        assert record.state == "START_CONFIRMED"
        assert "START_CONFIRMED" in record.message

    def test_withdrawal_triggers_alert(self):
        """WITHDRAWAL → 高等级撤离提醒。"""
        mgr = AlertManager()
        event = _make_event(new_state=State.WITHDRAWAL)
        record = mgr.process_event(event, now_ms=1000)
        assert record is not None
        assert record.state == "WITHDRAWAL"

    def test_exhaustion_triggers_alert(self):
        """EXHAUSTION → 风险提醒。"""
        mgr = AlertManager()
        event = _make_event(new_state=State.EXHAUSTION, confidence=ConfidenceState.DEGRADED)
        record = mgr.process_event(event, now_ms=1000)
        assert record is not None

    def test_non_alert_state_no_alert(self):
        """非告警状态 → 不告警。"""
        mgr = AlertManager()
        event = _make_event(new_state=State.SLEEPING)
        record = mgr.process_event(event, now_ms=1000)
        assert record is None

    def test_cooldown_prevents_repeat(self):
        """冷却期内不重复告警。"""
        mgr = AlertManager()
        event = _make_event(new_state=State.START_CONFIRMED, asof=1000)
        # 第一次告警
        record1 = mgr.process_event(event, now_ms=1000)
        assert record1 is not None
        # 冷却期内（300s）再触发 → 不告警
        record2 = mgr.process_event(event, now_ms=2000)
        assert record2 is None
        # 冷却后 → 告警
        record3 = mgr.process_event(event, now_ms=400_000)  # 400s > 300s
        assert record3 is not None

    def test_low_confidence_blocks_alert(self):
        """confidence 不足 → 不告警。"""
        mgr = AlertManager()
        # START_CONFIRMED 需要 CONFIDENT，但给 UNKNOWN
        event = _make_event(
            new_state=State.START_CONFIRMED,
            confidence=ConfidenceState.UNKNOWN,
        )
        record = mgr.process_event(event, now_ms=1000)
        assert record is None

    def test_alert_history(self):
        mgr = AlertManager()
        mgr.process_event(_make_event("BTC", State.START_CONFIRMED), now_ms=1000)
        mgr.process_event(_make_event("ETH", State.WITHDRAWAL), now_ms=2000)
        history = mgr.get_history()
        assert len(history) == 2

    def test_sender_called(self):
        """自定义 sender 被调用。"""
        sent: list[AlertRecord] = []

        def sender(record):
            sent.append(record)

        mgr = AlertManager(sender=sender)
        mgr.process_event(_make_event(State.START_CONFIRMED), now_ms=1000)
        assert len(sent) == 1
