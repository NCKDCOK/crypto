"""推荐快照 — §20 Immutable RecommendationSnapshot + §22 自动快照服务。

依据：V1.3 更新计划 §20 / §21 / §22 / §47。

- §20：每次正式推荐（START_CONFIRMED 且过 Top 门槛）冻结一份不可变快照，
  包含产生该推荐的全部上下文（子评分 / 证据 / Veto / 突破结构 / 现货合约 /
  Trade Plan），供 Replay 复现与 Simulation 引用。
- §21：冻结理由 — 防止 Replay 用未来数据污染历史判定；冻结后不可修改。
- §22：正式推荐 → 自动 RecommendationSnapshot → SimulationQueue 状态 WATCHING。
- §47：仅 LIVE 模式自动构建正式快照（由 runtime 门控调用）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# §18 正式 Trade Plan 状态范围（与 trade_plan.FORMAL_STATES 一致，避免循环导入）
FORMAL_STATES: frozenset = frozenset({"START_CONFIRMED", "CONTINUATION"})

# 无效 Setup（进入后原 Setup 视为失效，§26 检查 3）
INVALID_SETUPS: frozenset = frozenset({"NONE", "DISTRIBUTION", "PUMP_RISK"})


@dataclass(frozen=True)
class RecommendationSnapshot:
    """§20 不可变推荐快照。

    frozen=True：任何字段赋值都会抛 FrozenInstanceError（§21 不可变约束，
    测试 §66.3 钉死）。to_dict() 返回普通 dict 供持久化 / API。
    """

    snapshot_id: str
    symbol: str
    timestamp: int
    market_regime: dict[str, Any]
    state: str
    setup_type: str
    direction: str | None
    primary_timeframe: str
    current_price: float | None
    opportunity_score: float | None
    signal_confirmation: float | None
    data_confidence: float | None
    all_subscores: dict[str, Any]
    all_evidence: list[dict[str, Any]]
    all_vetoes: list[dict[str, Any]]
    breakout_state: dict[str, Any]
    structure_state: dict[str, Any]
    spot_perp_state: dict[str, Any]
    trade_plan: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "market_regime": self.market_regime,
            "state": self.state,
            "setup_type": self.setup_type,
            "direction": self.direction,
            "primary_timeframe": self.primary_timeframe,
            "current_price": self.current_price,
            "opportunity_score": self.opportunity_score,
            "signal_confirmation": self.signal_confirmation,
            "data_confidence": self.data_confidence,
            "all_subscores": self.all_subscores,
            "all_evidence": self.all_evidence,
            "all_vetoes": self.all_vetoes,
            "breakout_state": self.breakout_state,
            "structure_state": self.structure_state,
            "spot_perp_state": self.spot_perp_state,
            "trade_plan": self.trade_plan,
        }


class RecommendationSnapshotService:
    """§22 正式推荐 → 自动快照。

    门槛（§13/§62，全部配置化，runtime 从 cfg.ranking 注入）：
    - state ∈ {START_CONFIRMED, CONTINUATION}
    - opportunity_score >= min_opportunity
    - signal_confirmation >= min_signal_confirmation
    - data_confidence >= min_data_confidence
    - Trade Plan 合法（status == ACTIVE）
    - 非 stale、pump_risk 不高于上限

    去重（§19 版本管理）：runtime 以 trade_plan_id 为准，同一正式计划只冻结一份。
    snapshot_id 格式（§42 风格）：{SYMBOL}-{YYYYMMDD}-{NNN}
    """

    def __init__(
        self,
        *,
        min_opportunity: float = 70.0,
        min_signal_confirmation: float = 75.0,
        min_data_confidence: float = 85.0,
        max_pump_risk: float = 50.0,
        primary_timeframe: str = "15m",
    ) -> None:
        self.min_opportunity = min_opportunity
        self.min_signal_confirmation = min_signal_confirmation
        self.min_data_confidence = min_data_confidence
        self.max_pump_risk = max_pump_risk
        self.primary_timeframe = primary_timeframe
        self._seq: dict[str, int] = {}

    def _make_id(self, symbol: str, timestamp: int) -> str:
        seq = self._seq.get(symbol, 0) + 1
        self._seq[symbol] = seq
        date = datetime.fromtimestamp(timestamp / 1000.0, tz=timezone.utc).strftime("%Y%m%d")
        return f"{symbol}-{date}-{seq:03d}"

    def passes_gate(
        self,
        *,
        state: Any,
        opportunity_score: float | None,
        signal_confirmation: float | None,
        data_confidence: float | None,
        trade_plan: dict[str, Any] | None,
        pump_risk: float | None,
        stale_flag: float | None,
    ) -> bool:
        """§13 门槛判定。任一不满足 → False（不产生正式推荐）。"""
        if state is None or str(getattr(state, "value", state)) not in FORMAL_STATES:
            return False
        if opportunity_score is None or opportunity_score < self.min_opportunity:
            return False
        if signal_confirmation is None or signal_confirmation < self.min_signal_confirmation:
            return False
        if data_confidence is None or data_confidence < self.min_data_confidence:
            return False
        plan = trade_plan or {}
        if plan.get("status") != "ACTIVE":
            return False
        if stale_flag:
            return False
        if pump_risk is not None and pump_risk > self.max_pump_risk:
            return False
        return True

    def build(
        self,
        *,
        symbol: str,
        timestamp: int,
        market_regime: dict[str, Any],
        state: Any,
        setup_type: str,
        direction: str | None,
        current_price: float | None,
        opportunity_score: float | None,
        signal_confirmation: float | None,
        data_confidence: float | None,
        all_subscores: dict[str, Any],
        all_evidence: list[dict[str, Any]],
        all_vetoes: list[dict[str, Any]],
        breakout_state: dict[str, Any],
        structure_state: dict[str, Any],
        spot_perp_state: dict[str, Any],
        trade_plan: dict[str, Any],
    ) -> RecommendationSnapshot:
        """冻结一份不可变快照（§20 全字段）。"""
        return RecommendationSnapshot(
            snapshot_id=self._make_id(symbol, timestamp),
            symbol=symbol,
            timestamp=timestamp,
            market_regime=dict(market_regime or {}),
            state=str(getattr(state, "value", state)),
            setup_type=setup_type,
            direction=direction,
            primary_timeframe=self.primary_timeframe,
            current_price=current_price,
            opportunity_score=opportunity_score,
            signal_confirmation=signal_confirmation,
            data_confidence=data_confidence,
            all_subscores=dict(all_subscores or {}),
            all_evidence=list(all_evidence or []),
            all_vetoes=list(all_vetoes or []),
            breakout_state=dict(breakout_state or {}),
            structure_state=dict(structure_state or {}),
            spot_perp_state=dict(spot_perp_state or {}),
            trade_plan=dict(trade_plan or {}),
        )