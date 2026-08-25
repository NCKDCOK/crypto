"""Paper Position Manager — §29–§32 模拟仓位监督 + 双退出（动态 vs 静态）。

依据：V1.3 更新计划 §29 / §30 / §31 / §32 / §50 / §61。

- §29：持仓期持续记录 current_pnl / MFE / MAE / current_state /
  fund_flow_change / continuation_change / withdrawal_risk_change / distribution_risk_change。
- §30：MFE/MAE 定义 — 入场后最大有利/不利波动（校准核心数据）。
- §31：退出原因 9 种（ExitReason）。
- §32：必须同时记录 A. Dynamic Exit（资金撤离退出）和 B. Static Plan
  （原 TP/Stop 固定执行），以后比较「跟资金撤」是否更优。
  用户决策：动态退出后静态跟踪直到触及原 TP1 或失效（孰先），24h 上限 → TIME_EXPIRED。
- §50：停机 > 1h 的旧 START_CONFIRMED/CONTINUATION 失效 → 持仓 INVALIDATION_HIT。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.simulation.enums import (
    STATIC_OUTCOME_INVALIDATED,
    STATIC_OUTCOME_STOP,
    STATIC_OUTCOME_TIME_EXPIRED,
    STATIC_OUTCOME_TP1,
    ExitReason,
)
from src.simulation.queue import SimulationQueueItem

logger = logging.getLogger(__name__)

# 资金撤离类退出（Dynamic Exit，§32A）：dynamic_exit_price 记录其退出价
_DYNAMIC_REASONS = {
    ExitReason.SIGNAL_WITHDRAWAL.value,
    ExitReason.DISTRIBUTION_EXIT.value,
    ExitReason.DIRECTION_FLIP.value,
    ExitReason.INVALIDATION_HIT.value,
    ExitReason.MANUAL_CLOSE.value,
}


@dataclass
class PaperPosition:
    """模拟持仓（§29 记录字段 + §32 双退出字段）。"""

    simulation_id: str
    snapshot_id: str
    symbol: str
    direction: str
    entry_time: int
    entry_price: float
    entry_reason: str
    entry_confirmation: dict[str, Any] = field(default_factory=dict)
    # 冻结 Trade Plan（静态计划执行用，§32B）
    entry_zone_low: float | None = None
    entry_zone_high: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    tp3: float | None = None
    stop_price: float | None = None
    # 持续记录（§29）
    status: str = "OPEN"
    current_price: float | None = None
    current_pnl_pct: float = 0.0
    mfe_pct: float = 0.0          # §30 最大有利波动
    mae_pct: float = 0.0          # §30 最大不利波动
    # 动态退出确认计时器
    withdrawal_since: int | None = None
    distribution_since: int | None = None
    flip_since: int | None = None
    invalidated_since: int | None = None
    # 退出
    exit_reason: str | None = None
    exit_time: int | None = None
    exit_price: float | None = None
    exit_is_dynamic: bool = False
    # 静态计划跟踪（§32B）
    static_tracking: bool = False
    static_track_until_ms: int | None = None
    static_exit_time: int | None = None
    static_outcome: str | None = None
    static_exit_price: float | None = None
    static_pnl_pct: float | None = None
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False
    stop_hit: bool = False
    invalidated: bool = False
    result_persisted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "simulation_id": self.simulation_id,
            "snapshot_id": self.snapshot_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_time": self.entry_time,
            "entry_price": self.entry_price,
            "entry_reason": self.entry_reason,
            "entry_confirmation": self.entry_confirmation,
            "entry_zone_low": self.entry_zone_low,
            "entry_zone_high": self.entry_zone_high,
            "tp1": self.tp1, "tp2": self.tp2, "tp3": self.tp3,
            "stop_price": self.stop_price,
            "status": self.status,
            "current_price": self.current_price,
            "current_pnl_pct": self.current_pnl_pct,
            "mfe_pct": self.mfe_pct,
            "mae_pct": self.mae_pct,
            "withdrawal_since": self.withdrawal_since,
            "distribution_since": self.distribution_since,
            "flip_since": self.flip_since,
            "invalidated_since": self.invalidated_since,
            "exit_reason": self.exit_reason,
            "exit_time": self.exit_time,
            "exit_price": self.exit_price,
            "exit_is_dynamic": self.exit_is_dynamic,
            "static_tracking": self.static_tracking,
            "static_track_until_ms": self.static_track_until_ms,
            "static_exit_time": self.static_exit_time,
            "static_outcome": self.static_outcome,
            "static_exit_price": self.static_exit_price,
            "static_pnl_pct": self.static_pnl_pct,
            "tp1_hit": self.tp1_hit, "tp2_hit": self.tp2_hit, "tp3_hit": self.tp3_hit,
            "stop_hit": self.stop_hit,
            "invalidated": self.invalidated,
            "result_persisted": self.result_persisted,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PaperPosition":
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in allowed})


class PaperPositionManager:
    """模拟持仓监督引擎。"""

    def __init__(
        self,
        cfg: Any,
        repository: Any | None = None,
        *,
        distribution_risk_threshold: float = 65.0,
        distribution_confirm_s: float = 30.0,
        invalidation_confirm_s: float = 5.0,
    ) -> None:
        self.cfg = cfg
        self.repo = repository
        self.distribution_risk_threshold = distribution_risk_threshold
        self.distribution_confirm_ms = int(distribution_confirm_s * 1000.0)
        self.invalidation_confirm_ms = int(invalidation_confirm_s * 1000.0)
        self.static_track_ms = int(cfg.static_track_max_hours * 3_600_000.0)
        self._positions: dict[str, PaperPosition] = {}
        self.events: list[dict[str, Any]] = []

    # ── 增删查 ──

    def open(self, item: SimulationQueueItem, now_ms: int) -> PaperPosition:
        """§28 模拟成交 → 建仓（OPEN）。"""
        snap = item.snapshot or {}
        plan = snap.get("trade_plan") or {}
        pos = PaperPosition(
            simulation_id=item.simulation_id,
            snapshot_id=item.snapshot_id,
            symbol=item.symbol,
            direction=snap.get("direction") or "LONG",
            entry_time=item.entered_at or now_ms,
            entry_price=item.entry_price,
            entry_reason=item.entry_reason or "",
            entry_confirmation=item.entry_confirmation or {},
            entry_zone_low=plan.get("reference_entry_low"),
            entry_zone_high=plan.get("reference_entry_high"),
            tp1=plan.get("tp1"),
            tp2=plan.get("tp2"),
            tp3=plan.get("tp3"),
            stop_price=plan.get("invalidation_price"),
        )
        self._positions[pos.simulation_id] = pos
        if self.repo is not None:
            self.repo.save_simulation_position(pos.to_dict())
        logger.info("[simulation] %s 模拟建仓 sim=%s @%s %s",
                    pos.symbol, pos.simulation_id, pos.entry_price, pos.direction)
        return pos

    def restore_position(self, pos_dict: dict[str, Any]) -> None:
        """§48 重启恢复：重新载入持仓（OPEN / 静态跟踪中的）。"""
        pos = PaperPosition.from_dict(pos_dict)
        self._positions[pos.simulation_id] = pos

    def get(self, simulation_id: str) -> PaperPosition | None:
        return self._positions.get(simulation_id)

    def all(self) -> list[PaperPosition]:
        return list(self._positions.values())

    def open_positions(self) -> list[PaperPosition]:
        return [p for p in self._positions.values() if p.status == "OPEN"]

    # ── 逐 tick 驱动 ──

    def tick_symbol(self, symbol: str, ctx: dict[str, Any], now_ms: int) -> list[dict[str, Any]]:
        occurred: list[dict[str, Any]] = []
        for pos in [p for p in self._positions.values()
                    if p.symbol == symbol and (p.status == "OPEN" or p.static_tracking)]:
            ev = self.tick(pos, ctx, now_ms)
            if ev is not None:
                occurred.append(ev)
                self.events.append(ev)
                if self.repo is not None:
                    self.repo.save_simulation_event(ev)
                    self.repo.save_simulation_position(pos.to_dict())
        return occurred

    def tick(self, pos: PaperPosition, ctx: dict[str, Any], now_ms: int) -> dict[str, Any] | None:
        price = ctx.get("price")
        if price is not None:
            pos.current_price = price
            pos.current_pnl_pct = self._pnl_pct(pos, price)
            if pos.current_pnl_pct > pos.mfe_pct:
                pos.mfe_pct = pos.current_pnl_pct
            if pos.current_pnl_pct < pos.mae_pct:
                pos.mae_pct = pos.current_pnl_pct

        if pos.status == "OPEN":
            # §32B 固定计划执行优先：触及 TP1 / Stop 立即平仓
            static_hit = self._static_target_hit(pos, price)
            if static_hit:
                return self._close_static(pos, static_hit, price, now_ms)
            # 超时保护：持仓超过 static_track_max_hours 未平 → TIME_EXPIRED
            if now_ms - pos.entry_time > self.static_track_ms:
                return self._close(pos, ExitReason.TIME_EXPIRED.value, price, now_ms,
                                   dynamic=False, static_outcome=STATIC_OUTCOME_TIME_EXPIRED)
            dyn = self._dynamic_exit(pos, ctx, now_ms)
            if dyn:
                return self._close(pos, dyn, price, now_ms, dynamic=True)
            return None

        if pos.static_tracking:
            return self._static_track_step(pos, price, ctx, now_ms)
        return None

    # ── 静态计划执行（§32B）──

    def _static_target_hit(self, pos: PaperPosition, price: float | None) -> str | None:
        """OPEN 期固定 TP/Stop 执行：谁先到谁平。返回退出原因或 None。"""
        if price is None:
            return None
        if pos.direction == "LONG":
            if pos.tp1 is not None and price >= pos.tp1:
                return ExitReason.TP1_HIT.value
            if pos.stop_price is not None and price <= pos.stop_price:
                return ExitReason.INVALIDATION_HIT.value  # 原 Setup 被破坏（止损）
        else:
            if pos.tp1 is not None and price <= pos.tp1:
                return ExitReason.TP1_HIT.value
            if pos.stop_price is not None and price >= pos.stop_price:
                return ExitReason.INVALIDATION_HIT.value
        return None

    def _close_static(self, pos: PaperPosition, reason: str, price: float | None,
                      now_ms: int) -> dict[str, Any]:
        """静态计划先触发 → 直接平仓（无需后续静态跟踪）。"""
        self._record_target_flags(pos, price)
        pos.static_exit_time = now_ms
        if reason == ExitReason.TP1_HIT.value:
            pos.static_outcome = STATIC_OUTCOME_TP1
        else:
            pos.stop_hit = True
            pos.static_outcome = STATIC_OUTCOME_STOP
        pos.static_exit_price = price
        pos.static_pnl_pct = self._pnl_pct(pos, price) if price is not None else None
        return self._close(pos, reason, price, now_ms, dynamic=False)

    def _static_track_step(self, pos: PaperPosition, price: float | None, ctx: dict[str, Any],
                           now_ms: int) -> dict[str, Any] | None:
        """动态退出后的后台静态跟踪（§32 + 用户决策：TP1 或失效孰先，24h 上限）。"""
        if price is not None:
            self._record_target_flags(pos, price)
            outcome = self._static_target_outcome(pos, price)
            if outcome is not None:
                pos.static_outcome = outcome[0]
                pos.static_exit_price = outcome[1]
                pos.static_exit_time = now_ms
                pos.static_pnl_pct = self._pnl_pct(pos, outcome[1])
                pos.static_tracking = False
                return self._finish_tracking(pos, now_ms, "STATIC_DONE", pos.static_outcome)
        if ctx.get("invalidated"):
            pos.static_outcome = STATIC_OUTCOME_INVALIDATED
            pos.static_exit_time = now_ms
            pos.invalidated = True
            pos.static_tracking = False
            return self._finish_tracking(pos, now_ms, "STATIC_DONE", STATIC_OUTCOME_INVALIDATED)
        if pos.static_track_until_ms is not None and now_ms >= pos.static_track_until_ms:
            pos.static_outcome = STATIC_OUTCOME_TIME_EXPIRED
            pos.static_exit_time = now_ms
            pos.static_tracking = False
            return self._finish_tracking(pos, now_ms, "STATIC_DONE", STATIC_OUTCOME_TIME_EXPIRED)
        return None

    def _static_target_outcome(self, pos: PaperPosition, price: float) -> tuple[str, float] | None:
        """静态跟踪结局：TP1 / Stop 孰先。"""
        if pos.direction == "LONG":
            if pos.tp1 is not None and price >= pos.tp1:
                return STATIC_OUTCOME_TP1, price
            if pos.stop_price is not None and price <= pos.stop_price:
                return STATIC_OUTCOME_STOP, price
        else:
            if pos.tp1 is not None and price <= pos.tp1:
                return STATIC_OUTCOME_TP1, price
            if pos.stop_price is not None and price >= pos.stop_price:
                return STATIC_OUTCOME_STOP, price
        return None

    def _record_target_flags(self, pos: PaperPosition, price: float | None) -> None:
        if price is None:
            return
        if pos.direction == "LONG":
            if pos.tp1 is not None and price >= pos.tp1:
                pos.tp1_hit = True
            if pos.tp2 is not None and price >= pos.tp2:
                pos.tp2_hit = True
            if pos.tp3 is not None and price >= pos.tp3:
                pos.tp3_hit = True
            if pos.stop_price is not None and price <= pos.stop_price:
                pos.stop_hit = True
        else:
            if pos.tp1 is not None and price <= pos.tp1:
                pos.tp1_hit = True
            if pos.tp2 is not None and price <= pos.tp2:
                pos.tp2_hit = True
            if pos.tp3 is not None and price <= pos.tp3:
                pos.tp3_hit = True
            if pos.stop_price is not None and price >= pos.stop_price:
                pos.stop_hit = True

    # ── 动态退出（§29/§31 资金撤离类）──

    def _dynamic_exit(self, pos: PaperPosition, ctx: dict[str, Any], now_ms: int) -> str | None:
        cfg = self.cfg
        # 原 Setup 失效（§50 停机/状态离开正式范围）
        if ctx.get("invalidated"):
            if pos.invalidated_since is None:
                pos.invalidated_since = now_ms
            elif now_ms - pos.invalidated_since >= self.invalidation_confirm_ms:
                pos.invalidated = True
                return ExitReason.INVALIDATION_HIT.value
        else:
            pos.invalidated_since = None
        # 资金撤离（Withdrawal 触发）
        if ctx.get("withdrawal_active"):
            if pos.withdrawal_since is None:
                pos.withdrawal_since = now_ms
            elif now_ms - pos.withdrawal_since >= int(cfg.withdrawal_confirm_s * 1000.0):
                return ExitReason.SIGNAL_WITHDRAWAL.value
        else:
            pos.withdrawal_since = None
        # 派发风险
        dist = ctx.get("distribution_risk")
        if dist is not None and dist > self.distribution_risk_threshold:
            if pos.distribution_since is None:
                pos.distribution_since = now_ms
            elif now_ms - pos.distribution_since >= self.distribution_confirm_ms:
                return ExitReason.DISTRIBUTION_EXIT.value
        else:
            pos.distribution_since = None
        # 方向翻转
        d = ctx.get("direction")
        if d not in (None, "NEUTRAL") and pos.direction and d != pos.direction:
            if pos.flip_since is None:
                pos.flip_since = now_ms
            elif now_ms - pos.flip_since >= int(cfg.direction_flip_confirm_s * 1000.0):
                return ExitReason.DIRECTION_FLIP.value
        else:
            pos.flip_since = None
        return None

    # ── 平仓 / 定稿 ──

    def _close(self, pos: PaperPosition, reason: str, price: float | None, now_ms: int,
               *, dynamic: bool, static_outcome: str | None = None) -> dict[str, Any]:
        pos.status = "CLOSED"
        pos.exit_reason = reason
        pos.exit_price = price
        pos.exit_time = now_ms
        pos.exit_is_dynamic = dynamic
        if dynamic:
            # §32 用户决策：动态退出后继续静态跟踪（原 TP1/Stop 固定执行，孰先；24h 上限）
            pos.static_tracking = True
            pos.static_track_until_ms = now_ms + self.static_track_ms
        elif static_outcome is not None and pos.static_outcome is None:
            pos.static_outcome = static_outcome
        self._maybe_finalize(pos, now_ms)
        return {
            "simulation_id": pos.simulation_id, "symbol": pos.symbol, "asof": now_ms,
            "old_status": "OPEN", "new_status": "CLOSED", "reason": reason,
        }

    def _finish_tracking(self, pos: PaperPosition, now_ms: int,
                         new_status: str, reason: str) -> dict[str, Any]:
        self._maybe_finalize(pos, now_ms)
        return {
            "simulation_id": pos.simulation_id, "symbol": pos.symbol, "asof": now_ms,
            "old_status": "STATIC_TRACKING", "new_status": new_status, "reason": reason,
        }

    def _maybe_finalize(self, pos: PaperPosition, now_ms: int) -> None:
        if pos.result_persisted:
            return
        if pos.status != "CLOSED" or pos.static_tracking:
            return  # 动态退出后静态跟踪未完，暂不定稿
        res = self._build_result(pos)
        pos.result_persisted = True
        if self.repo is not None:
            self.repo.save_simulation_result(res)
            self.repo.save_simulation_position(pos.to_dict())
        logger.info("[simulation] %s 结果定稿 sim=%s reason=%s pnl=%.2f%%",
                    pos.symbol, pos.simulation_id, pos.exit_reason, res.get("pnl_pct", 0.0) * 100)

    def _build_result(self, pos: PaperPosition) -> dict[str, Any]:
        """§61 simulation_results 行。"""
        duration = (pos.exit_time - pos.entry_time) / 3_600_000.0 if pos.exit_time else 0.0
        exit_pnl = self._pnl_pct(pos, pos.exit_price) if pos.exit_price is not None else pos.current_pnl_pct
        return {
            "simulation_id": pos.simulation_id,
            "snapshot_id": pos.snapshot_id,
            "symbol": pos.symbol,
            "direction": pos.direction,
            "entry_time": pos.entry_time,
            "entry_price": pos.entry_price,
            "entry_reason": pos.entry_reason,
            "entry_confirmation": pos.entry_confirmation,
            "exit_time": pos.exit_time,
            "exit_price": pos.exit_price,
            "exit_reason": pos.exit_reason,
            "pnl_pct": round(exit_pnl, 4),
            "mfe_pct": round(pos.mfe_pct, 4),
            "mae_pct": round(pos.mae_pct, 4),
            "tp1_hit": pos.tp1_hit,
            "tp2_hit": pos.tp2_hit,
            "tp3_hit": pos.tp3_hit,
            "invalidation_hit": pos.invalidated or pos.exit_reason == ExitReason.INVALIDATION_HIT.value,
            "dynamic_exit_price": pos.exit_price if pos.exit_is_dynamic else None,
            "static_plan_result": {
                "outcome": pos.static_outcome,
                "static_exit_price": pos.static_exit_price,
                "static_pnl_pct": round(pos.static_pnl_pct, 4) if pos.static_pnl_pct is not None else None,
                "tp1_hit": pos.tp1_hit,
                "tp2_hit": pos.tp2_hit,
                "tp3_hit": pos.tp3_hit,
                "stop_hit": pos.stop_hit,
                "duration_hours": round((pos.static_exit_time - pos.entry_time) / 3_600_000.0, 3)
                if pos.static_exit_time is not None else round(duration, 3),
                "tracked_until_ms": pos.static_track_until_ms,
            },
            "duration_hours": round(duration, 3),
            "closed_at": pos.exit_time,
        }

    def _pnl_pct(self, pos: PaperPosition, price: float | None) -> float:
        if not price or not pos.entry_price:
            return 0.0
        sign = 1.0 if pos.direction == "LONG" else -1.0
        return (price - pos.entry_price) / pos.entry_price * 100.0 * sign