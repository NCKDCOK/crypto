"""存储抽象 — Repository 接口空骨架。

V1 PostgreSQL，量大后 TimescaleDB。写入失败不得拖死 collectors。
依据：ARCHITECTURE.md §5
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any

from src.domain import AnalysisEvent, FeatureSnapshot, OpenInterestSnapshot


class Repository(ABC):
    """统一存储接口。Gate 0 仅定义接口，实现后续 Gate 补充。"""

    @abstractmethod
    async def save_event(self, event: Any) -> None:
        """持久化原始事件子集。"""
        ...

    @abstractmethod
    async def save_feature_snapshot(self, snap: FeatureSnapshot) -> None:
        """保存 FeatureSnapshot。"""
        ...

    @abstractmethod
    async def save_analysis_event(self, ev: AnalysisEvent) -> None:
        """保存状态转换 + evidence/veto。"""
        ...

    @abstractmethod
    async def get_oi_snapshot_asof(
        self,
        symbol: str,
        target_time: int,
        tolerance: int,
    ) -> OpenInterestSnapshot | None:
        """OI 时间对齐查询。

        在 [target_time - tolerance, target_time + tolerance] 内取
        receive_time 最近的快照；无满足条件的快照返回 None。
        """
        ...

    @abstractmethod
    async def list_transitions(
        self,
        symbol: str,
        since: int,
        until: int,
    ) -> list[AnalysisEvent]:
        """回放/历史查询。"""
        ...

    # ── V1.2 持久化方法（默认 no-op，SqliteRepository 覆写）──

    def save_kline(self, kline: Any) -> None:
        """持久化 closed K 线。默认 no-op。"""
        pass

    def save_oi_snapshot(self, snap: OpenInterestSnapshot) -> None:
        """持久化 OI 快照。默认 no-op。"""
        pass

    def save_funding_snapshot(self, snap: Any) -> None:
        """持久化 Funding 快照。默认 no-op。"""
        pass

    def save_trade_plan(self, symbol: str, asof: int, plan: dict[str, Any]) -> None:
        """持久化 Trade Plan 快照。默认 no-op。"""
        pass

    def expire_trade_plans(self, symbol: str | None, before_asof: int) -> int:
        """过期 Trade Plan。默认 no-op。"""
        return 0

    def get_active_trade_plan(self, symbol: str) -> dict[str, Any] | None:
        return None

    # ── V1.3 模拟验证持久化（§59, 默认 no-op，SqliteRepository 覆写）──

    def save_recommendation_snapshot(self, symbol: str, asof: int, snap: dict[str, Any]) -> None:
        """保存推荐快照（§60 recommendation_snapshots）。"""
        pass

    def save_simulation_queue_item(self, item: dict[str, Any]) -> None:
        """保存/更新模拟队列项（§59 simulation_queue）。"""
        pass

    def save_simulation_event(self, event: dict[str, Any]) -> None:
        """保存模拟事件（§59 simulation_events）。"""
        pass

    def save_simulation_position(self, pos: dict[str, Any]) -> None:
        """保存/更新模拟持仓（§59 simulation_positions）。"""
        pass

    def save_simulation_result(self, res: dict[str, Any]) -> None:
        """保存模拟结果（§61 simulation_results）。"""
        pass

    def list_recommendation_snapshots(
        self, symbol: str | None = None, limit: int = 200,
    ) -> list[dict[str, Any]]:
        return []

    def list_simulation_queue(self, status: str | None = None) -> list[dict[str, Any]]:
        return []

    def list_simulation_positions(self) -> list[dict[str, Any]]:
        return []

    def list_simulation_results(self, limit: int = 500) -> list[dict[str, Any]]:
        return []

    def get_simulation_result(self, simulation_id: str) -> dict[str, Any] | None:
        return None

    def get_simulation_position(self, simulation_id: str) -> dict[str, Any] | None:
        return None

    def list_simulation_events(
        self, symbol: str | None = None, limit: int = 200,
    ) -> list[dict[str, Any]]:
        return []

    def get_last_write_ms(self) -> int | None:
        return None

    def get_recent_klines(self, symbol: str, interval: str, limit: int = 300) -> list[Any]:
        return []

    def close(self) -> None:
        pass


class InMemoryRepository(Repository):
    """内存实现 — 测试/replay 用。Gate 0 提供基本可用版本。"""

    def __init__(self) -> None:
        self._events: list[Any] = []
        self._feature_snapshots: list[FeatureSnapshot] = []
        self._analysis_events: list[AnalysisEvent] = []
        self._oi_snapshots: list[OpenInterestSnapshot] = []
        # V1.3 模拟验证（§59）
        self._recommendation_snapshots: dict[str, dict[str, Any]] = {}
        self._simulation_queue: dict[str, dict[str, Any]] = {}
        self._simulation_events: list[dict[str, Any]] = []
        self._simulation_positions: dict[str, dict[str, Any]] = {}
        self._simulation_results: dict[str, dict[str, Any]] = {}

    async def save_event(self, event: Any) -> None:
        self._events.append(event)
        if isinstance(event, OpenInterestSnapshot):
            self._oi_snapshots.append(event)

    async def save_feature_snapshot(self, snap: FeatureSnapshot) -> None:
        self._feature_snapshots.append(snap)

    async def save_analysis_event(self, ev: AnalysisEvent) -> None:
        self._analysis_events.append(ev)

    async def get_oi_snapshot_asof(
        self,
        symbol: str,
        target_time: int,
        tolerance: int,
    ) -> OpenInterestSnapshot | None:
        """在容差范围内找最近的 OI 快照。容差外返回 None。"""
        candidates = [
            s
            for s in self._oi_snapshots
            if s.symbol == symbol
            and abs(s.receive_time - target_time) <= tolerance
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda s: abs(s.receive_time - target_time))

    async def list_transitions(
        self,
        symbol: str,
        since: int,
        until: int,
    ) -> list[AnalysisEvent]:
        return [
            ev
            for ev in self._analysis_events
            if ev.symbol == symbol and since <= ev.asof <= until
        ]

    # ── V1.3 模拟验证（内存版，测试/replay 用）──

    def save_recommendation_snapshot(self, symbol: str, asof: int, snap: dict[str, Any]) -> None:
        self._recommendation_snapshots[(snap.get("snapshot_id") or f"{symbol}-{asof}")] = dict(snap)

    def save_simulation_queue_item(self, item: dict[str, Any]) -> None:
        self._simulation_queue[item["simulation_id"]] = dict(item)

    def save_simulation_event(self, event: dict[str, Any]) -> None:
        self._simulation_events.append(dict(event))

    def save_simulation_position(self, pos: dict[str, Any]) -> None:
        self._simulation_positions[pos["simulation_id"]] = dict(pos)

    def save_simulation_result(self, res: dict[str, Any]) -> None:
        self._simulation_results[res["simulation_id"]] = dict(res)

    def list_recommendation_snapshots(
        self, symbol: str | None = None, limit: int = 200,
    ) -> list[dict[str, Any]]:
        snaps = [v for k, v in self._recommendation_snapshots.items()
                 if symbol is None or v.get("symbol") == symbol]
        snaps.sort(key=lambda s: s.get("timestamp", 0), reverse=True)
        return snaps[:limit]

    def list_simulation_queue(self, status: str | None = None) -> list[dict[str, Any]]:
        items = list(self._simulation_queue.values())
        if status is not None:
            items = [i for i in items if i.get("status") == status]
        items.sort(key=lambda i: i.get("updated_at", 0), reverse=True)
        return items

    def list_simulation_positions(self) -> list[dict[str, Any]]:
        return list(self._simulation_positions.values())

    def list_simulation_results(self, limit: int = 500) -> list[dict[str, Any]]:
        results = list(self._simulation_results.values())
        results.sort(key=lambda r: r.get("entry_time", 0), reverse=True)
        return results[:limit]

    def get_simulation_result(self, simulation_id: str) -> dict[str, Any] | None:
        return self._simulation_results.get(simulation_id)

    def get_simulation_position(self, simulation_id: str) -> dict[str, Any] | None:
        return self._simulation_positions.get(simulation_id)

    def list_simulation_events(
        self, symbol: str | None = None, limit: int = 200,
    ) -> list[dict[str, Any]]:
        events = [e for e in self._simulation_events
                  if symbol is None or e.get("symbol") == symbol]
        events.sort(key=lambda e: e.get("asof", 0), reverse=True)
        return events[:limit]
