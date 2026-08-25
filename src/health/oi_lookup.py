"""OI as-of lookup — 带容差的时间对齐查询。

依据：docs/DATA_HEALTH.md §7
查找 N 分钟前 OI 快照时：
1. target_time = now − N
2. 在 [target_time − tolerance, target_time + tolerance] 内取 receive_time 最近的快照
3. 无满足条件的快照 → 返回 None（unavailable）
4. 不得回退取更旧数据
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from src.domain import OpenInterestSnapshot

logger = logging.getLogger(__name__)


@dataclass
class OILookupResult:
    """OI as-of lookup 结果。

    若 snapshot 为 None，表示无满足容差条件的快照（unavailable）。
    """

    snapshot: OpenInterestSnapshot | None
    target_time: int
    tolerance: int
    found: bool
    reason: str | None = None

    @property
    def available(self) -> bool:
        return self.snapshot is not None

    @property
    def oi_change(self) -> Decimal | None:
        """与当前快照的差值（如果有）。"""
        if self.snapshot is None:
            return None
        return self.snapshot.open_interest


class OILookup:
    """OI 时间对齐查询器。

    从 OI 快照历史中按时间查找，带容差。
    容差外无数据 → unavailable，不得回退取更旧数据。
    """

    def __init__(self, default_tolerance_ms: int = 15_000) -> None:
        self.default_tolerance_ms = default_tolerance_ms
        self._snapshots: dict[str, list[OpenInterestSnapshot]] = {}

    def add_snapshot(self, snap: OpenInterestSnapshot) -> None:
        """添加一个 OI 快照到历史。"""
        if snap.symbol not in self._snapshots:
            self._snapshots[snap.symbol] = []
        self._snapshots[snap.symbol].append(snap)
        # 保持按 receive_time 排序
        self._snapshots[snap.symbol].sort(key=lambda s: s.receive_time)

    def lookup(
        self,
        symbol: str,
        target_time: int,
        tolerance: int | None = None,
    ) -> OILookupResult:
        """在容差范围内查找最近的 OI 快照。

        在 [target_time - tolerance, target_time + tolerance] 内取
        receive_time 最近的快照。容差外返回 unavailable。
        """
        tol = tolerance if tolerance is not None else self.default_tolerance_ms
        snapshots = self._snapshots.get(symbol, [])

        if not snapshots:
            return OILookupResult(
                snapshot=None,
                target_time=target_time,
                tolerance=tol,
                found=False,
                reason="no_snapshots",
            )

        # 在容差范围内找最近的
        candidates = [
            s
            for s in snapshots
            if abs(s.receive_time - target_time) <= tol
        ]

        if not candidates:
            # 找最近的快照看差多少，用于日志
            nearest = min(snapshots, key=lambda s: abs(s.receive_time - target_time))
            gap = abs(nearest.receive_time - target_time)
            logger.debug(
                "oi_lookup_unavailable symbol=%s target=%d nearest_gap=%dms tolerance=%dms",
                symbol,
                target_time,
                gap,
                tol,
            )
            return OILookupResult(
                snapshot=None,
                target_time=target_time,
                tolerance=tol,
                found=False,
                reason=f"nearest_gap_{gap}ms_exceeds_tolerance_{tol}ms",
            )

        # 取距 target_time 最近的
        best = min(candidates, key=lambda s: abs(s.receive_time - target_time))
        return OILookupResult(
            snapshot=best,
            target_time=target_time,
            tolerance=tol,
            found=True,
        )

    def compute_change(
        self,
        symbol: str,
        current: OpenInterestSnapshot,
        lookback_ms: int,
        tolerance: int | None = None,
    ) -> Decimal | None:
        """计算 OI 变化 = current - asof。

        target_time = current.receive_time - lookback_ms
        容差外返回 None（unavailable），不得回退取更旧数据。
        """
        target_time = current.receive_time - lookback_ms
        result = self.lookup(symbol, target_time, tolerance)
        if not result.available or result.snapshot is None:
            return None
        return current.open_interest - result.snapshot.open_interest

    def clear(self, symbol: str | None = None) -> None:
        """清除快照历史。"""
        if symbol:
            self._snapshots.pop(symbol, None)
        else:
            self._snapshots.clear()
