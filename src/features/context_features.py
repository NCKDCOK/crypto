"""Context 特征 — Funding / Premium percentile + zscore。

依据：ANALYSIS_MODEL.md §2.6 / V1.4 §十六
- funding_percentile = 当前 funding 在基线的百分位
- funding_zscore = 当前 funding 的 robust z-score（相对自身历史，避免固定阈值）
- premium_percentile = 当前 premium 在基线的百分位

V1.4 §十六：禁止用固定阈值（如 funding < -0.1%）单独触发；改用 zscore / percentile。

只能 context / soft veto，不得单独触发信号。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from src.domain import FundingRateSnapshot
from .baseline import compute_baseline, percentile, robust_z_score


@dataclass
class ContextFeatures:
    """上下文类特征结果。"""

    funding_percentile: float | None
    premium_percentile: float | None
    # V1.4 §十六：funding zscore（相对自身历史 robust baseline）
    funding_zscore: float | None = None
    # §十六：7d / 30d 百分位（需更长历史基线；基线不足时为 None，优雅降级）
    funding_percentile_7d: float | None = None
    funding_percentile_30d: float | None = None


def compute_context_features(
    current: FundingRateSnapshot,
    baseline_snapshots: Sequence[FundingRateSnapshot],
    *,
    baseline_7d: Sequence[FundingRateSnapshot] | None = None,
    baseline_30d: Sequence[FundingRateSnapshot] | None = None,
) -> ContextFeatures:
    """计算上下文类特征。

    Args:
        current: 当前 FundingRateSnapshot。
        baseline_snapshots: 基线期 FundingRateSnapshot 序列（默认窗口）。
        baseline_7d / baseline_30d: 更长历史窗口（可选；样本不足对应字段为 None）。

    Returns:
        ContextFeatures，样本不足时对应字段为 None。
    """
    if len(baseline_snapshots) < 3:
        result = ContextFeatures(funding_percentile=None, premium_percentile=None)
    else:
        funding_values = sorted(float(s.last_funding_rate) for s in baseline_snapshots)
        premium_values = sorted(float(s.premium) for s in baseline_snapshots)
        current_funding = float(current.last_funding_rate)
        current_premium = float(current.premium)
        # V1.4 §十六：funding zscore
        funding_baseline = compute_baseline([float(s.last_funding_rate) for s in baseline_snapshots])
        funding_z = robust_z_score(current_funding, funding_baseline)
        result = ContextFeatures(
            funding_percentile=percentile(current_funding, funding_values),
            premium_percentile=percentile(current_premium, premium_values),
            funding_zscore=funding_z,
        )
    # §十六：7d / 30d 百分位（更长历史；样本不足为 None）
    if baseline_7d is not None and len(baseline_7d) >= 3:
        vals7 = sorted(float(s.last_funding_rate) for s in baseline_7d)
        result.funding_percentile_7d = percentile(float(current.last_funding_rate), vals7)
    if baseline_30d is not None and len(baseline_30d) >= 3:
        vals30 = sorted(float(s.last_funding_rate) for s in baseline_30d)
        result.funding_percentile_30d = percentile(float(current.last_funding_rate), vals30)
    return result
