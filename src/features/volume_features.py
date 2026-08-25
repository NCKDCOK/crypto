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
    volume_acceleration: float | None
    average_trade_size: float | None
    large_trade_ratio: float | None


def compute_window_volume(trades: Sequence[TradeEvent]) -> float:
    """计算窗口内总成交量（基础资产数量）。"""
    return float(sum(float(t.qty) for t in trades))


def compute_window_trade_count(trades: Sequence[TradeEvent]) -> int:
    """计算窗口内成交笔数。"""
    return len(trades)


def compute_volume_acceleration(trades: Sequence[TradeEvent]) -> float | None:
    """成交量加速度 = 后半段成交量 - 前半段成交量（归一化）。"""
    if len(trades) < 4:
        return None
    sorted_trades = sorted(trades, key=lambda t: t.receive_time)
    mid = len(sorted_trades) // 2
    first_half = sum(float(t.qty) for t in sorted_trades[:mid])
    second_half = sum(float(t.qty) for t in sorted_trades[mid:])
    total = first_half + second_half
    if total <= 0:
        return None
    return (second_half - first_half) / total


def compute_average_trade_size(trades: Sequence[TradeEvent]) -> float | None:
    """平均单笔成交量（基础资产数量）。"""
    if not trades:
        return None
    return compute_window_volume(trades) / len(trades)


def compute_large_trade_ratio(
    trades: Sequence[TradeEvent],
    large_threshold: float | None = None,
) -> float | None:
    """大单占比 = 超过阈值的成交笔数 / 总笔数。

    threshold 默认用窗口内单笔成交量的 3× 中位数（robust）。
    """
    if not trades:
        return None
    sizes = sorted(float(t.qty) for t in trades)
    if not sizes:
        return None
    median_size = sizes[len(sizes) // 2]
    threshold = large_threshold if large_threshold is not None else median_size * 3.0
    if threshold <= 0:
        return None
    large = sum(1 for s in sizes if s > threshold)
    return large / len(sizes)


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
        volume_acceleration=compute_volume_acceleration(window_trades),
        average_trade_size=compute_average_trade_size(window_trades),
        large_trade_ratio=compute_large_trade_ratio(window_trades),
    )
