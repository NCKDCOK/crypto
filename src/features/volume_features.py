"""Volume / Trade Count 特征 — RVOL / Volume Z / Trade Count Z。

依据：ANALYSIS_MODEL.md §2.1
- rvol = 当前窗口成交量 / baseline median
- volume_z = robust Z of volume
- trade_count_z = robust Z of trade count
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from src.domain import TradeEvent
from .baseline import BaselineResult, compute_baseline, robust_z_score


@dataclass
class VolumeFeatures:
    """量类特征结果。"""

    rvol: float | None
    volume_z: float | None
    trade_count_z: float | None
    window_volume: float
    window_trade_count: int


def compute_window_volume(trades: Sequence[TradeEvent]) -> float:
    """计算窗口内总成交量（基础资产数量）。"""
    return float(sum(float(t.qty) for t in trades))


def compute_window_trade_count(trades: Sequence[TradeEvent]) -> int:
    """计算窗口内成交笔数。"""
    return len(trades)


def compute_volume_features(
    window_trades: Sequence[TradeEvent],
    baseline_volumes: Sequence[float],
    baseline_trade_counts: Sequence[float],
) -> VolumeFeatures:
    """计算量类特征。

    Args:
        window_trades: 当前窗口内的 TradeEvent 列表。
        baseline_volumes: 基线期各窗口的成交量序列。
        baseline_trade_counts: 基线期各窗口的成交笔数序列。

    Returns:
        VolumeFeatures，缺数据时对应字段为 None。
    """
    window_volume = compute_window_volume(window_trades)
    window_trade_count = compute_window_trade_count(window_trades)

    # RVOL = 当前成交量 / baseline median
    vol_baseline = compute_baseline(baseline_volumes)
    rvol = window_volume / vol_baseline.median if vol_baseline.median > 0 else None

    # Volume Z
    volume_z = robust_z_score(window_volume, vol_baseline)

    # Trade Count Z
    tc_baseline = compute_baseline(baseline_trade_counts)
    trade_count_z = robust_z_score(float(window_trade_count), tc_baseline)

    return VolumeFeatures(
        rvol=rvol,
        volume_z=volume_z,
        trade_count_z=trade_count_z,
        window_volume=window_volume,
        window_trade_count=window_trade_count,
    )
