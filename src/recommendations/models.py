"""Published Recommendation — V1.4 正式推荐生命周期实体。

依据：crypto_radar_v1.4_fix_update_plan.md §一 / §二 / §二十七。

设计决策（用户已确认）：
- 推荐状态使用**独立枚举** RecommendationStatus，不扩展 StateMachine 的 State。
- 与 simulation.snapshot.RecommendationSnapshot（发布时刻不可变快照）并存：
  PublishedRecommendation 是带生命周期的实体，通过 snapshot_id / simulation_id 挂接
  现有快照与模拟队列系统。
- published_* 字段在发布时冻结不可变；current_* 持续更新；禁止覆盖历史发布值（§二十七）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RecommendationStatus(str, Enum):
    """正式推荐生命周期状态（计划 §二，用户确认：独立枚举，StateMachine 不动）。"""

    PENDING = "PENDING"            # 已过 Gate，等待发布窗口（当前流程直接发布，保留枚举）
    PUBLISHED = "PUBLISHED"        # 已发布（首页活跃区）
    MONITORING = "MONITORING"      # 正常监督中
    WEAKENING = "WEAKENING"        # 条件减弱但未失效，继续监督（§三十三）
    RISK = "RISK"                  # 风险池（派发 / 撤离 / 动能衰竭）
    EXITED = "EXITED"              # 资金撤离 / 正常退出
    EXPIRED = "EXPIRED"            # 生命周期超时
    INVALIDATED = "INVALIDATED"    # Hard Veto / 结构失效 / 方向翻转


# 首页活跃区：PUBLISHED → RISK 持续展示；终态移出首页活跃区（§九/§十）
ACTIVE_STATUSES: frozenset = frozenset({
    RecommendationStatus.PUBLISHED,
    RecommendationStatus.MONITORING,
    RecommendationStatus.WEAKENING,
    RecommendationStatus.RISK,
})

TERMINAL_STATUSES: frozenset = frozenset({
    RecommendationStatus.EXITED,
    RecommendationStatus.EXPIRED,
    RecommendationStatus.INVALIDATED,
})


# 退出原因（计划 §三十 B 资金监督退出 与 §三十一 两类退出比较）
EXIT_REASON_WITHDRAWAL = "SIGNAL_WITHDRAWAL"
EXIT_REASON_DIRECTION_FLIP = "DIRECTION_FLIP"
EXIT_REASON_HARD_VETO = "HARD_VETO"
EXIT_REASON_STATE_EXIT = "STATE_EXIT"          # 离开正式范围（非 WITHDRAWAL）
EXIT_REASON_EXHAUSTION = "EXHAUSTION"          # 动能衰竭
EXIT_REASON_DISTRIBUTION = "DISTRIBUTION"      # 派发增强
EXIT_REASON_INVALIDATION_HIT = "INVALIDATION_HIT"  # 价格触及失效位
EXIT_REASON_EXPIRED = "EXPIRED"                # 生命周期超时
EXIT_REASON_DATA_CRITICAL = "DATA_CRITICAL"    # 核心数据源严重异常（§三十三例外）
EXIT_REASON_MANUAL = "MANUAL_CLOSE"


@dataclass
class PublishedRecommendation:
    """一条正式推荐（计划 §二 全部字段 + 生命周期管理字段）。"""

    # ── 身份 / 时间 ──
    recommendation_id: str
    symbol: str
    created_at: int                 # Gate 通过（记录创建）时间
    published_at: int               # 正式发布（5m 收盘窗口）时间
    closed_at: int | None = None

    # ── Setup ──
    side: str | None = None         # LONG / SHORT
    setup_type: str = ""
    # V1.4 修复：主周期拆三段（§四 trigger=5m 收盘 / §3.2 confirmation=15m / context=1h）
    trigger_timeframe: str = "5m"        # 正式触发绑定的新 5m 收盘
    confirmation_timeframe: str = "15m"  # 强确认周期
    context_timeframe: str = "1h"        # 上下文同向周期
    # 废弃别名（保留向后兼容；to_dict 映射为 trigger_timeframe，禁止新代码使用）
    primary_timeframe: str = "15m"

    # ── 发布时冻结（§二十七：不可变）──
    published_state: str = ""
    published_price: float | None = None
    published_opportunity_score: float | None = None
    published_signal_confirmation: float | None = None
    published_data_confidence: float | None = None
    entry_zone_low: float | None = None
    entry_zone_high: float | None = None
    invalidation_price: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    tp3: float | None = None
    rr1: float | None = None
    rr2: float | None = None
    rr3: float | None = None

    # ── 实时（持续更新，绝不回写 published_*）──
    current_state: str | None = None
    current_price: float | None = None
    current_opportunity_score: float | None = None
    current_signal_confirmation: float | None = None
    current_data_confidence: float | None = None

    # ── 生命周期 ──
    status: RecommendationStatus = RecommendationStatus.PENDING
    risk_status: str | None = None   # NORMAL / EXHAUSTION / DISTRIBUTION / HIGH_WITHDRAWAL / HIGH_PUMP
    exit_reason: str | None = None
    confirmation_level: str = "STANDARD"  # STANDARD / STRONG（计划 §3.2）
    updated_at: int = 0

    # ── 关联（挂接现有快照 / 模拟队列）──
    snapshot_id: str | None = None
    simulation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON 友好字典（枚举转 .value）。"""
        return {
            "recommendation_id": self.recommendation_id,
            "symbol": self.symbol,
            "created_at": self.created_at,
            "published_at": self.published_at,
            "closed_at": self.closed_at,
            "side": self.side,
            "setup_type": self.setup_type,
            "trigger_timeframe": self.trigger_timeframe,
            "confirmation_timeframe": self.confirmation_timeframe,
            "context_timeframe": self.context_timeframe,
            "primary_timeframe": self.trigger_timeframe,   # 废弃别名=trigger
            "published_state": self.published_state,
            "current_state": self.current_state,
            "published_price": self.published_price,
            "current_price": self.current_price,
            "published_opportunity_score": self.published_opportunity_score,
            "published_signal_confirmation": self.published_signal_confirmation,
            "published_data_confidence": self.published_data_confidence,
            "current_opportunity_score": self.current_opportunity_score,
            "current_signal_confirmation": self.current_signal_confirmation,
            "current_data_confidence": self.current_data_confidence,
            "entry_zone_low": self.entry_zone_low,
            "entry_zone_high": self.entry_zone_high,
            "invalidation_price": self.invalidation_price,
            "tp1": self.tp1, "tp2": self.tp2, "tp3": self.tp3,
            "rr1": self.rr1, "rr2": self.rr2, "rr3": self.rr3,
            "status": self.status.value,
            "risk_status": self.risk_status,
            "exit_reason": self.exit_reason,
            "confirmation_level": self.confirmation_level,
            "updated_at": self.updated_at,
            "snapshot_id": self.snapshot_id,
            "simulation_id": self.simulation_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PublishedRecommendation":
        """从 to_dict() 结果还原（重启恢复用）。"""
        status = d.get("status")
        return cls(
            recommendation_id=d["recommendation_id"],
            symbol=d["symbol"],
            created_at=d.get("created_at", 0),
            published_at=d.get("published_at", 0),
            closed_at=d.get("closed_at"),
            side=d.get("side"),
            setup_type=d.get("setup_type", ""),
            trigger_timeframe=d.get("trigger_timeframe", "5m"),
            confirmation_timeframe=d.get("confirmation_timeframe", "15m"),
            context_timeframe=d.get("context_timeframe", "1h"),
            primary_timeframe=d.get("primary_timeframe", "5m"),
            published_state=d.get("published_state", ""),
            current_state=d.get("current_state"),
            published_price=d.get("published_price"),
            current_price=d.get("current_price"),
            published_opportunity_score=d.get("published_opportunity_score"),
            published_signal_confirmation=d.get("published_signal_confirmation"),
            published_data_confidence=d.get("published_data_confidence"),
            current_opportunity_score=d.get("current_opportunity_score"),
            current_signal_confirmation=d.get("current_signal_confirmation"),
            current_data_confidence=d.get("current_data_confidence"),
            entry_zone_low=d.get("entry_zone_low"),
            entry_zone_high=d.get("entry_zone_high"),
            invalidation_price=d.get("invalidation_price"),
            tp1=d.get("tp1"), tp2=d.get("tp2"), tp3=d.get("tp3"),
            rr1=d.get("rr1"), rr2=d.get("rr2"), rr3=d.get("rr3"),
            status=RecommendationStatus(status) if status else RecommendationStatus.PENDING,
            risk_status=d.get("risk_status"),
            exit_reason=d.get("exit_reason"),
            confirmation_level=d.get("confirmation_level", "STANDARD"),
            updated_at=d.get("updated_at", 0),
            snapshot_id=d.get("snapshot_id"),
            simulation_id=d.get("simulation_id"),
        )

    def is_active(self) -> bool:
        """是否仍在首页活跃区。"""
        return self.status in ACTIVE_STATUSES

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES