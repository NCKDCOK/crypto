"""Context 特征 — Funding / Premium percentile。

依据：ANALYSIS_MODEL.md §2.6
- funding_percentile = 当前 funding 在 24h 基线的百分位
- premium_percentile = 当前 premium 在基线的百分位

只能 context / soft veto，不得单独触发信号。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from src.domain import FundingRateSnapshot
from .baseline import percentile


@dataclass
class ContextFeatures:
    """上下文类特征结果。"""

    funding_percentile: float | None
    premium_percentile: float | None


def compute_context_features(
    current: FundingRateSnapshot,
    baseline_snapshots: Sequence[FundingRateSnapshot],
) -> ContextFeatures:
    """计算上下文类特征。

    Args:
        current: 当前 FundingRateSnapshot。
        baseline_snapshots: 基线期的 FundingRateSnapshot 序列。

    Returns:
        ContextFeatures，样本不足时为 None。
    """
    if len(baseline_snapshots) < 3:
        return ContextFeatures(funding_percentile=None, premium_percentile=None)

    funding_values = sorted(float(s.last_funding_rate) for s in baseline_snapshots)
    premium_values = sorted(float(s.premium) for s in baseline_snapshots)

    current_funding = float(current.last_funding_rate)
    current_premium = float(current.premium)

    return ContextFeatures(
        funding_percentile=percentile(current_funding, funding_values),
        premium_percentile=percentile(current_premium, premium_values),
    )
