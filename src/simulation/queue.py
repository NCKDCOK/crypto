"""Simulation Queue — §22–§28 模拟队列状态机。

依据：V1.3 更新计划 §22 / §23 / §24 / §25 / §27 / §28 / §33–§36（列表数据源）。

状态机（§23）：
    WATCHING → ENTRY_ZONE_REACHED → REVALIDATING → ARMED
    → SIMULATED_ENTRY → OPEN → CLOSED
    旁路：EXPIRED / CANCELLED / INVALIDATED / MISSED

- §22：正式推荐（START_CONFIRMED 且过 Top 门槛）→ 自动快照 → WATCHING。
- §24：WATCHING 持续记录 推荐价 / Entry Zone / 当前价 / 距离 / 最大涨跌。
- §25：价格进入 reference_entry_low~high → 必须 REVALIDATING，不能直接成交。
- §27：通过 → ARMED；不通过 → CANCELLED 记录原因。
- §28：第一版模拟成交 = Entry Zone 内第一笔符合 Revalidation 的价格。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.simulation.enums import SimulationStatus
from src.simulation.revalidation import EntryRevalidationEngine, RevalidationResult
from src.simulation.snapshot import FORMAL_STATES

logger = logging.getLogger(__name__)

# 队伍上的“活跃”状态（未终态）
_ACTIVE_STATUSES = {
    SimulationStatus.WATCHING,
    SimulationStatus.ENTRY_ZONE_REACHED,
    SimulationStatus.REVALIDATING,
    SimulationStatus.ARMED,
    SimulationStatus.SIMULATED_ENTRY,
    SimulationStatus.OPEN,
}


@dataclass
class SimulationQueueItem:
    """一条模拟队列记录（§24 字段 + 状态机进度）。"""

    simulation_id: str
    snapshot_id: str
    symbol: str
    snapshot: dict[str, Any]                 # 冻结快照 to_dict 副本（§20）
    created_at: int
    status: SimulationStatus = SimulationStatus.WATCHING
    updated_at: int = 0
    # §24 WATCHING 记录
    recommendation_price: float | None = None
    entry_zone_low: float | None = None
    entry_zone_high: float | None = None
    current_price: float | None = None
    distance_pct: float | None = None        # 距 Entry Zone 中点的距离（%）
    max_gain_pct: float = 0.0                # 推荐后最大上涨（%）
    max_drawdown_pct: float = 0.0            # 推荐后最大回撤（%）
    # 状态机进度
    entry_zone_reached_at: int | None = None
    entry_zone_reached_price: float | None = None
    revalidate_started_at: int | None = None
    revalidate_result: dict[str, Any] | None = None
    armed_at: int | None = None
    entered_at: int | None = None
    entry_price: float | None = None
    entry_reason: str | None = None
    entry_confirmation: dict[str, Any] | None = None
    closed_at: int | None = None
    exit_reason: str | None = None
    exit_price: float | None = None
    fail_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "simulation_id": self.simulation_id,
            "snapshot_id": self.snapshot_id,
            "symbol": self.symbol,
            "snapshot": self.snapshot,
            "created_at": self.created_at,
            "status": self.status.value,
            "updated_at": self.updated_at,
            "recommendation_price": self.recommendation_price,
            "entry_zone_low": self.entry_zone_low,
            "entry_zone_high": self.entry_zone_high,
        }
        # 公开字段（供 UI §34/§35）：
        d.update({
            "current_price": self.current_price,
            "distance_pct": self.distance_pct,
            "max_gain_pct": self.max_gain_pct,
            "max_drawdown_pct": self.max_drawdown_pct,
            "entry_zone_reached_at": self.entry_zone_reached_at,
            "entry_zone_reached_price": self.entry_zone_reached_price,
            "revalidate_started_at": self.revalidate_started_at,
            "revalidate_result": self.revalidate_result,
            "armed_at": self.armed_at,
            "entered_at": self.entered_at,
            "entry_price": self.entry_price,
            "entry_reason": self.entry_reason,
            "entry_confirmation": self.entry_confirmation,
            "closed_at": self.closed_at,
            "exit_reason": self.exit_reason,
            "exit_price": self.exit_price,
            "fail_reason": self.fail_reason,
        })
        return d


class SimulationQueueManager:
    """模拟队列状态机。每个正式推荐一份 WATCHING 记录，逐 tick 单步推进。"""

    def __init__(
        self,
        cfg: Any,
        repository: Any | None = None,
        *,
        revalidation: EntryRevalidationEngine | None = None,
        positions: Any | None = None,
    ) -> None:
        self.cfg = cfg
        self.repo = repository
        self.revalidation = revalidation or EntryRevalidationEngine()
        self.positions = positions
        self._items: dict[str, SimulationQueueItem] = {}
        self.events: list[dict[str, Any]] = []
        self.expire_ms = int(cfg.recommendation_expire_minutes * 60_000.0)

    # ── 增删查 ──

    def create_from_snapshot(self, snapshot: dict[str, Any], now_ms: int) -> SimulationQueueItem:
        """§22 正式推荐 → WATCHING 入队。"""
        plan = snapshot.get("trade_plan") or {}
        zone_low = plan.get("reference_entry_low")
        zone_high = plan.get("reference_entry_high")
        price = snapshot.get("current_price")
        distance = None
        if price and zone_low is not None and zone_high is not None and zone_low > 0:
            center = (zone_low + zone_high) / 2.0
            distance = (price - center) / center * 100.0
        item = SimulationQueueItem(
            simulation_id=snapshot["snapshot_id"],
            snapshot_id=snapshot["snapshot_id"],
            symbol=snapshot["symbol"],
            snapshot=dict(snapshot),
            created_at=now_ms,
            updated_at=now_ms,
            recommendation_price=price,
            entry_zone_low=zone_low,
            entry_zone_high=zone_high,
            distance_pct=distance,
        )
        self._items[item.simulation_id] = item
        if self.repo is not None:
            self.repo.save_simulation_queue_item(item.to_dict())
        logger.info("[simulation] %s 入队 WATCHING sim=%s zone=%s~%s",
                    item.symbol, item.simulation_id, zone_low, zone_high)
        return item

    def restore_item(self, item_dict: dict[str, Any]) -> None:
        """§48 重启恢复：重新载入队列记录。"""
        item = SimulationQueueItem(
            simulation_id=item_dict["simulation_id"],
            snapshot_id=item_dict["snapshot_id"],
            symbol=item_dict["symbol"],
            snapshot=item_dict.get("snapshot") or {},
            created_at=item_dict.get("created_at", 0),
            status=SimulationStatus(item_dict["status"]),
            updated_at=item_dict.get("updated_at", 0),
        )
        for k in ("recommendation_price", "entry_zone_low", "entry_zone_high",
                  "current_price", "distance_pct", "max_gain_pct", "max_drawdown_pct",
                  "entry_zone_reached_at", "entry_zone_reached_price",
                  "revalidate_started_at", "revalidate_result", "armed_at",
                  "entered_at", "entry_price", "entry_reason", "entry_confirmation",
                  "closed_at", "exit_reason", "exit_price", "fail_reason"):
            if item_dict.get(k) is not None:
                setattr(item, k, item_dict[k])
        self._items[item.simulation_id] = item

    def get(self, simulation_id: str) -> SimulationQueueItem | None:
        return self._items.get(simulation_id)

    def all(self) -> list[SimulationQueueItem]:
        return list(self._items.values())

    def by_status(self, status: str | SimulationStatus) -> list[SimulationQueueItem]:
        st = SimulationStatus(status) if not isinstance(status, SimulationStatus) else status
        return [i for i in self._items.values() if i.status == st]

    def active(self) -> list[SimulationQueueItem]:
        return [i for i in self._items.values() if i.status in _ACTIVE_STATUSES]

    # ── 逐 tick 驱动 ──

    def tick_symbol(self, symbol: str, ctx: dict[str, Any], now_ms: int) -> list[dict[str, Any]]:
        """推进该 symbol 全部活跃队列项一个状态步。返回本次产生的事件列表。"""
        occurred: list[dict[str, Any]] = []
        for item in [i for i in self._items.values()
                     if i.symbol == symbol and i.status in _ACTIVE_STATUSES]:
            ev = self._tick_item(item, ctx, now_ms)
            if ev is not None:
                occurred.append(ev)
                self.events.append(ev)
                if self.repo is not None:
                    self.repo.save_simulation_event(ev)
                    self.repo.save_simulation_queue_item(item.to_dict())
        return occurred

    def _tick_item(self, item: SimulationQueueItem, ctx: dict[str, Any], now_ms: int) -> dict[str, Any] | None:
        price = ctx.get("price")
        st = item.status

        # WATCHING 期持续记录 §24（最大涨跌/距离/当前价）
        if price is not None:
            base = item.recommendation_price
            item.current_price = price
            if base:
                pct = (price - base) / base * 100.0
                item.max_gain_pct = max(item.max_gain_pct, pct)
                item.max_drawdown_pct = min(item.max_drawdown_pct, pct)

        if st == SimulationStatus.WATCHING:
            if self._state_invalid(ctx):
                return self._set(item, SimulationStatus.INVALIDATED, now_ms,
                                 reason="推荐状态失效（离开正式范围/明确 Veto）")
            if self._expired(item, now_ms):
                return self._set(item, SimulationStatus.EXPIRED, now_ms,
                                 reason=f"推荐超时未入场（{self.expire_ms // 60000} 分钟）")
            if self._in_zone(price, item):
                item.entry_zone_reached_at = now_ms
                item.entry_zone_reached_price = price
                return self._set(item, SimulationStatus.ENTRY_ZONE_REACHED, now_ms,
                                 reason="价格进入参考 Entry Zone（启动二次验证，§25）")

        elif st == SimulationStatus.ENTRY_ZONE_REACHED:
            item.revalidate_started_at = now_ms
            return self._set(item, SimulationStatus.REVALIDATING, now_ms,
                             reason="执行入场二次验证（§26）")

        elif st == SimulationStatus.REVALIDATING:
            res: RevalidationResult = self.revalidation.evaluate(ctx, item.snapshot, now_ms)
            item.revalidate_result = res.to_dict()
            if res.passed:
                item.armed_at = now_ms
                return self._set(item, SimulationStatus.ARMED, now_ms,
                                 reason=f"二次验证通过 {res.passed_checks}/{len(res.checks)} 项，等待入场（§27）")
            item.fail_reason = res.fail_reason
            return self._set(item, SimulationStatus.CANCELLED, now_ms,
                             reason=f"二次验证未通过：{res.fail_reason}（§27）")

        elif st == SimulationStatus.ARMED:
            if self._state_invalid(ctx):
                return self._set(item, SimulationStatus.INVALIDATED, now_ms,
                                 reason="武装等待期状态失效")
            if self._expired(item, now_ms):
                return self._set(item, SimulationStatus.EXPIRED, now_ms,
                                 reason=f"推荐超时（{self.expire_ms // 60000} 分钟）")
            res = self.revalidation.evaluate(ctx, item.snapshot, now_ms)
            if not res.passed:
                item.fail_reason = res.fail_reason
                return self._set(item, SimulationStatus.CANCELLED, now_ms,
                                 reason=f"二次验证未通过：{res.fail_reason}（§27）")
            if self._in_zone(price, item):
                # §28 第一版：Entry Zone 内第一笔符合 Revalidation 的价格
                item.entered_at = now_ms
                item.entry_price = price
                item.entry_reason = "Entry Zone 内第一笔符合 Revalidation 的价格（§28）"
                item.entry_confirmation = res.to_dict()
                if self.positions is not None:
                    self.positions.open(item, now_ms)
                return self._set(item, SimulationStatus.SIMULATED_ENTRY, now_ms,
                                 reason="模拟成交（§28）")
            grace_ms = int(getattr(self.cfg, "entry_zone_grace_s", 10.0) * 1000.0)
            if item.armed_at is not None and now_ms - item.armed_at > grace_ms:
                return self._set(item, SimulationStatus.MISSED, now_ms,
                                 reason=f"武装后 {grace_ms // 1000}s 宽限期满未成交")

        elif st == SimulationStatus.SIMULATED_ENTRY:
            return self._set(item, SimulationStatus.OPEN, now_ms, reason="模拟持仓开始（§29）")

        elif st == SimulationStatus.OPEN:
            pos = self.positions.get(item.simulation_id) if self.positions is not None else None
            if pos is not None and pos.status == "CLOSED":
                item.closed_at = pos.exit_time
                item.exit_reason = pos.exit_reason
                item.exit_price = pos.exit_price
                return self._set(item, SimulationStatus.CLOSED, now_ms,
                                 reason=f"平仓：{pos.exit_reason}（§31）")
        return None

    # ── 辅助 ──

    def _set(self, item: SimulationQueueItem, status: SimulationStatus, now_ms: int,
             *, reason: str) -> dict[str, Any]:
        old = item.status
        item.status = status
        item.updated_at = now_ms
        ev = {
            "simulation_id": item.simulation_id,
            "symbol": item.symbol,
            "asof": now_ms,
            "old_status": old.value,
            "new_status": status.value,
            "reason": reason,
        }
        logger.info("[simulation] %s %s→%s %s", item.symbol, old.value, status.value, reason)
        return ev

    def _expired(self, item: SimulationQueueItem, now_ms: int) -> bool:
        return now_ms - item.created_at > self.expire_ms

    def _in_zone(self, price: float | None, item: SimulationQueueItem) -> bool:
        if price is None or item.entry_zone_low is None or item.entry_zone_high is None:
            return False
        return item.entry_zone_low <= price <= item.entry_zone_high

    def _state_invalid(self, ctx: dict[str, Any]) -> bool:
        state = ctx.get("state")
        if state is not None and state not in FORMAL_STATES:
            return True
        return bool(ctx.get("invalidated"))