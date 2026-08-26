"""正式推荐子系统（V1.4）— Published Recommendation 生命周期。

依据：crypto_radar_v1.4_fix_update_plan.md §一 ~ §六 / §二十七 / §三十三 / §三十四。

组成：
- models.PublishedRecommendation：正式推荐实体（独立生命周期枚举，StateMachine 不动）。
- gate.RecommendationGate：正式门禁（§三：标准确认 + 强确认）。
- repository.PublishedRecommendationRepository：存储（首页数据源）。
- lifecycle.RecommendationLifecycleEngine：发布后生命周期状态机（Supervisor 监督节奏驱动）。
"""

from src.recommendations.gate import (
    BREAKOUT_SETUPS,
    FORMAL_STATES,
    GateContext,
    GateResult,
    NON_BREAKOUT_SETUPS,
    RecommendationGate,
)
from src.recommendations.lifecycle import (
    LifecycleContext,
    LifecycleDecision,
    RecommendationLifecycleEngine,
)
from src.recommendations.models import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    PublishedRecommendation,
    RecommendationStatus,
)
from src.recommendations.repository import PublishedRecommendationRepository

__all__ = [
    "ACTIVE_STATUSES",
    "TERMINAL_STATUSES",
    "BREAKOUT_SETUPS",
    "FORMAL_STATES",
    "NON_BREAKOUT_SETUPS",
    "GateContext",
    "GateResult",
    "LifecycleContext",
    "LifecycleDecision",
    "PublishedRecommendation",
    "PublishedRecommendationRepository",
    "RecommendationGate",
    "RecommendationLifecycleEngine",
    "RecommendationStatus",
]