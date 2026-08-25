"""OI 特征 — Δ / velocity / acceleration（基础资产数量）。

依据：ANALYSIS_MODEL.md §2.4
- oi_change = open_interest_now - open_interest_asof（基础资产数量差）
- oi_velocity = oi_change / Δt
- oi_accel = 二阶差分 of open_interest

价格变动但 open_interest 不变 ⇒ oi_change=0（不得用美元名义）。
缺数据 → None / unavailable。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from src.domain import OpenInterestSnapshot


@dataclass
class OIFeatures:
    """OI 特征结果。"""

    oi_contracts: float | None
    oi_change_30s: float | None
    oi_change_1m: float | None
    oi_change_5m: float | None
    oi_change_15m: float | None
    oi_velocity: float | None
    oi_accel: float | None


def compute_oi_change(
    snapshots: Sequence[OpenInterestSnapshot],
    lookback_ms: int,
    tolerance_ms: int,
) -> float | None:
    """计算 OI 变化 = current - asof。

    在容差范围内找最近快照，容差外返回 None。
    """
    if not snapshots:
        return None
    # 按时间排序
    sorted_snaps = sorted(snapshots, key=lambda s: s.receive_time)
    current = sorted_snaps[-1]
    target_time = current.receive_time - lookback_ms

    # 在容差内找最近
    candidates = [
        s for s in sorted_snaps
        if abs(s.receive_time - target_time) <= tolerance_ms
        and s.receive_time < current.receive_time
    ]
    if not candidates:
        return None

    asof = min(candidates, key=lambda s: abs(s.receive_time - target_time))
    return float(current.open_interest - asof.open_interest)


def compute_oi_velocity(snapshots: Sequence[OpenInterestSnapshot]) -> float | None:
    """计算 OI velocity = Δoi / Δt（每秒变化量）。"""
    if len(snapshots) < 2:
        return None
    sorted_snaps = sorted(snapshots, key=lambda s: s.receive_time)
    s0 = sorted_snaps[-2]
    s1 = sorted_snaps[-1]
    dt = (s1.receive_time - s0.receive_time) / 1000.0
    if dt <= 0:
        return None
    return float(s1.open_interest - s0.open_interest) / dt


def compute_oi_accel(snapshots: Sequence[OpenInterestSnapshot]) -> float | None:
    """计算 OI 加速度 = 二阶差分。"""
    if len(snapshots) < 3:
        return None
    sorted_snaps = sorted(snapshots, key=lambda s: s.receive_time)
    s0 = sorted_snaps[-3]
    s1 = sorted_snaps[-2]
    s2 = sorted_snaps[-1]

    dt1 = (s1.receive_time - s0.receive_time) / 1000.0
    dt2 = (s2.receive_time - s1.receive_time) / 1000.0
    if dt1 <= 0 or dt2 <= 0:
        return None

    v1 = float(s1.open_interest - s0.open_interest) / dt1
    v2 = float(s2.open_interest - s1.open_interest) / dt2
    return v2 - v1


def compute_oi_features(
    snapshots: Sequence[OpenInterestSnapshot],
    tolerance_ms: int = 15_000,
) -> OIFeatures:
    """计算全部 OI 特征。

    Args:
        snapshots: OI 快照历史（按 receive_time）。
        tolerance_ms: as-of lookup 容差。

    Returns:
        OIFeatures，缺数据时对应字段为 None。
    """
    oi_contracts: float | None = None
    if snapshots:
        sorted_snaps = sorted(snapshots, key=lambda s: s.receive_time)
        oi_contracts = float(sorted_snaps[-1].open_interest)

    return OIFeatures(
        oi_contracts=oi_contracts,
        oi_change_30s=compute_oi_change(snapshots, 30_000, tolerance_ms),
        oi_change_1m=compute_oi_change(snapshots, 60_000, tolerance_ms),
        oi_change_5m=compute_oi_change(snapshots, 300_000, tolerance_ms),
        oi_change_15m=compute_oi_change(snapshots, 900_000, tolerance_ms),
        oi_velocity=compute_oi_velocity(snapshots),
        oi_accel=compute_oi_accel(snapshots),
    )
