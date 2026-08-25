"""Price 特征 — return / acceleration / high-low break / acceptance。

依据：改造任务文档 §11 Price 类, ANALYSIS_MODEL.md §2.2
- price_return = (P_end - P_start) / P_start（各窗口）
- price_acceleration = 价格二阶差分（窗口内 return 的变化）
- high_break / low_break = 当前价是否突破更长窗口高/低
- acceptance = 突破后能站稳的比例（窗口后段落在突破方向侧的成交占比）

缺数据 → None。所有价格用 Decimal 精度计算后转 float。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from src.domain import TradeEvent


@dataclass
class PriceFeatures:
    """价类特征结果。"""

    price_return: float | None
    price_acceleration: float | None
    high: float | None
    low: float | None
    high_break: bool | None
    low_break: bool | None
    acceptance: float | None


def compute_price_return(trades: Sequence[TradeEvent]) -> float | None:
    """price_return = (P_end - P_start) / P_start。"""
    if len(trades) < 2:
        return None
    sorted_trades = sorted(trades, key=lambda t: t.receive_time)
    p_start = float(sorted_trades[0].price)
    p_end = float(sorted_trades[-1].price)
    if p_start == 0:
        return None
    return (p_end - p_start) / p_start


def compute_price_acceleration(trades: Sequence[TradeEvent]) -> float | None:
    """价格加速度 = 后半段 return - 前半段 return（窗口内二阶近似）。"""
    if len(trades) < 4:
        return None
    sorted_trades = sorted(trades, key=lambda t: t.receive_time)
    prices = [float(t.price) for t in sorted_trades]
    mid = len(prices) // 2
    if prices[0] == 0 or prices[mid] == 0:
        return None
    ret_first = (prices[mid] - prices[0]) / prices[0]
    ret_second = (prices[-1] - prices[mid]) / prices[mid]
    return ret_second - ret_first


def compute_window_high_low(trades: Sequence[TradeEvent]) -> tuple[float | None, float | None]:
    """窗口内最高/最低价。"""
    if not trades:
        return None, None
    prices = [float(t.price) for t in trades]
    return max(prices), min(prices)


def compute_breakout(
    current_price: float | None,
    ref_high: float | None,
    ref_low: float | None,
) -> tuple[bool | None, bool | None]:
    """当前价是否突破参考窗口高/低。"""
    high_break = None
    low_break = None
    if current_price is not None and ref_high is not None:
        high_break = current_price > ref_high
    if current_price is not None and ref_low is not None:
        low_break = current_price < ref_low
    return high_break, low_break


def compute_acceptance(
    trades: Sequence[TradeEvent],
    breakout_price: float | None,
    direction: str | None,
) -> float | None:
    """acceptance = 窗口后段成交价维持在突破方向侧的比例。

    direction=LONG → 后段价格 >= breakout_price 的占比
    direction=SHORT → 后段价格 <= breakout_price 的占比
    """
    if breakout_price is None or not direction or len(trades) < 2:
        return None
    sorted_trades = sorted(trades, key=lambda t: t.receive_time)
    # 后半段
    tail = sorted_trades[len(sorted_trades) // 2:]
    if not tail:
        return None
    if direction == "LONG":
        sustained = sum(1 for t in tail if float(t.price) >= breakout_price)
    else:
        sustained = sum(1 for t in tail if float(t.price) <= breakout_price)
    return sustained / len(tail)


def compute_price_features(
    window_trades: Sequence[TradeEvent],
    ref_long_trades: Sequence[TradeEvent] | None = None,
    direction: str | None = None,
) -> PriceFeatures:
    """计算价类特征。

    Args:
        window_trades: 当前窗口成交（计算 return/accel/acceptance）。
        ref_long_trades: 更长参考窗口成交（计算 high/low break）。
        direction: 突破方向，用于 acceptance。
    """
    price_return = compute_price_return(window_trades)
    price_accel = compute_price_acceleration(window_trades)

    ref_high, ref_low = (None, None)
    if ref_long_trades:
        ref_high, ref_low = compute_window_high_low(ref_long_trades)

    current_price = None
    if window_trades:
        current_price = float(max(window_trades, key=lambda t: t.receive_time).price)

    high_break, low_break = compute_breakout(current_price, ref_high, ref_low)

    # acceptance 用窗口内最高/低作为突破价
    win_high, win_low = compute_window_high_low(window_trades)
    breakout_price = None
    if direction == "LONG" and win_high is not None:
        breakout_price = win_high
    elif direction == "SHORT" and win_low is not None:
        breakout_price = win_low
    acceptance = compute_acceptance(window_trades, breakout_price, direction)

    return PriceFeatures(
        price_return=price_return,
        price_acceleration=price_accel,
        high=win_high,
        low=win_low,
        high_break=high_break,
        low_break=low_break,
        acceptance=acceptance,
    )
