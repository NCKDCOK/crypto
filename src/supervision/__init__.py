"""状态监督（V1.3 §5–§10）— State Pool + Supervisor Engine。

把 deep symbols 按当前生命周期（State，以及派生标签）分配到监督池，
并基于每池节奏做 state-aware 监督。池是**派生分组**：不扩展主 State 枚举。
"""

from __future__ import annotations

from .state_pool import (
    PoolName,
    PoolSpec,
    StatePoolManager,
    SupervisionLevel,
)
from .supervisor import (
    SupervisionAction,
    SupervisionDecision,
    SupervisorEngine,
    SymbolSupervisionRecord,
)

__all__ = [
    "PoolName",
    "PoolSpec",
    "StatePoolManager",
    "SupervisionLevel",
    "SupervisionAction",
    "SupervisionDecision",
    "SupervisorEngine",
    "SymbolSupervisionRecord",
]