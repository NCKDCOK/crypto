"""Supervisor Engine — 已进入生命周期的 symbol 的 state-aware 监督（V1.3 §7–§10）。

职责不是发现新币，而是：

> 已经进入某个生命周期的币，接下来应该重点监督什么。

- 每 symbol 维护 §7 元数据（current_pool / current_state / setup_type /
  entered_pool_at / entered_state_at / last_transition_at /
  supervision_level / next_check_at）。
- 每池独立监督节奏（configs/supervision.yaml → SupervisionConfig），
  后台 feature 计算仍保持 1~2s 不变。
- §10 状态滞回：连续 `hysteresis_downgrade_streak` 次失去核心条件才降级；
  明确 hard Veto 立即失效；进入新池后 `min_pool_dwell_s` 内不降级。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from src.config import SupervisionConfig
from src.domain import State, Veto, VetoSeverity
from src.supervision.state_pool import (
    PoolName,
    StatePoolManager,
    SupervisionLevel,
)


class SupervisionAction(str, Enum):
    """监督动作（§9 每池的 Upgrade/Downgrade/Expire/Vetoes 的引擎侧决策）。"""

    STAY = "stay"            # 维持当前池（含滞回保护中的暂时失去条件）
    DOWNGRADE = "downgrade"  # 连续 N 次失去核心条件 → 降级（§10）
    INVALIDATE = "invalidate"  # 明确 hard Veto → 立即失效（§10）


@dataclass
class SymbolSupervisionRecord:
    """§7 每 symbol 监督元数据。"""

    symbol: str
    current_pool: PoolName
    current_state: State
    setup_type: str | None = None
    entered_pool_at: int = 0
    entered_state_at: int = 0
    last_transition_at: int = 0
    supervision_level: SupervisionLevel = SupervisionLevel.LOW
    next_check_at: int = 0
    # 滞回（§10）：连续失去核心条件的次数；命中阈值且驻留期满才降级
    condition_fail_streak: int = 0
    last_action: SupervisionAction = SupervisionAction.STAY

    def to_dict(self) -> dict:
        """JSON 友好字典（枚举转 .value，供 API/UI 使用）。"""
        return {
            "symbol": self.symbol,
            "current_pool": self.current_pool.value,
            "current_state": self.current_state.value,
            "setup_type": self.setup_type,
            "entered_pool_at": self.entered_pool_at,
            "entered_state_at": self.entered_state_at,
            "last_transition_at": self.last_transition_at,
            "supervision_level": self.supervision_level.value,
            "next_check_at": self.next_check_at,
            "condition_fail_streak": self.condition_fail_streak,
            "last_action": self.last_action.value,
        }


@dataclass(frozen=True)
class SupervisionDecision:
    """一次监督评估的决策输出。"""

    symbol: str
    action: SupervisionAction
    pool: PoolName
    state: State
    condition_fail_streak: int
    threshold: int
    reason: str
    supervision_question: str
    next_check_at: int


_INTERVAL_ATTR: dict[PoolName, str] = {
    PoolName.NORMAL: "normal_interval_sec",
    PoolName.ANOMALY: "anomaly_interval_sec",
    PoolName.WATCH: "watch_interval_sec",
    PoolName.CONFIRMED: "confirmed_interval_sec",
    PoolName.CONTINUATION: "continuation_interval_sec",
    PoolName.RISK: "risk_interval_sec",
    PoolName.EXIT: "exit_interval_sec",
    PoolName.ARCHIVE: "archive_interval_sec",
}


class SupervisorEngine:
    """State-aware 监督引擎（V1.3 §7–§10）。"""

    def __init__(
        self,
        config: SupervisionConfig,
        pool_manager: StatePoolManager | None = None,
    ) -> None:
        self.config = config
        self.pools = pool_manager or StatePoolManager()
        self.records: dict[str, SymbolSupervisionRecord] = {}

    # ── 元数据更新（§7） ───────────────────────────────────────────────
    def update(
        self,
        symbol: str,
        state: State,
        setup_type: str | None = None,
        labels: Iterable[str] = (),
        now_ms: int = 0,
    ) -> SymbolSupervisionRecord:
        """更新一个 symbol 的生命周期信息，维护 §7 元数据。

        - 池迁移（state/labels 派生变化）→ 重置 entered_pool_at /
          condition_fail_streak，迁移后立即可检查（next_check_at=now）。
        - 仅 State 变化（池不变）→ 更新 entered_state_at。
        - 无变化 → 幂等，保留全部时间戳。
        """
        pool = self.pools.pool_for(state, labels)
        rec = self.records.get(symbol)
        if rec is None:
            rec = SymbolSupervisionRecord(
                symbol=symbol,
                current_pool=pool,
                current_state=state,
                setup_type=setup_type,
                entered_pool_at=now_ms,
                entered_state_at=now_ms,
                last_transition_at=now_ms,
                supervision_level=self.pools.level_for(pool),
                next_check_at=now_ms,
            )
            self.records[symbol] = rec
            return rec

        pool_changed = pool != rec.current_pool
        state_changed = state != rec.current_state
        if not pool_changed and not state_changed:
            return rec  # 幂等

        # 池迁移 → §10 驻留窗口重新计时 + 滞回计数清零
        if pool_changed:
            rec.current_pool = pool
            rec.entered_pool_at = now_ms
            rec.condition_fail_streak = 0
            rec.supervision_level = self.pools.level_for(pool)
        if state_changed:
            rec.current_state = state
            rec.entered_state_at = now_ms
        if setup_type is not None:
            rec.setup_type = setup_type
        rec.last_transition_at = now_ms
        rec.next_check_at = now_ms  # 迁移后立即检查
        return rec

    # ── 节奏（§6 每池独立监督频率） ────────────────────────────────────
    def interval_sec(self, pool: PoolName) -> float:
        return float(getattr(self.config, _INTERVAL_ATTR[pool]))

    def is_due(self, symbol: str, now_ms: int) -> bool:
        rec = self.records.get(symbol)
        if rec is None:
            return False
        return now_ms >= rec.next_check_at

    def due_symbols(self, now_ms: int) -> list[str]:
        return [sym for sym in self.records if self.is_due(sym, now_ms)]

    # ── 滞回评估（§10 + §66.2） ────────────────────────────────────────
    # V1.4 §6 职责边界：本方法只管 symbol 级监督池滞回（是否降级到更低监督池），
    #   **不控制 PublishedRecommendation 退出**——推荐退出统一由 RecommendationLifecycleEngine
    #   （tick_fast/tick_slow）决定。三层职责固定：StatePool Supervisor 管池 /
    #   Recommendation Lifecycle 管推荐存在 / Simulation Supervisor 管模拟仓位。
    def evaluate(
        self,
        symbol: str,
        core_conditions_met: bool,
        vetoes: Iterable[Veto] = (),
        now_ms: int = 0,
        force: bool = False,
    ) -> SupervisionDecision | None:
        """执行一次 state-aware 监督评估（symbol 级池滞回，不管推荐退出）。

        `core_conditions_met=False` 表示本 tick 失去该池核心条件（§9 Stay）。
        `vetoes` 中的 hard Veto 立即失效（无视滞回/驻留）。
        未到 `next_check_at` 时返回 None（节奏控制，`force=True` 跳过）。
        """
        rec = self.records.get(symbol)
        if rec is None:
            raise KeyError(f"supervisor 未注册 symbol: {symbol}（先调用 update）")
        if not force and not self.is_due(symbol, now_ms):
            return None

        threshold = self.config.hysteresis_downgrade_streak
        dwell_ms = int(self.config.min_pool_dwell_s * 1000)
        in_dwell = (now_ms - rec.entered_pool_at) < dwell_ms

        hard_veto = next(
            (v for v in vetoes
             if v.triggered and v.severity == VetoSeverity.HARD),
            None,
        )

        if hard_veto is not None:
            # 明确 Veto → 立即失效（不做滞回、不做驻留保护）
            rec.condition_fail_streak = 0
            action = SupervisionAction.INVALIDATE
            reason = f"明确 Veto：{hard_veto.type.value}"
        elif core_conditions_met:
            rec.condition_fail_streak = 0
            action = SupervisionAction.STAY
            reason = "核心条件满足"
        elif in_dwell:
            # 驻留保护（§10）：新入池 grace period 内不累计滞回计数、不降级
            action = SupervisionAction.STAY
            reason = (
                f"失去核心条件，驻留保护 {dwell_ms // 1000}s 内不降级"
                f"（0/{threshold}，期满后重新计数）"
            )
        else:
            rec.condition_fail_streak += 1
            if rec.condition_fail_streak >= threshold:
                action = SupervisionAction.DOWNGRADE
                reason = f"连续 {rec.condition_fail_streak} 次失去核心条件"
                rec.condition_fail_streak = 0
            else:
                action = SupervisionAction.STAY
                reason = f"失去核心条件（{rec.condition_fail_streak}/{threshold}，滞回保护）"

        rec.last_action = action
        rec.next_check_at = now_ms + int(self.interval_sec(rec.current_pool) * 1000)

        return SupervisionDecision(
            symbol=symbol,
            action=action,
            pool=rec.current_pool,
            state=rec.current_state,
            condition_fail_streak=rec.condition_fail_streak,
            threshold=threshold,
            reason=reason,
            supervision_question=self.pools.spec(rec.current_pool).supervision_question,
            next_check_at=rec.next_check_at,
        )

    # ── 查询 ───────────────────────────────────────────────────────────
    def get_record(self, symbol: str) -> SymbolSupervisionRecord | None:
        return self.records.get(symbol)

    def all_records(self) -> list[SymbolSupervisionRecord]:
        return list(self.records.values())

    def by_pool(self) -> dict[PoolName, list[SymbolSupervisionRecord]]:
        """按池分组（§65 监督台 API 的引擎侧来源）。"""
        grouped: dict[PoolName, list[SymbolSupervisionRecord]] = {
            pool: [] for pool in self.pools.pool_names
        }
        for rec in self.records.values():
            grouped[rec.current_pool].append(rec)
        return grouped