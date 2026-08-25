"""Price Efficiency / Flow Impact / Retrace 特征。

依据：ANALYSIS_MODEL.md §2.5
- directional_efficiency = |P_end - P_start| / Σ|ΔP_i|  （窗口：30s / 1m）
- flow_impact = signed_return / max(|net_taker_notional|, ε)
  （窗口：30s / 1m；ε 默认 1.0 USDT）
- retrace_ratio = 突破后回吐位移 / 突破位移

AbsorptionCandidate = |delta| 大 AND flow_impact 极低。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from src.domain import AggressorSide, TradeEvent


@dataclass
class EfficiencyFeatures:
    """效率类特征结果。"""

    directional_efficiency: float | None
    flow_impact: float | None
    retrace_ratio: float | None


def compute_directional_efficiency(trades: Sequence[TradeEvent]) -> float | None:
    """DirectionalEfficiency = |P_end - P_start| / Σ|ΔP_i|。

    窗口内首末价格净位移 / 路径总位移。
    0 ≤ efficiency ≤ 1：1 = 纯趋势，接近 0 = 来回震荡。
    """
    if len(trades) < 2:
        return None
    # 按 receive_time 排序
    sorted_trades = sorted(trades, key=lambda t: t.receive_time)
    prices = [float(t.price) for t in sorted_trades]

    net_displacement = abs(prices[-1] - prices[0])
    path_total = sum(abs(prices[i] - prices[i - 1]) for i in range(1, len(prices)))

    if path_total == 0:
        return 0.0  # 价格没变
    return net_displacement / path_total


def compute_flow_impact(
    trades: Sequence[TradeEvent],
    epsilon: float = 1.0,
) -> float | None:
    """FlowImpact = signed_return / max(|net_taker_notional|, ε)。

    signed_return = (P_end - P_start) / P_start
    net_taker_notional = Σ(buy_notional) - Σ(sell_notional)

    极低 flow_impact 可能表示吸收（absorption）。
    """
    if len(trades) < 2:
        return None
    sorted_trades = sorted(trades, key=lambda t: t.receive_time)

    p_start = float(sorted_trades[0].price)
    p_end = float(sorted_trades[-1].price)
    if p_start == 0:
        return None

    signed_return = (p_end - p_start) / p_start

    buy_notional = sum(
        float(t.quote_notional) for t in sorted_trades
        if t.aggressor_side == AggressorSide.BUY
    )
    sell_notional = sum(
        float(t.quote_notional) for t in sorted_trades
        if t.aggressor_side == AggressorSide.SELL
    )
    net_taker_notional = buy_notional - sell_notional

    return signed_return / max(abs(net_taker_notional), epsilon)


def compute_retrace_ratio(
    trades: Sequence[TradeEvent],
    window_ms: int = 60_000,
) -> float | None:
    """RetraceRatio = 突破后回吐位移 / 突破位移。

    窗口内最大方向位移 vs 最后回吐。
    计算：找到窗口内最大价格偏移点，然后算从该点到末尾的回吐比例。
    """
    if len(trades) < 2:
        return None
    sorted_trades = sorted(trades, key=lambda t: t.receive_time)
    prices = [float(t.price) for t in sorted_trades]

    p_start = prices[0]
    if p_start == 0:
        return None

    # 找最大偏移点
    max_offset_idx = 0
    max_offset = 0.0
    for i, p in enumerate(prices):
        offset = abs(p - p_start)
        if offset > max_offset:
            max_offset = offset
            max_offset_idx = i

    if max_offset == 0:
        return 0.0

    # 从最大偏移点到末尾的回吐
    p_peak = prices[max_offset_idx]
    p_end = prices[-1]

    # 回吐 = 从峰值回到起点的比例
    breakout = p_peak - p_start
    retrace = p_peak - p_end

    if breakout == 0:
        return 0.0

    # 如果 breakout 向上，retrace 为正表示回吐
    # 如果 breakout 向下，retrace 为负表示回吐
    ratio = retrace / breakout if breakout != 0 else 0.0
    return ratio


def compute_efficiency_features(
    trades: Sequence[TradeEvent],
    epsilon: float = 1.0,
) -> EfficiencyFeatures:
    """计算效率类特征。"""
    return EfficiencyFeatures(
        directional_efficiency=compute_directional_efficiency(trades),
        flow_impact=compute_flow_impact(trades, epsilon),
        retrace_ratio=compute_retrace_ratio(trades),
    )
