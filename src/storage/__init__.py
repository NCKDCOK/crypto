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
