"""Taker Delta / CVD 特征 — 主动买卖资金流。

依据：ANALYSIS_MODEL.md §2.3
- taker_delta = Σ(buy_notional) - Σ(sell_notional)，buy/sell 由 aggressor_side 判定
- cvd = 累积 taker_delta（按 symbol）
- cvd_slope_z = robust Z of CVD 斜率
- cvd_accel_z = robust Z of CVD 二阶差分

CVD 方向完全依赖 aggressor_side 正确性（m=true → SELL）。
重连重复 trade 必须在进入 CVD 前去重（Gate 2 已保证）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Sequence

from src.domain import AggressorSide, TradeEvent
from .baseline import BaselineResult, compute_baseline, robust_z_score


@dataclass
class FlowFeatures:
    """资金流特征结果。"""

    taker_delta: float | None
    taker_buy_volume: float | None
    taker_sell_volume: float | None
    delta_ratio: float | None
    cvd: float
    cvd_slope: float | None
    cvd_slope_z: float | None
    cvd_accel_z: float | None


def compute_taker_delta(trades: Sequence[TradeEvent]) -> float | None:
    """计算窗口内 Taker Delta = Σ(buy_notional) - Σ(sell_notional)。

    buy/sell 由 aggressor_side 判定：
    - BUY → 主动买入，计入 buy_notional
    - SELL → 主动卖出，计入 sell_notional
    - UNKNOWN → 不计入
    """
    if not trades:
        return None

    buy_notional = sum(
        float(t.quote_notional) for t in trades
        if t.aggressor_side == AggressorSide.BUY
    )
    sell_notional = sum(
        float(t.quote_notional) for t in trades
        if t.aggressor_side == AggressorSide.SELL
    )
    return buy_notional - sell_notional


def compute_taker_buy_sell_volume(trades: Sequence[TradeEvent]) -> tuple[float | None, float | None]:
    """计算窗口内主动买/卖名义额。

    Returns:
        (taker_buy_volume, taker_sell_volume)，空窗口为 None。
    """
    if not trades:
        return None, None
    buy = sum(
        float(t.quote_notional) for t in trades
        if t.aggressor_side == AggressorSide.BUY
    )
    sell = sum(
        float(t.quote_notional) for t in trades
        if t.aggressor_side == AggressorSide.SELL
    )
    return buy, sell


class CVDTracker:
    """CVD 累积器 — 按 symbol 维护累积 taker_delta。

    同时维护窗口化的 CVD 斜率和加速度历史，用于 robust Z 计算。
    """

    def __init__(self) -> None:
        # symbol → 累积 CVD
        self._cvd: dict[str, float] = {}
        # symbol → [(receive_time, cvd)] 窗口化历史
        self._cvd_history: dict[str, list[tuple[int, float]]] = {}
        # symbol → 历史斜率序列（用于 baseline）
        self._slope_history: dict[str, list[float]] = {}
        # symbol → 历史加速度序列
        self._accel_history: dict[str, list[float]] = {}

    def update(self, symbol: str, delta: float, receive_time: int) -> float:
        """更新 CVD。返回当前累积 CVD。"""
        old_cvd = self._cvd.get(symbol, 0.0)
        new_cvd = old_cvd + delta
        self._cvd[symbol] = new_cvd

        # 记录历史
        if symbol not in self._cvd_history:
            self._cvd_history[symbol] = []
        self._cvd_history[symbol].append((receive_time, new_cvd))

        # 计算斜率
        hist = self._cvd_history[symbol]
        if len(hist) >= 2:
            t0, c0 = hist[-2]
            t1, c1 = hist[-1]
            dt = (t1 - t0) / 1000.0  # 转秒
            if dt > 0:
                slope = (c1 - c0) / dt
                if symbol not in self._slope_history:
                    self._slope_history[symbol] = []
                self._slope_history[symbol].append(slope)

                # 计算加速度
                slopes = self._slope_history[symbol]
                if len(slopes) >= 2:
                    accel = slopes[-1] - slopes[-2]
                    if symbol not in self._accel_history:
                        self._accel_history[symbol] = []
                    self._accel_history[symbol].append(accel)

        return new_cvd

    def get_cvd(self, symbol: str) -> float:
        return self._cvd.get(symbol, 0.0)

    def get_cvd_slope(self, symbol: str) -> float | None:
        slopes = self._slope_history.get(symbol, [])
        return slopes[-1] if slopes else None

    def get_cvd_slope_z(self, symbol: str) -> float | None:
        slopes = self._slope_history.get(symbol, [])
        current = slopes[-1] if slopes else None
        if current is None:
            return None
        baseline = compute_baseline(slopes[:-1] if len(slopes) > 1 else [])
        return robust_z_score(current, baseline)

    def get_cvd_accel_z(self, symbol: str) -> float | None:
        accels = self._accel_history.get(symbol, [])
        current = accels[-1] if accels else None
        if current is None:
            return None
        baseline = compute_baseline(accels[:-1] if len(accels) > 1 else [])
        return robust_z_score(current, baseline)

    def reset(self, symbol: str | None = None) -> None:
        if symbol:
            self._cvd.pop(symbol, None)
            self._cvd_history.pop(symbol, None)
            self._slope_history.pop(symbol, None)
            self._accel_history.pop(symbol, None)
        else:
            self._cvd.clear()
            self._cvd_history.clear()
            self._slope_history.clear()
            self._accel_history.clear()


def compute_flow_features(
    symbol: str,
    window_trades: Sequence[TradeEvent],
    cvd_tracker: CVDTracker,
    now_ms: int,
) -> FlowFeatures:
    """计算资金流特征。

    Args:
        symbol: 交易对
        window_trades: 当前窗口内的 TradeEvent 列表
        cvd_tracker: CVD 累积器
        now_ms: 当前时间

    Returns:
        FlowFeatures
    """
    delta = compute_taker_delta(window_trades)
    if delta is not None:
        cvd_tracker.update(symbol, delta, now_ms)

    cvd = cvd_tracker.get_cvd(symbol)
    slope = cvd_tracker.get_cvd_slope(symbol)
    slope_z = cvd_tracker.get_cvd_slope_z(symbol)
    accel_z = cvd_tracker.get_cvd_accel_z(symbol)

    buy_vol, sell_vol = compute_taker_buy_sell_volume(window_trades)
    delta_ratio: float | None = None
    if buy_vol is not None and sell_vol is not None and (buy_vol + sell_vol) > 0:
        delta_ratio = (buy_vol - sell_vol) / (buy_vol + sell_vol)

    return FlowFeatures(
        taker_delta=delta,
        taker_buy_volume=buy_vol,
        taker_sell_volume=sell_vol,
        delta_ratio=delta_ratio,
        cvd=cvd,
        cvd_slope=slope,
        cvd_slope_z=slope_z,
        cvd_accel_z=accel_z,
    )
