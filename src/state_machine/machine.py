"""State Machine — 状态转换与 transition guards。

依据：STATE_MACHINE.md §1-§3
完整 12 条转移 + guard + Fail Closed 约束。
每个 symbol 维护独立状态机实例。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from src.domain import (
    AnalysisEvent,
    ConfidenceState,
    Direction,
    Evidence,
    FeatureSnapshot,
    State,
    Veto,
)
from src.health.confidence import ConfidenceTracker
from src.detectors.anomaly import AnomalyDetector, AnomalyResult
from src.detectors.continuation_withdrawal import (
    ContinuationDetector,
    ExhaustionDetector,
    WithdrawalDetector,
)
from src.detectors.false_start import FalseStartFilter
from src.detectors.startup import StartupDetector


@dataclass
class SymbolStateMachine:
    """单个 symbol 的状态机。"""

    symbol: str
    state: State = State.SLEEPING
    direction: Direction | None = None
    # 证据持续时间追踪
    anomaly_first_seen_ms: int | None = None
    suspected_first_seen_ms: int | None = None
    confirmed_first_seen_ms: int | None = None
    cooldown_until_ms: int = 0
    # 上次检测结果缓存
    last_anomaly: AnomalyResult | None = None


class StateMachine:
    """状态机 — 整合所有 detector，管理状态转移。

    转移依据 STATE_MACHINE.md §10.1 的 12 条 guard。
    """

    def __init__(
        self,
        anomaly_detector: AnomalyDetector | None = None,
        startup_detector: StartupDetector | None = None,
        false_start_filter: FalseStartFilter | None = None,
        continuation_detector: ContinuationDetector | None = None,
        exhaustion_detector: ExhaustionDetector | None = None,
        withdrawal_detector: WithdrawalDetector | None = None,
        confidence_tracker: ConfidenceTracker | None = None,
        anomaly_decay_s: float = 30.0,
        cooldown_s: float = 300.0,
    ) -> None:
        self.anomaly = anomaly_detector or AnomalyDetector()
        self.startup = startup_detector or StartupDetector()
        self.false_start = false_start_filter or FalseStartFilter()
        self.continuation = continuation_detector or ContinuationDetector()
        self.exhaustion = exhaustion_detector or ExhaustionDetector()
        self.withdrawal = withdrawal_detector or WithdrawalDetector()
        self.confidence = confidence_tracker or ConfidenceTracker()
        self.anomaly_decay_ms = int(anomaly_decay_s * 1000)
        self.cooldown_ms = int(cooldown_s * 1000)

        self._symbols: dict[str, SymbolStateMachine] = {}

    def get_symbol(self, symbol: str) -> SymbolStateMachine:
        if symbol not in self._symbols:
            self._symbols[symbol] = SymbolStateMachine(symbol=symbol)
        return self._symbols[symbol]

    def process(
        self,
        snap: FeatureSnapshot,
        now_ms: int,
    ) -> AnalysisEvent | None:
        """处理一个 FeatureSnapshot，可能产出 AnalysisEvent。

        返回 None 表示无状态变化。
        """
        sym = self.get_symbol(snap.symbol)
        confidence = self.confidence.get(snap.symbol)
        old_state = sym.state

        new_state = old_state
        evidence: list[Evidence] = []
        vetoes: list[Veto] = []
        direction = sym.direction

        # COOLDOWN → SLEEPING (T12)
        if old_state == State.COOLDOWN and now_ms >= sym.cooldown_until_ms:
            new_state = State.SLEEPING
            sym.direction = None

        # SLEEPING → ANOMALY (T1)
        if old_state == State.SLEEPING:
            anomaly_result = self.anomaly.detect(snap, confidence)
            sym.last_anomaly = anomaly_result
            if anomaly_result.is_anomaly:
                new_state = State.ANOMALY
                sym.anomaly_first_seen_ms = now_ms
                evidence = anomaly_result.evidence
                if anomaly_result.direction_hint:
                    direction = Direction(anomaly_result.direction_hint) if anomaly_result.direction_hint in ("LONG", "SHORT") else None
                    sym.direction = direction

        # ANOMALY → SLEEPING (T2) / → SUSPECTED_START (T3)
        elif old_state == State.ANOMALY:
            anomaly_result = self.anomaly.detect(snap, confidence)
            sym.last_anomaly = anomaly_result
            if not anomaly_result.is_anomaly:
                # 检查是否持续 decay 窗口无复发
                if sym.anomaly_first_seen_ms and (now_ms - sym.anomaly_first_seen_ms) > self.anomaly_decay_ms:
                    new_state = State.SLEEPING
                    sym.direction = None
            else:
                # 尝试升级到 SUSPECTED_START
                startup_result = self.startup.detect(snap, anomaly_result, confidence)
                if startup_result.suspected:
                    new_state = State.SUSPECTED_START
                    sym.suspected_first_seen_ms = now_ms
                    direction = startup_result.direction
                    sym.direction = direction
                    evidence = startup_result.evidence

        # SUSPECTED_START → REJECTED (T4) / → START_CONFIRMED (T5)
        elif old_state == State.SUSPECTED_START:
            anomaly_result = self.anomaly.detect(snap, confidence)
            startup_result = self.startup.detect(
                snap, anomaly_result, confidence,
                hold_duration_s=(now_ms - (sym.suspected_first_seen_ms or now_ms)) / 1000.0,
            )
            # False Start Filter
            fs_result = self.false_start.check(
                snap, direction,
                is_confident=(confidence == ConfidenceState.CONFIDENT),
            )
            vetoes = fs_result.vetoes

            if fs_result.rejected:
                # hard veto → REJECTED
                new_state = State.REJECTED
                evidence = startup_result.evidence
            elif startup_result.confirmed:
                new_state = State.START_CONFIRMED
                sym.confirmed_first_seen_ms = now_ms
                evidence = startup_result.evidence

        # START_CONFIRMED → CONTINUATION (T6)
        elif old_state == State.START_CONFIRMED:
            cont_result = self.continuation.detect(snap, direction)
            if cont_result.is_continuing:
                new_state = State.CONTINUATION
                evidence = cont_result.evidence

        # CONTINUATION → EXHAUSTION (T7) / 维持
        elif old_state == State.CONTINUATION:
            exh_result = self.exhaustion.detect(snap, direction)
            if exh_result.is_exhausted:
                new_state = State.EXHAUSTION
                evidence = exh_result.evidence
            else:
                cont_result = self.continuation.detect(snap, direction)
                evidence = cont_result.evidence

        # EXHAUSTION → WITHDRAWAL (T8) / → CONTINUATION (T9 回退)
        elif old_state == State.EXHAUSTION:
            wd_result = self.withdrawal.detect(snap, direction)
            if wd_result.is_withdrawal:
                new_state = State.WITHDRAWAL
                evidence = wd_result.evidence
            else:
                # 回退检查
                exh_result = self.exhaustion.detect(snap, direction)
                if not exh_result.is_exhausted:
                    new_state = State.CONTINUATION
                    evidence = exh_result.evidence

        # WITHDRAWAL → COOLDOWN (T10)
        elif old_state == State.WITHDRAWAL:
            new_state = State.COOLDOWN
            sym.cooldown_until_ms = now_ms + self.cooldown_ms

        # REJECTED → COOLDOWN (T11)
        elif old_state == State.REJECTED:
            new_state = State.COOLDOWN
            sym.cooldown_until_ms = now_ms + self.cooldown_ms

        # 状态变化时产出 AnalysisEvent
        if new_state != old_state:
            sym.state = new_state
            return AnalysisEvent(
                symbol=snap.symbol,
                direction=direction,
                previous_state=old_state,
                new_state=new_state,
                evidence=evidence,
                vetoes=vetoes,
                asof=now_ms,
                confidence_state=confidence,
            )

        return None
