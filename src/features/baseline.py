"""Robust baseline — rolling median / MAD / robust Z-score。

依据：ANALYSIS_MODEL.md §0, §1
优先用"相对自身历史"的 robust baseline，避免固定阈值对不同山寨币失效。
默认用过去 1h 的滚动数据计算 median/MAD；不同 symbol 不共享基线。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Sequence


@dataclass
class BaselineResult:
    """robust baseline 计算结果。"""

    median: float
    mad: float  # Median Absolute Deviation
    robust_std: float  # 1.4826 * MAD（近似标准差）
    sample_count: int

    @property
    def is_valid(self) -> bool:
        """样本数 >= 3 且 robust_std > 0 才有效。"""
        return self.sample_count >= 3 and self.robust_std > 0


def compute_median(values: Sequence[float]) -> float:
    """计算中位数。"""
    if not values:
        return 0.0
    return statistics.median(values)


def compute_mad(values: Sequence[float], median: float | None = None) -> float:
    """计算 Median Absolute Deviation。

    MAD = median(|x_i - median(x)|)
    """
    if not values:
        return 0.0
    if median is None:
        median = statistics.median(values)
    deviations = [abs(v - median) for v in values]
    return statistics.median(deviations)


def compute_baseline(values: Sequence[float]) -> BaselineResult:
    """计算 robust baseline (median + MAD)。

    Args:
        values: 基线样本值序列。

    Returns:
        BaselineResult，含 median / mad / robust_std (1.4826*MAD) / sample_count。
    """
    if not values:
        return BaselineResult(median=0.0, mad=0.0, robust_std=0.0, sample_count=0)

    median = statistics.median(values)
    mad = compute_mad(values, median)
    robust_std = 1.4826 * mad

    return BaselineResult(
        median=median,
        mad=mad,
        robust_std=robust_std,
        sample_count=len(values),
    )


def robust_z_score(value: float, baseline: BaselineResult) -> float | None:
    """计算 robust Z-score = (value - median) / (1.4826 * MAD)。

    样本不足或 robust_std=0 → None。
    """
    if not baseline.is_valid:
        return None
    return (value - baseline.median) / baseline.robust_std


def percentile(value: float, sorted_values: Sequence[float]) -> float | None:
    """计算 value 在 sorted_values 中的百分位。

    Args:
        value: 待计算值。
        sorted_values: 已排序的基线值序列。

    Returns:
        0-100 的百分位，样本不足返回 None。
    """
    if len(sorted_values) < 3:
        return None
    count = sum(1 for v in sorted_values if v <= value)
    return (count / len(sorted_values)) * 100.0
