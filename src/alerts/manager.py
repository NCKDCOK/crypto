"""Alert Manager — 仅消费 AnalysisEvent，发出提醒。

依据：epic-09 Task 09-E
- START_CONFIRMED → 高等级提醒
- EXHAUSTION → 风险提醒
- WITHDRAWAL → 高等级撤离提醒
- 可配置阈值与冷却
- 不自动执行交易
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from src.domain import AnalysisEvent, State

logger = logging.getLogger(__name__)


@dataclass
class AlertRule:
    """告警规则。"""

    state: State
    min_confidence: str  # CONFIDENT / DEGRADED / UNKNOWN
    cooldown_s: float  # 冷却时间
    message_template: str


DEFAULT_RULES: dict[State, AlertRule] = {
    State.START_CONFIRMED: AlertRule(
        state=State.START_CONFIRMED,
        min_confidence="CONFIDENT",
        cooldown_s=300,
        message_template="🟢 {symbol} START_CONFIRMED direction={direction} — 证据链通过",
    ),
    State.EXHAUSTION: AlertRule(
        state=State.EXHAUSTION,
        min_confidence="DEGRADED",
        cooldown_s=180,
        message_template="🟡 {symbol} EXHAUSTION — 推动效率下降，注意风险",
    ),
    State.WITHDRAWAL: AlertRule(
        state=State.WITHDRAWAL,
        min_confidence="CONFIDENT",
        cooldown_s=600,
        message_template="🔴 {symbol} WITHDRAWAL — 资金撤离确认",
    ),
}


@dataclass
class AlertRecord:
    """告警记录。"""

    symbol: str
    state: str
    direction: str | None
    message: str
    asof: int


class AlertManager:
    """告警管理器。

    仅消费 AnalysisEvent，根据规则发出提醒。
    支持冷却（同一 symbol 同一状态在冷却期内不重复告警）。
    """

    def __init__(
        self,
        rules: dict[State, AlertRule] | None = None,
        sender: Callable[[AlertRecord], Any] | None = None,
    ) -> None:
        self.rules = rules or DEFAULT_RULES
        self.sender = sender
        self._last_alert_time: dict[str, dict[State, int]] = {}  # symbol → state → time
        self._history: list[AlertRecord] = []

    def process_event(self, event: AnalysisEvent, now_ms: int) -> AlertRecord | None:
        """处理一个 AnalysisEvent，可能发出告警。

        Returns:
            AlertRecord 如果发出了告警，否则 None。
        """
        rule = self.rules.get(event.new_state)
        if rule is None:
            return None

        # confidence 检查
        confidence_order = {"CONFIDENT": 3, "DEGRADED": 2, "UNKNOWN": 1}
        event_conf = confidence_order.get(event.confidence_state.value, 0)
        required_conf = confidence_order.get(rule.min_confidence, 0)
        if event_conf < required_conf:
            return None

        # 冷却检查
        symbol_states = self._last_alert_time.get(event.symbol, {})
        last_time = symbol_states.get(event.new_state)
        if last_time is not None:
            cooldown_ms = int(rule.cooldown_s * 1000)
            if now_ms - last_time < cooldown_ms:
                return None  # 冷却中

        # 生成告警
        direction = event.direction.value if event.direction else "NEUTRAL"
        message = rule.message_template.format(
            symbol=event.symbol,
            direction=direction,
        )

        record = AlertRecord(
            symbol=event.symbol,
            state=event.new_state.value,
            direction=direction,
            message=message,
            asof=event.asof,
        )

        # 记录
        if event.symbol not in self._last_alert_time:
            self._last_alert_time[event.symbol] = {}
        self._last_alert_time[event.symbol][event.new_state] = now_ms
        self._history.append(record)

        # 发送
        if self.sender:
            try:
                result = self.sender(record)
                if hasattr(result, "__await__"):
                    import asyncio
                    asyncio.get_event_loop().create_task(result)
            except Exception:
                logger.exception("alert_send_failed symbol=%s", event.symbol)

        logger.info("alert_sent symbol=%s state=%s", event.symbol, event.new_state.value)
        return record

    def get_history(self) -> list[AlertRecord]:
        return list(self._history)
