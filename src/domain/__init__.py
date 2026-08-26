"""资金行为驱动行情分析系统 — domain contracts。

所有模块通过本包定义的事件对象交互，禁止各模块自定义 JSON。
依据：docs/DATA_MODEL.md
"""

from __future__ import annotations

from .enums import (
    AggressorSide,
    ConfidenceState,
    Direction,
    EvidenceFamily,
    HealthLevel,
    KlineInterval,
    State,
    SystemMode,
    VetoSeverity,
    VetoType,
)
from .events import (
    AnalysisEvent,
    Evidence,
    FeatureSnapshot,
    FeatureValue,
    FundingRateSnapshot,
    HealthStatus,
    KlineEvent,
    LongShortRatioSnapshot,
    OpenInterestSnapshot,
    TradeEvent,
    Veto,
)

__all__ = [
    # enums
    "AggressorSide",
    "ConfidenceState",
    "Direction",
    "EvidenceFamily",
    "HealthLevel",
    "KlineInterval",
    "State",
    "SystemMode",
    "VetoSeverity",
    "VetoType",
    # events
    "TradeEvent",
    "KlineEvent",
    "OpenInterestSnapshot",
    "FundingRateSnapshot",
    "LongShortRatioSnapshot",
    "HealthStatus",
    "FeatureSnapshot",
    "FeatureValue",
    "AnalysisEvent",
    "Evidence",
    "Veto",
]
