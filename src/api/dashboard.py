"""FastAPI 应用 — Dashboard API。

[DEPRECATED] V1.1 统一到 runtime data model。
此模块的 DashboardService 已不再被 main.py（实际运行入口）使用。
main.py 直接调用 MarketRadarRuntime.get_radar() / get_symbol_detail() 等。
保留此文件供向后兼容参考，新代码请勿使用。

依据：epic-09 Task 09-A, V1.1 P0.6
- Market Radar：symbol/price/state/direction/health/evidence 摘要
- Symbol Detail：时间线/特征/Evidence/Veto/State transition
- Data Health：每流 freshness/reconnect/message rate
- Signal History：CONFIRMED/REJECTED/WITHDRAWAL 历史 + outcome

UI 只消费 AnalysisEvent，不订阅原始行情自行计算。
AI 解读只读 AnalysisEvent，不覆盖状态。
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field
from typing import Any

from src.domain import AnalysisEvent, State
from src.storage import InMemoryRepository

logger = logging.getLogger(__name__)

# P0.6: 弃用警告
warnings.warn(
    "src.api.dashboard.DashboardService is deprecated in V1.1. "
    "Use MarketRadarRuntime directly via src.main.",
    DeprecationWarning,
    stacklevel=2,
)


@dataclass
class DashboardData:
    """Dashboard 当前状态数据。"""

    symbols: list[dict[str, Any]] = field(default_factory=list)
    recent_events: list[dict[str, Any]] = field(default_factory=list)
    health_summary: dict[str, Any] = field(default_factory=dict)


class DashboardService:
    """Dashboard 数据服务。

    从 Repository 聚合数据，供 API 层使用。
    UI 只消费 AnalysisEvent，不复制业务逻辑。
    """

    def __init__(self, repository: InMemoryRepository | None = None) -> None:
        self.repository = repository or InMemoryRepository()
        self._latest_events: dict[str, AnalysisEvent] = {}  # symbol → latest

    def update_event(self, event: AnalysisEvent) -> None:
        """更新最新事件。"""
        self._latest_events[event.symbol] = event

    def get_market_radar(self) -> list[dict[str, Any]]:
        """获取 Market Radar 数据。

        返回所有 symbol 的 state/direction/health/evidence 摘要。
        """
        result = []
        for symbol, event in self._latest_events.items():
            result.append({
                "symbol": symbol,
                "state": event.new_state.value,
                "direction": event.direction.value if event.direction else None,
                "confidence_state": event.confidence_state.value,
                "asof": event.asof,
                "evidence_count": len(event.evidence),
                "veto_count": len(event.vetoes),
            })
        return result

    def get_symbol_detail(self, symbol: str) -> dict[str, Any] | None:
        """获取 Symbol Detail。"""
        event = self._latest_events.get(symbol)
        if event is None:
            return None
        return {
            "symbol": symbol,
            "state": event.new_state.value,
            "direction": event.direction.value if event.direction else None,
            "confidence_state": event.confidence_state.value,
            "evidence": [
                {
                    "family": e.family.value,
                    "type": e.type,
                    "value": e.value,
                    "threshold": e.threshold,
                    "passed": e.passed,
                }
                for e in event.evidence
            ],
            "vetoes": [
                {
                    "type": v.type.value,
                    "triggered": v.triggered,
                    "severity": v.severity.value,
                }
                for v in event.vetoes
            ],
        }

    def get_signal_history(self, symbol: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """获取信号历史。"""
        events = list(self.repository._analysis_events)
        if symbol:
            events = [e for e in events if e.symbol == symbol]
        events = events[-limit:]
        return [
            {
                "symbol": e.symbol,
                "state": e.new_state.value,
                "direction": e.direction.value if e.direction else None,
                "asof": e.asof,
                "evidence_count": len(e.evidence),
            }
            for e in events
        ]
