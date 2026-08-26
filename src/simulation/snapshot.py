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
    # V1.4 §2：绑定发布它的正式推荐 id（无 recommendation_id 禁止进模拟）
    recommendation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "market_regime": self.market_regime,
            "recommendation_id": self.recommendation_id,
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

    V1.4 §2：正式推荐门槛统一由 RecommendationGate（§三）判定——通过 → 发布
    PublishedRecommendation → 此处 build 冻结快照。本服务不再独立判定门槛
    （旧 passes_gate 已删除，禁止第二套真相）。冻结后不可修改（§21）。

    snapshot_id 格式（§42 风格）：{SYMBOL}-{YYYYMMDD}-{NNN}
    """

    def __init__(self, *, primary_timeframe: str = "5m") -> None:
        self.primary_timeframe = primary_timeframe
        self._seq: dict[str, int] = {}

    def _make_id(self, symbol: str, timestamp: int) -> str:
        seq = self._seq.get(symbol, 0) + 1
        self._seq[symbol] = seq
        date = datetime.fromtimestamp(timestamp / 1000.0, tz=timezone.utc).strftime("%Y%m%d")
        return f"{symbol}-{date}-{seq:03d}"

    # V1.4 §2：删旧 passes_gate —— 正式推荐门槛统一由 RecommendationGate（§三）判定，
    # 通过 → 发布 PublishedRecommendation → 此处 build 冻结快照。禁止第二套门槛。

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
        recommendation_id: str | None = None,
    ) -> RecommendationSnapshot:
        """冻结一份不可变快照（§20 全字段），绑定发布它的正式推荐 id。"""
        return RecommendationSnapshot(
            snapshot_id=self._make_id(symbol, timestamp),
            symbol=symbol,
            timestamp=timestamp,
            market_regime=dict(market_regime or {}),
            recommendation_id=recommendation_id,
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