"""模拟验证包 — V1.3 P2（§11, §20–§32, §37–§39）。

模块划分：
- enums：SimulationStatus（§23 状态机）/ ExitReason（§31 九种退出原因）
- snapshot：§20 Immutable RecommendationSnapshot + §22 自动快照服务
- decision：§11 DecisionSnapshotService（首页稳定决策快照）
- revalidation：§26 入场二次验证（十一项检查）
- queue：§22–§28 SimulationQueueManager（WATCHING → … → CLOSED）
- position：§29–§32 PaperPositionManager（MFE/MAE + 动态/静态双退出）
- statistics：§37–§39 统计汇总 / 分桶 / Setup 转化率

硬约束（AI_RULES）：只做 Paper / Shadow Trading，禁止真实下单。
"""

from __future__ import annotations

from src.simulation.decision import DecisionSnapshotService
from src.simulation.enums import ExitReason, SimulationStatus
from src.simulation.position import PaperPosition, PaperPositionManager
from src.simulation.queue import SimulationQueueItem, SimulationQueueManager
from src.simulation.revalidation import (
    EntryRevalidationEngine,
    RevalidationCheck,
    RevalidationResult,
)
from src.simulation.snapshot import RecommendationSnapshot, RecommendationSnapshotService
from src.simulation.statistics import SimulationStatistics

__all__ = [
    "DecisionSnapshotService",
    "EntryRevalidationEngine",
    "ExitReason",
    "PaperPosition",
    "PaperPositionManager",
    "RevalidationCheck",
    "RevalidationResult",
    "RecommendationSnapshot",
    "RecommendationSnapshotService",
    "SimulationQueueItem",
    "SimulationQueueManager",
    "SimulationStatistics",
    "SimulationStatus",
]