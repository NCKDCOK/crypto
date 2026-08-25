"""AI 解读测试 — 只读翻译，不覆盖状态。"""

from __future__ import annotations

from src.alerts.ai_summary import generate_summary
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
    new_state=State.SUSPECTED_START,
    direction=Direction.LONG,
    confidence=ConfidenceState.CONFIDENT,
    evidence_passed=True,
    veto_triggered=False,
):
    return AnalysisEvent(
        symbol="ONGUSDT",
        direction=direction,
        previous_state=State.SLEEPING,
        new_state=new_state,
        evidence=[
            Evidence(
                family=EvidenceFamily.ANOMALY, type="volume_z",
                value=4.72, threshold=3.0, passed=evidence_passed,
            ),
        ],
        vetoes=[
            Veto(
                type=VetoType.RAPID_RETRACE,
                triggered=veto_triggered,
                severity=VetoSeverity.HARD,
            ),
        ],
        asof=1672515782136,
        confidence_state=confidence,
    )


class TestGenerateSummary:
    def test_suspected_start_summary(self):
        event = _make_event(State.SUSPECTED_START)
        summary = generate_summary(event)
        assert "ONGUSDT" in summary
        assert "SUSPECTED_START" in summary
        assert "疑似启动" in summary

    def test_confirmed_summary(self):
        event = _make_event(State.START_CONFIRMED)
        summary = generate_summary(event)
        assert "启动确认" in summary

    def test_rejected_summary(self):
        event = _make_event(State.REJECTED, veto_triggered=True)
        summary = generate_summary(event)
        assert "假启动" in summary

    def test_withdrawal_summary(self):
        event = _make_event(State.WITHDRAWAL)
        summary = generate_summary(event)
        assert "撤离" in summary

    def test_evidence_shown(self):
        event = _make_event()
        summary = generate_summary(event)
        assert "volume_z" in summary

    def test_veto_shown_when_triggered(self):
        event = _make_event(State.REJECTED, veto_triggered=True)
        summary = generate_summary(event)
        assert "rapid_retrace" in summary

    def test_summary_doesnt_override_state(self):
        """摘要不改变 AnalysisEvent 的状态。"""
        event = _make_event(State.SUSPECTED_START)
        original_state = event.new_state
        generate_summary(event)
        assert event.new_state == original_state

    def test_summary_doesnt_override_direction(self):
        """摘要不改变 direction。"""
        event = _make_event(direction=Direction.LONG)
        original_dir = event.direction
        generate_summary(event)
        assert event.direction == original_dir
